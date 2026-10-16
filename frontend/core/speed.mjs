/* How fast the rock went along the delivery, from line.delivery ([t, y, x], t
 * seconds from the release, y house metres from the destination tee).
 *
 * Along the ice the hog-line camera is about 10x noisier than across (3.3 cm a
 * frame against 0.3 cm, 172 rocks, 2026-10-15), so a frame-to-frame difference
 * is mostly noise: each sample's speed is the slope of a straight line through
 * every sample within SPEED_HALF_S of it. What is left after the release --
 * ripples of 0.1-0.2 m/s at the same places for every rock on a sheet, and a
 * dip at the hog line on every sheet -- is the camera, not the throw. So the
 * hog speed is not read off the smoothed line: it is one straight fit from a
 * metre short of the hog line to the end of the path, which agreed with the
 * pipeline's own hog speed (median +0.03 m/s over 172 rocks).
 *
 * Pure -- core/** may not touch the DOM. */
import { SPEEDBOX, SPEED_GRID_M_S } from "./constants.mjs";
import { HACK_PAST, HOG_PAST, deliveryPoints, rampColor, rampSpan } from "./delivery.mjs";

export const SPEED_HALF_S = 0.5;
const SPEED_MIN_N = 5;          // samples a window needs before it has a slope
const PUSH_FROM_M_S = 0.25;     // back from the peak until the rock is nearly still
const HOG_FIT_FROM_M = 1.0;     // the hog fit starts this far short of the hog line
const HOG_FIT_MIN_N = 6;
const HOG_FIT_MIN_S = 0.5;

/* The slope of the least-squares line through (t, yp): metres a second. */
function slope(pts) {
  const n = pts.length;
  const mt = pts.reduce((a, p) => a + p.t, 0) / n, my = pts.reduce((a, p) => a + p.yp, 0) / n;
  let num = 0, den = 0;
  for (const p of pts) { num += (p.t - mt) * (p.yp - my); den += (p.t - mt) ** 2; }
  return den > 0 ? num / den : null;
}

/* The speed series from the push on, its peak before the hog line, and the
 * speed at the hog line (null when the path ends short of it). null when the
 * path is too short to read. */
export function deliverySpeed(shot) {
  const pts = deliveryPoints(shot);
  if (pts.length < SPEED_MIN_N) return null;
  const all = [];
  for (const p of pts) {
    const w = pts.filter(q => Math.abs(q.t - p.t) <= SPEED_HALF_S + 1e-9);
    if (w.length < SPEED_MIN_N) continue;
    const v = slope(w);
    if (v != null) all.push({ t: p.t, yp: p.yp, v });
  }
  let pk = -1;
  all.forEach((q, i) => { if (q.yp < HOG_PAST && (pk < 0 || q.v > all[pk].v)) pk = i; });
  if (pk < 0) return null;
  // From the push: a stone nudged into place before the delivery is not it.
  let st = pk;
  while (st > 0 && all[st - 1].v > PUSH_FROM_M_S) st--;
  const tail = pts.filter(p => p.yp >= HOG_PAST - HOG_FIT_FROM_M);
  const span = tail.length ? tail[tail.length - 1].t - tail[0].t : 0;
  const hv = tail.length >= HOG_FIT_MIN_N && span >= HOG_FIT_MIN_S ? slope(tail) : null;
  const peak = all[pk];
  return { series: all.slice(st), peak: { t: peak.t, yp: peak.yp, v: peak.v },
           hog: hv == null ? null : { v: hv } };
}

/* The words over the speed chart: the peak and where, the hog speed, and what
 * was lost between them. The loss is the two figures shown subtracted, so the
 * three always add up -- and it is never negative: no loss, or a gain, is
 * "held its speed". */
export function speedNumbers(speed) {
  if (!speed) return null;
  const f = v => v.toFixed(1);
  const out = {
    peak: `${f(speed.peak.v)} m/s`,
    peakWhere: `at its fastest, ${f(speed.peak.yp - HACK_PAST)} m out of the hack`,
    hog: null, change: null,
  };
  if (speed.hog) {
    out.hog = `${f(speed.hog.v)} m/s`;
    // Rounded again: 2.6 - 1.7 is 0.9000000000000001 in floating point.
    const lost = +f(+f(speed.peak.v) - +f(speed.hog.v));
    out.change = lost > 0 ? { value: `${f(lost)} m/s`, words: "lost between them" }
                          : { value: null, words: "held its speed to the hog line" };
  }
  return out;
}

const SPEED_GAP_S = 0.25;       // a longer step between samples is a gap: the line breaks there
const r1 = v => +(+v).toFixed(1);

/* The speed chart in pixels, along the same axis as the big delivery: grid
 * every SPEED_GRID_M_S, figured every 1 m/s; the hack, tee and hog lines;
 * the line coloured as the delivery's dots; the peak and the hog speed. */
export function speedGeometry(speed, box = SPEEDBOX, shot = null) {
  if (!speed) return null;
  const { w, h, from, to, pad } = box;
  const shown = speed.series.filter(q => q.yp >= from && q.yp <= to);
  if (shown.length < 2) return null;
  const plotW = w - pad.l - pad.r, plotH = h - pad.t - pad.b;
  const top = Math.max(3, Math.ceil((Math.max(...shown.map(q => q.v)) + 0.25) * 2) / 2);
  const X = yp => r1(pad.l + (yp - from) * plotW / (to - from));
  const Y = v => r1(pad.t + plotH * (1 - Math.max(0, v) / top));
  const path = deliveryPoints(shot).filter(p => p.yp >= from && p.yp <= to);
  const { t0, t1 } = path.length ? rampSpan(path) : { t0: shown[0].t, t1: shown[shown.length - 1].t };
  const segs = [];
  for (let i = 1; i < shown.length; i++) {
    const a = shown[i - 1], b = shown[i];
    if (b.t - a.t > SPEED_GAP_S) continue;
    segs.push({ x1: X(a.yp), y1: Y(a.v), x2: X(b.yp), y2: Y(b.v),
                color: rampColor((b.t - t0) / Math.max(1e-6, t1 - t0)) });
  }
  const grid = [];
  for (let k = 0; k * SPEED_GRID_M_S <= top + 1e-9; k++) {
    const v = k * SPEED_GRID_M_S;
    grid.push({ v, y: Y(v), label: Number.isInteger(v) ? (v === 0 ? "0" : `${v} m/s`) : "" });
  }
  const lines = [[HACK_PAST, "hack"], [0, "tee"], [HOG_PAST, "hog"]]
    .filter(([yp]) => yp >= from && yp <= to)
    .map(([yp, kind]) => ({ kind, x: X(yp), y1: pad.t, y2: r1(pad.t + plotH) }));
  return {
    w, h, plot: { x: pad.l, y: pad.t, w: plotW, h: plotH }, top, axisX: r1(pad.l + plotW + 6),
    grid, lines, segs,
    peak: { x: X(speed.peak.yp), y: Y(speed.peak.v), v: speed.peak.v },
    hog: speed.hog ? { x: X(HOG_PAST), y: Y(speed.hog.v), v: speed.hog.v } : null,
  };
}
