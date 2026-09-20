import sqlite3

import pytest

from app.period_structure import PeriodStructureService
from app.store import Store
from tests.chan_fixtures import seed, session_rows


def test_successful_staging_run_activates_atomically_and_preserves_old_success(tmp_path):
    store = Store(str(tmp_path / "snapshot.db"))
    seed(store, "000001", session_rows())
    service = PeriodStructureService(store, calculator_fingerprint="fingerprint-a")
    first = service.ensure("000001", "5", force=True)
    second = PeriodStructureService(store, calculator_fingerprint="fingerprint-b").ensure("000001", "5")
    assert first["meta"]["run_id"] != second["meta"]["run_id"]
    active = store.active_chan_run("000001", "5", "2")
    assert active["id"] == second["meta"]["run_id"]
    assert active["calculator_fingerprint"] == "fingerprint-b"
    successful = store.db.execute(
        "SELECT COUNT(*) FROM chan_structure_runs WHERE symbol='000001' AND timeframe='5' AND status='success'"
    ).fetchone()[0]
    assert successful == 2
    assert store.load_chan_structure(first["meta"]["run_id"])["structure"] == first["structure"]


def test_failed_normalized_write_does_not_switch_active_pointer(tmp_path):
    store = Store(str(tmp_path / "failed.db"))
    seed(store, "000001", session_rows())
    service = PeriodStructureService(store, calculator_fingerprint="stable")
    snapshot = service.ensure("000001", "5", force=True)
    active_before = store.active_chan_run("000001", "5", "2")["id"]
    broken = {
        **snapshot,
        "structure": {**snapshot["structure"], "center_revisions": [{"id": "broken"}]},
    }
    with pytest.raises((AssertionError, KeyError, ValueError)):
        store.replace_chan_structure("000001", "5", "2", broken, snapshot["meta"]["market_version"])
    assert store.active_chan_run("000001", "5", "2")["id"] == active_before


def test_normalized_tables_and_foreign_keys_round_trip(tmp_path):
    store = Store(str(tmp_path / "normalized.db"))
    seed(store, "000001", session_rows())
    snapshot = PeriodStructureService(store).ensure("000001", "5", force=True)
    run_id = snapshot["meta"]["run_id"]
    loaded = store.load_chan_structure(run_id)
    assert loaded["meta"]["structure_version"] == snapshot["meta"]["structure_version"]
    assert loaded["structure"]["centers"] == snapshot["structure"]["centers"]
    assert store.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert store.db.execute("PRAGMA foreign_key_check").fetchall() == []
    tables = {row[0] for row in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "chan_center_revisions" in tables
    assert "period_structure_runs" not in tables
    store.db.close()
    with sqlite3.connect(tmp_path / "normalized.db") as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_staged_run_does_not_activate_and_matrix_switch_validates_all_rows(tmp_path):
    store = Store(str(tmp_path / "staging.db"))
    seed(store,"000001",session_rows())
    snapshot = PeriodStructureService(store).ensure("000001","5")
    old = snapshot["meta"]["run_id"]
    staged = store.replace_chan_structure("000001","5","2",snapshot,snapshot["meta"]["market_version"],activate=False)
    assert store.active_chan_run("000001","5","2")["id"] == old
    kwargs = {k:snapshot["meta"][k] for k in ("definition_version","calculator_fingerprint")}
    with pytest.raises(ValueError):
        store.activate_chan_runs([staged,-1],**kwargs)
    assert store.active_chan_run("000001","5","2")["id"] == old
    store.activate_chan_runs([staged],**kwargs)
    assert store.active_chan_run("000001","5","2")["id"] == staged
    assert store.load_chan_structure(old)["structure"] == snapshot["structure"]
