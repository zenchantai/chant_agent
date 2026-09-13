import pytest

from app.indicators import calculate_macd, calculate_moving_averages


def rows(values):
    return [{"trade_date": f"2026-01-{index + 1:02d}", "close": value}
            for index, value in enumerate(values)]


def test_macd_uses_standard_12_26_9_formula():
    result = calculate_macd(rows([1, 2]))
    assert result[0] == {"trade_date": "2026-01-01", "dif": 0, "dea": 0, "histogram": 0}
    assert result[1]["dif"] == pytest.approx(2 / 13 - 2 / 27)
    assert result[1]["dea"] == pytest.approx(result[1]["dif"] * 2 / 10)
    assert result[1]["histogram"] == pytest.approx(2 * (result[1]["dif"] - result[1]["dea"]))


def test_macd_resets_at_each_continuous_range():
    data = rows([1, 2, 8, 9])
    ranges = [
        {"start_date": "2026-01-01", "end_date": "2026-01-02"},
        {"start_date": "2026-01-03", "end_date": "2026-01-04"},
    ]
    result = calculate_macd(data, ranges)
    assert result[2]["dif"] == 0
    assert result[2]["dea"] == 0
    assert result[2]["histogram"] == 0


def test_moving_averages_use_full_ordered_history():
    result = calculate_moving_averages(rows(list(range(1, 22))))
    assert result[3]["ma5"] is None
    assert result[4]["ma5"] == 3
    assert result[9]["ma10"] == 5.5
    assert result[19]["ma20"] == 10.5
