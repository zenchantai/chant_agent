#!/usr/bin/env python3
"""Remove legacy same-level rows and metadata from the structure database.

The command is read-only by default.  Use ``--execute`` only with the service
stopped; it creates a timestamped backup of the database and any WAL/SHM files
before changing anything.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


REMOVED_COLUMNS = {"decomposition_meta", "movement_input_hash", "hierarchy_input_hash"}
RUN_COLUMNS = [
    "id", "symbol", "timeframe", "adjustflag", "definition_version", "started_at",
    "finished_at", "status", "market_version", "coverage_version", "structure_version",
    "calculator_fingerprint", "error", "movement_count", "center_level_counts",
    "movement_level_counts", "max_confirmed_center_level", "max_available_center_level",
]


def backup_files(db: Path, backup_dir: Path) -> list[str]:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    copied: list[str] = []
    for suffix in ("", "-wal", "-shm"):
        source = Path(f"{db}{suffix}")
        if source.exists():
            target = backup_dir / f"{db.name}.{stamp}{suffix}.bak"
            shutil.copy2(source, target)
            copied.append(str(target))
    return copied


def table_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]


def migrate(db: Path, execute: bool, backup_dir: Path | None = None) -> dict:
    connection = sqlite3.connect(db)
    connection.row_factory = sqlite3.Row
    try:
        movement_rows = connection.execute("SELECT COUNT(*) FROM period_movements WHERE payload LIKE '%same_level%'").fetchone()[0]
        center_rows = connection.execute("SELECT COUNT(*) FROM period_pen_centers WHERE payload LIKE '%same_level%'").fetchone()[0]
        run_columns = table_columns(connection, "period_structure_runs")
        removable = sorted(REMOVED_COLUMNS.intersection(run_columns))
        report = {"database": str(db), "legacy_movement_candidates": movement_rows,
                  "legacy_center_candidates": center_rows,
                  "legacy_run_columns": removable, "executed": execute, "backups": []}
        if not execute:
            return report
        backup_root = backup_dir or db.parent / "backups"
        report["backups"] = backup_files(db, backup_root)
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("""DELETE FROM period_movements
            WHERE json_extract(payload, '$.role') IN ('same_level','same_level_decomposition')
               OR payload LIKE '%same-level-center%' OR payload LIKE '%same_level%'""")
        connection.execute("""DELETE FROM period_pen_centers
            WHERE json_extract(payload, '$.role') = 'same_level'
               OR payload LIKE '%same-level-center%' OR payload LIKE '%same_level%'""")
        connection.execute("""DELETE FROM period_center_relations
            WHERE payload LIKE '%same-level-center%' OR payload LIKE '%same_level%'""")
        # Rebuild rather than ALTER DROP so this works with older SQLite builds.
        if removable:
            existing = set(run_columns)
            kept = [column for column in RUN_COLUMNS if column in existing]
            definitions = {
                row[1]: row[2] + (" NOT NULL" if row[3] else "") + (f" DEFAULT {row[4]}" if row[4] is not None else "")
                for row in connection.execute("PRAGMA table_info(period_structure_runs)")
            }
            connection.execute("ALTER TABLE period_structure_runs RENAME TO period_structure_runs_legacy")
            connection.execute("""CREATE TABLE period_structure_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
                adjustflag TEXT NOT NULL, definition_version TEXT NOT NULL, started_at TEXT NOT NULL,
                finished_at TEXT, status TEXT NOT NULL, market_version TEXT NOT NULL,
                coverage_version TEXT NOT NULL DEFAULT '', structure_version TEXT NOT NULL DEFAULT '',
                calculator_fingerprint TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
                movement_count INTEGER NOT NULL DEFAULT 0, center_level_counts TEXT NOT NULL DEFAULT '{}',
                movement_level_counts TEXT NOT NULL DEFAULT '{}', max_confirmed_center_level INTEGER NOT NULL DEFAULT 0,
                max_available_center_level INTEGER NOT NULL DEFAULT 0)""")
            columns_sql = ",".join(kept)
            connection.execute(f"INSERT INTO period_structure_runs ({columns_sql}) SELECT {columns_sql} FROM period_structure_runs_legacy")
            connection.execute("DROP TABLE period_structure_runs_legacy")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_period_runs_lookup ON period_structure_runs(symbol,timeframe,adjustflag,id DESC)")
        bad_centers = connection.execute("""SELECT COUNT(*) FROM period_pen_centers
            WHERE json_extract(payload, '$.role') IS NOT NULL AND json_extract(payload, '$.role') <> 'hierarchy'""").fetchone()[0]
        bad_movements = connection.execute("""SELECT COUNT(*) FROM period_movements
            WHERE json_extract(payload, '$.role') IS NOT NULL AND json_extract(payload, '$.role') <> 'hierarchy_component'""").fetchone()[0]
        if bad_centers or bad_movements:
            raise RuntimeError(f"迁移后仍存在非正式结构: centers={bad_centers}, movements={bad_movements}")
        connection.commit()
        report["deleted_same_level_movements"] = movement_rows
        report["deleted_same_level_centers"] = center_rows
        return report
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "chant_agent.db")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args()
    result = migrate(args.db, args.execute, args.backup_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
