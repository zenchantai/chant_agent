# 笔中枢与非同级别分解结构引擎全量重构

## Summary

以 `docs/requirements/2026-09-18-pen-center-non-same-level-chan-engine-requirements.md` 为唯一需求基线，彻底替换当前 v23/v24 中枢、走势和买卖点实现，不保留旧结构语义、旧数据库结构或兼容字段。

执行第一步将本计划原文完整保存至 `docs/plans/2026-09-18-pen-center-non-same-level-engine-rebuild.md`；若文件存在则递增序号，回读确认完整后才修改业务代码。

已确定：

- 保留标准笔算法、行情同步、自选分组、普通绘图、实时行情和历史分页功能。
- 废弃当前 `hierarchy.py`/`non_same_level.py` 中所有 v23/v24 结构算法及其测试语义。
- 数据库保留行情与用户配置，删除全部旧结构、快照、覆盖、同步历史、人工结构修订和旧分析数据。
- 新持久化采用规范关系表加证据 JSON。
- 行情同步后预计算 `5/30/d/w/m`，图表发现快照过期时按需补算。
- 背驰采用结构强度向量优先，MACD仅作辅助证据。
- 不提供旧 API 字段、旧角色名称或数据库迁移兼容层。

## Implementation Changes

### 1. 重建结构领域模型和计算流水线

- 保留 `app/engine.py` 的包含、分型和标准笔逻辑；新结构引擎从确认笔开始，按连续行情区间和序列独立计算。
- 使用不可变领域对象：`PenUnit`、`CenterFreeComponent`、`CenterFamily/CenterRevision`、`MovementFamily/MovementRevision`、`PointFamily/PointRevision`、`StructureRelation`、`StructureIssue`。
- 单级状态机固定为：
  ```text
  走势边界
  → 非空进入段
  → 最早合法三单位核心
  → 固定核心
  → 延伸 / 暂时离开返回 / 离开 / 回试
  → 同级新生或高级扩张
  → 买卖点
  → 提交走势边界
  ```
- 正式核心必须位于进入段之后；全局三笔滑窗、核心单位复用和传递重叠吸收全部删除。
- 核心形成后固定 `ZD/ZG`。与核心同向的后续 Z 单位严格重叠时记为延伸；中间反向单位记为外围。离开后返回核心则撤销离开候选并继续原中枢。
- 新生中枢必须拥有独立进入段和三单位核心，且核心及完整波动系统均与前中枢分离。
- 扩张只允许发生在两个连续、完整、同级中枢系统之间：核心分离但 `[DD,GG]` 重叠。升级沿用较早中枢的 `center_family_id`，新增高级修订并把后中枢作为组成证据；后续结构不得仅凭与升级后包络重叠继续吸收。
- 同一区域的扩张路径与递归路径指向同一高级结构时合并为一个活动修订，记录多条 `formation_modes`。
- 活动中枢升级时，尚未完成的所属走势同步产生高级修订；已确认低级走势不删除，作为高级结构子走势保留。
- L2 以上只能消费连续、已确认、分类明确且 `recursive_eligible=true` 的低级走势，严禁再次直接消费笔；递归上限 L8。
- 无合法解释时输出 `candidate/provisional/undetermined/truncated`，不得通过兜底盘整、固定数量或数据结束强制确认。

### 2. 重写买卖点与走势边界

- 第一类点只允许出现在至少两个同级中枢构成的趋势中；单中枢力度减弱只能产生盘整背驰点。
- `b/c` 强度按以下确定顺序比较：
  1. `max_internal_center_level`；
  2. `confirmed_child_movement_count`；
  3. 当前两项相同时，要求 `c.amplitude <= b.amplitude + epsilon` 且 `c.average_slope < b.average_slope - epsilon` 才判结构减弱；
  4. 指标一强一弱时保持候选。
- MACD面积和峰值写入辅助证据：与结构减弱一致时提高确认置信度；缺失或中性不阻止确认；明确显示增强时点位保持候选，不提交正式趋势边界。
- 第一类点的真实极值为 `point_date/point_price`，后续反向低级走势完成时间为 `confirmed_at`；确认后结束前一趋势。
- 第二类点必须依赖已确认一类点，第一次完整回撤不得破坏一类点极值；二类点不默认切分走势。
- 第三类点必须由完整离开组件和第一次完整回试组件构成；回试进入核心则候选失效。三类点确认中枢破坏，不机械结束更高级走势。
- 相邻走势只共享一个真实价格端点，不共享整笔、组件或低级走势；任何边界不得切开中枢核心。
- 一个中枢分类为 `consolidation`；两个以上完整系统满足 `后DD > 前GG` 为上涨、`后GG < 前DD` 为下跌；核心分离但外围重叠进入高级扩张，不判同级趋势。
- 已确认前缀冻结；追加行情只重算最后一个确认边界之后的活动后缀。

### 3. 全量重建结构数据库

保留并原样迁移以下表及数据：

```text
market_bars
trade_calendar
security_catalog
stock_pool
watchlist_groups
watchlist_group_members
watchlist_section_order
drawing_objects
```

删除旧的结构、分析及运行数据，包括：

```text
period_*
active_period_structure_runs
structure_overrides
structure_override_events
analyses
journals
sync_runs
market_data_conflicts
market_coverage
market_gap_tasks
```

新建以下结构域表：

```text
chan_structure_runs
chan_active_runs
chan_processed_bars
chan_fractals
chan_pens
chan_components
chan_component_units
chan_center_families
chan_center_revisions
chan_center_units
chan_movement_families
chan_movement_revisions
chan_movement_units
chan_movement_centers
chan_point_families
chan_point_revisions
chan_relations
chan_issues
```

约束要求：

- 所有结构明细必须归属 `run_id`，删除运行时级联删除。
- family 表保存稳定身份和当前修订；revision 表只追加、不可原地改写。
- 单位归属表保存 `role` 与 `ordinal`，并建立同级核心唯一归属约束。
- 简单可查询字段规范化为列；形成证据、力度比较、失效原因和诊断信息存入 `evidence_json`。
- 每个标的、周期、复权方式只有一个活动运行。
- 新运行先完整写入 staging run，通过全部不变量后在单事务内切换活动指针；随后删除旧成功运行及其结构明细，仅保留当前活动运行和最近失败元数据。
- 版本固定为：
  ```text
  definition_version = chan-period-pen-recursive-non-same-level-v25
  decomposition_mode = non_same_level
  base_unit_mode = current_period_confirmed_pen
  center_selection_mode = entry_then_earliest_three_unit_core
  center_promotion_mode = evidence_based_family_revision
  movement_boundary_mode = structural_buy_sell_point
  divergence_mode = structural_strength_vector
  ```

### 4. 服务、API与前端整体替换

- 保持 `/api/chart-data/{symbol}` 路径，但删除旧扁平兼容字段、`pen_centers` 别名、`role=hierarchy_component`、`confirmation_center_id` 等旧语义。
- 图表响应改为明确分组：
  ```text
  meta
  market
  structure.pens
  structure.components
  structure.centers
  structure.center_revisions
  structure.movements
  structure.movement_revisions
  structure.points
  structure.point_revisions
  structure.relations
  structure.issues
  indicators
  drawings
  pagination
  ```
- `structure_level` 选择当前显示层级；默认 L1。`diagnostics=true` 时才返回失效候选、历史修订和详细问题。
- 删除全部 `/api/structure-overrides/*` 路由、请求模型、存储方法和前端编辑入口；普通 `/api/drawings/*` 保留。
- `/api/health` 返回新版本、计算模式、实际最高级别和活动运行状态，不再返回 v24 模式。
- 前端删除万能 `Node` 类型，改为判别联合类型 `Pen/Component/Center/Movement/StructuralPoint`，不保留旧字段容错。
- 默认每个中枢族只画活动修订；矩形使用活动修订核心，选择后分别高亮进入、核心、延伸、外围、离开和回试。
- `confirmed/provisional/undetermined/truncated` 使用明确视觉差异；买卖点区分一、二、三类及盘整背驰，并显示点位时间与确认时间。
- 保留当前未提交的历史分页视野修复、实时行情、自选分组和绘图改动；结构相关 v23/v24 改动全部替换。
- 同步服务在行情写入成功后预计算启用标的 `5/30/d/w/m`；图表请求仅在活动快照缺失或指纹、行情版本过期时补算。

### 5. 数据切换与运行流程

- 停止服务及后台同步，确认无进程持有 SQLite WAL。
- 对现有 `chant_agent.db`、`-wal`、`-shm` 生成一次一致性迁移备份并执行 `PRAGMA integrity_check`。
- 创建全新的 sidecar 数据库，初始化新表，只复制上述八张保留表；逐表校验行数、内容哈希、主键和外键。
- 从保留的 `market_bars` 重建覆盖信息，并对启用标的的 `5/30/d/w/m` 生成新结构活动运行。
- 全量审计通过后原子替换正式数据库文件；启动服务后再次检查数据库完整性、健康接口和活动运行。
- 完成 API 与浏览器验收后删除旧数据库及旧结构数据备份；验收前保留一次回滚副本。
- 不执行旧表到新结构表的内容转换，不引用任何旧中枢、走势、买卖点或人工覆盖记录。

## Test Plan

- **核心示例**：覆盖需求文档中的 `1,10,6,15,8...`、`1,5,3,10,7...`、`20,14,18,13,16,8`，验证进入段、最早核心、固定 `[ZD,ZG]` 和无滑窗重复。
- **状态机**：覆盖严格重叠、单点接触、延伸、离开返回、三买三卖、新生、扩张、禁止传递吸收和禁止九笔自动升级。
- **走势与买卖点**：覆盖盘整、上涨、下跌、盘背、一二三类点、结构向量强弱、MACD一致/缺失/冲突、点位与确认时间分离。
- **递归与不变量**：验证 L2/L3 只消费已确认低级走势、源单位唯一归属、边界不切中枢、父子证据完整、确认前缀稳定。
- **持久化**：验证 staging 写入失败不切换活动运行、成功后原子激活、旧运行清理、关系约束和证据 JSON 可回读。
- **数据迁移**：证明八张保留表迁移前后行数和内容哈希一致，旧结构表不存在，新结构表完整，SQLite `integrity_check=ok`。
- **真实行情**：科创50日线不得再形成覆盖 2020—2026 的单一巨型中枢；上证指数重叠结构必须正确区分延伸、新生和扩张；争议日期只由通用证据决定。
- **接口与前端**：验证新响应结构、分页上下文、层级筛选、诊断模式、候选/确认样式、结构点击、普通绘图和历史拖动。
- **完整检查**：运行 `pytest -q`、`npm test -- --run`、`npm run build`、`git diff --check`、重算脚本、结构审计、API 实测和真实浏览器交互。

## Assumptions

- 标准笔规则保持不变；如果新测试发现笔本身错误，单独记录，不在本轮顺带修改。
- 当前周期 L1/L2/L3 仅是该周期内部结构层级，不映射为真实其他时间周期。
- 普通绘图属于用户配置并保留；人工结构修订功能和数据永久删除。
- 旧结构历史、旧 API 客户端和旧数据库查询均不兼容，也不提供迁移适配。
- 当前错误 v23/v24 代码可直接删除或重写，只有与结构无关且已验证的工作区改动需要保留。
- 无法由当前数据唯一确认的结构宁可保持候选或待定，不为了图形连续性补造中枢、走势或买卖点。
