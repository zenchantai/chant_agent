import { describe, expect, it } from "vitest";
import { centerDisplayLabel, centerParents, centersForDisplay, collapseToFinalForm, compareCenterHit, displayCenterCollection, sameDisplayRun, selectedCenterEvidence } from "./centerDisplay";
import { buildCenterAreas, buildPriceSeries, normalizeChartData } from "./chartBuilders";
import type { Center, ChartData } from "./types";

const center = (id: string, level: number, active: boolean): Center => ({
  id, kind: "center", family_id: id.split(":")[0], revision_no: Number(id.split(":")[1]) || 1, ordinal: 0, level, active, status: "formed",
  start_date: "2026-01-01", end_date: "2026-01-03", fixed_zd: 10, fixed_zg: 11, zd: 10, zg: 11,
  entry_unit_ids: [], core_unit_ids: [], z_unit_ids: [], child_center_ids: [], source_pen_ids: [], formation_modes: [],
} as unknown as Center);
const fixture = () => {
  const old = center("left:1", 1, false), child = center("left:2", 1, false), right = center("right:1", 1, false), parent = center("left:3", 2, true);
  parent.child_center_ids = [child.id, right.id]; parent.formation_modes = ["expansion_envelope_overlap"];
  const data = { centers: [parent], center_revisions: [old, child, right, parent], pens: [], components: [], relations: [], levels: [2],
    display_center_levels: [1, 2], display_centers: [
      { revision_id: child.id, display_role: "constituent", parent_revision_ids: [parent.id] },
      { revision_id: right.id, display_role: "constituent", parent_revision_ids: [parent.id] },
      { revision_id: parent.id, display_role: "active", parent_revision_ids: [] },
    ], bars: [{ trade_date: "2026-01-01", open: 10, close: 11, low: 9, high: 12 }], timeframe: "d", run_id: 1, structure_version: "same", calculator_fingerprint: "same",
  } as unknown as ChartData;
  return { data, old, child, right, parent };
};

describe("expanded center display", () => {
  it("shows constituent L1 without reactivating it or drawing every revision", () => {
    const { data, child, right } = fixture();
    const before = JSON.stringify(data);
    expect(centersForDisplay(data, null, true, { 1: true, 2: false }).map(item => item.id)).toEqual([child.id, right.id]);
    expect(displayCenterCollection(data)).toHaveLength(3);
    expect(JSON.stringify(data)).toBe(before);
    expect(normalizeChartData(data)?.center_levels).toEqual([1, 2]);
  });
  it("selects exact historical revision, replaces representative, and restores after clearing", () => {
    const { data, old, child } = fixture();
    expect(selectedCenterEvidence(data, old.id)?.id).toBe(old.id);
    const selected = centersForDisplay(data, old.id);
    expect(selected.some(item => item.id === child.id)).toBe(false);
    expect(selected.find(item => item.id === old.id)?.temporary_evidence).toBe(true);
    expect(centersForDisplay(data).some(item => item.id === child.id)).toBe(true);
  });
  it("reveals hidden exact evidence without changing switches and does not fallback on missing references", () => {
    const { data, old } = fixture();
    const levels = { 1: false, 2: false };
    const visible = centersForDisplay(data, old.id, false, levels);
    expect(visible.map(item => item.id)).toEqual([old.id]);
    expect(levels).toEqual({ 1: false, 2: false });
    expect(centerDisplayLabel(data, visible[0])).toContain("临时证据");
    expect(selectedCenterEvidence(data, "missing")).toBeUndefined();
    expect(centersForDisplay(data, "missing", false, levels)).toEqual([]);
  });
  it("draws parents before children, uses solid constituent outlines, and renders hidden selected evidence", () => {
    const { data, old } = fixture();
    const dates = ["2026-01-01", "2026-01-03"];
    const areas = buildCenterAreas({ data }, dates);
    expect(areas.visibleCenters.map(item => item.level)).toEqual([2, 1, 1]);
    expect(areas.areas[1][0].itemStyle.borderType).toBe("solid");
    expect(areas.areas[1][0].label.formatter).toContain("L2 的组成中枢");
    const context = { data, selectedStructureId: old.id, visible: { centers: false } };
    const overlay = buildCenterAreas(context, dates);
    expect(overlay.visibleCenters[0].id).toBe(old.id);
    expect(buildPriceSeries(context, overlay.areas)[0].markArea.data).toHaveLength(1);
  });
  it("orders hit testing by selected evidence, level, area and stable identity", () => {
    const { child, parent } = fixture();
    expect([{ center: parent, area: 1 }, { center: child, area: 5 }].sort(compareCenterHit)[0].center).toBe(child);
    parent.selected_evidence = true;
    expect([{ center: child, area: 1 }, { center: parent, area: 10 }].sort(compareCenterHit)[0].center).toBe(parent);
  });
  it("links an earlier revision to the expansion parent", () => {
    const { data, old, parent } = fixture();
    expect(centerParents(data, old).map(item => item.id)).toEqual([parent.id]);
    expect(selectedCenterEvidence(data, old.id)?.id).toBe(old.id);
  });
  it("refuses to merge pagination from a different run or live preview", () => {
    const { data } = fixture();
    expect(sameDisplayRun(data, { ...data })).toBe(true);
    expect(sameDisplayRun(data, { ...data, run_id: 2 })).toBe(false);
    expect(sameDisplayRun(data, { ...data, structure_preview: true })).toBe(false);
  });
});

const l2 = (id: string, boundary: "dynamic" | "fixed", revisionAt: string, children: string[], active = false): Center => ({
  ...center(id, 2, active), boundary_status: boundary, revision_at: revisionAt, child_center_ids: children,
} as unknown as Center);

const competing = (centers: Center[]): ChartData => ({
  centers: centers.filter(item => item.active), center_revisions: centers, pens: [], components: [], relations: [], levels: [2],
  display_centers: centers.map(item => ({ revision_id: item.id, display_role: item.active ? "active" : "constituent", parent_revision_ids: [] })),
  timeframe: "d", run_id: 1, structure_version: "same", calculator_fingerprint: "same",
} as unknown as ChartData);

describe("final-form collapse", () => {
  it("keeps the fixed center and drops the dynamic one sharing the same child centers", () => {
    const dyn = l2("famA:1", "dynamic", "2015-11-04", ["c1", "c2"], true);
    const fix = l2("famB:5", "fixed", "2016-03-23", ["c1", "c2"]);
    expect(collapseToFinalForm([dyn, fix]).map(item => item.id)).toEqual([fix.id]);
  });
  it("picks the latest revision when several fixed centers share the same children", () => {
    const early = l2("famA:1", "fixed", "2016-01-11", ["c1", "c2"]);
    const late = l2("famB:2", "fixed", "2017-01-11", ["c1", "c2"]);
    expect(collapseToFinalForm([early, late]).map(item => item.id)).toEqual([late.id]);
  });
  it("drops every dynamic center even when its group has no fixed alternative", () => {
    const older = l2("famA:1", "dynamic", "2016-10-26", ["c5", "c6"]);
    const newer = l2("famB:2", "dynamic", "2016-12-27", ["c5", "c6"], true);
    expect(collapseToFinalForm([older, newer])).toEqual([]);
  });
  it("drops a lone dynamic center that has no competitor at all", () => {
    const only = l2("famA:1", "dynamic", "2016-08-03", ["c7", "c8"], true);
    expect(collapseToFinalForm([only])).toEqual([]);
  });
  it("never collapses centers without child centers, so L1 survives untouched", () => {
    const first = center("l1a:1", 1, true), second = center("l1b:1", 1, true), third = center("l1c:1", 1, false);
    expect(collapseToFinalForm([first, second, third]).map(item => item.id)).toEqual([first.id, second.id, third.id]);
  });
  it("collapses through centersForDisplay only when the switch is on", () => {
    const dyn = l2("famA:1", "dynamic", "2015-11-04", ["c1", "c2"], true);
    const fix = l2("famB:5", "fixed", "2016-03-23", ["c1", "c2"]);
    const data = competing([dyn, fix]);
    expect(centersForDisplay(data, null, true, undefined, false).map(item => item.id).sort()).toEqual([dyn.id, fix.id]);
    expect(centersForDisplay(data, null, true, undefined, true).map(item => item.id)).toEqual([fix.id]);
  });
  it("still reveals a collapsed center once the user selects it", () => {
    const dyn = l2("famA:1", "dynamic", "2015-11-04", ["c1", "c2"], true);
    const fix = l2("famB:5", "fixed", "2016-03-23", ["c1", "c2"]);
    const data = competing([dyn, fix]);
    expect(centersForDisplay(data, dyn.id, true, undefined, true).map(item => item.id)).toContain(dyn.id);
  });
  it("drops the collapsed rectangle from the chart when the layer switch is on", () => {
    const dyn = l2("famA:1", "dynamic", "2015-11-04", ["c1", "c2"], true);
    const fix = l2("famB:5", "fixed", "2016-03-23", ["c1", "c2"]);
    const data = competing([dyn, fix]);
    const dates = ["2026-01-01", "2026-01-03"];
    expect(buildCenterAreas({ data, visible: { centers: true } }, dates).visibleCenters).toHaveLength(2);
    expect(buildCenterAreas({ data, visible: { centers: true, finalForm: true } }, dates).visibleCenters.map(item => item.id)).toEqual([fix.id]);
  });
});
