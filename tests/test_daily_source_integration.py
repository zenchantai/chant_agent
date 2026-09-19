import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest

from app import intraday as intraday_module, main
from app.intraday import IntradayService, TZ
from app.period_structure import PeriodStructureService
from app.store import Store


SYMBOL = "000001"


def bar(stamp, close=10):
    return {"trade_date": stamp, "open": 10, "high": max(11, close), "low": 9,
            "close": close, "volume": 100, "amount": 1000}


@pytest.fixture
def source(tmp_path, monkeypatch):
    clock = [datetime(2026, 9, 17, 10, tzinfo=TZ)]
    store = Store(str(tmp_path / "source.db"), clock=lambda: clock[0])
    store.upsert_trade_calendar([
        {"trade_date": (clock[0] + timedelta(days=offset)).date().isoformat(),
         "is_trading_day": (clock[0] + timedelta(days=offset)).weekday() < 5}
        for offset in range(-35, 20)
    ])
    # The middle day was cached intraday and is intentionally still unconfirmed.
    store.upsert_bars(SYMBOL, "d", "2", [bar("2026-09-16"), bar("2026-09-17")])
    clock[0] = datetime(2026, 9, 19, 10, tzinfo=TZ)
    store.upsert_bars(SYMBOL, "d", "2", [bar("2026-09-18")])
    store.upsert_bars(SYMBOL, "m", "2", [bar("2026-08-31")])
    structures = PeriodStructureService(store)
    intraday = IntradayService(store, clock=lambda: clock[0])
    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(main, "period_structure_service", structures)
    monkeypatch.setattr(main, "intraday_service", intraday)
    yield store, structures, intraday, clock
    store.db.close()


def test_formal_daily_coverage_excludes_unconfirmed_cache_and_exposes_gap(source):
    store, structures, _, _ = source
    result = main.chart_data(SYMBOL, timeframe="d")
    assert [row["trade_date"] for row in store.confirmed_daily_bars(SYMBOL)] == [
        "2026-09-16", "2026-09-18",
    ]
    assert structures.coverage(SYMBOL, "d")["missing_sessions"] == ["2026-09-17"]
    for field in ("coverage", "formal_coverage", "sampling_coverage"):
        assert result["meta"][field]["missing_sessions"] == ["2026-09-17"]
    assert result["meta"]["preview"] is False
    assert result["meta"]["market_version"] == structures.market_version(store.confirmed_daily_bars(SYMBOL))


def test_preview_coverage_is_separate_from_formal_source(source):
    _, _, intraday, clock = source
    clock[0] = datetime(2026, 9, 21, 10, tzinfo=TZ)
    intraday._live_rows[(SYMBOL, "d", "2")] = [bar("2026-09-21", 12)]
    result = main.chart_data(SYMBOL, timeframe="d")
    assert result["meta"]["preview"] is True and result["meta"]["persisted"] is False
    assert result["meta"]["formal_coverage"]["missing_sessions"] == ["2026-09-17"]
    assert result["meta"]["sampling_coverage"] == result["meta"]["coverage"]
    assert result["meta"]["sampling_coverage"]["range_end"] == "2026-09-21"
    assert result["meta"]["formal_coverage"]["range_end"] == "2026-09-18"


def test_refresh_failure_survives_cached_and_paginated_reads_and_quote_success(source, monkeypatch):
    _, _, service, clock = source
    clock[0] = datetime(2026, 9, 21, 10, tzinfo=TZ)

    def unavailable(*args):
        raise TimeoutError()

    monkeypatch.setattr(intraday_module, "fetch_tencent", unavailable)
    monkeypatch.setattr(intraday_module, "fetch_tencent_quotes", unavailable)
    failed = main.chart_data(SYMBOL, timeframe="m", refresh=True)
    assert failed["overlays"]["daily_l2"]["status"] == "stale"
    error = failed["overlays"]["daily_l2"]["error"]
    for before in (None, "2026-09-01"):
        cached = main.chart_data(SYMBOL, timeframe="m", before=before)
        assert cached["overlays"]["daily_l2"]["status"] == "stale"
        assert cached["overlays"]["daily_l2"]["error"] == error
    # Auction quotes are successful, but do not repair the daily formal source.
    clock[0] = datetime(2026, 9, 22, 9, 20, tzinfo=TZ)
    service._attempts.clear()
    monkeypatch.setattr(intraday_module, "fetch_tencent_quotes", lambda *args: [{
        "status": "success", "quote_time": "20260922092000", "latest": 12,
    }])
    assert service.refresh_period(SYMBOL, "d", include_quote=True)["result"] == "success"
    assert main.chart_data(SYMBOL, timeframe="m")["overlays"]["daily_l2"]["status"] == "stale"
    # An intraday daily preview cannot repair it either.
    clock[0] = datetime(2026, 9, 22, 10, tzinfo=TZ)
    service._attempts.clear()
    monkeypatch.setattr(intraday_module, "fetch_tencent", lambda *args: [bar("2026-09-22")])
    assert service.refresh_period(SYMBOL, "d")["result"] == "success"
    assert main.chart_data(SYMBOL, timeframe="m")["overlays"]["daily_l2"]["status"] == "stale"
    # Fresh post-close daily rows are confirmed and clear the remembered error.
    clock[0] = datetime(2026, 9, 22, 15, 0, 15, tzinfo=TZ)
    assert service.refresh_period(SYMBOL, "d")["formal_daily_confirmed"] is True
    recovered = main.chart_data(SYMBOL, timeframe="m")["overlays"]["daily_l2"]
    assert recovered["status"] == "ready" and recovered["error"] is None
    assert recovered["source"]["source_cutoff"] == "2026-09-22"


def test_capture_keeps_native_market_and_run_consistent_during_concurrent_update(source, monkeypatch):
    store, structures, _, _ = source
    old_version = structures.market_version(store.market_bars(SYMBOL, "m", "2", "0000-01-01"))
    start_writer, writer_entered, writer_done = threading.Event(), threading.Event(), threading.Event()
    original_page = structures.chart_page

    def page(*args, **kwargs):
        start_writer.set()
        assert writer_entered.wait(2)
        assert not writer_done.wait(.05)
        return original_page(*args, **kwargs)

    def update():
        assert start_writer.wait(2)
        writer_entered.set()
        store.upsert_bars(SYMBOL, "m", "2", [bar("2026-08-31", 12)])
        writer_done.set()

    monkeypatch.setattr(structures, "chart_page", page)
    with ThreadPoolExecutor(max_workers=2) as executor:
        writer = executor.submit(update)
        result = main.chart_data(SYMBOL, timeframe="m")
        writer.result(timeout=2)
    assert result["market"]["bars"][0]["close"] == 10
    assert result["meta"]["market_version"] == old_version
    assert store.market_bars(SYMBOL, "m", "2")[0]["close"] == 12


def test_network_and_calendar_helpers_run_outside_capture_lock(source, monkeypatch):
    store, structures, service, _ = source
    original_calendar, original_ensure = service.calendar, structures.ensure
    calls = []

    def refresh(symbol, timeframe, adjustflag, include_quote=False):
        assert not store._lock._is_owned()
        calls.append(timeframe)
        return {"result": "success", "finalized_count": 0}

    def calendar(fetch):
        assert not store._lock._is_owned()
        assert not fetch
        return original_calendar(fetch)

    def ensure(*args, **kwargs):
        assert store._lock._is_owned()
        return original_ensure(*args, **kwargs)

    monkeypatch.setattr(service, "refresh_period", refresh)
    monkeypatch.setattr(service, "calendar", calendar)
    monkeypatch.setattr(structures, "ensure", ensure)
    main.chart_data(SYMBOL, timeframe="m", refresh=True)
    assert calls == ["m", "d"]
    calls.clear()
    main.chart_data(SYMBOL, timeframe="m")
    assert calls == []


def test_calendar_gap_change_invalidates_an_existing_structure_run(source):
    store, structures, _, _ = source
    store.db.execute("DELETE FROM trade_calendar WHERE trade_date='2026-09-17'")
    store.db.commit()
    first = structures.ensure(SYMBOL, "d")
    assert first["meta"]["coverage"]["missing_sessions"] == []
    store.upsert_trade_calendar([{"trade_date": "2026-09-17", "is_trading_day": True}])
    second = structures.ensure(SYMBOL, "d")
    assert second["meta"]["market_version"] == first["meta"]["market_version"]
    assert second["meta"]["run_id"] != first["meta"]["run_id"]
    assert second["meta"]["coverage"]["missing_sessions"] == ["2026-09-17"]
