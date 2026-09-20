from __future__ import annotations

from datetime import date, timedelta

from app.coverage import EXPECTED_5M_TIMES


def pens_from_prices(prices: list[float], *, sequence_id: int = 0) -> list[dict]:
    pens = []
    for index, (start, end) in enumerate(zip(prices, prices[1:])):
        start_date = (date(2026, 1, 1) + timedelta(days=index)).isoformat()
        end_date = (date(2026, 1, 2) + timedelta(days=index)).isoformat()
        pens.append({
            "id": f"p{index}",
            "kind": "pen",
            "level": 0,
            "ordinal": index,
            "start_date": start_date,
            "end_date": end_date,
            "start_price": float(start),
            "end_price": float(end),
            "direction": "up" if end > start else "down",
            "status": "confirmed",
            "confirmed_at": end_date,
            "continuous_range_id": 0,
            "sequence_id": sequence_id,
            "structure_sequence_id": f"range-0-sequence-{sequence_id}",
        })
    return pens


def session_rows(days: int = 12, mutate: float | None = None) -> list[dict]:
    rows = []
    current = date(2026, 1, 5)
    bar_index = 0
    completed_days = 0
    while completed_days < days:
        if current.weekday() < 5:
            for clock in EXPECTED_5M_TIMES:
                wave = [1, 2, 3, 4, 5, 4, 3, 2][bar_index % 8] + bar_index // 160 * 0.05
                rows.append({
                    "trade_date": f"{current.isoformat()} {clock}:00",
                    "open": wave - 0.1,
                    "high": wave + 0.4,
                    "low": wave - 0.4,
                    "close": wave + 0.1,
                    "volume": 100,
                    "amount": 1000,
                })
                bar_index += 1
            completed_days += 1
        current += timedelta(days=1)
    if mutate is not None:
        rows[-1].update(
            open=mutate - 0.1,
            high=mutate + 0.4,
            low=mutate - 0.4,
            close=mutate + 0.1,
        )
    return rows


def seed(store, symbol: str, rows: list[dict], timeframe: str = "5") -> None:
    store.upsert_bars(symbol, timeframe, "2", rows)


def legacy_expansion_snapshot():
    """Frozen v27 run: projection/display must continue to read old revisions."""
    import json
    from pathlib import Path
    return json.loads((Path(__file__).parent / "fixtures" / "legacy_expansion_v27.json").read_text())
