import { describe, expect, it } from "vitest";
import {
  buildAxes,
  buildChartArtifacts,
  buildChartOption,
  buildChartPaneLayout,
  visiblePriceExtremes,
  buildVisiblePriceMarkLine,
  defaultPaneRatios,
  buildEndpointLabels,
  buildMovementSeries,
  movementVisualStyle,
  buildCenterAreas,
  buildBuySellPointSeries,
  buildPenSeries,
  buildComponentSeries,
  normalizeChartData,
  paneLayoutStorageKey,
  parseStoredPaneRatios,
  resizeAdjacentPanes,
  serializePaneRatios,
  validateAxes,
  validateSeries,
} from "./chartBuilders";

const bar = (date: string, close = 10) => ({
  trade_date: date, open: close - 0.2, high: close + 0.4, low: close - 0.4, close, volume: 100, amount: 1000,
});

it("never renders undetermined or truncated movements as confirmed solid lines", () => {
  for (const status of ["undetermined", "truncated"]) {
    expect(movementVisualStyle("dark", { status, level: 1 } as any).lineType).toBe("dotted");
  }
  expect(movementVisualStyle("dark", { status: "provisional", level: 1 } as any).lineType).toBe("dashed");
  expect(movementVisualStyle("dark", { status: "confirmed", level: 1 } as any).lineType).toBe("solid");
});
const base = (overrides: Record<string, unknown> = {}) => ({
  symbol: "1A0001", timeframe: "d", bars: [bar("2026-01-01"), bar("2026-01-02", 11), bar("2026-01-03", 10.5)],
  pens: [], centers: [], center_revisions: [], movement_revisions: [], components: [], movements: [], promotion_candidates: [], promotion_candidate_revisions: [], relations: [], indicators: { macd: [], ma: [], boll: [] },
  active_structure_level: 1, max_available_center_level: 1, has_more: false, available: true,
  definition_version: "v12", structure_version: "v12", ...overrides,
}) as any;
const movement = (overrides: Record<string, unknown> = {}) => ({
  id: "m1", family_id: "mf1", revision_no: 1, ordinal: 0, level: 1, kind: "movement",
  direction: "up", classification: "trend", status: "confirmed", start_date: "2026-01-01",
  end_date: "2026-01-03", start_price: 8, end_price: 12, source_unit_ids: [],
  center_family_ids: ["cf1", "cf2"], center_revision_ids: ["cr1", "cr2"], center_levels: [1],
  child_movement_ids: [], price_envelope_low: 8, price_envelope_high: 12, recursive_eligible: true,
  termination_reason: "first_buy_sell_point", ...overrides,
});
const center = (overrides: Record<string, unknown> = {}) => ({
  id: "c1", family_id: "cf1", revision_no: 1, ordinal: 0, level: 1, kind: "center",
  status: "confirmed", start_date: "2026-01-01", end_date: "2026-01-03", zd: 9, zg: 10,
  fixed_zd: 9, fixed_zg: 10, dd: 8, gg: 12, fluctuation_dd: 8, fluctuation_gg: 12,
  entry_unit_ids: [], core_unit_ids: [], extension_unit_ids: [], peripheral_unit_ids: [],
  departure_unit_ids: [], retest_unit_ids: [], owned_unit_ids: [], context_unit_ids: [],
  child_center_ids: [], child_movement_ids: [], formation_modes: ["strict_three_unit_core"], recursive_eligible: true,
  ...overrides,
});
const component = (overrides: Record<string, unknown> = {}) => ({
  id: "component-1", ordinal: 0, kind: "component", level: 1, role: "entry", direction: "up",
  status: "confirmed", start_date: "2026-01-01", end_date: "2026-01-02", start_price: 9,
  end_price: 11, low: 9, high: 11, source_unit_ids: ["p1"], ...overrides,
});
const point = (overrides: Record<string, unknown> = {}) => ({
  id: "point-1", family_id: "pf1", revision_no: 1, ordinal: 0, kind: "structural_point", level: 1,
  point_type: "third_buy", status: "confirmed", start_date: "2026-01-02", end_date: "2026-01-02",
  point_date: "2026-01-02", point_price: 10.2, source_unit_id: "p1", source_component_ids: [],
  center_family_id: "cf1", center_revision_id: "cr1", ...overrides,
});

describe("chart builders", () => {
  it("方向核心、Z 范围和扩展见证使用独立证据", () => {
    const selected = center({formation_stage:"directional",formation_type:"pullback",core_unit_ids:["a","b","c"],z_unit_ids:["a","c"],context_low:7,context_high:13});
    const pens = ["a","b","c","witness"].map((id,ordinal) => ({id,ordinal,kind:"pen",level:0,status:"confirmed",start_date:"2026-01-01",end_date:"2026-01-03",start_price:9,end_price:11}));
    const data = base({pens,centers:[selected],center_revisions:[selected],relations:[{id:"contact",from_id:"other",to_id:"c1",expansion_status:"confirmed",evidence:{overlap_witness_unit_ids:["c","witness"]}}]});
    const series = buildPenSeries({data,selectedStructureId:"c1"},data.bars.map((b:any)=>b.trade_date));
    expect(series.find(s=>s.id==="a")?.name).toContain("核心 A");
    expect(series.find(s=>s.id==="c")?.name).toContain("核心 C");
    expect(series.find(s=>s.id==="c")?.lineStyle.color).toBe("#e11d9b");
    expect(series.find(s=>s.id==="witness")?.lineStyle.color).toBe("#e11d9b");
    const artifacts = buildChartArtifacts({data,selectedStructureId:"c1"});
    const areas = artifacts.option?.series.find((s:any)=>s.id==="kline").markArea.data;
    expect(areas.map((a:any)=>a[0].name)).toContain("Z 波动范围");
    expect(areas.map((a:any)=>a[0].name)).toContain("上下文范围");
  });

  it("动态父中枢使用虚线框并按三个规范走势高亮底层笔", () => {
    const pens = ["p1", "p2", "p3"].map((id, ordinal) => ({ id, ordinal, kind: "pen", level: 0, status: "confirmed", start_date: "2026-01-01", end_date: "2026-01-03", start_price: 9, end_price: 11 }));
    const selected = center({
      level: 2, boundary_status: "dynamic", fixed_zd: null, fixed_zg: null,
      decomposition_proof: { boundary_status: "dynamic", available_at: "2026-01-03", segments: pens.map((pen, index) => ({ movement_revision_id: `m${index + 1}`, start_date: pen.start_date, end_date: pen.end_date, low: 8, high: 12, status: index < 2 ? "confirmed" : "provisional", source_pen_ids: [pen.id] })) },
    });
    const data = base({ pens, centers: [selected], center_revisions: [selected] });
    const series = buildPenSeries({ data, selectedStructureId: selected.id }, data.bars.map((item: any) => item.trade_date));
    expect(series.map((item) => item.lineStyle.color)).toEqual(["#0e7490", "#b45309", "#7e22ce"]);
    const area = buildCenterAreas({ data }, data.bars.map((item: any) => item.trade_date)).areas[0];
    expect(area[0].itemStyle.borderType).toBe("dashed");
  });

  it("区分核心、Z延伸、进入和连接笔的选择高亮，不绘制历史中枢", () => {
    const pens = ["entry", "core", "z", "connection"].map((id, ordinal) => ({ id, ordinal, kind: "pen", level: 0, status: "confirmed", start_date: "2026-01-01", end_date: "2026-01-03", start_price: 9, end_price: 11 }));
    const selected = center({ entry_unit_ids: ["entry"], core_unit_ids: ["core"], z_unit_ids: ["core", "z"], connection_component_ids: ["join"] });
    const data = base({ pens, centers: [selected], center_revisions: [center({ id: "old", active: false })], components: [component({ id: "join", source_unit_ids: ["connection"] })] });
    const series = buildPenSeries({ data, selectedStructureId: "c1" }, data.bars.map((item: any) => item.trade_date));
    expect(new Set(series.map((item) => item.lineStyle.color)).size).toBe(3);
    expect(series.every((item) => item.lineStyle.width === 3.4)).toBe(true);
    expect(normalizeChartData(data)?.centers.map((item) => item.id)).toEqual(["c1"]);
  });

  it("全量层级元数据在空分页中保留，历史子修订不会冒充活动L1", () => {
    const data = normalizeChartData(base({ levels: [2], centers: [], center_revisions: [center({ active: false })] }));
    expect(data?.center_levels).toEqual([2]);
    expect(data?.centers).toEqual([]);
  });
  it("分时昨收未知时不使用开盘价冒充昨收线", () => {
    const data = base({ timeframe: "1", previous_close: null });
    const artifacts = buildChartArtifacts({ data, theme: "light" });
    const price = (artifacts.option?.series as any[])?.find((item) => item.id === "intraday-price");
    expect(price).toBeDefined();
    expect(price.markLine).toBeUndefined();
  });
  it("只统计当前可视K线的最高价和最低价", () => {
    const bars = [bar("2026-01-01", 10), { ...bar("2026-01-02", 11), high: 30, low: 9 }, { ...bar("2026-01-03", 12), high: 20, low: 1 }, bar("2026-01-04", 13)];
    expect(visiblePriceExtremes(bars, 0, 50)).toMatchObject({
      high: { date: "2026-01-02", price: 30 },
      low: { date: "2026-01-03", price: 1 },
      startIndex: 0,
      endIndex: 2,
    });
    expect(visiblePriceExtremes(bars, 50, 100)).toMatchObject({
      high: { date: "2026-01-02", price: 30 },
      low: { date: "2026-01-03", price: 1 },
      startIndex: 1,
      endIndex: 3,
    });
  });

  it("把可视最高最低价标记在K线主图", () => {
    const option = buildChartOption({ data: base(), zoomStart: 30, zoomEnd: 80 });
    const candle = option?.series.find((series: any) => series.id === "kline");
    expect(candle?.markLine.data).toHaveLength(2);
    expect(candle?.markLine.data.map((item: any) => item.name)).toEqual(["最高价", "最低价"]);
  });

  it("缩放范围变化时生成不同的最高最低价标记", () => {
    const bars = Array.from({ length: 10 }, (_, index) => ({ ...bar(`2026-01-${String(index + 1).padStart(2, "0")}`, index + 10), high: index < 5 ? index + 20 : index + 100, low: index < 5 ? index : index + 5 }));
    const first = buildVisiblePriceMarkLine(bars, "dark", 0, 40);
    const second = buildVisiblePriceMarkLine(bars, "dark", 50, 100);
    expect(first?.data).not.toEqual(second?.data);
    expect(second?.data).toEqual([{ name: "最高价", yAxis: 109 }, { name: "最低价", yAxis: 4 }]);
  });

  it("K线顶部固定12px且不受指标和副图数量影响", () => {
    for (const compact of [true, false]) {
      for (const subplotCount of [1, 2, 3, 4]) {
        for (const mainIndicatorVisible of [true, false]) {
          const layout = buildChartPaneLayout({ requestedHeight: 560, subplotCount, intraday: false, compact, mainIndicatorVisible });
          expect(layout.topInset).toBe(12);
          expect(layout.tops[0]).toBe(12);
          expect(layout.heights.every((height, index) => height >= layout.minHeights[index])).toBe(true);
        }
      }
    }
    expect(buildChartPaneLayout({ requestedHeight: 560, subplotCount: 1, intraday: true }).topInset).toBe(48);
  });

  it("显式启用时间轴平移、滚轮缩放并联动全部副图", () => {
    const option = buildChartOption({ data: base(), subplotVisible: [true, true, true, true], zoomStart: 50, zoomEnd: 90 });
    expect(option?.dataZoom[0]).toMatchObject({ type: "inside", moveOnMouseMove: true, zoomOnMouseWheel: true, moveOnMouseWheel: false, preventDefaultMouseMove: true, xAxisIndex: [0, 1, 2, 3, 4], start: 50, end: 90 });
    expect(option?.dataZoom[1]).toMatchObject({ type: "slider", xAxisIndex: [0, 1, 2, 3, 4], start: 50, end: 90 });
  });

  it("normalizes invalid bars, nodes and missing indicator arrays", () => {
    const result = normalizeChartData(base({
      bars: [bar("2026-01-02"), { trade_date: "bad", open: "x", high: 1, low: 1, close: 1 }, bar("2026-01-02", 12)],
      pens: [{ id: "bad", ordinal: 0, start_date: "bad", end_date: "2026-01-01" }],
      indicators: {},
    }));
    expect(result?.bars).toHaveLength(1);
    expect(result?.pens).toEqual([]);
    expect(result?.indicators).toEqual({ macd: [], ma: [], boll: [] });
  });

  it("normalizes promotion candidates separately and removes them from reference profiles", () => {
    const candidate = { id: "candidate:r1", kind: "promotion_candidate", family_id: "candidate", revision_no: 1, active: true, candidate_source: "extension_decomposition", child_level: 1, parent_level: 2, status: "unresolved", source_entity_ids: ["center:r1"], required_unit_ids: Array.from({ length: 9 }, (_, index) => `p${index}`), search_unit_ids: Array.from({ length: 9 }, (_, index) => `p${index}`), start_date: "2026-01-01", end_date: "2026-01-03", observed_at: "2026-01-03", evidence_available_at: "2026-01-03", proof_ids: [], missing_evidence: [{ code: "three_segment_proofs_missing" }], rejected_proofs: [] };
    const full = normalizeChartData(base({ promotion_candidates: [candidate], promotion_candidate_revisions: [candidate] }));
    expect(full?.promotion_candidates[0].missing_evidence[0].code).toBe("three_segment_proofs_missing");
    const reference = normalizeChartData(base({ timeframe: "w", calculation_profile: "pen_centers_only", promotion_candidates: [candidate], promotion_candidate_revisions: [candidate] }));
    expect(reference?.promotion_candidates).toEqual([]);
    expect(reference?.promotion_candidate_revisions).toEqual([]);
  });

  it("uses nested API levels for center and movement filters", () => {
    const result = normalizeChartData({
      meta: { active_level: null, max_level: 2 },
      market: { symbol: "1A0688", timeframe: "d", adjustflag: "2", bars: [bar("2026-01-01")] },
      structure: { pens: [], components: [], centers: [], center_revisions: [], movements: [], movement_revisions: [], points: [], point_revisions: [], relations: [], issues: [], levels: [1, 2], unassigned_by_level: {} },
      indicators: { macd: [], ma: [], boll: [] }, drawings: { items: [], version: "" }, pagination: { has_more: false },
    } as any);
    expect(result?.center_levels).toEqual([1, 2]);
    expect(result?.movement_levels).toEqual([1, 2]);
  });

  it("keeps intraday price and volume in separate chart grids", () => {
    const data = base({ timeframe: "1", bars: [bar("2026-01-01 09:30:00"), bar("2026-01-01 09:31:00", 10.1)] });
    const option = buildChartOption({ data, theme: "dark", subplotVisible: [true, true], visible: { pens: true, centers: true, movements: true } });
    expect(option?.xAxis).toHaveLength(3);
    expect(option?.yAxis).toHaveLength(3);
    expect(option?.series.map((item: any) => item.type)).toEqual(["line", "line", "bar", "bar", "line", "line", "line"]);
    expect(option?.series[2]).toMatchObject({ xAxisIndex: 1, yAxisIndex: 1, name: "成交量" });
    expect(option?.dataZoom[0].xAxisIndex).toEqual([0, 1, 2]);
  });

  it("uses the same contiguous pane layout for intraday and K-line charts", () => {
    for (const intraday of [true, false]) {
      for (const subplotCount of [1, 2, 3, 4]) {
        const layout = buildChartPaneLayout({ requestedHeight: 560, subplotCount, intraday });
        expect(layout.heights).toHaveLength(subplotCount + 1);
        expect(layout.heights[0]).toBeGreaterThanOrEqual(240);
        expect(layout.heights.slice(1).every((height) => height >= 80)).toBe(true);
        layout.tops.slice(1).forEach((top, index) => {
          expect(top).toBeCloseTo(layout.tops[index] + layout.heights[index] + layout.gap + (index === 0 ? layout.timeAxisHeight : 0));
        });
        expect(layout.tops.at(-1)! + layout.heights.at(-1)! + layout.bottomInset).toBeCloseTo(layout.chartHeight);
      }
    }
  });

  it("maps one through four subplots to matching grids and axes for every chart type", () => {
    for (const timeframe of ["1", "d"]) {
      for (const subplotCount of [1, 2, 3, 4]) {
        const visible = Array.from({ length: 4 }, (_, index) => index < subplotCount);
        const data = base({ timeframe });
        const axes = buildAxes({ data, chartHeight: 800, subplotVisible: visible });
        expect(axes.grid).toHaveLength(subplotCount + 1);
        expect(axes.xAxis).toHaveLength(subplotCount + 1);
        expect(axes.yAxis).toHaveLength(subplotCount + 1);
        expect(axes.xAxis[0].axisLabel.show).toBe(true);
        expect(axes.xAxis.slice(1).every((axis: any) => axis.axisLabel.show === false)).toBe(true);
        expect(Object.values(axes.subplotAxisIndices)).toEqual(Array.from({ length: subplotCount }, (_, index) => index + 1));
      }
    }
  });

  it("defaults to a 60/40 split and grows only when minimum heights require it", () => {
    expect(defaultPaneRatios(2)).toEqual([0.6, 0.2, 0.2]);
    const roomy = buildChartPaneLayout({ requestedHeight: 800, subplotCount: 2, intraday: true });
    expect(roomy.heights[0] / roomy.heights.reduce((sum, value) => sum + value, 0)).toBeCloseTo(0.6);
    const crowded = buildChartPaneLayout({ requestedHeight: 560, subplotCount: 4, intraday: false });
    expect(crowded.chartHeight).toBe(674);
    expect(crowded.heights[0]).toBeGreaterThanOrEqual(240);
    const compact = buildChartPaneLayout({ requestedHeight: 430, subplotCount: 4, intraday: true, compact: true });
    expect(compact.heights[0]).toBeGreaterThanOrEqual(200);
    expect(compact.heights.slice(1).every((height) => height >= 72)).toBe(true);
  });

  it("resizes only adjacent panes, preserves their sum and clamps minimums", () => {
    const heights = [300, 100, 100];
    const minimums = [240, 80, 80];
    const ratios = resizeAdjacentPanes(heights, 0, 200, minimums);
    const resized = ratios.map((ratio) => ratio * 500);
    expect(resized).toEqual([320, 80, 100]);
    expect(resized.reduce((sum, value) => sum + value, 0)).toBe(500);
    expect(resizeAdjacentPanes(heights, 1, -200, minimums).map((ratio) => ratio * 500)).toEqual([300, 80, 120]);
  });

  it("versions stored layouts and isolates them by stock, timeframe and subplot count", () => {
    const stored = serializePaneRatios([0.7, 0.3], 1);
    expect(parseStoredPaneRatios(stored, 1)).toEqual([0.7, 0.3]);
    expect(parseStoredPaneRatios("broken", 1)).toEqual([0.6, 0.4]);
    expect(parseStoredPaneRatios(JSON.stringify({ version: 2, ratios: [0.8, 0.2] }), 1)).toEqual([0.6, 0.4]);
    expect(paneLayoutStorageKey("000001", "d", 1)).not.toBe(paneLayoutStorageKey("000001", "5", 1));
    expect(paneLayoutStorageKey("000001", "d", 1)).not.toBe(paneLayoutStorageKey("000002", "d", 1));
    expect(paneLayoutStorageKey("000001", "d", 1)).not.toBe(paneLayoutStorageKey("000001", "d", 2));
  });

  it("builds a minimal single candlestick series when optional layers are absent", () => {
    const option = buildChartOption({ data: base() });
    expect(option?.series).toHaveLength(1);
    expect(option?.series[0].type).toBe("candlestick");
    expect(JSON.stringify(option)).not.toContain("undefined");
  });

  it("keeps every series axis index within the generated axes", () => {
    const data = base({ indicators: { macd: [], ma: [], boll: [] } });
    const axes = buildAxes({ data, subplotIndicators: ["volume", "macd"], subplotVisible: [true, false] });
    const artifacts = buildChartArtifacts({ data, theme: "dark", subplotIndicators: ["volume", "macd"], subplotVisible: [true, false] });
    expect(axes.axisCount).toEqual({ x: 2, y: 2 });
    expect(artifacts.option?.series.every((item: any) => (item.xAxisIndex ?? 0) < axes.axisCount.x && (item.yAxisIndex ?? 0) < axes.axisCount.y)).toBe(true);
  });

  it("does not treat a legacy center without an explicit level as L1", () => {
    const data = base({ centers: [{ id: "legacy", ordinal: 0, kind: "center", status: "confirmed", start_date: "2026-01-01", end_date: "2026-01-03", zd: 9, zg: 10 }] });
    const result = buildChartArtifacts({ data, theme: "dark" });
    expect(result.visibleCenters).toEqual([]);
  });

  it("does not create hit targets for hidden pens", () => {
    const data = base({
      pens: [{ id: "p1", ordinal: 0, kind: "standard", status: "confirmed", start_date: "2026-01-01", end_date: "2026-01-02", start_price: 9, end_price: 11, direction: "up" }],
    });
    const result = buildChartArtifacts({ data, visible: { pens: false } });
    expect((result.option?.series as any[]).some((item) => item.id === "pen-hit-p1")).toBe(false);
  });

  it("draws every hierarchy level by default and supports per-level visibility", () => {
    const data = base({
      center_levels: [1, 2, 3],
      movement_levels: [1, 2, 3],
      max_available_center_level: 3,
      centers: [1, 2, 3].map((level) => ({ id: `c${level}`, ordinal: level - 1, level, role: "hierarchy", kind: "center", status: "confirmed", start_date: "2026-01-01", end_date: "2026-01-03", zd: 9 + level, zg: 10 + level })),
      movements: [1, 2, 3].map((level) => movement({ id: `m${level}`, family_id: `mf${level}`, ordinal: level - 1, level, start_price: 9, end_price: 12 })),
    });
    const defaultArtifacts = buildChartArtifacts({ data, theme: "dark" });
    expect(defaultArtifacts.visibleCenters.map((item) => item.level)).toEqual([3, 2, 1]);
    expect(defaultArtifacts.visibleMovements.map((item) => item.level)).toEqual([1, 2, 3]);
    const movementArtifacts = buildMovementSeries({ data, theme: "dark" }, data.bars.map((item: any) => item.trade_date));
    expect(movementArtifacts.series.filter((item: any) => item.type === "line").map((item: any) => item.lineStyle.color)).toEqual(["#F2C14E", "#4DD0E1", "#EC75B5"]);
    const filtered = buildChartArtifacts({ data, theme: "dark", visible: { centers: true, movements: true, centerLevels: { "2": false }, movementLevels: { "3": false } } });
    expect(filtered.visibleCenters.map((item) => item.level)).toEqual([3, 1]);
    expect(filtered.visibleMovements.map((item) => item.level)).toEqual([1, 2]);
    const hidden = buildChartArtifacts({ data, theme: "dark", visible: { centers: false, movements: false } });
    expect(hidden.visibleCenters).toEqual([]);
    expect(hidden.visibleMovements).toEqual([]);
  });


  it("filters malformed series and undefined data before ECharts", () => {
    const result = validateSeries([
      { id: "ok", type: "line", data: [["2026-01-01", 1]] },
      { id: "bad-axis", type: "line", xAxisIndex: 4, data: [] },
      { id: "bad-axis-type", type: "line", xAxisIndex: "0", data: [] },
      { id: "bad-data", type: "line", data: [undefined] },
      { id: "bad-type", type: "unknown", data: [] },
    ], { x: 1, y: 1 });
    expect(result.valid.map((item: any) => item.id)).toEqual(["ok"]);
    expect(result.issues.map((item) => item.code)).toEqual(["series_axis_out_of_range", "series_axis_out_of_range", "series_data_invalid", "series_invalid_shape"]);
  });

  it("rejects sparse, non-finite and malformed cartesian coordinates", () => {
    const sparse: unknown[] = [];
    sparse.length = 1;
    const result = validateSeries([
      { id: "sparse", type: "line", data: sparse },
      "series_coordinate_invalid",
      "series_coordinate_invalid",
    ], { x: 1, y: 1 });
  });

  it("rejects an axis whose grid index cannot be resolved", () => {
    const result = validateAxes({
      grid: [{}],
      xAxis: [{ type: "category", data: ["2026-01-01"], gridIndex: 1 }],
      yAxis: [{ type: "value" }],
    });
    expect(result.valid).toBe(false);
    expect(result.issues.map((item) => item.code)).toContain("axis_grid_out_of_range");
  });

  it("filters a center whose advertised source pen is absent", () => {
    const data = base({
      pens: [{ id: "p1", ordinal: 0, kind: "pen", status: "confirmed", start_date: "2026-01-01", end_date: "2026-01-02", start_price: 9, end_price: 10 }],
      centers: [{ id: "bad-center", ordinal: 0, level: 1, kind: "center", status: "confirmed", start_date: "2026-01-01", end_date: "2026-01-03", zd: 9, zg: 10, source_pen_ids: ["missing"] }],
    });
    const result = buildChartArtifacts({ data });
    expect(result.visibleCenters).toEqual([]);
    expect(result.issues.map((item) => item.code)).toContain("center_reference_missing");
  });

  it("does not build an option from an invalid explicit bars window", () => {
    const result = buildChartArtifacts({ data: base(), bars: [{ ...bar("2026-01-01"), close: Number.NaN }] });
    expect(result.option).toBeNull();
    expect(result.issues.map((item) => item.code)).toContain("empty_bars");
  });

  it("renders a clipped direct movement and deduplicated H/L labels", () => {
    const currentMovement = movement({ classification: "consolidation", start_date: "2025-12-01", path_points: [{ trade_date: "2026-01-01", price: 10 }] });
    const data = base({ movements: [currentMovement] });
    const result = buildMovementSeries({ data, theme: "dark", visible: { movements: true } }, data.bars.map((item: any) => item.trade_date));
    expect(result.series.filter((item: any) => item.type === "line")).toHaveLength(1);
    expect(result.visibleMovements[0].start_date).toBe("2026-01-01");
    expect(buildEndpointLabels([currentMovement] as any, ["2026-01-01", "2026-01-02", "2026-01-03"]).map((item) => item.label)).toEqual(["低点1", "高点1"]);
  });

  it("keeps the complete movement option valid after adding arrows and endpoints", () => {
    const currentMovement = movement();
    const result = buildChartArtifacts({
      data: base({ movements: [currentMovement] }),
      visible: { pens: true, centers: true, movements: true },
      subplotIndicators: ["volume"],
      subplotVisible: [true],
    });
    expect(result.option).not.toBeNull();
    expect(result.option?.series.some((item: any) => item.id === "movement-arrow-heads")).toBe(true);
    expect(result.option?.series.some((item: any) => item.id === "movement-boundaries")).toBe(true);
  });

  it("keeps original movement boundaries for tooltip callbacks after clipping", () => {
    const currentMovement = movement({ classification: "consolidation", start_date: "2025-12-01", path_points: [{ trade_date: "2026-01-01", price: 10 }] });
    const data = base({ movements: [currentMovement] });
    let callbackNode: any;
    const result = buildMovementSeries({ data, theme: "dark", formatMovementTooltip: (node) => { callbackNode = node; return "movement"; } }, ["2026-01-01", "2026-01-02", "2026-01-03"]);
    const endpoint = result.series.find((item: any) => item.id === "movement-boundaries");
    endpoint?.tooltip?.formatter({ data: { movementId: "m1" } });
    expect(callbackNode.start_date).toBe("2025-12-01");
  });

  it("marks provisional centers as candidates", () => {
    const data = base({ centers: [center({ id: "candidate", status: "provisional", recursive_eligible: false })] });
    const option = buildChartOption({ data, theme: "dark" });
    const mark = option?.series.find((item: any) => item.name === "K线")?.markArea?.data?.[0]?.[0];
    expect(mark?.itemStyle?.borderType).toBe("dashed");
    expect(mark?.label?.formatter).toContain("候选");
  });

  it("renders confirmed and candidate buy-sell points with distinct fills", () => {
    const data = base({ points: [
      point({ id: "p1" }),
      point({ id: "p2", family_id: "pf2", ordinal: 1, point_type: "first_sell", status: "candidate", start_date: "2026-01-03", end_date: "2026-01-03", point_date: "2026-01-03", point_price: 10.8 }),
      point({ id: "p3", family_id: "pf3", ordinal: 2, point_type: "third_sell", status: "invalidated", start_date: "2026-01-03", end_date: "2026-01-03", point_date: "2026-01-03", point_price: 10.7 }),
    ] });
    const option = buildChartOption({ data, theme: "dark" });
    const series = option?.series.find((item: any) => item.id === "buy-sell-points");
    expect(series.data).toHaveLength(2);
    expect(series.data[0].itemStyle.color).not.toBe("transparent");
    expect(series.data[1].itemStyle.color).toBe("transparent");
  });

  it.each(["light", "dark"])("matches point colors to exact historical center revisions in %s theme", (theme) => {
    const historical = center({ id: "cr1", active: false, level: 1 });
    const parent = center({ id: "cr2", active: true, revision_no: 2, level: 2 });
    const data = base({ centers: [parent], center_revisions: [historical, parent], points: [
      point({ id: "buy", level: 2, confirmed_at: "2026-01-03" }),
      point({ id: "sell", point_type: "third_sell", status: "candidate" }),
      point({ id: "parent-point", center_revision_id: parent.id }),
    ] });
    const before = JSON.stringify(data);
    const dates = data.bars.map((item: any) => item.trade_date);
    const childColor = buildCenterAreas({ data, theme, selectedStructureId: historical.id }, dates).areas.find((area) => area[0].centerId === historical.id)?.[0].itemStyle.borderColor;
    const parentColor = buildCenterAreas({ data, theme }, dates).areas[0][0].itemStyle.borderColor;
    const [series] = buildBuySellPointSeries({ data, theme, visible: { centers: false, centerLevels: { 1: false } } }, dates);
    expect(series.data.map((item: any) => item.label.color)).toEqual([childColor, childColor, parentColor]);
    expect(series.data.map((item: any) => item.itemStyle.borderColor)).toEqual([childColor, childColor, parentColor]);
    expect(series.data.map((item: any) => item.itemStyle.color)).toEqual([childColor, "transparent", parentColor]);
    expect(series.data.map((item: any) => item.label.position)).toEqual(["bottom", "top", "bottom"]);
    expect(series.data.map((item: any) => item.value)).toEqual(data.points.map((item: any) => [item.point_date, item.point_price]));
    expect(childColor).not.toBe(parentColor);
    expect(JSON.stringify(data)).toBe(before);
  });

  it("uses the center's supplied color for both buy and sell labels and markers", () => {
    const associated = center({ id: "cr1", color: "#123456" });
    const data = base({ centers: [associated], points: [point(), point({ id: "sell", point_type: "third_sell" })] });
    const [series] = buildBuySellPointSeries({ data }, data.bars.map((item: any) => item.trade_date));
    expect(series.data.every((item: any) => item.label.color === "#123456" && item.itemStyle.color === "#123456" && item.itemStyle.borderColor === "#123456")).toBe(true);
  });

  it.each(["light", "dark"])("uses neutral %s markers rather than a same-family parent when evidence is missing", (theme) => {
    const data = base({ centers: [center({ id: "parent", level: 2 })], points: [point()] });
    const [series] = buildBuySellPointSeries({ data, theme }, data.bars.map((item: any) => item.trade_date));
    expect(series.data[0].label.color).toBe(theme === "light" ? "#64748b" : "#94a3b8");
    expect(series.tooltip.formatter({ data: { pointId: "point-1" } })).toContain("中枢证据缺失");
  });

  it("keeps components hidden by default and highlights selected center roles", () => {
    const entryComponent = component();
    const selectedCenter = center({ id: "center-1", entry_component_id: entryComponent.id });
    const data = base({ components: [entryComponent], centers: [selectedCenter] });
    expect(buildComponentSeries({ data, visible: { components: false } }, ["2026-01-01", "2026-01-02", "2026-01-03"]).series).toHaveLength(0);
    const selected = buildComponentSeries({ data, visible: { components: false }, selectedStructureId: selectedCenter.id }, ["2026-01-01", "2026-01-02", "2026-01-03"]);
    expect(selected.series).toHaveLength(1);
    expect(selected.series[0].lineStyle.width).toBe(3.2);
  });

  it("returns an explicit empty artifact without constructing ECharts series", () => {
    const data = base({ bars: [] });
    const result = buildChartArtifacts({ data });
    expect(result.option).toBeNull();
    expect(result.issues.map((item) => item.code)).toContain("empty_bars");
  });
});
