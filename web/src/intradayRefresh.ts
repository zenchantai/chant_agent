import type { ChartData, IntradayRefresh } from "./types";

export function refreshDelay(state: IntradayRefresh): number {
  if (state.phase === "unknown") return 60_000;
  const untilTransition = state.next_transition_at
    ? Math.max(0, Date.parse(state.next_transition_at) - Date.parse(state.server_time)) : 60_000;
  if (state.phase === "trading") return untilTransition <= 15_000 ? untilTransition + 15_000 : 15_000;
  return Math.max(1000, untilTransition);
}

export function advanceSession(state: IntradayRefresh, elapsed: number): IntradayRefresh {
  const serverTime = Date.parse(state.server_time) + elapsed;
  const next = { ...state, server_time: new Date(serverTime).toISOString() };
  if (!state.next_transition_at || serverTime < Date.parse(state.next_transition_at)) return next;
  const day = state.next_transition_at.slice(0, 10);
  const clock = state.next_transition_at.slice(11, 16);
  if (clock === "09:30" || clock === "13:00") {
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
    quote: fresh.quote ?? current.quote, previous_close: fresh.previous_close,
    intraday_refresh: fresh.intraday_refresh, available: fresh.available || current.available,
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
