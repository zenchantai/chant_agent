from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Iterable

from .chan_direction import breakout_context, select_context
from .chan_expansion import build_parent_center_proofs, segment_missing_evidence


EPSILON = 1e-9
HIERARCHY_VERSION = "center-hierarchy-pen-center-l2-v1"


def _stable_id(prefix: str, *values: Any) -> str:
    raw = "|".join(str(value) for value in values)
    return f"{prefix}-{hashlib.sha256(raw.encode()).hexdigest()[:14]}"


def _center_family_id(
    level: int, stream_key: tuple[int, int, str], unit_family_ids: Iterable[str],
) -> str:
    return _stable_id(
        "center-family", level, stream_key[0], stream_key[1], stream_key[2],
        *unit_family_ids,
    )


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


def atomic_pen_units(pens: list[dict[str, Any]], *, include_provisional: bool = False) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    for ordinal, pen in enumerate(pens):
        status = pen.get("status", "confirmed")
        if status != "confirmed" and not include_provisional:
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
            "status": status,
            "continuous_range_id": int(pen.get("continuous_range_id", pen.get("range_index", 0))),
            "sequence_id": int(pen.get("sequence_id", 0)),
            "structure_sequence_id": str(pen.get("structure_sequence_id", "")),
            "source_pen_ids": [str(pen["id"])],
            "confirmed_at": pen.get("confirmed_at") or (pen["end_date"] if status == "confirmed" else None),
        })
    return units


def _split_streams(units: list[dict[str, Any]], *, include_provisional: bool = False) -> list[list[dict[str, Any]]]:
    streams: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for unit in sorted(units, key=lambda item: (item["start_date"], item["end_date"], item["id"])):
        if unit.get("status") != "confirmed" and not include_provisional:
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
        "unit_kind": units[0].get("kind", "pen"),
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
    family_id = _center_family_id(
        level, _stream_key(core[0]),
        (
            [unit.get("family_id", unit["id"]) for unit in core]
            if level > 1 else [core[0]["id"]]
        ),
    )
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
        "status": "provisional" if any(unit.get("status") != "confirmed" for unit in [*entry, *core]) else "formed",
        "start_date": core[0]["start_date"],
        "end_date": core[-1]["end_date"],
        "core_start_date": core[0]["start_date"],
        "core_end_date": core[-1]["end_date"],
        "entry_component_id": entry_component["id"] if entry_component else None,
        "entry_unit_ids": [unit["id"] for unit in entry],
        "pre_core_unit_ids": [unit["id"] for unit in entry],
        "direction_context": deepcopy(seed.direction_context),
        "process_direction_at_formation": (seed.direction_context or {}).get("process_direction", "unknown"),
        "ownership_scope": f"L{level}:{_stream_key(core[0])}",
        "ownership_commit_at": None,
        "closure_reason": None,
        "successor_center_id": None,
        "predecessor_center_id": None,
        "boundary_status": "dynamic" if any(unit.get("status") != "confirmed" for unit in [*entry, *core]) else "fixed",
        "formation_type": ("pullback" if seed.direction_context["process_direction"] == "up" else "rebound") if seed.direction_context else "undetermined",
        "formation_stage": "directional" if seed.direction_context else "origin_overlap",
        "direction_established_at": (seed.direction_context or {}).get("available_at"),
        "core_unit_ids": [unit["id"] for unit in core],
        "evidence_cursor_unit_id": core[-1]["id"],
        "unit_kind": "pen",
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
        "formation_modes": ["directional_core" if seed.direction_context else "origin_overlap"],
        "recursive_eligible": bool(seed.direction_context) and not any(unit.get("status") != "confirmed" for unit in [*entry, *core]),
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
    center["ownership_commit_at"] = center["formed_at"]
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
    available_at = max([available_at, center["formed_at"], *[u.get("confirmed_at") or u["end_date"] for u in context]])
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


def _center_candidate_record(
    stream: list[dict[str, Any]], start: int, reason: str, *,
    level: int, direction: str | None = None, overlap: tuple[float, float] | None = None,
    selected_center_family_id: str | None = None, context_unit_ids: list[str] | None = None,
) -> dict[str, Any]:
    source = stream[start:start + 3]
    available_at = max((unit.get("confirmed_at") or unit["end_date"] for unit in source), default="")
    candidate_id = _stable_id(
        "center-candidate", level, _stream_key(source[0]) if source else "empty",
        *(unit["id"] for unit in source),
    )
    return {
        "id": candidate_id,
        "kind": "center_candidate",
        "family_id": _stable_id("center-candidate-family", level, *(unit["id"] for unit in source)),
        "revision_no": 1,
        "previous_revision_id": None,
        "active": reason in {"eligible", "selected"},
        "level": level,
        "stream_id": _stream_key(source[0])[2] if source else "",
        "source_kind": source[0].get("kind", "pen") if source else "pen",
        "status": "selected" if reason == "selected" else "eligible" if reason == "eligible" else "rejected",
        "direction": direction or (_component_direction(source) if source else None),
        "start_date": source[0]["start_date"] if source else "",
        "end_date": source[-1]["end_date"] if source else "",
        "start_price": float(source[0]["start_price"]) if source else None,
        "end_price": float(source[-1]["end_price"]) if source else None,
        "zd": overlap[0] if overlap else None,
        "zg": overlap[1] if overlap else None,
        "source_unit_ids": [unit["id"] for unit in source],
        "context_unit_ids": list(context_unit_ids or []),
        "entry_evidence_id": None,
        "boundary_evidence_id": None,
        "observed_at": available_at,
        "evidence_available_at": available_at,
        "core_start_ordinal": start,
        "core_end_ordinal": start + 2,
        "source_unit_count": len(source),
        "selected_center_family_id": selected_center_family_id,
        "rejection_code": None if reason in {"eligible", "selected"} else reason,
        "rejection_detail": {"reason": reason},
    }


def _successor_core_candidates(
    stream: list[dict[str, Any]], center: dict[str, Any], level: int,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Enumerate independent same-level cores after the committed owner suffix."""
    owned_ids = set(center.get("owned_unit_ids", []))
    boundary_ids = set(center.get("departure_unit_ids", [])) | set(center.get("retest_unit_ids", [])) if center.get("retest_unit_ids") else set()
    scan_ids = boundary_ids or owned_ids
    owned_positions = [unit["ordinal"] for unit in stream if unit["id"] in scan_ids]
    start_floor = max(owned_positions, default=int(center.get("_core_end", 0) - 1)) + 1
    expected = (center.get("process_direction_at_formation") or "").strip()
    candidates: list[dict[str, Any]] = []
    eligible: list[dict[str, Any]] = []
    for start in range(start_floor, len(stream) - 2):
        window = stream[start:start + 3]
        overlap = _strict_overlap(window)
        if not _span_contiguous(window):
            candidates.append(_center_candidate_record(stream, start, "sequence_discontinuity", level=level))
            continue
        if not _alternating(window):
            candidates.append(_center_candidate_record(stream, start, "direction_mismatch", level=level))
            continue
        if overlap is None:
            candidates.append(_center_candidate_record(stream, start, "common_overlap_empty", level=level))
            continue
        if expected not in {"up", "down"} or _direction(window[0]) != expected:
            candidates.append(_center_candidate_record(stream, start, "direction_mismatch", level=level, overlap=overlap))
            continue
        if any(unit["id"] in owned_ids for unit in window):
            candidates.append(_center_candidate_record(stream, start, "ownership_conflict", level=level, overlap=overlap))
            continue
        if _strict_interval_overlap(float(center["zd"]), float(center["zg"]), *overlap):
            candidates.append(_center_candidate_record(stream, start, "overlaps_selected_center", level=level, overlap=overlap))
            continue
        candidate = _center_candidate_record(
            stream, start, "eligible", level=level, direction=_direction(window[0]),
            overlap=overlap, context_unit_ids=[stream[start - 1]["id"]] if start else [],
        )
        candidates.append(candidate)
        eligible.append(candidate)
    if not eligible:
        return candidates, None
    selected = min(eligible, key=lambda item: (
        item["evidence_available_at"], item["core_end_ordinal"], item["core_start_ordinal"],
        item["source_unit_count"], item["id"],
    ))
    selected = deepcopy(selected)
    selected["status"] = "selected"
    selected["active"] = True
    selected["rejection_code"] = None
    for item in candidates:
        if item["id"] == selected["id"]:
            item.update(selected)
        elif item.get("status") == "eligible":
            item["status"] = "rejected"
            item["active"] = False
            item["rejection_code"] = "prior_boundary_won"
            item["rejection_detail"] = {"selected_candidate_id": selected["id"]}
    return candidates, selected


def build_level_centers(
    units: list[dict[str, Any]], level: int, *, origin_kind: str = "model_origin",
    direction_context: dict[str, Any] | None = None,
    allow_successor_core: bool = True,
    include_provisional: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    centers, components, issues = [], [], []
    for stream_index, stream in enumerate(_split_streams(units, include_provisional=include_provisional)):
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
            if level == 1 and context and context.get("reason") == "confirmed_successor_core":
                selected_ids = [unit["id"] for unit in stream[seed.core_start:seed.core_end]]
                for candidate in reversed(issues):
                    if candidate.get("kind") == "center_candidate" and candidate.get("source_unit_ids") == selected_ids:
                        candidate["status"] = "selected"
                        candidate["active"] = True
                        candidate["rejection_code"] = None
                        candidate["selected_center_family_id"] = center["family_id"]
                        break
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
            successor_candidate = None
            candidate_records_by_id: dict[str, dict[str, Any]] = {}
            for scan in range(seed.core_end, len(stream)):
                unit = stream[scan]
                center["evidence_cursor_unit_id"] = unit["id"]
                reverse = breakout_context(stream[max(seed.core_start, scan - 2):scan + 1])
                if reverse and reverse["process_direction"] != center["process_direction_at_formation"]:
                    reverse.update(anchor_kind="successor_evidence", start_index=max(seed.core_start, scan - 2))
                    center.setdefault("successor_direction_evidence", []).append(reverse)
                intersects = (
                    _closed_interval_overlap(_low(unit), _high(unit), seed.zd, seed.zg)
                )
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
                if allow_successor_core:
                    # Commit a successor at the first prefix where its third
                    # unit is available. Later units must not rewrite this
                    # same-level ownership decision.
                    prefix_records, prefix_selected = _successor_core_candidates(
                        stream[:scan + 1], center, level,
                    )
                    for candidate in prefix_records:
                        source_ids = candidate.get("source_unit_ids", [])
                        if source_ids:
                            start_ordinal = next(
                                (index for index, item in enumerate(stream)
                                 if item["id"] == source_ids[0]),
                                candidate.get("core_start_ordinal", scan - 2),
                            )
                            candidate["core_start_ordinal"] = start_ordinal
                            candidate["core_end_ordinal"] = start_ordinal + 2
                        candidate_records_by_id[candidate["id"]] = candidate
                    if prefix_selected:
                        successor_candidate = candidate_records_by_id.get(
                            prefix_selected["id"], prefix_selected,
                        )
                        break
            if successor_candidate and allow_successor_core:
                # Keep later windows for diagnostics, but the first committed
                # boundary remains the sole owner of the successor prefix.
                later_records, _ = _successor_core_candidates(
                    stream, center, level,
                )
                for candidate in later_records:
                    if candidate.get("id") == successor_candidate["id"]:
                        continue
                    if candidate.get("status") == "selected":
                        candidate["status"] = "rejected"
                        candidate["active"] = False
                        candidate["rejection_code"] = "prior_boundary_won"
                        candidate["rejection_detail"] = {
                            "selected_candidate_id": successor_candidate["id"],
                        }
                    candidate_records_by_id[candidate["id"]] = candidate
            candidate_records = list(candidate_records_by_id.values())
            for candidate in candidate_records:
                if candidate.get("status") == "selected":
                    candidate["status"] = "eligible"
                    candidate["active"] = True
                    candidate["rejection_code"] = None
            issues.extend(candidate_records)
            if successor_candidate:
                retest_at = center.get("revision_at", "") if center.get("retest_unit_ids") else ""
                if retest_at and retest_at < successor_candidate["evidence_available_at"]:
                    successor_candidate_id = successor_candidate["id"]
                    successor_candidate = None
                    for item in candidate_records:
                        if item.get("id") == successor_candidate_id:
                            item["status"] = "rejected"
                            item["active"] = False
                            item["rejection_code"] = "prior_boundary_won"
                            item["rejection_detail"] = {"reason": "retest_before_successor"}
                            break
            if successor_candidate:
                    selected_start = int(successor_candidate["core_start_ordinal"])
                    selected_core = stream[selected_start:selected_start + 3]
                    center["status"] = "broken"
                    center["closure_reason"] = "successor_core"
                    center["successor_candidate_id"] = successor_candidate["id"]
                    center["successor_direction"] = _direction(selected_core[0])
                    center["boundary_status"] = "fixed"
                    center["context_unit_ids"] = _unique([
                        *center.get("context_unit_ids", []),
                        *[unit["id"] for unit in stream[seed.core_end:selected_start + 3]],
                    ])
                    _capture_center_revision(center, successor_candidate["evidence_available_at"])
                    next_workspace = max(workspace + 1, selected_start - 1)
                    context = {
                        "process_direction": "down" if _direction(selected_core[0]) == "up" else "up",
                        "reason": "confirmed_successor_core",
                        "anchor_kind": "confirmed_boundary",
                        "anchor_date": selected_core[0]["start_date"],
                        "anchor_price": selected_core[0]["start_price"],
                        "source_unit_ids": [unit["id"] for unit in selected_core],
                        "source_revision_id": center["id"],
                        "available_at": successor_candidate["evidence_available_at"],
                        "start_index": selected_start - 1,
                    }
                    floor = selected_start - 1
            centers.append(center)
            if next_workspace is None or next_workspace <= workspace:
                break
            workspace = next_workspace
    centers.sort(key=lambda item: (item["start_date"], item["end_date"], item["id"]))
    if include_provisional:
        unit_by_id = {unit["id"]: unit for unit in units}
        for center in centers:
            references = [
                *center.get("entry_unit_ids", []), *center.get("core_unit_ids", []),
                *center.get("extension_unit_ids", []), *center.get("peripheral_unit_ids", []),
                *center.get("departure_unit_ids", []), *center.get("retest_unit_ids", []),
            ]
            if not any(unit_by_id.get(identifier, {}).get("status") != "confirmed" for identifier in references):
                continue
            center["status"] = "provisional"
            center["boundary_status"] = "dynamic"
            center["recursive_eligible"] = False
            for revision in center.get("_history", []):
                if revision.get("id") == center.get("id"):
                    revision["status"] = "provisional"
                    revision["boundary_status"] = "dynamic"
                    revision["recursive_eligible"] = False
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


def _candidate_record(
    *, family_id: str, revision_no: int, source: str, child_level: int,
    source_entity_ids: list[str], required_unit_ids: list[str],
    search_unit_ids: list[str], observed_at: str, proofs: list[dict[str, Any]],
    unit_by_id: dict[str, dict[str, Any]],
    missing_evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    selected_proofs = [
        proof for proof in proofs if proof.get("selection_status", "selected") == "selected"
    ]
    rejected_proofs = [
        {key: value for key, value in proof.items() if key != "selection_status"}
        for proof in proofs if proof.get("selection_status") == "rejected"
    ]
    selected = max(
        selected_proofs,
        key=lambda proof: (
            proof["boundary_status"] == "fixed", proof["evidence_available_at"],
            len(proof["source_unit_ids"]), proof["id"],
        ),
        default=None,
    )
    status = selected["boundary_status"] if selected else "unresolved"
    segment_proof_revisions: list[dict[str, Any]] = []
    for proof in proofs:
        selected_proof = proof.get("selection_status", "selected") == "selected"
        for segment in proof.get("segments", []):
            item = deepcopy(segment)
            item["parent_proof_id"] = proof["id"]
            item["candidate_source"] = source
            item["selection_status"] = "selected" if selected_proof else "rejected"
            segment_proof_revisions.append(item)
    return {
        "id": f"{family_id}:r{revision_no}",
        "kind": "promotion_candidate",
        "family_id": family_id,
        "revision_no": revision_no,
        "previous_revision_id": f"{family_id}:r{revision_no - 1}" if revision_no > 1 else None,
        "active": False,
        "candidate_source": source,
        "child_level": child_level,
        "parent_level": child_level + 1,
        "status": status,
        "source_entity_ids": source_entity_ids,
        "required_unit_ids": required_unit_ids,
        "search_unit_ids": search_unit_ids,
        "start_date": unit_by_id[search_unit_ids[0]]["start_date"],
        "end_date": unit_by_id[search_unit_ids[-1]]["end_date"],
        "observed_at": observed_at,
        "evidence_available_at": (
            max(selected["evidence_available_at"], observed_at)
            if selected else observed_at
        ),
        "proof_ids": [proof["id"] for proof in selected_proofs],
        "selected_parent_proof_id": selected["id"] if selected else None,
        "selected_segment_proof_ids": [
            segment["id"] for proof in selected_proofs
            for segment in proof.get("segments", [])
        ],
        "selected_parent_family_id": None,
        "missing_evidence": (
            [] if selected_proofs else (
                missing_evidence or [{"code": "three_segment_proofs_missing"}]
            )
        ),
        "rejected_proofs": rejected_proofs,
        "segment_proof_revisions": segment_proof_revisions,
        "_proofs": [
            {key: value for key, value in proof.items() if key != "selection_status"}
            for proof in selected_proofs
        ],
    }


_SEGMENT_REVISION_STATE_FIELDS = (
    "status", "direction", "start_date", "end_date", "start_price", "end_price",
    "low", "high", "source_unit_ids", "source_pen_ids", "center_witnesses",
    "level_evidence_ids", "boundary_mode", "completion_evidence_id",
    "evidence_available_at", "continuous_range_id", "sequence_id",
    "structure_sequence_id", "recursive_eligible",
)


def _segment_revision_state(segment: dict[str, Any]) -> str:
    return json.dumps(
        {field: segment.get(field) for field in _SEGMENT_REVISION_STATE_FIELDS},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )


def _normalize_segment_proof_revisions(candidates: list[dict[str, Any]]) -> None:
    """Assign append-only local segment revisions in first-observed order."""
    occurrences: list[tuple[tuple[Any, ...], dict[str, Any], dict[str, Any]]] = []
    for candidate in candidates:
        proof_groups = [
            (0, proof) for proof in candidate.get("_proofs", [])
        ] + [
            (1, proof) for proof in candidate.get("rejected_proofs", [])
        ]
        for rejected, proof in proof_groups:
            for index, segment in enumerate(proof.get("segments", [])):
                if segment.get("source_kind") != "local_pen_group":
                    continue
                occurrences.append((
                    (
                        candidate.get("observed_at", ""),
                        int(candidate.get("revision_no", 0)), candidate["id"],
                        rejected, proof.get("evidence_available_at", ""),
                        proof.get("id", ""), index,
                    ),
                    proof,
                    segment,
                ))

    family_states: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    family_order: dict[str, list[str]] = defaultdict(list)
    for _, _, segment in sorted(occurrences, key=lambda item: item[0]):
        family_id = segment["family_id"]
        state = _segment_revision_state(segment)
        canonical = family_states[family_id].get(state)
        if canonical is None:
            revision_no = len(family_order[family_id]) + 1
            previous = (
                f"{family_id}:r{revision_no - 1}" if revision_no > 1 else None
            )
            canonical = {
                **deepcopy(segment),
                "id": f"{family_id}:r{revision_no}",
                "revision_no": revision_no,
                "previous_revision_id": previous,
            }
            family_states[family_id][state] = canonical
            family_order[family_id].append(state)
        candidate_source = segment.get("candidate_source")
        segment.clear()
        segment.update(deepcopy(canonical))
        if candidate_source:
            segment["candidate_source"] = candidate_source

    for candidate in candidates:
        selected_proofs = candidate.get("_proofs", [])
        rejected_proofs = candidate.get("rejected_proofs", [])
        for proof in [*selected_proofs, *rejected_proofs]:
            if proof.get("source_kind") != "local_pen_group":
                continue
            proof["id"] = _stable_id(
                "parent-proof", proof["parent_level"], "local_pen_group",
                proof["boundary_status"],
                *(segment["id"] for segment in proof.get("segments", [])),
            )
        selected = max(
            selected_proofs,
            key=lambda proof: (
                proof["boundary_status"] == "fixed",
                proof["evidence_available_at"], len(proof["source_unit_ids"]),
                proof["id"],
            ),
            default=None,
        )
        candidate["status"] = selected["boundary_status"] if selected else "unresolved"
        candidate["evidence_available_at"] = (
            max(selected["evidence_available_at"], candidate["observed_at"])
            if selected else candidate["observed_at"]
        )
        candidate["proof_ids"] = [proof["id"] for proof in selected_proofs]
        candidate["selected_parent_proof_id"] = selected["id"] if selected else None
        candidate["selected_segment_proof_ids"] = [
            segment["id"] for proof in selected_proofs
            for segment in proof.get("segments", [])
        ]
        revisions: list[dict[str, Any]] = []
        for selected_proof, proof in [
            *((True, proof) for proof in selected_proofs),
            *((False, proof) for proof in rejected_proofs),
        ]:
            for segment in proof.get("segments", []):
                item = deepcopy(segment)
                item["parent_proof_id"] = proof["id"]
                item["candidate_source"] = candidate["candidate_source"]
                item["selection_status"] = (
                    "selected" if selected_proof else "rejected"
                )
                revisions.append(item)
        candidate["segment_proof_revisions"] = revisions


def _parent_family_id(proof: dict[str, Any]) -> str:
    first = proof["segments"][0]
    return _center_family_id(
        proof["parent_level"],
        (
            int(first.get("continuous_range_id", 0)),
            int(first.get("sequence_id", 0)),
            str(first.get("structure_sequence_id", "")),
        ),
        (
            segment.get("family_id") or segment["id"]
            for segment in proof["segments"]
        ),
    )


def _commit_parent_centers(
    candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
    proof_parent: dict[str, str] = {}
    for candidate in candidates:
        for proof in candidate.get("_proofs", []):
            family_id = _parent_family_id(proof)
            proof_parent[proof["id"]] = family_id
            effective_proof = deepcopy(proof)
            effective_at = max(
                proof["evidence_available_at"], candidate.get("observed_at", ""),
            )
            effective_proof["evidence_available_at"] = effective_at
            effective_proof["available_at"] = effective_at
            key = (
                family_id, effective_proof["boundary_status"],
                tuple(effective_proof["source_unit_ids"]), effective_at,
            )
            entry = grouped.setdefault(key, {
                "family_id": family_id,
                "proof": effective_proof,
                "formation_modes": [],
                "candidate_source_ids": [],
                "child_center_ids": [],
            })
            entry["formation_modes"] = _unique([
                *entry["formation_modes"], candidate["candidate_source"],
            ])
            entry["candidate_source_ids"] = _unique([
                *entry["candidate_source_ids"], candidate["id"],
            ])
            entry["child_center_ids"] = _unique([
                *entry["child_center_ids"], *candidate.get("child_center_ids", []),
            ])

    by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in grouped.values():
        by_family[entry["family_id"]].append(entry)
    promoted: list[dict[str, Any]] = []
    claimed_source_units: dict[int, set[str]] = defaultdict(set)
    for family_id, entries in by_family.items():
        family_units = {
            unit_id
            for entry in entries
            for unit_id in entry["proof"].get("source_unit_ids", [])
        }
        parent_level = int(entries[0]["proof"]["parent_level"])
        historical_only = bool(claimed_source_units[parent_level].intersection(family_units))
        if not historical_only:
            claimed_source_units[parent_level].update(family_units)
        entries.sort(key=lambda entry: (
            entry["proof"]["evidence_available_at"],
            entry["proof"]["boundary_status"] == "fixed",
            len(entry["proof"]["source_unit_ids"]),
            entry["proof"]["id"],
        ))
        formed_at = entries[0]["proof"]["evidence_available_at"]
        previous = None
        family_modes: list[str] = []
        family_candidate_ids: list[str] = []
        family_child_center_ids: list[str] = []
        for revision_no, entry in enumerate(entries, 1):
            proof = entry["proof"]
            family_modes = _unique([*family_modes, *entry["formation_modes"]])
            family_candidate_ids = _unique([
                *family_candidate_ids, *entry["candidate_source_ids"],
            ])
            family_child_center_ids = _unique([
                *family_child_center_ids, *entry["child_center_ids"],
            ])
            parts = proof["segments"]
            core_ids = [part["id"] for part in parts]
            fixed = proof["boundary_status"] == "fixed"
            dd = min(parts[0]["low"], parts[2]["low"])
            gg = max(parts[0]["high"], parts[2]["high"])
            child_center_ids = list(family_child_center_ids)
            child_segment_ids = _unique(part["id"] for part in parts)
            item = {
                "id": f"{family_id}:r{revision_no}",
                "kind": "center",
                "family_id": family_id,
                "revision_no": revision_no,
                "previous_revision_id": previous,
                "active": revision_no == len(entries) and not historical_only,
                "historical_only": historical_only,
                "level": proof["parent_level"],
                "status": "formed",
                "boundary_status": proof["boundary_status"],
                "start_date": parts[0]["start_date"],
                "end_date": parts[-1]["end_date"],
                "core_start_date": parts[0]["start_date"],
                "core_end_date": parts[-1]["end_date"],
                "entry_component_id": None,
                "entry_unit_ids": [],
                "pre_core_unit_ids": [],
                "direction_context": None,
                "process_direction_at_formation": "unknown",
                "formation_type": "pullback" if parts[0]["direction"] == "down" else "rebound",
                "formation_stage": "directional",
                "direction_established_at": proof["evidence_available_at"],
                "core_unit_ids": core_ids,
                "evidence_cursor_unit_id": core_ids[-1],
                "unit_kind": "segment_proof",
                "child_segment_ids": child_segment_ids,
                "z_unit_ids": [core_ids[0], core_ids[2]],
                "z_direction": parts[0]["direction"],
                "formed_at": formed_at,
                "promotion_confirmed_at": proof["evidence_available_at"] if fixed else None,
                "connection_component_ids": [],
                "overlap_witness_unit_ids": [],
                "missing_evidence": [],
                "extension_unit_ids": [],
                "peripheral_unit_ids": [],
                "departure_unit_ids": [],
                "retest_unit_ids": [],
                "owned_unit_ids": core_ids,
                "context_unit_ids": core_ids,
                "source_pen_ids": proof["source_pen_ids"],
                "child_center_ids": child_center_ids,
                "zd": proof["zd"],
                "zg": proof["zg"],
                "fixed_zd": proof["zd"] if fixed else None,
                "fixed_zg": proof["zg"] if fixed else None,
                "dd": min(part["low"] for part in parts),
                "gg": max(part["high"] for part in parts),
                "z_high_min": min(part["high"] for part in parts),
                "z_low_max": max(part["low"] for part in parts),
                "touch_unit_ids": [],
                "fluctuation_dd": dd,
                "fluctuation_gg": gg,
                "context_low": min(part["low"] for part in parts),
                "context_high": max(part["high"] for part in parts),
                "entry_direction": None,
                "core_formation_pattern": "-".join(part["direction"] for part in parts),
                "departure_direction": None,
                "formation_modes": list(family_modes),
                "candidate_source_ids": list(family_candidate_ids),
                "recursive_eligible": fixed,
                "continuous_range_id": parts[0].get("continuous_range_id", 0),
                "sequence_id": parts[0].get("sequence_id", 0),
                "structure_sequence_id": parts[0].get("structure_sequence_id", ""),
                "decomposition_proof": proof,
                "revision_at": proof["evidence_available_at"],
                "available_at": proof["evidence_available_at"],
                "evidence": {"decomposition_proof": proof},
            }
            promoted.append(item)
            previous = item["id"]
    return promoted, proof_parent


def build_promotion_candidates(
    centers: list[dict[str, Any]], relations: list[dict[str, Any]], child_level: int,
    units: list[dict[str, Any]], components: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    if child_level != 1:
        raise ValueError("Only L1-to-L2 center promotion is supported")
    unit_by_id = {unit["id"]: unit for unit in units}
    candidates: list[dict[str, Any]] = []

    for center in centers:
        snapshots = sorted(
            center.get("_history", [center]),
            key=lambda item: (item.get("revision_at", item.get("formed_at", "")), int(item.get("revision_no", 0)), item["id"]),
        )
        qualifying: list[dict[str, Any]] = []
        seen_spans: set[tuple[str, ...]] = set()
        for snapshot in snapshots:
            search_ids = tuple(snapshot.get("owned_unit_ids", []))
            if len(search_ids) < 9 or search_ids in seen_spans:
                continue
            seen_spans.add(search_ids)
            qualifying.append(snapshot)
        if not qualifying:
            continue
        if len(qualifying) > 13:
            qualifying = [*qualifying[:12], qualifying[-1]]
        family_id = _stable_id(
            "promotion-candidate-family", "extension", child_level, center["family_id"],
        )
        required = list(qualifying[0]["owned_unit_ids"][:9])
        for revision_no, snapshot in enumerate(qualifying, 1):
            search_ids = list(snapshot["owned_unit_ids"])
            observed_at = snapshot.get("revision_at", snapshot["formed_at"])
            proofs = build_parent_center_proofs(
                child_level=child_level,
                units=units,
                required_unit_ids=set(required),
                search_unit_ids=search_ids,
                candidate_source="extension_decomposition",
                observed_at=observed_at,
                include_rejected=True,
            )
            candidate = _candidate_record(
                family_id=family_id,
                revision_no=revision_no,
                source="extension_decomposition",
                child_level=child_level,
                source_entity_ids=[snapshot["id"]],
                required_unit_ids=required,
                search_unit_ids=search_ids,
                observed_at=observed_at,
                proofs=proofs,
                unit_by_id=unit_by_id,
                missing_evidence=(segment_missing_evidence(
                    units=units, required_unit_ids=required, search_unit_ids=search_ids,
                ) if not proofs else None),
            )
            candidate["child_center_ids"] = [snapshot["id"]]
            candidates.append(candidate)
            if candidates[-1].get("status") == "fixed":
                break

    by_id = {center["id"]: center for center in centers}
    for relation in relations:
        if relation["relation_type"] not in {"expansion_up", "expansion_down"}:
            continue
        final_left, final_right = by_id[relation["from_id"]], by_id[relation["to_id"]]
        left, right = final_left, final_right
        evidence = expansion_evidence(left, right, units, components)
        for snapshot in final_right.get("_history", [final_right]):
            available = [
                item for item in final_left.get("_history", [final_left])
                if item["revision_at"] <= snapshot["revision_at"]
            ]
            if not available:
                continue
            proof = expansion_evidence(available[-1], snapshot, units, components)
            if not proof["missing_evidence"]:
                left, right, evidence = available[-1], snapshot, proof
                break
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
        relation.update(
            id=_stable_id("relation", left["family_id"], right["family_id"], relation["relation_type"]),
            status="confirmed",
            expansion_status="confirmed",
            confirmed_at=evidence["evidence_available_at"],
            from_id=left["id"],
            to_id=right["id"],
            start_date=left["start_date"],
            end_date=right["end_date"],
        )
        family_id = _stable_id(
            "promotion-candidate-family", "expansion", child_level,
            left["family_id"], right["family_id"],
        )
        expansion_source_ids = list(evidence["source_unit_ids"])
        positions = [
            index for index, unit in enumerate(units)
            if unit["id"] in set(expansion_source_ids)
        ]
        span_start, span_end = min(positions), max(positions)
        observation_spans: list[list[str]] = []
        for end_index in range(span_end, len(units)):
            span = units[span_start:end_index + 1]
            if not span or _stream_key(span[0]) != _stream_key(left) or not _span_contiguous(span):
                break
            observation_spans.append([unit["id"] for unit in span])
        required_ids = list(
            observation_spans[0] if observation_spans else expansion_source_ids
        )
        frozen_entities = [relation["id"], left["id"], right["id"]]
        if len(observation_spans) > 13:
            observation_spans = [*observation_spans[:12], observation_spans[-1]]
        expansion_revisions: list[dict[str, Any]] = []
        for revision_no, search_ids in enumerate(observation_spans, 1):
            observed_at = max(
                evidence["evidence_available_at"],
                unit_by_id[search_ids[-1]].get("confirmed_at") or unit_by_id[search_ids[-1]]["end_date"],
            )
            proofs = build_parent_center_proofs(
                child_level=child_level,
                units=units,
                required_unit_ids=set(required_ids),
                search_unit_ids=search_ids,
                candidate_source="expansion_decomposition",
                observed_at=observed_at,
                include_rejected=True,
            )
            missing = segment_missing_evidence(
                units=units, required_unit_ids=required_ids, search_unit_ids=search_ids,
            )
            candidate = _candidate_record(
                family_id=family_id,
                revision_no=revision_no,
                source="expansion_decomposition",
                child_level=child_level,
                source_entity_ids=frozen_entities,
                required_unit_ids=required_ids,
                search_unit_ids=search_ids,
                observed_at=observed_at,
                proofs=proofs,
                unit_by_id=unit_by_id,
                missing_evidence=missing,
            )
            candidate["child_center_ids"] = [left["id"], right["id"]]
            expansion_revisions.append(candidate)
            if expansion_revisions[-1].get("status") == "fixed":
                break
        candidates.extend(expansion_revisions)
        candidate = expansion_revisions[-1]
        relation["promotion_candidate_status"] = candidate["status"]
        relation["promotion_candidate_family_id"] = family_id
        relation["promotion_proof_ids"] = candidate["proof_ids"]
        relation["boundary_missing_evidence"] = [
            item["code"] for item in candidate["missing_evidence"]
        ]
        relation["boundary_status"] = candidate["status"]

    _normalize_segment_proof_revisions(candidates)
    promoted, proof_parent = _commit_parent_centers(candidates)
    candidate_parent: dict[str, str] = {}
    for candidate in candidates:
        parents = _unique(
            proof_parent[proof["id"]]
            for proof in candidate.get("_proofs", [])
            if proof["id"] in proof_parent
        )
        if parents:
            candidate["selected_parent_family_id"] = parents[0]
            candidate_parent[candidate["family_id"]] = parents[0]
    for relation in relations:
        if relation.get("promotion_proof_ids"):
            parent_ids = _unique(
                proof_parent[proof_id]
                for proof_id in relation["promotion_proof_ids"]
                if proof_id in proof_parent
            )
            relation["selected_parent_family_id"] = parent_ids[0] if parent_ids else None

    grouped_candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        grouped_candidates[candidate["family_id"]].append(candidate)
    for revisions in grouped_candidates.values():
        revisions.sort(key=lambda item: item["revision_no"])
        revisions[-1]["active"] = True

    latest_parent = {
        family_id: max(
            (center for center in promoted if center["family_id"] == family_id),
            key=lambda item: item["revision_no"],
        )
        for family_id in {center["family_id"] for center in promoted}
    }
    lineage: list[dict[str, Any]] = []
    for parent in latest_parent.values():
        for child_id in parent["child_center_ids"]:
            lineage.append({
                "id": _stable_id("relation", child_id, parent["id"], "promoted_into"),
                "kind": "center_relation",
                "level": parent["level"],
                "relation_type": "promoted_into",
                "from_id": child_id,
                "to_id": parent["id"],
                "start_date": parent["start_date"],
                "end_date": parent["end_date"],
                "evidence": {
                    "formation_modes": parent["formation_modes"],
                    "parent_family_id": parent["family_id"],
                },
            })
    for candidate in candidates:
        candidate.pop("_proofs", None)
    return candidates, promoted, lineage


def _promote_expansions(
    centers: list[dict[str, Any]], relations: list[dict[str, Any]], next_level: int,
    units: list[dict[str, Any]], components: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Compatibility adapter; production uses ``build_promotion_candidates``."""
    _, promoted, lineage = build_promotion_candidates(
        centers, relations, next_level - 1, units, components,
    )
    return promoted, lineage


def _strip_internal(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if not key.startswith("_")}


def _merge_center_revision_record(
    left: dict[str, Any], right: dict[str, Any],
) -> dict[str, Any]:
    preferred = right if right.get("decomposition_proof") else left
    other = left if preferred is right else right
    merged = {**deepcopy(other), **deepcopy(preferred)}
    for field in (
        "formation_modes", "candidate_source_ids", "child_center_ids",
        "source_pen_ids", "alternate_formation_revision_ids",
    ):
        merged[field] = _unique([*left.get(field, []), *right.get(field, [])])
    statuses = {left.get("boundary_status"), right.get("boundary_status")}
    if "fixed" in statuses:
        merged["boundary_status"] = "fixed"
        merged["fixed_zd"] = merged["zd"]
        merged["fixed_zg"] = merged["zg"]
        merged["promotion_confirmed_at"] = max(
            filter(None, [
                left.get("promotion_confirmed_at"), right.get("promotion_confirmed_at"),
                left.get("revision_at"), right.get("revision_at"),
            ]),
            default=None,
        )
    elif "dynamic" in statuses:
        merged["boundary_status"] = "dynamic"
        merged["fixed_zd"] = None
        merged["fixed_zg"] = None
    merged["recursive_eligible"] = bool(
        left.get("recursive_eligible") or right.get("recursive_eligible")
    ) and merged.get("boundary_status") != "dynamic"
    merged["evidence"] = {
        **(left.get("evidence") if isinstance(left.get("evidence"), dict) else {}),
        **(right.get("evidence") if isinstance(right.get("evidence"), dict) else {}),
    }
    return merged


def _merge_level_center_sources(
    regular: list[dict[str, Any]], promoted: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Merge regular recursion and committed parent proofs before level analysis."""
    regular_by_family = {center["family_id"]: center for center in regular}
    promoted_by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for revision in promoted:
        promoted_by_family[revision["family_id"]].append(revision)
    calculation: list[dict[str, Any]] = []
    display_only: list[dict[str, Any]] = []
    for family_id in sorted(set(regular_by_family) | set(promoted_by_family)):
        regular_center = regular_by_family.get(family_id)
        incoming_all = promoted_by_family.get(family_id, [])
        incoming = [item for item in incoming_all if not item.get("historical_only")]
        historical = [item for item in incoming_all if item.get("historical_only")]
        display_only.extend(historical)
        if not incoming and regular_center is None:
            continue
        if regular_center is not None and not incoming:
            calculation.append(regular_center)
            continue
        records = [
            deepcopy(item)
            for item in (
                [*(regular_center.get("_history", [regular_center]) if regular_center else []), *incoming]
            )
        ]
        merged_events: dict[tuple[Any, ...], dict[str, Any]] = {}
        for record in records:
            event_key = (
                record.get("revision_at", record.get("formed_at", "")),
                tuple(record.get("core_unit_ids", [])),
                record.get("end_date"), float(record.get("zd", 0)), float(record.get("zg", 0)),
            )
            if event_key in merged_events:
                merged_events[event_key] = _merge_center_revision_record(
                    merged_events[event_key], record,
                )
            else:
                merged_events[event_key] = record
        history = sorted(merged_events.values(), key=lambda item: (
            item.get("revision_at", item.get("formed_at", "")),
            item.get("boundary_status") == "fixed",
            len(item.get("owned_unit_ids", [])), item.get("id", ""),
        ))
        previous = None
        for revision_no, item in enumerate(history, 1):
            item["id"] = f"{family_id}:r{revision_no}"
            item["revision_no"] = revision_no
            item["previous_revision_id"] = previous
            item["active"] = revision_no == len(history)
            if item.get("unit_kind") == "segment_proof":
                item["fluctuation_dd"] = item.get("dd")
                item["fluctuation_gg"] = item.get("gg")
            previous = item["id"]
        latest = deepcopy(history[-1])
        latest["_history"] = history
        has_fixed_parent = any(item.get("boundary_status") == "fixed" for item in incoming)
        if regular_center is not None or has_fixed_parent:
            calculation.append(latest)
        else:
            display_only.extend(history)
    calculation.sort(key=lambda item: (item["start_date"], item["end_date"], item["id"]))
    for ordinal, center in enumerate(calculation):
        center["ordinal"] = ordinal
        for revision in center.get("_history", []):
            revision["ordinal"] = ordinal
    return calculation, display_only


def merge_formation_paths(centers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    by_span: dict[tuple[Any, ...], dict[str, Any]] = {}
    for center in sorted(
        [item for item in centers if item.get("active")],
        key=lambda item: (item["formed_at"], item["start_date"], item["id"]),
    ):
        key = (center["level"], _stream_key(center), tuple(center["source_pen_ids"]))
        previous = by_span.get(key)
        if previous is None:
            by_span[key] = center
            continue
        if previous["family_id"] == center["family_id"]:
            continue
        issues.append({
            "id": _stable_id("issue", previous["id"], center["id"]),
            "kind": "formation_conflict",
            "status": "undetermined",
            "level": center["level"],
            "start_date": center["start_date"],
            "end_date": center["end_date"],
            "evidence": {
                "first_revision_id": previous["id"],
                "second_revision_id": center["id"],
                "same_geometry": (previous["zd"], previous["zg"]) == (center["zd"], center["zg"]),
            },
        })
    return issues


def _clean_center_fields(center: dict[str, Any]) -> dict[str, Any]:
    cleaned = _strip_internal(center)
    for key in ("recursive_eligible",):
        cleaned.pop(key, None)
    return cleaned


def _build_pen_center_hierarchy(
    pens: list[dict[str, Any]], profile: str, *, include_provisional: bool = False,
) -> dict[str, Any]:
    promotes_l2 = profile == "pen_centers_l2"
    units = atomic_pen_units(pens, include_provisional=include_provisional)
    all_centers: list[dict[str, Any]] = []
    all_candidates: list[dict[str, Any]] = []
    all_center_candidates: dict[str, dict[str, Any]] = {}
    all_segment_proofs: dict[str, dict[str, Any]] = {}
    all_components: dict[str, dict[str, Any]] = {}
    all_relations: list[dict[str, Any]] = []
    all_issues: list[dict[str, Any]] = []

    if units:
        regular_centers, components, issues = build_level_centers(
            units, 1, allow_successor_core=True,
            include_provisional=include_provisional,
        )
        centers, display_only = _merge_level_center_sources(regular_centers, [])
        relations = build_relations(centers)
        candidate_revisions, promoted, promotion_relations = (
            build_promotion_candidates(
                centers, relations, 1, units, components,
            ) if promotes_l2 else ([], [], [])
        )

        for center in [*centers, *display_only, *promoted]:
            history = center.get("_history") or [center]
            for revision in history:
                snapshot = deepcopy(revision)
                snapshot["active"] = center.get("active", True) and snapshot["id"] == center["id"]
                if center.get("absorbed_into_family_id"):
                    snapshot["absorbed_into_family_id"] = center["absorbed_into_family_id"]
                all_centers.append(snapshot)

        for candidate in candidate_revisions:
            for segment in candidate.get("segment_proof_revisions", []):
                existing = all_segment_proofs.get(segment["id"])
                item = deepcopy(segment)
                if existing:
                    if _segment_revision_state(existing) != _segment_revision_state(item):
                        raise AssertionError(f"segment revision identity collision: {segment['id']}")
                    if existing.get("selection_status") == "selected" or item.get("selection_status") == "selected":
                        existing["selection_status"] = "selected"
                    continue
                all_segment_proofs[segment["id"]] = item
            candidate.pop("segment_proof_revisions", None)
        all_candidates.extend(candidate_revisions)
        all_components.update({item["id"]: item for item in components})
        all_relations.extend([*relations, *promotion_relations])
        for issue in issues:
            if issue.get("kind") != "center_candidate":
                all_issues.append(issue)
                continue
            normalized = _strip_internal(issue)
            previous = all_center_candidates.get(issue["id"])
            if previous:
                if previous.get("selected_center_family_id") and not normalized.get("selected_center_family_id"):
                    normalized["selected_center_family_id"] = previous["selected_center_family_id"]
                if previous.get("status") == "selected" and normalized.get("status") != "selected":
                    normalized.update(status="selected", active=True, rejection_code=None)
            all_center_candidates[issue["id"]] = normalized

    latest_center_by_family: dict[str, dict[str, Any]] = {}
    for center in all_centers:
        previous = latest_center_by_family.get(center["family_id"])
        if previous is None or (
            center.get("revision_at", center.get("formed_at", "")), center.get("revision_no", 0)
        ) > (
            previous.get("revision_at", previous.get("formed_at", "")), previous.get("revision_no", 0)
        ):
            latest_center_by_family[center["family_id"]] = center
    for center in all_centers:
        if center.get("unit_kind") == "segment_proof" or center.get("decomposition_proof"):
            center["fluctuation_dd"] = center.get("dd")
            center["fluctuation_gg"] = center.get("gg")
    center_revision_ids = {center["id"] for center in all_centers}
    for relation in all_relations:
        if relation.get("relation_type") != "promoted_into":
            continue
        parent = latest_center_by_family.get(relation.get("evidence", {}).get("parent_family_id"))
        if not parent:
            continue
        relation["to_id"] = parent["id"]
        relation["end_date"] = parent["end_date"]
        relation["id"] = _stable_id("relation", relation["from_id"], parent["id"], "promoted_into")
        if relation["from_id"] not in center_revision_ids:
            relation["status"] = "invalidated"

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
    candidate_revisions = sorted(all_candidates, key=lambda item: (item["family_id"], item["revision_no"]))
    segment_proof_revisions = sorted(
        all_segment_proofs.values(),
        key=lambda item: (item.get("family_id", item["id"]), int(item.get("revision_no", 1)), item["id"]),
    )
    latest_segment_by_family: dict[str, dict[str, Any]] = {}
    for segment in segment_proof_revisions:
        segment["active"] = False
        previous = latest_segment_by_family.get(segment["family_id"])
        if previous is None or (int(segment.get("revision_no", 1)), segment["id"]) > (int(previous.get("revision_no", 1)), previous["id"]):
            latest_segment_by_family[segment["family_id"]] = segment
    for segment in latest_segment_by_family.values():
        segment["active"] = True

    levels = sorted({int(center["level"]) for center in active_centers})
    return {
        "centers": [_clean_center_fields(item) for item in active_centers],
        "center_revisions": [_clean_center_fields(item) for item in center_revisions],
        "promotion_candidates": [_strip_internal(item) for item in candidate_revisions if item.get("active")],
        "promotion_candidate_revisions": [_strip_internal(item) for item in candidate_revisions],
        "center_candidates": sorted(all_center_candidates.values(), key=lambda item: (item.get("evidence_available_at", ""), item.get("core_end_ordinal", 0), item["id"])),
        "center_candidate_revisions": sorted(all_center_candidates.values(), key=lambda item: (item.get("evidence_available_at", ""), item.get("core_end_ordinal", 0), item["id"])),
        "segment_proofs": [_strip_internal(item) for item in segment_proof_revisions if item.get("selection_status") == "selected" and item.get("active", True)],
        "segment_proof_revisions": [_strip_internal(item) for item in segment_proof_revisions],
        "components": [_strip_internal(item) for item in sorted(all_components.values(), key=lambda item: (item["start_date"], item["id"]))],
        "relations": [_strip_internal(item) for item in all_relations],
        "issues": all_issues,
        "unassigned_by_level": {"1": []} if units else {},
        "levels": levels,
        "max_level": max(levels, default=0),
        "hierarchy_version": HIERARCHY_VERSION,
    }


def build_structure_hierarchy(
    pens: list[dict[str, Any]], macd: list[dict[str, Any]] | None = None,
    market_dates: list[str] | None = None,
    *, calculation_profile: str = "pen_centers_l2", include_provisional: bool = False,
) -> dict[str, Any]:
    if calculation_profile not in {"pen_centers_l2", "pen_centers_only"}:
        raise ValueError(f"不支持的结构计算策略: {calculation_profile}")
    return _build_pen_center_hierarchy(
        pens, calculation_profile, include_provisional=include_provisional,
    )


def validate_structure(payload: dict[str, Any], *, include_provisional: bool = False) -> list[str]:
    structure = payload.get("structure", payload)
    centers = structure.get("center_revisions", structure.get("centers", []))
    segment_proofs = structure.get("segment_proof_revisions", structure.get("segment_proofs", []))
    candidates = structure.get(
        "promotion_candidate_revisions", structure.get("promotion_candidates", []),
    )
    center_candidates = structure.get(
        "center_candidate_revisions", structure.get("center_candidates", []),
    )
    pens = structure.get("pens", payload.get("pens", []))
    pen_ids = {pen["id"] for pen in pens}
    segment_by_id = {item["id"]: item for item in segment_proofs}
    errors: list[str] = []
    for obsolete in ("movements", "movement_revisions", "movement_levels", "points", "point_revisions"):
        if obsolete in structure:
            errors.append(f"obsolete_field:{obsolete}")
    for name, values in (
        ("centers", centers),
        ("promotion_candidates", candidates), ("segment_proofs", segment_proofs),
        ("center_candidates", center_candidates),
    ):
        identifiers = [item.get("id") for item in values]
        if len(identifiers) != len(set(identifiers)):
            errors.append(f"{name}:duplicate_ids")
    core_owner: dict[tuple[int, str], str] = {}
    revision_ids = {center["id"] for center in centers}
    center_by_id = {center["id"]: center for center in centers}
    units = atomic_pen_units(pens, include_provisional=include_provisional)
    unit_by_id = {unit["id"]: unit for unit in units}
    for segment in segment_proofs:
        prefix = f"segment-proof:{segment['id']}:"
        source_ids = segment.get("source_unit_ids", [])
        selected = [unit_by_id.get(identifier) for identifier in source_ids]
        if len(source_ids) < 3 or len(selected) != len(source_ids) or any(unit is None for unit in selected):
            errors.append(prefix + "missing_source_units")
            continue
        if not _span_contiguous(selected):
            errors.append(prefix + "continuity_failed")
        expected_direction = "up" if float(selected[-1]["end_price"]) > float(selected[0]["start_price"]) + EPSILON else "down" if float(selected[-1]["end_price"]) < float(selected[0]["start_price"]) - EPSILON else None
        if expected_direction is None or segment.get("direction") != expected_direction:
            errors.append(prefix + "direction_mismatch")
        if (segment.get("low"), segment.get("high")) != (min(_low(unit) for unit in selected), max(_high(unit) for unit in selected)):
            errors.append(prefix + "range_mismatch")
        if segment.get("status") == "confirmed" and not segment.get("completion_evidence_id"):
            errors.append(prefix + "missing_completion")
        if segment.get("recursive_eligible") and segment.get("status") != "confirmed":
            errors.append(prefix + "invalid_recursion")
    components = structure.get("components", [])
    family_ids = {center["family_id"] for center in centers}
    family_levels: dict[str, set[int]] = defaultdict(set)
    for center in centers:
        family_levels[center["family_id"]].add(int(center["level"]))
    for family_id, levels in family_levels.items():
        if len(levels) != 1:
            errors.append(f"center-family:{family_id}:mixed_levels")
    active_families: set[str] = set()
    parent_unit_owner: dict[tuple[int, str], str] = {}
    for center in centers:
        prefix = f"center:{center['id']}:"
        if center.get("active"):
            if center["family_id"] in active_families:
                errors.append(prefix + "multiple_active_revisions")
            active_families.add(center["family_id"])
        if not float(center["zd"]) + EPSILON < float(center["zg"]):
            errors.append(prefix + "nonpositive_core")
        proof = center.get("decomposition_proof")
        if proof:
            dynamic = center.get("boundary_status") == "dynamic"
            if dynamic and (center.get("fixed_zd") is not None or center.get("fixed_zg") is not None):
                errors.append(prefix + "dynamic_fixed_core")
            if not dynamic and (
                center.get("fixed_zd") != center["zd"]
                or center.get("fixed_zg") != center["zg"]
            ):
                errors.append(prefix + "changed_fixed_core")
        elif center.get("fixed_zd") != center["zd"] or center.get("fixed_zg") != center["zg"]:
            errors.append(prefix + "changed_fixed_core")
        if any(identifier not in pen_ids for identifier in center.get("source_pen_ids", [])):
            errors.append(prefix + "missing_pen")
        if not center.get("formed_at"):
            errors.append(prefix + "missing_formation_time")
        if center.get("fluctuation_dd") != center["dd"] or center.get("fluctuation_gg") != center["gg"]:
            errors.append(prefix + "inconsistent_z_envelope")
        if proof:
            parts = proof.get("segments", [])
            if len(parts) != 3:
                errors.append(prefix + "decomposition_count")
                continue
            if any(p.get("status") != "confirmed" for p in parts[:2]):
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
            segment_ids = [part.get("id") for part in parts]
            expected_kind = "segment_proof"
            if center.get("unit_kind") != expected_kind or center.get("core_unit_ids") != segment_ids:
                errors.append(prefix + "decomposition_core_units")
            if center.get("child_segment_ids") != segment_ids:
                errors.append(prefix + "decomposition_child_segments")
            if center.get("owned_unit_ids") != segment_ids:
                errors.append(prefix + "decomposition_owned_units")
            for unit_id in ids:
                key = (int(center["level"]), unit_id)
                if key in parent_unit_owner and parent_unit_owner[key] != center["family_id"] and center.get("active"):
                    errors.append(prefix + f"shared_parent_unit:{unit_id}")
                if center.get("active"):
                    parent_unit_owner[key] = center["family_id"]
            for part in parts:
                source_kind = part.get("source_kind")
                if source_kind != "local_pen_group" or center["level"] != 2 or not all(
                    unit_by_id.get(identifier, {}).get("kind") == "pen"
                    for identifier in part.get("source_unit_ids", [])
                ):
                    errors.append(prefix + "decomposition_source_kind")
                if part.get("status") == "confirmed" and not part.get("completion_evidence_id"):
                    errors.append(prefix + "decomposition_completion")
                if not part.get("level_evidence_ids"):
                    errors.append(prefix + "decomposition_level")
                for identifier in part.get("level_evidence_ids", []):
                    witness = center_by_id.get(identifier)
                    local_witness = next(
                        (w for w in part.get("center_witnesses", []) if w.get("id") == identifier), None,
                    )
                    if witness:
                        if witness["level"] != center["level"] - 1 or witness.get("revision_at", witness.get("formed_at", "")) > part["available_at"] or not set(witness.get("owned_unit_ids", [])) <= set(part["source_unit_ids"]):
                            errors.append(prefix + "decomposition_level")
                    elif local_witness:
                        witness_units = [unit_by_id.get(identifier) for identifier in local_witness.get("source_unit_ids", [])]
                        if len(witness_units) != 3 or any(unit is None for unit in witness_units) or _strict_overlap(witness_units) != (local_witness.get("zd"), local_witness.get("zg")) or not _alternating(witness_units):
                            errors.append(prefix + "decomposition_level")
                    else:
                        errors.append(prefix + "decomposition_level")
                selected = [unit_by_id[i] for i in part["source_unit_ids"] if i in unit_by_id]
                if len(selected) != len(part["source_unit_ids"]) or not _span_contiguous(selected):
                    errors.append(prefix + "decomposition_continuity")
                elif (part["low"], part["high"]) != (min(_low(u) for u in selected), max(_high(u) for u in selected)):
                    errors.append(prefix + "decomposition_range")
            if center.get("boundary_status") == "dynamic" and center.get("recursive_eligible"):
                errors.append(prefix + "dynamic_recursion")
            if center.get("revision_at", "") < proof.get("evidence_available_at", proof.get("available_at", "")):
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
        expected_kind = "pen"
        if center.get("unit_kind") != expected_kind or any(unit["kind"] != expected_kind or unit["level"] != center["level"] - 1 for unit in owned):
            errors.append(prefix + "invalid_unit_kind")
        if center.get("source_pen_ids") != _source_pen_ids(owned):
            errors.append(prefix + "source_pen_ownership")
        context = center.get("direction_context") or {}
        if center.get("formation_stage") == "directional":
            expected = "down" if context.get("process_direction") == "up" else "up"
            if not context or center.get("z_direction") != expected:
                errors.append(prefix + "direction_context_mismatch")
        if center["formed_at"] != max([unit.get("confirmed_at") or unit["end_date"] for unit in [*entry, *core]] + [context.get("available_at", "")]):
            errors.append(prefix + "formation_time")

    active_candidate_families: set[str] = set()
    for candidate in candidates:
        prefix = f"promotion-candidate:{candidate.get('id', 'unknown')}:"
        if candidate.get("active"):
            if candidate["family_id"] in active_candidate_families:
                errors.append(prefix + "multiple_active_revisions")
            active_candidate_families.add(candidate["family_id"])
        required = candidate.get("required_unit_ids", [])
        search = candidate.get("search_unit_ids", [])
        if candidate.get("candidate_source") == "extension_decomposition" and len(required) != 9:
            errors.append(prefix + "required_prefix_count")
        prefix_count = min(len(required), len(search))
        if not required or search[:prefix_count] != required[:prefix_count]:
            errors.append(prefix + "required_prefix")
        if any(identifier not in unit_by_id for identifier in search):
            errors.append(prefix + "missing_unit")
        else:
            selected = [unit_by_id[identifier] for identifier in search]
            if not _span_contiguous(selected):
                errors.append(prefix + "discontinuous_search_span")
            if any((unit.get("confirmed_at") or unit["end_date"]) > candidate.get("observed_at", "") for unit in selected):
                errors.append(prefix + "future_unit")
        missing = candidate.get("missing_evidence", [])
        if any(not isinstance(item, dict) or not item.get("code") for item in missing):
            errors.append(prefix + "invalid_missing_evidence")
        if candidate.get("status") == "unresolved" and not missing:
            errors.append(prefix + "unresolved_without_reason")
        if candidate.get("status") in {"dynamic", "fixed"} and not candidate.get("proof_ids"):
            errors.append(prefix + "resolved_without_proof")
        parent_family_id = candidate.get("selected_parent_family_id")
        if parent_family_id and parent_family_id not in family_ids:
            errors.append(prefix + "missing_parent_family")
    owner_by_level_unit: dict[tuple[int, str], str] = {}
    active_center_rows = [item for item in centers if item.get("active", True)]
    for center in active_center_rows:
        for unit_id in center.get("owned_unit_ids", []):
            key = (int(center.get("level", 0)), unit_id)
            owner = center.get("family_id", center.get("id"))
            previous = owner_by_level_unit.get(key)
            if previous and previous != owner:
                errors.append(f"center:{center['id']}:shared_owned_unit:{unit_id}")
            owner_by_level_unit[key] = owner
    candidate_ids: set[str] = set()
    for candidate in center_candidates:
        prefix = f"center-candidate:{candidate.get('id', 'unknown')}:"
        if candidate.get("id") in candidate_ids:
            errors.append(prefix + "duplicate_id")
        candidate_ids.add(candidate.get("id"))
        source_ids = candidate.get("source_unit_ids", [])
        if len(source_ids) < 3 or len(source_ids) != len(set(source_ids)):
            errors.append(prefix + "source_count")
        if any(identifier not in unit_by_id for identifier in source_ids):
            errors.append(prefix + "missing_unit")
        if candidate.get("status") == "selected" and not candidate.get("selected_center_family_id"):
            errors.append(prefix + "selected_without_center")
        if candidate.get("status") == "rejected" and not candidate.get("rejection_code"):
            errors.append(prefix + "rejected_without_code")
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


def assert_valid_structure(payload: dict[str, Any], *, include_provisional: bool = False) -> None:
    errors = validate_structure(payload, include_provisional=include_provisional)
    if errors:
        raise ValueError("结构校验失败: " + "; ".join(errors[:30]))


def structure_version(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:24]
