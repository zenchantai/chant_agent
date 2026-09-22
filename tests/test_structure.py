from pathlib import Path

import pytest

from app.period_structure import (
    CALCULATOR_SOURCE_FILES,
    PeriodStructureService,
    calculate_calculator_fingerprint,
)
from app.rules import PERIOD_DEFINITION_VERSION, load_rulebook
from app.store import Store
from tests.chan_fixtures import seed, session_rows


def test_rulebook_and_definition_version_are_v31():
    rulebook = load_rulebook()
    assert rulebook["version"] == PERIOD_DEFINITION_VERSION
    assert PERIOD_DEFINITION_VERSION == "chan-period-candidate-ownership-boundary-v31"


def test_calculator_fingerprint_is_deterministic_and_fails_closed(tmp_path):
    for relative_path in CALCULATOR_SOURCE_FILES:
        source = tmp_path / relative_path
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(f"source:{relative_path}\n", encoding="utf-8")
    first = calculate_calculator_fingerprint(tmp_path)
    assert first == calculate_calculator_fingerprint(tmp_path)
    assert len(first) == 64
    changed = tmp_path / CALCULATOR_SOURCE_FILES[0]
    changed.write_text(changed.read_text() + "changed\n", encoding="utf-8")
    assert calculate_calculator_fingerprint(tmp_path) != first
    changed.unlink()
    with pytest.raises(FileNotFoundError):
        calculate_calculator_fingerprint(tmp_path)


def test_fingerprint_and_market_version_invalidate_active_run(tmp_path):
    store = Store(str(tmp_path / "fingerprint.db"))
    rows = session_rows()
    seed(store, "000001", rows)
    first = PeriodStructureService(store, "fingerprint-a").ensure("000001", "5", force=True)
    reused = PeriodStructureService(store, "fingerprint-a").ensure("000001", "5")
    assert reused["meta"]["run_id"] == first["meta"]["run_id"]
    changed = PeriodStructureService(store, "fingerprint-b").ensure("000001", "5")
    assert changed["meta"]["run_id"] != first["meta"]["run_id"]
    store.upsert_bars("000001", "5", "2", [{
        **rows[-1],
        "open": rows[-1]["open"] + 1,
        "high": rows[-1]["high"] + 1,
        "low": rows[-1]["low"] + 1,
        "close": rows[-1]["close"] + 1,
    }])
    market_changed = PeriodStructureService(store, "fingerprint-b").ensure("000001", "5")
    assert market_changed["meta"]["run_id"] != changed["meta"]["run_id"]


def test_period_snapshot_is_reproducible_and_uses_new_structure_groups(tmp_path):
    rows = session_rows()
    first_store = Store(str(tmp_path / "first.db"))
    second_store = Store(str(tmp_path / "second.db"))
    seed(first_store, "000001", rows)
    seed(second_store, "000001", rows)
    first = PeriodStructureService(first_store, "same").ensure("000001", "5", force=True)
    second = PeriodStructureService(second_store, "same").ensure("000001", "5", force=True)
    assert first["meta"]["structure_version"] == second["meta"]["structure_version"]
    assert first["structure"] == second["structure"]
    assert set(first) == {"meta", "structure"}
    assert {"pens", "components", "centers", "center_revisions", "movements", "movement_revisions", "points", "point_revisions", "relations", "issues"} <= set(first["structure"])


def test_chart_page_is_nested_and_pagination_preserves_structure_identity(tmp_path):
    store = Store(str(tmp_path / "page.db"))
    rows = session_rows(15)
    seed(store, "000001", rows)
    service = PeriodStructureService(store, "page")
    service.ensure("000001", "5", force=True)
    first = service.chart_page("000001", "5", "2", None, 300)
    assert set(first) == {"meta", "market", "structure", "indicators", "drawings", "pagination"}
    assert first["pagination"]["has_more"] is True
    second = service.chart_page("000001", "5", "2", first["pagination"]["next_before"], 300)
    assert first["market"]["bars"][0]["trade_date"] > second["market"]["bars"][0]["trade_date"]
    first_ids = {item["id"] for item in first["structure"]["centers"]}
    second_ids = {item["id"] for item in second["structure"]["centers"]}
    overlap = first_ids & second_ids
    source = service.load("000001", "5")["structure"]["centers"]
    by_id = {item["id"]: item for item in source}
    assert all(by_id[identifier]["family_id"] for identifier in overlap)
    assert "pen_centers" not in first
    assert "buy_sell_points" not in first


def test_chart_page_level_zero_returns_all_active_levels(tmp_path):
    store = Store(str(tmp_path / "all-levels.db"))
    seed(store, "000001", session_rows(15))
    service = PeriodStructureService(store, "all-levels")
    snapshot = service.ensure("000001", "5", force=True)
    page = service.chart_page("000001", "5", "2", None, 300, structure_level=0)
    assert page["meta"]["active_level"] == 0
    assert page["structure"]["levels"] == snapshot["structure"]["levels"]
    assert {item["level"] for item in page["structure"]["centers"]} <= set(page["structure"]["levels"])


def test_preview_never_writes_or_activates_run(tmp_path):
    store = Store(str(tmp_path / "preview.db"))
    rows = session_rows(3)
    seed(store, "000001", rows)
    service = PeriodStructureService(store, "preview")
    formal = service.ensure("000001", "5", force=True)
    run_count = store.db.execute("SELECT COUNT(*) FROM chan_structure_runs").fetchone()[0]
    preview = service.preview_period("000001", "5", "2", rows)
    assert preview["meta"]["preview"] is True
    assert preview["meta"]["persisted"] is False
    assert store.db.execute("SELECT COUNT(*) FROM chan_structure_runs").fetchone()[0] == run_count
    assert store.active_chan_run("000001", "5")["id"] == formal["meta"]["run_id"]


def test_preview_tracks_forming_tail_and_keeps_database_formal(tmp_path):
    store = Store(str(tmp_path / "forming-preview.db"))
    rows = session_rows(12)
    seed(store, "000001", rows)
    service = PeriodStructureService(store, "forming-preview")
    formal = service.ensure("000001", "5", force=True)
    run_count = store.db.execute("SELECT COUNT(*) FROM chan_structure_runs").fetchone()[0]
    forming = {**rows[-1], "trade_date": "2026-01-21 10:00:00", "close": rows[-1]["close"] + 1,
               "high": rows[-1]["high"] + 1, "low": rows[-1]["low"] + 1}
    marker = {"trade_date": forming["trade_date"], "is_forming": True, "status": "provisional"}

    first = service.preview_period("000001", "5", "2", [*rows, forming], marker)
    tail = first["structure"]["pens"][-1]
    assert tail["status"] == "provisional"
    assert tail["end_date"] == forming["trade_date"]
    assert any(center["status"] == "provisional" for center in first["structure"]["centers"])
    assert any(movement["status"] == "provisional" for movement in first["structure"]["movements"])
    assert store.db.execute("SELECT COUNT(*) FROM chan_structure_runs").fetchone()[0] == run_count
    assert service.load("000001", "5")["meta"]["run_id"] == formal["meta"]["run_id"]

    forming["close"] += 2
    forming["high"] += 2
    forming["low"] -= 2
    second = service.preview_period("000001", "5", "2", [*rows, forming], marker)
    assert second["structure"]["pens"][-1]["end_price"] != tail["end_price"]
    assert second["meta"]["structure_version"] != first["meta"]["structure_version"]


def test_source_paths_exist_in_repository():
    root = Path(__file__).resolve().parents[1]
    assert all((root / path).is_file() for path in CALCULATOR_SOURCE_FILES)
