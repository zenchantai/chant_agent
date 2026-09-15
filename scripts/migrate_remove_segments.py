"""Remove only the retired system-segment table, with backup and preservation checks."""

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path


def snapshot(connection: sqlite3.Connection) -> dict:
    result = {}
    tables = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name <> 'period_segments' ORDER BY name"
    ).fetchall()
    for (name,) in tables:
        quoted = '"' + name.replace('"', '""') + '"'
        columns = connection.execute(f"PRAGMA table_info({quoted})").fetchall()
        ordering = ",".join(str(index + 1) for index in range(len(columns)))
        digest = hashlib.sha256()
        count = 0
        for row in connection.execute(f"SELECT * FROM {quoted} ORDER BY {ordering}"):
            digest.update(repr(row).encode("utf-8"))
            digest.update(b"\n")
            count += 1
        schema = connection.execute(
            "SELECT type,name,sql FROM sqlite_master WHERE tbl_name=? ORDER BY type,name", (name,)
        ).fetchall()
        result[name] = {"count": count, "sha256": digest.hexdigest(), "schema": schema}
    return result


def migrate(db: Path, execute: bool = False, backup_dir: Path | None = None) -> dict:
    db = db.resolve(strict=True)
    connection = sqlite3.connect(f"{db.as_uri()}?mode={'rw' if execute else 'ro'}", uri=True)
    try:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='period_segments'"
        ).fetchone()
        count = connection.execute("SELECT COUNT(*) FROM period_segments").fetchone()[0] if exists else 0
        report = {"db": str(db), "execute": execute, "segment_rows": count, "table_exists": bool(exists)}
        if not execute or not exists:
            return report
        target_dir = backup_dir or db.parent / "backups"
        target_dir.mkdir(parents=True, exist_ok=True)
        backup = target_dir / f"{db.stem}-before-remove-segments-{datetime.now():%Y%m%d-%H%M%S-%f}.db"
        with sqlite3.connect(backup) as destination:
            connection.backup(destination)
        connection.execute("BEGIN IMMEDIATE")
        before = snapshot(connection)
        connection.execute("DROP TABLE period_segments")
        after = snapshot(connection)
        if before != after:
            raise RuntimeError("非线段表发生变化，已拒绝提交迁移")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"数据库完整性检查失败: {integrity}")
        connection.commit()
        report.update(backup=str(backup), deleted_segment_rows=count,
                      preserved_tables=after, other_tables_unchanged=True, integrity_check=integrity)
        return report
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path(__file__).resolve().parents[1] / "data/chant_agent.db")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(migrate(args.db, args.execute, args.backup_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
