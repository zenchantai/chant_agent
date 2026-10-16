import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.intraday import aggregate_daily_history
from app.market_sources import Candidate, candidate_summary, normalize_candidate_rows, select_and_fill
from app import providers
from app import sync
from app.store import Store


TZ = ZoneInfo("Asia/Shanghai")


def bar(day: str, price: float = 10, source: str = "baostock") -> dict:
    return {
        "trade_date": day, "open": price, "high": price + 1,
        "low": price - 1, "close": price + .5,
        "volume": 100, "amount": 1000, "source": source,
    }


def days(count: int, start: str = "2026-01-01") -> list[str]:
    first = datetime.fromisoformat(start)
    return [(first + timedelta(days=index)).date().isoformat() for index in range(count)]


def candidate(source: str, rows: list[dict], expected: list[str]) -> Candidate:
    normalized = normalize_candidate_rows(rows, "d", source)
    return Candidate(source, normalized, candidate_summary(
        source, normalized, "d", expected, expected[0], expected[-1], datetime(2026, 2, 1, tzinfo=TZ),
    ))


def test_longest_valid_candidate_wins_even_when_shorter_source_is_non_empty():
    expected = days(10)
    short = candidate("baostock", [bar(day) for day in expected[-3:]], expected)
    long = candidate("tencent", [bar(day, source="tencent") for day in expected], expected)

    selected = select_and_fill([short, long], "d", expected_dates=expected,
                               target_start=expected[0], target_end=expected[-1])

    assert selected["primary_source"] == "tencent"
    assert len(selected["rows"]) == 10
    assert selected["coverage"]["complete"] is True


def test_invalid_ohlc_candidate_row_is_quarantined_before_selection():
    valid = bar("2026-01-05")
    invalid = {**bar("2026-01-06", source="tencent"), "low": 99}

    rows = normalize_candidate_rows([valid, invalid], "d", "tencent")

    assert [row["trade_date"] for row in rows] == ["2026-01-05"]


def test_secondary_source_fills_tail_only_after_five_matching_overlap_rows():
    expected = days(15)
    primary = candidate("baostock", [bar(day) for day in expected[:10]], expected)
    secondary = candidate("tencent", [bar(day, source="tencent") for day in expected[5:]], expected)

    selected = select_and_fill([primary, secondary], "d", expected_dates=expected,
                               target_start=expected[0], target_end=expected[-1])

    assert len(selected["rows"]) == 15
    assert selected["selected_sources"] == ["baostock", "tencent"]
    assert selected["source_conflicts"] == []


def test_price_conflict_refuses_cross_source_splice():
    expected = days(15)
    primary = candidate("baostock", [bar(day) for day in expected[:10]], expected)
    secondary_rows = [bar(day, source="tencent") for day in expected[5:]]
    secondary_rows[0] = bar(expected[5], 20, "tencent")
    secondary = candidate("tencent", secondary_rows, expected)

    selected = select_and_fill([primary, secondary], "d", expected_dates=expected,
                               target_start=expected[0], target_end=expected[-1])

    assert len(selected["rows"]) == 10
    assert {item["reason"] for item in selected["source_conflicts"]} == {
        "overlap_ohlc_mismatch", "ohlc_mismatch",
    }


def test_baostock_history_is_requested_in_calendar_year_chunks(monkeypatch):
    calls = []

    def fetch(symbol, timeframe, start, end, adjustflag):
        calls.append((start, end))
        return [bar(start)]

    monkeypatch.setattr(providers, "fetch_baostock", fetch)
    rows = providers.fetch_baostock_chunked("000001", "d", "2024-06-01", "2026-09-24")

    assert calls == [
        ("2024-06-01", "2024-12-31"),
        ("2025-01-01", "2025-12-31"),
        ("2026-01-01", "2026-09-24"),
    ]
    assert len(rows) == 3


def test_higher_period_history_excludes_forming_key_and_keeps_listing_partial_period():
    rows = [bar("2026-01-30"), bar("2026-02-02", 11), bar("2026-02-03", 12)]

    monthly = aggregate_daily_history(rows, "m", exclude_key="2026-02")

    assert [row["period_key"] for row in monthly] == ["2026-01"]
    assert monthly[0]["trade_date"] == "2026-01-30"


def test_new_stock_full_sync_selects_longer_source_and_persists_audit(tmp_path, monkeypatch):
    expected = days(3, "2026-01-05")
    forming_day = days(4, "2026-01-05")[-1]
    store = Store(str(tmp_path / "multi-source.db"))
    store.upsert_stock("588000", "科创50ETF")
    store.upsert_trade_calendar([{"trade_date": day, "is_trading_day": True} for day in expected])
    store.upsert_bars("588000", "d", "2", [bar(forming_day, source="cache")])
    monkeypatch.setattr(sync, "fetch_security_profile", lambda symbol: {"ipo_date": expected[0], "security_type": "5"})
    monkeypatch.setattr(sync, "fetch_baostock", lambda *args: [bar(expected[-1])])
    monkeypatch.setattr(sync, "fetch_tencent", lambda *args: [bar(day, source="tencent") for day in [*expected, forming_day]])
    monkeypatch.setattr(sync.SyncService, "ensure_trade_calendar_start", lambda self, start: asyncio.sleep(0))
    monkeypatch.setattr(sync.SyncService, "_last_completed_date", lambda self: expected[-1])

    result = asyncio.run(sync.SyncService(store).sync_one("588000", "d", "full"))

    assert result["result"] == "success"
    assert result["selected_sources"] == ["tencent"]
    assert [row["trade_date"] for row in store.market_bars("588000", "d", "2", "0000-01-01")] == expected
    audit = store.latest_market_sync("588000", "d")
    assert audit["status"] == "success"
    assert audit["selected_sources"] == ["tencent"]


def test_full_sync_preserves_cache_when_longer_source_conflicts(tmp_path, monkeypatch):
    expected = days(10, "2026-01-05")
    cached = [bar(day, 10, "cache") for day in expected[:6]]
    remote = [bar(day, 20 if day == expected[0] else 10, "tencent") for day in expected]
    store = Store(str(tmp_path / "cache-conflict.db"))
    store.upsert_trade_calendar([{"trade_date": day, "is_trading_day": True} for day in expected])
    store.upsert_bars_with_changes("588000", "d", "2", cached, source="cache")
    monkeypatch.setattr(sync, "fetch_security_profile", lambda symbol: {"ipo_date": expected[0]})
    monkeypatch.setattr(sync, "fetch_baostock", lambda *args: [])
    monkeypatch.setattr(sync, "fetch_tencent", lambda *args: remote)
    monkeypatch.setattr(sync.SyncService, "ensure_trade_calendar_start", lambda self, start: asyncio.sleep(0))
    monkeypatch.setattr(sync.SyncService, "_last_completed_date", lambda self: expected[-1])

    result = asyncio.run(sync.SyncService(store).sync_one("588000", "d", "full"))

    assert result["result"] == "partial"
    assert result["selected_sources"] == ["cache"]
    assert len(store.market_bars("588000", "d", "2", "0000-01-01")) == len(cached)
    assert store.market_bars("588000", "d", "2", "0000-01-01")[0]["open"] == 10
    assert any(item["source"] == "cache" for item in result["source_conflicts"])
