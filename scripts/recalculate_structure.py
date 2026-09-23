#!/usr/bin/env python3
"""Dry-run or rebuild current structure runs from retained market bars."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.period_structure import (  # noqa: E402
    STRUCTURE_TIMEFRAMES,
    PeriodStructureService,
    calculate_calculator_fingerprint,
)
from app.rules import PERIOD_DEFINITION_VERSION  # noqa: E402


ADJUSTFLAG = "2"
EXPECTED_FINGERPRINT = calculate_calculator_fingerprint(ROOT)


def _ro_connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"file:{quote(str(path.resolve()))}?mode=ro", uri=True,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def _enabled_symbols(connection: sqlite3.Connection) -> tuple[str, ...]:
    return tuple(
        str(row[0]) for row in connection.execute(
            "SELECT symbol FROM stock_pool WHERE enabled=1 ORDER BY sort_order,symbol"
        ).fetchall()
    )


def _bar_count(connection: sqlite3.Connection, symbol: str, timeframe: str) -> int:
    return int(connection.execute(
        "SELECT COUNT(*) FROM market_bars WHERE symbol=? AND timeframe=? AND adjustflag=?",
        (symbol, timeframe, ADJUSTFLAG),
    ).fetchone()[0])


def _active(connection: sqlite3.Connection, symbol: str, timeframe: str) -> dict | None:
    row = connection.execute("""SELECT r.* FROM chan_active_runs a
        JOIN chan_structure_runs r ON r.id=a.run_id
        WHERE a.symbol=? AND a.timeframe=? AND a.adjustflag=?""",
        (symbol, timeframe, ADJUSTFLAG)).fetchone()
    return dict(row) if row else None


def dry_run(path: Path, requested: tuple[str, ...] | None) -> dict:
    connection = _ro_connect(path)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        symbols = requested or _enabled_symbols(connection)
        items = []
        for symbol in symbols:
            for timeframe in STRUCTURE_TIMEFRAMES:
                active = _active(connection, symbol, timeframe)
                items.append({
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "bars": _bar_count(connection, symbol, timeframe),
                    "active_run_id": active.get("id") if active else None,
                    "active_version": active.get("definition_version") if active else None,
                    "active_fingerprint": active.get("calculator_fingerprint") if active else None,
                })
        return {
            "mode": "dry-run",
            "integrity_check": integrity,
            "definition_version": PERIOD_DEFINITION_VERSION,
            "calculator_fingerprint": EXPECTED_FINGERPRINT,
            "symbols": list(symbols),
            "matrix_count": len(items),
            "items": items,
        }
    finally:
        connection.close()


def execute(path: Path, requested: tuple[str, ...] | None) -> dict:
    from app.store import Store

    store = Store(str(path))
    service = PeriodStructureService(store, EXPECTED_FINGERPRINT)
    symbols = requested or tuple(item["symbol"] for item in store.list_stock_pool())
    items = []
    for symbol in symbols:
        for timeframe in STRUCTURE_TIMEFRAMES:
            item = {
                "symbol": symbol,
                "timeframe": timeframe,
                "started_at": datetime.now(timezone.utc).isoformat(),
            }
            try:
                if not store.market_range(symbol, timeframe, ADJUSTFLAG)[0]:
                    raise RuntimeError("行情为空")
                snapshot = service.ensure(symbol, timeframe, ADJUSTFLAG, True)
                item.update({
                    "status": "success",
                    "run_id": snapshot["meta"]["run_id"],
                    "structure_version": snapshot["meta"]["structure_version"],
                    "max_level": snapshot["meta"]["max_level"],
                    "pens": len(snapshot["structure"]["pens"]),
                    "centers": len(snapshot["structure"]["centers"]),
                    "promotion_candidates": len(snapshot["structure"].get("promotion_candidates", [])),
                    "segment_proofs": len(snapshot["structure"].get("segment_proofs", [])),
                })
            except Exception as exc:
                item.update({
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(limit=12),
                })
            item["finished_at"] = datetime.now(timezone.utc).isoformat()
            items.append(item)
    integrity = store.db.execute("PRAGMA integrity_check").fetchone()[0]
    store.db.close()
    return {
        "mode": "execute",
        "integrity_check": integrity,
        "definition_version": PERIOD_DEFINITION_VERSION,
        "calculator_fingerprint": EXPECTED_FINGERPRINT,
        "symbols": list(symbols),
        "matrix_count": len(items),
        "success_count": sum(item["status"] == "success" for item in items),
        "failure_count": sum(item["status"] == "failed" for item in items),
        "items": items,
    }


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "chant_agent.db")
    parser.add_argument("--report", type=Path, default=ROOT / "logs" / "structure-recalc-plan.json")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--symbols", nargs="+", default=None)
    args = parser.parse_args()
    if not args.db.exists():
        parser.error(f"数据库不存在: {args.db}")
    symbols = tuple(args.symbols) if args.symbols else None
    payload = execute(args.db, symbols) if args.execute else dry_run(args.db, symbols)
    _write(args.report, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if payload.get("failure_count") else 0


if __name__ == "__main__":
    raise SystemExit(main())
