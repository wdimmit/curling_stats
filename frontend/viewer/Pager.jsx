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
