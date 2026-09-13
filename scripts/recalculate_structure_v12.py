#!/usr/bin/env python3
"""Recalculate the approved v12 structure matrix from existing market bars.

The command is deliberately dry-run by default.  Pass ``--execute`` only after
the service has been stopped and a database/WAL/SHM backup has been verified.
Each symbol/timeframe is processed independently so one failed calculation does
not replace the active snapshot for another item.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import traceback
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Kept only as a compatibility fallback for a pre-stock-pool database.  A
# production run resolves its scope from ``stock_pool.enabled`` below.
DEFAULT_SYMBOLS = ("1A0001", "399673", "1A0688", "300308")
TIMEFRAMES = ("5", "30", "d", "w", "m")
ADJUSTFLAG = "2"
EXPECTED_VERSION = "chan-period-center-hierarchy-same-level-color-v12"


def _ro_connect(path: Path) -> sqlite3.Connection:
    # ``mode=ro`` is WAL-aware and does not mutate the database.  Keep an
    # immutable fallback for environments where SQLite cannot open a Unicode
    # path in ordinary read-only mode.
    base = f"file:{quote(str(path.resolve()))}"
    try:
        connection = sqlite3.connect(f"{base}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        connection = sqlite3.connect(f"{base}?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def enabled_symbols(connection: sqlite3.Connection) -> tuple[str, ...]:
    """Resolve the recalculation scope from the current enabled stock pool.

    Older exported databases may not have ``stock_pool`` yet; only in that
    case do we use the historical compatibility list.  An existing but empty
    enabled pool intentionally produces an empty matrix rather than silently
    recalculating unrelated symbols.
    """
    table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='stock_pool'"
    ).fetchone()
    if not table:
        return DEFAULT_SYMBOLS
    rows = connection.execute(
        "SELECT symbol FROM stock_pool WHERE enabled=1 ORDER BY sort_order,symbol"
    ).fetchall()
    return tuple(str(row[0]) for row in rows if row[0])


def _active(connection: sqlite3.Connection, symbol: str, timeframe: str) -> dict | None:
    row = connection.execute(
        """SELECT r.* FROM active_period_structure_runs a
           JOIN period_structure_runs r ON r.id=a.run_id
           WHERE a.symbol=? AND a.timeframe=? AND a.adjustflag=?""",
        (symbol, timeframe, ADJUSTFLAG),
    ).fetchone()
    return dict(row) if row else None


def _bar_count(connection: sqlite3.Connection, symbol: str, timeframe: str) -> int:
    return int(connection.execute(
        "SELECT COUNT(*) FROM market_bars WHERE symbol=? AND timeframe=? AND adjustflag=?",
        (symbol, timeframe, ADJUSTFLAG),
    ).fetchone()[0])


def _json(value: str | None, fallback: object) -> object:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return fallback


def _summarize_result(result: dict) -> dict:
    centers = result.get("centers", result.get("pen_centers", [])) or []
    movements = result.get("movements", []) or []
    center_counts = Counter(str(item.get("level", 1)) for item in centers)
    movement_counts = Counter(
        f"L{item.get('level', 1)}:{item.get('role', 'same_level_decomposition')}"
        for item in movements
    )
    return {
        "run_id": result.get("run_id"),
        "definition_version": result.get("definition_version"),
        "center_count": len(centers),
        "movement_count": len(movements),
        "center_level_counts": dict(sorted(center_counts.items())),
        "movement_level_counts": dict(sorted(movement_counts.items())),
        "max_confirmed_center_level": result.get("max_confirmed_center_level", 0),
        "max_available_center_level": result.get("max_available_center_level", 0),
        "coverage_status": (result.get("coverage") or {}).get("status"),
    }


def _dry_run(db: Path, requested_symbols: tuple[str, ...] | None = None) -> dict:
    connection = _ro_connect(db)
    try:
        symbols = requested_symbols or enabled_symbols(connection)
        items = []
        for symbol in symbols:
            for timeframe in TIMEFRAMES:
                active = _active(connection, symbol, timeframe)
                items.append({
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "adjustflag": ADJUSTFLAG,
                    "bars": _bar_count(connection, symbol, timeframe),
                    "active_run_id": active.get("id") if active else None,
                    "active_definition_version": active.get("definition_version") if active else None,
                    "active_status": active.get("status") if active else None,
                })
        return {
            "mode": "dry-run",
            "expected_version": EXPECTED_VERSION,
            "symbols": list(symbols),
            "matrix_count": len(items),
            "items": items,
        }
    finally:
        connection.close()


def _execute(db: Path, requested_symbols: tuple[str, ...] | None = None) -> dict:
    # Import only in execute mode: a dry-run must not initialize Store or touch
    # SQLite journal state.
    from app.period_structure import PeriodStructureService
    from app.rules import PERIOD_DEFINITION_VERSION
    from app.store import Store

    if PERIOD_DEFINITION_VERSION != EXPECTED_VERSION:
        raise RuntimeError(
            f"代码规则版本为 {PERIOD_DEFINITION_VERSION!r}，期望 {EXPECTED_VERSION!r}"
        )

    store = Store(str(db))
    service = PeriodStructureService(store)
    symbols = requested_symbols
    if symbols is None:
        symbols = tuple(item["symbol"] for item in store.list_stock_pool())
        # ``list_stock_pool`` returns only enabled rows.  Keep the compatibility
        # fallback for databases created before the stock-pool migration.
        if not symbols:
            tables = store.db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='stock_pool'"
            ).fetchone()
            if not tables:
                symbols = DEFAULT_SYMBOLS
    items: list[dict] = []
    for symbol in symbols:
        for timeframe in TIMEFRAMES:
            item = {
                "symbol": symbol,
                "timeframe": timeframe,
                "adjustflag": ADJUSTFLAG,
                "started_at": datetime.now(timezone.utc).isoformat(),
            }
            before = store.active_period_structure_run(symbol, timeframe, ADJUSTFLAG)
            item["old_run_id"] = before.get("id") if before else None
            try:
                result = service.ensure(symbol, timeframe, ADJUSTFLAG, force=True)
                active = store.active_period_structure_run(symbol, timeframe, ADJUSTFLAG)
                item.update(_summarize_result(result))
                item["new_run_id"] = active.get("id") if active else None
                item["active_status"] = active.get("status") if active else None
                item["active_definition_version"] = active.get("definition_version") if active else None
                if not result.get("available"):
                    raise RuntimeError("行情为空或没有可用 continuous_ranges")
                if not active or active.get("status") != "success":
                    raise RuntimeError("新结构快照未成功激活")
                if active.get("definition_version") != EXPECTED_VERSION:
                    raise RuntimeError("活动快照版本不是 v12")
                item["status"] = "success"
            except Exception as exc:  # continue with the remaining matrix items
                try:
                    store.db.rollback()
                except Exception:
                    pass
                item["status"] = "failed"
                item["error"] = str(exc)
                item["traceback"] = traceback.format_exc(limit=12)
            item["finished_at"] = datetime.now(timezone.utc).isoformat()
            items.append(item)
    try:
        store.db.close()
    except Exception:
        pass
    return {
        "mode": "execute",
        "expected_version": EXPECTED_VERSION,
        "symbols": list(symbols),
        "matrix_count": len(items),
        "success_count": sum(item.get("status") == "success" for item in items),
        "failure_count": sum(item.get("status") == "failed" for item in items),
        "items": items,
    }


def _write_report(report: Path, payload: dict) -> None:
    report.parent.mkdir(parents=True, exist_ok=True)
    temporary = report.with_name(f".{report.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, report)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "chant_agent.db")
    parser.add_argument("--report", type=Path,
                        default=ROOT / "logs" / "v12-structure-recalc-plan.json")
    parser.add_argument("--execute", action="store_true",
                        help="执行重算；缺省只读检查 20 项矩阵")
    parser.add_argument("--symbols", nargs="+", default=None,
                        help="显式处理指定标的；缺省使用 stock_pool.enabled")
    args = parser.parse_args()
    if not args.db.exists():
        parser.error(f"数据库不存在: {args.db}")
    symbols = tuple(args.symbols) if args.symbols else None
    payload = _execute(args.db, symbols) if args.execute else _dry_run(args.db, symbols)
    _write_report(args.report, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if payload.get("failure_count", 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
