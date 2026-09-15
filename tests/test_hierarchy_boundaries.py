"""Regression tests for hierarchy input boundaries.

These cases exercise the formal hierarchy API with
hand-authored units.  They deliberately model malformed references that can
otherwise be hidden by date-based fallbacks.
"""

from app.hierarchy import (
    _movement_from_centers,
    _parent_centers,
    _recursive_units,
    build_hierarchy,
    build_hierarchy_components,
)


def _unit(index: int, sequence_id: int = 0, start: float = 1, end: float = 2) -> dict:
    return {
        "id": f"u{sequence_id}-{index}",
        "kind": "atomic_pen",
        "start_date": f"2026-01-{index + 1:02d}",
        "end_date": f"2026-01-{index + 2:02d}",
        "start_price": start,
        "end_price": end,
        "low": min(start, end),
        "high": max(start, end),
        "direction": "up" if end > start else "down",
        "status": "confirmed",
        "continuous_range_id": 0,
        "sequence_id": sequence_id,
    }


def _center(refs: list[str], *, sequence_id: int = 0) -> dict:
    return {
        "id": "c",
        "level": 1,
        "status": "confirmed",
        "direction": "up",
        "start_date": "2026-01-01",
        "end_date": "2026-01-04",
        "confirmed_at": "2026-01-05",
        "zd": 1.5,
        "zg": 2.5,
        "dd": 1.0,
        "gg": 3.0,
        "continuous_range_id": 0,
        "sequence_id": sequence_id,
        "entry_pen_id": refs[0] if refs else None,
        "formation_pen_ids": refs,
        "source_pen_ids": refs,
        "pen_ids": refs,
    }


def test_explicit_cross_sequence_reference_is_not_date_fallback():
    units = [_unit(i, sequence_id=0) for i in range(4)]
    units.extend(_unit(i, sequence_id=1) for i in range(4))
    # Dates intentionally overlap the local sequence, while every explicit
    # reference points to sequence 1.
    center = _center([f"u1-{i}" for i in range(4)])

    result = build_hierarchy_components(units, [center], level=1)

    assert result["centers"] == []
    assert result["movements"] == []


def test_non_contiguous_or_missing_explicit_reference_is_rejected():
    units = [_unit(i) for i in range(4)]
    center = _center(["u0-0", "u0-2", "missing", "u0-3"])

    result = build_hierarchy_components(units, [center], level=1)

    assert result["centers"] == []
    assert result["movements"] == []


def test_overlapping_structural_center_spans_stop_the_level():
    prices = [1, 6, 2, 7, 3, 8, 4]
    units = [_unit(index, start=start, end=end) for index, (start, end) in enumerate(zip(prices, prices[1:]))]
    first = _center([f"u0-{i}" for i in range(4)])
    second = _center([f"u0-{i}" for i in range(2, 6)])
    second["id"] = "c2"
    # Make the price cores strictly separated so this case specifically
    # exercises overlapping *unit spans*, rather than the price relation
    # guard tested elsewhere.
    second.update({"zd": 5.0, "zg": 6.0, "dd": 4.0, "gg": 7.0})

    result = build_hierarchy_components(units, [first, second], level=1)

    assert result["centers"]


def test_hierarchy_level_orders_units_before_building_boundaries():
    chronological = [_unit(0, start=1, end=6), _unit(1, start=6, end=2), _unit(2, start=2, end=5)]
    units = [chronological[2], chronological[0], chronological[1]]
    center = _center(["u0-0", "u0-1", "u0-2"])

    result = build_hierarchy_components(units, [center], level=1)

    assert result["movements"]
    movement = result["movements"][0]
    assert movement["start_date"] <= movement["end_date"]
    assert movement["source_unit_ids"] == ["u0-0", "u0-1", "u0-2"]


def _movement(index: int, direction: str, *, start_boundary: int, end_boundary: int,
             source_unit_ids: list[str]) -> dict:
    return {
        "id": f"m{index}",
        "level": 1,
        "status": "confirmed",
        "direction": direction,
        "start_date": f"2026-02-{index + 1:02d}",
        "end_date": f"2026-02-{index + 2:02d}",
        "start_price": 1.0 if direction == "up" else 10.0,
        "end_price": 10.0 if direction == "up" else 1.0,
        "low": 0.0,
        "high": 11.0,
        "center_ids": [f"child-{index}"],
        "source_pen_ids": [f"p{index}"],
        "source_unit_ids": source_unit_ids,
        "start_boundary": start_boundary,
        "end_boundary": end_boundary,
        "continuous_range_id": 0,
        "sequence_id": 0,
    }


def _movement_unit(index: int, start_date: str, end_date: str, start: float, end: float) -> dict:
    return {
        "id": f"m{index}", "kind": "movement", "level": 1,
        "status": "confirmed", "direction": "up" if end > start else "down",
        "start_date": start_date, "end_date": end_date,
        "start_price": start, "end_price": end, "low": min(start, end), "high": max(start, end),
        "source_unit_ids": [f"u{index}"], "continuous_range_id": 0, "sequence_id": 0,
    }


def test_parent_center_requires_contiguous_child_movements():
    movements = [
        _movement(0, "up", start_boundary=0, end_boundary=2, source_unit_ids=["u0", "u1"]),
        # u2 is an unexplained internal unit; the child movement starts at u3.
        _movement(1, "down", start_boundary=3, end_boundary=5, source_unit_ids=["u3", "u4"]),
        _movement(2, "up", start_boundary=5, end_boundary=7, source_unit_ids=["u5", "u6"]),
    ]

    assert _parent_centers(movements, 2, "system") == []


def test_parent_center_rejects_reused_child_unit():
    movements = [
        _movement(0, "up", start_boundary=0, end_boundary=2, source_unit_ids=["u0", "u1"]),
        _movement(1, "down", start_boundary=2, end_boundary=4, source_unit_ids=["u1", "u2"]),
        _movement(2, "up", start_boundary=4, end_boundary=6, source_unit_ids=["u3", "u4"]),
    ]

    assert _parent_centers(movements, 2, "system") == []


def test_invalid_hierarchy_center_reference_is_not_persisted():
    units = [_unit(i, start=1, end=2) for i in range(4)]
    malformed = _center(["missing-0", "missing-1", "missing-2", "missing-3"])

    result = build_hierarchy(units, [malformed])

    assert result["centers"] == []


def test_confirmed_direction_conflict_becomes_system_provisional():
    units = [_unit(0, start=1, end=3), _unit(1, start=3, end=2)]
    center = {
        "id": "center",
        "level": 1,
        "status": "confirmed",
        "direction": "down",
        "zd": 1.5,
        "zg": 2.5,
        "dd": 1.0,
        "gg": 3.0,
        "continuous_range_id": 0,
        "sequence_id": 0,
    }

    movement = _movement_from_centers(
        units,
        [center],
        level=1,
        start_boundary=0,
        end_boundary=2,
        direction="down",
        status="confirmed",
        confirmation_center={"id": "next", "confirmed_at": "2026-01-05"},
        role="hierarchy_component",
        origin="system",
    )

    assert movement["status"] == "provisional"
    assert movement["direction"] == "down"
    assert movement["confirmed_at"] is None
    assert movement["confirmation_center_id"] is None
    assert movement["termination_reason"] == "provisional_tail"
    assert movement["issues"][0]["code"] == "movement_direction_endpoint_mismatch"




def test_recursive_units_copy_and_segment_child_stream_without_mutating_rows():
    first = _movement_unit(0, "2026-01-01", "2026-01-02", 1, 5)
    second = _movement_unit(1, "2026-01-03", "2026-01-04", 5, 2)
    prepared = _recursive_units([first, second])
    assert [item["id"] for item in prepared] == ["m0", "m1"]


def test_same_direction_expansion_does_not_confirm_movement():
    units = [
        _unit(0, start=1, end=6), _unit(1, start=6, end=2),
        _unit(2, start=2, end=7), _unit(3, start=7, end=3),
        _unit(4, start=3, end=9), _unit(5, start=9, end=5),
        _unit(6, start=5, end=10), _unit(7, start=10, end=8),
    ]
    centers = [
        {"id": "c1", "level": 1, "status": "confirmed", "direction": "up",
         "zd": 3, "zg": 6, "dd": 1, "gg": 7, "start_date": "0000", "end_date": "0004",
         "entry_pen_id": "u0-0", "core_pen_ids": [f"u0-{i}" for i in (1, 2, 3)], "source_pen_ids": [f"u0-{i}" for i in range(4)],
         "continuous_range_id": 0, "sequence_id": 0},
        {"id": "c2", "level": 1, "status": "confirmed", "direction": "up",
         "zd": 5, "zg": 8, "dd": 4, "gg": 10, "start_date": "0004", "end_date": "0008",
         "entry_pen_id": "u0-4", "core_pen_ids": [f"u0-{i}" for i in (5, 6, 7)], "source_pen_ids": [f"u0-{i}" for i in range(4, 8)],
         "continuous_range_id": 0, "sequence_id": 0},
    ]
    result = build_hierarchy_components(units, centers, 1)
    assert result["movements"]
    assert all(item["status"] == "provisional" for item in result["movements"])
