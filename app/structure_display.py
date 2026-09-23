from copy import deepcopy
from typing import Any

from .intraday import period_date_range


def center_display_catalog(source: dict[str, Any]) -> list[dict[str, Any]]:
    revisions = {item["id"]: item for item in source.get("center_revisions", [])}
    revisions.update({item["id"]: item for item in source.get("centers", [])})
    parents: dict[str, set[str]] = {}
    candidates: dict[str, dict[str, Any]] = {}
    visited: set[str] = set()

    relation_parents: dict[str, set[str]] = {}
    for relation in source.get("relations", []):
        if relation.get("relation_type") == "promoted_into":
            relation_parents.setdefault(relation["from_id"], set()).add(relation["to_id"])

    def visit(center: dict[str, Any]) -> None:
        if center["id"] in visited or center.get("status") not in {"formed", "closed", "broken", "confirmed"}:
            return
        visited.add(center["id"])
        candidates[center["id"]] = center
        for identifier in center.get("child_center_ids", []):
            child = revisions.get(identifier)
            if child and child["level"] < center["level"]:
                parents.setdefault(identifier, set()).add(center["id"])
                visit(child)
        for identifier in relation_parents.get(center["id"], set()):
            parent = revisions.get(identifier)
            if parent and parent["level"] > center["level"]:
                parents.setdefault(center["id"], set()).add(identifier)
                visit(parent)

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


def project_center_display(
    page: dict[str, Any], source: dict[str, Any], level: int = 0, diagnostics: bool = True,
) -> dict[str, Any]:
    result = deepcopy(page)
    structure = result["structure"]
    revisions = {item["id"]: item for item in source.get("center_revisions", [])}
    revisions.update({item["id"]: item for item in source.get("centers", [])})
    segments = {
        item["id"]: item
        for item in source.get("segment_proof_revisions", source.get("segment_proofs", []))
    }
    candidate_source = source if diagnostics else structure
    candidates = {
        item["id"]: item
        for item in candidate_source.get(
            "promotion_candidate_revisions", candidate_source.get("promotion_candidates", []),
        )
    }
    catalog = center_display_catalog(source)
    structure["display_center_levels"] = sorted({revisions[item["revision_id"]]["level"] for item in catalog})
    bars = result["market"]["bars"]
    def visible(center: dict[str, Any]) -> bool:
        return bool(bars) and (level == 0 or center["level"] == level) and center["end_date"] >= bars[0]["trade_date"] and center["start_date"] <= bars[-1]["trade_date"]
    structure["display_centers"] = [item for item in catalog if visible(revisions[item["revision_id"]])]
    center_ids: set[str] = set()
    for item in structure["display_centers"]:
        center_ids.add(item["revision_id"])
        center_ids.update(item["parent_revision_ids"])
    for relation in structure.get("relations", []):
        for identifier in (relation.get("from_id"), relation.get("to_id")):
            if identifier in revisions:
                center_ids.add(identifier)
    for candidate in candidates.values():
        center_ids.update(identifier for identifier in candidate.get("source_entity_ids", []) if identifier in revisions)
        center_ids.update(identifier for identifier in candidate.get("child_center_ids", []) if identifier in revisions)
    segment_ids: set[str] = set()
    component_ids = {item["id"] for item in structure.get("components", [])}
    for relation in structure.get("relations", []):
        component_ids.update(relation.get("evidence", {}).get("connection_component_ids", []))
    visited_centers: set[str] = set()
    while center_ids - visited_centers:
        for identifier in sorted(center_ids - visited_centers):
            visited_centers.add(identifier)
            center = revisions.get(identifier)
            if not center:
                continue
            center_ids.update(center.get("child_center_ids", []))
            if center.get("previous_revision_id"):
                center_ids.add(center["previous_revision_id"])
            segment_ids.update(center.get("child_segment_ids", []))
            proof = center.get("decomposition_proof") or {}
            segment_ids.update(item.get("id") for item in proof.get("segments", []) if item.get("id"))
            component_ids.update(center.get("connection_component_ids", []))
            component_ids.update(filter(None, [center.get("entry_component_id"), center.get("departure_component_id"), center.get("retest_component_id")]))
    for identifier in list(segment_ids):
        segment = segments.get(identifier)
        if segment:
            component_ids.update(segment.get("source_component_ids", []))
    structure["center_revisions"] = [deepcopy(item) for item in revisions.values() if item["id"] in center_ids]
    structure["segment_proof_revisions"] = [deepcopy(item) for item in segments.values() if item["id"] in segment_ids]
    structure["segment_proofs"] = [item for item in structure["segment_proof_revisions"] if item.get("active", True)]
    structure["components"] = [deepcopy(item) for item in source.get("components", []) if item["id"] in component_ids]
    pen_ids = {item["id"] for item in structure.get("pens", [])}
    for item in [*structure["center_revisions"], *structure["segment_proof_revisions"], *structure["components"]]:
        pen_ids.update(item.get("source_pen_ids", []))
        if item.get("unit_kind") == "pen":
            pen_ids.update(item.get("context_unit_ids", []))
    structure["pens"] = [deepcopy(item) for item in source.get("pens", []) if item["id"] in pen_ids]
    if diagnostics:
        structure["promotion_candidates"] = [
            deepcopy(item) for item in source.get("promotion_candidates", [])
        ]
        structure["promotion_candidate_revisions"] = [
            deepcopy(item) for item in source.get("promotion_candidate_revisions", [])
        ]
        structure["center_candidates"] = [deepcopy(item) for item in source.get("center_candidates", [])]
        structure["center_candidate_revisions"] = [
            deepcopy(item) for item in source.get("center_candidate_revisions", [])
        ]
        structure["relations"] = [deepcopy(item) for item in source.get("relations", [])]
        structure["issues"] = [deepcopy(item) for item in source.get("issues", [])]
    for obsolete in ("movements", "movement_revisions", "movement_levels", "points", "point_revisions"):
        structure.pop(obsolete, None)
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
