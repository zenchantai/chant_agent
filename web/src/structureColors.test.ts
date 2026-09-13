import { describe, expect, it } from "vitest";
import {
  levelStructureAppearance,
  levelStructureColor,
  levelStructureMetadata,
  periodStructureColor,
} from "./structureColors";

describe("period structure colors", () => {
  it("groups intraday structure colors by the requested timeframe bands", () => {
    expect(periodStructureColor("dark", "15")).toBe(periodStructureColor("dark", "5"));
    expect(periodStructureColor("dark", "60")).toBe(periodStructureColor("dark", "30"));
    expect(periodStructureColor("dark", "120")).toBe(periodStructureColor("dark", "d"));
    expect(periodStructureColor("light", "15")).toBe(periodStructureColor("light", "5"));
    expect(periodStructureColor("light", "60")).toBe(periodStructureColor("light", "30"));
    expect(periodStructureColor("light", "120")).toBe(periodStructureColor("light", "d"));
  });

  it("keeps daily pens and centers yellow", () => {
    expect(periodStructureColor("dark", "d")).toBe("#F2C14E");
    expect(periodStructureColor("light", "d")).toBe("#A87800");
  });

  it("falls back to the daily palette for an unknown timeframe", () => {
    expect(periodStructureColor("dark", "unknown")).toBe("#F2C14E");
  });
});

describe("structure level colors", () => {
  it("maps daily L1/L2/L3 to the daily, weekly and monthly palettes", () => {
    expect(levelStructureAppearance("dark", "d", 1)).toEqual({
      color: "#F2C14E", colorKey: "period-d", displayPeriod: "d",
    });
    expect(levelStructureAppearance("dark", "d", 2)).toEqual({
      color: "#A78BFA", colorKey: "period-w", displayPeriod: "w",
    });
    expect(levelStructureAppearance("light", "d", 3)).toEqual({
      color: "#C43F24", colorKey: "period-m", displayPeriod: "m",
    });
  });

  it("maps weekly and monthly levels without changing their calculation timeframe", () => {
    expect(levelStructureAppearance("dark", "w", 1).colorKey).toBe("period-w");
    expect(levelStructureAppearance("dark", "w", 2).colorKey).toBe("period-m");
    expect(levelStructureAppearance("dark", "w", 3).colorKey).toBe("structure-higher-L3");
    expect(levelStructureAppearance("dark", "m", 1).colorKey).toBe("period-m");
    expect(levelStructureAppearance("dark", "m", 2).colorKey).toBe("structure-higher-L2");
  });

  it("keeps intraday higher levels as stable variants of their own timeframe", () => {
    const l1 = levelStructureAppearance("dark", "5", 1);
    const l2 = levelStructureAppearance("dark", "5", 2);
    expect(l1).toEqual({ color: "#35C7A0", colorKey: "period-5", displayPeriod: "5" });
    expect(l2.colorKey).toBe("period-5-level-L2");
    expect(l2.displayPeriod).toBe("5");
    expect(l2.color).not.toBe(l1.color);
    expect(levelStructureColor("dark", "5", 2)).toBe(l2.color);
  });

  it("uses a dedicated higher-level palette beyond the named daily periods", () => {
    const l4 = levelStructureAppearance("dark", "d", 4);
    expect(l4.colorKey).toBe("structure-higher-L4");
    expect(l4.displayPeriod).toBe("higher");
    expect(l4.color).not.toBe(periodStructureColor("dark", "d"));
  });

  it("falls back unknown chart timeframes to the daily display mapping", () => {
    expect(levelStructureColor("dark", "unknown", 1)).toBe(periodStructureColor("dark", "d"));
  });

  it("exposes API-compatible snake_case metadata", () => {
    expect(levelStructureMetadata("dark", "d", 2)).toEqual({
      color: "#A78BFA", color_key: "period-w", display_period: "w",
    });
  });
});
