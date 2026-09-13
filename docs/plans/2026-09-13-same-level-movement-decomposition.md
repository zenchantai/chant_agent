# 简化版走势定义与同级别分解方案

计划文档目标路径：

`/Users/saber/Desktop/saber/投资/缠论/docs/plans/2026-09-13-same-level-movement-decomposition.md`

## 一、总体目标

在现有链路上增加一层确定性的同周期走势：

```text
本周期 K线
→ 包含处理
→ 分型
→ 笔
→ 方向性 L1 笔中枢
→ 本周期走势
→ 同级别分解
```

这是工程化的简化缠论定义，不引入线段、L2 中枢、递归级别、背驰或买卖点。

执行阶段第一步必须将本 `<proposed_plan>` 原文完整写入目标文件并回读核验，成功后才能修改业务代码。

## 二、走势正式定义

### 1. 基本单位

一个走势必须：

- 位于同一个 `symbol + timeframe + adjustflag + continuous_range_id`。
- 至少包含一个已确认的 L1 笔中枢。
- 起点和终点都必须是已确认笔的真实高低端点。
- 相邻走势只共享一个转折端点，不共享整条笔。
- 不跨越行情缺口、连续区间边界或 `sequence_id` 边界。

首个中枢进入笔之前的笔不强行归入走势，记录为 `unassigned_pen_ids`。

### 2. 走势类型

盘整走势：

```text
只包含一个独立中枢
classification = consolidation
direction = up | down
```

上升趋势：

```text
至少包含两个依次抬高的独立中枢
ZD(next) > ZG(previous)
classification = trend
direction = up
```

下降趋势：

```text
至少包含两个依次降低的独立中枢
ZG(next) < ZD(previous)
classification = trend
direction = down
```

同向出现第二个严格分离中枢时，将原盘整候选升级为趋势，不能切成两个走势。

中枢区间仅接触但不严格分离时，不作为趋势成立证据；重叠中枢应由现有中枢延伸规则吸收。人工修正造成重叠或矛盾时停止确认并输出分解问题，不猜测结构。

### 3. 走势完成

第一条反向笔、第一次离开中枢都不能确认走势完成。

走势完成条件：

```text
当前走势方向的反向独立中枢已经确认
```

确认时回溯分界点：

- 上行走势取上一中枢至反向中枢之间的最高已确认笔端点。
- 下行走势取同一区间的最低已确认笔端点。
- 同价极值取最早端点，保持历史边界稳定。
- 旧走势终点与新走势起点使用同一个端点。
- `end_date/end_price` 保存真实分界点。
- `confirmed_at` 保存反向中枢确认时间，两者不得混用。

最后一个走势永远允许为 `provisional`；其显示范围延伸至最后一条确认笔，同时保存当前候选极值，但不锁定最终终点。

## 三、同级别分解算法

每个连续行情区间独立执行：

1. 按笔序和时间排序已确认笔、中枢，校验所有中枢引用的笔均存在且顺序连续。
2. 从第一个可确认中枢的进入笔起点创建首个走势候选，方向取该中枢进入方向。
3. 比较后续独立中枢与当前最后中枢的位置：
   - 与当前方向一致：加入当前走势；中枢数达到两个后升级为 `trend`。
   - 与当前方向相反：在两中枢之间寻找当前方向的极值端点，确认旧走势，并从同一端点创建反向新走势。
   - 重叠、接触、方向或笔引用矛盾：停止跨越该位置确认，保留当前尾部为 `provisional` 并记录问题。
4. 重复扫描，得到方向严格交替的走势序列。
5. 区间结束时保留一个可选的未完成尾走势；没有中枢时不生成走势。
6. 强制校验：
   - 相邻走势 `previous.end == next.start`；
   - 相邻走势方向相反；
   - 笔不重复归属，只共享端点；
   - 已确认走势至少一个中枢；
   - 趋势至少两个严格同向排列的中枢；
   - 不存在未解释的内部笔或跨缺口连接。

## 四、数据、接口与展示

### 持久化

在结构快照计算阶段生成系统走势，写入已有 `period_movements`：

```json
{
  "id": "movement-...",
  "kind": "movement",
  "direction": "up",
  "classification": "trend",
  "status": "confirmed",
  "start_date": "...",
  "start_price": 10.2,
  "end_date": "...",
  "end_price": 18.6,
  "confirmed_at": "...",
  "center_ids": ["center-a", "center-b"],
  "pen_ids": ["pen-1", "pen-2"],
  "center_count": 2,
  "confirmation_center_id": "center-c",
  "termination_reason": "opposite_center_confirmed",
  "continuous_range_id": 0,
  "origin": "system",
  "evidence": ["MOVEMENT-BASE-001", "MOVEMENT-TREND-001", "MOVEMENT-BOUNDARY-001"]
}
```

同时保存 `movement_count`、`movement_input_hash` 和分解元数据。走势 ID 由连续区间、首中枢和起始端点稳定生成，尾部由 `provisional` 变为 `confirmed` 时不更换 ID。

规则版本升级为新的 movement 版本，使旧活动快照失效；旧快照保留审计，不删除。

### 人工修正

- 系统走势随系统笔和中枢持久化。
- 人工修改笔或中枢保存成功后，后端基于有效笔和有效中枢重新计算 `manual-derived` 走势，不覆盖系统走势。
- 存在未保存修改时，前端暂时隐藏旧走势并显示“走势待重算”；保存并刷新后展示新结果。
- 第一版不允许直接手工增删改走势。

### API

`chart-data` 新增：

```text
movements
decomposition
```

`decomposition` 返回算法版本、自动起点策略、未归属笔、分解状态和问题列表。分页返回相交走势及当前页面需要的 `path_points`；前端加载历史页时按走势 ID 合并路径点。

`health` 增加：

```text
same_level_decomposition = true
movement_mode = same_period_center_driven
```

不恢复旧的手动 anchor API，也不恢复线段、递归级别和 `movement_levels`。

### K线图

- 图层控制增加“走势”，默认开启并按股票、周期持久化。
- 同时绘制走势价格路径和底部走势区间带。
- 上行、下行、`provisional` 使用可区分的颜色和虚实线。
- 相邻走势的共同高低点只画一个边界标记。
- Tooltip 明确显示走势类型、方向、中枢数量、实际边界、确认时间和状态。
- 走势可点击查看证据，但不能拖动、删除或直接修改。
- “笔中枢”信息栏改为“笔 / 中枢 / 走势”数量。

## 五、规则、测试与迁移

新增产品覆盖规则：

- `MOVEMENT-BASE-001`：一个中枢构成盘整候选。
- `MOVEMENT-TREND-001`：两个以上严格同向分离中枢构成趋势。
- `MOVEMENT-BOUNDARY-001`：反向中枢确认，边界回溯到真实极值。
- `MOVEMENT-DECOMP-001`：相邻走势共享端点、方向交替、笔不重叠。
- `MOVEMENT-STATUS-001`：右端走势保持 provisional，确认时间不得前移。

重点测试：

- 单中枢向上、向下盘整。
- 双中枢及多中枢上升、下降趋势。
- 同向第二中枢合并为趋势，不错误切分。
- 上转下共享最高点、下转上共享最低点。
- 实际边界早于 `confirmed_at`，不存在未来函数。
- 假离开后重新进入不产生新走势。
- 中枢延伸不增加趋势中枢数。
- 接触、重叠、引用缺失和方向矛盾进入问题状态。
- 左侧残段未归属、右侧尾走势 provisional。
- 行情缺口和连续区间之间不连线。
- 人工修正后生成 `manual-derived` 走势，未保存时不展示旧走势。
- SQLite 持久化、重新加载、分页合并及稳定 ID。
- API 不再排除 `movements`，但继续排除线段和递归字段。
- 前端路径、区间带、共同端点、Tooltip 和图层持久化。

执行验证：

```bash
cd /Users/saber/Desktop/saber/投资/缠论/chant_agent
uv run --with-requirements requirements.txt --with pytest python -m pytest -q
cd web
npm test -- --run
npm run build
```

基线为后端 `50 passed`、前端 `13 passed`、生产构建通过。构建存在现有大 chunk 警告，本迭代不把代码拆包纳入范围。

代码和测试通过后，先备份 SQLite 及 WAL/SHM，再对全部启用股票、五个周期和 `adjustflag=2` 强制生成新版本活动快照；核验走势不变量、数量、版本、引用完整性和旧新快照隔离。

## 六、明确不包含

- 不生成线段。
- 不递归生成更高缠论级别。
- 不生成 L2/L3 中枢。
- 不判断背驰和一二三类买卖点。
- 不跨行情周期组合走势。
- 不允许走势人工编辑。
- 不用窗口起始笔伪造一个被截断的走势。
