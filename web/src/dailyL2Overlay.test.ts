import { describe, expect, it } from "vitest";
import * as echarts from "echarts";
import { buildChartArtifacts, buildComponentSeries, buildDailyL2Series, buildPenSeries, normalizeChartData } from "./chartBuilders";
import { centersForDisplay, sameDisplayRun } from "./centerDisplay";
import { dailyL2Bounds, dailyL2Label, dailyL2StatusLabel, mergeDailyL2Overlays, projectionContains, projectionRectangle } from "./dailyL2Overlay";
import { mergeIntradayData } from "./intradayRefresh";
import type { Center, ChartData, DailyL2Projection } from "./types";

const projection = (patch: Partial<DailyL2Projection> = {}): DailyL2Projection => ({
  id: "daily-l2:daily-revision", revision_id: "daily-revision", family_id: "family", ordinal: 3, level: 2,
  display_role: "active", parent_revision_ids: [], status: "formed", active: true,
  start_date: "2026-02-02", end_date: "2026-02-05", zd: 10, zg: 11, source_timeframe: "d",
  target_start_date: "2026-02-27", target_end_date: "2026-02-27", clipped_start: false, clipped_end: false, ...patch,
});
const native = (patch: Partial<Center> = {}): Center => ({
  id: "native", family_id: "family", revision_no: 1, ordinal: 0, kind: "center", level: 1,
  status: "formed", active: true, start_date: "2026-01-30", end_date: "2026-02-27", zd: 9, zg: 10, fixed_zd: 9, fixed_zg: 10,
  source_pen_ids: ["p"], entry_unit_ids: [], core_unit_ids: ["p"], z_unit_ids: ["p"], child_center_ids: [], formation_modes: [], ...patch,
} as Center);
const fixture = (patch: Partial<ChartData> = {}): ChartData => ({
  symbol: "000001", timeframe: "m", adjustflag: "2", calculation_profile: "pen_centers_only",
  bars: ["2026-01-30", "2026-02-27", "2026-03-31"].map(trade_date => ({ trade_date, open: 10, close: 10.5, high: 12, low: 8, volume: 100, amount: 1000 })),
  pens: [{ id: "p", kind: "pen", ordinal: 0, level: 0, status: "confirmed", start_date: "2026-01-30", end_date: "2026-02-27", start_price: 8, end_price: 12 }],
  centers: [native()], center_revisions: [native()], components: [], movements: [], movement_revisions: [], points: [], point_revisions: [], relations: [], issues: [],
  levels: [1], center_levels: [1], movement_levels: [], unassigned_by_level: {}, drawings: [], drawings_version: "", indicators: {macd: []},
  available: true, has_more: true, active_structure_level: 1, max_available_center_level: 1,
  definition_version: "v27", calculator_fingerprint: "engine", structure_version: "monthly", run_id: 5,
  display_centers: [{ revision_id: "native", display_role: "active", parent_revision_ids: [] }], display_center_levels: [1],
  overlays: { daily_l2: { status: "ready", error: null, source: {timeframe: "d", symbol: "000001", adjustflag: "2", run_id: 10, definition_version: "v27", calculator_fingerprint: "engine", market_version: "daily-market", structure_version: "daily-structure", source_cutoff: "2026-03-31"}, centers: [projection()] } },
  ...patch,
});

describe("周月独立日线 L2 参考", () => {
  it("月初日期使用后端目标月，不靠最近月末映射，单格矩形与命中共用完整宽度", () => {
    const data = fixture();
    const bounds = dailyL2Bounds(data, data.bars.map(bar => bar.trade_date));
    expect(bounds[0].startIndex).toBe(1);
    const rect = projectionRectangle(bounds[0], (index, price) => [100 + index * 20, 200 - price * 10], 20);
    expect(rect).toEqual({x: 110, y: 90, width: 20, height: 10});
    expect(projectionContains(rect, 111, 95)).toBe(true);
    expect(projectionContains(rect, 131, 95)).toBe(false);
    expect(data.overlays?.daily_l2.centers[0].start_date).toBe("2026-02-02");
  });

  it.each([
    ["跨年 ISO 周", "2025-12-29", "2026-01-02"],
    ["节假日短周", "2026-02-16", "2026-02-20"],
  ])("%s 使用明确的目标周标签", (_, original, target) => {
    const data = fixture({timeframe: "w"});
    data.overlays!.daily_l2.centers = [projection({start_date: original, target_start_date: target, target_end_date: target})];
    expect(dailyL2Bounds(data, ["2025-12-26", target])[0].startIndex).toBe(1);
    expect(dailyL2Bounds(data, ["2025-12-26"])).toEqual([]);
  });

  it("独立投影不混进中枢或匹配周月源笔，可单独隐藏且不被原生中枢开关影响", () => {
    const data = fixture();
    const visible = buildChartArtifacts({data, visible: {centers: false, dailyL2: true}});
    expect(visible.visibleCenters).toEqual([]);
    expect(visible.visibleDailyL2).toHaveLength(1);
    expect(visible.issues).toEqual([]);
    expect(visible.option?.series.some((item: any) => item.id === "daily-l2-projections")).toBe(true);
    expect(buildChartArtifacts({data, visible: {dailyL2: false}}).visibleDailyL2).toEqual([]);
    expect(centersForDisplay(data).map(center => center.id)).toEqual(["native"]);
    expect(centersForDisplay(data, null, true, {1: false, 2: true}).map(center => center.id)).toEqual(["native"]);
    expect(centersForDisplay(data, "daily-l2:daily-revision").map(center => center.id)).toEqual(["native"]);
  });

  it("绘制函数保持单格非零宽度及日线配色，缩放保留跨过整个视窗的投影", () => {
    const data = fixture();
    const built = buildDailyL2Series({data, theme: "dark"}, data.bars.map(bar => bar.trade_date));
    const render = built.series[0].renderItem({dataIndex: 0, coordSys: {x: 0, y: 0, width: 300, height: 300}}, {coord: ([index, price]: number[]) => [100 + index * 20, 200 - price * 10], size: () => [20, 0]});
    expect(render?.children[0].shape).toMatchObject({width: 20, height: 10});
    expect(render?.children[0].style.stroke).toBe("#4DD0E1");
    expect(buildChartArtifacts({data}).option?.dataZoom.every((zoom: any) => zoom.filterMode === "weakFilter")).toBe(true);
  });

  it("ECharts 实际渲染缩放窗口内的单格和跨窗口投影", () => {
    const data = fixture();
    data.overlays!.daily_l2.centers.push(projection({id: "daily-l2:span", ordinal: 4, target_start_date: "2026-01-30", target_end_date: "2026-03-31"}));
    const chart = echarts.init(null, undefined, {renderer: "svg", ssr: true, width: 800, height: 600});
    try {
      chart.setOption(buildChartArtifacts({data, zoomStart: 45, zoomEnd: 55}).option!);
      const svg = chart.renderToSVGString();
      expect(svg).toContain("日线 L2 #4");
      expect(svg).toContain("日线 L2 #5");
      expect(svg).not.toContain("NaN");
    } finally { chart.dispose(); }
  });

  it("遗留周月高阶中枢、走势、组件、买卖点不绘制，选中原生中枢只高亮笔", () => {
    const data = fixture();
    data.centers.push(native({id: "old-l2", level: 2}));
    data.center_revisions.push(native({id: "old-l2", level: 2}));
    data.display_centers!.push({revision_id: "old-l2", display_role: "active", parent_revision_ids: []});
    data.movements = [{id: "old-movement", kind: "movement", level: 1, status: "confirmed", start_date: "2026-01-30", end_date: "2026-02-27", start_price: 8, end_price: 12}] as any;
    data.components = [{id: "old-component", kind: "component"}] as any;
    data.points = [{id: "old-point", kind: "structural_point"}] as any;
    const before = JSON.stringify(data);
    const artifacts = buildChartArtifacts({data, selectedStructureId: "native", visible: {components: true, movements: true}});
    expect(artifacts.visibleCenters.map(center => center.level)).toEqual([1]);
    expect(artifacts.visibleMovements).toEqual([]);
    expect(artifacts.visibleComponents).toEqual([]);
    expect(artifacts.option?.series.some((item: any) => item.id === "buy-sell-points")).toBe(false);
    expect(buildComponentSeries({data, selectedStructureId: "native", visible: {components: true}}, data.bars.map(bar => bar.trade_date)).series).toEqual([]);
    expect(buildPenSeries({data, selectedStructureId: "native"}, data.bars.map(bar => bar.trade_date))[0].lineStyle.width).toBe(3.4);
    expect(JSON.stringify(data)).toBe(before);
    expect(centersForDisplay(data, "old-l2").map(center => center.level)).toEqual([1]);
  });

  it("日线与分钟原生 L2 不受参考模式过滤，也不展示参考投影", () => {
    for (const timeframe of ["d", "5", "30"]) {
      const data = fixture({timeframe, calculation_profile: "full", centers: [native({level: 2})], center_revisions: [native({level: 2})]});
      expect(normalizeChartData(data)?.centers[0].level).toBe(2);
      expect(buildChartArtifacts({data}).visibleDailyL2).toEqual([]);
    }
  });

  it("同来源分页合并同一投影的覆盖边界，原始证据日期保持不变", () => {
    const old = fixture(), fresh = fixture();
    old.overlays!.daily_l2.centers[0] = projection({target_start_date: "2026-02-27", target_end_date: "2026-03-31", clipped_start: true});
    fresh.overlays!.daily_l2.centers[0] = projection({target_start_date: "2026-01-30", target_end_date: "2026-02-27", clipped_end: true});
    expect(sameDisplayRun(old, fresh)).toBe(true);
    expect(mergeDailyL2Overlays(fresh, old)?.daily_l2.centers).toEqual([projection({target_start_date: "2026-01-30", target_end_date: "2026-03-31"})]);
  });

  it("仅日线源版本改变也禁止拼接；刷新同时替换投影、展示引用和版本", () => {
    const old = fixture(), fresh = fixture();
    fresh.overlays!.daily_l2.source!.run_id = 11;
    fresh.overlays!.daily_l2.centers = [];
    fresh.display_centers = [];
    fresh.display_center_levels = [];
    fresh.calculator_fingerprint = "new-engine";
    expect(sameDisplayRun(old, {...fresh, calculator_fingerprint: old.calculator_fingerprint})).toBe(false);
    expect(mergeDailyL2Overlays(fresh, old)).toEqual(fresh.overlays);
    const refreshed = mergeIntradayData(old, fresh);
    expect(refreshed.overlays).toBe(fresh.overlays);
    expect(refreshed.display_centers).toEqual([]);
    expect(refreshed.display_center_levels).toEqual([]);
    expect(refreshed.calculator_fingerprint).toBe("new-engine");
  });

  it("区分来源失败和没有 L2，组成身份与截止日可见，只有正式来源可绘制", () => {
    const data = fixture();
    expect(dailyL2Label(projection({display_role: "constituent"}))).toBe("日线 L2 #4 · 组成中枢");
    data.overlays!.daily_l2.status = "stale";
    expect(dailyL2StatusLabel(data)).toContain("更新失败");
    expect(dailyL2StatusLabel(data)).toContain("2026-03-31");
    expect(dailyL2Bounds(data, data.bars.map(bar => bar.trade_date))).toHaveLength(1);
    data.overlays!.daily_l2.status = "unavailable";
    expect(dailyL2StatusLabel(data)).toContain("来源不可用");
    expect(dailyL2Bounds(data, data.bars.map(bar => bar.trade_date))).toEqual([]);
    data.overlays!.daily_l2.status = "ready";
    data.overlays!.daily_l2.centers = [];
    expect(dailyL2StatusLabel(data)).toContain("暂无日线 L2");
  });

  it("拒绝错误股票或复权来源，不因本地同名 id 绑定周月笔", () => {
    const data = fixture({centers: [native({id: "daily-l2:daily-revision"})], center_revisions: [native({id: "daily-l2:daily-revision"})]});
    data.overlays!.daily_l2.source!.adjustflag = "3";
    expect(dailyL2Bounds(data, data.bars.map(bar => bar.trade_date))).toEqual([]);
    data.overlays!.daily_l2.source!.adjustflag = "2";
    const plainPens = buildPenSeries({data, selectedProjectionId: "daily-l2:daily-revision"}, data.bars.map(bar => bar.trade_date));
    expect(plainPens[0].lineStyle.width).toBe(1.8);
    expect(plainPens[0].lineStyle.opacity).toBe(0.88);
  });
});
