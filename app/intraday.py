from __future__ import annotations

import math
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .providers import fetch_tencent, fetch_trade_calendar
from .store import Store
from .indicators import calculate_bollinger, calculate_macd, calculate_moving_averages
from .rules import PERIOD_DEFINITION_VERSION

TZ = ZoneInfo("Asia/Shanghai")


def session_state(now: datetime, calendar: dict[str, bool]) -> dict:
    now = now.astimezone(TZ)
    today = now.date().isoformat()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if today not in calendar:
        return {"phase": "unknown", "market_status": "日历未知", "next_transition_at": None}
    morning = midnight.replace(hour=9, minute=30)
    lunch = midnight.replace(hour=11, minute=30)
    afternoon = midnight.replace(hour=13)
    close = midnight.replace(hour=15)
    phase, transition = "closed", None
    if calendar[today]:
        if now < morning:
            transition = morning
        elif now < lunch:
            phase, transition = "trading", lunch
        elif now < afternoon:
            phase, transition = "lunch", afternoon
        elif now < close:
            phase, transition = "trading", close
    if transition is None:
        for offset in range(1, 15):
            candidate = midnight + timedelta(days=offset)
            day = candidate.date().isoformat()
            if day not in calendar:
                transition = candidate
                break
            if calendar[day]:
                transition = candidate.replace(hour=9, minute=30)
                break
        transition = transition or midnight + timedelta(days=1)
    return {"phase": phase, "market_status": {"trading": "交易中", "lunch": "午间休市", "closed": "已休市"}[phase],
            "next_transition_at": transition.isoformat()}


def validate_rows(rows: list[dict], now: datetime) -> list[dict]:
    if not rows:
        raise ValueError("行情源未返回分时数据")
    latest_day = max(row["trade_date"][:10] for row in rows)
    result = {}
    for row in rows:
        if row["trade_date"][:10] != latest_day:
            continue
        stamp = datetime.strptime(row["trade_date"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)
        if stamp > now:
            continue
        clock = (stamp.hour, stamp.minute)
        values = [float(row[field]) for field in ("open", "high", "low", "close", "volume")]
        amount = float(row.get("amount", 0))
        if (stamp.second or not ((9, 30) <= clock <= (11, 30) or (13, 0) <= clock <= (15, 0))
                or not all(math.isfinite(value) for value in [*values, amount])
                or min(values[:4]) <= 0 or values[4] < 0 or amount < 0
                or row["low"] > min(row["open"], row["close"])
                or row["high"] < max(row["open"], row["close"])):
            raise ValueError("行情源返回无效分时数据")
        result[row["trade_date"]] = row
    if not result:
        raise ValueError("行情源尚未返回已到时间的分时数据")
    return [result[stamp] for stamp in sorted(result)]


class IntradayService:
    def __init__(self, store: Store, clock=None):
        self.store = store
        self.clock = clock or (lambda: datetime.now(TZ))
        self._guard = threading.Lock()
        self._locks: dict[tuple, threading.Lock] = {}
        self._attempts: dict[tuple, dict] = {}
        self._calendar_lock = threading.Lock()
        self._calendar_attempt = float("-inf")
        self._calendar_error = None
        self._future_calendar: dict[str, bool] = {}

    def calendar_days(self, start: str, end: str) -> dict[str, bool]:
        with self.store._lock:
            return {row[0]: bool(row[1]) for row in self.store.db.execute(
                "SELECT trade_date,is_trading_day FROM trade_calendar WHERE exchange='CN' AND trade_date BETWEEN ? AND ?",
                (start, end))}

    def replace_intraday(self, symbol: str, adjustflag: str, rows: list[dict]) -> bool:
        if not rows:
            raise ValueError("分时数据为空")
        with self.store._lock:
            _, latest = self.store.market_range(symbol, "1", adjustflag)
            if latest and rows[-1]["trade_date"] < latest:
                return False
            day = rows[-1]["trade_date"][:10]
            try:
                self.store.db.execute("BEGIN IMMEDIATE")
                self.store.db.execute("DELETE FROM market_bars WHERE symbol=? AND timeframe='1' AND adjustflag=? AND substr(trade_date,1,10)<>?", (symbol, adjustflag, day))
                self.store.db.executemany("""INSERT INTO market_bars
                    (symbol,timeframe,trade_date,open,high,low,close,volume,amount,adjustflag,source)
                    VALUES (?,'1',?,?,?,?,?,?,?,?,'tencent')
                    ON CONFLICT(symbol,timeframe,trade_date,adjustflag) DO UPDATE SET
                    open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,
                    volume=excluded.volume,amount=excluded.amount,source=excluded.source""",
                    [(symbol, row["trade_date"], row["open"], row["high"], row["low"], row["close"],
                      row["volume"], row.get("amount", 0), adjustflag) for row in rows])
                self.store.db.commit()
            except Exception:
                self.store.db.rollback()
                raise
        return True

    def calendar(self, fetch: bool) -> dict[str, bool]:
        now = self.clock()
        start = (now.date() - timedelta(days=30)).isoformat()
        end = (now.date() + timedelta(days=14)).isoformat()
        with self._calendar_lock:
            days = self.calendar_days(start, end)
            stored_dates = set(days)
            days.update({day: trading for day, trading in self._future_calendar.items() if start <= day <= end})
            if fetch and len(days) < 45 and time.monotonic() - self._calendar_attempt >= 60:
                self._calendar_attempt = time.monotonic()
                try:
                    fetched = fetch_trade_calendar(start, end)
                    today = now.date().isoformat()
                    self.store.upsert_trade_calendar([row for row in fetched if row["trade_date"] <= today and row["trade_date"] not in stored_dates])
                    self._future_calendar = {row["trade_date"]: row["is_trading_day"] for row in fetched if row["trade_date"] > today}
                    days = self.calendar_days(start, end)
                    days.update(self._future_calendar)
                    self._calendar_error = None
                except Exception:
                    self._calendar_error = "交易日历获取失败"
            return days

    def refresh(self, symbol: str, adjustflag: str = "2") -> dict:
        key = (symbol, "1", adjustflag)
        with self._guard:
            lock = self._locks.setdefault(key, threading.Lock())
        with lock:
            previous = self._attempts.get(key, {})
            if time.monotonic() - previous.get("finished", float("-inf")) < 15:
                return previous
            calendar = self.calendar(True)
            now = self.clock()
            attempt = {"result": "failed", "error": None, "last_success_at": previous.get("last_success_at")}
            if now.date().isoformat() not in calendar:
                if previous:
                    return previous
            try:
                rows = validate_rows(fetch_tencent(symbol, "1", "2015-01-01", now.date().isoformat(), adjustflag), self.clock())
                accepted = self.replace_intraday(symbol, adjustflag, rows)
                attempt.update(result="success" if accepted else "stale", last_success_at=self.clock().isoformat(),
                               error=None if accepted else "行情源数据早于已有缓存，已保留缓存")
            except Exception as exc:
                attempt["error"] = f"分时刷新失败：{type(exc).__name__}"
            attempt["finished"] = time.monotonic()
            self._attempts[key] = attempt
            return attempt

    def metadata(self, symbol: str, adjustflag: str = "2") -> dict:
        now = self.clock()
        state = session_state(now, self.calendar(False))
        _, latest = self.store.market_range(symbol, "1", adjustflag)
        attempt = self._attempts.get((symbol, "1", adjustflag), {})
        return {**state, "server_time": now.isoformat(), "data_date": latest[:10] if latest else None,
                "latest_data_at": latest, "last_success_at": attempt.get("last_success_at"),
                "result": attempt.get("result", "cached"), "error": attempt.get("error"),
                "calendar_error": self._calendar_error,
                "is_today": bool(latest and latest[:10] == now.date().isoformat())}

    def chart_page(self, symbol: str, adjustflag: str, before: str | None, limit: int,
                   ma_periods: tuple, boll_period: int, boll_multiplier: float) -> dict:
        with self.store._lock:
            rows = self.store.market_bars(symbol, "1", adjustflag, "0000-01-01")
            day = rows[-1]["trade_date"][:10] if rows else None
            rows = [row for row in rows if row["trade_date"][:10] == day]
            daily = self.store.market_bars(symbol, "d", adjustflag, "0000-01-01", day or "0000-01-01")
            drawings = self.store.drawings(symbol, "1")
            drawings_version = self.store.drawings_version(symbol, "1")
        eligible = [row for row in rows if before is None or row["trade_date"] < before]
        page = eligible[-limit:]
        stamps = {row["trade_date"] for row in page}
        previous_close = None
        if day:
            start = (datetime.fromisoformat(day) - timedelta(days=30)).date().isoformat()
            calendar = self.calendar_days(start, day)
            cursor = datetime.fromisoformat(day) - timedelta(days=1)
            while cursor.date().isoformat() in calendar:
                previous_day = cursor.date().isoformat()
                if calendar[previous_day]:
                    previous_close = next((row["close"] for row in daily if row["trade_date"][:10] == previous_day), None)
                    break
                cursor -= timedelta(days=1)
        quote = None
        metadata = self.metadata(symbol, adjustflag)
        if rows:
            latest = rows[-1]
            change = latest["close"] - previous_close if previous_close else None
            high, low = max(row["high"] for row in rows), min(row["low"] for row in rows)
            quote = {"trade_date": latest["trade_date"], "latest": latest["close"], "previous_close": previous_close,
                     "change": change, "change_pct": change / previous_close * 100 if previous_close else None,
                     "open": rows[0]["open"], "high": high, "low": low,
                     "volume": sum(row["volume"] for row in rows),
                     "amount": sum(row["amount"] for row in rows) if any(row["amount"] for row in rows) else None,
                     "amplitude_pct": (high - low) / previous_close * 100 if previous_close else None,
                     "market_status": metadata["market_status"]}
        return {"symbol": symbol, "timeframe": "1", "adjustflag": adjustflag, "bars": page,
                "previous_close": previous_close, "quote": quote, "intraday_refresh": metadata,
                "indicators": {
                    "macd": [item for item in calculate_macd(rows) if item["trade_date"] in stamps],
                    "ma": [{**item, "values": {str(period): item.get(f"ma{period}") for period in ma_periods}}
                           for item in calculate_moving_averages(rows, ma_periods) if item["trade_date"] in stamps],
                    "boll": [item for item in calculate_bollinger(rows, boll_period, boll_multiplier) if item["trade_date"] in stamps]},
                "has_more": len(eligible) > limit, "next_before": page[0]["trade_date"] if page else None,
                "available": bool(rows), "definition_version": PERIOD_DEFINITION_VERSION, "structure_version": "",
                "pens": [], "pen_diagnostics": [], "centers": [], "pen_centers": [], "movements": [],
                "center_relations": [], "center_levels": [], "movement_levels": [],
                "drawings": drawings, "drawings_version": drawings_version, "structure_overrides_enabled": False}
