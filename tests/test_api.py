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


class _RealtimeStub:
    def __init__(self, timeframe: str, row: dict):
        self.timeframe = timeframe
        self.row = row

    def refresh_period(self, symbol, timeframe, adjustflag="2", include_quote=False):
        assert timeframe == self.timeframe
        return {"result": "success", "finalized_count": 0, "changed_from": None}

    def live_rows(self, symbol, timeframe, adjustflag="2"):
        return [dict(self.row)]

    def forming_bar(self, symbol, timeframe, adjustflag="2"):
        return {"trade_date": self.row["trade_date"], "is_forming": True,
                "status": "provisional", "finalize_at": "2026-01-23T10:05:15+08:00"}

    def period_metadata(self, symbol, timeframe, adjustflag="2"):
        period = {
            "period_key": self.row["trade_date"], "state": "provisional", "is_forming": True,
            "server_time": "2026-01-23T10:04:00+08:00", "latest_data_at": self.row["trade_date"],
            "last_success_at": "2026-01-23T10:04:00+08:00", "is_today": True, "error": None,
        }
        return {
            "server_time": period["server_time"], "phase": "trading", "market_status": "交易中",
            "next_transition_at": "2026-01-23T11:30:00+08:00", "data_date": "2026-01-23",
            "latest_data_at": self.row["trade_date"], "last_success_at": period["last_success_at"],
            "result": "success", "error": None, "calendar_error": None, "is_today": True,
            "period_refresh": period,
        }

    def latest_quote(self, symbol, adjustflag="2"):
        return {
            "latest": self.row["close"], "previous_close": self.row["open"], "change": 0.1,
            "change_pct": 1.0, "quote_time": "20260123100400", "source": "tencent",
            "status": "success",
        }


def test_health_exposes_period_profiles_and_active_level(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    health = client.get("/api/health")
    assert health.status_code == 200
    payload = health.json()
    assert payload["definition_version"] == PERIOD_DEFINITION_VERSION
    assert payload["structure_mode"] == "period_profiled"
    assert payload["calculation_profiles"] == {"5": "pen_centers_l2", "30": "pen_centers_l2", "d": "pen_centers_l2", "w": "pen_centers_only", "m": "pen_centers_only"}
    assert payload["decomposition_mode"] == "non_same_level"
    assert payload["center_promotion_mode"] == "unified_segment_proof"
    assert payload["promotion_segment_mode"] == "pen_native_segment_proof"
    assert payload["movement_partition_mode"] == "disabled"
    assert payload["nine_unit_mode"] == "owned_units_from_core"
    assert payload["center_envelope_mode"] == "owned_z_units"
    assert payload["envelope_touch_mode"] == "expansion_contact"
    assert payload["movement_boundary_mode"] == "disabled"
    assert payload["divergence_mode"] == "disabled"
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
    assert not {"movements", "movement_revisions", "movement_levels", "points", "point_revisions"} & set(payload["structure"])
    serialized = response.text
    assert "pen_centers" not in payload
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


def test_realtime_delta_is_small_and_skips_structure_analysis(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    active = store.active_chan_run("000001", "5", "2")
    row = {**session_rows(15)[-1], "trade_date": "2026-01-23 10:05:00", "close": 9.8}
    monkeypatch.setattr(main_module, "intraday_service", _RealtimeStub("5", row))
    monkeypatch.setattr(
        main_module.period_structure_service, "preview_period",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("realtime must not preview")),
    )
    first = client.get(
        "/api/chart-realtime/000001",
        params={"timeframe": "5", "known_structure_version": active["structure_version"]},
        headers={"Accept-Encoding": "gzip"},
    )
    assert first.status_code == 200
    assert first.headers["content-encoding"] == "gzip"
    assert int(first.headers["x-uncompressed-bytes"]) < 100_000
    assert {part.split(";")[0] for part in first.headers["server-timing"].split(", ")} == {
        "provider", "sqlite", "structure", "indicator", "serialize",
    }
    payload = first.json()
    assert payload["structure_changed"] is False
    assert payload["structure_update"] is None
    assert payload["bar_upserts"][-1]["trade_date"] == row["trade_date"]
    second = client.get(
        "/api/chart-realtime/000001",
        params={
            "timeframe": "5", "known_market_version": payload["market_version"],
            "known_structure_version": active["structure_version"],
        },
    )
    assert second.status_code == 200
    assert second.json()["bar_upserts"] == []


def test_realtime_structure_update_is_projected_and_30m_is_stable(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    rows = [dict(row) for index, row in enumerate(session_rows(15)) if index % 6 == 5]
    seed(store, "000001", rows, timeframe="30")
    main_module.period_structure_service.ensure("000001", "30", force=True)
    row = {**rows[-1], "trade_date": "2026-01-23 10:30:00", "close": 9.9}
    monkeypatch.setattr(main_module, "intraday_service", _RealtimeStub("30", row))
    response = client.get(
        "/api/chart-realtime/000001",
        params={"timeframe": "30", "known_structure_version": "outdated"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["structure_changed"] is True
    assert payload["structure_update"]
    structure = payload["structure_update"]["structure"]
    assert structure["promotion_candidate_revisions"] == []
    assert structure["segment_proof_revisions"] == []
    assert structure["pen_diagnostics"] == []
    assert int(response.headers["x-uncompressed-bytes"]) < 1_000_000


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
