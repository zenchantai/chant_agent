export type ChartZoom = { start: number; end: number };
export type GestureOwner = "pan" | "draw" | "drawing" | "structure";
export type ChartPointer = { pointerId: number; clientX: number; clientY: number };

export const initialChartZoom = (count: number, intraday: boolean, saved: string | null): ChartZoom => {
  if (intraday) return { start: 0, end: 100 };
  try {
    const parsed = saved ? JSON.parse(saved) : null;
    if (parsed && Number.isFinite(parsed.start) && Number.isFinite(parsed.end)
      && parsed.start >= 0 && parsed.end <= 100 && parsed.start < parsed.end) {
      return { start: parsed.start, end: parsed.end };
    }
  } catch {}
  return { start: intraday || count <= 120 ? 0 : (count - 120) / (count - 1) * 100, end: 100 };
};

export const rebaseChartZoom = (zoom: ChartZoom, previous: string[], dates: string[]): ChartZoom => {
  if (!previous.length || dates.length <= previous.length || dates[0] === previous[0]) return zoom;
  const start = dates.indexOf(previous[Math.round(zoom.start / 100 * (previous.length - 1))]);
  const end = dates.indexOf(previous[Math.round(zoom.end / 100 * (previous.length - 1))]);
  return start >= 0 && end >= 0
    ? { start: start / (dates.length - 1) * 100, end: end / (dates.length - 1) * 100 }
    : zoom;
};

export const gestureOwner = (drawingTool: boolean, drawingHit: boolean, structureHit: boolean): GestureOwner =>
  drawingTool ? "draw" : drawingHit ? "drawing" : structureHit ? "structure" : "pan";

export class ChartGesture {
  active: { pointer: ChartPointer; owner: GestureOwner; distance: number } | null = null;
  suppressClick = false;

  begin(pointer: ChartPointer, owner: GestureOwner) {
    this.active = { pointer, owner, distance: 0 };
    this.suppressClick = owner !== "pan";
  }

  move(pointer: ChartPointer) {
    if (!this.active || pointer.pointerId !== this.active.pointer.pointerId) return false;
    this.active.distance += Math.hypot(pointer.clientX - this.active.pointer.clientX, pointer.clientY - this.active.pointer.clientY);
    this.active.pointer = pointer;
    if (this.active.distance > 5) this.suppressClick = true;
    return this.active.distance > 5;
  }

  finish(cancelled = false) {
    if (cancelled && this.active) this.suppressClick = true;
    this.active = null;
  }
}

export type ChartGestureCallbacks = {
  begin: (event: PointerEvent) => GestureOwner | null;
  move: (event: PointerEvent) => void;
  end: (event: PointerEvent) => void;
  cancel: () => void;
};

export const bindChartGestures = (element: HTMLElement, gesture: ChartGesture, callbacks: () => ChartGestureCallbacks) => {
  const view = element.ownerDocument.defaultView!;
  const stopEditingEvent = (event: PointerEvent) => {
    event.preventDefault();
    event.stopPropagation();
  };
  const down = (event: PointerEvent) => {
    if (event.button !== 0 || !event.isPrimary || gesture.active) return;
    gesture.suppressClick = false;
    const owner = callbacks().begin(event);
    if (!owner) return;
    gesture.begin(event, owner);
    if (owner !== "pan") {
      stopEditingEvent(event);
      element.setPointerCapture(event.pointerId);
    }
  };
  const move = (event: PointerEvent) => {
    const active = gesture.active;
    if (!active || active.pointer.pointerId !== event.pointerId) return;
    const dragging = gesture.move(event);
    if (active.owner === "pan") {
      element.classList.toggle("is-panning", dragging);
    } else {
      stopEditingEvent(event);
      if (dragging) callbacks().move(event);
    }
  };
  const finish = (event?: PointerEvent, cancelled = false) => {
    const active = gesture.active;
    if (!active || (event && active.pointer.pointerId !== event.pointerId)) return;
    if (event) gesture.move(event);
    if (active.owner !== "pan" && event) stopEditingEvent(event);
    gesture.finish(cancelled);
    element.classList.remove("is-panning");
    if (element.hasPointerCapture(active.pointer.pointerId)) element.releasePointerCapture(active.pointer.pointerId);
    if (cancelled || !event) callbacks().cancel();
    else callbacks().end(event);
  };
  const up = (event: PointerEvent) => finish(event);
  const cancel = (event: PointerEvent) => finish(event, true);
  const blur = () => finish(undefined, true);
  const click = (event: MouseEvent) => {
    if (gesture.suppressClick) {
      event.preventDefault();
      event.stopPropagation();
    }
  };
  element.addEventListener("pointerdown", down, true);
  element.addEventListener("click", click, true);
  view.addEventListener("pointermove", move, true);
  view.addEventListener("pointerup", up, true);
  view.addEventListener("pointercancel", cancel, true);
  element.addEventListener("lostpointercapture", cancel);
  view.addEventListener("blur", blur);
  return () => {
    blur();
    element.removeEventListener("pointerdown", down, true);
    element.removeEventListener("click", click, true);
    view.removeEventListener("pointermove", move, true);
    view.removeEventListener("pointerup", up, true);
    view.removeEventListener("pointercancel", cancel, true);
    element.removeEventListener("lostpointercapture", cancel);
    view.removeEventListener("blur", blur);
  };
};
