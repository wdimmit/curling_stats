/* Where a throw's slide was aimed and where its release sent the rock, both
 * measured at the skip's broom, and the set between them: how much the release
 * changed the line. Pure functions of the throw record the API serves
 * (service/practice.py). The design: docs/superpowers/specs/
 * 2026-10-28-slide-release-labels-design.md.
 *
 * Ice coordinates as the record has them: y metres from the destination tee
 * (up-sheet toward the thrower), x metres to the thrower's right. */

const IN_PER_M = 1 / 0.0254;
// The rock has left the hack this far down the sheet from where it sat.
const LEFT_HACK_M = 0.3;
// The slide fit stops this far before the release: the 6 ft the set happens
// in, plus 0.5 m because the release read itself is off by up to that on 83%
// of throws (docs/superpowers/specs/2026-10-22-release-point-design.md).
const BEFORE_RELEASE_M = 1.83 + 0.5;
// Fewer points, or a shorter span, is not a slide to extend 30 m.
const FIT_MIN_N = 8;
const FIT_MIN_SPAN_M = 2.0;
// The release offset's error, which the record does not carry: today's R and
// a refit from the slide's tracking differ by a median 3 in on 53 practice
// throws, two measurements' worth.
const SIGMA_R_IN = 1.5;
// A set is shown only when it is this many of its standard errors.
const SET_SIGMAS = 2;
// A line within this either side of the broom was thrown to it: curlers
// count 2 in across the broom as on it. Judged on the figure as shown.
const ON_BROOM_IN = 1.0;

/* The depths the slide is fitted over, { lo, hi } in y: from leaving the hack
 * to BEFORE_RELEASE_M before the release. Null without a release point. */
function slideWindow(line) {
  const d = line?.delivery, rel = line?.release;
  if (!Array.isArray(d) || !Array.isArray(rel) || !Number.isFinite(rel[1])) return null;
  const startY = Number.isFinite(line.start?.y) ? line.start.y : d[0]?.[1];
  if (!Number.isFinite(startY)) return null;
  return { lo: rel[1] + BEFORE_RELEASE_M, hi: startY - LEFT_HACK_M };
}

/* The tracked points of the slide that the fit uses, as [t, y, x]. */
function slidePoints(line) {
  const w = slideWindow(line);
  if (!w) return [];
  return line.delivery.filter(p => Array.isArray(p) && p.slice(0, 3).every(Number.isFinite)
                                   && p[1] > w.lo && p[1] < w.hi);
}

/* The straight line x = a + b·y through the slide (slidePoints). Null without
 * a release point, or with too little slide to trust. */
export function slideFit(line) {
  const pts = slidePoints(line);
  const n = pts.length;
  if (n < FIT_MIN_N) return null;
  const ys = pts.map(p => p[1]), xs = pts.map(p => p[2]);
  if (Math.max(...ys) - Math.min(...ys) < FIT_MIN_SPAN_M) return null;
  const my = ys.reduce((s, y) => s + y, 0) / n, mx = xs.reduce((s, x) => s + x, 0) / n;
  const sxx = ys.reduce((s, y) => s + (y - my) ** 2, 0);
  const b = ys.reduce((s, y, i) => s + (y - my) * (xs[i] - mx), 0) / sxx;
  const a = mx - b * my;
  const rms = Math.sqrt(ys.reduce((s, y, i) => s + (xs[i] - a - b * y) ** 2, 0) / (n - 2));
  return { a, b, n, rms, my, sxx };
}

/* The fit's x at depth y, and its standard error there. */
function fitAt(fit, y) {
  const x = fit.a + fit.b * y;
  const se = fit.rms * Math.sqrt(1 / fit.n + (y - fit.my) ** 2 / fit.sxx);
  return { x, se };
}

/* +1 when the rock curled right, -1 left, null when no curl was seen. Wide is
 * the side away from the curl (game/linetime.py side_of). */
function toward(curl) {
  return curl === "right" ? 1 : curl === "left" ? -1 : null;
}

/* The numbers behind the summary, in inches: S and R signed to the right (+x),
 * the set signed wide-positive (to the right without a curl), and whether the
 * set is big enough to show. Null without a broom and a release line. */
export function slideReading(t) {
  const line = t?.line, broom = t?.broom;
  if (!broom || !Number.isFinite(line?.miss_m)) return null;
  const tw = toward(t.curl);
  const wide = v => (tw == null ? v : -tw * v);
  const r_in = line.miss_m * IN_PER_M;
  const fit = slideFit(line);
  if (!fit) return { s_in: null, r_in, set_in: null, se_s_in: null, set_shown: false, toward: tw };
  const at = fitAt(fit, broom.y);
  const s_in = (at.x - broom.x) * IN_PER_M, se_s_in = at.se * IN_PER_M;
  const set_in = wide(r_in) - wide(s_in);
  const sigma = Math.sqrt(se_s_in ** 2 + SIGMA_R_IN ** 2);
  return { s_in, r_in, set_in, se_s_in, set_shown: Math.abs(set_in) > SET_SIGMAS * sigma,
           toward: tw };
}

/* Inches to the nearest whole one: finer would claim more than the cameras
 * measure, whose error is inches. */
function inches(v) {
  return String(Math.round(Math.abs(v)));
}

/* { value: '9"', side: "narrow" }, or { value: "to the broom", side: null }
 * within ON_BROOM_IN either side. ``v`` is signed right. */
function offsetParts(v, tw) {
  if (Number(inches(v)) <= ON_BROOM_IN) return { value: "to the broom", side: null };
  const side = tw == null ? (v > 0 ? "right" : "left") : (-tw * v > 0 ? "wide" : "narrow");
  return { value: `${inches(v)}"`, side };
}

/* The set as { value }: signed, + toward wide and − toward narrow ('+11"',
 * '−11"'); without a curl, which way it moved ('11" right'). */
function setParts(set, tw) {
  if (tw == null) return { value: `${inches(set)}" ${set > 0 ? "right" : "left"}` };
  return { value: `${set > 0 ? "+" : "−"}${inches(set)}"` };
}

/* The summary in parts, for the page to lay out: { slide, release, set },
 * slide null when it cannot be measured, set null when it is not shown. Null
 * altogether without a broom. */
export function slideFigures(t) {
  const r = slideReading(t);
  if (!r) return null;
  return {
    slide: r.s_in == null ? null : offsetParts(r.s_in, r.toward),
    release: offsetParts(r.r_in, r.toward),
    set: r.set_shown ? setParts(r.set_in, r.toward) : null,
  };
}

/* The summary as two lines: { slide, release }, slide null when it cannot be
 * measured. Null altogether without a broom. */
export function slideSummary(t) {
  const f = slideFigures(t);
  if (!f) return null;
  return {
    slide: f.slide && `Slide: ${figureWords(f.slide)}`,
    release: `Release: ${figureWords(f.release)}${f.set ? ` (set ${setWords(f.set)})` : ""}`,
  };
}

/* A slide or release figure in words: '9" narrow', "to the broom". */
export function figureWords(f) {
  return f.side ? `${f.value} ${f.side}` : f.value;
}

/* The throws table's Line: the card's Release figure in words, so the two
 * agree on inches and on what counts as to the broom ('14" wide', not
 * "1 ft 2 in wide"). Null without a broom, for the table to fall back on. */
export function releaseWords(t) {
  const f = slideFigures(t);
  return f && figureWords(f.release);
}

/* The set in short words: '+11"', or '11" right' without a curl. */
export function setWords(s) {
  return s.value;
}
