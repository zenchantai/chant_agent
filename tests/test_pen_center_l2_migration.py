from app.period_structure import PeriodStructureService
from app.store import Store
from scripts.migrate_pen_center_l2 import REMOVED_TABLES, migrate, protected_digest, table_names


def test_migration_removes_legacy_schema_without_changing_market_or_drawings(tmp_path):
    store = Store(str(tmp_path / "legacy.db"))
    store.upsert_bars("000001", "w", "2", [{
        "trade_date": "2026-01-09", "open": 10, "high": 12, "low": 9,
        "close": 11, "volume": 10, "amount": 100,
    }])
    store.create_drawing({
        "symbol": "000001", "timeframe": "w", "object_type": "segment",
        "start_anchor": {"trade_date": "2026-01-09", "price": 10},
        "end_anchor": {"trade_date": "2026-01-09", "price": 11},
    })
    PeriodStructureService(store).ensure("000001", "w")
    connection = store.db
    connection.execute("ALTER TABLE chan_center_revisions ADD COLUMN owner_movement_id TEXT")
    for table in REMOVED_TABLES:
        connection.execute(f"CREATE TABLE {table}(run_id INTEGER, id TEXT)")
        connection.execute(f"INSERT INTO {table} VALUES(1, 'legacy')")
    connection.commit()

    protected = {
        table: protected_digest(connection, table)
        for table in ("market_bars", "drawing_objects")
    }
    result = migrate(connection)

    assert result["after"]["quick_check"] == ["ok"]
    assert result["after"]["foreign_key_check"] == []
    assert not set(REMOVED_TABLES) & table_names(connection)
    assert "owner_movement_id" not in {
        row[1] for row in connection.execute("PRAGMA table_info(chan_center_revisions)")
    }
    assert all(protected_digest(connection, table) == digest for table, digest in protected.items())
    assert len(result["after"]["active_runs"]) == 1
    connection.close()
