from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Iterable

from .chan_direction import breakout_context, select_context
from .chan_expansion import decomposition_proofs, internal_decomposition_proofs


EPSILON = 1e-9
MAX_LEVEL = 8
HIERARCHY_VERSION = "center-hierarchy-v28-directional-z"


def _stable_id(prefix: str, *values: Any) -> str:
    raw = "|".join(str(value) for value in values)
    return f"{prefix}-{hashlib.sha256(raw.encode()).hexdigest()[:14]}"


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _low(unit: dict[str, Any]) -> float:
    return float(unit.get("low", min(float(unit["start_price"]), float(unit["end_price"]))))


def _high(unit: dict[str, Any]) -> float:
    return float(unit.get("high", max(float(unit["start_price"]), float(unit["end_price"]))))


def _direction(unit: dict[str, Any]) -> str:
    direction = unit.get("direction")
    if direction in {"up", "down"}:
        return direction
    return "up" if float(unit["end_price"]) > float(unit["start_price"]) else "down"


def _stream_key(unit: dict[str, Any]) -> tuple[int, int, str]:
    return (
        int(unit.get("continuous_range_id", unit.get("range_index", 0))),
        int(unit.get("sequence_id", 0)),
        str(unit.get("structure_sequence_id", "")),
    )


def _strict_interval_overlap(low_a: float, high_a: float, low_b: float, high_b: float) -> bool:
    return max(low_a, low_b) + EPSILON < min(high_a, high_b)


def _closed_interval_overlap(low_a: float, high_a: float, low_b: float, high_b: float) -> bool:
    return max(low_a, low_b) <= min(high_a, high_b) + EPSILON


def _crosses_out_of_core(unit: dict[str, Any], zd: float, zg: float) -> bool:
    start = float(unit["start_price"])
    end = float(unit["end_price"])
    starts_at_core = zd - EPSILON <= start <= zg + EPSILON
    return starts_at_core and (end < zd - EPSILON or end > zg + EPSILON)


def _strict_overlap(units: list[dict[str, Any]]) -> tuple[float, float] | None:
    if not units:
        return None
    zd = max(_low(unit) for unit in units)
    zg = min(_high(unit) for unit in units)
    return (zd, zg) if zd + EPSILON < zg else None


def _alternating(units: list[dict[str, Any]]) -> bool:
    return all(_direction(left) != _direction(right) for left, right in zip(units, units[1:]))


def _contiguous(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        _stream_key(left) == _stream_key(right)
        and left["end_date"] == right["start_date"]
        and abs(float(left["end_price"]) - float(right["start_price"])) <= EPSILON
    )


def _span_contiguous(units: list[dict[str, Any]]) -> bool:
    return all(_contiguous(left, right) for left, right in zip(units, units[1:]))


def _component_direction(units: list[dict[str, Any]]) -> str | None:
    if not units:
        return None
    start = float(units[0]["start_price"])
    end = float(units[-1]["end_price"])
    if end > start + EPSILON:
        return "up"
    if end < start - EPSILON:
        return "down"
    return None


def _contains_core(units: list[dict[str, Any]]) -> bool:
    return any(
        _alternating(units[index:index + 3]) and _strict_overlap(units[index:index + 3]) is not None
        for index in range(max(0, len(units) - 2))
    )


def _center_free(units: list[dict[str, Any]]) -> bool:
    return bool(units) and _span_contiguous(units) and _component_direction(units) is not None and not _contains_core(units)


def _source_pen_ids(units: Iterable[dict[str, Any]]) -> list[str]:
    values: list[str] = []
    for unit in units:
        if unit.get("kind") == "pen":
            values.append(str(unit["id"]))
        values.extend(str(item) for item in unit.get("source_pen_ids", []))
    return _unique(values)


def atomic_pen_units(pens: list[dict[str, Any]]) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    for ordinal, pen in enumerate(pens):
        if pen.get("status", "confirmed") != "confirmed":
            continue
        units.append({
            "id": str(pen["id"]),
            "kind": "pen",
            "level": 0,
            "ordinal": ordinal,
            "direction": _direction(pen),
            "start_date": pen["start_date"],
            "end_date": pen["end_date"],
            "start_price": float(pen["start_price"]),
            "end_price": float(pen["end_price"]),
            "low": min(float(pen["start_price"]), float(pen["end_price"])),
            "high": max(float(pen["start_price"]), float(pen["end_price"])),
            "status": "confirmed",
            "continuous_range_id": int(pen.get("continuous_range_id", pen.get("range_index", 0))),
            "sequence_id": int(pen.get("sequence_id", 0)),
            "structure_sequence_id": str(pen.get("structure_sequence_id", "")),
            "source_pen_ids": [str(pen["id"])],
            "confirmed_at": pen.get("confirmed_at", pen["end_date"]),
        })
    return units


def movement_units(movements: list[dict[str, Any]], level: int) -> list[dict[str, Any]]:
    eligible = [
        movement for movement in movements
        if movement.get("status") == "confirmed"
        and movement.get("recursive_eligible")
        and movement.get("classification") in {"consolidation", "trend"}
        and movement.get("confirmed_at")
        and int(movement.get("level", 0)) == level
    ]
    eligible.sort(key=lambda item: (item["start_date"], item["end_date"], item["id"]))
    units: list[dict[str, Any]] = []
    for ordinal, movement in enumerate(eligible):
        units.append({
            "id": movement["id"],
            "kind": "movement",
            "level": level,
            "ordinal": ordinal,
            "direction": movement["direction"],
            "start_date": movement["start_date"],
            "end_date": movement["end_date"],
            "start_price": float(movement["start_price"]),
            "end_price": float(movement["end_price"]),
            "low": float(movement["price_envelope_low"]),
            "high": float(movement["price_envelope_high"]),
            "status": "confirmed",
            "continuous_range_id": int(movement.get("continuous_range_id", 0)),
            "sequence_id": int(movement.get("sequence_id", 0)),
            "structure_sequence_id": str(movement.get("structure_sequence_id", "")),
            "source_pen_ids": list(movement.get("source_pen_ids", [])),
            "confirmed_at": movement.get("confirmed_at", movement["end_date"]),
            "max_internal_center_level": max(
                [int(value) for value in movement.get("center_levels", [])] or [level]
            ),
            "confirmed_child_movement_count": len(movement.get("child_movement_ids", [])),
        })
    return units


def _split_streams(units: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    streams: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for unit in sorted(units, key=lambda item: (item["start_date"], item["end_date"], item["id"])):
        if unit.get("status") != "confirmed":
            if current:
                streams.append(current)
                current = []
            continue
        if current and (not _contiguous(current[-1], unit) or _stream_key(current[-1]) != _stream_key(unit)):
            streams.append(current)
            current = []
        current.append(unit)
    if current:
        streams.append(current)
    return streams


@dataclass(frozen=True)
class CoreSeed:
    workspace_start: int
    core_start: int
    core_end: int
    zd: float
    zg: float
    direction_context: dict[str, Any] | None = None


def _find_earliest_core(units: list[dict[str, Any]], workspace_start: int, core_floor: int = 0,
                        direction_context: dict[str, Any] | None = None) -> CoreSeed | None:
    context = direction_context or select_context(units, workspace_start, [u["direction_evidence"] for u in units if u.get("direction_evidence")])
    if not context:
        return None
    expected = "down" if context["process_direction"] == "up" else "up"
    floor = max(workspace_start, core_floor, int(context.get("start_index", workspace_start)) + 1)
    for start in range(floor, len(units) - 2):
        core = units[start:start + 3]
        if _direction(core[0]) != expected or not _alternating(core) or not _span_contiguous(core):
            continue
        overlap = _strict_overlap(core)
        if overlap is not None and _strict_overlap([core[0], core[2]]) == overlap:
            return CoreSeed(workspace_start, start, start + 3, *overlap, context)
    return None


def _component_record(
    units: list[dict[str, Any]], level: int, role: str, owner_id: str | None = None,
) -> dict[str, Any] | None:
    if not units:
        return None
    direction = _component_direction(units)
    component_id = _stable_id("component", level, role, units[0]["id"], units[-1]["id"])
    return {
        "id": component_id,
        "kind": "component",
        "level": level,
        "role": role,
        "owner_id": owner_id,
        "direction": direction,
        "status": "confirmed" if all(unit.get("status") == "confirmed" for unit in units) else "provisional",
        "start_date": units[0]["start_date"],
        "end_date": units[-1]["end_date"],
        "start_price": float(units[0]["start_price"]),
        "end_price": float(units[-1]["end_price"]),
        "low": min(_low(unit) for unit in units),
        "high": max(_high(unit) for unit in units),
        "source_unit_ids": [unit["id"] for unit in units],
        "unit_kind": units[0].get("kind", "pen" if level == 1 else "movement"),
        "confirmed_at": max(unit.get("confirmed_at") or unit["end_date"] for unit in units),
        "source_pen_ids": _source_pen_ids(units),
        "continuous_range_id": _stream_key(units[0])[0],
        "sequence_id": _stream_key(units[0])[1],
        "structure_sequence_id": _stream_key(units[0])[2],
        "evidence": {
            "center_free": not _contains_core(units),
            "unit_count": len(units),
        },
    }


def _make_center(
    units: list[dict[str, Any]], seed: CoreSeed, level: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    entry = units[seed.workspace_start:seed.core_start]
    core = units[seed.core_start:seed.core_end]
    z_units = [core[0], core[2]]
    family_id = _stable_id("center-family", level, _stream_key(core[0]), core[0]["id"])
    revision_id = f"{family_id}:r1"
    entry_component = _component_record(entry, level, "entry" if _center_free(entry) else "pre_core", family_id)
    center = {
        "id": revision_id,
        "kind": "center",
        "family_id": family_id,
        "revision_no": 1,
        "previous_revision_id": None,
        "active": True,
        "level": level,
        "status": "formed",
        "start_date": core[0]["start_date"],
        "end_date": core[-1]["end_date"],
        "core_start_date": core[0]["start_date"],
        "core_end_date": core[-1]["end_date"],
        "entry_component_id": entry_component["id"] if entry_component else None,
        "entry_unit_ids": [unit["id"] for unit in entry],
        "pre_core_unit_ids": [unit["id"] for unit in entry],
        "direction_context": deepcopy(seed.direction_context),
        "process_direction_at_formation": (seed.direction_context or {}).get("process_direction", "unknown"),
        "formation_type": ("pullback" if seed.direction_context["process_direction"] == "up" else "rebound") if seed.direction_context else "undetermined",
        "formation_stage": "directional" if seed.direction_context else "origin_overlap",
        "direction_established_at": (seed.direction_context or {}).get("available_at"),
        "core_unit_ids": [unit["id"] for unit in core],
        "evidence_cursor_unit_id": core[-1]["id"],
        "unit_kind": "pen" if level == 1 else "movement",
        "z_unit_ids": [unit["id"] for unit in z_units],
        "z_direction": _direction(core[0]),
        "formed_at": max([unit.get("confirmed_at") or unit["end_date"] for unit in [*entry, *core]] + [(seed.direction_context or {}).get("available_at", "")]),
        "promotion_confirmed_at": None,
        "connection_component_ids": [],
        "overlap_witness_unit_ids": [],
        "missing_evidence": [],
        "extension_unit_ids": [],
        "peripheral_unit_ids": [],
        "departure_unit_ids": [],
        "retest_unit_ids": [],
        "owned_unit_ids": [unit["id"] for unit in core],
        "context_unit_ids": [unit["id"] for unit in [*entry, *core]],
        "source_pen_ids": _source_pen_ids(core),
        "child_center_ids": [],
        "child_movement_ids": [],
        "zd": seed.zd,
        "zg": seed.zg,
        "fixed_zd": seed.zd,
        "fixed_zg": seed.zg,
        "dd": min(_low(unit) for unit in z_units),
        "gg": max(_high(unit) for unit in z_units),
        "z_high_min": min(_high(unit) for unit in z_units),
        "z_low_max": max(_low(unit) for unit in z_units),
        "touch_unit_ids": [],
        "fluctuation_dd": min(_low(unit) for unit in z_units),
        "fluctuation_gg": max(_high(unit) for unit in z_units),
        "context_low": min(_low(unit) for unit in [*entry, *core]),
        "context_high": max(_high(unit) for unit in [*entry, *core]),
        "entry_direction": _component_direction(entry),
        "core_formation_pattern": "-".join(_direction(unit) for unit in core),
        "departure_direction": None,
        "owner_movement_id": None,
        "formation_modes": ["directional_core" if seed.direction_context else "origin_overlap"],
        "recursive_eligible": bool(seed.direction_context),
        "continuous_range_id": _stream_key(core[0])[0],
        "sequence_id": _stream_key(core[0])[1],
        "structure_sequence_id": _stream_key(core[0])[2],
        "evidence": {
            "entry_is_center_free": _center_free(entry),
            "strict_core": True,
            "z_endpoints": [{key: unit[key] for key in ("id", "start_date", "end_date", "start_price", "end_price", "confirmed_at")} for unit in z_units],
        },
        "_workspace_start": seed.workspace_start,
        "_core_start": seed.core_start,
        "_core_end": seed.core_end,
        "_last_owned": seed.core_end - 1,
        "_units": {unit["id"]: unit for unit in units},
    }
    _capture_center_revision(center, center["formed_at"])
    return center, entry_component


def _capture_center_revision(center: dict[str, Any], available_at: str) -> None:
    context = [center["_units"][identifier] for identifier in center["context_unit_ids"]]
    center["context_low"] = min(_low(unit) for unit in context)
    center["context_high"] = max(_high(unit) for unit in context)
    history = center.setdefault("_history", [])
    revision_no = len(history) + 1
    center["previous_revision_id"] = history[-1]["id"] if history else None
    center["revision_no"] = revision_no
    center["id"] = f"{center['family_id']}:r{revision_no}"
    available_at = max([available_at, center["formed_at"], *[u["confirmed_at"] for u in context]])
    center["revision_at"] = available_at
    center["available_at"] = available_at
    history.append(deepcopy({key: value for key, value in center.items() if not key.startswith("_")}))


def _absorb_return(
    center: dict[str, Any], units: list[dict[str, Any]], start: int, end: int,
    return_available_at: str = "",
) -> None:
    z_direction = center["core_formation_pattern"].split("-")[0]
    center["departure_unit_ids"] = []
    center["departure_component_id"] = None
    center["departure_direction"] = None
    center["context_unit_ids"] = _unique([*center["entry_unit_ids"], *center["owned_unit_ids"]])
    available_at = max([center["revision_at"], return_available_at, *[unit.get("confirmed_at") or unit["end_date"] for unit in units[start:end]]])
    for unit in units[start:end]:
        target = "extension_unit_ids" if _direction(unit) == z_direction and _closed_interval_overlap(
            _low(unit), _high(unit), float(center["fixed_zd"]), float(center["fixed_zg"])
        ) else "peripheral_unit_ids"
        center[target].append(unit["id"])
        center["owned_unit_ids"].append(unit["id"])
        center["context_unit_ids"].append(unit["id"])
        center["source_pen_ids"] = _unique([*center["source_pen_ids"], *_source_pen_ids([unit])])
        if target == "extension_unit_ids":
            center["z_unit_ids"].append(unit["id"])
            center["dd"] = min(float(center["dd"]), _low(unit))
            center["gg"] = max(float(center["gg"]), _high(unit))
            center["z_high_min"] = min(center["z_high_min"], _high(unit))
            center["z_low_max"] = max(center["z_low_max"], _low(unit))
            if not _strict_interval_overlap(_low(unit), _high(unit), center["zd"], center["zg"]):
                center["touch_unit_ids"].append(unit["id"])
            center["evidence"]["z_endpoints"].append({key: unit[key] for key in ("id", "start_date", "end_date", "start_price", "end_price", "confirmed_at")})
        center["fluctuation_dd"] = center["dd"]
        center["fluctuation_gg"] = center["gg"]
        center["context_low"] = min(float(center["context_low"]), _low(unit))
        center["context_high"] = max(float(center["context_high"]), _high(unit))
        center["_last_owned"] = max(int(center["_last_owned"]), int(unit["ordinal"]))
        center["end_date"] = unit["end_date"]
        _capture_center_revision(center, available_at)


def _set_departure(center: dict[str, Any], tail: list[dict[str, Any]], level: int, components: list[dict[str, Any]], available_at: str) -> None:
    center["departure_unit_ids"] = [unit["id"] for unit in tail]
    center["context_unit_ids"] = _unique([*center["entry_unit_ids"], *center["owned_unit_ids"], *center["departure_unit_ids"]])
    center["departure_direction"] = _component_direction(tail)
    component = _component_record(tail, level, "departure", center["family_id"])
    if component:
        center["departure_component_id"] = component["id"]
        components.append(component)
    if not center.get("retest_unit_ids"):
        evidence, created = _departure_retest_evidence(center, tail, level)
        if evidence:
            components.extend(created)
    if tail:
        center["context_low"] = min(center["context_low"], *[_low(unit) for unit in tail])
        center["context_high"] = max(center["context_high"], *[_high(unit) for unit in tail])
    _capture_center_revision(center, available_at)


def build_level_centers(
    units: list[dict[str, Any]], level: int, *, origin_kind: str = "model_origin",
    direction_context: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    centers, components, issues = [], [], []
    for stream_index, stream in enumerate(_split_streams(units)):
        stream_origin = origin_kind if stream_index == 0 else "truncated_left"
        for ordinal, unit in enumerate(stream):
            unit["ordinal"] = ordinal
        workspace, floor, context = 0, 0, deepcopy(direction_context)
        origin = None
        if len(stream) >= 3 and _alternating(stream[:3]) and _strict_overlap(stream[:3]):
            origin, _ = _make_center(stream, CoreSeed(0, 0, 3, *_strict_overlap(stream[:3])), level)
            origin["origin_kind"] = stream_origin
            if stream_origin == "truncated_left":
                origin["formation_stage"] = "boundary_candidate"
            origin["_history"] = []
            _capture_center_revision(origin, origin["formed_at"])
        while workspace <= len(stream) - 3:
            seed = _find_earliest_core(stream, workspace, floor, context)
            if seed is None:
                if origin:
                    centers.append(origin)
                break
            center, component = _make_center(stream, seed, level)
            center["origin_kind"] = stream_origin if workspace == 0 else "confirmed_boundary"
            if origin:
                center["family_id"] = origin["family_id"]
                center["_history"] = origin["_history"]
                center["normalization_event"] = {"type": "origin_normalized", "previous_revision_id": origin["id"], "available_at": center["formed_at"]}
                _capture_center_revision(center, center["formed_at"])
                origin = None
            if component:
                components.append(component)
            departure = None
            next_workspace = None
            for scan in range(seed.core_end, len(stream)):
                unit = stream[scan]
                center["evidence_cursor_unit_id"] = unit["id"]
                reverse = breakout_context(stream[max(seed.core_start, scan - 2):scan + 1])
                if reverse and reverse["process_direction"] != center["process_direction_at_formation"]:
                    reverse.update(anchor_kind="successor_evidence", start_index=max(seed.core_start, scan - 2))
                    center.setdefault("successor_direction_evidence", []).append(reverse)
                intersects = _closed_interval_overlap(_low(unit), _high(unit), seed.zd, seed.zg)
                leaving = ((_direction(unit) == "up" and unit["end_price"] > seed.zg + EPSILON)
                           or (_direction(unit) == "down" and unit["end_price"] < seed.zd - EPSILON))
                if departure is not None and intersects and _direction(unit) != _direction(stream[departure]):
                    # Return proves the pending path's membership at this time.
                    # A return crossing the whole core can simultaneously start a new departure.
                    _absorb_return(center, stream, departure, scan if leaving else scan + 1,
                                   unit.get("confirmed_at") or unit["end_date"])
                    departure = None
                    if not leaving:
                        continue
                elif intersects and not leaving:
                    _absorb_return(center, stream, departure if departure is not None else scan, scan + 1)
                    departure = None
                    continue
                if departure is None:
                    departure = scan
                _set_departure(center, stream[departure:scan + 1], level, components, unit.get("confirmed_at") or unit["end_date"])
                if center.get("retest_unit_ids"):
                    next_workspace = departure
                    evidence_units = stream[departure:scan + 1]
                    context = {
                        "process_direction": _direction(evidence_units[0]), "reason": "confirmed_departure_retest",
                        "anchor_kind": "confirmed_boundary", "anchor_date": evidence_units[0]["start_date"],
                        "anchor_price": evidence_units[0]["start_price"], "source_unit_ids": [u["id"] for u in evidence_units],
                        "source_revision_id": center["id"], "available_at": center["revision_at"], "start_index": departure,
                    }
                    floor = scan
                    break
            centers.append(center)
            if next_workspace is None or next_workspace <= workspace:
                break
            workspace = next_workspace
    centers.sort(key=lambda item: (item["start_date"], item["end_date"], item["id"]))
    for ordinal, center in enumerate(centers):
        center["ordinal"] = ordinal
    return centers, components, issues


def classify_center_relation(previous: dict[str, Any], current: dict[str, Any]) -> str:
    if _stream_key(previous) != _stream_key(current) or int(previous["level"]) != int(current["level"]):
        return "sequence_boundary"
    if _strict_interval_overlap(float(previous["zd"]), float(previous["zg"]), float(current["zd"]), float(current["zg"])):
        return "overlap_conflict"
    core_separated_up = float(current["zd"]) > float(previous["zg"]) + EPSILON
    core_separated_down = float(current["zg"]) < float(previous["zd"]) - EPSILON
    if not (core_separated_up or core_separated_down):
        return "boundary_touch_candidate"
    if float(current["dd"]) > float(previous["gg"]) + EPSILON:
        return "newborn_up"
    if float(current["gg"]) < float(previous["dd"]) - EPSILON:
        return "newborn_down"
    envelope_overlap = _closed_interval_overlap(
        float(previous["dd"]), float(previous["gg"]),
        float(current["dd"]), float(current["gg"]),
    )
    if core_separated_up and envelope_overlap:
        return "expansion_up"
    if core_separated_down and envelope_overlap:
        return "expansion_down"
    if _strict_interval_overlap(
        float(previous["zd"]), float(previous["zg"]), float(current["zd"]), float(current["zg"]),
    ):
        return "overlap_conflict"
    return "boundary_touch_candidate"


def build_relations(centers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    relations: list[dict[str, Any]] = []
    grouped: dict[tuple[int, int, str, int], list[dict[str, Any]]] = defaultdict(list)
    for center in centers:
        grouped[(*_stream_key(center), int(center["level"]))].append(center)
    for group in grouped.values():
        group.sort(key=lambda item: (item["start_date"], item["end_date"], item["id"]))
        for previous, current in zip(group, group[1:]):
            relation = classify_center_relation(previous, current)
            relations.append({
                "id": _stable_id("relation", previous["id"], current["id"], relation),
                "kind": "center_relation",
                "level": int(previous["level"]),
                "relation_type": relation,
                "status": "candidate" if relation in {"boundary_touch_candidate", "expansion_up", "expansion_down"} else "confirmed",
                "missing_evidence": ["positive_width_overlap"] if relation == "boundary_touch_candidate" else [],
                "from_id": previous["id"],
                "to_id": current["id"],
                "start_date": previous["start_date"],
                "end_date": current["end_date"],
                "evidence": {
                    "previous_core": [previous["zd"], previous["zg"]],
                    "current_core": [current["zd"], current["zg"]],
                    "previous_envelope": [previous["fluctuation_dd"], previous["fluctuation_gg"]],
                    "current_envelope": [current["fluctuation_dd"], current["fluctuation_gg"]],
                },
            })
    return relations


def _units_between(
    units: list[dict[str, Any]], start_date: str, end_date: str,
) -> list[dict[str, Any]]:
    return [unit for unit in units if unit["start_date"] >= start_date and unit["end_date"] <= end_date]


def _component_strength(units: list[dict[str, Any]], market_dates: list[str]) -> dict[str, Any]:
    if not units:
        return {
            "max_internal_center_level": 0,
            "confirmed_child_movement_count": 0,
            "amplitude": 0.0,
            "bar_span": 1,
            "average_slope": 0.0,
        }
    amplitude = abs(float(units[-1]["end_price"]) - float(units[0]["start_price"]))
    date_index = {stamp: index for index, stamp in enumerate(market_dates)}
    start = date_index.get(units[0]["start_date"], 0)
    end = date_index.get(units[-1]["end_date"], start + len(units))
    span = max(1, end - start)
    return {
        "max_internal_center_level": max(int(unit.get("max_internal_center_level", 0)) for unit in units),
        "confirmed_child_movement_count": sum(int(unit.get("confirmed_child_movement_count", 0)) for unit in units),
        "amplitude": amplitude,
        "bar_span": span,
        "average_slope": amplitude / span,
    }


def _structurally_weaker(before: dict[str, Any], after: dict[str, Any]) -> tuple[bool | None, str]:
    before_rank = (before["max_internal_center_level"], before["confirmed_child_movement_count"])
    after_rank = (after["max_internal_center_level"], after["confirmed_child_movement_count"])
    if after_rank < before_rank:
        return True, "lower_internal_structure_rank"
    if after_rank > before_rank:
        return False, "higher_internal_structure_rank"
    amplitude_weaker = after["amplitude"] <= before["amplitude"] + EPSILON
    slope_weaker = after["average_slope"] < before["average_slope"] - EPSILON
    if amplitude_weaker and slope_weaker:
        return True, "amplitude_and_slope_weaker"
    if not amplitude_weaker and not slope_weaker:
        return False, "amplitude_and_slope_stronger"
    return None, "mixed_price_strength"


def _macd_evidence(macd: list[dict[str, Any]], start: str, end: str) -> dict[str, float | None]:
    values = [item for item in macd if start <= item["trade_date"] <= end]
    if not values:
        return {"area": None, "peak": None}
    histograms = [abs(float(item.get("histogram", 0))) for item in values]
    return {"area": sum(histograms), "peak": max(histograms, default=0.0)}


def _macd_conclusion(before: dict[str, Any], after: dict[str, Any]) -> str:
    if before["area"] is None or after["area"] is None:
        return "unavailable"
    if after["area"] < before["area"] and after["peak"] <= before["peak"]:
        return "weaker"
    if after["area"] > before["area"] and after["peak"] >= before["peak"]:
        return "stronger"
    return "neutral"


def _point_revision(
    level: int, point_type: str, status: str, point_date: str, point_price: float,
    center: dict[str, Any], source_unit: dict[str, Any], components: list[dict[str, Any]],
    evidence: dict[str, Any], confirmed_at: str | None = None, parent_point_id: str | None = None,
) -> dict[str, Any]:
    family_id = _stable_id("point-family", level, point_type, center["family_id"], point_date, point_price)
    return {
        "id": f"{family_id}:r1",
        "kind": "structural_point",
        "family_id": family_id,
        "revision_no": 1,
        "active": True,
        "level": level,
        "point_type": point_type,
        "status": status,
        "point_date": point_date,
        "point_price": float(point_price),
        "confirmed_at": max(confirmed_at or "", center.get("formed_at") or "", *[component.get("confirmed_at") or component["end_date"] for component in components]) if status == "confirmed" else None,
        "source_unit_id": source_unit["id"],
        "source_component_ids": [item["id"] for item in components],
        "center_family_id": center["family_id"],
        "center_revision_id": center["id"],
        "movement_family_id": None,
        "parent_point_id": parent_point_id,
        "evidence": evidence,
        "invalidated_reason": None,
        "start_date": point_date,
        "end_date": point_date,
        "continuous_range_id": _stream_key(source_unit)[0],
        "sequence_id": _stream_key(source_unit)[1],
        "structure_sequence_id": _stream_key(source_unit)[2],
    }


def _third_point(
    center: dict[str, Any], units: list[dict[str, Any]], level: int,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    history = center.get("_history", [])
    for snapshot in history:
        if snapshot.get("retest_unit_ids"):
            point, components = _third_point_tail(deepcopy(snapshot), units, level)
            if point:
                return point, components
    tail_ids = center.get("departure_unit_ids", [])
    for end in range(2, len(tail_ids) + 1):
        candidate = {**center, "departure_unit_ids": tail_ids[:end]}
        point, components = _third_point_tail(candidate, units, level)
        if point:
            snapshots = [snapshot for snapshot in center.get("_history", []) if snapshot["revision_at"] <= point["confirmed_at"] and set(tail_ids[:end]) <= set(snapshot["departure_unit_ids"])]
            if snapshots:
                point["center_revision_id"] = snapshots[0]["id"]
            center["retest_unit_ids"] = candidate["retest_unit_ids"]
            center["retest_component_id"] = candidate["retest_component_id"]
            return point, components
    return None, []


def _departure_retest_evidence(
    center: dict[str, Any], units: list[dict[str, Any]], level: int,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Confirm a center's departure/retest state without constructing a point."""
    by_id = {unit["id"]: unit for unit in units}
    tail = [by_id[identifier] for identifier in center.get("departure_unit_ids", []) if identifier in by_id]
    if len(tail) < 2:
        return None, []
    for split in (1,):
        departure = tail[:split]
        retest = tail[split:split + 1]
        departure_direction = _component_direction(departure)
        retest_direction = _component_direction(retest)
        if not departure_direction or not retest_direction or departure_direction == retest_direction:
            continue
        if not _center_free(departure) or not _center_free(retest):
            continue
        departure_component = _component_record(departure, level, "departure", center["family_id"])
        retest_component = _component_record(retest, level, "retest", center["family_id"])
        if not departure_component or not retest_component:
            continue
        if departure_direction == "up":
            extreme_unit = min(retest, key=lambda item: (_low(item), item["end_date"]))
            point_price = _low(extreme_unit)
            if _high(departure[-1]) <= float(center["zg"]) + EPSILON or point_price <= float(center["zg"]) + EPSILON:
                continue
        else:
            extreme_unit = max(retest, key=lambda item: (_high(item), item["end_date"]))
            point_price = _high(extreme_unit)
            if _low(departure[-1]) >= float(center["zd"]) - EPSILON or point_price >= float(center["zd"]) - EPSILON:
                continue
        center["retest_unit_ids"] = [unit["id"] for unit in retest]
        center["retest_component_id"] = retest_component["id"]
        center["status"] = "broken"
        return {
            "departure_direction": departure_direction,
            "extreme_unit": extreme_unit,
            "price": point_price,
            "confirmed_at": max(center["formed_at"], *[unit.get("confirmed_at") or unit["end_date"] for unit in [*departure, *retest]]),
        }, [departure_component, retest_component]
    return None, []


def _third_point_tail(
    center: dict[str, Any], units: list[dict[str, Any]], level: int,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    evidence, components = _departure_retest_evidence(center, units, level)
    if evidence is None:
        return None, []
    departure_component, retest_component = components
    extreme_unit = evidence["extreme_unit"]
    point = _point_revision(
        level, "third_buy" if evidence["departure_direction"] == "up" else "third_sell",
        "confirmed", extreme_unit["end_date"], evidence["price"], center, extreme_unit, components,
        {
            "departure_component_id": departure_component["id"],
            "retest_component_id": retest_component["id"],
            "strictly_outside_core": True,
        },
        confirmed_at=evidence["confirmed_at"],
    )
    return point, components


def build_structural_points(
    units: list[dict[str, Any]], centers: list[dict[str, Any]], relations: list[dict[str, Any]],
    level: int, market_dates: list[str], macd: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    points: list[dict[str, Any]] = []
    components: list[dict[str, Any]] = []
    by_id = {unit["id"]: unit for unit in units}
    center_by_id = {center["id"]: center for center in centers}
    index = {unit["id"]: ordinal for ordinal, unit in enumerate(units)}
    for center in centers:
        if center.get("formation_stage") in {"origin_overlap", "boundary_candidate"}:
            continue
        point, created = _third_point(center, units, level)
        components.extend(created)
        if point:
            points.append(point)
        entry_units = [by_id[identifier] for identifier in center.get("entry_unit_ids", []) if identifier in by_id]
        tail_units = [by_id[identifier] for identifier in center.get("departure_unit_ids", []) if identifier in by_id]
        if entry_units and len(tail_units) >= 2:
            direction = _component_direction(tail_units)
            if direction not in {"up", "down"}:
                direction = _direction(tail_units[0])
            extreme_index = (
                max(range(len(tail_units)), key=lambda offset: _high(tail_units[offset]))
                if direction == "up"
                else min(range(len(tail_units)), key=lambda offset: _low(tail_units[offset]))
            )
            c_units = tail_units[:extreme_index + 1]
            reversal = tail_units[extreme_index + 1:extreme_index + 2]
            if c_units and reversal and _component_direction(entry_units) == direction:
                before = _component_strength(entry_units, market_dates)
                after = _component_strength(c_units, market_dates)
                structural, reason = _structurally_weaker(before, after)
                before_macd = _macd_evidence(macd, entry_units[0]["start_date"], entry_units[-1]["end_date"])
                after_macd = _macd_evidence(macd, c_units[0]["start_date"], c_units[-1]["end_date"])
                macd_result = _macd_conclusion(before_macd, after_macd)
                previous_extreme = max(_high(item) for item in entry_units) if direction == "up" else min(
                    _low(item) for item in entry_units
                )
                current_extreme = max(_high(item) for item in c_units) if direction == "up" else min(
                    _low(item) for item in c_units
                )
                new_extreme = current_extreme > previous_extreme + EPSILON if direction == "up" else (
                    current_extreme < previous_extreme - EPSILON
                )
                qualifies = (not new_extreme or structural is True) and macd_result != "stronger"
                before_component = _component_record(entry_units, level, "consolidation_before", center["family_id"])
                after_component = _component_record(c_units, level, "consolidation_after", center["family_id"])
                if before_component and after_component:
                    extreme_unit = c_units[-1]
                    points.append(_point_revision(
                        level,
                        "consolidation_divergence_sell" if direction == "up" else "consolidation_divergence_buy",
                        "confirmed" if qualifies else "candidate",
                        extreme_unit["end_date"],
                        _high(extreme_unit) if direction == "up" else _low(extreme_unit),
                        center,
                        extreme_unit,
                        [before_component, after_component],
                        {
                            "before_strength": before,
                            "after_strength": after,
                            "structural_conclusion": structural,
                            "structural_reason": reason,
                            "new_extreme": new_extreme,
                            "before_macd": before_macd,
                            "after_macd": after_macd,
                            "auxiliary_indicator_conclusion": macd_result,
                        },
                        confirmed_at=reversal[0].get("confirmed_at", reversal[0]["end_date"]) if qualifies else None,
                    ))
                    components.extend([before_component, after_component])
    trend_relations = [item for item in relations if item["relation_type"] in {"newborn_up", "newborn_down"}]
    for relation in trend_relations:
        previous = center_by_id[relation["from_id"]]
        current = center_by_id[relation["to_id"]]
        previous_last = max(index.get(identifier, -1) for identifier in previous["owned_unit_ids"])
        current_entry = min(index.get(identifier, len(units)) for identifier in current["core_unit_ids"])
        current_last = max(index.get(identifier, -1) for identifier in current["owned_unit_ids"])
        b_units = units[previous_last + 1:current_entry]
        c_units = [by_id[identifier] for identifier in current["departure_unit_ids"]]
        if not b_units or not c_units or not _span_contiguous([*b_units, *[by_id[identifier] for identifier in current["owned_unit_ids"]], *c_units]):
            continue
        direction = "up" if relation["relation_type"] == "newborn_up" else "down"
        if _component_direction(b_units) != direction:
            continue
        if direction not in {"up", "down"}:
            continue
        extreme_index = max(range(len(c_units)), key=lambda i: c_units[i]["end_price"]) if direction == "up" else min(
            range(len(c_units)), key=lambda i: c_units[i]["end_price"]
        )
        c_move = c_units[:extreme_index + 1]
        reversal = c_units[extreme_index + 1:extreme_index + 2]
        if not c_move or _component_direction(c_move) != direction:
            continue
        b_component = _component_record(b_units, level, "strength_before", current["family_id"])
        c_component = _component_record(c_move, level, "strength_after", current["family_id"])
        if not b_component or not c_component:
            continue
        before_strength = _component_strength(b_units, market_dates)
        after_strength = _component_strength(c_move, market_dates)
        structural, structural_reason = _structurally_weaker(before_strength, after_strength)
        before_macd = _macd_evidence(macd, b_units[0]["start_date"], b_units[-1]["end_date"])
        after_macd = _macd_evidence(macd, c_move[0]["start_date"], c_move[-1]["end_date"])
        macd_result = _macd_conclusion(before_macd, after_macd)
        new_extreme = float(c_move[-1]["end_price"]) > max(_high(unit) for unit in b_units) + EPSILON if direction == "up" else float(c_move[-1]["end_price"]) < min(_low(unit) for unit in b_units) - EPSILON
        status = "confirmed" if new_extreme and structural is True and reversal and macd_result != "stronger" else "candidate"
        point_type = "first_sell" if direction == "up" else "first_buy"
        extreme_unit = c_move[-1]
        point_price = _high(extreme_unit) if direction == "up" else _low(extreme_unit)
        point = _point_revision(
            level, point_type, status, extreme_unit["end_date"], point_price,
            current, extreme_unit, [b_component, c_component],
            {
                "before_strength": before_strength,
                "after_strength": after_strength,
                "structural_conclusion": structural,
                "structural_reason": structural_reason,
                "before_macd": before_macd,
                "after_macd": after_macd,
                "auxiliary_indicator_conclusion": macd_result,
                "new_extreme": new_extreme,
            },
            confirmed_at=(reversal[0].get("confirmed_at", reversal[0]["end_date"]) if status == "confirmed" else None),
        )
        points.append(point)
        components.extend([b_component, c_component])

    confirmed_first_points = [
        point for point in points
        if point["status"] == "confirmed" and point["point_type"] in {"first_buy", "first_sell"}
    ]
    for first in confirmed_first_points:
        source_index = index.get(first["source_unit_id"])
        center = center_by_id.get(first["center_revision_id"])
        if source_index is None or center is None:
            continue
        expected = "up" if first["point_type"] == "first_buy" else "down"
        departure_index = next(
            (offset for offset in range(source_index + 1, len(units)) if _direction(units[offset]) == expected),
            None,
        )
        if departure_index is None or departure_index + 1 >= len(units):
            continue
        retrace_index = departure_index + 1
        if _direction(units[retrace_index]) == expected:
            continue
        confirmation = units[retrace_index + 1] if retrace_index + 1 < len(units) and _direction(
            units[retrace_index + 1]
        ) == expected else None
        retrace = units[retrace_index]
        point_price = _low(retrace) if expected == "up" else _high(retrace)
        preserves_extreme = point_price > float(first["point_price"]) + EPSILON if expected == "up" else (
            point_price < float(first["point_price"]) - EPSILON
        )
        departure_component = _component_record([units[departure_index]], level, "second_departure", center["family_id"])
        retrace_component = _component_record([retrace], level, "second_retest", center["family_id"])
        if not departure_component or not retrace_component:
            continue
        status = "confirmed" if preserves_extreme and confirmation else (
            "candidate" if preserves_extreme else "invalidated"
        )
        second = _point_revision(
            level, "second_buy" if expected == "up" else "second_sell", status,
            retrace["end_date"], point_price, center, retrace,
            [departure_component, retrace_component],
            {"parent_point_id": first["family_id"], "preserves_first_extreme": preserves_extreme},
            confirmed_at=confirmation.get("confirmed_at", confirmation["end_date"]) if status == "confirmed" else None,
            parent_point_id=first["family_id"],
        )
        if status == "invalidated":
            second["active"] = False
            second["invalidated_reason"] = "first_point_extreme_broken"
        points.append(second)
        components.extend([departure_component, retrace_component])
    points.sort(key=lambda item: (item["point_date"], item["level"], item["id"]))
    for ordinal, point in enumerate(points):
        point["ordinal"] = ordinal
    dedup = {component["id"]: component for component in components}
    return points, list(dedup.values())


def _movement_classification(
    owned_centers: list[dict[str, Any]], relations: list[dict[str, Any]],
) -> tuple[str | None, str | None]:
    if len(owned_centers) == 1:
        return "consolidation", None
    center_ids = {center["id"] for center in owned_centers}
    relevant = [
        relation["relation_type"] for relation in relations
        if relation["from_id"] in center_ids and relation["to_id"] in center_ids
    ]
    if len(relevant) != len(owned_centers) - 1:
        return None, None
    if all(value == "newborn_up" for value in relevant):
        return "trend", "up"
    if all(value == "newborn_down" for value in relevant):
        return "trend", "down"
    return None, None


def _movement_record(
    level: int, selected: list[dict[str, Any]], centers: list[dict[str, Any]],
    relations: list[dict[str, Any]], status: str, end_point: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not selected or not centers:
        return None
    classification, trend_direction = _movement_classification(centers, relations)
    direction = trend_direction or _component_direction(selected)
    if direction not in {"up", "down"}:
        return None
    family_id = _stable_id("movement-family", level, selected[0]["id"], centers[0]["family_id"])
    movement_status = status if classification is not None else "undetermined"
    confirmed = movement_status == "confirmed"
    start_price = float(selected[0]["start_price"])
    end_price = float(end_point["point_price"] if end_point else selected[-1]["end_price"])
    return {
        "id": f"{family_id}:r1",
        "kind": "movement",
        "family_id": family_id,
        "revision_no": 1,
        "active": True,
        "level": level,
        "status": movement_status,
        "classification": classification,
        "direction": direction,
        "start_date": selected[0]["start_date"],
        "start_price": start_price,
        "end_date": end_point["point_date"] if end_point else selected[-1]["end_date"],
        "end_price": end_price,
        "point_date": end_point["point_date"] if end_point else None,
        "confirmed_at": end_point.get("confirmed_at") if confirmed and end_point else None,
        "start_point_id": None,
        "end_point_id": end_point["family_id"] if end_point else None,
        "source_unit_ids": [unit["id"] for unit in selected],
        "source_pen_ids": _source_pen_ids(selected),
        "center_family_ids": [center["family_id"] for center in centers],
        "center_revision_ids": [center["id"] for center in centers],
        "center_levels": sorted({int(center["level"]) for center in centers}),
        "child_movement_ids": [unit["id"] for unit in selected if unit.get("kind") == "movement"],
        "price_envelope_low": min(_low(unit) for unit in selected),
        "price_envelope_high": max(_high(unit) for unit in selected),
        "candidate_extreme_date": selected[-1]["end_date"],
        "candidate_extreme_price": float(selected[-1]["end_price"]),
        "recursive_eligible": confirmed and classification is not None,
        "termination_reason": (
            "first_buy_sell_point" if end_point and end_point["point_type"].startswith("first_")
            else "third_buy_sell_center_break" if end_point
            else "provisional_tail"
        ),
        "undetermined_reason": None if classification is not None else "mixed_or_incomplete_center_relation",
        "continuous_range_id": _stream_key(selected[0])[0],
        "sequence_id": _stream_key(selected[0])[1],
        "structure_sequence_id": _stream_key(selected[0])[2],
        "evidence": {
            "boundary_point_revision_id": end_point["id"] if end_point else None,
        },
    }


def build_movements(
    units: list[dict[str, Any]], centers: list[dict[str, Any]], relations: list[dict[str, Any]],
    points: list[dict[str, Any]], level: int,
) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    movements: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    assigned: set[str] = set()
    for stream in _split_streams(units):
        unit_index = {unit["id"]: index for index, unit in enumerate(stream)}
        stream_centers = [center for center in centers if _stream_key(center) == _stream_key(stream[0]) and center.get("formation_stage") != "origin_overlap" and center.get("formation_stage") != "boundary_candidate"]
        stream_points = [
            point for point in points
            if point.get("status") == "confirmed" and _stream_key(point) == _stream_key(stream[0])
            and point.get("point_type") not in {"second_buy", "second_sell"}
        ]
        if not stream_centers:
            continue
        start = min(unit_index.get(identifier, len(stream)) for identifier in [*stream_centers[0]["entry_unit_ids"], *stream_centers[0]["owned_unit_ids"]])
        for point in stream_points:
            end = unit_index.get(point["source_unit_id"])
            if end is None or end < start:
                continue
            selected = stream[start:end + 1]
            selected_ids = {unit["id"] for unit in selected}
            boundary_centers = []
            owned_centers = []
            for center in stream_centers:
                available = [snapshot for snapshot in center.get("_history", []) if snapshot["revision_at"] <= point["confirmed_at"]]
                snapshot = available[-1] if available else None
                if snapshot and set(snapshot["owned_unit_ids"]) <= selected_ids:
                    boundary_centers.append(snapshot)
                    owned_centers.append(center)
            movement = _movement_record(level, selected, boundary_centers, build_relations(boundary_centers), "confirmed", point)
            if movement is None:
                continue
            expected_direction = "up" if point["point_type"].endswith("sell") else "down"
            if movement["direction"] != expected_direction:
                continue
            if movement["status"] != "confirmed":
                continue
            if movement["classification"] == "trend" and not point["point_type"].startswith("first_"):
                continue
            if any(center["formed_at"] <= point["confirmed_at"] and set(center["core_unit_ids"]) & selected_ids and not set(center["core_unit_ids"]) <= selected_ids for center in stream_centers):
                continue
            if movements and movement["direction"] == movements[-1]["direction"]:
                continue
            movement["start_point_id"] = movements[-1]["end_point_id"] if movements else None
            movements.append(movement)
            assigned.update(selected_ids)
            for center in owned_centers:
                center["owner_movement_id"] = movement["family_id"]
            start = end + 1
        if start < len(stream):
            selected = stream[start:]
            selected_ids = {unit["id"] for unit in selected}
            owned_centers = [center for center in stream_centers if set(center["owned_unit_ids"]) <= selected_ids]
            if not owned_centers and not movements:
                selected = stream[min((unit_index.get(identifier, 0) for identifier in stream_centers[0]["entry_unit_ids"]), default=0):]
                selected_ids = {unit["id"] for unit in selected}
                owned_centers = [center for center in stream_centers if set(center["owned_unit_ids"]) <= selected_ids]
            movement = _movement_record(level, selected, owned_centers, relations, "provisional", None)
            if movement:
                movement["start_point_id"] = movements[-1]["end_point_id"] if movements else None
                movements.append(movement)
                assigned.update(selected_ids)
                for center in owned_centers:
                    center["owner_movement_id"] = movement["family_id"]
    for ordinal, movement in enumerate(movements):
        movement["ordinal"] = ordinal
    unassigned = [unit["id"] for unit in units if unit["id"] not in assigned]
    return movements, unassigned, issues


def expansion_evidence(
    left: dict[str, Any], right: dict[str, Any], units: list[dict[str, Any]],
    components: list[dict[str, Any]],
) -> dict[str, Any]:
    unit_by_id = {unit["id"]: unit for unit in units}
    missing: list[str] = []
    if left["id"] == right["id"] or _stream_key(left) != _stream_key(right) or left["level"] != right["level"]:
        missing.append("distinct_same_level_sequence")
    if left.get("formation_stage") in {"origin_overlap", "boundary_candidate"} or right.get("formation_stage") in {"origin_overlap", "boundary_candidate"}:
        missing.append("directional_children")
    if not left.get("formed_at") or not right.get("formed_at"):
        missing.append("formed_children")
    if classify_center_relation(left, right) not in {"expansion_up", "expansion_down"}:
        missing.append("separated_cores_overlapping_z")
    left_ids, right_ids = set(left.get("owned_unit_ids", [])), set(right.get("owned_unit_ids", []))
    if left_ids & right_ids or not left_ids or not right_ids:
        missing.append("disjoint_ownership")
    required = left_ids | right_ids
    if not required <= unit_by_id.keys():
        missing.append("source_units")
    indices = {unit["id"]: index for index, unit in enumerate(units)}
    owned_positions = [indices[identifier] for identifier in required if identifier in indices]
    span = units[min(owned_positions):max(owned_positions) + 1] if owned_positions else []
    if not span or not _span_contiguous(span):
        missing.append("continuous_source_span")
    left_positions = [indices[identifier] for identifier in left_ids if identifier in indices]
    right_positions = [indices[identifier] for identifier in right_ids if identifier in indices]
    connection = units[max(left_positions) + 1:min(right_positions)] if left_positions and right_positions else []
    connection_ids = [unit["id"] for unit in connection]
    matching = [component for component in components if component.get("source_unit_ids") == connection_ids and connection_ids]
    if connection and (not matching or not _span_contiguous(connection)):
        missing.append("connection_component")
    if any(unit.get("status") != "confirmed" or not unit.get("confirmed_at") for unit in span):
        missing.append("confirmed_inputs")
    witnesses = [
        (left_unit, right_unit)
        for left_id in left.get("z_unit_ids", []) for right_id in right.get("z_unit_ids", [])
        if (left_unit := unit_by_id.get(left_id)) and (right_unit := unit_by_id.get(right_id))
        and left_id in left_ids and right_id in right_ids
        and _closed_interval_overlap(_low(left_unit), _high(left_unit), _low(right_unit), _high(right_unit))
        and left_unit.get("status") == right_unit.get("status") == "confirmed"
    ]
    witnesses.sort(key=lambda pair: (max(unit.get("confirmed_at") or unit["end_date"] for unit in pair), pair[0]["start_date"], pair[0]["id"], pair[1]["id"]))
    if not witnesses:
        missing.append("overlap_witness")
    available_at = max([left.get("revision_at") or left.get("formed_at") or "", right.get("revision_at") or right.get("formed_at") or "", *[unit.get("confirmed_at") or unit["end_date"] for unit in span]])
    return {
        "missing_evidence": missing,
        "connection_component_ids": [min(matching, key=lambda item: (item.get("role") != "entry", item["id"]))["id"]] if matching else [],
        "overlap_witness_unit_ids": [unit["id"] for unit in witnesses[0]] if witnesses else [],
        "source_unit_ids": [unit["id"] for unit in span],
        "source_pen_ids": _source_pen_ids(span),
        "evidence_available_at": available_at,
    }


def _promote_expansions(
    centers: list[dict[str, Any]], relations: list[dict[str, Any]], next_level: int,
    units: list[dict[str, Any]], components: list[dict[str, Any]],
    movements: list[dict[str, Any]] | None = None,
    points: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_id = {c["id"]: c for c in centers}
    promoted, absorption, consumed = [], [], set()
    for relation in relations:
        if relation["relation_type"] not in {"expansion_up", "expansion_down"}:
            continue
        final_left, final_right = by_id[relation["from_id"]], by_id[relation["to_id"]]
        left, right = final_left, final_right
        evidence = expansion_evidence(left, right, units, components)
        for snapshot in final_right.get("_history", [final_right]):
            available = [c for c in final_left.get("_history", [final_left]) if c["revision_at"] <= snapshot["revision_at"]]
            if not available:
                continue
            proof = expansion_evidence(available[-1], snapshot, units, components)
            if not proof["missing_evidence"]:
                left, right, evidence = available[-1], snapshot, proof
                break
        # The relation is rebuilt on every prefix replay.  Once a valid contact
        # pair has been found, all geometry fields must come from that pair,
        # rather than from the latest revision's expanded envelope.  Otherwise
        # a later Z extension silently rewrites the historical contact proof.
        contact_interval = [max(left["dd"], right["dd"]), min(left["gg"], right["gg"])]
        relation["evidence"].update({
            **evidence,
            "previous_core": [left["zd"], left["zg"]],
            "current_core": [right["zd"], right["zg"]],
            "previous_envelope": [left["fluctuation_dd"], left["fluctuation_gg"]],
            "current_envelope": [right["fluctuation_dd"], right["fluctuation_gg"]],
            "previous_envelope_at_contact": [left["fluctuation_dd"], left["fluctuation_gg"]],
            "current_envelope_at_contact": [right["fluctuation_dd"], right["fluctuation_gg"]],
            "previous_center_revision_id": left["id"],
            "current_center_revision_id": right["id"],
            "contact_confirmed_at": evidence["evidence_available_at"],
            "contact_interval": contact_interval,
            "child_revision_ids": [left["id"], right["id"]],
        })
        relation["missing_evidence"] = evidence["missing_evidence"]
        relation["boundary_status"] = "unresolved"
        if evidence["missing_evidence"]:
            relation["expansion_status"] = "candidate"
            continue
        relation.update(id=_stable_id("relation", left["family_id"], right["family_id"], relation["relation_type"]),
                        status="confirmed", expansion_status="confirmed", confirmed_at=evidence["evidence_available_at"],
                        from_id=left["id"], to_id=right["id"], start_date=left["start_date"], end_date=right["end_date"])
        proofs = decomposition_proofs(movements or [], units, set(evidence["source_unit_ids"]), next_level - 1, centers)
        first_owned = evidence["source_unit_ids"][0]
        proofs = [p for p in proofs if p["source_unit_ids"][0] == first_owned]
        internal = internal_decomposition_proofs(units, centers, points or [], set(evidence["source_unit_ids"]), next_level - 1)
        if internal and (not proofs or (internal[0]["available_at"], internal[0]["segments"][0]["start_date"]) < (proofs[0]["available_at"], proofs[0]["segments"][0]["start_date"])):
            proofs = internal
        relation["boundary_missing_evidence"] = [] if proofs else ["three_subordinate_movement_boundaries"]
        if not proofs or left["family_id"] in consumed or right["family_id"] in consumed:
            continue
        previous = final_left["id"]
        base_revision = final_left["revision_no"]
        for offset, proof in enumerate(proofs, 1):
            parts = proof["segments"]
            zd, zg = proof["zd"], proof["zg"]
            stamp = max(evidence["evidence_available_at"], proof["available_at"])
            dd, gg = min(p["low"] for p in parts[::2]), max(p["high"] for p in parts[::2])
            item = {**deepcopy(left), "id": f"{left['family_id']}:r{base_revision + offset}",
                "previous_revision_id": previous, "revision_no": base_revision + offset,
                "level": next_level, "unit_kind": "center_revision", "formation_modes": ["expansion_decomposition"],
                "boundary_status": proof["boundary_status"], "decomposition_proof": proof,
                "status": "formed", "active": offset == len(proofs), "recursive_eligible": False,
                "formed_at": stamp, "available_at": stamp, "revision_at": stamp,
                "promotion_confirmed_at": evidence["evidence_available_at"],
                "start_date": parts[0]["start_date"], "end_date": parts[-1]["end_date"],
                "core_start_date": parts[0]["start_date"], "core_end_date": parts[-1]["end_date"],
                "zd": zd, "zg": zg, "fixed_zd": zd, "fixed_zg": zg,
                "dd": dd, "gg": gg, "fluctuation_dd": dd, "fluctuation_gg": gg,
                "z_high_min": min(p["high"] for p in parts[::2]), "z_low_max": max(p["low"] for p in parts[::2]),
                "formation_type": "pullback" if parts[0]["direction"] == "down" else "rebound",
                "formation_stage": "directional", "z_direction": parts[0]["direction"],
                "direction_context": None, "process_direction_at_formation": "unknown",
                "z_unit_ids": [], "core_unit_ids": [], "entry_unit_ids": [], "extension_unit_ids": [],
                "peripheral_unit_ids": [], "departure_unit_ids": [], "retest_unit_ids": [],
                "owned_unit_ids": [left["id"], right["id"]], "context_unit_ids": [left["id"], right["id"]],
                "child_center_ids": [left["id"], right["id"]],
                "child_movement_ids": [p["movement_revision_id"] for p in parts if p.get("construction_scope") != "internal"],
                "source_pen_ids": _unique(p for part in parts for p in part["source_pen_ids"]),
                "context_low": min(p["low"] for p in parts), "context_high": max(p["high"] for p in parts),
                "connection_component_ids": evidence["connection_component_ids"],
                "overlap_witness_unit_ids": evidence["overlap_witness_unit_ids"], "missing_evidence": [],
                "evidence": {**evidence, "relation_id": relation["id"], "decomposition_proof": proof}}
            item.pop("_history", None)
            promoted.append(item)
            previous = item["id"]
        final_left["active"] = final_right["active"] = False
        final_right["absorbed_into_family_id"] = left["family_id"]
        consumed.update([left["family_id"], right["family_id"]])
        relation["boundary_status"] = proofs[-1]["boundary_status"]
        for child in (left, right):
            absorption.append({"id": _stable_id("relation", child["id"], previous), "kind": "center_relation",
                "level": next_level, "relation_type": "promoted_into", "from_id": child["id"], "to_id": previous,
                "start_date": child["start_date"], "end_date": promoted[-1]["end_date"],
                "evidence": {"formation_mode": "expansion_decomposition"}})
    return promoted, absorption


def _promote_open_movements(
    movements: list[dict[str, Any]], promoted_centers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    revisions: list[dict[str, Any]] = []
    centers_by_movement: dict[str, list[dict[str, Any]]] = defaultdict(list)
    movements_by_id = {movement["id"]: movement for movement in movements}
    for center in promoted_centers:
        center_pens = set(center.get("source_pen_ids", []))
        candidates = [
            movement for movement in movements
            if movement.get("status") != "confirmed"
            and center_pens <= set(movement.get("source_pen_ids", []))
        ]
        if candidates:
            current = min(candidates, key=lambda item: len(item.get("source_pen_ids", [])))
            centers_by_movement[current["id"]].append(center)
    for movement_id, centers in centers_by_movement.items():
        current = movements_by_id[movement_id]
        highest_level = max(int(center["level"]) for center in centers)
        selected_centers = sorted(
            [center for center in centers if int(center["level"]) == highest_level],
            key=lambda item: (item["start_date"], item["end_date"], item["id"]),
        )
        classification, trend_direction = _movement_classification(selected_centers, build_relations(selected_centers))
        current["active"] = False
        revision_no = int(current.get("revision_no", 1)) + 1
        revisions.append({
            **current,
            "id": f"{current['family_id']}:r{revision_no}",
            "revision_no": revision_no,
            "previous_revision_id": current["id"],
            "level": highest_level,
            "status": "provisional" if classification else "undetermined",
            "classification": classification,
            "direction": trend_direction or current.get("direction"),
            "active": True,
            "center_family_ids": _unique(center["family_id"] for center in selected_centers),
            "center_revision_ids": [center["id"] for center in selected_centers],
            "center_levels": [highest_level],
            "child_movement_ids": list(current.get("child_movement_ids", [])),
            "recursive_eligible": False,
            "termination_reason": "provisional_tail",
            "undetermined_reason": None if classification else "mixed_or_incomplete_center_relation",
            "evidence": {
                **current.get("evidence", {}),
                "promoted_with_center_revision_ids": [center["id"] for center in selected_centers],
            },
        })
    return revisions


def _strip_internal(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if not key.startswith("_")}


def merge_formation_paths(centers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues = []
    by_span: dict[tuple[Any, ...], dict[str, Any]] = {}
    additions = []
    for center in sorted([item for item in centers if item.get("active")], key=lambda item: (item["formed_at"], item["start_date"], item["id"])):
        key = (center["level"], _stream_key(center), tuple(center["source_pen_ids"]))
        previous = by_span.get(key)
        if previous is None:
            by_span[key] = center
            continue
        if previous["family_id"] == center["family_id"]:
            continue
        center["active"] = False
        center["superseded_by_revision_id"] = previous["id"]
        if (previous["zd"], previous["zg"]) != (center["zd"], center["zg"]):
            issues.append({"id": _stable_id("issue", previous["id"], center["id"]), "kind": "formation_conflict", "status": "undetermined", "level": center["level"], "start_date": center["start_date"], "end_date": center["end_date"], "evidence": {"retained_revision_id": previous["id"], "conflicting_revision_id": center["id"]}})
            continue
        revision_no = max(item["revision_no"] for item in [*centers, *additions] if item["family_id"] == previous["family_id"]) + 1
        merged = {**deepcopy(previous), "id": f"{previous['family_id']}:r{revision_no}", "revision_no": revision_no, "previous_revision_id": previous["id"], "active": True, "formation_modes": _unique([*previous["formation_modes"], *center["formation_modes"]]), "alternate_formation_revision_ids": _unique([*previous.get("alternate_formation_revision_ids", []), center["id"]]), "revision_at": max(previous["revision_at"], center["revision_at"])}
        previous["active"] = False
        additions.append(merged)
        by_span[key] = merged
    centers.extend(additions)
    return issues


def _point_event_history(units, centers, level, market_dates, macd):
    """Evaluate points as-of confirmation events; later extensions cannot erase an event."""
    confirmed, components, tail = {}, {}, []
    for count in range(3, len(units) + 1):
        prefix = units[:count]
        prefix_ids = {u["id"] for u in prefix}
        cutoff = max(u["confirmed_at"] for u in prefix)
        snapshots = []
        for center in centers:
            history = [h for h in center.get("_history", [center]) if h.get("revision_at", h["formed_at"]) <= cutoff
                       and set(h.get("context_unit_ids", [])) <= prefix_ids
                       and h.get("evidence_cursor_unit_id", h["core_unit_ids"][-1]) in prefix_ids]
            if history:
                snapshot = deepcopy(history[-1])
                snapshot["_history"] = history
                snapshots.append(snapshot)
        if not snapshots:
            continue
        points, created = build_structural_points(prefix, snapshots, build_relations(snapshots), level, market_dates, macd)
        components.update((c["id"], c) for c in created)
        for point in points:
            if point["status"] == "confirmed" and point["confirmed_at"] <= cutoff:
                confirmed.setdefault(point["family_id"], point)
        tail = points
    result = list(confirmed.values()) + [p for p in tail if p["family_id"] not in confirmed]
    result.sort(key=lambda p: (p.get("confirmed_at") or "9999", p["point_date"], p["id"]))
    for ordinal, point in enumerate(result):
        point["ordinal"] = ordinal
    return result, list(components.values())


def build_structure_hierarchy(
    pens: list[dict[str, Any]], macd: list[dict[str, Any]] | None = None,
    market_dates: list[str] | None = None,
    *, calculation_profile: str = "full",
) -> dict[str, Any]:
    if calculation_profile not in {"full", "pen_centers_only"}:
        raise ValueError(f"不支持的结构计算策略: {calculation_profile}")
    full = calculation_profile == "full"
    macd = macd or []
    market_dates = market_dates or []
    all_centers: list[dict[str, Any]] = []
    all_movements: list[dict[str, Any]] = []
    all_points: list[dict[str, Any]] = []
    all_components: dict[str, dict[str, Any]] = {}
    all_relations: list[dict[str, Any]] = []
    all_issues: list[dict[str, Any]] = []
    unassigned_by_level: dict[str, list[str]] = {}
    units = atomic_pen_units(pens)
    level = 1
    while units and level <= MAX_LEVEL:
        centers, components, issues = build_level_centers(units, level)
        if full:
            relations = build_relations(centers)
            points, point_components = _point_event_history(units, centers, level, market_dates, macd)
            movements, unassigned, movement_issues = build_movements(units, centers, relations, points, level)
            promoted, absorption = _promote_expansions(centers, relations, level + 1, units, components, movements, points) if level < MAX_LEVEL else ([], [])
            movement_promotions = _promote_open_movements(movements, [p for p in promoted if p.get("boundary_status") == "fixed"])
        else:
            relations, promoted, absorption, points, point_components = [], [], [], [], []
            movements, unassigned, movement_issues, movement_promotions = [], [], [], []
        for center in centers:
            history = center.get("_history") or [center]
            for revision in history:
                snapshot = deepcopy(revision)
                snapshot["active"] = center.get("active", True) and snapshot["id"] == center["id"]
                snapshot["owner_movement_id"] = center.get("owner_movement_id")
                if center.get("absorbed_into_family_id"):
                    snapshot["absorbed_into_family_id"] = center["absorbed_into_family_id"]
                all_centers.append(snapshot)
        all_centers.extend(promoted)
        all_movements.extend(movements)
        all_movements.extend(movement_promotions)
        all_points.extend(points)
        for component in [*components, *point_components]:
            all_components[component["id"]] = component
        all_relations.extend([*relations, *absorption])
        all_issues.extend([*issues, *movement_issues])
        unassigned_by_level[str(level)] = unassigned
        if not full:
            break
        next_units = movement_units(movements, level)
        if not next_units:
            break
        units = next_units
        level += 1

    if full:
        all_issues.extend(merge_formation_paths(all_centers))
    family_revisions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for center in all_centers:
        family_revisions[center["family_id"]].append(center)
    active_centers: list[dict[str, Any]] = []
    for revisions in family_revisions.values():
        if all(revision.get("absorbed_into_family_id") for revision in revisions):
            for revision in revisions:
                revision["active"] = False
            continue
        active_centers.extend(revision for revision in revisions if revision.get("active"))
    active_centers.sort(key=lambda item: (item["level"], item["start_date"], item["id"]))
    level_ordinals: dict[int, dict[str, int]] = defaultdict(dict)
    for center in sorted(all_centers, key=lambda item: (item["level"], item["start_date"], item["family_id"])):
        families = level_ordinals[int(center["level"])]
        if center["family_id"] not in families:
            families[center["family_id"]] = len(families)
        center["ordinal"] = families[center["family_id"]]
    center_revisions = sorted(all_centers, key=lambda item: (item["family_id"], item["revision_no"]))
    levels = sorted({int(center["level"]) for center in active_centers})
    return {
        "centers": [_strip_internal(item) for item in active_centers],
        "center_revisions": [_strip_internal(item) for item in center_revisions],
        "movements": [_strip_internal(item) for item in all_movements if item.get("active", True)],
        "movement_revisions": [_strip_internal(item) for item in all_movements],
        "points": [_strip_internal(item) for item in all_points if item.get("active", True)],
        "point_revisions": [_strip_internal(item) for item in all_points],
        "components": [_strip_internal(item) for item in sorted(all_components.values(), key=lambda item: (item["start_date"], item["id"]))],
        "relations": [_strip_internal(item) for item in all_relations],
        "issues": all_issues,
        "unassigned_by_level": unassigned_by_level,
        "levels": levels,
        "max_level": max(levels, default=0),
        "hierarchy_version": HIERARCHY_VERSION,
    }


def validate_structure(payload: dict[str, Any]) -> list[str]:
    structure = payload.get("structure", payload)
    centers = structure.get("center_revisions", structure.get("centers", []))
    movements = structure.get("movement_revisions", structure.get("movements", []))
    points = structure.get("point_revisions", structure.get("points", []))
    pens = structure.get("pens", payload.get("pens", []))
    pen_ids = {pen["id"] for pen in pens}
    movement_by_id = {m["id"]: m for m in movements}
    point_by_family = {p["family_id"]: p for p in points if p.get("status") == "confirmed"}
    errors: list[str] = []
    for name, values in (("centers", centers), ("movements", movements), ("points", points)):
        identifiers = [item.get("id") for item in values]
        if len(identifiers) != len(set(identifiers)):
            errors.append(f"{name}:duplicate_ids")
    core_owner: dict[tuple[int, str], str] = {}
    revision_ids = {center["id"] for center in centers}
    center_by_id = {center["id"]: center for center in centers}
    units = atomic_pen_units(pens)
    for level in range(1, MAX_LEVEL):
        units.extend(movement_units(movements, level))
    unit_by_id = {unit["id"]: unit for unit in units}
    components = structure.get("components", [])
    family_ids = {center["family_id"] for center in centers}
    active_families: set[str] = set()
    for center in centers:
        prefix = f"center:{center['id']}:"
        if center.get("active"):
            if center["family_id"] in active_families:
                errors.append(prefix + "multiple_active_revisions")
            active_families.add(center["family_id"])
        if not float(center["zd"]) + EPSILON < float(center["zg"]):
            errors.append(prefix + "nonpositive_core")
        if center.get("fixed_zd") != center["zd"] or center.get("fixed_zg") != center["zg"]:
            errors.append(prefix + "changed_fixed_core")
        if any(identifier not in pen_ids for identifier in center.get("source_pen_ids", [])):
            errors.append(prefix + "missing_pen")
        if not center.get("formed_at"):
            errors.append(prefix + "missing_formation_time")
        if center.get("fluctuation_dd") != center["dd"] or center.get("fluctuation_gg") != center["gg"]:
            errors.append(prefix + "inconsistent_z_envelope")
        if center.get("unit_kind") == "center_revision":
            proof = center.get("decomposition_proof", {})
            parts = proof.get("segments", [])
            if len(parts) != 3:
                errors.append(prefix + "decomposition_count")
                continue
            if any(p.get("status") != "confirmed" or not p.get("completion_evidence_id") for p in parts[:2]):
                errors.append(prefix + "decomposition_completion")
            if (center["zd"], center["zg"]) != (max(p["low"] for p in parts), min(p["high"] for p in parts)):
                errors.append(prefix + "expansion_core")
            ids = [i for part in parts for i in part["source_unit_ids"]]
            if len(ids) != len(set(ids)) or len(ids) < 9:
                errors.append(prefix + "decomposition_ownership")
            if parts[0]["direction"] != parts[2]["direction"] or parts[0]["direction"] == parts[1]["direction"]:
                errors.append(prefix + "decomposition_direction")
            if any(a["end_date"] != b["start_date"] for a, b in zip(parts, parts[1:])):
                errors.append(prefix + "decomposition_boundary")
            for part in parts:
                movement = movement_by_id.get(part.get("movement_revision_id"))
                if part.get("construction_scope") != "internal" and (not movement or movement["level"] != center["level"] - 1 or not set(part["source_unit_ids"]) <= set(movement["source_unit_ids"])):
                    errors.append(prefix + "decomposition_movement")
                if part.get("status") == "confirmed":
                    point = point_by_family.get(part.get("completion_evidence_id"))
                    if not point or point["confirmed_at"] > part["available_at"]:
                        errors.append(prefix + "decomposition_completion")
                if not part.get("level_evidence_ids"):
                    errors.append(prefix + "decomposition_level")
                for identifier in part.get("level_evidence_ids", []):
                    witness = center_by_id.get(identifier)
                    if not witness or witness["level"] != center["level"] - 1 or witness["revision_at"] > part["available_at"] or not set(witness["owned_unit_ids"]) <= set(part["source_unit_ids"]):
                        errors.append(prefix + "decomposition_level")
                selected = [unit_by_id[i] for i in part["source_unit_ids"] if i in unit_by_id]
                if len(selected) != len(part["source_unit_ids"]) or not _span_contiguous(selected):
                    errors.append(prefix + "decomposition_continuity")
                elif (part["low"], part["high"]) != (min(_low(u) for u in selected), max(_high(u) for u in selected)):
                    errors.append(prefix + "decomposition_range")
            if center.get("boundary_status") == "dynamic" and center.get("recursive_eligible"):
                errors.append(prefix + "dynamic_recursion")
            if center["formed_at"] < proof.get("available_at", ""):
                errors.append(prefix + "promotion_time")
            continue
        core_ids = center.get("core_unit_ids", [])
        if len(core_ids) != 3:
            errors.append(f"center:{center['id']}:core_count")
        if set(core_ids) & set(center.get("entry_unit_ids", [])):
            errors.append(f"center:{center['id']}:entry_owned")
        for unit_id in core_ids:
            key = (int(center["level"]), unit_id)
            if key in core_owner and core_owner[key] != center["family_id"]:
                errors.append(f"center:{center['id']}:shared_core:{unit_id}")
            core_owner[key] = center["family_id"]
        owned_ids = center.get("owned_unit_ids", [])
        context_ids = center.get("context_unit_ids", [])
        if any(identifier not in unit_by_id for identifier in [*owned_ids, *context_ids]):
            errors.append(prefix + "missing_unit")
            continue
        core = [unit_by_id[identifier] for identifier in core_ids if identifier in unit_by_id]
        if len(core) != 3 or not _span_contiguous(core) or not _alternating(core) or _strict_overlap(core) != (center["zd"], center["zg"]):
            errors.append(prefix + "invalid_core_evidence")
            continue
        entry = [unit_by_id[identifier] for identifier in center.get("entry_unit_ids", [])]
        if not _span_contiguous([*entry, *core]):
            errors.append(prefix + "invalid_entry")
        expected_z = [core[0]["id"], core[2]["id"], *center.get("extension_unit_ids", [])]
        if center.get("z_unit_ids") != expected_z or not set(expected_z) <= set(owned_ids):
            errors.append(prefix + "invalid_z_ownership")
        z_units = [unit_by_id[identifier] for identifier in center.get("z_unit_ids", []) if identifier in unit_by_id]
        if not z_units or any(_direction(unit) != _direction(core[0]) for unit in z_units) or center.get("z_direction") != _direction(core[0]):
            errors.append(prefix + "invalid_z_direction")
        if z_units and (center["dd"], center["gg"]) != (min(_low(unit) for unit in z_units), max(_high(unit) for unit in z_units)):
            errors.append(prefix + "invalid_z_envelope")
        if any(not _closed_interval_overlap(_low(unit), _high(unit), center["zd"], center["zg"]) for unit in z_units):
            errors.append(prefix + "invalid_z_extension")
        context = [unit_by_id[identifier] for identifier in context_ids]
        if context and (center.get("context_low"), center.get("context_high")) != (min(_low(unit) for unit in context), max(_high(unit) for unit in context)):
            errors.append(prefix + "invalid_context_envelope")
        owned = [unit_by_id[identifier] for identifier in owned_ids]
        if not _span_contiguous(owned) or set(core_ids) - set(owned_ids) or set(center.get("entry_unit_ids", [])) & set(owned_ids):
            errors.append(prefix + "invalid_owned_span")
        expected_kind = "pen" if center["level"] == 1 else "movement"
        if center.get("unit_kind") != expected_kind or any(unit["kind"] != expected_kind or unit["level"] != center["level"] - 1 for unit in owned):
            errors.append(prefix + "invalid_unit_kind")
        if center.get("source_pen_ids") != _source_pen_ids(owned):
            errors.append(prefix + "source_pen_ownership")
        context = center.get("direction_context") or {}
        if center.get("formation_stage") == "directional":
            expected = "down" if context.get("process_direction") == "up" else "up"
            if not context or center.get("z_direction") != expected:
                errors.append(prefix + "direction_context_mismatch")
        if center["formed_at"] != max([unit["confirmed_at"] for unit in [*entry, *core]] + [context.get("available_at", "")]):
            errors.append(prefix + "formation_time")
    point_ids = {point["family_id"] for point in points}
    movement_unit_owner: dict[tuple[int, str], str] = {}
    for movement in movements:
        if movement.get("active", True):
            for unit_id in movement.get("source_unit_ids", []):
                key = (int(movement["level"]), unit_id)
                if key in movement_unit_owner:
                    errors.append(f"movement:{movement['id']}:shared_unit:{unit_id}")
                movement_unit_owner[key] = movement["id"]
        if movement.get("status") == "confirmed" and movement.get("end_point_id") not in point_ids:
            errors.append(f"movement:{movement['id']}:missing_point")
        if movement.get("recursive_eligible") and (
            movement.get("status") != "confirmed" or movement.get("classification") is None
        ):
            errors.append(f"movement:{movement['id']}:invalid_recursion")
        if any(identifier not in family_ids for identifier in movement.get("center_family_ids", [])):
            errors.append(f"movement:{movement['id']}:missing_center_family")
        for center_id in movement.get("center_revision_ids", []):
            center = center_by_id.get(center_id)
            if not center or not set(center["source_pen_ids"]) <= set(movement.get("source_pen_ids", [])):
                errors.append(f"movement:{movement['id']}:center_ownership")
        if movement.get("status") == "confirmed":
            selected_ids = set(movement.get("source_unit_ids", []))
            for center in centers:
                if center["level"] == movement["level"] and center["formed_at"] <= movement["confirmed_at"] and set(center["core_unit_ids"]) & selected_ids and not set(center["core_unit_ids"]) <= selected_ids:
                    errors.append(f"movement:{movement['id']}:split_core")
    for point in points:
        if point.get("center_revision_id") not in revision_ids:
            errors.append(f"point:{point['id']}:missing_center")
        if point.get("status") == "confirmed" and not point.get("confirmed_at"):
            errors.append(f"point:{point['id']}:missing_confirmation_time")
        source = unit_by_id.get(point.get("source_unit_id"))
        if not source or point["point_date"] != source["end_date"] or abs(float(point["point_price"]) - float(source["end_price"])) > EPSILON:
            errors.append(f"point:{point['id']}:not_real_endpoint")
        center = center_by_id.get(point.get("center_revision_id"))
        if point.get("status") == "confirmed" and center and point["confirmed_at"] < center["formed_at"]:
            errors.append(f"point:{point['id']}:confirmation_before_center")
    for relation in structure.get("relations", []):
        left, right = center_by_id.get(relation["from_id"]), center_by_id.get(relation["to_id"])
        if not left or not right:
            errors.append(f"relation:{relation['id']}:missing_revision")
        elif relation["relation_type"] == "promoted_into":
            if left["id"] not in right.get("child_center_ids", []):
                errors.append(f"relation:{relation['id']}:invalid_parent_link")
        elif relation["relation_type"] in {"expansion_up", "expansion_down"} and relation.get("status") == "confirmed":
            proof = expansion_evidence(left, right, [u for u in units if u["level"] == left["level"] - 1], components)
            if proof["missing_evidence"]:
                errors.extend(f"relation:{relation['id']}:{item}" for item in proof["missing_evidence"])
            if relation.get("confirmed_at") != proof["evidence_available_at"]:
                errors.append(f"relation:{relation['id']}:confirmation_time")
            if relation.get("evidence", {}).get("overlap_witness_unit_ids") != proof["overlap_witness_unit_ids"]:
                errors.append(f"relation:{relation['id']}:witness")
        elif classify_center_relation(left, right) != relation["relation_type"]:
            errors.append(f"relation:{relation['id']}:invalid_geometry")
    return errors


def assert_valid_structure(payload: dict[str, Any]) -> None:
    errors = validate_structure(payload)
    if errors:
        raise ValueError("结构校验失败: " + "; ".join(errors[:30]))


def structure_version(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:24]
