import json
from datetime import date, timedelta

from app.coverage import EXPECTED_5M_TIMES, validate_5m_coverage
from app.period_structure import PeriodStructureService, build_pen_centers, color_period_for_level
from app.rules import load_rulebook
from app.store import Store


def session_rows(days=12, mutate=None):
    rows = []
    day = date(2026, 1, 5)
    bar_index = 0
    made = 0
    while made < days:
        if day.weekday() < 5:
            for clock in EXPECTED_5M_TIMES:
                wave = [1, 2, 3, 4, 5, 4, 3, 2][bar_index % 8] + bar_index // 160 * .05
                stamp = f"{day.isoformat()} {clock}:00"
                rows.append({"trade_date": stamp, "open": wave-.1, "high": wave+.4, "low": wave-.4,
                             "close": wave+.1, "volume": 100, "amount": 1000})
                bar_index += 1
            made += 1
        day += timedelta(days=1)
    if mutate is not None:
        rows[-1].update(open=mutate-.1, high=mutate+.4, low=mutate-.4, close=mutate+.1)
    return rows


def seed(store, symbol, rows, timeframe="5"):
    store.upsert_bars(symbol, timeframe, "2", rows)


def pen(i, start, end):
    return {"id": f"p{i}", "ordinal": i, "start_date": f"{i:04d}", "end_date": f"{i+1:04d}",
            "start_price": start, "end_price": end, "confirmed_at": f"{i+2:04d}", "status": "confirmed"}


def test_rulebook_version_and_coverage_gate():
    assert load_rulebook()["version"] == "chan-period-center-hierarchy-same-level-color-v12"
    rows = session_rows(1)
    dates = sorted({x["trade_date"][:10] for x in rows})
    assert validate_5m_coverage(rows, expected_trading_dates=dates)["complete"] is True
    assert validate_5m_coverage(rows[:-1], expected_trading_dates=dates)["complete"] is False


def test_period_snapshot_is_persisted_and_reproducible(tmp_path):
    rows = session_rows()
    first_store = Store(str(tmp_path / "first.db")); seed(first_store, "000001", rows)
    first = PeriodStructureService(first_store).ensure("000001", "5", force=True)
    second_store = Store(str(tmp_path / "second.db")); seed(second_store, "000001", rows)
    second = PeriodStructureService(second_store).ensure("000001", "5", force=True)
    assert first["available"] is True
    assert first["structure_version"] == second["structure_version"]
    for key in ("processed_bars", "fractals", "pens", "pen_centers", "movements"):
        assert first[key] == second[key]
    active = first_store.active_period_structure_run("000001", "5")
    assert active["structure_version"] == first["structure_version"]
    assert first["max_available_center_level"] >= 1
    assert first["center_relations"] == second["center_relations"]
    assert first_store.period_rows("period_movements", active["id"]) == first["movements"]
    assert active["movement_count"] == len(first["movements"])
    assert active["hierarchy_input_hash"] == first["hierarchy_input_hash"]
    assert first_store.period_rows("period_center_relations", active["id"]) == first["center_relations"]
    assert json.loads(active["center_level_counts"]) == {
        str(level): sum(item["level"] == level for item in first["centers"])
        for level in sorted({item["level"] for item in first["centers"]})
    }
    pen_ids = {item["id"] for item in first["pens"]}
    l1_centers = [item for item in first["centers"]
                  if item["level"] == 1 and item.get("role") == "hierarchy"]
    assert l1_centers
    assert all("R0-R0-" not in item["id"] for item in first["centers"])
    assert all(item["source_pen_ids"] and set(item["source_pen_ids"]) <= pen_ids
               for item in l1_centers)
    assert all(len(item["formation_pen_ids"]) == 4 for item in l1_centers)
    center_ids = {item["id"] for item in first["centers"]}
    movement_ids = {item["id"] for item in first["movements"]}
    unit_ids = pen_ids | movement_ids
    assert all(set(item.get("center_ids", [])) <= center_ids for item in first["movements"])
    assert all(set(item.get("child_movement_ids", [])) <= movement_ids for item in first["movements"])
    assert all(set(item.get("source_unit_ids", [])) <= unit_ids for item in first["movements"])
    assert all(set(item.get("source_pen_ids", [])) <= pen_ids for item in first["movements"])
    assert all(set(item.get("child_center_ids", [])) <= center_ids for item in first["centers"])
    assert all(set(item.get("child_movement_ids", [])) <= movement_ids for item in first["centers"])
    assert all(set(item.get("core_unit_ids", [])) <= unit_ids for item in first["centers"])
    assert all(set(item.get("source_pen_ids", [])) <= pen_ids for item in first["centers"])
    assert all(set(item.get("parent_center_ids", [])) <= center_ids for item in first["centers"])
    assert all({item["previous_center_id"], item["current_center_id"]} <= center_ids
               for item in first["center_relations"])


def test_timeframes_are_fully_isolated(tmp_path):
    store = Store(str(tmp_path / "isolated.db"))
    five = session_rows(2)
    daily = [{"trade_date": f"2026-01-{i+1:02d}", "open": i+1, "high": i+2,
              "low": i+.5, "close": i+1.5, "volume": 100} for i in range(12)]
    seed(store, "000001", five, "5"); seed(store, "000001", daily, "d")
    service = PeriodStructureService(store)
    five_before = service.ensure("000001", "5", force=True)["structure_version"]
    daily_before = service.ensure("000001", "d", force=True)["structure_version"]
    daily[-1]["high"] += 3
    store.upsert_bars("000001", "d", "2", daily[-1:])
    daily_after = service.ensure("000001", "d", force=True)["structure_version"]
    five_after = service.ensure("000001", "5")["structure_version"]
    assert daily_after != daily_before
    assert five_after == five_before


def test_market_change_makes_only_that_period_stale(tmp_path):
    store = Store(str(tmp_path / "stale.db")); rows = session_rows()
    seed(store, "000001", rows); service = PeriodStructureService(store)
    before = service.ensure("000001", "5", force=True)
    store.upsert_bars_with_changes("000001", "5", "2", session_rows(mutate=8)[-1:])
    stale = service.load("000001", "5")
    assert stale["available"] is False
    after = service.ensure("000001", "5", force=True)
    assert after["available"] is True and after["market_version"] != before["market_version"]


def test_chart_pagination_uses_same_period_structures(tmp_path):
    store = Store(str(tmp_path / "page.db")); rows = session_rows(15)
    seed(store, "000001", rows); service = PeriodStructureService(store)
    service.ensure("000001", "5", force=True)
    pages = []; before = None
    while True:
        page = service.chart_page("000001", "5", "2", before, 300); pages.extend(page["bars"])
        assert "segments" not in page
        assert all(key in page for key in ("center_relations", "center_levels", "movement_levels",
                                            "movements", "decomposition"))
        assert page["active_structure_level"] == 1
        assert page["centers"] == page["pen_centers"]
        assert all(center["level"] == 1 for center in page["centers"])
        assert all(center.get("role") == "hierarchy" for center in page["centers"])
        assert all(center["display_period"] == "5" and center["color_key"] == "period-5"
                   for center in page["centers"])
        returned_pen_ids = {item["id"] for item in page["pens"]}
        assert all(set(center["source_pen_ids"]) <= returned_pen_ids for center in page["pen_centers"])
        assert all(set(center.get("departure_pen_ids", [])) <= returned_pen_ids for center in page["pen_centers"])
        assert all(page["bars"][0]["trade_date"] <= point["trade_date"] <= page["bars"][-1]["trade_date"]
                   for movement in page["movements"] for point in movement["path_points"])
        if not page["has_more"]: break
        before = page["next_before"]
    assert len(pages) == len(rows) and len({x["trade_date"] for x in pages}) == len(rows)


def test_color_period_for_level_maps_display_semantics_without_cross_period_recursion():
    assert color_period_for_level("d", 1) == {"display_period": "d", "color_key": "period-d"}
    assert color_period_for_level("d", 2) == {"display_period": "w", "color_key": "period-w"}
    assert color_period_for_level("d", 3) == {"display_period": "m", "color_key": "period-m"}
    assert color_period_for_level("d", 4) == {
        "display_period": "higher", "color_key": "structure-higher-L4",
    }
    assert color_period_for_level("w", 1) == {"display_period": "w", "color_key": "period-w"}
    assert color_period_for_level("w", 2) == {"display_period": "m", "color_key": "period-m"}
    assert color_period_for_level("m", 2) == {
        "display_period": "higher", "color_key": "structure-higher-L2",
    }
    assert color_period_for_level("30", 1) == {"display_period": "30", "color_key": "period-30"}
    assert color_period_for_level("30", 3) == {
        "display_period": "30", "color_key": "period-30-level-L3",
    }


def test_chart_macd_is_stable_across_page_sizes(tmp_path):
    store = Store(str(tmp_path / "macd-page.db")); rows = session_rows(15)
    seed(store, "000001", rows); service = PeriodStructureService(store)
    service.ensure("000001", "5", force=True)
    small = service.chart_page("000001", "5", "2", None, 100)["indicators"]["macd"]
    large = service.chart_page("000001", "5", "2", None, 300)["indicators"]["macd"]
    large_by_date = {item["trade_date"]: item for item in large}
    assert small
    assert all(item == large_by_date[item["trade_date"]] for item in small)


def test_active_manual_override_rebuilds_effective_movements(tmp_path):
    store = Store(str(tmp_path / "manual-movement.db")); rows = session_rows()
    seed(store, "000001", rows); service = PeriodStructureService(store)
    system = service.ensure("000001", "5", force=True)
    assert system["movements"]
    base = store.active_period_structure_run("000001", "5", "2")
    store.create_structure_override({
        "symbol": "000001", "timeframe": "5", "adjustflag": "2",
        "structure_type": "pen", "operation": "update", "target_id": system["pens"][0]["id"],
        "payload": {"kind": "manual-test"}, "base_run_id": base["id"],
        "base_structure_version": base["structure_version"],
    }, base)
    effective = service.effective_structure("000001", "5", "2")
    assert effective["movements"]
    assert all(item["origin"] == "manual-derived" for item in effective["movements"])
    assert effective["decomposition"]["movement_input_hash"] != ""


def test_directional_pen_center_uses_entry_plus_three_core_pens():
    pens = [pen(0, 1, 6), pen(1, 6, 2), pen(2, 2, 7), pen(3, 7, 3)]
    centers = build_pen_centers(pens)
    assert len(centers) == 1
    assert (centers[0]["zd"], centers[0]["zg"]) == (3, 6)
    assert centers[0]["entry_pen_id"] == "p0"
    assert centers[0]["core_pen_ids"] == ["p1", "p2", "p3"]
    assert centers[0]["status"] == "confirmed"


def test_directional_pen_center_rejects_single_point_touch_and_provisional_pen():
    assert build_pen_centers([pen(0, 1, 6), pen(1, 6, 5), pen(2, 2, 5), pen(3, 5, 3)]) == []
    values = [pen(0, 1, 6), pen(1, 6, 2), pen(2, 2, 7), pen(3, 7, 3)]
    values[-1]["status"] = "provisional"
    assert build_pen_centers(values) == []
