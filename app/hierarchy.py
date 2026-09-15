from __future__ import annotations

import hashlib
from typing import Any


HIERARCHY_VERSION = "center-hierarchy-cache-fingerprint-v18-reverse-center-movement"
MAX_CENTER_LEVEL = 8
EPSILON = 1e-9


def _stable_id(prefix: str, values: list[str]) -> str:
    digest = hashlib.sha256("|".join(values).encode()).hexdigest()[:12]
    return f"{prefix}-{digest}"


def _latest_timestamp(*values: Any) -> str | None:
    """Return the latest non-empty ISO-like timestamp deterministically.

    Structure timestamps are emitted in lexicographically sortable ISO form.
    Keeping this helper local avoids treating a confirmation timestamp from a
    short center as earlier than the final unit consumed by an extension.
    """
    candidates = [value for value in values if isinstance(value, str) and value]
    return max(candidates) if candidates else None


def _bounds(unit: dict[str, Any]) -> tuple[float, float]:
    if unit.get("low") is not None and unit.get("high") is not None:
        return float(unit["low"]), float(unit["high"])
    start, end = float(unit["start_price"]), float(unit["end_price"])
    return min(start, end), max(start, end)


def _direction(unit: dict[str, Any]) -> str:
    direction = unit.get("direction")
    if direction in {"up", "down"}:
        return direction
    return "up" if float(unit["end_price"]) > float(unit["start_price"]) else "down"


def _strict_overlap(units: list[dict[str, Any]]) -> tuple[float, float] | None:
    if not units:
        return None
    bounds = [_bounds(unit) for unit in units]
    zd = max(low for low, _ in bounds)
    zg = min(high for _, high in bounds)
    return (zd, zg) if zd + EPSILON < zg else None


def _alternating(units: list[dict[str, Any]]) -> bool:
    directions = [_direction(unit) for unit in units]
    return all(left != right for left, right in zip(directions, directions[1:]))


def _group_key(item: dict[str, Any]) -> tuple[int, int]:
    return int(item.get("continuous_range_id", item.get("range_index", 0))), int(item.get("sequence_id", 0))


def _source_pen_ids(units: list[dict[str, Any]]) -> list[str]:
    result: list[str] = []
    for unit in units:
        ids = unit.get("source_pen_ids") or unit.get("pen_ids") or ([unit["id"]] if unit.get("kind") != "movement" else [])
        for pen_id in ids:
            if pen_id not in result:
                result.append(pen_id)
    return result


def atomic_pen_units(pens: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{
        "id": pen["id"], "kind": "atomic_pen", "level": 0,
        "start_date": pen["start_date"], "end_date": pen["end_date"],
        "start_price": float(pen["start_price"]), "end_price": float(pen["end_price"]),
        "low": min(float(pen["start_price"]), float(pen["end_price"])),
        "high": max(float(pen["start_price"]), float(pen["end_price"])),
        "direction": _direction(pen), "status": pen.get("status", "confirmed"),
        "confirmed_at": pen.get("confirmed_at"), "source_pen_ids": [pen["id"]],
        "continuous_range_id": int(pen.get("range_index", 0)),
        "sequence_id": int(pen.get("sequence_id", 0)),
    } for pen in pens if pen.get("status", "confirmed") == "confirmed"]


def classify_center_relation(previous: dict[str, Any], current: dict[str, Any]) -> str:
    previous_zd, previous_zg = float(previous["zd"]), float(previous["zg"])
    current_zd, current_zg = float(current["zd"]), float(current["zg"])
    previous_dd, previous_gg = float(previous["dd"]), float(previous["gg"])
    current_dd, current_gg = float(current["dd"]), float(current["gg"])
    overlap_low = max(previous_zd, current_zd)
    overlap_high = min(previous_zg, current_zg)
    # A single-point contact is not a strict separation and must not be
    # mistaken for an extension or a newborn center.
    if abs(overlap_low - overlap_high) <= EPSILON:
        return "touching"
    if overlap_low + EPSILON < overlap_high:
        return "extension"
    if current_zd > previous_zg + EPSILON:
        return "newborn_up" if current_dd > previous_gg + EPSILON else "expansion_up"
    if current_zg < previous_zd - EPSILON:
        return "newborn_down" if current_gg < previous_dd - EPSILON else "expansion_down"
    return "extension"


def _center_relation_row(previous: dict[str, Any], current: dict[str, Any], relation: str) -> dict[str, Any]:
    return {
        "id": _stable_id(f"center-relation-L{previous['level']}", [previous["id"], current["id"], relation]),
        "kind": "center_relation", "level": previous["level"], "relation": relation,
        "previous_center_id": previous["id"], "current_center_id": current["id"],
        "previous_core": [previous["zd"], previous["zg"]], "current_core": [current["zd"], current["zg"]],
        "previous_envelope": [previous["dd"], previous["gg"]], "current_envelope": [current["dd"], current["gg"]],
        "start_date": previous["start_date"], "end_date": current["end_date"],
        "confirmed_at": current.get("confirmed_at"),
        "continuous_range_id": current.get("continuous_range_id", 0),
        "sequence_id": current.get("sequence_id", 0),
        "evidence": ["CENTER-RELATION-HIERARCHY-001"],
    }


def build_center_relations(centers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    relations: list[dict[str, Any]] = []
    grouped: dict[tuple[int, int, int], list[dict[str, Any]]] = {}
    for center in centers:
        if center.get("role", "hierarchy") != "hierarchy" or center.get("status") != "confirmed":
            continue
        grouped.setdefault((*_group_key(center), int(center["level"])), []).append(center)
    for items in grouped.values():
        items.sort(key=lambda item: (item["start_date"], item.get("level_ordinal", 0)))
        for previous, current in zip(items, items[1:]):
            relation = classify_center_relation(previous, current)
            # A single-point contact is an invalid/ambiguous boundary and is
            # not persisted as a center relationship.
            if relation == "touching":
                continue
            relations.append(_center_relation_row(previous, current, relation))
    for ordinal, relation in enumerate(relations):
        relation["ordinal"] = ordinal
    return relations


def _endpoint_units(units: list[dict[str, Any]], start: int, end: int) -> list[dict[str, Any]]:
    selected = units[start:end]
    if not selected:
        return []
    return [{"trade_date": selected[0]["start_date"], "price": float(selected[0]["start_price"]), "boundary": start, "unit_id": selected[0].get("id")}, *(
        {"trade_date": unit["end_date"], "price": float(unit["end_price"]), "boundary": start + index + 1, "unit_id": unit.get("id")}
        for index, unit in enumerate(selected)
    )]


def _extreme(points: list[dict[str, Any]], direction: str) -> dict[str, Any]:
    target = max(point["price"] for point in points) if direction == "up" else min(point["price"] for point in points)
    return next(point for point in points if abs(point["price"] - target) <= EPSILON)


def _opposite(direction: str) -> str:
    return "down" if direction == "up" else "up"


def _boundary_matches_direction(start_price: float, end_price: float, direction: str) -> bool:
    if direction == "up":
        return end_price + EPSILON >= start_price
    return end_price <= start_price + EPSILON


def _units_are_contiguous(
    previous: dict[str, Any], current: dict[str, Any], *, require_alternating: bool = True,
) -> bool:
    """Whether two lower-level units can belong to one structural stream.

    A market ``sequence_id`` only guarantees that the source pen stream was
    continuous.  A hierarchy component may still leave an unassigned tail
    between two confirmed units.  Higher-level hierarchy must not bridge
    that hole, nor combine two units which point in the same direction.
    """
    if previous.get("end_date") != current.get("start_date"):
        return False
    try:
        if abs(float(previous["end_price"]) - float(current["start_price"])) > EPSILON:
            return False
    except (KeyError, TypeError, ValueError):
        return False
    return not require_alternating or _direction(previous) != _direction(current)


def _structural_sequence_id(sequence_id: int, segment_index: int) -> int:
    """Return a collision-resistant ID for a contiguous derived segment.

    The first segment keeps the source sequence ID.  Later segments use a
    negative namespace so they cannot collide with another source sequence in
    the same range (e.g. source sequence 0 segment 1 must not become sequence
    1).  Source units themselves are copied rather than mutated.
    """
    if segment_index <= 0:
        return int(sequence_id)
    sequence = int(sequence_id)
    # Encode the sign as part of the magnitude so re-segmenting an already
    # derived (negative) sequence remains injective and deterministic.
    sequence_code = abs(sequence) * 2 + (1 if sequence < 0 else 0)
    return -((sequence_code + 1) * 1_000_000 + int(segment_index))


def _split_contiguous_units(
    units: list[dict[str, Any]],
) -> list[tuple[int, list[dict[str, Any]], dict[str, Any] | None]]:
    """Split a confirmed hierarchy stream at real endpoint or direction gaps."""
    if not units:
        return []
    ordered = sorted(
        (dict(unit) for unit in units),
        key=lambda item: (item.get("start_date", ""), item.get("end_date", ""), item.get("id", "")),
    )
    source_sequence = int(_group_key(ordered[0])[1])
    require_alternating = any(unit.get("kind") == "movement" for unit in ordered)
    segments: list[tuple[int, list[dict[str, Any]], dict[str, Any] | None]] = []
    current: list[dict[str, Any]] = []
    segment_index = 0
    break_info: dict[str, Any] | None = None
    for original in ordered:
        if current and not _units_are_contiguous(
            current[-1], original, require_alternating=require_alternating
        ):
            segments.append((segment_index, current, break_info))
            segment_index += 1
            break_info = {
                "previous_unit_id": current[-1].get("id"),
                "current_unit_id": original.get("id"),
            }
            current = []
        copied = dict(original)
        copied["source_sequence_id"] = source_sequence
        copied["sequence_id"] = _structural_sequence_id(source_sequence, segment_index)
        copied["structure_segment_index"] = segment_index
        current.append(copied)
    if current:
        segments.append((segment_index, current, break_info))
    return segments


def _cores_separated(previous: dict[str, Any], current: dict[str, Any]) -> bool:
    return (
        float(current["zd"]) > float(previous["zg"]) + EPSILON
        or float(current["zg"]) < float(previous["zd"]) - EPSILON
    )


def is_reverse_confirmation(previous: dict[str, Any], current: dict[str, Any], direction: str) -> bool:
    return (
        current.get("status") == "confirmed"
        and bool(current.get("confirmed_at"))
        and current.get("direction") == _opposite(direction)
        and int(previous.get("level", 1)) == int(current.get("level", 1))
        and _group_key(previous) == _group_key(current)
        and current.get("id") != previous.get("id")
        and _cores_separated(previous, current)
    )


def _movement_classification(centers: list[dict[str, Any]], direction: str) -> str:
    if len(centers) < 2:
        return "consolidation"
    return "trend" if all(
        previous.get("direction") == current.get("direction") == direction
        and (
            float(current["zd"]) > float(previous["zg"]) + EPSILON if direction == "up"
            else float(current["zg"]) < float(previous["zd"]) - EPSILON
        )
        for previous, current in zip(centers, centers[1:])
    ) else "consolidation"


def movement_confirmation_errors(movement: dict[str, Any], centers: dict[str, dict[str, Any]]) -> list[str]:
    if movement.get("status") != "confirmed":
        return ["未完成走势含确认信息"] if movement.get("confirmed_at") or movement.get("confirmation_center_id") else []
    referenced = movement.get("center_ids") or []
    previous = centers.get(referenced[-1]) if referenced else None
    confirmation = centers.get(movement.get("confirmation_center_id"))
    if not previous or not confirmation or any(center_id not in centers for center_id in referenced):
        return ["已确认走势缺少真实中枢引用"]
    if not is_reverse_confirmation(previous, confirmation, movement.get("direction")):
        return ["确认中枢不是同级反向独立中枢"]
    if (
        int(movement.get("level", 1)) != int(confirmation.get("level", 1))
        or movement.get("termination_reason") != "reverse_independent_center"
        or movement.get("confirmed_at") != confirmation.get("confirmed_at")
        or str(movement.get("confirmed_at") or "") < str(movement.get("end_date") or "")
    ):
        return ["走势确认时间、级别或结束原因不一致"]
    return []


def _movement_from_centers(
    units: list[dict[str, Any]], centers: list[dict[str, Any]], level: int,
    start_boundary: int, end_boundary: int, direction: str, status: str,
    confirmation_center: dict[str, Any] | None, role: str, origin: str,
    source_end_boundary: int | None = None,
    candidate_extreme: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source_end = source_end_boundary if source_end_boundary is not None else end_boundary
    points = _endpoint_units(units, start_boundary, source_end)
    if not points:
        raise ValueError("走势边界没有可用低级运动")
    selected_units = units[start_boundary:source_end]
    start_point = points[0]
    candidate = candidate_extreme or _extreme(points, direction)
    endpoint = _endpoint_units(units, start_boundary, end_boundary)[-1] if end_boundary > start_boundary else start_point
    issues = []
    if status == "confirmed" and not _boundary_matches_direction(start_point["price"], endpoint["price"], direction):
        issues.append({"code": "movement_direction_endpoint_mismatch", "hinted_direction": direction,
                       "start_price": start_point["price"], "end_price": endpoint["price"]})
        status = "provisional"
    if status == "confirmed" and (
        not confirmation_center
        or not is_reverse_confirmation(centers[-1], confirmation_center, direction)
        or any(unit.get("status", "confirmed") != "confirmed" for unit in selected_units)
        or str(confirmation_center["confirmed_at"]) < str(_latest_timestamp(
            *(unit.get("confirmed_at") for unit in selected_units),
            *(unit.get("end_date") for unit in selected_units),
            *(center.get("confirmed_at") for center in centers),
        ) or "")
    ):
        issues.append({"code": "missing_reverse_confirmation"})
        status = "provisional"
    if status != "confirmed":
        confirmation_center = None
        endpoint = candidate
    center_ids = [center["id"] for center in centers]
    movement_id = _stable_id(f"movement-L{level}", [
        role, str(_group_key(centers[0])[0]), str(_group_key(centers[0])[1]), center_ids[0],
        start_point["trade_date"], f'{start_point["price"]:.12g}',
    ])
    movement = {
        "id": movement_id, "kind": "movement", "role": role, "level": level, "state": "formed",
        "direction": direction, "classification": _movement_classification(centers, direction),
        "status": status, "start_date": start_point["trade_date"], "start_price": start_point["price"],
        "end_date": endpoint["trade_date"], "end_price": endpoint["price"],
        "low": min(point["price"] for point in points), "high": max(point["price"] for point in points),
        "confirmed_at": confirmation_center["confirmed_at"] if confirmation_center else None,
        "center_ids": center_ids, "center_count": len(centers),
        "confirmation_center_id": confirmation_center["id"] if confirmation_center else None,
        "child_movement_ids": [unit["id"] for unit in selected_units if unit.get("kind") == "movement"],
        "source_unit_ids": [unit["id"] for unit in selected_units], "source_pen_ids": _source_pen_ids(selected_units),
        "continuous_range_id": _group_key(centers[0])[0], "sequence_id": _group_key(centers[0])[1],
        "origin": origin, "termination_reason": "reverse_independent_center" if confirmation_center else "provisional_tail",
        "start_boundary": start_boundary, "end_boundary": int(endpoint["boundary"]),
        "source_end_boundary": source_end,
        "boundary_source_unit_id": endpoint.get("unit_id"), "boundary_source_price": float(endpoint["price"]),
        "endpoint_points": [start_point, endpoint], "path_points": [start_point, endpoint],
        "candidate_extreme_date": candidate["trade_date"], "candidate_extreme_price": candidate["price"],
        "tail_end_date": points[-1]["trade_date"], "tail_end_price": points[-1]["price"],
        "evidence": ["MOVEMENT-BOUNDARY-001", "MOVEMENT-SHARED-ENDPOINT-001"],
    }
    if issues:
        movement["issues"] = issues
    return movement


def _center_unit_span(
    center: dict[str, Any], units: list[dict[str, Any]], unit_index: dict[str, int],
) -> tuple[int, int] | None:
    """Locate a structural center on the units used at its level.

    L1 centers reference pens while higher-level centers reference child
    movements.  Prefer IDs so extensions and range boundaries remain stable;
    dates are only a defensive fallback for hand-authored corrections.
    """
    # Source pen IDs are evidence on higher-level centers, not units at that
    # level. Requiring them here would incorrectly invalidate every L2+ center.
    movement_units = any(unit.get("kind") == "movement" for unit in units)
    fields = (
        ("core_unit_ids", "child_movement_ids", "source_unit_ids")
        if movement_units else
        ("entry_pen_id", "core_unit_ids", "child_movement_ids", "source_unit_ids",
         "source_pen_ids", "formation_pen_ids", "pen_ids")
    )
    referenced: list[str] = []
    for field in fields:
        value = center.get(field)
        values = value if isinstance(value, list) else [value] if value else []
        referenced.extend(str(item) for item in values)
    # An explicit reference is authoritative. If any referenced unit belongs
    # to another sequence/range (or no longer exists), fail the span instead
    # of silently falling back to a date-based approximation.
    if referenced and any(item not in unit_index for item in referenced):
        return None
    indexes = sorted({unit_index[item] for item in referenced if item in unit_index})
    if indexes:
        if indexes != list(range(indexes[0], indexes[-1] + 1)):
            return None
        return indexes[0], indexes[-1] + 1
    start_date, end_date = center.get("start_date"), center.get("end_date")
    if start_date is None or end_date is None:
        return None
    matching = [
        index for index, unit in enumerate(units)
        if unit.get("end_date", "") >= start_date and unit.get("start_date", "") <= end_date
    ]
    return (min(matching), max(matching) + 1) if matching else None


def build_hierarchy_components(
    units: list[dict[str, Any]], structural_centers: list[dict[str, Any]], level: int,
    role: str = "hierarchy_component", origin: str = "system",
) -> dict[str, Any]:
    movements: list[dict[str, Any]] = []
    used_centers: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    grouped: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for unit in units:
        grouped.setdefault(_group_key(unit), []).append(unit)
    global_end = max((unit.get("end_date", "") for unit in units), default="")
    for key, group in sorted(grouped.items()):
        group.sort(key=lambda unit: (unit["start_date"], unit["end_date"], unit["id"]))
        segments = []
        segment = []
        for unit in group:
            if unit.get("status", "confirmed") != "confirmed":
                if segment:
                    segments.append(segment)
                segment = []
                continue
            if segment and not _units_are_contiguous(segment[-1], unit):
                segments.append(segment)
                segment = []
            segment.append(unit)
        if segment:
            segments.append(segment)
        for segment in segments:
            unit_index = {unit["id"]: index for index, unit in enumerate(segment)}
            centers = []
            for center in structural_centers:
                if center.get("status") != "confirmed" or _group_key(center) != key:
                    continue
                span = _center_unit_span(center, segment, unit_index)
                if span is not None:
                    centers.append(dict(center, unit_start_index=span[0], unit_end_index=span[1]))
            centers.sort(key=lambda center: (center["unit_start_index"], center["id"]))
            if not centers:
                continue
            valid = [centers[0]]
            for center in centers[1:]:
                if center["unit_start_index"] < valid[-1]["unit_end_index"]:
                    issues.append({"code": "overlapping_hierarchy_center_spans", "center_id": center["id"]})
                    break
                valid.append(center)
            centers = valid
            used_centers.extend(centers)
            current = [centers[0]]
            direction = _direction(current[0])
            start_boundary = current[0]["unit_start_index"]
            confirmation_floor = start_boundary
            for center in centers[1:]:
                if not is_reverse_confirmation(current[-1], center, direction):
                    current.append(center)
                    continue
                next_start = center["unit_start_index"]
                search = _endpoint_units(segment, max(start_boundary, confirmation_floor), next_start)
                if not search:
                    current.append(center)
                    continue
                boundary = _extreme(search, direction)
                if boundary["boundary"] <= start_boundary:
                    issues.append({"code": "invalid_movement_boundary", "center_id": center["id"]})
                    current.append(center)
                    continue
                built = _movement_from_centers(
                    segment, current, level, start_boundary, boundary["boundary"], direction,
                    "confirmed", center, role, origin,
                )
                if built["status"] != "confirmed":
                    issues.extend(built.get("issues", []))
                    current.append(center)
                    continue
                movements.append(built)
                start_boundary = boundary["boundary"]
                confirmation_floor = next_start
                current = [center]
                direction = _opposite(direction)
            if start_boundary < len(segment):
                candidate = _extreme(_endpoint_units(segment, start_boundary, len(segment)), direction)
                tail = _movement_from_centers(
                    segment, current, level, start_boundary, candidate["boundary"], direction,
                    "provisional", None, role, origin, source_end_boundary=len(segment), candidate_extreme=candidate,
                )
                if segment[-1]["end_date"] < global_end:
                    tail["termination_reason"] = "sequence_boundary" if segment[-1] is not group[-1] else "data_boundary"
                movements.append(tail)
    covered = {unit_id for movement in movements for unit_id in movement["source_unit_ids"]}
    for ordinal, movement in enumerate(movements):
        movement["ordinal"] = ordinal
    return {"centers": used_centers, "movements": movements, "issues": issues,
            "unassigned": [unit["id"] for unit in units if unit["id"] not in covered]}


def _recursive_units(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prepare confirmed hierarchy components for the next recursive level."""
    groups: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for unit in units:
        if unit.get("status", "confirmed") == "confirmed":
            groups.setdefault(_group_key(unit), []).append(unit)
    prepared: list[dict[str, Any]] = []
    for group_units in groups.values():
        for _segment_index, segment, _break_info in _split_contiguous_units(group_units):
            prepared.extend(segment)
    prepared.sort(key=lambda item: (
        _group_key(item)[0], item.get("start_date", ""), item.get("end_date", ""),
        _group_key(item)[1], item.get("id", "")
    ))
    return prepared


def _upgrade_kind(core_movements: list[dict[str, Any]]) -> str:
    center_ids = [center_id for movement in core_movements for center_id in movement.get("center_ids", [])]
    return "extension_3x3" if len(center_ids) != len(set(center_ids)) else "expansion"


def _parent_centers(
    movements: list[dict[str, Any]], parent_level: int, origin: str,
    child_centers: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build a parent-level center from completed child movements.

    A pair of overlapping child movements is a provisional 2/3 candidate.  A
    pair is classified as an ``expansion`` only when the referenced child
    centers have separated cores but overlapping envelopes; otherwise it is
    the first two 3-segment units of the extension path.  The classification
    is derived from the first pair, so the candidate ID remains unchanged when
    the third child later confirms it.
    """
    centers: list[dict[str, Any]] = []
    center_by_id = {item.get("id"): item for item in (child_centers or []) if item.get("id")}

    def child_relation(left: dict[str, Any], right: dict[str, Any]) -> str | None:
        ids_left = left.get("center_ids", []) or []
        ids_right = right.get("center_ids", []) or []
        pairs = [
            (center_by_id[left_id], center_by_id[right_id])
            for left_id in ids_left
            for right_id in ids_right
            if left_id in center_by_id and right_id in center_by_id
        ]
        # Prefer an explicit expansion relation.  A movement can contain more
        # than one child center, so inspect all referenced pairs rather than
        # relying on only the first ID.
        for previous, current in pairs:
            relation = classify_center_relation(previous, current)
            if relation.startswith("expansion_"):
                return relation
        for previous, current in pairs:
            if classify_center_relation(previous, current) == "extension":
                return "extension"
        return None

    def classify_upgrade_kind(selected: list[dict[str, Any]]) -> str | None:
        if len(selected) >= 2 and child_relation(selected[0], selected[1]) in {
            "expansion_up", "expansion_down",
        }:
            return "expansion"
        # The extension path is specifically 3+3 (+3 on confirmation), not a
        # generic overlap of arbitrary completed movements.
        referenced_centers = [
            center_id
            for movement in selected
            for center_id in (movement.get("center_ids") or [])
        ]
        # Distinct newborn centers cannot be reclassified as a 3x3 extension.
        # Hand-authored fixtures without center IDs retain the historical
        # extension interpretation, while real hierarchy components carry
        # their canonical center ID and therefore get this guard.
        if len(referenced_centers) != len(set(referenced_centers)) and all(len(movement.get("source_unit_ids") or []) == 3 for movement in selected):
            return "extension_3x3"
        return "recursive_three"

    groups: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for movement in movements:
        if movement.get("status") == "confirmed" and not movement_confirmation_errors(movement, center_by_id):
            groups.setdefault(_group_key(movement), []).append(movement)
    for key, units in groups.items():
        units.sort(key=lambda item: (item["start_date"], item["end_date"], item["id"]))

        # Parent upgrades consume a continuous, non-overlapping child stream.
        # Reject malformed/manual streams before attempting a three-child
        # overlap; otherwise a gap or reused child could create a plausible
        # but irreproducible higher-level center.
        sequence_valid = True
        seen_source_units: set[str] = set()
        bounded = all(
            movement.get("start_boundary") is not None
            and movement.get("end_boundary") is not None
            for movement in units
        )
        previous_end: int | None = None
        for movement in units:
            source_units = movement.get("source_unit_ids") or movement.get("child_movement_ids") or []
            if len(source_units) != len(set(source_units)) or seen_source_units.intersection(source_units):
                sequence_valid = False
                break
            seen_source_units.update(source_units)
            if not source_units:
                sequence_valid = False
                break
            if bounded:
                try:
                    start_boundary = int(movement["start_boundary"])
                    end_boundary = int(movement["end_boundary"])
                except (TypeError, ValueError):
                    sequence_valid = False
                    break
                if end_boundary <= start_boundary or end_boundary - start_boundary != len(source_units):
                    sequence_valid = False
                    break
                if previous_end is not None and start_boundary != previous_end:
                    sequence_valid = False
                    break
                previous_end = end_boundary
        if not sequence_valid:
            continue
        if any(not _units_are_contiguous(left, right) for left, right in zip(units, units[1:])):
            continue
        index = 0
        while index + 1 < len(units):
            pair = units[index:index + 2]
            pair_overlap = _strict_overlap(pair)
            if not pair_overlap or not _alternating(pair):
                index += 1
                continue
            core = units[index:index + 3]
            confirmed = len(core) == 3 and _alternating(core) and _strict_overlap(core) is not None
            selected = core if confirmed else pair
            overlap = _strict_overlap(selected)
            if not overlap:
                index += 1
                continue
            zd, zg = overlap
            lows, highs = zip(*(_bounds(unit) for unit in selected))
            # Each completed child movement is built from one non-overlapping
            # three-unit group. Two groups create the stable 2/3 candidate;
            # the third group confirms the 3+3+3 or expansion upgrade.
            upgrade_kind = classify_upgrade_kind(pair)
            if upgrade_kind is None:
                index += 1
                continue
            center_id = _stable_id(f"center-L{parent_level}", [
                str(key[0]), str(key[1]), selected[0]["id"], upgrade_kind,
            ])
            source_pens = _source_pen_ids(selected)
            child_centers = []
            for movement in selected:
                for child_id in movement.get("center_ids", []):
                    if child_id not in child_centers:
                        child_centers.append(child_id)
            centers.append({
                "id": center_id, "kind": "center", "role": "hierarchy", "level": parent_level,
                "status": "confirmed" if confirmed else "provisional", "upgrade_kind": upgrade_kind,
                "progress": "3/3" if confirmed else "2/3", "start_date": selected[0]["start_date"],
                "end_date": selected[-1]["end_date"], "confirmed_at": _latest_timestamp(
                    *(item.get("confirmed_at") for item in selected),
                    *(item.get("end_date") for item in selected),
                ) if confirmed else None,
                "direction": _direction(selected[0]),
                "start_price": float(selected[0]["start_price"]),
                "end_price": float(selected[-1]["end_price"]),
                "zd": zd, "zg": zg, "fixed_zd": zd, "fixed_zg": zg,
                "dd": min(lows), "gg": max(highs), "low": zd, "high": zg,
                "core_unit_ids": [unit["id"] for unit in selected],
                "child_movement_ids": [unit["id"] for unit in selected],
                "child_center_ids": child_centers, "source_pen_ids": source_pens, "pen_ids": source_pens,
                "parent_center_ids": [], "continuous_range_id": key[0], "sequence_id": key[1],
                "origin": origin, "evidence": [
                    "CENTER-HIERARCHY-THREE-001",
                    "CENTER-UPGRADE-EXTENSION-001" if upgrade_kind == "extension_3x3" else "CENTER-UPGRADE-EXPANSION-001" if upgrade_kind == "expansion" else "CENTER-HIERARCHY-THREE-001",
                ],
            })
            index += 3 if confirmed else 2
    return centers


def build_hierarchy(pens: list[dict[str, Any]], l1_centers: list[dict[str, Any]], origin: str = "system") -> dict[str, Any]:
    # Callers normally pass only the raw directional L1 seeds.  Persisted
    # Only directional L1 seeds are valid hierarchy inputs. Keep legacy
    # hand-authored centers (which omit ``role``) compatible.
    l1_centers = [
        center for center in l1_centers
        if int(center.get("level", 1) or 1) == 1
        and center.get("role", "hierarchy") == "hierarchy"
    ]
    pen_ids = {str(pen.get("id")) for pen in pens if pen.get("id")}
    hierarchy_centers: list[dict[str, Any]] = []
    for center in l1_centers:
        references = []
        for field in ("entry_pen_id", "core_pen_ids", "formation_pen_ids", "source_pen_ids", "pen_ids"):
            value = center.get(field)
            references.extend(value if isinstance(value, list) else [value] if value else [])
        if references and any(str(value) not in pen_ids for value in references):
            continue
        normalized = dict(center, role="hierarchy", origin=origin, parent_center_ids=[])
        normalized.setdefault("upgrade_kind", None)
        normalized.setdefault("progress", "3/3")
        normalized.setdefault("core_unit_ids", list(normalized.get("core_pen_ids") or []))
        normalized.setdefault("child_movement_ids", [])
        normalized.setdefault("child_center_ids", [])
        hierarchy_centers.append(normalized)
    units_by_level: dict[int, list[dict[str, Any]]] = {0: atomic_pen_units(pens)}
    movements: list[dict[str, Any]] = []
    unassigned_by_level = {}
    hierarchy_issues = []

    for level in range(1, MAX_CENTER_LEVEL + 1):
        units = units_by_level.get(level - 1, [])
        if not units:
            break
        if level == 1:
            structural_centers = hierarchy_centers
        else:
            structural_centers = [
                center for center in hierarchy_centers
                if int(center.get("level", 1)) == level
            ]
        component_result = build_hierarchy_components(
            units, structural_centers, level, role="hierarchy_component", origin=origin,
        )
        unassigned_by_level[str(level)] = component_result["unassigned"]
        hierarchy_issues.extend(component_result["issues"])
        component_movements = component_result["movements"]
        movements.extend(component_movements)

        # A higher-level center may only consume a genuinely continuous stream
        # of completed lower-level movements.  The hierarchy component builder
        # deliberately leaves provisional tails and unexplained units out of
        # that stream; those holes must become explicit sequence boundaries
        # before the next recursive pass.  Use shallow copies so the persisted
        # lower-level movement rows retain their original source sequence IDs
        # and stable identity, while parent centers and the next level operate
        # on the split structural stream.
        completed_components = [
            item for item in component_movements if item.get("status") == "confirmed"
        ]
        segmented_components: list[dict[str, Any]] = []
        component_groups: dict[tuple[int, int], list[dict[str, Any]]] = {}
        for item in completed_components:
            component_groups.setdefault(_group_key(item), []).append(item)
        for key in sorted(component_groups, key=lambda value: (
            value[0],
            min(item.get("start_date", "") for item in component_groups[value]),
            value[1],
        )):
            for _segment_index, segment, _break_info in _split_contiguous_units(component_groups[key]):
                segmented_components.extend(segment)
        if level == MAX_CENTER_LEVEL:
            break
        parent_centers = _parent_centers(
            segmented_components, level + 1, origin, structural_centers
        )
        if not parent_centers:
            break
        hierarchy_centers.extend(parent_centers)
        units_by_level[level] = segmented_components

    centers = hierarchy_centers
    center_by_id = {center["id"]: center for center in centers}
    for parent in hierarchy_centers:
        for child_id in parent.get("child_center_ids", []):
            child = center_by_id.get(child_id)
            if child and parent["id"] not in child.setdefault("parent_center_ids", []):
                child["parent_center_ids"].append(parent["id"])
    relations = build_center_relations(hierarchy_centers)
    for parent in hierarchy_centers:
        for child_id in parent.get("child_center_ids", []):
            child = center_by_id.get(child_id)
            if not child:
                continue
            relations.append({
                "id": _stable_id("center-relation-parent", [parent["id"], child_id]),
                "kind": "center_relation", "relation": "parent_child", "level": child["level"],
                "previous_center_id": child_id, "current_center_id": parent["id"],
                "start_date": child["start_date"], "end_date": parent["end_date"],
                "confirmed_at": parent.get("confirmed_at"), "continuous_range_id": parent.get("continuous_range_id", 0),
                "sequence_id": parent.get("sequence_id", 0), "evidence": ["CENTER-PARENT-CHILD-001"],
            })
    for ordinal, item in enumerate(centers):
        item["ordinal"] = ordinal
    per_level: dict[int, int] = {}
    for item in centers:
        per_level[item["level"]] = per_level.get(item["level"], 0) + 1
        item["level_ordinal"] = per_level[item["level"]] - 1
    for ordinal, item in enumerate(relations):
        item["ordinal"] = ordinal
    for ordinal, item in enumerate(movements):
        item["ordinal"] = ordinal
    confirmed_levels = [center["level"] for center in hierarchy_centers if center["status"] == "confirmed"]
    available_levels = [center["level"] for center in hierarchy_centers]
    return {
        "centers": centers,
        "center_relations": relations,
        "movements": movements,
        "max_confirmed_center_level": max(confirmed_levels, default=1 if l1_centers else 0),
        "max_available_center_level": max(available_levels, default=1 if l1_centers else 0),
        "hierarchy_version": HIERARCHY_VERSION,
        "unassigned_by_level": unassigned_by_level, "hierarchy_issues": hierarchy_issues,
    }
