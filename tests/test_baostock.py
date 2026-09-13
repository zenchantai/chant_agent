import sys
import types
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.providers import catalog_symbol, fetch_baostock, fetch_security_profile, normalize_baostock_symbol
from app.store import Store
from app.sync import SyncService
from app.securities import SecurityCatalogService


def test_baostock_symbol_mapping():
    assert normalize_baostock_symbol("000001") == "sz.000001"
    assert normalize_baostock_symbol("600000") == "sh.600000"
    assert normalize_baostock_symbol("588000") == "sh.588000"
    assert normalize_baostock_symbol("159915") == "sz.159915"
    assert normalize_baostock_symbol("1A0001") == "sh.000001"
    assert normalize_baostock_symbol("sh.000001") == "sh.000001"
    assert normalize_baostock_symbol("bj.830799") == "bj.830799"
    assert catalog_symbol("sh.000001") == "1A0001"
    assert catalog_symbol("sh.000688") == "1A0688"
    assert catalog_symbol("sh.588000") == "588000"


def test_supplemental_science_technology_index_profile():
    profile = fetch_security_profile("1A0688")
    assert profile == {
        "market_code": "sh.000688", "symbol": "1A0688", "name": "科创50指数",
        "market": "SH", "trade_status": "1", "provider": "tencent",
    }


def test_baostock_minute_mapping_and_adjustflag(monkeypatch):
    calls = {}

    class Result:
        error_code = "0"
        error_msg = "success"
        fields = ["date", "time", "code", "open", "high", "low", "close", "volume", "amount", "adjustflag"]
        rows = [["2026-09-01", "20260901093500000", "sz.000001", "1", "2", "0.5", "1.5", "100", "150", "2"]]
        index = 0

        def next(self):
            if self.index < len(self.rows):
                self.index += 1
                return True
            return False

        def get_row_data(self):
            return self.rows[self.index - 1]

    fake = types.SimpleNamespace(
        login=lambda: types.SimpleNamespace(error_code="0", error_msg="success"),
        logout=lambda: types.SimpleNamespace(error_code="0"),
        query_history_k_data_plus=lambda *args, **kwargs: (calls.update(kwargs) or Result()),
    )
    monkeypatch.setitem(sys.modules, "baostock", fake)
    rows = fetch_baostock("000001", "5", "2026-09-01", "2026-09-01", "2")
    assert calls["adjustflag"] == "2"
    assert calls["frequency"] == "5"
    assert rows[0]["trade_date"] == "2026-09-01 09:35:00"
    assert rows[0]["close"] == 1.5


def test_supplemental_index_unsupported_frequency_falls_back_to_tencent(monkeypatch):
    class Result:
        error_code = "10004012"
        error_msg = "请求数据类型不正确"

    fake = types.SimpleNamespace(
        login=lambda: types.SimpleNamespace(error_code="0", error_msg="success"),
        logout=lambda: types.SimpleNamespace(error_code="0"),
        query_history_k_data_plus=lambda *args, **kwargs: Result(),
    )
    monkeypatch.setitem(sys.modules, "baostock", fake)
    monkeypatch.setattr(
        "app.providers.fetch_tencent",
        lambda symbol, timeframe, start_date, end_date, adjustflag: [{"trade_date": "2026-09-12", "close": 1}],
    )
    rows = fetch_baostock("1A0001", "120", "2026-09-01", "2026-09-12", "2")
    assert rows[0]["trade_date"] == "2026-09-12"


def test_market_cache_upsert_and_limit(tmp_path):
    store = Store(str(tmp_path / "bars.db"))
    rows = [{"trade_date": f"2026-09-0{i}", "open": i, "high": i + 1, "low": i - 1, "close": i, "volume": 10} for i in range(1, 4)]
    assert store.upsert_bars("000001", "d", "2", rows) == 3
    assert store.upsert_bars("000001", "d", "2", rows) == 3
    assert len(store.market_bars("000001", "d", "2", "2015-01-01", "2026-12-31", limit=2)) == 2
    assert store.market_range("000001", "d", "2") == ("2026-09-01", "2026-09-03")


def test_stock_pool_upsert_and_delete(tmp_path):
    store = Store(str(tmp_path / "pool.db"))
    item = store.upsert_stock("000001", "平安银行")
    assert item["symbol"] == "000001"
    assert item["name"] == "平安银行"
    store.upsert_stock("000001", "平安银行（更新）")
    assert len(store.list_stock_pool()) == 1
    assert store.list_stock_pool()[0]["name"] == "平安银行（更新）"
    assert store.delete_stock("000001") is True
    assert store.delete_stock("000001") is False


def test_stock_pool_sort_and_sync_summary(tmp_path):
    store = Store(str(tmp_path / "pool.db"))
    store.upsert_stock("000001", "平安银行")
    store.upsert_stock("600000", "浦发银行")
    store.update_stock("600000", move="up")
    assert [item["symbol"] for item in store.list_stock_pool()] == ["600000", "000001"]
    first = store.create_sync_run("600000", "d", "incremental", "2026-09-01T20:30:00+08:00")
    store.finish_sync_run(first, "success", 3, "2026-08-28", "2026-09-01")
    second = store.create_sync_run("600000", "5", "incremental", "2026-09-01T20:30:00+08:00")
    store.finish_sync_run(second, "failed", error="timeout")
    item = store.list_stock_pool()[0]
    assert item["sync_status"] == "partial_failed"
    assert "timeout" in item["sync_error"]
    assert store.has_successful_sync("600000", "d", "2026-09-01T20:30:00+08:00")


def test_watchlist_groups_memberships_and_legacy_migration(tmp_path):
    path = tmp_path / "watchlist-groups.db"
    store = Store(str(path))
    store.upsert_stock("300308", "中际旭创")
    store.upsert_stock("688041", "海光信息")
    assert store.watchlist()["groups"] == []
    assert store.watchlist()["memberships"] == []

    core = store.create_watchlist_group("核心持仓")
    ai = store.create_watchlist_group("AI / 算力")
    assert [group["name"] for group in store.list_watchlist_groups()] == ["核心持仓", "AI / 算力"]
    store.add_watchlist_group_member(core["id"], "300308")
    store.add_watchlist_group_member(ai["id"], "300308")
    store.add_watchlist_group_member(ai["id"], "688041")
    store.add_watchlist_group_member(ai["id"], "300308")
    assert len(store.watchlist_memberships()) == 3
    assert store.list_watchlist_groups()[1]["member_count"] == 2

    store.reorder_watchlist_groups([ai["id"], core["id"]])
    store.reorder_watchlist_group_members(ai["id"], ["688041", "300308"])
    assert [item["symbol"] for item in store.watchlist_memberships() if item["group_id"] == ai["id"]] == ["688041", "300308"]
    with pytest.raises(ValueError, match="成员已变化"):
        store.reorder_watchlist_group_members(ai["id"], ["300308"])

    assert store.remove_watchlist_group_member(core["id"], "300308") is True
    assert store.delete_watchlist_group(ai["id"]) is True
    assert {item["symbol"] for item in store.list_stock_pool()} == {"300308", "688041"}
    assert store.watchlist_memberships() == []

    store = Store(str(path))
    assert store.list_watchlist_groups()[0]["name"] == "核心持仓"
    assert {item["symbol"] for item in store.list_stock_pool()} == {"300308", "688041"}


def test_watchlist_group_validation_and_stock_delete_cleanup(tmp_path):
    store = Store(str(tmp_path / "watchlist-validation.db"))
    store.upsert_stock("300308", "中际旭创")
    group = store.create_watchlist_group("观察")
    with pytest.raises(ValueError, match="不能为空"):
        store.create_watchlist_group("  ")
    with pytest.raises(ValueError, match="30"):
        store.create_watchlist_group("甲" * 31)
    with pytest.raises(ValueError, match="已存在"):
        store.create_watchlist_group("观察 ")
    with pytest.raises(LookupError, match="分组不存在"):
        store.add_watchlist_group_member(999, "300308")
    with pytest.raises(LookupError, match="股票不在"):
        store.add_watchlist_group_member(group["id"], "600000")
    store.add_watchlist_group_member(group["id"], "300308")
    assert store.delete_stock("300308") is True
    assert store.watchlist_memberships() == []


def test_security_catalog_search_and_selected_state(tmp_path):
    store = Store(str(tmp_path / "catalog.db"))
    store.replace_security_catalog([
        {"market_code": "sz.000001", "symbol": "000001", "name": "平安银行", "market": "SZ", "trade_status": "1"},
        {"market_code": "sh.600000", "symbol": "600000", "name": "浦发银行", "market": "SH", "trade_status": "1"},
        {"market_code": "sh.000001", "symbol": "1A0001", "name": "上证综合指数", "market": "SH", "trade_status": "1"},
    ], "2026-09-09")
    assert store.search_security_catalog("平安")[0]["symbol"] == "000001"
    assert store.search_security_catalog("600")[0]["name"] == "浦发银行"
    assert store.search_security_catalog("1A0001")[0]["name"] == "上证综合指数"
    assert any(item["symbol"] == "1A0001" for item in store.search_security_catalog("000001"))
    store.upsert_stock("000001", "平安银行")
    assert store.search_security_catalog("000001")[0]["selected"] is True


def test_security_catalog_exact_code_fallback(tmp_path):
    store = Store(str(tmp_path / "catalog.db"))
    fetched_items = [
        {"market_code": f"sz.{index:06d}", "symbol": f"{index:06d}", "name": f"证券{index}", "market": "SZ", "trade_status": "1"}
        for index in range(3000)
    ]
    fetched = {"catalog_date": "2026-09-09", "items": fetched_items}
    profile = {"market_code": "sh.600000", "symbol": "600000", "name": "浦发银行", "market": "SH", "trade_status": "1"}
    service = SecurityCatalogService(store, lambda: fetched, lambda symbol: profile if symbol == "600000" else None)
    result = service.search("600000")
    assert result["items"][0]["name"] == "浦发银行"


def test_security_catalog_refreshes_partial_catalog_for_growth_market_search(tmp_path):
    store = Store(str(tmp_path / "growth-catalog.db"))
    partial_items = [
        {"market_code": f"sz.{index:06d}", "symbol": f"{index:06d}", "name": f"证券{index}",
         "market": "SZ", "trade_status": "1"}
        for index in range(1, 4006)
    ]
    partial = {
        "catalog_date": "2026-09-11",
        "items": partial_items,
    }
    complete = {
        "catalog_date": "2026-09-11",
        "items": [
            {"market_code": "sz.300308", "symbol": "300308", "name": "中际旭创",
             "market": "SZ", "trade_status": "1"},
        ] + partial["items"] + [
            {"market_code": f"sh.{index:06d}", "symbol": f"{index:06d}", "name": f"证券SH{index}",
             "market": "SH", "trade_status": "1"}
            for index in range(1, 2001)
        ],
    }
    calls = []

    def fetch_catalog():
        calls.append(True)
        return complete if len(calls) > 1 else partial

    service = SecurityCatalogService(store, fetch_catalog, lambda symbol: None)
    service.refresh_if_stale()
    result = service.search("中际旭创")
    assert result["items"][0]["symbol"] == "300308"
    assert len(calls) == 2


def test_scheduler_next_target():
    tz = ZoneInfo("Asia/Shanghai")
    friday = datetime(2026, 9, 4, 21, 0, tzinfo=tz)
    target = SyncService.next_target(friday)
    assert target.weekday() == 5
    assert (target.hour, target.minute) == (18, 0)


def test_full_sync_starts_at_2015_and_writes_log(tmp_path, monkeypatch):
    store = Store(str(tmp_path / "sync.db"))
    store.upsert_stock("000001", "平安银行")
    calls = []

    def fake_fetch(symbol, timeframe, start_date, end_date, adjustflag):
        calls.append((symbol, timeframe, start_date, adjustflag))
        return [{"trade_date": "2026-09-01", "open": 1, "high": 2, "low": .5, "close": 1.5, "volume": 10, "amount": 15}]

    monkeypatch.setattr("app.sync.fetch_baostock", fake_fetch)
    asyncio.run(SyncService(store).sync_one("000001", "d", "full"))
    assert calls == [("000001", "d", "2015-01-01", "2")]
    assert store.list_sync_runs()[0]["status"] == "success"
    assert store.market_range("000001", "d", "2") == ("2026-09-01", "2026-09-01")
    assert store.active_period_structure_run("000001", "d", "2") is not None
