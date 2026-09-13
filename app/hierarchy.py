from __future__ import annotations

import hashlib
import json
from typing import Any


HIERARCHY_VERSION = "center-hierarchy-same-level-color-v12"
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
            # A single-point contact is an invalid/ambiguous boundary, not a
            # persisted center relationship in the v12 contract. The level
            # decomposition records it as an issue and stops confirmation.
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
    return [{"trade_date": selected[0]["start_date"], "price": float(selected[0]["start_price"]), "boundary": start}, *(
        {"trade_date": unit["end_date"], "price": float(unit["end_price"]), "boundary": start + index + 1}
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
    between two confirmed units.  Higher-level decomposition must not bridge
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


def _movement_classification(centers: list[dict[str, Any]], direction: str) -> str:
    """Classify a trend only from strictly separated newborn centers."""
    if len(centers) < 2:
        return "consolidation"
    expected = "newborn_up" if direction == "up" else "newborn_down"
    return "trend" if all(
        classify_center_relation(previous, current) == expected
        for previous, current in zip(centers, centers[1:])
    ) else "consolidation"


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
    hinted_direction = direction
    direction_issue: dict[str, Any] | None = None
    demoted_for_direction_conflict = False
    endpoint = points[-1] if status == "provisional" else (
        candidate_extreme or
        _endpoint_units(units, start_boundary, end_boundary)[-1]
        if end_boundary > start_boundary else points[0]
    )
    start_point = points[0]
    # A short provisional tail can return to its starting price (for example,
    # a down pen followed by an up pen).  A zero-height arrow has no usable
    # direction and fails the movement contract.  Keep the visible tail end
    # in ``tail_end_*``, but use the already-recorded candidate extreme as the
    # actual structural endpoint whenever it is a real, non-flat endpoint.
    if (
        status == "provisional"
        and candidate_extreme is not None
        and abs(float(endpoint["price"]) - float(start_point["price"])) <= EPSILON
        and int(candidate_extreme.get("boundary", start_boundary)) > start_boundary
        and abs(float(candidate_extreme["price"]) - float(start_point["price"])) > EPSILON
    ):
        endpoint = candidate_extreme
    if not _boundary_matches_direction(float(start_point["price"]), float(endpoint["price"]), direction):
        direction_issue = {
            "code": "movement_direction_endpoint_mismatch",
            "hinted_direction": direction,
            "start_price": float(start_point["price"]),
            "end_price": float(endpoint["price"]),
        }
        # A provisional tail may temporarily disagree with its structural
        # hint, so its visible arrow follows the current endpoint. A confirmed
        # movement cannot retain a contradictory direction: stop confirmation,
        # clear the confirmation center/time, and expose the issue for review.
        if status == "confirmed":
            status = "provisional"
            demoted_for_direction_conflict = True
            confirmation_center = None
            # A manual correction must keep the operator's structural hint so
            # the contradiction remains visible and can be fixed at its
            # source. System arrows may safely follow their actual endpoint
            # after losing confirmation; this keeps the persisted provisional
            # arrow renderable and prevents audit-time direction mismatches.
            if origin != "manual-derived":
                direction = "up" if float(endpoint["price"]) >= float(start_point["price"]) else "down"
        else:
            direction = "up" if float(endpoint["price"]) >= float(start_point["price"]) else "down"
    selected_units = units[start_boundary:source_end]
    center_ids = [center["id"] for center in centers]
    movement_id = _stable_id(f"movement-L{level}", [
        role, str(_group_key(centers[0])[0]), str(_group_key(centers[0])[1]), center_ids[0],
        start_point["trade_date"], f'{start_point["price"]:.12g}',
    ])
    low = min(point["price"] for point in points)
    high = max(point["price"] for point in points)
    movement = {
        "id": movement_id, "kind": "movement", "role": role, "level": level,
        "direction": direction, "classification": _movement_classification(centers, direction),
        "status": status, "start_date": start_point["trade_date"], "start_price": start_point["price"],
        "end_date": endpoint["trade_date"], "end_price": endpoint["price"],
        "low": low, "high": high, "confirmed_at": confirmation_center.get("confirmed_at") if confirmation_center else None,
        "center_ids": center_ids, "center_count": len(centers),
        "confirmation_center_id": confirmation_center.get("id") if confirmation_center else None,
        "child_movement_ids": [unit["id"] for unit in selected_units if unit.get("kind") == "movement"],
        "source_unit_ids": [unit["id"] for unit in selected_units],
        "source_pen_ids": _source_pen_ids(selected_units),
        "continuous_range_id": _group_key(centers[0])[0], "sequence_id": _group_key(centers[0])[1],
        "origin": origin, "termination_reason": (
            "direction_conflict" if demoted_for_direction_conflict else
            "next_same_level_structure" if confirmation_center else "right_edge"
        ),
        "start_boundary": start_boundary, "end_boundary": int(endpoint["boundary"]),
        "endpoint_points": [start_point, endpoint],
        "path_points": [start_point, endpoint],
        "evidence": ["SAME-LEVEL-DECOMPOSITION-001", "MOVEMENT-SHARED-ENDPOINT-001"],
    }
    if direction_issue is not None:
        movement["issues"] = [direction_issue]
    if candidate_extreme is not None:
        movement.update({
            "candidate_extreme_date": candidate_extreme["trade_date"],
            "candidate_extreme_price": candidate_extreme["price"],
            "candidate_direction": hinted_direction,
            "tail_end_date": points[-1]["trade_date"],
            "tail_end_price": points[-1]["price"],
        })
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


def _extension_unit_span(
    center: dict[str, Any], units: list[dict[str, Any]], unit_index: dict[str, int],
) -> tuple[int, int] | None:
    """Return the units absorbed by a canonical center for 3x3 grouping.

    The ordinary hierarchy view starts an L1 movement at the entry pen.  An
    extension upgrade, however, groups the *post-entry* low-level units, so
    P0 must not be consumed by the first 3-unit component.  Higher-level
    centers already reference their child movements directly.  Explicit IDs
    are authoritative; malformed or non-contiguous references are rejected
    instead of being approximated from dates.
    """
    movement_units = any(unit.get("kind") == "movement" for unit in units)

    def values_for(*fields: str) -> list[str]:
        values: list[str] = []
        for field in fields:
            value = center.get(field)
            items = value if isinstance(value, list) else [value] if value else []
            for item in items:
                item_id = str(item)
                if item_id not in values:
                    values.append(item_id)
        return values

    if movement_units:
        # A parent center's children are the units at the next lower level.
        refs = values_for(
            "child_movement_ids", "core_unit_ids", "source_unit_ids",
            "extension_movement_ids", "child_unit_ids",
        )
        start_refs = refs
    else:
        core_refs = values_for("core_pen_ids", "core_unit_ids")
        refs = values_for(
            "source_pen_ids", "pen_ids", "formation_pen_ids", "core_pen_ids",
            "core_unit_ids", "extension_pen_ids", "peripheral_pen_ids",
        )
        # Hand-authored centers may only provide core/extension IDs.  Include
        # them in the span while still preferring the core as the start.
        refs.extend(item for item in core_refs if item not in refs)
        start_refs = core_refs or refs

    if refs:
        if any(item_id not in unit_index for item_id in refs):
            return None
        indexes = sorted({unit_index[item_id] for item_id in refs})
        if indexes != list(range(indexes[0], indexes[-1] + 1)):
            return None
        start_indexes = sorted({unit_index[item_id] for item_id in start_refs if item_id in unit_index})
        if not start_indexes:
            start_indexes = indexes
        if start_indexes != list(range(start_indexes[0], start_indexes[-1] + 1)):
            return None
        start = start_indexes[0] if not movement_units else indexes[0]
        # The start refs for an L1 center must lie inside the complete source
        # span.  This catches stale manual references before any component is
        # emitted.
        if start < indexes[0] or start > indexes[-1]:
            return None
        return start, indexes[-1] + 1

    # A date-only manual center has no reliable entry/core IDs.  Preserve the
    # existing defensive span lookup, with the L1 entry excluded when a
    # start_pen_index/start_pen hint is available.
    fallback = _center_unit_span(center, units, unit_index)
    if fallback is None:
        return None
    if not movement_units:
        start_hint = center.get("start_pen_index")
        if start_hint is not None:
            try:
                hinted = int(start_hint)
            except (TypeError, ValueError):
                hinted = fallback[0]
            if fallback[0] <= hinted < fallback[1]:
                fallback = (hinted, fallback[1])
        elif center.get("start_pen") in unit_index:
            fallback = (unit_index[center["start_pen"]], fallback[1])
        elif center.get("entry_pen_id") in unit_index and fallback[1] - fallback[0] > 1:
            fallback = (fallback[0] + 1, fallback[1])
    return fallback


def _decompose_hierarchy_level_split(
    units: list[dict[str, Any]], structural_centers: list[dict[str, Any]], level: int,
    role: str, origin: str,
) -> dict[str, Any]:
    """Componentize canonical center extensions into non-overlapping 3-unit chunks.

    This is the hierarchy-only nine-segment path.  It intentionally does not
    replace the ordinary boundary-oriented implementation below: callers that
    need the latter (including compatibility tests and diagnostics) leave
    ``split_extensions`` disabled.
    """
    movements: list[dict[str, Any]] = []
    unassigned: list[str] = []
    issues: list[dict[str, Any]] = []
    used_centers: list[dict[str, Any]] = []
    groups: dict[tuple[int, int], list[dict[str, Any]]] = {}
    units_by_group: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for unit in units:
        if unit.get("status", "confirmed") == "confirmed":
            units_by_group.setdefault(_group_key(unit), []).append(unit)
    for center in structural_centers:
        if center.get("status", "confirmed") != "confirmed":
            continue
        groups.setdefault(_group_key(center), []).append(center)

    for key, raw_centers in groups.items():
        group_units = units_by_group.get(key, [])
        group_units.sort(key=lambda item: (
            item.get("start_date", ""), item.get("end_date", ""),
            int(item.get("ordinal", 0) or 0), item.get("id", ""),
        ))
        if not group_units:
            continue
        unit_index = {str(unit.get("id")): index for index, unit in enumerate(group_units)}
        centers: list[dict[str, Any]] = []
        for center in raw_centers:
            extension_span = _extension_unit_span(center, group_units, unit_index)
            if extension_span is None:
                issues.append({
                    "code": "invalid_hierarchy_center_reference",
                    "center_id": center.get("id"), "range_index": key[0],
                    "sequence_id": key[1], "level": level,
                })
                continue
            # A normal L1 center remains one boundary-oriented component. It
            # is only the post-entry six-or-more-unit extension that enters
            # the 3+3(+3) grouping path. Short centers retain their entry pen
            # in the component so adjacent independent centers remain a
            # contiguous stream for expansion upgrades.
            span = extension_span
            needs_chunking = True
            if not any(unit.get("kind") == "movement" for unit in group_units):
                if extension_span[1] - extension_span[0] < 6:
                    legacy_span = _center_unit_span(center, group_units, unit_index)
                    if legacy_span is not None:
                        span = legacy_span
                    needs_chunking = False
            normalized = dict(center)
            normalized["unit_start_index"], normalized["unit_end_index"] = span
            normalized["extension_unit_start_index"], normalized["extension_unit_end_index"] = extension_span
            normalized["needs_chunking"] = needs_chunking
            centers.append(normalized)
        centers.sort(key=lambda item: (item["unit_start_index"], item["start_date"], item["id"]))
        # Canonical center spans may not overlap.  Keep the leftmost valid
        # stream and stop before an ambiguous manual overlap.
        valid_centers: list[dict[str, Any]] = []
        previous: dict[str, Any] | None = None
        for center in centers:
            if previous and center["unit_start_index"] < previous["unit_end_index"]:
                issues.append({
                    "code": "overlapping_hierarchy_center_spans",
                    "center_id": center.get("id"),
                    "previous_center_id": previous.get("id"),
                    "range_index": key[0], "sequence_id": key[1], "level": level,
                })
                break
            valid_centers.append(center)
            previous = center
        centers = valid_centers
        if not centers:
            continue
        used_centers.extend(centers)
        cursor = centers[0]["unit_start_index"]
        if cursor:
            unassigned.extend(unit["id"] for unit in group_units[:cursor])

        for center in centers:
            start = center["unit_start_index"]
            end = center["unit_end_index"]
            if start > cursor:
                # Departure/gap units are deliberately not attached to either
                # center.  This prevents a parent upgrade from crossing an
                # unexplained boundary.
                unassigned.extend(unit["id"] for unit in group_units[cursor:start])
                cursor = start
            if start < cursor:
                issues.append({
                    "code": "overlapping_hierarchy_center_spans",
                    "center_id": center.get("id"), "range_index": key[0],
                    "sequence_id": key[1], "level": level,
                })
                continue

            if not center.get("needs_chunking", True):
                # Keep the ordinary short-center component intact.  This is
                # also what lets two independent centers with overlapping
                # envelopes form an expansion parent even when their source
                # spans contain four units (entry + core three).
                direction = _direction(center)
                built = _movement_from_centers(
                    group_units, [center], level, start, end, direction,
                    "confirmed", None, role, origin,
                )
                if built.get("status") == "confirmed":
                    consumed = group_units[start:end]
                    built["confirmed_at"] = _latest_timestamp(
                        center.get("confirmed_at"),
                        *(unit.get("confirmed_at") for unit in consumed),
                        *(unit.get("end_date") for unit in consumed),
                    )
                    built["termination_reason"] = "center_component_confirmed"
                for issue in built.get("issues", []):
                    issues.append({"movement_id": built["id"], **issue})
                movements.append(built)
                cursor = end
                continue

            remaining_start = start
            while remaining_start + 3 <= end:
                chunk = group_units[remaining_start:remaining_start + 3]
                valid_chunk = _alternating(chunk) and _strict_overlap(chunk) is not None
                if not valid_chunk:
                    issues.append({
                        "code": "invalid_hierarchy_component_chunk",
                        "center_id": center.get("id"), "start_unit_id": chunk[0]["id"] if chunk else None,
                        "range_index": key[0], "sequence_id": key[1], "level": level,
                    })
                    break
                direction = _direction(chunk[0])
                built = _movement_from_centers(
                    group_units, [center], level, remaining_start, remaining_start + 3,
                    direction, "confirmed", None, role, origin,
                )
                if built.get("status") == "confirmed":
                    built["confirmed_at"] = _latest_timestamp(
                        *(unit.get("confirmed_at") for unit in chunk),
                        *(unit.get("end_date") for unit in chunk),
                    )
                    built["termination_reason"] = "extension_chunk_confirmed"
                    built.setdefault("evidence", []).append("CENTER-EXTENSION-3SEGMENT-001")
                for issue in built.get("issues", []):
                    issues.append({"movement_id": built["id"], **issue})
                movements.append(built)
                remaining_start += 3
                cursor = remaining_start

            if remaining_start < end:
                # A one/two-unit tail is visible but cannot confirm a 3-unit
                # hierarchy component.  Keep its current endpoint and retain
                # the candidate extreme separately for later recalculation.
                tail_points = _endpoint_units(group_units, remaining_start, end)
                if tail_points:
                    candidate = _extreme(tail_points, _direction(group_units[remaining_start]))
                    built = _movement_from_centers(
                        group_units, [center], level, remaining_start, candidate["boundary"],
                        _direction(group_units[remaining_start]), "provisional", None, role, origin,
                        source_end_boundary=end, candidate_extreme=candidate,
                    )
                    for issue in built.get("issues", []):
                        issues.append({"movement_id": built["id"], **issue})
                    movements.append(built)
                cursor = end
            else:
                cursor = end

    covered = {
        unit_id for movement in movements for unit_id in movement.get("source_unit_ids", [])
    }
    unassigned.extend(
        unit["id"] for unit in units
        if unit.get("status", "confirmed") == "confirmed"
        and unit["id"] not in covered and unit["id"] not in unassigned
    )
    for ordinal, movement in enumerate(movements):
        movement["ordinal"] = ordinal
    input_hash = hashlib.sha256(
        json.dumps(structural_centers, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:20]
    return {
        "centers": used_centers, "movements": movements,
        "meta": {
            "level": level, "status": "partial" if issues else ("complete" if movements else "no_center"),
            "issues": issues, "unassigned_unit_ids": unassigned, "unassigned_pen_ids": unassigned,
            "movement_input_hash": input_hash, "algorithm_version": HIERARCHY_VERSION,
        },
    }


def decompose_hierarchy_level(
    units: list[dict[str, Any]], structural_centers: list[dict[str, Any]], level: int,
    role: str = "hierarchy_component", origin: str = "system", split_extensions: bool = False,
) -> dict[str, Any]:
    """Build hierarchy movements around already-confirmed structural centers.

    This deliberately differs from :func:`decompose_same_level`: the latter
    makes operation-view centers mechanically from three units, while this
    function consumes the canonical centers supplied by the preceding
    hierarchy stage (raw directional L1 centers or confirmed parent centers).
    """
    if split_extensions:
        return _decompose_hierarchy_level_split(units, structural_centers, level, role, origin)
    movements: list[dict[str, Any]] = []
    unassigned: list[str] = []
    issues: list[dict[str, Any]] = []
    used_centers: list[dict[str, Any]] = []
    groups: dict[tuple[int, int], list[dict[str, Any]]] = {}
    units_by_group: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for unit in units:
        units_by_group.setdefault(_group_key(unit), []).append(unit)
    for center in structural_centers:
        if center.get("status", "confirmed") != "confirmed":
            continue
        key = _group_key(center)
        groups.setdefault(key, []).append(center)

    for key, raw_centers in groups.items():
        group_units = units_by_group.get(key, [])
        group_units.sort(key=lambda item: (
            item.get("start_date", ""), item.get("end_date", ""),
            int(item.get("ordinal", 0) or 0), item.get("id", ""),
        ))
        unit_index = {
            str(unit.get("id")): index for index, unit in enumerate(group_units)
        }
        centers: list[dict[str, Any]] = []
        for center in raw_centers:
            span = _center_unit_span(center, group_units, unit_index)
            if span is None:
                # Keep the failure visible in decomposition metadata instead
                # of silently dropping a canonical center from the hierarchy
                # input. The affected group remains unassigned.
                issues.append({
                    "code": "invalid_hierarchy_center_reference",
                    "center_id": center.get("id"), "range_index": key[0],
                    "sequence_id": key[1], "level": level,
                })
                continue
            normalized = dict(center)
            normalized["unit_start_index"], normalized["unit_end_index"] = span
            centers.append(normalized)
        centers.sort(key=lambda item: (item["unit_start_index"], item["start_date"], item["id"]))
        # Structural centers must consume disjoint low-level units. A manual
        # overlap is ambiguous; retain the left center for a provisional tail
        # and stop before confirming anything across the overlap.
        for overlap_index, (previous, current_center) in enumerate(zip(centers, centers[1:]), start=1):
            if current_center["unit_start_index"] < previous["unit_end_index"]:
                issues.append({
                    "code": "overlapping_hierarchy_center_spans",
                    "center_id": current_center.get("id"),
                    "previous_center_id": previous.get("id"),
                    "range_index": key[0], "sequence_id": key[1], "level": level,
                })
                centers = centers[:overlap_index]
                break
        if not centers or not group_units:
            continue
        used_centers.extend(centers)
        first_start = centers[0]["unit_start_index"]
        if first_start:
            unassigned.extend(unit["id"] for unit in group_units[:first_start])
        current = [centers[0]]
        start_boundary = first_start
        direction = _direction(centers[0])
        last_end = centers[0]["unit_end_index"]
        stopped = False
        for center in centers[1:]:
            relation = classify_center_relation(current[-1], center)
            relation_direction = (
                "up" if relation in {"newborn_up", "expansion_up"} else
                "down" if relation in {"newborn_down", "expansion_down"} else None
            )
            if relation in {"extension", "touching"}:
                # An overlapping/contacting canonical center is not a new
                # independent structure.  Stop this level rather than using
                # it as an artificial reversal boundary.
                issues.append({
                    "code": "overlapping_hierarchy_centers" if relation == "extension" else "touching_hierarchy_centers",
                    "center_id": center.get("id"),
                    "previous_center_id": current[-1].get("id"),
                    "range_index": key[0], "level": level,
                })
                break
            if relation.startswith("newborn_") and relation_direction == direction:
                direction = relation_direction
                current.append(center)
                last_end = max(last_end, center["unit_end_index"])
                continue
            next_start = center["unit_start_index"]
            # The boundary is the extreme confirmed endpoint between the
            # start of the current movement and the next center's entry. It
            # may occur in an earlier core unit, so searching only after the
            # current center's right edge would lose the real H/L turning
            # point (and can reverse the apparent arrow direction).
            search = _endpoint_units(group_units, start_boundary, next_start + 1)
            boundary = _extreme(
                search or _endpoint_units(group_units, start_boundary, next_start),
                direction,
            )
            if boundary["boundary"] <= start_boundary:
                issues.append({
                    "code": "invalid_movement_boundary", "center_id": center.get("id"),
                    "previous_center_id": current[-1].get("id"),
                    "range_index": key[0], "level": level,
                })
                stopped = True
                break
            built = _movement_from_centers(
                group_units, current, level, start_boundary, boundary["boundary"], direction,
                "confirmed", center, role, origin,
            )
            for issue in built.get("issues", []):
                issues.append({"movement_id": built["id"], **issue})
            movements.append(built)
            start_boundary = boundary["boundary"]
            current = [center]
            # Expansion starts a new consolidation in its own structural
            # direction. A newborn in the opposite direction is a reversal.
            direction = relation_direction or _opposite(built["direction"])
            last_end = center["unit_end_index"]
        if start_boundary < len(group_units) and not stopped:
            tail_points = _endpoint_units(group_units, start_boundary, len(group_units))
            candidate = _extreme(tail_points, direction)
            built = _movement_from_centers(
                group_units, current, level, start_boundary, candidate["boundary"], direction,
                "provisional", None, role, origin, source_end_boundary=len(group_units),
                candidate_extreme=candidate,
            )
            for issue in built.get("issues", []):
                issues.append({"movement_id": built["id"], **issue})
            movements.append(built)
    # Units in groups that were never reached remain explicitly unassigned.
    covered = {
        unit_id
        for movement in movements
        for unit_id in movement.get("source_unit_ids", [])
    }
    unassigned.extend(unit["id"] for unit in units if unit["id"] not in covered and unit["id"] not in unassigned)
    for ordinal, movement in enumerate(movements):
        movement["ordinal"] = ordinal
    input_hash = hashlib.sha256(json.dumps(structural_centers, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:20]
    return {
        "centers": used_centers, "movements": movements,
        "meta": {
            "level": level, "status": "partial" if issues else ("complete" if movements else "no_center"),
            "issues": issues, "unassigned_unit_ids": unassigned, "unassigned_pen_ids": unassigned,
            "movement_input_hash": input_hash, "algorithm_version": HIERARCHY_VERSION,
        },
    }


def _mechanical_centers(
    units: list[dict[str, Any]], level: int, origin: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    centers: list[dict[str, Any]] = []
    unassigned: list[str] = []
    index = 0
    while index + 2 < len(units):
        core = units[index:index + 3]
        overlap = _strict_overlap(core)
        if not overlap or not _alternating(core):
            unassigned.append(units[index]["id"])
            index += 1
            continue
        zd, zg = overlap
        lows, highs = zip(*(_bounds(unit) for unit in core))
        center_id = _stable_id(f"same-level-center-L{level}", [unit["id"] for unit in core])
        centers.append({
            "id": center_id, "kind": "center", "role": "same_level", "level": level,
            "status": "confirmed", "upgrade_kind": None, "progress": "3/3",
            "start_date": core[0]["start_date"], "end_date": core[-1]["end_date"],
            "confirmed_at": core[-1].get("confirmed_at") or core[-1]["end_date"],
            "zd": zd, "zg": zg, "fixed_zd": zd, "fixed_zg": zg,
            "dd": min(lows), "gg": max(highs), "low": zd, "high": zg,
            "core_unit_ids": [unit["id"] for unit in core], "source_unit_ids": [unit["id"] for unit in core],
            "child_movement_ids": [unit["id"] for unit in core if unit.get("kind") == "movement"],
            # ``unit_end_index`` is an exclusive boundary.  Keeping the full
            # three-unit span here makes the evidence center internally
            # consistent even though the operation decomposition only uses its
            # start index as a scan anchor.
            "source_pen_ids": _source_pen_ids(core), "unit_start_index": index, "unit_end_index": index + 3,
            "continuous_range_id": _group_key(core[0])[0], "sequence_id": _group_key(core[0])[1],
            "parent_center_ids": [], "origin": origin,
            "evidence": ["SAME-LEVEL-CENTER-THREE-001"],
        })
        index += 3
    unassigned.extend(unit["id"] for unit in units[index:])
    return centers, unassigned


def _split_contiguous_units(
    units: list[dict[str, Any]],
) -> list[tuple[int, list[dict[str, Any]], dict[str, Any] | None]]:
    """Split a source group at structural holes before same-level parsing.

    The returned unit dictionaries are shallow copies.  A derived sequence ID
    is assigned only to segments after the first one; this keeps the original
    lower-level movement rows immutable while making the sequence boundary
    explicit to all subsequent center/movement builders.
    """
    if not units:
        return []
    ordered = sorted(
        (dict(unit) for unit in units),
        key=lambda item: (item.get("start_date", ""), item.get("end_date", ""), item.get("id", "")),
    )
    source_sequence = int(_group_key(ordered[0])[1])
    # Atomic pens already carry exchange/session sequence boundaries and may
    # legitimately contain a same-direction replacement.  Derived movement
    # streams, however, are the input to recursive levels and must alternate
    # as well as share their real endpoint.
    require_alternating = any(unit.get("kind") == "movement" for unit in ordered)
    segments: list[tuple[int, list[dict[str, Any]], dict[str, Any] | None]] = []
    current: list[dict[str, Any]] = []
    segment_index = 0
    break_info: dict[str, Any] | None = None
    for original in ordered:
        if current and not _units_are_contiguous(
            current[-1], original, require_alternating=require_alternating
        ):
            previous_id = current[-1].get("id")
            segments.append((segment_index, current, break_info))
            segment_index += 1
            current = []
            break_info = {
                "previous_unit_id": previous_id,
                "current_unit_id": original.get("id"),
            }
        copied = dict(original)
        copied["source_sequence_id"] = source_sequence
        copied["sequence_id"] = _structural_sequence_id(source_sequence, segment_index)
        copied["structure_segment_index"] = segment_index
        current.append(copied)
    if current:
        segments.append((segment_index, current, break_info))
    return segments


def _recursive_units(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prepare confirmed hierarchy components for the next recursive level.

    Components from one market sequence are not necessarily one continuous
    structural stream: an incomplete component between two centers is omitted
    from the confirmed input.  Split those streams and copy the rows so their
    derived sequence IDs are visible to the next level without mutating the
    persisted component movement records.
    """
    groups: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for unit in units:
        if unit.get("status", "confirmed") != "confirmed":
            continue
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


def decompose_same_level(
    units: list[dict[str, Any]], level: int, role: str = "same_level_decomposition", origin: str = "system"
) -> dict[str, Any]:
    movements: list[dict[str, Any]] = []
    view_centers: list[dict[str, Any]] = []
    unassigned: list[str] = []
    issues: list[dict[str, Any]] = []
    groups: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for unit in units:
        if unit.get("status", "confirmed") == "confirmed":
            groups.setdefault(_group_key(unit), []).append(unit)
    for key, group_units in groups.items():
        # A higher-level operation unit is valid only inside a genuinely
        # contiguous, alternating lower-level stream.  Confirmed hierarchy
        # components can have unassigned tails even when their source
        # sequence_id is unchanged, so process each derived segment alone.
        for segment_index, segment_units, break_info in _split_contiguous_units(group_units):
            segment_key = (key[0], _group_key(segment_units[0])[1])
            if break_info is not None:
                issues.append({
                    "code": "non_contiguous_input",
                    "range_index": key[0], "sequence_id": key[1],
                    "segment_index": segment_index,
                    **break_info,
                })
            centers, missing = _mechanical_centers(segment_units, level, origin)
            view_centers.extend(centers)
            unassigned.extend(missing)
            if not centers:
                continue
            current = [centers[0]]
            start_boundary = centers[0]["unit_start_index"]
            direction = _direction(segment_units[min(start_boundary, len(segment_units) - 1)])
            for center in centers[1:]:
                relation = classify_center_relation(current[-1], center)
                relation_direction = "up" if relation == "newborn_up" else "down" if relation == "newborn_down" else None
                if relation_direction and relation_direction == direction:
                    direction = relation_direction
                    current.append(center)
                    continue
                next_boundary = center["unit_start_index"]
                # Search the entire current movement span. The true H/L
                # boundary can be inside the center's earlier core units, not
                # just in the final unit immediately before the next center.
                search = _endpoint_units(segment_units, start_boundary, next_boundary + 1)
                boundary = _extreme(search or _endpoint_units(segment_units, start_boundary, next_boundary), direction)
                if boundary["boundary"] <= start_boundary:
                    # An extreme at the current start is not a valid movement
                    # boundary. Keep the ambiguity visible and restart at the
                    # next mechanical center instead of emitting an empty
                    # slice.
                    issues.append({
                        "code": "invalid_movement_boundary",
                        "center_id": center.get("id"),
                        "previous_center_id": current[-1].get("id"),
                        "range_index": segment_key[0], "sequence_id": segment_key[1], "level": level,
                    })
                    unassigned.extend(
                        unit["id"] for unit in segment_units[start_boundary:next_boundary]
                        if unit["id"] not in unassigned
                    )
                    start_boundary = next_boundary
                    current = [center]
                    direction = _direction(segment_units[min(next_boundary, len(segment_units) - 1)])
                    continue
                built = _movement_from_centers(
                    segment_units, current, level, start_boundary, boundary["boundary"], direction,
                    "confirmed", center, role, origin,
                )
                for issue in built.get("issues", []):
                    issues.append({"movement_id": built["id"], **issue})
                movements.append(built)
                start_boundary = boundary["boundary"]
                current = [center]
                # A confirmed reversal flips direction at the shared extreme.
                direction = _opposite(built["direction"])
            if start_boundary < len(segment_units):
                tail_points = _endpoint_units(segment_units, start_boundary, len(segment_units))
                candidate = _extreme(tail_points, direction)
                built = _movement_from_centers(
                    segment_units, current, level, start_boundary, candidate["boundary"], direction,
                    "provisional", None, role, origin, source_end_boundary=len(segment_units),
                    candidate_extreme=candidate,
                )
                for issue in built.get("issues", []):
                    issues.append({"movement_id": built["id"], **issue})
                movements.append(built)
    for ordinal, movement in enumerate(movements):
        movement["ordinal"] = ordinal
    for ordinal, center in enumerate(view_centers):
        center["ordinal"] = ordinal
        center["level_ordinal"] = ordinal
    input_hash = hashlib.sha256(json.dumps(units, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:20]
    return {
        "centers": view_centers, "movements": movements,
        "meta": {"level": level, "status": "partial" if issues else ("complete" if movements else "no_center"),
                 "issues": issues,
                 "unassigned_unit_ids": unassigned, "unassigned_pen_ids": unassigned,
                 "movement_input_hash": input_hash, "algorithm_version": HIERARCHY_VERSION},
    }


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
        if referenced_centers and len(referenced_centers) == len(set(referenced_centers)):
            return None
        if all(len(movement.get("source_unit_ids") or []) == 3 for movement in selected):
            return "extension_3x3"
        return None

    groups: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for movement in movements:
        if movement.get("status") == "confirmed":
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
            upgrade_kind = classify_upgrade_kind(selected)
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
                    "CENTER-UPGRADE-EXTENSION-001" if upgrade_kind == "extension_3x3" else "CENTER-UPGRADE-EXPANSION-001",
                ],
            })
            index += 3 if confirmed else 2
    return centers


def build_hierarchy(pens: list[dict[str, Any]], l1_centers: list[dict[str, Any]], origin: str = "system") -> dict[str, Any]:
    # Callers normally pass only the raw directional L1 seeds.  Persisted
    # snapshots also contain operation-view ``same_level`` centers; accepting
    # those as seeds would feed a center without an entry direction back into
    # the hierarchy engine and can fail while deriving its boundary.  Keep
    # legacy hand-authored centers (which omit ``role``) compatible.
    l1_centers = [
        center for center in l1_centers
        if int(center.get("level", 1) or 1) == 1
        and center.get("role", "hierarchy") != "same_level"
    ]
    hierarchy_centers: list[dict[str, Any]] = []
    for center in l1_centers:
        normalized = dict(center, role="hierarchy", origin=origin, parent_center_ids=[])
        normalized.setdefault("upgrade_kind", None)
        normalized.setdefault("progress", "3/3")
        normalized.setdefault("core_unit_ids", list(normalized.get("core_pen_ids") or []))
        normalized.setdefault("child_movement_ids", [])
        normalized.setdefault("child_center_ids", [])
        hierarchy_centers.append(normalized)
    units_by_level: dict[int, list[dict[str, Any]]] = {0: atomic_pen_units(pens)}
    movements: list[dict[str, Any]] = []
    decompositions: dict[str, dict[str, Any]] = {}
    same_level_centers: list[dict[str, Any]] = []

    for level in range(1, MAX_CENTER_LEVEL + 1):
        units = units_by_level.get(level - 1, [])
        if not units:
            break
        decomposition = decompose_same_level(units, level, origin=origin)
        level_movements = decomposition["movements"]
        same_level_centers.extend(decomposition["centers"])
        movements.extend(level_movements)
        level_meta = dict(decomposition["meta"])
        decompositions[str(level)] = level_meta
        if level >= MAX_CENTER_LEVEL:
            break
        if level == 1:
            structural_centers = hierarchy_centers
        else:
            structural_centers = [
                center for center in hierarchy_centers
                if int(center.get("level", 1)) == level
            ]
        component_decomposition = decompose_hierarchy_level(
            units, structural_centers, level, role="hierarchy_component", origin=origin,
            split_extensions=True,
        )
        # Surface hierarchy-component reference and direction issues through
        # the same level metadata consumed by the API/UI.
        component_issues = component_decomposition.get("meta", {}).get("issues", [])
        if component_issues:
            level_meta.setdefault("issues", []).extend(component_issues)
        # The operation-view decomposition and the hierarchy-component
        # stream can each leave a different left/tail residue.  Expose the
        # union so callers can account for every unit rather than silently
        # losing the entry unit excluded by the 3x3 extension path.
        for field in ("unassigned_unit_ids", "unassigned_pen_ids"):
            existing = level_meta.setdefault(field, [])
            for unit_id in component_decomposition.get("meta", {}).get(field, []) or []:
                if unit_id not in existing:
                    existing.append(unit_id)
            order = {str(item.get("id")): index for index, item in enumerate(units)}
            existing.sort(key=lambda value: order.get(str(value), len(order)))
        component_movements = component_decomposition["movements"]
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
            for _segment_index, segment, break_info in _split_contiguous_units(component_groups[key]):
                if break_info is not None:
                    level_meta.setdefault("issues", []).append({
                        "code": "non_contiguous_hierarchy_input",
                        "range_index": key[0],
                        "sequence_id": key[1],
                        **break_info,
                    })
                segmented_components.extend(segment)
        parent_centers = _parent_centers(
            # Parent upgrades must inspect the canonical centers consumed by
            # this hierarchy stage.  The component decomposition intentionally
            # has no operation-view centers of its own.
            segmented_components, level + 1, origin, structural_centers
        )
        if not parent_centers:
            break
        hierarchy_centers.extend(parent_centers)
        units_by_level[level] = segmented_components

    # Same-level centers are evidence nodes referenced by both movement roles.
    # Persist them, but keep their role separate so chart APIs can expose only
    # the structural hierarchy selected by the user.
    centers = [*hierarchy_centers, *same_level_centers]
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
    hierarchy_input = json.dumps({"pens": pens, "l1_centers": l1_centers}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    # Keep the movement/decomposition input fingerprint separate from the
    # hierarchy fingerprint.  The former describes the normalized units and
    # structural centers consumed by the movement builders; it is persisted
    # independently so a snapshot can distinguish a hierarchy-only change
    # from a movement-input change.
    movement_input = json.dumps({
        "units_by_level": {
            str(level): [
                {
                    "id": unit.get("id"), "kind": unit.get("kind"),
                    "start_date": unit.get("start_date"), "end_date": unit.get("end_date"),
                    "start_price": unit.get("start_price"), "end_price": unit.get("end_price"),
                    "status": unit.get("status"), "continuous_range_id": _group_key(unit)[0],
                    "sequence_id": _group_key(unit)[1],
                }
                for unit in values
            ]
            for level, values in sorted(units_by_level.items())
        },
        "structural_centers": [
            {
                "id": center.get("id"), "level": center.get("level"),
                "status": center.get("status"), "child_movement_ids": center.get("child_movement_ids", []),
                "core_unit_ids": center.get("core_unit_ids", []),
            }
            for center in hierarchy_centers
        ],
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    movement_input_hash = hashlib.sha256(movement_input.encode()).hexdigest()[:20]
    # Expose the fingerprint in both contracts: callers persisting the whole
    # hierarchy use the top-level field, while decomposition-only consumers
    # read the metadata object.
    decompositions.setdefault("movement_input_hash", movement_input_hash)
    return {
        "centers": centers,
        "same_level_centers": same_level_centers,
        "center_relations": relations,
        "movements": movements,
        "decompositions": decompositions,
        "max_confirmed_center_level": max(confirmed_levels, default=1 if l1_centers else 0),
        "max_available_center_level": max(available_levels, default=1 if l1_centers else 0),
        "hierarchy_input_hash": hashlib.sha256(hierarchy_input.encode()).hexdigest()[:20],
        "movement_input_hash": movement_input_hash,
        "hierarchy_version": HIERARCHY_VERSION,
    }
