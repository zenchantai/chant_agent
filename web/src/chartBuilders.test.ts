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
  buildCenterAreas,
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
const base = (overrides: Record<string, unknown> = {}) => ({
  symbol: "1A0001", timeframe: "d", bars: [bar("2026-01-01"), bar("2026-01-02", 11), bar("2026-01-03", 10.5)],
  pens: [], centers: [], pen_centers: [], movements: [], center_relations: [], indicators: { macd: [], ma: [], boll: [] },
  active_structure_level: 1, max_available_center_level: 1, has_more: false, available: true,
  definition_version: "v12", structure_version: "v12", ...overrides,
}) as any;

describe("chart builders", () => {
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

  it("draws every hierarchy level by default and supports per-level visibility", () => {
    const data = base({
      center_levels: [1, 2, 3],
      movement_levels: [1, 2, 3],
      max_available_center_level: 3,
      centers: [1, 2, 3].map((level) => ({ id: `c${level}`, ordinal: level - 1, level, role: "hierarchy", kind: "center", status: "confirmed", start_date: "2026-01-01", end_date: "2026-01-03", zd: 9 + level, zg: 10 + level })),
      movements: [1, 2, 3].map((level) => ({ id: `m${level}`, ordinal: level - 1, level, role: "hierarchy_component", kind: "movement", classification: "trend", status: "confirmed", direction: "up", start_date: "2026-01-01", end_date: "2026-01-03", start_price: 9, end_price: 12 })),
    });
    const defaultArtifacts = buildChartArtifacts({ data, theme: "dark" });
    expect(defaultArtifacts.visibleCenters.map((item) => item.level)).toEqual([1, 2, 3]);
    expect(defaultArtifacts.visibleMovements.map((item) => item.level)).toEqual([1, 2, 3]);
    const movementArtifacts = buildMovementSeries({ data, theme: "dark" }, data.bars.map((item: any) => item.trade_date));
    expect(movementArtifacts.series.filter((item: any) => item.type === "line").map((item: any) => item.lineStyle.color)).toEqual(["#F2C14E", "#A78BFA", "#FF7043"]);
    const filtered = buildChartArtifacts({ data, theme: "dark", visible: { centers: true, movements: true, centerLevels: { "2": false }, movementLevels: { "3": false } } });
    expect(filtered.visibleCenters.map((item) => item.level)).toEqual([1, 3]);
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
    const movement = { id: "m1", ordinal: 0, level: 1, role: "hierarchy_component", direction: "up", classification: "consolidation", status: "confirmed", start_date: "2025-12-01", end_date: "2026-01-03", start_price: 8, end_price: 12, path_points: [{ trade_date: "2026-01-01", price: 10 }] };
    const data = base({ movements: [movement] });
    const result = buildMovementSeries({ data, theme: "dark", visible: { movements: true } }, data.bars.map((item: any) => item.trade_date));
    expect(result.series.filter((item: any) => item.type === "line")).toHaveLength(1);
    expect(result.visibleMovements[0].start_date).toBe("2026-01-01");
    expect(buildEndpointLabels([movement] as any, ["2026-01-01", "2026-01-02", "2026-01-03"]).map((item) => item.label)).toEqual(["L1", "H1"]);
  });

  it("keeps the complete movement option valid after adding arrows and endpoints", () => {
    const movement = { id: "m1", ordinal: 0, level: 1, role: "hierarchy_component", direction: "up", classification: "trend", status: "confirmed", start_date: "2026-01-01", end_date: "2026-01-03", start_price: 8, end_price: 12 };
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
    const movement = { id: "m1", ordinal: 0, level: 1, role: "hierarchy_component", direction: "up", classification: "consolidation", status: "confirmed", start_date: "2025-12-01", end_date: "2026-01-03", start_price: 8, end_price: 12, path_points: [{ trade_date: "2026-01-01", price: 10 }] };
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
