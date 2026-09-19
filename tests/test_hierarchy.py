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


def test_expansion_promotes_existing_family_instead_of_creating_parallel_parent():
    result = build_structure_hierarchy(
        pens_from_prices([1, 10, 6, 15, 8, 20, 12, 25, 21, 30, 26, 35]), [], [],
    )
    revisions = result["center_revisions"]
    active = result["centers"]
    assert len(active) == 1
    assert active[0]["level"] == 2
    family_revisions = [item for item in revisions if item["family_id"] == active[0]["family_id"]]
    assert [item["revision_no"] for item in family_revisions] == list(range(1, len(family_revisions) + 1))
    assert family_revisions[0]["active"] is False
    assert family_revisions[-1]["active"] is True
    absorbed = [item for item in revisions if item.get("absorbed_into_family_id")]
    assert len({item["family_id"] for item in absorbed}) == 1
    assert absorbed[0]["absorbed_into_family_id"] == active[0]["family_id"]
    assert {item["relation_type"] for item in result["relations"]} >= {"expansion_up", "promoted_into"}


def test_open_movement_is_promoted_with_center_family():
    result = build_structure_hierarchy(
        pens_from_prices([1, 10, 6, 15, 8, 20, 12, 25, 21, 30, 26, 35]), [], [],
    )
    assert len(result["movements"]) == 1
    movement = result["movements"][0]
    assert movement["level"] == 2
    assert movement["status"] == "provisional"
    assert movement["classification"] == "consolidation"
    assert movement["center_family_ids"] == [result["centers"][0]["family_id"]]


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
