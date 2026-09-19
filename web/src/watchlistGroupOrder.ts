import type { WatchlistGroup } from "./types";

export type GroupDropPosition = "before" | "after";
export type WatchlistSection = { key: string; sort_order: number };

export const groupDropPosition = (pointerY: number, top: number, height: number): GroupDropPosition =>
  pointerY < top + height / 2 ? "before" : "after";

export const reorderWatchlistGroups = (
  groups: WatchlistGroup[], sourceId: number, targetId: number, position: GroupDropPosition,
): WatchlistGroup[] => {
  const moving = groups.find((group) => group.id === sourceId);
  if (!moving || sourceId === targetId || !groups.some((group) => group.id === targetId)) return groups;
  const reordered = groups.filter((group) => group.id !== sourceId);
  const targetIndex = reordered.findIndex((group) => group.id === targetId);
  reordered.splice(targetIndex + (position === "after" ? 1 : 0), 0, moving);
  if (reordered.every((group, index) => group.id === groups[index].id)) return groups;
  return reordered.map((group, index) => ({ ...group, sort_order: index }));
};

export const reorderWatchlistSections = <T extends WatchlistSection>(
  sections: T[], sourceKey: string, targetKey: string, position: GroupDropPosition,
): T[] => {
  if (sourceKey === targetKey || !sections.some((section) => section.key === sourceKey)
      || !sections.some((section) => section.key === targetKey)) return sections;
  const moving = sections.find((section) => section.key === sourceKey)!;
  const reordered = sections.filter((section) => section.key !== sourceKey);
  const targetIndex = reordered.findIndex((section) => section.key === targetKey);
  reordered.splice(targetIndex + (position === "after" ? 1 : 0), 0, moving);
  if (reordered.every((section, index) => section.key === sections[index].key)) return sections;
  return reordered.map((section, index) => ({ ...section, sort_order: index }));
};

export const normalizeSectionOrder = (order: string[], groups: WatchlistGroup[]): string[] => {
  const expected = ["all", "ungrouped", ...groups.map((group) => `group-${group.id}`)];
  return [...new Set([...order.filter((key) => expected.includes(key)), ...expected])];
};
