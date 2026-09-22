import { describe, expect, it, vi } from "vitest";
import { chartDates, intradayCoordinate, intradayPointDates } from "./intradayTimeline";
import { buildChartOption } from "./chartBuilders";
import type { ChartData } from "./types";

const makeBar = (clock: string, close = 10) => ({
  trade_date: `2026-09-16 ${clock}:00`, open: close, high: close + 1,
  low: close - 1, close, volume: 100, amount: 1000,
});
const dataFor = (clocks: string[]): ChartData => {
  const bars = clocks.map((clock, index) => makeBar(clock, 10 + index));
  return { symbol: "000001", timeframe: "1", adjustflag: "2", bars, pens: [], centers: [], center_revisions: [], movements: [], movement_revisions: [], components: [], points: [], point_revisions: [], promotion_candidates: [], promotion_candidate_revisions: [], relations: [], issues: [], levels: [], unassigned_by_level: {}, center_levels: [], movement_levels: [], drawings: [], drawings_version: "",
    indicators: { macd: bars.map((bar) => ({ trade_date: bar.trade_date, dif: 1, dea: 2, histogram: 3 })) },
    has_more: false, available: true, definition_version: "test", calculator_fingerprint: "test", structure_version: "", active_structure_level: 1, max_available_center_level: 0 };
};

describe("完整交易日分时时间轴", () => {
  it.each([["09:30"], ["09:31", "11:30"], ["09:30", "13:05"], ["09:30", "15:00"]])(
    "已有数据为 %j 时仍显示完整全天", (...clocks) => {
      const data = dataFor(clocks);
      const option = buildChartOption({ data, subplotVisible: [true, true, true, true],
        subplotIndicators: ["volume", "macd", "amount", "volume"], zoomStart: 60, zoomEnd: 80 });
      expect(option).not.toBeNull();
      const dates = option!.xAxis[0].data;
      expect(dates).toHaveLength(241);
      expect(dates[0]).toBe("2026-09-16 09:30:00");
      expect(dates.at(-1)).toBe("2026-09-16 15:00:00");
      expect(dates[120]).toBe("2026-09-16 11:30:00");
      expect(dates[121]).toBe("2026-09-16 13:01:00");
      expect(dates.some((stamp: string) => stamp.slice(11, 13) === "12")).toBe(false);
      expect(option!.xAxis).toHaveLength(5);
      for (const axis of option!.xAxis) expect(axis.data).toEqual(dates);
      for (const series of option!.series) expect(series.data).toHaveLength(dates.length);
      expect(option!.dataZoom[0]).toMatchObject({ start: 0, end: 100, disabled: true,
        moveOnMouseMove: false, zoomOnMouseWheel: false });
      const formatter = option!.xAxis[0].axisLabel.formatter;
      expect(formatter(dates[0])).toBe("09:30");
      expect(formatter(dates[120])).toBe("11:30/13:00");
      expect(formatter(dates.at(-1))).toBe("15:00");
    },
  );

  it("缺失分钟和未来时段均为空，不将午后数据挪到上午", () => {
    const data = dataFor(["09:30", "09:32", "13:05"]);
    const original = JSON.stringify(data);
    const option = buildChartOption({ data, subplotVisible: [true, true, true],
      subplotIndicators: ["volume", "macd", "amount"] })!;
    const dates = option.xAxis[0].data;
    for (const series of option.series) {
      expect(series.data[1].value[1]).toBeNull();
      expect(series.data[dates.indexOf("2026-09-16 13:06:00")].value[1]).toBeNull();
      expect(series.data.at(-1).value[1]).toBeNull();
      expect(series.connectNulls).toBe(false);
    }
    const price = option.series.find((series: any) => series.id === "intraday-price");
    expect(price.data[2].value[1]).toBe(11);
    expect(price.data[dates.indexOf("2026-09-16 13:05:00")].value[1]).toBe(12);
    expect(JSON.stringify(data)).toBe(original);
  });

  it("tooltip 根据时间查找数据，未来留白不冒充最后价格", () => {
    const data = dataFor(["09:30", "13:05"]);
    const formatKlineTooltip = vi.fn(() => "correct");
    const option = buildChartOption({ data, formatKlineTooltip })!;
    const dates = option.xAxis[0].data;
    const formatter = option.tooltip.formatter;
    expect(formatter([{ seriesName: "分时", axisIndex: 0, dataIndex: dates.indexOf("2026-09-16 13:05:00") }])).toBe("correct");
    expect(formatKlineTooltip).toHaveBeenCalledWith(data.bars[1], undefined);
    expect(formatter([{ seriesName: "分时", axisIndex: 0, dataIndex: 240, value: null }])).toBe("");
    expect(formatter([{ seriesName: "成交量", dataIndex: 240, value: null }])).toBe("");
    expect(formatKlineTooltip).toHaveBeenCalledTimes(1);
  });

  it("收盘数据完整对齐，次日使用新日期，其他K线周期不变", () => {
    const dates = chartDates("1", [makeBar("09:30")]);
    const data = dataFor(dates.map((stamp) => stamp.slice(11, 16)));
    const option = buildChartOption({ data })!;
    const price = option.series.find((series: any) => series.id === "intraday-price");
    expect(price.data.every((point: any) => point.value[1] !== null)).toBe(true);
    expect(chartDates("1", [{ ...makeBar("09:30"), trade_date: "2026-09-17 09:30:00" }])[0]).toBe("2026-09-17 09:30:00");
    const daily = { ...dataFor(["09:30", "13:05"]), timeframe: "d" };
    expect(chartDates("d", daily.bars)).toEqual(daily.bars.map((bar) => bar.trade_date));
    expect(buildChartOption({ data: daily, zoomStart: 30, zoomEnd: 80 })!.dataZoom[0]).toMatchObject({ start: 30, end: 80, disabled: false });
  });

  it("11:30与13:01直接相接，午休没有空数据位置", () => {
    const data = dataFor(["11:29", "11:30", "13:01", "13:02"]);
    const option = buildChartOption({ data, subplotVisible: [true, true], subplotIndicators: ["volume", "macd"] })!;
    expect(option.xAxis[0].data[120]).toBe("2026-09-16 11:30:00");
    expect(option.xAxis[0].data[121]).toBe("2026-09-16 13:01:00");
    for (const series of option.series) {
      expect(series.data.slice(119, 123).every((point: any) => point.value[1] !== null)).toBe(true);
      expect(series.connectNulls).toBe(false);
    }
    const price = option.series.find((series: any) => series.id === "intraday-price");
    expect(price.data[120].value).toEqual(["2026-09-16 11:30:00", 11]);
    expect(price.data[121].value).toEqual(["2026-09-16 13:01:00", 12]);
  });

  it("真实13:00数据共享午间坐标，不丢弃价格或成交量，tooltip保留原始时间", () => {
    const data = dataFor(["11:30", "13:00", "13:01"]);
    const formatKlineTooltip = vi.fn((bar) => bar.trade_date);
    const option = buildChartOption({ data, subplotVisible: [true], subplotIndicators: ["volume"], formatKlineTooltip })!;
    const dates = option.xAxis[0].data;
    expect(dates).toHaveLength(241);
    expect(intradayCoordinate(data.bars[1].trade_date)).toBe(dates[120]);
    expect(intradayPointDates(data.bars, dates).slice(120, 123)).toEqual(data.bars.map((bar) => bar.trade_date));
    const price = option.series.find((series: any) => series.id === "intraday-price");
    expect(price.data.slice(120, 123).map((point: any) => point.value)).toEqual([
      [dates[120], 10], [dates[120], 11], [dates[121], 12],
    ]);
    const volume = option.series.find((series: any) => series.name === "成交量");
    expect(volume.data.reduce((sum: number, point: any) => sum + (point.value[1] || 0), 0)).toBe(300);
    expect(option.tooltip.formatter([{ seriesName: "分时", axisIndex: 0, dataIndex: 121 }])).toBe("2026-09-16 13:00:00");
    expect(option.tooltip.formatter([{ seriesName: "分时", axisIndex: 0, dataIndex: 122 }])).toBe("2026-09-16 13:01:00");
    expect(price.data[123].value[1]).toBeNull();
  });

  it("没有11:30而存在13:00时仍能显示午后开盘点", () => {
    const data = dataFor(["13:00", "13:01"]);
    const option = buildChartOption({ data })!;
    const price = option.series.find((series: any) => series.id === "intraday-price");
    expect(price.data[120].value[1]).toBeNull();
    expect(price.data[121].value).toEqual(["2026-09-16 11:30:00", 10]);
    expect(price.data[122].value).toEqual(["2026-09-16 13:01:00", 11]);
  });
});
