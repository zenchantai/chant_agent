# 同级中枢候选唯一选择与所有权边界统一修正执行计划

## 摘要

针对科创50周线 `2024-02-08` 至 `2026-09-11` 中“笔正确、中枢未能新生”的问题，统一修正 L1 中枢候选选择、旧中枢生命周期、后继核心扫描和同级所有权规则。

本计划基于当前未提交的 v30 工作树继续实施，不回退、不覆盖已有 v29/v30 文档和实现。

正式版本升级为：

```text
definition_version = chan-period-candidate-ownership-boundary-v31
hierarchy_version  = center-hierarchy-v31-candidate-ownership
```

执行阶段第一步将本计划正文逐字保存到：

```text
/Users/saber/Desktop/saber/投资/缠论/chant_agent/docs/plans/2026-09-21-center-candidate-selection-ownership-boundary-execution.md
```

若文件已存在，依次使用 `-2`、`-3` 顺延；保存后立即回读，确认文件非空、章节完整、内容未被摘要化。

## 一、统一规则

### 1. 三类对象分离

同一周期、同一级别、同一连续行情流内，严格区分：

```text
CenterCandidate       几何和结构上可能成立的候选
CenterFamily          已唯一提交的正式中枢
ContextEvidence       进入、离开、回试、连接、边界等上下文证据
```

候选不是中枢，上下文证据也不是中枢。

正式拥有角色只有：

```text
core
extension
peripheral
```

以下角色只能作为上下文引用：

```text
entry
departure
retest
connection
boundary_evidence
```

`context_unit_ids` 可以与其他结构引用重叠，但 `owned_unit_ids` 不得在同一级别、同一连续流内重复。

### 2. 同级所有权账本

建立逻辑所有权账本：

```text
(level, stream_id, direct_unit_id)
    -> owner_family_id
    -> owner_role
    -> committed_at
```

不变量：

1. 同级同一连续流中，一个直接单位最多只有一个正式拥有者。
2. L1 直接拥有笔；L2 直接拥有 `SegmentProof`；L3 以上直接拥有已确认的低级走势。
3. 高级结构只引用低级结构，不改变低级结构所有权。
4. 已提交拥有权不能被后续滑动窗口转移给另一个同级中枢。
5. `departure/retest/connection` 单位在事件确认前不属于旧中枢或新中枢。
6. 回试返回旧核心并被判定为延伸时，相关单位才转为旧中枢的 `extension/peripheral`。
7. 独立后继核心提交后，其源单位归后继中枢所有，但仍可作为旧中枢的离开或边界上下文。

核心原则：

```text
上下文引用可以重叠；
同级正式拥有权不能重叠。
```

### 3. 候选资格门槛

三笔或三段只有同时满足以下条件，才进入“可提交候选”：

1. 所有源单位已确认；
2. 位于同一连续流、序列和级别；
3. 起止日期及价格端点连续衔接；
4. 方向交替；
5. 方向与当前走势上下文一致；
6. 严格共同重叠：

```text
ZD = max(low1, low2, low3)
ZG = min(high1, high2, high3)
ZD + EPSILON < ZG
```

7. 不跨越已确认走势边界、数据断点或待定隔离区；
8. 不使用其他正式同级中枢已经提交的拥有单位；
9. 有合法进入或连接上下文；
10. 所有证据的 `available_at` 不晚于当前观察时点；
11. 不存在更早成立且优先级更高的旧中枢回试或边界事件。

允许使用旧中枢最后一个拥有单位作为边界上下文，但不能将该单位再次作为新中枢源单位。

因此：

```text
#20–#22
```

即使几何交集成立，只要 `#20` 已属于旧中枢，就必须以：

```text
segment_ownership_conflict
```

淘汰，而不是把它当作正式新中枢。

### 4. 候选唯一选择顺序

对所有通过资格门槛的候选，使用确定性排序：

```text
1. evidence_available_at 升序
2. core_end_ordinal 升序
3. core_start_ordinal 升序
4. source_unit_count 升序
5. stable_candidate_id 升序
```

方向匹配属于资格门槛，不作为“方向更像”的模糊评分。

候选一旦提交为 `CenterFamily`：

- 后续数据只能追加新修订；
- 可以追加延伸、离开、回试和关闭证据；
- 不得被后续更长或更漂亮的滑动窗口替换；
- 已确认前缀的候选选择和所有权保持稳定。

所有未选候选必须保留结构化淘汰原因，包括：

```text
direction_mismatch
sequence_discontinuity
common_overlap_empty
ownership_conflict
entry_context_missing
future_evidence
prior_boundary_won
overlaps_selected_center
```

### 5. 旧中枢生命周期和后继核心仲裁

每个确认笔进入后，按以下顺序处理：

```text
更新旧中枢回试/延伸状态
→ 更新未拥有连续后缀
→ 枚举后继核心候选
→ 生成全部可用事件证据
→ 按证据时间仲裁
→ 提交唯一事件
→ 更新旧中枢状态及中枢关系
```

事件规则：

| 首先成立的证据 | 旧中枢状态 | 新单位归属 |
|---|---|---|
| 返回核心内部 | 延伸/继续 | 转为旧中枢拥有单位 |
| 核心外完整回试 | `broken` | 保留上下文，不归旧中枢拥有 |
| 独立后继核心 | 关闭当前中枢 | 后继核心取得拥有权 |
| 仅有几何窗口但冲突 | 保持当前状态 | 候选淘汰 |
| 只触边或核心仍重叠 | 候选/延伸 | 不提交独立新生 |

若多个事件具有相同 `available_at`，固定优先级为：

```text
1. 已确认旧中枢回试/延伸证据
2. 已确认外部边界证据
3. 独立后继核心证据
4. 其他候选解释
```

独立后继核心可以关闭旧中枢，不要求先生成第三类买卖点，但必须满足：

```text
核心严格分离
不重复旧中枢拥有单位
方向上下文一致
连接和边界证据完整
没有更早的回试事件吸收这些单位
```

### 6. 新生、延伸和扩张分类

新核心相对于旧核心：

```text
核心分离，外围也分离
    -> newborn

核心分离，外围区间重叠
    -> expansion_candidate

核心仍重叠
    -> extension/retest_candidate

只有单点接触
    -> boundary_candidate
```

不得使用区间重叠的传递闭包：

```text
A 与 B 重叠
B 与 C 重叠
不能推导 A、B、C 属于一个中枢
```

每个新候选都必须重新验证进入关系、核心关系、所有权和生命周期边界。
## 二、代码实现改造

### 1. 重构 `build_level_centers()` 状态机

重点修改：

- 将“旧中枢离开/回试”和“未拥有后缀扫描”拆成两个并行状态；
- 删除固定只检查离开尾部第一对单位的逻辑；
- 从旧中枢最后拥有单位之后开始扫描新的候选核心；
- 允许旧中枢最后拥有单位作为边界证据，但禁止新中枢重复拥有；
- 在同一观察前缀内先生成全部事件，再按统一排序提交；
- `next_workspace` 不得因为第一对离开/回试失败而直接终止后继核心扫描；
- 所有选择和淘汰写入诊断结果。

### 2. 抽取统一候选证明器

新增统一候选证明流程：

```text
enumerate_center_candidates()
validate_candidate_geometry()
validate_candidate_context()
validate_candidate_ownership()
select_canonical_candidate()
commit_center_family()
```

L1 使用：

```text
source_kind = pen
```

L2 使用：

```text
source_kind = segment_proof
```

L3 以上使用：

```text
source_kind = confirmed_recursive_movement
```

同一套方向、连续性、重叠、时间和所有权校验适用于所有层级，但不同层级不共享直接拥有权。

### 3. 低级结构保留

后继中枢提交时：

- 旧中枢保留完整历史修订；
- 旧中枢不被删除或伪造为新家族；
- 旧中枢的 `departure/retest` 上下文继续可追溯；
- 新中枢使用独立 `family_id`；
- 仅通过 `successor_of`、`predecessor_of` 或 `center_relation` 表达关系。

禁止通过复用旧 `family_id` 或停用低级中枢实现升级。

## 三、契约、持久化和 API

### 1. 新增 `CenterCandidate`

新增只追加契约：

```text
id
family_id
revision_no
previous_revision_id
active
level
stream_id
source_kind
status
direction
start_date
end_date
start_price
end_price
zd
zg
source_unit_ids
context_unit_ids
entry_evidence_id
boundary_evidence_id
observed_at
evidence_available_at
selected_center_family_id
rejection_code
rejection_detail
```

`status`：

```text
geometric
eligible
selected
rejected
invalidated
```

新增运行级表：

```text
chan_center_candidates
```

旧运行不迁移、不重写；新表为空时保持读取兼容。

### 2. `Center` 字段补充

正式中枢补充或统一：

```text
ownership_scope
owned_unit_ids
context_unit_ids
ownership_commit_at
closure_reason
successor_center_id
predecessor_center_id
boundary_status
```

其中：

```text
owned_unit_ids
```

只能包含 `core/extension/peripheral`；

```text
context_unit_ids
```

可包含进入、离开、回试、连接和边界证据。

### 3. API `structure`

新增：

```text
center_candidates
center_candidate_revisions
ownership_audit
```

普通视图返回：

- 当前选中的正式中枢；
- 当前活动候选；
- 当前中枢的拥有单位和上下文单位。

诊断视图额外返回：

- 全部候选；
- 所有合法候选；
- 所有淘汰候选；
- 淘汰码和证据；
- 所有权冲突；
- 事件仲裁顺序。

`issues` 不用于伪造中枢或承载完整候选实体。

### 4. 规则和元数据

同步更新：

```text
knowledge/chan_rules.md
knowledge/chan_rules.yaml
```

新增规则 ID：

```text
CENTER-CANDIDATE-ELIGIBILITY-V31
CENTER-CANDIDATE-SELECTION-V31
CENTER-OWNERSHIP-LEDGER-V31
CENTER-CONTEXT-NONOWNERSHIP-V31
CENTER-LIFECYCLE-ARBITRATION-V31
CENTER-PREFIX-STABILITY-V31
```

健康接口和快照 `meta` 共用配置函数，输出：

```text
center_candidate_mode = canonical_eligible_candidate
center_ownership_mode = same_level_single_owner
center_context_mode = references_do_not_own
center_lifecycle_mode = timestamped_event_arbitration
center_prefix_mode = immutable_committed_prefix
```

周线、月线继续使用：

```text
calculation_profile = pen_centers_only
```

它们可以计算 L1 笔中枢候选和生命周期，但不得生成 L2 以上走势、点位、升级候选或递归结构。

## 四、前端和诊断展示

候选诊断面板显示：

- 候选编号和稳定 ID；
- 来源周期和级别；
- 源笔或段；
- 几何区间；
- 方向；
- 进入/连接证据；
- 当前所有权；
- 选中或淘汰状态；
- 稳定淘汰码；
- 旧中枢关系。

图表显示：

- 正式中枢使用实线矩形；
- 候选使用虚线或低透明度矩形；
- 旧中枢离开/回试单位使用上下文颜色；
- 新中枢拥有笔使用正式中枢颜色；
- 同一笔被多个结构引用时，按“拥有者优先、上下文次之”显示；
- 诊断模式可查看所有滑动窗口，但普通模式不得叠加全部候选。
## 五、验收测试

### 1. 合成候选测试

覆盖：

- 最早合法核心选择；
- 几何有效但方向错误；
- 几何有效但所有权冲突；
- 核心共同区间为空；
- 单点接触；
- 进入段缺失；
- 序列断裂；
- 未来证据引用；
- 同观察时间多候选稳定排序；
- 后续追加数据不得替换已提交候选。

### 2. 科创50周线回归

使用周线 `2024-02-08` 至 `2026-09-11`：

```text
C0 = #18–#20
区间 = [640.35, 827.63]

#20–#22
淘汰：ownership_conflict

C1 = #21–#23
区间 = [931.00, 1080.57]

#22–#24
淘汰：direction_mismatch 或 overlaps_selected_center

C2 = #25–#27
区间 = [1273.49, 1575.45]
```

验收要求：

- 旧中枢不能继续吞掉 `#21–#29` 全部离开单位；
- `#21–#23` 能成为独立后继核心；
- `#20` 可作为旧中枢离开上下文，但不得同时成为 C1 拥有笔；
- C0、C1、C2 家族 ID 独立；
- 旧中枢历史修订完整保留；
- 所有未选窗口均有结构化淘汰原因；
- 页面矩形只显示正式中枢，不显示全部几何滑窗。

### 3. 日线回归

验证科创50日线：

- 现有合法中枢数量和核心区间不发生无证据变化；
- 原有离开/回试状态保持；
- 细粒度日线不能因为新增后继扫描而产生重复中枢；
- 日线前缀重放与全量观察时点结果同构。

### 4. 层级和周期测试

验证：

- L1 只拥有确认笔；
- L2 只引用 `SegmentProof`；
- L3 以上只消费确认且 `recursive_eligible=true` 的走势；
- 高级结构不能直接引用原始笔越级递归；
- 低级中枢保持活动和可追溯；
- 周/月不生成 L2 以上结构；
- 日线、周线和月线所有权账本互相隔离。

### 5. 全量验证

执行：

```text
后端全套测试
前端全套测试
生产构建
git diff --check
SQLite quick_check
SQLite foreign_key_check
结构审计
API 审计
前缀逐笔重放
同观察时点全量同构检查
启用证券 5/30/d/w/m 隔离重算
浏览器实测候选详情、正式中枢矩形、上下文高亮、分页和级别切换
```

验证 10 个隔离运行，并输出：

- v30/v31 中枢数差异；
- 新增后继中枢数量；
- 候选来源和淘汰原因；
- 所有权冲突数量；
- 旧中枢关闭时间；
- 性能差异；
- 前端显示差异；
- 周/月是否错误产生高级结构。

## 六、发布和回滚

- 实施前冻结当前 v30 工作树、源码指纹、隔离数据库和主库 WAL/SHM 三件套；
- 新版本只写新的 v31 隔离数据库；
- 不自动切换活动指针；
- 不重启 `8765`；
- 不提交、不推送；
- 只有隔离矩阵、结构/API 审计、科创50周线真实证据和浏览器验收全部通过后，才请求单独确认活动指针切换；
- 回滚只恢复匹配版本代码和旧活动指针；
- 本计划不修改确认笔、分型、包含关系、三类买卖点、背驰、训练模式和周/月高级结构策略。

## 七、明确假设

1. “独立后继核心可以关闭旧中枢”正式纳入规则，不要求第三类买卖点先确认。
2. 同级拥有权比上下文引用更严格；上下文引用允许重叠。
3. 已选候选的拥有权不可被后续滑动窗口替换。
4. `#21–#23` 是科创50周线第一个独立后继核心，`#25–#27` 是其后续独立核心；最终结构仍以完整实现和证据时间为准。
5. 周月仍只计算原生 L1 笔中枢，不参与 L2 以上递归。
