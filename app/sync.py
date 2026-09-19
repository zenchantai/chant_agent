from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .providers import TIMEFRAMES, MarketDataProvider, fetch_baostock, fetch_stock_name, fetch_trade_calendar, is_trading_day
from .store import Store
from .period_structure import PeriodStructureService, STRUCTURE_TIMEFRAMES
from .intraday import IntradayService

TZ = ZoneInfo("Asia/Shanghai")
INTRADAY = ("1", "5", "15", "30", "60", "120", "d")
HISTORY_START = "2015-01-01"


class SyncService:
    def __init__(self, store: Store, structures: PeriodStructureService | None = None, intraday: IntradayService | None = None):
        self.store = store
        self.intraday = intraday or IntradayService(store)
        self.period_structures = structures or PeriodStructureService(store)
        self.market_provider = MarketDataProvider()
        self._lock = asyncio.Lock()
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def start(self):
        self._stop.clear()
        asyncio.create_task(self.fill_missing_names())
        self._task = asyncio.create_task(self._run(), name="baostock-sync")

    async def stop(self):
        self._stop.set()
        if self._task:
            await self._task

    async def _run(self):
        await self.catch_up()
        while not self._stop.is_set():
            now = datetime.now(TZ)
            target = self.next_target(now)
            try:
                await asyncio.wait_for(self._stop.wait(), max(1, (target - now).total_seconds()))
            except asyncio.TimeoutError:
                await self.scheduled_update(target)

    @staticmethod
    def next_target(now: datetime) -> datetime:
        candidates = []
        for day_offset in range(0, 8):
            day = (now + timedelta(days=day_offset)).date()
            if day.weekday() < 5:
                candidates.append(datetime.combine(day, datetime.min.time(), TZ).replace(hour=20, minute=30))
            if day.weekday() == 5:
                candidates.append(datetime.combine(day, datetime.min.time(), TZ).replace(hour=18))
            if day.day == 1:
                candidates.append(datetime.combine(day, datetime.min.time(), TZ).replace(hour=18))
        return next(x for x in sorted(candidates) if x > now)

    async def catch_up(self):
        now = datetime.now(TZ)
        cutoff = now.date() if (now.hour, now.minute) >= (20, 30) else now.date() - timedelta(days=1)
        for offset in range(10):
            day = cutoff - timedelta(days=offset)
            if await asyncio.to_thread(is_trading_day, day.isoformat()):
                target = datetime.combine(day, datetime.min.time(), TZ).replace(hour=20, minute=30)
                await self.sync_pool(INTRADAY, "incremental", target)
                break
        saturday = now.date() - timedelta(days=(now.weekday() - 5) % 7)
        weekly_target = datetime.combine(saturday, datetime.min.time(), TZ).replace(hour=18)
        if weekly_target <= now:
            await self.sync_pool(("w",), "incremental", weekly_target)
        month_day = now.date().replace(day=1)
        monthly_target = datetime.combine(month_day, datetime.min.time(), TZ).replace(hour=18)
        if monthly_target > now:
            previous = month_day - timedelta(days=1)
            monthly_target = datetime.combine(previous.replace(day=1), datetime.min.time(), TZ).replace(hour=18)
        await self.sync_pool(("m",), "incremental", monthly_target)
        if month_day.month == 1:
            await self.sync_pool(("y",), "incremental", monthly_target)

    async def scheduled_update(self, target: datetime):
        if target.weekday() < 5:
            if is_trading_day(target.date().isoformat()):
                await self.sync_pool(INTRADAY, "incremental", target)
        elif target.weekday() == 5:
            await self.sync_pool(("w",), "incremental", target)
        if target.day == 1:
            await self.sync_pool(("m",), "incremental", target)
            if target.month == 1:
                await self.sync_pool(("y",), "incremental", target)

    async def sync_pool(self, timeframes=TIMEFRAMES, mode="incremental", scheduled_for: datetime | None = None, symbols: list[str] | None = None):
        async with self._lock:
            await self.ensure_trade_calendar()
            pool = self.store.list_stock_pool()
            selected = symbols or [item["symbol"] for item in pool]
            for symbol in selected:
                for timeframe in timeframes:
                    await self.sync_one(symbol, timeframe, mode, scheduled_for)

    async def ensure_trade_calendar(self):
        start, end = self.store.trade_calendar_range()
        today = date.today().isoformat()
        if start and start <= HISTORY_START and end and end >= today:
            return
        fetch_start = HISTORY_START if not start else max(HISTORY_START, (datetime.fromisoformat(end) - timedelta(days=7)).date().isoformat())
        rows = await asyncio.to_thread(fetch_trade_calendar, fetch_start, today)
        self.store.upsert_trade_calendar(rows)

    async def sync_one(self, symbol: str, timeframe: str, mode: str = "incremental", scheduled_for: datetime | None = None, adjustflag: str = "2"):
        try:
            if timeframe == "1":
                attempt = await asyncio.to_thread(self.intraday.refresh, symbol, adjustflag)
                if attempt["result"] == "failed":
                    raise RuntimeError(attempt["error"])
                return attempt
            cached_start, cached_end = self.store.market_range(symbol, timeframe, adjustflag)
            if mode == "full" or not cached_end:
                start = HISTORY_START
            else:
                start = max(HISTORY_START, (datetime.fromisoformat(cached_end[:10]) - timedelta(days=7)).date().isoformat())
            # A tail-only incremental update cannot repair a hole in the middle
            # of the 5-minute series. Use the cached daily calendar as the
            # authoritative trading-day index and backfill from the first gap.
            if timeframe == "5" and mode != "full":
                daily_dates = {row["trade_date"][:10] for row in self.store.market_bars(symbol, "d", "2", HISTORY_START, date.today().isoformat())}
                minute_dates = {row["trade_date"][:10] for row in self.store.market_bars(symbol, "5", "2", HISTORY_START, date.today().isoformat())}
                missing_dates = sorted(daily_dates - minute_dates)
                if missing_dates:
                    start = min(start, missing_dates[0])
            error = None
            rows = []
            for attempt, delay in enumerate((0, 2, 5)):
                if delay:
                    await asyncio.sleep(delay)
                try:
                    request_started_at = datetime.now(TZ)
                    if timeframe == "5":
                        fetched = await self.market_provider.fetch_5m(symbol, start, date.today().isoformat(), adjustflag)
                        rows = fetched["rows"]
                    else:
                        rows = await asyncio.to_thread(fetch_baostock, symbol, timeframe, start, date.today().isoformat(), adjustflag)
                    fetched_at = datetime.now(TZ)
                    error = None
                    break
                except Exception as exc:
                    error = exc
            if error:
                raise error
            if not rows and not cached_end:
                raise RuntimeError("BaoStock 未返回可缓存的行情数据")
            source = rows[0].get("source", "baostock") if rows else "baostock"
            snapshot_id = rows[0].get("snapshot_id", "") if rows else ""
            count, changed_from = self.store.upsert_bars_with_changes(
                symbol, timeframe, adjustflag, rows, source=source, snapshot_id=snapshot_id,
                **({"fetched_at": fetched_at, "request_started_at": request_started_at}
                   if timeframe == "d" else {}),
            )
            active = self.store.active_chan_run(symbol, timeframe, adjustflag)
            if timeframe in STRUCTURE_TIMEFRAMES and (changed_from or not active):
                await asyncio.to_thread(self.period_structures.ensure, symbol, timeframe, adjustflag, True)
            if timeframe == "d" and rows and count:
                latest = max(rows, key=lambda row: row["trade_date"])
                accepted_source = {**latest, "source": latest.get("source", source),
                                   "snapshot_id": latest.get("snapshot_id", snapshot_id)}
                formal = self.store.confirmed_daily_bars(symbol, adjustflag)
                if (formal and formal[-1]["trade_date"] == latest["trade_date"]
                        and self.store._daily_bar_hash(formal[-1]) == self.store._daily_bar_hash(accepted_source)):
                    self.intraday.record_formal_daily_refresh(
                        symbol, adjustflag, fetched_at, latest["trade_date"],
                    )
            range_start, range_end = self.store.market_range(symbol, timeframe, adjustflag)
            return {
                "result": "success", "rows_received": count,
                "range_start": range_start, "range_end": range_end,
                "changed_from": changed_from,
            }
        except Exception as exc:
            return {"result": "failed", "error": f"{type(exc).__name__}: {exc}"}

    async def add_stock(self, symbol: str, name: str = "", sync: bool = True,
                        group_id: int | None = None):
        if not name:
            try:
                name = await asyncio.to_thread(fetch_stock_name, symbol)
            except Exception:
                name = ""
        item = self.store.upsert_stock(symbol, name, group_id)
        if sync:
            asyncio.create_task(self.sync_pool(TIMEFRAMES, "full", symbols=[symbol]))
        return item

    async def fill_missing_names(self):
        for item in self.store.list_stock_pool():
            if not item["name"]:
                try:
                    name = await asyncio.to_thread(fetch_stock_name, item["symbol"])
                    if name:
                        self.store.update_stock(item["symbol"], name=name)
                except Exception:
                    continue
