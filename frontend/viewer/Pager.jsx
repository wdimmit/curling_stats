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

/* The middle of the pager is also the way to another end: a native <select>
 * laid over it, invisible, so a tap opens the phone's own picker rather than
 * one we would have to draw, scroll and dismiss. An end with no rocks is
 * listed but cannot be picked -- the pager has nothing to show there, and
 * without a pager there is no way back but the menu. */
export function Pager({ row, count, index, ends, ei, canPrev, canNext, onStep, onEnd }) {
  const sub = [row.color, row.position, row.name].filter(Boolean).join(" · ");
  return (
    <div className="wpager">
      <button type="button" aria-label="Previous rock" disabled={!canPrev} onClick={() => onStep(-1)}>‹</button>
      <div className="wpmid">
        <div className="wptop">
          <span className={`wdisc ${row.color}`}>{row.number}</span>
          <span>End {ends[ei]?.number} · {sub}</span>
          <span className="wpcaret" aria-hidden="true">▾</span>
        </div>
        <select className="wpend" aria-label="Go to end" value={ei}
                onChange={e => onEnd(+e.target.value)}>
          {ends.map((x, i) => (
            <option key={i} value={i} disabled={!x.rocks}>
              {`End ${x.number}${x.house ? ` (${x.house})` : ""}${x.rocks ? "" : " · no rocks"}`}
            </option>
          ))}
        </select>
        <div className="wpdots" aria-hidden="true">
          {Array.from({ length: count }, (_, i) => <i key={i} className={i === index ? "on" : undefined} />)}
        </div>
      </div>
      <button type="button" aria-label="Next rock" disabled={!canNext} onClick={() => onStep(1)}>›</button>
    </div>
  );
}
