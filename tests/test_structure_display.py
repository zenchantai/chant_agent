from copy import deepcopy

from app.chan_structure import build_structure_hierarchy
from app.structure_display import center_display_catalog, project_center_display
from tests.chan_fixtures import pens_from_prices, legacy_expansion_snapshot


def sample():
    pens = pens_from_prices([1, 10, 6, 15, 8, 20, 12, 25, 21, 30, 26, 35])
    return legacy_expansion_snapshot()


def page(source, start=None, end=None):
    return {"meta": {"run_id": 1}, "market": {"bars": [{"trade_date": start or source["pens"][0]["start_date"]}, {"trade_date": end or source["pens"][-1]["end_date"]}]},
            "structure": {"centers": source["centers"], "center_revisions": [], "pens": [], "components": []}}


def test_display_children_without_reactivating_or_mutating_snapshot():
    source = sample()
    before = deepcopy(source)
    original = page(source)
    result = project_center_display(original, source, 1)
    references = result["structure"]["display_centers"]
    assert len(references) == 2
    assert all(item["display_role"] == "constituent" for item in references)
    assert result["structure"]["display_center_levels"] == [1, 2]
    assert source == before
    assert "display_centers" not in original["structure"]
    assert result["structure"]["centers"] == source["centers"]
    by_id = {item["id"]: item for item in result["structure"]["center_revisions"]}
    assert all(not by_id[item["revision_id"]]["active"] for item in references)
    assert all(parent in by_id for item in references for parent in item["parent_revision_ids"])
    pen_ids = {pen["id"] for pen in result["structure"]["pens"]}
    assert all(set(item["source_pen_ids"]) <= pen_ids for item in by_id.values())


def test_catalog_does_not_draw_all_history_and_is_pagination_stable():
    source = sample()
    catalog = center_display_catalog(source)
    assert len(catalog) == 3
    assert len(source["center_revisions"]) > len(catalog)
    first = project_center_display(page(source), source, 0)
    last = project_center_display(page(source, source["pens"][-1]["start_date"]), source, 0)
    assert first["structure"]["display_center_levels"] == last["structure"]["display_center_levels"]
    assert all(item in first["structure"]["display_centers"] for item in last["structure"]["display_centers"])


def test_touch_candidate_does_not_create_a_display_parent():
    source = {"pens": [], **build_structure_hierarchy(pens_from_prices([1, 10, 6, 15, 8, 20, 15, 25, 18, 30, 26, 35]))}
    assert all(item["display_role"] == "active" for item in center_display_catalog(source))


def test_nested_expansion_and_latest_representative_are_deterministic():
    source = sample()
    child = source["centers"][0]
    child["active"] = False
    parent = {**deepcopy(child), "id": "higher:r1", "family_id": "higher", "level": 3, "active": True, "child_center_ids": [child["id"]]}
    source["center_revisions"].append(parent)
    source["centers"] = [parent]
    catalog = center_display_catalog(source)
    assert len(catalog) == 4
    assert {item["display_role"] for item in catalog} == {"active", "constituent"}
    bad = {**deepcopy(child), "id": "invalid:r1", "status": "invalidated"}
    source["center_revisions"].append(bad)
    parent["child_center_ids"].append(bad["id"])
    assert center_display_catalog(source) == catalog


def test_center_revision_and_components_are_in_non_diagnostic_closure():
    source = sample()
    older = next(center for center in source["center_revisions"] if center["level"] == 1 and center["revision_no"] == 1)
    component = source["components"][0]
    result = project_center_display(page(source), source, 1)["structure"]
    assert older in result["center_revisions"]
    assert component in result["components"]
    assert "points" not in result and "movements" not in result


def test_l2_display_closure_preserves_promotion_proofs_and_source_pens():
    pens = pens_from_prices([1, 10, 6, 15, 8, 20, 12, 25, 18, 30, 26, 35])
    source = {"pens": pens, **build_structure_hierarchy(pens)}
    result = project_center_display(page(source), source, 2)["structure"]
    parent, = [item for item in result["centers"] if item["level"] == 2]
    assert parent["decomposition_proof"]["source_kind"] == "local_pen_group"
    assert result["promotion_candidates"]
    assert {segment["id"] for segment in parent["decomposition_proof"]["segments"]} <= {
        segment["id"] for segment in result["segment_proof_revisions"]
    }
    assert set(parent["source_pen_ids"]) <= {pen["id"] for pen in result["pens"]}
