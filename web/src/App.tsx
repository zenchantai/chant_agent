import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { DragEvent, PointerEvent as ReactPointerEvent } from "react";
import { chartDates, intradayCoordinate, intradayPointDates } from "./intradayTimeline";
import { centerDisplayLabel, centerDisplayRange, centerParents, compareCenterHit, displayCenterCollection, isWeeklyCoreCenter, sameDisplayRun, selectedCenterEvidence } from "./centerDisplay";
import { mergeIntradayData, mergeRealtimeData, startIntradayRefresh } from "./intradayRefresh";
import { dailyL2Label, dailyL2StatusLabel, isReferenceProfile, mergeDailyL2Overlays, projectionContains, projectionRectangle } from "./dailyL2Overlay";
import type { DailyL2Projection } from "./types";
import { groupDropPosition, normalizeSectionOrder, reorderWatchlistSections } from "./watchlistGroupOrder";
import type { GroupDropPosition } from "./watchlistGroupOrder";
import { chartQuoteToWatchlistQuote, mergeWatchlistQuotes, startWatchlistQuoteRefresh, watchlistQuoteLabel } from "./watchlistQuotes";
import type { WatchlistQuote, WatchlistQuoteResponse } from "./watchlistQuotes";
import * as echarts from "echarts/core";
import { BarChart, CandlestickChart, CustomChart, LineChart, ScatterChart } from "echarts/charts";
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
  GripVertical,
} from "lucide-react";
import type { Bar, Center, ChartApiResponse, ChartData, ChartRealtimeResponse, Coverage, PromotionCandidate, StructureEntity, SecurityCandidate, SecuritySearchResult, Stock, Drawing, DrawingStyle, WatchlistGroup, WatchlistMembership, WatchlistResponse, SubplotVisibility } from "./types";
import { bindChartGestures, chartZoomFromDataZoomEvent, ChartGesture, gestureOwner, initialChartZoom, panChartZoom, rebaseChartZoom } from "./chartInteractions";
import type { ChartGestureCallbacks } from "./chartInteractions";
import { levelStructureAppearance, levelStructureColor, periodStructureColor } from "./structureColors";
import { formatCompactNumber, formatIndicatorValue, formatPrice, formatVolume } from "./marketFormatters";
import {
  buildChartArtifacts,
  buildVisiblePriceMarkLine,
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
  buildDrawingSeries,
  validateSeries,
  validateAxes,
  normalizeChartData as normalizeChartDataBuilder,
} from "./chartBuilders";

export {
  buildChartArtifacts,
  buildChartOption,
  buildAxes,
  buildPriceSeries,
  buildIndicatorSeries,
  buildPenSeries,
  buildCenterAreas,
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
  components: boolean;
  dailyL2: boolean;
  centerLevels: Record<string, boolean>;
};
echarts.use([
  BarChart,
  CandlestickChart,
  LineChart,
  ScatterChart,
  CustomChart,
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
export const normalizeChartData = (input: ChartData | ChartApiResponse | null): ChartData | null => {
  return normalizeChartDataBuilder(input);
};
const scopeNodeOrdinals = <T extends StructureEntity>(items: T[]): T[] => {
  return items.slice().sort((left, right) => nodeLevel(left) - nodeLevel(right) || left.ordinal - right.ordinal);
};
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
  center: "中枢", pen: "标准笔", component: "无中枢组件",
}[kind || ""] || kind || "结构");
const nodeLevel = (node: StructureEntity) => Number.isFinite(Number(node.level)) ? Math.max(1, Math.floor(Number(node.level))) : 1;
const isCenterNode = (node: StructureEntity) => node.kind === "center";
const isProvisionalStatus = (status?: string) => status === "provisional" || status === "candidate" || status === "pending";
const centerZd = (center: Center) => Number(center.zd ?? center.fixed_zd);
const centerZg = (center: Center) => Number(center.zg ?? center.fixed_zg);
const displayPeriodLabel = (displayPeriod?: string) => displayPeriod === "higher"
  ? "更高一级"
  : displayPeriod ? (periodLabels[displayPeriod] || displayPeriod) : "未提供（结构元数据缺失）";
const calculationSourceLabel = (timeframe: string) => `${periodLabels[timeframe] || timeframe}独立结构`;
const centerSemanticLabel = (timeframe: string, level: number, _displayPeriod?: string) =>
  `${periodLabels[timeframe] || timeframe}内部 L${level} 中枢（非跨周期递归）`;

export const centersForStructureLevel = (centers: StructureEntity[], activeLevel: number) =>
  centers.filter((center) =>
    Number.isInteger(Number(center.level))
    && Number(center.level) === activeLevel,
  );

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
    `<b>${formatTradeDate(bar.trade_date)}${bar.is_forming ? "（未完成）" : ""}</b>`,
    price("开盘", bar.open),
    price("收盘", bar.close),
    price("最低", bar.low),
    price("最高", bar.high),
    `<span>涨跌幅：<b${toneAttr}>${change === null ? "--" : `${change >= 0 ? "+" : ""}${change.toFixed(2)}%`}</b></span>`,
    `成交量：${formatVolume(bar.volume)}`,
    `成交额：${formatCompactNumber(bar.amount)}`,
  ].join("<br/>");
};

export const formatCenterTooltip = (center: Center, chartTimeframe: string) => {
  const level = nodeLevel(center);
  const appearance = levelStructureAppearance("dark", chartTimeframe, level);
  const displayPeriod = appearance.displayPeriod;
  const range = centerDisplayRange(center, chartTimeframe);
  const weeklyCore = isWeeklyCoreCenter(center, chartTimeframe);
  return [
    `<b>L${level} ${weeklyCore ? "三笔核心" : "中枢"}</b>`,
    `状态：${center.status === "confirmed" ? "已确认" : "候选"}`,
    `配色参考（非结构周期）：${displayPeriodLabel(displayPeriod)}`,
    `计算来源：${calculationSourceLabel(chartTimeframe)}`,
    `语义对应：${centerSemanticLabel(chartTimeframe, level, displayPeriod)}`,
    `颜色标识：${appearance.colorKey}`,
    `${weeklyCore ? "三笔核心范围" : "实际边界"}：${range?.start_date || "--"} → ${range?.end_date || "--"}`,
    `核心区间：${formatPrice(centerZd(center))} / ${formatPrice(centerZg(center))}`,
    ...(center.dd !== undefined || center.gg !== undefined ? [`外围区间：${formatPrice(center.dd)} / ${formatPrice(center.gg)}`] : []),
    `确认时间：${center.confirmed_at || "等待确认"}`,
    ...(center.formation_modes.length ? [`形成路径：${center.formation_modes.join(" / ")}`] : []),
  ].join("<br/>");
};


const promotionSourceLabel = (candidate: PromotionCandidate) =>
  candidate.candidate_source === "extension_decomposition" ? "延伸九单位" : "扩展接触";
const promotionStatusLabel = (status: PromotionCandidate["status"] | Center["boundary_status"]) =>
  status === "fixed" ? "固定" : status === "dynamic" ? "动态" : "边界未解";

export function DailyL2Evidence({ center, data }: {center: DailyL2Projection; data: ChartData}) {
  const source = data.overlays?.daily_l2.source;
  return <div className="evidence">
    <div className="selected-structure-summary"><strong>{dailyL2Label(center)}</strong><span>{center.display_role === "constituent" ? "组成中枢" : "活动中枢"}</span></div>
    <p>{center.start_date}<br />至 {center.end_date}</p>
    <dl>
      <dt>计算来源</dt><dd>日线正式结构</dd>
      <dt>当前展示</dt><dd>{periodLabels[data.timeframe] || data.timeframe}参考投影</dd>
      <dt>结构级别</dt><dd>日线内部 L2</dd>
      <dt>结构家族</dt><dd>{center.family_id}</dd>
      <dt>来源修订</dt><dd>{center.revision_id}</dd>
      <dt>状态</dt><dd>{center.status}</dd>
      <dt>ZD / ZG</dt><dd>{formatPrice(center.zd)} / {formatPrice(center.zg)}</dd>
      <dt>数据截止日</dt><dd>{source?.source_cutoff || "--"}</dd>
      <dt>来源状态</dt><dd>{dailyL2StatusLabel(data)}</dd>
      {(center.clipped_start || center.clipped_end) && <><dt>显示范围</dt><dd>已按当前行情窗口裁剪；以上日期为日线原始范围</dd></>}
    </dl>
  </div>;
}

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
  selectedStructureId,
  selectedProjectionId,
  onProjectionSelect,
}: {
  data: ChartData | null;
  theme: string;
  onOlder: () => void;
  onSelect: (n: StructureEntity | null) => void;
  selectedStructureId?: string | null;
  selectedProjectionId?: string | null;
  onProjectionSelect: (projection: DailyL2Projection | null) => void;
  height: number;
  visible: LayerVisibility;
  subplotIndicators: SubplotIndicator[];
  subplotVisible: SubplotVisibility;
  onSubplotIndicator: (index: number, value: SubplotIndicator) => void;
  onLayerToggle: (key: "pens" | "centers" | "components" | "dailyL2", level?: number) => void;
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
}) {
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<EChartsType | null>(null);
  const zoomState = useRef({ start: 0, end: 100 });
  const previousDates = useRef<string[]>([]);
  const lastChartKey = useRef<string | undefined>(undefined);
  const lastFullRenderKey = useRef<string | undefined>(undefined);
  const gesture = useRef(new ChartGesture());
  const panStart = useRef<{ clientX: number; zoom: { start: number; end: number } } | null>(null);
  const editor = useRef<{
    point: { trade_date: string; price: number };
    drawing?: Drawing;
    handle?: "start" | "end" | "move";
    tool?: "segment" | "line" | "rectangle";
  } | null>(null);
  const [contextMenu, setContextMenu] = useState<{x:number;y:number;drawing:Drawing} | null>(null);
  const [preview, setPreview] = useState<{start:{trade_date:string;price:number};end:{trade_date:string;price:number}} | null>(null);
  const [selectedDrawing, setSelectedDrawing] = useState<number | null>(null);
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);
  const hoveredIndexRef = useRef<number | null>(null);
  const [renderError, setRenderError] = useState<string | null>(null);
  const [compactLayout, setCompactLayout] = useState(() => innerWidth <= 760);
  // App state is normalized at the API boundary.  Re-normalizing it on every
  // quote tick would clone every formal structure array and defeat reference
  // stability provided by the realtime delta merge.
  const safeData = data;
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
    const dates = chartDates(safeData?.timeframe || "", bars);
    const index = Math.max(0, Math.min(dates.length - 1, Math.round(rawIndex)));
    const y = Number(value?.[1]);
    if (!Number.isFinite(y)) return null;
    return { trade_date: dates[index], price: y };
  }, [bars, safeData?.timeframe]);
  const pointPixel = useCallback((point: {trade_date:string;price:number}) => {
    if (!chart.current) return [0,0];
    const dates = chartDates(safeData?.timeframe || "", bars);
    const index = safeData?.timeframe === "1" ? Math.max(0, dates.indexOf(intradayCoordinate(point.trade_date))) : Math.max(0, datesIndex(bars, point.trade_date));
    const px = chart.current.convertToPixel({xAxisIndex:0,yAxisIndex:0}, [index, point.price]) as number[];
    return [Number(px?.[0] || 0), Number(px?.[1] || 0)];
  }, [bars, safeData?.timeframe]);
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
        lastFullRenderKey.current = undefined;
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
    const builderDates = chartDates(builderData.timeframe, bars);
    const builderPointDates = builderData.timeframe === "1" ? intradayPointDates(bars, builderDates) : builderDates;
    const builderChartKey = `${builderData.symbol || ""}:${builderData.timeframe || ""}`;
    const builderTimeframeChanged = lastChartKey.current !== builderChartKey;
    if (builderTimeframeChanged) {
      let saved: string | null = null;
      try { saved = localStorage.getItem(`chan-zoom-${builderChartKey}`); } catch {}
      zoomState.current = initialChartZoom(bars.length, builderData.timeframe === "1", saved);
      setHoveredIndex(null);
      hoveredIndexRef.current = null;
    } else if (builderData.timeframe === "1") {
      zoomState.current = { start: 0, end: 100 };
    } else {
      zoomState.current = rebaseChartZoom(zoomState.current, previousDates.current, builderDates);
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
        zoomStart: zoomState.current.start,
        zoomEnd: zoomState.current.end,
        chartHeight: effectiveHeight,
        paneRatios,
        compactLayout,
        selectedStructureId: selectedStructureId || null,
        selectedProjectionId,
        formatKlineTooltip,
        formatCenterTooltip,
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
      const fullRenderKey = JSON.stringify([
        builderChartKey, builderData.structure_version, builderData.drawings_version,
        bars[0]?.trade_date || "", bars.length, theme, visible, subplotIndicators,
        subplotVisible, mainIndicator, selectedStructureId, selectedProjectionId,
        effectiveHeight, paneRatios, compactLayout, drawingItems || builderData.drawings,
      ]);
      const canPatchLiveSeries = !builderTimeframeChanged && lastFullRenderKey.current === fullRenderKey;
      if (canPatchLiveSeries) {
        const liveSeries = (artifacts.option.series as Record<string, any>[]).filter((series) => {
          const id = String(series.id || "");
          return id === "kline" || id === "intraday-price" || id === "intraday-average"
            || /^ma\d+$/.test(id) || id.startsWith("boll-") || id.startsWith("volume-")
            || id.startsWith("amount-") || id.startsWith("macd-");
        });
        instance.setOption({
          xAxis: artifacts.option.xAxis,
          tooltip: artifacts.option.tooltip,
          series: liveSeries,
        }, { notMerge: false, lazyUpdate: true });
      } else {
        instance.setOption(artifacts.option, { notMerge: true, lazyUpdate: false });
        lastFullRenderKey.current = fullRenderKey;
      }
      if (hoveredIndexRef.current !== null && hoveredIndexRef.current < bars.length) {
        const hoveredDate = bars[hoveredIndexRef.current].trade_date;
        const dataIndex = builderData.timeframe === "1" ? builderPointDates.indexOf(hoveredDate) : builderDates.indexOf(hoveredDate);
        if (dataIndex >= 0) instance.dispatchAction({ type: "showTip", seriesIndex: 0, dataIndex });
      }
      setRenderError(null);
    } catch (error) {
      console.error("缠论图表渲染失败", error);
      setRenderError("图表暂时无法渲染");
      return;
    }
    const builderZoom = (event: any) => {
      if (builderData.timeframe === "1") return;
      const nextZoom = chartZoomFromDataZoomEvent(event, builderDates);
      if (nextZoom) {
        zoomState.current = nextZoom;
        try { localStorage.setItem(`chan-zoom-${builderChartKey}`, JSON.stringify(zoomState.current)); } catch { /* storage is optional */ }
        if (builderData.timeframe !== "1") {
          instance.setOption({ series: [{ id: "kline", markLine: buildVisiblePriceMarkLine(bars, theme, nextZoom.start, nextZoom.end) }] }, { lazyUpdate: false });
        }
      }
      if ((nextZoom?.start ?? 100) <= 15 && builderData.has_more) onOlder();
    };
    const builderClick = () => {};
    const builderAxisPointer = (event: any) => {
      const axis = event.axesInfo?.find((item: any) => item.axisDim === "x" && item.axisIndex === 0);
      const axisIndex = typeof axis?.value === "string" ? builderDates.indexOf(axis.value) : Number(axis?.value);
      const pricePoint = axis?.seriesDataIndices?.find((item: any) => item.seriesIndex === 0);
      const stamp = pricePoint ? builderPointDates[pricePoint.dataIndex] : builderDates[axisIndex];
      const index = builderData.timeframe === "1" ? bars.findIndex((bar) => bar.trade_date === stamp) : axisIndex;
      if (Number.isInteger(index) && index >= 0 && index < bars.length) {
        hoveredIndexRef.current = index;
        setHoveredIndex(index);
      } else if (builderData.timeframe === "1") {
        hoveredIndexRef.current = null;
        setHoveredIndex(null);
      }
    };
    const builderGlobalOut = () => { hoveredIndexRef.current = null; setHoveredIndex(null); };
    const distanceToSegment = (x: number, y: number, start: number[], end: number[]) => {
      const dx = end[0] - start[0], dy = end[1] - start[1];
      if (!dx && !dy) return Math.hypot(x - start[0], y - start[1]);
      const ratio = Math.max(0, Math.min(1, ((x - start[0]) * dx + (y - start[1]) * dy) / (dx * dx + dy * dy)));
      return Math.hypot(x - (start[0] + ratio * dx), y - (start[1] + ratio * dy));
    };
    const nodePixelBoundary = (node: StructureEntity) => {
      const fallbackStart = node.kind === "center" ? node.fixed_zd : node.low;
      const fallbackEnd = node.kind === "center" ? node.fixed_zg : node.high;
      const start = pointPixel({ trade_date: node.start_date, price: Number(node.start_price ?? fallbackStart) });
      const end = pointPixel({ trade_date: node.end_date, price: Number(node.end_price ?? fallbackEnd) });
      return { start, end };
    };
    const findStructureAt = (x: number, y: number) => {
      const center = artifacts.visibleCenters.map((node) => {
        const range = centerDisplayRange(node, builderData.timeframe);
        const zd = Number(node.zd ?? node.fixed_zd), zg = Number(node.zg ?? node.fixed_zg);
        if (!range || !Number.isFinite(zd) || !Number.isFinite(zg)) return null;
        const topLeft = pointPixel({ trade_date: range.start_date, price: zg });
        const bottomRight = pointPixel({ trade_date: range.end_date, price: zd });
        const contains = x >= Math.min(topLeft[0], bottomRight[0]) - 8 && x <= Math.max(topLeft[0], bottomRight[0]) + 8 && y >= Math.min(topLeft[1], bottomRight[1]) - 8 && y <= Math.max(topLeft[1], bottomRight[1]) + 8;
        return contains ? { center: node, area: Math.abs((topLeft[0] - bottomRight[0]) * (topLeft[1] - bottomRight[1])) } : null;
      }).filter((item): item is { center: Center; area: number } => item !== null).sort(compareCenterHit)[0]?.center;
      const nearestNode = (candidates: { node: StructureEntity; tolerance: number }[]) => candidates.map(({ node, tolerance }) => {
        const boundary = nodePixelBoundary(node);
        return { node, distance: distanceToSegment(x, y, boundary.start, boundary.end), tolerance };
      }).filter((item) => item.distance <= item.tolerance).sort((a, b) => a.distance - b.distance)[0]?.node || null;
      if (center) return center;
      return nearestNode([
        ...(visible.pens ? (builderData.pens || []).filter((node) => node.status === "confirmed" || (builderData.structure_preview === true && node.status === "provisional")).map((node) => ({ node, tolerance: 20 })) : []),
        ...artifacts.visibleComponents.map((node) => ({ node, tolerance: 12 })),
      ]);
    };
    const builderCanvasClick = (event: any) => {
      if (drawingTool || gesture.current.suppressClick) return;
      const x = Number(event?.zrX ?? event?.offsetX), y = Number(event?.zrY ?? event?.offsetY);
      if (!Number.isFinite(x) || !Number.isFinite(y)) return;
      if (!instance.containPixel({ gridIndex: 0 }, [x, y])) return;
      const native = findStructureAt(x, y);
      const coord = (index: number, price: number) => instance.convertToPixel({ xAxisIndex: 0, yAxisIndex: 0 }, [index, price]) as number[];
      const grid = (artifacts.option?.grid as any[])?.[0];
      const categoryWidth = Math.abs(coord(1, 0)[0] - coord(0, 0)[0]) || instance.getWidth() - Number(grid?.left || 0) - Number(grid?.right || 0);
      const projections = artifacts.visibleDailyL2.map(bound => ({ bound, rect: projectionRectangle(bound, coord, categoryWidth) }))
        .filter(({ rect }) => projectionContains(rect, x, y))
        .sort((a, b) => Number(b.bound.center.id === selectedProjectionId) - Number(a.bound.center.id === selectedProjectionId)
          || a.rect.width * a.rect.height - b.rect.width * b.rect.height || a.bound.center.id.localeCompare(b.bound.center.id));
      const projection = projections[0]?.bound.center;
      if (projection && (projection.id === selectedProjectionId || native?.kind !== "center")) onProjectionSelect(projection);
      else onSelect(native);
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
  }, [data, bars, onOlder, onSelect, onProjectionSelect, selectedProjectionId, selectedStructureId, height, visible, subplotIndicators, subplotVisible, mainIndicator, drawingTool, drawingItems, theme, paneRatios, compactLayout, effectiveHeight]);
  const eventPixel = (event: MouseEvent) => {
    const rect = ref.current!.getBoundingClientRect();
    return [event.clientX - rect.left, event.clientY - rect.top];
  };
  const drawingAt = (pixel: number[]) => (drawingItems || []).slice().reverse().find((drawing) => {
    if (!drawing.visible) return false;
    const start = pointPixel(drawing.start_anchor), end = pointPixel(drawing.end_anchor);
    if (Math.hypot(pixel[0] - start[0], pixel[1] - start[1]) < 14
      || Math.hypot(pixel[0] - end[0], pixel[1] - end[1]) < 14) return true;
    if (drawing.object_type === "rectangle") return pixel[0] >= Math.min(start[0], end[0]) - 12
      && pixel[0] <= Math.max(start[0], end[0]) + 12 && pixel[1] >= Math.min(start[1], end[1]) - 12
      && pixel[1] <= Math.max(start[1], end[1]) + 12;
    const deltaX = end[0] - start[0], deltaY = end[1] - start[1];
    const length = Math.hypot(deltaX, deltaY);
    if (!length) return false;
    const projection = ((pixel[0] - start[0]) * deltaX + (pixel[1] - start[1]) * deltaY) / (length * length);
    return (drawing.object_type === "line" || (projection >= 0 && projection <= 1))
      && Math.abs((pixel[0] - start[0]) * deltaY - (pixel[1] - start[1]) * deltaX) / length < 12;
  });
  const clearEditor = () => { editor.current = null; setPreview(null); };
  const gestureCallbacks = useRef<ChartGestureCallbacks>(null!);
  gestureCallbacks.current = {
    begin: (event) => {
      const instance = chart.current;
      if (!instance || isChartDisposed(instance)) return null;
      const pixel = eventPixel(event);
      if (!instance.containPixel({ gridIndex: paneLayout.tops.map((_, index) => index) }, pixel)) return null;
      const mainGrid = instance.containPixel({ gridIndex: 0 }, pixel);
      const editing = drawingOpen && mainGrid;
      const drawing = editing && !drawingTool ? drawingAt(pixel) : undefined;
      const owner = gestureOwner(Boolean(editing && drawingTool), Boolean(drawing), false);
      if (owner === "pan") {
        panStart.current = { clientX: event.clientX, zoom: { ...zoomState.current } };
        return owner;
      }
      const point = toPoint(event as unknown as React.PointerEvent);
      if (!point) return null;
      editor.current = { point };
      if (drawingTool) {
        editor.current.tool = drawingTool;
        setPreview({ start: point, end: point });
      } else if (drawing) {
        const start = pointPixel(drawing.start_anchor), end = pointPixel(drawing.end_anchor);
        editor.current.drawing = drawing;
        editor.current.handle = Math.hypot(pixel[0] - start[0], pixel[1] - start[1]) < 14 ? "start"
          : Math.hypot(pixel[0] - end[0], pixel[1] - end[1]) < 14 ? "end" : "move";
        setSelectedDrawing(drawing.id);
        onDrawingSelect?.(drawing);
      }
      return owner;
    },
    move: (event) => {
      const pan = panStart.current;
      if (pan) {
        const instance = chart.current;
        if (!instance || isChartDisposed(instance)) return;
        const next = panChartZoom(pan.zoom, event.clientX - pan.clientX, instance.getWidth());
        instance.dispatchAction({ type: "dataZoom", dataZoomIndex: 0, start: next.start, end: next.end });
        return;
      }
      const active = editor.current;
      const point = toPoint(event as unknown as React.PointerEvent);
      if (!active || !point) return;
      if (active.tool) { setPreview({ start: active.point, end: point }); return; }
      if (active.drawing) {
        const drawing = active.drawing;
        const deltaTime = Date.parse(point.trade_date) - Date.parse(active.point.trade_date);
        const deltaPrice = point.price - active.point.price;
        const moveAnchor = (anchor: { trade_date: string; price: number }) => ({
          trade_date: new Date(Date.parse(anchor.trade_date) + deltaTime).toISOString().replace("T", " ").slice(0, 19),
          price: anchor.price + deltaPrice,
        });
        onDrawingUpdate?.({ ...drawing,
          start_anchor: active.handle === "start" ? point : active.handle === "move" ? moveAnchor(drawing.start_anchor) : drawing.start_anchor,
          end_anchor: active.handle === "end" ? point : active.handle === "move" ? moveAnchor(drawing.end_anchor) : drawing.end_anchor,
        });
      }
    },
    end: (event) => {
      panStart.current = null;
      const active = editor.current;
      const point = toPoint(event as unknown as React.PointerEvent);
      if (active?.tool && point && (active.point.trade_date !== point.trade_date || Math.abs(active.point.price - point.price) > 1e-9)) {
        onDrawingCreate?.({ object_type: active.tool, start_anchor: active.point, end_anchor: point,
          style: defaultDrawingStyle(data?.timeframe || "d", theme, active.tool === "rectangle"), label: "", visible: true });
      }
      clearEditor();
    },
    cancel: () => {
      panStart.current = null;
      clearEditor();
      const instance = chart.current;
      if (instance && !isChartDisposed(instance)) instance.getZr().trigger("mouseup", { event: {} });
    },
  };
  useEffect(() => {
    const element = ref.current;
    if (!element || !bars.length) return;
    return bindChartGestures(element, gesture.current, () => gestureCallbacks.current);
  }, [data?.symbol, data?.timeframe, bars.length > 0, drawingOpen, drawingTool]);
  const drawingContext = useRef<(event: MouseEvent) => void>(null!);
  drawingContext.current = (event) => {
    if (!drawingOpen) return;
    event.preventDefault();
    const pixel = eventPixel(event);
    const drawing = drawingAt(pixel);
    if (drawing) {
      setSelectedDrawing(drawing.id); onDrawingSelect?.(drawing);
      setContextMenu({ x: event.clientX, y: event.clientY, drawing });
    }
  };
  useEffect(() => {
    const element = ref.current;
    const context = (event: MouseEvent) => drawingContext.current(event);
    element?.addEventListener("contextmenu", context);
    return () => element?.removeEventListener("contextmenu", context);
  }, []);
  const infoIndex = hoveredIndex ?? Math.max(0, bars.length - 1);
  const infoBar = bars[infoIndex];
  const infoMa = infoBar ? (data?.indicators?.ma || []).find((item) => item.trade_date === infoBar.trade_date) : undefined;
  const chartCenters = data ? displayCenterCollection(data) : [];
  const referenceProfile = data ? isReferenceProfile(data) : false;
  const visibleCenters = chartCenters.filter((item) => visible.centers && (referenceProfile || visible.centerLevels[String(nodeLevel(item))] !== false));
  const visibleCenterCount = visibleCenters.filter((item) => item.display_role === "active").length;
  const constituentCenterCount = visibleCenters.filter((item) => item.display_role === "constituent").length;
  const constituentL1Count = chartCenters.filter((item) => item.level === 1 && item.display_role === "constituent").length;
  const noActiveL1 = data && !data.levels.includes(1);
  const centerLevels = Array.from(new Set((data?.center_levels || chartCenters.map(nodeLevel)).map(Number).filter((level) => Number.isInteger(level) && level >= 1))).sort((a, b) => a - b);
  return <div className={`chart-shell ${data?.timeframe === "1" ? "intraday-chart" : "kline-chart"}`} style={{ height: effectiveHeight }} onClick={() => contextMenu && setContextMenu(null)}>
    <div ref={ref} className="chart" aria-label={bars.length ? "K线图" : "暂无行情图表"} />
    {safeData && !bars.length && <div className="chart-empty-state" role="status">暂无可绘制行情</div>}
    {renderError && <div className="chart-render-error" role="alert">{renderError}</div>}
    <svg className={`drawing-overlay ${drawingOpen ? "editing" : ""}`} aria-hidden="true">
      {preview && (() => { const [sx,sy]=pointPixel(preview.start), [ex,ey]=pointPixel(preview.end); const color=periodStructureColor(document.documentElement.dataset.theme === "light" ? "light":"dark", data?.timeframe || "d"); return preview && drawingTool === "rectangle" ? <rect x={Math.min(sx,ex)} y={Math.min(sy,ey)} width={Math.abs(ex-sx)} height={Math.abs(ey-sy)} fill={`${color}22`} stroke={color} strokeDasharray="5 4" /> : <line x1={sx} y1={sy} x2={ex} y2={ey} stroke={color} strokeWidth="2" strokeDasharray="5 4" />; })()}
      {selectedDrawing !== null && (() => { const item=(drawingItems || []).find((d)=>d.id===selectedDrawing); if(!item) return null; const [sx,sy]=pointPixel(item.start_anchor), [ex,ey]=pointPixel(item.end_anchor); return <g className="drawing-selection"><circle cx={sx} cy={sy} r="5"/><circle cx={ex} cy={ey} r="5"/></g>; })()}
    </svg>
    {data && <div className="chart-main-overlay" style={{ top: data.timeframe === "1" ? 8 : paneLayout.tops[0] + 6 }}>
      <div className="chart-indicator-group">
        <div className="main-indicator-controls">
          <select className={data.timeframe === "1" ? "intraday-indicator-hidden" : ""}
            value={mainIndicator.mode} disabled={data.timeframe === "1"}
            onChange={(event) => onMainIndicator?.(event.target.value as MainIndicator["mode"])} aria-label="主图指标">
            <option value="none">不显示指标</option><option value="ma">MA</option>
            <option value="boll">布林线</option><option value="pen_center">笔中枢</option>
          </select>
          <button title={data.timeframe === "1" ? "分时图设置" : "主图指标设置"}
            aria-label={data.timeframe === "1" ? "分时图设置" : "主图指标设置"} onClick={onOpenIndicatorSettings}>
            <Settings size={15}/>
          </button>
        </div>
        {data.timeframe !== "1" && infoBar && mainIndicator.mode !== "none" && <div className="kline-ma-strip" tabIndex={0} aria-label="主图指标数值，可横向滚动">
          {mainIndicator.mode === "ma" && mainIndicator.maPeriods.map((period) => <span key={period}>MA{period}: {formatPrice(infoMa?.values?.[String(period)])}</span>)}
          {mainIndicator.mode === "boll" && (() => { const boll=(data?.indicators?.boll||[]).find((item)=>item.trade_date===infoBar.trade_date); return <><span>BOLL上轨: {formatPrice(boll?.upper)}</span><span>BOLL中轨: {formatPrice(boll?.middle)}</span><span>BOLL下轨: {formatPrice(boll?.lower)}</span></>; })()}
          {mainIndicator.mode === "pen_center" && <span>笔 · {visibleCenterCount} 活动中枢 · {constituentCenterCount} 组成中枢</span>}
        </div>}
      </div>
      {data.timeframe !== "1" && <div className="chart-layer-controls" aria-label="缠论图层控制">
        {([['pens', '笔', 'pen'], ['centers', referenceProfile ? '笔中枢' : '中枢', 'center'], ['components', '组件', 'pen']] as const).filter(([key]) => !referenceProfile || key === "pens" || key === "centers").map(([key, label, icon]) => (
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
              }}
              aria-hidden="true"
            />
            <span>{label}</span>
          </button>
        ))}
        {!referenceProfile && centerLevels.map((level) => <button key={`center-level-${level}`} className={`chart-layer-toggle chart-level-toggle ${visible.centers && visible.centerLevels[String(level)] !== false ? "active" : ""}`} aria-label={`${visible.centerLevels[String(level)] !== false ? "隐藏" : "显示"}中枢 L${level}`} aria-pressed={visible.centers && visible.centerLevels[String(level)] !== false} title={`${visible.centerLevels[String(level)] !== false ? "隐藏" : "显示"}中枢 L${level}`} onClick={() => onLayerToggle("centers", level)}><i className="legend-swatch center" style={{borderColor: levelStructureColor(theme === "light" ? "light" : "dark", data?.timeframe || "d", level), background: `${levelStructureColor(theme === "light" ? "light" : "dark", data?.timeframe || "d", level)}${theme === "light" ? "1A" : "24"}`}} aria-hidden="true"/><span>中枢 L{level}</span></button>)}
        {referenceProfile && <button className={`chart-layer-toggle ${visible.dailyL2 ? "active" : ""}`} aria-label={`${visible.dailyL2 ? "隐藏" : "显示"}日线 L2 参考`} aria-pressed={visible.dailyL2} onClick={() => onLayerToggle("dailyL2")}><i className="legend-swatch center" style={{borderColor: levelStructureColor(theme, "d", 2)}} aria-hidden="true"/><span>日线 L2 参考</span></button>}
      </div>}
      {referenceProfile && <div role="status" title={dailyL2StatusLabel(data)} style={{position:"absolute", top:34, left:0, maxWidth:"100%", overflow:"hidden", whiteSpace:"nowrap", textOverflow:"ellipsis", fontSize:11, padding:"4px 6px", background:"var(--panel)"}}>{dailyL2StatusLabel(data)}</div>}
    </div>}
    {data && !referenceProfile && (noActiveL1 || data.relations.some((relation) => relation.status === "candidate" || relation.expansion_status === "confirmed")) && <div role="status" style={{position:"absolute", top:52, left:68, zIndex:12, maxWidth:"70%", maxHeight:"45%", overflowY:"auto", padding:"6px 10px", background:"var(--panel)", border:"1px dashed var(--accent)", fontSize:11}}>
      {noActiveL1 && <div>当前无活动 L1{constituentL1Count ? `，${visible.centers && visible.centerLevels["1"] !== false ? "显示" : "可显示"} ${constituentL1Count} 个组成 L1` : centerLevels.length ? `，可查看 L${Math.min(...centerLevels)}` : "，等待结构形成"}{centerLevels.filter((level) => level > 1).map((level) => <button key={level} onClick={() => { if (!visible.centers) onLayerToggle("centers"); if (visible.centerLevels[String(level)] === false) onLayerToggle("centers", level); }}>查看 L{level}</button>)}</div>}
      {data.relations.some((relation) => relation.expansion_status === "confirmed") && <details><summary>已成立的中枢扩展</summary>{data.relations.filter((relation) => relation.expansion_status === "confirmed").map((relation) => <p key={String(relation.id)}>{String(relation.start_date)} → {String(relation.end_date)} · {relation.boundary_status === "unresolved" ? "扩展已成立，区间待确定" : relation.boundary_status === "dynamic" ? "扩展已成立，区间动态" : "扩展区间已固定"} · 确认 {String(relation.confirmed_at)}<br/>{relation.boundary_status === "unresolved" ? "等待笔原生构成段的共同交集证明" : "构成段证据已齐备"}<button onClick={() => onSelect(data.center_revisions.find(center => center.id === relation.to_id) || null)}>查看 Z 接触证据</button></p>)}</details>}
      {data.relations.some((relation) => relation.status === "candidate") && <details><summary>待确认中枢关系：保留低级活动结构</summary>{data.relations.filter((relation) => relation.status === "candidate").map((relation) => <p key={String(relation.id)}>{String(relation.relation_type)} · {JSON.stringify(relation.missing_evidence)}</p>)}</details>}
    </div>}
    {preview && <div className="drawing-preview" aria-live="polite">正在绘制 · {preview.end.trade_date} · {formatPrice(preview.end.price)}</div>}
    {contextMenu && <div className="drawing-context-menu" style={{left:contextMenu.x,top:contextMenu.y}} onClick={(event)=>event.stopPropagation()}>
      <button onClick={() => { onDrawingDelete?.(contextMenu.drawing.id); setContextMenu(null); }}>删除</button>
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
  const [sectionOrder, setSectionOrder] = useState<string[]>(["all", "ungrouped"]);
  const [watchlistQuotes, setWatchlistQuotes] = useState<Record<string, WatchlistQuote>>({});
  const [quoteStatus, setQuoteStatus] = useState("等待行情");
  const sectionRevision = useRef(0);
  const sectionSaving = useRef(false);
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
  const [selected, setSelected] = useState<StructureEntity | null>(null);
  const weeklyCoreSelected = selected?.kind === "center" && isWeeklyCoreCenter(selected, timeframe);
  const selectedCenterRange = selected?.kind === "center" ? centerDisplayRange(selected, timeframe) : null;
  const [selectedProjection, setSelectedProjection] = useState<DailyL2Projection | null>(null);
  const [selectedDrawing, setSelectedDrawing] = useState<Drawing | null>(null);
  useEffect(() => { setSelected(null); setSelectedProjection(null); }, [symbol, timeframe]);
  useEffect(() => {
    setSelectedProjection(current => current && data && isReferenceProfile(data) && data.overlays?.daily_l2.status !== "unavailable"
      ? data.overlays?.daily_l2.centers.find(center => center.id === current.id) || null : null);
  }, [data]);
  useEffect(() => {
    if (!selected || !data) return;
    const nodes = [...(data.pens || []), ...(data.centers || []), ...(data.center_revisions || []), ...(data.components || [])];
    if (!nodes.some((node) => node.id === selected.id)) setSelected(null);
  }, [data, selected]);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [visible, setVisible] = useState<LayerVisibility>({
    pens: true,
    centers: true,
    components: false,
    dailyL2: true,
    centerLevels: {},
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
  const drawingBaseline = useRef<Drawing[]>([]);
  const drawingHistory = useRef<Drawing[][]>([]);
  const drawingRedo = useRef<Drawing[][]>([]);
  const [drawingOpen, setDrawingOpen] = useState(false);
  const [dragStock, setDragStock] = useState<{symbol:string;viewKey:string;groupId:number|null} | null>(null);
  const [dragGroupId, setDragGroupId] = useState<string | null>(null);
  const [groupDropTarget, setGroupDropTarget] = useState<{id:string;position:GroupDropPosition} | null>(null);
  const [groupOrderSaving, setGroupOrderSaving] = useState(false);
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
    const revision = sectionRevision.current;
    const result = await api<WatchlistResponse>("/api/watchlist");
    setStocks(result.stocks);
    setWatchlistGroups(result.groups);
    if (!sectionSaving.current && sectionRevision.current === revision) setSectionOrder(normalizeSectionOrder(result.section_order || [], result.groups));
    setMemberships(result.memberships);
    setWatchlistLoaded(true);
    if (result.stocks.length) setSymbol((current) => current || result.stocks[0].symbol);
    return result.stocks;
  }, []);
  useEffect(() => { refreshStocks().catch((error) => setWatchlistError(error.message)); }, [refreshStocks]);
  const quoteSymbolsKey = [...new Set(stocks.map((stock) => stock.symbol))].sort().join(",");
  const quoteGroupsKey = watchlistGroups.map((group) => `${group.id}:${group.name}`).join("|") + JSON.stringify(memberships) + sectionOrder.join(",");
  useEffect(() => {
    if (!watchlistLoaded) return;
    if (!quoteSymbolsKey) { setWatchlistQuotes({}); setQuoteStatus("暂无自选"); return; }
    const symbols = quoteSymbolsKey.split(",");
    return startWatchlistQuoteRefresh({
      visibility: document,
      fetch: (signal) => api<WatchlistQuoteResponse>(`/api/watchlist/quotes?current_symbol=${encodeURIComponent(symbol)}`, {signal}),
      onQuotes: (response) => {
        const backgroundSymbols = symbols.filter((code) => code !== symbol);
        setWatchlistQuotes((current) => mergeWatchlistQuotes(current,
          response.quotes.filter((quote) => quote.symbol !== symbol), backgroundSymbols));
        setQuoteStatus(`${response.market_status} · ${response.refresh_after_ms === 15_000 ? "15秒刷新" : "低频更新"}`);
      },
      onError: () => {
        setQuoteStatus("行情更新失败，已保留有效报价");
        setWatchlistQuotes((current) => Object.fromEntries(Object.entries(current)
          .filter(([code]) => symbols.includes(code)).map(([code, quote]) => [code, {...quote, status:"error", error:"行情请求失败"}])));
      },
    });
  }, [watchlistLoaded, quoteSymbolsKey, quoteGroupsKey, symbol]);
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
          structure_level: "0",
          limit: "300",
          ma_periods: mainIndicator.maPeriods.join(","),
          boll_period: String(mainIndicator.bollPeriod),
          boll_multiplier: String(mainIndicator.bollMultiplier),
        });
        const cursor = nextBefore.current;
        if (prepend && cursor) q.set("before", cursor);
        const response = await api<ChartApiResponse>(`/api/chart-data/${encodeURIComponent(requestSymbol)}?${q}`, { signal: controller.signal });
        const fresh = normalizeChartData(response);
        if (!fresh) throw new Error("图表数据无效");
        if (!isCurrent()) return;
        setAnswer("");
        const centerLevels = (fresh.center_levels || []).map(Number).filter((level) => Number.isInteger(level) && level >= 1);
        const preferredLevel = 1;
        if (!localStorage.getItem(`formal-structure-visible-v26-levels-${requestSymbol}-${requestTimeframe}`)) {
          setVisible((current) => ({
            ...current,
            pens: true,
            centers: true,
            components: false,
            centerLevels: Object.fromEntries([1, ...centerLevels].map((level) => [String(level), level === preferredLevel])),
          }));
          localStorage.setItem(`formal-structure-visible-v26-levels-${requestSymbol}-${requestTimeframe}`, "1");
        }
        setVisible((current) => {
          const nextCenterLevels = { ...current.centerLevels };
          centerLevels.forEach((level) => { if (!(String(level) in nextCenterLevels)) nextCenterLevels[String(level)] = level === 1; });
          return { ...current, centerLevels: nextCenterLevels };
        });
        nextBefore.current = fresh.next_before;
        setData((old) =>
          prepend && old && sameDisplayRun(old, fresh)
            ? {
                ...fresh,
                bars: unique([...fresh.bars, ...old.bars], (x) => x.trade_date).sort((a, b) => a.trade_date.localeCompare(b.trade_date)),
                pens: unique([...fresh.pens, ...old.pens], (x) => x.id),
                centers: scopeNodeOrdinals(unique([...(fresh.centers || []), ...(old.centers || [])], (x) => x.id)),
                components: scopeNodeOrdinals(unique([...(fresh.components || []), ...(old.components || [])], (x) => x.id)),
                center_revisions: unique([...fresh.center_revisions, ...old.center_revisions], (x) => x.id),
                overlays: mergeDailyL2Overlays(fresh, old),
                display_centers: unique([...(fresh.display_centers || []), ...(old.display_centers || [])], (x) => x.revision_id),
                promotion_candidates: unique([...(fresh.promotion_candidates || []), ...(old.promotion_candidates || [])], (x) => x.id),
                promotion_candidate_revisions: unique([...(fresh.promotion_candidate_revisions || []), ...(old.promotion_candidate_revisions || [])], (x) => x.id),
                drawings: fresh.drawings || old.drawings,
                indicators: {
                  macd: unique([...fresh.indicators.macd, ...old.indicators.macd], (x) => x.trade_date).sort((a, b) => a.trade_date.localeCompare(b.trade_date)),
                  ma: unique([...(fresh.indicators.ma || []), ...(old.indicators.ma || [])], (x) => x.trade_date).sort((a, b) => a.trade_date.localeCompare(b.trade_date)),
                  boll: unique([...(fresh.indicators.boll || []), ...(old.indicators.boll || [])], (x) => x.trade_date).sort((a, b) => a.trade_date.localeCompare(b.trade_date)),
                },
              }
            : fresh,
        );
        if (!prepend) { const loaded = fresh.drawings || []; setDraftDrawings(loaded); drawingBaseline.current = loaded; setDeletedDrawings([]); drawingHistory.current=[]; drawingRedo.current=[]; }
        if (requestTimeframe === "1") {
          setCoverage(null);
          return;
        }
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
  const [intradayError, setIntradayError] = useState("");
  const realtimePeriod = ["1", "5", "30", "d", "w", "m"].includes(timeframe);
  const intradayReady = !loading && data?.symbol === symbol && data?.timeframe === timeframe && realtimePeriod;
  const intradayState = useRef(data?.intraday_refresh);
  intradayState.current = data?.intraday_refresh;
  const realtimeVersions = useRef({ market: data?.market_version || "", structure: data?.structure_version || "" });
  realtimeVersions.current = { market: data?.market_version || "", structure: data?.structure_version || "" };
  const deltaRealtime = ["5", "30", "d"].includes(timeframe)
    && (import.meta.env.VITE_REALTIME_DELTA ?? "1") !== "0";
  useEffect(() => {
    setIntradayError("");
    if (!symbol || !realtimePeriod || !intradayReady) return;
    return startIntradayRefresh({
      visibility: document,
      initial: intradayState.current,
      onError: () => setIntradayError("分时刷新失败，已保留上次数据"),
      fetch: async (signal) => {
        if (deltaRealtime) {
          const params = new URLSearchParams({ timeframe, adjustflag: "2",
            known_market_version: realtimeVersions.current.market,
            known_structure_version: realtimeVersions.current.structure,
            ma_periods: mainIndicator.maPeriods.join(","), boll_period: String(mainIndicator.bollPeriod),
            boll_multiplier: String(mainIndicator.bollMultiplier), structure_level: "0" });
          const fresh = await api<ChartRealtimeResponse>(`/api/chart-realtime/${encodeURIComponent(symbol)}?${params}`, { signal });
          if (!fresh?.intraday_refresh || fresh.symbol !== symbol || fresh.timeframe !== timeframe) throw new Error("实时行情响应无效");
          if (!signal.aborted) {
            realtimeVersions.current = { market: fresh.market_version, structure: fresh.structure_version };
            setData((current) => mergeRealtimeData(current, fresh));
            setIntradayError(fresh.period_refresh?.error || fresh.intraday_refresh.error || fresh.intraday_refresh.calendar_error || "");
          }
          return fresh.intraday_refresh;
        }
        const params = new URLSearchParams({ timeframe, adjustflag: "2", refresh: "true", limit: "300",
          ma_periods: mainIndicator.maPeriods.join(","), boll_period: String(mainIndicator.bollPeriod),
          boll_multiplier: String(mainIndicator.bollMultiplier) });
        const response = await api<ChartApiResponse>(`/api/chart-data/${encodeURIComponent(symbol)}?${params}`, { signal });
        const fresh = normalizeChartData(response);
        if (!fresh?.intraday_refresh || fresh.symbol !== symbol || fresh.timeframe !== timeframe) throw new Error("实时行情响应无效");
        if (!signal.aborted) {
          setData((current) => current?.symbol === symbol && current.timeframe === timeframe ? mergeIntradayData(current, fresh) : current);
          setIntradayError(fresh.period_refresh?.error || fresh.intraday_refresh.error || fresh.intraday_refresh.calendar_error || "");
        }
        return fresh.intraday_refresh;
      },
    });
  }, [symbol, timeframe, mainIndicator, intradayReady, realtimePeriod, deltaRealtime]);
  useEffect(() => {
    nextBefore.current = undefined;
    setSelected(null);
    const versionedLayerKey = `chan-layers-v3-${symbol}-${timeframe}`;
    const saved = localStorage.getItem(versionedLayerKey);
    if (saved) {
      try {
        const parsed = JSON.parse(saved);
        const migrated = { pens: parsed.pens ?? true, centers: parsed.centers ?? true, components: parsed.components ?? false, dailyL2: parsed.dailyL2 ?? true, centerLevels: parsed.centerLevels || {} };
        setVisible(migrated);
      }
      catch { /* Ignore invalid legacy browser state. */ }
    } else setVisible({ pens: true, centers: true, components: false, dailyL2: true, centerLevels: {} });
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
  const setLayer = (key: "pens" | "centers" | "components" | "dailyL2", checked: boolean, level?: number) => {
    const next = level === undefined
      ? { ...visible, [key]: checked }
      : { ...visible, centerLevels: { ...visible.centerLevels, [String(level)]: checked } };
    setVisible(next);
    if (key === "dailyL2" && !checked) setSelectedProjection(null);
    if (symbol) localStorage.setItem(`chan-layers-v3-${symbol}-${timeframe}`, JSON.stringify(next));
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
    const baseline = new Map(drawingBaseline.current.map((x) => [x.id, x]));
    const create = draftDrawings.filter((x) => x.id < 0).map(({id: _id,symbol: _s,timeframe: _t,...x}) => ({...x,timeframe}));
    const update = draftDrawings.filter((x) => x.id > 0 && JSON.stringify(x) !== JSON.stringify(baseline.get(x.id))).map(({id,...x}) => ({id,drawing:{...x,timeframe}}));
    try {
      const result = await api<{items:Drawing[];version:string}>(`/api/drawings/${symbol}/batch`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({timeframe,base_version:data?.drawings_version || "",create,update,delete_ids:deletedDrawings}) });
      drawingBaseline.current = result.items; setDraftDrawings(result.items); setDeletedDrawings([]); drawingHistory.current=[]; drawingRedo.current=[];
      setDrawingTool(null); await load(false); setNotice({ kind: "success", text: "绘图已保存" });
    } catch (error) { setNotice({ kind: "error", text: (error as Error).message }); }
  }, [draftDrawings, deletedDrawings, symbol, timeframe, load, data?.drawings_version]);
  const cancelDrawings = useCallback(() => { setDraftDrawings(drawingBaseline.current); setDeletedDrawings([]); drawingHistory.current=[]; drawingRedo.current=[]; setDrawingTool(null); load(false); }, [load]);
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
  const selectNode = useCallback((node: StructureEntity | null) => { setSelected(node); setSelectedProjection(null); }, []);
  const selectProjection = useCallback((projection: DailyL2Projection | null) => { setSelectedProjection(projection); setSelected(null); }, []);
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
  const clearGroupDrag = () => {
    setDragGroupId(null);
    setGroupDropTarget(null);
  };
  const reorderGroup = async (targetId: string, position: GroupDropPosition) => {
    clearGroupDrag();
    if (dragGroupId === null || sectionSaving.current) return;
    const previous = sectionOrder;
    const sections = normalizeSectionOrder(previous, watchlistGroups).map((key, sort_order) => ({key, sort_order}));
    const reordered = reorderWatchlistSections(sections, dragGroupId, targetId, position);
    if (reordered === sections) return;
    sectionRevision.current += 1;
    sectionSaving.current = true;
    setGroupOrderSaving(true);
    setSectionOrder(reordered.map((section) => section.key));
    try {
      const saved = await api<{section_order:string[]}>("/api/watchlist/order", {method:"PUT", headers:{"Content-Type":"application/json"}, body:JSON.stringify({section_keys:reordered.map((section)=>section.key)})});
      setSectionOrder(saved.section_order);
    } catch (error) {
      setSectionOrder(previous);
      setNotice({kind:"error", text:`分组排序失败：${(error as Error).message}`});
    } finally { sectionRevision.current += 1; sectionSaving.current = false; setGroupOrderSaving(false); }
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
    prev = timeframe === "1" ? (data?.previous_close ? { close: data.previous_close } : undefined) : data?.bars.at(-2),
    change = quote?.change_pct ?? (last && prev && Number.isFinite(last.close) && Number.isFinite(prev.close) && prev.close !== 0
      ? (last.close / prev.close - 1) * 100
      : null),
    changeAmount = quote?.change ?? (last && prev && Number.isFinite(last.close) && Number.isFinite(prev.close)
      ? last.close - prev.close
      : null),
    quoteTone = change === null || change === 0 ? "" : change > 0 ? "rise" : "fall",
    formattedChange = change === null ? "--" : `${change >= 0 ? "+" : ""}${change.toFixed(2)}%`;
  const ungroupedStocks = watchlistUngroupedStocks(stocks, memberships);
  const groupDragProps = (key: string) => ({
    className: `watchlist-group-head${groupDropTarget?.id === key ? ` group-drop-${groupDropTarget.position}` : ""}`,
    draggable: !groupOrderSaving,
    title: "拖动分组标题排序；上半部插入前面，下半部插入后面",
    onDragStart: (event: DragEvent<HTMLDivElement>) => {
      if (groupOrderSaving || (event.target as HTMLElement).closest(".group-actions,.group-add-stock")) { event.preventDefault(); return; }
      event.stopPropagation();
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", key);
      setDragStock(null);
      setDragGroupId(key);
      setGroupDropTarget(null);
      setGroupMenuId(null);
      setMembershipMenu(null);
    },
    onDragEnd: clearGroupDrag,
    onDragOver: (event: DragEvent<HTMLDivElement>) => {
      if (dragGroupId === null || dragGroupId === key || groupOrderSaving) return;
      event.preventDefault();
      event.stopPropagation();
      event.dataTransfer.dropEffect = "move";
      const bounds = event.currentTarget.getBoundingClientRect();
      setGroupDropTarget({id:key, position:groupDropPosition(event.clientY, bounds.top, bounds.height)});
    },
    onDragLeave: (event: DragEvent<HTMLDivElement>) => {
      if (!event.currentTarget.contains(event.relatedTarget as globalThis.Node | null)) setGroupDropTarget(null);
    },
    onDrop: (event: DragEvent<HTMLDivElement>) => {
      if (dragGroupId === null || groupOrderSaving) return;
      event.preventDefault();
      event.stopPropagation();
      const bounds = event.currentTarget.getBoundingClientRect();
      reorderGroup(key, groupDropPosition(event.clientY, bounds.top, bounds.height));
    },
  });
  const touchGroupTarget = (event: ReactPointerEvent<SVGSVGElement>) => {
    const head = document.elementFromPoint(event.clientX, event.clientY)?.closest<HTMLElement>(".watchlist-group-head");
    const key = head?.closest<HTMLElement>("[data-section-key]")?.dataset.sectionKey;
    if (!head || !key || key === dragGroupId) return null;
    const bounds = head.getBoundingClientRect();
    return {id:key, position:groupDropPosition(event.clientY, bounds.top, bounds.height)};
  };
  const renderGroupGrip = (key: string) => <GripVertical className="group-drag-handle" size={14} aria-hidden="true"
    onPointerDown={(event) => {
      if (event.pointerType === "mouse" || sectionSaving.current) return;
      event.preventDefault();
      event.currentTarget.setPointerCapture(event.pointerId);
      setDragStock(null);
      setDragGroupId(key);
      setGroupDropTarget(null);
      setGroupMenuId(null);
      setMembershipMenu(null);
    }}
    onPointerMove={(event) => {
      if (event.pointerType === "mouse" || !event.currentTarget.hasPointerCapture(event.pointerId)) return;
      event.preventDefault();
      setGroupDropTarget(touchGroupTarget(event));
    }}
    onPointerUp={(event) => {
      if (event.pointerType === "mouse" || !event.currentTarget.hasPointerCapture(event.pointerId)) return;
      event.preventDefault();
      const target = touchGroupTarget(event);
      event.currentTarget.releasePointerCapture(event.pointerId);
      if (target) void reorderGroup(target.id, target.position);
      else clearGroupDrag();
    }}
    onPointerCancel={clearGroupDrag}
  />;
  const renderStockRows = (items: Stock[], viewKey: string, groupId: number | null, canReorder: boolean) => items.map((stock) => {
    const menuKey = `${viewKey}:${stock.symbol}`;
    const quote = stock.symbol === symbol && data?.symbol === symbol
      ? chartQuoteToWatchlistQuote(symbol, data.quote) : watchlistQuotes[stock.symbol];
    const quoteChange = quote?.change_pct;
    return <div key={menuKey} draggable={canReorder} className={`stock-row ${symbol === stock.symbol ? "active" : ""}`}
      onDragStart={(event) => {
        event.stopPropagation();
        clearGroupDrag();
        if (canReorder) setDragStock({symbol:stock.symbol,viewKey,groupId});
      }}
      onDragEnd={() => setDragStock(null)}
      onDragOver={(event) => { if (canReorder) event.preventDefault(); }}
      onDrop={(event) => { event.stopPropagation(); if (canReorder) reorderStock(stock.symbol, viewKey, groupId); }}>
      <button className="stock" onClick={() => setSymbol(stock.symbol)}>
        <span>
          <b>{stock.name || "未命名"}</b>
          <small>{stock.symbol}</small>
          {syncLabel(stock.sync_status) && <small className={`sync-${stock.sync_status}`}>{syncLabel(stock.sync_status)}</small>}
        </span>
        <span className={`watchlist-quote ${quoteChange == null || quoteChange === 0 ? "" : quoteChange > 0 ? "rise" : "fall"}`}
          title={`${quote?.source || "腾讯行情"} · ${quote?.quote_time || "暂无报价时间"}${quote?.error ? ` · ${quote.error}` : ""}`}>
          {formatPrice(quote?.latest ?? undefined)}
          <small>{quoteChange == null ? "--" : `${quoteChange > 0 ? "+" : ""}${quoteChange.toFixed(2)}%`}</small>
          <small className={`quote-state quote-state-${quote?.status || "pending"}`}>{watchlistQuoteLabel(quote)}</small>
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
  const renderSystemSection = (key: "all"|"ungrouped", label: string, items: Stock[]) => <section className={`watchlist-section system-group${dragGroupId === key ? " group-dragging" : ""}`} key={key} data-section-key={key}>
    <div {...groupDragProps(key)}>
      {renderGroupGrip(key)}
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
              <button className="icon" title="新建分组" aria-label="新建分组" disabled={groupOrderSaving} onClick={createGroup}><FolderPlus size={17}/></button>
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
          <div className="watchlist-quote-status" role="status">{quoteStatus}</div>
          <div className="stock-list watchlist-tree">
            {normalizeSectionOrder(sectionOrder, watchlistGroups).map((key) => {
              if (key === "all") return renderSystemSection("all", "全部", stocks);
              if (key === "ungrouped") return renderSystemSection("ungrouped", "未分组", ungroupedStocks);
              const group = watchlistGroups.find((item) => `group-${item.id}` === key)!;
              const items = stocksForGroup(group.id);
              const isCollapsed = collapsedGroups[key] ?? true;
              return <section className={`watchlist-section custom-group${dragGroupId === key ? " group-dragging" : ""}`} key={key} data-section-key={key}>
                <div {...groupDragProps(key)}>
                  {renderGroupGrip(key)}
                  <button className="group-toggle" onClick={() => toggleGroup(key)} aria-expanded={!isCollapsed}>
                    {isCollapsed ? <ChevronRight size={15}/> : <ChevronDown size={15}/>}<Folder className="group-folder" size={14}/><b title={group.name}>{group.name}</b><small>{items.length}</small>
                  </button>
                  <div className="group-actions">
                    <button className="icon" title={`添加到${group.name}`} aria-label={`添加到${group.name}`} onClick={() => openStockSearch(group.id)}><Plus size={14}/></button>
                    <button className="icon" title="分组菜单" disabled={groupOrderSaving} aria-label={`${group.name}分组菜单`} onClick={() => setGroupMenuId((current) => current === group.id ? null : group.id)}><MoreVertical size={15}/></button>
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
              <span className="quote-date">{quote?.quote_time?.replace("T", " ").slice(0, 19) || quote?.trade_date || last?.trade_date || "暂无行情"}</span>
              {(quote || last) && <>
                <strong className={`quote-last ${quoteTone}`}>{formatPrice(quote?.latest ?? last?.close)}</strong>
                <span className={`quote-change-amount ${quoteTone}`}>{changeAmount === null ? "--" : `${changeAmount >= 0 ? "+" : ""}${formatPrice(changeAmount)}`}</span>
                <span className={`quote-change ${quoteTone}`}><small>涨跌幅</small>{formattedChange}</span>
              </>}
            </div>
            {data?.intraday_refresh && <div className="intraday-refresh-status" role="status">
              {data.intraday_refresh.market_status} · 数据：{data.intraday_refresh.latest_data_at || "暂无数据"}
              {data.period_refresh?.state === "provisional" && " · 形成中"}
              {timeframe !== "1" && ["5", "30", "d", "w", "m"].includes(timeframe) && ` · ${data.structure_preview ? "实时预览" : "正式结构"}`}
              {data.forming_bar?.is_forming && " · 最后一根未完成"}
              {!data.intraday_refresh.is_today && data.intraday_refresh.data_date && "（非今日行情）"}
              {data.intraday_refresh.last_success_at && ` · 获取：${data.intraday_refresh.last_success_at.slice(11, 19)}`}
              {intradayError && ` · ${intradayError}`}
            </div>}
            {(quote || last) && <div className="quote-details">
              <span>昨收：<b>{formatPrice(quote?.previous_close)}</b></span>
              <span>今开：<b>{formatPrice(quote?.open ?? last?.open)}</b></span>
              <span>最高：<b className="rise">{formatPrice(quote?.high ?? last?.high)}</b></span>
              <span>最低：<b className="fall">{formatPrice(quote?.low ?? last?.low)}</b></span>
              <span>成交量：<b>{formatVolume(quote?.volume ?? last?.volume)}</b></span>
              <span>成交额：<b>{formatCompactNumber(timeframe === "1" ? quote?.amount : quote?.amount ?? last?.amount)}</b></span>
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
            selectedProjectionId={selectedProjection?.id || null}
            onProjectionSelect={selectProjection}
            height={height}
            visible={visible}
            subplotIndicators={subplotIndicators}
            subplotVisible={subplotVisible}
            mainIndicator={mainIndicator}
            onMainIndicator={changeMainIndicator}
            onOpenIndicatorSettings={openIndicatorSettings}
            onSubplotIndicator={setSubplotIndicator}
            onLayerToggle={(key, level) => level === undefined ? setLayer(key, !visible[key]) : setLayer(key, visible.centerLevels[String(level)] === false, level)}
            drawingTool={drawingTool}
            onDrawingCreate={createDraftDrawing}
            drawingItems={draftDrawings}
            drawingOpen={drawingOpen}
            onDrawingUpdate={updateDraftDrawing}
            onDrawingDelete={deleteDraftDrawing}
            onDrawingSelect={setSelectedDrawing}
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
                {data?.pens.length || 0} 笔 · {(data ? displayCenterCollection(data) : []).filter((item) => item.display_role === "active" && visible.centers && (isReferenceProfile(data!) || visible.centerLevels[String(item.level)] !== false)).length} 活动中枢 · {(data ? displayCenterCollection(data) : []).filter((item) => item.display_role === "constituent" && visible.centers && (isReferenceProfile(data!) || visible.centerLevels[String(item.level)] !== false)).length} 组成中枢{data && isReferenceProfile(data) ? ` · ${visible.dailyL2 ? data.overlays?.daily_l2.centers.length || 0 : 0} 日线 L2 参考` : ""}
              </dd>
            </dl>
          </section>
          {data && !isReferenceProfile(data) && (!!data.promotion_candidates.length || data.centers.some((center) => center.boundary_status === "dynamic" || center.boundary_status === "fixed")) && (
            <section className="promotion-panel">
              <div className="panel-head"><b>中枢升级证据</b><Minimize2 size={15} /></div>
              <div className="promotion-summary">
                <span>延伸候选 <b>{data.promotion_candidates.filter((item) => item.candidate_source === "extension_decomposition").length}</b></span>
                <span>扩展未解 <b>{data.promotion_candidates.filter((item) => item.candidate_source === "expansion_decomposition" && item.status === "unresolved").length}</b></span>
                <span>动态父中枢 <b>{data.centers.filter((item) => item.boundary_status === "dynamic").length}</b></span>
                <span>固定父中枢 <b>{data.centers.filter((item) => item.boundary_status === "fixed").length}</b></span>
              </div>
              <div className="promotion-list">
                {data.promotion_candidates.map((candidate) => {
                  const parent = candidate.selected_parent_family_id
                    ? data.center_revisions.filter((item) => item.family_id === candidate.selected_parent_family_id).sort((a, b) => b.revision_no - a.revision_no)[0]
                    : undefined;
                  return <button key={candidate.id} disabled={!parent} onClick={() => parent && setSelected(parent)}>
                    <span><b>{promotionSourceLabel(candidate)} · L{candidate.child_level}→L{candidate.parent_level}</b><small>{candidate.start_date.slice(0, 10)} 至 {candidate.end_date.slice(0, 10)} · {promotionStatusLabel(candidate.status)}</small></span>
                    <code>{candidate.missing_evidence.map((item) => item.code).join(" / ") || (candidate.selected_segment_proof_ids || candidate.proof_ids).join(" / ")}</code>
                  </button>;
                })}
                {data.centers.filter((center) => center.boundary_status === "dynamic" || center.boundary_status === "fixed").map((center) => <button key={center.id} onClick={() => setSelected(center)}>
                  <span><b>L{center.level} 父中枢 · {promotionStatusLabel(center.boundary_status)}</b><small>{center.start_date.slice(0, 10)} 至 {center.end_date.slice(0, 10)}</small></span>
                  <code>{center.formation_modes.join(" / ")}</code>
                </button>)}
              </div>
            </section>
          )}
          {data && (data.center_candidates || []).length > 0 && (
            <section className="promotion-panel center-candidate-panel">
              <div className="panel-head"><b>中枢候选与所有权</b><Minimize2 size={15} /></div>
              <div className="promotion-summary">
                <span>候选 <b>{(data.center_candidates || []).length}</b></span>
                <span>已选 <b>{(data.center_candidates || []).filter((item) => item.status === "selected").length}</b></span>
                <span>淘汰 <b>{(data.center_candidates || []).filter((item) => item.status === "rejected").length}</b></span>
              </div>
              <div className="promotion-list">
                {(data.center_candidates || []).slice(0, 24).map((candidate) => <button key={candidate.id} disabled={!candidate.selected_center_family_id} onClick={() => {
                  const center = data.center_revisions.find((item) => item.family_id === candidate.selected_center_family_id);
                  if (center) setSelected(center);
                }}>
                  <span><b>{candidate.status === "selected" ? "已选" : "淘汰"} · {candidate.source_unit_ids.slice(0, 3).join(" / ")}</b><small>{candidate.start_date.slice(0, 10)} 至 {candidate.end_date.slice(0, 10)} · {candidate.direction === "up" ? "向上" : "向下"}</small></span>
                  <code>{candidate.rejection_code || `[${candidate.zd}, ${candidate.zg}]`}</code>
                </button>)}
              </div>
            </section>
          )}
          <section>
            <div className="panel-head">
              <b>证据检查器</b>
              <Minimize2 size={15} />
            </div>
            {selectedProjection && data ? <DailyL2Evidence center={selectedProjection} data={data} /> : selected ? (
              <div className="evidence">
                <div className="selected-structure-summary">
                  <strong>{selected.kind === "center" && data ? centerDisplayLabel(data, selected) : `${kindName(selected.kind)} #${selected.ordinal + 1}`}</strong>
                  <span>{selected.direction === "up" ? "向上" : selected.direction === "down" ? "向下" : selected.status}</span>
                </div>
                <h3>
                  {weeklyCoreSelected ? "三笔核心" : kindName(selected.kind)} #{selected.ordinal + 1}
                </h3>
                <p>
                  {selected.kind === "center" ? selectedCenterRange?.start_date || "--" : selected.start_date}
                  <br />至 {selected.kind === "center" ? selectedCenterRange?.end_date || "--" : selected.end_date}
                </p>
                <dl>
                  <dt>状态</dt>
                  <dd>{selected.status}</dd>
                  {selected.kind === "center" && <><dt>计算活动状态</dt><dd>{selected.active ? "活动中枢" : "非活动历史修订（不参与重新递归）"}</dd></>}
                  {isCenterNode(selected) && <>
                    {(() => { const appearance = levelStructureAppearance(theme === "light" ? "light" : "dark", timeframe, nodeLevel(selected)); return <>
                    <dt>结构级别</dt><dd>L{nodeLevel(selected)}</dd>
                    <dt>结构家族</dt><dd>{selected.family_id}</dd>
                    <dt>{selected.active ? "活动修订" : "历史证据修订"}</dt><dd>revision {selected.revision_no}</dd>
                    {!!selected.child_center_ids?.length && <><dt>组成中枢</dt><dd>{selected.child_center_ids.length} 个</dd></>}
                    <dt>配色参考（非结构周期）</dt><dd>{displayPeriodLabel(appearance.displayPeriod)}</dd>
                    <dt>计算来源</dt><dd>{calculationSourceLabel(timeframe)}</dd>
                    <dt>语义对应</dt><dd>{centerSemanticLabel(timeframe, nodeLevel(selected))}</dd>
                    <dt>颜色标识</dt><dd>{appearance.colorKey}</dd>
                    </>; })()}
                  </>}
                  {selected.direction && <><dt>方向</dt><dd>{selected.direction === "up" ? "向上" : "向下"}</dd></>}
                  {selected.kind === "center" && !!selected.entry_unit_ids.length && <><dt>进入上下文</dt><dd>{selected.entry_unit_ids.join(" / ")}</dd></>}
                  {selected.kind === "center" && !!selected.core_unit_ids.length && <><dt>核心三单位</dt><dd>{selected.core_unit_ids.join(" / ")}</dd></>}
                  {selected.kind === "center" && selected.core_start_date && <><dt>核心范围</dt><dd>{selected.core_start_date}<br />至 {selected.core_end_date}</dd></>}
                  {selected.kind === "center" && !weeklyCoreSelected && !!selected.extension_unit_ids.length && <><dt>延伸单位</dt><dd>{selected.extension_unit_ids.length}</dd></>}
                  {selected.kind === "center" && !!selected.peripheral_unit_ids.length && <><dt>外围单位</dt><dd>{selected.peripheral_unit_ids.length}</dd></>}
                  {selected.kind === "center" && !!selected.departure_unit_ids.length && <><dt>离开候选</dt><dd>{selected.departure_unit_ids.length}</dd></>}
                  {selected.kind === "center" && !!selected.retest_unit_ids.length && <><dt>回试单位</dt><dd>{selected.retest_unit_ids.length}</dd></>}
                  {selected.kind === "center" && <><dt>ZD / ZG</dt><dd>{formatPrice(selected.zd)} / {formatPrice(selected.zg)}</dd></>}
                  {selected.kind === "center" && <><dt>DD / GG</dt><dd>{formatPrice(selected.dd)} / {formatPrice(selected.gg)}</dd></>}
                  {selected.kind === "center" && <><dt>形成类型 / 过程方向</dt><dd>{selected.formation_type === "pullback" ? "回调形成" : selected.formation_type === "rebound" ? "回升形成" : selected.formation_stage ? "方向待定" : "旧版证据"} / {selected.process_direction_at_formation || "--"}</dd><dt>方向确认时间</dt><dd>{selected.direction_established_at || "--"}</dd><dt>证据颜色</dt><dd>紫：核心 B；青：Z（含核心 A/C）；橙：进入；红：离开；绿：回试；粉：扩展见证</dd><dt>G / D</dt><dd>{formatPrice(selected.z_high_min)} / {formatPrice(selected.z_low_max)}</dd><dt>高级区间状态</dt><dd>{selected.boundary_status || "初始核心"}</dd>{selected.decomposition_proof?.segments.map((part,index) => <div key={index}><dt>构成段 {index+1}</dt><dd>{part.start_date} → {part.end_date}<br/>{formatPrice(part.low)} / {formatPrice(part.high)} · {part.status}</dd></div>)}</>}
                  {selected.kind === "center" && <><dt>Z 包络</dt><dd>{formatPrice(selected.fluctuation_dd)} / {formatPrice(selected.fluctuation_gg)}</dd><dt>上下文范围（不参与升级）</dt><dd>{formatPrice(selected.context_low)} / {formatPrice(selected.context_high)}</dd><dt>进入方向 / Z 方向</dt><dd>{selected.entry_direction || "--"} / {selected.z_direction || "子中枢系统"}</dd><dt>Z 单位</dt><dd>{selected.z_unit_ids.join(" / ") || "高级子修订证据"}</dd><dt>形成时间</dt><dd>{selected.formed_at || "--"}</dd>{!(data && isReferenceProfile(data)) && <><dt>升级时间</dt><dd>{selected.promotion_confirmed_at || "未升级"}</dd><dt>重叠见证</dt><dd>{selected.overlap_witness_unit_ids.join(" / ") || "--"}</dd><dt>连接组件</dt><dd>{selected.connection_component_ids.join(" / ") || "--"}</dd></>}</>}
                  <dt>确认时间</dt>
                  <dd>{selected.kind === "center" ? selected.formed_at : selected.confirmed_at || "等待确认"}</dd>
                  <dt>{weeklyCoreSelected ? "核心笔" : "构成笔"}</dt>
                  <dd>{weeklyCoreSelected && selected.kind === "center" ? selected.core_unit_ids.length : selected.source_pen_ids?.length || 0}</dd>
                  {selected.continuous_range_id !== undefined && <><dt>连续区间</dt><dd>R{selected.continuous_range_id}</dd></>}
                </dl>
                {data && !isReferenceProfile(data) && selected.kind === "center" && (() => { const center = selectedCenterEvidence(data, selected.id); return center ? <section><b>父中枢</b>{centerParents(data, center).map((parent) => <button key={parent.id} onClick={() => setSelected(parent)}>{centerDisplayLabel(data, parent)}</button>)}</section> : null; })()}
                {selected.kind === "center" && !!selected.child_center_ids.length && <details><summary>展开子中枢证据</summary>{selected.child_center_ids.map((identifier) => { const child = data?.center_revisions.find((item) => item.id === identifier); return child ? <button key={identifier} onClick={() => setSelected(child)}>L{child.level} · {child.core_start_date} · Z[{formatPrice(child.dd)}, {formatPrice(child.gg)}]</button> : <p key={identifier}>缺失子修订：{identifier}</p>; })}</details>}
                <p className="rules">{selected.evidence ? JSON.stringify(selected.evidence) : "暂无附加证据"}</p>
              </div>
            ) : (
              <p className="empty">{data && isReferenceProfile(data) ? "点击笔、笔中枢或日线 L2 参考查看证据" : "点击笔、中枢或 L2 晋级证据查看规则详情"}</p>
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
