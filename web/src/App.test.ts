import { describe, expect, it } from "vitest";
import { centersForStructureLevel, formatCenterTooltip, formatCompactNumber, formatKlineTooltip, formatMovementTooltip, formatPrice, formatTradeDate, formatVolume, movementEndpointMarkers, movementLineEndpoints, movementVisualStyle, normalizeChartData, watchlistStocksForGroup, watchlistUngroupedStocks } from "./App";

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

describe("走势 tooltip", () => {
  it("区分实际边界与确认时间", () => {
    const html = formatMovementTooltip({
      id:"m1", ordinal:0, kind:"movement", direction:"up", classification:"trend", status:"confirmed",
      start_date:"2026-01-01", end_date:"2026-02-01", start_price:10, end_price:20,
      center_count:2, confirmed_at:"2026-02-10",
    });
    expect(html).toContain("结束原因：尚未确认");
    expect(html).toContain("趋势 · 向上");
    expect(html).toContain("实际边界：2026-01-01 → 2026-02-01");
    expect(html).toContain("确认时间：2026-02-10");
  });

  it("展示正式层级走势来源和级别语义", () => {
    const html = formatMovementTooltip({
      id:"m1", ordinal:0, level:1, role:"hierarchy_component", kind:"movement", direction:"up", classification:"trend", status:"confirmed",
      start_date:"2026-01-01", end_date:"2026-02-01", start_price:10, end_price:20,
      center_count:2, confirmed_at:"2026-02-10",
    }, "d");
    expect(html).toContain("结构级别：L1");
    expect(html).toContain("计算来源：日线独立结构");
    expect(html).toContain("语义对应：日线内部 L1 走势（反向独立中枢确认结束）");
    expect(html).not.toContain("简化的周线一笔");
  });
});

describe("中枢级别显示", () => {
  const centers = [
    { id:"l1", ordinal:0, level:1, kind:"center", status:"confirmed", start_date:"2026-01-01", end_date:"2026-01-03", zd:10, zg:11 },
    { id:"l2", ordinal:1, level:2, kind:"center", status:"confirmed", start_date:"2026-01-04", end_date:"2026-01-08", zd:10.5, zg:11.5 },
    { id:"legacy", ordinal:2, kind:"center", status:"confirmed", start_date:"2026-01-09", end_date:"2026-01-12", zd:10, zg:12 },
  ];

  it("只返回与 active level 严格相等的中枢", () => {
    expect(centersForStructureLevel(centers, 1).map((center) => center.id)).toEqual(["l1"]);
    expect(centersForStructureLevel(centers, 2).map((center) => center.id)).toEqual(["l2"]);
  });

  it("不把未知角色中枢当作层级中枢绘制", () => {
    expect(centersForStructureLevel([
      ...centers,
      { id:"evidence", ordinal:3, level:1, role:"legacy", kind:"center", status:"confirmed", start_date:"2026-01-13", end_date:"2026-01-14", zd:10, zg:11 },
    ] as any, 1).map((center) => center.id)).toEqual(["l1"]);
  });

  it("tooltip 展示后端提供的显示周期、颜色标识和独立计算来源", () => {
    const html = formatCenterTooltip({
      id:"l2", ordinal:1, level:2, kind:"center", status:"confirmed",
      display_period:"w", color_key:"period-w",
      start_date:"2026-01-04", end_date:"2026-01-08", zd:10.5, zg:11.5,
      confirmed_at:"2026-01-09",
    }, "d");
    expect(html).toContain("L2 中枢");
    expect(html).toContain("配色参考（非结构周期）：周线");
    expect(html).toContain("计算来源：日线独立结构");
    expect(html).toContain("语义对应：日线内部 L2 中枢（非跨周期递归）");
    expect(html).toContain("颜色标识：period-w");
    expect(html).toContain("实际边界：2026-01-04 → 2026-01-08");
  });
});

describe("走势端点", () => {
  const movements = [
    { id:"m1", ordinal:0, level:1, direction:"up", start_date:"2026-01-01", end_date:"2026-01-03", start_price:10, end_price:15, status:"confirmed" },
    { id:"m2", ordinal:1, level:1, direction:"down", start_date:"2026-01-03", end_date:"2026-01-05", start_price:15, end_price:9, status:"confirmed" },
    { id:"m3", ordinal:2, level:1, direction:"up", start_date:"2026-01-05", end_date:"2026-01-06", start_price:9, end_price:12, status:"provisional" },
  ] as any;

  it("共享端点按日期和价格去重并稳定编号高低点", () => {
    const markers = movementEndpointMarkers(movements);
    expect(markers).toHaveLength(4);
    expect(markers.filter((marker) => marker.kind === "high").map((marker) => marker.label)).toEqual(["H1", "H2"]);
    expect(markers.filter((marker) => marker.kind === "low").map((marker) => marker.label)).toEqual(["L1", "L2"]);
    expect(markers.find((marker) => marker.key === "2026-01-03|15")?.label).toBe("H1");
  });

  it("忽略内部 path_points，仅用真实起止点生成直线", () => {
    const movement = {
      ...movements[0],
      path_points: [
        { trade_date:"2026-01-01", price:10 },
        { trade_date:"2026-01-02", price:12 },
        { trade_date:"2026-01-03", price:15 },
      ],
    };
    expect(movementLineEndpoints(movement)).toEqual([
      { value:["2026-01-01", 10], movementId:"m1" },
      { value:["2026-01-03", 15], movementId:"m1" },
    ]);
  });

  it("按级别着色，并让 provisional 使用虚线", () => {
    expect(movementVisualStyle("dark", movements[0], "d")).toEqual({ color:"#F2C14E", lineType:"solid" });
    expect(movementVisualStyle("light", movements[1], "d")).toEqual({ color:"#A87800", lineType:"solid" });
    expect(movementVisualStyle("dark", movements[2], "d")).toEqual({ color:"#F2C14E", lineType:"dashed" });
  });
});

describe("图表数据归一化", () => {
  it("过滤非法 K 线并补齐可选结构数组", () => {
    const result = normalizeChartData({ bars: [
      { trade_date: "2026-01-02", open: 1, high: 2, low: 0.5, close: 1.5, volume: 1, amount: 2 },
      { trade_date: "bad", open: Number.NaN, high: 2, low: 1, close: 1.5, volume: 1, amount: 2 },
    ], pens: undefined as any, pen_centers: [], movements: undefined as any, indicators: {} as any } as any);
    expect(result?.bars).toHaveLength(1);
    expect(result?.pens).toEqual([]);
    expect(result?.movements).toEqual([]);
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
