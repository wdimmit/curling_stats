/* The phone viewer's rock pager and the swipe that drives it. */
import { useRef } from "react";
import { swipeStep } from "../core/index.mjs";

/* Pointer handlers that turn a horizontal drag into a rock step. The pane they
 * go on needs `touch-action: pan-y` (or none, as #house has) so the browser
 * hands the horizontal part of the gesture to us. */
export function useSwipe(onStep) {
  const start = useRef(null);
  return {
    onPointerDown: e => { start.current = { x: e.clientX, y: e.clientY }; },
    onPointerUp: e => {
      const s = start.current; start.current = null;
      if (!s) return;
      const d = swipeStep(e.clientX - s.x, e.clientY - s.y, s.x);
      if (d) onStep(d);
    },
    onPointerCancel: () => { start.current = null; },
  };
}

export function Pager({ row, count, index, end, canPrev, canNext, onStep }) {
  const sub = [row.color, row.position, row.name].filter(Boolean).join(" · ");
  return (
    <div className="wpager">
      <button type="button" aria-label="Previous rock" disabled={!canPrev} onClick={() => onStep(-1)}>‹</button>
      <div className="wpmid">
        <div className="wptop">
          <span className={`wdisc ${row.color}`}>{row.number}</span>
          <span>End {end} · {sub}</span>
        </div>
        <div className="wpdots" aria-hidden="true">
          {Array.from({ length: count }, (_, i) => <i key={i} className={i === index ? "on" : undefined} />)}
        </div>
      </div>
      <button type="button" aria-label="Next rock" disabled={!canNext} onClick={() => onStep(1)}>›</button>
    </div>
  );
}
