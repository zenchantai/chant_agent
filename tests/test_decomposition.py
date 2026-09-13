from app.decomposition import build_movements


def pens_from_points(points):
    return [
        {
            "id": f"p{index}", "ordinal": index, "start_date": f"2026-01-{index + 1:02d}",
            "end_date": f"2026-01-{index + 2:02d}", "start_price": start, "end_price": end,
            "direction": "up" if end > start else "down", "confirmed_at": f"2026-01-{index + 3:02d}",
            "status": "confirmed", "sequence_id": 0,
        }
        for index, (start, end) in enumerate(zip(points, points[1:]))
    ]


def center(name, entry, zd, zg, direction="up"):
    ids = [f"p{index}" for index in range(entry, entry + 4)]
    return {
        "id": name, "ordinal": entry, "start_date": f"2026-01-{entry + 2:02d}",
        "end_date": f"2026-01-{entry + 5:02d}", "confirmed_at": f"2026-01-{entry + 6:02d}",
        "status": "confirmed", "direction": direction, "sequence_id": 0,
        "entry_pen_id": ids[0], "formation_pen_ids": ids, "core_pen_ids": ids[1:],
        "source_pen_ids": ids, "pen_ids": ids, "zd": zd, "zg": zg,
        "fixed_zd": zd, "fixed_zg": zg,
    }


def test_single_center_is_provisional_consolidation():
    pens = pens_from_points([1, 6, 3, 7, 4, 8])
    result = build_movements(pens, [center("a", 0, 3, 6)])
    assert result["decomposition"]["status"] == "complete"
    assert len(result["movements"]) == 1
    movement = result["movements"][0]
    assert movement["classification"] == "consolidation"
    assert movement["direction"] == "up"
    assert movement["status"] == "provisional"


def test_two_higher_centers_form_one_up_trend():
    pens = pens_from_points([1, 6, 3, 7, 4, 10, 8, 12, 9, 13])
    result = build_movements(pens, [center("a", 0, 3, 6), center("b", 4, 8, 10)])
    assert len(result["movements"]) == 1
    assert result["movements"][0]["classification"] == "trend"
    assert result["movements"][0]["center_ids"] == ["a", "b"]


def test_two_lower_centers_form_one_down_trend():
    pens = pens_from_points([13, 8, 11, 7, 10, 4, 6, 2, 5, 1])
    result = build_movements(pens, [center("a", 0, 8, 11, "down"), center("b", 4, 4, 7, "down")])
    assert len(result["movements"]) == 1
    assert result["movements"][0]["classification"] == "trend"
    assert result["movements"][0]["direction"] == "down"


def test_opposite_center_confirms_shared_extreme_endpoint_without_shared_pen():
    points = [1, 6, 3, 7, 4, 10, 8, 12, 9, 13, 5, 2, 6, 3, 7]
    pens = pens_from_points(points)
    centers = [center("a", 0, 3, 6), center("b", 4, 8, 10), center("c", 9, 3, 6, "down")]
    result = build_movements(pens, centers)
    assert len(result["movements"]) == 2
    previous, current = result["movements"]
    assert previous["status"] == "confirmed"
    assert previous["end_date"] == current["start_date"] == "2026-01-10"
    assert previous["end_price"] == current["start_price"] == 13
    assert previous["confirmed_at"] == centers[-1]["confirmed_at"]
    assert set(previous["pen_ids"]).isdisjoint(current["pen_ids"])
    assert previous["confirmation_center_id"] == "c"
    assert current["direction"] == "down"


def test_left_fragment_is_unassigned_and_id_stays_stable_after_confirmation():
    base_pens = pens_from_points([0, 2, 1, 6, 3, 7, 4, 9, 5, 10, 2, 6, 3, 7])
    first = center("a", 2, 3, 6)
    provisional = build_movements(base_pens[:9], [first])["movements"][0]
    confirmed = build_movements(base_pens, [first, center("b", 9, 3, 6, "down")])
    assert confirmed["decomposition"]["unassigned_pen_ids"] == ["p0", "p1"]
    assert confirmed["movements"][0]["id"] == provisional["id"]


def test_touching_or_overlapping_centers_stop_confirmation():
    pens = pens_from_points([1, 6, 3, 7, 4, 10, 8, 12, 9, 13])
    touching = build_movements(pens, [center("a", 0, 3, 6), center("b", 4, 6, 9)])
    assert touching["decomposition"]["status"] == "partial"
    assert touching["decomposition"]["issues"][0]["code"] == "touching_centers"
    assert touching["movements"][0]["status"] == "provisional"


def test_no_center_does_not_invent_a_movement():
    pens = pens_from_points([1, 6, 3, 7])
    result = build_movements(pens, [])
    assert result["movements"] == []
    assert result["decomposition"]["status"] == "no_center"
    assert result["decomposition"]["unassigned_pen_ids"] == ["p0", "p1", "p2"]


def test_continuous_ranges_are_decomposed_independently():
    first_pens = pens_from_points([1, 6, 3, 7, 4])
    second_pens = pens_from_points([10, 5, 8, 4, 7])
    for pen in second_pens:
        pen["id"] = f"r1-{pen['id']}"
        pen["range_index"] = 1
    first_center = center("a", 0, 3, 6)
    second_center = center("b", 0, 5, 8, "down")
    second_center.update({
        "range_index": 1,
        "entry_pen_id": "r1-p0",
        "formation_pen_ids": [f"r1-p{index}" for index in range(4)],
        "core_pen_ids": [f"r1-p{index}" for index in range(1, 4)],
        "source_pen_ids": [f"r1-p{index}" for index in range(4)],
        "pen_ids": [f"r1-p{index}" for index in range(4)],
    })
    result = build_movements(first_pens + second_pens, [first_center, second_center])
    assert len(result["movements"]) == 2
    assert all(movement["status"] == "provisional" for movement in result["movements"])
    assert {movement["continuous_range_id"] for movement in result["movements"]} == {0, 1}
