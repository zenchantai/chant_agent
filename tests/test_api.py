from fastapi.testclient import TestClient

from app import main as main_module
from app.period_structure import PeriodStructureService
from app.rules import PERIOD_DEFINITION_VERSION
from app.store import Store
from tests.chan_fixtures import seed, session_rows


def _client(tmp_path, monkeypatch):
    store = Store(str(tmp_path / "api.db"))
    seed(store, "000001", session_rows(15))
    service = PeriodStructureService(store, calculator_fingerprint="api-fingerprint")
    service.ensure("000001", "5", force=True)
    monkeypatch.setattr(main_module, "store", store)
    monkeypatch.setattr(main_module, "period_structure_service", service)
    return TestClient(main_module.app), store


def test_health_exposes_period_profiles_and_active_level(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    health = client.get("/api/health")
    assert health.status_code == 200
    payload = health.json()
    assert payload["definition_version"] == PERIOD_DEFINITION_VERSION
    assert payload["structure_mode"] == "period_profiled"
    assert payload["calculation_profiles"] == {"5": "full", "30": "full", "d": "full", "w": "pen_centers_only", "m": "pen_centers_only"}
    assert payload["decomposition_mode"] == "non_same_level"
    assert payload["center_promotion_mode"] == "verified_expansion_or_recursive_core"
    assert payload["center_envelope_mode"] == "owned_z_units"
    assert payload["envelope_touch_mode"] == "candidate"
    assert payload["movement_boundary_mode"] == "structural_buy_sell_point"
    assert payload["divergence_mode"] == "structural_strength_vector"
    assert payload["max_computed_level"] >= 1


def test_chart_api_only_returns_new_nested_contract(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    response = client.get("/api/chart-data/000001", params={"timeframe": "5", "structure_level": 1})
    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"meta", "market", "structure", "indicators", "drawings", "pagination"}
    assert payload["meta"]["active_level"] == 1
    assert payload["meta"]["definition_version"] == PERIOD_DEFINITION_VERSION
    assert all(item["level"] == 1 for item in payload["structure"]["centers"])
    assert all(item["level"] == 1 for item in payload["structure"]["movements"])
    serialized = response.text
    assert "pen_centers" not in serialized
    assert "buy_sell_points" not in serialized
    assert "hierarchy_component" not in serialized
    assert "confirmation_center_id" not in serialized


def test_diagnostics_controls_revision_and_issue_payloads(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    normal = client.get("/api/chart-data/000001", params={"timeframe": "5"}).json()
    diagnostic = client.get(
        "/api/chart-data/000001", params={"timeframe": "5", "diagnostics": "true"},
    ).json()
    assert {item["id"] for item in normal["structure"]["center_revisions"]} <= {item["id"] for item in diagnostic["structure"]["center_revisions"]}
    assert normal["structure"]["issues"] == []
    assert diagnostic["structure"]["center_revisions"]
    assert diagnostic["meta"]["diagnostics"] is True


def test_old_structure_routes_are_removed(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    assert client.get("/api/v2/chart-data/000001").status_code == 404
    assert client.post("/api/structure-overrides/000001", json={}).status_code == 404
    assert client.get("/api/recursive-structure/000001/levels").status_code == 404


def test_history_pagination_returns_older_market_window(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    first = client.get("/api/chart-data/000001", params={"timeframe": "5", "limit": 300}).json()
    assert first["pagination"]["has_more"] is True
    second = client.get(
        "/api/chart-data/000001",
        params={"timeframe": "5", "limit": 300, "before": first["pagination"]["next_before"]},
    ).json()
    assert second["market"]["bars"][-1]["trade_date"] < first["market"]["bars"][0]["trade_date"]


def test_drawing_batch_remains_available(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    request = {
        "timeframe": "d",
        "base_version": store.drawings_version("000001", "d"),
        "create": [{
            "timeframe": "d",
            "object_type": "rectangle",
            "start_anchor": {"trade_date": "2026-01-05", "price": 10},
            "end_anchor": {"trade_date": "2026-01-06", "price": 11},
            "style": {"color": "#123456", "width": 2},
        }],
        "update": [],
        "delete_ids": [],
    }
    response = client.post("/api/drawings/000001/batch", json=request)
    assert response.status_code == 200
    assert len(response.json()["items"]) == 1
