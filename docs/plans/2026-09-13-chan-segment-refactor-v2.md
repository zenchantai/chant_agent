# 缠论线段定义重构与主图独立展示

计划文件目标路径：

`/Users/saber/Desktop/saber/投资/缠论/chant_agent/docs/plans/2026-09-13-chan-segment-refactor-v2.md`

## 一、目标与当前问题

重构现有 v14 线段实现，使其严格遵循《读缠论108课札记》相关原文，并解决两个核心错误：

- 当前算法把“出现三笔”与“线段结束”混为一谈；
- 当前线段绘制沿消费笔端点画折线，视觉上等同于笔；
- 当前相邻线段复用了整个边界笔，而正确关系应是只共享转折点；
- 当前 provisional 线段追加新笔时可能改变身份，分页合并不稳定；
- 当前部分复杂线段在单笔破坏时过早切段。

主图采用“线段真实起点到真实终点的独立虚线”显示。消费笔、特征序列、包含处理和结束依据保留在 API 与证据面板中，不再把所有消费笔重画成线段。

本次属于线段持久化语义和边界归属的实质变化，规则版本升级为：

- `chan-period-center-hierarchy-cache-fingerprint-v15`
- `center-hierarchy-cache-fingerprint-v15`
- `SEGMENT_ALGORITHM_VERSION = chan-segment-feature-sequence-v2`

旧 v14 run 保留，不删除。

## 二、严格规则依据

唯一依据为：

`/Users/saber/Desktop/saber/投资/缠论/读缠论108课札记.pdf`

重点按以下页码和课次实现：

- 第 395-397 页：笔和线段的基本形态；
- 第 412-413 页：笔破坏、三笔重叠和线段破坏；
- 第 419-421 页：特征序列、标准特征序列、两种结束情况；
- 第 445-446 页：中间状态和复杂线段；
- 第 490-496 页：方向约束、线段被线段破坏、古怪线段和标准化。

必须保留以下定义：

- 线段至少三笔；
- 前三笔必须严格存在共同重叠；
- 向上段只能从向上笔开始，向下段只能从向下笔开始；
- 向上段使用向下笔构成特征序列；
- 向下段使用向上笔构成特征序列；
- 特征序列只在同一线段内部做包含处理，并从左到右处理；
- 向上段只研究顶分型；
- 向下段只研究底分型；
- 第一种结束：分型第一、第二元素之间无缺口，在分型极值处结束；
- 第二种结束：第一、第二元素有缺口，从该极值开始的反向笔序列出现特征序列分型即可结束，不要求缺口回补；
- 单笔破坏不能直接确认旧线段结束；
- 旧线段只有在新线段形成并完成破坏时才结束；
- 复杂线段不能因为单笔越界直接切段；
- 后续分析可以使用标准化高低点，但必须保留真实边界。

## 三、算法重构

### 1. 输入归一化与边界

`app/segments.py` 改为纯函数状态机，输入为同一连续区间内的已确认笔。

每条笔统一为：

- `id`
- `direction`
- `start_date/end_date`
- `start_price/end_price`
- `low/high`
- `continuous_range_id`
- `sequence_id`
- `ordinal`

以下边界为硬隔离：

- 不跨 `continuous_range_id`；
- 不跨 `sequence_id`；
- 不跨行情缺口；
- 不跨笔序列断点；
- 不使用 provisional 笔确认历史线段。

### 2. 候选线段

从候选起点开始：

1. 检查连续三笔；
2. 检查三笔方向是否构成有效交替；
3. 检查三笔是否严格共同重叠；
4. 不满足时向右寻找下一个合法候选起点；
5. 满足时建立 provisional/confirmed 候选。

以向上段为例：

```text
S1 X1 S2 X2 S3 X3 ...
```

特征序列：

```text
X1 X2 X3 ...
```

以向下段为例：

```text
X1 S1 X2 S2 X3 S3 ...
```

特征序列：

```text
S1 S2 S3 ...
```

### 3. 特征序列

每个特征元素包含：

- `source_pen_ids`
- `low/high`
- `start_date/end_date`
- `gap_before`
- `gap_after`
- `inclusion_merged`
- `inclusion_sources`

包含处理要求：

- 只处理当前候选线段内部的特征元素；
- 从左到右处理；
- 第一种结束情况下，候选分界点两侧不能被错误地跨界包含；
- 第二种结束情况下的反向特征序列必须重新按包含规则处理。

### 4. 结束点与笔归属

这是本次重构的关键。

当向上段的特征序列顶分型在某个向下笔的起点形成时：

- 线段终点是该向下笔的起点；
- 该向下笔不属于前一条线段的 `pen_ids`；
- 它属于下一条向下候选线段；
- 前后线段只共享同一个转折点，不共享整个边界笔。

向下段同理：

- 特征序列底分型极值对应的向上笔不属于前一条向下线段；
- 它作为下一条向上线段的首笔。

每条线段增加明确边界字段：

- `start_pen_id`
- `end_pen_id`
- `termination_pen_id`
- `start_point`
- `end_point`
- `standard_start_price`
- `standard_end_price`

其中：

- `start_price/end_price` 保存真实边界；
- `low/high` 保存真实区间；
- `standard_start_price/standard_end_price` 用于后续把线段视为基本单元；
- `termination_pen_id` 可以指向形成终点的下一笔，但不计入前一段消费笔。

这样可以保证：

```text
上一线段 end_point == 下一线段 start_point
上一线段 pen_ids 与下一线段 pen_ids 不重复
```

### 5. 两种结束情况

第一种：

- 标准特征序列形成目标顶/底分型；
- 第一、第二特征元素无缺口；
- 在分型极值处提出终止候选；
- 只有当终止点之后能够形成下一条合法线段时，才确认旧线段结束；
- 否则保持原线段延伸。

第二种：

- 标准特征序列形成目标顶/底分型；
- 第一、第二特征元素之间有缺口；
- 从该极值对应的反向笔开始重新构建反向特征序列；
- 反向特征序列出现任意合法分型即可确认；
- 不要求原缺口回补；
- 反向序列包含关系必须独立处理。

### 6. 复杂和古怪线段

加入明确的中间状态：

- `pending_pen_break`
- `pending_reverse_segment`
- `pending_inclusion`
- `confirmed`
- `provisional`

规则：

- 单笔越过旧线段边界，只记录笔破坏；
- 后续反向三笔未形成合法线段时，旧线段继续；
- 如果反向线段形成并破坏旧线段，才确认分界；
- A/B/C 三段在第二种情况下不能仅因第三段直接创新高/新低就强行拆成三段；
- 第二特征序列的包含处理完成后仍无分型，则合并为同一条线段；
- 已确认前缀不能被未来笔重写。

### 7. 稳定身份

线段 ID 不再由全部消费笔计算。

稳定 ID 输入固定为：

```text
continuous_range_id
sequence_id
起始笔 ID
线段方向
候选序号
```

结果：

- provisional 追加新笔时保持相同 ID；
- provisional 转 confirmed 时保持相同 ID；
- 已确认线段的 ID、边界和 `pen_ids` 不因右侧新数据重算而改变；
- 前端分页可以按 ID 合并，不会把同一候选拆成多个对象。

## 四、持久化与版本

### 数据库

保留 `period_segments` 表，不新增人工线段修订接口。

payload 增加：

- `start_pen_id`
- `end_pen_id`
- `termination_pen_id`
- `start_point`
- `end_point`
- `standard_start_price`
- `standard_end_price`
- `inclusion_merged`
- `pending_reason`

`replace_period_structure()` 继续在同一事务内写入：

- processed bars
- fractals
- pens
- segments
- centers
- relations
- movements

失败事务不得切换活动 run。

### 版本和指纹

- `app/segments.py` 纳入计算器指纹；
- `structure_version` 继续包含 `segments`；
- 规则库 `knowledge/chan_rules.yaml` 增加 v15 线段边界归属和状态机规则；
- `knowledge/chan_rules.md` 更新定义、边界笔归属和主图显示语义；
- v14 活动快照自然失效；
- 旧 v14/v13 历史 run 保留。

## 五、API 行为

`GET /api/chart-data/{symbol}`：

- 返回 `segments`；
- 线段按日期范围相交裁剪；
- 返回真实 `start_date/end_date/start_price/end_price`；
- 不返回越界路径点；
- 线段引用的所有 `pen_ids` 必须补入 `pens`；
- `structure_level` 仍兼容但不影响线段；
- `active_structure_level` 固定为 `1`；
- `segment_count` 可选返回；
- `segments` 不与普通 `drawings` 合并。

线段 API 记录必须满足：

- `role == "line_segment"`
- `len(pen_ids) >= 3`
- `pen_ids` 连续且不跨区间；
- 相邻线段不重复消费边界笔；
- 相邻线段端点连续；
- provisional 只能是每个连续序列最右侧未完成候选；
- `termination_pen_id` 不得被误计入上一线段消费笔。

## 六、前端展示

### 图表语义

`buildSegmentSeries()` 采用首尾独立直线：

- 每条线段主图只生成两个绘制端点；
- `lineStyle.type = "dashed"`；
- 颜色与当前周期笔颜色一致；
- provisional 降低透明度；
- 不使用 `path_points` 绘制内部笔折线；
- 不把线段画成笔的虚线副本；
- 笔继续使用实线；
- 线段绘制层位于 K 线和笔层之上但不遮挡笔端点。

`path_points` 如保留，仅用于详情、审计或调试，不参与主图绘制。

### Tooltip 和证据面板

点击线段时显示：

- 方向；
- 状态；
- 真实起止日期和价格；
- 标准化起止价格；
- 消费笔列表；
- 起始笔、结束笔、终止笔；
- 特征序列；
- 包含处理结果；
- 是否存在缺口；
- 结束情况；
- 结束原因；
- 确认时间。

### 图层开关

保留独立“线段”开关：

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

- `segments=false` 隐藏全部系统线段；
- 不影响普通手动画线段 `object_type="segment"`；
- 配置迁移沿用 `chan-layers-v2-{symbol}-{timeframe}`；
- 缺失 `segments` 时默认为 `true`；
- 分时图不显示系统线段。

### 分页合并

- `segments` 按稳定 ID 去重；
- provisional 与 confirmed 使用同一 ID；
- 不覆盖更完整的证据字段；
- 不因分页顺序重复或丢失线段；
- 线段引用笔继续合并到 `pens`。

## 七、测试计划

### 线段纯函数测试

新增或重写 `tests/test_segments.py`：

- 少于三笔不生成线段；
- 前三笔无共同重叠不生成线段；
- #144/#145/#146 这类三笔生成同一条候选线段；
- 三笔满足最小条件但没有分型时保持 provisional；
- 向上段只使用向下笔特征序列；
- 向下段只使用向上笔特征序列；
- 标准特征序列从左到右处理包含；
- 向上段只识别顶分型；
- 向下段只识别底分型；
- 第一种无缺口结束；
- 第二种有缺口且反向特征序列有分型结束；
- 有缺口但反向序列无分型时原段继续；
- 缺口不要求回补；
- 单笔破坏不确认旧段结束；
- 新线段形成后才确认旧段结束；
- 复杂 A/B/C 场景按 PDF 合并或拆分；
- 相邻线段方向交替；
- 相邻线段只共享端点，不共享边界笔；
- `len(pen_ids) >= 3`；
- 不跨连续区间、缺口或序列边界；
- provisional 转 confirmed 保持 ID；
- 已确认前缀不被未来笔改写；
- 重复输入得到完全相同的 ID、边界、状态和证据。

### 持久化和 API 测试

- 旧库迁移后 `period_segments` 可读写；
- 新字段能够持久化和加载；
- 失败事务不切换活动 run；
- API 返回完整线段；
- 所有线段至少引用三笔；
- 线段引用笔均存在；
- 分页边界正确；
- 结构级别参数不影响线段返回；
- `active_structure_level == 1`；
- v15 规则版本和计算器指纹一致；
- 健康接口返回 `segment_algorithm_version`；
- 审计检查相邻线段端点连续且边界笔不重复。

### 前端测试

- 默认绘制全部系统线段；
- `segments=false` 后全部消失；
- 笔为实线，线段为虚线；
- 线段颜色与笔颜色一致；
- 每条线段主图数据恰好为两个结构端点；
- `pen_ids` 多于等于三笔时仍只绘制独立首尾线；
- provisional 虚线并显示未完成；
- 点击线段可定位结构记录；
- 普通手动画线段不受系统开关影响；
- 分时图不显示系统线段；
- 分页合并不丢失或重复；
- 缺失 `segments` 的旧配置默认为显示。

## 八、执行与验收顺序

1. 执行阶段第一步，把本计划完整 Markdown 原文保存到目标路径；若已存在，使用 `-2`、`-3` 后缀。
2. 立即回读，核验文件非空、章节完整、规则/API/实现/测试/假设没有丢失。
3. 修改线段纯函数状态机和边界笔归属。
4. 更新 v15 规则、层级版本、指纹和结构版本输入。
5. 更新数据库 payload、加载、分页和审计逻辑。
6. 更新前端线段构建器，使主图只画独立首尾虚线。
7. 更新线段 tooltip、证据面板和图层状态。
8. 运行后端测试、前端测试和生产构建。
9. 停止旧 Uvicorn，确认无残留进程。
10. 备份主库及 WAL，执行 `PRAGMA quick_check` 和关键表行数核对。
11. dry-run 输出 enabled 股票和 `5/30/d/w/m` 矩阵。
12. 对所有矩阵项强制生成 v15 结构。
13. 任一项失败则不接受混合活动状态，并从备份恢复。
14. 全部成功后运行数据库审计：
    - 所有活动 run 为 v15；
    - 指纹与当前代码一致；
    - 每条线段至少三笔；
    - 相邻线段只共享端点；
    - 无跨连续区间；
    - provisional 仅位于允许的最右侧；
    - 无缺失笔引用。
15. 启动服务并验证：
    - `/api/health`
    - `/api/chart-data/1A0001?timeframe=d`
    - `1A0001` 日线 #144/#145/#146 对应候选；
    - 页面中笔为实线、线段为独立虚线；
    - 线段开关和分页行为正常。

## 九、明确边界与假设

- 线段主图采用“真实首尾直线”，不绘制消费笔折线；
- 消费笔和特征序列只作为证据，不改变线段主图几何；
- 线段至少三笔，三笔只代表候选成立，不代表线段已经结束；
- 相邻线段只共享转折点，不共享整个边界笔；
- 不修改现有笔、中枢、走势、买卖点和背驰算法；
- 不让线段替换现有中枢输入；
- 不新增人工线段修订接口；
- 不修改行情数据；
- 不删除 v14/v13 历史 run；
- 由于本次改变了线段边界归属、稳定 ID 和主图语义，规则版本从 v14 升级到 v15；
- 计划阶段不修改业务代码、不执行生产重算。
