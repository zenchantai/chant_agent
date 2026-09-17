import sqlite3

from scripts.audit_structure import EXPECTED_FINGERPRINT, audit_api_payload, enabled_symbols
from app.rules import PERIOD_DEFINITION_VERSION


def test_audit_scope_reads_enabled_stock_pool_only():
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE stock_pool(symbol TEXT, enabled INTEGER, sort_order INTEGER)")
    connection.executemany(
        "INSERT INTO stock_pool VALUES(?,?,?)",
        [("B", 1, 2), ("A", 1, 1), ("C", 0, 0)],
    )
    assert enabled_symbols(connection) == ("A", "B")


def test_api_audit_requires_all_levels_and_color_metadata():
    payload = {
        "timeframe": "d",
        "definition_version": PERIOD_DEFINITION_VERSION,
        "calculator_fingerprint": EXPECTED_FINGERPRINT,
        "active_structure_level": 1,
        "max_available_center_level": 3,
        "centers": [{"id": "c", "level": 2, "display_period": "w", "color_key": "period-w", "owned_unit_ids": ["u"], "source_unit_ids": ["u"]}],
        "pen_centers": [{"id": "c", "level": 2, "display_period": "w", "color_key": "period-w", "owned_unit_ids": ["u"], "source_unit_ids": ["u"]}],
        "movements": [{"id": "m", "level": 2, "role": "hierarchy_component", "status": "provisional", "center_ids": ["c"], "source_unit_ids": ["u"], "recursive_eligible": False}],
        "pens": [{"id": "p1"}, {"id": "p2"}, {"id": "p3"}],
    }
    assert audit_api_payload(payload, 2) == []
    payload["centers"][0]["level"] = 4
    assert any("级别非法" in problem for problem in audit_api_payload(payload, 2))
