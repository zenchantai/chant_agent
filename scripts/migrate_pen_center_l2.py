#!/usr/bin/env python3
"""Safely retire movement/point storage while preserving pen-center evidence.

The command is read-only by default.  ``--execute`` must be run only after the
service and every SQLite writer have stopped.  The database, WAL, and SHM files
are copied together before any schema change is committed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.period_structure import calculate_calculator_fingerprint  # noqa: E402
from app.rules import PERIOD_DEFINITION_VERSION  # noqa: E402


MOVEMENT_TABLES = (
    "chan_movement_centers",
    "chan_movement_units",
    "chan_movement_revisions",
    "chan_movement_families",
)
POINT_TABLES = ("chan_point_revisions", "chan_point_families")
REMOVED_TABLES = (*MOVEMENT_TABLES, *POINT_TABLES)
PAYLOAD_COLUMNS = (
    ("chan_structure_runs", "meta_json"),
    ("chan_components", "evidence_json"),
    ("chan_center_revisions", "evidence_json"),
    ("chan_center_candidates", "evidence_json"),
    ("chan_promotion_candidates", "evidence_json"),
    ("chan_segment_proofs", "evidence_json"),
    ("chan_relations", "evidence_json"),
    ("chan_issues", "evidence_json"),
)
REMOVED_KEYS = {
    "owner_movement_id",
    "child_movement_ids",
    "movement_revision_id",
    "movement_family_id",
    "boundary_certificate_id",
    "point_revision_id",
    "point_family_id",
    "start_point_id",
    "end_point_id",
    "parent_point_id",
}


def connect(path: Path, *, readonly: bool) -> sqlite3.Connection:
    if readonly:
        connection = sqlite3.connect(f"file:{quote(str(path.resolve()))}?mode=ro", uri=True)
    else:
        connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0]) for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }


def pragma_rows(connection: sqlite3.Connection, statement: str) -> list[str]:
    return [str(row[0]) for row in connection.execute(statement).fetchall()]


def table_count(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def snapshot(connection: sqlite3.Connection) -> dict:
    tables = table_names(connection)
    counts = {
        table: table_count(connection, table)
        for table in (
            "market_bars", "drawing_objects", "chan_pens", "chan_center_revisions",
            "chan_promotion_candidates", "chan_segment_proofs", "chan_structure_runs",
            *REMOVED_TABLES,
        )
        if table in tables
    }
    active = [
        dict(row) for row in connection.execute(
            """SELECT a.symbol,a.timeframe,a.adjustflag,a.run_id,r.max_level,
                      r.definition_version,r.calculator_fingerprint
                 FROM chan_active_runs a
                 JOIN chan_structure_runs r ON r.id=a.run_id
                ORDER BY a.symbol,a.timeframe,a.adjustflag"""
        ).fetchall()
    ] if "chan_active_runs" in tables else []
    return {
        "tables": sorted(tables),
        "counts": counts,
        "active_runs": active,
        "quick_check": pragma_rows(connection, "PRAGMA quick_check"),
        "foreign_key_check": pragma_rows(connection, "PRAGMA foreign_key_check"),
    }


def protected_digest(connection: sqlite3.Connection, table: str) -> str:
    digest = hashlib.sha256()
    for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid"):
        digest.update(json.dumps(tuple(row), ensure_ascii=False, default=str).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def preflight(connection: sqlite3.Connection) -> dict:
    report = snapshot(connection)
    if report["quick_check"] != ["ok"] or report["foreign_key_check"]:
        raise RuntimeError("数据库完整性检查失败，禁止迁移")
    fingerprint = calculate_calculator_fingerprint(ROOT)
    if not report["active_runs"]:
        raise RuntimeError("没有活动运行，禁止迁移")
    for row in report["active_runs"]:
        profile = "pen_centers_only" if row["timeframe"] in {"w", "m"} else "pen_centers_l2"
        maximum = 1 if profile == "pen_centers_only" else 2
        if row["timeframe"] not in {"5", "30", "d", "w", "m"}:
            raise RuntimeError(f"未知活动周期: {row['timeframe']}")
        if row["definition_version"] != PERIOD_DEFINITION_VERSION or row["calculator_fingerprint"] != fingerprint:
            raise RuntimeError(f"活动运行未重算或指纹不匹配: {row['run_id']}")
        if int(row["max_level"]) > maximum:
            raise RuntimeError(f"活动运行级别越界: {row['run_id']}")
        meta = connection.execute("SELECT meta_json FROM chan_structure_runs WHERE id=?", (row["run_id"],)).fetchone()
        if json.loads(meta[0] or "{}").get("calculation_profile") != profile:
            raise RuntimeError(f"活动运行模式不匹配: {row['run_id']}")
        levels = [int(item[0]) for item in connection.execute(
            "SELECT DISTINCT level FROM chan_center_revisions WHERE run_id=?", (row["run_id"],)
        )]
        if any(level < 1 or level > maximum for level in levels):
            raise RuntimeError(f"中枢级别越界: {row['run_id']}")
        if connection.execute(
            "SELECT COUNT(*) FROM chan_segment_proofs WHERE run_id=? AND source_kind!='local_pen_group'",
            (row["run_id"],),
        ).fetchone()[0]:
            raise RuntimeError(f"存在非笔原生晋级证据: {row['run_id']}")
    return report


def backup_sqlite_trio(db: Path, backup_dir: Path) -> list[str]:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = backup_dir / f"pen-center-l2-{stamp}"
    target.mkdir(parents=True, exist_ok=False)
    copied: list[str] = []
    for suffix in ("", "-wal", "-shm"):
        source = Path(f"{db}{suffix}")
        if source.exists():
            destination = target / source.name
            shutil.copy2(source, destination)
            copied.append(str(destination))
    if not copied:
        raise RuntimeError(f"未找到数据库文件: {db}")
    return copied


def scrub(value):
    if isinstance(value, dict):
        return {
            key: scrub(item) for key, item in value.items()
            if key not in REMOVED_KEYS
        }
    if isinstance(value, list):
        return [scrub(item) for item in value]
    return value


def rebuild_center_revisions(connection: sqlite3.Connection) -> None:
    connection.execute("""CREATE TABLE chan_center_revisions__new (
        run_id INTEGER NOT NULL, id TEXT NOT NULL, family_id TEXT NOT NULL,
        revision_no INTEGER NOT NULL, previous_revision_id TEXT, level INTEGER NOT NULL,
        status TEXT NOT NULL, active INTEGER NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
        zd REAL NOT NULL, zg REAL NOT NULL, dd REAL NOT NULL, gg REAL NOT NULL,
        entry_direction TEXT, core_formation_pattern TEXT, departure_direction TEXT,
        evidence_json TEXT NOT NULL DEFAULT '{}',
        PRIMARY KEY(run_id,id), UNIQUE(run_id,family_id,revision_no),
        FOREIGN KEY(run_id,family_id) REFERENCES chan_center_families(run_id,id) ON DELETE CASCADE)""")
    connection.execute("""INSERT INTO chan_center_revisions__new
        (run_id,id,family_id,revision_no,previous_revision_id,level,status,active,start_date,end_date,
         zd,zg,dd,gg,entry_direction,core_formation_pattern,departure_direction,evidence_json)
        SELECT run_id,id,family_id,revision_no,previous_revision_id,level,status,active,start_date,end_date,
               zd,zg,dd,gg,entry_direction,core_formation_pattern,departure_direction,evidence_json
          FROM chan_center_revisions""")
    connection.execute("""CREATE TABLE chan_center_units__new (
        run_id INTEGER NOT NULL, center_revision_id TEXT NOT NULL, unit_kind TEXT NOT NULL,
        unit_id TEXT NOT NULL, role TEXT NOT NULL, ordinal INTEGER NOT NULL,
        PRIMARY KEY(run_id,center_revision_id,role,ordinal),
        FOREIGN KEY(run_id,center_revision_id) REFERENCES chan_center_revisions(run_id,id) ON DELETE CASCADE)""")
    connection.execute("""INSERT INTO chan_center_units__new
        (run_id,center_revision_id,unit_kind,unit_id,role,ordinal)
        SELECT run_id,center_revision_id,unit_kind,unit_id,role,ordinal
          FROM chan_center_units
         WHERE unit_kind NOT IN ('movement', 'structural_point')""")
    connection.execute("DROP TABLE chan_center_units")
    connection.execute("DROP TABLE chan_center_revisions")
    connection.execute("ALTER TABLE chan_center_revisions__new RENAME TO chan_center_revisions")
    connection.execute("ALTER TABLE chan_center_units__new RENAME TO chan_center_units")
    connection.execute("""CREATE INDEX IF NOT EXISTS idx_chan_core_owner
        ON chan_center_units(run_id,unit_kind,unit_id) WHERE role='core'""")


def scrub_payloads(connection: sqlite3.Connection) -> int:
    changed = 0
    for table, column in PAYLOAD_COLUMNS:
        if table not in table_names(connection):
            continue
        rows = connection.execute(f"SELECT rowid,{column} FROM {table}").fetchall()
        for row in rows:
            raw = row[column]
            try:
                parsed = json.loads(raw or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            cleaned = scrub(parsed)
            encoded = json.dumps(cleaned, ensure_ascii=False, separators=(",", ":"))
            if encoded != raw:
                connection.execute(
                    f"UPDATE {table} SET {column}=? WHERE rowid=?", (encoded, row["rowid"])
                )
                changed += 1
    return changed


def migrate(connection: sqlite3.Connection) -> dict:
    before = preflight(connection)
    tables = table_names(connection)
    protected = {
        table: protected_digest(connection, table)
        for table in ("market_bars", "drawing_objects") if table in tables
    }
    connection.execute("PRAGMA foreign_keys=OFF")
    connection.execute("BEGIN IMMEDIATE")
    try:
        for table in sorted(tables):
            if not table.startswith("chan_") or table in {"chan_active_runs", "chan_structure_runs"}:
                continue
            columns = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}
            if "run_id" in columns:
                connection.execute(f"DELETE FROM {table} WHERE run_id NOT IN (SELECT run_id FROM chan_active_runs)")
        connection.execute("DELETE FROM chan_structure_runs WHERE id NOT IN (SELECT run_id FROM chan_active_runs)")
        for table in REMOVED_TABLES:
            if table in tables:
                connection.execute(f"DROP TABLE {table}")
        if "chan_center_revisions" in tables and "chan_center_units" in tables:
            rebuild_center_revisions(connection)
        scrubbed_payloads = scrub_payloads(connection)
        after = snapshot(connection)
        if any(protected_digest(connection, table) != digest for table, digest in protected.items()):
            raise RuntimeError("行情或用户绘图发生变化")
        if after["quick_check"] != ["ok"] or after["foreign_key_check"]:
            raise RuntimeError({
                "quick_check": after["quick_check"],
                "foreign_key_check": after["foreign_key_check"],
            })
        connection.commit()
        connection.execute("PRAGMA foreign_keys=ON")
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise RuntimeError("无法重新启用外键约束")
    except Exception:
        connection.rollback()
        connection.execute("PRAGMA foreign_keys=ON")
        raise
    return {"before": before, "after": snapshot(connection), "scrubbed_payloads": scrubbed_payloads}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path, default=ROOT / "data" / "backups")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="只检查，不写入；默认行为")
    mode.add_argument("--execute", action="store_true", help="备份三件套并执行迁移")
    args = parser.parse_args()
    db = args.db.resolve()
    if not db.exists():
        parser.error(f"数据库不存在: {db}")
    if not args.execute:
        with connect(db, readonly=True) as connection:
            print(json.dumps({"mode": "dry-run", "db": str(db), "snapshot": snapshot(connection)}, ensure_ascii=False, indent=2))
        return 0
    copied = backup_sqlite_trio(db, args.backup_dir.resolve())
    with connect(db, readonly=False) as connection:
        result = migrate(connection)
    print(json.dumps({"mode": "execute", "db": str(db), "backup_files": copied, **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
