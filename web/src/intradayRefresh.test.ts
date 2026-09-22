import { afterEach, describe, expect, it, vi } from "vitest";
import { advanceSession, mergeIntradayData, refreshDelay, startIntradayRefresh } from "./intradayRefresh";
import type { ChartData, IntradayRefresh } from "./types";

function state(time = "10:00:00", phase: IntradayRefresh["phase"] = "trading", next = "11:30:00"): IntradayRefresh {
  return { server_time: `2026-09-16T${time}+08:00`, phase,
    next_transition_at: `2026-09-16T${next}+08:00`, market_status: "交易中",
    data_date: "2026-09-16", latest_data_at: "2026-09-16 10:00:00", last_success_at: null,
    result: "success", error: null, is_today: true };
}

class Visibility extends EventTarget {
  hidden = false;
  change(hidden: boolean) {
    this.hidden = hidden;
    this.dispatchEvent(new Event("visibilitychange"));
  }
}

afterEach(() => vi.useRealTimers());

describe("分时刷新调度", () => {
  it("周期K线在服务端给定的收口时刻补取", () => {
    expect(refreshDelay({ ...state("10:29:55"), next_bar_finalize_at: "2026-09-16T10:30:15+08:00" })).toBe(15_000);
    expect(refreshDelay({ ...state("10:30:05"), next_bar_finalize_at: "2026-09-16T10:30:15+08:00" })).toBe(10_000);
  });
  it("盘中15秒、边界补取、休市等待和日历未知60秒", () => {
    expect(refreshDelay(state())).toBe(15_000);
    expect(refreshDelay(state("09:20:00", "auction", "09:30:00"))).toBe(15_000);
    expect(refreshDelay(state("11:29:50"))).toBe(25_000);
    expect(refreshDelay(state("14:59:45", "trading", "15:00:00"))).toBe(30_000);
    expect(refreshDelay(state("11:30:15", "lunch", "13:00:00"))).toBe(5_385_000);
    expect(refreshDelay(state("10:00:00", "unknown"))).toBe(60_000);
  });

  it("立即获取，完成后每15秒刷新，请求未完成不重叠", async () => {
    vi.useFakeTimers();
    let resolve!: (value: IntradayRefresh) => void;
    const fetch = vi.fn((signal: AbortSignal) => new Promise<IntradayRefresh>((done) => { resolve = done; }));
    const stop = startIntradayRefresh({ fetch, visibility: new Visibility(), onError: vi.fn() });
    expect(fetch).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(fetch).toHaveBeenCalledTimes(1);
    resolve(state());
    await vi.advanceTimersByTimeAsync(14_999);
    expect(fetch).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(fetch).toHaveBeenCalledTimes(2);
    stop();
    expect(fetch.mock.calls[1][0].aborted).toBe(true);
  });

  it("午休补取一次后暂停，13点恢复", async () => {
    vi.useFakeTimers();
    const fetch = vi.fn().mockResolvedValueOnce(state("11:29:50"))
      .mockResolvedValueOnce(state("11:30:15", "lunch", "13:00:00"))
      .mockResolvedValue(state("13:00:00", "trading", "15:00:00"));
    const stop = startIntradayRefresh({ fetch, visibility: new Visibility(), onError: vi.fn() });
    await vi.advanceTimersByTimeAsync(24_999);
    expect(fetch).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(fetch).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(5_384_999);
    expect(fetch).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1);
    expect(fetch).toHaveBeenCalledTimes(3);
    stop();
  });

  it("收盘补取后等待下一交易日，非交易时段打开只获取一次", async () => {
    vi.useFakeTimers();
    const closed = { ...state("15:00:15", "closed"), next_transition_at: "2026-09-17T09:30:00+08:00" };
    const fetch = vi.fn().mockResolvedValueOnce(state("14:59:50", "trading", "15:00:00")).mockResolvedValue(closed);
    const stop = startIntradayRefresh({ fetch, visibility: new Visibility(), onError: vi.fn() });
    await vi.advanceTimersByTimeAsync(25_000);
    expect(fetch).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(12 * 3_600_000);
    expect(fetch).toHaveBeenCalledTimes(2);
    stop();
    const offHours = vi.fn().mockResolvedValue(closed);
    const stopOffHours = startIntradayRefresh({ fetch: offHours, visibility: new Visibility(), onError: vi.fn() });
    await vi.advanceTimersByTimeAsync(60_000);
    expect(offHours).toHaveBeenCalledTimes(1);
    stopOffHours();
  });

  it("页面隐藏取消请求，恢复立即获取，晚到响应不能重新启动旧调度", async () => {
    vi.useFakeTimers();
    const visibility = new Visibility();
    const resolvers: Array<(value: IntradayRefresh) => void> = [];
    const fetch = vi.fn((signal: AbortSignal) => new Promise<IntradayRefresh>((resolve) => { resolvers.push(resolve); }));
    const stop = startIntradayRefresh({ fetch, visibility, onError: vi.fn() });
    visibility.change(true);
    expect(fetch.mock.calls[0][0].aborted).toBe(true);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(fetch).toHaveBeenCalledTimes(1);
    visibility.change(false);
    expect(fetch).toHaveBeenCalledTimes(2);
    resolvers[0](state());
    await vi.advanceTimersByTimeAsync(60_000);
    expect(fetch).toHaveBeenCalledTimes(2);
    stop();
    resolvers[1](state());
    await vi.advanceTimersByTimeAsync(60_000);
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("盘中失败重试，休市补取失败不会持续轮询", async () => {
    vi.useFakeTimers();
    const onError = vi.fn();
    const fetch = vi.fn().mockResolvedValueOnce(state("11:29:50")).mockRejectedValue(new Error("offline"));
    const stop = startIntradayRefresh({ fetch, visibility: new Visibility(), onError });
    await vi.advanceTimersByTimeAsync(25_000);
    expect(onError).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(fetch).toHaveBeenCalledTimes(2);
    stop();
    const failed = vi.fn().mockRejectedValue(new Error("offline"));
    const stopFailed = startIntradayRefresh({ fetch: failed, initial: state(), visibility: new Visibility(), onError });
    await vi.advanceTimersByTimeAsync(30_000);
    expect(failed).toHaveBeenCalledTimes(3);
    stopFailed();
  });

  it("日历未知60秒重试，停止后不再请求", async () => {
    vi.useFakeTimers();
    const fetch = vi.fn().mockResolvedValue(state("10:00:00", "unknown"));
    const stop = startIntradayRefresh({ fetch, visibility: new Visibility(), onError: vi.fn() });
    await vi.advanceTimersByTimeAsync(59_999);
    expect(fetch).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(fetch).toHaveBeenCalledTimes(2);
    stop();
    await vi.advanceTimersByTimeAsync(120_000);
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("请求跨越午休边界返回时仍在11:30:15完成补取", async () => {
    vi.useFakeTimers();
    const fetch = vi.fn().mockResolvedValueOnce(state("11:29:44"))
      .mockResolvedValueOnce(state("11:30:05", "lunch", "13:00:00"))
      .mockResolvedValue(state("11:30:15", "lunch", "13:00:00"));
    const stop = startIntradayRefresh({ fetch, visibility: new Visibility(), onError: vi.fn() });
    await vi.advanceTimersByTimeAsync(15_000);
    expect(fetch).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(fetch).toHaveBeenCalledTimes(3);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(fetch).toHaveBeenCalledTimes(3);
    stop();
  });

  it("开盘请求失败仍进入盘中重试状态", () => {
    const advanced = advanceSession(state("12:59:50", "lunch", "13:00:00"), 15_000);
    expect(advanced.phase).toBe("trading");
    expect(advanced.next_transition_at).toBe("2026-09-16T15:00:00+08:00");
  });
});

describe("静默行情合并", () => {
  const data = (symbol: string, close: number): ChartData => ({
    symbol, timeframe: "1", adjustflag: "2", bars: [{ trade_date: "2026-09-16 10:00:00", open: 10, high: 12, low: 9, close, volume: 10, amount: 0 }],
    pens: [], centers: [], center_revisions: [], movements: [], movement_revisions: [], components: [], points: [], point_revisions: [], promotion_candidates: [], promotion_candidate_revisions: [], relations: [], issues: [], levels: [], unassigned_by_level: {}, center_levels: [], movement_levels: [],
    drawings: [], drawings_version: "", indicators: { macd: [] }, has_more: false, available: true,
    definition_version: "test", calculator_fingerprint: "test", structure_version: "", active_structure_level: 1, max_available_center_level: 0, intraday_refresh: state(),
  });

  it("只更新行情，不替换本地绘图", () => {
    const current = { ...data("000001", 10), drawings_version: "local-draft" };
    const fresh = { ...data("000001", 11), drawings_version: "server" };
    const merged = mergeIntradayData(current, fresh);
    expect(merged.bars[0].close).toBe(11);
    expect(merged.drawings).toBe(current.drawings);
    expect(merged.drawings_version).toBe("local-draft");
  });

  it("合并5分钟实时结构和形成中K线，同时保留本地绘图", () => {
    const current = { ...data("000001", 10), timeframe: "5", drawings: [], drawings_version: "local-draft" };
    const fresh = { ...data("000001", 11), timeframe: "5", forming_bar: {
      trade_date: "2026-09-16 10:05:00", is_forming: true, status: "provisional" as const,
    }, structure_preview: true, structure_version: "preview-1", drawings_version: "server" };
    const merged = mergeIntradayData(current, fresh);
    expect(merged.forming_bar?.is_forming).toBe(true);
    expect(merged.structure_preview).toBe(true);
    expect(merged.structure_version).toBe("preview-1");
    expect(merged.drawings).toBe(current.drawings);
    expect(merged.drawings_version).toBe("local-draft");
  });

  it("空结果保留旧曲线，跨日响应整体替换不混合日期", () => {
    const current = data("000001", 10);
    expect(mergeIntradayData(current, { ...current, bars: [] }).bars).toBe(current.bars);
    const fresh = data("000001", 11);
    fresh.bars[0].trade_date = "2026-09-17 09:30:00";
    expect(mergeIntradayData(current, fresh).bars).toEqual(fresh.bars);
  });
});
