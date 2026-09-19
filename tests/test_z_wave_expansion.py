from copy import deepcopy

import pytest

from app.chan_structure import (
    _promote_expansions, atomic_pen_units, build_level_centers, build_relations,
    build_structure_hierarchy, expansion_evidence, merge_formation_paths, validate_structure,
)
from tests.chan_fixtures import pens_from_prices


@pytest.mark.parametrize("middle,relation,levels", [
    (16, "newborn_up", [1, 1]),
    (12, "expansion_up", [2]),
    (15, "boundary_touch_candidate", [1, 1]),
])
@pytest.mark.parametrize("mirror", [False, True])
def test_owned_z_envelope_distinguishes_newborn_expansion_and_touch(middle, relation, levels, mirror):
    prices = [1, 10, 6, 15, 8, 20, middle, 25, 21, 30, 26, 35]
    if mirror:
        prices = [40 - price for price in prices]
        relation = relation.replace("_up", "_down")
    pens = pens_from_prices(prices)
    result = build_structure_hierarchy(pens)
    latest = {center["family_id"]: center for center in result["center_revisions"] if center["level"] == 1}
    children = sorted(latest.values(), key=lambda center: center["start_date"])
    assert len(children) == 2
    assert children[0]["entry_unit_ids"] == ["p0"]
    assert children[0]["core_unit_ids"] == ["p1", "p2", "p3"]
    assert children[0]["z_unit_ids"] == ["p1", "p3"]
    assert children[1]["z_unit_ids"] == ["p6", "p8"]
    assert children[0]["z_direction"] == ("up" if mirror else "down")
    expected = [(6, 15), (middle, 30)]
    if mirror:
        expected = [(40 - high, 40 - low) for low, high in expected]
    assert [(center["dd"], center["gg"]) for center in children] == expected
    assert [center["level"] for center in result["centers"]] == levels
    assert relation in {item["relation_type"] for item in result["relations"]}
    assert validate_structure({"pens": pens, **result}) == []
    if middle == 12:
        parent = result["centers"][0]
        assert (parent["zd"], parent["zg"]) == ((25, 28) if mirror else (12, 15))
        assert parent["connection_component_ids"]
        assert parent["overlap_witness_unit_ids"] == ["p3", "p6"]
        assert "p4" in parent["source_pen_ids"]
        assert parent["promotion_confirmed_at"] >= children[1]["formed_at"]


def test_expansion_audit_rejects_missing_connection_and_witness():
    pens = pens_from_prices([1, 10, 6, 15, 8, 20, 12, 25, 21, 30, 26, 35])
    result = deepcopy(build_structure_hierarchy(pens))
    parent = next(center for center in result["center_revisions"] if center["level"] == 2)
    parent["connection_component_ids"] = []
    parent["overlap_witness_unit_ids"] = []
    errors = validate_structure({"pens": pens, **result})
    assert any("connection" in error for error in errors)
    assert any("witness" in error for error in errors)


def test_entry_extreme_does_not_change_owned_z_envelope():
    outputs = [build_structure_hierarchy(pens_from_prices([start, 10, 6, 15, 8, 20, 16, 25, 21, 30, 26, 35])) for start in (1, -1000)]
    assert [[(center["zd"], center["zg"], center["dd"], center["gg"], center["level"]) for center in output["centers"]] for output in outputs][0] == [[(center["zd"], center["zg"], center["dd"], center["gg"], center["level"]) for center in output["centers"]] for output in outputs][1]
    assert outputs[0]["centers"][0]["context_low"] != outputs[1]["centers"][0]["context_low"]


@pytest.mark.parametrize("missing", ["connection", "sequence", "confirmation"])
def test_overlap_without_complete_proof_keeps_children_active(missing):
    units = atomic_pen_units(pens_from_prices([1, 10, 6, 15, 8, 20, 12, 25, 21, 30, 26, 35]))
    centers, components, _ = build_level_centers(units, 1)
    for center in centers:
        center.pop("_history")
    if missing == "connection":
        components = []
    if missing == "sequence":
        units[5]["sequence_id"] = 2
    if missing == "confirmation":
        units[6]["status"] = "provisional"
    relations = build_relations(centers)
    promoted, _ = _promote_expansions(centers, relations, 2, units, components)
    assert promoted == []
    assert all(center["active"] for center in centers)
    assert relations[0]["status"] == "candidate"
    assert relations[0]["missing_evidence"]


def test_pen_and_completed_movement_adapters_share_same_z_rules():
    pens = atomic_pen_units(pens_from_prices([1, 10, 6, 15, 8, 20, 16, 25, 21, 30, 26, 35]))
    movements = [{**unit, "kind": "movement", "level": 1} for unit in pens]
    low = build_level_centers(pens, 1)[0]
    high = build_level_centers(movements, 2)[0]
    fields = ("entry_unit_ids", "core_unit_ids", "z_unit_ids", "z_direction", "dd", "gg", "zd", "zg")
    assert [[item[key] for key in fields] for item in low] == [[item[key] for key in fields] for item in high]
    assert all(center["unit_kind"] == "movement" for center in high)


def test_extension_and_return_only_append_immutable_evidence():
    pens = pens_from_prices([1, 10, 6, 15, 8, 20, 9, 18, 7, 17, 11, 23, 19, 28, 24, 32])
    previous = {}
    for count in range(4, len(pens) + 1):
        result = build_structure_hierarchy(pens[:count])
        current = {center["id"]: center for center in result["center_revisions"]}
        for identifier, old in previous.items():
            assert identifier in current
            for field in ("core_unit_ids", "z_unit_ids", "zd", "zg", "dd", "gg", "formed_at", "promotion_confirmed_at", "evidence"):
                assert old[field] == current[identifier][field], (count, identifier, field)
        previous = current
        assert validate_structure({"pens": pens[:count], **result}) == []


@pytest.mark.parametrize("field,value,expected", [
    ("zd", 13, "expansion_core"),
    ("promotion_confirmed_at", "1900-01-01", "promotion_time"),
    ("unit_kind", "pen", "expansion_unit_kind"),
    ("source_pen_ids", [], "source_pen_ids"),
])
def test_shared_auditor_recalculates_expansion_not_just_child_count(field, value, expected):
    pens = pens_from_prices([1, 10, 6, 15, 8, 20, 12, 25, 21, 30, 26, 35])
    result = build_structure_hierarchy(pens)
    parent = next(center for center in result["center_revisions"] if center["level"] == 2)
    parent[field] = value
    assert any(expected in error for error in validate_structure({"pens": pens, **result}))


@pytest.mark.parametrize("conflict", [False, True])
def test_alternate_formation_paths_merge_or_preserve_first_valid_core(conflict):
    result = build_structure_hierarchy(pens_from_prices([1, 10, 6, 15, 8, 20, 12, 25, 21, 30, 26, 35]))
    first = deepcopy(result["centers"][0])
    first_evidence = deepcopy(first["evidence"])
    alternate = deepcopy(first)
    alternate.update(id="recursive-family:r1", family_id="recursive-family", revision_no=1,
                     formed_at="2099-01-01", revision_at="2099-01-01", formation_modes=["recursive_core"])
    if conflict:
        alternate["zd"] += 0.5
    revisions = [first, alternate]
    issues = merge_formation_paths(revisions)
    active = [center for center in revisions if center["active"]]
    assert len(active) == 1
    assert active[0]["family_id"] == first["family_id"]
    assert first["evidence"] == first_evidence
    if conflict:
        assert issues[0]["kind"] == "formation_conflict"
        assert active[0]["id"] == first["id"]
    else:
        assert issues == []
        assert active[0]["previous_revision_id"] == first["id"]
        assert active[0]["formation_modes"] == ["expansion_envelope_overlap", "recursive_core"]
        assert active[0]["alternate_formation_revision_ids"] == [alternate["id"]]
