import { useCallback, useEffect, useRef, useState } from "react";
import * as echarts from "echarts/core";
import { BarChart, CandlestickChart, LineChart, ScatterChart } from "echarts/charts";
import {
  AxisPointerComponent,
  DataZoomComponent,
  GridComponent,
  MarkAreaComponent,
  MarkLineComponent,
  TooltipComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { EChartsType } from "echarts/core";
import {
  ChevronLeft,
  ChevronRight,
  ChartCandlestick,
  Maximize2,
  Minimize2,
  Moon,
  Plus,
  RefreshCw,
  Search,
  Send,
  Sun,
  Trash2,
  X,
  Pencil,
  Minus,
  Square,
  MousePointer2,
  Undo2,
  Redo2,
  Save,
  ChevronDown,
  Settings,
  FolderPlus,
  Folder,
  MoreVertical,
} from "lucide-react";
import type { Bar, ChartData, Coverage, Node, SecurityCandidate, SecuritySearchResult, Stock, Drawing, DrawingStyle, WatchlistGroup, WatchlistMembership, WatchlistResponse, SubplotVisibility } from "./types";
import { levelStructureAppearance, levelStructureColor, periodStructureColor } from "./structureColors";
import { formatCompactNumber, formatIndicatorValue, formatPrice, formatVolume } from "./marketFormatters";
import {
  buildChartArtifacts,
  buildChartOption,
  buildChartPaneLayout,
  defaultPaneRatios,
  paneLayoutStorageKey,
  parseStoredPaneRatios,
  resizeAdjacentPanes,
  serializePaneRatios,
  buildAxes,
  buildPriceSeries,
  buildIndicatorSeries,
  buildPenSeries,
  buildCenterAreas,
  buildMovementSeries,
  buildEndpointLabels,
  buildDrawingSeries,
  validateSeries,
  validateAxes,
  normalizeChartData as normalizeChartDataBuilder,
  movementEndpointMarkers as movementEndpointMarkersBuilder,
  movementLineEndpoints as movementLineEndpointsBuilder,
  movementVisualStyle as movementVisualStyleBuilder,
} from "./chartBuilders";

export {
  buildChartArtifacts,
  buildChartOption,
  buildAxes,
  buildPriceSeries,
  buildIndicatorSeries,
  buildPenSeries,
  buildCenterAreas,
  buildMovementSeries,
  buildEndpointLabels,
  buildDrawingSeries,
  validateSeries,
  validateAxes,
} from "./chartBuilders";

export { formatCompactNumber, formatPrice, formatVolume } from "./marketFormatters";

const weekdays = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
export const formatTradeDate = (stamp: string) => {
  const day = stamp.slice(0, 10);
  const parsed = new Date(`${day}T00:00:00`);
  return Number.isNaN(parsed.getTime()) ? stamp : `${day} ${weekdays[parsed.getDay()]}`;
};

const periodLabels: Record<string, string> = { "1": "分时", "5": "5分钟", "15": "15分钟", "30": "30分钟", "60": "60分钟", "120": "120分钟", d: "日线", w: "周线", m: "月线", y: "年线" };
const periods = [["1", "分时"], ["d", "日线"], ["w", "周线"], ["m", "月线"], ["5", "5分钟"], ["30", "30分钟"]];
const morePeriods = [["15", "15分钟"], ["60", "60分钟"], ["120", "120分钟"], ["y", "年线"]];
type SubplotIndicator = "macd" | "volume" | "amount";
type MainIndicator = {mode:"ma"|"boll"|"pen_center"|"none";maPeriods:number[];bollPeriod:number;bollMultiplier:number};
type LayerVisibility = {
  pens: boolean;
  centers: boolean;
  movements: boolean;
  centerLevels: Record<string, boolean>;
  movementLevels: Record<string, boolean>;
};
echarts.use([
  BarChart,
  CandlestickChart,
  LineChart,
  ScatterChart,
  AxisPointerComponent,
  DataZoomComponent,
  GridComponent,
  MarkAreaComponent,
  MarkLineComponent,
  TooltipComponent,
  CanvasRenderer,
]);
async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, init);
  if (!r.ok) {
    const e = await r.json().catch(() => ({ detail: r.statusText }));
    throw new Error(e.detail || r.statusText);
  }
  return r.json();
}
const isAbortError = (error: unknown) => Boolean(error && typeof error === "object" && "name" in error && (error as { name?: string }).name === "AbortError");
const isChartDisposed = (instance: EChartsType | null | undefined) => {
  try { return Boolean(instance && typeof instance.isDisposed === "function" && instance.isDisposed()); }
  catch { return true; }
};
const unique = <T,>(items: T[], key: (x: T) => string) =>
  Array.from(new Map(items.map((x) => [key(x), x])).values());
export const normalizeChartData = (input: ChartData | null): ChartData | null => {
  return normalizeChartDataBuilder(input);
};
const scopeNodeOrdinals = (items: Node[]) => {
  const byLevel = new Map<number, Node[]>();
  items.forEach((item) => {
    const level = nodeLevel(item);
    byLevel.set(level, [...(byLevel.get(level) || []), item]);
  });
  return Array.from(byLevel.entries()).flatMap(([level, values]) => values
    .slice().sort((a, b) => `${a.start_date}|${a.end_date}|${a.id}`.localeCompare(`${b.start_date}|${b.end_date}|${b.id}`))
    .map((item, ordinal) => ({ ...item, level, ordinal, level_ordinal: ordinal })));
};
const mergeMovements = (fresh: Node[], existing: Node[]) => Array.from(
  [...fresh, ...existing].reduce((result, movement) => {
    const previous = result.get(movement.id);
    result.set(movement.id, previous ? { ...previous, ...movement,
      source_unit_ids: unique([...(movement.source_unit_ids || []), ...(previous.source_unit_ids || [])], (id) => id),
      source_pen_ids: unique([...(movement.source_pen_ids || []), ...(previous.source_pen_ids || [])], (id) => id),
      center_ids: unique([...(movement.center_ids || []), ...(previous.center_ids || [])], (id) => id),
      path_points: movement.path_points?.length ? movement.path_points : previous.path_points,
    } : movement);
    return result;
  }, new Map<string, Node>()).values(),
);
export const watchlistStocksForGroup = (stocks: Stock[], memberships: WatchlistMembership[], groupId: number) => memberships
  .filter((item) => item.group_id === groupId)
  .sort((a, b) => a.sort_order - b.sort_order)
  .map((item) => stocks.find((stock) => stock.symbol === item.symbol))
  .filter((item): item is Stock => Boolean(item));
export const watchlistUngroupedStocks = (stocks: Stock[], memberships: WatchlistMembership[]) => {
  const grouped = new Set(memberships.map((item) => item.symbol));
  return stocks.filter((stock) => !grouped.has(stock.symbol));
};
const datesIndex = (bars: Bar[], stamp: string) => {
  if (!bars.length) return 0;
  let best = 0, distance = Infinity;
  bars.forEach((bar, index) => {
    const d = Math.abs(Date.parse(bar.trade_date) - Date.parse(stamp));
    if (d < distance) { distance = d; best = index; }
  });
  return best;
};
const kindName = (kind?: string) => ({
  center: "中枢", pen_center: "笔中枢", movement: "走势", segment: "线段", standard: "标准笔", secondary: "次高低特殊笔", gap: "跳空特殊笔",
}[kind || ""] || kind || "结构");
const nodeLevel = (node: Node) => Number.isFinite(Number(node.level)) ? Math.max(1, Math.floor(Number(node.level))) : 1;
const isCenterNode = (node: Node) => node.kind === "center" || node.kind === "pen_center";
const isProvisionalStatus = (status?: string) => status === "provisional" || status === "candidate" || status === "pending";
const centerZd = (center: Node) => Number(center.zd ?? center.fixed_zd);
const centerZg = (center: Node) => Number(center.zg ?? center.fixed_zg);
const displayPeriodLabel = (displayPeriod?: string) => displayPeriod === "higher"
  ? "更高一级"
  : displayPeriod ? (periodLabels[displayPeriod] || displayPeriod) : "未提供（结构元数据缺失）";
const calculationSourceLabel = (timeframe: string) => `${periodLabels[timeframe] || timeframe}独立结构`;
const centerSemanticLabel = (timeframe: string, level: number, _displayPeriod?: string) =>
  `${periodLabels[timeframe] || timeframe}内部 L${level} 中枢（非跨周期递归）`;
const movementSemanticLabel = (timeframe: string, level: number) =>
  `${periodLabels[timeframe] || timeframe}内部 L${level} 走势（反向独立中枢确认结束）`;
const movementEndLabel = (reason?: string) => ({
  reverse_independent_center: "反向独立中枢确认结束",
  sequence_boundary: "结构序列中断，尚未完成",
  data_boundary: "行情区间中断，尚未完成",
  provisional_tail: "等待反向独立中枢",
}[reason || ""] || "尚未确认");

export const centersForStructureLevel = (centers: Node[], activeLevel: number) =>
  centers.filter((center) =>
    (center.role === undefined || center.role === "hierarchy")
    && Number.isInteger(Number(center.level))
    && Number(center.level) === activeLevel,
  );

export type MovementEndpointMarker = {
  key: string;
  trade_date: string;
  price: number;
  label: string;
  kind: "high" | "low";
  movementId: string;
  level: number;
};

const movementBoundaryValues = (movement: Node) => {
  const path = Array.isArray(movement.path_points) ? movement.path_points : [];
  const endpoints = Array.isArray(movement.endpoint_points) ? movement.endpoint_points : [];
  const first = endpoints[0] || path[0];
  const last = endpoints[endpoints.length - 1] || path[path.length - 1];
  const startDate = movement.start_date || first?.trade_date;
  const endDate = movement.end_date || last?.trade_date;
  const startPrice = Number(movement.start_price ?? first?.price);
  const endPrice = Number(movement.end_price ?? last?.price);
  if (!startDate || !endDate || startDate > endDate || !Number.isFinite(startPrice) || !Number.isFinite(endPrice)) return null;
  return { startDate, endDate, startPrice, endPrice };
};

/** Build one stable marker per shared movement endpoint, numbering highs/lows independently. */
export const movementEndpointMarkers = movementEndpointMarkersBuilder;

export const movementLineEndpoints = movementLineEndpointsBuilder;

export const movementVisualStyle = movementVisualStyleBuilder;
const syncLabel = (status?: string) => ({
  pending: "等待同步", running: "同步中", partial_failed: "部分失败",
  failed: "同步失败", success: "已完成",
}[status || ""] || "");
const defaultDrawingStyle = (timeframe: string, theme: string, rectangle = false): DrawingStyle => {
  const color = periodStructureColor(theme === "light" ? "light" : "dark", timeframe);
  return { color, width: 1.5, line_type: "solid", opacity: 0.9, ...(rectangle ? { fill_color: color, fill_opacity: theme === "light" ? 0.1 : 0.14 } : {}) };
};
const normalizedDrawingStyle = (drawing: Drawing, theme: string): DrawingStyle => {
  const defaults = defaultDrawingStyle(drawing.timeframe, theme, drawing.object_type === "rectangle");
  return { ...defaults, ...(drawing.style || {}), ...(drawing.object_type === "rectangle" ? {} : { fill_color: undefined, fill_opacity: undefined }) };
};

export const formatKlineTooltip = (bar?: Bar, previousClose?: number) => {
  if (!bar) return "";
  const change = Number.isFinite(previousClose) && previousClose !== 0 && Number.isFinite(bar.close)
    ? (bar.close / Number(previousClose) - 1) * 100
    : null;
  const tone = change === null || change === 0 ? "" : change > 0 ? "rise" : "fall";
  const toneAttr = tone ? ` class="${tone}"` : "";
  const price = (label: string, value: number) => `<span>${label}：<b${toneAttr}>${formatPrice(value)}</b></span>`;
  return [
    `<b>${formatTradeDate(bar.trade_date)}</b>`,
    price("开盘", bar.open),
    price("收盘", bar.close),
    price("最低", bar.low),
    price("最高", bar.high),
    `<span>涨跌幅：<b${toneAttr}>${change === null ? "--" : `${change >= 0 ? "+" : ""}${change.toFixed(2)}%`}</b></span>`,
    `成交量：${formatVolume(bar.volume)}`,
    `成交额：${formatCompactNumber(bar.amount)}`,
  ].join("<br/>");
};

export const formatCenterTooltip = (center: Node, chartTimeframe: string) => {
  const level = nodeLevel(center);
  const appearance = levelStructureAppearance("dark", chartTimeframe, level);
  const displayPeriod = center.display_period || appearance.displayPeriod;
  return [
    `<b>L${level} 中枢</b>`,
    `状态：${center.status === "confirmed" ? "已确认" : "候选"}`,
    `配色参考（非结构周期）：${displayPeriodLabel(displayPeriod)}`,
    `计算来源：${calculationSourceLabel(chartTimeframe)}`,
    `语义对应：${centerSemanticLabel(chartTimeframe, level, displayPeriod)}`,
    `颜色标识：${center.color_key || appearance.colorKey}`,
    `实际边界：${center.start_date} → ${center.end_date}`,
    `核心区间：${formatPrice(centerZd(center))} / ${formatPrice(centerZg(center))}`,
    ...(center.dd !== undefined || center.gg !== undefined ? [`外围区间：${formatPrice(center.dd)} / ${formatPrice(center.gg)}`] : []),
    `确认时间：${center.confirmed_at || "等待确认"}`,
    ...(center.upgrade_kind ? [`升级路径：${center.upgrade_kind}`] : []),
    ...(center.progress ? [`升级进度：${center.progress}`] : []),
  ].join("<br/>");
};

export const formatMovementTooltip = (movement: Node, chartTimeframe?: string) => {
  const level = nodeLevel(movement);
  const appearance = chartTimeframe ? levelStructureAppearance("dark", chartTimeframe, level) : null;
  const direction = movement.direction || (Number(movement.end_price) >= Number(movement.start_price) ? "up" : "down");
  return [
    `<b>${movement.classification === "trend" ? "趋势" : "盘整走势"} · ${direction === "up" ? "向上" : "向下"}</b>`,
    ...(chartTimeframe ? [
      `结构级别：L${level}`,
      `配色参考（非结构周期）：${displayPeriodLabel(movement.display_period || appearance?.displayPeriod)}`,
      `计算来源：${calculationSourceLabel(chartTimeframe)}`,
      `语义对应：${movementSemanticLabel(chartTimeframe, level)}`,
    ] : []),
    `状态：${movement.status === "confirmed" ? "已确认" : "未完成"}`,
    `结构来源：正式层级`,
    `参考中枢数量：${movement.center_count || 0}`,
    ...(movement.center_ids?.length ? [`参考中枢 ID：${movement.center_ids.join(" / ")}`] : []),
    ...(movement.source_unit_ids?.length ? [`构成单位：${movement.source_unit_ids.join(" / ")}`] : []),
    ...(movement.source_pen_ids?.length ? [`构成笔：${movement.source_pen_ids.join(" / ")}`] : []),
    `实际边界：${movement.start_date} → ${movement.end_date}`,
    `确认时间：${movement.confirmed_at || "等待反向中枢"}`,
    `结束原因：${movementEndLabel(movement.termination_reason)}`,
    ...(movement.confirmation_center_id ? [`确认中枢：${movement.confirmation_center_id}`] : []),
  ].join("<br/>");
};

function Chart({
  data,
  theme,
  onOlder,
  onSelect,
  height,
  visible,
  subplotIndicators,
  subplotVisible,
  onSubplotIndicator,
  onLayerToggle,
  mainIndicator = { mode: "pen_center", maPeriods: [5, 10, 20], bollPeriod: 20, bollMultiplier: 2 },
  onMainIndicator,
  onOpenIndicatorSettings = () => {},
  drawingTool,
  onDrawingCreate,
  drawingItems,
  drawingOpen,
  onDrawingUpdate,
  onDrawingDelete,
  onDrawingSelect,
  onStructureUpdate,
  onStructureDelete,
  selectedStructureId,
  movementsStale = false,
}: {
  data: ChartData | null;
  theme: string;
  onOlder: () => void;
  onSelect: (n: Node | null) => void;
  selectedStructureId?: string | null;
  height: number;
  visible: LayerVisibility;
  subplotIndicators: SubplotIndicator[];
  subplotVisible: SubplotVisibility;
  onSubplotIndicator: (index: number, value: SubplotIndicator) => void;
  onLayerToggle: (key: "pens" | "centers" | "movements", level?: number) => void;
  mainIndicator?: {mode:"ma"|"boll"|"pen_center"|"none"; maPeriods:number[]; bollPeriod:number; bollMultiplier:number};
  onMainIndicator?: (mode:"ma"|"boll"|"pen_center"|"none") => void;
  onOpenIndicatorSettings?: () => void;
  drawingTool?: "segment" | "line" | "rectangle" | null;
  onDrawingCreate?: (drawing: Omit<Drawing, "id" | "symbol" | "timeframe">) => void;
  drawingItems?: Drawing[];
  drawingOpen?: boolean;
  onDrawingUpdate?: (drawing: Drawing) => void;
  onDrawingDelete?: (id: number) => void;
  onDrawingSelect?: (drawing: Drawing | null) => void;
  onStructureUpdate?: (node: Node, payload: Partial<Node>) => void;
  onStructureDelete?: (node: Node) => void;
  movementsStale?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<EChartsType | null>(null);
  const zoomState = useRef({ start: 0, end: 100 });
  const previousDates = useRef<string[]>([]);
  const lastChartKey = useRef<string | undefined>(undefined);
  const drawingStart = useRef<{trade_date:string;price:number} | null>(null);
  const drawingCurrent = useRef<{trade_date:string;price:number} | null>(null);
  const currentPointer = useRef({x:0,y:0});
  const dragOrigin = useRef<{point:{trade_date:string;price:number};drawing:Drawing} | null>(null);
  const structureDrag = useRef<{node:Node;handle:"start"|"end"|"left"|"right"|"top"|"bottom"} | null>(null);
  const [contextMenu, setContextMenu] = useState<{x:number;y:number;node?:Node;drawing?:Drawing} | null>(null);
  const [preview, setPreview] = useState<{start:{trade_date:string;price:number};end:{trade_date:string;price:number}} | null>(null);
  const [selectedDrawing, setSelectedDrawing] = useState<number | null>(null);
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);
  const [dragHandle, setDragHandle] = useState<"start"|"end"|"move"|null>(null);
  const [renderError, setRenderError] = useState<string | null>(null);
  const [compactLayout, setCompactLayout] = useState(() => innerWidth <= 760);
  const safeData = normalizeChartData(data);
  const bars = safeData?.bars || [];
  const activeSubplotCount = subplotVisible.filter(Boolean).length;
  const paneKey = paneLayoutStorageKey(safeData?.symbol || "", safeData?.timeframe || "d", activeSubplotCount);
  const [paneRatios, setPaneRatios] = useState(() => defaultPaneRatios(activeSubplotCount));
  const paneResize = useRef<{ pointerId: number; separatorIndex: number; startY: number; heights: number[]; ratios: number[] } | null>(null);
  const paneLayout = buildChartPaneLayout({ requestedHeight: height, subplotCount: activeSubplotCount, ratios: paneRatios, intraday: safeData?.timeframe === "1", mainIndicatorVisible: mainIndicator.mode !== "none", compact: compactLayout });
  const effectiveHeight = paneLayout.chartHeight;
  useEffect(() => {
    const media = window.matchMedia("(max-width: 760px)");
    const update = () => setCompactLayout(media.matches);
    media.addEventListener?.("change", update);
    return () => media.removeEventListener?.("change", update);
  }, []);
  useEffect(() => {
    setPaneRatios(parseStoredPaneRatios(localStorage.getItem(paneKey), activeSubplotCount));
  }, [paneKey, activeSubplotCount]);
  const persistPaneRatios = (ratios: number[]) => {
    localStorage.setItem(paneKey, serializePaneRatios(ratios, activeSubplotCount));
  };
  const toPoint = useCallback((event: React.PointerEvent) => {
    if (!chart.current || !bars.length) return null;
    const rect = ref.current?.getBoundingClientRect(); if (!rect) return null;
    const px = [event.clientX - rect.left, event.clientY - rect.top];
    const value = chart.current.convertFromPixel({xAxisIndex: 0, yAxisIndex: 0}, px) as any[];
    const rawIndex = Number(value?.[0]);
    const index = Math.max(0, Math.min(bars.length - 1, Math.round(rawIndex)));
    const y = Number(value?.[1]);
    if (!Number.isFinite(y)) return null;
    return { trade_date: bars[index].trade_date, price: y };
  }, [bars]);
  const pointPixel = useCallback((point: {trade_date:string;price:number}) => {
    if (!chart.current) return [0,0];
    const index = Math.max(0, datesIndex(bars, point.trade_date));
    const px = chart.current.convertToPixel({xAxisIndex:0,yAxisIndex:0}, [index, point.price]) as number[];
    return [Number(px?.[0] || 0), Number(px?.[1] || 0)];
  }, [bars]);
  useEffect(() => {
    const element = ref.current;
    if (!element || !bars.length) {
      // Empty data must not create or retain an ECharts instance. This also
      // handles a stale instance left on the DOM by an interrupted fast switch.
      const owned = chart.current;
      try {
        const live = element ? echarts.getInstanceByDom(element) : undefined;
        (live || owned)?.dispose();
      } catch (error) {
        console.warn("缠论空图表销毁失败", error);
      } finally {
        chart.current = null;
      }
      return;
    }
    let instance: EChartsType | null = null;
    try {
      const existing = echarts.getInstanceByDom(element);
      instance = existing || echarts.init(element);
      chart.current = instance;
      setRenderError(null);
    } catch (error) {
      console.error("缠论图表初始化失败", error);
      setRenderError("图表暂时无法渲染");
      return;
    }
    const resize = () => {
      try {
        if (instance && !isChartDisposed(instance)) instance.resize();
      } catch { /* disposed during unmount */ }
    };
    const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(resize) : null;
    observer?.observe(element);
    if (!observer) window.addEventListener("resize", resize);
    return () => {
      observer?.disconnect();
      if (!observer) window.removeEventListener("resize", resize);
      try {
        const live = echarts.getInstanceByDom(element);
        if (live === instance && !isChartDisposed(instance)) instance.dispose();
      } catch (error) {
        console.warn("缠论图表销毁失败", error);
      } finally {
        if (chart.current === instance) chart.current = null;
      }
    };
  }, [bars.length > 0]);
  useEffect(() => {
    const element = ref.current;
    if (!element || !bars.length) return;
    let instance: EChartsType | null = null;
    try {
      const live = echarts.getInstanceByDom(element);
      instance = live && !isChartDisposed(live) ? live : null;
    } catch (error) {
      console.error("缠论图表实例读取失败", error);
      setRenderError("图表暂时无法渲染");
      return;
    }
    if (!instance) return;
    chart.current = instance;
    /* Keep option construction outside the React effect's ad-hoc object graph.
       The builder validates every series and axis before ECharts sees it. */
    const builderData = safeData as ChartData;
    const builderDates = bars.map((bar) => bar.trade_date);
    const builderChartKey = `${builderData.symbol || ""}:${builderData.timeframe || ""}`;
    const builderTimeframeChanged = lastChartKey.current !== builderChartKey;
    if (builderTimeframeChanged) {
      try {
        const saved = localStorage.getItem(`chan-zoom-${builderChartKey}`);
        const parsed = saved ? JSON.parse(saved) : null;
        zoomState.current = parsed && Number.isFinite(parsed.start) && Number.isFinite(parsed.end)
          ? { start: Math.max(0, Math.min(100, parsed.start)), end: Math.max(0, Math.min(100, parsed.end)) }
          : { start: 0, end: 100 };
      } catch { zoomState.current = { start: 0, end: 100 }; }
    } else if (previousDates.current.length && builderDates.length > previousDates.current.length && previousDates.current[0] !== builderDates[0]) {
      const old = previousDates.current;
      const oldStart = old[Math.round((zoomState.current.start / 100) * Math.max(0, old.length - 1))];
      const oldEnd = old[Math.round((zoomState.current.end / 100) * Math.max(0, old.length - 1))];
      const startIndex = builderDates.indexOf(oldStart), endIndex = builderDates.indexOf(oldEnd);
      if (startIndex >= 0 && endIndex >= 0) zoomState.current = {
        start: (startIndex / Math.max(1, builderDates.length - 1)) * 100,
        end: (endIndex / Math.max(1, builderDates.length - 1)) * 100,
      };
    }
    previousDates.current = builderDates;
    lastChartKey.current = builderChartKey;
    let artifacts: ReturnType<typeof buildChartArtifacts>;
    try {
      artifacts = buildChartArtifacts({
        data: builderData,
        bars,
        theme,
        visible,
        subplotIndicators: subplotIndicators.slice(0, 4) as any,
        subplotVisible,
        mainIndicator,
        drawingItems: drawingItems || builderData.drawings,
        movementsStale,
        zoomStart: zoomState.current.start,
        zoomEnd: zoomState.current.end,
        chartHeight: effectiveHeight,
        paneRatios,
        compactLayout,
        selectedStructureId: selectedStructureId || null,
        formatKlineTooltip,
        formatCenterTooltip,
        formatMovementTooltip,
      });
    } catch (error) {
      console.error("缠论图表配置生成失败", error);
      setRenderError("图表暂时无法渲染");
      return;
    }
    if (!artifacts.option) {
      try { instance.clear(); } catch { /* disposed during a switch */ }
      setRenderError("图表暂时无法渲染");
      return;
    }
    if (artifacts.issues.length && import.meta.env.DEV) console.warn("缠论图表配置已过滤异常项", artifacts.issues);
    try {
      if (isChartDisposed(instance) || echarts.getInstanceByDom(element) !== instance) return;
      instance.setOption(artifacts.option, { notMerge: true, lazyUpdate: false });
      setRenderError(null);
    } catch (error) {
      console.error("缠论图表渲染失败", error);
      setRenderError("图表暂时无法渲染");
      return;
    }
    const builderZoom = (event: any) => {
      const value = event.batch?.[0] || event;
      if (Number.isFinite(value.start) && Number.isFinite(value.end)) {
        zoomState.current = { start: value.start, end: value.end };
        try { localStorage.setItem(`chan-zoom-${builderChartKey}`, JSON.stringify(zoomState.current)); } catch { /* storage is optional */ }
      }
      if ((value.start ?? 100) <= 15 && builderData.has_more) onOlder();
    };
    const builderClick = () => {};
    const builderAxisPointer = (event: any) => {
      const axis = event.axesInfo?.find((item: any) => item.axisDim === "x" && item.axisIndex === 0);
      const index = typeof axis?.value === "string" ? builderDates.indexOf(axis.value) : Number(axis?.value);
      if (Number.isInteger(index) && index >= 0 && index < bars.length) setHoveredIndex(index);
    };
    const builderGlobalOut = () => setHoveredIndex(null);
    const distanceToSegment = (x: number, y: number, start: number[], end: number[]) => {
      const dx = end[0] - start[0], dy = end[1] - start[1];
      if (!dx && !dy) return Math.hypot(x - start[0], y - start[1]);
      const ratio = Math.max(0, Math.min(1, ((x - start[0]) * dx + (y - start[1]) * dy) / (dx * dx + dy * dy)));
      return Math.hypot(x - (start[0] + ratio * dx), y - (start[1] + ratio * dy));
    };
    const nodePixelBoundary = (node: Node) => {
      const start = pointPixel({ trade_date: node.start_date, price: Number(node.start_price ?? node.low) });
      const end = pointPixel({ trade_date: node.end_date, price: Number(node.end_price ?? node.high) });
      return { start, end };
    };
    const findStructureAt = (x: number, y: number) => {
      const center = artifacts.visibleCenters.find((node) => {
        const zd = Number(node.zd ?? node.fixed_zd), zg = Number(node.zg ?? node.fixed_zg);
        if (!Number.isFinite(zd) || !Number.isFinite(zg)) return false;
        const topLeft = pointPixel({ trade_date: node.start_date, price: zg });
        const bottomRight = pointPixel({ trade_date: node.end_date, price: zd });
        return x >= Math.min(topLeft[0], bottomRight[0]) - 8 && x <= Math.max(topLeft[0], bottomRight[0]) + 8 && y >= Math.min(topLeft[1], bottomRight[1]) - 8 && y <= Math.max(topLeft[1], bottomRight[1]) + 8;
      });
      const candidates = [
        ...(builderData.pens || []).filter((node) => node.status === "confirmed").map((node) => ({ node, tolerance: 20 })),
        ...artifacts.visibleMovements.map((node) => ({ node, tolerance: 18 })),
      ];
      const nearest = candidates.map(({ node, tolerance }) => {
        const boundary = nodePixelBoundary(node);
        return { node, distance: distanceToSegment(x, y, boundary.start, boundary.end), tolerance };
      }).filter((item) => item.distance <= item.tolerance).sort((a, b) => a.distance - b.distance)[0];
      return nearest?.node || center || null;
    };
    const builderCanvasClick = (event: any) => {
      if (drawingTool) return;
      const x = Number(event?.zrX ?? event?.offsetX), y = Number(event?.zrY ?? event?.offsetY);
      if (!Number.isFinite(x) || !Number.isFinite(y)) return;
      onSelect(findStructureAt(x, y));
    };
    let zr: ReturnType<EChartsType["getZr"]> | undefined;
    try {
      zr = instance.getZr?.();
      instance.off("datazoom");
      instance.off("click");
      instance.off("updateAxisPointer");
      instance.on("datazoom", builderZoom);
      instance.on("click", builderClick);
      instance.on("updateAxisPointer", builderAxisPointer);
      zr?.off("globalout", builderGlobalOut);
      zr?.on("globalout", builderGlobalOut);
      zr?.off("click", builderCanvasClick);
      zr?.on("click", builderCanvasClick);
    } catch (error) {
      console.error("缠论图表事件绑定失败", error);
      setRenderError("图表暂时无法渲染");
      return;
    }
    return () => {
      try {
        instance.off("datazoom", builderZoom);
        instance.off("click", builderClick);
        instance.off("updateAxisPointer", builderAxisPointer);
        zr?.off("globalout", builderGlobalOut);
        zr?.off("click", builderCanvasClick);
      } catch { /* ECharts may already be disposed during a fast switch. */ }
    };
  }, [data, bars, onOlder, onSelect, selectedStructureId, height, visible, subplotIndicators, subplotVisible, mainIndicator, drawingTool, drawingItems, theme, movementsStale, paneRatios, compactLayout, effectiveHeight]);
  useEffect(() => {
    const el = ref.current;
    if (!el || !drawingOpen) return;
    const hit = (point: {trade_date:string;price:number}) => {
      const [x,y] = pointPixel(point);
      const rect = el.getBoundingClientRect();
      const px = currentPointer.current;
      const distance = (a:number,b:number) => Math.hypot(a-b, 0);
      return (drawingItems || []).slice().reverse().find((d) => {
        const [sx,sy] = pointPixel(d.start_anchor), [ex,ey] = pointPixel(d.end_anchor);
        const tolerance = 12;
        if (Math.hypot(px.x-sx, px.y-sy) < tolerance || Math.hypot(px.x-ex, px.y-ey) < tolerance) return true;
        if (d.object_type === "rectangle") return px.x >= Math.min(sx,ex)-tolerance && px.x <= Math.max(sx,ex)+tolerance && px.y >= Math.min(sy,ey)-tolerance && px.y <= Math.max(sy,ey)+tolerance;
        const dx=ex-sx, dy=ey-sy, len=Math.hypot(dx,dy); if (!len) return false;
        return Math.abs((px.x-sx)*dy-(px.y-sy)*dx)/len < tolerance;
      });
    };
    const onDown = (event: PointerEvent) => {
      if (event.button !== 0) return;
      const point = toPoint(event as unknown as React.PointerEvent); if (!point) return;
      currentPointer.current = {x:event.offsetX,y:event.offsetY};
      if (drawingTool) {
        drawingStart.current = point; drawingCurrent.current = point; setPreview({start:point,end:point});
        el.setPointerCapture(event.pointerId); event.preventDefault(); return;
      }
      const target = hit(point);
      if (target) {
        setSelectedDrawing(target.id); onDrawingSelect?.(target);
        const [sx,sy]=pointPixel(target.start_anchor), [ex,ey]=pointPixel(target.end_anchor);
        const nearStart=Math.hypot(event.offsetX-sx,event.offsetY-sy)<14, nearEnd=Math.hypot(event.offsetX-ex,event.offsetY-ey)<14;
        setDragHandle(nearStart ? "start" : nearEnd ? "end" : "move");
        dragOrigin.current = {point, drawing:target}; el.setPointerCapture(event.pointerId); event.preventDefault();
      }
    };
    const onMove = (event: PointerEvent) => {
      const point = toPoint(event as unknown as React.PointerEvent); if (!point) return;
      currentPointer.current = {x:event.offsetX,y:event.offsetY};
      if (drawingStart.current && drawingTool) { drawingCurrent.current = point; setPreview({start:drawingStart.current,end:point}); return; }
      if (dragHandle && dragOrigin.current) {
        const origin = dragOrigin.current; const dx = Date.parse(point.trade_date)-Date.parse(origin.point.trade_date); const dp = point.price-origin.point.price;
        const moveAnchor = (a:{trade_date:string;price:number}) => ({trade_date:new Date(Date.parse(a.trade_date)+dx).toISOString().replace('T',' ').slice(0,19), price:a.price+dp});
        if (dragHandle === "start") onDrawingUpdate?.({...origin.drawing,start_anchor:point});
        else if (dragHandle === "end") onDrawingUpdate?.({...origin.drawing,end_anchor:point});
        else onDrawingUpdate?.({...origin.drawing,start_anchor:moveAnchor(origin.drawing.start_anchor),end_anchor:moveAnchor(origin.drawing.end_anchor)});
      }
    };
    const onUp = (event: PointerEvent) => {
      const point = toPoint(event as unknown as React.PointerEvent); if (!point) return;
      if (drawingStart.current && drawingTool) {
        const start = drawingStart.current; if (start.trade_date !== point.trade_date || Math.abs(start.price-point.price)>1e-9) onDrawingCreate?.({object_type:drawingTool,start_anchor:start,end_anchor:point,style:defaultDrawingStyle(data?.timeframe || "d", document.documentElement.dataset.theme || "dark", drawingTool === "rectangle"),label:"",visible:true});
        drawingStart.current=null; drawingCurrent.current=null; setPreview(null); return;
      }
      setDragHandle(null); dragOrigin.current=null;
    };
    const onContext = (event: MouseEvent) => { event.preventDefault(); const point = toPoint(event as unknown as React.PointerEvent); if (point) { const target = hit(point); if (target) { setSelectedDrawing(target.id); onDrawingSelect?.(target); setContextMenu({x:event.clientX,y:event.clientY,drawing:target}); } } };
    el.addEventListener("pointerdown", onDown); el.addEventListener("pointermove", onMove); el.addEventListener("pointerup", onUp); el.addEventListener("contextmenu", onContext);
    return () => { el.removeEventListener("pointerdown", onDown); el.removeEventListener("pointermove", onMove); el.removeEventListener("pointerup", onUp); el.removeEventListener("contextmenu", onContext); };
  }, [drawingOpen, drawingTool, drawingItems, toPoint, pointPixel, onDrawingCreate, onDrawingUpdate, onDrawingDelete, onDrawingSelect, data?.timeframe, dragHandle]);
  useEffect(() => {
    const zr = chart.current?.getZr();
    if (!zr || !drawingOpen || drawingTool || !onStructureUpdate) return;
    const editableCenters = (data?.centers?.length ? data.centers : (data?.pen_centers || []))
      .filter((node) => nodeLevel(node) === 1);
    // Only pens and L1 centers are editable; higher-level structures are derived.
    const nodes = [...(data?.pens || []), ...editableCenters];
    const nodePoint = (node: Node, end: boolean) => pointPixel({trade_date: end ? node.end_date : node.start_date, price: end ? (node.end_price ?? node.high ?? 0) : (node.start_price ?? node.low ?? 0)});
    const find = (event: any) => {
      const [x,y] = [event.offsetX,event.offsetY];
      let best: {node:Node;handle:"start"|"end";distance:number}|null=null;
      nodes.forEach((node) => (["start","end"] as const).forEach((handle) => { const [px,py]=nodePoint(node,handle==="end"); const d=Math.hypot(x-px,y-py); if(d<14 && (!best||d<best.distance)) best={node,handle,distance:d}; }));
      return best;
    };
    const findNodeBody = (event:any) => {
      const [x,y] = [event.offsetX,event.offsetY];
      return nodes.slice().reverse().find((node) => {
        const [sx,sy]=nodePoint(node,false), [ex,ey]=nodePoint(node,true); const dx=ex-sx,dy=ey-sy,len=Math.hypot(dx,dy);
        return len > 0 && Math.abs((x-sx)*dy-(y-sy)*dx)/len < 9 && x >= Math.min(sx,ex)-8 && x <= Math.max(sx,ex)+8;
      });
    };
    const down=(event:any)=>{ const hit=find(event) as {node:Node;handle:"start"|"end";distance:number}|null; if(hit){ structureDrag.current={node:hit.node,handle:hit.handle}; event.event?.preventDefault?.(); }};
    const move=(event:any)=>{ const drag=structureDrag.current; if(!drag) return; const point=toPoint({clientX:(ref.current?.getBoundingClientRect().left || 0)+(event.event?.zrX ?? event.offsetX),clientY:(ref.current?.getBoundingClientRect().top || 0)+(event.event?.zrY ?? event.offsetY)} as any); if(!point) return; const payload=drag.handle==="start"?{start_date:point.trade_date,start_price:point.price}:{end_date:point.trade_date,end_price:point.price}; onStructureUpdate?.(drag.node,payload); };
    const up=()=>{structureDrag.current=null;};
    const context=(event:any)=>{ event.event?.preventDefault?.(); const endpoint = find(event) as {node:Node;handle:"start"|"end";distance:number}|null; const node=findNodeBody(event) || endpoint?.node; if(node) setContextMenu({x:event.event?.clientX || event.offsetX,y:event.event?.clientY || event.offsetY,node}); };
    zr.on("mousedown",down); zr.on("mousemove",move); zr.on("mouseup",up); zr.on("contextmenu",context);
    return ()=>{zr.off("mousedown",down);zr.off("mousemove",move);zr.off("mouseup",up);zr.off("contextmenu",context);};
  }, [drawingOpen, drawingTool, data?.pens, data?.centers, data?.pen_centers, pointPixel, toPoint, onStructureUpdate]);
  const infoIndex = hoveredIndex ?? Math.max(0, bars.length - 1);
  const infoBar = bars[infoIndex];
  const infoMa = infoBar ? (data?.indicators?.ma || []).find((item) => item.trade_date === infoBar.trade_date) : undefined;
  const infoPreviousClose = infoIndex > 0 ? bars[infoIndex - 1]?.close : undefined;
  const infoChange = infoBar && infoPreviousClose ? (infoBar.close / infoPreviousClose - 1) * 100 : null;
  const infoChangeAmount = infoBar && infoPreviousClose ? infoBar.close - infoPreviousClose : null;
  const chartCenters = data?.centers?.length ? data.centers : (data?.pen_centers || []);
  const visibleCenterCount = chartCenters.filter((item) => (item.role === undefined || item.role === "hierarchy") && visible.centers && visible.centerLevels[String(nodeLevel(item))] !== false).length;
  const visibleMovementCount = (data?.movements || []).filter((item) => visible.movements && item.role === "hierarchy_component" && visible.movementLevels[String(nodeLevel(item))] !== false).length;
  const centerLevels = Array.from(new Set((data?.center_levels || chartCenters.map(nodeLevel)).map(Number).filter((level) => Number.isInteger(level) && level >= 1))).sort((a, b) => a - b);
  const movementLevels = Array.from(new Set((data?.movement_levels || (data?.movements || []).map(nodeLevel)).map(Number).filter((level) => Number.isInteger(level) && level >= 1))).sort((a, b) => a - b);
  return <div className={`chart-shell ${data?.timeframe === "1" ? "intraday-chart" : "kline-chart"}`} style={{ height: effectiveHeight }} onClick={() => contextMenu && setContextMenu(null)}>
    <div ref={ref} className="chart" aria-label={bars.length ? "K线图" : "暂无行情图表"} />
    {safeData && !bars.length && <div className="chart-empty-state" role="status">暂无可绘制行情</div>}
    {renderError && <div className="chart-render-error" role="alert">{renderError}</div>}
    {data?.timeframe !== "1" && infoBar && <div className="kline-info-strip">
      <strong>{formatTradeDate(infoBar.trade_date)}</strong>
      <span>开盘：<b>{formatPrice(infoBar.open)}</b></span><span>最高：<b>{formatPrice(infoBar.high)}</b></span>
      <span>最低：<b>{formatPrice(infoBar.low)}</b></span><span>收盘：<b>{formatPrice(infoBar.close)}</b></span>
      <span>涨跌额 / 涨跌幅：<b className={infoChange === null || infoChange === 0 ? "" : infoChange > 0 ? "rise" : "fall"}>{infoChangeAmount === null ? "--" : `${infoChangeAmount >= 0 ? "+" : ""}${formatPrice(infoChangeAmount)}`} / {infoChange === null ? "--" : `${infoChange >= 0 ? "+" : ""}${infoChange.toFixed(2)}%`}</b></span>
      <span>成交量：<b>{formatVolume(infoBar.volume)}</b></span><span>成交额：<b>{formatCompactNumber(infoBar.amount)}</b></span>
    </div>}
    {data?.timeframe !== "1" && infoBar && mainIndicator.mode !== "none" && <div className="kline-ma-strip">
      {mainIndicator.mode === "ma" && mainIndicator.maPeriods.map((period) => <span key={period}>MA{period}: {formatPrice(infoMa?.values?.[String(period)])}</span>)}
      {mainIndicator.mode === "boll" && (() => { const b=(data?.indicators?.boll||[]).find((item)=>item.trade_date===infoBar.trade_date); return <><span>BOLL上轨: {formatPrice(b?.upper)}</span><span>BOLL中轨: {formatPrice(b?.middle)}</span><span>BOLL下轨: {formatPrice(b?.lower)}</span></>; })()}
      {mainIndicator.mode === "pen_center" && <span>笔 / 中枢 / 走势 · {data?.pens.length || 0} 笔 · {visibleCenterCount} 中枢 · {visibleMovementCount} 走势</span>}
    </div>}
    <svg className={`drawing-overlay ${drawingOpen ? "editing" : ""}`} aria-hidden="true">
      {preview && (() => { const [sx,sy]=pointPixel(preview.start), [ex,ey]=pointPixel(preview.end); const color=periodStructureColor(document.documentElement.dataset.theme === "light" ? "light":"dark", data?.timeframe || "d"); return preview && drawingTool === "rectangle" ? <rect x={Math.min(sx,ex)} y={Math.min(sy,ey)} width={Math.abs(ex-sx)} height={Math.abs(ey-sy)} fill={`${color}22`} stroke={color} strokeDasharray="5 4" /> : <line x1={sx} y1={sy} x2={ex} y2={ey} stroke={color} strokeWidth="2" strokeDasharray="5 4" />; })()}
      {selectedDrawing !== null && (() => { const item=(drawingItems || []).find((d)=>d.id===selectedDrawing); if(!item) return null; const [sx,sy]=pointPixel(item.start_anchor), [ex,ey]=pointPixel(item.end_anchor); return <g className="drawing-selection"><circle cx={sx} cy={sy} r="5"/><circle cx={ex} cy={ey} r="5"/></g>; })()}
    </svg>
    {data?.timeframe !== "1" && <div className="chart-layer-controls" style={{top: paneLayout.tops[0] + 6}} aria-label="缠论图层控制">
      {([['pens', '正式笔', 'pen'], ['centers', '正式中枢', 'center'], ['movements', '正式走势', 'movement']] as const).map(([key, label, icon]) => (
        <button
          key={key}
          className={`chart-layer-toggle ${visible[key] ? "active" : ""}`}
          aria-label={`${visible[key] ? "隐藏" : "显示"}${label}`}
          aria-pressed={visible[key]}
          title={`${visible[key] ? "隐藏" : "显示"}${label}`}
          onClick={() => onLayerToggle(key)}
        >
          <i
            className={`legend-swatch ${icon}`}
            style={{
              borderColor: icon === "center" ? levelStructureColor(theme === "light" ? "light" : "dark", data?.timeframe || "d", centerLevels[0] || 1) : periodStructureColor(theme === "light" ? "light" : "dark", data?.timeframe || "d"),
              ...(icon === "center" ? { background: `${levelStructureColor(theme === "light" ? "light" : "dark", data?.timeframe || "d", centerLevels[0] || 1)}${theme === "light" ? "1A" : "24"}` } : {}),
              ...(icon === "movement" ? { borderColor: levelStructureColor(theme === "light" ? "light" : "dark", data?.timeframe || "d", movementLevels[0] || 1), borderWidth: 3 } : {}),
            }}
            aria-hidden="true"
          />
          <span>{label}</span>
        </button>
      ))}
      {centerLevels.map((level) => <button key={`center-level-${level}`} className={`chart-layer-toggle chart-level-toggle ${visible.centers && visible.centerLevels[String(level)] !== false ? "active" : ""}`} aria-label={`${visible.centerLevels[String(level)] !== false ? "隐藏" : "显示"}L${level}中枢`} aria-pressed={visible.centers && visible.centerLevels[String(level)] !== false} title={`${visible.centerLevels[String(level)] !== false ? "隐藏" : "显示"}L${level}中枢`} onClick={() => onLayerToggle("centers", level)}><i className="legend-swatch center" style={{borderColor: levelStructureColor(theme === "light" ? "light" : "dark", data?.timeframe || "d", level), background: `${levelStructureColor(theme === "light" ? "light" : "dark", data?.timeframe || "d", level)}${theme === "light" ? "1A" : "24"}`}} aria-hidden="true"/><span>L{level}</span></button>)}
      {movementLevels.map((level) => <button key={`movement-level-${level}`} className={`chart-layer-toggle chart-level-toggle ${visible.movements && visible.movementLevels[String(level)] !== false ? "active" : ""}`} aria-label={`${visible.movementLevels[String(level)] !== false ? "隐藏" : "显示"}L${level}走势`} aria-pressed={visible.movements && visible.movementLevels[String(level)] !== false} title={`${visible.movementLevels[String(level)] !== false ? "隐藏" : "显示"}L${level}走势`} onClick={() => onLayerToggle("movements", level)}><i className="legend-swatch movement" style={{borderColor: levelStructureColor(theme === "light" ? "light" : "dark", data?.timeframe || "d", level), borderWidth: 3}} aria-hidden="true"/><span>L{level}</span></button>)}
    </div>}
    {movementsStale && <div className="drawing-preview" aria-live="polite">走势待重算</div>}
    {preview && <div className="drawing-preview" aria-live="polite">正在绘制 · {preview.end.trade_date} · {formatPrice(preview.end.price)}</div>}
    {contextMenu && <div className="drawing-context-menu" style={{left:contextMenu.x,top:contextMenu.y}} onClick={(event)=>event.stopPropagation()}>
      <button onClick={() => { if (contextMenu.drawing) onDrawingDelete?.(contextMenu.drawing.id); if (contextMenu.node) onStructureDelete?.(contextMenu.node); setContextMenu(null); }}>删除</button>
      <button onClick={() => setContextMenu(null)}>取消</button>
    </div>}
    {paneLayout.separators.map((separator, index) => {
      const upper = paneLayout.heights[index], lower = paneLayout.heights[index + 1];
      const pairTotal = upper + lower;
      const applyResize = (delta: number, heights = paneLayout.heights) => {
        const next = resizeAdjacentPanes(heights, index, delta, paneLayout.minHeights);
        setPaneRatios(next);
        return next;
      };
      return <div
        key={`pane-separator-${index}`}
        className="pane-resizer"
        style={{top: separator - 5}}
        role="separator"
        aria-label={`调整${index === 0 ? "主图与副图" : `副图${index}与副图${index + 1}`}高度`}
        aria-orientation="horizontal"
        aria-valuemin={Math.round(paneLayout.minHeights[index] / pairTotal * 100)}
        aria-valuemax={Math.round((pairTotal - paneLayout.minHeights[index + 1]) / pairTotal * 100)}
        aria-valuenow={Math.round(upper / pairTotal * 100)}
        tabIndex={0}
        title="拖动调整相邻面板高度，双击恢复默认"
        onPointerDown={(event) => {
          event.preventDefault(); event.stopPropagation();
          event.currentTarget.setPointerCapture(event.pointerId);
          paneResize.current = { pointerId: event.pointerId, separatorIndex: index, startY: event.clientY, heights: [...paneLayout.heights], ratios: paneLayout.ratios };
        }}
        onPointerMove={(event) => {
          const drag = paneResize.current;
          if (!drag || drag.pointerId !== event.pointerId || drag.separatorIndex !== index) return;
          drag.ratios = applyResize(event.clientY - drag.startY, drag.heights);
        }}
        onPointerUp={(event) => {
          const drag = paneResize.current;
          if (!drag || drag.pointerId !== event.pointerId) return;
          persistPaneRatios(drag.ratios);
          paneResize.current = null;
          event.currentTarget.releasePointerCapture(event.pointerId);
        }}
        onPointerCancel={() => {
          const drag = paneResize.current;
          if (drag) persistPaneRatios(drag.ratios);
          paneResize.current = null;
        }}
        onDoubleClick={(event) => {
          event.preventDefault(); event.stopPropagation();
          const next = defaultPaneRatios(activeSubplotCount);
          setPaneRatios(next); persistPaneRatios(next);
        }}
        onKeyDown={(event) => {
          if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
          event.preventDefault(); event.stopPropagation();
          const next = applyResize(event.key === "ArrowUp" ? -12 : 12);
          persistPaneRatios(next);
        }}
      ><span /></div>;
    })}
    {data && <div className="main-indicator-controls"><select className={data.timeframe === "1" ? "intraday-indicator-hidden" : ""} value={mainIndicator.mode} disabled={data.timeframe === "1"} onChange={(event)=>onMainIndicator?.(event.target.value as any)} aria-label="主图指标"><option value="none">不显示指标</option><option value="ma">MA</option><option value="boll">布林线</option><option value="pen_center">笔中枢</option></select><button title={data.timeframe === "1" ? "分时图设置" : "主图指标设置"} aria-label={data.timeframe === "1" ? "分时图设置" : "主图指标设置"} onClick={onOpenIndicatorSettings}><Settings size={15}/></button></div>}
    {subplotIndicators.map((indicator, index) => subplotVisible[index] && <label className={`indicator-select subplot-${index + 1}`} style={{top: paneLayout.tops[subplotVisible.slice(0, index).filter(Boolean).length + 1] + 4}} key={index}>
      <select value={indicator} onChange={(event) => onSubplotIndicator(index as 0 | 1, event.target.value as SubplotIndicator)}>
        <option value="macd">MACD(12,26,9)</option>
        <option value="volume">成交量</option>
        <option value="amount">成交额</option>
      </select>
    </label>)}
  </div>;
}

export function App() {
  const heightKey =
    innerWidth <= 760 ? "chan-chart-height-mobile" : "chan-chart-height";
  const [theme, setTheme] = useState(
    localStorage.getItem("chan-theme") || "dark",
  );
  const [stocks, setStocks] = useState<Stock[]>([]);
  const [watchlistGroups, setWatchlistGroups] = useState<WatchlistGroup[]>([]);
  const [memberships, setMemberships] = useState<WatchlistMembership[]>([]);
  const [watchlistLoaded, setWatchlistLoaded] = useState(false);
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>(() => {
    try { return JSON.parse(localStorage.getItem("chan-watchlist-collapsed-groups") || "{}") || {}; }
    catch { return {}; }
  });
  const [symbol, setSymbol] = useState("");
  const [timeframe, setTimeframe] = useState("1");
  const [moreOpen, setMoreOpen] = useState(false);
  const [data, setData] = useState<ChartData | null>(null);
  const [coverage, setCoverage] = useState<Coverage | null>(null);
  const [left, setLeft] = useState(localStorage.getItem("chan-left") !== "0");
  const [right, setRight] = useState(
    localStorage.getItem("chan-right") !== "0",
  );
  const [height, setHeight] = useState(
    Number(localStorage.getItem(heightKey)) ||
      Math.max(560, innerHeight - (innerWidth <= 760 ? 230 : 150)),
  );
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [repairing, setRepairing] = useState(false);
  const [selected, setSelected] = useState<Node | null>(null);
  const [selectedDrawing, setSelectedDrawing] = useState<Drawing | null>(null);
  useEffect(() => { setSelected(null); }, [symbol, timeframe]);
  useEffect(() => {
    if (!selected || !data) return;
    const nodes = [...(data.pens || []), ...(data.centers || data.pen_centers || []), ...(data.movements || [])];
    if (!nodes.some((node) => node.id === selected.id)) setSelected(null);
  }, [data, selected]);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [visible, setVisible] = useState<LayerVisibility>({
    pens: true,
    centers: true,
    movements: true,
    centerLevels: {},
    movementLevels: {},
  });
  const [subplotIndicators, setSubplotIndicators] = useState<SubplotIndicator[]>(["volume", "macd", "macd", "macd"]);
  const [subplotVisible, setSubplotVisible] = useState<SubplotVisibility>(() => {
    try { return JSON.parse(localStorage.getItem("chan-subplot-visible") || "[true,true]") as SubplotVisibility; } catch { return [true, true]; }
  });
  const [subplotCount, setSubplotCount] = useState(() => Math.max(1, Math.min(4, Number(localStorage.getItem(`chan-subplot-count-${symbol}-${timeframe}`) || 2))));
  useEffect(() => {
    if (!symbol) return;
    const count = Math.max(1, Math.min(4, Number(localStorage.getItem(`chan-subplot-count-${symbol}-${timeframe}`) || 2)));
    setSubplotCount(count); setSubplotVisible(Array.from({length:4}, (_, index) => index < count));
  }, [symbol, timeframe]);
  const defaultMainIndicator: MainIndicator = { mode: "pen_center", maPeriods: [5, 10, 20, 60], bollPeriod: 20, bollMultiplier: 2 };
  const [mainIndicator, setMainIndicator] = useState(defaultMainIndicator);
  const [indicatorSettingsOpen, setIndicatorSettingsOpen] = useState(false);
  const [draftMainIndicator, setDraftMainIndicator] = useState(defaultMainIndicator);
  const [draftSubplotCount, setDraftSubplotCount] = useState(2);
  const [showAdd, setShowAdd] = useState(false);
  const [addTargetGroupId, setAddTargetGroupId] = useState<number | null>(null);
  const [groupMenuId, setGroupMenuId] = useState<number | null>(null);
  const [membershipMenu, setMembershipMenu] = useState<{key:string;symbol:string} | null>(null);
  const [membershipUpdating, setMembershipUpdating] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<SecurityCandidate[]>([]);
  const [searching, setSearching] = useState(false);
  const [addingSymbol, setAddingSymbol] = useState("");
  const [deletingSymbol, setDeletingSymbol] = useState("");
  const [notice, setNotice] = useState<{kind: "success" | "error"; text: string} | null>(null);
  const [watchlistError, setWatchlistError] = useState("");
  const [pollingSymbols, setPollingSymbols] = useState<string[]>([]);
  const [drawingTool, setDrawingTool] = useState<"segment" | "line" | "rectangle" | null>(null);
  const [draftDrawings, setDraftDrawings] = useState<Drawing[]>([]);
  const [deletedDrawings, setDeletedDrawings] = useState<number[]>([]);
  const [structureOperations, setStructureOperations] = useState<Record<string, unknown>[]>([]);
  const drawingBaseline = useRef<Drawing[]>([]);
  const drawingHistory = useRef<Drawing[][]>([]);
  const drawingRedo = useRef<Drawing[][]>([]);
  const [drawingOpen, setDrawingOpen] = useState(false);
  const [dragStock, setDragStock] = useState<{symbol:string;viewKey:string;groupId:number|null} | null>(null);
  const [dragGroupId, setDragGroupId] = useState<number | null>(null);
  const loadingOlder = useRef(false);
  const loadSequence = useRef(0);
  const loadAbort = useRef<AbortController | null>(null);
  const nextBefore = useRef<string | undefined>(undefined);
  const pendingAdds = useRef(new Set<string>());
  const pendingDeletes = useRef(new Set<string>());
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("chan-theme", theme);
  }, [theme]);
  useEffect(() => {
    if (!notice || notice.kind === "error") return;
    const timer = window.setTimeout(() => setNotice(null), 2600);
    return () => window.clearTimeout(timer);
  }, [notice]);
  useEffect(() => {
    const adapt = () => {
      if (innerWidth <= 760) {
        setLeft(false);
        setRight(false);
      }
    };
    addEventListener("resize", adapt);
    adapt();
    return () => removeEventListener("resize", adapt);
  }, []);
  const refreshStocks = useCallback(async () => {
    const result = await api<WatchlistResponse>("/api/watchlist");
    setStocks(result.stocks);
    setWatchlistGroups(result.groups);
    setMemberships(result.memberships);
    setWatchlistLoaded(true);
    if (result.stocks.length) setSymbol((current) => current || result.stocks[0].symbol);
    return result.stocks;
  }, []);
  useEffect(() => { refreshStocks().catch((error) => setWatchlistError(error.message)); }, [refreshStocks]);
  useEffect(() => {
    if (!watchlistLoaded) return;
    setCollapsedGroups((current) => {
      const next: Record<string, boolean> = {
        all: current.all ?? false,
        ungrouped: current.ungrouped ?? true,
      };
      watchlistGroups.forEach((group) => {
        const key = `group-${group.id}`;
        next[key] = current[key] ?? true;
      });
      localStorage.setItem("chan-watchlist-collapsed-groups", JSON.stringify(next));
      return next;
    });
  }, [watchlistGroups, watchlistLoaded]);
  useEffect(() => {
    localStorage.setItem("chan-watchlist-collapsed-groups", JSON.stringify(collapsedGroups));
  }, [collapsedGroups]);
  useEffect(() => {
    if (!showAdd) return;
    const query = searchQuery.trim();
    if (query.length < 2 && !(query.length === 6 && /^\d+$/.test(query))) {
      setSearchResults([]);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      setSearching(true);
      setWatchlistError("");
      try {
        const result = await api<SecuritySearchResult>(`/api/securities/search?q=${encodeURIComponent(query)}&limit=20`);
        if (!cancelled) setSearchResults(result.items);
      } catch (error) {
        if (!cancelled) setWatchlistError((error as Error).message);
      } finally {
        if (!cancelled) setSearching(false);
      }
    }, 250);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [searchQuery, showAdd]);
  const load = useCallback(
    async (prepend = false) => {
      if (!symbol || (prepend && loadingOlder.current)) return;
      const requestId = ++loadSequence.current;
      loadAbort.current?.abort();
      const controller = new AbortController();
      loadAbort.current = controller;
      const requestSymbol = symbol;
      const requestTimeframe = timeframe;
      if (prepend) loadingOlder.current = true;
      else setLoading(true);
      const isCurrent = () => requestId === loadSequence.current
        && loadAbort.current === controller
        && !controller.signal.aborted
        && requestSymbol === symbol
        && requestTimeframe === timeframe;
      try {
        const q = new URLSearchParams({
          timeframe: requestTimeframe,
          adjustflag: "2",
          limit: "300",
          ma_periods: mainIndicator.maPeriods.join(","),
          boll_period: String(mainIndicator.bollPeriod),
          boll_multiplier: String(mainIndicator.bollMultiplier),
        });
        const cursor = nextBefore.current;
        if (prepend && cursor) q.set("before", cursor);
        const response = await api<ChartData>(`/api/chart-data/${encodeURIComponent(requestSymbol)}?${q}`, { signal: controller.signal });
        const fresh = normalizeChartData(response);
        if (!fresh) throw new Error("图表数据无效");
        if (!isCurrent()) return;
        setAnswer("");
        const centerLevels = (fresh.center_levels || []).map(Number).filter((level) => Number.isInteger(level) && level >= 1);
        const movementLevels = (fresh.movement_levels || []).map(Number).filter((level) => Number.isInteger(level) && level >= 1);
        if (!localStorage.getItem(`formal-structure-visible-${requestSymbol}-${requestTimeframe}`)) {
          setVisible((current) => ({
            ...current,
            pens: true,
            centers: true,
            movements: true,
            centerLevels: Object.fromEntries(centerLevels.map((level) => [String(level), true])),
            movementLevels: Object.fromEntries(movementLevels.map((level) => [String(level), true])),
          }));
          localStorage.setItem(`formal-structure-visible-${requestSymbol}-${requestTimeframe}`, "1");
        }
        setVisible((current) => {
          const nextCenterLevels = { ...current.centerLevels };
          const nextMovementLevels = { ...current.movementLevels };
          centerLevels.forEach((level) => { if (!(String(level) in nextCenterLevels)) nextCenterLevels[String(level)] = true; });
          movementLevels.forEach((level) => { if (!(String(level) in nextMovementLevels)) nextMovementLevels[String(level)] = true; });
          return { ...current, centerLevels: nextCenterLevels, movementLevels: nextMovementLevels };
        });
        nextBefore.current = fresh.next_before;
        setData((old) =>
          prepend && old
            ? {
                ...fresh,
                bars: unique([...fresh.bars, ...old.bars], (x) => x.trade_date).sort((a, b) => a.trade_date.localeCompare(b.trade_date)),
                pens: unique([...fresh.pens, ...old.pens], (x) => x.id),
                centers: scopeNodeOrdinals(unique([...(fresh.centers || fresh.pen_centers || []), ...(old.centers || old.pen_centers || [])], (x) => x.id)),
                pen_centers: scopeNodeOrdinals(unique([...(fresh.pen_centers || fresh.centers || []), ...(old.pen_centers || old.centers || [])], (x) => x.id)),
                movements: scopeNodeOrdinals(mergeMovements(fresh.movements || [], old.movements || [])),
                drawings: fresh.drawings || old.drawings,
                indicators: {
                  macd: unique([...fresh.indicators.macd, ...old.indicators.macd], (x) => x.trade_date).sort((a, b) => a.trade_date.localeCompare(b.trade_date)),
                  ma: unique([...(fresh.indicators.ma || []), ...(old.indicators.ma || [])], (x) => x.trade_date).sort((a, b) => a.trade_date.localeCompare(b.trade_date)),
                  boll: unique([...(fresh.indicators.boll || []), ...(old.indicators.boll || [])], (x) => x.trade_date).sort((a, b) => a.trade_date.localeCompare(b.trade_date)),
                },
              }
            : fresh,
        );
        if (!prepend) { const loaded = fresh.drawings || []; setDraftDrawings(loaded); drawingBaseline.current = loaded; setDeletedDrawings([]); setStructureOperations([]); drawingHistory.current=[]; drawingRedo.current=[]; }
        try {
          const coverageResult = await api<Coverage>(`/api/market-coverage/${encodeURIComponent(requestSymbol)}?timeframe=${requestTimeframe}&adjustflag=2`, { signal: controller.signal });
          if (isCurrent()) setCoverage(coverageResult);
        } catch (error) {
          if (!isAbortError(error) && isCurrent()) setCoverage(fresh.coverage || null);
        }
      } catch (error) {
        if (!isAbortError(error) && isCurrent()) setAnswer((error as Error).message);
      } finally {
        if (isCurrent()) {
          setLoading(false);
          loadingOlder.current = false;
          loadAbort.current = null;
        }
      }
    },
    [symbol, timeframe, mainIndicator],
  );
  useEffect(() => () => {
    // A symbol/timeframe switch must invalidate an in-flight response even if
    // the fetch implementation resolves after aborting. The request-id guard
    // then prevents stale chart data from being committed.
    loadSequence.current += 1;
    loadAbort.current?.abort();
    loadAbort.current = null;
    loadingOlder.current = false;
  }, [symbol, timeframe]);
  useEffect(() => {
    nextBefore.current = undefined;
    setSelected(null);
    const versionedLayerKey = `chan-layers-v2-${symbol}-${timeframe}`;
    const legacyLayerKey = `chan-layers-${symbol}-${timeframe}`;
    const formalStructureKey = `formal-structure-visible-${symbol}-${timeframe}`;
    const saved = localStorage.getItem(versionedLayerKey) || localStorage.getItem(legacyLayerKey);
    if (saved) {
      try {
        const parsed = JSON.parse(saved);
        const formalVisible = localStorage.getItem(formalStructureKey);
        const migrated = { pens: parsed.pens ?? true, centers: formalVisible === null ? (parsed.centers ?? true) : formalVisible !== "0", movements: formalVisible === null ? (parsed.movements ?? true) : formalVisible !== "0", centerLevels: parsed.centerLevels || {}, movementLevels: parsed.movementLevels || {} };
        setVisible(migrated);
        if (!localStorage.getItem(versionedLayerKey)) localStorage.setItem(versionedLayerKey, JSON.stringify(migrated));
      }
      catch { /* Ignore invalid legacy browser state. */ }
    } else setVisible({ pens: true, centers: true, movements: true, centerLevels: {}, movementLevels: {} });
    const indicatorKey = `chan-subplots-${symbol}-${timeframe}`;
    try {
      const savedIndicators = JSON.parse(localStorage.getItem(indicatorKey) || "null");
      setSubplotIndicators(
        Array.isArray(savedIndicators) && savedIndicators.length >= 1 && savedIndicators.length <= 4 &&
        savedIndicators.every((item) => item === "macd" || item === "volume" || item === "amount")
          ? [...savedIndicators, "macd", "macd", "macd"].slice(0, 4) as SubplotIndicator[] : ["volume", "macd", "macd", "macd"],
      );
    } catch { setSubplotIndicators(["volume", "macd", "macd", "macd"]); }
    try { const saved = JSON.parse(localStorage.getItem(`chan-main-indicator-${symbol}-${timeframe}`) || "null"); const next = saved && Array.isArray(saved.maPeriods) ? {...defaultMainIndicator, ...saved, maPeriods: saved.maPeriods.filter((x:number)=>Number.isInteger(x)&&x>=1&&x<=1000).slice(0,10)} : defaultMainIndicator; setMainIndicator((current) => JSON.stringify(current) === JSON.stringify(next) ? current : next); } catch { setMainIndicator((current) => JSON.stringify(current) === JSON.stringify(defaultMainIndicator) ? current : defaultMainIndicator); }
    load(false);
  }, [load]);
  const setLayer = (key: "pens" | "centers" | "movements", checked: boolean, level?: number) => {
    const next = level === undefined
      ? { ...visible, [key]: checked }
      : { ...visible, [key === "centers" ? "centerLevels" : "movementLevels"]: { ...(key === "centers" ? visible.centerLevels : visible.movementLevels), [String(level)]: checked } };
    setVisible(next);
    if (symbol) localStorage.setItem(`chan-layers-v2-${symbol}-${timeframe}`, JSON.stringify(next));
    if (symbol && (key === "centers" || key === "movements")) localStorage.setItem(`formal-structure-visible-${symbol}-${timeframe}`, next.centers && next.movements ? "1" : "0");
  };
  const createDraftDrawing = useCallback((item: Omit<Drawing, "id" | "symbol" | "timeframe">) => {
    setDraftDrawings((items) => { drawingHistory.current.push(items); drawingRedo.current=[]; return [...items, { ...item, id: -Date.now(), symbol, timeframe }]; });
  }, [symbol, timeframe]);
  const updateDraftDrawing = useCallback((drawing: Drawing) => {
    setDraftDrawings((items) => { drawingHistory.current.push(items); drawingRedo.current=[]; return items.map((item) => item.id === drawing.id ? drawing : item); });
    setSelectedDrawing((current) => current?.id === drawing.id ? drawing : current);
  }, []);
  const updateDrawingStyle = useCallback((style: DrawingStyle) => {
    if (!selectedDrawing) return;
    const next = {...selectedDrawing, style: {...normalizedDrawingStyle(selectedDrawing, theme), ...style}};
    updateDraftDrawing(next); setSelectedDrawing(next);
  }, [selectedDrawing, theme, updateDraftDrawing]);
  const resetDrawingStyle = useCallback(() => {
    if (!selectedDrawing) return;
    const next = {...selectedDrawing, style: defaultDrawingStyle(timeframe, theme, selectedDrawing.object_type === "rectangle")};
    updateDraftDrawing(next); setSelectedDrawing(next);
  }, [selectedDrawing, timeframe, theme, updateDraftDrawing]);
  const deleteDraftDrawing = useCallback((id: number) => {
    setDraftDrawings((items) => { drawingHistory.current.push(items); drawingRedo.current=[]; return items.filter((item) => item.id !== id); });
    if (id > 0) setDeletedDrawings((items) => items.includes(id) ? items : [...items, id]);
  }, []);
  const undoDrawing = useCallback(() => setDraftDrawings((items) => { const prev = drawingHistory.current.pop(); if (!prev) return items; drawingRedo.current.push(items); return prev; }), []);
  const redoDrawing = useCallback(() => setDraftDrawings((items) => { const next = drawingRedo.current.pop(); if (!next) return items; drawingHistory.current.push(items); return next; }), []);
  const saveDrawings = useCallback(async () => {
    if (!symbol) return;
    if (structureOperations.length) { setNotice({kind:"error", text:"人工结构修订暂时关闭"}); return; }
    const baseline = new Map(drawingBaseline.current.map((x) => [x.id, x]));
    const create = draftDrawings.filter((x) => x.id < 0).map(({id: _id,symbol: _s,timeframe: _t,...x}) => ({...x,timeframe}));
    const update = draftDrawings.filter((x) => x.id > 0 && JSON.stringify(x) !== JSON.stringify(baseline.get(x.id))).map(({id,...x}) => ({id,drawing:{...x,timeframe}}));
    try {
      const result = await api<{items:Drawing[];version:string}>(`/api/drawings/${symbol}/batch`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({timeframe,base_version:data?.drawings_version || "",create,update,delete_ids:deletedDrawings}) });
      drawingBaseline.current = result.items; setDraftDrawings(result.items); setDeletedDrawings([]); drawingHistory.current=[]; drawingRedo.current=[];
      setDrawingTool(null); await load(false); setNotice({ kind: "success", text: "绘图已保存" });
    } catch (error) { setNotice({ kind: "error", text: (error as Error).message }); }
  }, [draftDrawings, deletedDrawings, structureOperations, symbol, timeframe, load, data?.drawings_version, data?.run_id, data?.structure_version]);
  const cancelDrawings = useCallback(() => { setDraftDrawings(drawingBaseline.current); setDeletedDrawings([]); setStructureOperations([]); drawingHistory.current=[]; drawingRedo.current=[]; setDrawingTool(null); load(false); }, [load]);
  const stocksForGroup = (groupId: number) => watchlistStocksForGroup(stocks, memberships, groupId);
  const reorderStock = async (targetSymbol: string, viewKey: string, groupId: number | null) => {
    if (!dragStock || dragStock.viewKey !== viewKey || dragStock.symbol === targetSymbol) return;
    const source = groupId === null ? stocks : stocksForGroup(groupId);
    const from = source.findIndex((item) => item.symbol === dragStock.symbol);
    const to = source.findIndex((item) => item.symbol === targetSymbol);
    if (from < 0 || to < 0) return;
    const reordered = [...source];
    const [moving] = reordered.splice(from, 1);
    reordered.splice(to, 0, moving);
    const previousStocks = stocks;
    const previousMemberships = memberships;
    if (groupId === null) setStocks(reordered);
    else {
      const positions = new Map(reordered.map((item, index) => [item.symbol, index]));
      setMemberships((items) => items.map((item) => item.group_id === groupId
        ? {...item, sort_order: positions.get(item.symbol) ?? item.sort_order}
        : item));
    }
    try {
      if (groupId === null) {
        const saved = await api<Stock[]>("/api/stock-pool/order", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({symbols:reordered.map((item)=>item.symbol)}) });
        setStocks(saved);
      } else {
        await api(`/api/watchlist-groups/${groupId}/members/order`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({symbols:reordered.map((item)=>item.symbol)}) });
      }
    } catch (error) {
      setStocks(previousStocks);
      setMemberships(previousMemberships);
      setNotice({ kind: "error", text: `自选排序失败：${(error as Error).message}` });
    } finally {
      setDragStock(null);
    }
  };
  useEffect(() => {
    if (!drawingOpen) return;
    const key = (event: KeyboardEvent) => {
      const mod = event.metaKey || event.ctrlKey;
      if (mod && event.key.toLowerCase() === "z") { event.preventDefault(); event.shiftKey ? redoDrawing() : undoDrawing(); }
      else if (event.key === "Escape") { setDrawingTool(null); }
    };
    window.addEventListener("keydown", key); return () => window.removeEventListener("keydown", key);
  }, [drawingOpen, undoDrawing, redoDrawing]);
  const selectNode = useCallback((node: Node | null) => setSelected(node), []);
  const older = useCallback(() => load(true), [load]);
  const toggleLeft = () => setLeft((current) => {
    const next = !current;
    localStorage.setItem("chan-left", next ? "1" : "0");
    if (next && innerWidth <= 760) {
      setRight(false);
      localStorage.setItem("chan-right", "0");
    }
    return next;
  });
  const toggleRight = () => setRight((current) => {
    const next = !current;
    localStorage.setItem("chan-right", next ? "1" : "0");
    if (next && innerWidth <= 760) {
      setLeft(false);
      localStorage.setItem("chan-left", "0");
    }
    return next;
  });
  const setSubplotIndicator = (index: number, value: SubplotIndicator) => {
    const next = [...subplotIndicators];
    next[index] = value;
    setSubplotIndicators(next);
    if (symbol) localStorage.setItem(`chan-subplots-${symbol}-${timeframe}`, JSON.stringify(next));
  };
  const applySubplotCount = (count: number) => {
    const next = Math.max(1, Math.min(4, count));
    setSubplotCount(next); setSubplotVisible(Array.from({length:4}, (_, index) => index < next));
    localStorage.setItem(`chan-subplot-count-${symbol}-${timeframe}`, String(next));
  };
  const changeMainIndicator = (mode: "ma"|"boll"|"pen_center"|"none") => setMainIndicator((current) => { const next={...current,mode}; if(symbol) localStorage.setItem(`chan-main-indicator-${symbol}-${timeframe}`, JSON.stringify(next)); return next; });
  const openIndicatorSettings = () => { setDraftMainIndicator({...mainIndicator, maPeriods:[...mainIndicator.maPeriods]}); setDraftSubplotCount(subplotCount); setIndicatorSettingsOpen(true); };
  const applyIndicatorSettings = () => { const next={...draftMainIndicator, maPeriods:draftMainIndicator.maPeriods.filter((x)=>Number.isInteger(x)&&x>=1&&x<=1000).slice(0,10)}; if(!next.maPeriods.length) return; setMainIndicator(next); applySubplotCount(draftSubplotCount); localStorage.setItem(`chan-main-indicator-${symbol}-${timeframe}`, JSON.stringify(next)); setIndicatorSettingsOpen(false); };
  const openStockSearch = (groupId: number | null = null) => {
    setAddTargetGroupId(groupId);
    setShowAdd(true);
    setSearchQuery("");
    setSearchResults([]);
    setWatchlistError("");
  };
  const toggleGroup = (key: string) => setCollapsedGroups((current) => {
    const next = {...current, [key]: !current[key]};
    localStorage.setItem("chan-watchlist-collapsed-groups", JSON.stringify(next));
    return next;
  });
  const createGroup = async () => {
    const name = window.prompt("新建自选分组名称");
    if (name === null) return;
    try {
      const group = await api<WatchlistGroup>("/api/watchlist-groups", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({name})});
      await refreshStocks();
      setCollapsedGroups((current) => ({...current, [`group-${group.id}`]: false}));
      setNotice({kind:"success", text:`已创建分组“${group.name}”`});
    } catch (error) { setNotice({kind:"error", text:(error as Error).message}); }
  };
  const renameGroup = async (group: WatchlistGroup) => {
    const name = window.prompt("重命名自选分组", group.name);
    if (name === null) return;
    try {
      await api(`/api/watchlist-groups/${group.id}`, {method:"PATCH", headers:{"Content-Type":"application/json"}, body:JSON.stringify({name})});
      await refreshStocks();
      setGroupMenuId(null);
    } catch (error) { setNotice({kind:"error", text:(error as Error).message}); }
  };
  const deleteGroup = async (group: WatchlistGroup) => {
    if (!window.confirm(`确认删除分组“${group.name}”？股票仍会保留在自选中。`)) return;
    try {
      await api(`/api/watchlist-groups/${group.id}`, {method:"DELETE"});
      await refreshStocks();
      setGroupMenuId(null);
      setNotice({kind:"success", text:`已删除分组“${group.name}”`});
    } catch (error) { setNotice({kind:"error", text:(error as Error).message}); }
  };
  const reorderGroup = async (targetId: number) => {
    if (dragGroupId === null || dragGroupId === targetId) return;
    const from = watchlistGroups.findIndex((group) => group.id === dragGroupId);
    const to = watchlistGroups.findIndex((group) => group.id === targetId);
    if (from < 0 || to < 0) return;
    const previous = watchlistGroups;
    const reordered = [...watchlistGroups];
    const [moving] = reordered.splice(from, 1);
    reordered.splice(to, 0, moving);
    setWatchlistGroups(reordered.map((group, index) => ({...group, sort_order:index})));
    try {
      const saved = await api<WatchlistGroup[]>("/api/watchlist-groups/order", {method:"PUT", headers:{"Content-Type":"application/json"}, body:JSON.stringify({group_ids:reordered.map((group)=>group.id)})});
      setWatchlistGroups(saved);
    } catch (error) {
      setWatchlistGroups(previous);
      setNotice({kind:"error", text:`分组排序失败：${(error as Error).message}`});
    } finally { setDragGroupId(null); }
  };
  const toggleMembership = async (groupId: number, stockSymbol: string, selected: boolean) => {
    if (membershipUpdating) return;
    setMembershipUpdating(true);
    try {
      await api(`/api/watchlist-groups/${groupId}/members/${encodeURIComponent(stockSymbol)}`, {method:selected ? "PUT" : "DELETE"});
      await refreshStocks();
    } catch (error) { setNotice({kind:"error", text:(error as Error).message}); }
    finally { setMembershipUpdating(false); }
  };
  const openOrAddStock = async (candidate: SecurityCandidate) => {
    const alreadyInTarget = addTargetGroupId === null || memberships.some((item) => item.group_id === addTargetGroupId && item.symbol === candidate.symbol);
    if (candidate.selected && alreadyInTarget) {
      setSymbol(candidate.symbol);
      setShowAdd(false);
      setSearchQuery("");
      setSearchResults([]);
      return;
    }
    if (pendingAdds.current.has(candidate.symbol)) return;
    pendingAdds.current.add(candidate.symbol);
    setAddingSymbol(candidate.symbol);
    setWatchlistError("");
    try {
      let added: Stock & {already_selected?: boolean};
      if (candidate.selected && addTargetGroupId !== null) {
        await api(`/api/watchlist-groups/${addTargetGroupId}/members/${encodeURIComponent(candidate.symbol)}`, {method:"PUT"});
        added = stocks.find((item) => item.symbol === candidate.symbol) || {symbol:candidate.symbol,name:candidate.name};
      } else {
        added = await api<Stock & {already_selected?: boolean}>("/api/stock-pool", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ symbol: candidate.symbol, ...(addTargetGroupId === null ? {} : {group_id:addTargetGroupId}) }),
        });
      }
      await refreshStocks();
      setSymbol(candidate.symbol);
      if (!candidate.selected && !added.already_selected) setPollingSymbols((items) => unique([...items, added.symbol], (item) => item));
      setShowAdd(false);
      setSearchQuery("");
      setSearchResults([]);
      const target = watchlistGroups.find((group) => group.id === addTargetGroupId)?.name;
      setNotice({ kind: "success", text: `${added.name || candidate.name} 已加入${target ? `“${target}”` : "自选"}` });
    } catch (error) {
      setWatchlistError((error as Error).message);
    } finally {
      pendingAdds.current.delete(candidate.symbol);
      setAddingSymbol("");
    }
  };
  const removeStock = async (stock: Stock, groupId: number | null = null) => {
    if (pendingDeletes.current.has(stock.symbol)) return;
    if (groupId !== null) {
      const group = watchlistGroups.find((item) => item.id === groupId);
      try {
        await api(`/api/watchlist-groups/${groupId}/members/${encodeURIComponent(stock.symbol)}`, {method:"DELETE"});
        await refreshStocks();
        setNotice({kind:"success", text:`${stock.name || stock.symbol} 已移出“${group?.name || "分组"}”`});
      } catch (error) { setNotice({kind:"error", text:(error as Error).message}); }
      return;
    }
    if (!window.confirm(`确认从自选中删除“${stock.name || stock.symbol}”？`)) return;
    pendingDeletes.current.add(stock.symbol);
    setDeletingSymbol(stock.symbol);
    try {
      await api(`/api/stock-pool/${encodeURIComponent(stock.symbol)}`, { method: "DELETE" });
      const index = stocks.findIndex((item) => item.symbol === stock.symbol);
      const remaining = stocks.filter((item) => item.symbol !== stock.symbol);
      setStocks(remaining);
      setMemberships((items) => items.filter((item) => item.symbol !== stock.symbol));
      setSearchResults((items) => items.map((item) => item.symbol === stock.symbol ? { ...item, selected: false } : item));
      setPollingSymbols((items) => items.filter((item) => item !== stock.symbol));
      if (symbol === stock.symbol) {
        const adjacent = stocks[index + 1] || stocks[index - 1];
        setSymbol(adjacent?.symbol || "");
        if (!adjacent) {
          setData(null);
          setCoverage(null);
          setSelected(null);
          nextBefore.current = undefined;
        }
      }
      setNotice({ kind: "success", text: `${stock.name || stock.symbol} 已从自选删除` });
    } catch (error) {
      setNotice({ kind: "error", text: (error as Error).message });
    } finally {
      pendingDeletes.current.delete(stock.symbol);
      setDeletingSymbol("");
    }
  };
  useEffect(() => {
    if (!pollingSymbols.length) return;
    const timer = window.setInterval(async () => {
      try {
        const items = await refreshStocks();
        const active = pollingSymbols.filter((code) => {
          const status = items.find((item) => item.symbol === code)?.sync_status;
          return !status || status === "running";
        });
        setPollingSymbols(active);
        if (!active.length && pollingSymbols.includes(symbol)) await load(false);
      } catch { /* The next poll will retry. */ }
    }, 2500);
    return () => window.clearInterval(timer);
  }, [pollingSymbols, refreshStocks, symbol, load]);
  const ask = async () => {
    if (!symbol) return;
    setLoading(true);
    try {
      const result: any = await api("/api/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          symbol,
          timeframes: [timeframe],
          adjustflag: "2",
          question,
        }),
      });
      setAnswer(
        (result.timeframes?.[timeframe] || result).explanation?.text ||
          "规则结构已刷新",
      );
    } catch (e) {
      setAnswer((e as Error).message);
    } finally {
      setLoading(false);
    }
  };
  const sync = async () => {
    if (!symbol || syncing) return;
    setSyncing(true);
    try {
      await api(`/api/stock-pool/${symbol}/sync`, { method: "POST" });
      setPollingSymbols((items) => unique([...items, symbol], (item) => item));
      setNotice({ kind: "success", text: "已提交全周期行情同步" });
    } catch (error) {
      setNotice({ kind: "error", text: `行情同步失败：${(error as Error).message}` });
    } finally {
      setSyncing(false);
    }
  };
  const repair = async () => {
    if (!symbol || repairing) return;
    setRepairing(true);
    try {
      const result = await api<any>(
        `/api/market-data/${symbol}/repair?timeframe=${timeframe}&adjustflag=2`,
        { method: "POST" },
      );
      setAnswer(result.message || "缺口扫描与补齐任务已提交。");
      await load(false);
    } catch (e) {
      setAnswer((e as Error).message);
    } finally {
      setRepairing(false);
    }
  };
  const resizeStart = (e: React.PointerEvent) => {
    const start = e.clientY,
      initial = height,
      max = innerWidth <= 760 ? 900 : 1200;
    let next = initial;
    const move = (x: PointerEvent) => {
      next = Math.max(560, Math.min(max, initial + x.clientY - start));
      setHeight(next);
    };
    const up = () => {
      localStorage.setItem(
        innerWidth <= 760 ? "chan-chart-height-mobile" : "chan-chart-height",
        String(next),
      );
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  const last = data?.bars.at(-1),
    quote = data?.quote,
    prev = timeframe === "1" && data?.previous_close ? { close: data.previous_close } : data?.bars.at(-2),
    change = quote?.change_pct ?? (last && prev && Number.isFinite(last.close) && Number.isFinite(prev.close) && prev.close !== 0
      ? (last.close / prev.close - 1) * 100
      : null),
    changeAmount = quote?.change ?? (last && prev && Number.isFinite(last.close) && Number.isFinite(prev.close)
      ? last.close - prev.close
      : null),
    quoteTone = change === null || change === 0 ? "" : change > 0 ? "rise" : "fall",
    formattedChange = change === null ? "--" : `${change >= 0 ? "+" : ""}${change.toFixed(2)}%`;
  const ungroupedStocks = watchlistUngroupedStocks(stocks, memberships);
  const renderStockRows = (items: Stock[], viewKey: string, groupId: number | null, canReorder: boolean) => items.map((stock) => {
    const menuKey = `${viewKey}:${stock.symbol}`;
    return <div key={menuKey} draggable={canReorder} className={`stock-row ${symbol === stock.symbol ? "active" : ""}`}
      onDragStart={() => canReorder && setDragStock({symbol:stock.symbol,viewKey,groupId})}
      onDragOver={(event) => { if (canReorder) event.preventDefault(); }}
      onDrop={(event) => { event.stopPropagation(); if (canReorder) reorderStock(stock.symbol, viewKey, groupId); }}>
      <button className="stock" onClick={() => setSymbol(stock.symbol)}>
        <span>
          <b>{stock.name || "未命名"}</b>
          <small>{stock.symbol}</small>
          {syncLabel(stock.sync_status) && <small className={`sync-${stock.sync_status}`}>{syncLabel(stock.sync_status)}</small>}
        </span>
        <span className={(stock.market?.change_pct || 0) >= 0 ? "rise" : "fall"}>
          {formatPrice(stock.market?.latest)}
          <small>{stock.market?.change_pct?.toFixed(2) || "--"}%</small>
        </span>
      </button>
      <button className="manage-memberships" aria-label={`管理分组：${stock.name || stock.symbol}`} title="管理分组"
        onClick={() => setMembershipMenu((current) => current?.key === menuKey ? null : {key:menuKey,symbol:stock.symbol})}>
        <Folder size={14}/>
      </button>
      <button className="delete-stock" disabled={deletingSymbol === stock.symbol}
        aria-label={`${groupId === null ? "删除自选" : "移出分组"}：${stock.name || stock.symbol}`}
        title={groupId === null ? "删除自选" : "移出当前分组"}
        onClick={() => removeStock(stock, groupId)}><Trash2 size={15}/></button>
      {membershipMenu?.key === menuKey && <div className="membership-menu" onClick={(event) => event.stopPropagation()}>
        <b>所属分组</b>
        {!watchlistGroups.length && <small>暂无自定义分组</small>}
        {watchlistGroups.map((group) => {
          const checked = memberships.some((item) => item.group_id === group.id && item.symbol === stock.symbol);
          return <label key={group.id}><input type="checkbox" checked={checked} disabled={membershipUpdating}
            onChange={(event) => toggleMembership(group.id, stock.symbol, event.target.checked)}/><span>{group.name}</span></label>;
        })}
      </div>}
    </div>;
  });
  const renderSystemSection = (key: "all"|"ungrouped", label: string, items: Stock[]) => <section className="watchlist-section" key={key}>
    <div className="watchlist-group-head">
      <button className="group-toggle" onClick={() => toggleGroup(key)} aria-expanded={!collapsedGroups[key]}>
        {collapsedGroups[key] ? <ChevronRight size={15}/> : <ChevronDown size={15}/>}<b>{label}</b><small>{items.length}</small>
      </button>
      {key === "all" && <button className="icon group-add-stock" title="添加自选" aria-label="添加自选" onClick={() => openStockSearch(null)}><Plus size={14}/></button>}
    </div>
    {!collapsedGroups[key] && <div className="group-stock-list">
      {!items.length && <p className="stock-list-empty">暂无股票</p>}
      {renderStockRows(items, key, null, key === "all")}
    </div>}
  </section>;
  return (
    <div className="app">
      <header>
        <div className="brand">
          <ChartCandlestick aria-hidden="true" />
          <span>缠论分析工作台</span>
        </div>
        <div className="header-actions">
          <button
            className="icon"
            title="切换主题"
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
          >
            {theme === "dark" ? <Sun /> : <Moon />}
          </button>
        </div>
      </header>
      {notice && <div className={`notice notice-${notice.kind}`} role={notice.kind === "error" ? "alert" : "status"}>
        <span>{notice.text}</span>
        {notice.kind === "error" && <button className="icon" aria-label="关闭提示" title="关闭提示" onClick={() => setNotice(null)}><X size={15} /></button>}
      </div>}
      <div
        className={`workspace ${left ? "" : "left-off"} ${right ? "" : "right-off"}`}
      >
        <aside className="leftbar">
          <div className="panel-head">
            <b>自选</b>
            <div className="watchlist-head-actions">
              <button className="icon" title="新建分组" aria-label="新建分组" onClick={createGroup}><FolderPlus size={17}/></button>
              <button className="icon" title={showAdd ? "关闭搜索" : "添加自选"} aria-label={showAdd ? "关闭搜索" : "添加自选"}
                onClick={() => showAdd ? setShowAdd(false) : openStockSearch(null)}>{showAdd ? <X size={17}/> : <Plus size={17}/>}</button>
            </div>
          </div>
          {showAdd && <div className="watchlist-search">
            <div className="search-target">加入 {watchlistGroups.find((group) => group.id === addTargetGroupId)?.name || "自选"}</div>
            <label>
              <Search size={14} />
              <input autoFocus value={searchQuery} onChange={(event) => setSearchQuery(event.target.value)} placeholder="输入代码或名称" />
            </label>
            <div className="security-results">
              {searching ? <p>正在搜索...</p> : searchResults.map((candidate) => <button
                key={candidate.market_code}
                className={addingSymbol === candidate.symbol ? "loading" : ""}
                disabled={Boolean(addingSymbol)}
                onClick={() => openOrAddStock(candidate)}
              >
                <span><b>{candidate.name}</b><small>{candidate.symbol} · {candidate.market}</small></span>
                <em>{addingSymbol === candidate.symbol ? "加入中" : candidate.selected
                  ? (addTargetGroupId !== null && !memberships.some((item) => item.group_id === addTargetGroupId && item.symbol === candidate.symbol) ? "加入分组" : "已添加")
                  : ""}</em>
              </button>)}
              {!searching && searchQuery.trim().length >= 2 && !searchResults.length && !watchlistError && <p>未找到匹配证券</p>}
            </div>
            {watchlistError && <p className="watchlist-error">{watchlistError}</p>}
          </div>}
          <div className="stock-list watchlist-tree">
            {renderSystemSection("all", "全部", stocks)}
            {renderSystemSection("ungrouped", "未分组", ungroupedStocks)}
            {watchlistGroups.map((group) => {
              const key = `group-${group.id}`;
              const items = stocksForGroup(group.id);
              const isCollapsed = collapsedGroups[key] ?? true;
              return <section className="watchlist-section custom-group" key={group.id} draggable
                onDragStart={(event) => { if ((event.target as HTMLElement).closest(".stock-row")) return; setDragGroupId(group.id); }}
                onDragOver={(event) => event.preventDefault()} onDrop={() => reorderGroup(group.id)}>
                <div className="watchlist-group-head">
                  <button className="group-toggle" onClick={() => toggleGroup(key)} aria-expanded={!isCollapsed}>
                    {isCollapsed ? <ChevronRight size={15}/> : <ChevronDown size={15}/>}<Folder className="group-folder" size={14}/><b title={group.name}>{group.name}</b><small>{items.length}</small>
                  </button>
                  <div className="group-actions">
                    <button className="icon" title={`添加到${group.name}`} aria-label={`添加到${group.name}`} onClick={() => openStockSearch(group.id)}><Plus size={14}/></button>
                    <button className="icon" title="分组菜单" aria-label={`${group.name}分组菜单`} onClick={() => setGroupMenuId((current) => current === group.id ? null : group.id)}><MoreVertical size={15}/></button>
                    {groupMenuId === group.id && <div className="group-menu">
                      <button onClick={() => renameGroup(group)}><Pencil size={14}/>重命名</button>
                      <button className="danger" onClick={() => deleteGroup(group)}><Trash2 size={14}/>删除分组</button>
                    </div>}
                  </div>
                </div>
                {!isCollapsed && <div className="group-stock-list">
                  {!items.length && <p className="stock-list-empty">暂无股票</p>}
                  {renderStockRows(items, key, group.id, true)}
                </div>}
              </section>;
            })}
          </div>
        </aside>
        <button
          className="rail left-rail"
          title={left ? "折叠自选栏" : "展开自选栏"}
          aria-label={left ? "折叠自选栏" : "展开自选栏"}
          aria-expanded={left}
          onClick={toggleLeft}
        >
          {left ? <ChevronLeft /> : <ChevronRight />}
        </button>
        <main>
          <div className="toolbar">
            <div className="identity">
              <b>
                {stocks.find((x) => x.symbol === symbol)?.name ||
                  symbol ||
                  "请选择股票"}
              </b>
              <small>{symbol}</small>
              {quote?.market_status && <span className={`market-status status-${quote.market_status}`}>{quote.market_status}</span>}
            </div>
            <div className="segmented period-selector" aria-label="行情周期">
              {periods.map(([v, n]) => (
                <button
                  className={timeframe === v ? "active" : ""}
                  onClick={() => {
                    setTimeframe(v);
                    setMoreOpen(false);
                  }}
                  key={v}
                >
                  {n}
                </button>
              ))}
              <div className="more-periods">
                <button className={morePeriods.some(([v]) => v === timeframe) ? "active" : ""} onClick={() => setMoreOpen((open) => !open)} aria-expanded={moreOpen}>
                  {morePeriods.some(([v]) => v === timeframe) ? periodLabels[timeframe] : "更多"}<ChevronDown size={13} />
                </button>
                {moreOpen && <div className="more-period-menu">
                  {morePeriods.map(([v, n]) => <button key={v} className={timeframe === v ? "active" : ""} onClick={() => { setTimeframe(v); setMoreOpen(false); }}>{n}</button>)}
                </div>}
              </div>
            </div>
            <div className={`drawing-tools ${drawingOpen ? "open" : ""}`}>
              <button className="drawing-toggle" title={drawingOpen ? "退出画图模式" : "打开画图模式"} onClick={() => { setDrawingOpen((v) => !v); setDrawingTool(null); }}><Pencil size={15} /><span>{drawingOpen ? "退出" : "画图"}</span></button>
              {drawingOpen && <>
                <button className={drawingTool === null ? "active" : ""} title="选择对象" onClick={() => setDrawingTool(null)}><MousePointer2 size={15} /></button>
                <button className={drawingTool === "segment" ? "active" : ""} title="画线段" onClick={() => setDrawingTool("segment")}><Minus size={15} /></button>
                <button className={drawingTool === "line" ? "active" : ""} title="画直线" onClick={() => setDrawingTool("line")}><span className="infinite-icon">↔</span></button>
                <button className={drawingTool === "rectangle" ? "active" : ""} title="画矩形" onClick={() => setDrawingTool("rectangle")}><Square size={15} /></button>
                <i className="tool-separator" />
                <button title="撤销（Ctrl/Cmd+Z）" onClick={undoDrawing}><Undo2 size={15} /></button>
                <button title="重做（Ctrl/Cmd+Shift+Z）" onClick={redoDrawing}><Redo2 size={15} /></button>
                <button title="保存全部修改" onClick={saveDrawings}><Save size={15} /></button><button title="取消编辑" onClick={cancelDrawings}><X size={15} /></button>
              </>}
            </div>
            <button className={`command sync-command ${syncing ? "is-syncing" : ""}`} onClick={sync} disabled={!symbol || syncing} title="同步当前股票的全部周期行情">
              <RefreshCw size={15} />
              {syncing ? "提交中" : "同步行情"}
            </button>
          </div>
          <div className="quote">
            <div className="quote-summary">
              <span className="quote-date">{quote?.trade_date || last?.trade_date || "暂无行情"}</span>
              {(quote || last) && <>
                <strong className={`quote-last ${quoteTone}`}>{formatPrice(quote?.latest ?? last?.close)}</strong>
                <span className={`quote-change-amount ${quoteTone}`}>{changeAmount === null ? "--" : `${changeAmount >= 0 ? "+" : ""}${formatPrice(changeAmount)}`}</span>
                <span className={`quote-change ${quoteTone}`}><small>涨跌幅</small>{formattedChange}</span>
              </>}
            </div>
            {(quote || last) && <div className="quote-details">
              <span>昨收：<b>{formatPrice(quote?.previous_close)}</b></span>
              <span>今开：<b>{formatPrice(quote?.open ?? last?.open)}</b></span>
              <span>最高：<b className="rise">{formatPrice(quote?.high ?? last?.high)}</b></span>
              <span>最低：<b className="fall">{formatPrice(quote?.low ?? last?.low)}</b></span>
              <span>成交量：<b>{formatVolume(quote?.volume ?? last?.volume)}</b></span>
              <span>成交额：<b>{formatCompactNumber(quote?.amount ?? last?.amount)}</b></span>
              <span>振幅：<b>{quote?.amplitude_pct == null ? "--" : `${quote.amplitude_pct.toFixed(2)}%`}</b></span>
            </div>}
          </div>
          {!data?.available && (
            <div className="gate">
              结构不可用：{data?.stale_reason || "等待本周期行情与结构同步"}
            </div>
          )}
          {((coverage || data?.coverage)?.missing_sessions?.length || 0) >
            0 && (
            <div className="coverage-warning">
              <span>
                缺少 {(coverage || data?.coverage)?.missing_sessions?.length}{" "}
                个周期单位，本周期结构已隔离为{" "}
                {(coverage || data?.coverage)?.continuous_ranges?.length || 0}{" "}
                个连续区间。
              </span>
              <button
                className="text-command"
                onClick={repair}
                disabled={repairing}
              >
                <RefreshCw size={13} />
                {repairing ? "扫描中" : "扫描并补齐缺口"}
              </button>
            </div>
          )}
          <Chart
            data={data}
            theme={theme}
            onOlder={older}
            onSelect={selectNode}
            selectedStructureId={selected?.id || null}
            height={height}
            visible={visible}
            subplotIndicators={subplotIndicators}
            subplotVisible={subplotVisible}
            mainIndicator={mainIndicator}
            onMainIndicator={changeMainIndicator}
            onOpenIndicatorSettings={openIndicatorSettings}
            onSubplotIndicator={setSubplotIndicator}
            onLayerToggle={(key, level) => level === undefined ? setLayer(key, !visible[key]) : setLayer(key, !(key === "centers" ? visible.centerLevels[String(level)] !== false : visible.movementLevels[String(level)] !== false), level)}
            drawingTool={drawingTool}
            onDrawingCreate={createDraftDrawing}
            drawingItems={draftDrawings}
            drawingOpen={drawingOpen}
            onDrawingUpdate={updateDraftDrawing}
            onDrawingDelete={deleteDraftDrawing}
            onDrawingSelect={setSelectedDrawing}
            onStructureUpdate={undefined}
            onStructureDelete={undefined}
            movementsStale={structureOperations.length > 0}
          />
          <div
            className="height-rail"
            onPointerDown={resizeStart}
            onDoubleClick={() => setHeight(Math.max(430, innerHeight - 150))}
          >
            <span />
          </div>
        </main>
        <button
          className="rail right-rail"
          title={right ? "折叠详情栏" : "展开详情栏"}
          aria-label={right ? "折叠详情栏" : "展开详情栏"}
          aria-expanded={right}
          onClick={toggleRight}
        >
          {right ? <ChevronRight /> : <ChevronLeft />}
        </button>
        <aside className="rightbar">
          <section>
            <div className="panel-head">
              <b>周期结构快照</b>
              <Maximize2 size={15} />
            </div>
            <dl>
              <dt>规则版本</dt>
              <dd>{data?.definition_version || "--"}</dd>
              <dt>活动版本</dt>
              <dd>{data?.structure_version || "--"}</dd>
              <dt>本周期覆盖</dt>
              <dd>
                {data?.coverage?.range_start?.slice(0, 10) || "--"}
                <br />至 {data?.coverage?.range_end?.slice(0, 10) || "--"}
              </dd>
              <dt>当前周期</dt>
              <dd>{periodLabels[timeframe] || timeframe}</dd>
              <dt>结构数量</dt>
              <dd>
                {data?.pens.length || 0} 正式笔 · {(data?.centers || data?.pen_centers || []).filter((item) => visible.centers && (item.role === undefined || item.role === "hierarchy") && visible.centerLevels[String(nodeLevel(item))] !== false).length} 正式中枢 · {(data?.movements || []).filter((item) => visible.movements && item.role === "hierarchy_component" && visible.movementLevels[String(nodeLevel(item))] !== false).length} 正式走势
              </dd>
            </dl>
          </section>
          <section>
            <div className="panel-head">
              <b>证据检查器</b>
              <Minimize2 size={15} />
            </div>
            {selected ? (
              <div className="evidence">
                <div className="selected-structure-summary">
                  <strong>{kindName(selected.kind)} #{selected.ordinal + 1}</strong>
                  <span>{selected.direction === "up" ? "向上" : selected.direction === "down" ? "向下" : selected.status}</span>
                </div>
                <h3>
                  {kindName(selected.kind)} #{selected.ordinal + 1}
                </h3>
                <p>
                  {selected.start_date}
                  <br />至 {selected.end_date}
                </p>
                <dl>
                  <dt>状态</dt>
                  <dd>{selected.status}</dd>
                  {isCenterNode(selected) && <>
                    {(() => { const appearance = levelStructureAppearance(theme === "light" ? "light" : "dark", timeframe, nodeLevel(selected)); return <>
                    <dt>结构级别</dt><dd>L{nodeLevel(selected)}</dd>
                    <dt>配色参考（非结构周期）</dt><dd>{displayPeriodLabel(selected.display_period || appearance.displayPeriod)}</dd>
                    <dt>计算来源</dt><dd>{calculationSourceLabel(timeframe)}</dd>
                    <dt>语义对应</dt><dd>{centerSemanticLabel(timeframe, nodeLevel(selected), selected.display_period)}</dd>
                    <dt>颜色标识</dt><dd>{selected.color_key || appearance.colorKey}</dd>
                    </>; })()}
                  </>}
                  {selected.direction && <><dt>方向</dt><dd>{selected.direction === "up" ? "向上" : "向下"}</dd></>}
                  {selected.kind === "movement" && <><dt>走势类型</dt><dd>{selected.classification === "trend" ? "趋势" : "盘整"}</dd></>}
                  {selected.kind === "movement" && <><dt>结构来源</dt><dd>正式层级</dd></>}
                  {selected.kind === "movement" && <><dt>参考中枢类型</dt><dd>正式层级中枢</dd></>}
                  {selected.kind === "movement" && <><dt>参考中枢 ID</dt><dd>{selected.center_ids?.length ? selected.center_ids.join(" / ") : "--"}</dd></>}
                  {selected.kind === "movement" && <><dt>实际边界</dt><dd>{formatPrice(selected.start_price)} → {formatPrice(selected.end_price)}</dd></>}
                  {selected.kind === "movement" && selected.confirmation_center_id && <><dt>确认中枢</dt><dd>{selected.confirmation_center_id}</dd></>}
                  {selected.kind === "movement" && selected.candidate_extreme_date && <><dt>候选极值</dt><dd>{selected.candidate_extreme_date}<br />{formatPrice(selected.candidate_extreme_price)}</dd></>}
                  {selected.entry_pen_id && <><dt>进入笔</dt><dd>{selected.entry_pen_id}</dd></>}
                  {!!selected.core_pen_ids?.length && <><dt>核心三笔</dt><dd>{selected.core_pen_ids.join(" / ")}</dd></>}
                  {selected.core_start_date && <><dt>核心范围</dt><dd>{selected.core_start_date}<br />至 {selected.core_end_date}</dd></>}
                  {selected.extension_end_date && <><dt>延伸至</dt><dd>{selected.extension_end_date}</dd></>}
                  {!!selected.extension_pen_ids?.length && <><dt>延伸笔</dt><dd>{selected.extension_pen_ids.length}</dd></>}
                  {!!selected.peripheral_pen_ids?.length && <><dt>外围笔</dt><dd>{selected.peripheral_pen_ids.length}</dd></>}
                  {!!selected.departure_pen_ids?.length && <><dt>离开候选</dt><dd>{selected.departure_pen_ids.length}</dd></>}
                  {selected.tail_status && <><dt>尾部状态</dt><dd>{selected.tail_status}</dd></>}
                  {selected.zd !== undefined && <><dt>ZD / ZG</dt><dd>{formatPrice(selected.zd)} / {formatPrice(selected.zg)}</dd></>}
                  {selected.dd !== undefined && <><dt>DD / GG</dt><dd>{formatPrice(selected.dd)} / {formatPrice(selected.gg)}</dd></>}
                  <dt>确认时间</dt>
                  <dd>{selected.confirmed_at || "等待反向中枢"}</dd>
                  {(selected.completion_reason || selected.termination_reason) && <><dt>完成依据</dt><dd>{selected.completion_reason || selected.termination_reason}</dd></>}
                  <dt>构成笔</dt>
                  <dd>{selected.source_pen_ids?.length || selected.pen_ids?.length || selected.child_ids?.length || 0}</dd>
                  {selected.kind === "movement" && <><dt>构成单位</dt><dd>{selected.source_unit_ids?.length ? selected.source_unit_ids.join(" / ") : "--"}</dd></>}
                  {selected.continuous_range_id !== undefined && <><dt>连续区间</dt><dd>R{selected.continuous_range_id}</dd></>}
                </dl>
                <p className="rules">{selected.evidence?.join(" · ")}</p>
              </div>
            ) : (
              <p className="empty">点击笔、中枢或走势查看规则证据</p>
            )}
          </section>
          {!!data?.pen_diagnostics?.length && (
            <section>
              <div className="panel-head"><b>成笔候选诊断</b></div>
              <div className="evidence">
                {data.pen_diagnostics.slice(-5).map((item, index) => (
                  <p className="rules" key={`${item.start_date}-${item.end_date}-${index}`}>
                    {item.direction === "up" ? "向上" : "向下"}候选 {item.start_date} → {item.end_date}：
                    {item.reason === "reverse_extreme_not_broken" ? "未突破起点右侧反向极值" : item.reason}
                    {item.start_right?.extreme !== undefined && `（起点极值 ${item.start_right.extreme}，候选 ${item.candidate_extreme}）`}
                  </p>
                ))}
              </div>
            </section>
          )}
          {drawingOpen && selectedDrawing && (
            <section className="drawing-style-panel">
              <div className="panel-head"><b>图形样式</b><Pencil size={15} /></div>
              {(() => { const style = normalizedDrawingStyle(selectedDrawing, theme); const set = (patch: DrawingStyle) => updateDrawingStyle(patch); return <div className="style-editor">
                <label><span>线条颜色</span><span className="color-field"><input type="color" value={(style.color || "#42b8d4").slice(0,7)} onChange={(e)=>set({color:e.target.value})}/><input className="hex-input" value={style.color || ""} pattern="^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$" onChange={(e)=>{if(/^#[0-9a-fA-F]{0,8}$/.test(e.target.value)) set({color:e.target.value})}} /></span></label>
                <label><span>线宽</span><select value={style.width || 1.5} onChange={(e)=>set({width:Number(e.target.value)})}><option value="1">1 px</option><option value="1.5">1.5 px</option><option value="2">2 px</option><option value="3">3 px</option><option value="4">4 px</option><option value="6">6 px</option></select></label>
                <label><span>线型</span><select value={style.line_type || "solid"} onChange={(e)=>set({line_type:e.target.value as DrawingStyle["line_type"]})}><option value="solid">实线</option><option value="dashed">虚线</option><option value="dotted">点线</option></select></label>
                {selectedDrawing.object_type === "rectangle" && <>
                  <label><span>填充颜色</span><span className="color-field"><input type="color" value={(style.fill_color || style.color || "#42b8d4").slice(0,7)} onChange={(e)=>set({fill_color:e.target.value})}/><input className="hex-input" value={style.fill_color || style.color || ""} onChange={(e)=>{if(/^#[0-9a-fA-F]{0,8}$/.test(e.target.value)) set({fill_color:e.target.value})}} /></span></label>
                  <label><span>填充透明度</span><input type="range" min="0" max="100" value={Math.round((style.fill_opacity ?? 0.14)*100)} onChange={(e)=>set({fill_opacity:Number(e.target.value)/100})}/><output>{Math.round((style.fill_opacity ?? 0.14)*100)}%</output></label>
                  <button className="text-command" onClick={()=>set({fill_opacity:0})}>无填充</button>
                </>}
                <button className="text-command" onClick={resetDrawingStyle}>恢复默认样式</button>
              </div>; })()}
            </section>
          )}
          <section className="agent">
            <div className="panel-head">
              <b>Agent 追问</b>
              <Send size={15} />
            </div>
            <textarea
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="当前结构为什么尚未确认？"
            />
            <button
              className="command primary"
              disabled={loading}
              onClick={ask}
            >
              <Send size={15} />
              {loading ? "处理中" : "提交追问"}
            </button>
            {answer && <p className="answer">{answer}</p>}
          </section>
        </aside>
      </div>
      {indicatorSettingsOpen && <div className="chart-settings-backdrop" onClick={()=>setIndicatorSettingsOpen(false)}><div className="chart-settings-modal" role="dialog" aria-modal="true" aria-label={timeframe === "1" ? "分时图设置" : "K线设置"} onClick={(e)=>e.stopPropagation()}><div className="panel-head"><b>{timeframe === "1" ? "分时图设置" : "K线设置"}</b><button className="icon" onClick={()=>setIndicatorSettingsOpen(false)} aria-label="关闭"><X size={16}/></button></div>{timeframe !== "1" && <label className="settings-row"><span>K线主图指标切换</span><select value={draftMainIndicator.mode} onChange={(e)=>setDraftMainIndicator({...draftMainIndicator,mode:e.target.value as MainIndicator["mode"]})}><option value="none">不显示指标</option><option value="ma">均线</option><option value="boll">布林线</option><option value="pen_center">笔中枢</option></select></label>}<div className="settings-row settings-count"><span>{timeframe === "1" ? "分时副图数量" : "K线副图数量"}</span><div className="count-segmented">{[1,2,3,4].map((count)=><button key={count} className={draftSubplotCount === count ? "active" : ""} onClick={()=>setDraftSubplotCount(count)}>{count}</button>)}</div></div><div className="indicator-modal-actions"><button className="command" onClick={()=>setIndicatorSettingsOpen(false)}>取消</button><button className="command primary" onClick={applyIndicatorSettings}>确定</button></div></div></div>}
    </div>
  );
}
