import type { Center, ChartData, StructuralPoint } from "./types";
import { dailyL2Version, isReferenceProfile } from "./dailyL2Overlay";

export const isWeeklyCoreCenter = (center: Center, timeframe: string): boolean => timeframe === "w" && center.level === 1;

/** Only the weekly native rectangle is shortened; source revisions and daily projections stay intact. */
export const centerDisplayRange = (center: Center, timeframe: string): {start_date: string; end_date: string} | null => {
  if (isWeeklyCoreCenter(center, timeframe)) {
    return center.core_start_date && center.core_end_date
      ? {start_date: center.core_start_date, end_date: center.core_end_date} : null;
  }
  return {start_date: center.start_date, end_date: center.end_date};
};

export const centerRevisionMap = (data: ChartData) => new Map([...data.center_revisions, ...data.centers].map((center) => [center.id, center]));

export const selectedPoint = (data: ChartData, selectedId?: string | null): StructuralPoint | undefined =>
  (data.points || []).find((point) => point.id === selectedId);

export const selectedCenterEvidence = (data: ChartData, selectedId?: string | null): Center | undefined => {
  const point = selectedPoint(data, selectedId);
  return centerRevisionMap(data).get(point ? point.center_revision_id : selectedId || "");
};

export const displayCenterCollection = (data: ChartData): Center[] => {
  const revisions = centerRevisionMap(data);
  const references = data.display_centers ?? data.centers.map((center) => ({ revision_id: center.id, display_role: "active" as const, parent_revision_ids: [] }));
  return references.flatMap((reference) => {
    const center = revisions.get(reference.revision_id);
    return center && (!isReferenceProfile(data) || center.level === 1) ? [{ ...center, display_role: reference.display_role, parent_revision_ids: reference.parent_revision_ids }] : [];
  });
};

export const centerParents = (data: ChartData, center: Center): Center[] => {
  const revisions = centerRevisionMap(data);
  return data.center_revisions.filter((parent) => parent.formation_modes.includes("expansion_envelope_overlap") && parent.child_center_ids.some((identifier) => {
    const child = revisions.get(identifier);
    return child?.family_id === center.family_id && child.level === center.level;
  }));
};

export const centerDisplayLabel = (data: ChartData, center: Center): string => {
  const parents = centerParents(data, center);
  const suffix = !center.active && parents.length ? ` · ${[...new Set(parents.map((parent) => `L${parent.level}`))].join("/")} 的组成中枢` : "";
  const candidate = ["candidate", "provisional", "pending"].includes(center.status) ? " · 候选" : "";
  return `L${center.level} ${isWeeklyCoreCenter(center, data.timeframe) ? "三笔核心" : "中枢"} #${center.ordinal + 1}${suffix}${candidate}${center.temporary_evidence ? " · 临时证据" : ""}`;
};

export const centersForDisplay = (data: ChartData, selectedId?: string | null, enabled = true, levels?: Record<string, boolean>): Center[] => {
  // Reference charts expose one native center layer, so obsolete L1/L2 preferences do not hide it.
  if (isReferenceProfile(data)) levels = undefined;
  let centers = displayCenterCollection(data).filter((center) => enabled && levels?.[String(center.level)] !== false);
  const selected = selectedCenterEvidence(data, selectedId);
  if (selected && (!isReferenceProfile(data) || selected.level === 1)) {
    const representative = displayCenterCollection(data).find((center) => center.family_id === selected.family_id && center.level === selected.level);
    centers = centers.filter((center) => center.family_id !== selected.family_id || center.level !== selected.level);
    centers.push({ ...selected, display_role: representative?.display_role, parent_revision_ids: representative?.parent_revision_ids,
      selected_evidence: true, temporary_evidence: !enabled || levels?.[String(selected.level)] === false || representative?.id !== selected.id });
  }
  return centers.sort((left, right) => right.level - left.level || left.start_date.localeCompare(right.start_date) || left.id.localeCompare(right.id));
};

export const compareCenterHit = (left: { center: Center; area: number }, right: { center: Center; area: number }) =>
  Number(Boolean(right.center.selected_evidence)) - Number(Boolean(left.center.selected_evidence)) || left.center.level - right.center.level || left.area - right.area || left.center.id.localeCompare(right.center.id);

export const sameDisplayRun = (left: ChartData, right: ChartData): boolean =>
  left.run_id === right.run_id && left.structure_version === right.structure_version && left.calculator_fingerprint === right.calculator_fingerprint && left.calculation_profile === right.calculation_profile && dailyL2Version(left) === dailyL2Version(right) && !left.structure_preview && !right.structure_preview;
