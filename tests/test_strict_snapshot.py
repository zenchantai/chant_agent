import json

import pytest

from app.hierarchy import build_hierarchy
from app.period_structure import PeriodStructureService
from app.store import Store
from scripts import recalculate_structure
from tests.test_strict_movements import structural_stream
from tests.test_structure import seed, session_rows


def test_snapshot_rejects_forged_confirmation_without_switching_active(tmp_path):
    store = Store(str(tmp_path / "snapshot.db"))
    seed(store, "000001", session_rows())
    service = PeriodStructureService(store)
    service.ensure("000001", "5", force=True)
    before = store.active_period_structure_run("000001", "5")
    units, centers = structural_stream(4)
    structure = build_hierarchy(units, centers)
    confirmed = next(item for item in structure["movements"] if item["status"] == "confirmed")
    confirmed["confirmation_center_id"] = "nonexistent"
    with pytest.raises(ValueError, match="走势确认不合法"):
        store.replace_period_structure("000001", "5", "2", before["definition_version"], structure, "test")
    assert store.active_period_structure_run("000001", "5")["id"] == before["id"]


def test_new_snapshot_keeps_historical_rows_and_retires_unsupported_pointer(tmp_path, monkeypatch):
    path = tmp_path / "migration.db"
    store = Store(str(path))
    seed(store, "000001", session_rows())
    service = PeriodStructureService(store)
    result = service.ensure("000001", "5", force=True)
    previous_id = result["run_id"]
    previous_rows = store.period_rows("period_movements", previous_id)
    store.db.execute("UPDATE period_structure_runs SET definition_version='old' WHERE id=?", (previous_id,))
    store.db.execute("INSERT INTO active_period_structure_runs VALUES ('000001','15','2',?,'2026-01-01')", (previous_id,))
    store.db.commit()
    store.db.close()
    monkeypatch.setattr(recalculate_structure, "TIMEFRAMES", ("5",))
    report = recalculate_structure._execute(path, ("000001",))
    assert report["success_count"] == 1 and report["failure_count"] == 0
    reopened = Store(str(path))
    assert reopened.active_period_structure_run("000001", "5")["id"] != previous_id
    assert reopened.period_rows("period_movements", previous_id) == previous_rows
    assert reopened.active_period_structure_run("000001", "15") is None
    assert report["retired_active_runs"] == [{"symbol": "000001", "timeframe": "15", "run_id": previous_id}]
    active = reopened.active_period_structure_run("000001", "5")
    assert "unassigned_by_level" in json.loads(active["structure_metadata"])


def test_chart_page_keeps_offscreen_confirmation_in_context(tmp_path, monkeypatch):
    store = Store(str(tmp_path / "page.db"))
    units, centers = structural_stream(4)
    structure = build_hierarchy(units, centers)
    bars = [{"trade_date": unit["start_date"], "open": unit["start_price"], "close": unit["end_price"],
             "high": unit["high"], "low": unit["low"], "volume": 1, "amount": 1} for unit in units]
    store.upsert_bars("000001", "d", "2", bars)
    service = PeriodStructureService(store)
    monkeypatch.setattr(service, "effective_structure", lambda *args: {**structure, "pens": units, "available": True})
    page = service.chart_page("000001", "d", "2", units[6]["start_date"], 6)
    movement = next(item for item in page["movements"] if item["status"] == "confirmed")
    visible = {center["id"] for center in page["centers"]}
    context = {center["id"] for center in page["context_centers"]}
    assert movement["confirmation_center_id"] in visible | context
    assert context and not context & visible
