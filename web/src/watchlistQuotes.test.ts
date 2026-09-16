import { afterEach, describe, expect, it, vi } from "vitest";
import { mergeWatchlistQuotes, startWatchlistQuoteRefresh, watchlistQuoteDelay, watchlistQuoteLabel } from "./watchlistQuotes";
import type { WatchlistQuote, WatchlistQuoteResponse } from "./watchlistQuotes";

class Visibility extends EventTarget {
  hidden = false;
  setHidden(hidden: boolean) { this.hidden = hidden; this.dispatchEvent(new Event("visibilitychange")); }
}

const quote = (symbol = "000001", latest = 11): WatchlistQuote => ({
  symbol, latest, previous_close:10, change:latest - 10, change_pct:(latest - 10) * 10,
  quote_time:"2026-09-16T10:00:00+08:00", source:"tencent", status:"success", error:null,
});
const response = (interval = 10_000): WatchlistQuoteResponse => ({
  quotes:[quote()], refresh_after_ms:interval, server_time:"2026-09-16T10:00:00+08:00",
  phase:interval === 10_000 ? "trading" : "closed", market_status:"交易中",
});

afterEach(() => { vi.useRealTimers(); });

describe("自选行情独立轮询", () => {
  it("只依据后端交易日历调度，不依赖浏览器时区或星期", () => {
    expect(watchlistQuoteDelay(response())).toBe(10_000);
    expect(watchlistQuoteDelay(response(60_000))).toBe(60_000);
    expect(watchlistQuoteDelay()).toBe(60_000);
    expect(watchlistQuoteDelay(response(-1))).toBe(60_000);
  });

  it("开页立即请求，交易十秒、午休及节假日六十秒刷新", async () => {
    vi.useFakeTimers();
    const fetch = vi.fn().mockResolvedValueOnce(response()).mockResolvedValue(response(60_000));
    const onQuotes = vi.fn();
    const stop = startWatchlistQuoteRefresh({fetch, onQuotes, onError:vi.fn(), visibility:new Visibility()});
    await vi.advanceTimersByTimeAsync(9_999);
    expect(fetch).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(fetch).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(59_999);
    expect(fetch).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1);
    expect(fetch).toHaveBeenCalledTimes(3);
    expect(onQuotes).toHaveBeenCalledTimes(3);
    stop();
    await vi.advanceTimersByTimeAsync(120_000);
    expect(fetch).toHaveBeenCalledTimes(3);
  });

  it("请求未完成时不重叠，隐藏后旧响应不更新，可见时补刷", async () => {
    vi.useFakeTimers();
    const visibility = new Visibility();
    let resolve!: (value: WatchlistQuoteResponse) => void;
    const fetch = vi.fn().mockImplementationOnce(() => new Promise<WatchlistQuoteResponse>((done) => { resolve = done; }))
      .mockResolvedValue(response());
    const onQuotes = vi.fn();
    const stop = startWatchlistQuoteRefresh({fetch, onQuotes, onError:vi.fn(), visibility});
    await vi.advanceTimersByTimeAsync(30_000);
    expect(fetch).toHaveBeenCalledTimes(1);
    visibility.setHidden(true);
    expect(fetch.mock.calls[0][0].aborted).toBe(true);
    visibility.setHidden(false);
    expect(fetch).toHaveBeenCalledTimes(1);
    resolve(response());
    await vi.advanceTimersByTimeAsync(1);
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(onQuotes).toHaveBeenCalledTimes(1);
    visibility.setHidden(true);
    await vi.advanceTimersByTimeAsync(120_000);
    expect(fetch).toHaveBeenCalledTimes(2);
    visibility.setHidden(false);
    await vi.advanceTimersByTimeAsync(0);
    expect(fetch).toHaveBeenCalledTimes(3);
    stop();
  });

  it("隐藏页不发起请求，切换股票或分组销毁后忽略晚到响应", async () => {
    vi.useFakeTimers();
    const visibility = new Visibility();
    visibility.hidden = true;
    let resolve!: (value: WatchlistQuoteResponse) => void;
    const fetch = vi.fn(() => new Promise<WatchlistQuoteResponse>((done) => { resolve = done; }));
    const onQuotes = vi.fn();
    const stop = startWatchlistQuoteRefresh({fetch, onQuotes, onError:vi.fn(), visibility});
    expect(fetch).not.toHaveBeenCalled();
    visibility.setHidden(false);
    stop();
    resolve(response());
    await vi.advanceTimersByTimeAsync(120_000);
    expect(onQuotes).not.toHaveBeenCalled();
    expect(fetch).toHaveBeenCalledTimes(1);
    visibility.setHidden(false);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("网络失败通知 UI 保留报价，降频重试后恢复", async () => {
    vi.useFakeTimers();
    const fetch = vi.fn().mockRejectedValueOnce(new Error("offline")).mockResolvedValue(response());
    const onError = vi.fn();
    const onQuotes = vi.fn();
    const stop = startWatchlistQuoteRefresh({fetch, onQuotes, onError, visibility:new Visibility()});
    await vi.advanceTimersByTimeAsync(59_999);
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onQuotes).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(onQuotes).toHaveBeenCalledTimes(1);
    stop();
  });
});

describe("报价合并", () => {
  it("同一证券只保留一份，过滤删除的股票，不回退旧时间", () => {
    const original = {"000001":quote(), "600000":quote("600000")};
    const newer = {...quote(), latest:12, quote_time:"2026-09-16T10:01:00+08:00"};
    const fresh = mergeWatchlistQuotes(original, [newer], ["000001", "000001"]);
    expect(Object.keys(fresh)).toEqual(["000001"]);
    expect(fresh["000001"].latest).toBe(12);
    expect(original["000001"].latest).toBe(11);
    const stale = mergeWatchlistQuotes(fresh, [quote()], ["000001"]);
    expect(stale["000001"].latest).toBe(12);
    expect(stale["000001"].status).toBe("error");
  });

  it("部分缺失及失败保留价格，无历史报价仍为 null，不读日线", () => {
    const failed = {...quote(), latest:null, quote_time:null, status:"error" as const};
    expect(mergeWatchlistQuotes({"000001":quote()}, [failed], ["000001"])["000001"].latest).toBe(11);
    expect(mergeWatchlistQuotes({}, [failed], ["000001"])["000001"].latest).toBeNull();
    expect(mergeWatchlistQuotes({"000001":quote()}, [], ["000001"])["000001"].status).toBe("error");
    expect(watchlistQuoteLabel()).toBe("等待行情");
    expect(watchlistQuoteLabel({...quote(), status:"historical"})).toBe("历史行情");
    expect(watchlistQuoteLabel({...quote(), status:"stale"})).toBe("数据延迟");
    expect(watchlistQuoteLabel(quote())).toBe("10:00:00");
  });
});
