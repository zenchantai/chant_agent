# 缠论线段算法与页面展示

计划文档目标路径：

`/Users/saber/Desktop/saber/投资/缠论/chant_agent/docs/plans/2026-09-13-chan-segment-definition-and-display.md`

## 一、目标与规则来源

在现有“分型 → 笔 → 中枢/走势”链路中新增“笔 → 线段”层：

- 系统线段由已确认笔确定性生成。
- 线段独立持久化，支持审计、分页和后续递归使用。
- 页面新增独立“线段”图层开关。
- 线段颜色与当前周期笔颜色一致，但使用虚线。
- 现有手动画图中的普通 `object_type="segment"` 保持不变，与系统缠论线段分离。
- 最右侧满足最小起始条件但尚未满足结束条件的线段显示为 `provisional`，使用虚线；已确认线段同样使用虚线，状态通过详情和 tooltip 区分。

线段规则唯一依据为：

:codex-file-citation{path="/Users/saber/Desktop/saber/投资/缠论/读缠论108课札记.pdf" purpose="source"}

重点采用 PDF 第 66、69、71、75、78 课对应内容：

- 线段至少由三笔组成。
- 线段的前三笔必须存在共同重叠区间。
- 线段只能从向上笔或向下笔开始，端点方向必须符合线段方向。
- 向上段使用反向的向下笔构成特征序列；向下段使用反向的向上笔构成特征序列。
- 特征序列只能在同一线段内部讨论包含关系，并且必须从左到右做非包含处理，得到标准特征序列。
- 向上段只考察特征序列顶分型；向下段只考察特征序列底分型。
- 第一种结束情况：特征序列分型的第一、第二元素之间没有缺口，线段在该分型极值处结束。
- 第二种结束情况：第一、第二元素之间有缺口，从该分型极值开始的反向笔序列出现特征序列分型时，原线段结束；不要求缺口一定回补。
- 单笔不能直接确认线段被破坏，必须由符合规则的新线段完成破坏。
- 复杂或古怪线段必须通过“线段被线段破坏”的关系解决，不能因一笔破坏就强行切段。
- 线段用于后续分析时保留真实边界，同时提供标准化高低点字段；不得使用未来数据改写已确认前缀。

## 二、计算、版本与持久化

### 新增线段计算模块

新增 `app/segments.py`，提供纯函数式、可测试的线段构建器：

- 输入：同一 `continuous_range_id` 内按时间排序的已确认笔。
- 不跨行情缺口、连续区间或笔序列边界。
- 每条笔归一化为：
  - `direction`
  - `start_date/end_date`
  - `start_price/end_price`
  - `low/high`
  - 原始笔 ID
- 以候选起点扫描笔序列；候选至少需要三笔且三笔区间严格共同重叠。
- 根据首笔方向建立对应特征序列。
- 在候选线段内部对特征序列从左到右处理包含关系。
- 依据 PDF 的两种分型/缺口情况确认结束边界。
- 只有新的候选线段完成“线段破坏”时才确认前一条线段结束。
- 对无法确认结束的最右侧候选保留 `provisional`，不伪造未来终点。
- 输出稳定顺序和稳定 ID，重复计算同一输入必须得到相同结果。

每条系统线段使用独立结构记录：

```json
{
  "id": "segment-...",
  "kind": "segment",
  "role": "line_segment",
  "level": 1,
  "ordinal": 0,
  "direction": "up",
  "status": "confirmed|provisional",
  "start_date": "...",
  "end_date": "...",
  "start_price": 0,
  "end_price": 0,
  "low": 0,
  "high": 0,
  "pen_ids": [],
  "source_pen_ids": [],
  "feature_sequence_pen_ids": [],
  "feature_sequence": [],
  "confirmed_at": null,
  "termination_reason": "...",
  "evidence": []
}
```

`level=1` 表示当前行情周期的基础线段，不将其误认为 L1/L2/L3 中枢层级。

### 规则版本与指纹

由于结构计算结果增加了新层，规则和层级版本升级：

- `DEFINITION_VERSION` / `PERIOD_DEFINITION_VERSION`：

  `chan-period-center-hierarchy-cache-fingerprint-v14`

- `HIERARCHY_VERSION`：

  `center-hierarchy-cache-fingerprint-v14`

- `CALCULATOR_SOURCE_FILES` 增加：

  `app/segments.py`

- 更新 `knowledge/chan_rules.yaml`：
  - 版本改为 v14。
  - 将现有 `SEG-001` 从 `reference_only` 改为运行规则。
  - 增加标准特征序列、两种结束情况、线段破坏、候选尾部等规则条目。
  - 标注 PDF 来源课次和页码。
- 更新 `knowledge/chan_rules.md`、规则版本断言和 API 健康检查断言。
- `structure_version` 哈希输入加入 `segments`，确保线段算法或线段结果变化时生成新的结构版本。
- 旧 v13 活动 run 自然失效，历史数据保留，不删除。

### 数据库

新增非破坏性表：

```sql
period_segments (
  run_id INTEGER NOT NULL,
  symbol TEXT NOT NULL,
  timeframe TEXT NOT NULL,
  adjustflag TEXT NOT NULL,
  definition_version TEXT NOT NULL,
  ordinal INTEGER NOT NULL,
  start_date TEXT NOT NULL,
  end_date TEXT NOT NULL,
  payload TEXT NOT NULL,
  PRIMARY KEY(run_id, ordinal)
)
```

同时：

- `Store._initialize()` 创建表和日期索引。
- `replace_period_structure()` 在同一事务中写入 `period_segments`。
- `load()`、`effective_structure()`、审计逻辑读取并返回 `segments`。
- 失败事务不得切换活动 run。
- 不增加人工线段修订接口；线段始终由有效笔重算生成。
- 现有笔、中枢、走势、人工绘图和旧 run 全部保留。

### 计算链路

在 `PeriodStructureService` 中：

1. 笔计算完成后，按连续区间调用 `build_segments()`。
2. 线段计算结果进入层级/走势输入前，明确区分：
   - 线段是新的一等结构输出；
   - 现有 v13 中枢和走势逻辑不被静默改写。
3. 本次默认不把线段替换现有 v13 中枢输入，不修改既有中枢升级公式。
4. `analyze_period()` 和 `analyze_period_ranges()` 均输出线段。
5. 线段参与结构版本计算，但不改变现有买卖点、背驰和走势算法。

## 三、API 与分页行为

### `GET /api/chart-data/{symbol}`

新增：

- `segments: Node[]`
- 可选的 `segment_count`
- 保留所有现有 v13 字段。

行为：

- 返回当前页面日期范围相交的系统线段。
- 每条线段保留完整笔引用和特征序列证据。
- 线段不跨分页缺口；若边界超出当前页，返回真实线段边界，但不生成越界的路径点。
- 为保证点击详情和笔引用完整，将线段引用的相关笔纳入 `pens`。
- `structure_level` 继续保留兼容语义，但不影响线段返回。
- `active_structure_level` 继续作为兼容字段，不用于线段过滤。
- `segments` 不与普通 `drawings` 合并。

健康接口增加：

- `segment_algorithm_version`
- 当前规则版本和计算器指纹保持 v14 一致。

## 四、前端展示与交互

### 类型和状态

更新 `web/src/types.ts`：

- `ChartData.segments?: Node[]`
- `Node` 增加线段证据所需可选字段：
  - `feature_sequence`
  - `feature_sequence_pen_ids`
  - `termination_reason`
  - `source_pen_ids`
  - `low/high`
- `role` 支持 `"line_segment"`。

图层状态扩展为：

```ts
{
  pens: boolean;
  segments: boolean;
  centers: boolean;
  movements: boolean;
  centerLevels: Record<string, boolean>;
  movementLevels: Record<string, boolean>;
}
```

持久化：

- `chan-layers-v2-{symbol}-{timeframe}` 增加 `segments`。
- 旧配置没有该字段时默认为 `true`。
- 非法状态回退为全部系统结构可见。
- 普通手动画线段的可见性和系统线段开关互不影响。

### 图层控制

在现有“笔 / 中枢 / 走势”图标旁新增“线段”图标：

- `aria-label`
- `aria-pressed`
- `title`
- 显示/隐藏状态
- 使用当前周期笔颜色作为色标，但色标显示为虚线。
- 移动端控件允许横向滚动，不遮挡主图。
- 页面不新增或复用手动画图工具中的“线段”按钮。

### 图表构建器

新增 `buildSegmentSeries()`：

- 分时图 `timeframe="1"` 不显示系统线段。
- `segments=false` 时返回空系列。
- 只绘制 `role="line_segment"` 且日期、价格、方向有效的记录。
- 线段颜色调用当前周期的 `periodStructureColor()`，与笔一致。
- `lineStyle.type = "dashed"`。
- 已确认和 provisional 都用虚线；provisional 可降低透明度并在 tooltip 中标记“未完成”。
- 线段绘制层位于 K 线之上、笔层附近，不能遮挡笔端点。
- 点击线段时在证据面板显示：
  - 方向
  - 状态
  - 起止日期/价格
  - 消费的笔
  - 特征序列
  - 包含处理结果
  - 结束原因
  - 确认时间
- 笔仍保持现有实线和周期色。
- 中枢、走势和普通绘图行为不改变。

### 分页合并

前端加载历史页时：

- `segments` 按 ID 去重。
- 不因分页顺序覆盖不同状态的记录。
- 新页若补充更完整的线段证据，按同一 ID 合并路径/证据字段。
- 不重新计算线段，只合并 API 返回结果。

## 五、测试与验收

### 线段算法单元测试

新增 `tests/test_segments.py`，夹具完全脱离生产数据库，覆盖：

- 少于三笔不生成线段。
- 前三笔无共同重叠不生成线段。
- 前三笔有共同重叠时生成候选线段。
- 向上段使用向下笔特征序列。
- 向下段使用向上笔特征序列。
- 特征序列包含关系从左到右处理。
- 只考察向上段顶分型。
- 只考察向下段底分型。
- 第一种情况：无缺口时在分型极值结束。
- 第二种情况：有缺口且反向特征序列出现分型时结束，不要求缺口回补。
- 只有单笔破坏时不确认旧线段结束。
- 必须由新线段破坏旧线段。
- 复杂/古怪线段按 PDF 规则保持为一段或拆成多段。
- 相邻线段方向交替，不能出现同方向相邻线段。
- 不跨 `continuous_range_id`、行情缺口或序列边界。
- 最右侧候选为 provisional，确认前不使用未来笔。
- 固定输入重复计算 ID、边界、状态和特征序列完全一致。

### 持久化与 API 测试

- 旧库迁移后存在 `period_segments`。
- 新 run 能写入和读取线段。
- `structure_version` 包含线段结果。
- 失败事务不会切换活动 run。
- API 返回 `segments`，且引用笔均存在。
- 分页线段边界和相关笔引用正确。
- 不同 `structure_level` 参数不影响线段返回。
- `health` 返回 v14 规则版本、层级版本和指纹。
- 现有 v13 中枢/走势字段兼容测试继续通过，除版本断言外不改变既有结构语义。

### 前端测试

新增或修改 Vitest：

- 默认绘制全部系统线段。
- 关闭“线段”图层后所有系统线段消失。
- 笔仍为实线，线段为虚线。
- 线段与笔使用同一周期颜色。
- provisional 线段仍为虚线并显示未完成状态。
- 点击线段可定位到对应结构记录。
- 普通手动画线段不受系统线段开关影响。
- 分时图不生成系统线段系列。
- 分页合并不丢失或重复线段。
- 旧 `chan-layers-v2` 配置缺少 `segments` 时自动默认为显示。

### 全量重算与运行验收

实施阶段执行：

1. 保存并回读本计划文件。
2. 更新规则库、计算器指纹和版本断言。
3. 在临时数据库运行后端全量测试。
4. 运行前端全量测试和生产构建。
5. 停止并确认不存在旧服务进程。
6. 备份主库及 WAL，执行 `PRAGMA quick_check` 和关键表行数核对。
7. dry-run 输出所有 enabled 标的和 `5/30/d/w/m` 周期执行矩阵。
8. 对矩阵逐项强制重算 v14 线段。
9. 任一周期失败则不切换活动 run，并从已验证备份恢复。
10. 全部成功后运行结构审计：
    - 所有活动 run 为 v14；
    - 指纹与当前代码一致；
    - 线段笔引用完整；
    - 无跨连续区间线段；
    - 线段方向、端点和特征序列满足 PDF 规则；
    - provisional 仅出现在允许的最右侧未完成位置。
11. 启动服务，通过 health、chart-data 和实际页面复核线段图层。
12. 对 `1A0001` 日线及其他 enabled 标的检查线段数量、边界和页面显示；不预设未经计算的具体数量。

## 六、假设与边界

- 本次新增的是系统生成的缠论线段，不是普通手动画图线段。
- 线段按当前行情周期的笔生成，不跨周期、不把日线走势写入周线笔或线段。
- 不修改现有笔规则、包含关系处理、L1/L2/L3 中枢升级公式、同级别走势分解、买卖点和背驰逻辑。
- 线段结果作为结构快照的一等数据保存，但本次不让线段替换现有 v13 中枢输入。
- 最右侧 provisional 线段允许展示，确认前不锁定最终终点。
- 旧 v13 run、行情、自选分组、人工绘图和人工结构修订全部保留。
- 若实现中发现必须改变中枢输入、跨周期语义或线段定义，则先保存新的完整计划版本，再继续修改。
- 当前仍处于计划阶段；计划文件尚未写入，业务代码尚未修改。
