# 笔原生走势段统一升级 v30 执行计划

## 摘要

修正 v29 将父级证明错误收紧为“必须预先存在三个全局 `MovementRevision`”的问题，恢复项目既定简化语义：

- 一笔是一个 L0 次级走势单位。
- 至少三笔可以构成一个 L1 `SegmentProof`。
- 三个合法、连续、方向交替且共同重叠的 L1 `SegmentProof` 可以构成 L2 中枢。
- 全局 `MovementRevision` 可以复用为段证明，但不是 L1→L2 的准入条件。
- 九笔合法 `3+3+3` 首先生成动态 L2；第三段被后续反向合法段或已有结构点确认结束后，再追加固定修订。
- L3 以上继续只消费已确认、可递归的低一级全局走势，禁止原始笔越级递归。

正式版本升级为：

```text
definition_version = chan-period-pen-native-segment-promotion-v30
hierarchy_version  = center-hierarchy-v30-pen-native-segment
```

执行阶段第一步将本计划 Markdown 正文逐字保存到：

```text
/Users/saber/Desktop/saber/投资/缠论/chant_agent/docs/plans/2026-09-20-pen-native-segment-promotion-execution.md
```

若文件已存在则使用 `-2`、`-3` 顺延；保存后立即回读核验。保留原方案、v29 执行计划、当前未提交 v29 实现及验收报告，不覆盖或回退。

## 核心规则

### 1. 笔原生 SegmentProof

L1→L2 的局部段证明直接从候选连续笔流构造。每个 `SegmentProof` 必须：

- 至少包含三笔，全部已确认且位于同一连续范围和序列。
- 起止日期、价格严格首尾相接。
- 段方向由首笔起点与末笔终点确定；差值不超过 `EPSILON` 时淘汰。
- 内部至少存在一个连续三笔见证，满足方向交替和严格正宽度共同重叠。
- 保存段价格包络、底层笔、局部中枢见证、可用时间和完成证据。
- 同一父级证明中的三段不得重复拥有笔。

局部中枢见证使用稳定 ID，只作为 `SegmentProof` 的级别证据，不额外创建或篡改全局 L1 中枢家族。

### 2. 三段枚举与选择

对延伸或扩展候选统一执行：

1. 从 `required_unit_ids` 的首笔开始，不允许使用更早的进入笔补位。
2. 枚举 `S1/S2/S3`，每段至少三笔，三段连续且完整覆盖冻结的必要单位。
3. 校验 `S1.direction == S3.direction != S2.direction`。
4. 计算每段完整价格包络：
   `low=min(unit.low)`、`high=max(unit.high)`。
5. 父核心为：

```text
ZD = max(S1.low, S2.low, S3.low)
ZG = min(S1.high, S2.high, S3.high)
ZD + EPSILON < ZG
```

6. 多解按以下顺序唯一选择：

```text
evidence_available_at
S1 结束位置
S2 结束位置
S3 结束位置
总单位数
稳定 proof_id
```

所有未选划分以结构化淘汰原因保存在诊断结果中。

### 3. 动态与固定边界

- 当 `S1/S2/S3` 均至少三笔且共同重叠成立时，生成动态父中枢。
- `S1` 的结束由完整合法 `S2` 证明，`S2` 的结束由完整合法 `S3` 证明，不要求全局买卖点。
- `S3` 初始为开放段；新增笔可以追加动态修订，但不得覆盖历史修订。
- 出现以下任一证据时固定 `S3`：
  - 从 `S3` 末端开始形成至少三笔、方向相反且含局部中枢见证的 `S4`；
  - 已有确认结构点精确落在 `S3` 末端。
- 固定修订沿用同一父家族；动态修订的 `fixed_zd/fixed_zg=null`，固定修订才写入固定核心和 `promotion_confirmed_at`。
- 动态父中枢不可递归；固定父中枢可以参与本级走势计算。

### 4. 层级边界

- L1 中枢直接单位仍是确认笔。
- L2 升级核心引用三个规范化 `SegmentProof`，不再强制引用三个预存全局走势。
- 全局走势通过适配器转换为 `SegmentProof(source_kind=movement)`；局部笔组转换为 `SegmentProof(source_kind=local_pen_group)`，二者进入同一校验器。
- L3 以上只允许由确认、分类明确且 `recursive_eligible=true` 的 L2+ `MovementRevision` 生成段证明。
- 局部笔段不得直接用于 L3 以上结构。

## 实现与接口

### 1. 证明器与层级循环

重构统一证明器，使其先生成规范 `SegmentProof`，再构造父级证明：

- 删除“没有三个全局走势就立即失败”的入口条件。
- 保留全局走势适配，但与局部笔段共用方向、连续性、重叠、所有权和时间校验。
- 为局部笔段生成稳定 family/revision ID；动态延长和固定使用同一家族的追加修订。
- 父家族 ID 使用父级别、连续流及三个段家族生成；延伸、扩展得到相同三段时合并父家族并累计 `formation_modes`。
- 父中枢使用 `unit_kind=segment_proof`，`core_unit_ids` 引用三个段修订，并新增 `child_segment_ids`；不再把局部段伪装成全局走势。
- 建立统一层级单位流：选中且固定的局部段在其覆盖范围内优先于重叠的全局走势适配单元；被替代的全局走势仍保留展示和审计，不参与该范围的高层所有权。
- 修复候选历史排序，同一时间的中心修订按 `revision_no` 排列，确保 `search_unit_ids` 只能保持或增长，不能出现 10 笔修订排在 9 笔之前。

### 2. 契约与持久化

新增只追加 `SegmentProof` 契约：

```text
id
family_id
revision_no
previous_revision_id
active
level
source_kind = local_pen_group | movement
status = provisional | confirmed
direction
start_date / end_date
start_price / end_price
low / high
source_unit_ids
source_pen_ids
center_witnesses
boundary_mode
completion_evidence_id
observed_at
evidence_available_at
recursive_eligible
```

API `structure` 新增：

```text
segment_proofs
segment_proof_revisions
```

普通视图返回当前选中段；诊断视图返回全部段修订、合法划分及淘汰划分。`PromotionCandidate` 增加：

```text
selected_segment_proof_ids
selected_parent_proof_id
```

缺失码改为可定位阶段的稳定代码，包括：

```text
nine_owned_units
three_segment_proofs_missing
segment_child_core_missing
segment_direction_mismatch
segment_continuity_failed
segment_ownership_conflict
segment_common_overlap_empty
third_segment_completion_missing
```

新增运行级只追加表 `chan_segment_proofs`；旧运行不迁移、不重写，新表为空即可兼容。候选和段证明继续与运行快照绑定。

健康接口和快照元数据共用配置，并输出：

```text
movement_partition_mode = canonical_boundary_stream
promotion_segment_mode = pen_native_segment_proof
center_promotion_mode = unified_segment_proof
nine_unit_mode = owned_units_from_core
```

### 3. 规则与前端

- 用 v30 规则替换 `MOVEMENT-CANONICAL-V29`：父级只消费规范 `SegmentProof`，但 L1→L2 允许笔原生局部段，全局 movement 不是必要条件。
- 保留九拥有单位口径：`core + extension + peripheral`；进入、离开、回试不计入延伸触发数。
- 候选面板显示选中的 S1/S2/S3、来源类型、底层笔、局部中枢见证、共同区间和固定所缺证据。
- 图表以三种颜色高亮 S1/S2/S3 底层笔；动态父中枢使用虚线，固定父中枢使用实线。
- 诊断视图展示所有合法与淘汰划分，不再只显示笼统的 `three_canonical_movements_missing`。

## 测试与验收

### 1. 合成用例

- 九笔示例必须识别：

```text
S1 = P1-P3，方向上，段区间 [100,125]
S2 = P4-P6，方向下，段区间 [108,125]
S3 = P7-P9，方向上，段区间 [108,130]
动态 L2 = [108,125]
```

- P9 确认后生成动态 L2；补充合法反向 `S4` 三笔后，追加固定修订。
- 覆盖三段方向错误、段内无严格三笔重叠、父级共同区间为空、序列断裂、单位重复、进入笔凑数和未来证据引用。
- 超过九笔时枚举全部合法边界并验证确定性排序。
- 没有任何全局 `MovementRevision` 时仍能升级；加入等价全局走势后，父家族、几何和时间不得变化。
- 同一笔流分别从延伸和扩展入口进入时，三段与父家族一致，仅来源证据不同。
- 候选修订的 `required_unit_ids` 永久冻结，`search_unit_ids` 单调扩展，同时间按原中心 `revision_no` 排序。

### 2. 上证日线回归

固定中枢 #16：

```text
S1 = pen-172..174，下，局部核心 [3227.3553,3509.8182]
S2 = pen-175..177，上，局部核心 [3346.4690,3418.9520]
S3 = pen-178..180，下，局部核心 [3140.9775,3418.9520]
父级动态核心 = [3227.3553,3439.0463]
```

验收要求：

- 第九拥有单位 `pen-180` 被中枢修订正式接纳时生成动态 L2；`observed_at` 取“该笔确认时间”和“接纳该笔的中心修订时间”中的较晚者，预计为 `2025-04-25`，不得提前到仅有笔确认但尚未证明中枢归属的 `2025-04-09`。
- `pen-181..183` 构成合法反向 S4 后固定 S3，预计固定证据时间为 `2025-05-16`。
- 固定后核心仍为前三段，不得用 `pen-181..186` 回写父核心。
- 页面可点击 L2 并高亮 `172–174 / 175–177 / 178–180`。
- 同时重新评估上证日线 #12，逐项输出合法和淘汰划分；不预设它一定升级，只按局部段、方向和共同重叠决定。

### 3. 全量验证

执行：

- 后端全套测试。
- 前端全套测试。
- 生产构建。
- `git diff --check`。
- SQLite `quick_check` 与 `foreign_key_check`。
- 结构审计和 API 审计。
- 前缀逐笔重放与同观察时点全量结果同构检查。
- 启用证券 `5/30/d/w/m` 的 10 项隔离重算。
- 周/月保持 `pen_centers_only`，不得产生段证明、升级候选、走势或点位。
- 浏览器实测候选详情、三段高亮、动态/固定矩形、选择、分页、级别切换及日线 L2 在周/月上的投影。

## 发布约束与假设

- 基于当前未提交 v29 工作树继续修改，不重置、不覆盖已有变更。
- 实施前冻结当前 v29 指纹、隔离运行和主库 v28 活动运行；全部新计算写入新的 v30 隔离数据库。
- 不修改确认笔、分型、包含关系、全局走势的交易语义、三类买卖点、背驰、训练模式或周/月策略。
- 不把任意九笔无条件升级；九笔只在三个局部段分别有合法三笔中枢见证、方向交替且父级共同重叠时形成动态 L2。
- 动态 L2 不进入递归；固定 L2 只有在审计通过并形成确认 L2 走势后，才可进入 L3。
- 输出 v29/v30 的中枢数、L2 数、段证明来源、动态/固定时间、缺失码、性能和页面差异。
- 只有隔离矩阵、结构/API 审计、#16 真实行情证据和浏览器验收全部通过后，才请求单独确认活动指针切换；不得在本轮实现中自动切换、重启 `8765`、提交或推送。
