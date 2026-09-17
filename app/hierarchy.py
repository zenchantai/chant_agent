from __future__ import annotations

import hashlib
from typing import Any


HIERARCHY_VERSION = "center-hierarchy-cache-fingerprint-v20-unified-directional-ownership"
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


def _center_core_overlap(left: dict[str, Any], right: dict[str, Any]) -> tuple[float, float] | None:
    zd = max(float(left["zd"]), float(right["zd"]))
    zg = min(float(left["zg"]), float(right["zg"]))
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
        "continuous_range_id": int(pen.get("continuous_range_id", pen.get("range_index", 0)) or 0),
        "sequence_id": int(pen.get("sequence_id", 0)),
        "structure_sequence_id": str(pen.get("structure_sequence_id", "")),
    } for pen in pens]


def _center_owned_unit_ids(center: dict[str, Any]) -> list[str]:
    if "owned_unit_ids" in center:
        return list(center["owned_unit_ids"])
    result: list[str] = []
    for field in (
        "entry_unit_id", "entry_pen_id", "core_unit_ids", "core_pen_ids",
        "extension_unit_ids", "extension_pen_ids", "peripheral_unit_ids",
        "peripheral_pen_ids",
        "formation_unit_ids", "formation_pen_ids", "source_unit_ids",
        "source_pen_ids", "pen_ids",
    ):
        value = center.get(field)
        values = value if isinstance(value, list) else [value] if value else []
        for unit_id in values:
            if unit_id and unit_id not in result:
                result.append(unit_id)
    return result


def _center_is_owned_by_units(center: dict[str, Any], units: list[dict[str, Any]]) -> bool:
    owned = set(_center_owned_unit_ids(center))
    available = {unit.get("id") for unit in units if unit.get("id")}
    return bool(owned) and owned <= available


def _stream_key(item: dict[str, Any]) -> tuple[int, int, str]:
    sequence = item.get("structure_sequence_id") or ""
    return (*_group_key(item), str(sequence))


def _unit_segments(units: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: dict[tuple[int, int, str], list[dict[str, Any]]] = {}
    for unit in units:
        groups.setdefault(_stream_key(unit), []).append(unit)
    segments = []
    for key in sorted(groups):
        current = []
        for unit in sorted(groups[key], key=lambda item: (item["start_date"], item["end_date"], item["id"])):
            if unit.get("status") != "confirmed" or unit.get("recursive_eligible") is False:
                if current:
                    segments.append(current)
                current = []
                continue
            if current and not _units_are_contiguous(current[-1], unit):
                segments.append(current)
                current = []
            current.append(unit)
        if current:
            segments.append(current)
    return segments


def _directional_seed(units: list[dict[str, Any]], start: int, core_count: int = 3) -> dict[str, Any] | None:
    selected = units[start:start + core_count + 1]
    if len(selected) != core_count + 1 or any(unit.get("status") != "confirmed" for unit in selected):
        return None
    if len({_stream_key(unit) for unit in selected}) != 1 or len({unit["id"] for unit in selected}) != len(selected):
        return None
    if any(not _units_are_contiguous(left, right) for left, right in zip(selected, selected[1:])):
        return None
    overlap = _strict_overlap(selected[1:])
    if overlap is None:
        return None
    zd, zg = overlap
    entry = selected[0]
    direction = _direction(entry)
    start_price, end_price = float(entry["start_price"]), float(entry["end_price"])
    entered = start_price + EPSILON < zd < end_price - EPSILON if direction == "up" else start_price - EPSILON > zg > end_price + EPSILON
    return {"entry": entry, "core": selected[1:], "zd": zd, "zg": zg, "direction": direction} if entered else None


def _center_record(units: list[dict[str, Any]], start: int, seed: dict[str, Any], level: int,
                   extension: list[int], peripheral: list[int], departure: list[int], confirmed: bool,
                   tail_status: str) -> dict[str, Any]:
    entry, core = seed["entry"], seed["core"]
    owned_indexes = sorted({*range(start, start + 1 + len(core)), *extension, *peripheral})
    source = [units[index] for index in owned_indexes]
    source_ids = [unit["id"] for unit in source]
    formation = [entry, *core]
    lows, highs = zip(*(_bounds(unit) for unit in source))
    child_centers = list(dict.fromkeys(center_id for unit in source for center_id in unit.get("center_ids", [])))
    record = {
        "id": _stable_id(f"directional-center-L{level}", [str(_stream_key(entry)), entry["id"], core[0]["id"], core[1]["id"]]),
        "ordinal": 0, "level_ordinal": 0, "kind": "pen_center" if level == 1 else "center",
        "role": "hierarchy", "level": level, "direction": seed["direction"],
        "entry_unit_id": entry["id"], "core_unit_ids": [unit["id"] for unit in core],
        "extension_unit_ids": [units[index]["id"] for index in extension],
        "peripheral_unit_ids": [units[index]["id"] for index in peripheral],
        "departure_unit_ids": [units[index]["id"] for index in departure],
        "owned_unit_ids": source_ids if confirmed else [],
        "source_unit_ids": source_ids, "formation_unit_ids": [unit["id"] for unit in formation],
        "source_pen_ids": _source_pen_ids(source), "pen_ids": _source_pen_ids(source),
        "start_date": core[0]["start_date"], "end_date": source[-1]["end_date"],
        "core_start_date": core[0]["start_date"], "core_end_date": core[-1]["end_date"],
        "extension_end_date": source[-1]["end_date"],
        "start_price": float(core[0]["start_price"]), "end_price": float(source[-1]["end_price"]),
        "zd": seed["zd"], "zg": seed["zg"], "fixed_zd": seed["zd"], "fixed_zg": seed["zg"],
        "dd": min(lows), "gg": max(highs), "low": seed["zd"], "high": seed["zg"],
        "status": "confirmed" if confirmed else "provisional", "progress": "3/3" if confirmed else "2/3",
        "confirmed_at": _latest_timestamp(*(unit.get("confirmed_at") for unit in formation), *(unit["end_date"] for unit in formation)) if confirmed else None,
        "tail_status": tail_status, "termination_reason": "independent_center" if tail_status == "confirmed_departure" else "right_edge",
        "completion_reason": "directional_core_with_extension" if confirmed else "pending_third_core_unit",
        "continuous_range_id": _group_key(entry)[0], "sequence_id": _group_key(entry)[1],
        "structure_sequence_id": str(entry.get("structure_sequence_id", "")),
        "parent_center_ids": [], "child_movement_ids": source_ids if level > 1 else [],
        "child_center_ids": child_centers if level > 1 else [], "owner_movement_id": None,
        "construction_mode": "unified_directional_ownership", "upgrade_kind": None if level == 1 else "directional_recursion",
        "evidence": ["CENTER-DIRECTIONAL-ENTRY-001", "CENTER-DIRECTIONAL-CORE-001"],
    }
    if level == 1:
        record.update({
            "entry_pen_id": entry["id"], "core_pen_ids": record["core_unit_ids"],
            "formation_pen_ids": record["formation_unit_ids"],
            "extension_pen_ids": record["extension_unit_ids"], "peripheral_pen_ids": record["peripheral_unit_ids"],
            "departure_pen_ids": record["departure_unit_ids"], "start_pen": core[0]["id"],
            "end_pen": source[-1]["id"], "start_pen_index": start + 1, "end_pen_index": owned_indexes[-1],
        })
        if extension:
            record["evidence"].append("CENTER-L1-EXTENSION-001")
        if peripheral:
            record["evidence"].append("CENTER-L1-REENTRY-001")
    return record


def build_directional_centers(units: list[dict[str, Any]], level: int) -> list[dict[str, Any]]:
    centers = []
    for segment in _unit_segments(units):
        start = 0
        while start + 2 < len(segment):
            seed = _directional_seed(segment, start)
            if seed is None:
                candidate = _directional_seed(segment, start, 2) if start + 3 == len(segment) else None
                if candidate:
                    centers.append(_center_record(segment, start, candidate, level, [], [], [], False, "pending_third_core_unit"))
                start += 1
                continue
            extension, peripheral, pending = [], [], []
            next_start = None
            for cursor in range(start + 4, len(segment)):
                proposed_start = cursor - 3
                proposed = _directional_seed(segment, proposed_start) if proposed_start >= start + 4 else None
                if proposed and (proposed["zd"] > seed["zg"] + EPSILON or proposed["zg"] < seed["zd"] - EPSILON):
                    next_start = proposed_start
                    extension = [index for index in extension if index < next_start]
                    peripheral = [index for index in peripheral if index < next_start]
                    pending = [index for index in pending if index < next_start]
                    break
                low, high = _bounds(segment[cursor])
                if max(low, seed["zd"]) + EPSILON < min(high, seed["zg"]):
                    peripheral.extend(pending)
                    pending = []
                    extension.append(cursor)
                else:
                    pending.append(cursor)
            departure = [next_start] if next_start is not None else pending
            tail_status = "confirmed_departure" if next_start is not None else "provisional_departure" if pending else "active_extension"
            centers.append(_center_record(segment, start, seed, level, extension, peripheral, departure, True, tail_status))
            if next_start is None:
                candidate_start = len(segment) - 3
                candidate = _directional_seed(segment, candidate_start, 2) if candidate_start >= start + 4 else None
                if candidate and (candidate["zd"] > seed["zg"] + EPSILON or candidate["zg"] < seed["zd"] - EPSILON):
                    centers.append(_center_record(segment, candidate_start, candidate, level, [], [], [], False, "pending_third_core_unit"))
                break
            start = next_start
    for ordinal, center in enumerate(centers):
        center["ordinal"] = center["level_ordinal"] = ordinal
    return centers


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
        grouped.setdefault((*_stream_key(center), int(center["level"])), []).append(center)
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
        and _stream_key(previous) == _stream_key(current)
        and current.get("id") != previous.get("id")
        and _cores_separated(previous, current)
    )


def _movement_classification(centers: list[dict[str, Any]], direction: str) -> str | None:
    if len(centers) < 2:
        return "consolidation"
    if all(
        previous.get("direction") == current.get("direction") == direction
        and (
            float(current["zd"]) > float(previous["zg"]) + EPSILON if direction == "up"
            else float(current["zg"]) < float(previous["zd"]) - EPSILON
        )
        for previous, current in zip(centers, centers[1:])
    ):
        return "trend"
    return None


def movement_confirmation_errors(movement: dict[str, Any], centers: dict[str, dict[str, Any]]) -> list[str]:
    errors = []
    referenced = movement.get("center_ids") or []
    source_ids = movement.get("source_unit_ids") or []
    if len(source_ids) != len(set(source_ids)):
        errors.append("走势重复消费源单位")
    if not referenced or any(center_id not in centers for center_id in referenced):
        errors.append("走势缺少真实所属中枢")
    for center_id in referenced:
        center = centers.get(center_id)
        if center and not set(_center_owned_unit_ids(center)) <= set(source_ids):
            errors.append("走势未完整包含所属中枢单位")
        if center and center.get("owner_movement_id") not in {None, movement.get("id")}:
            errors.append("所属中枢所有者不一致")
    if movement.get("status") != "confirmed":
        if movement.get("confirmed_at") or movement.get("confirmation_center_id") or movement.get("recursive_eligible"):
            errors.append("未完成走势含确认信息或递归资格")
        return errors
    previous = centers.get(referenced[-1]) if referenced else None
    confirmation = centers.get(movement.get("confirmation_center_id"))
    if not previous or not confirmation:
        return [*errors, "已确认走势缺少真实确认中枢"]
    if confirmation["id"] in referenced or set(_center_owned_unit_ids(confirmation)) & set(source_ids):
        errors.append("确认中枢不能属于前一走势")
    if not is_reverse_confirmation(previous, confirmation, movement.get("direction")):
        errors.append("确认中枢不是同级反向独立中枢")
    if (
        int(movement.get("level", 1)) != int(confirmation.get("level", 1))
        or movement.get("termination_reason") != "reverse_independent_center"
        or not movement.get("confirmed_at")
        or str(movement.get("confirmed_at") or "") < str(confirmation.get("confirmed_at") or "")
        or str(movement.get("confirmed_at") or "") < str(movement.get("end_date") or "")
        or movement.get("classification") not in {"trend", "consolidation"}
        or movement.get("state", "formed") != "formed"
        or not _boundary_matches_direction(float(movement["start_price"]), float(movement["end_price"]), movement["direction"])
    ):
        errors.append("走势确认时间、方向、分类、级别或结束原因不一致")
    return errors


def _legal_boundaries(units, start, lower, upper, direction):
    if not units or lower > upper:
        return []
    points = _endpoint_units(units, start, upper)
    return [point for point in points if lower <= point["boundary"] <= upper
            and point["boundary"] > start
            and _direction(units[point["boundary"] - 1]) == direction
            and (point["price"] > float(units[start]["start_price"]) + EPSILON if direction == "up"
                 else point["price"] < float(units[start]["start_price"]) - EPSILON)]


def _range_extreme(units, direction):
    candidates = []
    for unit in units:
        low, high = _bounds(unit)
        if direction == "up":
            value, side = high, "high"
        else:
            value, side = low, "low"
        stamp = unit.get(f"range_{side}_date")
        if not stamp:
            stamp = unit["start_date"] if abs(float(unit["start_price"]) - value) <= EPSILON else unit["end_date"]
        candidates.append({"price": value, "trade_date": stamp})
    return _extreme(candidates, direction)


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
        raise ValueError("走势边界没有可用低级单位")
    selected = units[start_boundary:source_end]
    start_point, endpoint = points[0], points[-1]
    issues = []
    classification = _movement_classification(centers, direction)
    if status == "confirmed" and not _boundary_matches_direction(start_point["price"], endpoint["price"], direction):
        issues.append({"code": "movement_direction_endpoint_mismatch"})
    if status == "confirmed" and (not confirmation_center or not is_reverse_confirmation(centers[-1], confirmation_center, direction)):
        issues.append({"code": "missing_reverse_confirmation"})
    if any(not _center_is_owned_by_units(center, selected) for center in centers):
        issues.append({"code": "center_ownership_boundary"})
    if classification is None:
        issues.append({"code": "mixed_center_relation"})
    if issues:
        status = "provisional"
    if status != "confirmed":
        confirmation_center = None
    center_ids = [center["id"] for center in centers]
    movement_id = _stable_id(f"movement-L{level}", [str(_stream_key(centers[0])), center_ids[0], selected[0]["id"]])
    low, high = _range_extreme(selected, "down"), _range_extreme(selected, "up")
    confirmed_at = _latest_timestamp(
        confirmation_center.get("confirmed_at"), *(unit.get("confirmed_at") for unit in selected),
        *(center.get("confirmed_at") for center in centers), endpoint["trade_date"],
    ) if confirmation_center else None
    return {
        "id": movement_id, "kind": "movement", "role": role, "level": level,
        "state": "undetermined" if issues else "formed", "direction": direction,
        "classification": None if issues else classification, "status": status,
        "start_date": start_point["trade_date"], "start_price": start_point["price"],
        "end_date": endpoint["trade_date"], "end_price": endpoint["price"],
        "low": low["price"], "high": high["price"],
        "range_low_date": low["trade_date"], "range_high_date": high["trade_date"],
        "range_low_price": low["price"], "range_high_price": high["price"],
        "confirmed_at": confirmed_at, "center_ids": center_ids, "center_count": len(centers),
        "confirmation_center_id": confirmation_center["id"] if confirmation_center else None,
        "child_movement_ids": [unit["id"] for unit in selected if unit.get("kind") == "movement"],
        "source_unit_ids": [unit["id"] for unit in selected], "source_pen_ids": _source_pen_ids(selected),
        "continuous_range_id": _group_key(centers[0])[0], "sequence_id": _group_key(centers[0])[1],
        "structure_sequence_id": centers[0].get("structure_sequence_id", ""),
        "origin": origin, "termination_reason": "reverse_independent_center" if confirmation_center else "provisional_tail",
        "start_boundary": start_boundary, "end_boundary": source_end, "source_end_boundary": source_end,
        "boundary_source_unit_id": endpoint["unit_id"], "boundary_source_price": endpoint["price"],
        "endpoint_points": [start_point, endpoint], "path_points": [start_point, endpoint],
        "candidate_extreme_date": candidate_extreme["trade_date"] if candidate_extreme else None,
        "candidate_extreme_price": candidate_extreme["price"] if candidate_extreme else None,
        "tail_end_date": endpoint["trade_date"], "tail_end_price": endpoint["price"],
        "construction_mode": "unified_directional_ownership", "boundary_mode": "structure_ownership_first",
        "recursive_eligible": status == "confirmed", "undetermined_reason": issues[0]["code"] if issues else None,
        "evidence": ["MOVEMENT-BOUNDARY-001", "MOVEMENT-SHARED-ENDPOINT-001"], "issues": issues,
    }


def _center_unit_span(center, units, unit_index):
    referenced = _center_owned_unit_ids(center)
    if not referenced or any(unit_id not in unit_index for unit_id in referenced):
        return None
    indexes = sorted({unit_index[unit_id] for unit_id in referenced})
    if indexes != list(range(indexes[0], indexes[-1] + 1)):
        return None
    return indexes[0], indexes[-1] + 1


def build_hierarchy_components(
    units: list[dict[str, Any]], structural_centers: list[dict[str, Any]], level: int,
    role: str = "hierarchy_component", origin: str = "system",
) -> dict[str, Any]:
    movements, used_centers, issues = [], [], []
    global_end = max((unit["end_date"] for unit in units), default="")
    for segment in _unit_segments(units):
        unit_index = {unit["id"]: index for index, unit in enumerate(segment)}
        centers = []
        for original in structural_centers:
            if original.get("status") != "confirmed" or _group_key(original) != _group_key(segment[0]):
                continue
            span = _center_unit_span(original, segment, unit_index)
            if span is not None:
                centers.append(dict(original, unit_start_index=span[0], unit_end_index=span[1]))
        centers.sort(key=lambda center: (center["unit_start_index"], center["id"]))
        if not centers:
            continue
        sequence = _stable_id(f"structure-sequence-L{level}", [str(_stream_key(segment[0])), segment[0]["id"]])
        current, start = [], centers[0]["unit_start_index"]

        def submit(end, confirmation=None, reason=None):
            direction = current[0]["direction"]
            legal = _legal_boundaries(segment, start, current[-1]["unit_end_index"], end, direction)
            candidate = _extreme(legal, direction) if legal else None
            built = _movement_from_centers(segment, current, level, start, end, direction,
                                           "confirmed" if confirmation else "provisional",
                                           confirmation, role, origin, candidate_extreme=candidate)
            if reason:
                built.update(status="provisional", state="undetermined", classification=None,
                             confirmed_at=None, confirmation_center_id=None, recursive_eligible=False,
                             undetermined_reason=reason, termination_reason="sequence_boundary")
                built["issues"].append({"code": reason})
                issues.append({"code": reason, "movement_id": built["id"]})
            for owned in current:
                owned["owner_movement_id"] = built["id"]
            movements.append(built)
            return built

        for center in centers:
            center["structure_sequence_id"] = sequence
            if not current:
                current = [center]
                used_centers.append(center)
                continue
            previous = current[-1]
            direction = current[0]["direction"]
            if center["unit_start_index"] < previous["unit_end_index"]:
                issues.append({
                    "code": "overlapping_center_ownership",
                    "center_id": center["id"],
                    "previous_center_id": previous["id"],
                })
                continue
            reverse = is_reverse_confirmation(previous, center, direction)
            same_trend = (center["direction"] == direction and (
                float(center["zd"]) > float(previous["zg"]) + EPSILON if direction == "up"
                else float(center["zg"]) < float(previous["zd"]) - EPSILON))
            if same_trend and not reverse:
                current.append(center)
                used_centers.append(center)
                continue
            legal = _legal_boundaries(segment, start, previous["unit_end_index"], center["unit_start_index"], direction) if reverse else []
            if reverse and legal:
                boundary = _extreme(legal, direction)
                submit(boundary["boundary"], center)
                start = boundary["boundary"]
            else:
                reason = "no_legal_structural_boundary" if reverse else "mixed_center_relation"
                submit(center["unit_start_index"], reason=reason)
                start = center["unit_start_index"]
                sequence = _stable_id(f"structure-sequence-L{level}", [sequence, center.get("entry_unit_id") or center.get("entry_pen_id") or center["id"]])
                center["structure_sequence_id"] = sequence
            current = [center]
            used_centers.append(center)
        if current:
            tail = submit(len(segment))
            if segment[-1]["end_date"] < global_end:
                tail["termination_reason"] = "data_boundary"
    covered = {unit_id for movement in movements for unit_id in movement["source_unit_ids"]}
    for ordinal, movement in enumerate(movements):
        movement["ordinal"] = ordinal
    return {"centers": used_centers, "movements": movements, "issues": issues,
            "unassigned": [unit["id"] for unit in units if unit["id"] not in covered]}


def _recursive_units(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [dict(unit) for segment in _unit_segments(units) for unit in segment]


def _parent_centers(movements, parent_level, origin, child_centers=None):
    child_by_id = {center["id"]: center for center in child_centers or []}
    adapted = []
    seen_sources: set[str] = set()
    for movement in sorted(movements, key=lambda item: (item["start_date"], item["end_date"], item["id"])):
        source = movement.get("source_unit_ids") or []
        eligible = (
            movement.get("status") == "confirmed"
            and movement.get("kind") == "movement"
            and int(movement.get("level", 0)) == parent_level - 1
            and movement.get("recursive_eligible") is True
            and bool(source)
            and len(source) == len(set(source))
            and not seen_sources.intersection(source)
            and not movement_confirmation_errors(movement, child_by_id)
        )
        adapted.append(dict(movement, recursive_eligible=eligible))
        if eligible:
            seen_sources.update(source)
    return [dict(center, origin=origin) for center in build_directional_centers(adapted, parent_level)]


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
        for field in (
            "entry_pen_id", "entry_unit_id", "core_pen_ids", "core_unit_ids",
            "formation_pen_ids", "formation_unit_ids", "source_pen_ids",
            "source_unit_ids", "pen_ids", "owned_unit_ids",
        ):
            value = center.get(field)
            references.extend(value if isinstance(value, list) else [value] if value else [])
        if references and any(str(value) not in pen_ids for value in references):
            continue
        normalized = dict(center, role="hierarchy", origin=origin, parent_center_ids=[])
        normalized.setdefault("upgrade_kind", None)
        normalized.setdefault("progress", "3/3")
        normalized.setdefault("construction_mode", "unified_directional_ownership")
        normalized.setdefault("entry_unit_id", normalized.get("entry_pen_id"))
        normalized.setdefault("core_unit_ids", list(normalized.get("core_pen_ids") or []))
        normalized.setdefault("formation_unit_ids", list(normalized.get("formation_pen_ids") or []))
        normalized.setdefault("extension_unit_ids", list(normalized.get("extension_pen_ids") or []))
        normalized.setdefault("peripheral_unit_ids", list(normalized.get("peripheral_pen_ids") or []))
        normalized.setdefault("source_unit_ids", list(normalized.get("source_pen_ids") or normalized.get("pen_ids") or []))
        normalized.setdefault("owned_unit_ids", _center_owned_unit_ids(normalized))
        normalized.setdefault("owner_movement_id", None)
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
        component_result = build_hierarchy_components(units, structural_centers, level, origin=origin)
        updated = {center["id"]: center for center in component_result["centers"]}
        for center in structural_centers:
            if center["id"] in updated:
                center.update(updated[center["id"]])
        unassigned_by_level[str(level)] = component_result["unassigned"]
        hierarchy_issues.extend(component_result["issues"])
        component_movements = component_result["movements"]
        movements.extend(component_movements)

        segmented_components = _recursive_units(component_result["movements"])
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
        "max_confirmed_center_level": max(confirmed_levels, default=0),
        "max_available_center_level": max(available_levels, default=0),
        "hierarchy_version": HIERARCHY_VERSION,
        "unassigned_by_level": unassigned_by_level, "hierarchy_issues": hierarchy_issues,
    }
