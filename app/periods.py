from __future__ import annotations

from datetime import date, datetime


INTRADAY_TIMEFRAMES = {"1", "5", "15", "30", "60", "120"}


def period_key(value: str | date | datetime, timeframe: str) -> str:
    """Return the stable logical key used to identify one market period."""
    if isinstance(value, datetime):
        day = value.date()
        stamp = value
    elif isinstance(value, date):
        day = value
        stamp = None
    else:
        raw = str(value).replace("T", " ")
        day = date.fromisoformat(raw[:10])
        stamp = datetime.fromisoformat(raw) if len(raw) > 10 else None
    if timeframe == "w":
        iso = day.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    if timeframe == "m":
        return day.strftime("%Y-%m")
    if timeframe == "y":
        return day.strftime("%Y")
    if timeframe in INTRADAY_TIMEFRAMES and timeframe != "1":
        if stamp is None:
            stamp = datetime.combine(day, datetime.min.time())
        return stamp.replace(second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:00")
    if timeframe == "1" and stamp is not None:
        return stamp.replace(second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:00")
    return day.isoformat()


def period_row_key(row: dict, timeframe: str) -> str:
    # Yearly bars are outside the live-refresh scope; retain their source-date
    # identity so the migration does not silently collapse legacy annual rows.
    if timeframe == "y":
        return str(row["trade_date"])[:10]
    return period_key(str(row["trade_date"]), timeframe)
