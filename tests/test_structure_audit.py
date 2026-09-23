import sqlite3
from copy import deepcopy
import json

import pytest

from app.chan_structure import build_structure_hierarchy
from app.period_structure import PeriodStructureService
from app.rules import PERIOD_DEFINITION_VERSION
from app.store import Store
from scripts.audit_structure import EXPECTED_FINGERPRINT, audit_api_payload, audit_run, enabled_symbols
from tests.chan_fixtures import pens_from_prices


def test_audit_scope_reads_enabled_stock_pool_only():
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE stock_pool(symbol TEXT, enabled INTEGER, sort_order INTEGER)")
    connection.executemany(
        "INSERT INTO stock_pool VALUES(?,?,?)",
        [("B", 1, 2), ("A", 1, 1), ("C", 0, 0)],
    )
    assert enabled_symbols(connection) == ("A", "B")


def test_nested_api_audit_accepts_center_only_contract_and_rejects_legacy_fields():
    payload = {
        "meta": {
            "definition_version": PERIOD_DEFINITION_VERSION,
            "calculator_fingerprint": EXPECTED_FINGERPRINT,
            "active_level": 2,
        },
        "market": {"bars": []},
        "structure": {
            "centers": [{"id": "c", "level": 2}],
        },
        "indicators": {},
        "drawings": {"items": [], "version": ""},
        "pagination": {"has_more": False},
    }
    assert audit_api_payload(payload, 2) == []
    payload["structure"]["movements"] = [{"id": "m", "level": 2}]
    assert "API返回已删除字段:movements" in audit_api_payload(payload, 2)
    payload["structure"].pop("movements")
    payload["pen_centers"] = []
    assert "API泄漏已删除的旧结构语义" in audit_api_payload(payload, 2)


def test_api_audit_rejects_cross_level_structure_rows():
    payload = {
        "meta": {
            "definition_version": PERIOD_DEFINITION_VERSION,
            "calculator_fingerprint": EXPECTED_FINGERPRINT,
            "active_level": 1,
        },
        "market": {},
        "structure": {"centers": [{"level": 2}]},
        "indicators": {}, "drawings": {}, "pagination": {},
    }
    assert "API centers混入其他级别" in audit_api_payload(payload, 1)


def reference_payload(timeframe="w"):
    pens = pens_from_prices([1, 10, 6, 15, 8, 20, 12, 25, 21, 30, 26, 35])
    bars = [{"trade_date": "2026-01-09"}, {"trade_date": "2026-01-16"}]
    if timeframe == "m":
        bars = [{"trade_date": "2026-01-30"}]
    return {
        "meta": {
            "symbol": "000001", "timeframe": timeframe, "calculation_profile": "pen_centers_only",
            "definition_version": PERIOD_DEFINITION_VERSION, "calculator_fingerprint": EXPECTED_FINGERPRINT,
            "active_level": 1, "max_level": 1,
        },
        "market": {"symbol": "000001", "timeframe": timeframe, "adjustflag": "2", "bars": bars},
        "structure": {"pens": pens, **build_structure_hierarchy(pens, calculation_profile="pen_centers_only")},
        "indicators": {}, "drawings": {}, "pagination": {},
        "overlays": {"daily_l2": {
            "status": "ready", "error": None,
            "source": {
                "symbol": "000001", "timeframe": "d", "adjustflag": "2", "run_id": 9,
                "definition_version": PERIOD_DEFINITION_VERSION, "calculator_fingerprint": EXPECTED_FINGERPRINT,
                "market_version": "daily-market", "structure_version": "daily-structure", "source_cutoff": "2026-01-16",
            },
            "centers": [{
                "id": "daily-l2:daily:r1", "revision_id": "daily:r1", "family_id": "daily", "ordinal": 0,
                "level": 2, "source_timeframe": "d", "status": "formed", "active": True,
                "display_role": "active", "parent_revision_ids": [],
                "start_date": "2026-01-06", "end_date": "2026-01-08", "zd": 8, "zg": 10,
                "target_start_date": bars[0]["trade_date"], "target_end_date": bars[0]["trade_date"],
                "clipped_start": False, "clipped_end": False,
            }],
        }},
    }


@pytest.mark.parametrize("timeframe", ["w", "m"])
def test_audit_accepts_reference_native_structure_and_daily_single_bar_projection(timeframe):
    assert audit_api_payload(reference_payload(timeframe), 1) == []


@pytest.mark.parametrize("group", [
    "movements", "movement_revisions", "points", "point_revisions",
    "promotion_candidates", "promotion_candidate_revisions",
])
def test_reference_api_audit_rejects_retired_or_promotion_groups(group):
    payload = reference_payload()
    payload["structure"][group] = [{"id": "forbidden", "level": 1}]
    expected = (f"profile:forbidden_group:{group}" if group in {"movements", "movement_revisions", "points", "point_revisions"}
                else "profile:promotion_evidence_forbidden")
    assert expected in audit_api_payload(payload, 1)


def test_reference_audit_checks_profile_and_all_revisions_not_just_active_centers():
    payload = reference_payload()
    payload["meta"]["calculation_profile"] = "full"
    assert "profile:unexpected:w:full" in audit_api_payload(payload, 1)
    payload["meta"]["calculation_profile"] = "pen_centers_only"
    payload["meta"]["max_level"] = 2
    payload["structure"]["center_revisions"][0].update(
        level=2, active=False, unit_kind="center_revision", formation_modes=["expansion_envelope_overlap"],
    )
    problems = audit_api_payload(payload, 1)
    assert "profile:max_level_above_limit" in problems
    assert any(item.startswith("profile:invalid_level:center_revisions:") for item in problems)
    assert any(item.startswith("profile:promotion_evidence:center_revisions:") for item in problems)


@pytest.mark.parametrize("group", ["pens", "components", "centers", "center_revisions", "display_centers"])
def test_api_audit_rejects_daily_projection_leaking_into_native_graph(group):
    payload = reference_payload()
    overlay = deepcopy(payload["overlays"]["daily_l2"]["centers"][0])
    overlay["level"] = 1  # Even a mislabeled overlay must not become native data.
    payload["structure"].setdefault(group, []).append(overlay)
    assert any(item.startswith(f"profile:overlay_in_native:{group}:") for item in audit_api_payload(payload, 1))


@pytest.mark.parametrize("field,value", [
    ("symbol", "000002"), ("adjustflag", "3"), ("timeframe", "w"),
    ("definition_version", "old"), ("calculator_fingerprint", "old"),
])
def test_api_audit_rejects_projection_from_wrong_source(field, value):
    payload = reference_payload()
    payload["overlays"]["daily_l2"]["source"][field] = value
    assert f"overlay:source_mismatch:{field}" in audit_api_payload(payload, 1)


@pytest.mark.parametrize("field,value,problem", [
    ("level", 1, "non_daily_l2"),
    ("source_timeframe", "m", "non_daily_l2"),
    ("zg", 8, "invalid_price_core"),
    ("zd", float("nan"), "invalid_price_core"),
    ("start_date", "2026-01-09", "invalid_source_dates"),
    ("end_date", "2026-01-19", "invalid_source_dates"),
    ("target_start_date", "2026-01-16", "invalid_target_mapping"),
    ("target_end_date", "2026-01-16", "invalid_target_mapping"),
    ("clipped_start", True, "invalid_clipping"),
    ("active", False, "invalid_display_role"),
    ("revision_id", "different", "invalid_identity"),
])
def test_api_audit_rejects_invalid_projected_geometry_or_identity(field, value, problem):
    payload = reference_payload()
    payload["overlays"]["daily_l2"]["centers"][0][field] = value
    assert any(item.endswith(":" + problem) for item in audit_api_payload(payload, 1))


def test_api_audit_distinguishes_unavailable_empty_ready_and_stale_sources():
    payload = reference_payload()
    overlay = payload["overlays"]["daily_l2"]
    overlay["centers"] = []
    assert audit_api_payload(payload, 1) == []
    overlay["status"] = "stale"
    assert "overlay:stale_without_reason" in audit_api_payload(payload, 1)
    overlay["error"] = "fetch failed"
    assert audit_api_payload(payload, 1) == []
    overlay["status"] = "unavailable"
    assert "overlay:unavailable_contains_data" in audit_api_payload(payload, 1)
    overlay["source"] = None
    assert audit_api_payload(payload, 1) == []


def test_api_audit_requires_reference_overlay_and_rejects_preview_source():
    payload = reference_payload()
    payload["overlays"]["daily_l2"]["source"]["preview"] = True
    assert "overlay:nonformal_source" in audit_api_payload(payload, 1)
    payload.pop("overlays")
    assert "overlay:missing_daily_l2" in audit_api_payload(payload, 1)


def test_database_audit_rejects_mislabeled_reference_profile(tmp_path):
    store = Store(str(tmp_path / "audit-reference.db"))
    store.upsert_bars("000001", "w", "2", [{
        "trade_date": "2026-01-09", "open": 10, "high": 12, "low": 9, "close": 11,
        "volume": 10, "amount": 100,
    }])
    snapshot = PeriodStructureService(store, EXPECTED_FINGERPRINT).ensure("000001", "w")
    assert audit_run(store.db, "000001", "w")["status"] == "ok"
    meta = {**snapshot["meta"], "calculation_profile": "full"}
    store.db.execute("UPDATE chan_structure_runs SET meta_json=? WHERE id=?", (json.dumps(meta), snapshot["meta"]["run_id"]))
    store.db.commit()
    result = audit_run(store.db, "000001", "w")
    assert result["status"] == "failed"
    assert "profile:unexpected:w:full" in result["problems"]
