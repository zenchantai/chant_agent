from __future__ import annotations

import math
import threading
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .providers import INTRADAY_TIMEFRAMES, TIMEFRAMES, fetch_tencent, fetch_tencent_quotes, fetch_trade_calendar
from .store import Store
from .indicators import calculate_bollinger, calculate_macd, calculate_moving_averages
from .rules import PERIOD_DEFINITION_VERSION
from .coverage import EXPECTED_TIMES
from .periods import period_key

TZ = ZoneInfo("Asia/Shanghai")
MINUTE_PERIODS = {key: int(key) for key in INTRADAY_TIMEFRAMES}
REALTIME_PERIODS = set(TIMEFRAMES)
LIVE_STRUCTURE_PERIODS = {"5", "30", "d", "w", "m"}


def _period_boundary(stamp: datetime, timeframe: str) -> datetime:
    """Return the close boundary encoded by a Tencent period timestamp."""
    if timeframe == "1":
        return stamp + timedelta(minutes=1)
    return stamp.replace(second=0, microsecond=0)


def period_date_range(value: str | date | datetime, timeframe: str) -> tuple[str, str]:
    day = date.fromisoformat(str(value)[:10]) if isinstance(value, str) else (value.date() if isinstance(value, datetime) else value)
    if timeframe == "w":
        start = day - timedelta(days=day.weekday())
        return start.isoformat(), (start + timedelta(days=6)).isoformat()
    if timeframe == "m":
        start = day.replace(day=1)
        next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        return start.isoformat(), (next_month - timedelta(days=1)).isoformat()
    if timeframe == "y":
        return day.replace(month=1, day=1).isoformat(), day.replace(month=12, day=31).isoformat()
    return day.isoformat(), day.isoformat()


def merge_period_rows(stored: list[dict], live: list[dict], timeframe: str) -> list[dict]:
    key = lambda row: period_key(row["trade_date"], timeframe)
    merged = {key(row): dict(row) for row in stored}
    merged.update({key(row): dict(row) for row in live})
    return sorted(merged.values(), key=lambda row: row["trade_date"])


def aggregate_daily_rows(rows: list[dict], timeframe: str, key: str) -> dict | None:
    """Build one provisional/formal higher-period bar from daily input rows."""
    selected = [row for row in rows if period_key(row["trade_date"], timeframe) == key]
    if not selected:
        return None
    selected = sorted(selected, key=lambda row: row["trade_date"])
    amounts = [row.get("amount") for row in selected]
    amount = sum(float(value or 0) for value in amounts) if any(value not in (None, 0, 0.0) for value in amounts) else 0.0
    revisions = [str(row.get("source_revision") or row.get("snapshot_id") or "") for row in selected]
    revisions = [revision for revision in revisions if revision]
    return {
        "trade_date": selected[-1]["trade_date"][:10],
        "period_key": key,
        "open": float(selected[0]["open"]),
        "high": max(float(row["high"]) for row in selected),
        "low": min(float(row["low"]) for row in selected),
        "close": float(selected[-1]["close"]),
        "volume": sum(float(row.get("volume", 0) or 0) for row in selected),
        "amount": amount,
        "adjustflag": selected[-1].get("adjustflag", "2"),
        "source": "daily_aggregate",
        "source_revision": "daily:" + ",".join(revisions),
    }


def session_state(now: datetime, calendar: dict[str, bool]) -> dict:
    now = now.astimezone(TZ)
    today = now.date().isoformat()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if today not in calendar:
        return {"phase": "unknown", "market_status": "日历未知", "next_transition_at": None}
    auction = midnight.replace(hour=9, minute=15)
    morning = midnight.replace(hour=9, minute=30)
    lunch = midnight.replace(hour=11, minute=30)
    afternoon = midnight.replace(hour=13)
    close = midnight.replace(hour=15)
    phase, transition = "closed", None
    if calendar[today]:
        if now < auction:
            transition = auction
        elif now < morning:
            phase, transition = "auction", morning
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
    return {"phase": phase, "market_status": {"auction": "开盘集合竞价", "trading": "交易中", "lunch": "午间休市", "closed": "已休市"}[phase],
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


def validate_period_rows(rows: list[dict], timeframe: str, now: datetime) -> list[dict]:
    if timeframe not in REALTIME_PERIODS:
        raise ValueError(f"不支持实时刷新的周期: {timeframe}")
    if timeframe == "1":
        return validate_rows(rows, now)
    if not rows:
        raise ValueError("行情源未返回周期数据")
    now = now.astimezone(TZ)
    result: dict[str, dict] = {}
    if timeframe in INTRADAY_TIMEFRAMES:
        latest_day = max(str(row["trade_date"])[:10] for row in rows)
        tolerance = timedelta(minutes=MINUTE_PERIODS[timeframe])
        expected_times = set(EXPECTED_TIMES[timeframe])
    else:
        current_key = period_key(now, timeframe)
    for row in rows:
        raw_stamp = str(row["trade_date"])
        if timeframe in INTRADAY_TIMEFRAMES:
            if raw_stamp[:10] != latest_day:
                continue
            stamp = datetime.strptime(raw_stamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)
            if stamp > now + tolerance:
                continue
            if stamp.second or stamp.strftime("%H:%M") not in expected_times:
                raise ValueError("行情源返回无效周期数据")
            row_key = raw_stamp
        else:
            try:
                stamp = datetime.fromisoformat(raw_stamp).replace(tzinfo=TZ)
            except ValueError as exc:
                raise ValueError("行情源返回无效周期数据") from exc
            if stamp.date() > now.date() or period_key(stamp, timeframe) != current_key:
                continue
            row_key = current_key
        values = [float(row[field]) for field in ("open", "high", "low", "close", "volume")]
        amount = float(row.get("amount", 0))
        if (not all(math.isfinite(value) for value in [*values, amount])
                or min(values[:4]) <= 0 or values[4] < 0 or amount < 0
                or float(row["low"]) > min(float(row["open"]), float(row["close"]))
                or float(row["high"]) < max(float(row["open"]), float(row["close"]))):
            raise ValueError("行情源返回无效周期数据")
        result[row_key] = {
            **row,
            "source": "tencent",
            "period_key": period_key(raw_stamp, timeframe),
        }
    if not result:
        raise ValueError("行情源尚未返回当日周期数据")
    return sorted(result.values(), key=lambda row: row["trade_date"])


class IntradayService:
    def __init__(self, store: Store, clock=None):
        self.store = store
        self.clock = clock or (lambda: datetime.now(TZ))
        self._guard = threading.Lock()
        self._locks: dict[tuple, threading.Lock] = {}
        self._attempts: dict[tuple, dict] = {}
        self._daily_source_errors: dict[tuple[str, str], str] = {}
        self._calendar_lock = threading.Lock()
        self._calendar_attempt = float("-inf")
        self._calendar_error = None
        self._future_calendar: dict[str, bool] = {}
        self._live_rows: dict[tuple[str, str, str], list[dict]] = {}
        self._quotes: dict[tuple[str, str], dict] = {}

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
                    (symbol,timeframe,trade_date,period_key,open,high,low,close,volume,amount,adjustflag,source)
                    VALUES (?,'1',?,?,?,?,?,?,?,?,?,'tencent')
                    ON CONFLICT(symbol,timeframe,trade_date,adjustflag) DO UPDATE SET
                    period_key=excluded.period_key,
                    open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,
                    volume=excluded.volume,amount=excluded.amount,source=excluded.source""",
                    [(symbol, row["trade_date"], period_key(row["trade_date"], "1"), row["open"], row["high"], row["low"], row["close"],
                      row["volume"], row.get("amount", 0), adjustflag) for row in rows])
                self.store.db.commit()
            except Exception:
                self.store.db.rollback()
                raise
        return True

    def period_finalize_at(self, row: dict, timeframe: str, calendar: dict[str, bool]) -> datetime | None:
        stamp = datetime.fromisoformat(str(row["trade_date"])).replace(tzinfo=TZ)
        if timeframe in INTRADAY_TIMEFRAMES:
            return _period_boundary(stamp, timeframe) + timedelta(seconds=15)
        day = stamp.date().isoformat()
        if not calendar.get(day):
            return None
        if timeframe == "d":
            return stamp.replace(hour=15, minute=0, second=15, microsecond=0)
        future_days = sorted(candidate for candidate, trading in calendar.items() if trading and candidate > day)
        if not future_days or period_key(future_days[0], timeframe) == period_key(day, timeframe):
            return None
        return stamp.replace(hour=15, minute=0, second=15, microsecond=0)

    def next_period_finalize_at(self, now: datetime, timeframe: str, calendar: dict[str, bool]) -> datetime | None:
        now = now.astimezone(TZ)
        if timeframe == "d":
            day = now.date().isoformat()
            return now.replace(hour=15, minute=0, second=15, microsecond=0) if calendar.get(day) else None
        if timeframe not in {"w", "m"}:
            return None
        current = period_key(now, timeframe)
        days = sorted(day for day, trading in calendar.items() if trading and period_key(day, timeframe) == current)
        if not days:
            return None
        return datetime.fromisoformat(days[-1]).replace(hour=15, minute=0, second=15, microsecond=0, tzinfo=TZ)

    def replace_current_period(self, symbol: str, timeframe: str, adjustflag: str,
                               rows: list[dict], finalize_before: datetime,
                               calendar: dict[str, bool], *, fetched_at: datetime | None = None) -> tuple[int, str | None]:
        if timeframe == "1" or timeframe not in REALTIME_PERIODS:
            raise ValueError("该周期不支持周期确认")
        confirmed = []
        for row in rows:
            finalize_at = self.period_finalize_at(row, timeframe, calendar)
            if finalize_at and finalize_at <= finalize_before:
                confirmed.append(row)
        if not confirmed:
            return 0, None
        result = self.store.upsert_bars_with_changes(
            symbol, timeframe, adjustflag, confirmed, source="tencent", allow_lower_priority=True,
            **({"fetched_at": fetched_at or finalize_before, "request_started_at": finalize_before}
               if timeframe == "d" else {}),
        )
        if timeframe != "d" and hasattr(self.store, "confirm_period_bars"):
            self.store.confirm_period_bars(
                symbol, timeframe, adjustflag, confirmed,
                fetched_at or finalize_before, finalize_before,
            )
        return result

    def calendar(self, fetch: bool) -> dict[str, bool]:
        now = self.clock()
        start = (now.date() - timedelta(days=400)).isoformat()
        end = (now.date() + timedelta(days=400)).isoformat()
        with self._calendar_lock:
            days = self.calendar_days(start, end)
            stored_dates = set(days)
            days.update({day: trading for day, trading in self._future_calendar.items() if start <= day <= end})
            future_known = any(day > now.date().isoformat() for day in days)
            if fetch and (not future_known or len(days) < 45) and time.monotonic() - self._calendar_attempt >= 60:
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
        return self.refresh_period(symbol, "1", adjustflag)

    def daily_source_error(self, symbol: str, adjustflag: str = "2") -> str | None:
        """Keep failed formal-source refreshes visible across cache-only reads."""
        with self._guard:
            return self._daily_source_errors.get((symbol, adjustflag))

    def _record_daily_source_attempt(self, symbol: str, adjustflag: str, attempt: dict) -> None:
        with self._guard:
            key = (symbol, adjustflag)
            if attempt.get("result") != "success":
                self._daily_source_errors[key] = attempt.get("error") or "日线刷新失败，显示已确认数据"
            elif attempt.get("formal_daily_confirmed"):
                self._daily_source_errors.pop(key, None)

    def record_formal_daily_refresh(self, symbol: str, adjustflag: str,
                                    fetched_at: datetime, confirmed_through: str) -> None:
        """Publish a historical-sync recovery after its accepted row was verified."""
        with self._guard:
            key = (symbol, "d", adjustflag)
            self._daily_source_errors.pop((symbol, adjustflag), None)
            self._attempts[key] = {
                "result": "success", "error": None, "last_success_at": fetched_at.isoformat(),
                "changed_from": None, "finalized_count": 0, "formal_daily_confirmed": True,
                "finished": time.monotonic(),
            }
            self._live_rows[key] = [row for row in self._live_rows.get(key, [])
                                    if row["trade_date"] > confirmed_through]

    def refresh_period(self, symbol: str, timeframe: str, adjustflag: str = "2", include_quote: bool = False) -> dict:
        if timeframe not in REALTIME_PERIODS:
            raise ValueError(f"不支持实时刷新的周期: {timeframe}")
        key = (symbol, timeframe, adjustflag)
        with self._guard:
            lock = self._locks.setdefault(key, threading.Lock())
        with lock:
            previous = self._attempts.get(key, {})
            live = self._live_rows.get(key, [])
            calendar = self.calendar(True)
            if timeframe in {"d", "w", "m"} and live:
                live_finalize_at = self.next_period_finalize_at(self.clock(), timeframe, calendar)
            else:
                live_finalize_at = self.period_finalize_at(live[-1], timeframe, calendar) if timeframe != "1" and live else None
            finalization_due = bool(live_finalize_at and self.clock() >= live_finalize_at)
            if time.monotonic() - previous.get("finished", float("-inf")) < 15 and not finalization_due:
                return previous
            now = self.clock()
            attempt = {"result": "failed", "error": None, "last_success_at": previous.get("last_success_at"),
                       "changed_from": None, "finalized_count": 0}
            if now.date().isoformat() not in calendar:
                if previous:
                    return previous
            quote = None
            if include_quote:
                try:
                    fetched_quotes = fetch_tencent_quotes([symbol])
                    candidate = fetched_quotes[0] if fetched_quotes else None
                    if candidate and candidate.get("status") == "success" and candidate.get("quote_time"):
                        quote_stamp = datetime.strptime(candidate["quote_time"], "%Y%m%d%H%M%S").replace(tzinfo=TZ)
                        if quote_stamp <= self.clock() + timedelta(seconds=60):
                            quote = candidate
                            self._quotes[(symbol, adjustflag)] = {**candidate, "quote_time": quote_stamp.isoformat()}
                except Exception:
                    quote = None
            if session_state(now, calendar)["phase"] == "auction":
                available = bool(quote or self.latest_quote(symbol, adjustflag))
                attempt.update(result="success" if available else "failed",
                               last_success_at=self.clock().isoformat() if available else previous.get("last_success_at"),
                               error=None if available else "实时刷新失败：暂无有效集合竞价报价",
                               finished=time.monotonic())
                self._attempts[key] = attempt
                if timeframe == "d":
                    self._record_daily_source_attempt(symbol, adjustflag, attempt)
                return attempt
            try:
                request_started_at = self.clock()
                if timeframe in {"w", "m"}:
                    # Higher periods are derived only from confirmed daily bars
                    # plus the current daily preview. Never persist a raw Tencent
                    # weekly/monthly row with a shifting source date.
                    daily_rows = self.store.confirmed_daily_bars(symbol, adjustflag)
                    daily_rows.extend(self.live_rows(symbol, "d", adjustflag))
                    current_key = period_key(now, timeframe)
                    aggregate = aggregate_daily_rows(daily_rows, timeframe, current_key)
                    if aggregate is None:
                        raise ValueError("暂无足够日线数据构造当前周期")
                    rows = [aggregate]
                    fetched_at = self.clock()
                else:
                    start, _ = period_date_range(now, timeframe)
                    if timeframe in INTRADAY_TIMEFRAMES or timeframe == "d":
                        start = now.date().isoformat()
                    rows = validate_period_rows(
                        fetch_tencent(symbol, timeframe, start, now.date().isoformat(), adjustflag),
                        timeframe, self.clock(),
                    )
                    fetched_at = self.clock()
                if quote and rows:
                    quote_stamp = datetime.strptime(quote["quote_time"], "%Y%m%d%H%M%S").replace(tzinfo=TZ)
                    daily_close = self.period_finalize_at(rows[-1], "d", calendar) if timeframe == "d" else None
                    # A pre-close quote cannot alter a completed daily response.
                    quote_is_current = not (daily_close and request_started_at >= daily_close and quote_stamp < daily_close)
                    if quote_stamp.date() == self.clock().date() and quote_is_current:
                        rows[-1] = {**rows[-1], "close": float(quote["latest"]),
                                    "high": max(float(rows[-1]["high"]), float(quote["latest"])),
                                    "low": min(float(rows[-1]["low"]), float(quote["latest"]))}
                if timeframe == "1":
                    accepted = self.replace_intraday(symbol, adjustflag, rows)
                else:
                    accepted = True
                    finalized_count, changed_from = self.replace_current_period(
                        symbol, timeframe, adjustflag, rows,
                        request_started_at if timeframe == "d" else fetched_at, calendar,
                        fetched_at=fetched_at,
                    )
                    attempt.update(finalized_count=finalized_count, changed_from=changed_from)
                    if timeframe == "d" and finalized_count:
                        formal = self.store.confirmed_daily_bars(symbol, adjustflag)
                        attempt["formal_daily_confirmed"] = bool(
                            formal and formal[-1]["trade_date"] == rows[-1]["trade_date"]
                        )
                    finalize_at = self.period_finalize_at(rows[-1], timeframe, calendar)
                    latest_is_forming = finalize_at is None or (
                        request_started_at if timeframe == "d" else fetched_at
                    ) < finalize_at
                    self._live_rows[key] = rows if latest_is_forming else []
                attempt.update(result="success" if accepted else "stale", last_success_at=self.clock().isoformat(),
                               error=None if accepted else "行情源数据早于已有缓存，已保留缓存")
                attempt["request_started_at"] = request_started_at.isoformat()
                attempt["fetched_at"] = fetched_at.isoformat()
                attempt["source_revision"] = rows[-1].get("source_revision", "") if rows else ""
            except Exception as exc:
                attempt["error"] = f"实时刷新失败：{type(exc).__name__}"
            attempt["finished"] = time.monotonic()
            self._attempts[key] = attempt
            if timeframe == "d":
                self._record_daily_source_attempt(symbol, adjustflag, attempt)
            return attempt

    def metadata(self, symbol: str, adjustflag: str = "2") -> dict:
        return self.period_metadata(symbol, "1", adjustflag)

    def period_metadata(self, symbol: str, timeframe: str, adjustflag: str = "2") -> dict:
        now = self.clock()
        calendar = self.calendar(False)
        state = session_state(now, calendar)
        live = self._live_rows.get((symbol, timeframe, adjustflag), [])
        _, stored_latest = self.store.market_range(symbol, timeframe, adjustflag)
        latest = live[-1]["trade_date"] if live else stored_latest
        attempt = self._attempts.get((symbol, timeframe, adjustflag), {})
        forming = self.forming_bar(symbol, timeframe, adjustflag)
        refresh_state = "cached"
        if attempt.get("result") == "failed":
            refresh_state = "stale"
        elif forming:
            refresh_state = "provisional"
        elif attempt.get("result") == "success" and latest:
            refresh_state = "confirmed"
        period = period_key(latest, timeframe) if latest else None
        finalize_at = forming.get("finalize_at") if forming else self.next_period_finalize_at(now, timeframe, calendar)
        metadata = {**state, "server_time": now.isoformat(), "data_date": latest[:10] if latest else None,
                "latest_data_at": latest, "last_success_at": attempt.get("last_success_at"),
                "result": attempt.get("result", "cached"), "error": attempt.get("error"),
                "calendar_error": self._calendar_error,
                "next_bar_finalize_at": finalize_at,
                "next_period_finalize_at": finalize_at,
                "is_today": bool(latest and latest[:10] == now.date().isoformat())}
        metadata["period_refresh"] = {
            "period_key": period,
            "state": refresh_state,
            "is_forming": bool(forming),
            "server_time": metadata["server_time"],
            "latest_data_at": latest,
            "source_revision": attempt.get("source_revision"),
            "request_started_at": attempt.get("request_started_at"),
            "last_success_at": metadata["last_success_at"],
            "next_period_finalize_at": finalize_at,
            "is_today": metadata["is_today"],
            "error": metadata["error"],
        }
        return metadata

    def live_rows(self, symbol: str, timeframe: str, adjustflag: str = "2") -> list[dict]:
        return [dict(row) for row in self._live_rows.get((symbol, timeframe, adjustflag), [])]

    def forming_bar(self, symbol: str, timeframe: str, adjustflag: str = "2") -> dict | None:
        if timeframe == "1" or timeframe not in REALTIME_PERIODS:
            return None
        rows = self._live_rows.get((symbol, timeframe, adjustflag), [])
        if not rows:
            return None
        latest = rows[-1]
        calendar = self.calendar(False)
        finalize_at = (self.next_period_finalize_at(self.clock(), timeframe, calendar)
                       if timeframe in {"d", "w", "m"}
                       else self.period_finalize_at(latest, timeframe, calendar))
        if finalize_at and self.clock() >= finalize_at:
            return None
        return {"trade_date": latest["trade_date"], "is_forming": True,
                "status": "provisional", "finalize_at": finalize_at.isoformat() if finalize_at else None}

    def latest_quote(self, symbol: str, adjustflag: str = "2") -> dict | None:
        quote = self._quotes.get((symbol, adjustflag))
        return dict(quote) if quote else None

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
            snapshot = self.latest_quote(symbol, adjustflag)
            quote_latest = float(snapshot["latest"]) if snapshot and snapshot.get("latest") is not None else latest["close"]
            previous_close = snapshot.get("previous_close") if snapshot else previous_close
            change = snapshot.get("change") if snapshot else (quote_latest - previous_close if previous_close else None)
            high, low = max(row["high"] for row in rows), min(row["low"] for row in rows)
            quote = {"trade_date": latest["trade_date"], "latest": quote_latest, "previous_close": previous_close,
                     "change": change, "change_pct": snapshot.get("change_pct") if snapshot else (change / previous_close * 100 if previous_close else None),
                     "open": rows[0]["open"], "high": high, "low": low,
                     "volume": sum(row["volume"] for row in rows),
                     "amount": sum(row["amount"] for row in rows) if any(row["amount"] for row in rows) else None,
                     "amplitude_pct": (high - low) / previous_close * 100 if previous_close else None,
                     "market_status": metadata["market_status"],
                     "quote_time": snapshot.get("quote_time") if snapshot else latest["trade_date"],
                     "source": snapshot.get("source", "tencent") if snapshot else "tencent",
                     "status": snapshot.get("status", "success") if snapshot else "success"}
        elif (snapshot := self.latest_quote(symbol, adjustflag)):
            latest = float(snapshot["latest"])
            previous_close = snapshot.get("previous_close")
            quote = {"trade_date": snapshot.get("quote_time") or metadata.get("latest_data_at"),
                     "latest": latest, "previous_close": previous_close,
                     "change": snapshot.get("change"), "change_pct": snapshot.get("change_pct"),
                     "open": snapshot.get("open") or latest, "high": snapshot.get("high") or latest,
                     "low": snapshot.get("low") or latest, "volume": snapshot.get("volume") or 0,
                     "amount": snapshot.get("amount"),
                     "amplitude_pct": ((snapshot.get("high", latest) - snapshot.get("low", latest)) / previous_close * 100)
                     if previous_close else None, "market_status": metadata["market_status"],
                     "quote_time": snapshot.get("quote_time"), "source": snapshot.get("source", "tencent"),
                     "status": snapshot.get("status", "success")}
        return {
                "meta": {"symbol": symbol, "timeframe": "1", "adjustflag": adjustflag,
                         "available": bool(rows), "definition_version": PERIOD_DEFINITION_VERSION,
                         "structure_version": "", "active_level": 1, "max_level": 0,
                         "diagnostics": False},
                "market": {"symbol": symbol, "timeframe": "1", "adjustflag": adjustflag,
                           "bars": page, "previous_close": previous_close, "quote": quote,
                           "intraday_refresh": metadata},
                "structure": {"pens": [], "components": [], "centers": [],
                              "center_revisions": [], "movements": [], "movement_revisions": [],
                              "points": [], "point_revisions": [], "relations": [], "issues": [],
                              "segment_proofs": [], "segment_proof_revisions": [],
                              "center_candidates": [], "center_candidate_revisions": [],
                              "promotion_candidates": [], "promotion_candidate_revisions": [],
                              "levels": [], "unassigned_by_level": {}, "pen_diagnostics": []},
                "indicators": {
                    "macd": [item for item in calculate_macd(rows) if item["trade_date"] in stamps],
                    "ma": [{**item, "values": {str(period): item.get(f"ma{period}") for period in ma_periods}}
                           for item in calculate_moving_averages(rows, ma_periods) if item["trade_date"] in stamps],
                    "boll": [item for item in calculate_bollinger(rows, boll_period, boll_multiplier) if item["trade_date"] in stamps]},
                "drawings": {"items": drawings, "version": drawings_version},
                "pagination": {"has_more": len(eligible) > limit,
                               "next_before": page[0]["trade_date"] if page else None},
        }
