export const formatPrice = (value: number | null | undefined) =>
  Number.isFinite(value) ? Number(value).toFixed(2) : "--";

export const formatCompactNumber = (value: number | null | undefined) => {
  if (!Number.isFinite(value)) return "--";
  const numeric = Number(value);
  const magnitude = Math.abs(numeric);
  if (magnitude >= 100_000_000) return `${(numeric / 100_000_000).toFixed(2)}亿`;
  if (magnitude >= 10_000) return `${Math.round(numeric / 10_000)}万`;
  return Math.round(numeric).toLocaleString("zh-CN");
};

export const formatVolume = (value: number | null | undefined) => {
  const formatted = formatCompactNumber(value);
  return formatted === "--" ? formatted : `${formatted}手`;
};

export const formatIndicatorValue = (value: number | null | undefined) =>
  Number.isFinite(value) ? Number(value).toFixed(2) : "--";
