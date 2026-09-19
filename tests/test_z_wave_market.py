import gzip
import json
from pathlib import Path

import pytest

from app.chan_structure import build_structure_hierarchy, validate_structure
from app.indicators import calculate_macd


FIXTURES = json.loads(gzip.decompress((Path(__file__).parent / "fixtures" / "z_wave_market.json.gz").read_bytes()))


@pytest.mark.parametrize("symbol", ["1A0688", "1A0001"])
def test_real_daily_prefixes_preserve_formation_evidence_and_confirmed_boundaries(symbol):
    fixture = FIXTURES[symbol]
    pens, bars = fixture["pens"], fixture["bars"]
    macd = calculate_macd(bars)
    dates = [bar["trade_date"] for bar in bars]
    previous_centers = {}
    confirmed_movements = {}
    for count in range(4, len(pens) + 1):
        result = build_structure_hierarchy(pens[:count], macd, dates)
        assert validate_structure({"pens": pens[:count], **result}) == [], (symbol, count)
        revisions = {center["id"]: center for center in result["center_revisions"]}
        for identifier, previous in previous_centers.items():
            assert identifier in revisions, (symbol, count, identifier)
            for field in ("level", "zd", "zg", "dd", "gg", "z_unit_ids", "formed_at", "promotion_confirmed_at", "evidence", "source_pen_ids"):
                assert previous[field] == revisions[identifier][field], (symbol, count, identifier, field)
        previous_centers = revisions
        movements = {movement["id"]: movement for movement in result["movements"] if movement["status"] == "confirmed"}
        for identifier, previous in confirmed_movements.items():
            assert identifier in movements, (symbol, count, identifier)
            for field in ("start_date", "end_date", "start_price", "end_price", "confirmed_at", "source_unit_ids", "classification", "center_revision_ids"):
                assert previous[field] == movements[identifier][field], (symbol, count, identifier, field)
        confirmed_movements = movements


@pytest.mark.parametrize("symbol", ["1A0688", "1A0001"])
def test_real_daily_expansions_have_actual_z_witness_and_connection(symbol):
    fixture = FIXTURES[symbol]
    result = build_structure_hierarchy(fixture["pens"], calculate_macd(fixture["bars"]), [bar["trade_date"] for bar in fixture["bars"]])
    centers = {center["id"]: center for center in result["center_revisions"]}
    units = {unit["id"]: unit for unit in fixture["pens"]}
    for parent in result["centers"]:
        if "expansion_envelope_overlap" not in parent["formation_modes"]:
            continue
        left, right = [centers[identifier] for identifier in parent["child_center_ids"]]
        witness_left, witness_right = parent["overlap_witness_unit_ids"]
        assert witness_left in left["z_unit_ids"] and witness_right in right["z_unit_ids"]
        assert parent["connection_component_ids"]
        if left["level"] == 1:
            low = max(min(units[identifier]["start_price"], units[identifier]["end_price"]) for identifier in (witness_left, witness_right))
            high = min(max(units[identifier]["start_price"], units[identifier]["end_price"]) for identifier in (witness_left, witness_right))
            assert low + 1e-9 < high
        assert (parent["zd"], parent["zg"]) == (max(left["dd"], right["dd"]), min(left["gg"], right["gg"]))
