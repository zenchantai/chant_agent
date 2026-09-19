# 5分钟与30分钟当日行情实时刷新方案

计划文档目标路径：`/Users/saber/Desktop/saber/投资/缠论/chant_agent/docs/plans/2026-09-17-live-5m-30m-refresh.md`

## Summary

在现有 `chant_agent` 中增加 5分钟、30分钟周期的当日准实时刷新能力：

- 只刷新当前打开的股票和当前图表周期，不后台轮询整个股票池。
- 交易时段约每15秒从腾讯行情获取最新 `m5` / `m30` 数据。
- 最后一根尚未收口的 K 线实时显示，并参与实时结构预览。
- 实时预览结构不写入正式结构表；周期边界后15秒确认 K 线完成，再生成正式结构快照。
- 保留现有历史数据、缠论编辑、绘图、缩放、悬浮和副图设置。
- `1分钟` 分时刷新行为保持兼容，不改变现有午休折叠和241坐标轴逻辑。

当前代码和数据基础：

- `TIMEFRAMES` 已包含 `"5"`、`"30"`。
- 数据库已有5分钟、30分钟历史行情。
- 腾讯接口已实测支持 `m5`、`m30`。
- 当前 `/api/chart-data` 的 `refresh=true` 仅允许 `timeframe=1`，需要扩展到 `5`、`30`。
- 当前定时同步主要在收盘后进行，不能满足盘中实时刷新。

## Backend implementation

### 1. 扩展实时行情服务

在现有 [app/intraday.py](/Users/saber/Desktop/saber/投资/缠论/chant_agent/app/intraday.py) 中将实时刷新逻辑参数化，保留现有1分钟行为：

- 定义实时周期配置：

  - `1分钟`：周期长度1分钟，当前日快照模式，只保留最新交易日。
  - `5分钟`：周期长度5分钟，保留全部历史，只更新当前交易日。
  - `30分钟`：周期长度30分钟，保留全部历史，只更新当前交易日。

- 新增通用方法：

  - `refresh_period(symbol, timeframe, adjustflag)`
  - `validate_period_rows(rows, timeframe, now)`
  - `replace_current_day_period(symbol, timeframe, adjustflag, rows)`
  - `period_session_state(...)`
  - `period_metadata(...)`

- 每个 `(symbol, timeframe, adjustflag)` 使用独立锁和15秒去重窗口，避免浏览器刷新、同步任务、重复请求同时访问腾讯接口。
- 保留晚到响应拒绝、失败缓存、原子写入和交易日历校验。
- 5分钟、30分钟刷新只删除或替换当前交易日对应周期的数据，不删除前一交易日和历史数据。
- 旧数据源与实时腾讯数据发生同一时间戳冲突时，实时路径允许腾讯更新当前未收口 K 线，并继续记录 `market_data_conflicts`。

### 2. 腾讯行情获取和数据质量

在 [app/providers.py](/Users/saber/Desktop/saber/投资/缠论/chant_agent/app/providers.py) 中补充实时周期能力：

- `m5` 映射到应用周期 `"5"`。
- `m30` 映射到应用周期 `"30"`。
- 实时请求使用腾讯最新一批数据，过滤到当前交易日和当前服务器时间之前。
- 解析为统一字段：

  - `trade_date`
  - `open`
  - `high`
  - `low`
  - `close`
  - `volume`
  - `amount`
  - `source`
  - `snapshot_id` 或 `source_revision`

- 如果腾讯未返回有效数据：

  - 不清空已有缓存；
  - 返回失败状态；
  - 前端保留上次有效曲线；
  - 历史完整同步仍使用现有 BaoStock/其他配置源。

- 调整数据源优先级逻辑，确保实时腾讯数据可以更新当前未收口 K 线，同时不批量覆盖无关历史数据。

### 3. 正在形成 K 线的定义

服务端根据交易日历、交易时段和周期边界计算 K 线状态：

- 5分钟边界：`09:35、09:40...11:30`，午后从 `13:05` 起继续。
- 30分钟边界：沿用现有30分钟网格，例如 `10:00、10:30、11:00、11:30、13:30...`。
- 当前时间尚未超过该周期边界加15秒时，最后一根标记为：

  - `is_forming: true`
  - `status: "provisional"`

- 周期边界后15秒重新获取并确认后：

  - `is_forming: false`
  - `status: "confirmed"`
  - 触发正式结构重算和正式快照更新。

- 午休期间不制造虚假的未来 K 线，不把午休空白当作缺失数据；只保留真实返回的上午数据，13:00后的新周期按现有时间网格处理。

### 4. 实时结构预览与正式结构

在 [app/period_structure.py](/Users/saber/Desktop/saber/投资/缠论/chant_agent/app/period_structure.py) 增加纯计算预览入口：

- 新增 `preview_period(...)` 或等价纯函数，直接基于“历史已缓存数据 + 当日实时数据”计算结构。
- 预览计算可以包含正在形成的最后一根 K 线，满足“结构也实时变化”。
- 预览结果不调用 `replace_period_structure`，不写入：

  - `period_structure_runs`
  - `period_pens`
  - `period_pen_centers`
  - `period_movements`

- 预览响应单独携带：

  - `structure_preview: true`
  - `structure_persisted: false`
  - `preview_structure_version`
  - `structure_as_of`
  - `forming_bar_trade_date`

- 周期边界后15秒确认后，调用现有正式结构计算和存储流程：

  - 更新正式 `market_version`；
  - 更新 `coverage_version`；
  - 生成正式 `structure_version`；
  - 将当前结构状态从预览切换为正式。

- 正式结构仍遵守现有标准笔、反向独立中枢确认、覆盖校验和计算器指纹规则。

### 5. 扩展图表接口

修改 [app/main.py](/Users/saber/Desktop/saber/投资/缠论/chant_agent/app/main.py:426) 的 `/api/chart-data/{symbol}`：

- 允许以下组合：

  - `timeframe=5&refresh=true`
  - `timeframe=30&refresh=true`

- 仍拒绝：

  - `refresh=true` 搭配 `before`
  - 不支持的周期
  - 非当前最新图表的实时刷新请求

- 普通请求不刷新行情，只读取缓存。
- `refresh=true` 的处理顺序：

  1. 获取当前交易日腾讯数据；
  2. 校验并更新缓存；
  3. 判断最后一根是否正在形成；
  4. 必要时生成内存结构预览；
  5. 若已达到边界后15秒，则生成正式结构；
  6. 返回完整图表数据。

- 扩展响应字段：

```json
{
  "symbol": "1A0001",
  "timeframe": "5",
  "bars": [],
  "forming_bar": {
    "trade_date": "2026-09-17 11:25:00",
    "is_forming": true,
    "status": "provisional"
  },
  "intraday_refresh": {
    "phase": "trading",
    "market_status": "交易中",
    "server_time": "2026-09-17T11:25:15+08:00",
    "latest_data_at": "2026-09-17 11:25:00",
    "next_transition_at": "...",
    "next_bar_finalize_at": "...",
    "result": "success",
    "is_today": true
  },
  "structure_preview": true,
  "structure_persisted": false,
  "preview_structure_version": "...",
  "structure_as_of": "2026-09-17 11:25:15+08:00",
  "forming_bar_trade_date": "2026-09-17 11:25:00",
  "structure_version": "..."
}
```

字段含义：

- `structure_version`：最后一个正式结构快照版本。
- `preview_structure_version`：当前实时预览版本。
- `structure_preview`：当前响应是否包含实时预览。
- `structure_persisted`：本次结构是否已正式落库。
- `forming_bar`：正在形成的最后一根 K 线；没有时返回 `null`。

### 6. 同步服务边界

修改 [app/sync.py](/Users/saber/Desktop/saber/投资/缠论/chant_agent/app/sync.py) 时保持职责清晰：

- 不新增全股票池的15秒后台轮询。
- 现有收盘后批量同步继续保留，用于历史补齐和完整覆盖校验。
- 当前图表的盘中刷新由图表接口负责。
- 周期边界正式落库和收盘同步使用同一套结构计算器与版本校验，避免产生两套结构语义。

## Frontend implementation

### 1. 扩展实时刷新范围

修改 [web/src/intradayRefresh.ts](/Users/saber/Desktop/saber/投资/缠论/chant_agent/web/src/intradayRefresh.ts)：

- 将实时刷新适用周期从仅 `"1"` 扩展为 `"1" | "5" | "30"`。
- 交易时段默认每15秒刷新。
- 午休和收盘按服务端 `next_transition_at` 等待。
- 周期边界后15秒保证至少执行一次确认请求。
- 页面隐藏时取消请求，页面恢复时立即请求一次。
- 请求失败时不清空当前图表，下一次继续重试。
- 请求 generation、AbortController 和晚到响应保护沿用现有实现。

### 2. 扩展数据合并

修改 `mergeIntradayData`：

- 支持1分钟、5分钟、30分钟。
- 实时刷新只替换：

  - `bars`
  - `indicators`
  - `quote`
  - `intraday_refresh`
  - 实时结构字段
  - `forming_bar`

- 保留：

  - 当前缩放范围；
  - hover 状态；
  - 副图数量和指标设置；
  - pane 比例；
  - 未提交绘图；
  - 绘图版本；
  - 手工结构编辑状态；
  - 不属于本次实时响应的历史结构数据。

- 跨交易日时整体替换当前数据，禁止把前一交易日和新交易日混合。

### 3. 图表和提示标识

修改 [web/src/App.tsx](/Users/saber/Desktop/saber/投资/缠论/chant_agent/web/src/App.tsx)、[web/src/chartBuilders.ts](/Users/saber/Desktop/saber/投资/缠论/chant_agent/web/src/chartBuilders.ts) 和 [web/src/types.ts](/Users/saber/Desktop/saber/投资/缠论/chant_agent/web/src/types.ts)：

- `ChartData` 增加实时周期和预览结构字段。
- 5分钟、30分钟图表启动与1分钟相同的轮询生命周期。
- 主图状态栏显示：

  - `交易中 / 午间休市 / 已休市`
  - 最新数据时间
  - `实时预览` 或 `正式结构`
  - `最后一根未完成`

- K线悬浮提示中，对正在形成的 K 线增加“未完成”。
- 实时结构节点显示“未完成”或“实时预览”状态。
- 周期确认后自动移除该标识。
- 保持现有K线缩放、拖拽、结构点击、绘图和副图布局行为。
- 5分钟、30分钟不使用分时图的241点固定轴；继续使用普通K线时间轴和历史分页。

### 4. 加载流程

在 `load` 和实时轮询之间明确分工：

- 初始 `load(false)`：读取缓存，显示历史和当前已有数据。
- 初始数据加载完成后：

  - 若周期为1、5、30，启动实时刷新；
  - 其他周期不启动实时刷新。

- `before` 分页请求不携带 `refresh=true`。
- 当前图表切换股票或周期时：

  - 取消旧请求；
  - 停止旧轮询；
  - 清除旧的实时错误状态；
  - 重新加载并启动新周期轮询。

## Data and compatibility constraints

- 不新增必需的数据库迁移字段；实时状态和预览结构只在内存与 API 响应中传递。
- 保留现有 `market_bars`、`sync_runs`、`market_data_conflicts` 和正式结构表。
- 不删除任何历史行情、绘图、手工修订或股票池数据。
- 继续使用前复权 `adjustflag=2`。
- 继续使用 `Asia/Shanghai` 交易日历。
- 不将实时未完成 K 线写入正式结构快照。
- 不把当前预览结构误认为已确认结构。
- 收盘后正式同步仍负责最终覆盖和完整性校验。

## Test plan

### Backend unit tests

新增或扩展 `tests/test_intraday.py`、`tests/test_providers.py`、`tests/test_period_structure.py`：

- 腾讯 `m5`、`m30` 响应解析。
- 当前交易日过滤和未来分钟过滤。
- 5分钟、30分钟周期边界识别。
- 午休、收盘、周末和节假日状态。
- 当前未完成 K 线正确标记。
- 边界后15秒转为已完成。
- 同一时间戳实时数据可以更新旧缓存。
- 旧交易日数据不会被删除。
- 腾讯失败时保留缓存。
- 空响应、未来数据、负价格、非法OHLC 被拒绝。
- 同一 `(symbol,timeframe)` 并发请求只产生一次行情源调用。
- 实时预览包含形成中 K 线。
- 预览计算不会新增正式结构数据库记录。
- 周期确认后才更新正式结构快照。
- 正式结构版本、行情版本和覆盖版本一致。
- `refresh=true&before=...` 返回400。
- `timeframe=d&refresh=true` 继续返回400。
- 1分钟现有测试全部保持通过。

### Frontend tests

扩展 [web/src/intradayRefresh.test.ts](/Users/saber/Desktop/saber/投资/缠论/chant_agent/web/src/intradayRefresh.test.ts)：

- 5分钟交易时段每15秒刷新。
- 30分钟交易时段每15秒刷新。
- 周期边界后15秒确认请求。
- 午休后恢复。
- 收盘后暂停到下一交易日。
- 页面隐藏取消请求，恢复立即请求。
- 请求失败后保留旧数据。
- 形成中 K 线标识随响应变化。
- 实时结构字段合并但不覆盖未提交绘图和缩放状态。
- 跨日响应不会混合两个交易日。

### Build and runtime checks

- `pytest -q`
- 前端 Vitest 全量测试。
- `npm run build`
- 启动实际服务后检查：

  - `/api/health`
  - `/api/chart-data/{symbol}?timeframe=5&refresh=true`
  - `/api/chart-data/{symbol}?timeframe=30&refresh=true`

- 运行时核验：

  - 实际监听端口和进程归属；
  - 5分钟最新时间戳；
  - 30分钟最新时间戳；
  - `source` 是否为实时腾讯；
  - `forming_bar` 和 `structure_preview` 是否符合当前时间；
  - 正式结构表是否只在收口后更新。

### Browser acceptance

使用真实浏览器检查桌面和移动宽度：

- 打开5分钟图，确认约15秒更新一次。
- 打开30分钟图，确认约15秒更新一次。
- 最后一根K线显示“未完成”。
- 拖动、缩放、悬浮后刷新，视图状态不跳回默认。
- 未提交绘图在实时刷新后仍存在。
- 切换股票和周期后旧轮询停止。
- 午休、收盘状态显示正确。
- 周期确认后“未完成/实时预览”标识消失。
- 检查桌面与390px移动布局无页面级横向溢出。

## Assumptions

- 实时行情源继续使用腾讯 `m5`、`m30`；BaoStock 保持历史和收盘同步用途。
- “实时”定义为交易时段约15秒轮询，不承诺交易所逐笔级实时。
- 当前图表范围是实时刷新范围，不扩展为整个股票池后台实时刷新。
- 用户已选择“未完成 K 线参与结构实时变化”，因此预览结构允许随每次行情更新变化。
- 用户已选择“预览不落库”，因此形成中结构不写正式结构表；K线边界后15秒确认后才正式落库。
- 当前标准笔 v19、反向独立中枢确认和各周期独立计算规则保持不变。
- 若腾讯接口在非交易时段返回上一交易日数据，服务端保留缓存并将 `is_today=false`，不伪造当日数据。
