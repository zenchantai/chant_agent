from __future__ import annotations

from typing import Any


def calculate_moving_averages(rows: list[dict[str, Any]], periods: tuple[int, ...] = (5, 10, 20)) -> list[dict[str, Any]]:
    """Calculate close-price moving averages without losing page-boundary context."""
    ordered = sorted(rows, key=lambda item: item["trade_date"])
    closes: list[float] = []
    result: list[dict[str, Any]] = []
    for row in ordered:
        closes.append(float(row["close"]))
        point: dict[str, Any] = {"trade_date": row["trade_date"]}
        for period in periods:
            point[f"ma{period}"] = sum(closes[-period:]) / period if len(closes) >= period else None
        result.append(point)
    return result


def calculate_bollinger(rows: list[dict[str, Any]], period: int = 20, multiplier: float = 2.0) -> list[dict[str, Any]]:
    """Calculate rolling close-price Bollinger bands."""
    ordered = sorted(rows, key=lambda item: item["trade_date"])
    closes: list[float] = []
    result: list[dict[str, Any]] = []
    for row in ordered:
        closes.append(float(row["close"]))
        window = closes[-period:]
        middle = sum(window) / period if len(window) >= period else None
        deviation = (sum((value - middle) ** 2 for value in window) / period) ** 0.5 if middle is not None else None
        result.append({"trade_date": row["trade_date"], "middle": middle,
                       "upper": middle + multiplier * deviation if middle is not None else None,
                       "lower": middle - multiplier * deviation if middle is not None else None})
    return result


def calculate_macd(rows: list[dict[str, Any]],
                   continuous_ranges: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Calculate MACD(12, 26, 9), resetting EMA at each verified range."""
    ranges = continuous_ranges or []

    def range_id(stamp: str) -> int:
        day = stamp[:10]
        for index, item in enumerate(ranges):
            if item["start_date"][:10] <= day <= item["end_date"][:10]:
                return index
        return -1

    alpha12, alpha26, alpha9 = 2 / 13, 2 / 27, 2 / 10
    ema12 = ema26 = dea = 0.0
    previous_range: int | None = None
    result: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda item: item["trade_date"]):
        current_range = range_id(row["trade_date"]) if ranges else 0
        close = float(row["close"])
        if current_range != previous_range:
            ema12 = ema26 = close
            dea = 0.0
        else:
            ema12 = alpha12 * close + (1 - alpha12) * ema12
            ema26 = alpha26 * close + (1 - alpha26) * ema26
        dif = ema12 - ema26
        dea = alpha9 * dif + (1 - alpha9) * dea
        result.append({
            "trade_date": row["trade_date"],
            "dif": dif,
            "dea": dea,
            "histogram": 2 * (dif - dea),
        })
        previous_range = current_range
    return result
