from __future__ import annotations

from typing import Any

from .hierarchy import (
    EPSILON, _alternating, _bounds, _center_owned_unit_ids, _direction, _directional_seed,
    _group_key, _latest_timestamp, _movement_classification, _source_pen_ids,
    _stream_key, _strict_overlap, _units_are_contiguous, atomic_pen_units, movement_confirmation_errors,
)


def structure_ownership_errors(payload: dict[str, Any]) -> list[str]:
    pens = payload.get("pens", [])
    centers = payload.get("centers", payload.get("pen_centers", []))
    movements = payload.get("movements", [])
    components = payload.get("components", [])
    buy_sell_points = payload.get("buy_sell_points", [])
    units = [*atomic_pen_units(pens), *movements]
    unit_by_id = {unit["id"]: unit for unit in units}
    center_by_id = {center["id"]: center for center in centers}
    movement_by_id = {movement["id"]: movement for movement in movements}
    component_by_id = {component["id"]: component for component in components}
    point_by_id = {point["id"]: point for point in buy_sell_points}
    errors: list[str] = []
    for name, items in (("units", units), ("centers", centers), ("components", components), ("buy_sell_points", buy_sell_points)):
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

    for component in components:
        owner = f"component:{component['id']}"
        try:
            selected = check_source(component, component.get("source_unit_ids", []), owner)
            if not selected:
                continue
            if component.get("kind") != "center_free_component" or component.get("status") != "confirmed":
                errors.append(f"{owner}: invalid kind or status")
            if len(selected) != 1 and (len(selected) < 3 or len(selected) % 2 == 0):
                errors.append(f"{owner}: component length must be one or odd and at least three")
            if component.get("direction") != _direction(component):
                errors.append(f"{owner}: direction mismatch")
            if abs(float(component["low"]) - min(_bounds(unit)[0] for unit in selected)) > EPSILON or abs(float(component["high"]) - max(_bounds(unit)[1] for unit in selected)) > EPSILON:
                errors.append(f"{owner}: envelope mismatch")
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"{owner}: malformed structure: {exc}")

    for point in buy_sell_points:
        owner = f"point:{point['id']}"
        try:
            point_type = point.get("point_type")
            status = point.get("status")
            if point.get("kind") != "buy_sell_point" or point_type not in {
                "first_buy", "second_buy", "third_buy", "first_sell", "second_sell", "third_sell",
            }:
                errors.append(f"{owner}: invalid kind or point type")
            if status not in {"candidate", "confirmed", "invalidated"}:
                errors.append(f"{owner}: invalid status")
            if (status == "confirmed") != bool(point.get("confirmed_at")):
                errors.append(f"{owner}: confirmation timestamp mismatch")
            component_ids = point.get("source_component_ids", [])
            if not component_ids or any(identifier not in component_by_id for identifier in component_ids):
                errors.append(f"{owner}: missing source component")
                point_components = []
            else:
                point_components = [component_by_id[identifier] for identifier in component_ids]
            flattened = list(dict.fromkeys(
                unit_id for component in point_components for unit_id in component.get("source_unit_ids", [])
            ))
            if flattened != point.get("source_unit_ids", []):
                errors.append(f"{owner}: source component evidence mismatch")
            if point_components and len({_stream_key(component) for component in point_components}) != 1:
                errors.append(f"{owner}: evidence crosses structural sequence")
            center = center_by_id.get(point.get("center_id"))
            if not center or int(center.get("level", 0)) != int(point.get("level", 0)):
                errors.append(f"{owner}: missing same-level center")
            if point_type in {"second_buy", "second_sell"}:
                parent = point_by_id.get(point.get("parent_point_id"))
                expected = "first_buy" if point_type == "second_buy" else "first_sell"
                if not parent or parent.get("status") != "confirmed" or parent.get("point_type") != expected:
                    errors.append(f"{owner}: second point lacks confirmed first point")
            if status == "confirmed" and point_type in {"third_buy", "third_sell"}:
                departure = component_by_id.get(point.get("departure_component_id"))
                retest = component_by_id.get(point.get("retest_component_id"))
                if not departure or not retest or _direction(departure) == _direction(retest):
                    errors.append(f"{owner}: invalid third-point departure or retest")
                elif point_type == "third_buy" and not (
                    float(departure["end_price"]) > float(center["zg"]) + EPSILON
                    and float(retest["low"]) > float(center["zg"]) + EPSILON
                ):
                    errors.append(f"{owner}: third buy re-enters center core")
                elif point_type == "third_sell" and not (
                    float(departure["end_price"]) < float(center["zd"]) - EPSILON
                    and float(retest["high"]) < float(center["zd"]) - EPSILON
                ):
                    errors.append(f"{owner}: third sell re-enters center core")
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"{owner}: malformed structure: {exc}")

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
            formation = list(center.get("formation_unit_ids", []))
            if center.get("formation_component_ids"):
                component_ids = center["formation_component_ids"]
                expected_component_count = count + 1
                if len(component_ids) != expected_component_count or any(identifier not in component_by_id for identifier in component_ids):
                    errors.append(f"{owner}: missing directional formation components")
                    formation_components = []
                else:
                    formation_components = [component_by_id[identifier] for identifier in component_ids]
                flattened = [unit_id for component in formation_components for unit_id in component.get("source_unit_ids", [])]
                if formation != flattened or not _alternating(formation_components):
                    errors.append(f"{owner}: formation component continuity mismatch")
                overlap = _strict_overlap(formation_components[1:]) if formation_components else None
                if overlap is None:
                    errors.append(f"{owner}: invalid component core")
                else:
                    zd, zg = overlap
                    entry = formation_components[0]
                    entered = (float(entry["start_price"]) + EPSILON < zd < float(entry["end_price"]) - EPSILON
                               if _direction(entry) == "up"
                               else float(entry["start_price"]) - EPSILON > zg > float(entry["end_price"]) + EPSILON)
                    if not entered or center.get("direction") != _direction(entry) or any(
                        abs(float(center[field]) - value) > EPSILON
                        for field, value in (("zd", zd), ("zg", zg), ("fixed_zd", zd), ("fixed_zg", zg))
                    ):
                        errors.append(f"{owner}: fixed core or entry direction mismatch")
            else:
                legacy = [center.get("entry_unit_id"), *center.get("core_unit_ids", [])]
                if len(legacy) != count + 1 or legacy != [unit["id"] for unit in selected[:count + 1]]:
                    errors.append(f"{owner}: directional formation requires entry and {count} core units")
                seed = _directional_seed(selected, 0, count)
                if seed is None:
                    errors.append(f"{owner}: invalid directional seed")
            expected_roles = [*formation, *center.get("extension_unit_ids", []), *center.get("peripheral_unit_ids", [])]
            if len(expected_roles) != len(set(expected_roles)) or set(expected_roles) != {unit["id"] for unit in selected}:
                errors.append(f"{owner}: ambiguous unit role ownership")
            if center.get("formation_component_ids"):
                core_components = formation_components[1:] if formation_components else []
                expected_start = core_components[0]["start_date"] if core_components else None
                expected_end = core_components[-1]["end_date"] if core_components else None
            else:
                expected_start = selected[1]["start_date"]
                expected_end = selected[3 if confirmed else 2]["end_date"]
            if center.get("start_date") != expected_start or center.get("end_date") != expected_end:
                errors.append(f"{owner}: rectangle must cover only the fixed core")
            if abs(float(center["dd"]) - min(_bounds(unit)[0] for unit in selected)) > EPSILON or abs(float(center["gg"]) - max(_bounds(unit)[1] for unit in selected)) > EPSILON:
                errors.append(f"{owner}: envelope does not cover owned units")
            if confirmed:
                expected_confirmed_at = (
                    _latest_timestamp(*(component.get("confirmed_at") for component in formation_components))
                    if center.get("formation_component_ids") else
                    _latest_timestamp(*(unit.get("confirmed_at") for unit in selected[:4]), *(unit["end_date"] for unit in selected[:4]))
                )
                if center.get("confirmed_at") != expected_confirmed_at:
                    errors.append(f"{owner}: formation confirmation time mismatch")
                if _center_owned_unit_ids(center) != center.get("source_unit_ids"):
                    errors.append(f"{owner}: owned and source units differ")
                if center.get("transition_role") == "turning":
                    entry_movement = movement_by_id.get(center.get("entry_movement_id"))
                    exit_movement = movement_by_id.get(center.get("exit_movement_id"))
                    point = point_by_id.get(center.get("transition_point_id"))
                    if not entry_movement or not exit_movement or not point:
                        errors.append(f"{owner}: incomplete turning references")
                else:
                    movement = movement_by_id.get(center.get("owner_movement_id"))
                    if not movement or center["id"] not in movement.get("center_ids", []) or not set(center["owned_unit_ids"]) <= set(movement.get("source_unit_ids", [])):
                        errors.append(f"{owner}: missing complete movement owner")
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
                expected_classification = _movement_classification(
                    [center for center in owned if center.get("direction") == movement["direction"]],
                    movement["direction"],
                ) if owned and all(center.get("direction") == movement["direction"] for center in owned) else (
                    "consolidation" if not owned else None
                )
                if movement.get("classification") != expected_classification:
                    errors.append(f"{owner}: classification inconsistent with owned centers")
                point = point_by_id.get(movement.get("confirmation_point_id"))
                confirmation = center_by_id.get(movement.get("confirmation_center_id"))
                if not point and not confirmation:
                    errors.append(f"{owner}: missing confirmation evidence")
                required_at = _latest_timestamp(
                    point.get("confirmed_at") if point else confirmation.get("confirmed_at") if confirmation else None,
                    *(unit.get("confirmed_at") for unit in selected),
                )
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
