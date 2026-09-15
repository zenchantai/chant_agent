from app.hierarchy import (
    _parent_centers,
    build_hierarchy,
    build_center_relations,
    classify_center_relation,
    build_hierarchy_components,
    _center_unit_span,
    _movement_from_centers,
)


def unit(i, a, b):
    return {"id": f"u{i}", "start_date": f"{i:04d}", "end_date": f"{i+1:04d}",
            "start_price": a, "end_price": b, "low": min(a, b), "high": max(a, b),
            "direction": "up" if b > a else "down", "status": "confirmed",
            "confirmed_at": f"{i+1:04d}",
            "continuous_range_id": 0, "sequence_id": 0}


def center(cid, level, zd, zg, dd, gg):
    return {"id": cid, "level": level, "status": "confirmed", "direction": "up",
            "start_date": "0000", "end_date": "0003", "confirmed_at": "0004",
            "zd": zd, "zg": zg, "dd": dd, "gg": gg, "sequence_id": 0,
            "range_index": 0, "formation_pen_ids": ["u0", "u1", "u2", "u3"],
            "core_pen_ids": ["u1", "u2", "u3"], "source_pen_ids": ["u0", "u1", "u2", "u3"],
            "pen_ids": ["u0", "u1", "u2", "u3"], "entry_pen_id": "u0",
            "start_pen": "u1", "end_pen": "u3"}


def test_center_relations_distinguish_newborn_and_expansion():
    a = center("a", 1, 2, 4, 1, 5)
    b = center("b", 1, 6, 8, 5.5, 9)
    assert classify_center_relation(a, b) == "newborn_up"
    b["dd"] = 3
    assert classify_center_relation(a, b) == "expansion_up"


def test_hierarchy_preserves_l1_and_adds_parent_only_when_three_children_complete():
    units = [unit(i, a, b) for i, (a, b) in enumerate([(1, 6), (6, 2), (2, 7), (7, 3), (3, 8), (8, 2), (2, 7), (7, 3), (3, 8)])]
    l1 = [center("c1", 1, 3, 6, 1, 8)]
    result = build_hierarchy(units, l1)
    assert result["centers"][0]["id"] == "c1"
    assert all(item["level"] >= 1 for item in result["centers"])


def test_hierarchy_persists_every_referenced_center_and_movement():
    units = [unit(i, a, b) for i, (a, b) in enumerate([
        (1, 6), (6, 2), (2, 7), (7, 3), (3, 8), (8, 2),
        (2, 7), (7, 3), (3, 8), (8, 2), (2, 7), (7, 3),
    ])]
    result = build_hierarchy(units, [center("c1", 1, 3, 6, 1, 8)])
    center_ids = {item["id"] for item in result["centers"]}
    movement_ids = {item["id"] for item in result["movements"]}

    assert all(item.get("role", "hierarchy") == "hierarchy" for item in result["centers"])
    for movement in result["movements"]:
        assert set(movement.get("center_ids", [])) <= center_ids
        assert set(movement.get("child_movement_ids", [])) <= movement_ids
        if movement.get("confirmation_center_id"):
            assert movement["confirmation_center_id"] in center_ids
    for item in result["centers"]:
        assert set(item.get("child_center_ids", [])) <= center_ids
        assert set(item.get("child_movement_ids", [])) <= movement_ids
        assert set(item.get("parent_center_ids", [])) <= center_ids
    for relation in result["center_relations"]:
        assert relation["previous_center_id"] in center_ids
        assert relation["current_center_id"] in center_ids


def test_hierarchy_component_movements_use_raw_l1_centers_for_parent_links():
    values = []
    for _ in range(5):
        # The broad alternating excursions give the first three completed
        # child movements a strict common range for the parent upgrade.
        values.extend([(0, 10), (10, 0), (0, 9), (9, 0)])
    units = [unit(i, a, b) for i, (a, b) in enumerate(values)]

    def raw_center(cid, start, base, direction):
        return {
            "id": cid, "level": 1, "status": "confirmed", "direction": direction,
            "start_date": f"{start:04d}", "end_date": f"{start + 3:04d}",
            "confirmed_at": f"{start + 4:04d}", "zd": base - 2, "zg": base + 2,
            "fixed_zd": base - 2, "fixed_zg": base + 2,
            "dd": base - 5, "gg": base + 5,
            "entry_pen_id": f"u{start}",
            "source_pen_ids": [f"u{i}" for i in range(start, start + 4)],
            "formation_pen_ids": [f"u{i}" for i in range(start, start + 4)],
            "core_pen_ids": [f"u{i}" for i in range(start + 1, start + 4)],
            "pen_ids": [f"u{i}" for i in range(start, start + 4)],
            "sequence_id": 0, "range_index": 0,
        }

    raw = [raw_center("raw-l1-a", 0, 5, "up"), raw_center("raw-l1-b", 4, 0, "down"),
           raw_center("raw-l1-c", 8, 5, "up"), raw_center("raw-l1-d", 12, 0, "down"),
           raw_center("raw-l1-e", 16, 5, "up")]
    result = build_hierarchy(units, raw)
    raw_ids = {item["id"] for item in raw}
    hierarchy_movements = [
        item for item in result["movements"]
        if item["role"] == "hierarchy_component" and item["level"] == 1
    ]
    assert hierarchy_movements
    assert all(set(item.get("center_ids", [])) <= raw_ids for item in hierarchy_movements)
    parents = [item for item in result["centers"] if item.get("role") == "hierarchy" and item["level"] == 2]
    assert parents
    assert all(set(item.get("child_center_ids", [])) <= raw_ids for item in parents)


def test_expansion_upgrade_keeps_kind_for_candidate_and_confirmation():
    from tests.test_strict_movements import structural_stream
    units, centers = structural_stream(4)
    movements = build_hierarchy_components(units, centers, 1)["movements"]
    candidate = _parent_centers(movements[:2], 2, "system", centers)[0]
    confirmed = _parent_centers(movements[:3], 2, "system", centers)[0]
    assert candidate["status"] == "provisional" and candidate["progress"] == "2/3"
    assert confirmed["status"] == "confirmed" and confirmed["progress"] == "3/3"
    assert candidate["id"] == confirmed["id"]
    assert candidate["upgrade_kind"] == confirmed["upgrade_kind"] == "expansion"


def test_same_direction_separated_cores_continue_trend_despite_envelope_overlap():
    """An expansion starts another consolidation instead of a trend."""
    values = [
        (1, 6), (6, 2), (2, 7), (7, 3),
        (3, 9), (9, 5), (5, 10), (10, 8),
    ]
    units = [unit(i, a, b) for i, (a, b) in enumerate(values)]

    def raw_center(cid, start, zd, zg, dd, gg):
        return {
            "id": cid, "level": 1, "status": "confirmed", "direction": "up",
            "start_date": f"{start:04d}", "end_date": f"{start + 3:04d}",
            "confirmed_at": f"{start + 4:04d}", "zd": zd, "zg": zg,
            "fixed_zd": zd, "fixed_zg": zg, "dd": dd, "gg": gg,
            "entry_pen_id": f"u{start}",
            "formation_pen_ids": [f"u{i}" for i in range(start, start + 4)],
            "core_pen_ids": [f"u{i}" for i in range(start + 1, start + 4)],
            "source_pen_ids": [f"u{i}" for i in range(start, start + 4)],
            "pen_ids": [f"u{i}" for i in range(start, start + 4)],
            "sequence_id": 0, "range_index": 0,
        }

    # Core intervals are separated, but the envelopes overlap: expansion_up.
    centers = [
        raw_center("c1", 0, 3, 6, 1, 8),
        raw_center("c2", 4, 7, 9, 4, 10),
    ]
    result = build_hierarchy_components(units, centers, 1)
    completed = [item for item in result["movements"] if item["status"] == "confirmed"]
    provisional = [item for item in result["movements"] if item["status"] == "provisional"]
    assert completed == []
    assert provisional
    assert all(item["classification"] == "trend" for item in result["movements"])
    assert [item["center_count"] for item in result["movements"]] == [2]


def test_hierarchy_component_tail_stays_inside_sequence_group():
    """A provisional tail must never consume units from a later sequence."""
    first = [unit(i, a, b) for i, (a, b) in enumerate([
        (1, 6), (6, 2), (2, 7), (7, 3),
    ])]
    second = [unit(i, a, b) for i, (a, b) in enumerate([
        (10, 5), (5, 9), (9, 4), (4, 8),
    ], start=4)]
    for item in second:
        item["sequence_id"] = 1
    units = first + second

    def raw_center(cid, start, direction, sequence_id):
        return {
            "id": cid, "level": 1, "status": "confirmed", "direction": direction,
            "start_date": f"{start:04d}", "end_date": f"{start + 3:04d}",
            "confirmed_at": f"{start + 4:04d}", "zd": 3, "zg": 6,
            "fixed_zd": 3, "fixed_zg": 6, "dd": 1, "gg": 8,
            "entry_pen_id": f"u{start}",
            "formation_pen_ids": [f"u{i}" for i in range(start, start + 4)],
            "core_pen_ids": [f"u{i}" for i in range(start + 1, start + 4)],
            "source_pen_ids": [f"u{i}" for i in range(start, start + 4)],
            "pen_ids": [f"u{i}" for i in range(start, start + 4)],
            "sequence_id": sequence_id, "range_index": 0,
        }

    centers = [raw_center("c1", 0, "up", 0), raw_center("c2", 4, "down", 1)]
    result = build_hierarchy_components(units, centers, 1)
    assert len(result["movements"]) == 2
    for movement in result["movements"]:
        source = [item for item in units if item["id"] in movement["source_unit_ids"]]
        assert source
        assert {item["sequence_id"] for item in source} == {movement["sequence_id"]}


def test_explicit_center_reference_cannot_fall_back_to_dates():
    units = [unit(i, i + 1, i + 2) for i in range(4)]
    index = {item["id"]: i for i, item in enumerate(units)}
    center = {
        "entry_pen_id": "u0",
        "core_unit_ids": ["u1", "missing-unit", "u3"],
        "start_date": "0000", "end_date": "0003",
    }
    assert _center_unit_span(center, units, index) is None


def test_manual_direction_conflict_stays_structural_and_loses_confirmation():
    units = [unit(0, 1, 3), unit(1, 3, 2)]
    center = {
        "id": "manual-center", "level": 1, "direction": "down",
        "zd": 1.5, "zg": 2.5, "dd": 1, "gg": 3,
        "continuous_range_id": 0, "sequence_id": 0,
    }
    confirmation = {"id": "confirmation", "confirmed_at": "0004"}
    movement = _movement_from_centers(
        units, [center], 1, 0, 2, "down", "confirmed", confirmation,
        "hierarchy_component", "manual-derived",
    )
    assert movement["direction"] == "down"
    assert movement["status"] == "provisional"
    assert movement["confirmed_at"] is None
    assert movement["issues"][0]["code"] == "movement_direction_endpoint_mismatch"


def test_center_core_single_point_contact_is_touching():
    left = {"zd": 2, "zg": 4, "dd": 1, "gg": 5}
    right = {"zd": 4, "zg": 6, "dd": 3, "gg": 7}
    assert classify_center_relation(left, right) == "touching"


def test_touching_center_is_not_persisted_as_a_relation():
    left = center("left", 1, 2, 4, 1, 5)
    right = center("right", 1, 4, 6, 3, 7)
    right.update({"start_date": "0004", "end_date": "0007"})
    assert build_center_relations([left, right]) == []


def test_three_unit_children_without_reverse_evidence_cannot_upgrade():
    def movement(index, direction, source_count=3):
        start = index * 3
        return {
            "id": f"m{index}", "kind": "movement", "level": 1,
            "role": "hierarchy_component", "status": "confirmed",
            "direction": direction,
            "start_date": f"{start:04d}", "end_date": f"{start + 3:04d}",
            "start_price": 1 if direction == "up" else 10,
            "end_price": 10 if direction == "up" else 1,
            "low": 0, "high": 11,
            "source_unit_ids": [f"u{i}" for i in range(start, start + source_count)],
            "source_pen_ids": [f"p{i}" for i in range(start, start + source_count)],
            "start_boundary": start, "end_boundary": start + source_count,
            "continuous_range_id": 0, "sequence_id": 0,
        }

    children = [movement(0, "up"), movement(1, "down"), movement(2, "up")]
    assert _parent_centers(children[:2], 2, "system") == []
    assert _parent_centers(children, 2, "system") == []

    malformed = [movement(0, "up", 4), movement(1, "down", 4)]
    # Keep their boundary stream contiguous so only the non-3-unit rule is
    # responsible for rejecting the extension candidate.
    malformed[1]["source_unit_ids"] = [f"u{i}" for i in range(4, 8)]
    malformed[1]["start_boundary"] = 4
    malformed[1]["end_boundary"] = 8
    assert _parent_centers(malformed, 2, "system") == []


def test_l1_hierarchy_center_has_uniform_persistence_fields():
    raw = center("raw", 1, 2, 4, 1, 5)
    result = build_hierarchy([unit(i, i + 1, i + 2) for i in range(4)], [raw])
    persisted = next(item for item in result["centers"] if item["id"] == "raw")
    assert persisted["core_unit_ids"] == raw["core_pen_ids"]
    assert persisted["child_movement_ids"] == []
    assert persisted["child_center_ids"] == []
    assert persisted["progress"] == "3/3"
    assert "upgrade_kind" in persisted and persisted["upgrade_kind"] is None


def test_build_hierarchy_does_not_confirm_parent_from_nine_pen_extension():
    # P0 is the entry. P1-P9 are nine consecutive, alternating L0 movements
    # absorbed by one fixed-core L1 center.
    prices = [0, 10, 2, 9, 3, 8, 4, 9, 3, 8, 4]
    pens = [unit(i, start, end) for i, (start, end) in enumerate(zip(prices, prices[1:]))]
    center_l1 = {
        "id": "extended-l1", "kind": "pen_center", "level": 1,
        "status": "confirmed", "direction": "up",
        "start_date": pens[1]["start_date"], "end_date": pens[9]["end_date"],
        "confirmed_at": pens[3]["confirmed_at"],
        "zd": 3, "zg": 9, "fixed_zd": 3, "fixed_zg": 9,
        "dd": 2, "gg": 10,
        "entry_pen_id": "u0",
        "formation_pen_ids": ["u0", "u1", "u2", "u3"],
        "core_pen_ids": ["u1", "u2", "u3"],
        "extension_pen_ids": [f"u{i}" for i in range(4, 10)],
        "source_pen_ids": [f"u{i}" for i in range(10)],
        "pen_ids": [f"u{i}" for i in range(10)],
        "continuous_range_id": 0, "range_index": 0, "sequence_id": 0,
    }

    result = build_hierarchy(pens, [center_l1])
    upgraded = [
        item for item in result["centers"]
        if item.get("role") == "hierarchy" and item["level"] == 2
        and item.get("upgrade_kind") == "extension_3x3"
    ]
    assert upgraded == []
    assert all(item["status"] == "provisional" for item in result["movements"])


def test_1a0001_daily_34_pen_extension_cannot_upgrade_without_reverse_center():
    # Static structural fixture from the 2022-03-03 through 2023-11-21 daily
    # endpoints. It intentionally has no dependency on the production DB.
    points = [
        ("2022-03-03", 3500.2872), ("2022-03-16", 3023.3045),
        ("2022-04-07", 3290.2561), ("2022-04-27", 2863.6497),
        ("2022-06-15", 3358.5453), ("2022-06-23", 3262.2939),
        ("2022-07-05", 3424.8366), ("2022-07-18", 3226.2315),
        ("2022-07-28", 3305.7057), ("2022-09-05", 3172.0394),
        ("2022-09-13", 3278.1659), ("2022-10-12", 2934.0922),
        ("2022-10-18", 3099.9182), ("2022-10-31", 2885.0894),
        ("2022-11-16", 3145.7526), ("2022-11-28", 3034.7045),
        ("2022-12-07", 3226.0817), ("2022-12-23", 3031.5358),
        ("2023-01-30", 3310.4903), ("2023-02-17", 3223.2581),
        ("2023-03-07", 3342.8585), ("2023-03-30", 3220.9846),
        ("2023-04-18", 3396.1745), ("2023-04-25", 3229.4461),
        ("2023-05-09", 3418.9534), ("2023-05-25", 3168.5716),
        ("2023-06-16", 3276.5522), ("2023-06-26", 3144.2484),
        ("2023-07-14", 3248.3849), ("2023-07-24", 3151.1252),
        ("2023-08-04", 3315.0492), ("2023-08-25", 3053.0373),
        ("2023-09-04", 3177.0603), ("2023-10-23", 2923.5113),
        ("2023-11-21", 3089.7738),
    ]
    pens = []
    for index, (start, end) in enumerate(zip(points, points[1:])):
        pens.append({
            "id": f"p{index}", "ordinal": index,
            "start_date": start[0], "end_date": end[0],
            "start_price": start[1], "end_price": end[1],
            "low": min(start[1], end[1]), "high": max(start[1], end[1]),
            "direction": "up" if end[1] > start[1] else "down",
            "status": "confirmed", "confirmed_at": end[0],
            "range_index": 0, "continuous_range_id": 0, "sequence_id": 0,
        })
    target = {
        "id": "target-l1", "kind": "pen_center", "level": 1,
        "status": "confirmed", "direction": "down",
        "start_date": "2022-03-16", "end_date": "2023-11-21",
        "confirmed_at": "2022-06-15",
        "zd": 3023.3045, "zg": 3290.2561,
        "fixed_zd": 3023.3045, "fixed_zg": 3290.2561,
        "dd": 2863.6497, "gg": 3500.2872,
        "entry_pen_id": "p0",
        "formation_pen_ids": ["p0", "p1", "p2", "p3"],
        "core_pen_ids": ["p1", "p2", "p3"],
        "extension_pen_ids": [f"p{i}" for i in range(4, 34)],
        "source_pen_ids": [f"p{i}" for i in range(34)],
        "pen_ids": [f"p{i}" for i in range(34)],
        "range_index": 0, "continuous_range_id": 0, "sequence_id": 0,
    }

    result = build_hierarchy(pens, [target])
    hierarchy_centers = [
        item for item in result["centers"] if item.get("role") == "hierarchy"
    ]
    level_two = [item for item in hierarchy_centers if item["level"] == 2]
    assert [
        (item["start_date"], item["end_date"], item["status"], item["progress"])
        for item in level_two
    ] == []
    level_three = [item for item in hierarchy_centers if item["level"] == 3]
    assert [
        (item["start_date"], item["end_date"], item["status"])
        for item in level_three
    ] == []
    assert result["max_available_center_level"] == 1
