# 中枢层级递归与同级别走势分解重构方案

计划文档目标路径：

`/Users/saber/Desktop/saber/投资/缠论/docs/plans/2026-09-13-center-hierarchy-and-same-level-decomposition.md`

## 一、目标与原则

将当前 v10 的“单层 L1 中枢直接驱动走势”重构为两条相互依赖、但概念独立的链路：

```text
层级结构链路：
笔
→ L1 中枢及其延伸
→ L1 完成走势
→ 中枢扩张/九段扩展
→ L2 中枢
→ L2 完成走势
→ 继续递归到 Ln

操作分解链路：
选择当前结构级别 Ln
→ 固定在 Ln 做纯机械同级别分解
→ 标记共享高低点
→ 用起点到终点的直箭头绘制走势
```

基本原则：

- 中枢延伸、扩张和升级负责确定结构级别。
- 同级别分解负责在选定级别上划分连续走势。
- 两套规则不能互相替代。
- 低级中枢升级后必须永久保留，同时新增高级中枢。
- 各行情周期独立递归，不能把 5 分钟、30 分钟、日线直接映射为 L1/L2/L3。
- 不引入跨周期递归、背驰或买卖点。

## 二、中枢层级体系

### 1. L1 中枢

保留现有产品规则：

```text
P0：进入笔
P1/P2/P3：核心三笔
```

成立条件：

```text
P0～P3 方向交替且全部确认
ZD = max(low(P1), low(P2), low(P3))
ZG = min(high(P1), high(P2), high(P3))
ZD < ZG

向上进入：start(P0) < ZD < end(P0)
向下进入：start(P0) > ZG > end(P0)
```

`P0` 只承担进入方向和证据作用，不计入中枢核心三段。

### 2. 中枢延伸

某 `Ln` 中枢确认后：

- 固定核心 `ZD/ZG`。
- 后续 `L(n-1)` 运动重新触及核心，归入同一中枢。
- 更新外围范围 `DD/GG`，不修改核心。
- 延伸不创建新中枢，不提高级别。
- 离开后重新进入仍是延伸。
- 延伸包含的低一级运动、中枢和笔全部保留。

### 3. 中枢新生

相邻两个 `Ln` 中枢的核心及外围波动完全分离：

```text
向上新生：DD(next) > GG(previous)
向下新生：GG(next) < DD(previous)
```

则二者是同级中枢新生关系，可用于构成该级别趋势，不触发升级。

### 4. 中枢扩张

相邻两个 `Ln` 中枢核心已经分离，但外围波动范围发生重叠：

```text
向上：
ZD(next) > ZG(previous)
且 DD(next) <= GG(previous)

向下：
ZG(next) < ZD(previous)
且 GG(next) >= DD(previous)
```

此时记录：

```text
relation = expansion_up | expansion_down
```

扩张只启动 `L(n+1)` 中枢候选，不能仅凭两个中枢立即确认升级。

### 5. 高一级中枢确认

采用两条升级路径，最终都统一为“三个连续低一级完成走势严格重叠”。

#### 九段扩展路径

同一个 `Ln` 中枢吸收满 9 个连续 `L(n-1)` 运动后：

```text
U1 U2 U3 | U4 U5 U6 | U7 U8 U9
```

按 `3+3+3` 组合成三个完成的 `Ln` 走势单元。三个单元严格重叠时，确认 `L(n+1)` 中枢。

未满 9 段时仍是原 `Ln` 中枢延伸，不提前升级。

#### 扩张路径

发现两个 `Ln` 中枢外围波动重叠后：

- 创建 `L(n+1)` 候选；
- 继续等待第三个连续、已完成的 `Ln` 走势；
- 三个 `Ln` 走势严格重叠后确认 `L(n+1)` 中枢。

统一公式：

```text
ZD(n+1) = max(low(M1), low(M2), low(M3))
ZG(n+1) = min(high(M1), high(M2), high(M3))
ZD(n+1) < ZG(n+1)
```

其中 `M1/M2/M3` 必须：

- 都是已完成的 `Ln` 走势；
- 时间连续且方向交替；
- 位于同一 `continuous_range_id + sequence_id`；
- 不跨行情缺口；
- 不重复消费同一个低级走势。

### 6. 候选与确认

高一级候选满足以下任一条件后生成：

- 九段扩展已形成前两个 `3` 段组合；
- 两个同级中枢已发生外围扩张。

候选使用稳定 ID：

```text
level + continuous_range_id + sequence_id + first_child_movement_id + upgrade_kind
```

候选字段：

```text
status = provisional
upgrade_kind = extension_3x3 | expansion
progress = 2/3
```

第三个完成走势确认后：

- 沿用原候选 ID；
- 更新为 `confirmed`；
- 保存 `confirmed_at`；
- 不删除或覆盖任何子中枢、子走势和笔。

递归持续到无法形成新的已确认或候选高一级中枢为止，设置安全上限 `L8`，避免异常数据导致无限递归。

## 三、同级别分解

### 1. 当前级别

每个股票和行情周期默认选择 `L1`，用户可切换到当前已存在的 `L2/L3/...`。

选择保存在：

```text
localStorage:
chan-structure-level-{symbol}-{timeframe}
```

选定 `Ln` 后：

- 只绘制 `Ln` 的走势箭头；
- `Ln` 中枢高亮；
- `L1～L(n-1)` 子中枢淡化显示；
- 高于 `Ln` 的中枢不显示；
- `Ln` 候选中枢用虚线显示。

### 2. 纯机械分解规则

在选定 `Ln` 上，不再使用“中枢延伸/扩张”决定分解边界。

输入单位：

```text
L1：已确认笔作为 L0 原子运动
Ln：已完成的 L(n-1) 走势
```

按时间顺序、从左向右、非重叠消费：

1. 从第一个满足当前级别中枢条件的进入端点开始。
2. 首三个连续低一级运动严格重叠，形成一个当前级别盘整。
3. 该三段完成后，本盘整立即完成，不继续吸收第 4～6 段。
4. 后续三个低一级运动再次重叠，形成下一个盘整。
5. 允许：

```text
盘整 + 盘整
上涨 + 盘整
盘整 + 下跌
上涨 + 下跌
```

6. 两个以上核心和外围均严格分离、方向一致的当前级别中枢，组织为一个当前级别趋势。
7. 当前级别不调用升级规则改变分解边界；升级结果只由层级结构链路维护。
8. 同价候选边界取最早出现者，保证结果稳定。
9. 左侧不足以确认当前级别走势的单位记入 `unassigned_unit_ids`。
10. 右侧未完成走势标记为 `provisional`。

### 3. 走势类型

盘整：

```text
classification = consolidation
center_count = 1
```

上升趋势：

```text
classification = trend
direction = up
至少两个同级新生中枢
DD(next) > GG(previous)
```

下降趋势：

```text
classification = trend
direction = down
至少两个同级新生中枢
GG(next) < DD(previous)
```

当前级别的中枢发生核心重叠、外围扩张或六段延伸时，不把它们合并为一个操作走势，而是按三段一组继续分解，允许盘整连接。

### 4. 走势边界

相邻当前级别走势必须共享一个真实高低端点：

```text
previous.end_date  = next.start_date
previous.end_price = next.start_price
```

同时满足：

- 上行走势终点是当前级别真实最高点；
- 下行走势终点是当前级别真实最低点；
- 相邻走势方向交替时共享同一个高点或低点；
- 只共享端点，不共享整条低级走势；
- `end_date/end_price` 表示实际边界；
- `confirmed_at` 表示后续结构确认时间；
- 两者不得混用。

## 四、数据结构与持久化

### 1. 中枢

继续使用 `period_pen_centers`，所有级别同时持久化：

```json
{
  "id": "center-L2-...",
  "kind": "center",
  "level": 2,
  "status": "confirmed",
  "upgrade_kind": "expansion",
  "zd": 10.2,
  "zg": 11.3,
  "dd": 9.7,
  "gg": 12.1,
  "core_unit_ids": ["movement-L1-a", "movement-L1-b", "movement-L1-c"],
  "child_movement_ids": ["..."],
  "child_center_ids": ["..."],
  "source_pen_ids": ["..."],
  "parent_center_ids": ["center-L3-x"],
  "confirmed_at": "...",
  "continuous_range_id": 0,
  "sequence_id": 0
}
```

低级中枢允许参与一个高级中枢，但不能被删除、替换或改变自身级别。

### 2. 中枢关系

启用现有 `period_center_relations`，保存：

```text
extension
newborn_up
newborn_down
expansion_up
expansion_down
parent_child
```

每条关系保存核心区间、外围区间、参与的中枢、确认时间和规则证据。

### 3. 走势

继续使用 `period_movements`，通过 `role` 区分：

```text
role = hierarchy_component
role = same_level_decomposition
```

共同字段：

```json
{
  "id": "movement-L2-...",
  "level": 2,
  "role": "same_level_decomposition",
  "direction": "up",
  "classification": "trend",
  "status": "confirmed",
  "start_date": "...",
  "start_price": 10.2,
  "end_date": "...",
  "end_price": 15.8,
  "confirmed_at": "...",
  "center_ids": ["..."],
  "child_movement_ids": ["..."],
  "source_pen_ids": ["..."],
  "continuous_range_id": 0,
  "sequence_id": 0
}
```

层级递归只读取 `hierarchy_component`；K线图只返回当前级别的 `same_level_decomposition`。

### 4. 快照元数据

`period_structure_runs` 写入：

```text
center_level_counts
movement_level_counts
hierarchy_input_hash
decomposition_meta
max_confirmed_center_level
max_available_center_level
```

结构版本升级到新的 hierarchy 版本，使 v10 活动快照失效；旧快照保留审计。

## 五、API 与前端

### 1. API

扩展：

```text
GET /api/chart-data/{symbol}
```

新增参数：

```text
structure_level=1
```

返回：

```text
centers
center_relations
movements
center_levels
movement_levels
active_structure_level
max_confirmed_center_level
max_available_center_level
decomposition
```

兼容字段：

- `pen_centers` 暂时保留一个版本周期，内容等于 `centers`。
- `movements` 只返回当前级别同级分解走势。
- 分时周期仍不生成缠论结构。

`health` 改为动态返回最高活动中枢级别，不再硬编码 `max_center_level=1`。

### 2. 中枢显示

- 当前级别中枢：实线、高对比度矩形。
- 当前级别候选：同色虚线矩形，显示升级进度。
- 子级中枢：淡色细边框，透明度随级别距离递减。
- 点击高级中枢，在证据栏显示升级路径、三个核心走势、子中枢和源笔。
- 图层控制保留“笔 / 中枢 / 走势”，增加结构级别选择器。

### 3. 走势显示

删除 v10 的内部折线路径和底部区间带，改为：

```text
低点 ● ─────────────▶ ● 高点
高点 ● ─────────────▶ ● 低点
```

规则：

- 每个当前级别走势只画一条起点到终点的直箭头。
- 上涨使用红色箭头，下降使用绿色箭头。
- `provisional` 使用紫色虚线箭头。
- 起点和终点绘制圆形端点。
- 高点标记 `H序号`，低点标记 `L序号`。
- 标签显示价格，日期和确认时间放入 Tooltip。
- 相邻走势共享端点只绘制一个圆点和一个标签。
- 箭头层位于 K线和中枢之上，但低于人工绘图。
- 点击箭头或端点只打开证据检查器，不允许拖动、删除或直接编辑。

### 4. 人工修正

- 修改笔或任一级中枢后，所有依赖层级和同级分解进入 `stale`。
- 未保存期间隐藏走势箭头并提示“层级与走势待重算”。
- 保存后由后端从受影响级别向上递归重算。
- 系统快照保留原结果，有人工覆盖时返回 `manual-derived` 层级和走势。
- 第一版不允许直接修改高级中枢和走势，只允许通过底层笔/L1 中枢修正驱动重算。

## 六、实现顺序

1. 保存并核验本计划原文。
2. 备份 SQLite、WAL、SHM。
3. 把 v10 的走势分解与中枢生命周期解耦。
4. 实现中枢关系分类和层级递归引擎。
5. 实现九段扩展、扩张候选及高级中枢确认。
6. 实现各级 `hierarchy_component` 走势。
7. 实现纯机械同级别分解器。
8. 扩展持久化和快照元数据。
9. 接入人工修正后的向上重算。
10. 扩展 API、类型和分页合并。
11. 实现级别选择、中枢分层显示和高低点箭头。
12. 重算启用标的并执行结构审计。
13. 启动新服务，完成桌面与移动端浏览器检查。

## 七、测试与验收

### 中枢测试

- L1 继续满足 `P0 + P1/P2/P3`。
- 三段核心严格重叠；单点接触拒绝。
- 中枢延伸保持 ID、级别和 `ZD/ZG` 不变。
- 中枢新生不升级。
- 核心分离、外围重叠生成扩张候选。
- 九段按 `3+3+3` 确认升级。
- 三个连续低级完成走势严格重叠确认高级中枢。
- 候选转确认保持 ID。
- L1/L2/L3 子结构全部保留且引用完整。
- 不跨 `continuous_range_id` 或 `sequence_id`。
- 递归无结果时停止，最高不超过 L8。

### 同级分解测试

- 单个三段重叠形成盘整。
- 六段延伸在当前级别分为两个盘整。
- 允许盘整加盘整。
- 两个外围完全分离的同级中枢组成趋势。
- 中枢扩张不直接改变当前级别分解。
- 相邻走势共享高低点但不共享低级走势。
- 左端残段未归属，右端走势为 `provisional`。
- 相同输入产生稳定 ID 和唯一分解。
- L1/L2/L3 分解互相独立。

### API、持久化与前端测试

- 所有层级中枢、关系和两种角色的走势可持久化并重载。
- `center_level_counts`、`movement_level_counts` 与实际行数一致。
- 分页不会截断箭头端点或重复高低点。
- 级别选择按股票和行情周期记忆。
- 当前级别高亮、子级淡显、候选虚线正确。
- 箭头方向、颜色、端点、H/L 标签和 Tooltip 正确。
- 人工未保存时隐藏箭头，保存后返回 `manual-derived` 结果。
- 分时周期不显示级别控件。

执行：

```bash
cd /Users/saber/Desktop/saber/投资/缠论/chant_agent
uv run --with-requirements requirements.txt --with pytest python -m pytest -q

cd web
npm test -- --run
npm run build
```

随后用真实数据库检查：

- 所有启用标的的 `5/30/d/w/m`；
- 各级中枢和走势数量；
- 父子引用完整性；
- 九段升级与扩张升级样例；
- 同级别走势首尾连续性；
- 相邻走势共享端点、不共享子走势；
- 候选和确认时间不存在未来函数；
- 浏览器桌面与移动视口无重叠、箭头和中枢可辨认。

## 八、迁移前提与边界

当前磁盘剩余空间约 675 MiB，而数据库约 2.1 GiB，无法安全保留旧快照并执行全量层级重算。实施迁移前必须先保证至少 5 GiB 可用空间；不自动删除旧数据库、备份或用户数据。

迁移仅使用数据库已有行情，不重新下载行情。旧 v10 快照继续保留为审计记录，新版本通过新的结构版本和活动快照切换生效。

本迭代不包含：

- 跨行情周期级别映射；
- 线段生成；
- 背驰；
- 一二三类买卖点；
- 自动交易信号；
- 走势或高级中枢直接人工编辑。
