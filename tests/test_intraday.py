import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import intraday, main
from app.intraday import IntradayService, TZ, period_key, session_state, validate_period_rows, validate_rows
from app.store import Store
from app.sync import SyncService


def bar(stamp="2026-09-16 10:00:00", close=11):
    return {"trade_date": stamp, "open": 10, "high": max(12, close), "low": 9,
            "close": close, "volume": 100, "amount": 0}


@pytest.fixture
def service(tmp_path, monkeypatch):
    store = Store(str(tmp_path / "market.db"))
    now = datetime(2026, 9, 16, 10, 10, tzinfo=TZ)
    rows = []
    for offset in range(-30, 15):
        day = now.date() + timedelta(days=offset)
        rows.append({"trade_date": day.isoformat(), "is_trading_day": day.weekday() < 5})
    store.upsert_trade_calendar(rows)
    monkeypatch.setattr(intraday, "fetch_trade_calendar", lambda *args: pytest.fail("不应获取已有日历"))
    return IntradayService(store, lambda: now)


@pytest.mark.parametrize("clock,phase,next_clock", [
    ("09:14:59", "closed", "09:15"), ("09:15:00", "auction", "09:30"),
    ("09:29:59", "auction", "09:30"), ("09:30:00", "trading", "11:30"),
    ("11:29:59", "trading", "11:30"), ("11:30:00", "lunch", "13:00"),
    ("12:59:59", "lunch", "13:00"), ("13:00:00", "trading", "15:00"),
    ("14:59:59", "trading", "15:00"), ("15:00:00", "closed", "09:30"),
])
def test_session_boundaries(clock, phase, next_clock):
    state = session_state(datetime.fromisoformat(f"2026-09-16T{clock}+08:00"),
                          {"2026-09-16": True, "2026-09-17": True})
    assert state["phase"] == phase
    assert state["next_transition_at"][11:16] == next_clock


def test_holiday_weekend_and_unknown():
    now = datetime(2026, 9, 19, 10, tzinfo=TZ)
    assert session_state(now, {})["phase"] == "unknown"
    assert session_state(now, {"2026-09-19": False})["phase"] == "closed"
    holiday = datetime(2026, 10, 1, 10, tzinfo=TZ)
    assert session_state(holiday, {"2026-10-01": False})["phase"] == "closed"
    assert session_state(now.astimezone(TZ), {"2026-09-19": False}) == session_state(
        now.astimezone(timezone.utc), {"2026-09-19": False})


def test_refresh_replaces_day_preserves_other_data_and_updates_same_minute(service, monkeypatch):
    store = service.store
    store.upsert_bars("000001", "1", "2", [bar("2026-09-15 15:00:00")])
    store.upsert_bars("000001", "d", "2", [bar("2026-09-15")])
    store.upsert_bars("000002", "1", "2", [bar("2026-09-15 15:00:00")])
    supplied = [bar(), bar("2026-09-15 15:00:00"), bar(close=12), bar("2026-09-16 09:59:00")]
    monkeypatch.setattr(intraday, "fetch_tencent", lambda *args: supplied)
    assert service.refresh("000001")["result"] == "success"
    current = store.market_bars("000001", "1", "2")
    assert [row["trade_date"] for row in current] == ["2026-09-16 09:59:00", "2026-09-16 10:00:00"]
    assert current[-1]["close"] == 12
    assert len(store.market_bars("000001", "d", "2")) == 1
    assert store.market_bars("000002", "1", "2")[0]["trade_date"].startswith("2026-09-15")
    assert store.db.execute("SELECT COUNT(*) FROM period_structure_runs").fetchone()[0] == 0
    supplied[-1] = bar(close=11.5)
    service._attempts.clear()
    service.refresh("000001")
    assert store.market_bars("000001", "1", "2")[-1]["close"] == 11.5


@pytest.mark.parametrize("response", [[], [bar("2026-09-15 15:00:00")], [bar("2026-09-16 09:59:00")],
                                       [bar(close=float("nan"))], [bar("2026-09-16 12:00:00")]])
def test_invalid_empty_or_older_response_preserves_cache(service, monkeypatch, response):
    service.replace_intraday("000001", "2", [bar()])
    before = service.store.market_bars("000001", "1", "2")
    monkeypatch.setattr(intraday, "fetch_tencent", lambda *args: response)
    assert service.refresh("000001")["result"] in {"failed", "stale"}
    assert service.store.market_bars("000001", "1", "2") == before


def test_failure_ttl_and_recovery(service, monkeypatch):
    calls = []
    tick = [100.0]
    monkeypatch.setattr(intraday.time, "monotonic", lambda: tick[0])

    def fetch(*args):
        calls.append(args)
        raise TimeoutError()

    monkeypatch.setattr(intraday, "fetch_tencent", fetch)
    assert service.refresh("000001")["result"] == "failed"
    tick[0] += 14
    service.refresh("000001")
    assert len(calls) == 1
    tick[0] += 1
    monkeypatch.setattr(intraday, "fetch_tencent", lambda *args: [bar()])
    assert service.refresh("000001")["result"] == "success"


def test_concurrent_live_and_scheduled_refresh_share_request(service, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def fetch(*args):
        calls.append(args)
        entered.set()
        assert release.wait(3)
        return [bar()]

    monkeypatch.setattr(intraday, "fetch_tencent", fetch)
    sync = SyncService(service.store, intraday=service)
    with ThreadPoolExecutor(max_workers=3) as executor:
        live = executor.submit(service.refresh, "000001")
        assert entered.wait(3)
        scheduled = executor.submit(asyncio.run, sync.sync_one("000001", "1"))
        second = executor.submit(service.refresh, "000001")
        release.set()
        assert live.result()["result"] == second.result()["result"] == "success"
        scheduled.result()
    assert len(calls) == 1
    assert service.store.db.execute("SELECT COUNT(*) FROM period_structure_runs").fetchone()[0] == 0


@pytest.mark.parametrize(("timeframe", "now_clock", "valid_stamps", "future_stamp"), [
    ("5", "10:12:00", ["2026-09-16 10:10:00", "2026-09-16 10:15:00"], "2026-09-16 10:20:00"),
    ("15", "10:12:00", ["2026-09-16 10:00:00", "2026-09-16 10:15:00"], "2026-09-16 10:30:00"),
    ("30", "10:12:00", ["2026-09-16 10:00:00", "2026-09-16 10:30:00"], "2026-09-16 11:00:00"),
    ("60", "10:12:00", ["2026-09-16 10:30:00"], "2026-09-16 11:30:00"),
    ("120", "10:12:00", ["2026-09-16 11:30:00"], "2026-09-16 15:00:00"),
])
def test_validate_period_rows_keeps_current_day_and_current_forming_bar(
        timeframe, now_clock, valid_stamps, future_stamp):
    now = datetime.fromisoformat(f"2026-09-16T{now_clock}+08:00")
    rows = [bar("2026-09-15 15:00:00"), *(bar(stamp) for stamp in valid_stamps), bar(future_stamp)]

    accepted = validate_period_rows(rows, timeframe, now)

    assert [row["trade_date"] for row in accepted] == valid_stamps
    assert all(row["source"] == "tencent" for row in accepted)


@pytest.mark.parametrize(("timeframe", "invalid"), [
    ("5", bar("2026-09-16 10:12:00")),
    ("30", bar("2026-09-16 10:15:00")),
    ("5", {**bar("2026-09-16 10:10:00"), "low": 11.5}),
    ("30", {**bar("2026-09-16 10:00:00"), "volume": -1}),
    ("15", bar("2026-09-16 10:12:00")),
    ("60", bar("2026-09-16 10:00:00")),
    ("120", bar("2026-09-16 10:30:00")),
])
def test_validate_period_rows_rejects_bad_grid_ohlc_and_volume(service, timeframe, invalid):
    with pytest.raises(ValueError, match="无效周期数据"):
        validate_period_rows([invalid], timeframe, service.clock())


@pytest.mark.parametrize(("timeframe", "confirmed_stamp", "forming_stamp", "before", "finalized"), [
    ("5", "2026-09-16 10:10:00", "2026-09-16 10:15:00", "2026-09-16T10:14:50+08:00", "2026-09-16T10:15:15+08:00"),
    ("30", "2026-09-16 10:00:00", "2026-09-16 10:30:00", "2026-09-16T10:29:50+08:00", "2026-09-16T10:30:15+08:00"),
    ("15", "2026-09-16 10:00:00", "2026-09-16 10:15:00", "2026-09-16T10:14:50+08:00", "2026-09-16T10:15:15+08:00"),
    ("60", "2026-09-16 10:30:00", "2026-09-16 11:30:00", "2026-09-16T11:29:50+08:00", "2026-09-16T11:30:15+08:00"),
    ("120", "2026-09-16 11:30:00", "2026-09-16 15:00:00", "2026-09-16T14:59:50+08:00", "2026-09-16T15:00:15+08:00"),
])
def test_period_refresh_preserves_history_and_persists_only_after_boundary(
        service, monkeypatch, timeframe, confirmed_stamp, forming_stamp, before, finalized):
    clock = [datetime.fromisoformat(before)]
    service.clock = lambda: clock[0]
    historical = bar("2026-09-15 15:00:00", 8)
    old_confirmed = bar(confirmed_stamp, 10)
    service.store.upsert_bars("000001", timeframe, "2", [historical, old_confirmed])
    supplied = [bar(confirmed_stamp, 11), bar(forming_stamp, 12)]
    calls = []
    monkeypatch.setattr(intraday, "fetch_tencent", lambda *args: calls.append(args) or supplied)

    first = service.refresh_period("000001", timeframe)

    assert first["result"] == "success"
    assert service.forming_bar("000001", timeframe) == {
        "trade_date": forming_stamp,
        "is_forming": True,
        "status": "provisional",
        "finalize_at": finalized,
    }
    stored = service.store.market_bars("000001", timeframe, "2")
    assert [row["trade_date"] for row in stored] == [historical["trade_date"], confirmed_stamp]
    assert stored[-1]["close"] == 11
    assert service.store.db.execute("SELECT COUNT(*) FROM market_data_conflicts").fetchone()[0] == 1

    clock[0] = datetime.fromisoformat(finalized)
    second = service.refresh_period("000001", timeframe)

    assert len(calls) == 2
    assert second["finalized_count"] == 2
    assert service.forming_bar("000001", timeframe) is None
    assert service.live_rows("000001", timeframe) == []
    stored = service.store.market_bars("000001", timeframe, "2")
    assert [row["trade_date"] for row in stored] == [historical["trade_date"], confirmed_stamp, forming_stamp]
    assert stored[-1]["close"] == 12


@pytest.mark.parametrize(("timeframe", "stamp", "now"), [
    ("5", "2026-09-16 10:15:00", "2026-09-16T10:14:50+08:00"),
    ("30", "2026-09-16 10:30:00", "2026-09-16T10:29:50+08:00"),
    ("15", "2026-09-16 10:15:00", "2026-09-16T10:14:50+08:00"),
    ("60", "2026-09-16 10:30:00", "2026-09-16T10:29:50+08:00"),
    ("120", "2026-09-16 11:30:00", "2026-09-16T11:29:50+08:00"),
])
def test_concurrent_period_refresh_is_deduplicated_per_symbol_timeframe(
        service, monkeypatch, timeframe, stamp, now):
    service.clock = lambda: datetime.fromisoformat(now)
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def fetch(*args):
        calls.append(args)
        entered.set()
        assert release.wait(3)
        return [bar(stamp)]

    monkeypatch.setattr(intraday, "fetch_tencent", fetch)
    with ThreadPoolExecutor(max_workers=3) as executor:
        requests = [executor.submit(service.refresh_period, "000001", timeframe) for _ in range(3)]
        assert entered.wait(3)
        release.set()
        assert [request.result()["result"] for request in requests] == ["success"] * 3

    assert len(calls) == 1
    assert service.forming_bar("000001", timeframe)["trade_date"] == stamp
    assert service.store.market_bars("000001", timeframe, "2") == []


def test_calendar_failure_retries_calendar_without_repeated_quotes(service, monkeypatch):
    service.store.db.execute("DELETE FROM trade_calendar")
    service.store.db.commit()
    calls = []
    calendar_calls = []
    tick = [100.0]
    monkeypatch.setattr(intraday.time, "monotonic", lambda: tick[0])

    def calendar(*args):
        calendar_calls.append(args)
        raise RuntimeError("offline")

    monkeypatch.setattr(intraday, "fetch_trade_calendar", calendar)
    monkeypatch.setattr(intraday, "fetch_tencent", lambda *args: calls.append(args) or [bar()])
    service.refresh("000001")
    assert service.metadata("000001")["phase"] == "unknown"
    tick[0] += 60
    service.refresh("000001")
    assert len(calendar_calls) == 2
    assert len(calls) == 1
    monkeypatch.setattr(intraday, "fetch_trade_calendar", lambda *args: [{"trade_date": "2026-09-16", "is_trading_day": True}])
    tick[0] += 60
    service.refresh("000001")
    assert len(calls) == 2
    assert service.metadata("000001")["phase"] == "trading"


def test_atomic_rollback(service):
    service.replace_intraday("000001", "2", [bar("2026-09-15 15:00:00")])
    service.store.db.execute("""CREATE TRIGGER reject_intraday BEFORE INSERT ON market_bars
        WHEN NEW.trade_date LIKE '2026-09-16%' BEGIN SELECT RAISE(ABORT, 'test'); END""")
    with pytest.raises(Exception, match="test"):
        service.replace_intraday("000001", "2", [bar()])
    assert service.store.market_bars("000001", "1", "2")[0]["trade_date"].startswith("2026-09-15")


def test_api_lightweight_quote_and_parameter_validation(service, monkeypatch):
    monkeypatch.setattr(main, "intraday_service", service)
    monkeypatch.setattr(intraday, "fetch_tencent", lambda *args: [bar()])
    service.store.upsert_bars("000001", "d", "2", [bar("2026-09-15", 10)])
    client = TestClient(main.app)
    assert client.get("/api/chart-data/000001?timeframe=unknown&refresh=true").status_code == 400
    assert client.get("/api/chart-data/000001?timeframe=1&refresh=true&before=").status_code == 400
    assert client.get("/api/chart-data/000001?timeframe=5&refresh=true&before=2026-09-16%2010:00:00").status_code == 400
    cached = client.get("/api/chart-data/000001?timeframe=1").json()
    assert cached["bars"] == []
    fresh = client.get("/api/chart-data/000001?timeframe=1&refresh=true").json()
    assert fresh["quote"]["latest"] == 11
    assert fresh["quote"]["change_pct"] == 10
    assert fresh["quote"]["trade_date"].startswith("2026-09-16")
    assert fresh["quote"]["amount"] is None
    assert fresh["intraday_refresh"]["is_today"] is True
    assert fresh["intraday_refresh"]["result"] == "success"
    assert len(fresh["indicators"]["macd"]) == len(fresh["bars"])
    assert fresh["pens"] == fresh["centers"] == fresh["movements"] == []
    assert service.store.db.execute("SELECT COUNT(*) FROM period_structure_runs").fetchone()[0] == 0


@pytest.mark.parametrize(("timeframe", "stamp"), [
    ("d", "2026-09-16"), ("w", "2026-09-16"),
    ("m", "2026-09-16"), ("y", "2026-09-16"),
])
def test_calendar_period_rows_only_keep_current_period(service, timeframe, stamp):
    previous = {"d":"2026-09-15", "w":"2026-09-11", "m":"2026-08-31", "y":"2025-12-31"}[timeframe]
    rows = validate_period_rows([bar(previous), bar(stamp)], timeframe, service.clock())
    assert [row["trade_date"] for row in rows] == [stamp]
    assert period_key(stamp, timeframe) == period_key(service.clock(), timeframe)


def test_daily_weekly_monthly_yearly_finalization_uses_next_trading_period(service):
    calendar = service.calendar(False)
    daily = bar("2026-09-16")
    weekly = bar("2026-09-16")
    assert service.period_finalize_at(daily, "d", calendar).isoformat() == "2026-09-16T15:00:15+08:00"
    assert service.period_finalize_at(weekly, "w", calendar) is None
    friday = bar("2026-09-18")
    assert service.period_finalize_at(friday, "w", calendar).isoformat() == "2026-09-18T15:00:15+08:00"
    service.store.upsert_trade_calendar([
        {"trade_date":"2026-09-30", "is_trading_day":True},
        {"trade_date":"2026-10-08", "is_trading_day":True},
        {"trade_date":"2026-12-31", "is_trading_day":True},
        {"trade_date":"2027-01-04", "is_trading_day":True},
    ])
    calendar = service.calendar(False)
    assert service.period_finalize_at(bar("2026-09-30"), "m", calendar).isoformat() == "2026-09-30T15:00:15+08:00"
    assert service.period_finalize_at(bar("2026-12-31"), "y", calendar).isoformat() == "2026-12-31T15:00:15+08:00"


def test_auction_refreshes_quote_without_creating_bar(service, monkeypatch):
    service.clock = lambda: datetime(2026, 9, 16, 9, 20, tzinfo=TZ)
    monkeypatch.setattr(intraday, "fetch_tencent", lambda *args: pytest.fail("集合竞价不得制造K线"))
    monkeypatch.setattr(intraday, "fetch_tencent_quotes", lambda symbols: [{
        "symbol":"000001", "latest":10.5, "previous_close":10, "change":.5, "change_pct":5,
        "quote_time":"20260916092000", "source":"tencent", "status":"success", "error":None,
    }])
    result = service.refresh_period("000001", "d", include_quote=True)
    assert result["result"] == "success"
    assert service.live_rows("000001", "d") == []
    assert service.latest_quote("000001")["latest"] == 10.5


def test_previous_close_requires_confirmed_previous_trading_date(service):
    service.replace_intraday("000001", "2", [bar()])
    service.store.upsert_bars("000001", "d", "2", [bar("2026-09-14", 8)])
    data = service.chart_page("000001", "2", None, 300, (5, 10), 20, 2)
    assert data["previous_close"] is None
    assert data["quote"]["change_pct"] is None
    assert data["quote"]["previous_close"] is None


def test_off_hours_latest_day_not_mislabelled(service, monkeypatch):
    service.clock = lambda: datetime(2026, 9, 19, 10, tzinfo=TZ)
    monkeypatch.setattr(intraday, "fetch_trade_calendar", lambda *args: [])
    monkeypatch.setattr(intraday, "fetch_tencent", lambda *args: [bar("2026-09-18 15:00:00")])
    assert service.refresh("000001")["result"] == "success"
    metadata = service.metadata("000001")
    assert metadata["phase"] == "closed"
    assert metadata["data_date"] == "2026-09-18"
    assert metadata["is_today"] is False


def test_future_invalid_prices_and_negative_volume_rejected(service):
    for row in [bar("2026-09-17 10:00:00"), {**bar(), "volume": -1}, {**bar(), "high": 8}]:
        with pytest.raises(ValueError):
            validate_rows([row], service.clock())


def test_provider_future_minute_placeholder_does_not_discard_valid_session(service):
    now = datetime(2026, 9, 16, 12, 59, 42, tzinfo=TZ)
    valid = bar("2026-09-16 11:30:00")
    placeholder = {**bar("2026-09-16 13:01:00"), "volume": 0}
    assert validate_rows([valid, placeholder], now) == [valid]


def test_future_calendar_does_not_expand_shared_coverage_range(service, monkeypatch):
    service.store.db.execute("DELETE FROM trade_calendar WHERE trade_date>'2026-09-16'")
    service.store.db.commit()
    before = service.store.trade_calendar_range()
    monkeypatch.setattr(intraday, "fetch_trade_calendar", lambda *args: [
        {"trade_date": "2026-09-16", "is_trading_day": True},
        {"trade_date": "2026-09-17", "is_trading_day": True},
    ])
    days = service.calendar(True)
    assert days["2026-09-17"] is True
    assert service.calendar(False)["2026-09-17"] is True
    assert service.store.trade_calendar_range() == before
    service.clock = lambda: datetime(2026, 9, 16, 16, tzinfo=TZ)
    assert service.metadata("000001")["next_transition_at"] == "2026-09-17T09:30:00+08:00"
    service.clock = lambda: datetime(2026, 9, 17, 9, tzinfo=TZ)
    service._calendar_attempt = float("-inf")
    assert service.calendar(True)["2026-09-17"] is True
    assert service.store.trade_calendar_range()[1] == "2026-09-17"
