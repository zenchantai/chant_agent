from __future__ import annotations

from typing import Any

from .hierarchy import (
    EPSILON, _bounds, _center_owned_unit_ids, _directional_seed,
    _group_key, _latest_timestamp, _movement_classification, _source_pen_ids,
    _stream_key, _units_are_contiguous, atomic_pen_units, movement_confirmation_errors,
)


def structure_ownership_errors(payload: dict[str, Any]) -> list[str]:
    pens = payload.get("pens", [])
    centers = payload.get("centers", payload.get("pen_centers", []))
    movements = payload.get("movements", [])
    units = [*atomic_pen_units(pens), *movements]
    unit_by_id = {unit["id"]: unit for unit in units}
    center_by_id = {center["id"]: center for center in centers}
    movement_by_id = {movement["id"]: movement for movement in movements}
    errors: list[str] = []
    for name, items in (("units", units), ("centers", centers)):
        if len(items) != len({item["id"] for item in items}):
            errors.append(f"{name}: duplicate IDs")
    center_owners: dict[tuple[int, str], str] = {}
    movement_owners: dict[tuple[int, str], str] = {}

    def check_source(item, identifiers, owner):
        if not identifiers or len(identifiers) != len(set(identifiers)):
            errors.append(f"{owner}: empty or duplicated source units")
            return []
        if any(identifier not in unit_by_id for identifier in identifiers):
            errors.append(f"{owner}: missing source unit")
            return []
        selected = [unit_by_id[identifier] for identifier in identifiers]
        if len({_stream_key(unit) for unit in selected}) != 1:
            errors.append(f"{owner}: source crosses structural sequence")
        if any(not _units_are_contiguous(left, right) for left, right in zip(selected, selected[1:])):
            errors.append(f"{owner}: source is not continuous and alternating")
        if any(int(unit.get("level", 0)) != int(item["level"]) - 1 for unit in selected):
            errors.append(f"{owner}: source level mismatch")
        if any(unit.get("status") != "confirmed" for unit in selected):
            errors.append(f"{owner}: unfinished source unit")
        if int(item["level"]) > 1 and any(unit.get("recursive_eligible") is not True for unit in selected):
            errors.append(f"{owner}: ineligible recursive input")
        if set(item.get("source_pen_ids", [])) != set(_source_pen_ids(selected)):
            errors.append(f"{owner}: source pen evidence mismatch")
        if _group_key(item) != _group_key(selected[0]):
            errors.append(f"{owner}: data range or sequence mismatch")
        return selected

    for center in centers:
        owner = f"center:{center['id']}"
        try:
            selected = check_source(center, center.get("source_unit_ids", []), owner)
            if not selected:
                continue
            confirmed = center.get("status") == "confirmed"
            count = 3 if confirmed else 2
            if center.get("status") not in {"confirmed", "provisional"}:
                errors.append(f"{owner}: invalid status")
            formation = [center.get("entry_unit_id"), *center.get("core_unit_ids", [])]
            if len(formation) != count + 1 or formation != [unit["id"] for unit in selected[:count + 1]]:
                errors.append(f"{owner}: directional formation requires entry and {count} core units")
            seed = _directional_seed(selected, 0, count)
            if seed is None:
                errors.append(f"{owner}: invalid directional seed")
            elif (center.get("direction") != seed["direction"] or any(
                abs(float(center[field]) - seed[key]) > EPSILON
                for field, key in (("zd", "zd"), ("zg", "zg"), ("fixed_zd", "zd"), ("fixed_zg", "zg"))
            )):
                errors.append(f"{owner}: fixed core or entry direction mismatch")
            expected_roles = [*formation, *center.get("extension_unit_ids", []), *center.get("peripheral_unit_ids", [])]
            if len(expected_roles) != len(set(expected_roles)) or set(expected_roles) != {unit["id"] for unit in selected}:
                errors.append(f"{owner}: ambiguous unit role ownership")
            if center.get("start_date") != selected[1]["start_date"] or center.get("end_date") != selected[-1]["end_date"]:
                errors.append(f"{owner}: rectangle must exclude entry and include owned tail")
            if abs(float(center["dd"]) - min(_bounds(unit)[0] for unit in selected)) > EPSILON or abs(float(center["gg"]) - max(_bounds(unit)[1] for unit in selected)) > EPSILON:
                errors.append(f"{owner}: envelope does not cover owned units")
            if confirmed:
                if center.get("confirmed_at") != _latest_timestamp(*(unit.get("confirmed_at") for unit in selected[:4]), *(unit["end_date"] for unit in selected[:4])):
                    errors.append(f"{owner}: formation confirmation time mismatch")
                if _center_owned_unit_ids(center) != center.get("source_unit_ids"):
                    errors.append(f"{owner}: owned and source units differ")
                movement = movement_by_id.get(center.get("owner_movement_id"))
                if not movement or center["id"] not in movement.get("center_ids", []) or not set(center["owned_unit_ids"]) <= set(movement.get("source_unit_ids", [])):
                    errors.append(f"{owner}: missing complete movement owner")
                for identifier in center["owned_unit_ids"]:
                    key = (int(center["level"]), identifier)
                    if key in center_owners:
                        errors.append(f"{owner}: shares unit with {center_owners[key]}")
                    center_owners[key] = center["id"]
            elif center.get("confirmed_at") or center.get("owner_movement_id") or center.get("owned_unit_ids"):
                errors.append(f"{owner}: candidate contains completion or ownership evidence")
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"{owner}: malformed structure: {exc}")

    for movement in movements:
        owner = f"movement:{movement['id']}"
        try:
            errors.extend(f"{owner}: {error}" for error in movement_confirmation_errors(movement, center_by_id))
            selected = check_source(movement, movement.get("source_unit_ids", []), owner)
            if not selected:
                continue
            for identifier in movement["source_unit_ids"]:
                key = (int(movement["level"]), identifier)
                if key in movement_owners:
                    errors.append(f"{owner}: shares source unit with {movement_owners[key]}")
                movement_owners[key] = movement["id"]
            for side, unit in (("start", selected[0]), ("end", selected[-1])):
                if movement[f"{side}_date"] != unit[f"{side}_date"] or abs(float(movement[f"{side}_price"]) - float(unit[f"{side}_price"])) > EPSILON:
                    errors.append(f"{owner}: boundary cuts a source unit")
            if movement.get("boundary_source_unit_id") != selected[-1]["id"]:
                errors.append(f"{owner}: incorrect boundary unit")
            if abs(float(movement["low"]) - min(_bounds(unit)[0] for unit in selected)) > EPSILON or abs(float(movement["high"]) - max(_bounds(unit)[1] for unit in selected)) > EPSILON:
                errors.append(f"{owner}: incorrect full-source envelope")
            if movement.get("state") == "undetermined" and (movement.get("classification") is not None or movement.get("recursive_eligible")):
                errors.append(f"{owner}: undetermined structure enters recursion")
            if movement.get("status") == "confirmed":
                owned = [center_by_id[identifier] for identifier in movement["center_ids"]]
                if movement.get("classification") != _movement_classification(owned, movement["direction"]):
                    errors.append(f"{owner}: classification inconsistent with owned centers")
                confirmation = center_by_id[movement["confirmation_center_id"]]
                entry = unit_by_id[confirmation["entry_unit_id"]]
                if movement["end_date"] > entry["start_date"]:
                    errors.append(f"{owner}: boundary cuts confirmation center")
                required_at = _latest_timestamp(confirmation.get("confirmed_at"), *(unit.get("confirmed_at") for unit in selected))
                if str(movement.get("confirmed_at") or "") < str(required_at or ""):
                    errors.append(f"{owner}: confirmation precedes necessary evidence")
                if movement.get("recursive_eligible") is not True:
                    errors.append(f"{owner}: completed movement lost recursion qualification")
            elif movement.get("termination_reason") not in {"provisional_tail", "sequence_boundary", "data_boundary"}:
                errors.append(f"{owner}: truncated structure pretends completed")
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"{owner}: malformed structure: {exc}")
    return errors


def assert_valid_structure(payload: dict[str, Any]) -> None:
    errors = structure_ownership_errors(payload)
    if errors:
        raise ValueError("结构归属审计失败: " + "; ".join(errors))
