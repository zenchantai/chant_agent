from copy import deepcopy
import hashlib
import json

import pytest

from app import chan_structure
from app.chan_structure import build_structure_hierarchy, validate_structure
from tests.chan_fixtures import pens_from_prices


EXPANSION_PRICES = [1, 10, 6, 15, 8, 20, 12, 25, 18, 30, 26, 35]
EXTENSION_PRICES = [1, 10, 6, 15, 8, 20, 9, 18, 7, 17, 11, 23, 19, 28, 24, 32]
EMPTY_REFERENCE_GROUPS = (
    "promotion_candidates", "promotion_candidate_revisions", "segment_proofs", "segment_proof_revisions",
)


@pytest.mark.parametrize("mirror", [False, True])
def test_reference_keeps_l1_centers_and_l2_mode_records_unresolved_expansion(mirror):
    prices = [40 - price for price in EXPANSION_PRICES] if mirror else EXPANSION_PRICES
    pens = pens_from_prices(prices)
    full = build_structure_hierarchy(pens, calculation_profile="pen_centers_l2")
    reference = build_structure_hierarchy(pens, calculation_profile="pen_centers_only")
    assert [center["level"] for center in full["centers"]] == [1, 1, 2]
    assert any(r.get("expansion_status") == "confirmed" and r.get("boundary_status") in {"dynamic", "fixed", "unresolved"} for r in full["relations"])
    assert [center["level"] for center in reference["centers"]] == [1, 1]
    assert reference["levels"] == [1]
    assert reference["max_level"] == 1
    assert all(reference[group] == [] for group in EMPTY_REFERENCE_GROUPS)
    assert any(relation["relation_type"].startswith("expansion_") for relation in reference["relations"])
    assert not {"movements", "movement_revisions", "points", "point_revisions"} & set(reference)
    assert all(center["level"] == 1 and center["promotion_confirmed_at"] is None
               and "expansion_envelope_overlap" not in center["formation_modes"]
               and not center.get("absorbed_into_family_id")
               for center in reference["center_revisions"])
    assert validate_structure({"pens": pens, **reference}) == []


def test_reference_never_invokes_points_movements_or_promotion(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("reference mode called L2 promotion")

    with monkeypatch.context() as patch:
        patch.setattr(chan_structure, "build_promotion_candidates", forbidden)
        result = build_structure_hierarchy(
            pens_from_prices(EXPANSION_PRICES), calculation_profile="pen_centers_only",
        )
    assert len(result["centers"]) == 2
    assert result["centers"][0]["retest_unit_ids"] == ["p5"]
    assert any(component["role"] == "retest" for component in result["components"])
    assert result["relations"]


def test_reference_preserves_extension_return_and_retest_boundary():
    pens = pens_from_prices(EXTENSION_PRICES)
    result = build_structure_hierarchy(pens, calculation_profile="pen_centers_only")
    first, = result["centers"]
    assert first["core_unit_ids"] == ["p1", "p2", "p3"]
    assert first["extension_unit_ids"] == ["p5", "p7"]
    assert (first["zd"], first["zg"], first["fixed_zd"], first["fixed_zg"]) == (8, 10, 8, 10)
    assert first["end_date"] == "2026-01-09"
    assert first["departure_unit_ids"] == ["p8", "p9"]
    assert first["retest_unit_ids"] == ["p9"]
    assert first["status"] == "broken"
    # No opposite-direction triple may become the successor core.
    assert validate_structure({"pens": pens, **result}) == []


def test_reference_retest_breaks_center_without_creating_a_point():
    pens = pens_from_prices([1, 10, 6, 15, 8, 20, 16])
    result = build_structure_hierarchy(pens, calculation_profile="pen_centers_only")
    center = result["centers"][0]
    assert center["status"] == "broken"
    assert center["retest_unit_ids"] == ["p5"]
    assert center["retest_component_id"] in {item["id"] for item in result["components"]}
    assert "points" not in result and "point_revisions" not in result


def test_reference_append_only_revisions_keep_fixed_core_and_evidence():
    pens = pens_from_prices(EXTENSION_PRICES)
    previous = {}
    for count in range(4, len(pens) + 1):
        result = build_structure_hierarchy(pens[:count], calculation_profile="pen_centers_only")
        revisions = {center["id"]: center for center in result["center_revisions"]}
        for identifier, old in previous.items():
            assert identifier in revisions
            for field in ("core_unit_ids", "z_unit_ids", "zd", "zg", "dd", "gg", "formed_at", "evidence"):
                assert old[field] == revisions[identifier][field], (count, identifier, field)
        previous = revisions
        assert validate_structure({"pens": pens[:count], **result}) == []


@pytest.mark.parametrize("boundary_field,boundary_value", [
    ("sequence_id", 1), ("continuous_range_id", 1), ("structure_sequence_id", "isolated"),
])
def test_reference_never_forms_a_center_across_a_sequence_boundary(boundary_field, boundary_value):
    pens = pens_from_prices(EXPANSION_PRICES)
    for pen in pens[4:]:
        pen[boundary_field] = boundary_value
    result = build_structure_hierarchy(pens, calculation_profile="pen_centers_only")
    pen_map = {pen["id"]: pen for pen in pens}
    for center in result["centers"]:
        assert len({pen_map[identifier][boundary_field] for identifier in center["context_unit_ids"]}) == 1
    assert validate_structure({"pens": pens, **result}) == []


def test_reference_ignores_provisional_pen_without_mutating_input():
    pens = pens_from_prices([1, 10, 6, 15, 8])
    pens[-1]["status"] = "provisional"
    original = deepcopy(pens)
    result = build_structure_hierarchy(pens, calculation_profile="pen_centers_only")
    assert len(result["centers"]) == 1
    assert result["centers"][0]["formation_stage"] == "origin_overlap"
    assert "recursive_eligible" not in result["centers"][0]
    assert result["max_level"] == 1
    assert pens == original


# Directional semantics intentionally supersede the pre-v27 hashes.
@pytest.mark.parametrize("prices", [EXPANSION_PRICES, EXTENSION_PRICES,
    [1,10,6,15,8,20,16,25,18,30], [1,10,6,15,8,20,15,25,18,30]])
def test_l2_profile_is_deterministic_and_keeps_input_unchanged(prices):
    pens = pens_from_prices(prices)
    before = deepcopy(pens)
    result = build_structure_hierarchy(pens, calculation_profile="pen_centers_l2")
    assert result == build_structure_hierarchy(pens)
    assert validate_structure({"pens": pens, **result}) == []
    assert pens == before


def test_empty_reference_has_compatible_output_shape_and_rejects_unknown_profile():
    full = build_structure_hierarchy([])
    reference = build_structure_hierarchy([], calculation_profile="pen_centers_only")
    assert reference == full
    with pytest.raises(ValueError, match="不支持的结构计算策略"):
        build_structure_hierarchy([], calculation_profile="unknown")
