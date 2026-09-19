# 笔中枢、非同级别分解与结构买卖点重构

## Summary

将 `chant_agent` 从当前 v22“无中枢组件 + 反向中枢/买卖点混合切分”重构为：

```text
当前周期K线
→ 当前周期确认笔
→ 严格三笔核心形成 L1 中枢
→ 中枢延伸、扩张、新生
→ 结构背驰、盘整背驰及一二三类买卖点
→ 买卖点确定非同级别走势边界
→ 根据中枢数量和关系判定盘整/上涨/下跌
→ 完成走势与中枢扩张共同递归形成 L2/L3...
```

这是“以笔为最低走势单位”的周期内简化模型，不冒充原文真实跨周期递归。理论语义以 `/Users/saber/Desktop/saber/投资/缠论/读缠论108课札记.pdf` 第17、20、21、29、38课为主。:codex-file-citation{path="/Users/saber/Desktop/saber/投资/缠论/读缠论108课札记.pdf" purpose="source"}

进入执行阶段后，第一步将本计划完整原文保存至：

`/Users/saber/Desktop/saber/投资/缠论/chant_agent/docs/plans/2026-09-17-non-same-level-pen-center-movement-refactor.md`

如重名则递增编号，立即回读校验后才允许修改业务代码。保留当前未提交的历史分页修复，不覆盖 `web/src/App.tsx`、`web/src/chartInteractions.ts`、`web/src/chartInteractions.test.ts` 中现有改动。

## Implementation Changes

### 1. 统一最低单位与方向性中枢

- L1 正式核心必须是三根连续、确认、方向交替、同一行情连续区间的笔；按 `ZD=max(low1,low2,low3)`、`ZG=min(high1,high2,high3)` 计算，要求 `ZD+1e-9<ZG`，单点接触不成枢。
- 进入对象可以是一笔，也可以是由三、五、七等奇数笔构成且内部不存在三笔中枢种子的无中枢走势段；选择紧邻核心、连续且最长的合法进入段，方向取其整体方向。
- 进入段仅是方向与买卖点上下文，不属于中枢核心和 `owned_unit_ids`。中枢核心不得跨已确认走势边界。
- 正式字段统一为：
  ```text
  entry_component_id / entry_unit_ids
  core_unit_ids
  extension_unit_ids
  peripheral_unit_ids
  departure_unit_ids
  retest_unit_ids
  owned_unit_ids
  context_unit_ids
  fixed_zd / fixed_zg
  dd / gg
  fluctuation_dd / fluctuation_gg
  formation_mode
  direction
  status
  ```
- 左到右唯一消费：发现最早合法核心后锁定三根核心笔；后续核心不得重复拥有这些笔。上下文可以引用相邻走势的末端，但不能产生源笔双重归属。

### 2. 实现非同级别中枢状态机

- 核心形成后固定 `ZD/ZG`。后续波动与核心严格重叠时归入延伸；暂时离开后在独立新核心形成前返回，归入外围震荡；最终离开、回试仅作为上下文，不扩大固定核心。
- 新核心与当前核心严格重叠时吸收为延伸，不创建重复中枢。
- 新核心与当前核心严格分离时：
  - 两个中枢的外围波动包络也严格分离：形成同级新生中枢，可参与趋势。
  - 核心分离但 `fluctuation_envelope=[fluctuation_dd, fluctuation_gg]` 严格重叠：触发中枢级别扩张。
- 扩张生成 `level+1` 父中枢：
  ```text
  parent.zd = max(left.fluctuation_dd, right.fluctuation_dd)
  parent.zg = min(left.fluctuation_gg, right.fluctuation_gg)
  ```
  要求严格重叠；父中枢引用两个子中枢、连接走势段和完整源笔，方向取进入第一个子中枢系统的低级走势方向。
- 扩张只新增父结构，不删除、重标或转移低级中枢。若“三个完成低级走势递归”和“扩张”指向同一父结构，按级别和源区间合并为一个父中枢，记录多个 `formation_modes`，禁止重复对象。
- 不允许按九笔、九段或固定数量自动升级。中枢扩张必须有两个完整同级中枢及外围波动严格重叠证据。

### 3. 重写结构背驰与买卖点

- MACD 完全退出买卖点判定，只保留为图表指标和可选诊断数据。
- 趋势一买/一卖比较最后两个同级中枢之间的同向连接段 `b` 与最后中枢后的离开段 `c`：
  ```text
  amplitude = abs(end_price - start_price)
  bar_span = 实际行情K线数量
  slope = amplitude / max(1, bar_span)
  ```
  `c` 必须创新极值，同时满足 `c.amplitude <= b.amplitude + epsilon` 且 `c.slope < b.slope - epsilon`，才形成结构背驰候选。
- 盘整背驰独立建模为：
  ```text
  consolidation_divergence_buy
  consolidation_divergence_sell
  ```
  不冒充趋势一买/一卖。同一中枢两次同向离开中，后一段不创新极值，或创新极值但满足上述力度减弱条件，即形成盘背候选。
- 一买/一卖点位取最后离开段真实最高/最低笔端点；二买/二卖要求依赖已确认一买/一卖，回试不得破坏一买/一卖极值；三买/三卖要求完整离开核心并以反向无中枢走势段回试，回试严格不进入核心。
- 买卖点状态统一为 `candidate / confirmed / invalidated`：
  - `point_date/point_price` 始终是真实转折端点；
  - `confirmed_at` 是后续反向笔或回试走势完成、证据可用的时间；
  - 新极值出现时旧候选失效，生成新候选，不原地改写历史证据。
- 反向独立中枢不再直接创建走势结束边界，只能作为已有买卖点或盘背点的追加确认事件；所有正式走势端点必须追溯到结构点位。

### 4. 用买卖点驱动非同级别走势

- 每一级分别运行走势状态机。买点启动向上候选，卖点启动向下候选；数据最左侧没有合法锚点的单位保持 `unassigned`。
- 候选买卖点立即建立 `provisional` 边界；点位确认后锁定为 `confirmed`；候选失效时仅重算最后一个已确认边界之后的后缀。
- 一个合法走势区间必须：
  - 源单位连续且不重复；
  - 至少完整包含一个同级中枢；
  - 不切开任何中枢核心；
  - 起止点为真实低级单位端点；
  - 买卖方向交替；
  - 边界与走势方向一致。
- 分类规则：
  - 一个同级中枢：`consolidation`；
  - 两个以上同向、核心及外围波动均依次严格分离的同级中枢：`trend`，方向为上涨或下跌；
  - 核心分离但外围波动重叠：不判同级趋势，转入父级扩张结构；
  - 无法完成上述分类：`state=undetermined`、`classification=null`、`recursive_eligible=false`。
- 三类买卖点都是候选边界证据，但只有“最早、方向相反、能够形成完整合法走势区间”的点才提交正式边界；二买/二卖和三买/三卖不会机械地把一个完整走势切碎。
- 相邻走势只共享价格端点，不共享整笔。确认走势在底层行情和笔不变时保持前缀稳定；最右侧走势始终允许 `provisional`。
- 仅 `confirmed` 且分类明确的低级走势可作为标准递归单位；扩张路径可直接生成父中枢，但不能伪造已完成低级走势。

### 5. 版本、持久化与 API

- 发布独立新版本，例如：
  ```text
  definition_version = chan-period-pen-center-non-same-level-v23
  hierarchy_version = center-hierarchy-v23-non-same-level-expansion
  decomposition_mode = non_same_level
  center_construction_mode = strict_three_pen_core
  movement_confirmation_mode = structural_point_boundary
  divergence_mode = price_slope_and_amplitude
  ```
- 保留现有明细表和历史运行，不原地改写旧快照。新字段继续存入 JSON payload；如确需新增运行级元数据列，只做可逆 `ALTER TABLE ADD COLUMN`。
- `movements` 仅返回正式层级走势及最右侧候选走势，新增或统一：
  ```text
  boundary_point_id
  boundary_point_type
  boundary_status
  confirmation_point_id
  corroborating_center_ids
  formation_mode
  classification
  recursive_eligible
  ```
  `confirmation_center_id` 保留兼容读取，但不再作为正式结束的唯一证据。
- `buy_sell_points` 增加盘整背驰类型、力度比较数据、候选失效原因和 `point_date/confirmed_at` 双时间。
- `/api/chart-data/{symbol}` 路径保持不变；分页必须补齐走势、中枢、边界点和上下文走势段，分页不能改变结构编号、归属或状态。
- `/api/health` 返回新分解模式、背驰模式、扩张模式及实际最高结构层级。
- 人工笔和人工中枢修订继续关闭；普通绘图与现有行情、分时刷新、自选列表不在本次重构范围内。

### 6. 前端展示

- 当前周期图默认只显示 L1 走势；L2/L3 通过现有级别筛选查看，明确标注为“当前周期数据域内部层级”。
- `provisional` 走势和候选买卖点使用虚线、低透明度；确认后改为实线，不改变真实点位日期。
- 走势详情展示起止点、点位类型、确认时间、盘整/趋势分类、中枢数量、扩张证据和递归资格。
- 中枢选择高亮区分进入上下文、三笔核心、延伸、外围、离开和回试；矩形只覆盖固定核心区间。
- 将端点序号 `H1/L1/H2/L2` 改为“高点1/低点1”等不会与结构级别混淆的标签。
- 保留并合并当前未提交的历史分页视野修复，不回退已通过的拖动加载行为。

## Test Plan

### 单元与不变量测试

- 严格三笔重叠成枢；单点接触、非连续笔、跨缺口笔不成枢。
- 进入段可为1/3/5笔且内部无中枢；进入段不属于核心和拥有单位。
- 左到右扫描不产生滑窗重复中枢；核心笔不得被两个同级中枢重复拥有。
- 中枢延伸、离开返回、新生和扩张分别命中唯一分支。
- 核心分离但外围重叠生成父级扩张中枢；核心和外围均分离形成同级趋势；九笔数量不能单独升级。
- 一买一卖、盘整背驰按价格幅度和K线平均斜率判定；MACD变化不影响结果。
- 二买二卖不破一买一卖极值；三买三卖严格离开并回试不进入核心。
- 候选点先形成 provisional 边界；创新极值后旧候选失效并重建；确认后历史边界冻结。
- 反向中枢只能追加佐证，不能单独结束走势或推迟端点。
- 一个中枢分类为盘整，两个以上严格分离中枢分类为趋势，扩张关系不得误判同级趋势。
- 未确认、待定或被扩张吸收的低级走势不得作为标准父级递归单位。
- 确认走势在追加行情时保持前缀稳定。

### 真实行情回归

- 科创50日线：
  - 2021-03-16 必须生成可审计的结构转折候选，不允许因 MACD 条件被直接忽略；
  - 前一走势不得仅因后续中枢完整结束而推迟到 2021-05-11；
  - 下一走势从实际结构点开始；
  - 2021-08-06 是否成为正式卖点由新结构力度状态机决定，测试必须输出比较段、幅度、K线数量、斜率、点位状态和未命中原因，不硬编码结果。
- 科创50 2022-04-27 至 2022-07-04：区分低点候选、一买、三买确认和上涨走势真实起点，三买不得把边界倒推到中枢核心起点。
- 上证指数日线：重叠中枢不得重复生成同级对象；外围重叠时验证扩张升级而非误判趋势。
- 为上述案例保存标准笔、中枢核心、比较走势段和买卖点证据夹具，禁止只按最终日期断言。

### API、数据库与浏览器验收

- 后端运行 `pytest -q`，前端运行 `npm test -- --run` 与 `npm run build`，并执行 `git diff --check`。
- 数据迁移前停止服务，备份 `chant_agent.db`、`-wal`、`-shm`，运行 SQLite `integrity_check` 和磁盘空间检查。
- 先运行 `scripts/recalculate_structure.py` dry-run，再以 `--execute` 重算启用标的的 `5/30/d/w/m`；全部成功后才切换各自活动快照。
- 使用 `scripts/audit_structure.py` 审计版本、指纹、源笔唯一归属、核心边界、买卖点证据、扩张父子关系和递归资格。
- 启动服务后检查 `/api/health`、科创50/上证指数多分页 API，以及空数据、单中枢、盘整背驰、完整趋势、扩张升级和 provisional 尾部。
- 浏览器实际验证历史拖动、L1默认展示、L2/L3筛选、候选转确认、端点标签、中枢角色高亮及分页前后结构稳定性。

## Assumptions

- 采用非同级别分解作为唯一正式结构引擎，本轮不恢复独立的同级别操作视图。
- L1 核心严格由三笔构成；多笔无中枢走势段只用于进入、连接、离开、回试和力度比较。
- 买卖点完全使用价格结构，MACD不参与确认。
- 候选点先切 provisional，确认后锁定；反向中枢仅作佐证。
- 正式恢复外围波动重叠驱动的中枢扩张，但禁止九笔自动升级。
- 默认显示 L1，可筛选更高级别；所有 L1/L2/L3 均为当前行情周期内部结构级别。
- 科创50 2021-08-06 是否为正式卖点由通用规则和证据决定，不为个别日期写特例。
- 旧快照完整保留；新旧版本不混算；当前工作区已有未提交改动全部保留。
