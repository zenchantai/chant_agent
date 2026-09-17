from copy import deepcopy
from datetime import date, timedelta

import pytest

from app.hierarchy import (
    _parent_centers, build_hierarchy, build_hierarchy_components,
    movement_confirmation_errors,
)


def structural_stream(count=5):
    prices = [0]
    for index in range(count):
        prices.extend([10, 4, 9, 5, 12] if index % 2 == 0 else [2, 3.5, 1, 3, 0])
    stamp = lambda index: (date(2026, 1, 1) + timedelta(days=index)).isoformat()
    units = [{
        "id": f"p{index}", "kind": "atomic_pen", "status": "confirmed", "level": 0,
        "start_date": stamp(index), "end_date": stamp(index + 1),
        "start_price": start, "end_price": end, "low": min(start, end), "high": max(start, end),
        "direction": "up" if end > start else "down", "confirmed_at": stamp(index + 1),
        "sequence_id": 0, "continuous_range_id": 0,
    } for index, (start, end) in enumerate(zip(prices, prices[1:]))]
    centers = []
    for index in range(count):
        start = index * 5
        selected = units[start:start + 4]
        centers.append({
            "id": f"c{index}", "kind": "pen_center", "level": 1, "role": "hierarchy", "status": "confirmed",
            "direction": selected[0]["direction"], "entry_pen_id": selected[0]["id"],
            "start_date": selected[1]["start_date"], "end_date": selected[-1]["end_date"],
            "confirmed_at": selected[-1]["confirmed_at"],
            "zd": max(unit["low"] for unit in selected[1:]),
            "zg": min(unit["high"] for unit in selected[1:]),
            "dd": min(unit["low"] for unit in selected), "gg": max(unit["high"] for unit in selected),
            "core_pen_ids": [unit["id"] for unit in selected[1:]],
            "source_pen_ids": [unit["id"] for unit in selected],
            "sequence_id": 0, "continuous_range_id": 0,
        })
    return units, centers


def test_reverse_center_confirms_at_event_not_extreme_and_preserves_shared_boundary():
    units, centers = structural_stream(3)
    result = build_hierarchy_components(units, centers, 1)
    first, second, tail = result["movements"]
    assert [item["status"] for item in result["movements"]] == ["confirmed", "confirmed", "provisional"]
    assert first["end_price"] == 12 and first["end_date"] == units[4]["end_date"]
    assert first["confirmed_at"] == centers[1]["confirmed_at"] > first["end_date"]
    assert first["confirmation_center_id"] == "c1"
    assert first["boundary_source_unit_id"] == "p4"
    assert first["end_date"] == second["start_date"] and first["end_price"] == second["start_price"]
    assert not set(first["source_unit_ids"]) & set(second["source_unit_ids"])
    assert tail["confirmed_at"] is None and tail["confirmation_center_id"] is None
    assert tail["state"] == "formed" and tail["tail_end_date"] == units[-1]["end_date"]


@pytest.mark.parametrize("change", ["same_direction", "touch", "overlap", "candidate", "missing_time"])
def test_non_reverse_or_unconfirmed_center_never_closes_movement(change):
    units, centers = structural_stream(2)
    if change == "same_direction":
        centers[1]["direction"] = "up"
    elif change == "touch":
        centers[1]["zg"] = centers[0]["zd"]
    elif change == "overlap":
        centers[1]["zg"] = centers[0]["zd"] + 1
    elif change == "candidate":
        centers[1]["status"] = "provisional"
    else:
        centers[1]["confirmed_at"] = None
    result = build_hierarchy_components(units, centers, 1)
    assert result["movements"]
    assert all(item["status"] == "provisional" for item in result["movements"])


def test_three_finished_variable_length_movements_form_parent_and_tail_does_not():
    units, centers = structural_stream(4)
    result = build_hierarchy(units, centers)
    assert not any(center["level"] > 1 for center in result["centers"])
    assert {item["id"] for item in centers} <= {item["id"] for item in result["centers"]}


def test_parent_candidate_id_stays_stable_when_third_movement_finishes():
    units, centers = structural_stream(4)
    movements = build_hierarchy_components(units, centers, 1)["movements"]
    assert _parent_centers(movements[:2], 2, "system", centers) == []
    assert _parent_centers(movements[:3], 2, "system", centers) == []


def test_parent_requires_actual_reverse_evidence_even_when_status_claims_confirmed():
    units, centers = structural_stream(4)
    movements = build_hierarchy_components(units, centers, 1)["movements"][:3]
    for movement in movements:
        movement["confirmation_center_id"] = "missing"
    assert _parent_centers(movements, 2, "system", centers) == []


@pytest.mark.parametrize("break_kind", ["sequence", "range", "price", "date", "unconfirmed"])
def test_discontinuity_cannot_supply_reverse_confirmation(break_kind):
    units, centers = structural_stream(2)
    if break_kind in {"sequence", "range"}:
        field = "sequence_id" if break_kind == "sequence" else "continuous_range_id"
        for unit in units[5:]:
            unit[field] = 1
        centers[1][field] = 1
    elif break_kind == "price":
        units[5]["start_price"] += 1
    elif break_kind == "date":
        units[5]["start_date"] += "T01:00:00"
    else:
        units[4]["status"] = "provisional"
    result = build_hierarchy_components(units, centers, 1)
    assert result["movements"] and all(item["status"] == "provisional" for item in result["movements"])
    assert any(item["termination_reason"] in {"sequence_boundary", "data_boundary"} for item in result["movements"])


def test_confirmed_prefix_does_not_change_when_more_data_arrives():
    units, centers = structural_stream(6)
    complete = build_hierarchy_components(units, centers, 1)["movements"]
    full = {item["id"]: item for item in complete}
    for count in range(1, 7):
        prefix = build_hierarchy_components(units[:count * 5], centers[:count], 1)
        for movement in prefix["movements"]:
            if movement["status"] == "confirmed":
                assert movement == full[movement["id"]]


def test_confirmation_validator_rejects_same_direction_evidence():
    units, centers = structural_stream(2)
    movement = build_hierarchy_components(units, centers, 1)["movements"][0]
    by_id = {center["id"]: center for center in centers}
    assert movement_confirmation_errors(movement, by_id) == []
    by_id["c1"]["direction"] = "up"
    assert movement_confirmation_errors(movement, by_id)


def test_second_level_confirmed_movements_can_construct_third_level_center():
    units, centers = structural_stream(4)
    movements = build_hierarchy_components(units, centers, 1)["movements"]
    for center in centers:
        center["level"] = 2
    for movement in movements:
        movement["level"] = 2
    assert _parent_centers(movements, 3, "system", centers) == []


def test_parent_center_uses_four_completed_units_and_retains_two_of_three_candidate():
    dates = [date(2026, 2, 1) + timedelta(days=index) for index in range(6)]
    prices = [(0, 10), (10, 4), (4, 9), (9, 5)]
    movements = []
    centers = []
    for index, (start, end) in enumerate(prices):
        center_direction = "up" if end > start else "down"
        centers.append({
            "id": f"child-{index}", "level": 1, "status": "confirmed",
            "direction": center_direction, "start_date": dates[index].isoformat(),
            "end_date": dates[index + 1].isoformat(), "confirmed_at": dates[index + 1].isoformat(),
            "zd": 5 if index % 2 == 0 else 1, "zg": 9 if index % 2 == 0 else 4,
            "dd": 0, "gg": 11, "owned_unit_ids": [f"leaf-{index}"],
            "source_unit_ids": [f"leaf-{index}"], "continuous_range_id": 0,
            "sequence_id": 0, "structure_sequence_id": "same-stream",
        })
        movements.append({
            "id": f"movement-{index}", "kind": "movement", "level": 1,
            "role": "hierarchy_component", "status": "confirmed", "state": "formed",
            "direction": center_direction, "classification": "consolidation",
            "start_date": dates[index].isoformat(), "end_date": dates[index + 1].isoformat(),
            "start_price": start, "end_price": end, "low": min(start, end), "high": max(start, end),
            "source_unit_ids": [f"leaf-{index}"], "center_ids": [f"child-{index}"],
            "confirmation_center_id": f"child-{index + 1}",
            "confirmed_at": (dates[index + 3] if index + 3 < len(dates) else dates[-1]).isoformat(),
            "termination_reason": "reverse_independent_center", "recursive_eligible": True,
            "continuous_range_id": 0, "sequence_id": 0, "structure_sequence_id": "same-stream",
        })

    centers.append(dict(centers[0], id="child-4", owned_unit_ids=["leaf-4"], source_unit_ids=["leaf-4"], confirmed_at=dates[-1].isoformat()))
    candidate = _parent_centers(movements[:3], 2, "system", centers)
    confirmed = _parent_centers(movements, 2, "system", centers)
    assert len(candidate) == 1
    assert candidate[0]["status"] == "provisional"
    assert candidate[0]["progress"] == "2/3"
    assert len(confirmed) == 1
    assert confirmed[0]["status"] == "confirmed"
    assert confirmed[0]["id"] == candidate[0]["id"]
    assert confirmed[0]["core_unit_ids"] == ["movement-1", "movement-2", "movement-3"]
    assert confirmed[0]["child_movement_ids"] == [f"movement-{index}" for index in range(4)]
