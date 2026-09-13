#!/usr/bin/env python3
"""Read-only audit for active v12 structure snapshots."""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
from urllib.parse import urlencode
from urllib.request import urlopen
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Compatibility fallback for databases created before the stock-pool table.
# Production audits resolve the scope from the current enabled pool.
DEFAULT_SYMBOLS = ("1A0001", "399673", "1A0688", "300308")
TIMEFRAMES = ("5", "30", "d", "w", "m")
ADJUSTFLAG = "2"
EXPECTED_VERSION = "chan-period-center-hierarchy-same-level-color-v12"
DOUBLE_RANGE_PREFIX = re.compile(r"R\d+-R\d+-")
ALLOWED_CENTER_RELATIONS = {
    "extension", "newborn_up", "newborn_down",
    "expansion_up", "expansion_down", "parent_child",
}


def connect(path: Path) -> sqlite3.Connection:
    # Read through WAL when it exists; immutable mode would silently ignore
    # uncheckpointed committed pages and could audit an older snapshot.
    base = f"file:{quote(str(path.resolve()))}"
    try:
        connection = sqlite3.connect(f"{base}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        connection = sqlite3.connect(f"{base}?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def enabled_symbols(connection: sqlite3.Connection) -> tuple[str, ...]:
    """Return the current production scope without inventing symbols.

    The fallback is intentionally limited to databases that predate the
    stock-pool migration.  If a pool exists and is empty, the correct scope is
    empty and the audit should report a zero-item matrix.
    """
    table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='stock_pool'"
    ).fetchone()
    if not table:
        return DEFAULT_SYMBOLS
    rows = connection.execute(
        "SELECT symbol FROM stock_pool WHERE enabled=1 ORDER BY sort_order,symbol"
    ).fetchall()
    return tuple(str(row[0]) for row in rows if row[0])


def payloads(connection: sqlite3.Connection, table: str, run_id: int) -> list[dict]:
    rows = connection.execute(
        f"SELECT payload FROM {table} WHERE run_id=? ORDER BY ordinal", (run_id,)
    ).fetchall()
    return [json.loads(row[0]) for row in rows]


def ids(items: list[dict]) -> set[str]:
    return {str(item.get("id")) for item in items if item.get("id")}


def _finite(value: object) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _date(value: object) -> str | None:
    """Return an ISO-like timestamp suitable for deterministic comparisons."""
    if not isinstance(value, str) or not value:
        return None
    try:
        # Dates in snapshots are either YYYY-MM-DD or a full ISO timestamp.
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value


def _group(item: dict) -> tuple[int, int]:
    return int(item.get("continuous_range_id", item.get("range_index", 0)) or 0), int(item.get("sequence_id", 0) or 0)


def _center_relation_is_strict(previous: dict, current: dict, direction: str) -> bool:
    try:
        previous_dd, previous_gg = float(previous["dd"]), float(previous["gg"])
        current_dd, current_gg = float(current["dd"]), float(current["gg"])
    except (KeyError, TypeError, ValueError):
        return False
    if direction == "up":
        return current_dd > previous_gg + 1e-9
    if direction == "down":
        return current_gg < previous_dd - 1e-9
    return False


def _strict_overlap_bounds(items: list[dict]) -> tuple[float, float] | None:
    bounds: list[tuple[float, float]] = []
    for item in items:
        try:
            low = float(item.get("low", min(item["start_price"], item["end_price"])))
            high = float(item.get("high", max(item["start_price"], item["end_price"])))
        except (KeyError, TypeError, ValueError):
            return None
        bounds.append((low, high))
    if not bounds:
        return None
    low = max(item[0] for item in bounds)
    high = min(item[1] for item in bounds)
    return (low, high) if low + 1e-9 < high else None


def audit_api_payload(payload: dict, requested_level: int) -> list[str]:
    """Validate the strict level contract of one chart-data response."""
    problems: list[str] = []
    try:
        active = int(payload.get("active_structure_level", 0))
        max_available = int(payload.get("max_available_center_level", 0))
    except (TypeError, ValueError):
        return ["API active/max level 不是整数"]
    expected_active = min(max(1, int(requested_level)), max_available or 1)
    if active != expected_active:
        problems.append(f"API active_structure_level 错误: 请求 L{requested_level} 得到 L{active}")
    advertised = payload.get("center_levels") or []
    if any(not isinstance(value, int) or value < 1 or value > max_available for value in advertised):
        problems.append("API center_levels 含越界级别")
    try:
        counted_levels = {int(value) for value in (payload.get("center_level_counts") or {}).keys()}
    except (TypeError, ValueError):
        counted_levels = set()
        problems.append("API center_level_counts 含非法级别")
    if sorted(set(advertised)) != sorted(counted_levels):
        problems.append("API center_levels 与 center_level_counts 不一致")
    centers = payload.get("centers") or []
    legacy_centers = payload.get("pen_centers") or []
    if centers != legacy_centers:
        problems.append("API centers 与 pen_centers 不一致")
    timeframe = str(payload.get("timeframe", ""))

    def expected_metadata(level: int) -> tuple[str, str]:
        if timeframe == "d" and level <= 3:
            period = ("d", "w", "m")[level - 1]
            return period, f"period-{period}"
        if timeframe == "w" and level <= 2:
            period = ("w", "m")[level - 1]
            return period, f"period-{period}"
        if timeframe == "m" and level == 1:
            return "m", "period-m"
        if timeframe in {"1", "5", "15", "30", "60", "120"}:
            return timeframe, f"period-{timeframe}" if level == 1 else f"period-{timeframe}-level-L{level}"
        return "higher", f"structure-higher-L{level}"

    for center in centers:
        if int(center.get("level", 0) or 0) != active:
            problems.append(f"API centers 混入非当前级别: {center.get('id')}")
        if not center.get("display_period") or not center.get("color_key"):
            problems.append(f"API center 缺少 display_period/color_key: {center.get('id')}")
        else:
            display_period, color_key = expected_metadata(active)
            if (center.get("display_period"), center.get("color_key")) != (display_period, color_key):
                problems.append(f"API center 颜色映射错误: {center.get('id')}")
    for movement in payload.get("movements") or []:
        if int(movement.get("level", 0) or 0) != active or movement.get("role") != "same_level_decomposition":
            problems.append(f"API movements 混入非当前级别/角色: {movement.get('id')}")
    advertised_movements = payload.get("movement_levels") or []
    if any(not isinstance(value, int) or value < 1 or value > max_available for value in advertised_movements):
        problems.append("API movement_levels 含越界级别")
    return problems


def fetch_api_audit(base_url: str, symbol: str, timeframe: str, level: int) -> list[str]:
    query = urlencode({"timeframe": timeframe, "adjustflag": "2", "structure_level": level, "limit": 300})
    url = f"{base_url.rstrip('/')}/api/chart-data/{symbol}?{query}"
    try:
        with urlopen(url, timeout=30) as response:  # noqa: S310 - operator supplied local URL
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # API availability is reported, not fatal to DB audit
        return [f"API 请求失败: {exc}"]
    return audit_api_payload(payload, level)


def audit_run(connection: sqlite3.Connection, symbol: str, timeframe: str) -> dict:
    active = connection.execute(
        """SELECT r.* FROM active_period_structure_runs a
           JOIN period_structure_runs r ON r.id=a.run_id
           WHERE a.symbol=? AND a.timeframe=? AND a.adjustflag=?""",
        (symbol, timeframe, ADJUSTFLAG),
    ).fetchone()
    result = {"symbol": symbol, "timeframe": timeframe, "status": "ok", "problems": []}
    if not active:
        result.update(status="failed", problems=["没有活动结构快照"])
        return result
    run = dict(active)
    result["run_id"] = run["id"]
    result["definition_version"] = run["definition_version"]
    if run["definition_version"] != EXPECTED_VERSION:
        result["problems"].append(f"活动版本不是 v12: {run['definition_version']}")
    if run["status"] != "success":
        result["problems"].append(f"活动快照状态不是 success: {run['status']}")
    movement_hash = str(run.get("movement_input_hash") or "")
    hierarchy_hash = str(run.get("hierarchy_input_hash") or "")
    if not movement_hash:
        result["problems"].append("活动快照缺少 movement_input_hash")
    if movement_hash and hierarchy_hash and movement_hash == hierarchy_hash:
        result["problems"].append("movement_input_hash 不应与 hierarchy_input_hash 相同")

    pens = payloads(connection, "period_pens", run["id"])
    centers = payloads(connection, "period_pen_centers", run["id"])
    relations = payloads(connection, "period_center_relations", run["id"])
    movements = payloads(connection, "period_movements", run["id"])
    pen_ids, center_ids, movement_ids = ids(pens), ids(centers), ids(movements)
    center_by_id = {item["id"]: item for item in centers if item.get("id")}
    movement_by_id = {item["id"]: item for item in movements if item.get("id")}
    problems: list[str] = result["problems"]

    def require_ids(owner: str, field: str, values: object, allowed: set[str]) -> None:
        if not isinstance(values, list):
            return
        for value in values:
            if value not in allowed:
                problems.append(f"{owner}.{field} 引用不存在: {value}")

    for center in centers:
        owner = f"center:{center.get('id')}"
        for field in ("source_pen_ids", "pen_ids"):
            require_ids(owner, field, center.get(field), pen_ids)
        # L1 core units are pens; higher-level units are movements. Accept the
        # legacy core_pen_ids field when present, but never silently ignore IDs.
        if int(center.get("level", 1)) <= 1:
            require_ids(owner, "core_unit_ids", center.get("core_unit_ids"), pen_ids | movement_ids)
            require_ids(owner, "core_pen_ids", center.get("core_pen_ids"), pen_ids)
        else:
            require_ids(owner, "core_unit_ids", center.get("core_unit_ids"), movement_ids | pen_ids)
        require_ids(owner, "child_movement_ids", center.get("child_movement_ids"), movement_ids)
        require_ids(owner, "child_center_ids", center.get("child_center_ids"), center_ids)
        require_ids(owner, "parent_center_ids", center.get("parent_center_ids"), center_ids)
        if DOUBLE_RANGE_PREFIX.search(str(center.get("id", ""))):
            problems.append(f"{owner} ID 重复追加 range 前缀")
        try:
            if float(center["zd"]) >= float(center["zg"]):
                problems.append(f"{owner} zd>=zg")
        except (KeyError, TypeError, ValueError):
            problems.append(f"{owner} 缺少合法 zd/zg")

    for movement in movements:
        owner = f"movement:{movement.get('id')}"
        require_ids(owner, "center_ids", movement.get("center_ids"), center_ids)
        require_ids(owner, "child_movement_ids", movement.get("child_movement_ids"), movement_ids)
        require_ids(owner, "source_pen_ids", movement.get("source_pen_ids"), pen_ids)
        require_ids(owner, "source_unit_ids", movement.get("source_unit_ids"), pen_ids | movement_ids)
        if DOUBLE_RANGE_PREFIX.search(str(movement.get("id", ""))):
            problems.append(f"{owner} ID 重复追加 range 前缀")
        if movement.get("status") == "confirmed" and not movement.get("confirmed_at"):
            problems.append(f"{owner} confirmed 但缺少 confirmed_at")
        if movement.get("start_date") and movement.get("end_date") and movement["start_date"] > movement["end_date"]:
            problems.append(f"{owner} 日期逆序")

    for relation in relations:
        if relation.get("relation") not in ALLOWED_CENTER_RELATIONS:
            problems.append(
                f"relation:{relation.get('id')} 类型未定义: {relation.get('relation')}"
            )
        previous, current = relation.get("previous_center_id"), relation.get("current_center_id")
        if previous not in center_by_id:
            problems.append(f"relation:{relation.get('id')} previous_center_id 不存在: {previous}")
        if current not in center_by_id:
            problems.append(f"relation:{relation.get('id')} current_center_id 不存在: {current}")

    # Validate every persisted movement independently of the chart page.  The
    # page API may clip a path, but a snapshot movement must always retain two
    # real endpoints and a non-overlapping source-unit interval.
    movement_groups: dict[tuple[str, int, tuple[int, int]], list[dict]] = {}
    for movement in movements:
        owner = f"movement:{movement.get('id')}"
        level = int(movement.get("level", 1) or 1)
        role = str(movement.get("role", ""))
        group = _group(movement)
        movement_groups.setdefault((role, level, group), []).append(movement)

        for field in ("start_date", "end_date"):
            if not _date(movement.get(field)):
                problems.append(f"{owner} 缺少合法 {field}")
        if _date(movement.get("start_date")) and _date(movement.get("end_date")) and movement["start_date"] > movement["end_date"]:
            problems.append(f"{owner} 日期逆序")
        for field in ("start_price", "end_price", "low", "high"):
            if not _finite(movement.get(field)):
                problems.append(f"{owner} 缺少有限 {field}")
        if movement.get("direction") not in {"up", "down"}:
            problems.append(f"{owner} 方向非法: {movement.get('direction')}")
        elif _finite(movement.get("start_price")) and _finite(movement.get("end_price")):
            start_price = float(movement["start_price"])
            end_price = float(movement["end_price"])
            if movement["direction"] == "up" and end_price <= start_price + 1e-9:
                problems.append(f"{owner} up 方向与首尾价格不一致")
            if movement["direction"] == "down" and end_price >= start_price - 1e-9:
                problems.append(f"{owner} down 方向与首尾价格不一致")
        if movement.get("status") not in {"confirmed", "provisional"}:
            problems.append(f"{owner} 状态非法: {movement.get('status')}")
        path = movement.get("path_points")
        endpoints = movement.get("endpoint_points")
        if not isinstance(path, list) or len(path) != 2:
            problems.append(f"{owner} path_points 必须恰好两个端点")
        if not isinstance(endpoints, list) or len(endpoints) != 2:
            problems.append(f"{owner} endpoint_points 必须恰好两个端点")
        if isinstance(path, list) and len(path) == 2:
            if path[0].get("trade_date") != movement.get("start_date") or path[-1].get("trade_date") != movement.get("end_date"):
                problems.append(f"{owner} path_points 日期与走势边界不一致")
            if not all(_finite(point.get("price")) and _date(point.get("trade_date")) for point in path if isinstance(point, dict)):
                problems.append(f"{owner} path_points 含非法坐标")
            elif abs(float(path[0]["price"]) - float(movement["start_price"])) > 1e-9 or abs(float(path[-1]["price"]) - float(movement["end_price"])) > 1e-9:
                problems.append(f"{owner} path_points 价格与走势边界不一致")

        if movement.get("status") == "confirmed":
            confirmed_at = _date(movement.get("confirmed_at"))
            if not confirmed_at:
                problems.append(f"{owner} confirmed 但缺少 confirmed_at")
            elif _date(movement.get("end_date")) and confirmed_at < movement["end_date"]:
                problems.append(f"{owner} confirmed_at 早于实际边界")
            confirmation_id = movement.get("confirmation_center_id")
            if confirmation_id:
                confirmation = center_by_id.get(confirmation_id)
                if confirmation is None:
                    problems.append(f"{owner} confirmation_center_id 不存在: {confirmation_id}")
                elif confirmation.get("status") != "confirmed":
                    problems.append(f"{owner} confirmation_center_id 不是已确认中枢")
                elif movement.get("confirmed_at") != confirmation.get("confirmed_at"):
                    problems.append(f"{owner} confirmed_at 与确认中枢时间不一致")
            # ``low/high`` may include an intramovement excursion (especially
            # when a higher-level unit contains its own reversal).  Validate
            # that the stored boundary is an actual source-unit endpoint;
            # extrema of the envelope are intentionally not used here.
            source_endpoints: set[tuple[str, float]] = set()
            for unit_id in movement.get("source_unit_ids") or []:
                unit = movement_by_id.get(unit_id) or next((p for p in pens if p.get("id") == unit_id), None)
                if unit and _finite(unit.get("start_price")) and _finite(unit.get("end_price")):
                    source_endpoints.add((str(unit.get("start_date")), float(unit["start_price"])))
                    source_endpoints.add((str(unit.get("end_date")), float(unit["end_price"])))
            if source_endpoints and _finite(movement.get("start_price")) and _finite(movement.get("end_price")):
                if (str(movement.get("start_date")), float(movement["start_price"])) not in source_endpoints:
                    problems.append(f"{owner} 起点不是源运动真实端点")
                if (str(movement.get("end_date")), float(movement["end_price"])) not in source_endpoints:
                    problems.append(f"{owner} 终点不是源运动真实端点")
        elif movement.get("confirmed_at"):
            problems.append(f"{owner} provisional 不应锁定 confirmed_at")

        source_units = movement.get("source_unit_ids")
        if source_units is not None and not isinstance(source_units, list):
            problems.append(f"{owner}.source_unit_ids 不是数组")
        if isinstance(source_units, list):
            # Every source unit must belong to one range/sequence.  This is a
            # cheap guard against accidentally joining across a market gap.
            source_groups = set()
            for unit_id in source_units:
                unit = movement_by_id.get(unit_id) or (next((p for p in pens if p.get("id") == unit_id), None))
                if unit is None:
                    continue
                source_groups.add(_group(unit))
            if len(source_groups) > 1:
                problems.append(f"{owner} source_unit_ids 跨 continuous_range/sequence: {sorted(source_groups)}")

    # Sequence-level invariants are evaluated per role, level, and structural
    # group.  Different continuous ranges are intentionally never compared.
    for (role, level, group), items in movement_groups.items():
        items.sort(key=lambda item: (item.get("start_date", ""), item.get("end_date", ""), int(item.get("ordinal", 0) or 0), item.get("id", "")))
        previous = None
        consumed: set[str] = set()
        for item in items:
            owner = f"movement:{item.get('id')}"
            source = item.get("source_unit_ids") or []
            duplicate = consumed.intersection(source)
            if duplicate:
                problems.append(f"{owner} 与同级走势重复消费低级单元: {sorted(duplicate)[:3]}")
            consumed.update(source)
            if previous is not None:
                if previous.get("end_date") != item.get("start_date") and role == "same_level_decomposition":
                    problems.append(f"{owner} 与前一同级走势未共享日期端点")
                elif previous.get("end_date", "") > item.get("start_date", ""):
                    problems.append(f"{owner} 与前一走势时间重叠")
                if previous.get("end_date") == item.get("start_date"):
                    try:
                        if abs(float(previous["end_price"]) - float(item["start_price"])) > 1e-9:
                            problems.append(f"{owner} 与前一走势共享时间端点但价格不一致")
                    except (KeyError, TypeError, ValueError):
                        pass
                # 盘整 + 盘整可以同向；只有两个同向趋势相邻时，才说明
                # 本应组织成一个趋势却被错误切开。
                if (
                    role == "same_level_decomposition"
                    and previous.get("classification") == item.get("classification") == "trend"
                    and previous.get("direction") == item.get("direction")
                ):
                    problems.append(f"{owner} 与前一同向趋势未合并")
            previous = item

            if item.get("classification") == "trend":
                center_list = [center_by_id.get(center_id) for center_id in item.get("center_ids", [])]
                center_list = [center for center in center_list if center]
                if len(center_list) < 2:
                    problems.append(f"{owner} trend 中枢数量不足")
                else:
                    direction = item.get("direction")
                    if any(not _center_relation_is_strict(left, right, direction) for left, right in zip(center_list, center_list[1:])):
                        problems.append(f"{owner} trend 中枢未严格同向分离")
            elif (
                item.get("classification") == "consolidation"
                and item.get("status") == "confirmed"
                and len(item.get("center_ids", [])) != 1
            ):
                problems.append(f"{owner} consolidation 中枢数量不是 1")
            elif (
                item.get("classification") == "consolidation"
                and item.get("status") == "provisional"
                and not item.get("center_ids")
            ):
                problems.append(f"{owner} provisional 至少需要一个候选中枢")

    # Center lifecycle fields are part of the v12 contract.  Keep the fixed
    # core immutable in the snapshot and ensure provisional upgrade candidates
    # have the advertised 2/3 progress.
    for center in centers:
        owner = f"center:{center.get('id')}"
        level = int(center.get("level", 1) or 1)
        if level < 1 or level > 8:
            problems.append(f"{owner} level 超出 L1-L8")
        for field in ("start_date", "end_date"):
            if not _date(center.get(field)):
                problems.append(f"{owner} 缺少合法 {field}")
        if _date(center.get("start_date")) and _date(center.get("end_date")) and center["start_date"] > center["end_date"]:
            problems.append(f"{owner} 日期逆序")
        for field in ("zd", "zg", "fixed_zd", "fixed_zg", "dd", "gg"):
            if not _finite(center.get(field)):
                problems.append(f"{owner} 缺少有限 {field}")
        try:
            if float(center["zd"]) >= float(center["zg"]):
                problems.append(f"{owner} zd>=zg")
            if float(center["fixed_zd"]) >= float(center["fixed_zg"]):
                problems.append(f"{owner} fixed_zd>=fixed_zg")
            if float(center["dd"]) > float(center["zd"]) or float(center["gg"]) < float(center["zg"]):
                problems.append(f"{owner} 外围范围未包含核心")
        except (KeyError, TypeError, ValueError):
            pass
        if center.get("status") == "provisional" and center.get("progress") != "2/3":
            problems.append(f"{owner} provisional progress 不是 2/3")
        if center.get("status") == "confirmed" and center.get("progress") not in {None, "3/3"}:
            problems.append(f"{owner} confirmed progress 非 3/3")
        if center.get("status") == "confirmed" and not _date(center.get("confirmed_at")):
            problems.append(f"{owner} confirmed 但缺少 confirmed_at")
        if (
            center.get("status") == "confirmed"
            and level >= 2
            and _date(center.get("confirmed_at"))
            and _date(center.get("end_date"))
            and center["confirmed_at"] < center["end_date"]
        ):
            problems.append(f"{owner} 高级中枢 confirmed_at 早于实际边界")

        # Canonical L1 centers must retain the P0 + P1/P2/P3 contract.  The
        # checks below deliberately ignore same-level evidence centers, which
        # are generated by the operation-view decomposition.
        if level == 1 and center.get("role", "hierarchy") == "hierarchy":
            formation = center.get("formation_pen_ids") or [
                center.get("entry_pen_id"), *(center.get("core_pen_ids") or [])
            ]
            formation = [value for value in formation if value]
            core_pen_ids = center.get("core_pen_ids") or []
            if center.get("core_unit_ids") != core_pen_ids:
                problems.append(f"{owner} L1 core_unit_ids 与 core_pen_ids 不一致")
            if center.get("child_movement_ids") != []:
                problems.append(f"{owner} L1 child_movement_ids 必须为空数组")
            if center.get("child_center_ids") != []:
                problems.append(f"{owner} L1 child_center_ids 必须为空数组")
            if "upgrade_kind" not in center or center.get("upgrade_kind") is not None:
                problems.append(f"{owner} L1 upgrade_kind 必须显式为 null")
            if center.get("progress") != "3/3":
                problems.append(f"{owner} L1 confirmed progress 必须为 3/3")
            if len(formation) != 4 or any(value not in pen_ids for value in formation):
                problems.append(f"{owner} L1 formation_pen_ids 不是四条有效笔")
            else:
                positions = [next(index for index, pen in enumerate(pens) if pen.get("id") == value) for value in formation]
                if positions != list(range(positions[0], positions[0] + 4)):
                    problems.append(f"{owner} L1 P0-P3 笔引用不连续")
                if center.get("entry_pen_id") and center.get("entry_pen_id") != formation[0]:
                    problems.append(f"{owner} L1 entry_pen_id 与 P0 不一致")
                directions = [next(pen.get("direction") for pen in pens if pen.get("id") == value) for value in formation]
                if any(direction not in {"up", "down"} for direction in directions) or any(left == right for left, right in zip(directions, directions[1:])):
                    problems.append(f"{owner} L1 P0-P3 方向未交替")
                core = [next(pen for pen in pens if pen.get("id") == value) for value in formation[1:]]
                core_bounds = [_strict_overlap_bounds([pen]) for pen in core]
                if all(bounds for bounds in core_bounds):
                    expected_zd = max(bounds[0] for bounds in core_bounds if bounds)
                    expected_zg = min(bounds[1] for bounds in core_bounds if bounds)
                    if abs(float(center.get("fixed_zd", center.get("zd"))) - expected_zd) > 1e-9 or abs(float(center.get("fixed_zg", center.get("zg"))) - expected_zg) > 1e-9:
                        problems.append(f"{owner} L1 fixed_zd/fixed_zg 与核心三笔不一致")
                entry = next(pen for pen in pens if pen.get("id") == formation[0])
                try:
                    zd, zg = float(center["zd"]), float(center["zg"])
                    start_price, end_price = float(entry["start_price"]), float(entry["end_price"])
                    if center.get("direction") == "up" and not start_price < zd < end_price:
                        problems.append(f"{owner} L1 向上进入条件不成立")
                    if center.get("direction") == "down" and not start_price > zg > end_price:
                        problems.append(f"{owner} L1 向下进入条件不成立")
                except (KeyError, TypeError, ValueError):
                    problems.append(f"{owner} L1 进入笔价格非法")

        if level >= 2 and center.get("role", "hierarchy") == "hierarchy":
            children = [movement_by_id.get(value) for value in center.get("child_movement_ids", [])]
            children = [child for child in children if child]
            expected_count = 3 if center.get("status") == "confirmed" else 2
            if len(children) != expected_count:
                problems.append(f"{owner} 高级中枢子走势数量与状态不符: {len(children)} != {expected_count}")
            if children and any(child.get("role") != "hierarchy_component" or int(child.get("level", 0) or 0) != level - 1 for child in children):
                problems.append(f"{owner} 高级中枢子走势级别/角色不符")
            if children and not _strict_overlap_bounds(children):
                problems.append(f"{owner} 高级中枢子走势未严格重叠")
            if children and any(left.get("direction") == right.get("direction") for left, right in zip(children, children[1:])):
                problems.append(f"{owner} 高级中枢子走势方向未交替")
            if center.get("upgrade_kind") not in {"extension_3x3", "expansion"}:
                problems.append(f"{owner} 高级中枢升级路径非法")
            elif center.get("upgrade_kind") == "extension_3x3" and children:
                # The extension path is specifically 3+3 (+3 on
                # confirmation).  A broad hierarchy component, a reused
                # lower-level unit, or a gap must never be accepted as a
                # nine-segment upgrade merely because the price envelopes
                # overlap.
                expected_child_count = 3 if center.get("status") == "confirmed" else 2
                child_sources: list[str] = []
                child_boundaries: list[tuple[int, int]] = []
                for child in children:
                    source = child.get("source_unit_ids") or []
                    if len(source) != 3 or len(set(source)) != 3:
                        problems.append(f"{owner} extension_3x3 子走势未严格消费三个低级单元")
                    child_sources.extend(str(value) for value in source)
                    if child.get("start_boundary") is not None and child.get("end_boundary") is not None:
                        try:
                            child_boundaries.append((int(child["start_boundary"]), int(child["end_boundary"])))
                        except (TypeError, ValueError):
                            problems.append(f"{owner} extension_3x3 子走势边界非法")
                if len(child_sources) != len(set(child_sources)):
                    problems.append(f"{owner} extension_3x3 子走势重复消费低级单元")
                if len(children) != expected_child_count:
                    problems.append(f"{owner} extension_3x3 子走势数量错误: {len(children)} != {expected_child_count}")
                if len(child_boundaries) == len(children):
                    child_boundaries.sort()
                    if any(end - start != 3 for start, end in child_boundaries):
                        problems.append(f"{owner} extension_3x3 子走势不是三个连续单元")
                    if any(right[0] != left[1] for left, right in zip(child_boundaries, child_boundaries[1:])):
                        problems.append(f"{owner} extension_3x3 子走势之间存在内部缺口")
                referenced = [
                    str(child_id)
                    for child in children
                    for child_id in (child.get("center_ids") or [])
                ]
                if referenced and len(set(referenced)) == len(referenced):
                    problems.append(f"{owner} extension_3x3 未重复引用同一低级中枢")
            child_centers = [center_by_id.get(value) for value in center.get("child_center_ids", [])]
            child_centers = [child for child in child_centers if child]
            if child_centers and any(int(child.get("level", 0) or 0) != level - 1 for child in child_centers):
                problems.append(f"{owner} child_center_ids 不是相邻低一级中枢")

    center_counts = Counter(str(item.get("level", 1)) for item in centers)
    movement_counts = Counter(f"L{item.get('level', 1)}:{item.get('role', 'same_level_decomposition')}" for item in movements)
    stored_centers = json.loads(run.get("center_level_counts") or "{}")
    stored_movements = json.loads(run.get("movement_level_counts") or "{}")
    if dict(center_counts) != dict(stored_centers):
        problems.append(f"center_level_counts 不一致: 实际={dict(center_counts)} 存储={stored_centers}")
    if dict(movement_counts) != dict(stored_movements):
        problems.append(f"movement_level_counts 不一致: 实际={dict(movement_counts)} 存储={stored_movements}")
    result.update({
        "center_count": len(centers), "movement_count": len(movements),
        "relation_count": len(relations), "center_level_counts": dict(center_counts),
        "movement_level_counts": dict(movement_counts), "problems": problems,
        "movement_input_hash": movement_hash, "hierarchy_input_hash": hierarchy_hash,
    })
    result["status"] = "failed" if problems else "ok"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "chant_agent.db")
    parser.add_argument("--report", type=Path,
                        default=ROOT / "logs" / "v12-structure-audit.json")
    parser.add_argument(
        "--symbols", nargs="+", default=None,
        help="显式审计指定标的；缺省按 stock_pool.enabled 解析生产范围",
    )
    parser.add_argument(
        "--api-base-url", default=None,
        help="可选：只读检查运行中的 chart-data API 严格级别过滤",
    )
    args = parser.parse_args()
    connection = connect(args.db)
    try:
        symbols = tuple(args.symbols) if args.symbols else enabled_symbols(connection)
        items = [audit_run(connection, symbol, timeframe)
                 for symbol in symbols for timeframe in TIMEFRAMES]
    finally:
        connection.close()
    if args.api_base_url:
        for item in items:
            symbol, timeframe = item["symbol"], item["timeframe"]
            # Check every level advertised by the snapshot.  A level absent
            # from the snapshot needs no API request.
            run_levels = set(item.get("center_level_counts", {}).keys())
            for raw_level in sorted(run_levels, key=lambda value: int(value)):
                level = int(raw_level)
                item.setdefault("api_problems", []).extend(
                    fetch_api_audit(args.api_base_url, symbol, timeframe, level)
                )
            if item.get("api_problems"):
                item["problems"].extend(f"api: {problem}" for problem in item["api_problems"])
                item["status"] = "failed"
    payload = {
        "mode": "read-only-audit", "expected_version": EXPECTED_VERSION,
        "symbols": list(symbols),
        "matrix_count": len(items),
        "success_count": sum(item["status"] == "ok" for item in items),
        "failure_count": sum(item["status"] == "failed" for item in items),
        "items": items,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if payload["failure_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
