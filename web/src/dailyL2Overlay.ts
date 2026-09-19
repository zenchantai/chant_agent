import type { ChartData, ChartOverlays, DailyL2Projection } from "./types";

export const isReferenceProfile = (data: Pick<ChartData, "timeframe" | "calculation_profile">) =>
  data.timeframe === "w" || data.timeframe === "m" || data.calculation_profile === "pen_centers_only";

export const dailyL2Label = (center: DailyL2Projection) =>
  `日线 L2 #${center.ordinal + 1}${center.display_role === "constituent" ? " · 组成中枢" : ""}`;

export const dailyL2StatusLabel = (data: ChartData): string => {
  const overlay = data.overlays?.daily_l2;
  if (!overlay || overlay.status === "unavailable" || !overlay.source) return `日线 L2 来源不可用${overlay?.error ? `：${overlay.error}` : ""}`;
  const cutoff = overlay.source.source_cutoff ? ` · 截止 ${overlay.source.source_cutoff}` : "";
  if (overlay.status === "stale") return `日线 L2 来源更新失败，显示已确认历史${cutoff}${overlay.error ? ` · ${overlay.error}` : ""}`;
  return `${overlay.centers.length ? `日线 L2 参考 ${overlay.centers.length} 个` : "当前范围暂无日线 L2"}${cutoff}`;
};

export const dailyL2Version = (data: ChartData): string => {
  const overlay = data.overlays?.daily_l2;
  const source = overlay?.source;
  return JSON.stringify(source ? [source.timeframe, source.symbol, source.adjustflag, source.run_id, source.definition_version,
    source.calculator_fingerprint, source.market_version, source.structure_version, source.source_cutoff] : [overlay?.status || "missing"]);
};

export const mergeDailyL2Overlays = (fresh: ChartData, old: ChartData): ChartOverlays | undefined => {
  const overlay = fresh.overlays?.daily_l2;
  if (!overlay || dailyL2Version(fresh) !== dailyL2Version(old)) return fresh.overlays;
  const centers = new Map((old.overlays?.daily_l2.centers || []).map(center => [center.id, center]));
  overlay.centers.forEach(center => {
    const previous = centers.get(center.id);
    centers.set(center.id, previous ? { ...center,
      target_start_date: previous.target_start_date < center.target_start_date ? previous.target_start_date : center.target_start_date,
      target_end_date: previous.target_end_date > center.target_end_date ? previous.target_end_date : center.target_end_date,
      clipped_start: previous.clipped_start && center.clipped_start, clipped_end: previous.clipped_end && center.clipped_end,
    } : center);
  });
  return { daily_l2: { ...overlay, centers: [...centers.values()] } };
};

export type ProjectionBounds = { center: DailyL2Projection; startIndex: number; endIndex: number };
export const dailyL2Bounds = (data: ChartData, dates: string[], enabled = true): ProjectionBounds[] => {
  const overlay = data.overlays?.daily_l2;
  if (!enabled || !isReferenceProfile(data) || !overlay?.source || overlay.status === "unavailable") return [];
  if (overlay.source.timeframe !== "d" || overlay.source.symbol !== data.symbol || overlay.source.adjustflag !== data.adjustflag) return [];
  return overlay.centers.flatMap(center => {
    const startIndex = dates.indexOf(center.target_start_date), endIndex = dates.indexOf(center.target_end_date);
    if (center.level !== 2 || center.source_timeframe !== "d" || !Number.isFinite(center.zd) || !Number.isFinite(center.zg)
      || center.zd >= center.zg || startIndex < 0 || endIndex < startIndex) return [];
    return [{ center, startIndex, endIndex }];
  });
};

export type ProjectionRect = {x:number;y:number;width:number;height:number};
/** Rendering and hit testing share these full-category boundaries. Dates are mapped by the backend. */
export const projectionRectangle = (bounds: ProjectionBounds, coord: (index: number, price: number) => number[], categoryWidth: number): ProjectionRect => {
  const first = coord(bounds.startIndex, bounds.center.zg), last = coord(bounds.endIndex, bounds.center.zd);
  const half = Math.abs(categoryWidth) / 2;
  return { x: Math.min(first[0], last[0]) - half, y: Math.min(first[1], last[1]),
    width: Math.abs(last[0] - first[0]) + half * 2, height: Math.abs(last[1] - first[1]) };
};

export const projectionContains = (rect: ProjectionRect, x: number, y: number) =>
  x >= rect.x && x <= rect.x + rect.width && y >= rect.y && y <= rect.y + rect.height;
