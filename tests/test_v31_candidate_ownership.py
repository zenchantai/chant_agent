from app.chan_structure import atomic_pen_units, build_level_centers, build_structure_hierarchy, validate_structure


def _pens(prices):
    result = []
    for index, (start, end) in enumerate(zip(prices, prices[1:])):
        result.append({
            "id": f"p{index}", "start_date": f"2025-01-{index + 1:02d}",
            "end_date": f"2025-01-{index + 2:02d}", "start_price": start,
            "end_price": end, "direction": "up" if end > start else "down",
            "status": "confirmed", "confirmed_at": f"2025-01-{index + 2:02d}",
            "continuous_range_id": 0, "sequence_id": 0, "structure_sequence_id": "range-0",
        })
    return result


def test_v31_preserves_candidate_rejections_and_unique_ownership():
    # The first triple is the existing center; the next independent triple is
    # the only canonical successor. Later windows remain diagnostics.
    pens = _pens([100, 120, 105, 125, 122, 140, 123, 150, 130, 155])
    centers, _, issues = build_level_centers(
        atomic_pen_units(pens), 1,
        direction_context={"process_direction": "down", "reason": "test", "available_at": "2025-01-01", "start_index": -1},
    )
    selected = [item for item in issues if item.get("kind") == "center_candidate" and item["status"] == "selected"]
    assert selected
    owners = {}
    for center in centers:
        for unit_id in center["owned_unit_ids"]:
            assert (center["level"], unit_id) not in owners
            owners[(center["level"], unit_id)] = center["family_id"]
    assert any(item.get("rejection_code") in {"prior_boundary_won", "overlaps_selected_center", "direction_mismatch"}
               for item in issues if item.get("kind") == "center_candidate")


def test_v31_no_future_candidate_unit_is_selected():
    pens = _pens([100, 120, 105, 125, 110, 123, 108])
    result = build_structure_hierarchy(pens, calculation_profile="pen_centers_only")
    assert validate_structure({"pens": pens, **result}) == []
    for candidate in result["center_candidate_revisions"]:
        assert all(unit["confirmed_at"] <= candidate["observed_at"] for unit in pens if unit["id"] in candidate["source_unit_ids"])


def test_weekly_successor_cores_use_canonical_nonoverlapping_windows():
    pens = _pens([
        22, 11, 29, 19, 25, 17, 28, 21, 23, 5,
        16, 6, 13, 9, 15, 4, 8, 1, 3, 2,
        12, 10, 14, 7, 27, 20, 26, 18, 30, 24,
    ])
    result = build_structure_hierarchy(pens, calculation_profile="pen_centers_only")
    cores = {tuple(center["core_unit_ids"]) for center in result["centers"]}
    assert all(tuple(pen["id"] for pen in pens[start:start + 3]) in cores for start in (17, 20, 24))
    assert not any(
        tuple(candidate["source_unit_ids"]) == tuple(pen["id"] for pen in pens[19:22])
        and candidate["status"] == "selected"
        for candidate in result["center_candidate_revisions"]
    )
