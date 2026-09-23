from app.chan_structure import (
    atomic_pen_units,
    build_level_centers,
    build_promotion_candidates,
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
    assert [c["level"] for c in result["centers"]] == [1, 1, 2]
    relation = next(r for r in result["relations"] if r["relation_type"] == "expansion_up")
    assert relation["status"] == relation["expansion_status"] == "confirmed"
    assert relation["boundary_status"] in {"dynamic", "fixed", "unresolved"}
    assert relation["boundary_missing_evidence"] in ([], ["three_segment_proofs_missing"])
    candidate = next(item for item in result["promotion_candidates"] if item["candidate_source"] == "expansion_decomposition")
    assert candidate["status"] in {"dynamic", "unresolved"}
    assert candidate["source_entity_ids"][0] == relation["id"]
    assert any(c["level"] == 2 and c.get("boundary_status") == "dynamic" for c in result["center_revisions"])


def test_expansion_contact_below_nine_units_still_persists_candidate():
    result = build_structure_hierarchy(pens_from_prices([1, 10, 6, 15, 8, 20, 12, 25, 18]))
    candidate = result["promotion_candidates"][0]
    assert candidate["candidate_source"] == "expansion_decomposition"
    assert len(candidate["search_unit_ids"]) == 7
    assert candidate["required_unit_ids"] == candidate["search_unit_ids"]
    assert candidate["missing_evidence"] == [{"code": "nine_owned_units", "actual_count": 7}]


def test_extension_candidate_freezes_first_nine_owned_units_only():
    units = atomic_pen_units(pens_from_prices([0, 10, 2, 11, 3, 12, 4, 13, 5, 14, 6, 15, 7, 16]))
    base = {
        "family_id": "child-family", "level": 1, "formation_stage": "directional",
        "formed_at": units[3]["confirmed_at"], "entry_unit_ids": ["p0"],
        "departure_unit_ids": ["p11"], "peripheral_unit_ids": [],
    }
    first = {
        **base, "id": "child-family:r1", "revision_at": units[9]["confirmed_at"],
        "owned_unit_ids": [f"p{index}" for index in range(1, 10)],
    }
    second = {
        **base, "id": "child-family:r2", "revision_at": units[10]["confirmed_at"],
        "owned_unit_ids": [f"p{index}" for index in range(1, 11)],
    }
    center = {**second, "_history": [first, second]}
    candidates, promoted, _ = build_promotion_candidates(
        [center], [], 1, units, [],
    )
    assert promoted and all(item["boundary_status"] == "dynamic" for item in promoted)
    assert [item["required_unit_ids"] for item in candidates] == [[f"p{index}" for index in range(1, 10)]] * 2
    assert candidates[0]["search_unit_ids"] == [f"p{index}" for index in range(1, 10)]
    assert candidates[1]["search_unit_ids"] == [f"p{index}" for index in range(1, 11)]
    assert candidates[0]["observed_at"] == units[9]["confirmed_at"]
    assert "p0" not in candidates[0]["required_unit_ids"]
    assert "p11" not in candidates[-1]["search_unit_ids"]


def test_shanghai_daily_center_12_first_owned_nine_prefix_is_frozen_at_2020_12_15():
    spans = [
        (96, "2020-07-09", "2020-07-27"), (97, "2020-07-27", "2020-08-18"),
        (98, "2020-08-18", "2020-09-11"), (99, "2020-09-11", "2020-09-21"),
        (100, "2020-09-21", "2020-09-25"), (101, "2020-09-25", "2020-10-13"),
        (102, "2020-10-13", "2020-11-02"), (103, "2020-11-02", "2020-12-02"),
        (104, "2020-12-02", "2020-12-11"), (105, "2020-12-11", "2021-01-25"),
        (106, "2021-01-25", "2021-01-29"), (107, "2021-01-29", "2021-02-18"),
        (108, "2021-02-18", "2021-03-09"), (109, "2021-03-09", "2021-03-18"),
        (110, "2021-03-18", "2021-03-25"), (111, "2021-03-25", "2021-04-08"),
        (112, "2021-04-08", "2021-04-15"), (113, "2021-04-15", "2021-04-26"),
        (114, "2021-04-26", "2021-05-11"),
    ]
    units = [{
        "id": f"R0-pen-{number}-{start}-{end}", "kind": "pen", "level": 0,
        "direction": "up" if index % 2 == 0 else "down", "start_date": start,
        "end_date": end, "start_price": float(index % 2), "end_price": float((index + 1) % 2),
        "low": 0.0, "high": 1.0, "status": "confirmed", "continuous_range_id": 0,
        "sequence_id": 0, "structure_sequence_id": "range-0-sequence-0",
        "source_pen_ids": [f"R0-pen-{number}-{start}-{end}"],
        "confirmed_at": "2020-12-15" if number == 104 else end,
    } for index, (number, start, end) in enumerate(spans)]
    ids = [unit["id"] for unit in units]
    extension_ids = [ids[index] for index in range(4, 19, 2)]
    peripheral_ids = [ids[index] for index in range(3, 19, 2)]
    first = {
        "id": "shanghai-center-12:r1", "family_id": "shanghai-center-12",
        "level": 1, "formation_stage": "directional", "formed_at": units[2]["confirmed_at"],
        "revision_at": "2020-12-15", "owned_unit_ids": ids[:9],
    }
    final = {
        **first, "id": "shanghai-center-12:r2", "revision_at": "2021-05-13",
        "owned_unit_ids": ids, "core_unit_ids": ids[:3],
        "extension_unit_ids": extension_ids, "peripheral_unit_ids": peripheral_ids,
        "_history": [first],
    }
    final["_history"].append({key: value for key, value in final.items() if key != "_history"})
    revisions, _, _ = build_promotion_candidates([final], [], 1, units, [])
    first_candidate = revisions[0]
    assert len(final["owned_unit_ids"]) == 19
    assert len(final["core_unit_ids"]) == 3
    assert len(final["extension_unit_ids"]) == len(final["peripheral_unit_ids"]) == 8
    assert first_candidate["observed_at"] == "2020-12-15"
    assert first_candidate["required_unit_ids"] == ids[:9]
    assert first_candidate["required_unit_ids"][-1] == "R0-pen-104-2020-12-02-2020-12-11"


def test_unresolved_expansion_does_not_create_movements_or_points():
    result = build_structure_hierarchy(pens_from_prices([1,10,6,15,8,20,12,25,18,30,26,35]))
    assert "movements" not in result and "points" not in result
    assert any(item["candidate_source"] == "expansion_decomposition" for item in result["promotion_candidates"])


def test_l2_hierarchy_satisfies_reference_and_level_invariants():
    pens = pens_from_prices([0, 10, 2, 9, 3, 12, 10, 15, 13, 18, 16])
    result = build_structure_hierarchy(pens, [], [])
    assert validate_structure({"structure": {"pens": pens, **result}}) == []
    assert result["max_level"] <= 2
    assert not {"movements", "movement_revisions", "points", "point_revisions"} & set(result)
    assert all(center["decomposition_proof"]["source_kind"] == "local_pen_group"
               for center in result["centers"] if center["level"] == 2)
