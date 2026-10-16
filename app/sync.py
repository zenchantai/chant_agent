from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .market_sources import Candidate, candidate_summary, normalize_candidate_rows, select_and_fill, uncovered_ranges
from .periods import period_key
from .providers import (
    OFFICIAL_START, TIMEFRAMES, MarketDataProvider, fetch_api, fetch_baostock_chunked as fetch_baostock,
    fetch_security_profile, fetch_stock_name, fetch_tencent, fetch_trade_calendar, is_trading_day,
)
from .store import Store
from .period_structure import PeriodStructureService, STRUCTURE_TIMEFRAMES
from .intraday import IntradayService, aggregate_daily_history

TZ = ZoneInfo("Asia/Shanghai")
INTRADAY = ("1", "5", "15", "30", "60", "120", "d")
HISTORY_START = "2015-01-01"


class SyncService:
    def __init__(self, store: Store, structures: PeriodStructureService | None = None, intraday: IntradayService | None = None):
        self.store = store
        self.intraday = intraday or IntradayService(store)
        self.period_structures = structures or PeriodStructureService(store)
        self.market_provider = MarketDataProvider()
        self._profiles: dict[str, dict] = {}
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

    async def ensure_trade_calendar_start(self, start_date: str) -> None:
        cached_start, cached_end = self.store.trade_calendar_range()
        today = date.today().isoformat()
        if cached_start and cached_start <= start_date and cached_end and cached_end >= today:
            return
        rows = await asyncio.to_thread(fetch_trade_calendar, start_date, today)
        self.store.upsert_trade_calendar(rows)

    async def security_profile(self, symbol: str) -> dict:
        if symbol not in self._profiles:
            profile = await asyncio.to_thread(fetch_security_profile, symbol)
            self._profiles[symbol] = profile or {}
        return dict(self._profiles[symbol])

    def _last_completed_date(self) -> str:
        now = datetime.now(TZ)
        days = self.store.trading_dates(HISTORY_START, now.date().isoformat())
        if not days:
            return (now.date() - timedelta(days=1)).isoformat()
        if days[-1] == now.date().isoformat() and now < now.replace(hour=15, minute=0, second=15, microsecond=0):
            days = days[:-1]
        return days[-1] if days else (now.date() - timedelta(days=1)).isoformat()

    async def _fetch_source_candidates(self, symbol: str, timeframe: str, start: str,
                                       end: str, adjustflag: str) -> tuple[dict[str, list[dict]], dict[str, str]]:
        async def capture(name: str, call):
            try:
                return name, await call, None
            except Exception as exc:
                return name, [], f"{type(exc).__name__}: {exc}"

        jobs = [
            capture("baostock", asyncio.to_thread(
                fetch_baostock, symbol, timeframe, start, end, adjustflag,
            )),
            capture("tencent", asyncio.to_thread(
                fetch_tencent, symbol, timeframe, start, end, adjustflag,
            )),
        ]
        if self.market_provider.api_url:
            jobs.insert(0, capture("api", fetch_api(
                self.market_provider.api_url, symbol, start, end, adjustflag, timeframe,
            )))
        results = await asyncio.gather(*jobs)
        rows = {name: value for name, value, error in results if value}
        errors = {name: error for name, value, error in results if error}
        return rows, errors

    @staticmethod
    def _audit_summaries(selection: dict, errors: dict[str, str]) -> list[dict]:
        summaries = []
        for item in selection.get("candidate_summaries", []):
            summaries.append({key: value for key, value in item.items() if key != "coverage"})
        summaries.extend({"source": source, "status": "failed", "error": error, "bar_count": 0}
                         for source, error in errors.items())
        return summaries

    @staticmethod
    def _preserve_cache_on_conflict(selection: dict, cache_rows: list[dict], timeframe: str,
                                    expected: list[str], target_start: str,
                                    target_end: str) -> dict:
        """Never replace proven cache when the selected source disagrees on OHLC."""
        if (not cache_rows or selection.get("primary_source") == "cache"
                or not any(item.get("source") == "cache"
                           for item in selection.get("source_conflicts", []))):
            return selection
        return {
            **selection,
            "rows": cache_rows,
            "primary_source": "cache",
            "selected_sources": ["cache"],
            "coverage": candidate_summary(
                "cache", cache_rows, timeframe, expected,
                target_start, target_end, datetime.now(TZ),
            )["coverage"],
        }

    @staticmethod
    def _coverage_reason(coverage: dict, conflicts: list[dict]) -> str:
        if coverage.get("complete") and not conflicts:
            return "目标范围覆盖完整"
        reasons = []
        if coverage.get("missing_sessions"):
            reasons.append(f"缺少{len(coverage['missing_sessions'])}个交易单元")
        if coverage.get("incomplete_days"):
            reasons.append(f"{len(coverage['incomplete_days'])}个交易日不完整")
        if conflicts:
            reasons.append(f"{len(conflicts)}个跨源拼接冲突")
        tf = str(coverage.get("timeframe") or "d")
        if (coverage.get("declared_start") and coverage.get("range_start")
                and period_key(coverage["range_start"][:10], tf) > period_key(coverage["declared_start"][:10], tf)):
            reasons.append("起点晚于目标范围")
        if (coverage.get("declared_end") and coverage.get("range_end")
                and period_key(coverage["range_end"][:10], tf) < period_key(coverage["declared_end"][:10], tf)):
            reasons.append("终点早于目标范围")
        return "；".join(reasons) or "覆盖完整性无法证明"

    async def _sync_history(self, symbol: str, timeframe: str, mode: str,
                            adjustflag: str, profile: dict) -> dict:
        cached = self.store.market_bars(symbol, timeframe, adjustflag, "0000-01-01")
        cached_end = cached[-1]["trade_date"] if cached else None
        ipo = str(profile.get("ipo_date") or HISTORY_START)[:10]
        full_start = ipo if timeframe == "d" else max(ipo, OFFICIAL_START.get(timeframe, HISTORY_START))
        await self.ensure_trade_calendar_start(full_start)
        request_start = full_start if mode == "full" or not cached_end else max(
            full_start, (datetime.fromisoformat(cached_end[:10]) - timedelta(days=7)).date().isoformat(),
        )
        target_end = self._last_completed_date()
        audit_id = self.store.begin_market_sync(symbol, timeframe, adjustflag, mode, full_start, target_end)
        try:
            raw_candidates, errors = await self._fetch_source_candidates(
                symbol, timeframe, request_start, date.today().isoformat(), adjustflag,
            )
            normalized = {
                source: normalize_candidate_rows(rows, timeframe, source)
                for source, rows in raw_candidates.items()
            }
            if timeframe == "d":
                normalized = {
                    source: [row for row in rows if row["trade_date"][:10] <= target_end]
                    for source, rows in normalized.items()
                }
            starts = [rows[0]["trade_date"][:10] for rows in normalized.values() if rows]
            if cached:
                starts.append(cached[0]["trade_date"][:10])
            target_start = ipo if timeframe == "d" and profile.get("ipo_date") else (
                min(starts) if starts else full_start
            )
            self.store.update_market_sync_target(audit_id, target_start, target_end)
            expected = self.store.trading_dates(target_start, target_end)
            candidates = [Candidate(
                source, rows,
                candidate_summary(source, rows, timeframe, expected, target_start, target_end, datetime.now(TZ)),
            ) for source, rows in normalized.items()]
            cache_rows = normalize_candidate_rows(cached, timeframe, "cache") if cached else []
            if cache_rows:
                if timeframe == "d":
                    cache_rows = [row for row in cache_rows if row["trade_date"][:10] <= target_end]
                if mode != "full":
                    cache_rows = [row for row in cache_rows if row["trade_date"][:10] >= request_start]
            if cache_rows:
                candidates.append(Candidate(
                    "cache", cache_rows,
                    candidate_summary("cache", cache_rows, timeframe, expected, target_start, target_end, datetime.now(TZ)),
                ))
            selection = select_and_fill(
                candidates, timeframe, expected_dates=expected,
                target_start=target_start, target_end=target_end, now=datetime.now(TZ),
            )
            selection = self._preserve_cache_on_conflict(
                selection, cache_rows, timeframe, expected, target_start, target_end,
            )
            selected = selection["rows"]
            if mode != "full" and cached:
                final_by_key = {row["period_key"]: row for row in normalize_candidate_rows(cached, timeframe, "cache")}
                final_by_key.update({row["period_key"]: row for row in selected})
                final_rows = sorted(final_by_key.values(), key=lambda item: item["trade_date"])
                selection["coverage"] = candidate_summary(
                    "merged", final_rows, timeframe, expected, target_start, target_end, datetime.now(TZ),
                )["coverage"]
            if not selected:
                raise RuntimeError("所有行情源均未返回可用数据")
            request_started_at = datetime.now(TZ)
            fetched_at = datetime.now(TZ)
            fresh_selected = selection.get("primary_source") != "cache"
            count, changed_from = self.store.upsert_bars_with_changes(
                symbol, timeframe, adjustflag, selected,
                source=selection.get("primary_source") or "baostock",
                allow_lower_priority=True,
                **({"fetched_at": fetched_at, "request_started_at": request_started_at}
                   if timeframe == "d" and fresh_selected else {}),
                prune_after=target_end if timeframe == "d" else None,
            )
            active = self.store.active_chan_run(symbol, timeframe, adjustflag)
            if timeframe in STRUCTURE_TIMEFRAMES and (changed_from or not active):
                await asyncio.to_thread(self.period_structures.ensure, symbol, timeframe, adjustflag, True)
            if timeframe == "d" and fresh_selected and selected and count:
                formal = self.store.confirmed_daily_bars(symbol, adjustflag)
                latest = max(selected, key=lambda row: row["trade_date"])
                accepted = {**latest, "source": latest.get("source", selection.get("primary_source") or "baostock")}
                if (formal and formal[-1]["trade_date"] == latest["trade_date"]
                        and self.store._daily_bar_hash(formal[-1]) == self.store._daily_bar_hash(accepted)):
                    self.intraday.record_formal_daily_refresh(
                        symbol, adjustflag, fetched_at, latest["trade_date"],
                    )
            coverage = selection["coverage"]
            conflicts = selection["source_conflicts"]
            cache_only = (selection.get("primary_source") == "cache"
                          and not any(rows for rows in normalized.values()))
            status = "success" if coverage.get("complete") and not conflicts and not cache_only else "partial"
            reason = ("外部行情源未返回可用数据，保留现有缓存" if cache_only
                      else self._coverage_reason(coverage, conflicts))
            gaps = uncovered_ranges(coverage)
            self.store.finish_market_sync(
                audit_id, status,
                selected_sources=selection["selected_sources"],
                candidate_summaries=self._audit_summaries(selection, errors),
                uncovered_ranges=gaps, source_conflicts=conflicts,
                coverage=coverage, coverage_reason=reason,
            )
            range_start, range_end = self.store.market_range(symbol, timeframe, adjustflag)
            return {
                "result": "success" if status == "success" else "partial",
                "rows_received": count, "range_start": range_start, "range_end": range_end,
                "changed_from": changed_from, "coverage": coverage,
                "selected_sources": selection["selected_sources"],
                "source_conflicts": conflicts, "uncovered_ranges": gaps,
            }
        except Exception as exc:
            self.store.finish_market_sync(audit_id, "failed", error=f"{type(exc).__name__}: {exc}",
                                          coverage_reason="所有候选源均无法产生可用结果")
            raise

    async def _sync_higher_period(self, symbol: str, timeframe: str, mode: str,
                                  adjustflag: str, profile: dict) -> dict:
        if not self.store.confirmed_daily_bars(symbol, adjustflag):
            daily = await self._sync_history(symbol, "d", mode, adjustflag, profile)
            if daily.get("result") == "failed":
                return daily
        ipo = str(profile.get("ipo_date") or HISTORY_START)[:10]
        await self.ensure_trade_calendar_start(ipo)
        current_key = period_key(datetime.now(TZ), timeframe)
        daily_rows = self.store.confirmed_daily_bars(symbol, adjustflag)
        derived = normalize_candidate_rows(
            aggregate_daily_history(daily_rows, timeframe, exclude_key=current_key),
            timeframe, "daily_aggregate",
        )
        completed_dates = [day for day in self.store.trading_dates(ipo, self._last_completed_date())
                           if period_key(day, timeframe) != current_key]
        target_end = completed_dates[-1] if completed_dates else self._last_completed_date()
        audit_id = self.store.begin_market_sync(symbol, timeframe, adjustflag, mode, ipo, target_end)
        try:
            raw_candidates, errors = await self._fetch_source_candidates(
                symbol, timeframe, ipo, date.today().isoformat(), adjustflag,
            )
            normalized = {source: [row for row in normalize_candidate_rows(rows, timeframe, source)
                                   if row["period_key"] != current_key]
                          for source, rows in raw_candidates.items()}
            normalized["daily_aggregate"] = derived
            cached = [row for row in normalize_candidate_rows(
                self.store.market_bars(symbol, timeframe, adjustflag, "0000-01-01"), timeframe, "cache",
            ) if row["period_key"] != current_key]
            starts = [rows[0]["trade_date"][:10] for rows in normalized.values() if rows]
            if cached:
                starts.append(cached[0]["trade_date"][:10])
            target_start = ipo if profile.get("ipo_date") else (min(starts) if starts else ipo)
            completed_dates = [day for day in completed_dates if day >= target_start]
            target_end = completed_dates[-1] if completed_dates else target_end
            self.store.update_market_sync_target(audit_id, target_start, target_end)
            candidates = [Candidate(
                source, rows,
                candidate_summary(source, rows, timeframe, completed_dates, target_start, target_end, datetime.now(TZ)),
            ) for source, rows in normalized.items() if rows]
            if cached:
                candidates.append(Candidate(
                    "cache", cached,
                    candidate_summary("cache", cached, timeframe, completed_dates, target_start, target_end, datetime.now(TZ)),
                ))
            selection = select_and_fill(
                candidates, timeframe, expected_dates=completed_dates,
                target_start=target_start, target_end=target_end, now=datetime.now(TZ),
            )
            selection = self._preserve_cache_on_conflict(
                selection, cached, timeframe, completed_dates, target_start, target_end,
            )
            rows = selection["rows"]
            if not rows:
                raise RuntimeError("没有可持久化的已收口高周期行情")
            stamp = datetime.now(TZ)
            count, changed_from = self.store.upsert_bars_with_changes(
                symbol, timeframe, adjustflag, rows,
                source=selection.get("primary_source") or "daily_aggregate",
                allow_lower_priority=True,
            )
            self.store.confirm_period_bars(symbol, timeframe, adjustflag, rows, stamp, stamp)
            active = self.store.active_chan_run(symbol, timeframe, adjustflag)
            if timeframe in STRUCTURE_TIMEFRAMES and (changed_from or not active):
                await asyncio.to_thread(self.period_structures.ensure, symbol, timeframe, adjustflag, True)
            if timeframe in {"w", "m"}:
                await asyncio.to_thread(self.intraday.refresh_period, symbol, timeframe, adjustflag, True)
            coverage = selection["coverage"]
            conflicts = selection["source_conflicts"]
            status = "success" if coverage.get("complete") and not conflicts else "partial"
            reason = self._coverage_reason(coverage, conflicts)
            gaps = uncovered_ranges(coverage)
            self.store.finish_market_sync(
                audit_id, status, selected_sources=selection["selected_sources"],
                candidate_summaries=self._audit_summaries(selection, errors),
                uncovered_ranges=gaps, source_conflicts=conflicts,
                coverage=coverage, coverage_reason=reason,
            )
            range_start, range_end = self.store.market_range(symbol, timeframe, adjustflag)
            return {"result": "success" if status == "success" else "partial", "rows_received": count,
                    "range_start": range_start, "range_end": range_end, "changed_from": changed_from,
                    "coverage": coverage, "selected_sources": selection["selected_sources"],
                    "source_conflicts": conflicts, "uncovered_ranges": gaps}
        except Exception as exc:
            self.store.finish_market_sync(audit_id, "failed", error=f"{type(exc).__name__}: {exc}",
                                          coverage_reason="高周期候选源均无法产生可用结果")
            raise

    async def sync_one(self, symbol: str, timeframe: str, mode: str = "incremental", scheduled_for: datetime | None = None, adjustflag: str = "2"):
        try:
            if timeframe == "1":
                attempt = await asyncio.to_thread(self.intraday.refresh, symbol, adjustflag)
                if attempt["result"] == "failed":
                    raise RuntimeError(attempt["error"])
                return attempt
            profile = await self.security_profile(symbol)
            if timeframe in {"w", "m", "y"}:
                return await self._sync_higher_period(symbol, timeframe, mode, adjustflag, profile)
            return await self._sync_history(symbol, timeframe, mode, adjustflag, profile)
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
