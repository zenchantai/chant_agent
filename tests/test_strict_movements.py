from app.chan_structure import _structurally_weaker, build_structure_hierarchy
from tests.chan_fixtures import pens_from_prices


def test_strength_vector_prioritizes_internal_structure_rank():
    before = {
        "max_internal_center_level": 2,
        "confirmed_child_movement_count": 3,
        "amplitude": 5,
        "average_slope": 1,
    }
    lower_rank = {
        "max_internal_center_level": 1,
        "confirmed_child_movement_count": 20,
        "amplitude": 50,
        "average_slope": 10,
    }
    stronger_rank = {**before, "max_internal_center_level": 3, "amplitude": 1, "average_slope": 0.1}
    assert _structurally_weaker(before, lower_rank) == (True, "lower_internal_structure_rank")
    assert _structurally_weaker(before, stronger_rank) == (False, "higher_internal_structure_rank")


def test_equal_rank_requires_both_amplitude_and_slope_to_weaken():
    before = {
        "max_internal_center_level": 1,
        "confirmed_child_movement_count": 0,
        "amplitude": 10,
        "average_slope": 2,
    }
    assert _structurally_weaker(before, {**before, "amplitude": 9, "average_slope": 1}) == (
        True, "amplitude_and_slope_weaker",
    )
    assert _structurally_weaker(before, {**before, "amplitude": 11, "average_slope": 3}) == (
        False, "amplitude_and_slope_stronger",
    )
    assert _structurally_weaker(before, {**before, "amplitude": 9, "average_slope": 3}) == (
        None, "mixed_price_strength",
    )


def test_third_buy_requires_complete_departure_and_retest_outside_core():
    result = build_structure_hierarchy(pens_from_prices([1, 10, 6, 15, 8, 20, 16]), [], [])
    third = next(point for point in result["points"] if point["point_type"] == "third_buy")
    center = result["centers"][0]
    assert third["status"] == "confirmed"
    assert third["point_date"] == "2026-01-07"
    assert third["point_price"] == 16
    assert center["status"] == "broken"
    assert center["retest_unit_ids"]


def test_single_center_only_produces_consolidation_divergence_not_first_point():
    result = build_structure_hierarchy(pens_from_prices([1, 10, 6, 15, 8, 20, 16]), [], [])
    types = {point["point_type"] for point in result["point_revisions"]}
    assert "consolidation_divergence_sell" in types
    assert "first_sell" not in types
    assert "first_buy" not in types


def test_latest_movement_remains_provisional_without_confirmed_boundary_point():
    result = build_structure_hierarchy(pens_from_prices([1, 10, 6, 15, 8]), [], [])
    assert len(result["movements"]) == 1
    movement = result["movements"][0]
    assert movement["status"] == "provisional"
    assert movement["confirmed_at"] is None
    assert movement["recursive_eligible"] is False
    assert movement["termination_reason"] == "provisional_tail"
