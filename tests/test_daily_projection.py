from copy import deepcopy
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.chan_structure import build_structure_hierarchy
from app.intraday import IntradayService, TZ
from app.period_structure import PeriodStructureService, analyze_period
from app.rules import PERIOD_DEFINITION_VERSION
from app.store import Store
from app.structure_display import center_display_catalog, project_daily_l2
from tests.chan_fixtures import pens_from_prices, legacy_expansion_snapshot


def daily_snapshot():
    pens = pens_from_prices([1, 10, 6, 15, 8, 20, 12, 25, 21, 30, 26, 35])
    return {
        "meta": {"symbol": "000001", "timeframe": "d", "adjustflag": "2", "run_id": 40,
                 "definition_version": PERIOD_DEFINITION_VERSION, "calculator_fingerprint": "test",
                 "market_version": "daily-market", "structure_version": "daily-structure",
                 "source_cutoff": "2026-01-12", "persisted": True, "preview": False},
        "structure": legacy_expansion_snapshot(),
    }


def target_page(timeframe="m", dates=("2025-12-31", "2026-01-30")):
    return {"market": {"symbol": "000001", "timeframe": timeframe, "adjustflag": "2",
                       "bars": [{"trade_date": stamp} for stamp in dates]},
            "structure": {"centers": [], "center_revisions": [], "pens": []}, "meta": {"run_id": 41}}


def test_month_start_projects_to_its_own_month_and_leaves_native_graph_untouched():
    page, source = target_page(), daily_snapshot()
    original = deepcopy((page, source))
    result = project_daily_l2(page, source)
    overlay = result["overlays"]["daily_l2"]
    assert overlay["status"] == "ready"
    assert overlay["source"]["run_id"] == 40
    center, = overlay["centers"]
    assert center["target_start_date"] == center["target_end_date"] == "2026-01-30"
    assert center["start_date"] == "2026-01-02"
    assert center["id"] == "daily-l2:" + center["revision_id"]
    assert center["source_timeframe"] == "d" and center["level"] == 2
    assert (page, source) == original
    assert result["structure"] == page["structure"]
    assert "source_pen_ids" not in center


def test_cross_year_iso_week_short_week_and_same_bucket():
    source = daily_snapshot()
    center = source["structure"]["centers"][0]
    center.update(start_date="2025-12-29", end_date="2026-01-01")
    page = target_page("w", ("2025-12-26", "2025-12-31", "2026-01-09"))
    projection, = project_daily_l2(page, source)["overlays"]["daily_l2"]["centers"]
    assert projection["target_start_date"] == projection["target_end_date"] == "2025-12-31"
    assert projection["start_date"] == "2025-12-29" and projection["end_date"] == "2026-01-01"


def test_page_uses_full_bucket_span_and_retains_original_geometry():
    source = daily_snapshot()
    center = source["structure"]["centers"][0]
    center.update(start_date="2025-12-20", end_date="2026-02-02")
    projection, = project_daily_l2(target_page(dates=("2026-01-30",)), source)["overlays"]["daily_l2"]["centers"]
    assert projection["clipped_start"] and projection["clipped_end"]
    assert projection["start_date"] == "2025-12-20"
    assert projection["end_date"] == "2026-02-02"
    assert (projection["zd"], projection["zg"]) == (center["zd"], center["zg"])
    assert project_daily_l2(target_page(dates=("2026-03-31",)), source)["overlays"]["daily_l2"]["centers"] == []


def test_constituent_daily_l2_matches_daily_display_catalog():
    source = daily_snapshot()
    child = source["structure"]["centers"][0]
    child["active"] = False
    parent = {**deepcopy(child), "id": "L3:r1", "family_id": "L3", "level": 3,
              "active": True, "child_center_ids": [child["id"]]}
    source["structure"]["center_revisions"].append(parent)
    source["structure"]["centers"] = [parent]
    overlay = project_daily_l2(target_page(), source)["overlays"]["daily_l2"]
    projected, = overlay["centers"]
    assert projected["revision_id"] == child["id"]
    assert projected["display_role"] == "constituent"
    assert projected["parent_revision_ids"] == [parent["id"]]
    assert projected["revision_id"] in {item["revision_id"] for item in center_display_catalog(source["structure"])}


def test_unavailable_empty_ready_stale_and_preview_are_distinct():
    page = target_page()
    assert project_daily_l2(page, None)["overlays"]["daily_l2"]["status"] == "unavailable"
    source = daily_snapshot()
    source["structure"] = {"centers": [], "center_revisions": []}
    empty = project_daily_l2(page, source)["overlays"]["daily_l2"]
    assert empty["status"] == "ready" and empty["centers"] == []
    stale = project_daily_l2(page, daily_snapshot(), "更新失败")["overlays"]["daily_l2"]
    assert stale["status"] == "stale" and stale["centers"] and stale["source"]["source_cutoff"] == "2026-01-12"
    source["meta"]["preview"] = True
    assert project_daily_l2(page, source)["overlays"]["daily_l2"]["status"] == "unavailable"
    assert project_daily_l2(target_page("d"), daily_snapshot()) == target_page("d")


@pytest.mark.parametrize("field,value", [("symbol", "999999"), ("adjustflag", "3"), ("timeframe", "w")])
def test_projection_rejects_a_mismatched_source_instead_of_relabelling_it(field, value):
    source = daily_snapshot()
    source["meta"][field] = value
    overlay = project_daily_l2(target_page(), source)["overlays"]["daily_l2"]
    assert overlay["status"] == "unavailable" and overlay["centers"] == [] and overlay["source"] is None


def bar(stamp, price=10):
    return {"trade_date": stamp, "open": price, "high": price + 1, "low": price - 1,
            "close": price, "volume": 100, "amount": 1000}


@pytest.mark.parametrize("timeframe", ["w", "m"])
def test_reference_formal_and_preview_have_the_same_calculation_profile(tmp_path, timeframe):
    store = Store(str(tmp_path / "profile.db"))
    rows = [bar("2026-01-09"), bar("2026-01-16", 12)]
    store.upsert_bars("000001", timeframe, "2", rows)
    service = PeriodStructureService(store)
    formal = service.ensure("000001", timeframe)
    preview = service.preview_period("000001", timeframe, "2", rows)
    for snapshot in (formal, preview, store.load_chan_structure(formal["meta"]["run_id"])):
        assert snapshot["meta"]["calculation_profile"] == "pen_centers_only"
        assert snapshot["meta"]["max_level"] <= 1
        assert all(snapshot["structure"][name] == [] for name in ("promotion_candidates", "segment_proofs"))
        assert "movements" not in snapshot["structure"] and "points" not in snapshot["structure"]
    assert analyze_period(rows, "000001", "d")["meta"]["calculation_profile"] == "pen_centers_l2"


def api_client(tmp_path, monkeypatch):
    store = Store(str(tmp_path / "api-reference.db"))
    store.upsert_bars("000001", "m", "2", [bar("2026-01-30")])
    service = PeriodStructureService(store)
    original_ensure = service.ensure
    source = daily_snapshot()
    monkeypatch.setattr(service, "ensure", lambda symbol, period, adjustflag="2", force=False:
                        deepcopy(source) if period == "d" else original_ensure(symbol, period, adjustflag, force))
    intraday = IntradayService(store, clock=lambda: datetime(2026, 9, 19, 18, tzinfo=TZ))
    monkeypatch.setattr(main_module, "store", store)
    monkeypatch.setattr(main_module, "period_structure_service", service)
    monkeypatch.setattr(main_module, "intraday_service", intraday)
    return TestClient(main_module.app), store, service, intraday, source


def test_api_overlay_is_independent_of_native_level_filter_and_version(tmp_path, monkeypatch):
    client, store, _, _, source = api_client(tmp_path, monkeypatch)
    first = client.get("/api/chart-data/000001?timeframe=m&structure_level=1").json()
    second = client.get("/api/chart-data/000001?timeframe=m&structure_level=2").json()
    assert first["overlays"] == second["overlays"]
    assert second["structure"]["centers"] == []
    before = first["meta"]["run_id"]
    source["meta"].update(run_id=50, structure_version="daily-new")
    after = client.get("/api/chart-data/000001?timeframe=m").json()
    assert after["meta"]["run_id"] == before
    assert after["overlays"]["daily_l2"]["source"]["run_id"] == 50
    assert store.db.execute("SELECT COUNT(*) FROM chan_structure_runs WHERE timeframe='d'").fetchone()[0] == 0


def test_api_refresh_failure_preserves_formal_source_without_recalculating_preview(tmp_path, monkeypatch):
    client, _, _, intraday, _ = api_client(tmp_path, monkeypatch)
    called = []
    def refresh(symbol, period, adjustflag, include_quote=False):
        called.append(period)
        return {"result": "failed", "error": "远端更新失败", "finalized_count": 0}
    monkeypatch.setattr(intraday, "refresh_period", refresh)
    monkeypatch.setattr(intraday, "live_rows", lambda *args: [bar("2026-09-18", 15)])
    monkeypatch.setattr(intraday, "forming_bar", lambda *args: {
        "trade_date": "2026-09-18", "is_forming": True, "status": "provisional"})
    response = client.get("/api/chart-data/000001?timeframe=m&refresh=true")
    assert response.status_code == 200
    payload = response.json()
    assert called == ["d", "m"]
    assert payload["meta"]["preview"] is False
    assert payload["meta"]["persisted"] is True
    assert payload["meta"]["calculation_profile"] == "pen_centers_only"
    assert payload["overlays"]["daily_l2"]["status"] == "stale"
    assert payload["overlays"]["daily_l2"]["source"]["structure_version"] == "daily-structure"
    assert payload["overlays"]["daily_l2"]["centers"]


def test_api_daily_failure_does_not_break_monthly_page(tmp_path, monkeypatch):
    client, _, service, _, _ = api_client(tmp_path, monkeypatch)
    original = service.ensure
    def fail_daily(symbol, period, adjustflag="2", force=False):
        if period == "d":
            raise RuntimeError("no usable daily source")
        return original(symbol, period, adjustflag, force)
    monkeypatch.setattr(service, "ensure", fail_daily)
    response = client.get("/api/chart-data/000001?timeframe=m")
    assert response.status_code == 200
    assert response.json()["market"]["bars"]
    assert response.json()["overlays"]["daily_l2"]["status"] == "unavailable"
