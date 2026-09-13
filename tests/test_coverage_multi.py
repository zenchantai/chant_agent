from datetime import datetime

from app.coverage import EXPECTED_30M_TIMES, validate_coverage


def _bars(day, clocks):
    return [{"trade_date": f"{day} {clock}:00", "open": 1, "high": 2, "low": 1, "close": 1.5, "volume": 1} for clock in clocks]


def test_30m_uses_own_grid():
    rows = _bars("2026-01-05", EXPECTED_30M_TIMES)
    result = validate_coverage(rows, "30", expected_trading_dates=["2026-01-05"])
    assert result["status"] == "complete" and result["complete"]
    assert validate_coverage(rows[:-1], "30", expected_trading_dates=["2026-01-05"])["status"] == "incomplete"


def test_current_session_is_partial_not_missing():
    rows = _bars("2026-01-05", EXPECTED_30M_TIMES[:2])
    result = validate_coverage(rows, "30", expected_trading_dates=["2026-01-05"], now=datetime(2026, 1, 5, 10, 45))
    assert result["status"] == "partial"
    assert result["partial_sessions"][0]["date"] == "2026-01-05"


def test_week_and_month_missing_are_period_keys():
    rows = [{"trade_date": "2026-01-05", "open": 1, "high": 2, "low": 1, "close": 1.5, "volume": 1},
            {"trade_date": "2026-02-02", "open": 1, "high": 2, "low": 1, "close": 1.5, "volume": 1}]
    weekly = validate_coverage(rows, "w", expected_trading_dates=["2026-01-05", "2026-01-12", "2026-02-02"])
    monthly = validate_coverage(rows, "m", expected_trading_dates=["2026-01-05", "2026-02-02"])
    assert "2026-W03" in weekly["missing_sessions"]
    assert monthly["missing_sessions"] == []
