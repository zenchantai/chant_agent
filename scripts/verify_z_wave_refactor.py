from __future__ import annotations

import argparse
import gzip
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_structure import audit_run, connect, enabled_symbols, json_rows
from scripts.rebuild_v25_database import PRESERVED_TABLES, _columns, _table_digest
from app.period_structure import STRUCTURE_TIMEFRAMES


def preserved_digest(connection: sqlite3.Connection) -> dict:
    return {table: dict(zip(("rows", "sha256"), _table_digest(connection, table, _columns(connection, table)))) for table in PRESERVED_TABLES}


def daily_evidence(connection: sqlite3.Connection, symbol: str) -> dict:
    run = connection.execute("SELECT run_id FROM chan_active_runs WHERE symbol=? AND timeframe='d' AND adjustflag='2'", (symbol,)).fetchone()
    if not run:
        return {}
    bars = [dict(row) for row in connection.execute("SELECT * FROM market_bars WHERE symbol=? AND timeframe='d' AND adjustflag='2' ORDER BY trade_date", (symbol,))]
    return {
        "symbol": symbol,
        "bars": bars,
        "pens": json_rows(connection, "chan_pens", run[0], "payload_json"),
        "center_revisions": json_rows(connection, "chan_center_revisions", run[0], "evidence_json"),
        "relations": json_rows(connection, "chan_relations", run[0], "evidence_json"),
    }


def pair_report(before: dict, after: dict) -> list[dict]:
    old_centers = {item["id"]: item for item in before["center_revisions"]}
    new_centers = {item["id"]: item for item in after["center_revisions"]}
    old_by_core = {tuple(item["core_unit_ids"]): item for item in old_centers.values() if item["level"] == 1}
    result = []
    for relation in after["relations"]:
        if relation["level"] != 1 or relation["from_id"] not in new_centers or relation["to_id"] not in new_centers:
            continue
        children = [new_centers[relation["from_id"]], new_centers[relation["to_id"]]]
        old = [old_by_core.get(tuple(child["core_unit_ids"])) for child in children]
        old_relation = next((item for item in before["relations"] if all(old) and item["from_id"] == old[0]["id"] and item["to_id"] == old[1]["id"]), None)
        left, right = children
        if relation["relation_type"] == "newborn_up":
            reason = f"后DD={right['dd']} > 前GG={left['gg']} + 1e-9；排除进入/连接后为向上新生"
        elif relation["relation_type"] == "newborn_down":
            reason = f"后GG={right['gg']} < 前DD={left['dd']} - 1e-9；排除进入/连接后为向下新生"
        elif relation["status"] == "confirmed":
            reason = f"Z正宽交集=[{max(left['dd'],right['dd'])},{min(left['gg'],right['gg'])}]；连接、见证和首次证据时间全部通过"
        else:
            reason = f"未提交升级：{relation.get('missing_evidence',[])}；关系状态={relation['status']}"
        result.append({
            "relation": relation,
            "old_relation": old_relation,
            "children": [{key: child.get(key) for key in ("id", "family_id", "core_unit_ids", "z_unit_ids", "entry_unit_ids", "zd", "zg", "dd", "gg", "formed_at", "revision_at")} for child in children],
            "old_children": [{key: child.get(key) for key in ("id", "core_unit_ids", "zd", "zg", "fluctuation_dd", "fluctuation_gg")} if child else None for child in old],
            "reason": reason if all(old) else "时间顺序/首次回试保护改变最早合法核心；" + reason,
        })
    represented = {tuple(child["core_unit_ids"]) for pair in result for child in pair["children"]}
    for core_ids, old in old_by_core.items():
        if core_ids not in represented:
            replacements = [center for center in new_centers.values() if center["level"] == 1 and center["revision_no"] == 1 and set(center["source_pen_ids"]) & set(old["source_pen_ids"])]
            result.append({"old_center": old, "overlapping_new_core_ids": [center["core_unit_ids"] for center in replacements], "reason": "旧核心不再出现在新相邻配对；由时间顺序扫描及已确认边界保护重新计算，不强留旧编号"})
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--fixtures", type=Path)
    args = parser.parse_args()
    with connect(args.source) as source, connect(args.candidate) as candidate:
        before, after = preserved_digest(source), preserved_digest(candidate)
        integrity = candidate.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = [list(row) for row in candidate.execute("PRAGMA foreign_key_check")]
        symbols = enabled_symbols(candidate)
        matrix = [audit_run(candidate, symbol, period) for symbol in symbols for period in STRUCTURE_TIMEFRAMES]
        daily = {symbol: pair_report(daily_evidence(source, symbol), daily_evidence(candidate, symbol)) for symbol in ("1A0688", "1A0001") if daily_evidence(source, symbol)}
        if args.fixtures:
            fixtures = {symbol: daily_evidence(source, symbol) for symbol in ("1A0688", "1A0001") if daily_evidence(source, symbol)}
            args.fixtures.parent.mkdir(parents=True, exist_ok=True)
            args.fixtures.write_bytes(gzip.compress(json.dumps(fixtures, ensure_ascii=False, separators=(",", ":")).encode(), mtime=0))
    passed = before == after and integrity == "ok" and not foreign_keys and all(item["status"] == "ok" for item in matrix)
    report = {"passed": passed, "protected_before": before, "protected_after": after, "integrity_check": integrity, "foreign_key_check": foreign_keys, "matrix": matrix, "daily_pairs": daily}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({"passed": passed, "matrix": len(matrix), "report": str(args.report), "pair_counts": {symbol: len(rows) for symbol, rows in daily.items()}}, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
