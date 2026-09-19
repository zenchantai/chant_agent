from copy import deepcopy
from typing import Any

from .intraday import period_date_range


def center_display_catalog(source: dict[str, Any]) -> list[dict[str, Any]]:
    revisions = {item["id"]: item for item in source.get("center_revisions", [])}
    revisions.update({item["id"]: item for item in source.get("centers", [])})
    parents: dict[str, set[str]] = {}
    candidates: dict[str, dict[str, Any]] = {}
    visited: set[str] = set()

    def visit(center: dict[str, Any]) -> None:
        if center["id"] in visited or center.get("status") not in {"formed", "closed", "broken", "confirmed"}:
            return
        visited.add(center["id"])
        candidates[center["id"]] = center
        if "expansion_envelope_overlap" not in center.get("formation_modes", []):
            return
        for identifier in center.get("child_center_ids", []):
            child = revisions.get(identifier)
            if child and child["level"] < center["level"]:
                parents.setdefault(identifier, set()).add(center["id"])
                visit(child)

    active_ids = {item["id"] for item in source.get("centers", []) if item.get("active", True)}
    for identifier in sorted(active_ids):
        visit(revisions[identifier])
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for center in candidates.values():
        grouped.setdefault((center["family_id"], center["level"]), []).append(center)
    result = []
    for members in grouped.values():
        selected = max(members, key=lambda item: (item["id"] in active_ids, item.get("revision_at", item.get("formed_at", "")), item["revision_no"], item["id"]))
        result.append({
            "revision_id": selected["id"],
            "display_role": "active" if selected["id"] in active_ids else "constituent",
            "parent_revision_ids": sorted(parents.get(selected["id"], set())),
        })
    return sorted(result, key=lambda item: (revisions[item["revision_id"]]["level"], revisions[item["revision_id"]]["start_date"], item["revision_id"]))


def project_center_display(page: dict[str, Any], source: dict[str, Any], level: int = 0) -> dict[str, Any]:
    result = deepcopy(page)
    structure = result["structure"]
    revisions = {item["id"]: item for item in source.get("center_revisions", [])}
    revisions.update({item["id"]: item for item in source.get("centers", [])})
    catalog = center_display_catalog(source)
    structure["display_center_levels"] = sorted({revisions[item["revision_id"]]["level"] for item in catalog})
    bars = result["market"]["bars"]
    def visible(center: dict[str, Any]) -> bool:
        return bool(bars) and (level == 0 or center["level"] == level) and center["end_date"] >= bars[0]["trade_date"] and center["start_date"] <= bars[-1]["trade_date"]
    structure["display_centers"] = [item for item in catalog if visible(revisions[item["revision_id"]])]
    center_ids = {item["id"] for item in structure.get("center_revisions", [])}
    for item in structure["display_centers"]:
        center_ids.add(item["revision_id"])
        center_ids.update(item["parent_revision_ids"])
    center_ids.update(item["center_revision_id"] for item in structure.get("points", []))
    for point in structure.get("points", []):
        center = revisions.get(point["center_revision_id"])
        if center:
            for reference in catalog:
                representative = revisions[reference["revision_id"]]
                if (representative["family_id"], representative["level"]) == (center["family_id"], center["level"]):
                    center_ids.update(reference["parent_revision_ids"])
    movement_ids = {item["id"] for item in structure.get("movement_revisions", [])}
    component_ids = {item["id"] for item in structure.get("components", [])}
    component_ids.update(identifier for item in structure.get("points", []) for identifier in item.get("source_component_ids", []))
    movements = {item["id"]: item for item in source.get("movement_revisions", [])}
    visited_centers: set[str] = set()
    visited_movements: set[str] = set()
    while center_ids - visited_centers or movement_ids - visited_movements:
        for identifier in sorted(center_ids - visited_centers):
            visited_centers.add(identifier)
            center = revisions.get(identifier)
            if not center:
                continue
            center_ids.update(center.get("child_center_ids", []))
            if center.get("previous_revision_id"):
                center_ids.add(center["previous_revision_id"])
            movement_ids.update(center.get("child_movement_ids", []))
            if center.get("unit_kind") == "movement":
                movement_ids.update(center.get("context_unit_ids", []))
                movement_ids.update(center.get("owned_unit_ids", []))
            component_ids.update(center.get("connection_component_ids", []))
            component_ids.update(filter(None, [center.get("entry_component_id"), center.get("departure_component_id"), center.get("retest_component_id")]))
        for identifier in sorted(movement_ids - visited_movements):
            visited_movements.add(identifier)
            movement = movements.get(identifier)
            if movement:
                center_ids.update(movement.get("center_revision_ids", []))
                movement_ids.update(movement.get("child_movement_ids", []))
    structure["center_revisions"] = [deepcopy(item) for item in revisions.values() if item["id"] in center_ids]
    structure["movement_revisions"] = [deepcopy(item) for item in movements.values() if item["id"] in movement_ids]
    structure["components"] = [deepcopy(item) for item in source.get("components", []) if item["id"] in component_ids]
    pen_ids = {item["id"] for item in structure.get("pens", [])}
    for item in [*structure["center_revisions"], *structure["movement_revisions"], *structure["components"]]:
        pen_ids.update(item.get("source_pen_ids", []))
        if item.get("unit_kind") == "pen":
            pen_ids.update(item.get("context_unit_ids", []))
    structure["pens"] = [deepcopy(item) for item in source.get("pens", []) if item["id"] in pen_ids]
    return result


def project_daily_l2(
    page: dict[str, Any], snapshot: dict[str, Any] | None,
    error: str | None = None,
) -> dict[str, Any]:
    """Project formal daily geometry without joining the target structure graph."""
    timeframe = page["market"]["timeframe"]
    if timeframe not in {"w", "m"}:
        return page
    overlay: dict[str, Any] = {"status": "unavailable", "error": error, "source": None, "centers": []}
    result = {**page, "overlays": {**page.get("overlays", {}), "daily_l2": overlay}}
    if not snapshot or not snapshot["meta"].get("source_cutoff") or snapshot["meta"].get("preview"):
        overlay["error"] = error or "暂无已收盘日线数据"
        return result
    meta = snapshot["meta"]
    if (meta.get("timeframe") != "d" or meta.get("symbol") != page["market"]["symbol"]
            or meta.get("adjustflag") != page["market"]["adjustflag"]):
        overlay["error"] = "日线来源与当前股票或复权方式不一致"
        return result
    source = snapshot["structure"]
    overlay.update({
        "status": "stale" if error else "ready",
        "source": {
            key: meta.get(key) for key in (
                "symbol", "timeframe", "adjustflag", "run_id", "definition_version", "calculator_fingerprint",
                "market_version", "structure_version", "source_cutoff",
            )
        },
    })
    bars = page["market"]["bars"]
    if not bars:
        return result
    buckets = [(bar["trade_date"], *period_date_range(bar["trade_date"], timeframe)) for bar in bars]
    revisions = {item["id"]: item for item in source.get("center_revisions", [])}
    revisions.update({item["id"]: item for item in source.get("centers", [])})
    for reference in center_display_catalog(source):
        center = revisions[reference["revision_id"]]
        if int(center["level"]) != 2:
            continue
        start, end = center["start_date"][:10], center["end_date"][:10]
        covered = [bucket for bucket in buckets if bucket[2] >= start and bucket[1] <= end]
        if not covered:
            continue
        overlay["centers"].append({
            **{key: center[key] for key in (
                "family_id", "ordinal", "level", "status", "start_date", "end_date", "zd", "zg",
            )},
            "id": f"daily-l2:{center['id']}",
            **reference,
            "active": bool(center.get("active", True)),
            "source_timeframe": "d",
            "target_start_date": covered[0][0],
            "target_end_date": covered[-1][0],
            "clipped_start": start < buckets[0][1],
            "clipped_end": end > buckets[-1][2],
        })
    return result
