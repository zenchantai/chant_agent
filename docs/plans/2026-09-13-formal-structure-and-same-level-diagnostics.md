# 正式结构与同级分解隔离重构

计划文档目标路径：

`/Users/saber/Desktop/saber/投资/缠论/docs/plans/2026-09-13-formal-structure-and-same-level-diagnostics.md`

## Summary

将当前混合展示的“正式层级结构”和“同级别分解”拆成两套明确的数据与 UI 语义：

```text
主结构：
笔 -> 正式 L1 笔中枢 -> hierarchy_component -> L2/L3/... 正式中枢

辅助诊断：
低一级单位 -> same_level 中枢 -> same_level_decomposition 走势
```

保留现有同级分解计算，作为可选诊断能力；主图和主计数只使用正式结构。新接口采用版本化重构，前端迁移到 `v2`，旧混合接口进入废弃状态。

## Key Changes

### 1. 后端结构分层与接口

新增版本化接口：

```text
GET /api/v2/chart-data/{symbol}
```

新响应拆成两个独立集合：

```json
{
  "api_version": "v2",
  "structure": {
    "pens": [],
    "centers": [],
    "movements": [],
    "center_levels": [],
    "movement_levels": []
  },
  "decomposition": {
    "centers": [],
    "movements": [],
    "levels": [],
    "meta": {}
  }
}
```

约束：

- `structure.centers` 只允许 `role=hierarchy`；
- `structure.movements` 只允许 `role=hierarchy_component`；
- `decomposition.centers` 只允许 `role=same_level`；
- `decomposition.movements` 只允许 `role=same_level_decomposition`；
- 正式结构和同级分解分别计算数量、级别和编号；
- 新接口中的 `ordinal` 改为在“集合 + level”范围内编号，不再使用跨角色全局编号；
- 保留稳定 `id`、`source_pen_ids`、`child_movement_ids` 等审计引用；
- 增加 `structure_view_version` 和 `decomposition.algorithm_version`，明确当前同级分解仍是产品机械规则，不宣称等同于原文完整递归。

旧的 `/api/chart-data/{symbol}` 保留一个过渡周期，增加 `Deprecation` 响应标识；前端不再使用旧混合字段。

### 2. 后端计算与持久化

不修改现有笔、正式笔中枢、层级递归和同级分解的价格计算公式，只调整输出边界和语义分组：

- 在 `PeriodStructureService` 增加正式结构与诊断结构的分组序列化；
- 将现有混合 `period_pen_centers`、`period_movements` 按 `role` 分离输出；
- 增加结构完整性校验：
  - 正式集合不包含 `same_level`；
  - 诊断集合不包含 `hierarchy`；
  - 每组编号从 1 连续递增；
  - 引用的中枢、走势、笔均存在；
  - 同级走势只能引用同级分解中枢；
  - 正式层级走势只能引用正式层级结构；
- 不修改已有 SQLite 表和历史快照，避免破坏审计数据；
- 不因展示重构改变现有结构 ID；
- 不新增走势人工编辑能力。

### 3. 前端主结构与诊断视图

主图默认只显示：

```text
笔
正式中枢
正式层级结构
```

同级分解改为独立的“同级分解诊断”图层，默认关闭，可单独开启。

界面调整：

- 主计数改为“正式笔 / 正式中枢 / 正式走势”；
- 诊断区域单独显示“同级中枢 / 同级分解走势”；
- 正式结构编号按级别分别显示：
  - `L1 正式中枢 #n`
  - `L2 正式中枢 #n`
- 同级分解编号显示：
  - `L1 同级分解走势 #n`
  - `L1 同级分解中枢 #n`
- 删除含义不明确的“中枢数量：1”；
- 走势详情明确显示：
  - `结构来源：正式层级 / 同级分解`
  - `参考中枢类型`
  - `参考中枢 ID`
  - `构成单位`
  - `构成笔`
- 同级分解中枢使用浅色虚线或低透明度样式，不与正式中枢同色同线型；
- 正式主图不再因为同级分解走势而出现一根笔或五根笔被命名为普通主走势的情况；
- 保留 `笔中枢` 默认模式，但它只控制正式笔中枢，不隐式显示同级诊断结构。

### 4. 图表构建器与类型

拆分现有混合走势构建逻辑：

```text
buildStructuralMovementSeries()
buildDecompositionMovementSeries()
```

分别处理：

- 正式层级走势；
- 同级分解走势。

更新 `ChartData`、`Node` 和相关辅助函数，显式增加：

```text
structure
decomposition
structure_center_levels
structure_movement_levels
decomposition_center_levels
decomposition_movement_levels
```

所有 tooltip、点击选择、端点标记和图层开关都携带 `role` 与 `level`，不再仅依赖全局 `ordinal`。

### 5. 兼容与迁移

- 前端切换到 `/api/v2/chart-data/{symbol}`；
- 旧接口继续返回旧格式但标记废弃，不再新增字段；
- 旧 localStorage 中的走势显示设置迁移为：
  - `formal-structure-visible-{symbol}-{timeframe}`
  - `same-level-diagnostics-visible-{symbol}-{timeframe}`
- 默认值：
  - 正式结构：开启；
  - 同级分解诊断：关闭；
- 不迁移旧的混合编号作为新编号；
- 手动画线、结构修订和股票池功能保持不变；
- 不删除旧数据库表或旧快照。

## Test Plan

### 后端

新增和更新测试：

- `v2` API 只返回正式结构与诊断结构各自允许的 role；
- 正式中枢、同级中枢编号分别从 1 开始且连续；
- 正式走势和同级走势编号互不混淆；
- 同级分解仍满足：
  - 三单位严格重叠；
  - 走势方向交替；
  - 相邻走势只共享端点；
  - 不跨连续行情缺口；
  - 右侧未完成走势为 `provisional`；
- 正式层级结构引用不包含 `same_level` 节点；
- 使用当前上证指数快照验证：
  - 中枢 #30 被归入正式 L2 结构；
  - 原走势 #33、#61 被归入同级分解诊断；
  - 新编号不再沿用混合全局编号；
- 历史快照和稳定结构 ID 不被改写。

### 前端

- 默认主图不绘制 `same_level_decomposition`；
- 打开诊断开关后才绘制同级分解走势；
- 正式中枢和同级中枢使用不同样式；
- tooltip 明确展示来源类型；
- 正式结构和诊断结构计数分离；
- 各级别编号独立；
- localStorage 设置按股票和周期持久化；
- 翻页后结构不会因编号或 role 混合而重复、丢失；
- 移动端和桌面端无横向溢出，图层控制不重叠。

### 构建与运行

```bash
cd /Users/saber/Desktop/saber/投资/缠论/chant_agent
uv run --with-requirements requirements.txt --with pytest python -m pytest -q

cd web
npm test -- --run
npm run build
```

启动新服务后验证：

```text
GET /api/v2/chart-data/1A0001?timeframe=d&limit=300
```

并检查上证指数日线实际响应中：

- `structure.centers` 不含 `role=same_level`；
- `decomposition.movements` 包含原 #33、#61 对应的同级分解走势；
- 主图默认不绘制诊断走势；
- 打开诊断后能够定位到对应同级中枢和构成笔。

## Assumptions

- 这次重构的目标是修正结构语义和展示，不重新定义笔、线段或正式中枢数学规则。
- 同级别分解继续保留在后端，用于诊断和研究，不作为主结构结论。
- 新接口采用 `/api/v2/chart-data/{symbol}`，旧接口只做过渡兼容并标记废弃。
- 正式主结构默认优先于同级分解诊断。
- 不把当前实现重新包装成“完全等同于缠论原文”，界面和接口中明确标注其为产品机械分解。
- 用户确认计划后，执行阶段第一步将本计划原文完整写入上述目标文件并回读核验；计划文件确认成功后才修改业务代码。
