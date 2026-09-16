import { describe, expect, it } from "vitest";
import { groupDropPosition, reorderWatchlistGroups, reorderWatchlistSections } from "./watchlistGroupOrder";

const groups = [1, 2, 3, 4].map((id, index) => ({ id, name: `分组${id}`, sort_order: index, member_count: id }));

describe("自选分组拖动排序", () => {
  it.each([
    [1, 3, "before", [2, 1, 3, 4]],
    [4, 2, "before", [1, 4, 2, 3]],
    [1, 4, "after", [2, 3, 4, 1]],
    [4, 1, "before", [4, 1, 2, 3]],
    [4, 2, "after", [1, 2, 4, 3]],
    [1, 2, "after", [2, 1, 3, 4]],
  ] as const)("移动 %s 到 %s 的 %s", (source, target, position, expected) => {
    const result = reorderWatchlistGroups(groups, source, target, position);
    expect(result.map((group) => group.id)).toEqual(expected);
    expect(result.map((group) => group.sort_order)).toEqual([0, 1, 2, 3]);
    expect(groups.map((group) => group.id)).toEqual([1, 2, 3, 4]);
    for (const group of result) {
      expect(group.name).toBe(`分组${group.id}`);
      expect(group.member_count).toBe(group.id);
    }
  });

  it("自身、未知分组和未改变的顺序不触发保存", () => {
    expect(reorderWatchlistGroups(groups, 2, 2, "before")).toBe(groups);
    expect(reorderWatchlistGroups(groups, 9, 2, "before")).toBe(groups);
    expect(reorderWatchlistGroups(groups, 1, 9, "before")).toBe(groups);
    expect(reorderWatchlistGroups(groups, 1, 2, "before")).toBe(groups);
    expect(reorderWatchlistGroups(groups, 2, 1, "after")).toBe(groups);
    const empty: typeof groups = [];
    expect(reorderWatchlistGroups(empty, 1, 2, "before")).toBe(empty);
  });

  it("标题上半部插入前面，下半部插入后面", () => {
    expect(groupDropPosition(101, 100, 36)).toBe("before");
    expect(groupDropPosition(117, 100, 36)).toBe("before");
    expect(groupDropPosition(118, 100, 36)).toBe("after");
    expect(groupDropPosition(135, 100, 36)).toBe("after");
  });

  it("系统分组和自定义分组可以混合排序", () => {
    const sections = ["all", "ungrouped", "group-1"].map((key, sort_order) => ({key, sort_order}));
    expect(reorderWatchlistSections(sections, "all", "group-1", "before").map((item) => item.key))
      .toEqual(["ungrouped", "all", "group-1"]);
    expect(reorderWatchlistSections(sections, "group-1", "all", "before").map((item) => item.key))
      .toEqual(["group-1", "all", "ungrouped"]);
  });
});
