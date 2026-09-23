from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .chan_structure import assert_valid_structure, build_structure_hierarchy, structure_version
from .coverage import validate_coverage
from .engine import build_pens, find_fractals, find_gaps, normalize_bars, process_inclusions
from .indicators import calculate_bollinger, calculate_macd, calculate_moving_averages
from .providers import INTRADAY_TIMEFRAMES
from .rules import PERIOD_DEFINITION_VERSION


STRUCTURE_TIMEFRAMES = ("5", "30", "d", "w", "m")
REFERENCE_TIMEFRAMES = ("w", "m")


def calculation_profile(timeframe: str) -> str:
    return "pen_centers_only" if timeframe in REFERENCE_TIMEFRAMES else "pen_centers_l2"


def structure_mode_metadata(profile: str) -> dict[str, str]:
    if profile not in {"pen_centers_l2", "pen_centers_only"}:
        raise ValueError(f"不支持的结构计算策略: {profile}")
    promotes_l2 = profile == "pen_centers_l2"
    return {
        "decomposition_mode": "non_same_level",
        "base_unit_mode": "current_period_confirmed_pen",
        "center_selection_mode": "canonical_eligible_candidate",
        "center_candidate_mode": "canonical_eligible_candidate",
        "center_ownership_mode": "same_level_single_owner",
        "center_context_mode": "references_do_not_own",
        "center_lifecycle_mode": "timestamped_event_arbitration",
        "center_prefix_mode": "immutable_committed_prefix",
        "center_envelope_mode": "owned_z_units",
        "movement_partition_mode": "disabled",
        "promotion_segment_mode": "pen_native_segment_proof" if promotes_l2 else "disabled",
        "center_promotion_mode": "unified_segment_proof" if promotes_l2 else "disabled",
        "nine_unit_mode": "owned_units_from_core" if promotes_l2 else "disabled",
        "envelope_touch_mode": "expansion_contact",
        "movement_boundary_mode": "disabled",
        "divergence_mode": "disabled",
    }


CALCULATOR_SOURCE_FILES = (
    "app/coverage.py",
    "app/engine.py",
    "app/chan_structure.py",
    "app/chan_direction.py",
    "app/chan_expansion.py",
    "app/models.py",
    "app/period_structure.py",
    "app/rules.py",
    "app/store.py",
    "knowledge/chan_rules.md",
    "knowledge/chan_rules.yaml",
)


def calculate_calculator_fingerprint(root: Path | None = None) -> str:
    project_root = root or Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for relative_path in CALCULATOR_SOURCE_FILES:
        source = project_root / relative_path
        content = source.read_bytes()
        digest.update(relative_path.encode())
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest()


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


def _decorate_pen(pen: dict[str, Any], range_index: int, ordinal: int) -> dict[str, Any]:
    item = dict(pen)
    item["id"] = f"R{range_index}-{item['id']}"
    item["kind"] = "pen"
    item["ordinal"] = ordinal
    item["range_index"] = range_index
    item["continuous_range_id"] = range_index
    item["sequence_id"] = int(item.get("sequence_id", 0))
    item["structure_sequence_id"] = f"range-{range_index}"
    item["status"] = item.get("status", "confirmed")
    return item


def _preview_tail_pen(
    rows: list[dict[str, Any]], pens: list[dict[str, Any]], forming_bar: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Build a clearly provisional tail for the in-flight period only.

    A forming bar cannot satisfy the right-side confirmation rule for a real
    fractal yet.  The preview therefore exposes a dashed candidate from the
    latest confirmed pen endpoint to the current bar's opposing extreme.  It
    is deliberately marked provisional and is never passed to persistence.
    """
    if not forming_bar or not pens or not rows:
        return None
    latest = rows[-1]
    stamp = str(forming_bar.get("trade_date") or latest.get("trade_date") or "")
    if not stamp or stamp <= pens[-1].get("end_date", ""):
        return None
    previous = pens[-1]
    direction = "down" if previous.get("direction") == "up" else "up"
    extreme_key = "low" if direction == "down" else "high"
    target = float(latest.get(extreme_key, latest.get("close", previous["end_price"])))
    if not target or not target == target:  # NaN guard without changing source values.
        target = float(latest.get("close", previous["end_price"]))
    return {
        "id": f"preview-pen-{previous['id']}-{stamp}",
        "kind": "pen",
        "ordinal": int(previous.get("ordinal", 0)) + 1,
        "range_index": int(previous.get("range_index", 0)),
        "continuous_range_id": int(previous.get("continuous_range_id", previous.get("range_index", 0))),
        "sequence_id": int(previous.get("sequence_id", 0)),
        "structure_sequence_id": previous.get("structure_sequence_id", ""),
        "start_date": previous["end_date"],
        "end_date": stamp,
        "start_price": float(previous["end_price"]),
        "end_price": target,
        "direction": direction,
        "status": "provisional",
        "confirmed_at": None,
        "kind_detail": "forming_bar_tail",
    }


def analyze_period_ranges(
    rows: list[dict[str, Any]], symbol: str, timeframe: str,
    ranges: list[dict[str, Any]], calculator_fingerprint: str | None = None,
    *, include_provisional: bool = False,
) -> dict[str, Any]:
    fingerprint = calculator_fingerprint or calculate_calculator_fingerprint()
    all_bars: list[dict[str, Any]] = []
    processed_bars: list[dict[str, Any]] = []
    fractals: list[dict[str, Any]] = []
    pens: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    for range_index, period in enumerate(ranges):
        subset = [
            row for row in rows
            if period["start_date"] <= row["trade_date"][:10] <= period["end_date"]
        ]
        if not subset:
            continue
        bars = normalize_bars(subset, symbol)
        processed = process_inclusions(bars)
        range_fractals = find_fractals(processed)
        gaps = find_gaps(bars) if timeframe in INTRADAY_TIMEFRAMES - {"1"} else []
        range_diagnostics: list[dict[str, Any]] = []
        raw_pens = [
            asdict(pen) for pen in build_pens(
                range_fractals, processed, gaps, symbol, bars,
                timeframe=timeframe, diagnostics=range_diagnostics,
            )
        ]
        offset = len(pens)
        pens.extend(_decorate_pen(pen, range_index, offset + ordinal) for ordinal, pen in enumerate(raw_pens))
        diagnostics.extend({**item, "range_index": range_index} for item in range_diagnostics)
        all_bars.extend(bar.json() | {"amount": getattr(bar, "amount", 0)} for bar in bars)
        processed_bars.extend({**asdict(item), "range_index": range_index} for item in processed)
        fractals.extend({**asdict(item), "range_index": range_index} for item in range_fractals)
    profile = calculation_profile(timeframe)
    macd = []
    hierarchy = build_structure_hierarchy(
        pens, macd, [bar["trade_date"] for bar in all_bars],
        calculation_profile=profile, include_provisional=include_provisional,
    )
    structure = {
        "processed_bars": processed_bars,
        "fractals": fractals,
        "pens": pens,
        "pen_diagnostics": diagnostics,
        **hierarchy,
    }
    result = {
        "meta": {
            "symbol": symbol,
            "timeframe": timeframe,
            "definition_version": PERIOD_DEFINITION_VERSION,
            "calculator_fingerprint": fingerprint,
            "calculation_profile": profile,
            **structure_mode_metadata(profile),
            "max_level": hierarchy["max_level"],
        },
        "structure": structure,
    }
    assert_valid_structure(result, include_provisional=include_provisional)
    result["meta"]["structure_version"] = structure_version(result)
    return result


def analyze_period(
    rows: list[dict[str, Any]], symbol: str, timeframe: str,
    calculator_fingerprint: str | None = None,
) -> dict[str, Any]:
    if not rows:
        return analyze_period_ranges([], symbol, timeframe, [], calculator_fingerprint)
    return analyze_period_ranges(
        rows, symbol, timeframe,
        [{"start_date": rows[0]["trade_date"][:10], "end_date": rows[-1]["trade_date"][:10]}],
        calculator_fingerprint,
    )


class PeriodStructureService:
    def __init__(self, store, calculator_fingerprint: str | None = None):
        self.store = store
        self.calculator_fingerprint = calculator_fingerprint or calculate_calculator_fingerprint()
        # Loading one formal run reconstructs every persisted evidence row.  A
        # chart refresh must not repeat that work while the active run is
        # unchanged, so keep immutable successful runs by their run id.
        self._snapshot_cache: dict[tuple[str, str, str], tuple[int, dict[str, Any]]] = {}

    @staticmethod
    def market_version(rows: list[dict[str, Any]]) -> str:
        raw = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def coverage(self, symbol: str, timeframe: str, adjustflag: str = "2") -> dict[str, Any]:
        with self.store._lock:
            rows = self.store.confirmed_daily_bars(symbol, adjustflag) if timeframe == "d" else self.store.market_bars(
                symbol, timeframe, adjustflag, "0000-01-01",
            )
            return self._coverage_for_rows(rows, timeframe)

    def _coverage_for_rows(self, rows: list[dict[str, Any]], timeframe: str) -> dict[str, Any]:
        start = rows[0]["trade_date"][:10] if rows else "2015-01-01"
        end = rows[-1]["trade_date"][:10] if rows else start
        return validate_coverage(rows, timeframe, self.store.trading_dates(start, end))

    @staticmethod
    def _analysis_ranges(coverage: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ranges = coverage.get("continuous_ranges") or []
        if ranges:
            return ranges
        if not rows:
            return []
        return [{"start_date": rows[0]["trade_date"][:10], "end_date": rows[-1]["trade_date"][:10]}]

    @staticmethod
    def _empty(symbol: str, timeframe: str, fingerprint: str, adjustflag: str = "2") -> dict[str, Any]:
        return {
            "meta": {
                "symbol": symbol,
                "timeframe": timeframe,
                "adjustflag": adjustflag,
                "definition_version": PERIOD_DEFINITION_VERSION,
                "calculator_fingerprint": fingerprint,
                "calculation_profile": calculation_profile(timeframe),
                "structure_version": "",
                "source_cutoff": None,
                "max_level": 0,
            },
            "structure": {
                "processed_bars": [], "fractals": [], "pens": [], "components": [],
                "centers": [], "center_revisions": [],
                "segment_proofs": [], "segment_proof_revisions": [],
                "center_candidates": [], "center_candidate_revisions": [],
                "relations": [], "issues": [], "levels": [], "unassigned_by_level": {},
            },
        }

    def ensure(
        self, symbol: str, timeframe: str, adjustflag: str = "2", force: bool = False,
    ) -> dict[str, Any]:
        # One captured input/run must survive concurrent activation and old-run cleanup.
        with self.store._lock:
            return self._ensure_locked(symbol, timeframe, adjustflag, force)

    def _ensure_locked(
        self, symbol: str, timeframe: str, adjustflag: str, force: bool,
    ) -> dict[str, Any]:
        if timeframe not in STRUCTURE_TIMEFRAMES:
            return self._empty(symbol, timeframe, self.calculator_fingerprint, adjustflag)
        rows = self.store.confirmed_daily_bars(symbol, adjustflag) if timeframe == "d" else self.store.market_bars(
            symbol, timeframe, adjustflag, "0000-01-01",
        )
        if not rows:
            return self._empty(symbol, timeframe, self.calculator_fingerprint, adjustflag)
        market_version = self.market_version(rows)
        coverage = self._coverage_for_rows(rows, timeframe)
        active = self.store.active_chan_run(symbol, timeframe, adjustflag)
        if (
            not force and active
            and active["definition_version"] == PERIOD_DEFINITION_VERSION
            and active["calculator_fingerprint"] == self.calculator_fingerprint
            and active["market_version"] == market_version
            and json.loads(active["meta_json"]).get("coverage") == coverage
        ):
            cache_key = (symbol, timeframe, adjustflag)
            cached = self._snapshot_cache.get(cache_key)
            if cached and cached[0] == int(active["id"]):
                return cached[1]
            snapshot = self.store.load_chan_structure(int(active["id"]))
            self._snapshot_cache[cache_key] = (int(active["id"]), snapshot)
            return snapshot
        result = analyze_period_ranges(
            rows, symbol, timeframe, self._analysis_ranges(coverage, rows), self.calculator_fingerprint,
        )
        result["meta"].update({
            "market_version": market_version, "coverage": coverage,
            "adjustflag": adjustflag,
            "source_cutoff": rows[-1]["trade_date"],
            "preview": False, "persisted": True,
        })
        run_id = self.store.replace_chan_structure(symbol, timeframe, adjustflag, result, market_version)
        result["meta"]["run_id"] = run_id
        self._snapshot_cache[(symbol, timeframe, adjustflag)] = (run_id, result)
        return result

    def load(self, symbol: str, timeframe: str, adjustflag: str = "2") -> dict[str, Any]:
        return self.ensure(symbol, timeframe, adjustflag)

    @staticmethod
    def _overlaps(item: dict[str, Any], start: str, end: str) -> bool:
        item_start = item.get("start_date", item.get("point_date", ""))
        item_end = item.get("end_date", item.get("point_date", item_start))
        return item_end >= start and item_start <= end

    def chart_page(
        self, symbol: str, timeframe: str, adjustflag: str, before: str | None, limit: int,
        ma_periods=(5, 10, 20, 60), boll_period: int = 20, boll_multiplier: float = 2.0,
        structure_level: int = 1, diagnostics: bool = False,
        *, snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        snapshot = snapshot if snapshot is not None else self.ensure(symbol, timeframe, adjustflag)
        page = self.store.market_page(symbol, timeframe, adjustflag, before, limit)
        bars = page["bars"]
        start = bars[0]["trade_date"] if bars else ""
        end = bars[-1]["trade_date"] if bars else ""
        source = snapshot["structure"]
        requested_level = int(structure_level)
        level = requested_level if requested_level >= 1 else None
        pens = [item for item in source.get("pens", []) if not bars or self._overlaps(item, start, end)]
        centers = [
            item for item in source.get("centers", [])
            if (level is None or int(item.get("level", 1)) == level)
            and (not bars or self._overlaps(item, start, end))
        ]
        component_ids = {
            identifier for item in centers
            for identifier in (
                [item.get("entry_component_id"), item.get("departure_component_id"), item.get("retest_component_id")]
                + list(item.get("source_component_ids", []))
            ) if identifier
        }
        components = [item for item in source.get("components", []) if item["id"] in component_ids]
        center_map = {item["id"]: item for item in source.get("center_revisions", [])}
        segment_map = {item["id"]: item for item in source.get("segment_proof_revisions", [])}
        component_map = {item["id"]: item for item in source.get("components", [])}
        center_refs = {item["id"] for item in centers}
        relations = [
            item for item in source.get("relations", [])
            if (level is None or int(item.get("level", 1)) == level)
            and (diagnostics or item.get("status") != "invalidated")
            and (diagnostics or not bars or self._overlaps(item, start, end))
        ]
        promotion_candidates = [
            {**item, "rejected_proofs": []} for item in source.get("promotion_candidates", [])
            if level is None or int(item.get("child_level", 1)) == level
            if not bars or self._overlaps(item, start, end)
        ]
        promotion_candidate_revisions = [
            item for item in source.get("promotion_candidate_revisions", [])
            if (level is None or int(item.get("child_level", 1)) == level)
            and (not bars or self._overlaps(item, start, end))
        ]
        segment_proofs = [
            item for item in source.get("segment_proofs", [])
            if (level is None or int(item.get("level", 1)) == level)
            and (not bars or self._overlaps(item, start, end))
        ]
        segment_proof_revisions = [
            item for item in source.get("segment_proof_revisions", [])
            if (level is None or int(item.get("level", 1)) == level)
            and (not bars or self._overlaps(item, start, end))
        ]
        center_candidates = [
            item for item in source.get("center_candidates", [])
            if (level is None or int(item.get("level", 1)) == level)
            and (not bars or self._overlaps(item, start, end))
        ]
        center_candidate_revisions = [
            item for item in source.get("center_candidate_revisions", [])
            if (level is None or int(item.get("level", 1)) == level)
            and (not bars or self._overlaps(item, start, end))
        ]
        for relation in relations:
            center_refs.update([relation["from_id"], relation["to_id"]])
        visited_centers: set[str] = set()
        while center_refs - visited_centers:
            for identifier in list(center_refs - visited_centers):
                visited_centers.add(identifier)
                item = center_map.get(identifier)
                if not item:
                    continue
                center_refs.update(item.get("child_center_ids", []))
                if item.get("previous_revision_id"):
                    center_refs.add(item["previous_revision_id"])
                segment_refs = set(item.get("child_segment_ids", []))
                for segment_id in segment_refs:
                    segment = segment_map.get(segment_id)
                    if segment:
                        component_ids.update(segment.get("source_pen_ids", []))
                component_ids.update(item.get("connection_component_ids", []))
                component_ids.update(filter(None, [item.get("entry_component_id"), item.get("departure_component_id"), item.get("retest_component_id")]))
        reference_centers = [item for item in source.get("center_revisions", []) if diagnostics or item["id"] in center_refs]
        components = [item for identifier, item in component_map.items() if diagnostics or identifier in component_ids]
        pen_refs = {identifier for item in [*reference_centers, *components] for identifier in item.get("source_pen_ids", [])}
        pen_refs.update(identifier for item in reference_centers if item.get("unit_kind") == "pen" for identifier in item.get("context_unit_ids", []))
        pens = [item for item in source.get("pens", []) if item["id"] in pen_refs or not bars or self._overlaps(item, start, end)]
        structure = {
            "pens": pens,
            "components": components,
            "centers": centers,
            "center_revisions": reference_centers,
            "center_candidates": center_candidates,
            "center_candidate_revisions": center_candidate_revisions if diagnostics else center_candidates,
            "promotion_candidates": promotion_candidates,
            "promotion_candidate_revisions": (
                promotion_candidate_revisions if diagnostics else []
            ),
            "segment_proofs": segment_proofs,
            "segment_proof_revisions": segment_proof_revisions if diagnostics else [],
            "relations": relations,
            "issues": source.get("issues", []) if diagnostics else [],
            "levels": source.get("levels", []),
            "unassigned_by_level": source.get("unassigned_by_level", {}) if diagnostics else {},
            "pen_diagnostics": source.get("pen_diagnostics", []) if diagnostics else [],
        }
        return {
            "meta": {
                **snapshot["meta"],
                "available": bool(bars),
                "active_level": requested_level,
                "diagnostics": diagnostics,
            },
            "market": {"symbol": symbol, "timeframe": timeframe, "adjustflag": adjustflag, "bars": bars},
            "structure": structure,
            "indicators": {
                "macd": calculate_macd(bars),
                "ma": [
                    {**item, "values": {str(period): item.get(f"ma{period}") for period in ma_periods}}
                    for item in calculate_moving_averages(bars, ma_periods)
                ],
                "boll": calculate_bollinger(bars, boll_period, boll_multiplier),
            },
            "drawings": {
                "items": self.store.drawings(symbol, timeframe),
                "version": self.store.drawings_version(symbol, timeframe),
            },
            "pagination": {"has_more": page["has_more"], "next_before": page["next_before"]},
        }

    def preview_period(
        self, symbol: str, timeframe: str, adjustflag: str, rows: list[dict[str, Any]],
        forming_bar: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        start = rows[0]["trade_date"][:10] if rows else "2015-01-01"
        end = rows[-1]["trade_date"][:10] if rows else start
        coverage = validate_coverage(rows, timeframe, self.store.trading_dates(start, end))
        result = analyze_period_ranges(
            rows, symbol, timeframe, self._analysis_ranges(coverage, rows), self.calculator_fingerprint,
            include_provisional=False,
        )
        if forming_bar:
            tail = _preview_tail_pen(rows, result["structure"].get("pens", []), forming_bar)
            if tail:
                result["structure"]["pens"].append(tail)
                # Rebuild only the in-memory hierarchy with the temporary pen.
                # The persisted run is never touched by this branch.
                ranges = self._analysis_ranges(coverage, rows)
                result["structure"].update(build_structure_hierarchy(
                    result["structure"]["pens"],
                    [],
                    [row["trade_date"] for row in rows],
                    calculation_profile=calculation_profile(timeframe), include_provisional=True,
                ))
                result["meta"]["max_level"] = result["structure"].get("max_level", 0)
                result["meta"]["structure_version"] = structure_version(result)
        result["meta"].update({"preview": True, "persisted": False, "coverage": coverage})
        assert_valid_structure(result, include_provisional=True)
        return result
