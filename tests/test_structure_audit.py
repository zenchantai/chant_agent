import sqlite3

from scripts.audit_structure import EXPECTED_FINGERPRINT, audit_api_payload, enabled_symbols


def test_audit_scope_reads_enabled_stock_pool_only():
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE stock_pool(symbol TEXT, enabled INTEGER, sort_order INTEGER)")
    connection.executemany(
        "INSERT INTO stock_pool VALUES(?,?,?)",
        [("B", 1, 2), ("A", 1, 1), ("C", 0, 0)],
    )
    assert enabled_symbols(connection) == ("A", "B")


def test_api_audit_requires_selected_level_and_color_metadata():
    payload = {
        "timeframe": "d",
        "definition_version": "chan-period-center-hierarchy-cache-fingerprint-v13",
        "calculator_fingerprint": EXPECTED_FINGERPRINT,
        "active_structure_level": 2,
        "max_available_center_level": 3,
        "centers": [{"id": "c", "level": 2, "display_period": "w", "color_key": "period-w"}],
        "pen_centers": [{"id": "c", "level": 2, "display_period": "w", "color_key": "period-w"}],
        "movements": [{"id": "m", "level": 2, "role": "same_level_decomposition"}],
    }
    assert audit_api_payload(payload, 2) == []
    payload["centers"][0]["level"] = 1
    assert any("非当前级别" in problem for problem in audit_api_payload(payload, 2))
