# 无中枢次级别走势组件与一二三买卖点完整重构

## Summary

将当前结构链路升级为：

```text
当前周期 K 线
→ 当前周期笔
→ 无中枢次级别走势组件
→ 方向性中枢
→ 一/二/三买卖点
→ 正式走势
→ 递归生成高级组件、中枢、买卖点和走势
```

核心原则：

- 进入、核心、离开和回试中枢的对象统一为“无同级中枢的次级别走势组件”，可退化为一笔，也可包含三笔、五笔等奇数笔。
- 中枢方向由进入组件确定，形成后不因买卖点改变。
- 买卖点不修改中枢方向，只确认走势转折、走势边界和新走势方向。
- 三买/三卖可独立识别；二买/二卖必须依赖已确认一买/一卖。
- 正式一买/一卖采用当前周期 MACD 柱面积和 DIF 极值双重背驰确认。
- 正式一买/一卖、三买/三卖、反向独立中枢均可确认走势；按 `confirmed_at` 最早的有效证据锁定边界，后续证据只追加、不覆盖。
- 所有规则统一应用到 L1-L8；L1 输入为笔，L2+ 输入为已确认低一级走势。
- 日线内部 L2/L3 仍仅表示日线数据域内部层级，不映射为真实周线/月线。

执行阶段首先将本计划完整原文保存到：

`/Users/saber/Desktop/saber/投资/缠论/chant_agent/docs/plans/2026-09-17-center-free-components-buy-sell-points.md`

如文件已存在则使用递增序号；写入后立即回读校验，成功后才能修改业务代码。

## Implementation Changes

### 1. 引入无中枢次级别走势组件

新增正式结构对象 `center_free_component`，在每一级中枢计算前生成。

组件规则：

- 输入必须属于同一 `continuous_range_id`、`sequence_id` 和结构序列。
- 一个组件包含一单位，或三个及以上奇数个连续、方向交替的低级单位。
- 组件首尾单位方向一致，整体方向由组件起止价格决定。
- 向上组件的同向端点必须持续创新高，内部回调不得跌破组件起点；向下对称。
- 组件内部不得包含三个连续低级单位的严格重叠中枢种子。
- 从当前游标按两单位增量扩展，保留最长合法组件；遇到最早中枢种子、断点、方向失效或结构边界时立即结束。
- 组件之间不共享低级单位，只共享一个真实端点。
- 单点接触不构成内部中枢，也不构成严格离开。
- 组件保存完整真实价格包络，不用首尾连线代替区间。
- 候选组件不得进入中枢、高级递归或正式买卖点计算。

组件至少包含：

```text
id
kind=center_free_component
level
direction
status
start_date/start_price
end_date/end_price
low/high
source_unit_ids
source_pen_ids
continuous_range_id
sequence_id
structure_sequence_id
confirmed_at
```

### 2. 使用组件统一构造所有级别中枢

中枢统一采用：

```text
E + C1 + C2 + C3
```

其中 `E` 为进入组件，`C1/C2/C3` 为核心组件。

- `ZD=max(low(C1),low(C2),low(C3))`
- `ZG=min(high(C1),high(C2),high(C3))`
- 必须满足 `ZD + EPSILON < ZG`。
- 中枢方向取进入组件 `E` 的整体方向。
- 向上进入要求 `start(E) < ZD < end(E)`；向下进入要求 `start(E) > ZG > end(E)`。
- `E+C1+C2` 只形成 `2/3 provisional` 候选；第三核心组件完成后才形成正式中枢。
- 初始核心形成后固定，后续组件与核心严格重叠时只延伸原中枢。
- 离开后返回核心则归入原中枢外围过程；未形成独立新中枢前不结束原结构。
- 新中枢种子不得复用旧中枢已锁定的形成组件。
- 核心第三组件如果沿外侧结束，可同时作为首个离开组件。
- 中枢矩形只覆盖固定核心时间和 `ZD/ZG`，不包含进入、离开或回试组件。

新增中枢字段：

```text
entry_component_id
core_component_ids
formation_component_ids
extension_component_ids
peripheral_component_ids
departure_component_ids
retest_component_ids
transition_role
entry_movement_id
exit_movement_id
transition_point_id
```

保留扁平化的 `source_unit_ids/source_pen_ids`。只有组件确实是一笔时才填写兼容字段 `entry_pen_id`；多笔进入使用 `entry_pen_ids`。

### 3. 建立买卖点状态机

新增独立对象 `buy_sell_point`，状态为：

```text
candidate
confirmed
invalidated
```

统一字段：

```text
id
kind=buy_sell_point
level
point_type
status
point_date
point_price
confirmed_at
center_id
movement_id
source_component_ids
source_unit_ids
departure_component_id
retest_component_id
invalidation_price
divergence_evidence
evidence
```

#### 一买/一卖

正式一买要求：

- 已存在至少两个核心向下严格分离的同级中枢，构成正式下跌趋势。
- 最后向下组件价格创新低。
- 比较同一连续结构序列中、由中枢分隔的最近两个向下组件。
- MACD 在各组件 `(start_date,end_date]` 范围计算，不能跨连续行情边界。
- 后一组件负柱绝对面积严格小于前一组件，差异大于相对浮点容差。
- 后一组件 DIF 最低值严格高于前一组件 DIF 最低值。
- 点位取最后组件的真实最低端点，同价取最早端点。
- `confirmed_at` 取终端组件和必要行情证据的最晚确认时间。

一卖完全对称：价格创新高、正柱面积缩小、DIF 最高值降低。

缺少完整趋势、MACD、连续行情或双确认条件时只保留候选，不正式结束走势。

#### 二买/二卖

- 二买必须依赖已确认一买。
- 一买后形成首个向上组件，再出现向下回试组件。
- 回试最低点必须严格高于一买价格，随后组件确认回试完成。
- 二卖完全对称。
- 二买/二卖只追加走势验证证据，不重新移动已锁定的走势边界。
- 未确认一买/一卖时不得生成正式二买/二卖。

#### 三买/三卖

三买：

```text
中枢形成
→ 向上离开组件结束于 ZG 上方
→ 向下回试组件最低点 > ZG + EPSILON
→ 回试组件完成确认
```

三卖对称：

```text
向下离开
→ 向上回试
→ 回试最高点 < ZD - EPSILON
```

- 三买/三卖不依赖一买、二买，可独立成立。
- 回试接触或进入核心边界时信号无效。
- 离开、回试组件均可包含一笔或多笔。
- 买卖点日期取回试真实端点；确认时间取回试组件正式确认时间。

### 4. 联合构造走势、边界和递归

走势确认通道调整为：

```text
confirmed first_buy / first_sell
confirmed third_buy / third_sell
reverse_independent_center
```

- 按 `confirmed_at` 最早的有效证据作为 `primary_confirmation`。
- 后续确认写入 `confirmation_events`，不得覆盖边界、方向和主确认。
- 二买/二卖只能作为附加验证事件。
- 走势真实端点与确认时间分离。
- 一买或三买确认时，前一下跌走势结束于真实最低点，新上涨走势从同一端点开始；一卖、三卖对称。
- 转折中枢允许跨越走势边界：进入组件属于前一走势，核心第一组件及后续单位属于新走势。
- 中枢对单位只建立结构引用，不取得单位所有权。
- 每个底层单位仍只能归属一个正式走势；相邻走势只共享端点。
- 延续中枢的全部低级单位必须属于同一走势。
- 转折中枢必须明确记录 `entry_movement_id`、`exit_movement_id` 和 `transition_point_id`。
- 未确认尾部继续保持 `provisional`；数据边界、缺口和序列隔离不能伪装成走势结束。
- 只有 `confirmed` 且归属审计通过的低一级走势才能进入高级递归。

所有级别重复：

```text
低级正式单位
→ 无中枢组件
→ 中枢
→ 买卖点
→ 正式走势
→ 下一级
```

高级买卖点的 MACD 仍使用当前行情周期的完整 K 线，在高级组件对应日期范围内积分，不引入不存在的低周期数据。

### 5. 持久化、API 与前端

采用两阶段版本化发布：

1. `v21-center-free-components-third-points`
   - 上线组件层、组件化中枢、三买三卖、转折中枢和走势回溯边界。
2. `v22-macd-first-second-points`
   - 上线 MACD 一买一卖、依赖式二买二卖和最早证据锁定。

新增快照明细表：

```text
period_structure_components
period_buy_sell_points
```

旧 `period_structure_runs`、旧明细和历史人工修订全部保留，不原地改写。

保持 `/api/chart-data/{symbol}` 路径，新增：

```text
components
context_components
buy_sell_points
context_buy_sell_points
buy_sell_point_mode
primary_confirmation
confirmation_events
```

健康接口更新为：

```json
{
  "structure_mode": "formal_hierarchy",
  "center_construction_mode": "center_free_component_directional",
  "movement_confirmation_mode": "earliest_valid_structural_evidence",
  "buy_sell_point_mode": "macd_divergence_and_structural_retest"
}
```

前端调整：

- 新增一买、二买、三买、一卖、二卖、三卖标记。
- 候选使用空心/低透明度，确认使用实心，失效默认隐藏。
- 中枢方向与走势方向分别展示。
- 选中中枢时分别突出进入、核心、延伸、离开和回试组件。
- 转折中枢显示前走势、后走势及确认买卖点。
- 走势详情分别显示真实起止点、主确认方式、确认时间和追加证据。
- 组件图层默认关闭，详情选择时自动高亮，避免主图过度拥挤。
- 日线内部高级结构继续标明“内部 L2/L3”，不称为真实周线/月线。
- Agent 只能解释已经持久化的买卖点证据，不得自行创造背驰或买卖点。

## Migration and Execution

- 保留当前工作区全部未提交改动，不回退、不覆盖无关功能。
- 每个阶段执行前停止服务和后台同步，验证磁盘空间，完整备份 SQLite、WAL、SHM，并运行 `PRAGMA integrity_check`。
- 新算法先对启用标的 `1A0001/1A0688` 的 `5/30/d/w/m` 全矩阵离线计算。
- 所有计算出口、入库前和独立审计均使用同一套组件、中枢、买卖点、走势归属校验器。
- 全矩阵成功后才在单一数据库事务中切换活动快照；任一周期失败则不切换任何活动指针。
- v21 验收通过后再执行 v22；v22 失败时继续保留 v21 活动快照。
- 更新规则版本、结构版本和计算器指纹，禁止复用 v20 快照。
- 人工笔和中枢修订继续返回 `409`；普通绘图不受影响。
- 各阶段完成后重启服务，进行延迟健康检查、全矩阵 API 审计和真实浏览器验收。

## Test Plan

### 组件与中枢

- 一笔、三笔、五笔、七笔无中枢组件。
- 方向端点未推进时组件正确终止。
- 内部出现严格三单位重叠时不得继续合并。
- 单点接触不形成内部中枢。
- 多笔进入、多笔核心、多笔离开、多笔回试均能形成稳定结构。
- 相同输入逐前缀计算时，已确认组件和固定中枢核心保持稳定。
- `2/3` 只能形成候选。
- 中枢延伸、离开后返回、独立新中枢和数据断点均正确处理。

### 买卖点与走势

- 一买/一卖必须同时满足价格创新极值、MACD 柱面积缩小和 DIF 极值改善。
- MACD 比较不得跨行情连续区间。
- 二买/二卖在没有已确认一买/一卖时不得成立。
- 三买/三卖允许没有一买二买而独立成立。
- 离开或回试由三笔以上组件构成时仍能识别。
- 回试接触或进入 `ZG/ZD` 时三买/三卖失效。
- 核心第三组件可作为离开组件。
- 多个确认通道同时存在时，最早 `confirmed_at` 锁定边界。
- 后续证据不能移动已确认走势端点。
- 转折中枢可以跨走势引用，但底层单位只能归属一个走势。
- L2-L8 不得消费未确认、候选或跨隔离区间的低级走势。

### 真实案例

科创50日线必须验证：

```text
中枢 #6 方向仍为 down
进入笔 #41
核心笔 #42/#43/#44
ZD=984.28
ZG=1050.06
#44 向上离开至 1121.45
#45 回试至 1073.04 > ZG
形成 confirmed third_buy
```

最终走势必须为：

```text
前一下跌走势结束：2022-04-27，853.21
新上涨走势开始：2022-04-27，853.21
确认时间：标准笔 #45 正式确认时间
```

并验证：

- `#41` 只归属前一下跌走势。
- `#42` 起归属新上涨走势。
- 中枢 #6 作为转折中枢引用两侧结构，不重复拥有笔。
- 页面不再把 `2022-06-27` 显示为前一下跌走势的正式结束点。

上证日线继续验证：

- 不恢复三走势直接成 L2 的旧逻辑。
- 重叠高级中枢不重复生成。
- 买卖点确认不破坏统一方向性 L2 构造。

### 全量验收

- 后端测试、前端测试、生产构建和 `git diff --check` 全部通过。
- SQLite 完整性检查通过。
- 两阶段全矩阵重算与独立审计均为 `10/10`。
- API 分页补齐相关组件、中枢、买卖点和低级单位引用。
- 浏览器验证周期切换、图层筛选、组件高亮、买卖点点击、走势详情和空白清除。
- 服务必须由真实监听进程持有，并通过延迟 `/api/health` 检查。

## Assumptions

- 本模型是周期内工程化缠论结构，不宣称还原原文真实跨周期递归。
- 无中枢组件只允许一单位或奇数个三单位以上的连续单位。
- 三买三卖独立识别；二买二卖必须依赖正式一买一卖。
- 当前周期 MACD(12,26,9) 是本版一买一卖的正式背驰依据。
- 买卖点仅为结构证据，不产生交易委托、仓位建议或自动交易动作。
- 中枢方向固定，所谓“转化为上涨/下跌走势”是走势归属发生变化，不是修改中枢方向。
- 旧快照完整可审计，新阶段失败时不得暴露新旧结构混算结果。
