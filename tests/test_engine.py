from app.engine import (ProcessedBar, StrictFractal, _special_secondary_pen, build_pens,
                         find_fractals, find_gaps, normalize_bars, process_inclusions)
from app.period_structure import build_pen_centers


def raw(triples):
    return [{"trade_date": f"2026-01-02 09:{35+i*5:02d}:00", "open": (h+l)/2, "high": h, "low": l,
             "close": (h+l)/2, "volume": 1} for i, (h, l) in enumerate(triples)]


def fx(index, kind, price, trio_high, trio_low):
    return StrictFractal(index, kind, price, f"{index:04d}", f"{index+1:04d}", index-1, index+1, index-1, index+1, trio_high, trio_low)


def pen(i, a, b, status="confirmed"):
    return {"id": f"p{i}", "ordinal": i, "start_date": f"{i:04d}", "end_date": f"{i+1:04d}",
            "direction": "up" if b > a else "down", "start_price": a, "end_price": b,
            "confirmed_at": f"{i+2:04d}", "status": status}


def test_inclusion_left_to_right_and_strict_fractal_confirmation():
    bars = normalize_bars(raw([(10, 5), (9, 6), (11, 7), (9, 4), (8, 3)]))
    processed = process_inclusions(bars)
    assert (processed[0].high, processed[0].low) == (10, 6)
    fractals = find_fractals(processed)
    assert fractals[0].kind == "top"
    assert fractals[0].confirmed_at == processed[2].source_end_date


def test_standard_pen_requires_independent_bar_and_basic_direction():
    bottom = fx(1, "bottom", 1, 2, .5)
    assert build_pens([bottom, fx(4, "top", 6, 6, 5)]) == []
    assert len(build_pens([bottom, fx(5, "top", 6, 6, 5)])) == 1
    assert len(build_pens([bottom, fx(5, "top", 1.8, 2, 1.2)])) == 1


def processed_context(values):
    return [ProcessedBar(i, str(i), 0, high, low, 0, 0, 0, i, i,
                          str(i), str(i), str(i), str(i), None)
            for i, (high, low) in enumerate(values)]


def test_standard_pen_requires_directional_reverse_extreme_breakout():
    processed = processed_context([(4, 2), (6, 3), (5, 3), (5, 3), (5, 3), (5, 2), (5, 2)])
    bottom = fx(0, "bottom", 2, 6, 2)
    candidate_top = fx(4, "top", 5, 5, 3)
    diagnostics = []
    assert build_pens([bottom, candidate_top], processed=processed, gaps=[], diagnostics=diagnostics) == []
    assert diagnostics[-1]["reason"] == "reverse_extreme_not_broken"


def test_standard_pen_continues_to_later_stronger_endpoint():
    processed = processed_context([(4, 2), (6, 3), (5, 3), (5, 3), (5, 3), (5, 2), (12, 8), (11, 7)])
    bottom = fx(0, "bottom", 2, 6, 2)
    weak_top = fx(4, "top", 5, 5, 3)
    strong_top = fx(6, "top", 12, 12, 8)
    pens = build_pens([bottom, weak_top, strong_top], processed=processed, gaps=[])
    assert [(item.start_date, item.end_date) for item in pens] == [(bottom.trade_date, strong_top.trade_date)]


def test_standard_pen_downward_breaks_start_right_low():
    processed = processed_context([(10, 8), (9, 7), (8, 6), (8, 6), (7, 7.5), (7, 6)])
    top = fx(0, "top", 10, 10, 8)
    candidate_bottom = fx(4, "bottom", 7.5, 7, 7.5)
    assert build_pens([top, candidate_bottom], processed=processed, gaps=[]) == []


def test_secondary_extreme_pen_uses_four_bars_and_retracement_ratio():
    processed = [ProcessedBar(i, str(i), 0, 10 if i == 3 else 2,
                              0 if i == 5 else 1, 0, 0, 0, i, i,
                              str(i), str(i), str(i), str(i), None)
                 for i in range(6)]
    anchor = fx(0, "bottom", 1, 2, .5)
    extreme = fx(3, "top", 5.5, 5.5, 4.5)
    secondary = fx(5, "top", 4.5, 4.5, 3.5)
    fractals = [anchor, extreme, secondary]
    assert _special_secondary_pen(anchor, secondary, fractals, processed)
    result = build_pens(fractals, processed=processed, gaps=[])
    assert any(item.kind == "secondary" for item in result)
    assert all(left.end_date == right.start_date for left, right in zip(result, result[1:]))
    too_deep = [anchor, extreme, fx(5, "top", 2.5, 2.5, 1.5)]
    assert not _special_secondary_pen(anchor, too_deep[-1], too_deep, processed)


def test_secondary_pen_rejects_four_bar_candidate_when_first_extreme_is_too_close():
    processed = [ProcessedBar(i, str(i), 0, 10, 0, 0, 0, 0, i, i,
                              str(i), str(i), str(i), str(i), None)
                 for i in range(4)]
    anchor = fx(0, "top", 10, 10, 1)
    first_bottom = fx(1, "bottom", 2, 2, 2)
    secondary_bottom = fx(3, "bottom", 3, 3, 3)
    fractals = [anchor, first_bottom, fx(2, "top", 6, 6, 4), secondary_bottom]
    assert not _special_secondary_pen(anchor, secondary_bottom, fractals, processed)
    assert not any(item.kind == "secondary" for item in build_pens(fractals, processed=processed, gaps=[]))


def test_secondary_pen_allows_exactly_fifty_percent_retracement():
    processed = [ProcessedBar(i, str(i), 0, 10, 0, 0, 0, 0, i, i,
                              str(i), str(i), str(i), str(i), None)
                 for i in range(6)]
    anchor = fx(0, "bottom", 1, 2, .5)
    extreme = fx(3, "top", 5, 5, 4)
    secondary = fx(5, "top", 3, 3, 2)
    assert _special_secondary_pen(anchor, secondary, [anchor, extreme, secondary], processed)


def test_secondary_low_pen_is_symmetric_and_must_be_weaker_than_first_low():
    processed = [ProcessedBar(i, str(i), 0, 10, 0, 0, 0, 0, i, i,
                              str(i), str(i), str(i), str(i), None)
                 for i in range(6)]
    anchor = fx(0, "top", 9, 9, 8)
    extreme = fx(3, "bottom", 1, 2, 1)
    secondary = fx(5, "bottom", 3, 4, 3)
    assert _special_secondary_pen(anchor, secondary, [anchor, extreme, secondary], processed)
    new_lower_low = fx(5, "bottom", .5, 1, .5)
    assert not _special_secondary_pen(anchor, new_lower_low,
                                      [anchor, extreme, new_lower_low], processed)


def test_repaired_tail_recovers_skipped_more_extreme_fractal():
    processed = processed_context([
        (5, 4), (6, 3), (6, 3), (6, 3), (6, 3),
        (20, 10), (19, 9), (18, 8), (7, 2), (10, 5),
        (22, 15), (21, 14), (20, 13), (8, 4), (5, 1),
    ])
    start = fx(0, "bottom", 5, 6, 4)
    first_top = fx(5, "top", 20, 20, 10)
    provisional_bottom = fx(8, "bottom", 7, 16, 7)
    skipped_higher_top = fx(10, "top", 22, 22, 15)
    stable_bottom = fx(14, "bottom", 5, 15, 5)
    pens = build_pens(
        [start, first_top, provisional_bottom, skipped_higher_top, stable_bottom],
        processed=processed,
        gaps=[],
    )
    assert [(item.start_date, item.end_date) for item in pens] == [
        (start.trade_date, skipped_higher_top.trade_date),
        (skipped_higher_top.trade_date, stable_bottom.trade_date),
    ]
    assert pens[0].end_price == 22


def test_gap_pen_threshold_confirmation_and_daily_exclusion():
    bars = normalize_bars(raw([(100, 99), (104, 103), (105, 103.5), (106, 104), (107, 104.5)]), "399673")
    gaps = find_gaps(bars)
    minute = build_pens([], processed=process_inclusions(bars), gaps=gaps, symbol="399673", raw_bars=bars, timeframe="5")
    assert len(minute) == 1 and minute[0].kind == "gap"
    assert minute[0].confirmed_at == bars[4].trade_date
    daily = build_pens([], processed=process_inclusions(bars), gaps=gaps, symbol="399673", raw_bars=bars, timeframe="d")
    assert daily == []


def test_stock_and_index_gap_thresholds_are_different():
    bars = normalize_bars(raw([(100, 99), (102, 101.5), (103, 102), (104, 102.5), (105, 103)]))
    gaps = find_gaps(bars)
    index_pens = build_pens([], process_inclusions(bars), gaps, "399673", bars, timeframe="5")
    stock_pens = build_pens([], process_inclusions(bars), gaps, "600000", bars, timeframe="5")
    assert len(index_pens) == 1
    assert stock_pens == []


def test_directional_l1_center_up_and_down_entry():
    up = [pen(i, a, b) for i, (a, b) in enumerate([(1, 6), (6, 2), (2, 7), (7, 3)])]
    result = build_pen_centers(up)
    assert len(result) == 1
    assert result[0]["direction"] == "up" and (result[0]["zd"], result[0]["zg"]) == (3, 6)
    assert result[0]["entry_pen_id"] == "p0" and result[0]["core_pen_ids"] == ["p1", "p2", "p3"]
    assert result[0]["start_date"] == up[1]["start_date"]
    down = [pen(i, a, b) for i, (a, b) in enumerate([(8, 2), (2, 7), (7, 1), (1, 6)])]
    assert build_pen_centers(down)[0]["direction"] == "down"


def test_directional_center_requires_strict_entry_and_core_overlap():
    not_outside = [pen(i, a, b) for i, (a, b) in enumerate([(4, 6), (6, 2), (2, 7), (7, 3)])]
    assert build_pen_centers(not_outside) == []
    boundary_equal = [pen(i, a, b) for i, (a, b) in enumerate([(3, 6), (6, 2), (2, 7), (7, 3)])]
    assert build_pen_centers(boundary_equal) == []
    point_touch = [pen(i, a, b) for i, (a, b) in enumerate([(1, 6), (6, 5), (2, 5), (5, 3)])]
    assert build_pen_centers(point_touch) == []
    provisional = [pen(i, a, b) for i, (a, b) in enumerate([(1, 6), (6, 2), (2, 7), (7, 3)])]
    provisional[-1]["status"] = "provisional"
    assert build_pen_centers(provisional) == []


def test_directional_center_consumes_four_pens_and_failure_slides_one():
    values = [pen(i, a, b) for i, (a, b) in enumerate([
        (1, 6), (6, 2), (2, 7), (7, 3),
        (3, 9), (9, 4), (4, 10), (10, 5),
    ])]
    centers = build_pen_centers(values)
    assert len(centers) == 1
    assert centers[0]["formation_pen_ids"] == ["p0", "p1", "p2", "p3"]
    assert centers[0]["source_pen_ids"] == [f"p{i}" for i in range(8)]
    assert centers[0]["extension_pen_ids"] == ["p4", "p5", "p6", "p7"]
    shifted = [pen(0, 4, 6)] + [pen(i + 1, a, b) for i, (a, b) in enumerate([(7, 1), (1, 7), (7, 2), (2, 6)])]
    assert build_pen_centers(shifted)[0]["entry_pen_id"] == "p1"


def test_l1_extension_keeps_fixed_core_and_updates_envelope():
    values = [pen(i, a, b) for i, (a, b) in enumerate([
        (1, 6), (6, 2), (2, 7), (7, 3), (3, 9), (9, 4),
    ])]
    center = build_pen_centers(values)[0]
    assert (center["fixed_zd"], center["fixed_zg"]) == (3, 6)
    assert center["extension_pen_ids"] == ["p4", "p5"]
    assert center["end_date"] == values[-1]["end_date"]
    assert (center["dd"], center["gg"]) == (1, 9)


def test_temporary_departure_reentry_is_peripheral_not_new_center():
    values = [pen(i, a, b) for i, (a, b) in enumerate([
        (1, 6), (6, 2), (2, 7), (7, 3), (3, 8), (8, 7), (7, 2),
    ])]
    center = build_pen_centers(values)[0]
    assert center["peripheral_pen_ids"] == ["p5"]
    assert center["extension_pen_ids"] == ["p4", "p6"]
    assert center["tail_status"] == "active_extension"


def test_confirmed_outside_center_rolls_crossing_pen_back_to_departure():
    values = [pen(i, a, b) for i, (a, b) in enumerate([
        (1, 6), (6, 2), (2, 7), (7, 3),
        (3, 10), (10, 8), (8, 12), (12, 9),
    ])]
    centers = build_pen_centers(values)
    assert len(centers) == 2
    assert centers[0]["end_pen"] == "p3"
    assert centers[0]["departure_pen_ids"] == ["p4"]
    assert centers[1]["entry_pen_id"] == "p4"
    assert centers[0]["termination_reason"] == "independent_center"


def test_centers_never_cross_pen_sequence_boundary():
    pens = [pen(i, a, b) for i, (a, b) in enumerate([(1,6),(6,2),(2,7),(7,3),(3,9),(9,4),(4,10),(10,5)])]
    for item in pens[4:]:
        item["sequence_id"] = 1
    centers = build_pen_centers(pens)
    assert len(centers) == 2
    assert all(len({pens[int(pid[1:])].get("sequence_id", 0) for pid in center["pen_ids"]}) == 1 for center in centers)
    assert all(center["level"] == 1 for center in centers)
