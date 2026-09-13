# 缠论工作台前端空白与 K 线不渲染彻底修复计划

## 摘要

当前后端服务和行情接口正常，但前端 ECharts 在 `setOption()` 阶段抛出 `TypeError: Cannot read properties of undefined (reading 'type')`，导致 React 页面空白或 K 线画布不显示。本次修复将重构图表数据契约、ECharts 配置生成、图表生命周期和异常恢复，先恢复稳定 K 线，再接入中枢层级和走势箭头。不修改行情数据，不删除旧快照，不改变用户绘图数据。

## 一、问题定位与数据契约

- 前端新增统一图表输入归一化：`bars` 、`indicators` 、`pens` 、`centers` 、`center_relations` 、`movements` 统一为安全数组，OHLC 和坐标必须是有限数字。
- 所有 series 必须有合法 `type` 和数组 `data`，`xAxisIndex/yAxisIndex` 不得越界，禁止 `undefined` series 或数据点。
- `pen_centers` 作为 `centers` 兼容别名；`decomposition` 兼容单层与按级别对象，进图表前统一为当前级别。
- `movements` 只保留当前级别且 `role=same_level_decomposition`；分时周期不传入中枢和走势 series。

## 二、ECharts 配置与生命周期

将 Chart 大段 effect 拆为 `buildAxes` 、`buildPriceSeries` 、`buildIndicatorSeries` 、`buildPenSeries` 、`buildCenterAreas` 、`buildMovementSeries` 、`buildDrawingSeries` 、`buildChartOption` 等纯函数，每层只返回验证后的配置。

先仅启用一个主图 grid、category xAxis、value yAxis 和 candlestick series，确认稳定后依次加入分时线、成交量、MACD、MA/BOLL、笔、中枢、走势和人工绘图。主图固定使用 index 0，副图只在匹配 grid 和轴时创建，分时不创建副图 series。

重构实例管理：同一 DOM 只允许一个实例，清理时使用保存的实例引用，`dispose()` 前通过 `echarts.getInstanceByDom()` 判断，`setOption/resize/dispose` 均使用保护。图表错误只显示“图表暂时无法渲染”，不能让 React 根节点崩溃。

## 三、中枢、走势与高低点

- 中枢只接收满足 `start_date<=end_date` 且 `zd<zg` 的数据；当前级别实线高亮，子级淡化，候选中枢虚线。
- 每个当前级别走势只画起点到终点的一条直线；上行红色，下行绿色，`provisional` 紫色虚线。如原生箭头不稳定，使用普通 line + 独立 scatter 终点或 SVG overlay，禁止使用数组形式 `symbol` 。
- 增加独立高低点层，标记 `H1/H2/...` 和 `L1/L2/...`，共享端点只画一次，不进入拖动编辑。

## 四、后端结构与人工修正

保留 L1 及高级中枢、中枢关系、两种走势 role、父子引用和快照元数据。`chart-data` 支持 `structure_level` 并保证当前级别不超过最高可用级别，不跨连续区间、sequence 或周期。

人工修正只允许底层笔/L1 中枢：修改后标记 stale，前端隐藏旧走势，后端重新递归并返回 `manual-derived`，不覆盖系统快照。

## 五、测试与验收

新增前端单元和 ECharts 集成测试，覆盖空数据、单根 K 线、数据归一化、series 轴索引、分时模式、走势箭头、共享端点、级别记忆、初始化/更新/销毁/异常恢复。后端覆盖 L1/L2/L3、延伸、新生、扩张、九段升级、父子引用和 SQLite 重载。

执行：

```bash
cd /Users/saber/Desktop/saber/投资/缠论/chant_agent
uv run --with-requirements requirements.txt --with pytest python -m pytest -q
cd web
npm test -- --run
npm run build
```

最终启动 8765 后验证桌面与移动视口，确认首页、分时、`5/30/d/w/m`、L1/L2、中枢、走势、H/L 标签均正常，无未捕获 ECharts TypeError。不删除旧数据，不修改行情数据。
