from copy import deepcopy
import hashlib
import json

import pytest

from app import chan_structure
from app.chan_structure import build_structure_hierarchy, validate_structure
from tests.chan_fixtures import pens_from_prices


EXPANSION_PRICES = [1, 10, 6, 15, 8, 20, 12, 25, 21, 30, 26, 35]
EXTENSION_PRICES = [1, 10, 6, 15, 8, 20, 9, 18, 7, 17, 11, 23, 19, 28, 24, 32]
EMPTY_REFERENCE_GROUPS = (
    "movements", "movement_revisions", "points", "point_revisions", "relations",
)


@pytest.mark.parametrize("mirror", [False, True])
def test_reference_keeps_two_l1_centers_where_full_mode_promotes(mirror):
    prices = [40 - price for price in EXPANSION_PRICES] if mirror else EXPANSION_PRICES
    pens = pens_from_prices(prices)
    full = build_structure_hierarchy(pens)
    reference = build_structure_hierarchy(pens, calculation_profile="pen_centers_only")
    assert [center["level"] for center in full["centers"]] == [2]
    assert [center["level"] for center in reference["centers"]] == [1, 1]
    assert reference["levels"] == [1]
    assert reference["max_level"] == 1
    assert all(reference[group] == [] for group in EMPTY_REFERENCE_GROUPS)
    assert all(center["level"] == 1 and center["promotion_confirmed_at"] is None
               and "expansion_envelope_overlap" not in center["formation_modes"]
               and not center.get("absorbed_into_family_id")
               for center in reference["center_revisions"])
    assert validate_structure({"pens": pens, **reference}) == []


def test_reference_never_invokes_points_movements_relations_or_promotion(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("reference mode called a full-mode calculation")

    with monkeypatch.context() as patch:
        for name in (
            "build_relations", "_promote_expansions", "_promote_open_movements",
            "build_structural_points", "_point_revision", "_third_point", "_third_point_tail",
            "build_movements", "movement_units", "merge_formation_paths",
            "_structurally_weaker", "_macd_evidence",
        ):
            patch.setattr(chan_structure, name, forbidden)
        result = build_structure_hierarchy(
            pens_from_prices(EXPANSION_PRICES), calculation_profile="pen_centers_only",
        )
    assert len(result["centers"]) == 2
    assert result["centers"][0]["retest_unit_ids"] == ["p5"]
    assert any(component["role"] == "retest" for component in result["components"])


def test_reference_preserves_extension_return_and_retest_boundary():
    pens = pens_from_prices(EXTENSION_PRICES)
    result = build_structure_hierarchy(pens, calculation_profile="pen_centers_only")
    first, second = result["centers"]
    assert first["core_unit_ids"] == ["p1", "p2", "p3"]
    assert first["extension_unit_ids"] == ["p5", "p7"]
    assert (first["zd"], first["zg"], first["fixed_zd"], first["fixed_zg"]) == (8, 10, 8, 10)
    assert first["end_date"] == "2026-01-10"
    assert first["departure_unit_ids"] == ["p9", "p10", "p11"]
    assert first["retest_unit_ids"] == ["p11"]
    assert first["status"] == "closed"
    assert second["core_unit_ids"] == ["p12", "p13", "p14"]
    assert validate_structure({"pens": pens, **result}) == []


def test_reference_retest_breaks_center_without_creating_a_point():
    pens = pens_from_prices([1, 10, 6, 15, 8, 20, 16])
    result = build_structure_hierarchy(pens, calculation_profile="pen_centers_only")
    center = result["centers"][0]
    assert center["status"] == "broken"
    assert center["retest_unit_ids"] == ["p5"]
    assert center["retest_component_id"] in {item["id"] for item in result["components"]}
    assert result["points"] == result["point_revisions"] == []


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
    assert result["centers"] == []
    assert result["max_level"] == 0
    assert pens == original


# Frozen from the pre-v27 calculator using these exact pen inputs. Only the
# declared hierarchy version is excluded; all structure content must agree.
@pytest.mark.parametrize("prices,expected", [
    (EXPANSION_PRICES, "1fddc2e4ebb12627cdb94e0501ceaacfd92abe09cd16be84520b1ce5c7c0bdff"),
    (EXTENSION_PRICES, "20da1b2738c043be3c133b051e1daa9de2bcc9937563d1d72c26ecc30b17d21d"),
    ([1, 10, 6, 15, 8, 20, 16, 25, 21, 30, 26, 35], "cae2a21fc6867b37d72200dcc00cea8a17a0b2adfb15e61acc1a71019923a481"),
    ([1, 10, 6, 15, 8, 20, 15, 25, 21, 30, 26, 35], "aa817bec8421ec8adad3cc0e9b850ddf1ad2822442393b2da2bfef759e342544"),
    ([1, 10, 6, 15, 8, 20, 16], "f948179191a50d341f1243bbdaf4afb307212dc1098994d544d95cd8b00fd8a9"),
])
def test_full_profile_preserves_pre_v27_structure_content(prices, expected):
    pens = pens_from_prices(prices)
    result = build_structure_hierarchy(pens, calculation_profile="full")
    assert result == build_structure_hierarchy(pens)
    result.pop("hierarchy_version")
    payload = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(payload.encode()).hexdigest() == expected


def test_empty_reference_has_compatible_output_shape_and_rejects_unknown_profile():
    full = build_structure_hierarchy([])
    reference = build_structure_hierarchy([], calculation_profile="pen_centers_only")
    assert reference == full
    with pytest.raises(ValueError, match="不支持的结构计算策略"):
        build_structure_hierarchy([], calculation_profile="unknown")
