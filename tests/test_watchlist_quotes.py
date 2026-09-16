from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main, providers
from app.intraday import TZ
from app.store import Store
from app.watchlist_quotes import WatchlistQuoteService


def raw_quote(code="000001", latest="11.00", stamp="20260916100000"):
    fields = [""] * 88
    for index, value in {1:"测试证券", 2:code, 3:latest, 4:"10.00", 30:stamp, 31:"1.00", 32:"10.00"}.items():
        fields[index] = value
    return "~".join(fields)


def parsed_quote(symbol="000001", stamp="20260916100000", latest=11):
    return {"symbol":symbol, "latest":latest, "previous_close":10, "change":latest - 10,
            "change_pct":(latest - 10) * 10, "quote_time":stamp, "source":"tencent", "status":"success", "error":None}


def test_provider_batch_mapping_and_partial_failures(monkeypatch):
    def get(url, **kwargs):
        assert url.endswith("sh000001,sz000001,sh600000,bj830799")
        body = ';'.join(f'v_{code}="{raw_quote(code[2:])}"' for code in ("sh000001", "sz000001", "sh600000"))
        return httpx.Response(200, content=body.encode("gb18030"), request=httpx.Request("GET", url))
    monkeypatch.setattr(providers.httpx, "get", get)
    quotes = providers.fetch_tencent_quotes(["1A0001", "000001", "600000", "830799", "000001"])
    assert [quote["symbol"] for quote in quotes] == ["1A0001", "000001", "600000", "830799"]
    assert [quote["latest"] for quote in quotes] == [11, 11, 11, None]
    assert quotes[-1]["status"] == "unavailable"
    assert providers.fetch_tencent_quotes([]) == []


@pytest.mark.parametrize("price", ["", "0", "-1", "nan", "inf", "not-a-price"])
def test_provider_rejects_invalid_or_suspended_price(monkeypatch, price):
    body = f'v_sz000001="{raw_quote(latest=price)}"'
    monkeypatch.setattr(providers.httpx, "get", lambda url, **kwargs: httpx.Response(
        200, content=body.encode(), request=httpx.Request("GET", url)))
    quote = providers.fetch_tencent_quotes(["000001"])[0]
    assert quote["status"] == "unavailable"
    assert quote["latest"] is None


@pytest.fixture
def quote_service(tmp_path):
    store = Store(str(tmp_path / "quotes.db"))
    store.upsert_stock("000001", "测试")
    clock = [datetime(2026, 9, 16, 10, 0, tzinfo=TZ)]
    tick = [0]
    calendar = {"2026-09-15":True, "2026-09-16":True, "2026-09-17":False,
                "2026-09-18":True, "2026-09-19":False}
    class Calendar:
        def calendar_days(self, start, end):
            return calendar
    calls = []
    def fetcher(symbols):
        calls.append(symbols)
        return [parsed_quote(symbol) for symbol in symbols]
    service = WatchlistQuoteService(store, Calendar(), clock=lambda:clock[0], fetcher=fetcher, monotonic=lambda:tick[0])
    yield service, clock, tick, calls, calendar
    store.db.close()


def test_cache_ttl_and_single_flight_do_not_write_database(quote_service):
    service, clock, tick, calls, calendar = quote_service
    before = service.store.db.total_changes
    service.store.list_stock_pool = lambda: pytest.fail("报价接口不得构建日线摘要")
    first = service.snapshot()
    assert first["refresh_after_ms"] == 10_000
    assert first["quotes"][0]["quote_time"] == "2026-09-16T10:00:00+08:00"
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _:service.snapshot(), range(4)))
    assert len(calls) == 1
    assert all(result == first for result in results)
    tick[0] = 10
    service.snapshot()
    assert len(calls) == 2
    assert service.store.db.total_changes == before


def test_failure_and_older_responses_preserve_last_good_quote(quote_service):
    service, clock, tick, calls, calendar = quote_service
    service.snapshot()
    tick[0] = 11
    service.fetcher = lambda symbols: [parsed_quote(stamp="20260915095900", latest=5)]
    quote = service.snapshot()["quotes"][0]
    assert quote["latest"] == 11 and quote["status"] == "error"
    tick[0] = 22
    service.fetcher = lambda symbols: (_ for _ in ()).throw(TimeoutError())
    quote = service.snapshot()["quotes"][0]
    assert quote["latest"] == 11 and quote["status"] == "error"
    tick[0] = 33
    service.fetcher = lambda symbols: [parsed_quote(latest=12)]
    assert service.snapshot()["quotes"][0]["status"] == "success"


@pytest.mark.parametrize("stamp", ["", "bad", "20260230093000", "20260917100000"])
def test_bad_or_future_time_does_not_enter_cache(quote_service, stamp):
    service, *_ = quote_service
    service.fetcher = lambda symbols: [parsed_quote(stamp=stamp)]
    quote = service.snapshot()["quotes"][0]
    assert quote["status"] == "unavailable" and quote["latest"] is None


def test_historical_stale_and_session_transitions(quote_service):
    service, clock, tick, calls, calendar = quote_service
    service.fetcher = lambda symbols: [parsed_quote(stamp="20260915150000")]
    assert service.snapshot()["quotes"][0]["status"] == "historical"
    tick[0] += 11
    service.fetcher = lambda symbols: [parsed_quote()]
    clock[0] += timedelta(minutes=3)
    assert service.snapshot()["quotes"][0]["status"] == "stale"
    for day, hour, minute, phase, interval in [(16,9,20,"auction",10_000), (16,12,0,"lunch",60_000),
                                            (16,13,0,"trading",10_000), (16,15,0,"closed",60_000),
                                            (17,10,0,"closed",60_000), (19,10,0,"closed",60_000)]:
        clock[0] = datetime(2026,9,day,hour,minute,tzinfo=TZ)
        result = service.snapshot()
        assert (result["phase"], result["refresh_after_ms"]) == (phase, interval)
    calendar.clear()
    assert service.snapshot()["phase"] == "unknown"
    assert service.snapshot()["refresh_after_ms"] == 60_000


def test_pool_changes_partial_failures_and_empty_pool(quote_service):
    service, clock, tick, calls, calendar = quote_service
    service.snapshot()
    service.store.upsert_stock("600000", "测试2")
    service.fetcher = lambda symbols: []
    result = service.snapshot()
    assert result["quotes"][0]["latest"] == 11
    assert result["quotes"][1]["status"] == "unavailable"
    with service.store.db:
        service.store.db.execute("UPDATE stock_pool SET enabled=0")
    assert service.snapshot()["quotes"] == []
    assert not service._quotes and not service._attempts


def test_quote_endpoint_uses_snapshot_service(quote_service, monkeypatch):
    service, *_ = quote_service
    monkeypatch.setattr(main, "watchlist_quote_service", service)
    response = TestClient(main.app).get("/api/watchlist/quotes")
    assert response.status_code == 200
    assert response.json()["quotes"][0]["latest"] == 11


def test_mixed_section_order_migration_legacy_and_preservation(tmp_path, monkeypatch):
    path = str(tmp_path / "order.db")
    store = Store(path)
    group_a = store.create_watchlist_group("A")["id"]
    group_b = store.create_watchlist_group("B")["id"]
    store.upsert_stock("000001", "测试", group_a)
    monkeypatch.setattr(main, "store", store)
    client = TestClient(main.app)
    before = store.watchlist()
    assert before["section_order"] == ["all", "ungrouped", f"group-{group_a}", f"group-{group_b}"]
    ordered = [f"group-{group_b}", "ungrouped", f"group-{group_a}", "all"]
    assert client.put("/api/watchlist/order", json={"section_keys":ordered}).json()["section_order"] == ordered
    assert [group["id"] for group in store.list_watchlist_groups()] == [group_b, group_a]
    reopened = Store(path)
    assert reopened.list_watchlist_section_order() == ordered
    reopened.db.close()
    for invalid in (ordered[:-1], ordered + ["all"], ordered[:-1] + ["unknown"]):
        assert client.put("/api/watchlist/order", json={"section_keys":invalid}).status_code == 400
        assert store.list_watchlist_section_order() == ordered
    for key in ("all", "ungrouped"):
        assert client.delete(f"/api/watchlist-groups/{key}").status_code == 422
        assert client.patch(f"/api/watchlist-groups/{key}", json={"name":"changed"}).status_code == 422
    store.reorder_watchlist_groups([group_a, group_b])
    assert store.list_watchlist_section_order() == [f"group-{group_a}", "ungrouped", f"group-{group_b}", "all"]
    assert store.watchlist()["stocks"] == before["stocks"]
    assert store.watchlist()["memberships"] == before["memberships"]
    group_c = store.create_watchlist_group("C")["id"]
    assert store.list_watchlist_section_order()[-1] == f"group-{group_c}"
    store.delete_watchlist_group(group_a)
    assert f"group-{group_a}" not in store.list_watchlist_section_order()
    assert len(store.list_stock_pool()) == 1
    assert not store.db.execute("SELECT 1 FROM watchlist_section_order WHERE section_key=?", (f"group-{group_a}",)).fetchall()
    store.db.close()


def test_section_order_transaction_rolls_back(tmp_path):
    store = Store(str(tmp_path / "rollback.db"))
    group = store.create_watchlist_group("A")
    original = store.list_watchlist_section_order()
    store.reorder_watchlist_sections(original)
    store.db.execute("""CREATE TRIGGER reject_section BEFORE INSERT ON watchlist_section_order
                       WHEN NEW.section_key='all' BEGIN SELECT RAISE(ABORT, 'test'); END""")
    store.db.commit()
    with pytest.raises(Exception, match="test"):
        store.reorder_watchlist_sections([f"group-{group['id']}", "ungrouped", "all"])
    assert store.list_watchlist_section_order() == original
    assert store.list_watchlist_groups()[0]["sort_order"] == 0
    store.db.close()


def test_only_system_sections_can_be_reordered(tmp_path):
    store = Store(str(tmp_path / "system-sections.db"))
    assert store.reorder_watchlist_sections(["ungrouped", "all"]) == ["ungrouped", "all"]
    group = store.create_watchlist_group("新分组")
    assert store.list_watchlist_section_order() == ["ungrouped", "all", f"group-{group['id']}"]
    store.delete_watchlist_group(group["id"])
    assert store.list_watchlist_section_order() == ["ungrouped", "all"]
    store.db.close()
