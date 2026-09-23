"""Pen-native segment proofs for L1-to-L2 center promotion."""
from __future__ import annotations

import hashlib
from copy import deepcopy
from typing import Any


EPSILON = 1e-9


def _stable_id(prefix: str, *values: Any) -> str:
    raw = "|".join(str(value) for value in values)
    return f"{prefix}-{hashlib.sha256(raw.encode()).hexdigest()[:14]}"


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
    """Prove a child-level center from confirmed pens."""
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


def _local_segment_proofs(
    *, child_level: int, units: list[dict[str, Any]], required_unit_ids: set[str],
    search_unit_ids: list[str], observed_at: str,
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


def build_parent_center_proofs(
    *,
    child_level: int,
    units: list[dict[str, Any]],
    required_unit_ids: set[str],
    search_unit_ids: list[str],
    candidate_source: str,
    observed_at: str,
    include_rejected: bool = False,
) -> list[dict[str, Any]]:
    """Build L2 parent proofs solely from contiguous L1 pens."""
    if child_level != 1:
        return []
    unit_by_id = {unit["id"]: unit for unit in units}
    if len(search_unit_ids) != len(set(search_unit_ids)) or any(
        unit_by_id.get(identifier, {}).get("kind") != "pen" for identifier in search_unit_ids
    ):
        return []
    local_proofs = _local_segment_proofs(
        child_level=child_level,
        units=units,
        required_unit_ids=required_unit_ids,
        search_unit_ids=search_unit_ids,
        observed_at=observed_at,
    )
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
