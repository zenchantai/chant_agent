from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Any, Callable, Iterable

MORNING = [f"{hour:02d}:{minute:02d}" for hour, start, end in ((9, 35, 55), (10, 0, 55), (11, 0, 30)) for minute in range(start, end + 1, 5)]
AFTERNOON = [f"{hour:02d}:{minute:02d}" for hour, start, end in ((13, 5, 55), (14, 0, 55), (15, 0, 0)) for minute in range(start, end + 1, 5)]
EXPECTED_5M_TIMES = MORNING + AFTERNOON
EXPECTED_30M_TIMES = ["10:00", "10:30", "11:00", "11:30", "13:30", "14:00", "14:30", "15:00"]
def _minute_times(step: int) -> list[str]:
    minutes = list(range(9 * 60 + 30 + step, 11 * 60 + 30 + 1, step))
    minutes += list(range(13 * 60 + step, 15 * 60 + 1, step))
    return [f"{value // 60:02d}:{value % 60:02d}" for value in minutes]


EXPECTED_TIMES = {
    "1": _minute_times(1), "5": EXPECTED_5M_TIMES, "15": _minute_times(15),
    "30": EXPECTED_30M_TIMES, "60": _minute_times(60), "120": _minute_times(120),
}


def _parse(value: Any) -> tuple[str, str | None]:
    raw = str(value or "")
    try:
        return raw, datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
    except (TypeError, ValueError):
        return raw, None


def _period(day: str, timeframe: str) -> str:
    d = date.fromisoformat(day)
    if timeframe == "w":
        i = d.isocalendar(); return f"{i.year}-W{i.week:02d}"
    if timeframe == "m": return d.strftime("%Y-%m")
    if timeframe == "y": return d.strftime("%Y")
    return day


def validate_coverage(rows: list[dict[str, Any]], timeframe: str = "5",
                     expected_trading_dates: Iterable[str] | None = None,
                     suspended_dates: Iterable[str] | None = None,
                     declared_start: str | None = None, declared_end: str | None = None,
                     session_calendar: dict[str, Any] | None = None,
                     is_market_open: bool | Callable[[str], bool] | None = None,
                     now: datetime | None = None) -> dict[str, Any]:
    if timeframe not in {"1", "5", "15", "30", "60", "120", "d", "w", "m", "y"}:
        raise ValueError(f"不支持的周期: {timeframe}")
    suspended = {str(x)[:10] for x in (suspended_dates or [])}
    grouped: dict[str, list[str]] = defaultdict(list); seen: set[str] = set()
    duplicates: list[str] = []; malformed: list[str] = []; invalid_bars: list[str] = []
    for row in sorted(rows, key=lambda x: str(x.get("trade_date", ""))):
        raw, day = _parse(row.get("trade_date"))
        if raw in seen: duplicates.append(raw)
        seen.add(raw)
        if day is None: malformed.append(raw); continue
        try:
            op, hi, lo, cl = (float(row[k]) for k in ("open", "high", "low", "close"))
            if hi < max(op, cl) or lo > min(op, cl) or min(op, hi, lo, cl) < 0:
                invalid_bars.append(raw)
        except (KeyError, TypeError, ValueError):
            invalid_bars.append(raw)
        try: clock = datetime.fromisoformat(raw.replace("Z", "+00:00")).strftime("%H:%M")
        except ValueError: clock = ""
        grouped[day].append(clock)
    expected = {str(x)[:10] for x in (expected_trading_dates or [])}
    observed = set(grouped)
    if expected and observed:
        lo, hi = min(observed), (declared_end[:10] if declared_end else max(observed))
        expected = {d for d in expected if lo <= d <= hi}
    incomplete: list[dict[str, Any]] = []; partial: list[dict[str, Any]] = []; complete_days: list[str] = []
    expected_times = set(EXPECTED_TIMES.get(timeframe, [])); expected_count = len(expected_times) or 1
    for day, clocks in grouped.items():
        if day in suspended: continue
        if expected_times:
            missing = sorted(expected_times - set(clocks)); unexpected = sorted(set(clocks) - expected_times)
            valid = not missing and not unexpected and len(clocks) == expected_count
            if valid: complete_days.append(day)
            elif now and day == now.date().isoformat() and (is_market_open is None or (is_market_open(day) if callable(is_market_open) else bool(is_market_open))):
                partial.append({"date": day, "bar_count": len(clocks), "expected_count": expected_count, "missing_times": missing})
            else: incomplete.append({"date": day, "bar_count": len(clocks), "missing_times": missing, "unexpected_times": unexpected})
        else: complete_days.append(day)
    missing = sorted(expected - observed - suspended)
    today = now.date().isoformat() if now else None
    if today and today in expected and today not in observed and today not in suspended and (is_market_open is None or (is_market_open(today) if callable(is_market_open) else bool(is_market_open))):
        partial.append({"date": today, "bar_count": 0, "expected_count": expected_count, "missing_times": sorted(expected_times)})
        missing = [d for d in missing if d != today]
    ranges: list[dict[str, Any]] = []; current: list[str] = []; complete_set = set(complete_days)
    if timeframe in {"w", "m", "y"} and expected:
        expected_periods = sorted({_period(d, timeframe) for d in expected})
        observed_periods = {_period(d, timeframe): d for d in observed}
        missing = sorted(set(expected_periods) - set(observed_periods))
        for period in expected_periods:
            day = observed_periods.get(period)
            if day in complete_set:
                current.append(day)
            elif current:
                ranges.append({"start_date": current[0], "end_date": current[-1], "session_count": len(current)}); current = []
    else:
        for day in sorted(expected or observed):
            if day in suspended: continue
            if day in complete_set: current.append(day)
            elif current:
                ranges.append({"start_date": current[0], "end_date": current[-1], "session_count": len(current)}); current = []
    if current: ranges.append({"start_date": current[0], "end_date": current[-1], "session_count": len(current)})
    if not ranges and complete_days and not expected:
        ranges = [{"start_date": min(complete_days), "end_date": max(complete_days), "session_count": len(complete_days)}]
    start, end = (min(seen), max(seen)) if seen else (None, None)
    mismatch = bool((declared_start and start and start[:10] > declared_start[:10]) or (declared_end and end and end[:10] < declared_end[:10]))
    # Daily/weekly/monthly rows are self-contained period observations when no
    # exchange calendar has been loaded; intraday coverage still requires an
    # explicit trading-day calendar to avoid inferring sessions from bars.
    calendar_verified = expected_trading_dates is not None or timeframe in {"d", "w", "m", "y"}
    complete = bool(rows) and calendar_verified and not duplicates and not malformed and not invalid_bars and not incomplete and not partial and not missing and not mismatch
    if not rows: status = "unknown"
    elif partial and not incomplete and not missing: status = "partial"
    elif complete: status = "complete"
    elif not calendar_verified: status = "unknown"
    elif observed and all(d in suspended for d in observed): status = "suspended"
    else: status = "incomplete"
    return {"complete": complete, "status": status, "timeframe": timeframe, "bar_count": len(rows), "session_count": len(grouped),
            "range_start": start, "range_end": end, "complete_days": len(complete_days), "incomplete_days": incomplete,
            "partial_sessions": partial, "duplicates": duplicates, "malformed": malformed, "invalid_bars": invalid_bars,
            "calendar_verified": calendar_verified, "missing_sessions": missing, "continuous_ranges": ranges,
            "declared_start": declared_start, "declared_end": declared_end,
            "stale_reason": None if complete else f"{timeframe}周期数据覆盖无法证明完整",
            "coverage_version": f"{timeframe}:{start or ''}:{end or ''}:{len(rows)}:{status}", "session_calendar": session_calendar or {}}


def validate_5m_coverage(rows: list[dict[str, Any]], suspended_dates: list[str] | None = None,
                         declared_start: str | None = None, declared_end: str | None = None,
                         expected_trading_dates: list[str] | None = None) -> dict[str, Any]:
    return validate_coverage(rows, "5", expected_trading_dates, suspended_dates, declared_start, declared_end)
