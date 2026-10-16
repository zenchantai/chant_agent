from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from .coverage import validate_coverage
from .periods import period_key, period_row_key


SOURCE_PRIORITY = {"daily_aggregate": -1, "api": 0, "baostock": 1, "tencent": 2, "cache": 4}
PRICE_FIELDS = ("open", "high", "low", "close")


@dataclass(frozen=True)
class Candidate:
    source: str
    rows: list[dict[str, Any]]
    summary: dict[str, Any]


def normalize_candidate_rows(rows: list[dict[str, Any]], timeframe: str, source: str) -> list[dict[str, Any]]:
    """Normalize one provider response and retain one row per logical period."""
    normalized: dict[str, dict[str, Any]] = {}
    for raw in sorted(rows, key=lambda item: str(item.get("trade_date", ""))):
        if not raw.get("trade_date"):
            continue
        row = dict(raw)
        try:
            day = str(row["trade_date"])[:10]
            op, high, low, close = (float(row[field]) for field in PRICE_FIELDS)
            volume, amount = float(row.get("volume", 0) or 0), float(row.get("amount", 0) or 0)
            if (day > date.today().isoformat() or min(op, high, low, close) < 0
                    or high < max(op, close) or low > min(op, close)
                    or volume < 0 or amount < 0):
                continue
        except (TypeError, ValueError, KeyError):
            continue
        row["period_key"] = row.get("period_key") or period_row_key(row, timeframe)
        row["source"] = row.get("source") or source
        row.setdefault("snapshot_id", "")
        row.setdefault("source_revision", "")
        row.setdefault("adjust_factor", 1)
        row.setdefault("is_suspended", False)
        row.setdefault("amount", 0)
        normalized[row["period_key"]] = row
    return sorted(normalized.values(), key=lambda item: str(item["trade_date"]))


def candidate_summary(
    source: str,
    rows: list[dict[str, Any]],
    timeframe: str,
    expected_dates: list[str],
    target_start: str | None,
    target_end: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    coverage = validate_coverage(
        rows,
        timeframe,
        expected_dates,
        declared_start=target_start,
        declared_end=target_end,
        now=now,
    )
    anomalies = sum(len(coverage.get(field, [])) for field in (
        "duplicates", "malformed", "invalid_bars", "incomplete_days", "missing_sessions",
    ))
    return {
        "source": source,
        "bar_count": len(rows),
        "range_start": rows[0]["trade_date"] if rows else None,
        "range_end": rows[-1]["trade_date"] if rows else None,
        "valid_units": max(0, len(rows) - anomalies),
        "missing_count": len(coverage.get("missing_sessions", [])),
        "anomaly_count": anomalies,
        "complete": bool(coverage.get("complete")),
        "status": coverage.get("status", "unknown"),
        "coverage": coverage,
    }


def _score(candidate: Candidate) -> tuple[Any, ...]:
    summary = candidate.summary
    # More valid observations and fewer gaps win. Earlier/later boundaries are
    # tie-breakers, followed by anomaly count and a deterministic source order.
    start = summary.get("range_start") or "9999-12-31"
    end = summary.get("range_end") or "0000-01-01"
    return (
        int(summary.get("valid_units", 0)),
        -int(summary.get("missing_count", 0)),
        -int(start[:10].replace("-", "") or 99999999),
        int(end[:10].replace("-", "") or 0),
        -int(summary.get("anomaly_count", 0)),
        -SOURCE_PRIORITY.get(candidate.source, 99),
    )


def prices_compatible(left: dict[str, Any], right: dict[str, Any]) -> bool:
    for field in PRICE_FIELDS:
        a, b = float(left[field]), float(right[field])
        tolerance = max(0.0001, max(abs(a), abs(b)) * 0.000001)
        if abs(a - b) > tolerance:
            return False
    return True


def _boundary_evidence(
    primary: dict[str, dict[str, Any]],
    secondary: dict[str, dict[str, Any]],
    missing_keys: list[str],
) -> list[str]:
    common = sorted(set(primary) & set(secondary))
    if not common or not missing_keys:
        return []
    lo, hi = min(missing_keys), max(missing_keys)
    before = [key for key in common if key < lo][-5:]
    after = [key for key in common if key > hi][:5]
    nearest = before + after
    return nearest[:5]


def _missing_runs(keys: list[str], primary_keys: set[str]) -> list[list[str]]:
    # Keys need not be arithmetically consecutive (trading dates skip holidays).
    # Split whenever the primary has an observation between two secondary-only
    # keys, so independent holes receive independent boundary evidence.
    runs: list[list[str]] = []
    ordered_primary = sorted(primary_keys)
    for key in keys:
        if runs:
            left = bisect.bisect_right(ordered_primary, runs[-1][-1])
            right = bisect.bisect_left(ordered_primary, key)
        else:
            left = right = 0
        if runs and left < right:
            runs.append([])
        if not runs:
            runs.append([])
        runs[-1].append(key)
    return runs


def select_and_fill(
    candidates: list[Candidate],
    timeframe: str,
    *,
    expected_dates: list[str],
    target_start: str | None,
    target_end: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    usable = [candidate for candidate in candidates if candidate.rows]
    if not usable:
        coverage = validate_coverage([], timeframe, expected_dates, declared_start=target_start, declared_end=target_end, now=now)
        return {
            "rows": [], "primary_source": None, "selected_sources": [],
            "candidate_summaries": [candidate.summary for candidate in candidates],
            "source_conflicts": [], "coverage": coverage,
        }
    primary = max(usable, key=_score)
    merged = {row["period_key"]: dict(row) for row in primary.rows}
    selected_sources = {primary.source}
    conflicts: list[dict[str, Any]] = []
    for secondary in sorted((item for item in usable if item is not primary), key=_score, reverse=True):
        source_rows = {row["period_key"]: row for row in secondary.rows}
        missing = sorted(set(source_rows) - set(merged))
        common = sorted(set(source_rows) & set(merged))
        overlap_mismatches = [key for key in common if not prices_compatible(merged[key], source_rows[key])]
        if overlap_mismatches:
            conflicts.append({
                "source": secondary.source,
                "reason": "overlap_ohlc_mismatch",
                "overlap_count": len(common),
                "mismatch_count": len(overlap_mismatches),
                "mismatch_keys": overlap_mismatches[:20],
            })
        for run in _missing_runs(missing, set(merged)):
            evidence = _boundary_evidence(merged, source_rows, run)
            if len(evidence) < 5:
                conflicts.append({
                    "source": secondary.source,
                    "reason": "insufficient_overlap",
                    "range_start": run[0], "range_end": run[-1],
                    "overlap_count": len(evidence),
                })
                continue
            mismatches = [key for key in evidence if not prices_compatible(merged[key], source_rows[key])]
            if mismatches:
                conflicts.append({
                    "source": secondary.source,
                    "reason": "ohlc_mismatch",
                    "range_start": run[0], "range_end": run[-1],
                    "mismatch_keys": mismatches,
                })
                continue
            for key in run:
                merged[key] = dict(source_rows[key])
            selected_sources.add(secondary.source)
    rows = sorted(merged.values(), key=lambda item: str(item["trade_date"]))
    coverage = validate_coverage(
        rows, timeframe, expected_dates,
        declared_start=target_start, declared_end=target_end, now=now,
    )
    return {
        "rows": rows,
        "primary_source": primary.source,
        "selected_sources": sorted(selected_sources, key=lambda value: SOURCE_PRIORITY.get(value, 99)),
        "candidate_summaries": [candidate.summary for candidate in candidates],
        "source_conflicts": conflicts,
        "coverage": coverage,
    }


def uncovered_ranges(coverage: dict[str, Any]) -> list[dict[str, str]]:
    ranges: list[dict[str, str]] = []
    for item in coverage.get("incomplete_days", []):
        day = str(item.get("date", ""))[:10]
        if day:
            ranges.append({"start_date": day, "end_date": day, "reason": "incomplete_session"})
    for key in coverage.get("missing_sessions", []):
        value = str(key)
        ranges.append({"start_date": value, "end_date": value, "reason": "missing_session"})
    observed_start = coverage.get("range_start")
    declared_start = coverage.get("declared_start")
    timeframe = str(coverage.get("timeframe") or "d")
    start_is_late = bool(observed_start and declared_start and (
        period_key(observed_start[:10], timeframe) > period_key(declared_start[:10], timeframe)
        if timeframe in {"w", "m", "y"} else observed_start[:10] > declared_start[:10]
    ))
    if start_is_late:
        ranges.insert(0, {
            "start_date": declared_start[:10],
            "end_date": observed_start[:10],
            "reason": "leading_gap",
        })
    observed_end = coverage.get("range_end")
    declared_end = coverage.get("declared_end")
    end_is_early = bool(observed_end and declared_end and (
        period_key(observed_end[:10], timeframe) < period_key(declared_end[:10], timeframe)
        if timeframe in {"w", "m", "y"} else observed_end[:10] < declared_end[:10]
    ))
    if end_is_early:
        ranges.append({
            "start_date": observed_end[:10],
            "end_date": declared_end[:10],
            "reason": "trailing_gap",
        })
    return ranges
