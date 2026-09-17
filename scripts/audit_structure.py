#!/usr/bin/env python3
"""Read-only audit for active structure snapshots."""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
from urllib.parse import urlencode
from urllib.request import urlopen
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.period_structure import calculate_calculator_fingerprint
from app.rules import PERIOD_DEFINITION_VERSION
from app.hierarchy import movement_confirmation_errors

# Compatibility fallback for databases created before the stock-pool table.
# Production audits resolve the scope from the current enabled pool.
DEFAULT_SYMBOLS = ("1A0001", "399673", "1A0688", "300308")
TIMEFRAMES = ("5", "30", "d", "w", "m")
ADJUSTFLAG = "2"
EXPECTED_VERSION = PERIOD_DEFINITION_VERSION
EXPECTED_FINGERPRINT = calculate_calculator_fingerprint(ROOT)
DOUBLE_RANGE_PREFIX = re.compile(r"R\d+-R\d+-")
ALLOWED_CENTER_RELATIONS = {
    "extension", "newborn_up", "newborn_down",
    "expansion_up", "expansion_down", "parent_child",
}


def connect(path: Path) -> sqlite3.Connection:
    # Read through WAL when it exists; immutable mode would silently ignore
    # uncheckpointed committed pages and could audit an older snapshot.
    base = f"file:{quote(str(path.resolve()))}"
    try:
        connection = sqlite3.connect(f"{base}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        connection = sqlite3.connect(f"{base}?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def enabled_symbols(connection: sqlite3.Connection) -> tuple[str, ...]:
    """Return the current production scope without inventing symbols.

    The fallback is intentionally limited to databases that predate the
    stock-pool migration.  If a pool exists and is empty, the correct scope is
    empty and the audit should report a zero-item matrix.
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


def payloads(connection: sqlite3.Connection, table: str, run_id: int) -> list[dict]:
    rows = connection.execute(
        f"SELECT payload FROM {table} WHERE run_id=? ORDER BY ordinal", (run_id,)
    ).fetchall()
    return [json.loads(row[0]) for row in rows]


def ids(items: list[dict]) -> set[str]:
    return {str(item.get("id")) for item in items if item.get("id")}


def _finite(value: object) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _date(value: object) -> str | None:
    """Return an ISO-like timestamp suitable for deterministic comparisons."""
    if not isinstance(value, str) or not value:
        return None
    try:
        # Dates in snapshots are either YYYY-MM-DD or a full ISO timestamp.
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value


def _group(item: dict) -> tuple[int, int]:
    return int(item.get("continuous_range_id", item.get("range_index", 0)) or 0), int(item.get("sequence_id", 0) or 0)


def _center_relation_is_strict(previous: dict, current: dict, direction: str) -> bool:
    try:
        previous_dd, previous_gg = float(previous["zd"]), float(previous["zg"])
        current_dd, current_gg = float(current["zd"]), float(current["zg"])
    except (KeyError, TypeError, ValueError):
        return False
    if direction == "up":
        return current_dd > previous_gg + 1e-9
    if direction == "down":
        return current_gg < previous_dd - 1e-9
    return False


def _strict_overlap_bounds(items: list[dict]) -> tuple[float, float] | None:
    bounds: list[tuple[float, float]] = []
    for item in items:
        try:
            low = float(item.get("low", min(item["start_price"], item["end_price"])))
            high = float(item.get("high", max(item["start_price"], item["end_price"])))
        except (KeyError, TypeError, ValueError):
            return None
        bounds.append((low, high))
    if not bounds:
        return None
    low = max(item[0] for item in bounds)
    high = min(item[1] for item in bounds)
    return (low, high) if low + 1e-9 < high else None


def audit_api_payload(payload: dict, requested_level: int) -> list[str]:
    """Validate the all-level chart-data contract; requested_level is legacy."""
    problems: list[str] = []
    if payload.get("definition_version") != EXPECTED_VERSION:
        problems.append("API definition_version 不是当前版本")
    if payload.get("calculator_fingerprint") != EXPECTED_FINGERPRINT:
        problems.append("API calculator_fingerprint 与当前代码不一致")
    try:
        active = int(payload.get("active_structure_level", 0))
        max_available = int(payload.get("max_available_center_level", 0))
    except (TypeError, ValueError):
        return ["API active/max level 不是整数"]
    if active != 1:
        problems.append(f"API active_structure_level 错误: 请求 L{requested_level} 得到 L{active}")
    advertised = payload.get("center_levels") or []
    if any(not isinstance(value, int) or value < 1 or value > max_available for value in advertised):
        problems.append("API center_levels 含越界级别")
    try:
        counted_levels = {int(value) for value in (payload.get("center_level_counts") or {}).keys()}
    except (TypeError, ValueError):
        counted_levels = set()
        problems.append("API center_level_counts 含非法级别")
    # level_counts describe the complete snapshot, while this paged response
    # advertises only levels that intersect the current page.
    centers = payload.get("centers") or []
    legacy_centers = payload.get("pen_centers") or []
    if centers != legacy_centers:
        problems.append("API centers 与 pen_centers 不一致")
    timeframe = str(payload.get("timeframe", ""))
    context_centers = centers + (payload.get("context_centers") or [])
    center_by_id = {center["id"]: center for center in context_centers if center.get("id")}
    for movement in payload.get("movements") or []:
        problems.extend(f"API movement:{movement.get('id')}: {error}" for error in movement_confirmation_errors(movement, center_by_id))

    def expected_metadata(level: int) -> tuple[str, str]:
        if timeframe == "d" and level <= 3:
            period = ("d", "w", "m")[level - 1]
            return period, f"period-{period}"
        if timeframe == "w" and level <= 2:
            period = ("w", "m")[level - 1]
            return period, f"period-{period}"
        if timeframe == "m" and level == 1:
            return "m", "period-m"
        if timeframe in {"1", "5", "15", "30", "60", "120"}:
            return timeframe, f"period-{timeframe}" if level == 1 else f"period-{timeframe}-level-L{level}"
        return "higher", f"structure-higher-L{level}"

    for center in centers:
        level = int(center.get("level", 0) or 0)
        if level < 1 or level > max_available:
            problems.append(f"API centers 级别非法: {center.get('id')}")
        if not center.get("display_period") or not center.get("color_key"):
            problems.append(f"API center 缺少 display_period/color_key: {center.get('id')}")
        else:
            display_period, color_key = expected_metadata(level)
            if (center.get("display_period"), center.get("color_key")) != (display_period, color_key):
                problems.append(f"API center 颜色映射错误: {center.get('id')}")
    for movement in payload.get("movements") or []:
        if int(movement.get("level", 0) or 0) < 1 or movement.get("role") != "hierarchy_component":
            problems.append(f"API movements 混入非当前级别/角色: {movement.get('id')}")
    advertised_movements = payload.get("movement_levels") or []
    if any(not isinstance(value, int) or value < 1 or value > max_available for value in advertised_movements):
        problems.append("API movement_levels 含越界级别")
    return problems


def fetch_api_audit(base_url: str, symbol: str, timeframe: str, level: int) -> list[str]:
    query = urlencode({"timeframe": timeframe, "adjustflag": "2", "structure_level": level, "limit": 300})
    url = f"{base_url.rstrip('/')}/api/chart-data/{symbol}?{query}"
    try:
        with urlopen(url, timeout=30) as response:  # noqa: S310 - operator supplied local URL
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # API availability is reported, not fatal to DB audit
        return [f"API 请求失败: {exc}"]
    return audit_api_payload(payload, level)


def audit_run(connection: sqlite3.Connection, symbol: str, timeframe: str) -> dict:
    active = connection.execute(
        """SELECT r.* FROM active_period_structure_runs a
           JOIN period_structure_runs r ON r.id=a.run_id
           WHERE a.symbol=? AND a.timeframe=? AND a.adjustflag=?""",
        (symbol, timeframe, ADJUSTFLAG),
    ).fetchone()
    result = {"symbol": symbol, "timeframe": timeframe, "status": "ok", "problems": []}
    if not active:
        return {**result, "status": "failed", "problems": ["没有活动结构快照"]}
    run = dict(active)
    result.update({
        "run_id": run["id"], "definition_version": run["definition_version"],
        "calculator_fingerprint": str(run.get("calculator_fingerprint") or ""),
    })
    problems = result["problems"]
    if run["definition_version"] != EXPECTED_VERSION:
        problems.append(f"活动版本不是当前版本: {run['definition_version']}")
    if result["calculator_fingerprint"] != EXPECTED_FINGERPRINT:
        problems.append("活动快照 calculator_fingerprint 与当前代码不一致")
    if run["status"] != "success":
        problems.append(f"活动快照状态不是 success: {run['status']}")
    pens = payloads(connection, "period_pens", run["id"])
    centers = payloads(connection, "period_pen_centers", run["id"])
    relations = payloads(connection, "period_center_relations", run["id"])
    movements = payloads(connection, "period_movements", run["id"])
    from app.structure_validation import structure_ownership_errors
    problems.extend(structure_ownership_errors({"pens": pens, "centers": centers, "movements": movements}))
    center_ids = ids(centers)
    for relation in relations:
        if relation.get("relation") not in ALLOWED_CENTER_RELATIONS:
            problems.append(f"relation:{relation.get('id')} 类型未定义: {relation.get('relation')}")
        if relation.get("previous_center_id") not in center_ids or relation.get("current_center_id") not in center_ids:
            problems.append(f"relation:{relation.get('id')} 引用不存在的中枢")
    center_counts = Counter(str(item.get("level", 1)) for item in centers)
    movement_counts = Counter(f"L{item.get('level', 1)}:{item.get('role', 'hierarchy_component')}" for item in movements)
    stored_centers = json.loads(run.get("center_level_counts") or "{}")
    stored_movements = json.loads(run.get("movement_level_counts") or "{}")
    if dict(center_counts) != dict(stored_centers):
        problems.append(f"center_level_counts 不一致: 实际={dict(center_counts)} 存储={stored_centers}")
    if dict(movement_counts) != dict(stored_movements):
        problems.append(f"movement_level_counts 不一致: 实际={dict(movement_counts)} 存储={stored_movements}")
    result.update({
        "center_count": len(centers), "movement_count": len(movements),
        "relation_count": len(relations), "center_level_counts": dict(center_counts),
        "movement_level_counts": dict(movement_counts), "problems": problems,
    })
    result["status"] = "failed" if problems else "ok"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "chant_agent.db")
    parser.add_argument("--report", type=Path,
                        default=ROOT / "logs" / "structure-audit.json")
    parser.add_argument(
        "--symbols", nargs="+", default=None,
        help="显式审计指定标的；缺省按 stock_pool.enabled 解析生产范围",
    )
    parser.add_argument(
        "--api-base-url", default=None,
        help="可选：只读检查运行中的 chart-data API 严格级别过滤",
    )
    args = parser.parse_args()
    connection = connect(args.db)
    try:
        symbols = tuple(args.symbols) if args.symbols else enabled_symbols(connection)
        items = [audit_run(connection, symbol, timeframe)
                 for symbol in symbols for timeframe in TIMEFRAMES]
    finally:
        connection.close()
    if args.api_base_url:
        for item in items:
            symbol, timeframe = item["symbol"], item["timeframe"]
            # Check every level advertised by the snapshot.  A level absent
            # from the snapshot needs no API request.
            run_levels = set(item.get("center_level_counts", {}).keys())
            for raw_level in sorted(run_levels, key=lambda value: int(value)):
                level = int(raw_level)
                item.setdefault("api_problems", []).extend(
                    fetch_api_audit(args.api_base_url, symbol, timeframe, level)
                )
            if item.get("api_problems"):
                item["problems"].extend(f"api: {problem}" for problem in item["api_problems"])
                item["status"] = "failed"
    payload = {
        "mode": "read-only-audit", "expected_version": EXPECTED_VERSION,
        "expected_calculator_fingerprint": EXPECTED_FINGERPRINT,
        "symbols": list(symbols),
        "matrix_count": len(items),
        "success_count": sum(item["status"] == "ok" for item in items),
        "failure_count": sum(item["status"] == "failed" for item in items),
        "items": items,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if payload["failure_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
