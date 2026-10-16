/* The line in one drawing, for the wide layout's side column (spec
 * 2026-10-15 §1): the whole sheet, thrower at the bottom. The far end as the
 * Detail strip draws it, from 1.5 m past the throwing hog line (the delivery's
 * top edge) to the far back line; below it the delivery close up at its own,
 * larger, stretch; between them a funnel joining the delivery's across window
 * to where that window sits on the sheet.
 *
 * Pure -- core/** may not touch the DOM. */
import { DELIVERYBOX, LINEBOX, R, STRIPBOX } from "./constants.mjs";
import { deliveryGeometry, deliveryReason } from "./delivery.mjs";
import { TEE_Y, stripGeometry, stripShapes } from "./line.mjs";

const r1 = v => +(+v).toFixed(1);

/* The delivery legend's place here: the plot corner with the fewest dots.
 * Its own place, top left, is where a rock thrown wide left ends up. `x`, `y`
 * are what Delivery.jsx's Legend draws from: text above y, dots on it. */
export function quietCorner(g, size = LINEBOX.legend) {
  const p = g.plot;
  const corners = [[p.x + 8, p.y + 12], [p.x + p.w - size.w, p.y + 12],
                   [p.x + 8, p.y + p.h - 8], [p.x + p.w - size.w, p.y + p.h - 8]];
  const crowd = ([x, y]) => g.dots.filter(d => d.x >= x - 4 && d.x <= x + size.w
                                            && d.y >= y - size.h + 8 && d.y <= y + 8).length;
  const [x, y] = corners.reduce((a, c) => (crowd(c) < crowd(a) ? c : a));
  return { x: r1(x), y: r1(y) };
}

/* The far end's lines, named at the left as the delivery names its own. */
function farLabels(top, x) {
  const ky = top.h / (top.y1 - top.y0);
  return [[R.back, "back"], [0, "tee"], [R.hog, "hog"]]
    .map(([y, kind]) => ({ x: r1(x - 4), y: r1((y - top.y0) * ky + 3), text: kind, kind }));
}

const acrossOf = top => Math.round((top.w / (2 * top.half)) / (top.h / (top.y1 - top.y0)));

export function lineChartGeometry(shot, doc, box = LINEBOX) {
  if (shot == null) return null;
  const { w, h, funnel: G, bottomShare, pad } = box;
  const Wt = w - pad.l - pad.r;
  const why = deliveryReason(shot, doc);
  if (why) {
    // No delivery to show: the strip, the whole sheet, the whole height.
    const top = { ...STRIPBOX, w: Wt, h };
    return { w, h, why, top: { x: pad.l, y: 0, w: Wt, h, shapes: stripShapes(stripGeometry(shot, top)) },
             bottom: null, funnel: null, labels: farLabels(top, pad.l),
             acrossTop: acrossOf(top), acrossBottom: null, every: null };
  }
  // The delivery takes a full half of the height, so its along axis is not squeezed; the far end the rest.
  const Hb = Math.round(h * bottomShare), Ht = h - G - Hb;
  const top = { ...STRIPBOX, w: Wt, h: Ht, y1: TEE_Y - DELIVERYBOX.to };
  // With the paint under it: the near house and the 4 ft stripe in front of it.
  const g0 = deliveryGeometry(shot, { ...DELIVERYBOX, w, h: Hb, pad, paint: true });
  const kx = Wt / (2 * top.half);
  const xl = pad.l + Wt / 2 + g0.win.x0 * kx;
  return {
    w, h, why: null,
    top: { x: pad.l, y: 0, w: Wt, h: Ht, shapes: stripShapes(stripGeometry(shot, top)) },
    // The aim line carries on into the sheet above, so "to the broom" is not said twice.
    bottom: { x: 0, y: Ht + G, w, h: Hb, g: { ...g0, aimLabel: null, legend: quietCorner(g0) } },
    funnel: { xl: r1(xl), xr: r1(xl + g0.win.span * kx), yt: Ht,
              bl: g0.plot.x, br: r1(g0.plot.x + g0.plot.w), yb: Ht + G + g0.plot.y },
    labels: farLabels(top, pad.l),
    acrossTop: acrossOf(top), acrossBottom: g0.stretch, every: g0.every,
  };
}
