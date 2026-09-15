from __future__ import annotations

import hashlib, json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any

from .coverage import validate_coverage
from .providers import INTRADAY_TIMEFRAMES
from .engine import normalize_bars, process_inclusions, find_fractals, find_gaps, build_pens
from .indicators import calculate_bollinger, calculate_macd, calculate_moving_averages
from .hierarchy import HIERARCHY_VERSION, build_hierarchy
from .rules import PERIOD_DEFINITION_VERSION


_DISPLAY_PERIOD_CHAINS = {
    "d": ("d", "w", "m"),
    "w": ("w", "m"),
    "m": ("m",),
}

# Only these independent market periods participate in the v13 hierarchy.
# Other supported quote periods remain available to the chart but deliberately
# do not create or reuse Chan structure snapshots.
STRUCTURE_TIMEFRAMES = ("5", "30", "d", "w", "m")
CALCULATOR_SOURCE_FILES = (
    "app/coverage.py",
    "app/engine.py",
    "app/hierarchy.py",
    "app/models.py",
    "app/period_structure.py",
    "app/rules.py",
    "app/store.py",
    "knowledge/chan_rules.md",
    "knowledge/chan_rules.yaml",
)


def calculate_calculator_fingerprint(root: Path | None = None) -> str:
    """Fingerprint every source that can change persisted Chan structures."""
    project_root = root or Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for relative_path in CALCULATOR_SOURCE_FILES:
        source = project_root / relative_path
        try:
            content = source.read_bytes()
        except OSError as exc:
            raise RuntimeError(f"无法读取结构计算源文件: {relative_path}: {exc}") from exc
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest()


def _structure_version(payload: dict[str, Any]) -> str:
    versioned = {
        "definition_version": payload["definition_version"],
        "calculator_fingerprint": payload["calculator_fingerprint"],
        "structure": {
            key: payload[key]
            for key in (
                "processed_bars", "fractals", "pens", "centers",
                "center_relations", "movements",
            )
        },
    }
    raw = json.dumps(versioned, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def color_period_for_level(chart_timeframe: str, level: int) -> dict[str, str]:
    """Return display-only period semantics without changing calculation input."""
    normalized_level = max(1, int(level))
    chain = _DISPLAY_PERIOD_CHAINS.get(chart_timeframe, ())
    if normalized_level <= len(chain):
        display_period = chain[normalized_level - 1]
        return {"display_period": display_period, "color_key": f"period-{display_period}"}
    if chart_timeframe in INTRADAY_TIMEFRAMES:
        color_key = (
            f"period-{chart_timeframe}"
            if normalized_level == 1
            else f"period-{chart_timeframe}-level-L{normalized_level}"
        )
        return {"display_period": chart_timeframe, "color_key": color_key}
    return {
        "display_period": "higher",
        "color_key": f"structure-higher-L{normalized_level}",
    }


def market_status(now: datetime, latest_day: str, trading_today: bool) -> str:
    clock = (now.hour, now.minute)
    today = now.date().isoformat()
    if trading_today and latest_day < today and (9, 30) <= clock <= (15, 10):
        return "数据延迟"
    if latest_day == today and ((9, 30) <= clock <= (11, 30) or (13, 0) <= clock <= (15, 0)):
        return "交易中"
    if latest_day == today and (11, 30) < clock < (13, 0):
        return "午间休市"
    return "已休市"


def _stable_id(prefix: str, values: list[str]) -> str:
    digest = hashlib.sha256("|".join(values).encode()).hexdigest()[:12]
    return f"{prefix}-{digest}"


def _strict_overlap(low_a: float, high_a: float, low_b: float, high_b: float) -> bool:
    return max(low_a, low_b) < min(high_a, high_b)


def _pen_direction(pen: dict[str, Any]) -> str:
    return pen.get("direction") or (
        "up" if float(pen["end_price"]) > float(pen["start_price"]) else "down"
    )


def _pen_bounds(pen: dict[str, Any]) -> tuple[float, float]:
    start, end = float(pen["start_price"]), float(pen["end_price"])
    return min(start, end), max(start, end)


def _directional_center_seed(pens: list[dict[str, Any]], index: int) -> dict[str, Any] | None:
    if index + 3 >= len(pens):
        return None
    group = pens[index:index + 4]
    entry, core = group[0], group[1:]
    sequence_id = entry.get("sequence_id", 0)
    continuous_range_id = entry.get("continuous_range_id", entry.get("range_index", 0))
    directions = [_pen_direction(pen) for pen in group]
    if any(pen.get("status", "confirmed") != "confirmed" for pen in group):
        return None
    if any(
        pen.get("sequence_id", 0) != sequence_id
        or pen.get("continuous_range_id", pen.get("range_index", 0)) != continuous_range_id
        for pen in group
    ):
        return None
    if any(left == right for left, right in zip(directions, directions[1:])):
        return None
    core_bounds = [_pen_bounds(pen) for pen in core]
    zd = max(low for low, _ in core_bounds)
    zg = min(high for _, high in core_bounds)
    if zd >= zg:
        return None
    entry_start, entry_end = float(entry["start_price"]), float(entry["end_price"])
    direction = directions[0]
    entered = (
        direction == "up" and entry_start < zd < entry_end
    ) or (
        direction == "down" and entry_start > zg > entry_end
    )
    if not entered:
        return None
    return {"group": group, "entry": entry, "core": core, "direction": direction,
            "sequence_id": sequence_id, "continuous_range_id": continuous_range_id,
            "zd": zd, "zg": zg}


def build_pen_centers(pens: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build directional L1 centers and extend each fixed core over later pens."""
    centers: list[dict[str, Any]] = []
    i = 0
    while i + 3 < len(pens):
        seed = _directional_center_seed(pens, i)
        if not seed:
            i += 1
            continue
        group, entry, core = seed["group"], seed["entry"], seed["core"]
        direction, sequence_id = seed["direction"], seed["sequence_id"]
        continuous_range_id = seed["continuous_range_id"]
        zd, zg = seed["zd"], seed["zg"]
        source = list(group)
        extension: list[dict[str, Any]] = []
        peripheral: list[dict[str, Any]] = []
        departure: list[dict[str, Any]] = []
        pending: list[dict[str, Any]] = []
        next_seed_index: int | None = None
        j = i + 4
        while j < len(pens) and (
            pens[j].get("sequence_id", 0) == sequence_id
            and pens[j].get("continuous_range_id", pens[j].get("range_index", 0)) == continuous_range_id
        ):
            pen = pens[j]
            if pen.get("status", "confirmed") != "confirmed":
                pending.append(pen)
                break
            low, high = _pen_bounds(pen)
            if _strict_overlap(low, high, zd, zg):
                if pending:
                    peripheral.extend(pending)
                    source.extend(pending)
                    pending = []
                extension.append(pen)
                source.append(pen)
                j += 1
                continue
            pending.append(pen)
            candidate_index = j
            candidate = None
            if j - 1 >= i + 4:
                previous_candidate = _directional_center_seed(pens, j - 1)
                if previous_candidate and not _strict_overlap(
                    previous_candidate["zd"], previous_candidate["zg"], zd, zg
                ):
                    candidate_index, candidate = j - 1, previous_candidate
            candidate = candidate or _directional_center_seed(pens, j)
            if candidate and not _strict_overlap(candidate["zd"], candidate["zg"], zd, zg):
                next_seed_index = candidate_index
                departure = [pens[candidate_index]]
                # Once the outside center is confirmed, the immediately
                # preceding crossing pen is retrospectively the leaving pen,
                # not an extension component of the old center.
                if extension and extension[-1] is pens[candidate_index]:
                    end_price = float(extension[-1]["end_price"])
                    if not zd < end_price < zg:
                        leaving = extension.pop()
                        source.pop()
                        departure = [leaving]
                break
            j += 1
        if j < len(pens) and (
            pens[j].get("sequence_id", 0) != sequence_id
            or pens[j].get("continuous_range_id", pens[j].get("range_index", 0)) != continuous_range_id
        ) and next_seed_index is None:
            next_seed_index = j
        if next_seed_index is None and pending:
            departure = list(pending)

        lows, highs = zip(*(_pen_bounds(pen) for pen in source))
        last = core[-1]
        absorbed_last = source[-1]
        centers.append({
            "id": _stable_id("directional-pen-center-L1", [pen["id"] for pen in group]),
            "ordinal": len(centers), "level_ordinal": len(centers),
            "kind": "pen_center", "level": 1, "direction": direction,
            "entry_pen_id": entry["id"],
            "formation_pen_ids": [pen["id"] for pen in group],
            "core_pen_ids": [pen["id"] for pen in core],
            "extension_pen_ids": [pen["id"] for pen in extension],
            "peripheral_pen_ids": [pen["id"] for pen in peripheral],
            "departure_pen_ids": [pen["id"] for pen in departure],
            "start_pen": core[0]["id"], "end_pen": absorbed_last["id"],
            "start_pen_index": i + 1, "end_pen_index": pens.index(absorbed_last),
            "pen_ids": [pen["id"] for pen in source],
            "source_pen_ids": [pen["id"] for pen in source],
            "sequence_id": sequence_id, "continuous_range_id": continuous_range_id,
            "start_date": core[0]["start_date"], "end_date": absorbed_last["end_date"],
            "core_start_date": core[0]["start_date"], "core_end_date": last["end_date"],
            "extension_end_date": absorbed_last["end_date"],
            "zd": zd, "zg": zg, "fixed_zd": zd, "fixed_zg": zg,
            "dd": min(lows), "gg": max(highs),
            "low": zd, "high": zg,
            "status": "confirmed", "confirmed_at": last["confirmed_at"],
            "tail_status": "confirmed_departure" if next_seed_index is not None else (
                "provisional_departure" if departure else "active_extension"
            ),
            "termination_reason": (
                "sequence_boundary" if next_seed_index is not None and pens[next_seed_index].get("sequence_id", 0) != sequence_id
                else "independent_center" if next_seed_index is not None else "right_edge"
            ),
            "completion_reason": "directional_core_with_extension",
            "evidence": ["CENTER-DIRECTIONAL-ENTRY-001", "CENTER-DIRECTIONAL-CORE-001",
                         *( ["CENTER-L1-EXTENSION-001"] if extension else [] ),
                         *( ["CENTER-L1-REENTRY-001"] if peripheral else [] )],
        })
        i = next_seed_index if next_seed_index is not None else len(pens)
    return centers


def analyze_period(
    rows: list[dict[str, Any]], symbol: str, timeframe: str,
    calculator_fingerprint: str | None = None,
) -> dict[str, Any]:
    fingerprint = calculator_fingerprint or calculate_calculator_fingerprint()
    bars = normalize_bars(rows, symbol)
    processed = process_inclusions(bars)
    fractals = find_fractals(processed)
    gaps = find_gaps(bars) if timeframe in INTRADAY_TIMEFRAMES - {"1"} else []
    pen_diagnostics: list[dict[str, Any]] = []
    pens = [asdict(p) for p in build_pens(fractals, processed, gaps, symbol, bars, timeframe=timeframe, diagnostics=pen_diagnostics)]
    centers = build_pen_centers(pens)
    hierarchy = build_hierarchy(pens, centers)
    payload = {"symbol": symbol, "timeframe": timeframe, "definition_version": PERIOD_DEFINITION_VERSION,
               "calculator_fingerprint": fingerprint,
               "bars": [b.json() | {"amount": getattr(b, "amount", 0)} for b in bars],
               "processed_bars": [asdict(x) for x in processed], "fractals": [asdict(x) for x in fractals],
               "pens": pens, "pen_diagnostics": pen_diagnostics,
               "pen_centers": hierarchy["centers"], "centers": hierarchy["centers"],
               "center_relations": hierarchy["center_relations"], "movements": hierarchy["movements"],
               "max_confirmed_center_level": hierarchy["max_confirmed_center_level"],
               "max_available_center_level": hierarchy["max_available_center_level"]}
    payload["structure_version"] = _structure_version(payload)
    return payload


def analyze_period_ranges(rows: list[dict[str, Any]], symbol: str, timeframe: str,
                          ranges: list[dict[str, Any]],
                          calculator_fingerprint: str | None = None) -> dict[str, Any]:
    fingerprint = calculator_fingerprint or calculate_calculator_fingerprint()
    merged = {key: [] for key in ("bars", "processed_bars", "fractals", "pens", "pen_centers", "pen_diagnostics")}
    for range_index, period in enumerate(ranges):
        subset = [row for row in rows
                  if period["start_date"] <= row["trade_date"][:10] <= period["end_date"]]
        if not subset:
            continue
        # Build the range seed independently; never reuse recursively derived
        # centers from analyze_period, otherwise each pass prefixes IDs again.
        bars_part = normalize_bars(subset, symbol)
        processed_part = process_inclusions(bars_part)
        fractals_part = find_fractals(processed_part)
        gaps_part = find_gaps(bars_part) if timeframe in INTRADAY_TIMEFRAMES - {"1"} else []
        pen_diagnostics: list[dict[str, Any]] = []
        pens_part = [asdict(p) for p in build_pens(fractals_part, processed_part, gaps_part, symbol, bars_part, timeframe=timeframe, diagnostics=pen_diagnostics)]
        for diagnostic in pen_diagnostics:
            diagnostic["range_index"] = range_index
        part = {"bars": [b.json() | {"amount": getattr(b, "amount", 0)} for b in bars_part],
                "processed_bars": [asdict(x) for x in processed_part], "fractals": [asdict(x) for x in fractals_part],
                "pens": pens_part, "pen_centers": build_pen_centers(pens_part), "pen_diagnostics": pen_diagnostics}
        pen_ids: dict[str, str] = {}
        for pen in part["pens"]:
            old_id = pen["id"]
            pen["id"] = f"R{range_index}-{old_id}"
            pen["range_index"] = range_index
            pen_ids[old_id] = pen["id"]
        for center in part["pen_centers"]:
            old_center_id = center["id"]
            center["id"] = f"R{range_index}-{old_center_id}"
            for field in ("start_pen", "end_pen", "entry_pen_id"):
                value = center.get(field)
                if value:
                    center[field] = pen_ids.get(value, value if value.startswith(f"R{range_index}-") else f"R{range_index}-{value}")
            for field in ("pen_ids", "source_pen_ids", "core_pen_ids", "formation_pen_ids", "extension_pen_ids", "peripheral_pen_ids", "departure_pen_ids"):
                center[field] = [pen_ids.get(item, item if item.startswith(f"R{range_index}-") else f"R{range_index}-{item}") for item in center.get(field, [])]
            center["range_index"] = range_index
            center["continuous_range_id"] = range_index
        for key in merged:
            merged[key].extend(part[key])
    # Each per-range analysis already contains recursively derived centers.
    # Re-enter the hierarchy engine only with the directional L1 seeds;
    # feeding L2/L3 back as L1 causes repeated R0 prefixes and empty sources.
    l1_seeds = [center for center in merged["pen_centers"]
                if int(center.get("level", 1)) == 1
                and center.get("kind") in {"pen_center", "center"}]
    hierarchy = build_hierarchy(merged["pens"], l1_seeds)
    merged["pen_centers"] = hierarchy["centers"]
    merged["centers"] = hierarchy["centers"]
    merged["center_relations"] = hierarchy["center_relations"]
    merged["movements"] = hierarchy["movements"]
    for key in ("pens", "pen_centers", "movements"):
        for ordinal, item in enumerate(merged[key]):
            item["ordinal"] = ordinal
    for level_ordinal, item in enumerate(sorted(merged["pen_centers"], key=lambda value: value["start_date"])):
        item["level_ordinal"] = level_ordinal
    payload = {"symbol": symbol, "timeframe": timeframe,
               "definition_version": PERIOD_DEFINITION_VERSION,
               "calculator_fingerprint": fingerprint,
               **merged,
               "unassigned_by_level": hierarchy["unassigned_by_level"],
               "hierarchy_issues": hierarchy["hierarchy_issues"],
               "max_confirmed_center_level": hierarchy["max_confirmed_center_level"],
               "max_available_center_level": hierarchy["max_available_center_level"]}
    payload["structure_version"] = _structure_version(payload)
    return payload


class PeriodStructureService:
    def __init__(self, store, calculator_fingerprint: str | None = None):
        self.store = store
        self.calculator_fingerprint = (
            calculator_fingerprint or calculate_calculator_fingerprint()
        )
        if not self.calculator_fingerprint:
            raise RuntimeError("结构计算器指纹不能为空")

    @staticmethod
    def market_version(rows):
        return hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:20]

    def coverage(self, symbol: str, timeframe: str, adjustflag: str = "2"):
        rows = self.store.market_bars(symbol, timeframe, adjustflag, "0000-01-01")
        expected = None
        declared_end = None
        if rows:
            _, calendar_end = self.store.trade_calendar_range()
            declared_end = calendar_end if timeframe in INTRADAY_TIMEFRAMES or timeframe == "d" else None
            expected = self.store.trading_dates(rows[0]["trade_date"], calendar_end or rows[-1]["trade_date"])
            expected = expected or None
        now = datetime.now()
        market_open = bool(declared_end == now.date().isoformat() and (now.hour, now.minute) < (15, 10))
        return validate_coverage(rows, timeframe, expected_trading_dates=expected,
                                 declared_end=declared_end, now=now, is_market_open=market_open)

    def ensure(self, symbol: str, timeframe: str, adjustflag: str = "2", force: bool = False):
        rows = self.store.market_bars(symbol, timeframe, adjustflag, "0000-01-01")
        if timeframe not in STRUCTURE_TIMEFRAMES:
            return {"symbol": symbol, "timeframe": timeframe, "available": bool(rows),
                    "definition_version": PERIOD_DEFINITION_VERSION,
                    "calculator_fingerprint": self.calculator_fingerprint,
                    "coverage": self.coverage(symbol, timeframe, adjustflag),
                    "pens": [], "centers": [], "pen_centers": [], "center_relations": [], "movements": [],
                    "processed_bars": [], "fractals": [], "max_confirmed_center_level": 0,
                    "max_available_center_level": 0}
        market_version = self.market_version(rows)
        coverage = self.coverage(symbol, timeframe, adjustflag)
        meta = self.store.save_market_coverage(symbol, timeframe, adjustflag, coverage)
        coverage["coverage_version"] = meta["coverage_version"]
        active = self.store.active_period_structure_run(symbol, timeframe, adjustflag)
        if (
            active
            and active["definition_version"] == PERIOD_DEFINITION_VERSION
            and active.get("calculator_fingerprint", "") == self.calculator_fingerprint
            and active["market_version"] == market_version
            and active["coverage_version"] == meta["coverage_version"]
            and not force
        ):
            return self.load(symbol, timeframe, adjustflag)
        if not rows or not coverage["continuous_ranges"]:
            return {"symbol": symbol, "timeframe": timeframe, "available": False,
                    "definition_version": PERIOD_DEFINITION_VERSION,
                    "calculator_fingerprint": self.calculator_fingerprint,
                    "coverage": coverage,
                    "pens": [], "centers": [], "pen_centers": [], "center_relations": [], "movements": [],
                    "max_confirmed_center_level": 0, "max_available_center_level": 0}
        result = analyze_period_ranges(
            rows, symbol, timeframe, coverage["continuous_ranges"],
            calculator_fingerprint=self.calculator_fingerprint,
        )
        run = self.store.replace_period_structure(symbol, timeframe, adjustflag, PERIOD_DEFINITION_VERSION, result, market_version, meta["coverage_version"])
        result.update({"available": True, "market_version": market_version,
                       "coverage_version": meta["coverage_version"], "run_id": run["id"],
                       "coverage": coverage})
        return result

    def load(self, symbol, timeframe, adjustflag="2"):
        active = self.store.active_period_structure_run(symbol, timeframe, adjustflag)
        if not active: return self.ensure(symbol,timeframe,adjustflag,True)
        if (
            active["definition_version"] != PERIOD_DEFINITION_VERSION
            or active.get("calculator_fingerprint", "") != self.calculator_fingerprint
        ):
            return self.ensure(symbol, timeframe, adjustflag, True)
        rows = self.store.market_bars(symbol,timeframe,adjustflag,"0000-01-01")
        mv = self.market_version(rows); cov = self.store.market_coverage(symbol,timeframe,adjustflag)
        if mv != active["market_version"]: return {"symbol":symbol,"timeframe":timeframe,"available":False,"definition_version":PERIOD_DEFINITION_VERSION,"stale_reason":"行情版本与结构快照不一致","market_version":mv,"structure_version":active["structure_version"]}
        current_cov = cov["coverage_version"] if cov else ""
        if current_cov != active.get("coverage_version", ""):
            return {"symbol":symbol,"timeframe":timeframe,"available":False,"definition_version":PERIOD_DEFINITION_VERSION,"stale_reason":"行情覆盖版本与结构快照不一致","market_version":mv,"coverage_version":current_cov,"structure_version":active["structure_version"],"coverage":cov["payload"] if cov else {}}
        run_id = active["id"]
        centers = self.store.period_rows("period_pen_centers", run_id)
        return {"symbol": symbol, "timeframe": timeframe, "available": True,
                "definition_version": active["definition_version"], "market_version": mv,
                "calculator_fingerprint": active.get("calculator_fingerprint", ""),
                                "coverage_version": active["coverage_version"],
                "structure_version": active["structure_version"], "run_id": run_id,
                "coverage": cov["payload"] if cov else {},
                "processed_bars": self.store.period_rows("period_processed_bars", run_id),
                "fractals": self.store.period_rows("period_fractals", run_id),
                "pens": self.store.period_rows("period_pens", run_id),
                "centers": centers, "pen_centers": centers,
                "center_relations": self.store.period_rows("period_center_relations", run_id),
                "movements": self.store.period_rows("period_movements", run_id),
                **json.loads(active.get("structure_metadata", "{}") or "{}"),
                "max_confirmed_center_level": int(active.get("max_confirmed_center_level", 0)),
                "max_available_center_level": int(active.get("max_available_center_level", 0)),
                "center_level_counts": json.loads(active.get("center_level_counts", "{}") or "{}"),
                "movement_level_counts": json.loads(active.get("movement_level_counts", "{}") or "{}")}

    def chart_page(self, symbol, timeframe, adjustflag, before, limit, ma_periods=(5, 10, 20, 60), boll_period=20, boll_multiplier=2.0):
        page, has_more = self.store.market_page(symbol, timeframe, adjustflag, before, limit)
        data = self.effective_structure(symbol, timeframe, adjustflag)
        coverage = data.get("coverage", {})
        all_rows = self.store.market_bars(symbol, timeframe, adjustflag, "0000-01-01")
        macd_by_date = {item["trade_date"]: item for item in calculate_macd(all_rows, coverage.get("continuous_ranges", []))}
        ma_by_date = {item["trade_date"]: item for item in calculate_moving_averages(all_rows, tuple(ma_periods))}
        boll_by_date = {item["trade_date"]: item for item in calculate_bollinger(all_rows, boll_period, boll_multiplier)}
        out = {
            "symbol": symbol, "timeframe": timeframe, "adjustflag": adjustflag, "bars": page,
            "indicators": {
                "macd": [macd_by_date[item["trade_date"]] for item in page if item["trade_date"] in macd_by_date],
                "ma": [{"trade_date": item["trade_date"], "values": {str(period): ma_by_date[item["trade_date"]].get(f"ma{period}") for period in ma_periods}, **{f"ma{period}": ma_by_date[item["trade_date"]].get(f"ma{period}") for period in ma_periods}} for item in page if item["trade_date"] in ma_by_date],
                "boll": [boll_by_date[item["trade_date"]] for item in page if item["trade_date"] in boll_by_date],
            },
            "has_more": has_more, "next_before": page[0]["trade_date"] if page else None,
        }
        if timeframe == "1" and page:
            daily_rows = self.store.market_bars(symbol, "d", adjustflag, "0000-01-01", page[0]["trade_date"][:10])
            previous = [row for row in daily_rows if row["trade_date"][:10] < page[0]["trade_date"][:10]]
            out["previous_close"] = previous[-1]["close"] if previous else page[0]["open"]
        daily_rows = self.store.market_bars(symbol, "d", adjustflag, "0000-01-01")
        if daily_rows:
            latest = daily_rows[-1]
            previous_close = daily_rows[-2]["close"] if len(daily_rows) > 1 else latest["open"]
            now = datetime.now(ZoneInfo("Asia/Shanghai"))
            latest_day = latest["trade_date"][:10]
            status = market_status(now, latest_day, bool(self.store.trading_dates(now.date().isoformat(), now.date().isoformat())))
            change = latest["close"] - previous_close
            out["quote"] = {
                "trade_date": latest["trade_date"], "latest": latest["close"],
                "change": change, "change_pct": change / previous_close * 100 if previous_close else None,
                "previous_close": previous_close, "open": latest["open"], "high": latest["high"],
                "low": latest["low"], "volume": latest["volume"], "amount": latest["amount"],
                "amplitude_pct": ((latest["high"] - latest["low"]) / previous_close * 100) if previous_close else None,
                "market_status": status,
            }
        out.update({key: data.get(key) for key in (
            "available", "definition_version", "calculator_fingerprint",
            "market_version", "coverage_version", "structure_version", "coverage", "run_id",
            "system_structure_version", "effective_structure_version", "override_version",
            "overrides", "override_conflicts", "drawings", "drawings_version",
            "structure_overrides_enabled", "movement_confirmation_mode",
            "unassigned_by_level", "hierarchy_issues", "pen_diagnostics",
            "center_level_counts", "movement_level_counts", "max_confirmed_center_level",
            "max_available_center_level",
        )})
        out["center_levels"] = sorted({int(item.get("level", 1)) for item in data.get("centers", [])})
        out["movement_levels"] = sorted({int(item.get("level", 1)) for item in data.get("movements", [])})
        if not data.get("available"):
            return out | {"pens": [], "centers": [], "pen_centers": [], "center_relations": [], "movements": [], "pen_diagnostics": []}
        start, end = (page[0]["trade_date"], page[-1]["trade_date"]) if page else ("", "9999")
        intersects = lambda item: max(item.get("end_date", item.get("trade_date", "")), item.get("tail_end_date", "")) >= start and item.get("start_date", item.get("trade_date", "")) <= end
        out["pen_diagnostics"] = [item for item in data.get("pen_diagnostics", []) if intersects(item)]
        centers = [{**item, **color_period_for_level(timeframe, int(item.get("level", 1)))} for item in data.get("centers", []) if item.get("role", "hierarchy") == "hierarchy" and intersects(item)]
        context_pen_ids = {pen_id for center in centers for pen_id in center.get("source_pen_ids", center.get("pen_ids", []))}
        pens = [item for item in data["pens"] if intersects(item) or item["id"] in context_pen_ids]
        movements = []
        for movement in data.get("movements", []):
            if movement.get("role") != "hierarchy_component" or not intersects(movement):
                continue
            item = dict(movement)
            item["path_points"] = self._clip_path_points(movement.get("path_points", []), page, all_rows)
            movements.append(item)
        out["center_levels"] = sorted({int(item.get("level", 1)) for item in centers})
        out["movement_levels"] = sorted({int(item.get("level", 1)) for item in movements})
        visible_center_ids = {item["id"] for item in centers}
        relations = [item for item in data.get("center_relations", []) if (item.get("previous_center_id") in visible_center_ids or item.get("current_center_id") in visible_center_ids) and intersects(item)]
        pens = [item for item in data["pens"] if intersects(item) or item["id"] in context_pen_ids]
        context_ids = {center_id for movement in movements for center_id in [*movement.get("center_ids", []), movement.get("confirmation_center_id")] if center_id}
        context_centers = [center for center in data.get("centers", []) if center["id"] in context_ids - visible_center_ids]
        return out | {"pens": pens, "centers": centers, "pen_centers": centers, "center_relations": relations, "movements": movements, "context_centers": context_centers}
    @staticmethod
    def _clip_path_points(path_points, page, all_rows):
        if not path_points or not page:
            return []
        start, end = page[0]["trade_date"], page[-1]["trade_date"]
        inside = [dict(point) for point in path_points if start <= point["trade_date"] <= end]
        row_index = {row["trade_date"]: index for index, row in enumerate(all_rows)}

        def boundary_point(stamp):
            for left, right in zip(path_points, path_points[1:]):
                if left["trade_date"] <= stamp <= right["trade_date"]:
                    li, ri, bi = row_index.get(left["trade_date"]), row_index.get(right["trade_date"]), row_index.get(stamp)
                    if li is None or ri is None or bi is None or ri == li:
                        return None
                    ratio = (bi - li) / (ri - li)
                    return {"trade_date": stamp, "price": float(left["price"]) + (float(right["price"]) - float(left["price"])) * ratio, "clipped": True}
            return None

        if path_points[0]["trade_date"] < start < path_points[-1]["trade_date"]:
            point = boundary_point(start)
            if point:
                inside.insert(0, point)
        if path_points[0]["trade_date"] < end < path_points[-1]["trade_date"]:
            point = boundary_point(end)
            if point:
                inside.append(point)
        return list({point["trade_date"]: point for point in inside}.values())

    def effective_structure(self, symbol: str, timeframe: str, adjustflag: str = "2"):
        """Return the system snapshot; historical overrides remain read-only."""
        data = self.ensure(symbol, timeframe, adjustflag)
        data.update({
            "overrides": self.store.structure_overrides(symbol, timeframe, adjustflag),
            "override_conflicts": [], "structure_overrides_enabled": False,
            "movement_confirmation_mode": "reverse_independent_center",
            "drawings": self.store.drawings(symbol, timeframe),
            "drawings_version": self.store.drawings_version(symbol, timeframe),
            "system_structure_version": data.get("structure_version", ""),
            "effective_structure_version": data.get("structure_version", ""),
            "override_version": "disabled",
        })
        return data
