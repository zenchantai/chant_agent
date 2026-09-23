"""Direction evidence uses confirmed units, never the direction of a selected core."""
from __future__ import annotations

from typing import Any

EPSILON = 1e-9


def breakout_context(units: list[dict[str, Any]], start: int = 0) -> dict[str, Any] | None:
    for index in range(start, len(units) - 2):
        a, b, c = units[index:index + 3]
        if any(u.get("status") != "confirmed" for u in (a, b, c)):
            continue
        if a["direction"] != c["direction"] or a["direction"] == b["direction"]:
            continue
        if a["end_date"] != b["start_date"] or b["end_date"] != c["start_date"]:
            continue
        sign = 1 if a["direction"] == "up" else -1
        if sign * (b["end_price"] - a["start_price"]) <= EPSILON or sign * (c["end_price"] - a["end_price"]) <= EPSILON:
            continue
        return {
            "process_direction": a["direction"], "reason": "confirmed_three_unit_breakout",
            "anchor_date": a["start_date"], "anchor_price": a["start_price"],
            "anchor_kind": "model_origin", "source_unit_ids": [u["id"] for u in (a, b, c)],
            "available_at": max(u.get("confirmed_at") or u["end_date"] for u in (a, b, c)),
            "start_index": index, "evidence_end_index": index + 3,
        }
    return None


def select_context(units: list[dict[str, Any]], start: int = 0,
                   evidence: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """Choose the earliest available independent evidence, then structural priority."""
    candidates = [dict(item) for item in evidence or [] if item.get('start_index', start) >= start
                  and item.get('process_direction') in {'up','down'} and item.get('available_at')]
    bootstrap = breakout_context(units,start)
    if bootstrap:
        candidates.append(bootstrap)
    priority = {'confirmed_departure_retest': 0, 'confirmed_three_unit_breakout': 1}
    return min(candidates,key=lambda item:(item['available_at'],priority.get(item.get('reason'),3),item.get('anchor_date',''))) if candidates else None
