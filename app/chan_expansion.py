"""Independent boundary certificates; contact alone never supplies parent geometry."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

EPSILON = 1e-9


def decomposition_proofs(movements: list[dict[str, Any]], units: list[dict[str, Any]],
                         required_ids: set[str], child_level: int,
                         centers: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    by_id = {u["id"]: u for u in units}
    revisions = {c["id"]: c for center in centers or [] for c in center.get("_history", [center])}
    ordered = sorted((m for m in movements if m["level"] == child_level and m.get("classification") in {"trend", "consolidation"}),
                     key=lambda m: (m["start_date"], m["end_date"], m["id"]))
    proofs = []
    for offset in range(len(ordered) - 2):
        triple = ordered[offset:offset + 3]
        if any(m.get("status") != "confirmed" or not m.get("confirmed_at") for m in triple[:2]):
            continue
        if triple[0]["direction"] != triple[2]["direction"] or triple[0]["direction"] == triple[1]["direction"]:
            continue
        if any(a["end_date"] != b["start_date"] or abs(a["end_price"] - b["start_price"]) > EPSILON for a, b in zip(triple, triple[1:])):
            continue
        segments = [[by_id[i] for i in m.get("source_unit_ids", []) if i in by_id] for m in triple]
        if any(len(part) < 3 for part in segments):
            continue
        ids = [u["id"] for part in segments for u in part]
        if len(ids) != len(set(ids)) or not required_ids <= set(ids):
            continue
        if any(not m.get("center_revision_ids") for m in triple):
            continue
        # Retain each historical third-segment boundary; later data cannot rewrite it.
        for count in range(3, len(segments[2]) + 1):
            parts = [*segments[:2], segments[2][:count]]
            source_ids = [u["id"] for part in parts for u in part]
            if not required_ids <= set(source_ids):
                continue
            certificate = []
            valid = True
            for index, (movement, part) in enumerate(zip(triple, parts)):
                fixed = index < 2 or (count == len(segments[2]) and movement["status"] == "confirmed")
                cutoff = max([u["confirmed_at"] for u in part] + ([movement["confirmed_at"]] if fixed else []))
                part_ids = {u["id"] for u in part}
                families = {revisions[i].get("family_id", i) for i in movement["center_revision_ids"] if i in revisions}
                eligible = sorted((r for r in revisions.values() if r.get("family_id", r["id"]) in families
                            and r["level"] == child_level and r.get("formation_stage") == "directional"
                            and r.get("revision_at", r["formed_at"]) <= cutoff
                            and set(r.get("owned_unit_ids", [])) <= part_ids), key=lambda r:(r["revision_at"],r["id"]))
                evidence_by_family = {}
                for r in eligible:
                    evidence_by_family.setdefault(r.get("family_id",r["id"]),r)
                evidence = list(evidence_by_family.values())
                if not evidence:
                    valid = False
                    break
                certificate.append({
                    "movement_revision_id": movement["id"], "level": child_level,
                    "direction": movement["direction"], "status": "confirmed" if fixed else "provisional",
                    "start_date": part[0]["start_date"], "end_date": part[-1]["end_date"],
                    "source_unit_ids": [u["id"] for u in part],
                    "source_pen_ids": list(dict.fromkeys(p for u in part for p in u.get("source_pen_ids", [u["id"]]))),
                    "low": min(u["low"] for u in part), "high": max(u["high"] for u in part),
                    "level_evidence_ids": [c["id"] for c in evidence],
                    "completion_evidence_id": movement.get("end_point_id") if fixed else None,
                    "available_at": max([u["confirmed_at"] for u in part] + ([movement["confirmed_at"]] if fixed else [])),
                })
            if not valid:
                continue
            zd = max(part["low"] for part in certificate)
            zg = min(part["high"] for part in certificate)
            if zd + EPSILON >= zg:
                continue
            if certificate[-1]["status"] == "confirmed":
                dynamic = deepcopy(certificate)
                dynamic_time = max(u["confirmed_at"] for u in parts[-1])
                grade = [revisions[i] for i in dynamic[-1]["level_evidence_ids"] if revisions[i]["revision_at"] <= dynamic_time]
                if grade and dynamic_time < certificate[-1]["available_at"]:
                    dynamic[-1].update(status="provisional", completion_evidence_id=None, available_at=dynamic_time,
                                       level_evidence_ids=[r["id"] for r in grade])
                    proofs.append({"segments":dynamic,"source_unit_ids":source_ids,"zd":zd,"zg":zg,
                                   "boundary_status":"dynamic","available_at":max(s["available_at"] for s in dynamic)})
            proofs.append({"segments": deepcopy(certificate), "source_unit_ids": source_ids,
                           "zd": zd, "zg": zg, "boundary_status": "fixed" if certificate[-1]["status"] == "confirmed" else "dynamic",
                           "available_at": max(p["available_at"] for p in certificate)})
    proofs.sort(key=lambda p: (p["available_at"], tuple((s["start_date"], s["end_date"]) for s in p["segments"])))
    if not proofs:
        return []
    chosen = tuple(s["movement_revision_id"] for s in proofs[0]["segments"])
    return [p for p in proofs if tuple(s["movement_revision_id"] for s in p["segments"]) == chosen]


def internal_decomposition_proofs(units: list[dict[str, Any]], centers: list[dict[str, Any]],
                                  points: list[dict[str, Any]], required_ids: set[str],
                                  child_level: int) -> list[dict[str, Any]]:
    """Local construction certificates do not complete or repartition global movements.

    The first two boundaries require independently confirmed reversal points.
    Every segment must contain its own directional center of the child level.
    An entry unit before the first child's owned span is never used as padding.
    """
    positions = {u['id']: i for i, u in enumerate(units)}
    if not required_ids or not required_ids <= positions.keys():
        return []
    start = min(positions[i] for i in required_ids)
    revisions = [r for c in centers for r in c.get('_history', [c])
                 if r.get('formation_stage') == 'directional' and r['level'] == child_level]
    boundaries = sorted((p for p in points if p.get('status') == 'confirmed'
                         and p.get('level') == child_level and p.get('source_unit_id') in positions
                         and p.get('point_type') in {'first_buy','first_sell','consolidation_divergence_buy','consolidation_divergence_sell'}),
                        key=lambda p: (p['confirmed_at'], p['point_date'], p['id']))
    proposals = []
    for first in boundaries:
        end_a = positions[first['source_unit_id']] + 1
        if end_a - start < 3:
            continue
        for second in boundaries:
            end_b = positions[second['source_unit_id']] + 1
            if end_b - end_a < 3 or len(units) - end_b < 3:
                continue
            ranges = [units[start:end_a], units[end_a:end_b], units[end_b:]]
            moves = []
            for index, part in enumerate(ranges):
                direction = 'up' if part[-1]['end_price'] > part[0]['start_price'] + EPSILON else 'down'
                endpoint = (first, second)[index] if index < 2 else None
                if index == 2:
                    eligible = [p for p in boundaries if positions[p['source_unit_id']] >= end_b + 2
                                and p['point_type'].endswith('sell' if direction == 'up' else 'buy')]
                    if eligible:
                        endpoint = min(eligible,key=lambda p:(positions[p['source_unit_id']],p['confirmed_at'],p['id']))
                        part = units[end_b:positions[endpoint['source_unit_id']] + 1]
                        direction = 'up' if part[-1]['end_price'] > part[0]['start_price'] + EPSILON else 'down'
                if endpoint and not endpoint['point_type'].endswith('sell' if direction == 'up' else 'buy'):
                    break
                cutoff = max([u['confirmed_at'] for u in part] + ([endpoint['confirmed_at']] if endpoint else []))
                ids = {u['id'] for u in part}
                contained = [r for r in revisions if r['revision_at'] <= cutoff and set(r['owned_unit_ids']) <= ids]
                # A single proven center family establishes a consolidation's level.
                # Multi-center local trends need a separate classification certificate.
                families = {r['family_id'] for r in contained}
                if len(families) != 1:
                    break
                witness = min(contained,key=lambda r:(r['revision_at'],r['id']))
                moves.append(dict(id=f"internal:{child_level}:{part[0]['id']}:{endpoint['family_id'] if index < 2 else 'open'}",
                    level=child_level, classification='consolidation', direction=direction,
                    status='confirmed' if endpoint else 'provisional', confirmed_at=cutoff if endpoint else None,
                    start_date=part[0]['start_date'],end_date=part[-1]['end_date'],
                    start_price=part[0]['start_price'],end_price=part[-1]['end_price'],
                    source_unit_ids=[u['id'] for u in part],center_revision_ids=[witness['id']],
                    end_point_id=endpoint['family_id'] if endpoint else None))
            if len(moves) != 3:
                continue
            for proof in decomposition_proofs(moves,units,required_ids,child_level,centers):
                for segment in proof['segments']:
                    segment['construction_scope'] = 'internal'
                proposals.append(proof)
    proposals.sort(key=lambda p:(p['available_at'],tuple((s['start_date'],s['end_date']) for s in p['segments'])))
    if not proposals:
        return []
    identity = tuple(s['movement_revision_id'] for s in proposals[0]['segments'])
    unique = {}
    for proof in proposals:
        if tuple(s['movement_revision_id'] for s in proof['segments']) == identity:
            unique.setdefault((tuple(proof['source_unit_ids']),proof['boundary_status']),proof)
    return list(unique.values())
