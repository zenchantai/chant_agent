export type WatchlistQuote = {
  symbol: string; latest: number | null; previous_close: number | null;
  change: number | null; change_pct: number | null; quote_time: string | null;
  source: string; status: "success" | "unavailable" | "error" | "stale" | "historical"; error: string | null;
};

export type WatchlistQuoteResponse = {
  quotes: WatchlistQuote[]; source?: string; error?: string; refresh_after_ms: number;
  server_time: string; phase: string; market_status: string;
};

export const chartQuoteToWatchlistQuote = (symbol: string, quote?: {
  latest?: number | null; previous_close?: number | null; change?: number | null; change_pct?: number | null;
  quote_time?: string | null; trade_date?: string; source?: string; status?: WatchlistQuote["status"];
} | null): WatchlistQuote | undefined => quote && quote.latest != null ? {
  symbol, latest:quote.latest, previous_close:quote.previous_close ?? null,
  change:quote.change ?? null, change_pct:quote.change_pct ?? null,
  quote_time:quote.quote_time || quote.trade_date || null, source:quote.source || "tencent",
  status:quote.status || "success", error:null,
} : undefined;

export const watchlistQuoteDelay = (response?: WatchlistQuoteResponse): number =>
  response?.refresh_after_ms === 15_000 ? 15_000 : 60_000;

export const mergeWatchlistQuotes = (
  current: Record<string, WatchlistQuote>, incoming: WatchlistQuote[], symbols: string[],
): Record<string, WatchlistQuote> => {
  const received = new Map(incoming.map((quote) => [quote.symbol, quote]));
  const next: Record<string, WatchlistQuote> = {};
  for (const symbol of symbols) {
    const previous = current[symbol];
    const fresh = received.get(symbol);
    if (!fresh) {
      if (previous) next[symbol] = {...previous, status:"error", error:"未返回该证券报价"};
    } else if (previous && (fresh.latest === null || (previous.quote_time && fresh.quote_time && fresh.quote_time < previous.quote_time))) {
      next[symbol] = {...previous, status:"error", error:fresh.error || "已保留最近有效报价"};
    } else next[symbol] = fresh;
  }
  return next;
};

export const watchlistQuoteLabel = (quote?: WatchlistQuote): string => {
  if (!quote) return "等待行情";
  if (quote.status === "error") return "更新失败";
  if (quote.status === "stale") return "数据延迟";
  if (quote.status === "historical") return "历史行情";
  if (quote.status === "unavailable") return "暂无行情";
  return quote.quote_time?.slice(11, 19) || "暂无行情";
};

export function startWatchlistQuoteRefresh(options: {
  fetch: (signal: AbortSignal) => Promise<WatchlistQuoteResponse>;
  onQuotes: (response: WatchlistQuoteResponse) => void;
  onError: (error: unknown) => void;
  visibility: Pick<Document, "hidden" | "addEventListener" | "removeEventListener">;
}) {
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let controller: AbortController | undefined;
  let running = false;
  let resumePending = false;
  let delay = 60_000;
  const run = async () => {
    if (stopped || options.visibility.hidden || running) return;
    running = true;
    const request = new AbortController();
    controller = request;
    try {
      const response = await options.fetch(request.signal);
      if (!stopped && !request.signal.aborted) {
        delay = watchlistQuoteDelay(response);
        options.onQuotes(response);
      }
    } catch (error) {
      if (!stopped && !request.signal.aborted) {
        delay = 60_000;
        options.onError(error);
      }
    } finally {
      running = false;
      controller = undefined;
      if (!stopped && !options.visibility.hidden) timer = setTimeout(run, resumePending ? 0 : delay);
      resumePending = false;
    }
  };
  const onVisibility = () => {
    clearTimeout(timer);
    if (options.visibility.hidden) controller?.abort();
    else if (running) resumePending = true;
    else void run();
  };
  options.visibility.addEventListener("visibilitychange", onVisibility);
  void run();
  return () => {
    stopped = true;
    clearTimeout(timer);
    controller?.abort();
    options.visibility.removeEventListener("visibilitychange", onVisibility);
  };
}
