# 缠论图表全级别中枢与走势展示

计划文档目标路径：

`/Users/saber/Desktop/saber/投资/缠论/chant_agent/docs/plans/2026-09-13-all-level-structure-display.md`

## 一、目标与范围

将图表从“单一当前结构级别”改为“同一响应返回并绘制全部可用级别”。

- 中枢同时展示 L1、L2、L3 及未来新增级别。
- 走势同时展示所有级别的 `same_level_decomposition`。
- 删除顶部“级别”选择器及其 localStorage 状态。
- 保留颜色区分级别：
  - 中枢继续使用现有 `levelStructureColor`。
  - 走势主体、箭头和端点统一使用级别颜色。
  - 上涨/下跌由箭头方向区分。
  - provisional/candidate 继续使用虚线表示。
- 不修改 v13 结构计算、升级公式、中枢存储、父子结构关系及全量重算结果。

## 二、后端接口与数据行为

### `GET /api/chart-data/{symbol}`

- 保留 `structure_level` 查询参数，作为兼容字段，但忽略其值。
- 无论是否传入 `structure_level=1/2/3`，均返回当前页面范围内全部级别的：
  - `centers`
  - `pen_centers`
  - `movements`
  - `center_relations`
- 每条结构保留自身的 `level`、`display_period`、`color_key`、`status`、`progress` 等元数据。
- `center_levels` 和 `movement_levels` 返回响应中实际存在的全部级别。
- `active_structure_level` 暂时保留为兼容字段，固定为 `1`，新前端不得再使用它进行过滤。
- `decomposition` 继续返回 L1 分解状态，避免破坏现有证据面板；不改变分解算法。
- `calculator_fingerprint`、`structure_version`、`run_id` 等 v13 字段保持不变。

### 分页与引用

- 中枢按页面日期范围裁剪，但为了保证绘图和证据引用完整，相关中枢涉及的笔全部纳入 `pens`。
- 走势继续按页面范围裁剪 `path_points`。
- `center_relations` 只返回与当前页面可见中枢有关的关系。
- 翻页合并时按结构 ID 去重，不能因不同级别或不同页面覆盖错误记录。
- 不改变数据库数据，不删除旧 run 或人工修订。

## 三、前端交互与状态

### 状态模型

将现有：

```ts
{
  pens: boolean;
  centers: boolean;
  movements: boolean;
}
```

扩展为：

```ts
{
  pens: boolean;
  centers: boolean;
  movements: boolean;
  centerLevels: Record<string, boolean>;
  movementLevels: Record<string, boolean>;
}
```

规则：

- 默认所有已返回级别均为显示。
- `centers=false` 时隐藏全部中枢。
- `movements=false` 时隐藏全部走势。
- `centerLevels["2"]=false` 时只隐藏 L2 中枢。
- `movementLevels["3"]=false` 时只隐藏 L3 走势。
- 分类总开关关闭后保留各级别开关状态，再次打开时恢复原级别选择。
- 新增级别默认显示，已存在级别的用户设置继续保留。

### 持久化与迁移

- 删除 `chan-structure-level-{symbol}-{timeframe}` 的读写。
- 使用版本化键：

```text
chan-layers-v2-{symbol}-{timeframe}
```

- 读取旧版 `chan-layers-{symbol}-{timeframe}` 时迁移三个总开关，所有级别默认开启。
- 非法或过期状态回退到全部显示。
- 切换级别可见性只重新生成图表，不重新请求后端。

### 控制区

删除顶部“级别”下拉选择器。

保留“笔 / 中枢 / 走势”三个分类图标，并为中枢和走势增加动态级别图标：

- 中枢：L1、L2、L3……使用对应级别的中枢色块。
- 走势：L1、L2、L3……使用对应级别的走势色线。
- 每个级别图标具有：
  - `aria-label`
  - `aria-pressed`
  - `title`
  - 显示/隐藏状态
- 级别控件只展示 API 实际返回的级别。
- 移动端保持可横向滚动或紧凑排列，不能遮挡主图。

统计信息改为统计当前响应中的全部级别，并按照实际可见状态计数；不再使用 `active_structure_level` 计算中枢数量。

## 四、图表构建逻辑

### `buildCenterAreas`

- 删除按 `active_structure_level` 的过滤。
- 对所有合法 hierarchy center 执行：
  - 日期范围校验；
  - 中枢价格区间校验；
  - 引用校验；
  - 对应级别颜色计算；
  - provisional 虚线边框和进度标签。
- 增加 `centerLevels` 可见性过滤。

### `buildMovementSeries`

- 删除按 `active_structure_level` 的过滤。
- 保留 `role === "same_level_decomposition"` 过滤。
- 增加 `movementLevels` 可见性过滤。
- 走势主体、箭头、端点统一调用级别颜色：
  - `levelStructureColor(theme, timeframe, nodeLevel(movement))`
- provisional 走势仍使用虚线。
- 上涨/下跌方向由箭头朝向保留，不再通过红绿主体颜色表达。

### `ChartBuildContext`

扩展可见性类型，兼容缺失字段：

- 未提供级别映射时默认全部显示。
- 保留旧测试和旧调用方传入三个布尔字段的能力。
- 不再依赖 `active_structure_level` 决定绘制内容。

### 类型与辅助函数

- 更新 `ChartData` 和图表上下文类型，明确 `active_structure_level` 为兼容字段。
- 增加统一的级别可见性判断和可见级别初始化辅助函数。
- 保留 `centersForStructureLevel` 供兼容测试或详情逻辑使用，但主图不再调用它筛选全部数据。

## 五、测试计划

### 后端

新增或修改 API 测试：

- 传入 `structure_level=1`、`2`、`3` 均返回全部级别。
- `center_levels`、`movement_levels` 与响应实际记录一致。
- 中枢和走势混合返回 L1/L2/L3。
- 所有走势仍为 `same_level_decomposition`。
- `path_points` 均落在当前分页日期范围内。
- 父子引用和中枢关系在全级别响应下不产生误报。
- `active_structure_level` 保持兼容值，不参与实际过滤。
- 现有 v13 指纹、活动 run、结构版本测试继续通过。

### 前端

新增或修改 Vitest 测试：

- 同时存在 L1/L2/L3 时，`buildCenterAreas` 返回全部可见中枢。
- 同时存在 L1/L2/L3 时，`buildMovementSeries` 返回全部可见走势。
- 关闭中枢总开关后所有中枢系列消失。
- 关闭走势总开关后所有走势系列消失。
- 关闭单个级别后仅对应级别消失。
- 未提供级别可见性映射时默认全部显示。
- 中枢颜色按级别区分。
- 走势线、箭头、端点颜色按级别区分。
- provisional 中枢和走势仍为虚线。
- 删除级别选择器后，旧 localStorage 数据不会阻止图表加载。
- 翻页合并后不同级别结构不会丢失或重复。

### 运行验收

- 后端全量测试不得低于当前基线。
- 前端全量测试通过。
- 前端生产构建通过。
- 通过实际 API 验证 `1A0001` 日线返回全部可用级别。
- 通过页面检查：
  - 默认同时显示全部中枢和走势；
  - L1/L2/L3 颜色可区分；
  - 中枢和走势总开关生效；
  - 各级别图标开关生效；
  - provisional 虚线可见；
  - 翻页和切换股票、周期后状态正确。

## 六、实现顺序

1. 保存本计划完整 Markdown 原文到目标路径；若文件已存在，使用 `-2`、`-3` 后缀。
2. 立即回读并核验文件非空、章节完整、API/实现/测试/假设未丢失。
3. 修改后端 `chart_page` 的全级别返回逻辑及兼容字段。
4. 修改前端 `ChartData`、图表上下文和中心/走势构建器。
5. 修改 `App.tsx`，删除级别选择器，加入总开关和按级别图标开关。
6. 修改样式，适配桌面和移动端级别控件。
7. 更新后端、前端测试。
8. 运行测试、构建和 API smoke test。
9. 检查工作区差异，确认没有修改 v13 计算、数据库数据或无关 UI。

## 七、假设与边界

- 本次只改变展示和 API 返回范围，不重新计算结构。
- 分时图 `timeframe="1"` 继续不展示中枢和走势。
- 走势颜色改为级别语义，方向由箭头表达，候选状态由虚线表达。
- `structure_level` 继续保留是为了兼容旧客户端，但不再支持单级别响应。
- 不增加数据库字段，不修改结构快照和人工修订数据。
- 计划执行阶段若发现必须改变结构计算、数据范围或公共接口契约，先保存新的完整计划版本，再继续修改。
