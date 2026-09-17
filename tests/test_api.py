from datetime import date, timedelta
from io import BytesIO
import pytest

from fastapi.testclient import TestClient

from app import main as main_module
from app.coverage import EXPECTED_5M_TIMES
from app.store import Store
from app.period_structure import PeriodStructureService
from app.rules import PERIOD_DEFINITION_VERSION
from tests.test_structure import seed
from app.securities import SecurityCatalogService


def rows(days=12):
    result=[]; start=date(2026,1,5); index=0
    for offset in range(days + 5):
        day=start+timedelta(days=offset)
        if day.weekday() >= 5: continue
        for clock in EXPECTED_5M_TIMES:
            v=[1,2,3,4,5,4,3,2][index%8]
            result.append({"trade_date":f"{day} {clock}:00","open":v-.1,"high":v+.4,"low":v-.4,"close":v+.1,"volume":100,"amount":1000})
            index+=1
        if len(result) >= days*48: break
    return result


def test_period_api_exposes_formal_hierarchy_without_segments(tmp_path, monkeypatch):
    store=Store(str(tmp_path/"api.db")); service=PeriodStructureService(store)
    seed(store,"000001",rows()); service.ensure("000001","5",force=True)
    monkeypatch.setattr(main_module,"store",store); monkeypatch.setattr(main_module,"period_structure_service",service)
    client=TestClient(main_module.app)
    health=client.get("/api/health").json()
    assert health["definition_version"]==PERIOD_DEFINITION_VERSION
    assert "segment_algorithm_version" not in health
    assert len(health["calculator_fingerprint"]) == 64
    assert health["max_center_level"] >= 0
    assert health["structure_mode"] == "formal_hierarchy"
    assert health["movement_confirmation_mode"] == "earliest_valid_structural_evidence"
    assert health["center_construction_mode"] == "center_free_component_directional"
    assert health["buy_sell_point_mode"] == "macd_divergence_and_structural_retest"
    chart=client.get("/api/chart-data/000001",params={"timeframe":"5", "structure_level": 1})
    payload = chart.json()
    assert chart.status_code==200 and all(key in payload for key in (
        "pens", "centers", "pen_centers", "center_relations", "movements",
        "center_levels", "movement_levels", "components", "buy_sell_points",
        "calculator_fingerprint",
    ))
    assert payload["calculator_fingerprint"] == service.calculator_fingerprint
    assert payload["active_structure_level"] == 1
    assert all(key not in payload for key in ("segments", "segment_count", "segment_algorithm_version"))
    assert all(key not in payload for key in ("edges","levels","structure_level","effective_center_ids"))
    assert payload["centers"] == payload["pen_centers"]
    assert {center["level"] for center in payload["centers"]} <= set(payload["center_levels"])
    assert all(center["display_period"] == "5" and center["color_key"].startswith("period-5")
               for center in payload["centers"])
    assert all(movement["level"] in payload["movement_levels"] and movement["role"] == "hierarchy_component"
               for movement in payload["movements"])
    level_two = client.get("/api/chart-data/000001", params={"timeframe": "5", "structure_level": 2}).json()
    assert [item["id"] for item in level_two["centers"]] == [item["id"] for item in payload["centers"]]
    assert [item["id"] for item in level_two["movements"]] == [item["id"] for item in payload["movements"]]
    levels=client.get("/api/recursive-structure/000001/levels")
    assert levels.status_code==404
    coverage = client.get("/api/market-coverage/000001", params={"timeframe":"5"})
    assert coverage.status_code == 200
    assert client.get("/api/chart-data/000001", params={"timeframe":"5"}).json()["available"] is True
    analyze_schema = client.get("/openapi.json").json()["components"]["schemas"]["AnalyzeRequest"]
    assert "movement_level" not in analyze_schema["properties"]


def test_v2_chart_api_is_removed(tmp_path, monkeypatch):
    store = Store(str(tmp_path / "v2.db"))
    service = PeriodStructureService(store)
    seed(store, "000001", rows())
    service.ensure("000001", "5", force=True)
    monkeypatch.setattr(main_module, "store", store)
    monkeypatch.setattr(main_module, "period_structure_service", service)
    client = TestClient(main_module.app)

    assert client.get("/api/v2/chart-data/000001", params={"timeframe": "5"}).status_code == 404
    payload = client.get("/api/chart-data/000001", params={"timeframe": "5"}).json()
    assert "decomposition" not in payload
    assert all(item.get("role") == "hierarchy" for item in payload["centers"])
    assert all(item.get("role") == "hierarchy_component" for item in payload["movements"])


def test_structure_override_api_blocks_all_mutations_without_writing(tmp_path, monkeypatch):
    store = Store(str(tmp_path / "overrides-disabled.db"))
    service = PeriodStructureService(store)
    seed(store, "000001", rows())
    service.ensure("000001", "5", force=True)
    run = store.active_period_structure_run("000001", "5", "2")
    monkeypatch.setattr(main_module, "store", store)
    monkeypatch.setattr(main_module, "period_structure_service", service)
    client = TestClient(main_module.app)
    base = {"timeframe": "5", "adjustflag": "2", "base_run_id": run["id"],
            "base_structure_version": run["structure_version"]}
    requests = [
        ("post", "/api/structure-overrides/000001", {**base, "structure_type": "pen", "operation": "create", "payload": {}}),
        ("post", "/api/structure-overrides/000001/batch", {**base, "operations": []}),
        ("patch", "/api/structure-overrides/000001/999", {"payload": {}, "base_run_id": run["id"], "base_structure_version": run["structure_version"]}),
        ("delete", "/api/structure-overrides/000001/999", None),
        ("post", "/api/structure-overrides/000001/999/restore", None),
        ("post", "/api/structure-overrides/000001/restore-all", None),
    ]
    before = store.structure_overrides("000001", "5", "2")
    for method, path, payload in requests:
        response = client.request(method, path, **({"json": payload} if payload is not None else {}))
        assert response.status_code == 409
        assert "暂时关闭" in response.json()["detail"]
    assert store.structure_overrides("000001", "5", "2") == before


def test_chart_data_includes_quote_and_full_history_moving_averages(tmp_path):
    store = Store(str(tmp_path / "quote.db"))
    daily = [{"trade_date": f"2026-01-{index:02d}", "open": index - .2,
              "high": index + .5, "low": index - .5, "close": float(index),
              "volume": index * 100, "amount": index * 1000}
             for index in range(1, 26)]
    store.upsert_bars("000001", "d", "2", daily)
    chart = PeriodStructureService(store).chart_page("000001", "d", "2", None, 5)
    assert chart["quote"]["latest"] == 25
    assert chart["quote"]["previous_close"] == 24
    assert chart["quote"]["change_pct"] == pytest.approx(100 / 24)
    assert chart["quote"]["amplitude_pct"] == pytest.approx(100 / 24)
    assert chart["quote"]["market_status"] in {"交易中", "午间休市", "已休市", "数据延迟"}
    assert chart["indicators"]["ma"][0]["ma20"] == 11.5


def test_security_search_and_confirmed_watchlist_add(tmp_path, monkeypatch):
    store = Store(str(tmp_path / "watchlist.db"))
    catalog = {"catalog_date": "2026-09-09", "items": [
        {"market_code": "sz.000001", "symbol": "000001", "name": "平安银行", "market": "SZ", "trade_status": "1"},
    ]}
    service = SecurityCatalogService(store, lambda: catalog, lambda symbol: None)

    class SyncStub:
        async def add_stock(self, symbol, name, sync=True):
            return store.upsert_stock(symbol, name)

    monkeypatch.setattr(main_module, "store", store)
    monkeypatch.setattr(main_module, "security_catalog_service", service)
    monkeypatch.setattr(main_module, "sync_service", SyncStub())
    client = TestClient(main_module.app)
    search = client.get("/api/securities/search", params={"q": "平安"})
    assert search.status_code == 200
    assert search.json()["items"][0]["selected"] is False
    added = client.post("/api/stock-pool", json={"symbol": "000001"})
    assert added.status_code == 200
    assert added.json()["name"] == "平安银行"
    assert added.json()["sync_status"] == "pending"
    duplicate = client.post("/api/stock-pool", json={"symbol": "000001"})
    assert duplicate.json()["already_selected"] is True
    assert len(store.list_stock_pool()) == 1


def test_watchlist_group_order_persists_without_changing_memberships(tmp_path, monkeypatch):
    database = str(tmp_path / "watchlist-order.db")
    store = Store(database)
    monkeypatch.setattr(main_module, "store", store)
    client = TestClient(main_module.app)
    group_ids = [client.post("/api/watchlist-groups", json={"name": name}).json()["id"]
                 for name in ("分组A", "分组B", "分组C")]
    store.upsert_stock("000001", "平安银行", group_ids[0])
    store.upsert_stock("000002", "万科A", group_ids[1])
    previous = client.get("/api/watchlist").json()
    expected = [group_ids[2], group_ids[0], group_ids[1]]
    response = client.put("/api/watchlist-groups/order", json={"group_ids": expected})
    assert response.status_code == 200
    assert [group["id"] for group in response.json()] == expected
    assert [group["sort_order"] for group in response.json()] == [0, 1, 2]
    refreshed = client.get("/api/watchlist").json()
    assert [group["id"] for group in refreshed["groups"]] == expected
    assert refreshed["stocks"] == previous["stocks"]
    assert sorted(refreshed["memberships"], key=lambda item: (item["group_id"], item["symbol"])) == sorted(
        previous["memberships"], key=lambda item: (item["group_id"], item["symbol"]))
    for invalid in ([group_ids[0]], [group_ids[0]] * 3, [group_ids[0], group_ids[1], 999999]):
        assert client.put("/api/watchlist-groups/order", json={"group_ids": invalid}).status_code == 400
        assert [group["id"] for group in client.get("/api/watchlist").json()["groups"]] == expected
    reopened = Store(database)
    try:
        assert [group["id"] for group in reopened.list_watchlist_groups()] == expected
    finally:
        reopened.db.close()
        store.db.close()


def test_watchlist_group_api_and_grouped_stock_add(tmp_path, monkeypatch):
    store = Store(str(tmp_path / "watchlist-api.db"))
    catalog = {"catalog_date": "2026-09-12", "items": [
        {"market_code": "sz.300308", "symbol": "300308", "name": "中际旭创", "market": "SZ", "trade_status": "1"},
    ]}
    service = SecurityCatalogService(store, lambda: catalog, lambda symbol: None)

    class SyncStub:
        async def add_stock(self, symbol, name, sync=True, group_id=None):
            return store.upsert_stock(symbol, name, group_id)

    monkeypatch.setattr(main_module, "store", store)
    monkeypatch.setattr(main_module, "security_catalog_service", service)
    monkeypatch.setattr(main_module, "sync_service", SyncStub())
    client = TestClient(main_module.app)

    core = client.post("/api/watchlist-groups", json={"name": "核心持仓"})
    ai = client.post("/api/watchlist-groups", json={"name": "AI / 算力"})
    assert core.status_code == 200 and ai.status_code == 200
    core_id, ai_id = core.json()["id"], ai.json()["id"]
    assert client.post("/api/watchlist-groups", json={"name": "核心持仓 "}).status_code == 400

    added = client.post("/api/stock-pool", json={"symbol": "300308", "group_id": core_id})
    assert added.status_code == 200 and added.json()["already_selected"] is False
    duplicate = client.post("/api/stock-pool", json={"symbol": "300308", "group_id": ai_id})
    assert duplicate.status_code == 200 and duplicate.json()["already_selected"] is True
    payload = client.get("/api/watchlist").json()
    assert payload["stocks"][0]["symbol"] == "300308"
    assert {(item["group_id"], item["symbol"]) for item in payload["memberships"]} == {
        (core_id, "300308"), (ai_id, "300308")
    }

    assert client.put(f"/api/watchlist-groups/{core_id}/members/order", json={"symbols": []}).status_code == 400
    assert client.delete(f"/api/watchlist-groups/{core_id}/members/300308").status_code == 200
    assert client.delete(f"/api/watchlist-groups/{ai_id}").status_code == 200
    assert client.get("/api/stock-pool").json()[0]["symbol"] == "300308"
    assert client.post("/api/stock-pool", json={"symbol": "300308", "group_id": 999}).status_code == 404


def test_csv_import_does_not_activate_an_incomplete_period(tmp_path, monkeypatch):
    store=Store(str(tmp_path/"import.db")); service=PeriodStructureService(store)
    monkeypatch.setattr(main_module,"store",store); monkeypatch.setattr(main_module,"period_structure_service",service)
    client=TestClient(main_module.app)
    data=rows(1)[:-1]
    body="symbol,trade_date,open,high,low,close,volume\n"+"\n".join(
        f"000001,{x['trade_date']},{x['open']},{x['high']},{x['low']},{x['close']},{x['volume']}" for x in data)
    response=client.post("/api/market-data/import?symbol=000001",files={"file":("bars.csv",BytesIO(body.encode()),"text/csv")})
    assert response.status_code==200
    assert response.json()["structure_available"] is False
    assert response.json()["coverage"]["complete"] is False


def test_drawing_style_validation_and_atomic_batch(tmp_path, monkeypatch):
    store = Store(str(tmp_path / "drawings.db"))
    monkeypatch.setattr(main_module, "store", store)
    client = TestClient(main_module.app)
    base = {"timeframe": "d", "object_type": "rectangle",
            "start_anchor": {"trade_date": "2026-01-05", "price": 10},
            "end_anchor": {"trade_date": "2026-01-06", "price": 11},
            "style": {"color": "#123456", "width": 3, "line_type": "dashed",
                      "fill_color": "#abcdef", "fill_opacity": 0.2}}
    created = client.post("/api/drawings/000001", json=base)
    assert created.status_code == 200
    assert created.json()["style"]["line_type"] == "dashed"
    invalid = {**base, "style": {"color": "red", "width": 3}}
    assert client.post("/api/drawings/000001", json=invalid).status_code == 400
    version = store.drawings_version("000001", "d")
    batch = {"timeframe": "d", "base_version": version, "create": [
        {**base, "start_anchor": {"trade_date": "2026-01-07", "price": 9},
         "end_anchor": {"trade_date": "2026-01-08", "price": 12}}],
        "update": [{"id": 99999, "drawing": base}], "delete_ids": []}
    assert client.post("/api/drawings/000001/batch", json=batch).status_code == 404
    assert len(store.drawings("000001", "d")) == 1
