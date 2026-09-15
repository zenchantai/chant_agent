import json
import sqlite3

from scripts.migrate_remove_same_level import migrate
from app.store import Store


def test_remove_same_level_migration_cleans_payloads_and_columns(tmp_path):
    db = tmp_path / "migration.db"
    store = Store(str(db))
    store.db.execute("ALTER TABLE period_structure_runs ADD COLUMN decomposition_meta TEXT NOT NULL DEFAULT '{}'")
    store.db.execute("ALTER TABLE period_structure_runs ADD COLUMN movement_input_hash TEXT NOT NULL DEFAULT ''")
    store.db.execute("ALTER TABLE period_structure_runs ADD COLUMN hierarchy_input_hash TEXT NOT NULL DEFAULT ''")
    store.db.execute("INSERT INTO period_pen_centers(run_id,symbol,timeframe,adjustflag,definition_version,ordinal,start_date,end_date,payload) VALUES(1,'s','d','2','v',0,'2026-01-01','2026-01-02',?)", (json.dumps({"id": "same-level-center-x", "role": "same_level"}),))
    store.db.execute("INSERT INTO period_movements(run_id,symbol,timeframe,adjustflag,definition_version,ordinal,start_date,end_date,payload) VALUES(1,'s','d','2','v',0,'2026-01-01','2026-01-02',?)", (json.dumps({"id": "m", "role": "same_level_decomposition"}),))
    store.db.commit()
    store.db.close()

    result = migrate(db, True, tmp_path / "backup")
    assert result["deleted_same_level_movements"] == 1
    connection = sqlite3.connect(db)
    assert all(name not in {row[1] for row in connection.execute("PRAGMA table_info(period_structure_runs)")} for name in ("decomposition_meta", "movement_input_hash", "hierarchy_input_hash"))
    assert connection.execute("SELECT COUNT(*) FROM period_pen_centers").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM period_movements").fetchone()[0] == 0
    connection.close()
