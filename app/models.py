from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


@dataclass
class Bar:
    symbol: str
    trade_date: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0
    adjust_factor: float = 1.0
    is_suspended: bool = False
    limit_up: float | None = None
    limit_down: float | None = None

    def json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Signal:
    signal: str
    index: int
    price: float
    confidence: float
    evidence: list[str]
    invalidation: float | None
    entry_range: list[float] | None
    time_stop_bars: int
    status: str = "paper_only"

    def json(self):
        return asdict(self)
