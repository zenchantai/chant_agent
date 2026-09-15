import sqlite3

import pytest

from app.store import Store
from scripts import migrate_remove_segments as migration


def seed_database(path):
    store = Store(str(path))
    store.db.execute("CREATE TABLE period_segments (id INTEGER PRIMARY KEY, payload TEXT)")
    store.db.execute("INSERT INTO period_segments VALUES (1, '{\"role\":\"line_segment\"}')")
    store.db.execute("CREATE TABLE preservation_sample (id INTEGER, content BLOB)")
    store.db.execute("INSERT INTO preservation_sample VALUES (1, x'0001ff')")
    store.db.commit()
    store.db.close()


def test_migration_only_drops_segments_and_store_does_not_recreate_table(tmp_path):
    db = tmp_path / "data.db"
    seed_database(db)
    with sqlite3.connect(db) as connection:
        before = migration.snapshot(connection)
    assert migration.migrate(db)["segment_rows"] == 1
    assert not (tmp_path / "backups").exists()
    result = migration.migrate(db, True)
    assert result["deleted_segment_rows"] == 1
    assert result["other_tables_unchanged"]
    with sqlite3.connect(result["backup"]) as connection:
        assert connection.execute("SELECT COUNT(*) FROM period_segments").fetchone()[0] == 1
        assert migration.snapshot(connection) == before
    store = Store(str(db))
    assert not store.db.execute("SELECT 1 FROM sqlite_master WHERE name='period_segments'").fetchone()
    store.db.close()
    with sqlite3.connect(db) as connection:
        assert migration.snapshot(connection) == before
    assert migration.migrate(db, True)["table_exists"] is False


def test_migration_rolls_back_when_preservation_check_fails(tmp_path, monkeypatch):
    db = tmp_path / "data.db"
    seed_database(db)
    snapshots = iter([{"before": True}, {"before": False}])
    monkeypatch.setattr(migration, "snapshot", lambda connection: next(snapshots))
    with pytest.raises(RuntimeError, match="非线段表发生变化"):
        migration.migrate(db, True)
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM period_segments").fetchone()[0] == 1
