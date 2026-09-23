import { describe, expect, it } from "vitest";
import { centersForStructureLevel, formatCenterTooltip, formatCompactNumber, formatKlineTooltip, formatPrice, formatTradeDate, formatVolume, normalizeChartData, watchlistStocksForGroup, watchlistUngroupedStocks } from "./App";
import type { Center } from "./types";

const center = (overrides: Partial<Center> = {}): Center => ({
  id: "c1", family_id: "cf1", revision_no: 1, ordinal: 0, level: 1, kind: "center",
  status: "confirmed", start_date: "2026-01-01", end_date: "2026-01-03", zd: 10, zg: 11,
  fixed_zd: 10, fixed_zg: 11, dd: 8, gg: 13, fluctuation_dd: 8, fluctuation_gg: 13,
  entry_unit_ids: [], core_unit_ids: [], extension_unit_ids: [], peripheral_unit_ids: [],
  departure_unit_ids: [], retest_unit_ids: [], owned_unit_ids: [], context_unit_ids: [],
  z_unit_ids: [], connection_component_ids: [], overlap_witness_unit_ids: [], missing_evidence: [],
  child_center_ids: [], formation_modes: ["strict_three_unit_core"],
  ...overrides,
});

describe("行情数值格式", () => {
  it("交易日期显示中文星期", () => {
    expect(formatTradeDate("2026-07-29")).toBe("2026-07-29 周三");
  });
  it("价格固定保留两位小数", () => {
    expect(formatPrice(10)).toBe("10.00");
    expect(formatPrice(10.126)).toBe("10.13");
    expect(formatPrice(undefined)).toBe("--");
    expect(formatPrice(Number.NaN)).toBe("--");
  });

  it("按原值、万和亿切换大数单位", () => {
    expect(formatCompactNumber(0)).toBe("0");
    expect(formatCompactNumber(9_999)).toBe("9,999");
    expect(formatCompactNumber(10_000)).toBe("1万");
    expect(formatCompactNumber(99_999_999)).toBe("10000万");
    expect(formatCompactNumber(100_000_000)).toBe("1.00亿");
    expect(formatCompactNumber(5_012_000_000)).toBe("50.12亿");
    expect(formatCompactNumber(-25_000)).toBe("-2万");
    expect(formatCompactNumber(undefined)).toBe("--");
  });
});

describe("K线 tooltip", () => {
  it("只输出中文K线核心字段", () => {
    const html = formatKlineTooltip({
      trade_date: "2026-09-10 10:00:00",
      open: 10.1,
      close: 10.25,
      low: 10,
      high: 10.3,
      volume: 123456,
      amount: 5_012_000_000,
    }, 10);
    expect(html).toContain("开盘：<b class=\"rise\">10.10</b>");
    expect(html).toContain("收盘：<b class=\"rise\">10.25</b>");
    expect(html).toContain("最低：<b class=\"rise\">10.00</b>");
    expect(html).toContain("最高：<b class=\"rise\">10.30</b>");
    expect(html).toContain("涨跌幅：<b class=\"rise\">+2.50%</b>");
    expect(html).toContain('class="rise"');
    expect(html).toContain("成交量：12万手");
    expect(html).toContain("成交额：50.12亿");
    expect(html).toContain("2026-09-10 周四");
    expect(html).not.toContain("MACD");
    expect(html).not.toContain("DIF");
    expect(html).not.toContain("DEA");
  });

  it("缺少前一根收盘价时涨跌幅显示占位符", () => {
    const html = formatKlineTooltip({
      trade_date: "2026-09-10 10:00:00",
      open: 10,
      close: 10.25,
      low: 9.9,
      high: 10.3,
      volume: 1,
      amount: 1,
    });
    expect(html).toContain("涨跌幅：<b>--</b>");
    expect(html).not.toContain('class="rise"');
    expect(html).not.toContain('class="fall"');
  });

  it("下跌时涨跌幅带负号", () => {
    const html = formatKlineTooltip({
      trade_date: "2026-09-10 10:00:00",
      open: 10,
      close: 9.5,
      low: 9.4,
      high: 10.1,
      volume: 1,
      amount: 1,
    }, 10);
    expect(html).toContain("涨跌幅：<b class=\"fall\">-5.00%</b>");
    expect(html).toContain('class="fall"');
  });

  it("平盘时不使用涨跌颜色", () => {
    const html = formatKlineTooltip({
      trade_date: "2026-09-10 10:00:00",
      open: 10,
      close: 10,
      low: 9.9,
      high: 10.1,
      volume: 1,
      amount: 1,
    }, 10);
    expect(html).toContain("涨跌幅：<b>+0.00%</b>");
    expect(html).not.toContain('class="rise"');
    expect(html).not.toContain('class="fall"');
  });

  it("成交量统一追加手单位", () => {
    expect(formatVolume(123456)).toBe("12万手");
    expect(formatVolume(undefined)).toBe("--");
  });
});

describe("中枢级别显示", () => {
  const centers = [
    center({ id:"l1", family_id:"f1" }),
    center({ id:"l2", family_id:"f2", ordinal:1, level:2, start_date:"2026-01-04", end_date:"2026-01-08", zd:10.5, fixed_zd:10.5, zg:11.5, fixed_zg:11.5 }),
  ];

  it("只返回与 active level 严格相等的中枢", () => {
    expect(centersForStructureLevel(centers, 1).map((center) => center.id)).toEqual(["l1"]);
    expect(centersForStructureLevel(centers, 2).map((center) => center.id)).toEqual(["l2"]);
  });

  it("tooltip 展示当前周期内部级别和独立计算来源", () => {
    const html = formatCenterTooltip(center({
      id:"l2", family_id:"f2", ordinal:1, level:2, start_date:"2026-01-04", end_date:"2026-01-08",
      zd:10.5, fixed_zd:10.5, zg:11.5, fixed_zg:11.5, confirmed_at:"2026-01-09",
    }), "d");
    expect(html).toContain("L2 中枢");
    expect(html).toContain("配色参考（非结构周期）：日线");
    expect(html).toContain("计算来源：日线独立结构");
    expect(html).toContain("语义对应：日线内部 L2 中枢（非跨周期递归）");
    expect(html).toContain("颜色标识：period-d-level-L2");
    expect(html).toContain("实际边界：2026-01-04 → 2026-01-08");
  });
});

describe("图表数据归一化", () => {
  it("过滤非法 K 线并补齐可选结构数组", () => {
    const result = normalizeChartData({ bars: [
      { trade_date: "2026-01-02", open: 1, high: 2, low: 0.5, close: 1.5, volume: 1, amount: 2 },
      { trade_date: "bad", open: Number.NaN, high: 2, low: 1, close: 1.5, volume: 1, amount: 2 },
    ], pens: undefined as any, centers: [], movements: undefined as any, indicators: {} as any } as any);
    expect(result?.bars).toHaveLength(1);
    expect(result?.pens).toEqual([]);
    expect("movements" in (result || {})).toBe(false);
    expect(result?.indicators.macd).toEqual([]);
  });
});

describe("自选分组", () => {
  const stocks = [
    {symbol:"300308", name:"中际旭创"},
    {symbol:"688041", name:"海光信息"},
    {symbol:"000001", name:"平安银行"},
  ];
  const memberships = [
    {group_id:1, symbol:"300308", sort_order:1},
    {group_id:1, symbol:"688041", sort_order:0},
    {group_id:2, symbol:"300308", sort_order:0},
  ];

  it("同一股票可以属于多个分组且组内按独立顺序展示", () => {
    expect(watchlistStocksForGroup(stocks, memberships, 1).map((item) => item.symbol)).toEqual(["688041", "300308"]);
    expect(watchlistStocksForGroup(stocks, memberships, 2).map((item) => item.symbol)).toEqual(["300308"]);
  });

  it("未分组视图只保留没有任何分组关系的股票", () => {
    expect(watchlistUngroupedStocks(stocks, memberships).map((item) => item.symbol)).toEqual(["000001"]);
  });
});
