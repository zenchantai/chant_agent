from app.chan_structure import (
    _promote_open_movements,
    build_level_centers,
    build_structure_hierarchy,
    classify_center_relation,
    validate_structure,
)
from tests.chan_fixtures import pens_from_prices


def test_core_units_are_owned_once_and_entry_is_context_only():
    centers, _, _ = build_level_centers(
        pens_from_prices([1, 10, 6, 15, 8, 20, 16, 25, 21, 30, 26, 35]), 1,
    )
    owners = {}
    for center in centers:
        assert not set(center["entry_unit_ids"]) & set(center["owned_unit_ids"])
        for unit_id in center["core_unit_ids"]:
            assert unit_id not in owners
            owners[unit_id] = center["family_id"]


def test_relation_uses_complete_fluctuation_system_not_core_only():
    left = {
        "level": 1, "continuous_range_id": 0, "sequence_id": 0, "structure_sequence_id": "s",
        "zd": 2, "zg": 4, "fixed_zd": 2, "fixed_zg": 4, "dd": 1, "gg": 5,
    }
    newborn = {**left, "zd": 7, "zg": 9, "fixed_zd": 7, "fixed_zg": 9, "dd": 6, "gg": 10}
    expansion = {**left, "zd": 7, "zg": 9, "fixed_zd": 7, "fixed_zg": 9, "dd": 4, "gg": 10}
    assert classify_center_relation(left, newborn) == "newborn_up"
    assert classify_center_relation(left, expansion) == "expansion_up"


def test_contact_confirms_relation_without_inventing_parent_geometry():
    result = build_structure_hierarchy(pens_from_prices([1,10,6,15,8,20,12,25,18,30,26,35]))
    assert [c["level"] for c in result["centers"]] == [1, 1]
    relation = next(r for r in result["relations"] if r["relation_type"] == "expansion_up")
    assert relation["status"] == relation["expansion_status"] == "confirmed"
    assert relation["boundary_status"] == "unresolved"
    assert relation["boundary_missing_evidence"] == ["three_subordinate_movement_boundaries"]
    assert not any(c["level"] > 1 for c in result["center_revisions"])


def test_unresolved_expansion_does_not_promote_open_movement():
    result = build_structure_hierarchy(pens_from_prices([1,10,6,15,8,20,12,25,18,30,26,35]))
    assert all(m["level"] == 1 for m in result["movements"])
    assert all(not m["recursive_eligible"] for m in result["movements"] if m["status"] != "confirmed")


def test_multiple_promoted_centers_create_one_movement_revision():
    movement = {
        "id": "movement-family-1:r1",
        "family_id": "movement-family-1",
        "revision_no": 1,
        "level": 1,
        "status": "provisional",
        "active": True,
        "source_pen_ids": ["p1", "p2", "p3", "p4"],
        "child_movement_ids": [],
        "evidence": {},
    }
    centers = [
        {
            "id": "center-family-1:r2", "family_id": "center-family-1", "level": 2,
            "start_date": "2026-01-01", "end_date": "2026-01-02", "source_pen_ids": ["p1", "p2"],
            "zd": 4, "zg": 6, "dd": 1, "gg": 9, "fluctuation_dd": 1, "fluctuation_gg": 9,
        },
        {
            "id": "center-family-2:r2", "family_id": "center-family-2", "level": 2,
            "start_date": "2026-01-03", "end_date": "2026-01-04", "source_pen_ids": ["p3", "p4"],
            "zd": 7, "zg": 10, "dd": 5, "gg": 12, "fluctuation_dd": 5, "fluctuation_gg": 12,
        },
    ]
    revisions = _promote_open_movements([movement], centers)
    assert len(revisions) == 1
    assert revisions[0]["id"] == "movement-family-1:r2"
    assert revisions[0]["classification"] is None
    assert revisions[0]["status"] == "undetermined"
    assert not revisions[0]["child_movement_ids"]
    assert revisions[0]["center_family_ids"] == ["center-family-1", "center-family-2"]
    assert revisions[0]["evidence"]["promoted_with_center_revision_ids"] == [
        "center-family-1:r2", "center-family-2:r2",
    ]


def test_full_hierarchy_satisfies_reference_and_ownership_invariants():
    pens = pens_from_prices([0, 10, 2, 9, 3, 12, 10, 15, 13, 18, 16])
    result = build_structure_hierarchy(pens, [], [])
    assert validate_structure({"structure": {"pens": pens, **result}}) == []
    active_movements = [item for item in result["movement_revisions"] if item.get("active")]
    owned = set()
    for movement in active_movements:
        assert not owned.intersection(movement["source_unit_ids"])
        owned.update(movement["source_unit_ids"])
