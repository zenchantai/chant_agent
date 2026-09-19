# 迭代2：日线、周线、月线联动交付验收报告

- 验收日期：2026-09-19，Asia/Shanghai。
- 项目：`/Users/saber/Desktop/saber/投资/缠论/chant_agent`。
- 正式计划：`/Users/saber/Desktop/saber/投资/缠论/chant_agent/docs/plans/2026-09-19-daily-weekly-monthly-linkage.md`。执行前已完整保存并回读。
- 发布证据目录：`/Users/saber/Desktop/saber/投资/缠论/chant_agent/data/v27-release-20260919-185013`，下文证据文件均相对此目录。
- 当前结论：实现、自动化验证、staging 重算与审计、真实页面验收及正式服务切换均已完成；下文单独列明尚未进行的跨时观察与真实高级别样本验收。

## 1. 已完成的业务行为与接口

结构定义升级为 `chan-period-daily-led-reference-v27`。本次 staging 最终核验的计算器指纹为：

```text
c934c2bb26717be4c1c5ecaea31670cda89da888f39abd601bbd37c8e77826c3
```

| 周期 | `calculation_profile` | 计算与显示行为 |
| --- | --- | --- |
| 日线、5分钟、30分钟 | `full` | 保留完整构件计算和原有规则 |
| 周线、月线 | `pen_centers_only` | 原生仅有笔、L1 笔中枢及延伸；不计算扩张、升级、走势或买卖点；选中原生中枢仅高亮相关笔 |

`GET /api/chart-data/{symbol}` 保持原路径，增加 `meta.calculation_profile` 和独立的 `overlays.daily_l2`。周/月参考集合保留日线家族、修订、编号、活动/组成身份、原始日期和核心价格；投影不进入周/月原生结构集合，也不计入原生最高级别。

正式日线来源使用 `daily_bar_confirmations` 保存内容指纹和收盘确认证据。形成中日 K 的实时预览不进入日线 L2 投影；内容变化会使旧确认失效，不能仅因时间跨过收盘或跨日就把未证明完成的输入当成正式日线。周/月 `refresh=true` 尝试更新日线正式来源，普通读取与分页使用本地来源。接口区分 `ready`、`stale`、`unavailable`，并区分“有效来源暂无 L2”和“来源不可用”。本次样本来源截止日均为 **2026-09-18**。

前端周/月提供“笔”“笔中枢”“日线 L2 参考”，参考图层默认开启、独立保存开关。参考框采用日线 L2 配色，使用后端给出的 ISO 周/自然月目标 K 线映射；同一根目标 K 线内仍画完整单格宽度。点击检查器展示日线原始证据，不串联周/月源笔；隐藏图层和点击空白均清除对应选择。分页同时检查原生与日线来源版本；刷新同步替换投影、展示引用和来源元数据。

## 2. 自动化、构建与 staging 验证

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 后端测试 | **257 passed**，6 条弃用警告 | `pytest-final.log` |
| 前端测试 `npm test` | **163 passed**，10 个测试文件 | 本任务前端测试执行输出 |
| 生产构建 | TypeScript 与 Vite 构建成功 | `staged-dist/index.html`；JS 为 `index-CQXckd3P.js` |
| whitespace 检查 | 前端 `git diff --check -- src` 及仓库 `git diff --check` 通过 | 本任务执行输出 |
| 完整模式兼容比较 | **6/6 相同** | `full-profile-comparison.json` |
| staging 全周期重算 | **10/10 成功**，0 失败；数据库完整性 `ok` | `staging-recalculation-final.json` |
| staging 结构审计 | **10/10 通过**，0 失败 | `staging-audit-final.json` |
| staging API 审计 | **10/10 通过**，各项 `api_problems=[]` | `staging-api-audit.json` |
| staging 运行与数据保护检查 | 健康信息版本/指纹一致；保护表均未变 | `staging-runtime-final.json` |

生产构建命令为：

```sh
npm run build -- --outDir ../data/v27-release-20260919-185013/staged-dist
```

构建有大于 500 kB 的 chunk 提示，不影响构建成功。自动化增加了参考模式禁止调用高阶生成流程、日线完成确认、来源状态与快照一致性、分页版本、目标周期映射、单格宽度、同名对象隔离及遗留 payload 防御过滤等检查。前端包含 ECharts SVG 实际渲染测试，验证单格和跨缩放视窗的投影保留；这项测试不替代下面的浏览器验收。

完整模式兼容比较针对上证 `1A0001`、科创50 `1A0688`，各自的日线、5分钟、30分钟完整输入。比较仅排除有意变化的 `hierarchy_version` 元数据，构件内容的规范化 JSON 哈希全部相同，各组 `validation_errors=[]`。

| 股票 | 周期 | K线数 | 笔数 | 内容比较 |
| --- | --- | ---: | ---: | --- |
| 上证 | 日线 | 2848 | 207 | 相同 |
| 上证 | 5分钟 | 584 | 43 | 相同 |
| 上证 | 30分钟 | 364 | 27 | 相同 |
| 科创50 | 日线 | 1630 | 121 | 相同 |
| 科创50 | 5分钟 | 630 | 46 | 相同 |
| 科创50 | 30分钟 | 371 | 27 | 相同 |

staging 重算的 10 组为两只股票各自的 `5/30/d/w/m`。其中四组周/月原生最高级别全部为 L1，走势和买卖点数量均为 0。

## 3. 真实浏览器验收

以下结果来自根任务对 staging 真实页面的观察和实际点击，并与同源日线证据、API 分页结果交叉核对。表中价格为页面显示的两位小数。

| 股票 / 中枢 | 原始日线日期 | ZD / ZG | 已验证页面 |
| --- | --- | --- | --- |
| 上证日线 L2 #1 | 2015-02-09—2015-09-29 | 3049.11 / 4000.68 | 周线、月线参考投影，与日线来源一致 |
| 上证日线 L2 #8 | 2025-09-04—2026-05-14 | 3794.68 / 4025.70 | 日线、月线同源对照 |
| 科创50日线 L2 #5 | 2024-10-16—2025-10-09 | 1042.84 / 1140.37 | 日线、周线、月线同源对照 |

全历史分页核对：上证可展示日线 L2 **8 个**，科创50 **5 个**；三周期源修订、原始日期和核心价格一致。上证分页为日线 10 页、周线 2 页、月线 1 页；科创50为日线 6 页、周线 2 页、月线 1 页。分页及来源证据保存在 `staging-runtime-final.json`。

已完成以下真实交互验收：

- 周/月只出现允许的原生结构与三个图层控制；日线 L2 参考来源和截止日可见。
- 关闭日线参考图层后投影消失且清除选择，重新开启恢复投影；点击空白清除选择。
- 已选中的投影在与原生中枢重叠处保持优先；未选中时原生中枢优先。
- 明、暗主题切换后投影可见，来源与核心价格不变。
- 周线向左加载历史后能查看较早投影，历史日期与原始日线证据一致。

科创周线 L2 #5 与原生中枢 #3 的重叠区较窄。投影严格按几何矩形命中，原生中枢另有 8px 点击容差，因此框上沿附近可能先选到笔或原生中枢。只读代码核对确认：在投影几何范围内命中时，笔不会抢占投影；这不是投影与笔的排序错误。本轮保留既有命中容差，不将所有边缘像素视为已经完成独立验收。

## 4. 数据保护、备份与切换状态

保留了任务开始前已有的未提交修改。重算前保存源代码、原前端资源与数据库基线，使用备份副本进行 staging 重算和验收；切换前另保存 SQLite 主文件、WAL 和 SHM 三件套。

| 保护表 | 基线记录数 | staging / 正式切换后核对 |
| --- | ---: | --- |
| `market_bars` | 10222 | 未变 |
| `stock_pool` | 2 | 未变 |
| `watchlist_groups` | 2 | 未变 |
| `watchlist_group_members` | 2 | 未变 |
| `watchlist_section_order` | 4 | 未变 |
| `drawing_objects` | 1 | 未变 |
| `trade_calendar` | 4280 | 未变 |

保护表范围记录在 `protected-baseline.json`；`verify_runtime.py` 只读查询 `baseline.db` 与 staging 数据库，将各表全部行按同一规则排序后逐行比较，结果写入 `staging-runtime-final.json`，不只比较记录数。staging `integrity_check=ok`，新增日线确认元数据记录数为 4478。

相关备份及构建产物：

- `baseline.db`：重算前数据库基线。
- `pre-cutover.db`：停服后的 SQLite 一致性备份；`pre-cutover-raw/` 保存主文件、WAL 和 SHM。
- `previous-source/`：修改前源代码备份。
- `previous-dist/`：原正式前端资源备份。
- `pre-cutover-backup.json`：切换前 `chant_agent.db`、`chant_agent.db-wal`、`chant_agent.db-shm` 的大小及 SHA-256 清单。
- `staged-dist/`：本次验收使用的生产构建。
- `unpublished-dist/`：早期默认构建曾写入 `web/dist` 的新产物留档；已由根任务接管原资源恢复及后续正式切换。

正式切换已完成：停止原 `com.zenchant.chant-agent` 服务并保存上述备份，在正式库执行 v27 重算及离线审计，通过后安装 `staged-dist`，按原启动参数恢复同名 launchctl 服务。正式地址为 `http://127.0.0.1:8765`，核验时监听进程 PID 为 `45106`；未使用陈旧 PID 文件操作服务。隔离服务 `8766` 已停止。

| 正式运行检查 | 结果 | 证据 |
| --- | --- | --- |
| 全周期重算 | 10/10 成功、0 失败 | `production-recalculation.json` |
| 数据库结构审计 | 10/10 通过、完整性 `ok` | `production-db-audit.json` |
| 运行接口审计 | 10/10 通过，`api_problems=[]` | `production-api-audit.json` |
| 健康接口 | v27、`period_profiled`、上文指纹完全一致 | `production-runtime.json` 内的 `api.health` |
| 全历史投影核对 | 上证 8 个、科创50 5 个；来源版本、日期、价格与日线一致 | `production-runtime.json` |
| 受保护数据 | 上表七张表全部行与原始基线相同；4478 条日线确认元数据；完整性 `ok` | `production-protected-before-start.json`、`production-protected-final.json` |
| 正式浏览器冒烟 | 月线三个允许图层、8 个日线参考正常；点击上证 #8 得到原日期、3794.68 / 4025.70 和截止日 2026-09-18 | 本任务正式页面操作输出 |

`production-protected-final.json` 在正式页面冒烟之后再次核对，行情、自选、普通绘图及交易日历均未变化。旧前端同时保存在 `previous-dist/` 和 `cutover-previous-dist/`，现有未提交代码没有被重置；未执行 Git 提交或推送。

## 5. 证据边界与计划差异

- 验收日为周六，没有经历真实交易日的“盘中形成日 K → 收盘确认 → 来源刷新”跨时观察；相关路径目前由自动化验证覆盖。
- 组成 L2、同一目标 K 线内的单格矩形及部分边界情形使用自动化合成样本；不将其描述为全部已由真实行情浏览器验证。
- 当前真实样本最高级别为 L2，没有浏览器真实 L3+ 样本，也没有据此宣称完成 L3 升级后组成 L2 的真实样本验收。
- 测试、构建、API 审计与图表交互分别记录；测试通过不等于未执行的真实交互已经通过。
- 本轮未进入重新计划阶段，没有业务范围偏离。新增 `CHANT_AGENT_DB_PATH`、`CHANT_AGENT_WEB_DIST`、`CHANT_AGENT_BACKGROUND_SYNC` 环境配置用于隔离测试数据库、暂存前端资源和后台同步，属于执行支持。
- 仍采用当前分析工作台的单图周期切换；本轮不包含训练模式或三图同屏。
