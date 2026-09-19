import asyncio
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app import intraday, sync
from app.intraday import IntradayService
from app.store import Store


TZ = ZoneInfo("Asia/Shanghai")
SYMBOL = "000001"


def bar(day="2026-09-18", **changes):
    return {"trade_date": day, "open": 10, "high": 12, "low": 9, "close": 11,
            "volume": 100, "amount": 1100, **changes}


@pytest.fixture
def market(tmp_path):
    clock = [datetime(2026, 9, 18, 10, 0, tzinfo=TZ)]
    store = Store(str(tmp_path / "confirmations.db"), clock=lambda: clock[0])
    store.upsert_trade_calendar([
        {"trade_date": (clock[0].date() + timedelta(days=offset)).isoformat(),
         "is_trading_day": (clock[0] + timedelta(days=offset)).weekday() < 5}
        for offset in range(-30, 16)
    ])
    yield store, clock
    store.db.close()


def post_close(clock):
    clock[0] = clock[0].replace(hour=15, minute=0, second=15)
    return clock[0]


def test_legacy_baseline_runs_once_and_never_promotes_current_or_future(tmp_path):
    path = str(tmp_path / "legacy.db")
    now = datetime(2026, 9, 18, 10, tzinfo=TZ)
    legacy = Store(path, clock=lambda: now)
    legacy.upsert_bars(SYMBOL, "d", "2", [bar("2026-09-17"), bar(), bar("2026-09-21")])
    legacy.db.execute("DROP TABLE daily_bar_confirmations")
    legacy.db.commit()
    legacy.db.close()

    migrated = Store(path, clock=lambda: now)
    assert [row["trade_date"] for row in migrated.confirmed_daily_bars(SYMBOL)] == ["2026-09-17"]
    migrated.db.close()
    restarted = Store(path, clock=lambda: now + timedelta(days=4))
    assert [row["trade_date"] for row in restarted.confirmed_daily_bars(SYMBOL)] == ["2026-09-17"]
    restarted.db.close()


@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
def test_interrupted_first_migration_rolls_back_table_and_partial_baseline(tmp_path, monkeypatch, failure):
    path = str(tmp_path / "interrupted-migration.db")
    now = datetime(2026, 9, 18, 10, tzinfo=TZ)
    legacy = Store(path, clock=lambda: now)
    legacy.upsert_bars(SYMBOL, "d", "2", [bar("2026-09-16"), bar("2026-09-17"), bar()])
    before = legacy.market_bars(SYMBOL, "d", "2")
    legacy.db.execute("DROP TABLE daily_bar_confirmations")
    legacy.db.commit()
    legacy.db.close()

    def interrupt_after_first_insert(self, migrated_at):
        row = dict(self.db.execute("SELECT * FROM market_bars ORDER BY trade_date LIMIT 1").fetchone())
        self.db.execute("""INSERT INTO daily_bar_confirmations
            (symbol,adjustflag,trade_date,row_hash,confirmed_at) VALUES(?,?,?,?,?)""",
            (row["symbol"], row["adjustflag"], row["trade_date"],
             self._daily_bar_hash(row), migrated_at.isoformat()))
        assert self.db.execute("SELECT COUNT(*) FROM daily_bar_confirmations").fetchone()[0] == 1
        raise failure("interrupted after first baseline row")

    with monkeypatch.context() as patch:
        patch.setattr(Store, "_backfill_daily_confirmations", interrupt_after_first_insert)
        with pytest.raises(failure, match="interrupted after first baseline row"):
            Store(path, clock=lambda: now)
    persisted = sqlite3.connect(path)
    assert persisted.execute("SELECT 1 FROM sqlite_master WHERE name='daily_bar_confirmations'").fetchone() is None
    assert persisted.execute("SELECT COUNT(*) FROM market_bars").fetchone()[0] == 3
    persisted.close()

    recovered = Store(path, clock=lambda: now)
    assert recovered.market_bars(SYMBOL, "d", "2") == before
    assert [row["trade_date"] for row in recovered.confirmed_daily_bars(SYMBOL)] == [
        "2026-09-16", "2026-09-17",
    ]
    recovered.db.close()
    restarted = Store(path, clock=lambda: now + timedelta(days=1))
    assert [row["trade_date"] for row in restarted.confirmed_daily_bars(SYMBOL)] == [
        "2026-09-16", "2026-09-17",
    ]
    restarted.db.close()


def test_intraday_csv_does_not_become_formal_after_close_cross_day_or_restart(market):
    store, clock = market
    row = bar(source="csv")
    store.upsert_bars(SYMBOL, "d", "2", [row])
    assert store.confirmed_daily_bars(SYMBOL) == []
    post_close(clock)
    assert store.confirmed_daily_bars(SYMBOL) == []
    clock[0] += timedelta(days=1)
    # Even explicitly re-importing the same old cache is not new fetch evidence.
    store.upsert_bars(SYMBOL, "d", "2", [row])
    store._initialize()
    assert store.confirmed_daily_bars(SYMBOL) == []
    assert store.confirm_daily_bars(SYMBOL, "2", [row], clock[0], clock[0]) == 1
    assert store.confirmed_daily_bars(SYMBOL)[0]["close"] == 11


def test_new_historical_import_is_explicit_completed_source(market):
    store, _ = market
    store.upsert_bars(SYMBOL, "d", "2", [bar("2026-09-17", source="csv")])
    assert [row["trade_date"] for row in store.confirmed_daily_bars(SYMBOL)] == ["2026-09-17"]
    assert store.confirmed_daily_bars(SYMBOL, "1") == []


@pytest.mark.parametrize("change", [{"close": 11.5}, {"amount": 1200}, {"volume": 101},
                                    {"source_revision": "revision-2"}, {"adjust_factor": 1.1}])
def test_content_changes_invalidate_proof_and_reversion_cannot_resurrect_it(market, change):
    store, clock = market
    old = bar("2026-09-17")
    store.upsert_bars(SYMBOL, "d", "2", [old])
    assert len(store.confirmed_daily_bars(SYMBOL)) == 1
    _, changed_from = store.upsert_bars_with_changes(SYMBOL, "d", "2", [{**old, **change}])
    assert changed_from == old["trade_date"]
    assert store.confirmed_daily_bars(SYMBOL) == []
    store.upsert_bars(SYMBOL, "d", "2", [old])
    assert store.confirmed_daily_bars(SYMBOL) == []
    assert store.confirm_daily_bars(SYMBOL, "2", [old], clock[0]) == 1


def test_request_must_begin_after_close_guard_and_exactly_match_accepted_content(market):
    store, clock = market
    row = bar()
    store.upsert_bars(SYMBOL, "d", "2", [row])
    started = clock[0].replace(hour=15, minute=0, second=14)
    received = post_close(clock)
    assert store.confirm_daily_bars(SYMBOL, "2", [row], received, started) == 0
    assert store.confirmed_daily_bars(SYMBOL) == []
    assert store.confirm_daily_bars(SYMBOL, "2", [bar(close=12)], received, received) == 0
    assert store.confirm_daily_bars(SYMBOL, "2", [row], received, received) == 1
    assert len(store.confirmed_daily_bars(SYMBOL)) == 1


@pytest.mark.parametrize("calendar_value", [None, False])
def test_same_day_confirmation_requires_positive_calendar(market, calendar_value):
    store, clock = market
    store.db.execute("DELETE FROM trade_calendar WHERE trade_date='2026-09-18'")
    store.db.commit()
    if calendar_value is not None:
        store.upsert_trade_calendar([{"trade_date": "2026-09-18", "is_trading_day": calendar_value}])
    at = post_close(clock)
    store.upsert_bars_with_changes(SYMBOL, "d", "2", [bar()], fetched_at=at, request_started_at=at)
    assert store.confirmed_daily_bars(SYMBOL) == []


def test_rejected_lower_priority_payload_cannot_confirm_an_intraday_csv(market):
    store, clock = market
    store.upsert_bars(SYMBOL, "d", "2", [bar(source="csv")])
    at = post_close(clock)
    assert store.upsert_bars_with_changes(
        SYMBOL, "d", "2", [bar()], fetched_at=at, request_started_at=at,
    ) == (0, None)
    assert store.confirmed_daily_bars(SYMBOL) == []
    assert store.confirm_daily_bars(SYMBOL, "2", [bar()], at, at) == 0
    assert store.market_bars(SYMBOL, "d", "2")[0]["source"] == "csv"


def test_confirmation_failure_rolls_back_market_write_and_proof(market, monkeypatch):
    store, clock = market
    store.upsert_bars(SYMBOL, "d", "2", [bar("2026-09-17")])
    before = store.market_bars(SYMBOL, "d", "2")
    proof = list(store.db.execute("SELECT * FROM daily_bar_confirmations"))

    def fail(*args):
        raise RuntimeError("confirmation failure")

    monkeypatch.setattr(store, "_confirm_daily_rows", fail)
    with pytest.raises(RuntimeError, match="confirmation failure"):
        store.upsert_bars_with_changes(SYMBOL, "d", "2", [bar("2026-09-17", close=12)],
                                       fetched_at=clock[0], request_started_at=clock[0])
    assert store.market_bars(SYMBOL, "d", "2") == before
    assert list(store.db.execute("SELECT * FROM daily_bar_confirmations")) == proof


def test_confirmation_is_not_bound_to_sqlite_id_and_rejects_invalid_timestamps(market):
    store, _ = market
    store.upsert_bars(SYMBOL, "d", "2", [bar("2026-09-17")])
    store.db.execute("UPDATE market_bars SET id=id+100")
    store.db.commit()
    assert len(store.confirmed_daily_bars(SYMBOL)) == 1
    for invalid in ("invalid", "2026-09-17T15:00:14+08:00", "2099-01-01T15:00:15+08:00"):
        store.db.execute("UPDATE daily_bar_confirmations SET confirmed_at=?", (invalid,))
        store.db.commit()
        assert store.confirmed_daily_bars(SYMBOL) == []


def test_intraday_fetch_crossing_guard_remains_unconfirmed_until_new_request(market, monkeypatch):
    store, clock = market
    service = IntradayService(store, clock=lambda: clock[0])
    clock[0] = clock[0].replace(hour=15, minute=0, second=14)

    def fetch(*args):
        clock[0] += timedelta(seconds=1)
        return [bar()]

    monkeypatch.setattr(intraday, "fetch_tencent", fetch)
    first = service.refresh_period(SYMBOL, "d")
    assert first["result"] == "success"
    assert first["finalized_count"] == 0
    assert store.confirmed_daily_bars(SYMBOL) == []
    second = service.refresh_period(SYMBOL, "d")
    assert second["finalized_count"] == 1
    assert store.confirmed_daily_bars(SYMBOL)[0]["close"] == 11


def test_refresh_failure_keeps_confirmed_history_but_does_not_promote_current_cache(market, monkeypatch):
    store, clock = market
    store.upsert_bars(SYMBOL, "d", "2", [bar("2026-09-17"), bar()])
    service = IntradayService(store, clock=lambda: clock[0])
    post_close(clock)

    def fail(*args):
        raise TimeoutError()

    monkeypatch.setattr(intraday, "fetch_tencent", fail)
    assert service.refresh_period(SYMBOL, "d")["result"] == "failed"
    assert [row["trade_date"] for row in store.confirmed_daily_bars(SYMBOL)] == ["2026-09-17"]


def test_preclose_quote_cannot_rewrite_postclose_daily_response(market, monkeypatch):
    store, clock = market
    post_close(clock)
    service = IntradayService(store, clock=lambda: clock[0])
    monkeypatch.setattr(intraday, "fetch_tencent", lambda *args: [bar()])
    monkeypatch.setattr(intraday, "fetch_tencent_quotes", lambda *args: [{
        "symbol": SYMBOL, "latest": 12, "quote_time": "20260918145959", "status": "success",
    }])
    assert service.refresh_period(SYMBOL, "d", include_quote=True)["result"] == "success"
    assert store.confirmed_daily_bars(SYMBOL)[0]["close"] == 11


def test_sync_records_confirmation_and_recomputes_when_prices_are_unchanged(market, monkeypatch):
    store, clock = market
    row = bar()
    store.upsert_bars(SYMBOL, "d", "2", [row])
    post_close(clock)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz) if tz else clock[0].replace(tzinfo=None)

    class Structures:
        def __init__(self):
            self.calls = []

        def ensure(self, *args):
            self.calls.append(args)

    structures = Structures()
    monkeypatch.setattr(sync, "datetime", Clock)
    monkeypatch.setattr(sync, "fetch_baostock", lambda *args: [row])
    monkeypatch.setattr(store, "active_chan_run", lambda *args: {"id": 1})
    result = asyncio.run(sync.SyncService(store, structures=structures).sync_one(SYMBOL, "d"))
    assert result["result"] == "success"
    assert result["changed_from"] == row["trade_date"]
    assert len(store.confirmed_daily_bars(SYMBOL)) == 1
    assert len(structures.calls) == 1


@pytest.mark.parametrize("outcome", ["confirmed", "intraday", "empty", "rejected"])
def test_history_sync_clears_old_refresh_error_only_after_usable_confirmed_response(market, monkeypatch, outcome):
    store, clock = market
    store.upsert_bars(SYMBOL, "d", "2", [bar(source="csv") if outcome == "rejected" else bar()])
    if outcome != "intraday":
        post_close(clock)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz) if tz else clock[0].replace(tzinfo=None)

    class Structures:
        def ensure(self, *args):
            return None

    service = IntradayService(store, clock=lambda: clock[0])
    failed = {"result": "failed", "error": "old remote timeout", "finalized_count": 0}
    service._attempts[(SYMBOL, "d", "2")] = failed
    service._record_daily_source_attempt(SYMBOL, "2", failed)
    service._live_rows[(SYMBOL, "d", "2")] = [bar(close=10.5)]
    monkeypatch.setattr(sync, "datetime", Clock)
    monkeypatch.setattr(sync, "fetch_baostock", lambda *args: [] if outcome == "empty" else [bar()])
    result = asyncio.run(sync.SyncService(store, structures=Structures(), intraday=service).sync_one(SYMBOL, "d"))
    assert result["result"] == "success"
    if outcome == "confirmed":
        assert service.daily_source_error(SYMBOL) is None
        assert service.live_rows(SYMBOL, "d") == []
        # A throttled next refresh must not resurrect the prior failed attempt.
        monkeypatch.setattr(intraday, "fetch_tencent", lambda *args: pytest.fail("应使用同步后的成功状态"))
        assert service.refresh_period(SYMBOL, "d")["result"] == "success"
    else:
        assert service.daily_source_error(SYMBOL) == "old remote timeout"
        assert service._attempts[(SYMBOL, "d", "2")]["result"] == "failed"
        assert service.live_rows(SYMBOL, "d")
