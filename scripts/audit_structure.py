#!/usr/bin/env python3
"""Read-only audit for active Chan structure runs and nested API responses."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from datetime import date
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.chan_structure import validate_structure  # noqa: E402
from app.intraday import period_date_range  # noqa: E402
from app.period_structure import STRUCTURE_TIMEFRAMES, calculate_calculator_fingerprint  # noqa: E402
from app.rules import PERIOD_DEFINITION_VERSION  # noqa: E402


ADJUSTFLAG = "2"
EXPECTED_FINGERPRINT = calculate_calculator_fingerprint(ROOT)
REFERENCE_TIMEFRAMES = {"w", "m"}


def audit_calculation_profile(meta: dict, structure: dict, timeframe: str | None) -> list[str]:
    """Check profile restrictions independently of the generic Chan geometry audit."""
    problems = []
    profile = meta.get("calculation_profile")
    if timeframe in STRUCTURE_TIMEFRAMES:
        expected = "pen_centers_only" if timeframe in REFERENCE_TIMEFRAMES else "full"
        if profile != expected:
            problems.append(f"profile:unexpected:{timeframe}:{profile}")
    if timeframe not in REFERENCE_TIMEFRAMES and profile != "pen_centers_only":
        return problems
    for group in (
        "movements", "movement_revisions", "points", "point_revisions", "relations",
        "promotion_candidates", "promotion_candidate_revisions",
    ):
        if structure.get(group):
            problems.append(f"profile:forbidden_group:{group}")
    if int(meta.get("max_level", 0)) > 1 or int(structure.get("max_level", 0)) > 1:
        problems.append("profile:max_level_above_l1")
    for group in ("levels", "display_center_levels"):
        if any(int(level) != 1 for level in structure.get(group, [])):
            problems.append(f"profile:forbidden_levels:{group}")
    for group in ("centers", "center_revisions", "components"):
        for item in structure.get(group, []):
            identifier = item.get("id", "unknown")
            if int(item.get("level", 0)) != 1:
                problems.append(f"profile:non_l1:{group}:{identifier}")
            if item.get("unit_kind") not in {None, "pen"}:
                problems.append(f"profile:non_pen_units:{group}:{identifier}")
            if (item.get("promotion_confirmed_at") or item.get("child_center_ids")
                    or item.get("child_movement_ids") or item.get("absorbed_into_family_id")
                    or set(item.get("formation_modes", [])) - {"entry_then_earliest_three_unit_core", "directional_core", "origin_overlap"}):
                problems.append(f"profile:promotion_evidence:{group}:{identifier}")
    for group in ("pens", "components", "centers", "center_revisions", "display_centers"):
        for item in structure.get(group, []):
            if (str(item.get("id", "")).startswith("daily-l2:")
                    or item.get("source_timeframe") == "d"
                    or "target_start_date" in item or "target_end_date" in item):
                problems.append(f"profile:overlay_in_native:{group}:{item.get('id', 'unknown')}")
    return problems


def audit_daily_overlay(payload: dict, timeframe: str | None) -> list[str]:
    problems = []
    overlay = payload.get("overlays", {}).get("daily_l2")
    if timeframe not in REFERENCE_TIMEFRAMES:
        return ["overlay:unexpected_target_period"] if overlay is not None else []
    if not isinstance(overlay, dict):
        return ["overlay:missing_daily_l2"]
    status, source, centers = overlay.get("status"), overlay.get("source"), overlay.get("centers", [])
    if status not in {"ready", "stale", "unavailable"}:
        problems.append("overlay:invalid_status")
    if status == "unavailable":
        if source is not None or centers:
            problems.append("overlay:unavailable_contains_data")
        return problems
    if not isinstance(source, dict):
        return [*problems, "overlay:missing_source"]
    market = payload["market"]
    for field, expected in (("timeframe", "d"), ("symbol", market.get("symbol")),
                            ("adjustflag", market.get("adjustflag")),
                            ("definition_version", PERIOD_DEFINITION_VERSION),
                            ("calculator_fingerprint", EXPECTED_FINGERPRINT)):
        if source.get(field) != expected or expected is None:
            problems.append(f"overlay:source_mismatch:{field}")
    if source.get("preview") or source.get("persisted") is False:
        problems.append("overlay:nonformal_source")
    for field in ("market_version", "structure_version"):
        if not isinstance(source.get(field), str) or not source[field]:
            problems.append(f"overlay:missing_source_version:{field}")
    try:
        cutoff = date.fromisoformat(str(source.get("source_cutoff", ""))[:10])
    except ValueError:
        cutoff = None
        problems.append("overlay:invalid_source_cutoff")
    if status == "stale" and not overlay.get("error"):
        problems.append("overlay:stale_without_reason")
    bars = market.get("bars", [])
    try:
        buckets = [(bar["trade_date"], *period_date_range(bar["trade_date"], timeframe)) for bar in bars]
    except (KeyError, ValueError, TypeError):
        return [*problems, "overlay:invalid_target_bars"]
    identifiers = [center.get("id") for center in centers]
    if len(set(identifiers)) != len(identifiers):
        problems.append("overlay:duplicate_ids")
    for center in centers:
        identifier = center.get("id", "unknown")
        prefix = f"overlay:{identifier}:"
        if (not center.get("revision_id") or identifier != f"daily-l2:{center['revision_id']}"
                or not center.get("family_id")):
            problems.append(prefix + "invalid_identity")
        if center.get("level") != 2 or center.get("source_timeframe") != "d":
            problems.append(prefix + "non_daily_l2")
        role = center.get("display_role")
        if role not in {"active", "constituent"} or center.get("active") != (role == "active"):
            problems.append(prefix + "invalid_display_role")
        try:
            zd, zg = float(center["zd"]), float(center["zg"])
            if not math.isfinite(zd) or not math.isfinite(zg) or zd + 1e-9 >= zg:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            problems.append(prefix + "invalid_price_core")
        try:
            start = date.fromisoformat(center["start_date"][:10])
            end = date.fromisoformat(center["end_date"][:10])
            if start > end or cutoff is not None and end > cutoff:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            problems.append(prefix + "invalid_source_dates")
            continue
        covered = [bucket for bucket in buckets if bucket[2] >= start.isoformat() and bucket[1] <= end.isoformat()]
        if not covered:
            problems.append(prefix + "outside_target_page")
            continue
        if (center.get("target_start_date") != covered[0][0]
                or center.get("target_end_date") != covered[-1][0]):
            problems.append(prefix + "invalid_target_mapping")
        if (center.get("clipped_start") != (start.isoformat() < buckets[0][1])
                or center.get("clipped_end") != (end.isoformat() > buckets[-1][2])):
            problems.append(prefix + "invalid_clipping")
    return problems


def connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{quote(str(path.resolve()))}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def enabled_symbols(connection: sqlite3.Connection) -> tuple[str, ...]:
    return tuple(str(row[0]) for row in connection.execute(
        "SELECT symbol FROM stock_pool WHERE enabled=1 ORDER BY sort_order,symbol"
    ).fetchall())


def json_rows(connection: sqlite3.Connection, table: str, run_id: int, column: str) -> list[dict]:
    return [json.loads(row[0]) for row in connection.execute(
        f"SELECT {column} FROM {table} WHERE run_id=? ORDER BY rowid", (run_id,),
    ).fetchall()]


def optional_json_rows(
    connection: sqlite3.Connection, table: str, run_id: int, column: str,
) -> list[dict]:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,),
    ).fetchone()
    return json_rows(connection, table, run_id, column) if exists else []


def audit_run(connection: sqlite3.Connection, symbol: str, timeframe: str) -> dict:
    result = {"symbol": symbol, "timeframe": timeframe, "status": "ok", "problems": []}
    run = connection.execute("""SELECT r.* FROM chan_active_runs a
        JOIN chan_structure_runs r ON r.id=a.run_id
        WHERE a.symbol=? AND a.timeframe=? AND a.adjustflag=?""",
        (symbol, timeframe, ADJUSTFLAG)).fetchone()
    if not run:
        result.update(status="failed", problems=["没有活动结构运行"])
        return result
    run = dict(run)
    result.update(run_id=run["id"], definition_version=run["definition_version"],
                  calculator_fingerprint=run["calculator_fingerprint"])
    problems = result["problems"]
    if run["definition_version"] != PERIOD_DEFINITION_VERSION:
        problems.append(f"版本错误: {run['definition_version']}")
    if run["calculator_fingerprint"] != EXPECTED_FINGERPRINT:
        problems.append("计算器指纹与当前代码不一致")
    if run["status"] != "success":
        problems.append(f"运行状态错误: {run['status']}")

    center_revisions = json_rows(connection, "chan_center_revisions", run["id"], "evidence_json")
    movement_revisions = json_rows(connection, "chan_movement_revisions", run["id"], "evidence_json")
    point_revisions = json_rows(connection, "chan_point_revisions", run["id"], "evidence_json")
    promotion_candidate_revisions = optional_json_rows(
        connection, "chan_promotion_candidates", run["id"], "evidence_json",
    )
    structure = {
        "pens": json_rows(connection, "chan_pens", run["id"], "payload_json"),
        "components": json_rows(connection, "chan_components", run["id"], "evidence_json"),
        "centers": [item for item in center_revisions if item.get("active")],
        "center_revisions": center_revisions,
        "movements": [item for item in movement_revisions if item.get("active", True)],
        "movement_revisions": movement_revisions,
        "points": [item for item in point_revisions if item.get("active", True)],
        "point_revisions": point_revisions,
        "promotion_candidates": [
            item for item in promotion_candidate_revisions if item.get("active")
        ],
        "promotion_candidate_revisions": promotion_candidate_revisions,
        "relations": json_rows(connection, "chan_relations", run["id"], "evidence_json"),
        "issues": json_rows(connection, "chan_issues", run["id"], "evidence_json"),
    }
    problems.extend(validate_structure({"structure": structure}))
    meta = json.loads(run["meta_json"])
    problems.extend(audit_calculation_profile({**meta, "max_level": run["max_level"]}, structure, timeframe))
    for center in center_revisions:
        rows = connection.execute("SELECT unit_kind,unit_id,role,ordinal FROM chan_center_units WHERE run_id=? AND center_revision_id=? ORDER BY role,ordinal", (run["id"], center["id"])).fetchall()
        z_rows = [row for row in rows if row["role"] == "z_wave"]
        if [row["unit_id"] for row in z_rows] != center.get("z_unit_ids", []):
            problems.append(f"normalized_z_units:{center['id']}")
        for row in rows:
            expected_kind = "center_revision" if row["role"] == "child_center" else "movement" if row["role"] == "child_movement" else center["unit_kind"]
            if row["unit_kind"] != expected_kind:
                problems.append(f"normalized_unit_kind:{center['id']}:{row['unit_id']}")
    for family in connection.execute("SELECT id,current_revision_id FROM chan_center_families WHERE run_id=?", (run["id"],)):
        current = [center for center in center_revisions if center["family_id"] == family["id"] and center.get("active")]
        if current and (len(current) != 1 or current[0]["id"] != family["current_revision_id"]):
            problems.append(f"family_active_pointer:{family['id']}")
    foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_keys:
        problems.append(f"外键错误: {len(foreign_keys)}")
    result.update({
        "pens": len(structure["pens"]),
        "centers": len(structure["centers"]),
        "center_revisions": len(center_revisions),
        "movements": len(structure["movements"]),
        "movement_revisions": len(movement_revisions),
        "points": len(structure["points"]),
        "promotion_candidates": len(structure["promotion_candidates"]),
        "max_level": max((int(item["level"]) for item in structure["centers"]), default=0),
    })
    if result["max_level"] != int(run["max_level"]):
        problems.append(f"max_level不一致: {result['max_level']} != {run['max_level']}")
    result["status"] = "failed" if problems else "ok"
    return result


def audit_api_payload(payload: dict, requested_level: int) -> list[str]:
    problems = []
    required = {"meta", "market", "structure", "indicators", "drawings", "pagination"}
    missing = required - set(payload)
    if missing:
        return [f"API缺少分组: {','.join(sorted(missing))}"]
    meta = payload["meta"]
    structure = payload["structure"]
    if meta.get("definition_version") != PERIOD_DEFINITION_VERSION:
        problems.append("API版本错误")
    if meta.get("calculator_fingerprint") != EXPECTED_FINGERPRINT:
        problems.append("API指纹错误")
    if int(meta.get("active_level", 0)) != requested_level:
        problems.append("API展示级别错误")
    if requested_level and any(int(item.get("level", 0)) != requested_level for item in structure.get("centers", [])):
        problems.append("API centers混入其他级别")
    if requested_level and any(int(item.get("level", 0)) != requested_level for item in structure.get("movements", [])):
        problems.append("API movements混入其他级别")
    if "pen_centers" in payload or "buy_sell_points" in payload or "confirmation_center_id" in json.dumps(payload):
        problems.append("API泄漏已删除的旧结构语义")
    timeframe = payload["market"].get("timeframe", meta.get("timeframe"))
    if meta.get("timeframe") and timeframe != meta["timeframe"]:
        problems.append("API行情周期与结构周期不一致")
    if meta.get("symbol") and payload["market"].get("symbol") != meta["symbol"]:
        problems.append("API行情证券与结构证券不一致")
    problems.extend(audit_calculation_profile(meta, structure, timeframe))
    problems.extend(audit_daily_overlay(payload, timeframe))
    return problems


def fetch_api_audit(base_url: str, symbol: str, timeframe: str, level: int) -> list[str]:
    query = urlencode({"timeframe": timeframe, "adjustflag": ADJUSTFLAG,
                       "structure_level": level, "limit": 300})
    try:
        with urlopen(f"{base_url.rstrip('/')}/api/chart-data/{symbol}?{query}", timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return [f"API请求失败: {exc}"]
    return audit_api_payload(payload, level)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "chant_agent.db")
    parser.add_argument("--report", type=Path, default=ROOT / "logs" / "structure-audit.json")
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--api-base-url", default=None)
    args = parser.parse_args()
    connection = connect(args.db)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        symbols = tuple(args.symbols) if args.symbols else enabled_symbols(connection)
        items = [
            audit_run(connection, symbol, timeframe)
            for symbol in symbols for timeframe in STRUCTURE_TIMEFRAMES
        ]
    finally:
        connection.close()
    if args.api_base_url:
        for item in items:
            for level in range(1, max(1, int(item.get("max_level", 0))) + 1):
                item.setdefault("api_problems", []).extend(fetch_api_audit(
                    args.api_base_url, item["symbol"], item["timeframe"], level,
                ))
            if item.get("api_problems"):
                item["problems"].extend(f"api: {value}" for value in item["api_problems"])
                item["status"] = "failed"
    payload = {
        "mode": "read-only-audit",
        "integrity_check": integrity,
        "expected_version": PERIOD_DEFINITION_VERSION,
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
