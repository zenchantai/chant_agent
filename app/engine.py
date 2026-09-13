from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from .models import Bar


@dataclass
class ProcessedBar:
    index: int; trade_date: str; open: float; high: float; low: float; close: float; volume: float; amount: float
    source_start: int; source_end: int; source_start_date: str; source_end_date: str; high_date: str; low_date: str
    direction: str | None


@dataclass
class StrictFractal:
    index: int; kind: str; price: float; trade_date: str; confirmed_at: str; left_index: int; right_index: int
    source_start: int; source_end: int; trio_high: float; trio_low: float


@dataclass
class GapEvent:
    id: str; direction: str; start: int; end: int; start_date: str; end_date: str; start_price: float
    end_price: float; gap_lower: float; gap_upper: float; confirmed_at: str


@dataclass
class Pen:
    id: str; ordinal: int; start: int; end: int; start_date: str; end_date: str; direction: str
    start_price: float; end_price: float; confirmed_at: str; status: str = "confirmed"; kind: str = "standard"
    sequence_id: int = 0


def normalize_bars(rows: list[dict[str, Any]], symbol: str = "DEMO") -> list[Bar]:
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        stamp = str(row.get("trade_date", row.get("date", row.get("datetime", "")))).strip()
        if not stamp:
            raise ValueError("K线缺少 trade_date")
        unique[stamp] = row
    result = []
    for stamp in sorted(unique):
        row = unique[stamp]
        o, h, low, c = (float(row[key]) for key in ("open", "high", "low", "close"))
        if not np.isfinite([o, h, low, c]).all():
            raise ValueError(f"非法OHLC: {stamp}")
        tolerance = max(abs(o), abs(h), abs(low), abs(c), 1.0) * 1e-6
        if max(o, c, low) - h > tolerance or low - min(o, c, h) > tolerance:
            raise ValueError(f"非法OHLC: {stamp}")
        h, low = max(h, o, c, low), min(low, o, c, h)
        bar = Bar(symbol=symbol, trade_date=stamp, open=o, high=h, low=low, close=c,
                  volume=float(row.get("volume", 0) or 0), adjust_factor=float(row.get("adjust_factor", 1) or 1),
                  is_suspended=bool(row.get("is_suspended", False)), limit_up=row.get("limit_up"), limit_down=row.get("limit_down"))
        setattr(bar, "amount", float(row.get("amount", 0) or 0))
        result.append(bar)
    return result


def _contains(a: ProcessedBar, high: float, low: float) -> bool:
    return (a.high >= high and a.low <= low) or (high >= a.high and low <= a.low)


def _direction(a: ProcessedBar, high: float, low: float) -> str | None:
    if high > a.high and low > a.low: return "up"
    if high < a.high and low < a.low: return "down"
    return None


def _lookahead_direction(bars: list[Bar], start: int, last: ProcessedBar) -> str | None:
    high, low = last.high, last.low
    for bar in bars[start:]:
        if (high >= bar.high and low <= bar.low) or (bar.high >= high and bar.low <= low):
            high, low = max(high, bar.high), min(low, bar.low)
        elif bar.high > high and bar.low > low: return "up"
        elif bar.high < high and bar.low < low: return "down"
    return None


def process_inclusions(bars: list[Bar]) -> list[ProcessedBar]:
    if not bars: return []
    first = bars[0]
    out = [ProcessedBar(0, first.trade_date, first.open, first.high, first.low, first.close, first.volume,
                        getattr(first, "amount", 0), 0, 0, first.trade_date, first.trade_date,
                        first.trade_date, first.trade_date, None)]
    trend = None
    for raw_index, bar in enumerate(bars[1:], 1):
        last = out[-1]
        if _contains(last, bar.high, bar.low):
            merge = trend or _lookahead_direction(bars, raw_index + 1, last)
            if merge == "up": high, low = max(last.high, bar.high), max(last.low, bar.low)
            elif merge == "down": high, low = min(last.high, bar.high), min(last.low, bar.low)
            else: high, low = max(last.high, bar.high), min(last.low, bar.low)
            high_date = last.high_date if high == last.high else bar.trade_date
            low_date = last.low_date if low == last.low else bar.trade_date
            out[-1] = ProcessedBar(last.index, last.trade_date, last.open, high, low, bar.close,
                last.volume + bar.volume, last.amount + getattr(bar, "amount", 0), last.source_start, raw_index,
                last.source_start_date, bar.trade_date, high_date, low_date, merge)
            trend = merge or trend
        else:
            trend = _direction(last, bar.high, bar.low) or trend
            out.append(ProcessedBar(len(out), bar.trade_date, bar.open, bar.high, bar.low, bar.close, bar.volume,
                getattr(bar, "amount", 0), raw_index, raw_index, bar.trade_date, bar.trade_date,
                bar.trade_date, bar.trade_date, trend))
    return out


def find_fractals(processed: list[ProcessedBar]) -> list[StrictFractal]:
    result = []
    for i in range(1, len(processed) - 1):
        left, mid, right = processed[i-1], processed[i], processed[i+1]
        if mid.high > left.high and mid.high > right.high and mid.low > left.low and mid.low > right.low:
            result.append(StrictFractal(i, "top", mid.high, mid.high_date, right.source_end_date, i-1, i+1,
                left.source_start, right.source_end, max(left.high, mid.high, right.high), min(left.low, mid.low, right.low)))
        elif mid.low < left.low and mid.low < right.low and mid.high < left.high and mid.high < right.high:
            result.append(StrictFractal(i, "bottom", mid.low, mid.low_date, right.source_end_date, i-1, i+1,
                left.source_start, right.source_end, max(left.high, mid.high, right.high), min(left.low, mid.low, right.low)))
    return result


def find_gaps(bars: list[Bar]) -> list[GapEvent]:
    result = []
    for index, (previous, current) in enumerate(zip(bars, bars[1:]), 1):
        if current.low > previous.high:
            direction, start_price, end_price, lower, upper = "up", previous.high, current.low, previous.high, current.low
        elif current.high < previous.low:
            direction, start_price, end_price, lower, upper = "down", previous.low, current.high, current.high, previous.low
        else: continue
        result.append(GapEvent(f"gap-{previous.trade_date}-{current.trade_date}", direction, index-1, index,
            previous.trade_date, current.trade_date, start_price, end_price, lower, upper, current.trade_date))
    return result


def _more_extreme(old: StrictFractal, new: StrictFractal) -> bool:
    return (new.kind == "top" and new.price > old.price) or (new.kind == "bottom" and new.price < old.price)


def _valid_pen_edge(a: StrictFractal, b: StrictFractal,
                    processed: list[ProcessedBar] | None,
                    fractals: list[StrictFractal] | None = None) -> bool:
    return _valid_pen(a, b, processed) or (
        processed is not None and fractals is not None
        and _special_secondary_pen(a, b, fractals, processed)
    )


def _repair_previous_endpoint(endpoints: list[StrictFractal], fractals: list[StrictFractal],
                              processed: list[ProcessedBar] | None) -> None:
    """Recover extrema skipped while the opposite endpoint was provisional.

    A short secondary reversal can be accepted before its endpoint stabilizes.
    If that endpoint later moves farther away, an intervening same-kind fractal
    may become the true previous endpoint.  The old greedy scan discarded that
    fractal permanently and therefore suffered from path dependence.
    """
    if len(endpoints) < 2:
        return
    previous, current = endpoints[-2], endpoints[-1]
    candidates = [
        item for item in fractals
        if previous.index < item.index < current.index
        and item.kind == previous.kind
        and _valid_pen_edge(item, current, processed, fractals)
    ]
    if not candidates:
        return
    candidates.append(previous)
    if previous.kind == "top":
        endpoints[-2] = min(candidates, key=lambda item: (-item.price, item.index))
    else:
        endpoints[-2] = min(candidates, key=lambda item: (item.price, item.index))


def _valid_pen(a: StrictFractal, b: StrictFractal,
               processed: list[ProcessedBar] | None = None) -> bool:
    if a.kind == b.kind or abs(b.index - a.index) < 4: return False
    top, bottom = (a, b) if a.kind == "top" else (b, a)
    if processed is None or top.index >= len(processed) or bottom.index >= len(processed):
        return top.price > bottom.price
    top_bar, bottom_bar = processed[top.index], processed[bottom.index]
    return top_bar.high > bottom_bar.high and top_bar.low > bottom_bar.low


def _special_secondary_pen(a: StrictFractal, b: StrictFractal,
                           fractals: list[StrictFractal],
                           processed: list[ProcessedBar]) -> bool:
    """Apply the documented secondary-extreme exception.

    The four-bar requirement is measured from the prior endpoint to the
    earlier, more extreme opposite fractal.  The later opposite fractal is
    only the weaker secondary endpoint; its total distance from ``a`` is not
    the four-bar measurement.
    """
    if a.kind == b.kind or abs(b.index - a.index) < 3:
        return False
    candidates = [item for item in fractals
                  if a.index < item.index < b.index and item.kind == b.kind]
    if not candidates:
        return False
    extreme = (min(candidates, key=lambda item: (item.price, item.index))
               if b.kind == "bottom" else
               max(candidates, key=lambda item: (item.price, -item.index)))
    if b.kind == "bottom" and b.price <= extreme.price:
        return False
    if b.kind == "top" and b.price >= extreme.price:
        return False
    # Count processed bars inclusively from the prior endpoint to the actual
    # first extreme, rather than from the endpoint to the secondary fractal.
    if abs(extreme.index - a.index) + 1 < 4:
        return False
    if a.kind == "bottom":
        amplitude = extreme.price - a.price
        retracement = extreme.price - b.price
    else:
        amplitude = a.price - extreme.price
        retracement = b.price - extreme.price
    return amplitude > 0 and 0 <= retracement <= amplitude * 0.5


def _special_gap_pens(fractals: list[StrictFractal], gaps: list[GapEvent], symbol: str,
                      bars: list[Bar] | None = None, timeframe: str = "5") -> list[Pen]:
    """Create documented index/stock gap pens only after three bars fail to
    close the gap.  Daily gaps are intentionally excluded by the caller."""
    if timeframe == "d" or timeframe in {"w", "m"}:
        return []
    threshold = (0.01 if str(symbol).startswith("399") else 0.025) if timeframe == "5" else (0.02 if str(symbol).startswith("399") else 0.05)
    result: list[Pen] = []
    for gap in gaps:
        if bars is not None and gap.end + 3 < len(bars):
            following = bars[gap.end + 1:gap.end + 4]
            if any(bar.low <= gap.gap_upper and bar.high >= gap.gap_lower for bar in following):
                continue
        elif gap.start + 4 >= (max((f.source_end for f in fractals), default=gap.end) + 1):
            continue
        base = max(abs(gap.start_price), 1e-9)
        if (gap.gap_upper - gap.gap_lower) / base < threshold:
            continue
        # A gap pen is valid only while its interval remains open for the
        # next three raw bars.
        confirmed_at = bars[gap.end + 3].trade_date if bars is not None else gap.confirmed_at
        result.append(Pen(f"gap-pen-{gap.id}", 0, gap.start, gap.end, gap.start_date,
                          gap.end_date, gap.direction, gap.start_price, gap.end_price,
                          confirmed_at, kind="gap"))
    return result


def build_pens(fractals: list[StrictFractal], processed: list[ProcessedBar] | None = None,
               gaps: list[GapEvent] | None = None, symbol: str = "", raw_bars: list[Bar] | None = None,
               timeframe: str = "5") -> list[Pen]:
    endpoints: list[StrictFractal] = []
    for fractal in fractals:
        if not endpoints: endpoints.append(fractal)
        elif fractal.kind == endpoints[-1].kind:
            if _more_extreme(endpoints[-1], fractal):
                endpoints[-1] = fractal
                _repair_previous_endpoint(endpoints, fractals, processed)
        elif _valid_pen_edge(endpoints[-1], fractal, processed, fractals):
            endpoints.append(fractal)
    pens = []
    for a, b in zip(endpoints, endpoints[1:]):
        if not _valid_pen_edge(a, b, processed, fractals):
            continue
        kind = "standard" if _valid_pen(a, b, processed) else "secondary"
        pens.append(Pen(f"pen-{len(pens)}-{a.trade_date}-{b.trade_date}", len(pens),
                        a.source_start, b.source_end, a.trade_date, b.trade_date,
                        "up" if a.kind == "bottom" else "down", a.price, b.price,
                        b.confirmed_at, kind=kind))
    if processed is None:
        return pens
    extras = [gap for gap in _special_gap_pens(fractals, gaps or [], symbol, raw_bars, timeframe)
              if not any(pen.start_date <= gap.start_date and pen.end_date >= gap.end_date for pen in pens)]
    merged = pens + extras
    merged.sort(key=lambda p: (p.start_date, p.end_date, p.kind))
    sequence_id = 0
    previous: Pen | None = None
    for ordinal, item in enumerate(merged):
        if previous is not None and not (
            previous.end_date == item.start_date and
            abs(previous.end_price - item.start_price) <= 1e-9 and
            previous.direction != item.direction
        ):
            sequence_id += 1
        item.ordinal = ordinal
        item.sequence_id = sequence_id
        previous = item
    return merged
