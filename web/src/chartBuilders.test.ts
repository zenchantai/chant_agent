import { describe, expect, it } from "vitest";
import {
  buildAxes,
  buildChartArtifacts,
  buildChartOption,
  buildEndpointLabels,
  buildMovementSeries,
  normalizeChartData,
  validateAxes,
  validateSeries,
} from "./chartBuilders";

const bar = (date: string, close = 10) => ({
  trade_date: date, open: close - 0.2, high: close + 0.4, low: close - 0.4, close, volume: 100, amount: 1000,
});
const base = (overrides: Record<string, unknown> = {}) => ({
  symbol: "1A0001", timeframe: "d", bars: [bar("2026-01-01"), bar("2026-01-02", 11), bar("2026-01-03", 10.5)],
  pens: [], centers: [], pen_centers: [], movements: [], center_relations: [], indicators: { macd: [], ma: [], boll: [] },
  active_structure_level: 1, max_available_center_level: 1, has_more: false, available: true,
  definition_version: "v12", structure_version: "v12", decomposition: {}, ...overrides,
}) as any;

describe("chart builders", () => {
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

  it("builds only one axis and no structure series for intraday", () => {
    const data = base({ timeframe: "1", bars: [bar("2026-01-01 09:30:00"), bar("2026-01-01 09:31:00", 10.1)] });
    const option = buildChartOption({ data, theme: "dark", subplotVisible: [true, true], visible: { pens: true, centers: true, movements: true } });
    expect(option?.xAxis).toHaveLength(1);
    expect(option?.yAxis).toHaveLength(1);
    expect(option?.series.map((item: any) => item.type)).toEqual(["line", "line"]);
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

  it("does not reject an L2 center whose lower-level child is omitted from the page", () => {
    const data = base({
      active_structure_level: 2,
      max_available_center_level: 2,
      center_levels: [1, 2],
      centers: [{ id: "l2", ordinal: 0, level: 2, kind: "center", status: "confirmed", start_date: "2026-01-01", end_date: "2026-01-03", zd: 9, zg: 10, child_center_ids: ["center-L1-child"] }],
    });
    expect(buildChartArtifacts({ data }).visibleCenters.map((item) => item.id)).toEqual(["l2"]);
  });

  it("converts per-level decomposition payloads to the active level", () => {
    const result = normalizeChartData(base({
      active_structure_level: 2,
      max_available_center_level: 2,
      center_levels: [1, 2],
      decomposition: { "1": { status: "complete" }, "2": { status: "partial", issues: [{ code: "x" }] } },
    }));
    expect(result?.decomposition).toEqual({ status: "partial", issues: [{ code: "x" }] });
    expect(result?.center_levels).toEqual([1, 2]);
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
      { id: "nan", type: "line", data: [["2026-01-01", Number.NaN]] },
      { id: "bad-line-point", type: "line", data: [["2026-01-01", "bad"]] },
      { id: "bad-candle", type: "candlestick", data: [[1, 2, 3]] },
      { id: "bad-scatter-date", type: "scatter", data: [["not-a-date", 2]] },
    ], { x: 1, y: 1 });
    expect(result.valid).toEqual([]);
    expect(result.issues.map((item) => item.code)).toEqual([
      "series_data_invalid",
      "series_data_invalid",
      "series_coordinate_invalid",
      "series_coordinate_invalid",
      "series_coordinate_invalid",
    ]);
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
    const movement = { id: "m1", ordinal: 0, level: 1, role: "same_level_decomposition", direction: "up", classification: "consolidation", status: "confirmed", start_date: "2025-12-01", end_date: "2026-01-03", start_price: 8, end_price: 12, path_points: [{ trade_date: "2026-01-01", price: 10 }] };
    const data = base({ movements: [movement] });
    const result = buildMovementSeries({ data, theme: "dark", visible: { movements: true } }, data.bars.map((item: any) => item.trade_date));
    expect(result.series.filter((item: any) => item.type === "line")).toHaveLength(1);
    expect(result.visibleMovements[0].start_date).toBe("2026-01-01");
    expect(buildEndpointLabels([movement] as any, ["2026-01-01", "2026-01-02", "2026-01-03"]).map((item) => item.label)).toEqual(["L1", "H1"]);
  });

  it("keeps the complete movement option valid after adding arrows and endpoints", () => {
    const movement = { id: "m1", ordinal: 0, level: 1, role: "same_level_decomposition", direction: "up", classification: "trend", status: "confirmed", start_date: "2026-01-01", end_date: "2026-01-03", start_price: 8, end_price: 12 };
    const result = buildChartArtifacts({
      data: base({ movements: [movement] }),
      visible: { pens: true, centers: true, movements: true },
      subplotIndicators: ["volume"],
      subplotVisible: [true],
    });
    expect(result.option).not.toBeNull();
    expect(result.option?.series.some((item: any) => item.id === "movement-arrow-heads")).toBe(true);
    expect(result.option?.series.some((item: any) => item.id === "movement-boundaries")).toBe(true);
  });

  it("keeps original movement boundaries for tooltip callbacks after clipping", () => {
    const movement = { id: "m1", ordinal: 0, level: 1, role: "same_level_decomposition", direction: "up", classification: "consolidation", status: "confirmed", start_date: "2025-12-01", end_date: "2026-01-03", start_price: 8, end_price: 12, path_points: [{ trade_date: "2026-01-01", price: 10 }] };
    const data = base({ movements: [movement] });
    let callbackNode: any;
    const result = buildMovementSeries({ data, theme: "dark", formatMovementTooltip: (node) => { callbackNode = node; return "movement"; } }, ["2026-01-01", "2026-01-02", "2026-01-03"]);
    const endpoint = result.series.find((item: any) => item.id === "movement-boundaries");
    endpoint?.tooltip?.formatter({ data: { movementId: "m1" } });
    expect(callbackNode.start_date).toBe("2025-12-01");
  });

  it("marks provisional centers with upgrade progress", () => {
    const data = base({ centers: [{ id: "candidate", ordinal: 0, level: 1, kind: "center", status: "provisional", upgrade_kind: "extension_3x3", progress: "2/3", start_date: "2026-01-01", end_date: "2026-01-03", zd: 9, zg: 10 }] });
    const option = buildChartOption({ data, theme: "dark" });
    const mark = option?.series.find((item: any) => item.name === "K线")?.markArea?.data?.[0]?.[0];
    expect(mark?.itemStyle?.borderType).toBe("dashed");
    expect(mark?.label?.formatter).toContain("2/3");
  });

  it("returns an explicit empty artifact without constructing ECharts series", () => {
    const data = base({ bars: [] });
    const result = buildChartArtifacts({ data });
    expect(result.option).toBeNull();
    expect(result.issues.map((item) => item.code)).toContain("empty_bars");
  });
});
