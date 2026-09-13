"""Regression tests for hierarchy input boundaries.

These cases exercise the structural (rather than chart) decomposition API with
hand-authored units.  They deliberately model malformed references that can
otherwise be hidden by date-based fallbacks.
"""

from app.hierarchy import (
    _movement_from_centers,
    _parent_centers,
    _recursive_units,
    build_hierarchy,
    decompose_hierarchy_level,
    decompose_same_level,
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

    result = decompose_hierarchy_level(units, [center], level=1)

    assert any(item["code"] == "invalid_hierarchy_center_reference" for item in result["meta"]["issues"])
    assert result["centers"] == []
    assert result["movements"] == []


def test_non_contiguous_or_missing_explicit_reference_is_rejected():
    units = [_unit(i) for i in range(4)]
    center = _center(["u0-0", "u0-2", "missing", "u0-3"])

    result = decompose_hierarchy_level(units, [center], level=1)

    assert any(item["code"] == "invalid_hierarchy_center_reference" for item in result["meta"]["issues"])
    assert result["centers"] == []
    assert result["movements"] == []


def test_overlapping_structural_center_spans_stop_the_level():
    units = [_unit(i, start=1 + i, end=2 + i) for i in range(6)]
    first = _center([f"u0-{i}" for i in range(4)])
    second = _center([f"u0-{i}" for i in range(2, 6)])
    second["id"] = "c2"
    # Make the price cores strictly separated so this case specifically
    # exercises overlapping *unit spans*, rather than the price relation
    # guard tested elsewhere.
    second.update({"zd": 5.0, "zg": 6.0, "dd": 4.0, "gg": 7.0})

    result = decompose_hierarchy_level(units, [first, second], level=1)

    assert any("overlap" in item["code"] for item in result["meta"]["issues"])


def test_hierarchy_level_orders_units_before_building_boundaries():
    chronological = [_unit(0, start=1, end=6), _unit(1, start=6, end=2), _unit(2, start=2, end=5)]
    units = [chronological[2], chronological[0], chronological[1]]
    center = _center(["u0-0", "u0-1", "u0-2"])

    result = decompose_hierarchy_level(units, [center], level=1)

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


def test_hierarchy_component_issues_are_exposed_in_decomposition_metadata():
    units = [_unit(i, start=1, end=2) for i in range(4)]
    malformed = _center(["missing-0", "missing-1", "missing-2", "missing-3"])

    result = build_hierarchy(units, [malformed])

    assert any(
        item["code"] == "invalid_hierarchy_center_reference"
        for item in result["decompositions"]["1"].get("issues", [])
    )


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
    assert movement["direction"] == "up"
    assert movement["confirmed_at"] is None
    assert movement["confirmation_center_id"] is None
    assert movement["termination_reason"] == "direction_conflict"
    assert movement["issues"][0]["code"] == "movement_direction_endpoint_mismatch"


def test_boundary_uses_earlier_extreme_inside_current_center():
    # The first center's highest point is u1's end.  A search beginning only
    # at the final center unit would miss it and produce a downward endpoint.
    values = [(1, 6), (6, 2), (2, 5), (5, 5.5), (5.5, 5.2), (5.2, 5.4)]
    result = decompose_same_level(
        [_unit(i, start=start, end=end) for i, (start, end) in enumerate(values)],
        level=1,
    )

    first, second = result["movements"]
    assert first["status"] == "confirmed"
    assert first["direction"] == "up"
    assert first["end_price"] == 6
    assert first["end_date"] == second["start_date"]
    assert first["end_price"] == second["start_price"]
    assert not first.get("issues")


def _movement_unit(index: int, start_date: str, end_date: str, start: float, end: float) -> dict:
    return {
        "id": f"m{index}", "kind": "movement", "level": 1,
        "role": "hierarchy_component", "status": "confirmed",
        "start_date": start_date, "end_date": end_date,
        "start_price": start, "end_price": end,
        "low": min(start, end), "high": max(start, end),
        "direction": "up" if end > start else "down",
        "confirmed_at": end_date, "continuous_range_id": 0, "sequence_id": 0,
    }


def test_same_level_does_not_bridge_a_gap_between_confirmed_child_movements():
    # The second triple has a date/price hole after m2.  It must become a new
    # derived sequence segment instead of producing one arrow across the hole.
    units = [
        _movement_unit(0, "2026-01-01", "2026-01-02", 1, 5),
        _movement_unit(1, "2026-01-02", "2026-01-03", 5, 2),
        _movement_unit(2, "2026-01-03", "2026-01-04", 2, 4),
        _movement_unit(3, "2026-01-06", "2026-01-07", 10, 14),
        _movement_unit(4, "2026-01-07", "2026-01-08", 14, 11),
        _movement_unit(5, "2026-01-08", "2026-01-09", 11, 13),
    ]
    result = decompose_same_level(units, level=2)
    movements = result["movements"]
    assert len(movements) == 2
    assert {movement["sequence_id"] for movement in movements} == {0, -1_000_001}
    assert all(len(movement["source_unit_ids"]) == 3 for movement in movements)
    assert any(item["code"] == "non_contiguous_input" for item in result["meta"]["issues"])
    assert movements[0]["end_date"] != movements[1]["start_date"]


def test_recursive_units_copy_and_segment_child_stream_without_mutating_rows():
    first = _movement_unit(0, "2026-01-01", "2026-01-02", 1, 5)
    second = _movement_unit(1, "2026-01-03", "2026-01-04", 5, 2)
    prepared = _recursive_units([first, second])
    assert [item["id"] for item in prepared] == ["m0", "m1"]
    assert prepared[0]["sequence_id"] == 0
    assert prepared[1]["sequence_id"] == -1_000_001
    assert first["sequence_id"] == second["sequence_id"] == 0


def test_flat_provisional_tail_uses_candidate_extreme_as_real_endpoint():
    units = [
        {"id": "u0", "kind": "atomic_pen", "start_date": "2026-02-01",
         "end_date": "2026-02-02", "start_price": 10, "end_price": 8,
         "low": 8, "high": 10, "direction": "down", "status": "confirmed"},
        {"id": "u1", "kind": "atomic_pen", "start_date": "2026-02-02",
         "end_date": "2026-02-03", "start_price": 8, "end_price": 10,
         "low": 8, "high": 10, "direction": "up", "status": "confirmed"},
    ]
    center = {"id": "center", "level": 1, "continuous_range_id": 0, "sequence_id": 0}
    movement = _movement_from_centers(
        units, [center], 1, 0, 2, "down", "provisional", None,
        "hierarchy_component", "system", source_end_boundary=2,
        candidate_extreme={"boundary": 1, "trade_date": "2026-02-02", "price": 8},
    )
    assert movement["direction"] == "down"
    assert movement["start_price"] > movement["end_price"]
    assert movement["end_date"] == "2026-02-02"
    assert movement["tail_end_date"] == "2026-02-03"


def test_build_hierarchy_ignores_persisted_same_level_centers_as_l1_seeds():
    # Operation-view centers have no entry direction and are persisted beside
    # raw L1 centers.  Reloading a mixed collection must not feed them back
    # into the hierarchy engine.
    pens = [_unit(i, start=1, end=2) for i in range(4)]
    raw = _center([pen["id"] for pen in pens])
    raw["id"] = "raw"
    evidence = {
        "id": "same-level", "kind": "center", "role": "same_level", "level": 1,
        "status": "confirmed", "start_date": raw["start_date"], "end_date": raw["end_date"],
        "zd": raw["zd"], "zg": raw["zg"], "dd": raw["dd"], "gg": raw["gg"],
        "core_unit_ids": [pen["id"] for pen in pens[1:]],
    }
    result = build_hierarchy(pens, [raw, evidence])
    assert [item["id"] for item in result["centers"] if item.get("role") == "hierarchy" and item["level"] == 1] == ["raw"]
