# 删除同级别分解并重构为正式层级唯一结构

计划文档目标路径：

`/Users/saber/Desktop/saber/投资/缠论/docs/plans/2026-09-13-remove-same-level-decomposition.md`

## Summary

彻底删除“同级诊断”和“同级别分解”功能，工程只保留正式层级结构：

```text
K线 -> 分型 -> 笔 -> 正式 L1 笔中枢
   -> hierarchy_component 正式走势
   -> L2/L3/... 正式层级中枢
```

正式层级递归仍然保留，但不再通过同级分解算法生成证据节点、操作走势或 `decomposition` 元数据。

接口统一为：

```text
GET /api/chart-data/{symbol}
```

旧 `/api/v2/chart-data/{symbol}` 删除，不保留兼容壳。接口只返回正式结构。

## Backend Refactor

- 删除 `app/decomposition.py` 及其 `build_movements`、`DECOMPOSITION_VERSION` 等实现。
- 从 `app/hierarchy.py` 删除：
  - `decompose_same_level`；
  - `_mechanical_centers`；
  - `same_level_centers`；
  - `decompositions`；
  - 所有 `same_level` 和 `same_level_decomposition` 角色处理。
- 将正式层级内部的 `decompose_hierarchy_level` 重命名为正式语义的 `build_hierarchy_components`，包括 split helper、调用方和测试。
- 重写 `build_hierarchy`：
  - 仅以正式 L1 中枢作为递归起点；
  - 只生成 `hierarchy_component` 走势；
  - L2/L3 中枢只由正式下级走势递归生成；
  - 返回的 `centers` 只允许 `role=hierarchy`；
  - 返回的 `movements` 只允许 `role=hierarchy_component`；
  - 不返回 `same_level_centers`、`decompositions`、`decomposition`、`movement_input_hash`、`hierarchy_input_hash`、阶段 `unassigned_*` 和阶段 issues 元数据。
- 保留结构缓存所必需的通用字段：
  - `definition_version`；
  - `calculator_fingerprint`；
  - `structure_version`；
  - `center_level_counts`；
  - `movement_level_counts`；
  - `max_confirmed_center_level`；
  - `max_available_center_level`。
- `PeriodStructureService` 只维护正式结构：
  - 删除 `chart_page_v2`；
  - `chart_page` 改为正式结构唯一序列化入口；
  - 删除所有诊断集合、诊断元数据和 role 分流；
  - 移除 `decomposition` 兼容读取；
  - 正式走势分页只按 `hierarchy_component` 返回。
- `Store` 更新为只读写正式走势：
  - `period_movements` 表保留，因为它仍持久化正式层级走势；
  - 新写入的数据禁止 `same_level` 和 `same_level_decomposition`；
  - 不再创建、读取或写入 `decomposition_meta`、`movement_input_hash`、`hierarchy_input_hash`；
  - 删除相关 schema 自动补列逻辑。
- `/api/health` 删除 `same_level_decomposition` 状态字段，并改为返回正式结构版本信息。
- `/api/v2/chart-data/{symbol}` 路由删除；前端和测试全部切换到 `/api/chart-data/{symbol}`。
- 正式接口使用扁平正式结构响应，不再包含 `structure` / `decomposition` 双集合：
  - `pens`；
  - `segments`；
  - `centers`；
  - `center_relations`；
  - `movements`；
  - `center_levels`；
  - `movement_levels`；
  - 行情、指标、覆盖和通用版本字段。

## Database Migration

新增一次性迁移流程，执行前必须停止服务并备份：

```text
data/chant_agent.db
data/chant_agent.db-wal
data/chant_agent.db-shm
```

迁移在事务中完成：

- 从 `period_movements` 删除 payload 中 `role` 为 `same_level` 或 `same_level_decomposition` 的记录；
- 保留正式 `hierarchy_component` 走势记录；
- 重建 `period_structure_runs`，移除：
  - `decomposition_meta`；
  - `movement_input_hash`；
  - `hierarchy_input_hash`；
- 保留正式结构版本、覆盖版本、计算指纹、数量统计和活动快照关系；
- 校验所有剩余中枢和走势均为正式 role；
- 清理完成后，对所有启用股票和结构周期 `5`、`30`、`d`、`w`、`m` 执行全量正式结构重算；
- 重算完成后重新激活每个股票/周期的最新正式快照；
- 不删除正式结构稳定 ID 生成规则，确保同一输入下正式结构 ID 可复现。

## Frontend Refactor

- `ChartData`、`Node` 和相关类型删除：
  - `decomposition`；
  - `decomposition_view`；
  - `showDecomposition`；
  - `same_level`；
  - `same_level_decomposition`。
- `normalizeChartData` 只解析正式结构字段。
- 删除 `buildDecompositionMovementSeries`。
- `buildCenterAreas` 只处理正式中枢。
- `buildMovementSeries` 只处理 `hierarchy_component`。
- 删除同级诊断开关、诊断状态提示、诊断计数、诊断 localStorage key 和诊断 CSS。
- 主图和详情栏只显示：
  - 正式笔；
  - 正式中枢；
  - 正式走势；
  - 正式层级编号。
- 走势 tooltip 固定显示：
  - `结构来源：正式层级`；
  - 正式参考中枢；
  - 构成单位；
  - 构成笔。
- 删除旧混合编号迁移逻辑和诊断分页合并逻辑。
- 保留正式结构图层开关、各级别开关、手动画线、股票池、指标和详情面板。
- 更新 README、当前知识规则和运行说明，移除同级诊断功能描述；历史计划文档保留为历史记录，不参与运行时。

## Tests

后端：

- 删除 `tests/test_decomposition.py`；
- 删除所有针对 `decompose_same_level`、`same_level`、`same_level_decomposition` 的测试；
- 更新 hierarchy 测试，验证：
  - L1 正式笔中枢可以生成 `hierarchy_component`；
  - L2/L3 正式中枢只引用正式下级走势；
  - 正式 centers/movements 不含同级 role；
  - 返回结果不含 `decomposition` 及相关输入哈希字段；
  - 正式走势方向、边界、连续行情区间和 provisional 状态仍正确。
- API 测试验证：
  - `/api/chart-data/{symbol}` 返回正式结构；
  - `/api/v2/chart-data/{symbol}` 不再存在；
  - 响应不含 `decomposition`、`same_level` 或诊断字段；
  - 正式编号按 level 连续；
  - 当前上证指数日线只返回正式中枢和正式走势。
- 新增数据库迁移测试：
  - 同级 payload 被清除；
  - 正式 payload 被保留；
  - 删除后的列不存在；
  - 全量重算后活动快照可正常读取。
- 更新 `scripts/audit_structure.py` 和 `scripts/recalculate_structure.py`，审计与重算只检查正式 role。

前端：

- 删除诊断开关和诊断状态相关测试；
- 验证默认图层只绘制正式结构；
- 验证正式走势和正式中枢 tooltip 不含诊断语义；
- 验证无 `decomposition_view` 时仍能完整绘制；
- 验证分页、层级开关、点击选择和移动端布局不受影响。

## Verification

执行：

```bash
cd /Users/saber/Desktop/saber/投资/缠论/chant_agent
uv run --with-requirements requirements.txt --with pytest python -m pytest -q

cd web
npm test -- --run
npm run build
```

迁移和重算后检查：

```text
GET /api/chart-data/1A0001?timeframe=d&limit=300
```

必须满足：

- HTTP `200`；
- 不存在 `decomposition` 字段；
- centers 全部为 `role=hierarchy`；
- movements 全部为 `role=hierarchy_component`；
- 数据库中不存在同级诊断 payload；
- 页面不再出现“同级诊断”入口；
- 正式 L1/L2/L3 结构仍可显示、点击和查看详情；
- 服务启动后无需同级诊断代码即可正常加载所有启用股票和周期。

## Assumptions

- “完整删除”解释为删除运行时算法、接口字段、前端功能、测试和当前规则文档中的同级诊断语义。
- 正式 `hierarchy_component` 不是同级诊断，必须保留并改用正式结构命名。
- `period_movements` 保留为正式走势持久化表，不改名，以减少历史审计和数据库迁移风险。
- 旧 `/api/v2/chart-data/{symbol}` 直接删除，不提供 410 或兼容响应。
- 历史计划文档作为项目变更记录保留，不再被代码、接口或前端引用。
- 迁移是破坏性的，执行前备份数据库；迁移完成后立即全量重算正式结构。
