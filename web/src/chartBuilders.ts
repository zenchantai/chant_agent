import type { Bar, ChartData, Drawing, DrawingStyle, Node } from "./types";
import { levelStructureColor, periodStructureColor } from "./structureColors";
import { formatCompactNumber, formatIndicatorValue, formatPrice, formatVolume } from "./marketFormatters";

export type SubplotIndicator = "macd" | "volume" | "amount";
export type MainIndicator = {
  mode: "ma" | "boll" | "pen_center" | "none";
  maPeriods: number[];
  bollPeriod: number;
  bollMultiplier: number;
};

export type ChartBuildContext = {
  data: ChartData;
  bars?: Bar[];
  theme?: string;
  visible?: { pens?: boolean; centers?: boolean; movements?: boolean };
  subplotIndicators?: SubplotIndicator[];
  subplotVisible?: boolean[];
  mainIndicator?: MainIndicator;
  drawingItems?: Drawing[];
  movementsStale?: boolean;
  zoomStart?: number;
  zoomEnd?: number;
  chartHeight?: number;
  paneRatios?: number[];
  compactLayout?: boolean;
  formatKlineTooltip?: (bar?: Bar, previousClose?: number) => string;
  formatCenterTooltip?: (center: Node, chartTimeframe: string) => string;
  formatMovementTooltip?: (movement: Node, chartTimeframe?: string) => string;
};

export type ChartBuildIssue = {
  code: string;
  id?: string;
  detail?: string;
};

export type ChartBuildArtifacts = {
  option: Record<string, any> | null;
  issues: ChartBuildIssue[];
  visibleCenters: Node[];
  visibleMovements: Node[];
};

export type ChartPaneLayout = {
  chartHeight: number;
  topInset: number;
  bottomInset: number;
  gap: number;
  heights: number[];
  minHeights: number[];
  tops: number[];
  separators: number[];
  ratios: number[];
};

const PERIOD_LABELS: Record<string, string> = {
  "1": "分时", "5": "5分钟", "15": "15分钟", "30": "30分钟", "60": "60分钟", "120": "120分钟",
  d: "日线", w: "周线", m: "月线", y: "年线",
};

const isObject = (value: unknown): value is Record<string, any> => Boolean(value) && typeof value === "object" && !Array.isArray(value);
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const numberOrNull = (value: unknown): number | null => {
  if (value === null || value === undefined || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
};
const validStamp = (value: unknown): value is string => typeof value === "string" && value.trim().length > 0 && !Number.isNaN(Date.parse(value));
const validRange = (start: unknown, end: unknown) => validStamp(start) && validStamp(end) && String(start) <= String(end);

const uniqueBy = <T,>(items: T[], key: (item: T) => string) => Array.from(new Map(items.map((item) => [key(item), item])).values());

/**
 * Keep the chart boundary deliberately strict. ECharts accepts a surprisingly
 * broad set of values, but a single NaN/undefined coordinate can make the
 * modular build fail while resolving a series model. Normalize bars once at
 * the API boundary so every builder can assume finite OHLC values.
 */
export const normalizeBars = (input: unknown): Bar[] => uniqueBy(
  (Array.isArray(input) ? input : [])
    .filter(isObject)
    .map((item) => {
      const tradeDate = typeof item.trade_date === "string" ? item.trade_date.trim() : "";
      const open = Number(item.open), high = Number(item.high), low = Number(item.low), close = Number(item.close);
      if (!validStamp(tradeDate) || ![open, high, low, close].every(Number.isFinite)) return null;
      const volume = Number(item.volume ?? 0), amount = Number(item.amount ?? 0);
      return {
        ...item,
        trade_date: tradeDate,
        open,
        high,
        low,
        close,
        volume: Number.isFinite(volume) ? volume : 0,
        amount: Number.isFinite(amount) ? amount : 0,
      } as Bar;
    })
    .filter((item): item is Bar => Boolean(item)),
  (item) => item.trade_date,
).sort((a, b) => a.trade_date.localeCompare(b.trade_date));

const normalizeIndicator = (item: unknown): Record<string, any> | null => {
  const tradeDate = isObject(item) && typeof item.trade_date === "string" ? item.trade_date.trim() : "";
  if (!isObject(item) || !validStamp(tradeDate)) return null;
  const result: Record<string, any> = { ...item, trade_date: tradeDate };
  Object.keys(result).forEach((key) => {
    if (key === "trade_date") return;
    if (key === "values" && isObject(result[key])) {
      const values: Record<string, number | null> = {};
      Object.entries(result[key]).forEach(([name, value]) => { values[name] = numberOrNull(value); });
      result.values = values;
      return;
    }
    if (typeof result[key] === "number" || result[key] === null || result[key] === undefined || typeof result[key] === "string") {
      result[key] = numberOrNull(result[key]);
    }
  });
  return result;
};

const normalizeNode = (item: unknown, ordinal: number): Node | null => {
  const startDate = isObject(item) && typeof item.start_date === "string" ? item.start_date.trim() : "";
  const endDate = isObject(item) && typeof item.end_date === "string" ? item.end_date.trim() : "";
  if (!isObject(item) || item.id === undefined || String(item.id).trim() === "" || !validRange(startDate, endDate)) return null;
  const result: Record<string, any> = { ...item, id: String(item.id), start_date: startDate, end_date: endDate, ordinal: Number.isFinite(Number(item.ordinal)) ? Number(item.ordinal) : ordinal };
  ["level", "start_price", "end_price", "low", "high", "zd", "zg", "fixed_zd", "fixed_zg", "dd", "gg", "candidate_extreme_price"].forEach((key) => {
    if (key in result && result[key] !== null && result[key] !== undefined) {
      const value = Number(result[key]);
      if (Number.isFinite(value)) result[key] = value;
      else delete result[key];
    }
  });
  ["source_pen_ids", "pen_ids", "core_pen_ids", "core_unit_ids", "source_unit_ids", "formation_pen_ids", "extension_pen_ids", "peripheral_pen_ids", "departure_pen_ids", "child_ids", "child_movement_ids", "child_center_ids", "center_ids", "evidence"].forEach((key) => {
    if (key in result) result[key] = Array.isArray(result[key]) ? result[key].filter((value: unknown) => value !== undefined && value !== null).map(String) : [];
  });
  ["path_points", "endpoint_points"].forEach((key) => {
    if (!(key in result)) return;
    result[key] = Array.isArray(result[key])
      ? result[key].map((point: unknown) => {
        if (!isObject(point)) return null;
        const tradeDate = typeof point.trade_date === "string" ? point.trade_date.trim() : "";
        const price = Number(point.price);
        return validStamp(tradeDate) && Number.isFinite(price) ? { ...point, trade_date: tradeDate, price } : null;
      }).filter(Boolean)
      : [];
  });
  return result as Node;
};

/** Normalize every API response before it can reach ECharts or the side panels. */
export const normalizeChartData = (input: ChartData | null): ChartData | null => {
  if (!isObject(input)) return null;
  const raw = input as Record<string, any>;
  const bars = normalizeBars(raw.bars);
  // Prefer the canonical field, but fall back to the legacy alias when an
  // older response sends an empty `centers` array rather than omitting it.
  const canonicalCenters = Array.isArray(raw.centers) ? raw.centers : [];
  const legacyCenters = Array.isArray(raw.pen_centers) ? raw.pen_centers : [];
  const centerInput = canonicalCenters.length ? canonicalCenters : legacyCenters;
  const centers = centerInput.map(normalizeNode).filter((item): item is Node => Boolean(item));
  const pens = (Array.isArray(raw.pens) ? raw.pens : []).map(normalizeNode).filter((item): item is Node => Boolean(item));
  const movements = (Array.isArray(raw.movements) ? raw.movements : []).map(normalizeNode).filter((item): item is Node => Boolean(item));
  const relationRows = (Array.isArray(raw.center_relations) ? raw.center_relations : []).filter(isObject).map((item) => ({ ...item }));
  const indicators = raw.indicators && isObject(raw.indicators) ? raw.indicators : {};
  const macd = uniqueBy((Array.isArray(indicators.macd) ? indicators.macd : []).map(normalizeIndicator).filter(Boolean) as Record<string, any>[], (item) => item.trade_date);
  const ma = uniqueBy((Array.isArray(indicators.ma) ? indicators.ma : []).map(normalizeIndicator).filter(Boolean) as Record<string, any>[], (item) => item.trade_date);
  const boll = uniqueBy((Array.isArray(indicators.boll) ? indicators.boll : []).map(normalizeIndicator).filter(Boolean) as Record<string, any>[], (item) => item.trade_date);
  const centerLevels = Array.from(new Set((Array.isArray(raw.center_levels) ? raw.center_levels : centers.map((item) => item.level)).map(Number).filter((level) => Number.isInteger(level) && level >= 1))).sort((a, b) => a - b);
  const movementLevels = Array.from(new Set((Array.isArray(raw.movement_levels) ? raw.movement_levels : movements.map((item) => item.level)).map(Number).filter((level) => Number.isInteger(level) && level >= 1))).sort((a, b) => a - b);
  const requestedActive = Number(raw.active_structure_level);
  const advertisedMax = Number(raw.max_available_center_level);
  const maxAvailable = Math.max(1, Number.isInteger(advertisedMax) && advertisedMax >= 1 ? advertisedMax : (centerLevels[centerLevels.length - 1] || 1));
  // L1 is the product default even when a malformed/partial response only
  // advertises a higher level. The server may still clamp an explicit request
  // to the highest available level.
  const activeLevel = Math.min(maxAvailable, Number.isInteger(requestedActive) && requestedActive >= 1 ? requestedActive : 1);
  const rawDecomposition = raw.decomposition;
  const decomposition = isObject(rawDecomposition)
    ? (rawDecomposition.status || rawDecomposition.algorithm_version || rawDecomposition.issues || rawDecomposition.unassigned_unit_ids
      ? rawDecomposition
      : (isObject(rawDecomposition[String(activeLevel)]) ? rawDecomposition[String(activeLevel)] : {}))
    : {};
  return {
    ...raw,
    bars,
    pens,
    centers,
    pen_centers: centers,
    movements,
    center_relations: relationRows,
    indicators: { macd, ma, boll },
    center_levels: centerLevels,
    movement_levels: movementLevels,
    active_structure_level: activeLevel,
    max_confirmed_center_level: Number.isInteger(Number(raw.max_confirmed_center_level)) ? Math.max(0, Number(raw.max_confirmed_center_level)) : (centerLevels[centerLevels.length - 1] || 0),
    max_available_center_level: maxAvailable,
    decomposition,
    has_more: Boolean(raw.has_more),
  } as ChartData;
};

export const nodeLevel = (node: Node) => Number.isFinite(Number(node.level)) ? Math.max(1, Math.floor(Number(node.level))) : 1;
export const isProvisionalStatus = (status?: string) => status === "provisional" || status === "candidate" || status === "pending";
export const centerZd = (center: Node) => Number(center.zd ?? center.fixed_zd);
export const centerZg = (center: Node) => Number(center.zg ?? center.fixed_zg);

const centerCollection = (data: ChartData) => data.centers?.length ? data.centers : (data.pen_centers || []);
export const centersForStructureLevel = (centers: Node[], activeLevel: number) => centers.filter((center) =>
  (center.role === undefined || center.role === "hierarchy")
  && Number.isInteger(Number(center.level))
  && Number(center.level) === activeLevel,
);

/** Map an arbitrary structural timestamp to the nearest visible category, never to an unrelated right edge. */
export const nearestDate = (dates: string[], stamp: string) => {
  if (!dates.length) return stamp;
  if (!validStamp(stamp)) return dates[0];
  const target = Date.parse(stamp);
  if (target <= Date.parse(dates[0])) return dates[0];
  if (target >= Date.parse(dates[dates.length - 1])) return dates[dates.length - 1];
  let low = 0, high = dates.length - 1;
  while (low <= high) {
    const middle = Math.floor((low + high) / 2);
    const value = Date.parse(dates[middle]);
    if (value === target) return dates[middle];
    if (value < target) low = middle + 1; else high = middle - 1;
  }
  const left = dates[Math.max(0, high)], right = dates[Math.min(dates.length - 1, low)];
  return Math.abs(Date.parse(left) - target) <= Math.abs(Date.parse(right) - target) ? left : right;
};

type Boundary = { startDate: string; endDate: string; startPrice: number; endPrice: number };
const nodeBoundary = (node: Node): Boundary | null => {
  const points = Array.isArray(node.endpoint_points) && node.endpoint_points.length ? node.endpoint_points : node.path_points;
  const first = points?.[0];
  const last = points?.[points.length - 1];
  const startDate = node.start_date || first?.trade_date;
  const endDate = node.end_date || last?.trade_date;
  const startPrice = Number(node.start_price ?? first?.price);
  const endPrice = Number(node.end_price ?? last?.price);
  if (!validRange(startDate, endDate) || !validStamp(startDate) || !validStamp(endDate) || !Number.isFinite(startPrice) || !Number.isFinite(endPrice)) return null;
  return { startDate, endDate, startPrice, endPrice };
};

const interpolate = (leftDate: string, leftPrice: number, rightDate: string, rightPrice: number, target: string) => {
  const left = Date.parse(leftDate), right = Date.parse(rightDate), at = Date.parse(target);
  if (!Number.isFinite(left) || !Number.isFinite(right) || !Number.isFinite(at) || right === left) return leftPrice;
  const ratio = Math.max(0, Math.min(1, (at - left) / (right - left)));
  return leftPrice + (rightPrice - leftPrice) * ratio;
};

const pointAt = (node: Node, target: string) => {
  const boundary = nodeBoundary(node);
  if (!boundary) return null;
  const points = [
    { trade_date: boundary.startDate, price: boundary.startPrice },
    ...(Array.isArray(node.path_points) ? node.path_points : []),
    { trade_date: boundary.endDate, price: boundary.endPrice },
  ].filter((point) => validStamp(point.trade_date) && Number.isFinite(Number(point.price)))
    .map((point) => ({ trade_date: point.trade_date, price: Number(point.price) }))
    .sort((a, b) => a.trade_date.localeCompare(b.trade_date));
  const exact = points.find((point) => point.trade_date === target);
  if (exact) return exact.price;
  for (let index = 1; index < points.length; index += 1) {
    if (points[index - 1].trade_date <= target && target <= points[index].trade_date) {
      return interpolate(points[index - 1].trade_date, points[index - 1].price, points[index].trade_date, points[index].price, target);
    }
  }
  return target < boundary.startDate ? boundary.startPrice : boundary.endPrice;
};

const clippedBoundary = (node: Node, dates: string[]): Boundary | null => {
  const boundary = nodeBoundary(node);
  if (!boundary || !dates.length || boundary.endDate < dates[0] || boundary.startDate > dates[dates.length - 1]) return null;
  const rawStart = boundary.startDate < dates[0] ? dates[0] : boundary.startDate;
  const rawEnd = boundary.endDate > dates[dates.length - 1] ? dates[dates.length - 1] : boundary.endDate;
  const startDate = nearestDate(dates, rawStart), endDate = nearestDate(dates, rawEnd);
  const startPrice = pointAt(node, rawStart), endPrice = pointAt(node, rawEnd);
  if (!startDate || !endDate || startPrice === null || endPrice === null || !Number.isFinite(startPrice) || !Number.isFinite(endPrice)) return null;
  return { startDate, endDate, startPrice, endPrice };
};

export type MovementEndpointMarker = {
  key: string;
  trade_date: string;
  price: number;
  label: string;
  kind: "high" | "low";
  movementId: string;
  level: number;
};

const movementMarkers = (movements: Node[], dates: string[] = []) => {
  const entries = movements.map((movement) => ({ movement, boundary: dates.length ? clippedBoundary(movement, dates) : nodeBoundary(movement) }))
    .filter((item): item is { movement: Node; boundary: Boundary } => Boolean(item.boundary))
    .sort((a, b) => a.boundary.startDate.localeCompare(b.boundary.startDate) || a.boundary.endDate.localeCompare(b.boundary.endDate) || a.movement.id.localeCompare(b.movement.id));
  const map = new Map<string, MovementEndpointMarker & { score: number }>();
  entries.forEach(({ movement, boundary }) => {
    const up = movement.direction === "up" || (movement.direction !== "down" && boundary.endPrice >= boundary.startPrice);
    [{ trade_date: boundary.startDate, price: boundary.startPrice, kind: up ? "low" as const : "high" as const }, { trade_date: boundary.endDate, price: boundary.endPrice, kind: up ? "high" as const : "low" as const }].forEach((point) => {
      const key = `${point.trade_date}|${point.price}`;
      if (!map.has(key)) map.set(key, { ...point, key, movementId: movement.id, level: nodeLevel(movement), label: "", score: up ? 1 : -1 });
    });
  });
  let high = 0, low = 0;
  return Array.from(map.values()).sort((a, b) => a.trade_date.localeCompare(b.trade_date) || a.price - b.price || a.key.localeCompare(b.key)).map((point) => ({
    key: point.key, trade_date: point.trade_date, price: point.price, kind: point.kind,
    label: point.kind === "high" ? `H${++high}` : `L${++low}`, movementId: point.movementId, level: point.level,
  }));
};

export const movementEndpointMarkers = (movements: Node[], dates: string[] = []) => movementMarkers(movements, dates);
/** Public endpoint-label builder used by the chart and by pure configuration tests. */
export const buildEndpointLabels = (movements: Node[], dates: string[] = []) => movementEndpointMarkers(movements, dates);
export const movementLineEndpoints = (movement: Node, dateMapper: (stamp: string) => string = (stamp) => stamp) => {
  const boundary = nodeBoundary(movement);
  if (!boundary) return [];
  return [{ value: [dateMapper(boundary.startDate), boundary.startPrice], movementId: movement.id }, { value: [dateMapper(boundary.endDate), boundary.endPrice], movementId: movement.id }];
};
export const movementVisualStyle = (theme: string, movement: Node) => {
  const boundary = nodeBoundary(movement);
  const up = movement.direction === "up" || (movement.direction !== "down" && Boolean(boundary && boundary.endPrice >= boundary.startPrice));
  return {
    color: isProvisionalStatus(movement.status) ? (theme === "light" ? "#6d43b5" : "#b59cff") : up ? (theme === "light" ? "#c73531" : "#ef6a65") : (theme === "light" ? "#177a50" : "#39b87f"),
    lineType: isProvisionalStatus(movement.status) ? "dashed" as const : "solid" as const,
  };
};

const safeColor = (theme: string, timeframe: string, level: number) => levelStructureColor(theme === "light" ? "light" : "dark", timeframe, level);
const withAlpha = (color: string, alpha: string) => {
  const normalized = color.trim();
  if (/^#[0-9a-f]{6}$/i.test(normalized)) return `${normalized}${alpha}`;
  if (/^#[0-9a-f]{8}$/i.test(normalized)) return `#${normalized.slice(1, 7)}${alpha}`;
  return normalized;
};
const centerColor = (theme: string, timeframe: string, center: Node) => {
  const supplied = (center as any).color;
  if (typeof supplied === "string" && supplied.trim()) return supplied;
  const key = typeof center.color_key === "string" ? center.color_key : "";
  const period = key.match(/^period-(1|5|15|30|60|120|d|w|m)$/)?.[1];
  if (period) return periodStructureColor(theme === "light" ? "light" : "dark", period);
  const levelKey = key.match(/(?:period-[^-]+-level-L|structure-higher-L)(\d+)$/)?.[1];
  if (levelKey) return safeColor(theme, timeframe, Number(levelKey));
  return safeColor(theme, timeframe, nodeLevel(center));
};
const hasMissingReference = (center: Node, data: ChartData) => {
  const penIds = new Set((data.pens || []).map((item) => item.id));
  const centerIds = new Set(centerCollection(data).map((item) => item.id));
  const movementIds = new Set((data.movements || []).map((item) => item.id));
  const centerLevel = nodeLevel(center);
  const advertisedCenterLevels = (data.center_levels || []).map(Number).filter(Number.isInteger);
  const advertisedMovementLevels = (data.movement_levels || []).map(Number).filter(Number.isInteger);
  const penRefs = [
    ...(center.source_pen_ids || []), ...(center.pen_ids || []), ...(center.core_pen_ids || []),
    ...(center.formation_pen_ids || []), ...(center.extension_pen_ids || []),
    ...(center.peripheral_pen_ids || []), ...(center.departure_pen_ids || []),
    ...(centerLevel === 1 ? (center.core_unit_ids || []) : []),
    ...(center.entry_pen_id ? [center.entry_pen_id] : []),
  ];
  if (penRefs.length && penRefs.some((id) => !penIds.has(id))) return "center_pen_reference_missing";
  const childCenterRefs = [...(center.child_center_ids || []), ...(center.child_ids || [])].filter(Boolean);
  // The chart API intentionally returns only the selected level's centers;
  // child IDs can therefore point to lower-level rows omitted from this page.
  // Validate them when no lower-level catalog is advertised, which catches
  // malformed standalone payloads without rejecting valid L2/L3 responses.
  const hasLowerLevelCatalog = advertisedCenterLevels.some((level) => level < centerLevel);
  if (childCenterRefs.length && centerIds.size && !hasLowerLevelCatalog && childCenterRefs.some((id) => !centerIds.has(id))) return "center_child_reference_missing";

  // Higher-level centers normally reference lower-level movements. Those rows
  // are intentionally omitted from a selected-level chart page, so only make
  // this check strict when no lower-level movement catalog is advertised.
  const movementRefs = [
    ...(centerLevel > 1 ? (center.child_movement_ids || []) : []),
    ...(centerLevel > 1 ? (center.source_unit_ids || []) : []),
  ];
  const hasLowerMovementCatalog = advertisedMovementLevels.some((level) => level < centerLevel);
  if (movementRefs.length && !hasLowerMovementCatalog && movementRefs.some((id) => !movementIds.has(id))) return "center_movement_reference_missing";
  return null;
};

export const buildCenterAreas = (context: ChartBuildContext, dates: string[], issues: ChartBuildIssue[] = []) => {
  const data = context.data;
  if (!dates.length) {
    issues.push({ code: "center_dates_empty" });
    return { areas: [] as any[], visibleCenters: [] as Node[] };
  }
  const activeLevel = Math.max(1, Number(data.active_structure_level || 1));
  const all = centerCollection(data);
  const visible = centersForStructureLevel(all, activeLevel).filter((center) => {
    const zd = centerZd(center), zg = centerZg(center);
    if (!Number.isFinite(zd) || !Number.isFinite(zg) || zd >= zg || !validRange(center.start_date, center.end_date)) {
      issues.push({ code: "center_invalid_range", id: center.id }); return false;
    }
    if (hasMissingReference(center, data)) { issues.push({ code: "center_reference_missing", id: center.id }); return false; }
    if (center.end_date < dates[0] || center.start_date > dates[dates.length - 1]) return false;
    return true;
  });
  const areas = visible.map((center) => {
    const color = centerColor(context.theme || "dark", data.timeframe, center);
    if (!(center as any).color && !center.color_key) issues.push({ code: "center_color_metadata_missing", id: center.id });
    const fill = withAlpha(color, context.theme === "light" ? "1A" : "24");
    return [
      { xAxis: nearestDate(dates, center.start_date), yAxis: centerZd(center), centerId: center.id, itemStyle: { color: fill, borderColor: color, borderWidth: 2.2, borderType: isProvisionalStatus(center.status) ? "dashed" : "solid" }, label: isProvisionalStatus(center.status) ? { show: true, formatter: `${center.upgrade_kind || "候选"}${center.progress ? ` ${center.progress}` : ""}`, color } : { show: false } },
      { xAxis: nearestDate(dates, center.end_date), yAxis: centerZg(center) },
    ];
  });
  return { areas, visibleCenters: visible };
};

const subplotSlots = (visible: boolean[]) => visible.map((shown, index) => shown ? index : -1).filter((index) => index >= 0);

export const defaultPaneRatios = (subplotCount: number) => {
  const count = Math.max(0, Math.min(4, Math.floor(subplotCount)));
  if (!count) return [1];
  return [0.6, ...Array.from({ length: count }, () => 0.4 / count)];
};

export const normalizePaneRatios = (input: unknown, subplotCount: number) => {
  const fallback = defaultPaneRatios(subplotCount);
  if (!Array.isArray(input) || input.length !== fallback.length) return fallback;
  const values = input.map(Number);
  if (values.some((value) => !Number.isFinite(value) || value <= 0)) return fallback;
  const total = values.reduce((sum, value) => sum + value, 0);
  return total > 0 ? values.map((value) => value / total) : fallback;
};

export const paneLayoutStorageKey = (symbol: string, timeframe: string, subplotCount: number) =>
  `chan-pane-layout-${symbol}-${timeframe}-${Math.max(0, Math.min(4, Math.floor(subplotCount)))}`;

export const parseStoredPaneRatios = (raw: string | null, subplotCount: number) => {
  if (!raw) return defaultPaneRatios(subplotCount);
  try {
    const parsed = JSON.parse(raw);
    if (parsed?.version !== 1) return defaultPaneRatios(subplotCount);
    return normalizePaneRatios(parsed.ratios, subplotCount);
  } catch {
    return defaultPaneRatios(subplotCount);
  }
};

export const serializePaneRatios = (ratios: number[], subplotCount: number) =>
  JSON.stringify({ version: 1, ratios: normalizePaneRatios(ratios, subplotCount) });

const allocatePaneHeights = (ratios: number[], availableHeight: number, minHeights: number[]) => {
  const heights = Array(ratios.length).fill(0);
  const remaining = new Set(ratios.map((_, index) => index));
  let remainingHeight = availableHeight;
  let remainingWeight = ratios.reduce((sum, value) => sum + value, 0);
  while (remaining.size) {
    const constrained = [...remaining].find((index) => remainingHeight * ratios[index] / remainingWeight < minHeights[index]);
    if (constrained === undefined) break;
    heights[constrained] = minHeights[constrained];
    remainingHeight -= minHeights[constrained];
    remainingWeight -= ratios[constrained];
    remaining.delete(constrained);
  }
  remaining.forEach((index) => { heights[index] = remainingHeight * ratios[index] / remainingWeight; });
  return heights;
};

export const buildChartPaneLayout = ({
  requestedHeight,
  subplotCount,
  ratios,
  intraday,
  mainIndicatorVisible = true,
  compact = false,
}: {
  requestedHeight: number;
  subplotCount: number;
  ratios?: number[];
  intraday: boolean;
  mainIndicatorVisible?: boolean;
  compact?: boolean;
}): ChartPaneLayout => {
  const count = Math.max(0, Math.min(4, Math.floor(subplotCount)));
  const normalized = normalizePaneRatios(ratios, count);
  const topInset = intraday ? 48 : mainIndicatorVisible ? 108 : 84;
  const bottomInset = intraday ? 28 : 34;
  const gap = 8;
  const minHeights = [compact ? 200 : 240, ...Array.from({ length: count }, () => compact ? 72 : 80)];
  const minimumHeight = topInset + bottomInset + count * gap + minHeights.reduce((sum, value) => sum + value, 0);
  const chartHeight = Math.max(Number.isFinite(requestedHeight) ? requestedHeight : 560, minimumHeight);
  const availableHeight = chartHeight - topInset - bottomInset - count * gap;
  const heights = allocatePaneHeights(normalized, availableHeight, minHeights);
  const tops = heights.map((_, index) => index === 0 ? topInset : 0);
  for (let index = 1; index < tops.length; index += 1) tops[index] = tops[index - 1] + heights[index - 1] + gap;
  return {
    chartHeight,
    topInset,
    bottomInset,
    gap,
    heights,
    minHeights,
    tops,
    separators: heights.slice(0, -1).map((height, index) => tops[index] + height + gap / 2),
    ratios: heights.map((height) => height / availableHeight),
  };
};

export const resizeAdjacentPanes = (heights: number[], separatorIndex: number, delta: number, minHeights: number[]) => {
  if (separatorIndex < 0 || separatorIndex >= heights.length - 1 || heights.length !== minHeights.length) {
    return normalizePaneRatios(heights, Math.max(0, heights.length - 1));
  }
  const next = [...heights];
  const upper = heights[separatorIndex], lower = heights[separatorIndex + 1];
  const minimumDelta = minHeights[separatorIndex] - upper;
  const maximumDelta = lower - minHeights[separatorIndex + 1];
  const applied = Math.max(minimumDelta, Math.min(maximumDelta, delta));
  next[separatorIndex] = upper + applied;
  next[separatorIndex + 1] = lower - applied;
  const total = next.reduce((sum, value) => sum + value, 0);
  return next.map((value) => value / total);
};

export const buildAxes = (context: ChartBuildContext) => {
  const data = context.data;
  const bars = context.bars || data.bars || [];
  const dates = bars.map((bar) => bar.trade_date);
  const dark = context.theme !== "light";
  const intraday = data.timeframe === "1";
  const indicators = context.subplotIndicators || ["volume", "macd", "macd", "macd"];
  const slots = subplotSlots((context.subplotVisible || (intraday ? [true, true] : [])).slice(0, 4));
  const count = slots.length;
  const layout = buildChartPaneLayout({ requestedHeight: context.chartHeight || 560, subplotCount: count, ratios: context.paneRatios, intraday, mainIndicatorVisible: context.mainIndicator?.mode !== "none", compact: context.compactLayout });
  const grid: Record<string, any>[] = [{ left: 64, right: 18, top: layout.tops[0], height: layout.heights[0] }];
  const clockLabel = (value: string) => value.endsWith("09:30:00") || value.endsWith("11:30:00") || value.endsWith("15:00:00") ? value.slice(11, 16) : "";
  const xAxis: Record<string, any>[] = [{ type: "category", data: dates, boundaryGap: !intraday, axisLine: { lineStyle: { color: dark ? "#44505b" : "#bdc7ce" } }, axisLabel: { show: count === 0, color: dark ? "#8998a5" : "#60707b", hideOverlap: true, ...(intraday ? { interval: 0, formatter: clockLabel } : {}) } }];
  const yAxis: Record<string, any>[] = [{ type: "value", scale: true, splitLine: { lineStyle: { color: dark ? "#28343e" : "#e7ecef" } }, axisLabel: { color: dark ? "#8998a5" : "#60707b", formatter: (value: number) => formatPrice(value) } }];
  const subplotAxisIndices: Record<number, number> = {};
  slots.forEach((slot, position) => {
    const axis = position + 1;
    grid.push({ left: 64, right: 18, top: layout.tops[axis], height: layout.heights[axis] });
    xAxis.push({ type: "category", gridIndex: axis, data: dates, boundaryGap: !intraday, axisLabel: { show: position === count - 1, color: dark ? "#8998a5" : "#60707b", hideOverlap: true, ...(intraday ? { interval: 0, formatter: clockLabel } : {}) }, axisLine: { lineStyle: { color: dark ? "#44505b" : "#bdc7ce" } } });
    yAxis.push({ type: "value", gridIndex: axis, scale: true, splitNumber: 2, splitLine: { show: false }, axisLabel: { color: dark ? "#8998a5" : "#60707b", formatter: (value: number) => indicators[slot] === "volume" ? formatVolume(value) : indicators[slot] === "amount" ? formatCompactNumber(value) : formatIndicatorValue(value) } });
    subplotAxisIndices[slot] = axis;
  });
  return { grid, xAxis, yAxis, axisCount: { x: xAxis.length, y: yAxis.length }, subplotAxisIndices, paneLayout: layout };
};

export const buildPriceSeries = (context: ChartBuildContext, centerAreas: any[] = []) => {
  const data = context.data;
  const bars = context.bars || data.bars || [];
  if (!bars.length) return [];
  const dark = context.theme !== "light";
  if (data.timeframe === "1") {
    let volume = 0, amount = 0;
    const average = bars.map((bar) => { volume += Number(bar.volume || 0); amount += ((bar.high + bar.low + bar.close) / 3) * Number(bar.volume || 0); return volume > 0 ? amount / volume : bar.close; });
    const previous = Number(data.previous_close ?? bars[0].open);
    const markLine = Number.isFinite(previous) ? { silent: true, symbol: "none", lineStyle: { type: "dashed", width: 1, color: dark ? "#71808b" : "#9aa7ad" }, label: { show: false }, data: [{ yAxis: previous }] } : undefined;
    const line: Record<string, any> = { id: "intraday-price", name: "分时", type: "line", data: bars.map((bar) => bar.close), showSymbol: false, smooth: false, lineStyle: { width: 2, color: "#42b8d4" }, itemStyle: { color: "#42b8d4" }, areaStyle: { color: "#42b8d4", opacity: 0.08 } };
    if (markLine) line.markLine = markLine;
    return [line, { id: "intraday-average", name: "均价", type: "line", data: average, showSymbol: false, lineStyle: { width: 1.2, color: "#e5ad35" }, itemStyle: { color: "#e5ad35" } }];
  }
  const candle: Record<string, any> = { id: "kline", name: "K线", type: "candlestick", data: bars.map((bar) => [bar.open, bar.close, bar.low, bar.high]), itemStyle: { color: "transparent", color0: "#26a269", borderColor: "#ef5350", borderColor0: "#26a269" } };
  if (centerAreas.length && context.visible?.centers !== false) {
    candle.markArea = { silent: false, label: { show: false }, data: centerAreas, tooltip: { trigger: "item", formatter: (params: any) => {
      const item = Array.isArray(params?.data) ? params.data[0] : params?.data;
      const center = centerCollection(data).find((candidate) => candidate.id === item?.centerId);
      return center && context.formatCenterTooltip ? context.formatCenterTooltip(center, data.timeframe) : "";
    } } };
  }
  return [candle];
};

export const buildIndicatorSeries = (context: ChartBuildContext, axes = buildAxes(context)) => {
  const data = context.data;
  const bars = context.bars || data.bars || [];
  if (!bars.length) return [];
  const dates = bars.map((bar) => bar.trade_date);
  const main = context.mainIndicator || { mode: "pen_center" as const, maPeriods: [5, 10, 20, 60], bollPeriod: 20, bollMultiplier: 2 };
  const maByDate = new Map((data.indicators?.ma || []).map((item: any) => [item.trade_date, item]));
  const bollByDate = new Map((data.indicators?.boll || []).map((item: any) => [item.trade_date, item]));
  const macdByDate = new Map((data.indicators?.macd || []).map((item: any) => [item.trade_date, item]));
  const maColors = ["#688ef2", "#f28b4b", "#e457b8", "#42b8d4", "#9ccc65", "#b59cff"];
  const series: Record<string, any>[] = [];
  if (main.mode === "ma") main.maPeriods.filter((period) => Number.isInteger(period) && period > 0).forEach((period, index) => {
    const color = maColors[index % maColors.length];
    series.push({ id: `ma${period}`, name: `MA${period}`, type: "line", data: dates.map((stamp) => maByDate.get(stamp)?.values?.[String(period)] ?? null), showSymbol: false, connectNulls: false, silent: true, lineStyle: { width: 1.25, color }, itemStyle: { color }, z: 3 });
  });
  if (main.mode === "boll") ([['middle', 'BOLL中轨', '#e5ad35'], ['upper', 'BOLL上轨', '#688ef2'], ['lower', 'BOLL下轨', '#e457b8']] as const).forEach(([key, name, color]) => series.push({ id: `boll-${key}`, name, type: "line", data: dates.map((stamp) => bollByDate.get(stamp)?.[key] ?? null), showSymbol: false, silent: true, lineStyle: { width: 1.25, color }, itemStyle: { color }, z: 3 }));
  const indicators = context.subplotIndicators || ["volume", "macd", "macd", "macd"];
  const visible = (context.subplotVisible || []).slice(0, 4);
  visible.forEach((shown, slot) => {
    if (!shown) return;
    const axis = axes.subplotAxisIndices[slot];
    if (!Number.isInteger(axis)) return;
    const indicator = indicators[slot] || "macd";
    if (indicator === "volume") {
      series.push({ id: `volume-${slot}`, name: "成交量", type: "bar", xAxisIndex: axis, yAxisIndex: axis, data: bars.map((bar) => ({ value: bar.volume, itemStyle: bar.close >= bar.open ? { color: "transparent", borderColor: "#ef5350", borderWidth: 1 } : { color: "#26a269", borderColor: "#26a269", borderWidth: 1 } })), tooltip: { valueFormatter: (value: number) => formatVolume(value) } });
    } else if (indicator === "amount") {
      const amount = bars.map((bar) => Number.isFinite(bar.amount) ? bar.amount : null);
      const amountMa5 = amount.map((_, index) => { const window = amount.slice(Math.max(0, index - 4), index + 1).filter((value): value is number => value !== null); return window.length ? window.reduce((sum, value) => sum + value, 0) / window.length : null; });
      series.push({ id: `amount-${slot}`, name: "成交额", type: "bar", xAxisIndex: axis, yAxisIndex: axis, data: bars.map((bar) => ({ value: bar.amount, itemStyle: { color: bar.close >= bar.open ? "#ef5350" : "#26a269" } })), tooltip: { valueFormatter: (value: number) => formatCompactNumber(value) } });
      series.push({ id: `amount-ma5-${slot}`, name: "成交额MA5", type: "line", xAxisIndex: axis, yAxisIndex: axis, data: amountMa5, showSymbol: false, lineStyle: { width: 1.3, color: "#f59e42" }, itemStyle: { color: "#f59e42" }, tooltip: { valueFormatter: (value: number) => formatCompactNumber(value) } });
    } else {
      const values = dates.map((stamp) => macdByDate.get(stamp));
      series.push({ id: `macd-histogram-${slot}`, name: "MACD", type: "bar", xAxisIndex: axis, yAxisIndex: axis, data: values.map((item: any) => ({ value: numberOrNull(item?.histogram), itemStyle: { color: Number(item?.histogram || 0) >= 0 ? "#ef5350" : "#26a269" } })), tooltip: { valueFormatter: (value: number) => formatIndicatorValue(value) } });
      series.push({ id: `macd-dif-${slot}`, name: "DIF", type: "line", xAxisIndex: axis, yAxisIndex: axis, data: values.map((item: any) => numberOrNull(item?.dif)), showSymbol: false, lineStyle: { width: 1.3, color: "#42b8d4" }, itemStyle: { color: "#42b8d4" }, tooltip: { valueFormatter: (value: number) => formatIndicatorValue(value) } });
      series.push({ id: `macd-dea-${slot}`, name: "DEA", type: "line", xAxisIndex: axis, yAxisIndex: axis, data: values.map((item: any) => numberOrNull(item?.dea)), showSymbol: false, lineStyle: { width: 1.3, color: "#f59e42" }, itemStyle: { color: "#f59e42" }, tooltip: { valueFormatter: (value: number) => formatIndicatorValue(value) } });
      series.push({ id: `macd-zero-${slot}`, name: "零轴", type: "line", xAxisIndex: axis, yAxisIndex: axis, data: dates.map(() => 0), showSymbol: false, silent: true, tooltip: { show: false }, lineStyle: { width: 1, color: context.theme === "light" ? "#aeb8bf" : "#56636d" } });
    }
  });
  return series;
};

export const buildPenSeries = (context: ChartBuildContext, dates: string[], issues: ChartBuildIssue[] = []) => {
  if (context.data.timeframe === "1" || context.visible?.pens === false) return [];
  const color = periodStructureColor(context.theme === "light" ? "light" : "dark", context.data.timeframe);
  return (context.data.pens || []).slice().sort((a, b) => a.start_date.localeCompare(b.start_date)).filter((pen) => pen.status === "confirmed").map((pen) => {
    const boundary = clippedBoundary(pen, dates);
    if (!boundary) return null;
    if (!Number.isFinite(boundary.startPrice) || !Number.isFinite(boundary.endPrice)) { issues.push({ code: "pen_invalid_boundary", id: pen.id }); return null; }
    return { id: pen.id, name: `笔 ${(Number(pen.ordinal) || 0) + 1}`, type: "line", data: [[boundary.startDate, boundary.startPrice], [boundary.endDate, boundary.endPrice]], showSymbol: true, symbolSize: 5, lineStyle: { width: 1.8, type: "solid", color }, itemStyle: { color }, z: 5 };
  }).filter(Boolean) as Record<string, any>[];
};

export const buildMovementSeries = (context: ChartBuildContext, dates: string[], issues: ChartBuildIssue[] = []) => {
  if (context.data.timeframe === "1" || context.visible?.movements === false || context.movementsStale) return { series: [] as Record<string, any>[], visibleMovements: [] as Node[], endpointMarkers: [] as MovementEndpointMarker[] };
  const active = Math.max(1, Number(context.data.active_structure_level || 1));
  const visible = (context.data.movements || []).filter((movement) => Number.isInteger(Number(movement.level)) && Number(movement.level) === active && movement.role === "same_level_decomposition").map((movement) => ({ movement, boundary: clippedBoundary(movement, dates) })).filter((item): item is { movement: Node; boundary: Boundary } => {
    if (!item.boundary) return false;
    if (!Number.isFinite(item.boundary.startPrice) || !Number.isFinite(item.boundary.endPrice)) { issues.push({ code: "movement_invalid_boundary", id: item.movement.id }); return false; }
    return true;
  });
  const lines = visible.map(({ movement, boundary }) => {
    const style = movementVisualStyle(context.theme === "light" ? "light" : "dark", movement);
    return { id: movement.id, name: `${movement.classification === "trend" ? "趋势" : "盘整"} ${(Number(movement.ordinal) || 0) + 1}`, type: "line", data: [{ value: [boundary.startDate, boundary.startPrice], movementId: movement.id }, { value: [boundary.endDate, boundary.endPrice], movementId: movement.id }], showSymbol: false, lineStyle: { width: 4.4, type: style.lineType, color: style.color, opacity: 0.72 }, itemStyle: { color: style.color }, tooltip: { trigger: "item", formatter: () => context.formatMovementTooltip?.(movement, context.data.timeframe) || "" }, z: 4 };
  });
  const rendered = visible.map(({ movement, boundary }) => ({ ...movement, start_date: boundary.startDate, end_date: boundary.endDate, start_price: boundary.startPrice, end_price: boundary.endPrice }));
  const markers = movementEndpointMarkers(rendered, dates);
  const endpointSeries = markers.length ? [{ id: "movement-boundaries", name: "走势端点", type: "scatter", data: markers.map((marker) => ({ value: [marker.trade_date, marker.price], movementId: marker.movementId, name: marker.label, label: { show: true, formatter: marker.label, position: marker.kind === "high" ? "top" : "bottom", color: safeColor(context.theme || "dark", context.data.timeframe, marker.level), fontWeight: 600 }, itemStyle: { color: context.theme === "light" ? "#25313a" : "#f3f5f7", borderColor: safeColor(context.theme || "dark", context.data.timeframe, marker.level), borderWidth: 2 } })), symbolSize: 8, tooltip: { trigger: "item", formatter: (params: any) => { const movement = visible.find((item) => item.movement.id === params?.data?.movementId)?.movement; return movement ? context.formatMovementTooltip?.(movement, context.data.timeframe) || "" : ""; } }, z: 7 }] : [];
  const arrowSeries = rendered.length ? [{ id: "movement-arrow-heads", name: "走势箭头", type: "scatter", data: rendered.map((movement) => { const style = movementVisualStyle(context.theme === "light" ? "light" : "dark", movement); const dx = Date.parse(movement.end_date) - Date.parse(movement.start_date) || 1; const dy = -(Number(movement.end_price) - Number(movement.start_price)); return { value: [movement.end_date, movement.end_price], movementId: movement.id, symbol: "path://M0,0 L12,6 L0,12 Z", symbolRotate: Math.atan2(dy, dx) * 180 / Math.PI, symbolSize: 11, itemStyle: { color: style.color, opacity: 0.9 } }; }), tooltip: { trigger: "item", formatter: (params: any) => { const movement = visible.find((item) => item.movement.id === params?.data?.movementId)?.movement; return movement ? context.formatMovementTooltip?.(movement, context.data.timeframe) || "" : ""; } }, z: 6 }] : [];
  return { series: [...lines, ...arrowSeries, ...endpointSeries], visibleMovements: rendered, endpointMarkers: markers };
};

const styleDefaults = (drawing: Drawing, theme: string): DrawingStyle => {
  const color = periodStructureColor(theme === "light" ? "light" : "dark", drawing.timeframe);
  return { color, width: 1.5, line_type: "solid", opacity: 0.9, ...(drawing.object_type === "rectangle" ? { fill_color: color, fill_opacity: theme === "light" ? 0.1 : 0.14 } : {}) };
};

export const buildDrawingSeries = (context: ChartBuildContext, dates: string[], issues: ChartBuildIssue[] = []) => {
  if (context.data.timeframe === "1") return [];
  return (context.drawingItems || context.data.drawings || []).filter((drawing) => drawing?.visible && drawing.start_anchor && drawing.end_anchor).map((drawing) => {
    const defaults = styleDefaults(drawing, context.theme || "dark");
    const style = { ...defaults, ...(drawing.style || {}) };
    const startPrice = Number(drawing.start_anchor.price), endPrice = Number(drawing.end_anchor.price);
    if (!Number.isFinite(startPrice) || !Number.isFinite(endPrice)) { issues.push({ code: "drawing_invalid_anchor", id: String(drawing.id) }); return null; }
    const start = [nearestDate(dates, drawing.start_anchor.trade_date), startPrice];
    const end = [nearestDate(dates, drawing.end_anchor.trade_date), endPrice];
    const lineStyle = { color: style.color || defaults.color, width: Number.isFinite(Number(style.width)) ? Number(style.width) : 1.5, opacity: Number.isFinite(Number(style.opacity)) ? Number(style.opacity) : 0.9, type: style.line_type || "solid" };
    if (drawing.object_type === "rectangle") return { id: `drawing-${drawing.id}`, name: drawing.label || "矩形", type: "line", data: [start, [end[0], start[1]], end, [start[0], end[1]], start], showSymbol: false, lineStyle, areaStyle: { color: style.fill_color || defaults.color, opacity: Number.isFinite(Number(style.fill_opacity)) ? Number(style.fill_opacity) : 0.14 }, z: 8 };
    if (drawing.object_type === "line") {
      const i1 = Math.max(0, dates.indexOf(start[0] as string)), i2 = Math.max(0, dates.indexOf(end[0] as string));
      const slope = i2 !== i1 ? (endPrice - startPrice) / (i2 - i1) : 0;
      return { id: `drawing-${drawing.id}`, name: drawing.label || "直线", type: "line", data: [[dates[0], startPrice - i1 * slope], [dates[dates.length - 1], startPrice + (dates.length - 1 - i1) * slope]], showSymbol: false, lineStyle, z: 8 };
    }
    return { id: `drawing-${drawing.id}`, name: drawing.label || "线段", type: "line", data: [start, end], showSymbol: false, lineStyle, z: 8 };
  }).filter(Boolean) as Record<string, any>[];
};

const ALLOWED_SERIES_TYPES = new Set(["line", "bar", "candlestick", "scatter"]);
/** Recursively reject undefined values, including holes in sparse arrays. */
const valueIsValid = (value: any, ancestors = new WeakSet<object>()): boolean => {
  if (value === null || value === undefined) return value === null;
  if (typeof value === "number") return Number.isFinite(value);
  if (typeof value === "string" || typeof value === "boolean" || typeof value === "function") return true;
  if (typeof value !== "object") return false;
  if (ancestors.has(value)) return false;
  ancestors.add(value);
  let valid = false;
  if (Array.isArray(value)) {
    valid = true;
    for (let index = 0; index < value.length; index += 1) {
      if (!Object.prototype.hasOwnProperty.call(value, index) || !valueIsValid(value[index], ancestors)) {
        valid = false;
        break;
      }
    }
    ancestors.delete(value);
    return valid;
  }
  valid = Object.entries(value).every(([key, item]) => key !== "__proto__" && item !== undefined && valueIsValid(item, ancestors));
  ancestors.delete(value);
  return valid;
};

const validCategoryCoordinate = (value: unknown) =>
  (typeof value === "number" && Number.isFinite(value)) || (typeof value === "string" && validStamp(value));
const validCategoryAxisValue = (value: unknown) =>
  (typeof value === "number" && Number.isFinite(value)) || (typeof value === "string" && value.trim().length > 0);

const unwrappedSeriesValue = (item: unknown) =>
  isObject(item) && Object.prototype.hasOwnProperty.call(item, "value") ? item.value : item;

/** Validate the data shape ECharts' cartesian series actually consumes. */
const validSeriesDataItem = (type: string, item: unknown): boolean => {
  const value = unwrappedSeriesValue(item);
  if (value === null) return type === "line" || type === "bar";
  if (type === "candlestick") {
    return Array.isArray(value) && value.length === 4 && value.every((part) => typeof part === "number" && Number.isFinite(part));
  }
  if (type === "scatter") {
    return Array.isArray(value) && value.length === 2 && validCategoryCoordinate(value[0]) && typeof value[1] === "number" && Number.isFinite(value[1]);
  }
  if (type === "line") {
    if (typeof value === "number") return Number.isFinite(value);
    return Array.isArray(value) && value.length === 2 && validCategoryCoordinate(value[0]) && typeof value[1] === "number" && Number.isFinite(value[1]);
  }
  if (type === "bar") {
    return typeof value === "number" && Number.isFinite(value);
  }
  return false;
};

export const validateSeries = (series: unknown[], axisCount: { x: number; y: number }) => {
  const issues: ChartBuildIssue[] = [];
  const xCount = Number(axisCount?.x), yCount = Number(axisCount?.y);
  const valid = (Array.isArray(series) ? series : []).filter((item: any) => {
    if (!isObject(item) || !ALLOWED_SERIES_TYPES.has(item.type) || !Array.isArray(item.data)) { issues.push({ code: "series_invalid_shape", id: item?.id }); return false; }
    const x = item.xAxisIndex === undefined ? 0 : item.xAxisIndex, y = item.yAxisIndex === undefined ? 0 : item.yAxisIndex;
    if (!Number.isInteger(x) || x < 0 || x >= xCount || !Number.isInteger(y) || y < 0 || y >= yCount) { issues.push({ code: "series_axis_out_of_range", id: item.id }); return false; }
    if (!valueIsValid(item.data)) { issues.push({ code: "series_data_invalid", id: item.id }); return false; }
    for (let index = 0; index < item.data.length; index += 1) {
      if (!validSeriesDataItem(item.type, item.data[index])) {
        issues.push({ code: "series_coordinate_invalid", id: item.id, detail: `data[${index}]` });
        return false;
      }
    }
    if (!valueIsValid(item)) { issues.push({ code: "series_contains_undefined", id: item.id }); return false; }
    return true;
  });
  return { valid, issues };
};

export const validateAxes = (axes: { grid?: unknown[]; xAxis?: unknown[]; yAxis?: unknown[] }) => {
  const issues: ChartBuildIssue[] = [];
  const grid = Array.isArray(axes.grid) ? axes.grid : [];
  const xAxis = Array.isArray(axes.xAxis) ? axes.xAxis : [];
  const yAxis = Array.isArray(axes.yAxis) ? axes.yAxis : [];
  if (!xAxis.length || !yAxis.length || !grid.length) issues.push({ code: "axis_collection_invalid" });
  grid.forEach((item: any) => {
    if (!isObject(item) || !valueIsValid(item)) issues.push({ code: "grid_invalid_shape" });
  });
  [...xAxis, ...yAxis].forEach((axis: any) => {
    const validType = isObject(axis) && (axis.type === "category" || axis.type === "value");
    const hasCategoryData = !isObject(axis) || axis.type !== "category" || Array.isArray(axis.data);
    if (!validType || !hasCategoryData) issues.push({ code: "axis_invalid_shape" });
    if (isObject(axis)) {
      const gridIndex = axis.gridIndex === undefined ? 0 : axis.gridIndex;
      if (!Number.isInteger(gridIndex) || gridIndex < 0 || gridIndex >= grid.length) issues.push({ code: "axis_grid_out_of_range" });
      if (Array.isArray(axis.data) && (!valueIsValid(axis.data) || (axis.type === "category" && axis.data.some((value: unknown) => !validCategoryAxisValue(value))))) issues.push({ code: "axis_data_invalid" });
      if (!valueIsValid(axis)) issues.push({ code: "axis_contains_undefined" });
    }
  });
  return { valid: issues.length === 0, issues };
};

export const buildChartArtifacts = (context: ChartBuildContext): ChartBuildArtifacts => {
  const data = normalizeChartData(context.data);
  if (!data) return { option: null, issues: [{ code: "invalid_chart_data" }], visibleCenters: [], visibleMovements: [] };
  const bars = normalizeBars(context.bars === undefined ? data.bars : context.bars);
  if (!bars.length) return { option: null, issues: [{ code: "empty_bars" }], visibleCenters: [], visibleMovements: [] };
  context = { ...context, data, bars };
  const dates = bars.map((bar) => bar.trade_date);
  const issues: ChartBuildIssue[] = [];
  const axes = buildAxes({ ...context, bars });
  const { axisCount, subplotAxisIndices, paneLayout: _paneLayout, ...axesOption } = axes;
  const centers = buildCenterAreas({ ...context, bars }, dates, issues);
  const movement = buildMovementSeries({ ...context, bars }, dates, issues);
  const series = [
    ...buildPriceSeries({ ...context, bars }, centers.areas),
    ...buildIndicatorSeries({ ...context, bars }, axes),
    ...buildPenSeries({ ...context, bars }, dates, issues),
    ...movement.series,
    ...buildDrawingSeries({ ...context, bars }, dates, issues),
  ];
  const checked = validateSeries(series, axes.axisCount);
  issues.push(...checked.issues);
  const checkedAxes = validateAxes(axesOption);
  issues.push(...checkedAxes.issues);
  // Never hand ECharts a partially invalid axis graph. A malformed server
  // response should produce the component's recoverable error state instead
  // of triggering an internal "reading type" exception.
  if (!checkedAxes.valid || !checked.valid.length) {
    issues.push({ code: "chart_option_invalid" });
    return { option: null, issues, visibleCenters: centers.visibleCenters, visibleMovements: movement.visibleMovements };
  }
  const dark = context.theme !== "light";
  const option: Record<string, any> = {
    animation: false,
    backgroundColor: "transparent",
    ...axesOption,
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    tooltip: { trigger: "axis", confine: true, backgroundColor: dark ? "#18232c" : "#fff", borderColor: dark ? "#44505b" : "#ccd5db", textStyle: { color: dark ? "#dce4e9" : "#22313a" }, formatter: (params: any) => {
      const items = Array.isArray(params) ? params : [params];
      const candle = items.find((item: any) => (item?.seriesName === "K线" || item?.seriesName === "分时") && item?.axisIndex === 0);
      if (candle && context.formatKlineTooltip) return context.formatKlineTooltip(bars[candle.dataIndex], data.timeframe === "1" ? data.previous_close : bars[candle.dataIndex - 1]?.close);
      const first = items.find((item: any) => item?.seriesName !== "零轴");
      if (!first) return "";
      return items.filter((item: any) => item?.seriesName !== "零轴").map((item: any) => {
        const value = Array.isArray(item?.value) ? item.value[item.value.length - 1] : item?.value;
        const formatted = item.seriesName === "成交量" ? formatVolume(Number(value)) : item.seriesName === "成交额" || item.seriesName === "成交额MA5" ? formatCompactNumber(Number(value)) : formatIndicatorValue(Number(value));
        return `${item.marker || ""}${item.seriesName}：${formatted}`;
      }).join("<br/>");
    } },
    dataZoom: [{ type: "inside", xAxisIndex: axes.xAxis.map((_, index) => index), start: context.zoomStart ?? 0, end: context.zoomEnd ?? 100 }, ...(data.timeframe === "1" ? [] : [{ type: "slider", xAxisIndex: axes.xAxis.map((_, index) => index), bottom: 5, height: 18, start: context.zoomStart ?? 0, end: context.zoomEnd ?? 100, borderColor: dark ? "#34424c" : "#bfd2df", backgroundColor: dark ? "#17232c" : "#edf5fa", fillerColor: dark ? "#3f92bd2e" : "#75a8c936" }])],
    series: checked.valid,
  };
  if (!valueIsValid(option)) {
    issues.push({ code: "chart_option_invalid" });
    return { option: null, issues, visibleCenters: centers.visibleCenters, visibleMovements: movement.visibleMovements };
  }
  return { option, issues, visibleCenters: centers.visibleCenters, visibleMovements: movement.visibleMovements };
};

export const buildChartOption = (context: ChartBuildContext) => buildChartArtifacts(context).option;

export const displayPeriodLabel = (period?: string) => period === "higher" ? "更高一级" : period ? (PERIOD_LABELS[period] || period) : "未提供（结构元数据缺失）";
