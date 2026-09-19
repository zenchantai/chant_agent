import sqlite3

from app.store import Store
from scripts.rebuild_v25_database import PRESERVED_TABLES, dry_run, execute


def _legacy_database(path):
    store = Store(str(path))
    store.upsert_bars("000001", "d", "2", [{
        "trade_date": "2026-01-05", "open": 10, "high": 11, "low": 9,
        "close": 10.5, "volume": 100, "amount": 1000,
    }])
    store.upsert_stock("000001", "平安银行")
    store.db.execute("UPDATE stock_pool SET enabled=0 WHERE symbol='000001'")
    store.db.execute("CREATE TABLE period_structure_runs(id INTEGER PRIMARY KEY, payload TEXT)")
    store.db.execute("INSERT INTO period_structure_runs VALUES(1, '{\"legacy\":true}')")
    store.db.commit()
    store.db.close()


def test_dry_run_reports_exact_preserved_scope_without_mutation(tmp_path):
    database = tmp_path / "legacy.db"
    _legacy_database(database)
    result = dry_run(database)
    assert result["mode"] == "dry-run"
    assert result["integrity_check"] == "ok"
    assert result["missing_preserved_tables"] == []
    assert {item["table"] for item in result["preserved_tables"]} == set(PRESERVED_TABLES)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM period_structure_runs").fetchone()[0] == 1


def test_execute_rebuilds_schema_and_preserves_application_data(tmp_path):
    database = tmp_path / "legacy.db"
    _legacy_database(database)
    result = execute(database)
    assert result["status"] == "success"
    assert result["final_integrity_check"] == "ok"
    assert result["active_run_count"] == 0
    assert result["backup"]
    assert result["rollback"]
    with sqlite3.connect(database) as connection:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert set(PRESERVED_TABLES) <= tables
        assert "chan_structure_runs" in tables
        assert "period_structure_runs" not in tables
        assert connection.execute("SELECT COUNT(*) FROM market_bars").fetchone()[0] == 1
        assert connection.execute("SELECT name FROM stock_pool WHERE symbol='000001'").fetchone()[0] == "平安银行"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
