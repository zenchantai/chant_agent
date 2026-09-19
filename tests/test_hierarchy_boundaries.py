from copy import deepcopy

from app.chan_structure import build_level_centers, movement_units
from tests.chan_fixtures import pens_from_prices


def _movement(index: int, start: float, end: float, *, status: str = "confirmed", eligible: bool = True):
    return {
        "id": f"m{index}", "kind": "movement", "level": 1, "direction": "up" if end > start else "down",
        "status": status, "classification": "trend", "recursive_eligible": eligible,
        "start_date": f"2026-01-{index + 1:02d}", "end_date": f"2026-01-{index + 2:02d}",
        "start_price": start, "end_price": end, "price_envelope_low": min(start, end),
        "price_envelope_high": max(start, end), "continuous_range_id": 0, "sequence_id": 0,
        "structure_sequence_id": "s", "source_pen_ids": [f"p{index}"], "center_levels": [1],
        "child_movement_ids": [], "confirmed_at": f"2026-01-{index + 2:02d}",
    }


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


def test_l2_input_only_contains_confirmed_recursive_eligible_l1_movements():
    values = [
        _movement(0, 1, 10),
        _movement(1, 10, 6, status="provisional"),
        _movement(2, 6, 15, eligible=False),
        _movement(3, 15, 8),
    ]
    assert [item["id"] for item in movement_units(values, 1)] == ["m0", "m3"]


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
    assert centers == []
    assert components == []
    assert issues == []
