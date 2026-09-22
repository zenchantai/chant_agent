#!/usr/bin/env python3
"""Replay every confirmed-pen prefix and compare it with the full v30 result."""
from __future__ import annotations

import argparse
import json
import sys
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.chan_structure import build_structure_hierarchy  # noqa: E402
from app.period_structure import STRUCTURE_TIMEFRAMES, calculation_profile  # noqa: E402
from app.store import Store  # noqa: E402


GROUPS = (
    "promotion_candidate_revisions",
    "segment_proof_revisions",
    "center_revisions",
)
FIELDS = {
    "promotion_candidate_revisions": (
        "family_id", "revision_no", "previous_revision_id", "candidate_source",
        "child_level", "parent_level", "status", "required_unit_ids",
        "search_unit_ids", "observed_at", "evidence_available_at",
        "selected_segment_proof_ids", "selected_parent_proof_id",
        "missing_evidence",
    ),
    "segment_proof_revisions": (
        "family_id", "revision_no", "previous_revision_id", "level", "source_kind",
        "status", "direction", "start_date", "end_date", "start_price",
        "end_price", "low", "high", "source_unit_ids", "source_pen_ids",
        "center_witnesses", "boundary_mode", "completion_evidence_id",
        "observed_at", "evidence_available_at", "recursive_eligible",
    ),
    "center_revisions": (
        "family_id", "revision_no", "previous_revision_id", "level", "status",
        "boundary_status", "start_date", "end_date", "zd", "zg", "fixed_zd",
        "fixed_zg", "core_unit_ids", "child_segment_ids", "unit_kind",
        "source_pen_ids", "formed_at", "promotion_confirmed_at", "revision_at",
        "recursive_eligible",
    ),
}


def _signature(group: str, item: dict[str, Any]) -> str:
    return json.dumps(
        {field: item.get(field) for field in FIELDS[group]},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )


def _items(structure: dict[str, Any], group: str) -> list[dict[str, Any]]:
    values = structure.get(group, [])
    if group == "center_revisions":
        return [item for item in values if int(item.get("level", 0)) >= 2]
    return values


def _event_time(group: str, item: dict[str, Any]) -> str:
    if group == "promotion_candidate_revisions":
        return str(item.get("observed_at", ""))
    if group == "segment_proof_revisions":
        return str(item.get("observed_at") or item.get("evidence_available_at") or "")
    return str(item.get("revision_at") or item.get("formed_at") or "")


def _source_ids(group: str, item: dict[str, Any]) -> set[str]:
    if group == "promotion_candidate_revisions":
        return set(item.get("search_unit_ids", []))
    return set(item.get("source_pen_ids", []))


def replay_run(store: Store, symbol: str, timeframe: str) -> dict[str, Any]:
    active = store.active_chan_run(symbol, timeframe, "2")
    if not active:
        raise ValueError(f"missing active run: {symbol}/{timeframe}")
    persisted = store.load_chan_structure(int(active["id"]))
    pens = sorted(
        persisted["structure"].get("pens", []),
        key=lambda item: (int(item.get("ordinal", 0)), item["id"]),
    )
    full = build_structure_hierarchy(deepcopy(pens), calculation_profile="full")
    full_maps = {
        group: {item["id"]: item for item in _items(full, group)}
        for group in GROUPS
    }
    problems: list[dict[str, Any]] = []
    started = time.perf_counter()
    checked_prefixes = 0
    for count in range(3, len(pens) + 1):
        prefix = pens[:count]
        prefix_ids = {pen["id"] for pen in prefix}
        cutoff = max(
            str(pen.get("confirmed_at") or pen["end_date"])
            for pen in prefix
        )
        current = build_structure_hierarchy(
            deepcopy(prefix), calculation_profile="full",
        )
        checked_prefixes += 1
        for group in GROUPS:
            current_map = {item["id"]: item for item in _items(current, group)}
            full_map = full_maps[group]
            for identifier, item in current_map.items():
                full_item = full_map.get(identifier)
                if full_item is None:
                    problems.append({
                        "prefix_count": count, "cutoff": cutoff, "group": group,
                        "id": identifier, "problem": "missing_from_full_result",
                    })
                elif _signature(group, item) != _signature(group, full_item):
                    problems.append({
                        "prefix_count": count, "cutoff": cutoff, "group": group,
                        "id": identifier, "problem": "semantic_mismatch",
                    })
            expected = {
                identifier for identifier, item in full_map.items()
                if _event_time(group, item) <= cutoff
                and _source_ids(group, item) <= prefix_ids
            }
            for identifier in sorted(expected - set(current_map)):
                problems.append({
                    "prefix_count": count, "cutoff": cutoff, "group": group,
                    "id": identifier, "problem": "missing_from_prefix_result",
                })
        if len(problems) >= 100:
            break
    elapsed = time.perf_counter() - started
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "run_id": int(active["id"]),
        "pen_count": len(pens),
        "checked_prefixes": checked_prefixes,
        "elapsed_seconds": round(elapsed, 3),
        "problem_count": len(problems),
        "problems": problems[:100],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db", type=Path,
        default=ROOT / "data" / "v30-candidate-20260920" / "v30-staging.db",
    )
    parser.add_argument("--report", type=Path, default=ROOT / "logs" / "v30-replay.json")
    parser.add_argument("--symbols", nargs="*")
    args = parser.parse_args()
    store = Store(str(args.db))
    try:
        symbols = args.symbols or [
            item["symbol"] for item in store.list_stock_pool() if item.get("enabled")
        ]
        items = [
            replay_run(store, symbol, timeframe)
            for symbol in symbols
            for timeframe in STRUCTURE_TIMEFRAMES
            if calculation_profile(timeframe) == "full"
        ]
    finally:
        store.db.close()
    report = {
        "mode": "confirmed-pen-prefix-replay",
        "database": str(args.db.resolve()),
        "symbols": symbols,
        "run_count": len(items),
        "prefix_count": sum(item["checked_prefixes"] for item in items),
        "failure_count": sum(item["problem_count"] for item in items),
        "items": items,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["failure_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
