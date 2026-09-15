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
    parent = next(center for center in result["centers"] if center["level"] == 2)
    assert parent["status"] == "confirmed" and parent["progress"] == "3/3"
    by_id = {item["id"]: item for item in result["movements"]}
    children = [by_id[child] for child in parent["child_movement_ids"]]
    assert len(children) == 3 and all(child["status"] == "confirmed" for child in children)
    assert all(len(child["source_unit_ids"]) == 5 for child in children)
    assert parent["confirmed_at"] == max(child["confirmed_at"] for child in children)
    assert not any(center["level"] > 2 for center in result["centers"])
    assert all(item["status"] == "provisional" for item in result["movements"] if item["level"] == 2)
    assert {item["id"] for item in centers} <= {item["id"] for item in result["centers"]}


def test_parent_candidate_id_stays_stable_when_third_movement_finishes():
    units, centers = structural_stream(4)
    movements = build_hierarchy_components(units, centers, 1)["movements"]
    candidate = _parent_centers(movements[:2], 2, "system", centers)[0]
    confirmed = _parent_centers(movements[:3], 2, "system", centers)[0]
    assert candidate["status"] == "provisional" and candidate["progress"] == "2/3"
    assert candidate["confirmed_at"] is None
    assert candidate["id"] == confirmed["id"]
    assert confirmed["status"] == "confirmed"


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
    parent = _parent_centers(movements, 3, "system", centers)[0]
    assert parent["level"] == 3 and parent["status"] == "confirmed"
