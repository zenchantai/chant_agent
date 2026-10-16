#!/usr/bin/env python3
"""Dry-run or execute multi-source market-history reconciliation."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import socket
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.providers import TIMEFRAMES  # noqa: E402
from app.store import Store  # noqa: E402
from app.sync import SyncService  # noqa: E402


DEFAULT_PERIODS = tuple(period for period in TIMEFRAMES if period != "1")


def quick_check(path: Path) -> str:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return str(connection.execute("PRAGMA quick_check").fetchone()[0])
    finally:
        connection.close()


def listener_active(port: int = 8765) -> bool:
    with socket.socket() as client:
        client.settimeout(.2)
        return client.connect_ex(("127.0.0.1", port)) == 0


def snapshot_counts(path: Path, symbols: list[str]) -> dict[str, dict[str, int]]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        result = {}
        for symbol in symbols:
            result[symbol] = {row[0]: int(row[1]) for row in connection.execute(
                "SELECT timeframe,COUNT(*) FROM market_bars WHERE symbol=? GROUP BY timeframe", (symbol,),
            )}
        return result
    finally:
        connection.close()


def selected_symbols(path: Path) -> list[str]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return [row[0] for row in connection.execute(
            "SELECT symbol FROM stock_pool WHERE enabled=1 ORDER BY sort_order,symbol"
        )]
    finally:
        connection.close()


def isolated_copy(path: Path, target: Path) -> None:
    source = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    destination = sqlite3.connect(target)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()


def backup_sqlite_trio(path: Path, root: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = root / f"market-history-{stamp}"
    target.mkdir(parents=True, exist_ok=False)
    for suffix in ("", "-wal", "-shm"):
        source = Path(str(path) + suffix)
        if source.exists():
            shutil.copy2(source, target / source.name)
    return target


def restore_sqlite_trio(path: Path, backup: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        target = Path(str(path) + suffix)
        saved = backup / target.name
        if saved.exists():
            shutil.copy2(saved, target)
        elif target.exists():
            target.unlink()


async def reconcile(store: Store, symbols: list[str], periods: tuple[str, ...]) -> list[dict]:
    service = SyncService(store)
    await service.ensure_trade_calendar()
    results = []
    for symbol in symbols:
        for timeframe in periods:
            outcome = await service.sync_one(symbol, timeframe, "full")
            results.append({"symbol": symbol, "timeframe": timeframe, **outcome})
    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "chant_agent.db")
    parser.add_argument("--symbols", default="", help="Comma-separated symbols; default is every enabled symbol")
    parser.add_argument("--timeframes", default=",".join(DEFAULT_PERIODS))
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--backup-dir", type=Path, default=ROOT / "data" / "backups")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    database = args.db.resolve()
    symbols = [item.strip() for item in args.symbols.split(",") if item.strip()] or selected_symbols(database)
    periods = tuple(item.strip() for item in args.timeframes.split(",") if item.strip())
    invalid = set(periods) - set(DEFAULT_PERIODS)
    if invalid:
        raise SystemExit(f"Unsupported timeframes: {','.join(sorted(invalid))}")
    if quick_check(database) != "ok":
        raise SystemExit("SQLite quick_check failed before reconciliation")
    before = snapshot_counts(database, symbols)
    backup = None
    work_db = database
    temporary = None
    if args.execute:
        if listener_active():
            raise SystemExit("Port 8765 is active; stop the service before --execute")
        usage = shutil.disk_usage(database.parent)
        if usage.free < sum(Path(str(database) + suffix).stat().st_size
                            for suffix in ("", "-wal", "-shm") if Path(str(database) + suffix).exists()) * 3:
            raise SystemExit("Insufficient free disk space for backup and reconciliation")
        backup = backup_sqlite_trio(database, args.backup_dir.resolve())
    else:
        temporary = tempfile.TemporaryDirectory(prefix="chant-market-audit-")
        work_db = Path(temporary.name) / database.name
        isolated_copy(database, work_db)
    store = Store(str(work_db))
    try:
        results = asyncio.run(reconcile(store, symbols, periods))
        store.db.close()
        after = snapshot_counts(work_db, symbols)
        failed = [item for item in results if item.get("result") == "failed"]
        report = {
            "mode": "execute" if args.execute else "dry-run",
            "database": str(database),
            "work_database": str(work_db),
            "symbols": symbols, "timeframes": periods,
            "backup": str(backup) if backup else None,
            "before": before, "after": after, "results": results,
            "quick_check": quick_check(work_db),
            "status": "failed" if failed else "success",
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if failed:
            if args.execute and backup:
                restore_sqlite_trio(database, backup)
            return 1
        return 0
    except BaseException:
        store.db.close()
        if args.execute and backup:
            restore_sqlite_trio(database, backup)
        raise
    finally:
        if temporary:
            temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
