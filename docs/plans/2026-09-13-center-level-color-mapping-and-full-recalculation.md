# 中枢级别颜色映射与全量结构重算方案

## 目标

重新定义中枢的“结构级别”和“显示周期对应关系”，并对当前股票池内所有启用标的重新计算结构快照。

核心原则：

```text
结构计算仍按每个行情周期独立进行；
日线/周线/月线对应关系只用于级别显示、颜色和解释；
不把日线走势实际写入周线笔，也不做跨周期递归。
```

本次全量重算范围：

```text
所有 enabled 股票
× 5分钟、30分钟、日线、周线、月线
× adjustflag=2
```

分时 `1` 不生成缠论结构；`15/60/120/y` 只保留现有行情能力，不纳入本次结构全量重算。

当前环境已确认：

- 启用股票 4 个：`1A0001`、`399673`、`1A0688`、`300308`
- 数据库约 2.4 GiB
- 当前磁盘可用空间约 10 GiB，满足备份和重算前提

## 一、中枢级别正式定义

### L0

```text
L0 = 一条已确认笔
```

L0 不是中枢，只是高一级结构的原子运动。

### L1 中枢

由当前行情周期的：

```text
P0：进入笔
P1/P2/P3：核心三笔
```

组成。

严格条件：

```text
P0～P3 全部确认
方向交替
ZD = max(low(P1), low(P2), low(P3))
ZG = min(high(P1), high(P2), high(P3))
ZD < ZG
```

进入方向：

```text
向上：start(P0) < ZD < end(P0)
向下：start(P0) > ZG > end(P0)
```

字段规则：

- `P0` 只承担进入方向和证据作用；
- 中枢核心只由 `P1/P2/P3` 决定；
- `fixed_zd/fixed_zg` 保存固定核心；
- `dd/gg` 保存含延伸后的外围范围；
- 延伸只更新 `dd/gg`，不改变 `level`、核心和 ID。

### 高级中枢

`Ln+1` 必须由三个连续、已完成、方向交替的 `Ln` 走势严格重叠形成：

```text
ZD(Ln+1) = max(low(M1), low(M2), low(M3))
ZG(Ln+1) = min(high(M1), high(M2), high(M3))
ZD(Ln+1) < ZG(Ln+1)
```

升级路径：

- 九段扩展：按 `3+3+3` 形成三个完成走势单元；
- 扩张路径：核心分离、外围重叠后建立候选，等待第三个完成走势；
- 候选为 `provisional`，进度为 `2/3`；
- 第三个走势确认后沿用候选 ID，转为 `confirmed`；
- 所有低级中枢、低级走势和源笔永久保留；
- 递归上限为 `L8`。

### 中枢关系

保存以下关系：

```text
extension
newborn_up
newborn_down
expansion_up
expansion_down
parent_child
```

关系只描述结构之间的连接方式，不改变中枢自身级别。

## 二、级别与显示周期对应

### 1. 日线图的简化对应

在日线图中采用：

| 结构级别 | 显示对应周期 | 颜色 |
|---|---|---|
| L1 | 日线 | 日线色 |
| L2 | 周线 | 周线色 |
| L3 | 月线 | 月线色 |
| L4 及以上 | 更高一级 | 专用高级色 |

当前主题颜色沿用现有周期色：

```text
深色主题：
日线 #F2C14E
周线 #A78BFA
月线 #FF7043

浅色主题：
日线 #A87800
周线 #6D43B5
月线 #C43F24
```

因此日线图中：

```text
L1 中枢 = 日线颜色
L2 中枢 = 周线颜色
L3 中枢 = 月线颜色
```

### 2. 其他行情周期

此对应关系只作为显示语义，不改变结构计算。

- 周线图：L1 使用周线色，L2 使用月线色，L3 使用高级级别色；
- 月线图：L1 使用月线色，L2 及以上使用高级级别色；
- 5/30 分钟图：L1 使用当前分钟周期颜色，高级级别使用当前周期的稳定明暗变体，不声称分钟周期等同于日/周/月；
- 分时图不显示中枢级别。

统一函数：

```text
color_period_for_level(chart_timeframe, level)
```

返回：

```json
{
  "display_period": "w",
  "color_key": "period-w",
  "color": "#A78BFA"
}
```

中枢 API 对每条记录增加：

```text
display_period
color_key
```

颜色由级别和当前图表周期确定，不能由前端根据缺失字段猜测。

### 3. 日线走势与周线笔的关系

定义为产品解释层面的“语义对应”：

```text
日线 L1 完成走势 ≈ 简化的周线一笔
日线 L2 完成走势 ≈ 更高一级结构运动
```

但必须禁止：

- 用日线走势直接写入周线笔表；
- 用周线笔反向修改日线 L1；
- 跨周期拼接中枢；
- 把这种对应关系当成真实递归输入。

可在 Tooltip 中显示：

```text
语义对应：周线级别运动
```

但同时标明：

```text
计算来源：日线独立结构
```

## 三、图层显示规则

### 中枢

用户选择 `L1` 时：

```text
只绘制 L1 中枢
不绘制 L2/L3/L4
```

用户选择 `L2` 时：

```text
只绘制 L2 中枢
不绘制 L1/L3/L4
```

不再使用“`level <= active_level`”的模糊筛选。

样式：

- 当前级别：高对比度实线矩形；
- 候选中枢：同级别颜色虚线矩形；
- 高级级别不可见时不生成对应 ECharts 对象；
- 非法区间 (`zd>=zg`、日期无效、引用缺失) 只进入问题列表，不绘制。

### 笔

笔保持当前行情周期的笔颜色：

```text
日线笔使用日线笔色；
周线笔使用周线笔色；
不因中枢升级而改变笔本身颜色。
```

### 走势

走势方向颜色保持独立语义：

```text
上涨：红色
下跌：绿色
provisional：紫色虚线
```

可用级别色作为端点边框或标签辅助色，但不能覆盖方向颜色。

## 四、后端实现

### 结构版本

将结构版本升级为新的版本号，例如：

```text
chan-period-center-hierarchy-same-level-color-v12
```

新版本使旧 v11 活动快照失效，但不删除旧快照。

### 计算流程

对每个启用股票依次执行：

```text
读取现有行情
→ 按连续行情区间拆分
→ 包含处理
→ 分型
→ 笔
→ 原始 L1 中枢
→ 中枢延伸/新生/扩张关系
→ L2/L3/... 递归
→ 各级 hierarchy_component 走势
→ 各级 same_level_decomposition
→ 保存快照
```

重要约束：

- 每个区间只使用原始 L1 中枢作为递归入口；
- 不把已生成的 L2/L3 再当成 L1 输入；
- 中枢 ID 不重复追加区间前缀；
- 所有 `source_pen_ids/core_unit_ids/child_movement_ids` 必须存在；
- 不跨 `continuous_range_id`、`sequence_id` 或行情缺口。

### 快照元数据

写入：

```text
center_level_counts
movement_level_counts
hierarchy_input_hash
max_confirmed_center_level
max_available_center_level
decomposition_meta
```

每个中枢保存：

```text
level
status
upgrade_kind
progress
fixed_zd
fixed_zg
dd
gg
core_unit_ids
child_movement_ids
child_center_ids
source_pen_ids
parent_center_ids
confirmed_at
```

### API

`GET /api/chart-data/{symbol}` 支持：

```text
structure_level=1
```

返回：

```text
centers
pen_centers
center_relations
center_levels
movement_levels
active_structure_level
max_confirmed_center_level
max_available_center_level
```

服务端保证：

```text
structure_level=1 → centers 全部 level=1
structure_level=2 → centers 全部 level=2
```

`center_levels` 仅表示当前快照可用级别，不代表本次响应混合返回所有级别。

## 五、前端实现

### 颜色模块

扩展 `structureColors`：

```text
periodStructureColor(theme, period)
levelStructureColor(theme, chartTimeframe, level)
```

禁止所有级别继续直接调用：

```text
periodStructureColor(theme, currentTimeframe)
```

### 图表配置

拆分并测试：

```text
normalizeChartData
buildAxes
buildPriceSeries
buildIndicatorSeries
buildPenSeries
buildCenterAreas
buildMovementSeries
buildEndpointLabels
buildChartOption
```

所有函数返回前执行配置校验：

- series 有合法 `type`；
- data 是数组；
- axis index 不越界；
- 不含 `undefined`；
- 坐标均为有限值；
- 中枢日期和价格合法。

### 高低点

为当前级别走势生成独立端点层：

```text
H1/H2/...：高点
L1/L2/...：低点
```

共享端点只生成一次。

### 生命周期

- 单一 DOM 只保留一个 ECharts 实例；
- `getInstanceByDom()` 后再初始化或销毁；
- 清理使用闭包保存的实例；
- `setOption` 使用 `notMerge`；
- 错误显示“图表暂时无法渲染”，不让 React 根节点崩溃；
- 组件切换、刷新、分页、股票切换均不得产生未捕获 TypeError。

## 六、全量重算与迁移

### 执行前

1. 停止或进入结构计算维护状态，避免同步任务并发写入。
2. 确认磁盘剩余空间至少 5 GiB。
3. 备份：

```text
chant_agent.db
chant_agent.db-wal
chant_agent.db-shm
```

4. 记录备份路径、大小和校验值。
5. 不删除旧数据库、旧快照或用户绘图。

### 执行中

按股票和周期逐项强制重算：

```text
4 个启用股票
× 5/30/d/w/m
× adjustflag=2
```

每项成功后检查：

- 快照状态为 `success`；
- 活动快照版本为 v12；
- 中枢和走势数量可读取；
- 级别统计与实际行数一致；
- 引用完整；
- 同一区间首尾连续；
- 相邻走势方向交替；
- 不跨缺口；
- 候选确认时间不早于结构实际确认时间。

单项失败时：

- 记录股票、周期、异常和堆栈；
- 不影响其他股票继续计算；
- 最终输出失败清单；
- 不把失败结果标记为活动快照。

### 执行后

1. 查询所有启用股票的活动快照。
2. 汇总每个股票、周期的：

```text
L1/L2/L3/L4 中枢数量
各角色走势数量
候选数量
问题数量
```

3. 检查旧 v11 快照仍可审计。
4. 重启服务。
5. 浏览器检查日线、周线、月线和分钟周期。
6. 确认日线图：
   - 选择 L1 只显示 L1；
   - 选择 L2 显示周线色；
   - 选择 L3 显示月线色；
   - Tooltip 显示结构级别、显示对应周期和计算来源。

## 七、测试计划

### 中枢算法

- L1 满足 `P0+P1/P2/P3`；
- 延伸保持 `level/ID/ZD/ZG`；
- 新生成关系不升级；
- 扩张生成 `2/3` 候选；
- 九段 `3+3+3` 确认高级中枢；
- 候选转确认保持 ID；
- L1/L2/L3 子结构完整保留；
- 不重复使用同一低级走势；
- 不跨连续区间、sequence 或缺口；
- 最高不超过 L8。

### 颜色与级别

- 日线 L1 使用日线色；
- 日线 L2 使用周线色；
- 日线 L3 使用月线色；
- 周线和月线映射符合规则；
- 分钟图不错误显示日/周/月语义；
- 级别切换时 API 与图层完全一致；
- L1 选择时响应中不存在 L2/L3 中枢。

### 前端图表

- 空行情不调用 ECharts；
- 单根和多根 K 线可渲染；
- 缺失指标不崩溃；
- 非法 series 被过滤；
- 坐标轴索引有效；
- 分时不生成结构 series；
- 走势直线、端点、H/L 标签正常；
- 共享端点去重；
- 级别切换、分页、刷新、销毁无 TypeError；
- 图表异常时 React 根节点仍存在。

### 全量重算

- 所有启用股票均被处理；
- 五个结构周期均有结果或明确失败原因；
- 活动快照均为新版本；
- 旧快照仍存在；
- 备份文件存在且校验通过；
- 统计、引用、版本和边界审计全部通过。

执行命令：

```bash
cd /Users/saber/Desktop/saber/投资/缠论/chant_agent
uv run --with-requirements requirements.txt --with pytest python -m pytest -q

cd web
npm test -- --run
npm run build
```

## 八、计划文件与执行边界

执行阶段第一步必须将本计划完整原文保存到：

```text
/Users/saber/Desktop/saber/投资/缠论/docs/plans/2026-09-13-center-level-color-mapping-and-full-recalculation.md
```

并立即回读核验非空、章节完整、内容未被摘要化。

本次不包含：

- 跨周期真实递归；
- 自动交易信号；
- 背驰和买卖点；
- 线段生成；
- 直接人工编辑高级中枢或走势；
- 删除旧数据库、旧快照或用户数据。

