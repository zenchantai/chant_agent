from __future__ import annotations

import hashlib
import json
from typing import Any


DECOMPOSITION_VERSION = "same-period-center-driven-v1"
PRICE_EPSILON = 1e-9


def _stable_id(values: list[str]) -> str:
    digest = hashlib.sha256("|".join(values).encode()).hexdigest()[:12]
    return f"movement-{digest}"


def _price_relation(previous: dict[str, Any], current: dict[str, Any]) -> str:
    previous_zd = float(previous.get("fixed_zd", previous["zd"]))
    previous_zg = float(previous.get("fixed_zg", previous["zg"]))
    current_zd = float(current.get("fixed_zd", current["zd"]))
    current_zg = float(current.get("fixed_zg", current["zg"]))
    if current_zd - previous_zg > PRICE_EPSILON:
        return "above"
    if previous_zd - current_zg > PRICE_EPSILON:
        return "below"
    if abs(current_zd - previous_zg) <= PRICE_EPSILON or abs(previous_zd - current_zg) <= PRICE_EPSILON:
        return "touching"
    return "overlap"


def _path_points(pens: list[dict[str, Any]], start_boundary: int, end_boundary: int) -> list[dict[str, Any]]:
    selected = pens[start_boundary:end_boundary]
    if not selected:
        return []
    return [
        {"trade_date": selected[0]["start_date"], "price": float(selected[0]["start_price"])},
        *({"trade_date": pen["end_date"], "price": float(pen["end_price"])} for pen in selected),
    ]


def _extreme_endpoint(
    pens: list[dict[str, Any]], start_pen_index: int, end_pen_index: int, direction: str
) -> dict[str, Any]:
    endpoints: list[dict[str, Any]] = []
    for index in range(max(0, start_pen_index), min(len(pens) - 1, end_pen_index) + 1):
        pen = pens[index]
        endpoints.extend((
            {"boundary": index, "trade_date": pen["start_date"], "price": float(pen["start_price"])},
            {"boundary": index + 1, "trade_date": pen["end_date"], "price": float(pen["end_price"])},
        ))
    if not endpoints:
        raise ValueError("走势转折区间没有可用笔端点")
    target = max(item["price"] for item in endpoints) if direction == "up" else min(item["price"] for item in endpoints)
    return next(item for item in endpoints if abs(item["price"] - target) <= PRICE_EPSILON)


def _movement(
    pens: list[dict[str, Any]], centers: list[dict[str, Any]], start_boundary: int,
    end_boundary: int, direction: str, status: str, origin: str,
    confirmation_center: dict[str, Any] | None = None,
    candidate_extreme: dict[str, Any] | None = None,
) -> dict[str, Any]:
    points = _path_points(pens, start_boundary, end_boundary)
    first_center = centers[0]
    movement = {
        "id": _stable_id([
            str(first_center.get("continuous_range_id", first_center.get("range_index", 0))),
            str(first_center.get("sequence_id", 0)), first_center["id"],
            points[0]["trade_date"], f'{points[0]["price"]:.12g}',
        ]),
        "ordinal": 0,
        "kind": "movement",
        "direction": direction,
        "classification": "trend" if len(centers) >= 2 else "consolidation",
        "status": status,
        "start_date": points[0]["trade_date"],
        "start_price": points[0]["price"],
        "end_date": points[-1]["trade_date"],
        "end_price": points[-1]["price"],
        "confirmed_at": confirmation_center.get("confirmed_at") if confirmation_center else None,
        "center_ids": [center["id"] for center in centers],
        "pen_ids": [pen["id"] for pen in pens[start_boundary:end_boundary]],
        "center_count": len(centers),
        "confirmation_center_id": confirmation_center.get("id") if confirmation_center else None,
        "termination_reason": "opposite_center_confirmed" if confirmation_center else "right_edge",
        "continuous_range_id": first_center.get("continuous_range_id", first_center.get("range_index", 0)),
        "sequence_id": first_center.get("sequence_id", 0),
        "origin": origin,
        "path_points": points,
        "start_boundary": start_boundary,
        "end_boundary": end_boundary,
        "evidence": [
            "MOVEMENT-BASE-001",
            *(["MOVEMENT-TREND-001"] if len(centers) >= 2 else []),
            *(["MOVEMENT-BOUNDARY-001", "MOVEMENT-DECOMP-001"] if confirmation_center else ["MOVEMENT-STATUS-001"]),
        ],
    }
    if candidate_extreme:
        movement.update({
            "candidate_extreme_date": candidate_extreme["trade_date"],
            "candidate_extreme_price": candidate_extreme["price"],
        })
    return movement


def build_movements(
    pens: list[dict[str, Any]], centers: list[dict[str, Any]], origin: str = "system"
) -> dict[str, Any]:
    """Build a deterministic same-period decomposition from confirmed pens and L1 centers."""
    confirmed_pens = [dict(pen) for pen in pens if pen.get("status", "confirmed") == "confirmed"]
    confirmed_pens.sort(key=lambda item: (item.get("range_index", 0), item.get("sequence_id", 0), item.get("ordinal", 0)))
    confirmed_centers = [dict(center) for center in centers if center.get("status", "confirmed") == "confirmed"]
    confirmed_centers.sort(key=lambda item: (item.get("range_index", 0), item.get("sequence_id", 0), item.get("start_date", ""), item.get("ordinal", 0)))
    movements: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    unassigned: list[str] = []

    groups: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for pen in confirmed_pens:
        key = (int(pen.get("range_index", 0)), int(pen.get("sequence_id", 0)))
        groups.setdefault(key, []).append(pen)

    for key, group_pens in groups.items():
        pen_index = {pen["id"]: index for index, pen in enumerate(group_pens)}
        group_centers = [center for center in confirmed_centers if (
            int(center.get("range_index", 0)), int(center.get("sequence_id", 0))
        ) == key]
        valid_centers: list[dict[str, Any]] = []
        center_bounds: dict[str, tuple[int, int]] = {}
        stopped = False
        for center in group_centers:
            formation = center.get("formation_pen_ids") or [center.get("entry_pen_id"), *(center.get("core_pen_ids") or [])]
            formation = [item for item in formation if item]
            if len(formation) < 4 or any(item not in pen_index for item in formation):
                issues.append({"code": "invalid_center_reference", "center_id": center.get("id"), "range_index": key[0]})
                stopped = True
                break
            indices = [pen_index[item] for item in formation]
            if indices != list(range(indices[0], indices[0] + len(indices))):
                issues.append({"code": "non_contiguous_center_pens", "center_id": center.get("id"), "range_index": key[0]})
                stopped = True
                break
            source_ids = center.get("source_pen_ids") or center.get("pen_ids") or formation
            if any(item not in pen_index for item in source_ids):
                issues.append({"code": "invalid_center_source", "center_id": center.get("id"), "range_index": key[0]})
                stopped = True
                break
            center_bounds[center["id"]] = (indices[0], max(pen_index[item] for item in source_ids))
            valid_centers.append(center)

        if not valid_centers:
            unassigned.extend(pen["id"] for pen in group_pens)
            continue

        first_center = valid_centers[0]
        start_boundary = center_bounds[first_center["id"]][0]
        unassigned.extend(pen["id"] for pen in group_pens[:start_boundary])
        direction = first_center.get("direction")
        if direction not in {"up", "down"}:
            issues.append({"code": "missing_initial_direction", "center_id": first_center["id"], "range_index": key[0]})
            unassigned.extend(pen["id"] for pen in group_pens[start_boundary:])
            continue

        current_centers = [first_center]
        last_center = first_center
        for center in valid_centers[1:]:
            relation = _price_relation(last_center, center)
            expected = "above" if direction == "up" else "below"
            opposite = "below" if direction == "up" else "above"
            if relation == expected:
                current_centers.append(center)
                last_center = center
                continue
            if relation == opposite:
                previous_end = center_bounds[last_center["id"]][1]
                next_entry = center_bounds[center["id"]][0]
                if next_entry <= previous_end:
                    issues.append({"code": "overlapping_center_pens", "center_id": center["id"], "range_index": key[0]})
                    stopped = True
                    break
                boundary = _extreme_endpoint(group_pens, previous_end + 1, next_entry, direction)
                if boundary["boundary"] <= start_boundary:
                    issues.append({"code": "invalid_movement_boundary", "center_id": center["id"], "range_index": key[0]})
                    stopped = True
                    break
                movements.append(_movement(
                    group_pens, current_centers, start_boundary, boundary["boundary"],
                    direction, "confirmed", origin, confirmation_center=center,
                ))
                start_boundary = boundary["boundary"]
                direction = "down" if direction == "up" else "up"
                current_centers = [center]
                last_center = center
                continue
            issues.append({
                "code": "touching_centers" if relation == "touching" else "overlapping_centers",
                "center_id": center["id"], "previous_center_id": last_center["id"], "range_index": key[0],
            })
            stopped = True
            break

        if current_centers and start_boundary < len(group_pens):
            candidate = _extreme_endpoint(group_pens, start_boundary, len(group_pens) - 1, direction)
            movements.append(_movement(
                group_pens, current_centers, start_boundary, len(group_pens),
                direction, "provisional", origin, candidate_extreme=candidate,
            ))
        if stopped:
            continue

    for ordinal, movement in enumerate(movements):
        movement["ordinal"] = ordinal
    payload = {
        "algorithm_version": DECOMPOSITION_VERSION,
        "anchor_policy": "first_confirmed_center_entry",
        "status": "partial" if issues else ("complete" if movements else "no_center"),
        "issues": issues,
        "unassigned_pen_ids": unassigned,
    }
    movement_input = json.dumps({"pens": confirmed_pens, "centers": confirmed_centers}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["movement_input_hash"] = hashlib.sha256(movement_input.encode()).hexdigest()[:20]
    return {"movements": movements, "decomposition": payload}
