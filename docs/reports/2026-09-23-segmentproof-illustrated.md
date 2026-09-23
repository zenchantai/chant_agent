# SegmentProof 图解与完整分类

配套视觉版：`2026-09-23-segmentproof-illustrated.html`
代码依据：`app/chan_expansion.py`、`app/chan_structure.py:1219-1400`、`app/chan_structure.py:1860-1915`

---

## 0. 全景：从 L1 中枢到 L2 的所有出口

```text
                      L1 笔中枢（正式实体，owned_unit_ids = 笔）
                                    │
                    ┌───────────────┴───────────────┐
              入口①  extension                 入口②  expansion
              单个中枢的 owned_unit_ids        一对扩展关系中枢 + 右滑窗
                    └───────────────┬───────────────┘
                                    ▼
                        search_unit_ids（候选笔范围）
                        required_unit_ids（冻结前 9 个）
                                    │
                    ┌───────────────┴──────────────┐
              len < 9                          len >= 9
                    ▼                               ▼
         ✗ nine_owned_units              笔连续 / 无重复 / 前缀未被改
                                                    │
                            ┌───────────────────────┼────────────────────┐
                            ▼                       ▼                    ▼
              ✗ segment_continuity_failed  ✗ segment_ownership_conflict  通过
                                                                         │
                                                        切三段 S1|S2|S3（各 >= 3 笔）
                                                                         │
                                        ┌────────────────────────────────┤
                                        ▼                                ▼
                      ✗ segment_direction_mismatch          方向 = 上下上 或 下上下
                      （dir0 != dir2 或 dir0 == dir1）                    │
                                                        每段内部找 local_core_witness
                                                                         │
                                        ┌────────────────────────────────┤
                                        ▼                                ▼
                      ✗ segment_child_core_missing              三段都有见证
                                                                         │
                                                        zd = max(low), zg = min(high)
                                                                         │
                                        ┌────────────────────────────────┤
                                        ▼                                ▼
                      ✗ segment_common_overlap_empty            zd + eps < zg
                      （zd + eps >= zg）                                 │
                                                                    ✓ L2 中枢
                                                                         │
                                                    ┌────────────────────┴───────────┐
                                        S3 有反向 S4 收口                    S3 仍是尾部
                                        boundary_status = fixed        boundary_status = dynamic
                                        candidate.status = fixed       candidate.status = dynamic
                                        （实线）                            （虚线）

  兜底码：three_segment_proofs_missing —— 前面全过但仍凑不出三段
  无 selected proof 时 candidate.status = unresolved
```

---

## 1. 单个 SegmentProof 的解剖

一段 = **N 笔（N ≥ 3，连续、已确认）** + **内部一个 L1 中枢见证**。

```text
价格
 ▲                                              ┌── end_price（末笔终点）
 │                                          ╱───┘
 │                              ╲       ╱            ← 段方向 = up
 │        ┌ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─╲─ ─╱─ ─ ─ ─ ┐  high = max(所有笔 high)
 │        │              ╱───╲     ╲╱          │
 │        │  zg ═══════════════════════════    │  ← 见证核心的 zg = min(high of p2,p3,p4)
 │        │      ╱───╲ ╱     ╲ ╱               │
 │        │  zd ═══════════════════════════    │  ← 见证核心的 zd = max(low of p2,p3,p4)
 │        │    ╱      V       V                │
 │        └ ─ ╱─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘  low = min(所有笔 low)
 │        ╱
 │    ───┘  ← start_price（首笔起点）
 └──────────────────────────────────────────────────────▶ 时间
        p1    p2    p3    p4    p5
              └──── 见证核心 ────┘
              连续三笔 + 方向交替 + 严格重叠

段字段                        见证字段（嵌在 center_witnesses）
─────────────────────        ──────────────────────────────
level            = 1          id            = local-center-witness-…
direction        = up         family_id     = local-center-family-…
start_price      = p1.start   level         = 1
end_price        = p5.end     source_unit_ids = [p2,p3,p4]
low / high       = 全段极值    zd / zg       = 严格重叠区间
source_unit_ids  = [p1..p5]   available_at  = max(三笔 confirmed_at)
level_evidence_ids = [见证id]  formation_stage = "directional"
```

要点：

- **low/high 是全段极值**，见证的 zd/zg 是**段内三笔的重叠**——两者是不同的量，L2 用的是前者。
- 见证核心**取最左**：`for offset in range(len(part)-2)` 找到第一个合法的就 `return`。
- 见证核心**可以不在段的端点**，图中它落在 p2–p4，p1 和 p5 不参与见证。

---

## 2. 三段成 L2（上-下-上）

```text
价格
 ▲                                                          ╱╲    ╱─── S3 末端
 │                                                     ╱╲ ╱  ╲ ╱
 │   S1 ▲上                S2 ▼下                 S3 ▲上╱  V    V
 │        ╱╲    ╱╲                                ╱
 │      ╱  ╲ ╱   ╲                             ╱
 │   ╱╲╱    V     ╲  ╱╲                      ╱
 │ ╱               ╲╱  ╲   ╱╲             ╱
 │                      ╲╱  ╲ ╱╲       ╱
 │                           V  ╲    ╱
 │                               ╲╱
 └───────────────────────────────────────────────────────────────▶ 时间

各段区间（low, high）           ZD / ZG 的由来
┌──────────────────────┐
│ S1  [ low1 ──── high1 ]      zd = max(low1, low2, low3)
│ S2      [ low2 ── high2 ]    zg = min(high1, high2, high3)
│ S3    [ low3 ───── high3 ]
│          ╠═══════╣           必须 zd + eps < zg，否则
│          zd     zg           ✗ segment_common_overlap_empty
└──────────────────────┘
        ↑ 三段区间的公共交集 = L2 的真实区间

方向约束：directions[0] == directions[2]  且  directions[0] != directions[1]
所以只有两种合法模式：上-下-上 ／ 下-上-下
```

**这里是全流程最讲究的一步**：L2 的 `zd/zg` 来自三段 `[low, high]` 的交集，**不是**把 L1 的包络抬一级。规则明禁后者（`CENTER-NEWBORN-EXPANSION-V26`：「禁止包络直接升级」）。

---

## 3. fixed 与 dynamic：差别只在 S4 是否存在

```text
【dynamic】S3 还没走完                    【fixed】S3 被反向 S4 收口

  S1     S2     S3(尾部)                   S1     S2     S3      S4(反向)
 ▲上    ▼下    ▲上 ┄┄┄?                   ▲上    ▼下    ▲上     ▼下
  ╱╲    ╲      ╱┄┄┄                        ╱╲    ╲      ╱        ╲
 ╱  ╲    ╲    ╱                           ╱  ╲    ╲    ╱          ╲╱╲
      ╲    ╲╱                                   ╲    ╲╱                ╲
                                                                   └─ 内部也须有见证

S3.status         = provisional            S3.status         = confirmed
S3.boundary_mode  = "pen_group"            S3.boundary_mode  = "pen_group_reversal"
S3.completion_evidence_id = None           S3.completion_evidence_id = local-boundary-…
S3.revision_no    = len - 2                S3.revision_no    = len + 1
proof.boundary_status = "dynamic"          proof.boundary_status = "fixed"
candidate.status      = "dynamic"          candidate.status      = "fixed"
前端画虚线                                  前端画实线
```

S4 的成立条件（`_find_local_completion`，`chan_expansion.py:141-163`）——它本身就是一次小型段校验：

1. S3 之后还剩 ≥ 3 笔
2. 这 3 笔连续、且确认时间 ≤ `observed_at`
3. 净方向与 S3 **相反**
4. **内部同样能证出 L1 中枢见证**

四条缺一，S3 就只能是 dynamic。注意第 4 条：收口证据自己也要带中枢见证，不是「随便反向三笔」。

---

## 4. 镜像：下-上-下

```text
价格
 ▲ ╲
 │  ╲  S1 ▼下          S2 ▲上              S3 ▼下
 │   ╲╱╲              ╱╲    ╱╲            ╲
 │      ╲    ╱╲     ╱   ╲ ╱   ╲          ╲╱╲
 │       ╲ ╱   ╲  ╱      V     ╲        ╱   ╲
 │        V     ╲╱               ╲    ╱      ╲╱╲
 │                                ╲╱╲╱          ╲
 └──────────────────────────────────────────────────▶ 时间

规则完全对称：directions = [down, up, down]
zd / zg 的计算、见证要求、fixed/dynamic 判定与上-下-上一致。
```

---

## 5. 六种失败情况

```text
① nine_owned_units                     ② segment_continuity_failed
   拥有单位不足 9                          笔之间断开（跨区间 / 价不相接）

   p1 p2 p3 p4 p5 p6 p7 p8                p1 p2 p3 ╳ p5 p6 p7 p8 p9
   └──── 只有 8 个 ────┘                          ↑ 断点
   直接返回，连切段都不尝试                  _units_contiguous 失败


③ segment_ownership_conflict            ④ segment_direction_mismatch
   冻结前缀被改 / id 重复                    三段方向不是 上下上 / 下上下

   required = [p1..p9]                     S1 ▲上   S2 ▼下   S3 ▼下
   search   = [p2..p10]  ← 前缀不符             ╱      ╲        ╲
   set(search[:9]) != set(required)                        dir0 != dir2  ✗


⑤ segment_child_core_missing            ⑥ segment_common_overlap_empty
   某段内部证不出中枢                        三段有区间但无公共交集

   S2 内部：                               S1 [──────]
     ╱╲                                    S2      [──────]
    ╱  ╲                                   S3              [──────]
   ╱    ╲   单调，无三笔严格重叠                    zd > zg  ✗
   _local_core_witness → None              三段完全错开

⑦ three_segment_proofs_missing（兜底）
   前六项全过，但滑窗仍凑不出合法三段切分
```

诊断码**按顺序**返回第一个卡住的环节（`segment_missing_evidence`），所以拿到码就知道卡在哪一级，不必逐层猜。

---

## 6. 多解仲裁：同一批笔的不同切法

同一批 `search_unit_ids` 常有多种合法切分。滑窗全部枚举后排序取一。

```text
9 笔：p1 p2 p3 p4 p5 p6 p7 p8 p9

切法 A：[p1 p2 p3] [p4 p5 p6] [p7 p8 p9]     ← 3/3/3
切法 B：[p1..p4]   [p5 p6 p7] [p8 p9 ...]    ← 4/3/N
切法 C：[p1 p2 p3] [p4..p7]   [p8 p9 ...]    ← 3/4/N

排序键（chan_expansion.py:307-313）：
  1. evidence_available_at        证据可用时间最早
  2. len(segments[0].source_unit_ids)   S1 最短
  3. len(segments[1].source_unit_ids)   S2 最短
  4. len(source_unit_ids)         总笔数最少
  5. id                           稳定 id 兜底

    ↓
切法 A  → selection_status = "selected"
切法 B  → selection_status = "rejected"，rejection_code = "noncanonical_segment_partition"
切法 C  → selection_status = "rejected"，rejection_code = "noncanonical_segment_partition"

落选的也整体存库（chan_segment_proofs），但结论只认 selected。
```

滑窗上限（避免 O(n³)）：`first_limit = min(len-5, required_len+6)`、`second_limit = min(len-2, required_len+9)`。

---

## 7. 两条晋级入口的范围差异

```text
入口① extension_decomposition            入口② expansion_decomposition
（chan_structure.py:1231-1281）           （chan_structure.py:1330-1400）

单个 L1 中枢                              一对 L1 中枢 + expansion 关系
  ┌─────────────┐                          ┌────────┐    ┌────────┐
  │ L1 center   │                          │ left   │───▶│ right  │
  │ owned_units │                          └────────┘ 扩展 └────────┘
  └──────┬──────┘                               └────────┬────────┘
         │                                               │
search_ids = snapshot.owned_unit_ids          expansion_evidence.source_unit_ids
required   = qualifying[0].owned[:9]              ↓ 再向右滑窗扩展
（首个达 9 的修订，冻结前 9）              observation_spans[0..N]
                                          required = observation_spans[0]

共同点：
  - 都调用同一个 build_parent_center_proofs
  - 都只传「哪些笔」，不传中枢的 zd/zg/dd/gg
  - 修订数上限 13（超出取前 12 + 最后 1）
  - status == "fixed" 即 break，不再往后找
```

---

## 8. 字段取值完整分类表

### 8.1 单段字段

| 字段 | 取值 | 决定因素 |
|---|---|---|
| `level` | 恒 `1` | `child_level`，只支持 L1→L2 |
| `source_kind` | 恒 `"local_pen_group"` | 唯一来源是笔组 |
| `direction` | `up` / `down` | 首笔 start_price vs 末笔 end_price（带 ε；相等则整段作废） |
| `status` | `confirmed` / `provisional` | S1/S2 恒 confirmed；S3 视有无 completion |
| `boundary_mode` | `pen_group_reversal` / `pen_group` | 有无 `completion_evidence_id` |
| `completion_evidence_id` | `local-boundary-…` / `None` | S1/S2 恒有（下一段前 3 笔）；S3 视有无反向 S4 |
| `revision_no` | `len-2` / `len+1` | S3 且 confirmed 时为 `len+1`，其余 `len-2` |
| `selection_status` | `selected` / `rejected` | 仲裁排序结果 |
| `rejection_code` | `noncanonical_segment_partition` / 无 | 仅 rejected 有 |
| `recursive_eligible` | 恒 `False` | 写死——段永不参与 L3 |
| `active` | 恒 `True`（构造时） | 落库前按 family 内最大 revision_no 重算 |

### 8.2 三段组合（parent proof）

| 字段 | 取值 | 决定因素 |
|---|---|---|
| `boundary_status` | `fixed` / `dynamic` | `segments[-1].status == "confirmed"` |
| `parent_level` | 恒 `2` | `child_level + 1` |
| `zd` / `zg` | 三段 low/high 交集 | 必须 `zd + ε < zg` |
| `evidence_available_at` | fixed 时 = 三段最大 available_at；dynamic 时再与 `observed_at` 取大 | 防未来函数 |

### 8.3 晋级候选（promotion candidate）

| `status` | 含义 |
|---|---|
| `fixed` | 有 selected proof 且 S3 已收口 |
| `dynamic` | 有 selected proof 但 S3 仍开放 |
| `unresolved` | 无 selected proof，`missing_evidence` 带上文七码之一 |

### 8.4 见证的两条校验通道

| | 通道 A：正式 L1 中枢 | 通道 B：段内局部见证 |
|---|---|---|
| id 前缀 | `center-family-…` | `local-center-witness-…` |
| 级别校验 | `witness.level == center.level - 1` | 隐含 level 1 |
| 时间校验 | `revision_at <= part.available_at` | 构造端保证 |
| 归属校验 | `owned_unit_ids ⊆ part.source_unit_ids` | 三笔须都在段内 |
| 区间校验 | 信任实体 zd/zg | **重算 `_strict_overlap` 必须等于存值** |
| 是否落 `chan_center_*` 表 | 是 | **否**，只作 payload 字段 |
| 当前运行时是否产出 | 否（扩展/兼容位） | 是 |

两通道皆不命中 → `validate_structure` 报 `decomposition_level`。

---

## 附：一眼对照

```text
         笔（唯一原料）
          │
          ├──▶ L1 笔中枢 ──── 拥有笔，进所有权账本，落 chan_center_* 表
          │       │
          │       └── 攒到 9 个拥有单位 ──▶ 圈定 search / required
          │                                        │
          └──▶ SegmentProof ×3 ◀───────────────────┘
                  │  level = 1，拥有笔的「引用」不拥有笔本身
                  │  内含 local_core_witness（不落库，不进账本）
                  │
                  └── 三段 [low,high] 交集 ──▶ L2 中枢
                                                │
                                                └── recursive_eligible = False，到此为止
```
