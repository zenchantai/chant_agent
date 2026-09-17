from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta

from .intraday import TZ, session_state
from .providers import fetch_tencent_quotes


class WatchlistQuoteService:
    def __init__(self, store, intraday, clock=None, fetcher=None, monotonic=None):
        self.store = store
        self.intraday = intraday
        self.clock = clock or (lambda: datetime.now(TZ))
        self.fetcher = fetcher or fetch_tencent_quotes
        self.monotonic = monotonic or time.monotonic
        self._lock = threading.Lock()
        self._quotes = {}
        self._errors = {}
        self._attempts = {}

    def snapshot(self, current_symbol: str | None = None):
        with self._lock:
            all_symbols = self.store.watchlist_symbols()
            symbols = [symbol for symbol in all_symbols if symbol != current_symbol]
            now = self.clock().astimezone(TZ)
            calendar = self.intraday.calendar_days(
                (now.date() - timedelta(days=30)).isoformat(),
                (now.date() + timedelta(days=14)).isoformat(),
            )
            state = session_state(now, calendar)
            today = now.date().isoformat()
            clock = (now.hour, now.minute)
            if calendar.get(today) and (9, 15) <= clock < (9, 30):
                state.update(phase="auction", market_status="开盘集合竞价")
            active = state["phase"] in {"trading", "auction"}
            interval = 15 if active else 60
            key = (today, state["phase"])
            due = [symbol for symbol in symbols if symbol not in self._attempts
                   or self._attempts[symbol][0] != key
                   or self.monotonic() - self._attempts[symbol][1] >= interval]
            for start in range(0, len(due), 50):
                batch = due[start:start + 50]
                try:
                    fetched = {quote["symbol"]: quote for quote in self.fetcher(batch)}
                except Exception:
                    fetched = {}
                for symbol in batch:
                    quote = fetched.get(symbol)
                    error = "行情快照请求失败或缺少该证券"
                    if quote and quote.get("status") == "success":
                        try:
                            stamp = datetime.strptime(quote["quote_time"], "%Y%m%d%H%M%S").replace(tzinfo=TZ)
                            if stamp > self.clock().astimezone(TZ) + timedelta(seconds=60):
                                raise ValueError("报价时间在未来")
                            normalized = {**quote, "quote_time": stamp.isoformat(), "error": None}
                            previous = self._quotes.get(symbol)
                            if previous and normalized["quote_time"] < previous["quote_time"]:
                                error = "报价早于已有缓存"
                            else:
                                self._quotes[symbol] = normalized
                                error = None
                        except (ValueError, TypeError, KeyError):
                            error = "报价时间无效"
                    elif quote:
                        error = quote.get("error") or "暂无有效报价"
                    self._errors[symbol] = error
                    self._attempts[symbol] = (key, self.monotonic())
            active_symbols = self.store.watchlist_symbols()
            for cache in (self._quotes, self._errors, self._attempts):
                for symbol in set(cache) - set(active_symbols):
                    del cache[symbol]
            current_symbols = [symbol for symbol in active_symbols if symbol != current_symbol]
            now = self.clock().astimezone(TZ)
            trading_days = [day for day, trading in calendar.items() if trading and day <= today]
            expected_day = max(trading_days, default=None)
            quotes = []
            for symbol in current_symbols:
                quote = dict(self._quotes.get(symbol) or {
                    "symbol": symbol, "latest": None, "previous_close": None, "change": None,
                    "change_pct": None, "quote_time": None, "source": "tencent",
                })
                error = self._errors.get(symbol)
                status = "success"
                if quote["quote_time"] is None:
                    status = "unavailable"
                elif error:
                    status = "error"
                elif expected_day and quote["quote_time"][:10] < expected_day:
                    status = "historical"
                elif state["phase"] == "unknown":
                    status = "stale"
                elif active and (now - datetime.fromisoformat(quote["quote_time"])).total_seconds() > 120:
                    status = "stale"
                quote.update(status=status, error=error)
                quotes.append(quote)
            return {"quotes": quotes, **state, "server_time": now.isoformat(),
                    "refresh_after_ms": interval * 1000, "source": "tencent"}
