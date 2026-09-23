from copy import deepcopy

from app.chan_structure import build_level_centers
from tests.chan_fixtures import pens_from_prices


def test_sequence_boundary_prevents_cross_boundary_center():
    pens = pens_from_prices([1, 10, 6, 15, 8, 20, 16, 25, 21])
    for pen in pens[4:]:
        pen["sequence_id"] = 1
        pen["structure_sequence_id"] = "range-0-sequence-1"
    centers, _, _ = build_level_centers(pens, 1)
    for center in centers:
        ids = center["entry_unit_ids"] + center["owned_unit_ids"] + center["departure_unit_ids"]
        sequence_ids = {pens[int(identifier[1:])]["sequence_id"] for identifier in ids}
        assert len(sequence_ids) == 1


def test_prefix_stability_keeps_fixed_core_when_tail_is_appended():
    prefix = pens_from_prices([1, 10, 6, 15, 8])
    first = build_level_centers(deepcopy(prefix), 1)[0][0]
    extended = pens_from_prices([1, 10, 6, 15, 8, 20, 16])
    later = build_level_centers(extended, 1)[0][0]
    assert first["family_id"] == later["family_id"]
    assert first["core_unit_ids"] == later["core_unit_ids"]
    assert (first["fixed_zd"], first["fixed_zg"]) == (later["fixed_zd"], later["fixed_zg"])


def test_no_valid_entry_keeps_units_unassigned_instead_of_forcing_center():
    centers, components, issues = build_level_centers(pens_from_prices([1, 5, 3, 7]), 1)
    assert len(centers) == 1
    assert centers[0]["formation_stage"] == "origin_overlap"
    assert not centers[0]["recursive_eligible"]
    assert components == []
    assert issues == []
