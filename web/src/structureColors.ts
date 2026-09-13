export const periodStructureColors = {
  dark: { "1": "#42B8D4", "5": "#35C7A0", "15": "#35C7A0", "30": "#42B8D4", "60": "#42B8D4", "120": "#F2C14E", d: "#F2C14E", w: "#A78BFA", m: "#FF7043" },
  light: { "1": "#08758F", "5": "#087F68", "15": "#087F68", "30": "#08758F", "60": "#08758F", "120": "#A87800", d: "#A87800", w: "#6D43B5", m: "#C43F24" },
} as const;

export type StructureTheme = "dark" | "light";

export type LevelStructureAppearance = {
  color: string;
  colorKey: string;
  displayPeriod: string;
};

export type LevelStructureMetadata = {
  color: string;
  color_key: string;
  display_period: string;
};

const intradayLevelColors: Record<StructureTheme, Record<string, string[]>> = {
  dark: {
    "1": ["#42B8D4", "#76CEE0", "#278DA7", "#9BE0EC", "#1D6D80"],
    "5": ["#35C7A0", "#69D7BA", "#22977D", "#93E5D0", "#176B59"],
    "15": ["#35C7A0", "#69D7BA", "#22977D", "#93E5D0", "#176B59"],
    "30": ["#42B8D4", "#76CEE0", "#278DA7", "#9BE0EC", "#1D6D80"],
    "60": ["#42B8D4", "#76CEE0", "#278DA7", "#9BE0EC", "#1D6D80"],
    "120": ["#F2C14E", "#FFD778", "#B88A1D", "#FFE39A", "#806012"],
  },
  light: {
    "1": ["#08758F", "#0B91AD", "#07596C", "#18A9C5", "#06414F"],
    "5": ["#087F68", "#0B9B80", "#06614F", "#16B193", "#044638"],
    "15": ["#087F68", "#0B9B80", "#06614F", "#16B193", "#044638"],
    "30": ["#08758F", "#0B91AD", "#07596C", "#18A9C5", "#06414F"],
    "60": ["#08758F", "#0B91AD", "#07596C", "#18A9C5", "#06414F"],
    "120": ["#A87800", "#C28B08", "#805B00", "#D49D1C", "#624600"],
  },
};

const higherLevelColors: Record<StructureTheme, string[]> = {
  dark: ["#4DD0E1", "#EC75B5", "#9CCC65", "#5C9DED", "#E6A15A"],
  light: ["#00838F", "#AD1457", "#558B2F", "#245EAA", "#A65408"],
};

const themeKey = (theme: string): StructureTheme => theme === "light" ? "light" : "dark";
const structureLevel = (level: number) => Number.isFinite(level) ? Math.max(1, Math.floor(level)) : 1;
const higherLevelColor = (theme: StructureTheme, level: number) => {
  const colors = higherLevelColors[theme];
  return colors[Math.max(0, structureLevel(level) - 2) % colors.length];
};

export const periodStructureColor = (theme: string, timeframe: string) => {
  const palette = periodStructureColors[themeKey(theme)];
  return palette[timeframe as keyof typeof palette] || palette.d;
};

export const levelStructureAppearance = (
  theme: string,
  chartTimeframe: string,
  level: number,
): LevelStructureAppearance => {
  const paletteTheme = themeKey(theme);
  const normalizedLevel = structureLevel(level);
  const knownTimeframes = new Set(["1", "5", "15", "30", "60", "120", "d", "w", "m"]);
  const timeframe = knownTimeframes.has(chartTimeframe) ? chartTimeframe : "d";

  if (timeframe === "d" && normalizedLevel <= 3) {
    const displayPeriod = (["d", "w", "m"] as const)[normalizedLevel - 1];
    return {
      color: periodStructureColor(paletteTheme, displayPeriod),
      colorKey: `period-${displayPeriod}`,
      displayPeriod,
    };
  }
  if (timeframe === "w" && normalizedLevel <= 2) {
    const displayPeriod = (["w", "m"] as const)[normalizedLevel - 1];
    return {
      color: periodStructureColor(paletteTheme, displayPeriod),
      colorKey: `period-${displayPeriod}`,
      displayPeriod,
    };
  }
  if (timeframe === "m" && normalizedLevel === 1) {
    return {
      color: periodStructureColor(paletteTheme, "m"),
      colorKey: "period-m",
      displayPeriod: "m",
    };
  }

  const intradayColors = intradayLevelColors[paletteTheme][timeframe];
  if (intradayColors) {
    return {
      color: intradayColors[(normalizedLevel - 1) % intradayColors.length],
      colorKey: normalizedLevel === 1
        ? `period-${timeframe}`
        : `period-${timeframe}-level-L${normalizedLevel}`,
      displayPeriod: timeframe,
    };
  }

  return {
    color: higherLevelColor(paletteTheme, normalizedLevel),
    colorKey: `structure-higher-L${normalizedLevel}`,
    displayPeriod: "higher",
  };
};

export const levelStructureColor = (theme: string, chartTimeframe: string, level: number) =>
  levelStructureAppearance(theme, chartTimeframe, level).color;

export const levelStructureMetadata = (
  theme: string,
  chartTimeframe: string,
  level: number,
): LevelStructureMetadata => {
  const appearance = levelStructureAppearance(theme, chartTimeframe, level);
  return {
    color: appearance.color,
    color_key: appearance.colorKey,
    display_period: appearance.displayPeriod,
  };
};
