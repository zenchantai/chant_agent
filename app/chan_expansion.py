"""Canonical movement-segment proofs for parent-center construction."""
from __future__ import annotations

import hashlib
from copy import deepcopy
from typing import Any


EPSILON = 1e-9


def _stable_id(prefix: str, *values: Any) -> str:
    raw = "|".join(str(value) for value in values)
    return f"{prefix}-{hashlib.sha256(raw.encode()).hexdigest()[:14]}"


def _center_revisions(centers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for center in centers:
        for revision in center.get("_history", [center]):
            unique[revision["id"]] = revision
    return list(unique.values())


def _movement_available(movement: dict[str, Any], observed_at: str) -> bool:
    if movement.get("status") == "confirmed":
        return bool(movement.get("confirmed_at") and movement["confirmed_at"] <= observed_at)
    return movement.get("status") == "provisional"


def _unit_direction(unit: dict[str, Any]) -> str:
    direction = unit.get("direction")
    if direction in {"up", "down"}:
        return direction
    return "up" if float(unit["end_price"]) > float(unit["start_price"]) else "down"


def _unit_low(unit: dict[str, Any]) -> float:
    return float(unit.get("low", min(float(unit["start_price"]), float(unit["end_price"]))))


def _unit_high(unit: dict[str, Any]) -> float:
    return float(unit.get("high", max(float(unit["start_price"]), float(unit["end_price"]))))


def _units_contiguous(units: list[dict[str, Any]]) -> bool:
    return all(
        left.get("continuous_range_id", 0) == right.get("continuous_range_id", 0)
        and left.get("sequence_id", 0) == right.get("sequence_id", 0)
        and left.get("structure_sequence_id", "") == right.get("structure_sequence_id", "")
        and left["end_date"] == right["start_date"]
        and abs(float(left["end_price"]) - float(right["start_price"])) <= EPSILON
        for left, right in zip(units, units[1:])
    )


def _alternating(units: list[dict[str, Any]]) -> bool:
    return all(_unit_direction(left) != _unit_direction(right) for left, right in zip(units, units[1:]))


def _strict_overlap(units: list[dict[str, Any]]) -> tuple[float, float] | None:
    if not units:
        return None
    low = max(_unit_low(unit) for unit in units)
    high = min(_unit_high(unit) for unit in units)
    return (low, high) if low + EPSILON < high else None


def _local_core_witness(
    part: list[dict[str, Any]], child_level: int, available_at: str,
) -> dict[str, Any] | None:
    """Prove the local child-level center without requiring a global movement row."""
    for offset in range(len(part) - 2):
        core = part[offset:offset + 3]
        overlap = _strict_overlap(core)
        if not overlap or not _alternating(core):
            continue
        if any(unit.get("confirmed_at", unit["end_date"]) > available_at for unit in core):
            continue
        return {
            "id": _stable_id(
                "local-center-witness", child_level,
                core[0]["id"], core[-1]["id"],
            ),
            "family_id": _stable_id("local-center-family", child_level, core[0]["id"], core[-1]["id"]),
            "level": child_level,
            "source_unit_ids": [unit["id"] for unit in core],
            "zd": overlap[0],
            "zg": overlap[1],
            "available_at": max(unit.get("confirmed_at", unit["end_date"]) for unit in core),
            "formation_stage": "directional",
        }
    return None


def _local_segment_proof(
    part: list[dict[str, Any]], child_level: int, observed_at: str,
    *, confirmed: bool, completion_evidence_id: str | None = None,
    completion_available_at: str | None = None, open_third: bool = False,
) -> dict[str, Any] | None:
    if len(part) < 3 or not _units_contiguous(part):
        return None
    if any(unit.get("confirmed_at", unit["end_date"]) > observed_at for unit in part):
        return None
    direction = "up" if float(part[-1]["end_price"]) > float(part[0]["start_price"]) + EPSILON else (
        "down" if float(part[-1]["end_price"]) < float(part[0]["start_price"]) - EPSILON else None
    )
    if direction is None:
        return None
    available_at = max(unit.get("confirmed_at", unit["end_date"]) for unit in part)
    witness = _local_core_witness(part, child_level, available_at)
    if witness is None:
        return None
    if confirmed and completion_available_at:
        available_at = max(available_at, completion_available_at)
    source_ids = [unit["id"] for unit in part]
    family_id = _stable_id(
        "segment-family", child_level, "local_pen_group", source_ids[0], direction,
    )
    status = "confirmed" if confirmed else "provisional"
    revision_no = (
        len(source_ids) + 1 if open_third and confirmed
        else len(source_ids) - 2
    )
    return {
        "id": f"{family_id}:r{revision_no}",
        "family_id": family_id,
        "revision_no": revision_no,
        "previous_revision_id": f"{family_id}:r{revision_no - 1}" if revision_no > 1 else None,
        "active": True,
        "source_kind": "local_pen_group",
        "level": child_level,
        "status": status,
        "direction": direction,
        "start_date": part[0]["start_date"],
        "end_date": part[-1]["end_date"],
        "start_price": float(part[0]["start_price"]),
        "end_price": float(part[-1]["end_price"]),
        "low": min(_unit_low(unit) for unit in part),
        "high": max(_unit_high(unit) for unit in part),
        "source_unit_ids": source_ids,
        "source_pen_ids": list(dict.fromkeys(
            pen_id for unit in part for pen_id in unit.get("source_pen_ids", [unit["id"]])
        )),
        "center_witnesses": [witness],
        "level_evidence_ids": [witness["id"]],
        "boundary_mode": "pen_group" if not completion_evidence_id else "pen_group_reversal",
        "completion_evidence_id": completion_evidence_id,
        "observed_at": observed_at,
        "evidence_available_at": available_at,
        "available_at": available_at,
        "continuous_range_id": int(part[0].get("continuous_range_id", 0)),
        "sequence_id": int(part[0].get("sequence_id", 0)),
        "structure_sequence_id": str(part[0].get("structure_sequence_id", "")),
        "recursive_eligible": False,
    }


def _find_local_completion(
    units: list[dict[str, Any]], third_end: int, direction: str, observed_at: str,
    child_level: int,
) -> tuple[str, str] | None:
    if third_end + 3 > len(units):
        return None
    part = units[third_end:third_end + 3]
    if not _units_contiguous(part) or any(
        unit.get("confirmed_at", unit["end_date"]) > observed_at for unit in part
    ):
        return None
    segment_direction = "up" if float(part[-1]["end_price"]) > float(part[0]["start_price"]) + EPSILON else (
        "down" if float(part[-1]["end_price"]) < float(part[0]["start_price"]) - EPSILON else None
    )
    if segment_direction != ("down" if direction == "up" else "up"):
        return None
    witness = _local_core_witness(part, child_level, observed_at)
    if witness is None:
        return None
    available_at = max(unit.get("confirmed_at", unit["end_date"]) for unit in part)
    return _stable_id("local-boundary", child_level, units[third_end - 1]["id"], part[-1]["id"]), available_at


def _point_completion(
    boundary_events: list[dict[str, Any]], unit: dict[str, Any],
    observed_at: str, child_level: int,
) -> tuple[str, str] | None:
    eligible = sorted(
        (
            event for event in boundary_events
            if int(event.get("level", 0)) == child_level
            and event.get("status") == "confirmed"
            and event.get("source_unit_id") == unit["id"]
            and event.get("point_date") == unit["end_date"]
            and event.get("confirmed_at")
            and event["confirmed_at"] <= observed_at
        ),
        key=lambda event: (event["confirmed_at"], event["id"]),
    )
    if not eligible:
        return None
    event = eligible[0]
    return event["id"], event["confirmed_at"]


def _local_segment_proofs(
    *, child_level: int, units: list[dict[str, Any]], required_unit_ids: set[str],
    search_unit_ids: list[str], observed_at: str,
    boundary_events: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if child_level != 1 or not required_unit_ids or len(search_unit_ids) < 9:
        return []
    by_id = {unit["id"]: unit for unit in units}
    if any(identifier not in by_id for identifier in search_unit_ids):
        return []
    search = [by_id[identifier] for identifier in search_unit_ids]
    if not _units_contiguous(search) or any(
        unit.get("confirmed_at", unit["end_date"]) > observed_at for unit in search
    ) or not required_unit_ids <= set(search_unit_ids):
        return []
    # The frozen prefix is the owned-unit prefix. An entry unit may not be
    # pulled in later to make a nine-unit proof look complete.
    if set(search_unit_ids[:len(required_unit_ids)]) != required_unit_ids:
        return []
    proofs: list[dict[str, Any]] = []
    boundary_events = boundary_events or []
    required_len = len(required_unit_ids)
    # The frozen prefix determines the first two boundaries. Permit a small
    # look-ahead for an extended local segment, while avoiding an O(n^3)
    # repartition over the whole historical center tail.
    first_limit = min(len(search) - 5, required_len + 6)
    for first_end in range(3, first_limit + 1):
        second_limit = min(len(search) - 2, required_len + 9)
        for second_end in range(first_end + 3, second_limit + 1):
            # A fixed S3 ends before the earliest valid reverse S4. If no
            # reverse segment is available, only the current search tail may
            # be used as the open dynamic S3.
            fixed_end: int | None = None
            for probe_end in range(second_end + 3, len(search) + 1):
                probe_parts = [search[:first_end], search[first_end:second_end], search[second_end:probe_end]]
                if any(len(part) < 3 for part in probe_parts):
                    continue
                probe_direction = "up" if float(probe_parts[2][-1]["end_price"]) > float(probe_parts[2][0]["start_price"]) + EPSILON else (
                    "down" if float(probe_parts[2][-1]["end_price"]) < float(probe_parts[2][0]["start_price"]) - EPSILON else None
                )
                if probe_direction is None:
                    continue
                first_direction = "up" if float(probe_parts[0][-1]["end_price"]) > float(probe_parts[0][0]["start_price"]) + EPSILON else "down" if float(probe_parts[0][-1]["end_price"]) < float(probe_parts[0][0]["start_price"]) - EPSILON else None
                second_direction = "up" if float(probe_parts[1][-1]["end_price"]) > float(probe_parts[1][0]["start_price"]) + EPSILON else "down" if float(probe_parts[1][-1]["end_price"]) < float(probe_parts[1][0]["start_price"]) - EPSILON else None
                if first_direction is None or first_direction != probe_direction or first_direction == second_direction:
                    continue
                if (
                    _find_local_completion(
                        search, probe_end, probe_direction, observed_at, child_level,
                    )
                    or _point_completion(
                        boundary_events, search[probe_end - 1], observed_at, child_level,
                    )
                ):
                    fixed_end = probe_end
                    break
            third_ends = [fixed_end] if fixed_end is not None else [len(search)]
            for third_end in third_ends:
                parts = [search[:first_end], search[first_end:second_end], search[second_end:third_end]]
                if any(len(part) < 3 for part in parts):
                    continue
                if not required_unit_ids <= {unit["id"] for part in parts for unit in part}:
                    continue
                directions = [
                    "up" if float(part[-1]["end_price"]) > float(part[0]["start_price"]) + EPSILON else (
                        "down" if float(part[-1]["end_price"]) < float(part[0]["start_price"]) - EPSILON else None
                    ) for part in parts
                ]
                if directions[0] is None or directions[0] != directions[2] or directions[0] == directions[1]:
                    continue
                completion = (
                    _find_local_completion(
                        search, third_end, directions[2], observed_at, child_level,
                    )
                    or _point_completion(
                        boundary_events, search[third_end - 1], observed_at, child_level,
                    )
                )
                if third_end < len(search) and completion is None:
                    continue
                segments = []
                valid = True
                for index, part in enumerate(parts):
                    if index < 2:
                        next_part = parts[index + 1][:3]
                        completion_id = _stable_id(
                            "local-boundary", child_level, part[-1]["id"], next_part[-1]["id"],
                        )
                        completion_at = max(
                            unit.get("confirmed_at", unit["end_date"])
                            for unit in next_part
                        )
                    else:
                        completion_id = completion[0] if completion else None
                        completion_at = completion[1] if completion else None
                    segment = _local_segment_proof(
                        part, child_level, observed_at,
                        confirmed=index < 2 or completion is not None,
                        completion_evidence_id=completion_id,
                        completion_available_at=completion_at,
                        open_third=index == 2,
                    )
                    if segment is None:
                        valid = False
                        break
                    segments.append(segment)
                if not valid:
                    continue
                zd = max(segment["low"] for segment in segments)
                zg = min(segment["high"] for segment in segments)
                if zd + EPSILON >= zg:
                    continue
                all_ids = [unit["id"] for part in parts for unit in part]
                boundary_status = "fixed" if segments[-1]["status"] == "confirmed" else "dynamic"
                available_at = max(segment["evidence_available_at"] for segment in segments)
                proof_available_at = available_at if boundary_status == "fixed" else max(available_at, observed_at)
                proofs.append({
                    "id": _stable_id(
                        "parent-proof", child_level + 1, "local_pen_group", boundary_status,
                        *(segment["id"] for segment in segments),
                    ),
                    "candidate_source": "local_pen_group",
                    "child_level": child_level,
                    "parent_level": child_level + 1,
                    "segments": deepcopy(segments),
                    "source_unit_ids": all_ids,
                    "source_pen_ids": list(dict.fromkeys(
                        pen_id for segment in segments for pen_id in segment["source_pen_ids"]
                    )),
                    "required_unit_ids": [identifier for identifier in search_unit_ids if identifier in required_unit_ids],
                    "zd": zd,
                    "zg": zg,
                    "boundary_status": boundary_status,
                    "observed_at": observed_at,
                    "evidence_available_at": proof_available_at,
                    "available_at": proof_available_at,
                    "source_kind": "local_pen_group",
                })
    proofs.sort(key=lambda proof: (
        proof["evidence_available_at"],
        len(proof["segments"][0]["source_unit_ids"]),
        len(proof["segments"][1]["source_unit_ids"]),
        len(proof["source_unit_ids"]),
        proof["id"],
    ))
    if not proofs:
        return []
    selected_id = proofs[0]["id"]
    for proof in proofs:
        proof["selection_status"] = "selected" if proof["id"] == selected_id else "rejected"
        if proof["selection_status"] == "rejected":
            proof["rejection_code"] = "noncanonical_segment_partition"
    return proofs


def segment_missing_evidence(
    *, units: list[dict[str, Any]], required_unit_ids: list[str], search_unit_ids: list[str],
) -> list[dict[str, Any]]:
    """Return stable, stage-specific diagnostics for a failed local proof."""
    if len(search_unit_ids) < 9:
        return [{"code": "nine_owned_units", "actual_count": len(search_unit_ids)}]
    by_id = {unit["id"]: unit for unit in units}
    search = [by_id.get(identifier) for identifier in search_unit_ids]
    if any(unit is None for unit in search):
        return [{"code": "segment_continuity_failed"}]
    if len(set(search_unit_ids)) != len(search_unit_ids):
        return [{"code": "segment_ownership_conflict"}]
    if set(search_unit_ids[:len(required_unit_ids)]) != set(required_unit_ids):
        return [{"code": "segment_ownership_conflict"}]
    if not _units_contiguous(search):
        return [{"code": "segment_continuity_failed"}]
    parts = [search[:3], search[3:6], search[6:9]]
    directions = [
        "up" if float(part[-1]["end_price"]) > float(part[0]["start_price"]) + EPSILON else
        "down" if float(part[-1]["end_price"]) < float(part[0]["start_price"]) - EPSILON else None
        for part in parts
    ]
    if any(direction is None for direction in directions) or directions[0] != directions[2] or directions[0] == directions[1]:
        return [{"code": "segment_direction_mismatch"}]
    if any(_local_core_witness(part, 1, max(unit.get("confirmed_at", unit["end_date"]) for unit in part)) is None for part in parts):
        return [{"code": "segment_child_core_missing"}]
    segment_bounds = [(min(_unit_low(unit) for unit in part), max(_unit_high(unit) for unit in part)) for part in parts]
    if max(bound[0] for bound in segment_bounds) + EPSILON >= min(bound[1] for bound in segment_bounds):
        return [{"code": "segment_common_overlap_empty"}]
    return [{"code": "three_segment_proofs_missing"}]


def _level_evidence(
    revisions: list[dict[str, Any]], child_level: int,
    source_unit_ids: set[str], available_at: str,
) -> list[dict[str, Any]]:
    eligible = sorted(
        (
            revision for revision in revisions
            if int(revision.get("level", 0)) == child_level
            and revision.get("formation_stage") == "directional"
            and revision.get("revision_at", revision.get("formed_at", "")) <= available_at
            and set(revision.get("owned_unit_ids", [])) <= source_unit_ids
        ),
        key=lambda item: (item.get("revision_at", item.get("formed_at", "")), item["id"]),
    )
    by_family: dict[str, dict[str, Any]] = {}
    for revision in eligible:
        by_family.setdefault(revision.get("family_id", revision["id"]), revision)
    return list(by_family.values())


def _segment_proof(
    movement: dict[str, Any], part: list[dict[str, Any]], child_level: int,
    revisions: list[dict[str, Any]], *, confirmed: bool,
) -> dict[str, Any] | None:
    available_at = max(
        [unit["confirmed_at"] for unit in part]
        + ([movement["confirmed_at"]] if confirmed else [])
    )
    evidence = _level_evidence(
        revisions, child_level, {unit["id"] for unit in part}, available_at,
    )
    if not evidence:
        return None
    movement_family_id = movement.get("family_id", movement["id"])
    source_unit_ids = [unit["id"] for unit in part]
    status = "confirmed" if confirmed else "provisional"
    family_id = _stable_id(
        "segment-family", child_level, movement_family_id,
        source_unit_ids[0], source_unit_ids[-1],
    )
    revision_no = 2 if confirmed else 1
    return {
        "id": f"{family_id}:r{revision_no}",
        "family_id": family_id,
        "revision_no": revision_no,
        "previous_revision_id": f"{family_id}:r1" if revision_no == 2 else None,
        "active": True,
        "source_kind": "movement",
        "movement_revision_id": movement["id"],
        "movement_family_id": movement_family_id,
        "level": child_level,
        "direction": movement["direction"],
        "status": status,
        "start_date": part[0]["start_date"],
        "end_date": part[-1]["end_date"],
        "start_price": part[0]["start_price"],
        "end_price": part[-1]["end_price"],
        "source_unit_ids": source_unit_ids,
        "source_pen_ids": list(dict.fromkeys(
            pen_id for unit in part
            for pen_id in unit.get("source_pen_ids", [unit["id"]])
        )),
        "low": min(unit["low"] for unit in part),
        "high": max(unit["high"] for unit in part),
        "level_evidence_ids": [item["id"] for item in evidence],
        "completion_evidence_id": movement.get("end_point_id") if confirmed else None,
        "available_at": available_at,
        "continuous_range_id": int(part[0].get("continuous_range_id", 0)),
        "sequence_id": int(part[0].get("sequence_id", 0)),
        "structure_sequence_id": str(part[0].get("structure_sequence_id", "")),
    }


def build_parent_center_proofs(
    *,
    child_level: int,
    units: list[dict[str, Any]],
    child_centers: list[dict[str, Any]],
    boundary_events: list[dict[str, Any]],
    movements: list[dict[str, Any]],
    required_unit_ids: set[str],
    search_unit_ids: list[str],
    candidate_source: str,
    observed_at: str,
    include_rejected: bool = False,
    allow_local: bool = True,
) -> list[dict[str, Any]]:
    """Build one canonical parent-proof stream.

    L1 uses pen-native local segment proofs; higher levels adapt confirmed
    movement revisions. Both paths share the same continuity, direction,
    ownership, overlap, and as-of checks.
    """
    if not required_unit_ids or len(search_unit_ids) < 9:
        return []
    by_id = {unit["id"]: unit for unit in units}
    if any(identifier not in by_id for identifier in search_unit_ids):
        return []
    search_set = set(search_unit_ids)
    if not required_unit_ids <= search_set:
        return []
    search_units = [by_id[identifier] for identifier in search_unit_ids]
    if any(
        left["end_date"] != right["start_date"]
        or abs(float(left["end_price"]) - float(right["start_price"])) > EPSILON
        for left, right in zip(search_units, search_units[1:])
    ):
        return []

    # L1 is intentionally pen-native in this project. A pre-existing global
    # movement is useful evidence, but it is not a prerequisite for the first
    # promotion from nine owned pens.
    if allow_local and child_level == 1 and all(unit.get("kind") == "pen" for unit in search_units):
        local_proofs = _local_segment_proofs(
            child_level=child_level,
            units=units,
            required_unit_ids=required_unit_ids,
            search_unit_ids=search_unit_ids,
            observed_at=observed_at,
            boundary_events=boundary_events,
        )
        if local_proofs:
            for proof in local_proofs:
                proof["candidate_source"] = candidate_source
                for segment in proof["segments"]:
                    segment["candidate_source"] = candidate_source
            if include_rejected:
                return local_proofs
            return [
                {key: value for key, value in proof.items() if key != "selection_status"}
                for proof in local_proofs if proof.get("selection_status") == "selected"
            ]

    confirmed_points = {
        event.get("family_id", event["id"]): event
        for event in boundary_events
        if event.get("status") == "confirmed"
        and event.get("confirmed_at")
        and event["confirmed_at"] <= observed_at
    }
    revisions = _center_revisions(child_centers)
    ordered = sorted(
        (
            movement for movement in movements
            if int(movement.get("level", 0)) == child_level
            and movement.get("classification") in {"trend", "consolidation"}
            and movement.get("status") in {"confirmed", "provisional"}
            and _movement_available(movement, observed_at)
            and movement.get("source_unit_ids")
        ),
        key=lambda item: (item["start_date"], item["end_date"], item["id"]),
    )
    proofs: list[dict[str, Any]] = []
    for offset in range(len(ordered) - 2):
        triple = ordered[offset:offset + 3]
        if any(movement.get("status") != "confirmed" for movement in triple[:2]):
            continue
        if any(
            movement.get("end_point_id") not in confirmed_points
            for movement in triple[:2]
        ):
            continue
        if (
            triple[0]["direction"] != triple[2]["direction"]
            or triple[0]["direction"] == triple[1]["direction"]
        ):
            continue
        if any(
            left["end_date"] != right["start_date"]
            or abs(float(left["end_price"]) - float(right["start_price"])) > EPSILON
            for left, right in zip(triple, triple[1:])
        ):
            continue
        parts: list[list[dict[str, Any]]] = []
        for movement in triple:
            identifiers = movement.get("source_unit_ids", [])
            if any(identifier not in by_id or identifier not in search_set for identifier in identifiers):
                parts = []
                break
            parts.append([by_id[identifier] for identifier in identifiers])
        if len(parts) != 3 or any(len(part) < 3 for part in parts):
            continue
        if parts[0][0]["id"] != search_unit_ids[0]:
            continue
        all_ids = [unit["id"] for part in parts for unit in part]
        if len(all_ids) != len(set(all_ids)):
            continue

        for third_count in range(3, len(parts[2]) + 1):
            selected_parts = [*parts[:2], parts[2][:third_count]]
            source_ids = [unit["id"] for part in selected_parts for unit in part]
            if not required_unit_ids <= set(source_ids):
                continue
            third_confirmed = (
                third_count == len(parts[2])
                and triple[2].get("status") == "confirmed"
                and triple[2].get("end_point_id") in confirmed_points
            )
            segment_proofs: list[dict[str, Any]] = []
            for index, (movement, part) in enumerate(zip(triple, selected_parts)):
                segment = _segment_proof(
                    movement, part, child_level, revisions,
                    confirmed=index < 2 or third_confirmed,
                )
                if segment is None or segment["available_at"] > observed_at:
                    segment_proofs = []
                    break
                segment_proofs.append(segment)
            if len(segment_proofs) != 3:
                continue
            zd = max(segment["low"] for segment in segment_proofs)
            zg = min(segment["high"] for segment in segment_proofs)
            if zd + EPSILON >= zg:
                continue
            boundary_status = "fixed" if third_confirmed else "dynamic"
            available_at = max(segment["available_at"] for segment in segment_proofs)
            proof_id = _stable_id(
                "parent-proof", child_level + 1, candidate_source, boundary_status,
                *(segment["id"] for segment in segment_proofs),
            )
            proofs.append({
                "id": proof_id,
                "candidate_source": candidate_source,
                "child_level": child_level,
                "parent_level": child_level + 1,
                "segments": deepcopy(segment_proofs),
                "source_unit_ids": source_ids,
                "source_pen_ids": list(dict.fromkeys(
                    pen_id for segment in segment_proofs
                    for pen_id in segment["source_pen_ids"]
                )),
                "required_unit_ids": [
                    identifier for identifier in search_unit_ids
                    if identifier in required_unit_ids
                ],
                "zd": zd,
                "zg": zg,
                "boundary_status": boundary_status,
                "observed_at": observed_at,
                "evidence_available_at": available_at,
                "available_at": available_at,
            })

    proofs.sort(key=lambda proof: (
        proof["evidence_available_at"],
        proof["segments"][0]["start_date"],
        proof["segments"][0]["end_date"],
        proof["segments"][1]["end_date"],
        len(proof["source_unit_ids"]),
        proof["id"],
    ))
    if not proofs:
        return []
    selected_families = tuple(
        segment["movement_family_id"] for segment in proofs[0]["segments"]
    )
    unique: dict[tuple[tuple[str, ...], str, tuple[str, ...]], dict[str, Any]] = {}
    for proof in proofs:
        families = tuple(segment["movement_family_id"] for segment in proof["segments"])
        candidate = deepcopy(proof)
        candidate["selection_status"] = "selected" if families == selected_families else "rejected"
        if families != selected_families:
            candidate["rejection_code"] = "noncanonical_movement_family_tuple"
        unique.setdefault((
            families, proof["boundary_status"], tuple(proof["source_unit_ids"]),
        ), candidate)
    values = list(unique.values())
    if include_rejected:
        return values
    return [
        {key: value for key, value in proof.items() if key != "selection_status"}
        for proof in values if proof["selection_status"] == "selected"
    ]


def decomposition_proofs(
    movements: list[dict[str, Any]], units: list[dict[str, Any]],
    required_ids: set[str], child_level: int,
    centers: list[dict[str, Any]] | None = None,
    points: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Compatibility wrapper for callers migrating to the canonical proof API."""
    observed_at = max((unit["confirmed_at"] for unit in units), default="")
    return build_parent_center_proofs(
        child_level=child_level,
        units=units,
        child_centers=centers or [],
        boundary_events=points or [],
        movements=movements,
        required_unit_ids=required_ids,
        search_unit_ids=[unit["id"] for unit in units],
        candidate_source="expansion_decomposition",
        observed_at=observed_at,
        allow_local=False,
    )
