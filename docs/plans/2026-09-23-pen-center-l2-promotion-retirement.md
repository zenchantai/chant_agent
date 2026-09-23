# chant_agent 走势下线并保留 L1→L2 中枢晋级

## 1. 目标与边界

### 最终结构链路

```text
行情 → 分型 → 笔 → L1 中枢 → L1 延伸/扩展 → L2 中枢
```

### 保留

- 分型、笔及笔诊断。
- L1 中枢形成、延伸、修订、离开、回试。
- L1 中枢关系，包括 `expansion_up`、`expansion_down`。
- L1→L2 晋级。
- L1→L2 的 `promotion_candidate`、`segment_proof`、父子中枢关系和动态/固定边界证据。
- L2 中枢展示、详情、证据追溯。
- 行情、用户手工绘图、组件和审计问题。

### 删除或停止

- `Movement` 走势及其所有计算、持久化和前端展示。
- 基于走势的 L2→L3 及以上递归。
- 结构买卖点 `Point`。
- 依赖买卖点结束边界的走势逻辑。
- 走势相关的 `owner_movement_id`、`child_movement_ids` 等字段。
- Agent 分析中的走势和买卖点输入。

### 周期边界

- `5`、`30`、`d`：保留 L1→L2 晋级。
- `w`、`m`：继续保持当前参考周期边界，只计算笔和 L1 中枢，不生成 L2 晋级。
- 所有周期都不生成走势和结构买卖点。

## 2. 结构引擎改造

### 新计算模式

在 `app/period_structure.py` 中废弃 `full` 作为正式模式，新增或改名为明确的结构配置：

```text
pen_centers_l2
pen_centers_only
```

配置语义：

```text
pen_centers_l2:
  L1 中枢
  L1 中枢延伸/扩展
  L1→L2 晋级
  L2 展示与证据
  无走势
  无买卖点
  不继续 L2→L3

pen_centers_only:
  笔
  L1 中枢
  L1 中枢延伸/扩展
  无 L2 晋级
  无走势
  无买卖点
```

周期映射：

```text
5/30/d → pen_centers_l2
w/m    → pen_centers_only
```

同时更新：

- `PERIOD_DEFINITION_VERSION`
- `HIERARCHY_VERSION`
- 计算指纹文件集合
- `structure_mode_metadata()`
- `max_level` 语义

新快照必须明确记录：

```text
movement_partition_mode = disabled
movement_boundary_mode = disabled
divergence_mode = disabled
center_promotion_mode = unified_segment_proof 或 disabled
promotion_segment_mode = pen_native_segment_proof 或 disabled
max_level = 2 或 1
```

### `build_structure_hierarchy()` 重构

在 `app/chan_structure.py` 中将结构流程改为：

1. 使用原始笔构造 L1 中枢。
2. 保留 L1 中枢关系和中枢生命周期。
3. 对 `pen_centers_l2`：
   - 只使用笔原生证据调用 L1→L2 晋级；
   - 生成 `promotion_candidate_revisions`；
   - 生成 `segment_proof_revisions`；
   - 提交动态或固定的 L2 中枢；
   - 生成 `promoted_into` 父子关系。
4. 对 `pen_centers_only`：
   - 不执行 L1→L2 晋级。
5. L2 生成后立即停止，不把 L2 中枢作为下一层输入。
6. 禁止调用 `movement_units()`。
7. 禁止调用 `build_movements()`。
8. 禁止调用 `build_structural_points()`。
9. 禁止生成 `points`、`movements`、L3/L4 中枢。

保留 `pending_promoted` 的一次性 L2 提交逻辑，但删除面向任意级别的递归循环。

### L1→L2 证据改造

当前 `build_promotion_candidates()` 同时接受走势和买卖点作为输入，需要拆出 L1 专用路径：

- L1→L2 只允许使用：
  - L1 笔；
  - L1 中枢；
  - L1 中枢组件；
  - 笔的连续性；
  - 笔级核心见证；
  - L1 中枢扩展/扩张证据。
- `movements=[]`，不允许回退到走势证据。
- `points=[]`，不使用结构买卖点作为晋级完成条件。
- `_local_segment_proofs()` 和 `_find_local_completion()` 作为 L1→L2 的唯一完成路径。
- `build_parent_center_proofs(child_level=1)` 必须拒绝走势来源证明。
- `source_kind` 固定为 `local_pen_group`。
- L1→L2 的动态/固定状态、证据时间和边界保持现有语义。

保留：

```text
promotion_candidate_revisions
segment_proof_revisions
center.decomposition_proof
relation_type = promoted_into
center.child_center_ids
```

删除：

```text
movement_revision_id
movement_family_id
boundary_certificate_id
point_revision_id
```

其中只有属于走势或买卖点的字段删除，L1→L2 所需的笔原生证据字段继续保留。

## 3. 后端 API 与数据结构

### API 保留字段

正式结构响应保留：

```text
processed_bars
fractals
pens
components
centers
center_revisions
center_candidates
center_candidate_revisions
promotion_candidates
promotion_candidate_revisions
segment_proofs
segment_proof_revisions
relations
issues
levels
unassigned_by_level
```

### API 删除字段

正式响应不再返回：

```text
movements
movement_revisions
movement_levels
points
point_revisions
```

响应约束：

- `5/30/d` 的 `levels` 最大为 `[1, 2]`。
- `w/m` 的 `levels` 最大为 `[1]`。
- 所有周期的 `movements` 和 `points` 不再生成。
- L2 中枢可以通过 `centers`、`center_revisions`、`relations`、`promotion_candidates` 和 `segment_proofs` 完整追溯。

### 后端模块

重点修改：

```text
app/period_structure.py
app/chan_structure.py
app/chan_expansion.py
app/structure_display.py
app/store.py
app/agent.py
scripts/audit_structure.py
scripts/recalculate_structure.py
```

`structure_display.py` 改为只追踪：

```text
center → child_center
center → promotion_candidate
center → segment_proof
center → component
component → pen
```

删除通过走势递归寻找中心和源笔的闭包逻辑。

## 4. SQLite 存储改造

### 保留表

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
chan_center_candidates
chan_relations
chan_issues
chan_promotion_candidates
chan_segment_proofs
market_bars
drawing_objects
```

### 删除表

```text
chan_movement_centers
chan_movement_units
chan_movement_revisions
chan_movement_families
chan_point_revisions
chan_point_families
```

### 删除字段

重建 `chan_center_revisions`，移除：

```text
owner_movement_id
```

删除或重建所有走势/买卖点专属字段：

```text
chan_point_revisions.movement_family_id
```

保留 L1→L2 所需的中心、候选、段证明字段。

### Store 行为

在 `app/store.py` 中：

- 停止创建走势表和买卖点表。
- 停止插入、读取 `_insert_movements()`、`_insert_points()`。
- 保留 `_insert_promotion_candidates()`。
- 保留 `_insert_segment_proofs()`。
- `load_chan_structure()` 不再读取 movement/point 表。
- `replace_chan_structure()` 只写入笔、中枢、L1→L2 证据和关系。
- 新结构运行不写入任何走势或买卖点 JSON。

## 5. 前端与 Agent 调整

### 前端保留

- 笔图层。
- 中枢图层。
- L1/L2 级别切换。
- L2 中枢的动态/固定状态。
- 晋级证据、段证明、父子中枢关系。
- 中枢延伸和扩展详情。
- 用户手工绘制线段。

### 前端删除

修改：

```text
web/src/types.ts
web/src/chartBuilders.ts
web/src/App.tsx
```

删除：

- `Movement` 类型。
- `movements`、`movement_revisions`、`movement_levels`。
- 走势图层和走势 L1/L2 图例。
- 走势端点标记。
- 走势线段和走势 hit-test。
- 走势 tooltip、选择详情和颜色。
- 依赖走势的 localStorage 状态迁移。

保留：

- `PromotionCandidate`。
- `SegmentProof`。
- 中枢级别颜色。
- L2 晋级证据展示。

页面文案改为：

```text
笔 · 活动中枢 · 组成中枢
点击笔、中枢或 L2 晋级证据查看规则详情
```

### Agent

`app/agent.py`：

- 删除走势和买卖点上下文。
- 保留笔、中枢、L1→L2 晋级候选、中枢扩展和证据。
- 提示词明确禁止自行创造走势、买卖点、背驰和高级中枢。
- Agent 只能解释已持久化证据。

## 6. 数据迁移与发布流程

新增：

```text
scripts/migrate_pen_center_l2.py
```

参数：

```text
--db PATH
--backup-dir PATH
--dry-run
--execute
```

### 迁移前

1. 停止服务和所有 SQLite 写入进程。
2. 一起备份：
   ```text
   chant_agent.db
   chant_agent.db-wal
   chant_agent.db-shm
   ```
3. 执行：
   ```text
   PRAGMA quick_check
   PRAGMA foreign_key_check
   ```
4. 记录迁移前活动矩阵和数量：
   - 行情；
   - 笔；
   - L1/L2 中枢；
   - 走势；
   - 买卖点；
   - promotion candidates；
   - segment proofs；
   - drawing objects。

### Staging 重算

在隔离数据库执行：

```text
5/30/d → pen_centers_l2
w/m    → pen_centers_only
```

验证：

- `5/30/d` 允许 L1 和 L2。
- `w/m` 只有 L1。
- 所有周期 `movements = 0`。
- 所有周期 `points = 0`。
- `5/30/d` 的 L2 只能来源于 L1 的 `local_pen_group` 证据。
- 不存在 `L3`。
- 所有 L2 中枢具有有效 `decomposition_proof` 或明确未完成状态。
- 扩张关系的证据时间和边界状态可复现。
- 新运行的 `definition_version` 和 `calculator_fingerprint` 正确。

### 生产切换

1. 新运行全部成功后写入生产数据库。
2. 校验每个标的/周期存在唯一新活动运行。
3. 原子切换 `chan_active_runs`。
4. 通过 API 重新读取所有周期。
5. 验证前端只显示笔、中枢和 L2 晋级证据。
6. 确认用户绘图未改变。
7. 删除旧运行及其派生数据。
8. 删除走势和买卖点表。
9. 重建 `chan_center_revisions`，移除 `owner_movement_id`。
10. 再次执行数据库完整性检查。

不删除：

```text
market_bars
drawing_objects
data/v28-release-20260919/
data/v29-candidate-20260920/
迁移前备份
审计报告
```

## 7. 测试计划

### 引擎测试

新增或改写测试：

- 少于三笔时不生成中枢。
- L1 中枢正常形成。
- L1 中枢延伸产生正确 revision。
- L1 中枢扩展产生 `expansion_up` / `expansion_down`。
- L1→L2 候选可以从笔原生证据生成。
- L1→L2 动态候选可以转为固定 L2。
- L2 中枢保留 `decomposition_proof`。
- L2 中枢保留 `promoted_into` 关系。
- L2 不再作为 L3 输入。
- 不生成走势。
- 不生成结构买卖点。
- 不使用走势或买卖点完成 L1→L2。
- 同一输入重复计算结果稳定。

重点覆盖：

```text
tests/test_structure.py
tests/test_directional_centers.py
tests/test_expansion_decomposition.py
tests/test_z_wave_expansion.py
tests/test_hierarchy.py
tests/test_hierarchy_boundaries.py
tests/test_api.py
tests/test_structure_audit.py
```

### 周期测试

明确验证：

```text
5/30/d:
  levels <= [1, 2]
  可以有 promotion_candidates
  可以有 segment_proofs
  不存在 movements/points

w/m:
  levels <= [1]
  不生成 promotion_candidates
  不生成 segment_proofs
  不存在 movements/points
```

### 存储迁移测试

验证：

```text
PRAGMA quick_check = ok
PRAGMA foreign_key_check 无结果
chan_movement_* 表不存在
chan_point_* 表不存在
chan_center_revisions 不包含 owner_movement_id
所有活动运行 max_level <= 2
所有活动运行无 movements/points
drawing_objects 内容未变化
market_bars 行数和日期范围未变化
```

### 前端与运行测试

执行：

```text
pytest -q
npm --prefix web test
npm --prefix web run build
```

启动隔离服务后检查：

```text
/api/health
5
30
d
w
m
```

浏览器验收：

- 5/30/d 能看到 L1 和可用的 L2 中枢。
- L2 中枢详情能查看晋级来源。
- 中枢延伸、扩展证据正常显示。
- 周/月没有 L2 晋级。
- 页面不再出现走势图层、走势数量和走势详情。
- 手工绘制线段仍存在。

## 8. 回滚与完成标准

### 回滚条件

出现以下任一情况立即停止删除：

- `quick_check` 失败。
- `foreign_key_check` 有结果。
- L1 中枢数量或生命周期异常。
- L1→L2 晋级证据无法重现。
- L2 中枢缺少父子关系或证明。
- API 仍返回旧走势。
- 前端构建或运行失败。
- 手工绘图发生变化。
- 活动运行切换不完整。

恢复时必须同时恢复：

```text
chant_agent.db
chant_agent.db-wal
chant_agent.db-shm
```

### 完成标准

只有以下条件全部满足才算完成：

1. 5/30/d 保留笔、L1 中枢、L1 延伸/扩展和 L1→L2 晋级。
2. w/m 保持笔和 L1 中枢参考模式。
3. L2 不再继续生成 L3。
4. 走势和结构买卖点彻底停止生成、读取和展示。
5. L1→L2 的候选、段证明、父子关系和证据仍完整。
6. 走势表、买卖点表和走势字段清理完成。
7. 行情和用户绘图数据未被删除。
8. 后端测试、前端测试、构建、数据库校验和 API 检查全部通过。
9. 最终报告列出实际删除、保留、验证和未验证内容。

## 9. 计划文件

执行阶段第一步保存本计划全文至：

```text
/Users/saber/Desktop/saber/投资/缠论/chant_agent/docs/plans/2026-09-23-pen-center-l2-promotion-retirement.md
```

如果目标文件已经存在，改用带序号的新文件名。保存后立即回读，确认文件非空、章节完整，且 L1→L2、API、数据库迁移、测试和回滚内容没有丢失。计划文件核验通过后，才能修改业务代码。
