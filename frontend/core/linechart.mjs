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

const AIM_WORD_PX = 56;          // "to the broom" at 8.5 px: 53 px in a wide sans

/* Where an upright delivery's legend goes, the line chart's `legendAt` (and
 * the phone's, Delivery.jsx): the plot corner with the fewest dots, and never
 * one over the words on the plot -- the release's marker, which the legend
 * explains, or "to the broom". Top left, the first tried, is where a rock
 * thrown wide left ends up. `x`, `y` are what Delivery.jsx's Legend draws
 * from: its first row above y, its last on it; it takes x - 4 to x + size.w
 * across and y - size.h + 8 to y + 8 down. */
export function quietCorner(g, size = LINEBOX.legend) {
  const p = g.plot;
  const corners = [[p.x + 8, p.y + 12], [p.x + p.w - size.w, p.y + 12],
                   [p.x + 8, p.y + p.h - 8], [p.x + p.w - size.w, p.y + p.h - 8]];
  const over = ([x, y], b) => b.x1 <= x + size.w && b.x2 >= x - 4 && b.y1 <= y + 8 && b.y2 >= y - size.h + 8;
  const a = g.aimLabel;
  const ax = a && (a.anchor === "end" ? a.x - AIM_WORD_PX : a.anchor === "middle" ? a.x - AIM_WORD_PX / 2 : a.x);
  const words = [g.release?.box, a && { x1: ax, y1: a.y - 8, x2: ax + AIM_WORD_PX, y2: a.y }].filter(Boolean);
  const crowd = c => (words.some(b => over(c, b)) ? Infinity : 0)
    + g.dots.filter(d => over(c, { x1: d.x, y1: d.y, x2: d.x, y2: d.y })).length;
  const [x, y] = corners.reduce((best, c) => (crowd(c) < crowd(best) ? c : best));
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
  // The aim line carries on into the sheet above, so "to the broom" is not said twice.
  const g = { ...g0, aimLabel: null };
  const kx = Wt / (2 * top.half);
  const xl = pad.l + Wt / 2 + g0.win.x0 * kx;
  return {
    w, h, why: null,
    top: { x: pad.l, y: 0, w: Wt, h: Ht, shapes: stripShapes(stripGeometry(shot, top)) },
    bottom: { x: 0, y: Ht + G, w, h: Hb, g: { ...g, legendAt: quietCorner(g) } },
    funnel: { xl: r1(xl), xr: r1(xl + g0.win.span * kx), yt: Ht,
              bl: g0.plot.x, br: r1(g0.plot.x + g0.plot.w), yb: Ht + G + g0.plot.y },
    labels: farLabels(top, pad.l),
    acrossTop: acrossOf(top), acrossBottom: g0.stretch, every: g0.every,
  };
}
