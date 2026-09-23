# 笔中枢延伸与扩展统一升级 v29 执行计划

## 审查结论

原方案方向正确，但不能直接实施，需修正以下问题：

- “九个延伸单位”与“九个拥有单位”表述冲突。统一为：从首个核心单位起计算 `core + extension + peripheral`，排除 `entry/departure/retest`；首次九单位前缀触发检查，不保证升级。
- `decomposition_proofs()` 与 `internal_decomposition_proofs()` 仍会形成两套走势分区，违反原子单位唯一性。改为先建立唯一规范走势流，父中枢只消费该流。
- 当前扩展升级复用子中枢 `family_id`、把 L1/L2 写入同一家族，并停用低级子中枢；这会破坏家族级别稳定性。父级必须使用独立家族，低级结构继续保留。
- 当前高级中枢使用 `unit_kind=center_revision`、空 `core_unit_ids`，与 `U(L>1)=低一级走势` 冲突。L2 以上核心必须直接引用三个规范 `MovementRevision`。
- `_promote_open_movements()` 会改变走势家族级别，且生成的修订没有进入下一轮 `movement_units()`，实际上没有接通递归。该路径应删除并重构层级循环。
- `candidate/dynamic/fixed` 与现有中枢生命周期、非空 `ZD/ZG` 约束混在一起。候选不是中枢；动态/固定使用独立 `boundary_status`。
- `/api/health` 仍返回旧模式常量，与规则书和快照 `meta` 不一致，必须改为共享元数据来源。

当前证据基线：

- 当前代码：`aab50c1`，工作树干净，源码指纹 `c37effc7…`。
- 活动 10 个 v28 运行指纹为 `e294e06a…`，与当前源码不一致；现有审计因此为 `0/10`，不可直接作为当前代码基线。
- 上证日线 `#12` 为 19 个拥有单位：3 核心、8 延伸、8 外围；第九个拥有单位 `R0-pen-104-2020-12-02-2020-12-11` 于 `2020-12-15` 可用。
- 当前代码仍只得到 L1、2 个已确认走势和 1 个长期 `undetermined` 走势。
- 当前基线检查：后端 `274 passed`，前端 `177 passed`，生产构建通过并保留现有大包警告，SQLite `quick_check=ok`。

计划文件目标为 `/Users/saber/Desktop/saber/投资/缠论/chant_agent/docs/plans/2026-09-20-unified-pen-center-promotion-execution.md`。执行阶段第一步逐字保存本计划正文并回读核验；若文件已存在则使用 `-2`、`-3` 顺延。原方案保留为历史文档，本计划作为替代实施基线。

## 实现改造

1. 将正式版本提升为 `chan-period-unified-center-promotion-v29`，层级版本改为 `center-hierarchy-v29-unified-promotion`，同步规则 Markdown、YAML、强制规则 ID、计算指纹和健康元数据。
2. 抽取统一 `BoundaryCertificate -> SegmentProof -> MovementRevision` 流程。局部路径只能使用截至前缀已确认的 `PointRevision` 或引用该点的既有走势终点，不新增依赖父中枢反向成立的边界。
3. 对每个连续流统一枚举走势候选，校验至少三个单位、独立同级中枢证据、方向、连续性、确认时间和无重复所有权，再按证据时间、起点、边界、单位数、稳定 ID 选择唯一非重叠规范走势流。
4. 删除全局走势优先、局部分段兜底的双轨选择；`decomposition_proofs()` 和 `internal_decomposition_proofs()` 合并为规范分段与父级证明模块。
5. 延伸候选在首次达到九个拥有单位时建立稳定候选家族：前九单位永久记录为 `required_unit_ids`，后续拥有单位只扩展 `search_unit_ids`，不得回写首次触发时间。
6. 扩展候选在扩展接触确认时冻结前中枢、连接和后中枢当时修订；后续数据只能追加候选修订。
7. 父级证明只消费三个连续规范走势：前两段必须确认，第三段至少三个单位；第三段未确认生成 `boundary_status=dynamic`，确认后追加 `fixed` 修订。
8. 父中枢家族 ID 由父级别、连续流和三个走势家族确定，不复用任何子中枢家族。相同三段从延伸、扩展或常规递归进入时合并为一个父家族并累计 `formation_modes`。
9. 所有 L2 以上中枢统一使用 `unit_kind=movement`，`core_unit_ids` 引用三个走势修订；低级子中枢保持活动和可追溯，只增加 `promoted_into` 关系。
10. 重排层级循环：合并本级常规核心与上一层提交的固定父核心后，再计算本级点位和走势；动态父中枢仅展示和审计。删除 `_promote_open_movements()`，只有已确认且 `recursive_eligible=true` 的 `MovementRevision` 进入下一层。
11. 中枢 `status` 继续表达 `formed/extended/broken`；候选独立存储。动态中枢保存当前 `zd/zg`，`fixed_zd/fixed_zg=null`；固定修订才写入固定核心和 `promotion_confirmed_at`。

## 接口与持久化

- 新增 `PromotionCandidate` 契约，包含稳定家族/修订 ID、来源、子/父级别、`required_unit_ids`、`search_unit_ids`、`observed_at`、`evidence_available_at`、结构化 `missing_evidence`、证明 ID 和选中父家族。
- API `structure` 新增 `promotion_candidates` 和 `promotion_candidate_revisions`；普通视图返回当前候选，诊断视图返回完整修订和未选证明。
- 新增只追加的 `chan_promotion_candidates` 运行级表；旧运行不迁移、不重写，新表为空即可兼容。候选不得塞入 `issues`，也不得伪造成具有价格几何的中枢。
- `Center` 前端类型将 `fixed_zd/fixed_zg` 改为可空，并补齐 `boundary_status`、父级证明和候选来源类型；图表按三段底层笔高亮动态/固定父中枢。
- 页面分别展示延伸候选、已确认扩展但边界未解、动态父中枢和固定父中枢，并显示稳定缺失码，不只显示“未升级”。
- 健康接口和快照 `meta` 共用同一配置生成函数，固定输出 `movement_partition_mode=canonical_boundary_stream`、`center_promotion_mode=unified_candidate_proof`、`nine_unit_mode=owned_units_from_core`。

## 测试与验收

- 增加九单位触发、进入/离开排除、非机械 `3+3+3`、方向错误、序列断裂、共同重叠失败、多解排序、所有权冲突和无未来引用测试。
- 同一笔流分别经延伸与扩展入口时，断言规范走势、父家族、动态/固定时间和几何一致，仅来源证据不同。
- 验证动态修订固定字段为空且不能进入走势递归；固定父核心可参与本级走势，但只有最终确认走势可进入更高一级。
- 固化上证日线 `#12`：必须在 `2020-12-15` 首次产生延伸候选；逐笔输出所有合法/淘汰划分及缺失证据。是否最终形成 L2 不预设，由独立边界和统一证明决定。
- 覆盖父子不同家族、低级中心不被停用、同级活动单位不重复、父级核心引用均可解析，以及前缀重放与同观察时点全量结果同构。
- 执行后端全套、前端全套、生产构建、SQLite `quick_check`/`foreign_key_check`、结构审计和 API 审计。
- 在隔离数据库对启用证券的 `5/30/d/w/m` 完成 10 项重算；周/月继续保持 `pen_centers_only`，不得出现候选、升级、走势或点位。
- 浏览器实际检查候选面板、动态/固定矩形、三段高亮、级别切换、分页、选择和周月日线 L2 投影，不能以测试或构建代替 UI 验证。

## 发布与假设

- 实施前分别冻结活动 `e294e06a…` 快照和当前 `c37effc7…` 代码基线；先用当前 HEAD 在独立数据库生成可比基线，不重启或覆盖活动运行。
- 新运行使用新定义版本和新指纹写入隔离数据库，保留旧运行及 SQLite 主库、WAL、SHM 三件套。
- 输出新旧中枢数、走势数、最高级别、候选来源、动态/固定时间、未解决原因和性能差异；异常不得通过证券、日期或页面编号特判。
- 只有隔离矩阵、结构审计、真实行情证据和浏览器验收全部通过后，才进入单独的活动指针切换确认；回滚只恢复匹配代码版本和旧活动指针。
- 本轮不修改确认笔、三类买卖点、背驰、训练模式或周/月计算策略。
