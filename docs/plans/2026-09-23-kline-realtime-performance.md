# K 线实时刷新性能优化方案

计划文档目标路径：`docs/plans/2026-09-23-kline-realtime-performance.md`

## Summary

当前实测基线：

- 日线请求：约 `3.05s / 14.09MB`，虽然只返回 300 根 K 线。
- 5 分钟请求：约 `0.19–0.66s / 1.0–1.4MB`。
- 30 分钟实时请求当前还出现一次 HTTP 500，需要一并修复。
- 日线响应中约 `13.85MB` 来自结构数据，其中 `promotion_candidate_revisions` 约 `6.9MB`、`center_revisions` 约 `2.5MB`。

瓶颈按严重程度排序：

1. 每 15 秒用全部历史 K 线重新运行 `preview_period()` 和完整缠论结构计算。
2. 实时分支直接用未经分页投影的完整 preview 覆盖原本已裁剪的结构，导致 14MB 响应。
3. 每次刷新都读取全部历史、重算指标并序列化完整结构。
4. 前端每次都替换 K 线和结构，执行 `setOption(..., {notMerge:true})`，使 ECharts 整图重建。
5. 实时刷新把 `has_more` 清空，已向左加载的历史数据会丢失。

采用已确认策略：报价和形成中 K 线继续约每 15 秒更新；笔、中枢等结构只在周期收口时重算。

## Implementation Changes

### 1. 后端拆分实时行情与结构刷新

- 保留 `/api/chart-data/{symbol}` 负责首次加载和历史分页，并修正 preview 结构必须经过与正式结构相同的 300 根窗口投影，禁止直接返回完整审计结构。
- 新增 `/api/chart-realtime/{symbol}`，仅返回：
  - `bar_upserts`：相对上次状态发生变化的当天 K 线。
  - `quote`、`forming_bar`、`period_refresh`。
  - 从最早变化时间开始的 `indicator_upserts`。
  - `market_version`、`structure_version`、`structure_changed`。
  - 仅在收口导致正式结构版本变化时返回 `structure_update`。
- 请求接受 `known_market_version` 和 `known_structure_version`；版本未变化时不返回重复结构。
- 5 分钟在边界后 15 秒、30 分钟在边界后 15 秒、日线在 15:00:15 才确认入库并重算正式结构；普通 15 秒刷新不得调用完整 `preview_period()`。
- 结构更新通过新旧正式快照比较，计算最早受影响时间 `replace_from`；只返回该时间之后新增、变化或删除的结构节点，避免传输完整历史修订。
- 正式结构快照和指标序列按 `symbol + timeframe + adjustflag + market_version` 做进程内缓存；K 线确认入库或结构版本变化时失效。
- 非诊断响应排除 `processed_bars`、`fractals`、完整候选修订、证明修订和笔诊断；这些只在 `diagnostics=true` 或按节点详情请求时返回。
- 增加 GZip 响应压缩，但以减少计算和原始 JSON 体积为主，压缩只作为补充。

### 2. 前端改为增量合并

- 首次加载仍获取最近 300 根及分页游标；实时轮询改用 `/api/chart-realtime/{symbol}`。
- 按 `trade_date` upsert K 线和指标，不再整体替换 `bars`；保留已加载的历史、`has_more` 和 `next_before`。
- `structure_version` 未变化时保持笔、中枢、组件等对象引用不变；收到 `structure_update` 时，从 `replace_from` 起删除旧节点并合并新节点。
- 将实时行情状态与正式结构状态分离，避免报价变化触发全部结构归一化和 React 大对象重建。
- ECharts 普通实时更新只更新 K 线、指标和报价系列，使用合并更新与 `lazyUpdate`；结构、布局、主题变化时才执行完整 option 构建。
- 保持当前 dataZoom 范围，实时刷新不得打断拖动、缩放或重置视口；同一动画帧内的重复更新合并执行。

### 3. 可观测性与兼容

- 为接口增加 `Server-Timing`，分别记录行情源、SQLite、指标、结构、序列化耗时，并记录未压缩响应字节数。
- 保留原 `/api/chart-data` 契约供首次加载和旧调用方使用；新实时接口为增量新增，不需要数据库迁移。
- 用开关控制前端启用增量刷新，出现问题时可回退旧接口；正式验证后删除旧的全量实时分支。
- 排查并修复当前 30 分钟 `refresh=true` 的 HTTP 500，纳入发布阻断测试。

## Test Plan

- 后端单测：
  - 普通 15 秒刷新不调用结构全量分析。
  - 相同报价返回空或最小 `bar_upserts`。
  - 5 分钟、30 分钟及日线收口后仅重算一次正式结构。
  - `known_structure_version` 相同时不返回结构。
  - `diagnostics=false` 不泄漏大体积审计数组。
  - 腾讯失败时继续返回缓存数据并标记 `stale`。
  - 30 分钟实时刷新稳定返回 200。
- 前端单测：
  - 增量 K 线合并不会删除历史分页数据。
  - 结构版本未变化时保持结构对象引用。
  - `structure_update` 能正确删除失效节点并合并新节点。
  - 实时更新保留 `has_more`、`next_before` 和 dataZoom。
- 集成与浏览器验证：
  - 日线、5 分钟、30 分钟分别验证首次加载、15 秒刷新、周期收口、午休、收盘和切换周期。
  - 连续拖动和缩放时触发实时更新，确认视口不跳动、手势不卡顿。
  - 对比优化前后 K 线、指标、正式笔和中枢结果，正式结构不得发生语义变化。
- 性能验收：
  - 普通实时增量响应未压缩体积不超过 `100KB`。
  - 收口结构更新和首次 300 根图表响应不超过 `1MB`。
  - 本地参考标的 `1A0001`：普通实时接口 p95 不超过 `200ms`，收口刷新不超过 `1s`。
  - 拖动、缩放期间不出现超过 `50ms` 的实时刷新主线程长任务。
  - 日线不得再出现当前约 `14MB / 3s` 的周期性请求。

## Assumptions

- 形成中的 K 线和报价保持约 15 秒刷新。
- 笔、中枢只在对应 K 线收口后更新，不要求随每次报价跳动实时重算。
- 不修改缠论计算规则、正式结构语义和 SQLite 已有数据。
- 历史分页、绘图、指标选择和当前复权参数行为保持兼容。
