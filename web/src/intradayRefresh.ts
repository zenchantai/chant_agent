import type { ChartData, IntradayRefresh } from "./types";

export function refreshDelay(state: IntradayRefresh): number {
  if (state.next_bar_finalize_at) {
    const untilFinalize = Date.parse(state.next_bar_finalize_at) - Date.parse(state.server_time);
    if (untilFinalize > 0 && untilFinalize <= 15_000) return untilFinalize;
  }
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
    movements: fresh.movements ?? current.movements, movement_revisions: fresh.movement_revisions ?? current.movement_revisions,
    promotion_candidates: fresh.promotion_candidates ?? current.promotion_candidates,
    promotion_candidate_revisions: fresh.promotion_candidate_revisions ?? current.promotion_candidate_revisions,
    components: fresh.components ?? current.components, points: fresh.points ?? current.points,
    point_revisions: fresh.point_revisions ?? current.point_revisions,
    relations: fresh.relations ?? current.relations, issues: fresh.issues ?? current.issues,
    levels: fresh.levels ?? current.levels, unassigned_by_level: fresh.unassigned_by_level ?? current.unassigned_by_level,
    center_levels: fresh.center_levels ?? current.center_levels, movement_levels: fresh.movement_levels ?? current.movement_levels,
    run_id: fresh.run_id, structure_version: fresh.structure_version,
    definition_version: fresh.definition_version, calculator_fingerprint: fresh.calculator_fingerprint,
    market_version: fresh.market_version, coverage: fresh.coverage,
    quote: fresh.quote ?? current.quote, previous_close: fresh.previous_close,
    intraday_refresh: fresh.intraday_refresh, available: fresh.available || current.available,
    forming_bar: fresh.forming_bar,
    structure_preview: fresh.structure_preview, structure_persisted: fresh.structure_persisted,
    has_more: false, next_before: undefined };
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
