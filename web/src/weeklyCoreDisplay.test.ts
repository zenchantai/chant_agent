import { describe, expect, it } from "vitest";
import { formatCenterTooltip } from "./App";
import { buildChartArtifacts, buildPenSeries } from "./chartBuilders";
import { centerDisplayLabel, centerDisplayRange } from "./centerDisplay";
import type { Center, ChartData, DailyL2Projection } from "./types";

const dates = ["2025-01-03", "2025-01-10", "2025-01-17", "2025-01-24", "2025-01-31", "2025-02-07", "2025-02-14"];
const native = (): Center => ({
  id: "native", family_id: "family", revision_no: 20, ordinal: 0, kind: "center", level: 1,
  status: "formed", active: true, start_date: dates[0], end_date: dates[6], core_start_date: dates[1], core_end_date: dates[4],
  zd: 9, zg: 10, fixed_zd: 9, fixed_zg: 10, dd: 8, gg: 12,
  source_pen_ids: ["entry", "core1", "core2", "core3", "extension", "departure"],
  entry_unit_ids: ["entry"], core_unit_ids: ["core1", "core2", "core3"], z_unit_ids: ["core1", "core3", "extension"],
  extension_unit_ids: ["extension"], departure_unit_ids: ["departure"], child_center_ids: [], formation_modes: [],
} as unknown as Center);
const fixture = (timeframe = "w"): ChartData => ({
  symbol: "1A0001", timeframe, adjustflag: "2", calculation_profile: ["w", "m"].includes(timeframe) ? "pen_centers_only" : "full",
  bars: dates.map(trade_date => ({trade_date, open: 10, close: 10.5, high: 12, low: 8, volume: 100, amount: 1000})),
  pens: ["entry", "core1", "core2", "core3", "extension", "departure"].map((id, index) => ({
    id, ordinal: index, kind: "pen", level: 0, status: "confirmed", start_date: dates[index], end_date: dates[index + 1], start_price: index % 2 ? 8 : 12, end_price: index % 2 ? 12 : 8,
  })),
  centers: [native()], center_revisions: [native()], components: [], movements: [], movement_revisions: [], points: [], point_revisions: [], promotion_candidates: [], promotion_candidate_revisions: [], relations: [], issues: [],
  levels: [1], center_levels: [1], movement_levels: [], unassigned_by_level: {}, drawings: [], drawings_version: "", indicators: {macd: []},
  available: true, has_more: true, active_structure_level: 1, max_available_center_level: 1,
  definition_version: "v27", calculator_fingerprint: "engine", structure_version: "weekly", run_id: 5,
  display_centers: [{revision_id: "native", display_role: "active", parent_revision_ids: []}], display_center_levels: [1],
});

describe("周线原生中枢仅展示三笔核心", () => {
  it("绘制框和透明命中框共用核心日期，完整源修订保持不变", () => {
    const data = fixture();
    const before = JSON.stringify(data);
    const artifacts = buildChartArtifacts({data});
    const candle = artifacts.option?.series.find((series: any) => series.id === "kline");
    const hit = artifacts.option?.series.find((series: any) => series.id === "center-hit-native");
    expect(candle.markArea.data[0][0].xAxis).toBe(dates[1]);
    expect(candle.markArea.data[0][1].xAxis).toBe(dates[4]);
    expect([...new Set(hit.data.map((item: any) => item.value[0]))]).toEqual([dates[1], dates[4]]);
    expect(candle.markArea.data[0][0].label.formatter).toBe("L1 三笔核心 #1 · 旧版证据");
    expect(centerDisplayRange(artifacts.visibleCenters[0], "w")).toEqual({start_date: dates[1], end_date: dates[4]});
    expect(artifacts.visibleCenters[0].end_date).toBe(dates[6]);
    expect(JSON.stringify(data)).toBe(before);
  });

  it("页面仅覆盖延伸时，即使选中该修订也不在页边生成幽灵框", () => {
    const data = fixture();
    const artifacts = buildChartArtifacts({data, bars: data.bars.slice(5), selectedStructureId: "native"});
    expect(artifacts.visibleCenters).toEqual([]);
    expect(artifacts.option?.series.some((series: any) => series.id === "center-hit-native")).toBe(false);
    expect(artifacts.option?.series.find((series: any) => series.id === "kline").markArea).toBeUndefined();
  });

  it("历史分页只裁剪核心与当前页面交集，右边界不延展至延伸区域", () => {
    const data = fixture();
    const artifacts = buildChartArtifacts({data, bars: data.bars.slice(3)});
    const area = artifacts.option?.series.find((series: any) => series.id === "kline").markArea.data[0];
    expect([area[0].xAxis, area[1].xAxis]).toEqual([dates[3], dates[4]]);
    expect(artifacts.visibleCenters[0].core_start_date).toBe(dates[1]);
  });

  it.each([undefined, "invalid-date", "2026-01-01"])("核心日期缺失或无效时不回退为完整延伸范围：%s", (coreStart) => {
    const data = fixture();
    data.centers[0].core_start_date = coreStart;
    data.center_revisions[0].core_start_date = coreStart;
    const artifacts = buildChartArtifacts({data});
    expect(artifacts.visibleCenters).toEqual([]);
    expect(artifacts.issues.some(issue => issue.code === "center_invalid_range")).toBe(true);
  });

  it("选中周线中枢只强调核心三笔，进入、延伸和离开笔保留普通线宽", () => {
    const data = fixture();
    const series = buildPenSeries({data, selectedStructureId: "native"}, dates);
    expect(series.filter(item => item.lineStyle.width === 3.4).map(item => item.id)).toEqual(["core1", "core2", "core3"]);
    expect(series.filter(item => ["entry", "extension", "departure"].includes(item.id)).every(item => item.lineStyle.width === 1.8 && item.lineStyle.opacity === 0.3)).toBe(true);
  });

  it.each(["m", "d", "5", "30"])("%s 保持原始完整范围与延伸笔强调", (timeframe) => {
    const data = fixture(timeframe);
    const artifacts = buildChartArtifacts({data});
    const area = artifacts.option?.series.find((series: any) => series.id === "kline").markArea.data[0];
    expect([area[0].xAxis, area[1].xAxis]).toEqual([dates[0], dates[6]]);
    expect(buildPenSeries({data, selectedStructureId: "native"}, dates).find(item => item.id === "extension")?.lineStyle.width).toBe(3.4);
    expect(centerDisplayLabel(data, data.centers[0])).toBe("L1 中枢 #1 · 旧版证据");
  });

  it("周线 tooltip 明确三笔核心，只显示核心日期；月线 tooltip 仍显示完整日期", () => {
    const center = native();
    const weekly = formatCenterTooltip(center, "w");
    expect(weekly).toContain("三笔核心范围：2025-01-10 → 2025-01-31");
    expect(weekly).not.toContain("2025-02-14");
    expect(formatCenterTooltip(center, "m")).toContain("实际边界：2025-01-03 → 2025-02-14");
  });

  it("独立日线 L2 参考不随周线原生核心收窄", () => {
    const data = fixture();
    const projection: DailyL2Projection = {
      id: "daily-l2:daily", revision_id: "daily", family_id: "daily-family", ordinal: 0, level: 2, status: "formed", active: true,
      display_role: "active", parent_revision_ids: [], start_date: dates[0], end_date: dates[6], source_timeframe: "d", zd: 10, zg: 11,
      target_start_date: dates[0], target_end_date: dates[6], clipped_start: false, clipped_end: false,
    };
    data.overlays = {daily_l2: {status: "ready", error: null, source: {timeframe: "d", symbol: data.symbol, adjustflag: "2", definition_version: "v27", calculator_fingerprint: "engine", market_version: "daily-market", structure_version: "daily", source_cutoff: dates[6]}, centers: [projection]}};
    const artifacts = buildChartArtifacts({data});
    expect(artifacts.visibleDailyL2[0]).toEqual({center: projection, startIndex: 0, endIndex: 6});
    expect(artifacts.option?.series.find((series: any) => series.id === "daily-l2-projections").data[0]).toEqual([0, 6, 10, 11]);
    expect(data.overlays.daily_l2.centers[0]).toBe(projection);
  });
});
