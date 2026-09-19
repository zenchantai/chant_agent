import type { Bar } from "./types";

export function chartDates(timeframe: string, bars: Bar[]): string[] {
  if (timeframe !== "1" || !bars.length) return bars.map((bar) => bar.trade_date);
  const day = bars[bars.length - 1].trade_date.slice(0, 10);
  const dates: string[] = [];
  for (const [start, end] of [[570, 690], [781, 900]]) {
    for (let minute = start; minute <= end; minute += 1) {
      const hour = String(Math.floor(minute / 60)).padStart(2, "0");
      const remainder = String(minute % 60).padStart(2, "0");
      dates.push(`${day} ${hour}:${remainder}:00`);
    }
  }
  return dates;
}

export function intradayCoordinate(stamp: string): string {
  return stamp.endsWith(" 13:00:00") ? `${stamp.slice(0, 10)} 11:30:00` : stamp;
}

export function intradayPointDates(bars: Bar[], dates: string[]): string[] {
  const opening = bars.find((bar) => bar.trade_date.endsWith(" 13:00:00"))?.trade_date;
  if (!opening) return dates;
  const boundary = dates.indexOf(intradayCoordinate(opening));
  return boundary < 0 ? dates : [...dates.slice(0, boundary + 1), opening, ...dates.slice(boundary + 1)];
}

export function alignIntradaySeries(series: Record<string, any>[], bars: Bar[], dates: string[]) {
  const indices = new Map(bars.map((bar, index) => [bar.trade_date, index]));
  return series.map((item) => ({ ...item, connectNulls: false,
    data: intradayPointDates(bars, dates).map((stamp) => {
      const index = indices.get(stamp);
      const point = index === undefined ? null : item.data[index] ?? null;
      const value = point !== null && typeof point === "object" ? point.value : point;
      return { ...(point !== null && typeof point === "object" ? point : {}),
        trade_date: stamp, value: [intradayCoordinate(stamp), value] };
    }),
  }));
}
