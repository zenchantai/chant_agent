from app.chan_structure import build_level_centers, build_structure_hierarchy
from app.engine import StrictFractal, build_pens, normalize_bars, process_inclusions, find_fractals
from tests.chan_fixtures import pens_from_prices


def _raw(values):
    return [
        {
            "trade_date": f"2026-01-02 09:{35 + index * 5:02d}:00",
            "open": (high + low) / 2,
            "high": high,
            "low": low,
            "close": (high + low) / 2,
            "volume": 1,
        }
        for index, (high, low) in enumerate(values)
    ]


def _fractal(index, kind, price, trio_high, trio_low):
    return StrictFractal(
        index, kind, price, f"{index:04d}", f"{index + 1:04d}", index - 1,
        index + 1, index - 1, index + 1, trio_high, trio_low,
    )


def test_standard_pen_and_inclusion_rules_remain_available():
    processed = process_inclusions(normalize_bars(_raw([(10, 5), (9, 6), (11, 7), (9, 4), (8, 3)])))
    assert (processed[0].high, processed[0].low) == (10, 6)
    assert find_fractals(processed)[0].kind == "top"
    bottom = _fractal(1, "bottom", 1, 2, 0.5)
    assert build_pens([bottom, _fractal(4, "top", 6, 6, 5)]) == []
    assert len(build_pens([bottom, _fractal(5, "top", 6, 6, 5)])) == 1


def test_requirement_examples_choose_non_empty_entry_then_earliest_core():
    first, _, _ = build_level_centers(pens_from_prices([1, 10, 6, 15, 8, 20, 16]), 1)
    assert len(first) == 1
    center = first[0]
    assert center["entry_unit_ids"] == ["p0"]
    assert center["core_unit_ids"] == ["p1", "p2", "p3"]
    assert (center["fixed_zd"], center["fixed_zg"]) == (8, 10)

    second, _, _ = build_level_centers(pens_from_prices([1, 5, 3, 10, 7, 15, 13]), 1)
    # Upward context cannot borrow the overlapping up/down/up triple.
    assert all(c["formation_stage"] == "origin_overlap" for c in second)
    assert not any(c["formation_stage"] == "directional" for c in second)


def test_turning_center_excludes_entry_and_uses_13_16_core():
    result, _, _ = build_level_centers(pens_from_prices([20, 14, 18, 13, 16, 8]), 1)
    center = result[0]
    assert center["entry_unit_ids"] == ["p0"]
    assert center["core_unit_ids"] == ["p1", "p2", "p3"]
    assert (center["fixed_zd"], center["fixed_zg"]) == (14, 16)
    assert center["departure_unit_ids"] == ["p4"]


def test_single_point_touch_and_unconfirmed_unit_do_not_form_center():
    touching = pens_from_prices([1, 6, 5, 2, 5])
    assert build_level_centers(touching, 1)[0] == []
    provisional = pens_from_prices([1, 10, 6, 15, 8])
    provisional[3]["status"] = "provisional"
    hierarchy = build_structure_hierarchy(provisional, [], [])
    assert len(hierarchy["centers"]) == 1
    assert hierarchy["centers"][0]["formation_stage"] == "origin_overlap"
    assert "p3" not in hierarchy["centers"][0]["owned_unit_ids"]


def test_preview_hierarchy_can_render_a_provisional_tail_without_changing_formal_default():
    pens = pens_from_prices([1, 10, 6, 15, 8])
    pens[-1]["status"] = "provisional"
    pens[-1]["confirmed_at"] = None

    formal = build_structure_hierarchy(pens, [], [])
    preview = build_structure_hierarchy(pens, [], [], include_provisional=True)

    assert all(unit["status"] != "provisional" for unit in formal["centers"])
    assert preview["centers"][0]["status"] == "provisional"
    assert preview["centers"][0]["boundary_status"] == "dynamic"
    assert "movements" not in preview and "points" not in preview
