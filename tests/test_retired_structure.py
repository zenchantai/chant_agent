import pytest

from app import chan_structure
from app.chan_structure import build_structure_hierarchy, validate_structure
from tests.chan_fixtures import pens_from_prices


@pytest.mark.parametrize("profile,max_level", [
    ("pen_centers_l2", 2), ("pen_centers_only", 1),
])
def test_retired_groups_are_not_emitted_and_hierarchy_stops_at_allowed_level(profile, max_level):
    pens = pens_from_prices([1, 10, 6, 15, 8, 20, 12, 25, 18, 30, 26, 35])
    result = build_structure_hierarchy(pens, calculation_profile=profile)
    assert result["max_level"] <= max_level
    assert all(center["level"] <= max_level for center in result["center_revisions"])
    assert not {"movements", "movement_revisions", "movement_levels", "points", "point_revisions"} & set(result)
    assert validate_structure({"pens": pens, **result}) == []


def test_retired_movement_or_point_builders_are_absent():
    for name in ("movement_units", "build_movements", "build_structural_points"):
        assert not hasattr(chan_structure, name)
    pens = pens_from_prices([1, 10, 6, 15, 8, 20, 12, 25, 18, 30, 26, 35])
    for profile in ("pen_centers_l2", "pen_centers_only"):
        result = build_structure_hierarchy(pens, calculation_profile=profile)
        assert result["centers"]


def test_l1_center_retest_is_preserved_without_a_structural_point():
    result = build_structure_hierarchy(pens_from_prices([1, 10, 6, 15, 8, 20, 16]))
    center = result["centers"][0]
    assert center["status"] == "broken"
    assert center["retest_unit_ids"]
    assert "points" not in result
