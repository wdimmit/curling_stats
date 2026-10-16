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
import { HACK_PAST, HOG_PAST, deliveryPoints } from "./delivery.mjs";

export const SPEED_HALF_S = 0.5;
const SPEED_MIN_N = 5;          // samples a window needs before it has a slope
const PUSH_FROM_M_S = 0.25;     // back from the peak until the rock is nearly still
const HOG_FIT_FROM_M = 1.0;     // the hog fit starts this far short of the hog line
const HOG_FIT_MIN_N = 6;
const HOG_FIT_MIN_S = 0.5;
const HELD_M_S = 0.05;          // a smaller loss than this is "held its speed"

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
 * was lost between them -- never a negative loss. */
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
    const lost = speed.peak.v - speed.hog.v;
    out.change = lost >= HELD_M_S ? { value: `${f(lost)} m/s`, words: "lost between them" }
                                  : { value: null, words: "held its speed to the hog line" };
  }
  return out;
}
