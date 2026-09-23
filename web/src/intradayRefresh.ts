import type { ChartData, ChartRealtimeResponse, IntradayRefresh, PeriodRefresh } from "./types";

type RefreshState = IntradayRefresh | PeriodRefresh;

export function refreshDelay(state: RefreshState): number {
  const finalizeAt = "phase" in state
    ? (state.next_period_finalize_at ?? state.next_bar_finalize_at)
    : state.next_period_finalize_at;
  if (finalizeAt) {
    const untilFinalize = Date.parse(finalizeAt) - Date.parse(state.server_time);
    if (untilFinalize > 0 && untilFinalize <= 15_000) return untilFinalize;
  }
  if (!("phase" in state)) return 15_000;
  if (state.phase === "unknown") return 60_000;
  const untilTransition = state.next_transition_at
    ? Math.max(0, Date.parse(state.next_transition_at) - Date.parse(state.server_time)) : 60_000;
  if (state.phase === "trading" || state.phase === "auction") return untilTransition <= 15_000 ? untilTransition + 15_000 : 15_000;
  return Math.max(1000, untilTransition);
}

export function advanceSession(state: IntradayRefresh, elapsed: number): IntradayRefresh {
  const serverTime = Date.parse(state.server_time) + elapsed;
  const next = { ...state, server_time: new Date(serverTime).toISOString() };
  if (!state.next_transition_at || serverTime < Date.parse(state.next_transition_at)) return next;
  const day = state.next_transition_at.slice(0, 10);
  const clock = state.next_transition_at.slice(11, 16);
  if (clock === "09:15") {
    next.phase = "auction";
    next.next_transition_at = `${day}T09:30:00+08:00`;
  } else if (clock === "09:30" || clock === "13:00") {
    next.phase = "trading";
    next.next_transition_at = `${day}T${clock === "09:30" ? "11:30" : "15:00"}:00+08:00`;
  } else if (clock === "11:30") {
    next.phase = "lunch";
    next.next_transition_at = `${day}T13:00:00+08:00`;
  } else if (clock === "15:00") {
    next.phase = "closed";
    next.next_transition_at = new Date(Date.parse(`${day}T00:00:00+08:00`) + 86_400_000).toISOString();
  } else {
    next.phase = "unknown";
    next.next_transition_at = null;
  }
  return next;
}

export function mergeIntradayData(current: ChartData | null, fresh: ChartData): ChartData {
  if (!current || current.symbol !== fresh.symbol || current.timeframe !== fresh.timeframe) return fresh;
  return { ...current, bars: fresh.bars.length ? fresh.bars : current.bars,
    indicators: fresh.bars.length ? fresh.indicators : current.indicators,
    pens: fresh.pens ?? current.pens, pen_diagnostics: fresh.pen_diagnostics ?? current.pen_diagnostics,
    centers: fresh.centers ?? current.centers, center_revisions: fresh.center_revisions ?? current.center_revisions,
    display_centers: fresh.display_centers, display_center_levels: fresh.display_center_levels,
    overlays: fresh.overlays, calculation_profile: fresh.calculation_profile,
    promotion_candidates: fresh.promotion_candidates ?? current.promotion_candidates,
    promotion_candidate_revisions: fresh.promotion_candidate_revisions ?? current.promotion_candidate_revisions,
    components: fresh.components ?? current.components,
    relations: fresh.relations ?? current.relations, issues: fresh.issues ?? current.issues,
    levels: fresh.levels ?? current.levels, unassigned_by_level: fresh.unassigned_by_level ?? current.unassigned_by_level,
    center_levels: fresh.center_levels ?? current.center_levels,
    run_id: fresh.run_id, structure_version: fresh.structure_version,
    definition_version: fresh.definition_version, calculator_fingerprint: fresh.calculator_fingerprint,
    market_version: fresh.market_version, coverage: fresh.coverage,
    quote: fresh.quote ?? current.quote, previous_close: fresh.previous_close,
    intraday_refresh: fresh.intraday_refresh, period_refresh: fresh.period_refresh ?? current.period_refresh,
    available: fresh.available || current.available,
    forming_bar: fresh.forming_bar,
    structure_preview: fresh.structure_preview, structure_persisted: fresh.structure_persisted,
    has_more: current.has_more || fresh.has_more, next_before: current.next_before ?? fresh.next_before };
}

const upsertStamped = <T extends {trade_date:string}>(current: T[], incoming: T[]): T[] => {
  if (!incoming.length) return current;
  return Array.from(new Map([...current, ...incoming].map((item) => [item.trade_date, item])).values())
    .sort((left, right) => left.trade_date.localeCompare(right.trade_date));
};

const mergeStructureRows = <T extends {id?:string;start_date?:string;end_date?:string}>(
  current: T[], incoming: T[] | undefined, removed: string[] | undefined, replaceFrom?: string | null,
): T[] => {
  if (!incoming) return current;
  if (!replaceFrom) return incoming;
  const removedIds = new Set(removed || []);
  const retained = current.filter((item) => {
    if (item.id && removedIds.has(item.id)) return false;
    const end = String(item.end_date || item.start_date || "");
    return Boolean(end) && end < replaceFrom;
  });
  return Array.from(new Map([...retained, ...incoming].map((item, index) => [item.id || `row-${index}`, item])).values());
};

/** Apply the lightweight realtime contract without replacing historical pages or formal structures. */
export function mergeRealtimeData(current: ChartData | null, fresh: ChartRealtimeResponse): ChartData | null {
  if (!current || current.symbol !== fresh.symbol || current.timeframe !== fresh.timeframe) return current;
  const update = fresh.structure_update;
  const structure = update?.structure;
  const removed = update?.removed_ids || {};
  const replaceFrom = update?.replace_from;
  const next: ChartData = {
    ...current,
    bars: upsertStamped(current.bars, fresh.bar_upserts || []),
    indicators: {
      macd: upsertStamped(current.indicators.macd || [], fresh.indicator_upserts?.macd || []),
      ma: upsertStamped(current.indicators.ma || [], fresh.indicator_upserts?.ma || []),
      boll: upsertStamped(current.indicators.boll || [], fresh.indicator_upserts?.boll || []),
    },
    quote: fresh.quote ?? current.quote,
    previous_close: fresh.quote?.previous_close ?? current.previous_close,
    forming_bar: fresh.forming_bar,
    intraday_refresh: fresh.intraday_refresh,
    period_refresh: fresh.period_refresh ?? current.period_refresh,
    market_version: fresh.market_version,
    structure_version: fresh.structure_version || current.structure_version,
    available: current.available || fresh.bar_upserts.length > 0,
    // Historical pagination belongs to the initial/page API and must survive
    // any number of realtime updates.
    has_more: current.has_more,
    next_before: current.next_before,
  };
  if (!structure || !update) return next;
  next.pens = mergeStructureRows(current.pens, structure.pens, removed.pens, replaceFrom);
  next.components = mergeStructureRows(current.components, structure.components, removed.components, replaceFrom);
  next.centers = mergeStructureRows(current.centers, structure.centers, removed.centers, replaceFrom);
  next.center_revisions = mergeStructureRows(current.center_revisions, structure.center_revisions, removed.center_revisions, replaceFrom);
  next.center_candidates = mergeStructureRows(current.center_candidates || [], structure.center_candidates, removed.center_candidates, replaceFrom);
  next.center_candidate_revisions = mergeStructureRows(current.center_candidate_revisions || [], structure.center_candidate_revisions, removed.center_candidate_revisions, replaceFrom);
  next.segment_proofs = mergeStructureRows(current.segment_proofs || [], structure.segment_proofs, removed.segment_proofs, replaceFrom);
  next.segment_proof_revisions = mergeStructureRows(current.segment_proof_revisions || [], structure.segment_proof_revisions, removed.segment_proof_revisions, replaceFrom);
  next.promotion_candidates = mergeStructureRows(current.promotion_candidates || [], structure.promotion_candidates, removed.promotion_candidates, replaceFrom);
  next.promotion_candidate_revisions = mergeStructureRows(current.promotion_candidate_revisions || [], structure.promotion_candidate_revisions, removed.promotion_candidate_revisions, replaceFrom);
  next.relations = mergeStructureRows(current.relations as any[], structure.relations as any[], removed.relations, replaceFrom);
  next.issues = structure.issues ?? current.issues;
  next.levels = structure.levels ?? current.levels;
  next.unassigned_by_level = structure.unassigned_by_level ?? current.unassigned_by_level;
  next.display_centers = structure.display_centers ?? current.display_centers;
  next.display_center_levels = structure.display_center_levels ?? current.display_center_levels;
  next.center_levels = structure.display_center_levels ?? structure.levels ?? current.center_levels;
  if (update.meta.run_id !== undefined) next.run_id = Number(update.meta.run_id);
  next.definition_version = String(update.meta.definition_version ?? current.definition_version);
  next.calculator_fingerprint = String(update.meta.calculator_fingerprint ?? current.calculator_fingerprint);
  next.max_available_center_level = Number(update.meta.max_level ?? current.max_available_center_level);
  next.structure_preview = false;
  next.structure_persisted = true;
  return next;
}

export function startIntradayRefresh(options: {
  fetch: (signal: AbortSignal) => Promise<IntradayRefresh>;
  initial?: IntradayRefresh;
  onError: (error: unknown) => void;
  visibility: Pick<Document, "hidden" | "addEventListener" | "removeEventListener">;
}) {
  let stopped = false;
  let generation = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let controller: AbortController | undefined;
  let state = options.initial;
  let receivedAt = Date.now();
  const run = async () => {
    if (stopped || options.visibility.hidden) return;
    const current = ++generation;
    const request = new AbortController();
    let boundaryDelay: number | undefined;
    controller = request;
    try {
      const fresh = await options.fetch(request.signal);
      if (stopped || current !== generation) return;
      if (state?.phase === "trading" && fresh.phase !== "trading" && state.next_transition_at) {
        const remaining = Date.parse(state.next_transition_at) + 15_000 - Date.parse(fresh.server_time);
        if (remaining > 0 && remaining <= 15_000) boundaryDelay = remaining;
      }
      state = fresh;
      receivedAt = Date.now();
    } catch (error) {
      if (stopped || current !== generation) return;
      options.onError(error);
      if (state) state = advanceSession(state, Date.now() - receivedAt);
      receivedAt = Date.now();
    } finally {
      if (!stopped && current === generation && !options.visibility.hidden) {
        controller = undefined;
        timer = setTimeout(run, boundaryDelay ?? (state ? refreshDelay(state) : 60_000));
      }
    }
  };
  const cancel = () => {
    generation += 1;
    clearTimeout(timer);
    controller?.abort();
    controller = undefined;
  };
  const onVisibility = () => {
    cancel();
    if (!options.visibility.hidden) void run();
  };
  options.visibility.addEventListener("visibilitychange", onVisibility);
  void run();
  return () => {
    stopped = true;
    cancel();
    options.visibility.removeEventListener("visibilitychange", onVisibility);
  };
}
