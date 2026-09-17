from __future__ import annotations

import hashlib
from typing import Any


HIERARCHY_VERSION = "center-hierarchy-cache-fingerprint-v22-center-free-buy-sell-points"
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


def _component_record(units: list[dict[str, Any]], start: int, end: int, level: int) -> dict[str, Any]:
    selected = units[start:end]
    lows, highs = zip(*(_bounds(unit) for unit in selected))
    direction = "up" if float(selected[-1]["end_price"]) > float(selected[0]["start_price"]) else "down"
    source_ids = [unit["id"] for unit in selected]
    low_unit = min(selected, key=lambda unit: (_bounds(unit)[0], unit["start_date"], unit["id"]))
    high_unit = max(selected, key=lambda unit: (_bounds(unit)[1], unit["start_date"], unit["id"]))
    low_date = low_unit.get("range_low_date")
    if not low_date:
        low_date = low_unit["start_date"] if abs(float(low_unit["start_price"]) - _bounds(low_unit)[0]) <= EPSILON else low_unit["end_date"]
    high_date = high_unit.get("range_high_date")
    if not high_date:
        high_date = high_unit["start_date"] if abs(float(high_unit["start_price"]) - _bounds(high_unit)[1]) <= EPSILON else high_unit["end_date"]
    return {
        "id": _stable_id(f"center-free-component-L{level}", [str(_stream_key(selected[0])), *source_ids]),
        "kind": "center_free_component", "level": level, "status": "confirmed",
        "direction": direction, "start_date": selected[0]["start_date"],
        "end_date": selected[-1]["end_date"], "start_price": float(selected[0]["start_price"]),
        "end_price": float(selected[-1]["end_price"]), "low": min(lows), "high": max(highs),
        "range_low_date": low_date, "range_high_date": high_date,
        "source_unit_ids": source_ids, "source_pen_ids": _source_pen_ids(selected),
        "continuous_range_id": _group_key(selected[0])[0], "sequence_id": _group_key(selected[0])[1],
        "structure_sequence_id": str(selected[0].get("structure_sequence_id", "")),
        "confirmed_at": _latest_timestamp(*(unit.get("confirmed_at") for unit in selected), selected[-1]["end_date"]),
        "unit_start_index": start, "unit_end_index": end,
        "evidence": ["COMPONENT-CENTER-FREE-001"],
    }


def _valid_center_free_span(units: list[dict[str, Any]], start: int, end: int) -> bool:
    selected = units[start:end]
    if not selected or len(selected) % 2 == 0:
        return False
    if any(unit.get("status") != "confirmed" for unit in selected):
        return False
    if len({_stream_key(unit) for unit in selected}) != 1:
        return False
    if any(not _units_are_contiguous(left, right) for left, right in zip(selected, selected[1:])):
        return False
    direction = _direction(selected[0])
    if _direction(selected[-1]) != direction:
        return False
    start_price = float(selected[0]["start_price"])
    end_price = float(selected[-1]["end_price"])
    if direction == "up":
        if end_price <= start_price + EPSILON:
            return False
        same_extremes = [float(unit["end_price"]) for unit in selected[::2]]
        if any(right <= left + EPSILON for left, right in zip(same_extremes, same_extremes[1:])):
            return False
        if any(_bounds(unit)[0] <= start_price + EPSILON for unit in selected[1::2]):
            return False
    else:
        if end_price >= start_price - EPSILON:
            return False
        same_extremes = [float(unit["end_price"]) for unit in selected[::2]]
        if any(right >= left - EPSILON for left, right in zip(same_extremes, same_extremes[1:])):
            return False
        if any(_bounds(unit)[1] >= start_price - EPSILON for unit in selected[1::2]):
            return False
    for index in range(max(0, len(selected) - 3)):
        candidate = selected[index:index + 4]
        if len(candidate) < 4 or not _alternating(candidate):
            continue
        overlap = _strict_overlap(candidate[1:])
        if overlap is None:
            continue
        zd, zg = overlap
        entry = candidate[0]
        entered = (
            float(entry["start_price"]) + EPSILON < zd < float(entry["end_price"]) - EPSILON
            if _direction(entry) == "up"
            else float(entry["start_price"]) - EPSILON > zg > float(entry["end_price"]) + EPSILON
        )
        if entered:
            return False
    return True


def _component_candidates(units: list[dict[str, Any]], start: int, level: int) -> list[dict[str, Any]]:
    candidates = []
    for end in range(start + 1, len(units) + 1, 2):
        if _valid_center_free_span(units, start, end):
            candidates.append(_component_record(units, start, end, level))
        elif end > start + 1:
            break
    return candidates


def build_center_free_components(units: list[dict[str, Any]], level: int) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    for segment in _unit_segments(units):
        cursor = 0
        while cursor < len(segment):
            candidates = _component_candidates(segment, cursor, level)
            component = candidates[-1] if candidates else _component_record(segment, cursor, cursor + 1, level)
            components.append(component)
            cursor = int(component["unit_end_index"])
    for ordinal, component in enumerate(components):
        component["ordinal"] = ordinal
    return components


def _component_seed(units: list[dict[str, Any]], start: int, level: int, core_count: int = 3):
    best = None

    def visit(parts: list[dict[str, Any]], cursor: int):
        nonlocal best
        if best is not None and cursor > best["end"]:
            return
        if len(parts) == core_count + 1:
            if not _alternating(parts):
                return
            overlap = _strict_overlap(parts[1:])
            if overlap is None:
                return
            zd, zg = overlap
            entry = parts[0]
            entered = (
                float(entry["start_price"]) + EPSILON < zd < float(entry["end_price"]) - EPSILON
                if _direction(entry) == "up"
                else float(entry["start_price"]) - EPSILON > zg > float(entry["end_price"]) + EPSILON
            )
            if not entered:
                return
            candidate = {"entry": entry, "core": parts[1:], "zd": zd, "zg": zg,
                         "direction": _direction(entry), "end": cursor}
            if best is None or (candidate["end"], sum(len(item["source_unit_ids"]) for item in parts)) < (
                best["end"], sum(len(item["source_unit_ids"]) for item in [best["entry"], *best["core"]])
            ):
                best = candidate
            return
        for component in _component_candidates(units, cursor, level):
            if parts and _direction(parts[-1]) == _direction(component):
                continue
            visit([*parts, component], int(component["unit_end_index"]))

    visit([], start)
    return best


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
        "construction_mode": "center_free_component_directional", "upgrade_kind": None if level == 1 else "directional_recursion",
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


def _flatten_component_units(components: list[dict[str, Any]]) -> list[str]:
    return list(dict.fromkeys(unit_id for component in components for unit_id in component.get("source_unit_ids", [])))


def _component_center_record(
    units: list[dict[str, Any]], seed: dict[str, Any], level: int,
    extension_components: list[dict[str, Any]], peripheral_components: list[dict[str, Any]],
    departure_components: list[dict[str, Any]], confirmed: bool, tail_status: str,
) -> dict[str, Any]:
    entry, core = seed["entry"], seed["core"]
    formation = [entry, *core]
    owned_components = sorted(
        {component["id"]: component for component in [*formation, *extension_components, *peripheral_components]}.values(),
        key=lambda component: (int(component["unit_start_index"]), int(component["unit_end_index"]), component["id"]),
    )
    source_ids = _flatten_component_units(owned_components)
    unit_by_id = {unit["id"]: unit for unit in units}
    source = [unit_by_id[unit_id] for unit_id in source_ids]
    lows, highs = zip(*(_bounds(unit) for unit in source))
    source_pen_ids = _source_pen_ids(source)
    core_unit_ids = _flatten_component_units(core)
    entry_unit_ids = list(entry["source_unit_ids"])
    child_centers = list(dict.fromkeys(center_id for unit in source for center_id in unit.get("center_ids", [])))
    record = {
        "id": _stable_id(f"directional-center-L{level}", [str(_stream_key(entry)), entry["id"], core[0]["id"], core[1]["id"]]),
        "ordinal": 0, "level_ordinal": 0, "kind": "pen_center" if level == 1 else "center",
        "role": "hierarchy", "level": level, "direction": seed["direction"],
        "entry_component_id": entry["id"], "entry_component_ids": [entry["id"]],
        "core_component_ids": [component["id"] for component in core],
        "formation_component_ids": [component["id"] for component in formation],
        "extension_component_ids": [component["id"] for component in extension_components],
        "peripheral_component_ids": [component["id"] for component in peripheral_components],
        "departure_component_ids": [component["id"] for component in departure_components],
        "retest_component_ids": [],
        "entry_unit_ids": entry_unit_ids, "entry_unit_id": entry_unit_ids[0],
        "core_unit_ids": core_unit_ids,
        "formation_unit_ids": _flatten_component_units(formation),
        "extension_unit_ids": _flatten_component_units(extension_components),
        "peripheral_unit_ids": _flatten_component_units(peripheral_components),
        "departure_unit_ids": _flatten_component_units(departure_components),
        "owned_unit_ids": source_ids if confirmed else [], "source_unit_ids": source_ids,
        "source_pen_ids": source_pen_ids, "pen_ids": source_pen_ids,
        "start_date": core[0]["start_date"], "end_date": core[-1]["end_date"],
        "core_start_date": core[0]["start_date"], "core_end_date": core[-1]["end_date"],
        "extension_end_date": source[-1]["end_date"],
        "start_price": float(core[0]["start_price"]), "end_price": float(source[-1]["end_price"]),
        "zd": seed["zd"], "zg": seed["zg"], "fixed_zd": seed["zd"], "fixed_zg": seed["zg"],
        "dd": min(lows), "gg": max(highs), "low": seed["zd"], "high": seed["zg"],
        "status": "confirmed" if confirmed else "provisional", "progress": "3/3" if confirmed else "2/3",
        "confirmed_at": _latest_timestamp(*(component.get("confirmed_at") for component in formation)) if confirmed else None,
        "tail_status": tail_status, "termination_reason": "independent_center" if tail_status == "confirmed_departure" else "right_edge",
        "completion_reason": "component_directional_core_with_extension" if confirmed else "pending_third_core_component",
        "continuous_range_id": _group_key(entry)[0], "sequence_id": _group_key(entry)[1],
        "structure_sequence_id": str(entry.get("structure_sequence_id", "")),
        "parent_center_ids": [], "child_movement_ids": source_ids if level > 1 else [],
        "child_center_ids": child_centers if level > 1 else [], "owner_movement_id": None,
        "entry_movement_id": None, "exit_movement_id": None, "transition_point_id": None,
        "transition_role": "continuation", "construction_mode": "center_free_component_directional",
        "upgrade_kind": None if level == 1 else "directional_recursion",
        "unit_start_index": int(entry["unit_start_index"]), "unit_end_index": int(seed["end"]),
        "evidence": ["CENTER-COMPONENT-ENTRY-001", "CENTER-COMPONENT-CORE-001"],
        "_component_records": list({component["id"]: component for component in [*owned_components, *departure_components]}.values()),
    }
    if level == 1:
        record.update({
            "entry_pen_id": source_pen_ids[0] if len(entry["source_pen_ids"]) == 1 else None,
            "entry_pen_ids": list(entry["source_pen_ids"]), "core_pen_ids": _source_pen_ids([unit_by_id[item] for item in core_unit_ids]),
            "formation_pen_ids": _source_pen_ids([unit_by_id[item] for item in record["formation_unit_ids"]]),
            "extension_pen_ids": _source_pen_ids([unit_by_id[item] for item in record["extension_unit_ids"]]),
            "peripheral_pen_ids": _source_pen_ids([unit_by_id[item] for item in record["peripheral_unit_ids"]]),
            "departure_pen_ids": _source_pen_ids([unit_by_id[item] for item in record["departure_unit_ids"]]),
            "start_pen": core_unit_ids[0], "end_pen": source_ids[-1],
            "start_pen_index": int(core[0]["unit_start_index"]), "end_pen_index": int(seed["end"]) - 1,
        })
    return record


def build_directional_centers(units: list[dict[str, Any]], level: int) -> list[dict[str, Any]]:
    centers = []
    for segment in _unit_segments(units):
        start = 0
        while start + 2 < len(segment):
            seed = _component_seed(segment, start, level)
            if seed is None:
                candidate = _component_seed(segment, start, level, 2) if start + 3 >= len(segment) else None
                if candidate:
                    centers.append(_component_center_record(segment, candidate, level, [], [], [], False, "pending_third_core_component"))
                start += 1
                continue
            extension, peripheral, pending = [], [], []
            next_start = None
            cursor = int(seed["end"])
            while cursor < len(segment):
                proposed = _component_seed(segment, cursor, level)
                if proposed and (proposed["zd"] > seed["zg"] + EPSILON or proposed["zg"] < seed["zd"] - EPSILON):
                    next_start = cursor
                    break
                candidates = _component_candidates(segment, cursor, level)
                component = candidates[-1] if candidates else _component_record(segment, cursor, cursor + 1, level)
                low, high = _bounds(component)
                if max(low, seed["zd"]) + EPSILON < min(high, seed["zg"]):
                    peripheral.extend(pending)
                    pending = []
                    extension.append(component)
                else:
                    pending.append(component)
                cursor = int(component["unit_end_index"])
            departure = ([proposed["entry"]] if next_start is not None and proposed else pending)
            tail_status = "confirmed_departure" if next_start is not None else "provisional_departure" if pending else "active_extension"
            centers.append(_component_center_record(segment, seed, level, extension, peripheral, departure, True, tail_status))
            if next_start is None:
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


def _component_extreme(component: dict[str, Any], direction: str) -> dict[str, Any]:
    if direction == "up":
        price = float(component["high"])
        stamp = component.get("range_high_date") or component["end_date"]
    else:
        price = float(component["low"])
        stamp = component.get("range_low_date") or component["end_date"]
    return {"trade_date": stamp, "price": price}


def build_third_buy_sell_points(
    units: list[dict[str, Any]], centers: list[dict[str, Any]],
    components: list[dict[str, Any]], level: int,
) -> list[dict[str, Any]]:
    component_by_id = {component["id"]: component for component in components}
    points: list[dict[str, Any]] = []
    for center in centers:
        if center.get("status") != "confirmed" or int(center.get("level", 1)) != level:
            continue
        core = [component_by_id.get(identifier) for identifier in center.get("core_component_ids", [])]
        if len(core) != 3 or any(component is None for component in core):
            continue
        departure = core[-1]
        point_type = None
        if _direction(departure) == "up" and float(departure["end_price"]) > float(center["zg"]) + EPSILON:
            point_type = "third_buy"
        elif _direction(departure) == "down" and float(departure["end_price"]) < float(center["zd"]) - EPSILON:
            point_type = "third_sell"
        if point_type is None:
            cursor = int(center.get("unit_end_index", departure["unit_end_index"]))
            candidates = _component_candidates(units, cursor, level) if cursor < len(units) else []
            departure = candidates[0] if candidates else None
            if departure and _direction(departure) == "up" and float(departure["end_price"]) > float(center["zg"]) + EPSILON:
                point_type = "third_buy"
            elif departure and _direction(departure) == "down" and float(departure["end_price"]) < float(center["zd"]) - EPSILON:
                point_type = "third_sell"
        if point_type is None or departure is None:
            continue
        retest_start = int(departure["unit_end_index"])
        retest_candidates = _component_candidates(units, retest_start, level) if retest_start < len(units) else []
        retest = next((component for component in retest_candidates if _direction(component) != _direction(departure)), None)
        status = "candidate"
        if retest:
            if point_type == "third_buy" and float(retest["low"]) > float(center["zg"]) + EPSILON:
                status = "confirmed"
            elif point_type == "third_sell" and float(retest["high"]) < float(center["zd"]) - EPSILON:
                status = "confirmed"
            else:
                status = "invalidated"
        extreme = _component_extreme(retest or departure, "down" if point_type == "third_buy" else "up")
        transition_unit_id = core[0]["source_unit_ids"][0]
        point = {
            "id": _stable_id(f"{point_type}-L{level}", [center["id"], departure["id"], retest["id"] if retest else "pending"]),
            "kind": "buy_sell_point", "level": level, "point_type": point_type, "status": status,
            "point_date": extreme["trade_date"], "point_price": extreme["price"],
            "confirmed_at": retest.get("confirmed_at") if status == "confirmed" and retest else None,
            "center_id": center["id"], "movement_id": None,
            "source_component_ids": [departure["id"], *([retest["id"]] if retest else [])],
            "source_unit_ids": _flatten_component_units([departure, *([retest] if retest else [])]),
            "departure_component_id": departure["id"], "retest_component_id": retest["id"] if retest else None,
            "transition_unit_id": transition_unit_id,
            "transition_date": core[0]["start_date"], "transition_price": float(core[0]["start_price"]),
            "invalidation_price": float(center["zg"] if point_type == "third_buy" else center["zd"]),
            "divergence_evidence": None,
            "continuous_range_id": center.get("continuous_range_id", 0),
            "sequence_id": center.get("sequence_id", 0),
            "structure_sequence_id": center.get("structure_sequence_id", ""),
            "evidence": ["POINT-THIRD-RETEST-001"],
            "_component_records": [departure, *([retest] if retest else [])],
        }
        center["departure_component_ids"] = [departure["id"]]
        center["retest_component_ids"] = [retest["id"]] if retest else []
        points.append(point)
    for ordinal, point in enumerate(points):
        point["ordinal"] = ordinal
    return points


def _macd_component_stats(component: dict[str, Any], macd: list[dict[str, Any]]) -> dict[str, float] | None:
    values = [item for item in macd if component["start_date"] < item["trade_date"] <= component["end_date"]]
    if not values:
        return None
    direction = _direction(component)
    histograms = [float(item["histogram"]) for item in values]
    dif_values = [float(item["dif"]) for item in values]
    area = sum(abs(value) for value in histograms if value < 0) if direction == "down" else sum(value for value in histograms if value > 0)
    dif_extreme = min(dif_values) if direction == "down" else max(dif_values)
    return {"area": area, "dif_extreme": dif_extreme}


def build_first_second_buy_sell_points(
    units: list[dict[str, Any]], centers: list[dict[str, Any]],
    components: list[dict[str, Any]], macd: list[dict[str, Any]], level: int,
) -> list[dict[str, Any]]:
    component_by_id = {component["id"]: component for component in components}
    ordered_components = sorted(components, key=lambda item: (item["start_date"], item["end_date"], item["id"]))
    points: list[dict[str, Any]] = []
    grouped: dict[tuple[int, int, str], list[dict[str, Any]]] = {}
    for center in centers:
        if center.get("status") == "confirmed" and int(center.get("level", 1)) == level:
            grouped.setdefault(_stream_key(center), []).append(center)
    for stream_centers in grouped.values():
        stream_centers.sort(key=lambda item: (item["start_date"], item["id"]))
        for previous, current in zip(stream_centers, stream_centers[1:]):
            direction = current.get("direction")
            if direction != previous.get("direction") or direction not in {"up", "down"}:
                continue
            separated = (float(current["zg"]) < float(previous["zd"]) - EPSILON if direction == "down"
                         else float(current["zd"]) > float(previous["zg"]) + EPSILON)
            if not separated:
                continue
            previous_entry = component_by_id.get(previous.get("entry_component_id"))
            current_entry = component_by_id.get(current.get("entry_component_id"))
            if not previous_entry or not current_entry:
                continue
            price_extreme = (float(current_entry["low"]) < float(previous_entry["low"]) - EPSILON if direction == "down"
                             else float(current_entry["high"]) > float(previous_entry["high"]) + EPSILON)
            previous_stats = _macd_component_stats(previous_entry, macd)
            current_stats = _macd_component_stats(current_entry, macd)
            divergence = False
            area_improved = False
            dif_improved = False
            if price_extreme and previous_stats and current_stats:
                area_improved = current_stats["area"] + EPSILON < previous_stats["area"]
                dif_improved = (current_stats["dif_extreme"] > previous_stats["dif_extreme"] + EPSILON
                                if direction == "down" else current_stats["dif_extreme"] < previous_stats["dif_extreme"] - EPSILON)
                divergence = area_improved and dif_improved
            point_type = "first_buy" if direction == "down" else "first_sell"
            extreme = _component_extreme(current_entry, direction)
            core_ids = current.get("core_component_ids", [])
            confirming = component_by_id.get(core_ids[0]) if core_ids else None
            point = {
                "id": _stable_id(f"{point_type}-L{level}", [previous["id"], current["id"], current_entry["id"]]),
                "kind": "buy_sell_point", "level": level, "point_type": point_type,
                "status": "confirmed" if divergence and confirming else "candidate",
                "point_date": extreme["trade_date"], "point_price": extreme["price"],
                "confirmed_at": confirming.get("confirmed_at") if divergence and confirming else None,
                "center_id": current["id"], "movement_id": None,
                "source_component_ids": [previous_entry["id"], current_entry["id"]],
                "source_unit_ids": _flatten_component_units([previous_entry, current_entry]),
                "departure_component_id": current_entry["id"], "retest_component_id": None,
                "transition_unit_id": confirming["source_unit_ids"][0] if confirming else current_entry["source_unit_ids"][-1],
                "transition_date": current_entry["end_date"], "transition_price": float(current_entry["end_price"]),
                "invalidation_price": extreme["price"],
                "divergence_evidence": {
                    "previous_area": previous_stats["area"] if previous_stats else None,
                    "current_area": current_stats["area"] if current_stats else None,
                    "previous_dif_extreme": previous_stats["dif_extreme"] if previous_stats else None,
                    "current_dif_extreme": current_stats["dif_extreme"] if current_stats else None,
                    "price_extreme": price_extreme,
                    "area_improved": bool(area_improved) if previous_stats and current_stats else False,
                    "dif_improved": bool(dif_improved) if previous_stats and current_stats else False,
                },
                "continuous_range_id": current.get("continuous_range_id", 0),
                "sequence_id": current.get("sequence_id", 0), "structure_sequence_id": current.get("structure_sequence_id", ""),
                "evidence": ["POINT-FIRST-MACD-DUAL-001"],
            }
            points.append(point)
            if point["status"] != "confirmed":
                continue
            entry_end = int(current_entry["unit_end_index"])
            following = [component for component in ordered_components
                         if _stream_key(component) == _stream_key(current_entry)
                         and int(component["unit_start_index"]) >= entry_end]
            advance = next((component for component in following if _direction(component) != direction), None)
            retest = next((component for component in following
                           if advance and int(component["unit_start_index"]) >= int(advance["unit_end_index"])
                           and _direction(component) == direction), None)
            confirm = next((component for component in following
                            if retest and int(component["unit_start_index"]) >= int(retest["unit_end_index"])
                            and _direction(component) != direction), None)
            valid_retest = False
            if retest:
                valid_retest = (float(retest["low"]) > point["point_price"] + EPSILON if point_type == "first_buy"
                                else float(retest["high"]) < point["point_price"] - EPSILON)
            if not retest:
                continue
            second_type = "second_buy" if point_type == "first_buy" else "second_sell"
            second_extreme = _component_extreme(retest, "down" if second_type == "second_buy" else "up")
            points.append({
                "id": _stable_id(f"{second_type}-L{level}", [point["id"], retest["id"]]),
                "kind": "buy_sell_point", "level": level, "point_type": second_type,
                "status": "confirmed" if valid_retest and confirm else "candidate",
                "point_date": second_extreme["trade_date"], "point_price": second_extreme["price"],
                "confirmed_at": confirm.get("confirmed_at") if valid_retest and confirm else None,
                "center_id": current["id"], "movement_id": None, "parent_point_id": point["id"],
                "source_component_ids": [advance["id"], retest["id"], *([confirm["id"]] if confirm else [])],
                "source_unit_ids": _flatten_component_units([advance, retest, *([confirm] if confirm else [])]),
                "departure_component_id": advance["id"], "retest_component_id": retest["id"],
                "transition_unit_id": retest["source_unit_ids"][0],
                "transition_date": retest["start_date"], "transition_price": float(retest["start_price"]),
                "invalidation_price": point["point_price"], "divergence_evidence": None,
                "continuous_range_id": current.get("continuous_range_id", 0),
                "sequence_id": current.get("sequence_id", 0), "structure_sequence_id": current.get("structure_sequence_id", ""),
                "evidence": ["POINT-SECOND-RETEST-001"],
            })
    return points


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
    turning_referenced = movement.get("turning_center_ids") or []
    source_ids = movement.get("source_unit_ids") or []
    if len(source_ids) != len(set(source_ids)):
        errors.append("走势重复消费源单位")
    if not referenced and not turning_referenced:
        errors.append("走势缺少真实所属中枢")
    if any(center_id not in centers for center_id in [*referenced, *turning_referenced]):
        errors.append("走势引用不存在的中枢")
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
    primary = movement.get("primary_confirmation") or {}
    if primary.get("type") in {"first_buy", "first_sell", "third_buy", "third_sell"}:
        if (
            movement.get("termination_reason") != primary.get("type")
            or movement.get("confirmation_point_id") != primary.get("signal_id")
            or not movement.get("confirmed_at")
            or movement.get("confirmed_at") != primary.get("confirmed_at")
            or movement.get("state", "formed") != "formed"
            or not _boundary_matches_direction(float(movement["start_price"]), float(movement["end_price"]), movement["direction"])
        ):
            errors.append("买卖点确认的时间、方向或结束原因不一致")
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
        "construction_mode": "center_free_component_directional", "boundary_mode": "earliest_valid_structural_evidence",
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


def _interval_classification(centers: list[dict[str, Any]], direction: str) -> str | None:
    if not centers:
        return "consolidation"
    aligned = [center for center in centers if center.get("direction") == direction]
    if len(aligned) != len(centers):
        return None
    return _movement_classification(aligned, direction)


def rebuild_movements_with_points(
    units: list[dict[str, Any]], centers: list[dict[str, Any]], movements: list[dict[str, Any]],
    points: list[dict[str, Any]], level: int, origin: str = "system",
) -> list[dict[str, Any]]:
    confirmed_points = [point for point in points if point.get("status") == "confirmed" and int(point.get("level", 1)) == level]
    if not confirmed_points:
        return movements
    rebuilt: list[dict[str, Any]] = []
    for segment in _unit_segments(units):
        unit_index = {unit["id"]: index for index, unit in enumerate(segment)}
        segment_centers = []
        spans = {}
        for center in centers:
            span = _center_unit_span(center, segment, unit_index)
            if span is not None and int(center.get("level", 1)) == level:
                spans[center["id"]] = span
                segment_centers.append(center)
        if not segment_centers:
            continue
        # First/third points change the movement boundary. Second points are
        # evidence attached to an already locked movement and must not split it.
        events = [point for point in confirmed_points
                  if point.get("point_type") in {"first_buy", "first_sell", "third_buy", "third_sell"}
                  and point.get("transition_unit_id") in unit_index]
        original_movements = sorted(
            (movement for movement in movements if _group_key(movement) == _group_key(segment[0])),
            key=lambda movement: (movement["start_date"], movement["end_date"], movement["id"]),
        )
        if not events:
            rebuilt.extend(original_movements)
            continue
        events.sort(key=lambda point: (point["confirmed_at"], unit_index[point["transition_unit_id"]], point["id"]))
        first_event = events[0]
        first_boundary = unit_index[first_event["transition_unit_id"]]
        target = next((movement for movement in original_movements
                       if first_event["transition_unit_id"] in movement.get("source_unit_ids", [])), None)
        if target is None:
            rebuilt.extend(original_movements)
            continue
        target_start_id = target["source_unit_ids"][0]
        start = unit_index[target_start_id]
        direction = target["direction"]
        rebuilt.extend(movement for movement in original_movements
                       if movement["end_date"] <= target["start_date"] and movement["id"] != target["id"])
        sequence = _stable_id(f"structure-sequence-L{level}", [str(_stream_key(segment[0])), segment[0]["id"], "points"])

        def make_movement(end: int, event: dict[str, Any] | None, next_direction: str | None = None):
            nonlocal start, direction
            selected = segment[start:end]
            if not selected:
                return None
            complete_centers = [center for center in segment_centers
                                if spans[center["id"]][0] >= start and spans[center["id"]][1] <= end]
            turning = [center for center in segment_centers
                       if spans[center["id"]][0] < end < spans[center["id"]][1]]
            start_point = {"trade_date": selected[0]["start_date"], "price": float(selected[0]["start_price"]),
                           "boundary": start, "unit_id": selected[0]["id"]}
            endpoint = {"trade_date": selected[-1]["end_date"], "price": float(selected[-1]["end_price"]),
                        "boundary": end, "unit_id": selected[-1]["id"]}
            low, high = _range_extreme(selected, "down"), _range_extreme(selected, "up")
            classification = _interval_classification(complete_centers, direction)
            status = "confirmed" if event and classification is not None else "provisional"
            confirmation = ({"type": event["point_type"], "signal_id": event["id"],
                             "confirmed_at": event["confirmed_at"]} if event else None)
            movement_id = _stable_id(f"movement-L{level}", [sequence, selected[0]["id"], direction])
            movement = {
                "id": movement_id, "kind": "movement", "role": "hierarchy_component", "level": level,
                "state": "undetermined" if classification is None else "formed", "direction": direction,
                "classification": classification, "status": status,
                "start_date": start_point["trade_date"], "start_price": start_point["price"],
                "end_date": endpoint["trade_date"], "end_price": endpoint["price"],
                "low": low["price"], "high": high["price"],
                "range_low_date": low["trade_date"], "range_low_price": low["price"],
                "range_high_date": high["trade_date"], "range_high_price": high["price"],
                "confirmed_at": event.get("confirmed_at") if status == "confirmed" and event else None,
                "center_ids": [center["id"] for center in complete_centers],
                "turning_center_ids": [center["id"] for center in turning],
                "center_count": len(complete_centers), "confirmation_center_id": None,
                "confirmation_point_id": event["id"] if status == "confirmed" and event else None,
                "primary_confirmation": confirmation if status == "confirmed" else None,
                "confirmation_events": [confirmation] if status == "confirmed" and confirmation else [],
                "child_movement_ids": [unit["id"] for unit in selected if unit.get("kind") == "movement"],
                "source_unit_ids": [unit["id"] for unit in selected], "source_pen_ids": _source_pen_ids(selected),
                "continuous_range_id": _group_key(segment[0])[0], "sequence_id": _group_key(segment[0])[1],
                "structure_sequence_id": sequence, "origin": origin,
                "termination_reason": event["point_type"] if status == "confirmed" and event else "provisional_tail",
                "start_boundary": start, "end_boundary": end, "source_end_boundary": end,
                "boundary_source_unit_id": endpoint["unit_id"], "boundary_source_price": endpoint["price"],
                "endpoint_points": [start_point, endpoint], "path_points": [start_point, endpoint],
                "candidate_extreme_date": None, "candidate_extreme_price": None,
                "tail_end_date": endpoint["trade_date"], "tail_end_price": endpoint["price"],
                "construction_mode": "center_free_component_directional",
                "boundary_mode": "earliest_valid_structural_evidence",
                "recursive_eligible": status == "confirmed",
                "undetermined_reason": "mixed_center_relation" if classification is None else None,
                "evidence": ["MOVEMENT-POINT-CONFIRMATION-001", "MOVEMENT-SHARED-ENDPOINT-001"],
                "issues": [],
            }
            for center in complete_centers:
                center["owner_movement_id"] = movement_id
            if event and status == "confirmed":
                event["movement_id"] = movement_id
            rebuilt.append(movement)
            start = end
            if next_direction:
                direction = next_direction
            return movement

        for event in events:
            boundary = unit_index[event["transition_unit_id"]]
            next_direction = "up" if event["point_type"].endswith("buy") else "down"
            if boundary <= start or direction == next_direction:
                continue
            selected = segment[start:boundary]
            if not selected or not _boundary_matches_direction(
                float(selected[0]["start_price"]), float(selected[-1]["end_price"]), direction,
            ):
                continue
            previous = make_movement(boundary, event, next_direction)
            turning_center = next((center for center in segment_centers if center["id"] == event["center_id"]), None)
            if previous and turning_center:
                turning_center["transition_role"] = "turning"
                turning_center["entry_movement_id"] = previous["id"]
                turning_center["transition_point_id"] = event["id"]
        tail = make_movement(len(segment), None)
        if tail:
            for event in reversed(events):
                if event.get("transition_unit_id") in tail["source_unit_ids"] or event.get("transition_date") == tail["start_date"]:
                    center = next((item for item in segment_centers if item["id"] == event["center_id"]), None)
                    if center:
                        center["exit_movement_id"] = tail["id"]
                        event["movement_id"] = tail["id"]
                    break
    movement_by_id = {movement["id"]: movement for movement in rebuilt}
    for center in centers:
        exit_movement = movement_by_id.get(center.get("exit_movement_id"))
        if exit_movement and center.get("transition_role") == "turning":
            turning_ids = exit_movement.setdefault("turning_center_ids", [])
            if center["id"] not in turning_ids:
                turning_ids.append(center["id"])
    # A turning center is referenced from both sides of the boundary: its
    # entry belongs to the prior movement, while its exit belongs to the new
    # movement that starts at the transition unit.
    for point in confirmed_points:
        center = next((center for center in centers if center.get("id") == point.get("center_id")), None)
        if not center:
            continue
        if point.get("point_type") in {"first_buy", "first_sell", "third_buy", "third_sell"} and center.get("transition_role") != "turning":
            continue
        matching = [movement for movement in rebuilt if point.get("transition_unit_id") in movement.get("source_unit_ids", [])]
        if point.get("point_type") in {"second_buy", "second_sell"}:
            matching = [movement for movement in rebuilt if center.get("id") in movement.get("center_ids", [])]
        if not matching:
            continue
        next_direction = "up" if point.get("point_type", "").endswith("buy") else "down"
        exit_movement = next((movement for movement in matching if movement.get("direction") == next_direction), None)
        if exit_movement:
            center["exit_movement_id"] = exit_movement["id"]
            if point.get("point_type") in {"first_buy", "first_sell", "third_buy", "third_sell"}:
                point["movement_id"] = next((movement["id"] for movement in matching if movement.get("direction") != next_direction), point.get("movement_id"))
        if point.get("point_type") in {"second_buy", "second_sell"}:
            point["movement_id"] = matching[0]["id"]
            event = {"type": point["point_type"], "signal_id": point["id"], "confirmed_at": point.get("confirmed_at")}
            for movement in matching:
                confirmation_events = movement.setdefault("confirmation_events", [])
                if not any(item.get("signal_id") == point["id"] for item in confirmation_events):
                    confirmation_events.append(event)
    for ordinal, movement in enumerate(rebuilt):
        movement["ordinal"] = ordinal
    return rebuilt


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


def build_hierarchy(
    pens: list[dict[str, Any]], l1_centers: list[dict[str, Any]], origin: str = "system",
    macd: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
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
        normalized.setdefault("construction_mode", "center_free_component_directional")
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
    components: list[dict[str, Any]] = []
    buy_sell_points: list[dict[str, Any]] = []
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
        level_components = build_center_free_components(units, level)
        component_registry = {component["id"]: component for component in level_components}
        for center in structural_centers:
            for component in center.get("_component_records", []):
                component_registry[component["id"]] = component
        level_components = sorted(component_registry.values(), key=lambda item: (
            item["start_date"], item["end_date"], item["id"],
        ))
        components.extend(level_components)
        level_points = build_third_buy_sell_points(units, structural_centers, level_components, level)
        level_points.extend(build_first_second_buy_sell_points(
            units, structural_centers, level_components, macd or [], level,
        ))
        for point in level_points:
            for component in point.get("_component_records", []):
                if component["id"] not in component_registry:
                    component_registry[component["id"]] = component
                    level_components.append(component)
                    components.append(component)
        level_points.sort(key=lambda item: (item.get("point_date", ""), item["id"]))
        for point_ordinal, point in enumerate(level_points):
            point["ordinal"] = point_ordinal
        buy_sell_points.extend(level_points)
        component_result = build_hierarchy_components(units, structural_centers, level, origin=origin)
        updated = {center["id"]: center for center in component_result["centers"]}
        for center in structural_centers:
            if center["id"] in updated:
                center.update(updated[center["id"]])
        unassigned_by_level[str(level)] = component_result["unassigned"]
        hierarchy_issues.extend(component_result["issues"])
        component_movements = rebuild_movements_with_points(
            units, structural_centers, component_result["movements"], level_points, level, origin,
        )
        covered = {unit_id for movement in component_movements for unit_id in movement.get("source_unit_ids", [])}
        unassigned_by_level[str(level)] = [unit["id"] for unit in units if unit["id"] not in covered]
        movements.extend(component_movements)

        segmented_components = _recursive_units(component_movements)
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
        item.pop("_component_records", None)
        item["ordinal"] = ordinal
    per_level: dict[int, int] = {}
    for item in centers:
        per_level[item["level"]] = per_level.get(item["level"], 0) + 1
        item["level_ordinal"] = per_level[item["level"]] - 1
    for ordinal, item in enumerate(relations):
        item["ordinal"] = ordinal
    for ordinal, item in enumerate(movements):
        item["ordinal"] = ordinal
    for ordinal, item in enumerate(components):
        item["ordinal"] = ordinal
    for ordinal, item in enumerate(buy_sell_points):
        item.pop("_component_records", None)
        item["ordinal"] = ordinal
    confirmed_levels = [center["level"] for center in hierarchy_centers if center["status"] == "confirmed"]
    available_levels = [center["level"] for center in hierarchy_centers]
    return {
        "centers": centers,
        "center_relations": relations,
        "movements": movements,
        "components": components,
        "buy_sell_points": buy_sell_points,
        "max_confirmed_center_level": max(confirmed_levels, default=0),
        "max_available_center_level": max(available_levels, default=0),
        "hierarchy_version": HIERARCHY_VERSION,
        "unassigned_by_level": unassigned_by_level, "hierarchy_issues": hierarchy_issues,
    }
