#!/usr/bin/env python3
"""Rebuild the production database into the destructive v25 schema.

Dry-run is the default. ``--execute`` requires the service to be stopped, makes
a WAL-consistent backup, copies the eight retained tables into a sidecar,
recomputes all enabled 5/30/d/w/m structures, audits them, and atomically swaps
the sidecar into place. The rollback database is intentionally retained.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.chan_structure import assert_valid_structure  # noqa: E402
from app.period_structure import STRUCTURE_TIMEFRAMES, PeriodStructureService  # noqa: E402
from app.rules import PERIOD_DEFINITION_VERSION  # noqa: E402
from app.store import Store  # noqa: E402


PRESERVED_TABLES = (
    "market_bars",
    "trade_calendar",
    "security_catalog",
    "stock_pool",
    "watchlist_groups",
    "watchlist_group_members",
    "watchlist_section_order",
    "drawing_objects",
)


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _open_holders(path: Path) -> list[str]:
    result = subprocess.run(
        ["lsof", "-t", str(path)], capture_output=True, text=True, check=False,
    )
    return sorted({line.strip() for line in result.stdout.splitlines() if line.strip()})


def _integrity(connection: sqlite3.Connection) -> str:
    return str(connection.execute("PRAGMA integrity_check").fetchone()[0])


def _columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()]


def _primary_key_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    rows = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    return [str(row[1]) for row in sorted(rows, key=lambda row: int(row[5])) if int(row[5]) > 0]


def _table_digest(connection: sqlite3.Connection, table: str, columns: list[str]) -> tuple[int, str]:
    quoted = ",".join(f'"{column}"' for column in columns)
    order = _primary_key_columns(connection, table) or columns
    order_by = ",".join(f'"{column}"' for column in order)
    digest = hashlib.sha256()
    count = 0
    for row in connection.execute(f'SELECT {quoted} FROM "{table}" ORDER BY {order_by}'):
        digest.update(json.dumps(list(row), ensure_ascii=False, separators=(",", ":")).encode())
        digest.update(b"\n")
        count += 1
    return count, digest.hexdigest()


def _consistent_backup(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    source_connection = sqlite3.connect(str(source))
    target_connection = sqlite3.connect(str(target))
    try:
        source_connection.backup(target_connection)
        if _integrity(target_connection) != "ok":
            raise RuntimeError("一致性备份 integrity_check 失败")
    finally:
        target_connection.close()
        source_connection.close()


def _copy_table(source: sqlite3.Connection, target: sqlite3.Connection, table: str) -> dict[str, Any]:
    source_columns = _columns(source, table)
    target_columns = _columns(target, table)
    columns = [column for column in source_columns if column in target_columns]
    if not columns:
        raise RuntimeError(f"{table}: 没有可复制列")
    target.execute(f'DELETE FROM "{table}"')
    quoted = ",".join(f'"{column}"' for column in columns)
    placeholders = ",".join("?" for _ in columns)
    rows = source.execute(f'SELECT {quoted} FROM "{table}"').fetchall()
    if rows:
        target.executemany(
            f'INSERT INTO "{table}" ({quoted}) VALUES ({placeholders})', rows,
        )
    source_count, source_hash = _table_digest(source, table, columns)
    target_count, target_hash = _table_digest(target, table, columns)
    if (source_count, source_hash) != (target_count, target_hash):
        raise RuntimeError(f"{table}: 行数或内容哈希不一致")
    return {
        "table": table,
        "columns": columns,
        "row_count": source_count,
        "content_sha256": source_hash,
    }


def _scope(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    symbols = [str(row[0]) for row in connection.execute(
        "SELECT symbol FROM stock_pool WHERE enabled=1 ORDER BY sort_order,symbol"
    ).fetchall()]
    return [
        {
            "symbol": symbol,
            "timeframe": timeframe,
            "bars": int(connection.execute(
                "SELECT COUNT(*) FROM market_bars WHERE symbol=? AND timeframe=? AND adjustflag='2'",
                (symbol, timeframe),
            ).fetchone()[0]),
        }
        for symbol in symbols for timeframe in STRUCTURE_TIMEFRAMES
    ]


def dry_run(database: Path) -> dict[str, Any]:
    connection = sqlite3.connect(str(database))
    try:
        table_names = {str(row[0]) for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        missing = sorted(set(PRESERVED_TABLES) - table_names)
        return {
            "mode": "dry-run",
            "database": str(database),
            "integrity_check": _integrity(connection),
            "open_holder_pids": _open_holders(database),
            "missing_preserved_tables": missing,
            "preserved_tables": [
                {
                    "table": table,
                    "row_count": connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0],
                }
                for table in PRESERVED_TABLES if table in table_names
            ],
            "recalculation_scope": _scope(connection) if not missing else [],
            "definition_version": PERIOD_DEFINITION_VERSION,
        }
    finally:
        connection.close()


def execute(database: Path) -> dict[str, Any]:
    holders = _open_holders(database)
    if holders:
        raise RuntimeError(f"数据库仍被进程持有，请先停止服务: {','.join(holders)}")
    available = shutil.disk_usage(database.parent).free
    required = max(database.stat().st_size * 3, 512 * 1024 * 1024)
    if available < required:
        raise RuntimeError(f"磁盘空间不足: available={available}, required={required}")

    stamp = _stamp()
    backup = database.parent / "backups" / f"{database.stem}-pre-v25-{stamp}.db"
    rollback = database.with_name(f"{database.stem}-rollback-v24-{stamp}.db")
    sidecar = database.with_name(f".{database.name}.v25-{stamp}.tmp")
    report: dict[str, Any] = {
        "mode": "execute",
        "database": str(database),
        "backup": str(backup),
        "rollback": str(rollback),
        "sidecar": str(sidecar),
        "definition_version": PERIOD_DEFINITION_VERSION,
        "tables": [],
        "runs": [],
    }
    checkpoint = sqlite3.connect(str(database))
    try:
        checkpoint.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        checkpoint.close()
    _consistent_backup(database, backup)
    source = sqlite3.connect(str(backup))
    source.row_factory = sqlite3.Row
    if _integrity(source) != "ok":
        source.close()
        raise RuntimeError("源数据库 integrity_check 失败")
    if sidecar.exists():
        sidecar.unlink()
    target_store = Store(str(sidecar))
    try:
        for table in PRESERVED_TABLES:
            report["tables"].append(_copy_table(source, target_store.db, table))
        target_store.db.commit()
        service = PeriodStructureService(target_store)
        scope = _scope(target_store.db)
        for item in scope:
            if not item["bars"]:
                raise RuntimeError(f"{item['symbol']} {item['timeframe']}: 行情为空")
            snapshot = service.ensure(item["symbol"], item["timeframe"], "2", True)
            assert_valid_structure(snapshot)
            report["runs"].append({
                **item,
                "run_id": snapshot["meta"]["run_id"],
                "structure_version": snapshot["meta"]["structure_version"],
                "max_level": snapshot["meta"]["max_level"],
                "centers": len(snapshot["structure"]["centers"]),
                "movements": len(snapshot["structure"]["movements"]),
                "points": len(snapshot["structure"]["points"]),
            })
        if _integrity(target_store.db) != "ok":
            raise RuntimeError("新数据库 integrity_check 失败")
        foreign_keys = target_store.db.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_keys:
            raise RuntimeError(f"新数据库外键错误: {len(foreign_keys)}")
        target_store.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        target_store.db.commit()
    except Exception:
        target_store.db.close()
        source.close()
        raise
    target_store.db.close()
    source.close()

    for suffix in ("-wal", "-shm"):
        companion = Path(str(sidecar) + suffix)
        if companion.exists():
            companion.unlink()
    os.replace(database, rollback)
    try:
        os.replace(sidecar, database)
    except Exception:
        os.replace(rollback, database)
        raise
    for suffix in ("-wal", "-shm"):
        old_companion = Path(str(database) + suffix)
        if old_companion.exists():
            old_companion.unlink()
    final = sqlite3.connect(str(database))
    try:
        report["final_integrity_check"] = _integrity(final)
        report["active_run_count"] = final.execute("SELECT COUNT(*) FROM chan_active_runs").fetchone()[0]
    finally:
        final.close()
    report["status"] = "success"
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "chant_agent.db")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--report", type=Path, default=ROOT / "logs" / "v25-database-rebuild.json")
    args = parser.parse_args()
    if not args.db.exists():
        parser.error(f"数据库不存在: {args.db}")
    try:
        payload = execute(args.db) if args.execute else dry_run(args.db)
    except Exception as exc:
        payload = {"mode": "execute" if args.execute else "dry-run", "status": "failed",
                   "error": f"{type(exc).__name__}: {exc}"}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("status", "success") != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
