from __future__ import annotations

import hashlib, json
from dataclasses import asdict
from datetime import datetime
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

# Only these independent market periods participate in the v12 hierarchy.
# Other supported quote periods remain available to the chart but deliberately
# do not create or reuse Chan structure snapshots.
STRUCTURE_TIMEFRAMES = ("5", "30", "d", "w", "m")


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


def analyze_period(rows: list[dict[str, Any]], symbol: str, timeframe: str) -> dict[str, Any]:
    bars = normalize_bars(rows, symbol)
    processed = process_inclusions(bars)
    fractals = find_fractals(processed)
    gaps = find_gaps(bars) if timeframe in INTRADAY_TIMEFRAMES - {"1"} else []
    pens = [asdict(p) for p in build_pens(fractals, processed, gaps, symbol, bars, timeframe=timeframe)]
    centers = build_pen_centers(pens)
    hierarchy = build_hierarchy(pens, centers)
    payload = {"symbol": symbol, "timeframe": timeframe, "definition_version": PERIOD_DEFINITION_VERSION,
               "bars": [b.json() | {"amount": getattr(b, "amount", 0)} for b in bars],
               "processed_bars": [asdict(x) for x in processed], "fractals": [asdict(x) for x in fractals],
               "pens": pens, "pen_centers": hierarchy["centers"], "centers": hierarchy["centers"],
               "center_relations": hierarchy["center_relations"], "movements": hierarchy["movements"],
               "decomposition": hierarchy["decompositions"],
               "hierarchy_input_hash": hierarchy["hierarchy_input_hash"],
               "movement_input_hash": hierarchy["movement_input_hash"],
               "max_confirmed_center_level": hierarchy["max_confirmed_center_level"],
               "max_available_center_level": hierarchy["max_available_center_level"]}
    raw = json.dumps({k: payload[k] for k in ("processed_bars", "fractals", "pens", "centers", "center_relations", "movements", "decomposition")}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["structure_version"] = hashlib.sha256(raw.encode()).hexdigest()[:20]
    return payload


def analyze_period_ranges(rows: list[dict[str, Any]], symbol: str, timeframe: str,
                          ranges: list[dict[str, Any]]) -> dict[str, Any]:
    merged = {key: [] for key in ("bars", "processed_bars", "fractals", "pens", "pen_centers")}
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
        pens_part = [asdict(p) for p in build_pens(fractals_part, processed_part, gaps_part, symbol, bars_part, timeframe=timeframe)]
        part = {"bars": [b.json() | {"amount": getattr(b, "amount", 0)} for b in bars_part],
                "processed_bars": [asdict(x) for x in processed_part], "fractals": [asdict(x) for x in fractals_part],
                "pens": pens_part, "pen_centers": build_pen_centers(pens_part)}
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
               "definition_version": PERIOD_DEFINITION_VERSION, **merged,
               "decomposition": hierarchy["decompositions"],
               "hierarchy_input_hash": hierarchy["hierarchy_input_hash"],
               "movement_input_hash": hierarchy["movement_input_hash"],
               "max_confirmed_center_level": hierarchy["max_confirmed_center_level"],
               "max_available_center_level": hierarchy["max_available_center_level"]}
    raw = json.dumps({k: payload[k] for k in ("processed_bars", "fractals", "pens", "centers", "center_relations", "movements", "decomposition")},
                     ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["structure_version"] = hashlib.sha256(raw.encode()).hexdigest()[:20]
    return payload


class PeriodStructureService:
    def __init__(self, store): self.store = store

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
                    "definition_version": PERIOD_DEFINITION_VERSION, "coverage": self.coverage(symbol, timeframe, adjustflag),
                    "pens": [], "centers": [], "pen_centers": [], "center_relations": [], "movements": [],
                    "processed_bars": [], "fractals": [], "max_confirmed_center_level": 0,
                    "max_available_center_level": 0, "movement_input_hash": "", "decomposition": {}}
        market_version = self.market_version(rows)
        coverage = self.coverage(symbol, timeframe, adjustflag)
        meta = self.store.save_market_coverage(symbol, timeframe, adjustflag, coverage)
        coverage["coverage_version"] = meta["coverage_version"]
        active = self.store.active_period_structure_run(symbol, timeframe, adjustflag)
        if active and active["definition_version"] == PERIOD_DEFINITION_VERSION and active["market_version"] == market_version and active["coverage_version"] == meta["coverage_version"] and not force:
            return self.load(symbol, timeframe, adjustflag)
        if not rows or not coverage["continuous_ranges"]:
            return {"symbol": symbol, "timeframe": timeframe, "available": False,
                    "definition_version": PERIOD_DEFINITION_VERSION, "coverage": coverage,
                    "pens": [], "centers": [], "pen_centers": [], "center_relations": [], "movements": [],
                    "max_confirmed_center_level": 0, "max_available_center_level": 0,
                    "movement_input_hash": "", "decomposition": {}}
        result = analyze_period_ranges(rows, symbol, timeframe, coverage["continuous_ranges"])
        run = self.store.replace_period_structure(symbol, timeframe, adjustflag, PERIOD_DEFINITION_VERSION, result, market_version, meta["coverage_version"])
        result.update({"available": True, "market_version": market_version,
                       "coverage_version": meta["coverage_version"], "run_id": run["id"],
                       "coverage": coverage})
        return result

    def load(self, symbol, timeframe, adjustflag="2"):
        active = self.store.active_period_structure_run(symbol, timeframe, adjustflag)
        if not active: return self.ensure(symbol,timeframe,adjustflag,True)
        if active["definition_version"] != PERIOD_DEFINITION_VERSION:
            return self.ensure(symbol, timeframe, adjustflag, True)
        rows = self.store.market_bars(symbol,timeframe,adjustflag,"0000-01-01")
        mv = self.market_version(rows); cov = self.store.market_coverage(symbol,timeframe,adjustflag)
        if mv != active["market_version"]: return {"symbol":symbol,"timeframe":timeframe,"available":False,"definition_version":PERIOD_DEFINITION_VERSION,"stale_reason":"行情版本与结构快照不一致","market_version":mv,"structure_version":active["structure_version"],"movement_input_hash":active.get("movement_input_hash", "")}
        current_cov = cov["coverage_version"] if cov else ""
        if current_cov != active.get("coverage_version", ""):
            return {"symbol":symbol,"timeframe":timeframe,"available":False,"definition_version":PERIOD_DEFINITION_VERSION,"stale_reason":"行情覆盖版本与结构快照不一致","market_version":mv,"coverage_version":current_cov,"structure_version":active["structure_version"],"movement_input_hash":active.get("movement_input_hash", ""),"coverage":cov["payload"] if cov else {}}
        run_id = active["id"]
        centers = self.store.period_rows("period_pen_centers", run_id)
        return {"symbol": symbol, "timeframe": timeframe, "available": True,
                "definition_version": active["definition_version"], "market_version": mv,
                "coverage_version": active["coverage_version"],
                "structure_version": active["structure_version"], "run_id": run_id,
                "coverage": cov["payload"] if cov else {},
                "processed_bars": self.store.period_rows("period_processed_bars", run_id),
                "fractals": self.store.period_rows("period_fractals", run_id),
                "pens": self.store.period_rows("period_pens", run_id),
                "centers": centers, "pen_centers": centers,
                "center_relations": self.store.period_rows("period_center_relations", run_id),
                "movements": self.store.period_rows("period_movements", run_id),
                "movement_input_hash": active.get("movement_input_hash", ""),
                "max_confirmed_center_level": int(active.get("max_confirmed_center_level", 0)),
                "max_available_center_level": int(active.get("max_available_center_level", 0)),
                "center_level_counts": json.loads(active.get("center_level_counts", "{}") or "{}"),
                "movement_level_counts": json.loads(active.get("movement_level_counts", "{}") or "{}"),
                "decomposition": json.loads(active.get("decomposition_meta", "{}") or "{}")}

    def chart_page(self, symbol, timeframe, adjustflag, before, limit, ma_periods=(5, 10, 20, 60), boll_period=20, boll_multiplier=2.0, structure_level=1):
        page, has_more = self.store.market_page(symbol,timeframe,adjustflag,before,limit)
        data = self.effective_structure(symbol, timeframe, adjustflag)
        coverage = data.get("coverage", {})
        all_rows = self.store.market_bars(symbol, timeframe, adjustflag, "0000-01-01")
        macd_by_date = {item["trade_date"]: item for item in calculate_macd(
            all_rows, coverage.get("continuous_ranges", [])
        )}
        ma_by_date = {item["trade_date"]: item for item in calculate_moving_averages(all_rows, tuple(ma_periods))}
        boll_by_date = {item["trade_date"]: item for item in calculate_bollinger(all_rows, boll_period, boll_multiplier)}
        out = {"symbol":symbol,"timeframe":timeframe,"adjustflag":adjustflag,"bars":page,
               "indicators":{"macd":[macd_by_date[item["trade_date"]] for item in page
                                      if item["trade_date"] in macd_by_date],
                             "ma":[{"trade_date": ma_by_date[item["trade_date"]]["trade_date"], "values": {str(period): ma_by_date[item["trade_date"]].get(f"ma{period}") for period in ma_periods}, **{f"ma{period}": ma_by_date[item["trade_date"]].get(f"ma{period}") for period in ma_periods}} for item in page if item["trade_date"] in ma_by_date],
                             "boll":[boll_by_date[item["trade_date"]] for item in page if item["trade_date"] in boll_by_date]},
               "has_more":has_more,"next_before":page[0]["trade_date"] if page else None}
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
            today = now.date().isoformat()
            status = market_status(now, latest_day, bool(self.store.trading_dates(today, today)))
            change = latest["close"] - previous_close
            out["quote"] = {"trade_date": latest["trade_date"], "latest": latest["close"],
                            "change": change, "change_pct": change / previous_close * 100 if previous_close else None,
                            "previous_close": previous_close, "open": latest["open"], "high": latest["high"],
                            "low": latest["low"], "volume": latest["volume"], "amount": latest["amount"],
                            "amplitude_pct": (latest["high"] - latest["low"]) / previous_close * 100 if previous_close else None,
                            "market_status": status}
        out.update({k: data.get(k) for k in ("available", "definition_version", "market_version",
                   "coverage_version", "structure_version", "coverage", "run_id", "stale_reason")})
        out.update({k: data.get(k) for k in ("system_structure_version", "effective_structure_version", "override_version", "overrides", "override_conflicts", "drawings", "center_level_counts", "movement_level_counts", "max_confirmed_center_level", "max_available_center_level")})
        out["movement_input_hash"] = data.get("movement_input_hash", "")
        max_level = max(1, int(data.get("max_available_center_level", 1) or 1))
        active_level = min(max(1, int(structure_level)), max_level)
        out.update({
            "active_structure_level": active_level,
            "center_levels": sorted({
                int(item.get("level", 1))
                for item in data.get("centers", data.get("pen_centers", []))
                if item.get("role", "hierarchy") == "hierarchy"
            }),
            "movement_levels": sorted({int(item.get("level", 1)) for item in data.get("movements", []) if item.get("role") == "same_level_decomposition"}),
        })
        if not data.get("available"):
            return out | {"pens": [], "centers": [], "pen_centers": [], "center_relations": [], "movements": [], "decomposition": {}}
        start,end = (page[0]["trade_date"],page[-1]["trade_date"]) if page else ("","9999")
        intersects=lambda x:x.get("end_date",x.get("trade_date",""))>=start and x.get("start_date",x.get("trade_date",""))<=end
        all_centers = data.get("centers", data.get("pen_centers", []))
        level_appearance = color_period_for_level(timeframe, active_level)
        centers = [
            {**x, **level_appearance}
            for x in all_centers
            if x.get("role", "hierarchy") == "hierarchy"
            and int(x.get("level", 1)) == active_level
            and intersects(x)
        ]
        context_pen_ids = {
            pen_id
            for item in centers
            for pen_id in item.get("source_pen_ids", item.get("pen_ids", []))
        }
        for center in centers:
            context_pen_ids.update(center.get("formation_pen_ids", []))
            context_pen_ids.update(center.get("core_pen_ids", []))
            context_pen_ids.update(center.get("extension_pen_ids", []))
            context_pen_ids.update(center.get("peripheral_pen_ids", []))
            context_pen_ids.update(center.get("departure_pen_ids", []))
            if center.get("entry_pen_id"):
                context_pen_ids.add(center["entry_pen_id"])
        pens = [x for x in data["pens"] if intersects(x) or x["id"] in context_pen_ids]
        movement_rows = []
        for movement in data.get("movements", []):
            if movement.get("role") != "same_level_decomposition" or int(movement.get("level", 1)) != active_level:
                continue
            if not intersects(movement):
                continue
            item = dict(movement)
            item["path_points"] = self._clip_path_points(
                movement.get("path_points", []), page, all_rows
            )
            movement_rows.append(item)
        visible_center_ids = {item["id"] for item in centers}
        relations = [item for item in data.get("center_relations", []) if (
            item.get("previous_center_id") in visible_center_ids or item.get("current_center_id") in visible_center_ids
        ) and intersects(item)]
        decomposition = data.get("decomposition", {}).get(str(active_level), {})
        return out | {"pens": pens, "centers": centers, "pen_centers": centers,
                      "center_relations": relations, "movements": movement_rows,
                      "decomposition": decomposition}

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
        """Compose read-only system snapshot with active manual overrides."""
        data = self.ensure(symbol, timeframe, adjustflag)
        system_pens = [dict(x, origin="system", system_id=x.get("id"), base_run_id=data.get("run_id")) for x in data.get("pens", [])]
        system_centers = [dict(x, origin="system", system_id=x.get("id"), base_run_id=data.get("run_id")) for x in data.get("centers", data.get("pen_centers", []))]
        overrides = self.store.structure_overrides(symbol, timeframe, adjustflag)
        by_target = {}
        creates = []
        conflicts = []
        for item in overrides:
            payload = item.get("payload") or {}
            target = item.get("target_id")
            collection = system_pens if item.get("structure_type") == "pen" else system_centers
            if target and not any(x.get("id") == target for x in collection):
                if item.get("status") != "conflicted":
                    self.store.set_structure_override_status(item["id"], "conflicted", item.get("operation", "update"))
                item = dict(item, status="conflicted")
                payload = dict(payload, conflict_reason="系统结构已不存在或锚点失效")
                conflicts.append(item)
                continue
            if item.get("operation") == "create" and item.get("status") == "active":
                creates.append((item, payload, collection)); continue
            if target and item.get("status") == "active":
                by_target[(item.get("structure_type"), target)] = (item, payload)
        def apply(items, typ):
            result = []
            for node in items:
                current = dict(node)
                entry = by_target.get((typ, node.get("id")))
                if entry:
                    override, payload = entry
                    if override.get("operation") == "delete":
                        continue
                    current.update(payload)
                    current.update(origin="manual", override_id=override["id"], manual_status="active")
                result.append(current)
            return result
        pens = apply(system_pens, "pen")
        centers = apply(system_centers, "pen_center")
        for override, payload, collection in creates:
            obj = dict(payload)
            obj.setdefault("id", f"manual-{override['id']}")
            obj.update(origin="manual", override_id=override["id"], manual_status="active", base_run_id=override.get("base_run_id"))
            (pens if collection is system_pens else centers).append(obj)
        active_override_ids = [x["id"] for x in overrides if x.get("status") == "active"]
        if active_override_ids:
            l1_centers = [
                item for item in centers
                if item.get("role", "hierarchy") == "hierarchy"
                and int(item.get("level", 1)) == 1
            ]
            derived = build_hierarchy(pens, l1_centers, origin="manual-derived")
            centers = derived["centers"]
            movements = derived["movements"]
            decomposition = derived["decompositions"]
            if isinstance(decomposition, dict):
                decomposition = dict(decomposition)
                decomposition.setdefault("movement_input_hash", derived.get("movement_input_hash", ""))
            center_relations = derived["center_relations"]
            max_confirmed = derived["max_confirmed_center_level"]
            max_available = derived["max_available_center_level"]
            movement_input_hash = derived.get("movement_input_hash", "")
        else:
            movements = data.get("movements", [])
            decomposition = data.get("decomposition", {})
            center_relations = data.get("center_relations", [])
            max_confirmed = data.get("max_confirmed_center_level", 0)
            max_available = data.get("max_available_center_level", 0)
            movement_input_hash = data.get("movement_input_hash", "")
        effective_payload = {"pens": pens, "centers": centers, "center_relations": center_relations,
                             "movements": movements, "override_ids": active_override_ids}
        effective_version = hashlib.sha256(json.dumps(effective_payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20]
        data.update({"pens": pens, "centers": centers, "pen_centers": centers,
                     "center_relations": center_relations, "movements": movements,
                     "decomposition": decomposition,
                     "movement_input_hash": movement_input_hash,
                     "max_confirmed_center_level": max_confirmed,
                     "max_available_center_level": max_available,
                     "overrides": overrides, "override_conflicts": conflicts,
                     "drawings": self.store.drawings(symbol, timeframe),
                     "drawings_version": self.store.drawings_version(symbol, timeframe),
                     "system_structure_version": data.get("structure_version", ""),
                     "effective_structure_version": effective_version, "override_version": effective_version})
        return data
