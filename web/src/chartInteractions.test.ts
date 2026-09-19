import { describe, expect, it, vi } from "vitest";
import { bindChartGestures, chartZoomFromDataZoomEvent, ChartGesture, gestureOwner, initialChartZoom, rebaseChartZoom } from "./chartInteractions";
import type { GestureOwner } from "./chartInteractions";

describe("K线浏览范围", () => {
  it.each([0, 1, 60, 120])("%i 根时显示全部", (count) => {
    expect(initialChartZoom(count, false, null)).toEqual({ start: 0, end: 100 });
  });
  it.each([121, 300, 1000])("%i 根时准确显示最近120根", (count) => {
    const zoom = initialChartZoom(count, false, null);
    expect(Math.round(zoom.start / 100 * (count - 1))).toBe(count - 120);
    expect(zoom.end).toBe(100);
  });
  it("分时不采用120根默认窗口", () => {
    expect(initialChartZoom(240, true, null)).toEqual({ start: 0, end: 100 });
    expect(initialChartZoom(20, true, '{"start":60,"end":80}')).toEqual({ start: 0, end: 100 });
  });
  it.each([{ start: 0, end: 100 }, { start: 25, end: 80 }])("恢复有效偏好 %j", (saved) => {
    expect(initialChartZoom(300, false, JSON.stringify(saved))).toEqual(saved);
  });
  it.each(["broken", "null", "{}", '{"start":80,"end":20}', '{"start":-1,"end":100}', '{"start":0,"end":101}', '{"start":0,"end":0}', '{"start":"1","end":100}'])("损坏存储回退 %s", (saved) => {
    expect(initialChartZoom(300, false, saved)).toEqual(initialChartZoom(300, false, null));
  });
  it("补入历史且不在左边缘时保持可见日期", () => {
    const dates = Array.from({ length: 600 }, (_, index) => String(index));
    const previous = dates.slice(300);
    const zoom = { start: 20, end: 60 };
    const rebased = rebaseChartZoom(zoom, previous, dates);
    for (const edge of ["start", "end"] as const) {
      expect(dates[Math.round(rebased[edge] / 100 * 599)]).toBe(previous[Math.round(zoom[edge] / 100 * 299)]);
    }
  });
  it("左边缘补入历史后展示新增的更早数据", () => {
    const dates = Array.from({ length: 600 }, (_, index) => String(index));
    const previous = dates.slice(300);
    const rebased = rebaseChartZoom({ start: 12, end: 52 }, previous, dates);
    expect(rebased.start).toBe(0);
    expect(Math.round(rebased.end / 100 * 599)).toBe(120);
  });
  it("左边缘显示整页时补入历史后切换到上一页", () => {
    const dates = Array.from({ length: 600 }, (_, index) => String(index));
    const previous = dates.slice(300);
    const rebased = rebaseChartZoom({ start: 0, end: 100 }, previous, dates);
    expect(rebased.start).toBe(0);
    expect(Math.round(rebased.end / 100 * 599)).toBe(299);
  });
  it("无补入或找不到原日期时不改视野", () => {
    const zoom = { start: 30, end: 90 };
    expect(rebaseChartZoom(zoom, [], ["a"])).toEqual(zoom);
    expect(rebaseChartZoom(zoom, ["b", "c"], ["b", "c", "d"])).toEqual(zoom);
    expect(rebaseChartZoom(zoom, ["b", "c"], ["a", "d", "e"])).toEqual(zoom);
  });
  it("兼容百分比格式的缩放事件", () => {
    expect(chartZoomFromDataZoomEvent({ batch: [{ start: 10, end: 50 }] }, ["a", "b", "c"])).toEqual({ start: 10, end: 50 });
  });
  it("兼容索引和值格式的缩放事件", () => {
    const dates = ["a", "b", "c", "d", "e"];
    expect(chartZoomFromDataZoomEvent({ startValue: 0, endValue: 2 }, dates)).toEqual({ start: 0, end: 50 });
    expect(chartZoomFromDataZoomEvent({ batch: [{ startValue: "b", endValue: "e" }] }, dates)).toEqual({ start: 25, end: 100 });
  });
  it("忽略不完整或倒序的缩放事件", () => {
    expect(chartZoomFromDataZoomEvent({ startValue: 2 }, ["a", "b", "c"])).toBeNull();
    expect(chartZoomFromDataZoomEvent({ start: 80, end: 20 }, ["a", "b", "c"])).toBeNull();
  });
});

describe("图表手势归属", () => {
  const pointer = (clientX: number, clientY = 0, pointerId = 1) => ({ clientX, clientY, pointerId });
  it("绘制、图形、端点依次优先于平移", () => {
    expect(gestureOwner(true, true, true)).toBe("draw");
    expect(gestureOwner(false, true, true)).toBe("drawing");
    expect(gestureOwner(false, false, true)).toBe("structure");
    expect(gestureOwner(false, false, false)).toBe("pan");
  });
  it("超过5px才抑制点击，释放后持续到下一次按下", () => {
    const gesture = new ChartGesture();
    gesture.begin(pointer(0), "pan");
    expect(gesture.move(pointer(3, 4))).toBe(false);
    expect(gesture.suppressClick).toBe(false);
    expect(gesture.move(pointer(4, 4))).toBe(true);
    gesture.finish();
    expect(gesture.active).toBeNull();
    expect(gesture.suppressClick).toBe(true);
    gesture.begin(pointer(0), "pan");
    expect(gesture.suppressClick).toBe(false);
  });
  it("往返位移累计且其他指针不干扰", () => {
    const gesture = new ChartGesture();
    gesture.begin(pointer(0), "pan");
    expect(gesture.move(pointer(100, 0, 2))).toBe(false);
    gesture.move(pointer(3));
    expect(gesture.move(pointer(0))).toBe(true);
  });
  it("编辑和取消不落入结构点击", () => {
    const gesture = new ChartGesture();
    gesture.begin(pointer(0), "drawing");
    expect(gesture.suppressClick).toBe(true);
    gesture.begin(pointer(0), "pan");
    gesture.finish(true);
    expect(gesture.active).toBeNull();
    expect(gesture.suppressClick).toBe(true);
  });
});

describe("手势事件绑定", () => {
  const setup = (owner: GestureOwner | null) => {
    const view = new EventTarget();
    const captured = new Set<number>();
    const classes = new Set<string>();
    const element = Object.assign(new EventTarget(), {
      ownerDocument: { defaultView: view },
      setPointerCapture: (pointerId: number) => captured.add(pointerId),
      hasPointerCapture: (pointerId: number) => captured.has(pointerId),
      releasePointerCapture: (pointerId: number) => captured.delete(pointerId),
      classList: { toggle: (name: string, enabled: boolean) => enabled ? classes.add(name) : classes.delete(name), remove: (name: string) => classes.delete(name) },
    });
    const callbacks = { begin: vi.fn(() => owner), move: vi.fn(), end: vi.fn(), cancel: vi.fn() };
    const gesture = new ChartGesture();
    const cleanup = bindChartGestures(element as unknown as HTMLElement, gesture, () => callbacks);
    const dispatch = (target: EventTarget, type: string, clientX = 0, extra = {}) => {
      const event = Object.assign(new Event(type, { cancelable: true }), { clientX, clientY: 0, pointerId: 1, button: 0, isPrimary: true }, extra);
      target.dispatchEvent(event);
      return event;
    };
    return { view, element, captured, classes, callbacks, gesture, cleanup, dispatch };
  };
  it.each(["draw", "drawing", "structure"] as const)("%s 捕获编辑事件，不交给原生平移", (owner) => {
    const state = setup(owner);
    expect(state.dispatch(state.element, "pointerdown").defaultPrevented).toBe(true);
    expect(state.captured.has(1)).toBe(true);
    expect(state.dispatch(state.view, "pointermove", 12).defaultPrevented).toBe(true);
    expect(state.callbacks.move).toHaveBeenCalledOnce();
    expect(state.dispatch(state.view, "pointerup", 12).defaultPrevented).toBe(true);
    expect(state.callbacks.end).toHaveBeenCalledOnce();
    expect(state.captured.size).toBe(0);
    state.cleanup();
  });
  it("平移事件交给ECharts，松手不误点", () => {
    const state = setup("pan");
    expect(state.dispatch(state.element, "pointerdown").defaultPrevented).toBe(false);
    expect(state.dispatch(state.view, "pointermove", 12).defaultPrevented).toBe(false);
    expect(state.classes.has("is-panning")).toBe(true);
    state.dispatch(state.view, "pointerup", 12);
    expect(state.classes.size).toBe(0);
    expect(state.dispatch(state.element, "click").defaultPrevented).toBe(true);
    state.dispatch(state.element, "pointerdown");
    state.dispatch(state.view, "pointerup");
    expect(state.dispatch(state.element, "click").defaultPrevented).toBe(false);
    state.cleanup();
  });
  it.each(["pointercancel", "blur", "cleanup"])("%s 清理手势和捕获", (reason) => {
    const state = setup("drawing");
    state.dispatch(state.element, "pointerdown");
    if (reason === "cleanup") state.cleanup();
    else state.dispatch(state.view, reason);
    expect(state.gesture.active).toBeNull();
    expect(state.captured.size).toBe(0);
    expect(state.callbacks.cancel).toHaveBeenCalledOnce();
    expect(state.callbacks.end).not.toHaveBeenCalled();
    state.cleanup();
  });
  it("忽略右键、其他指针和非绘图区", () => {
    const state = setup(null);
    state.dispatch(state.element, "pointerdown", 0, { button: 2 });
    state.dispatch(state.element, "pointerdown", 0, { isPrimary: false });
    expect(state.callbacks.begin).not.toHaveBeenCalled();
    state.dispatch(state.element, "pointerdown");
    expect(state.gesture.active).toBeNull();
    state.cleanup();
  });
});
