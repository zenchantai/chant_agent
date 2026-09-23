# chant_agent 架构设计分析

分析日期：2026-09-23
分析分支：`refactor_zs_hongluhuo_0919`（工作区含未提交改动）
分析方式：逐文件阅读 `app/`、`knowledge/`、`scripts/`、`web/src/`、`store.py` 全部 DDL

---

## 一、一句话结论

这是一个**单机、单进程、SQLite 承载的"缠论结构计算 + 证据留痕"工作台**：核心不是交易信号，而是把"行情 → 包含 → 分型 → 确认笔 → L1 中枢 → 延伸/扩展 → L2 中枢"这条链路的**每一步证据可持久化、可审计、可版本回滚**。架构上最值得注意的三个决策是：

1. **结构计算结果按不可变 run 快照存储**，用 `chan_active_runs` 一张指针表切换生效版本（`app/store.py:90-104`、`app/store.py:1240-1330`）。
2. **计算器指纹 + 定义版本 + 行情版本三重指纹**共同决定是否需要重算（`app/period_structure.py:50-76`、`app/period_structure.py:300-318`）。
3. **"已确认" 与 "形成中" 严格二分**：未收口数据只能作为 `provisional` 预览，不进正式结构（`knowledge/chan_rules.yaml` 的 `DAILY-CONFIRMED-SOURCE-V27`；`app/store.py:309-357`）。

---

## 二、功能设计

### 2.1 功能域划分

| 功能域 | 入口 | 主要实现 |
|---|---|---|
| 自选股与分组管理 | `/api/watchlist*`、`/api/stock-pool*` | `app/store.py:409-737` |
| 证券检索与目录 | `/api/securities/search` | `app/securities.py`、`security_catalog` 表 |
| 行情抓取与同步 | `/api/stock-pool/{symbol}/sync`、后台定时 | `app/sync.py`、`app/providers.py` |
| 实时/准实时刷新 | `/api/chart-data?refresh=true`、`/api/chart-realtime` | `app/intraday.py`、`app/main.py:533-770` |
| 缠论结构计算 | `PeriodStructureService.ensure` | `app/period_structure.py:222+`、`app/chan_structure.py` |
| 数据完整性校验 | `/api/market-coverage`、`/api/market-data/{symbol}/repair` | `app/coverage.py` |
| 图表与结构展示投影 | `/api/chart-data` | `app/structure_display.py`、`web/src/chartBuilders.ts` |
| 手工画线标注 | `/api/drawings/*` | `drawing_objects` 表 |
| LLM 追问解释 | `/api/analyze` | `app/agent.py` |

### 2.2 周期与计算策略矩阵

已核实（`app/providers.py:14-16`、`app/period_structure.py:18-26`、`knowledge/chan_rules.yaml:2-7`）：

| 周期 | 是否抓取 | 是否算结构 | calculation_profile | max_level |
|---|---|---|---|---|
| `1`（分时） | 是 | **否** | — | — |
| `5` / `30` | 是 | 是 | `pen_centers_l2` | 2 |
| `15` / `60` / `120` | 是 | **否** | — | — |
| `d` | 是 | 是 | `pen_centers_l2` | 2 |
| `w` / `m` | 是 | 是 | `pen_centers_only` | 1 |
| `y` | 是 | 否 | — | — |

周线/月线只算 L1，L2 靠**跨周期投影**：把日线正式结构里的 L2 叠加到周月图上，保留日线来源身份，不参与周月原生计算（`DAILY-L2-PROJECTION-V27`；`app/structure_display.py:138+`）。

### 2.3 三条读路径

1. **`/api/chart-data`（全量页）**：抓取 → `ensure`（必要时重算并落库）→ 分页裁剪 → `project_center_display` → 周月再叠 `project_daily_l2`（`app/main.py:651-770`）。
2. **`/api/chart-realtime`（增量 delta）**：只返回变化集 `replace_from` + `removed_ids` + 变化实体，供前端局部 patch（`app/main.py:484-520`）。这是 2026-09-23 性能计划的产物。
3. **`/api/chart-data?timeframe=1`**：分时走独立的 `IntradayService.chart_page`，不进结构引擎。

### 2.4 刻意"不做"的功能

规则库和 `structure_mode_metadata` 显式把一批能力置为 `disabled`（`app/period_structure.py:26-48`）：走势分区（`movement_partition_mode`）、走势边界、背驰（`divergence_mode`）、结构买卖点、L3 以上递归。README 同时写明"人工结构修订暂时禁用"。

**这是设计意图，不是缺失**——`tests/test_retired_structure.py`、`scripts/migrate_pen_center_l2.py` 专门用于确认这些能力已下线且历史数据不被误删。

---

## 三、缠论构件设计

### 3.1 计算管道

```
market_bars (已确认)
   │  normalize_bars              app/engine.py:37
   ▼
Bar
   │  process_inclusions（包含关系，左→右）   engine.py:82
   ▼
ProcessedBar
   │  find_fractals（严格顶底分型）           engine.py:110
   ▼
StrictFractal ──┐
                │  build_pens（标准笔 + 特殊笔 + 跳空笔 + 活动尾部修正）  engine.py:290
   find_gaps ───┘
   ▼
Pen（confirmed / provisional）
   │  atomic_pen_units → _split_streams   chan_structure.py:130,158
   ▼
build_level_centers(level=1)              chan_structure.py:508
   │  → L1 中枢 + components + issues(候选)
   ├─ build_relations（延伸/扩展关系分类）   chan_structure.py:708,736
   ├─ expansion_evidence / _absorb_return  chan_structure.py:809,350
   ▼
build_promotion_candidates(level 1→2)     chan_structure.py:1219
   │  依赖 chan_expansion.build_parent_center_proofs（笔原生 SegmentProof）
   ▼
L2 中枢（独立 family_id）→ 到此停止，不构造 L3
```

### 3.2 构件清单与不变式

| 构件 | 定义要点 | 关键规则 ID |
|---|---|---|
| `ProcessedBar` | 向上取高高低高、向下取低低高低，方向由首个非包含关系确定 | `INC-001` |
| `StrictFractal` | 中间 K 高低点严格高于/低于左右，右侧 K 完成时确认 | `FX-001` |
| `Pen` | 交替分型、不共享 K 线、中间至少一根独立处理后 K；含次高次低笔、跳空笔、打横笔、尾部修正笔 | `PEN-NEW-001/002`、`PEN-SPECIAL-001..005` |
| `Component` | 进入/连接/离开/回试，由连续、方向交替、不重复的低级单位组成，**内部不含可提交同级中枢** | `COMPONENT-CENTER-FREE-V25` |
| `Center` 核心 | 三笔连续、已确认、方向交替、**严格**重叠；`ZD=max(low)`、`ZG=min(high)`、`ZD+ε<ZG` | `CENTER-STRICT-CORE-V25` |
| Z 波（DD/GG） | 只取核心第 1、第 3 及已接纳的同向延伸 Z 单位；进入/连接/离开/回试不参与 | `CENTER-Z-WAVE-V26` |
| 扩展 | 核心分离后 Z 闭区间再接触，产生 `expansion_up/down` 关系 | `CENTER-NEWBORN-EXPANSION-V26` |
| `SegmentProof` | 从确认笔直接构造、至少三笔的段证明，**走势不是输入也不是回退路径** | `SEGMENT-PEN-NATIVE-V30` |
| L1→L2 晋级 | 三个连续 SegmentProof 的真实区间交集定界，第三段可动态；**禁止用包络直接升级** | `CENTER-NEWBORN-EXPANSION-V26`、`PROMOTION-OWNED-NINE-V30` |
| 九单位闸门 | core+extension+peripheral 首次达 9 个连续**拥有**单位才触发升级检查；entry/departure/retest 不计数；九单位≠自动升级 | `PROMOTION-OWNED-NINE-V30` |

### 3.3 三条贯穿全局的架构不变式

这三条是整个引擎设计的骨架，值得单独列出：

**(1) 所有权账本（ownership ledger）**
同一级别、同一连续流的直接单位只能有一个正式拥有者：L1 拥有笔，L2 拥有段证明。`entry/departure/retest/connection/boundary_evidence` 只能作为**上下文引用**进 `context_unit_ids`，可以重叠，但不进 `owned_unit_ids`（`CENTER-OWNERSHIP-LEDGER-V31`、`CENTER-CONTEXT-NONOWNERSHIP-V31`）。数据库层用一条部分索引兜底：`idx_chan_core_owner ON chan_center_units(run_id,unit_kind,unit_id) WHERE role='core'`（`app/store.py:142`）。

**(2) 前缀稳定性（无未来函数）**
追加数据只能扩展未确认后缀；已提交候选和确认前缀结果必须稳定（`CENTER-PREFIX-STABILITY-V31`、`PEN-SPECIAL-004`）。结构时间与**证据可用时间**分开存储：`observed_at` / `evidence_available_at` / `confirmed_at` 是独立字段（`chan_center_candidates`、`chan_segment_proofs`）。无法唯一确认的结构保留 `candidate/provisional/undetermined/truncated` 状态（`CONFIRM-V25`）。

**(3) 修订链而非原地覆盖**
中枢不是一行记录，而是 `family → revisions` 两级：`chan_center_families` 持 `current_revision_id`，`chan_center_revisions` 用 `(family_id, revision_no)` 唯一约束 + `previous_revision_id` 构成链表，`active` 标志指示当前生效修订。候选、晋级候选、段证明三张表用**同一套** `family_id/revision_no/previous_revision_id/active` 模式。生命周期冲突按证据时间仲裁（`CENTER-LIFECYCLE-ARBITRATION-V31`）。

### 3.4 规则库的运行时校验

`knowledge/chan_rules.yaml` 不是文档，是**启动时强校验的契约**：`app/rules.py:28-37` 在 import 时检查 `version` 与 `DEFINITION_VERSION` 一致、且 `REQUIRED_RUNTIME_RULES`（28 条）全部存在，否则直接 `RuntimeError` 拒绝启动。`chan_rules.md` 是同内容的人类可读版，二者都进计算器指纹。

---

## 四、数据库表设计

单库 SQLite 共 25 张表，`data/chant_agent.db`，WAL 模式、`foreign_keys=ON`、`busy_timeout=5000`（`app/store.py:33-35`）。当前实测 417 MB。

### 4.1 表分组

**A. 行情与日历（4 张）**

| 表 | 主键 / 唯一约束 | 说明 |
|---|---|---|
| `market_bars` | `UNIQUE(symbol,timeframe,trade_date,adjustflag)` + `uq_market_bars_period(symbol,timeframe,adjustflag,period_key)` | 全周期 K 线单表。`period_key` 是**逻辑周期身份**（周→`2026-W39`、月→`2026-09`、分钟→对齐时间戳），解决同一逻辑周期出现多 `trade_date` 的历史脏数据 |
| `daily_bar_confirmations` | `(symbol,adjustflag,trade_date)` | 日线"已确认"基线，存 `row_hash` |
| `period_confirmations` | `(symbol,timeframe,adjustflag,period_key)` | 上者的泛化版，含 `request_started_at/fetched_at/confirmed_at` 三段时间证据 |
| `trade_calendar` | `(exchange,trade_date)` | 含 `expected_5m_count/expected_30m_count`，供 coverage 校验用 |

**B. 自选与目录（5 张）**
`stock_pool`、`watchlist_groups`、`watchlist_group_members`、`watchlist_section_order`、`security_catalog`。分组排序用 `sort_order` 整数，"全部/未分组"系统分组通过 `watchlist_section_order` 的 `section_key` 统一参与混合排序。

**C. 结构快照（15 张，全部以 `run_id` 为根）**

```
chan_structure_runs (id, symbol, timeframe, adjustflag,
                     definition_version, calculator_fingerprint,
                     market_version, structure_version, status, max_level, meta_json)
   ▲ 指针
chan_active_runs (symbol, timeframe, adjustflag) → run_id     ← 生效版本只此一处
   │
   ├─ chan_processed_bars  ┐
   ├─ chan_fractals        ├ 同构：(run_id,id) PK + UNIQUE(run_id,ordinal) + payload_json
   ├─ chan_pens            ┘
   ├─ chan_components ──── chan_component_units (unit_kind, unit_id, role, ordinal)
   ├─ chan_center_families ─ chan_center_revisions ─ chan_center_units
   ├─ chan_center_candidates      (family/revision/active + rejection_code)
   ├─ chan_promotion_candidates   (child_level, parent_level, candidate_source)
   ├─ chan_segment_proofs         (source_kind, direction, evidence_available_at)
   ├─ chan_relations              (relation_type, from_id, to_id)
   └─ chan_issues                 (issue_type)
```

所有子表 `ON DELETE CASCADE` 挂在 `chan_structure_runs(id)` 上，删 run 即整体回收。

**D. 用户标注（1 张）**
`drawing_objects`：软删除（`deleted_at`），`object_type ∈ {segment,line,rectangle}`，锚点用 `(trade_date, price)` JSON。README 明确它**不因走势功能下线而删除**。

### 4.2 三个值得学的表设计决策

1. **结构化列 + `payload_json` 双写**。查询维度（level/status/日期/价格/family）提列出来建索引，完整实体存 JSON。读取时 `_load_json_rows` 直接反序列化 JSON 还原对象（`app/store.py:1338-1382`），避免 ORM 映射与结构演进的 schema 迁移成本。
2. **写失败也留痕**。`replace_chan_structure` 的 except 分支回滚后**再插一条 `status='failed'` 的 run**，并只保留最近一条失败记录（`app/store.py:1301-1316`）。成功的 run 是不可变证据和回滚目标，注释明写"Retention is an explicit maintenance operation, never activation"。
3. **迁移必须 dry-run 双人确认**。`canonicalize_period_bars(expected_delete_ids)` 要求调用方传入**精确的待删 ID 列表**，与当轮 `period_key_migration_report()` 不一致就 `ValueError` 中止（`app/store.py:263-308`）。`scripts/migrate_*.py` 默认只读，`--execute` 前先复制 db/wal/shm 三件套。

---

## 五、各目录功能

```
app/                后端（7694 行 Python / 20 个文件）
  main.py           FastAPI 装配 + 30 个路由 + 三条读路径的编排（935 行）
  store.py          SQLite 全部 DDL、行情/结构/标注读写、迁移（1453 行）
  chan_structure.py 缠论层级引擎：中枢构建、候选选择、关系分类、晋级、校验（2051 行）
  chan_expansion.py 笔原生 SegmentProof 构造（L1→L2 的定界证据）
  chan_direction.py 方向证据（突破上下文 / 离开回试上下文的最早可用仲裁）
  period_structure.py 周期编排：profile 决策、指纹计算、run 缓存、分页投影（500 行）
  engine.py         包含 → 分型 → 笔（纯函数，dataclass 输出）
  intraday.py       实时刷新：会话状态机、周期收口时间、provisional 预览（612 行）
  providers.py      BaoStock / 腾讯 / CSV 三数据源 + 交易日历 + 证券目录
  sync.py           后台定时同步（工作日 20:30、周六 18:00、每月 1 日 18:00）
  coverage.py       按交易日历校验 K 线完整性，产出 continuous_ranges
  structure_display.py 展示投影：中枢显示目录、跨周期日线 L2 叠加
  rules.py          规则库加载 + 启动时强校验
  indicators.py     MA / BOLL / MACD
  securities.py     证券目录刷新与检索（含别名）
  watchlist_quotes.py 左栏批量报价（仅内存缓存，不落 K 线）
  models.py         Bar / Signal dataclass
  periods.py        逻辑周期键（period_key）唯一定义处

web/src/            前端（5761 行 TS/TSX，源码 4044 + 测试 1716；React + ECharts + Vite + Vitest）
  App.tsx           页面主体（2190 行，单文件）
  chartBuilders.ts  ECharts option 构建、数据归一化（980 行）
  chartInteractions.ts 缩放/平移/手势归属仲裁（pan vs draw vs structure）
  centerDisplay.ts  中枢显示集合选择、父子标签
  dailyL2Overlay.ts 周月图上的日线 L2 投影渲染
  intradayRefresh.ts 轮询节奏与增量合并
  intradayTimeline.ts 09:30–15:00 固定时间轴、午休折叠
  structureColors.ts 级别配色（注释明确：配色不代表真实周月级别）
  watchlistQuotes.ts / watchlistGroupOrder.ts 左栏报价与拖拽排序
  types.ts          前后端契约类型（83 行高密度定义，是接口事实源）

knowledge/          规则契约：chan_rules.yaml（运行时校验）+ chan_rules.md（人读版）
scripts/            运维脚本：audit_structure（只读审计）、recalculate_structure、
                    migrate_pen_center_l2、migrate_realtime_period_keys（均 dry-run 优先）
tests/              29 个测试文件，按规则版本命名（test_v31_candidate_ownership、
                    test_z_wave_expansion、test_reference_profile 等）
docs/plans/         57 份日期命名的方案文档，等于一份完整重构编年史
docs/reports/       验证报告（每次大版本晋级后的核对结果）
docs/requirements/  需求文档
data/               SQLite 库、备份、导入 CSV（不入 Git）
```

**目录设计上的一个显著特征**：`docs/plans/` 与 `tests/` 的命名直接绑定规则版本（V25→V26→V27→V29→V30→V31）。这条"每次规则变更都留方案 + 验证报告 + 版本化测试"的纪律，是这个项目能在两周内完成 6 次结构引擎重构而不失控的实际原因。

---

## 六、评价与风险

### 6.1 已核实（本轮工具验证，附来源）

1. **`store.py` 单连接 + 全局 `RLock` 串行化所有 DB 访问**（`app/store.py:22-26`）。FastAPI 的同步 `def` 路由跑在线程池，因此并发请求在这把锁上排队；`chart_data` 更是**整段业务逻辑持锁**（`app/main.py:711-723`，含两次 `ensure` 可能触发的完整重算）。
2. **启动时无条件全表扫描 `market_bars`**：`_initialize_period_keys` 执行 `SELECT id,timeframe,trade_date,period_key FROM market_bars`（无 WHERE），逐行比对 `period_key`（`app/store.py:187-214`）。当前库 417 MB。
3. **成功 run 永不自动回收**（`app/store.py:1297-1299` 注释与代码一致：只清理多余的 failed run）。13 张 run 子表随重算次数单调增长，这是 417 MB 的主要来源之一。
4. **`app/rules.py` 在模块 import 期读文件并可能抛 `RuntimeError`**（`rules.py:38` 模块级 `RULEBOOK = load_rulebook()`）。任何 import `app.rules` 的路径（含测试收集）都依赖 `knowledge/` 可读且版本匹配。
5. **两个超大单文件**：`app/chan_structure.py` 2051 行、`web/src/App.tsx` 2190 行（`wc -l` 实测）。
6. **`chart_data` 与 `chart_realtime` 存在并行的响应组装逻辑**（`app/main.py:533-650` 与 `651-770`），指标计算、报价拼装、live 合并在两处各写一遍。

### 6.2 我的判断（含反方）

**(a) 全局锁 + 持锁重算是当前最可能的性能天花板。**
理由：一次 `ensure` 触发的 `analyze_period_ranges` 是 CPU 密集的全量重算，持锁期间所有其他请求（含左栏报价、其他股票切换）都被阻塞。
*反方*：这是单用户本地工作台，`PeriodStructureService._snapshot_cache` 已按 `run_id` 缓存快照（`period_structure.py:228-231`），命中时不落库不重算；`chart_realtime` 的增量设计也正是为此。**所以这更可能是"已知取舍"而非疏漏**——`docs/plans/2026-09-23-kline-realtime-performance.md` 的存在支持这个解读。建议先量 `Server-Timing`（`main.py:521-532` 已埋点），再决定是否拆读写连接。

**(b) 启动全表扫描随数据量线性恶化。**
可改为：建好 `uq_market_bars_period` 后只扫 `period_key IS NULL` 的行；或用一张 `schema_migrations` 表记录迁移已完成。
*反方*：作者显然知道这是迁移代码——docstring 明写"Filling the key is lossless"，且刻意不做删除。作为一次性迁移它是正确的；问题只是**没有完成标记**，每次启动都重跑。这是我认为最值得修且最低风险的一条。

**(c) 结构 run 需要保留策略。**
不是"该删"，而是"该有可配置的保留窗口 + 手工回收入口"。代码注释已经把这件事定义为"explicit maintenance operation"，说明是留位而非遗漏。缺的是那个入口（`scripts/` 里目前没有 retention 脚本）。

**(d) `chan_structure.py` 2051 行不等于该拆。**
它内聚的是同一个不变式集合（所有权、前缀稳定、修订链），拆开反而容易让不变式跨文件漂移——`chan_expansion.py` 和 `chan_direction.py` 已经是按"证据种类"切出去的合理边界。相比之下 `App.tsx` 2190 行的拆分收益更明确，因为周边已有 10 个纯函数模块 + 对应测试，说明拆分路径是通的。

**(e) 架构最强的部分是"可审计性"而非"算法"。**
三重指纹 + run 快照 + 修订链 + 证据时间分离 + dry-run 迁移，这套组合让"结构算错了"这件事可定位、可回滚、可复现。对缠论这种规则本身存在解释分歧的领域，这个选择比追求算法先进更正确。

### 6.3 待你拍板

1. **`@app.on_event("startup"/"shutdown")` 是否迁移到 lifespan**（`main.py:55,76`）。我未核实本项目 FastAPI 版本对该 API 的弃用状态，需要先 `pip show fastapi` 确认再讨论。
2. **`chart_data` 与 `chart_realtime` 是否合并组装层**。收益是消除双份逻辑，风险是把已验证的增量路径和全量路径耦合回去。这是架构取舍，我不替你决定。
3. **结构 run 保留窗口取多少**（按数量？按天数？按 definition_version 只留最新两代？）。这依赖你回溯审计的实际习惯。
4. **周线/月线是否长期维持 `pen_centers_only`**。当前靠日线 L2 投影补齐，是产品决策而非技术限制。

---

## 附：可直接执行的核实命令

```bash
# 结构 run 数量与占用
sqlite3 data/chant_agent.db "SELECT timeframe,status,COUNT(*) FROM chan_structure_runs GROUP BY 1,2;"
sqlite3 data/chant_agent.db "SELECT COUNT(*) FROM market_bars;"

# 各表体积
sqlite3 data/chant_agent.db "SELECT name FROM sqlite_master WHERE type='table';"

# 接口耗时分解（Server-Timing 已埋点）
curl -s -D- -o/dev/null 'http://127.0.0.1:8765/api/chart-data/000001?timeframe=d' | grep -i server-timing

# 只读结构审计
uv run python scripts/audit_structure.py --help
```
