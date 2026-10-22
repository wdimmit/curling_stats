/* The delivery, close up: the thrown rock from where it sat in front of the
 * hack to 1.5 m past the throwing hog line, from above, as the hog-line camera
 * saw it frame by frame (schema 8's `line.delivery`, [t, y, x] with t seconds
 * from the release, y house metres from the destination tee, x metres to the
 * thrower's right). The Detail strip shows the whole sheet at ×1.5-3 across;
 * this shows the throwing end at ×4-8, where the hand moves the rock
 * sideways before it lets go.
 *
 * Pure -- core/** may not touch the DOM. */
import { DELIVERY_COLORS, OVERLAYBOX, R, STRIPE_HALF_M } from "./constants.mjs";
import { HACK_X_M, HACK_Y, TEE_Y, hackAimX, hackOf, lineReason, median, sideNames } from "./line.mjs";

export const DELIVERY_SCHEMA = 8;
// Fail closed, as LINE_SCHEMA does: undefined >= 8 is false.
export const deliveryReadable = doc => Number(doc?.schema_version) >= DELIVERY_SCHEMA;

export const HACK_PAST = -(HACK_Y - TEE_Y);          // -3.658: the hack, in metres past the throwing tee
export const HOG_PAST = 6.401;
const MARGIN_M = 0.06;                        // room between the data and the window's edge
const SNAP_M = 0.1;
const INCH_M = 0.0254;

/* The rock's samples as {t, yp, x}: yp metres past the throwing tee. */
export function deliveryPoints(shot) {
  const d = shot?.line?.delivery;
  if (!Array.isArray(d)) return [];
  return d.filter(p => Array.isArray(p) && p.length >= 3 && p.every(Number.isFinite))
    .map(([t, y, x]) => ({ t, yp: TEE_Y - y, x }));
}

/* Why there is no delivery to draw, or null. */
export function deliveryReason(shot, doc) {
  if (!deliveryReadable(doc)) return "This chart predates the delivery chart";
  const r = lineReason(shot, doc);
  if (r) return r;
  if (deliveryPoints(shot).length < 2) return "The hog-line camera did not follow this rock from the hack";
  return null;
}

/* Where the hand let go, as {t, yp, x} in deliveryPoints' frame, from
 * `line.release` ([t, y, x] as the delivery's samples). null when the release
 * was not read -- and when its record is not three finite numbers, which is
 * read the same way, so a bad record draws teal dots and not a wrong split. */
export function deliveryRelease(shot) {
  const r = shot?.line?.release;
  if (!Array.isArray(r) || r.length < 3 || !r.every(Number.isFinite)) return null;
  const [t, y, x] = r;
  return { t, yp: TEE_Y - y, x };
}

/* A dot's colour, `t` seconds on the delivery's clock: before the release
 * `rel` (deliveryRelease's), from it on, or unknown without one. The speed
 * line takes its colours from here too, so the two split at one moment. */
export function dotColor(t, rel) {
  if (!rel) return DELIVERY_COLORS.unknown;
  return t < rel.t ? DELIVERY_COLORS.before : DELIVERY_COLORS.after;
}

const r1 = v => +(+v).toFixed(1);

/* Ticks across a window `span` metres wide from `x0`: one every `every`
 * inches, figured every `figure` inches as the miss scatter figures its feet
 * -- "6 in" either side of a bare 0, the chart itself showing which side.
 * Counted in whole inches, so they do not drift. */
function inchTicks(x0, span, every, figure) {
  const out = [];
  for (let k = Math.ceil(x0 / (every * INCH_M) - 1e-9); k * every * INCH_M <= x0 + span + 1e-9; k++) {
    const inches = k * every;
    out.push({ c: inches * INCH_M, inches,
               label: inches % figure ? "" : inches === 0 ? "0" : `${Math.abs(inches)} in` });
  }
  return out;
}

/* The across window: `across` metres wide, centred on the centre line when
 * everything fits, shifted the least that makes it fit (snapped to 10 cm),
 * and only widened when nothing else will do -- so most rocks share one scale
 * and a corner guard is still drawn whole. */
function acrossWindow(xs, across) {
  const lo = Math.min(...xs) - MARGIN_M, hi = Math.max(...xs) + MARGIN_M;
  if (hi - lo > across) return { c: (lo + hi) / 2, span: hi - lo };
  const half = across / 2;
  let c = 0;
  if (lo < c - half) c = lo + half;
  if (hi > c + half) c = hi - half;
  c = Math.round(c / SNAP_M) * SNAP_M;
  // Snapping can push an edge back out by up to 5 cm; nudge in if it did.
  if (lo < c - half) c = lo + half;
  if (hi > c + half) c = hi - half;
  return { c, span: across };
}

const TICK_PX = 6;                     // the release's tick, each side of the path
const WORD_GAP_PX = 3;                 // from the tick's end to its word
const WORD_PX = { w: 32, h: 8 };       // "Release" at the charts' 8.5-9 px type, about

/* The release's marker at `at` (page px, on the plot): a tick across the
 * path, and the word beside it. The word goes on the side `away` (+1 the
 * thrower's right, -1 the thrower's left: away from the aim line, which runs
 * close by) unless the plot's edge is nearer there than the word is long.
 * Upright the path runs up the page and the word sits to one side of the
 * tick; on its side the path runs across the page and the word sits above or
 * below. */
function releaseMark(at, side, plot, away) {
  const reach = TICK_PX + WORD_GAP_PX;
  // Across the path is page x upright, page y on its side; both grow to the thrower's right.
  const [c, lo, hi, need] = side ? [at.y, plot.y, plot.y + plot.h, WORD_PX.h]
                                 : [at.x, plot.x, plot.x + plot.w, WORD_PX.w];
  const room = s => (s > 0 ? hi - (c + reach) : c - reach - lo);
  const s = room(away) >= need || room(away) >= room(-away) ? away : -away;
  if (side) {
    const anchor = at.x - WORD_PX.w / 2 < plot.x ? "start"
      : at.x + WORD_PX.w / 2 > plot.x + plot.w ? "end" : "middle";
    return { ...at, tick: { x1: at.x, y1: r1(at.y - TICK_PX), x2: at.x, y2: r1(at.y + TICK_PX) },
             label: { x: at.x, y: r1(s > 0 ? at.y + reach + WORD_PX.h : at.y - reach), anchor, text: "Release" } };
  }
  // Upright the word is level with the tick: its baseline 3 px below it.
  const y = Math.min(Math.max(at.y + 3, plot.y + WORD_PX.h), plot.y + plot.h);
  return { ...at, tick: { x1: r1(at.x - TICK_PX), y1: at.y, x2: r1(at.x + TICK_PX), y2: at.y },
           label: { x: r1(at.x + s * reach), y: r1(y), anchor: s > 0 ? "start" : "end", text: "Release" } };
}

/* The chart in pixels. `box.side` false is upright, the thrower at the bottom
 * and the thrower's right to the right, as the phone's strip; true turns it a
 * quarter as the desktop strip is turned, the hack at the left and the
 * thrower's left along the top. null only without a shot. */
export function deliveryGeometry(shot, box) {
  if (shot == null) return null;
  const { w, h, side, from, to, across, pad } = box;
  const pts = deliveryPoints(shot).filter(p => p.yp >= from && p.yp <= to);
  const l = shot.line || {};
  const start = l.start && Number.isFinite(l.start.x) ? { x: l.start.x, yp: TEE_Y - l.start.y } : null;
  const hand = hackOf(shot);
  const aimAt = yp => hackAimX(shot, TEE_Y - yp);
  const aim = [HACK_PAST, to].map(yp => [aimAt(yp), yp]).filter(([x]) => x != null);
  const xs = [-HACK_X_M, HACK_X_M, ...pts.map(p => p.x), ...(start ? [start.x] : []), ...aim.map(([x]) => x)];
  const win = acrossWindow(xs, across);
  const plotW = w - pad.l - pad.r, plotH = h - pad.t - pad.b;
  const along = to - from;
  // px per metre in each direction, and the map from (x, yp) to the page.
  const kAlong = (side ? plotW : plotH) / along, kAcross = (side ? plotH : plotW) / win.span;
  const x0 = win.c - win.span / 2;
  const P = side
    ? (x, yp) => [pad.l + (yp - from) * kAlong, pad.t + (x - x0) * kAcross]
    : (x, yp) => [pad.l + (x - x0) * kAcross, pad.t + (to - yp) * kAlong];
  const pt = (x, yp) => { const [a, b] = P(x, yp); return { x: r1(a), y: r1(b) }; };
  const poly = list => list.map(([x, yp]) => P(x, yp).map(v => v.toFixed(1)).join(",")).join(" ");
  // A line at one depth, across the whole window.
  const acrossLine = (yp, kind) => {
    const a = pt(x0, yp), b = pt(x0 + win.span, yp);
    return { x1: a.x, y1: a.y, x2: b.x, y2: b.y, kind };
  };
  const lines = [[HACK_PAST, "hack"], [0, "tee"], [HOG_PAST, "hog"]]
    .filter(([yp]) => yp >= from && yp <= to).map(([yp, k]) => acrossLine(yp, k));
  if (x0 < 0 && x0 + win.span > 0) {
    const a = pt(0, from), b = pt(0, to);
    lines.push({ x1: a.x, y1: a.y, x2: b.x, y2: b.y, kind: "centre" });
  }
  // Labels just outside the plot: to the left of each line upright, above it
  // on its side (the line runs top to bottom there).
  const labels = lines.filter(q => q.kind !== "centre").map(q => side
    ? { x: q.x1, y: r1(pad.t - 4), text: q.kind, anchor: "middle", kind: q.kind }
    : { x: r1(pad.l - 4), y: r1(q.y1 + 3), text: q.kind, anchor: "end", kind: q.kind });
  // A tick every 6 in across, each figured, off the centre line.
  const ticks = inchTicks(x0, win.span, 6, 6).map(({ c, label }) => {
    const a = pt(c, from);
    return side
      ? { x1: r1(pad.l + plotW), y1: a.y, x2: r1(pad.l + plotW + 4), y2: a.y,
          label, lx: r1(pad.l + plotW + 6), ly: r1(a.y + 3), anchor: "start" }
      : { x1: a.x, y1: r1(pad.t + plotH), x2: a.x, y2: r1(pad.t + plotH + 4),
          label, lx: a.x, ly: r1(pad.t + plotH + 14), anchor: "middle" };
  });
  // Unfigured ticks between the figured ones, for a box drawn big enough to use them.
  const minor = !box.minorInches ? [] : inchTicks(x0, win.span, box.minorInches, box.minorInches)
    .filter(({ inches }) => inches % 6 !== 0)
    .map(({ c }) => {
      const a = pt(c, from);
      return side ? { x1: r1(pad.l + plotW), y1: a.y, x2: r1(pad.l + plotW + 2.5), y2: a.y }
                  : { x1: a.x, y1: r1(pad.t + plotH), x2: a.x, y2: r1(pad.t + plotH + 2.5) };
    });
  // The paint under the close up, for a box that asks (the line chart's and
  // the large view's): the near house, and the club's 4 ft centre stripe --
  // painted in front of the house only, from where its edges meet the 12 ft,
  // along that ring's curve, out to the chart's far edge.
  let paint = null;
  if (box.paint) {
    const [cx, cy] = P(0, 0);
    const radii = r => (side ? [r * kAlong, r * kAcross] : [r * kAcross, r * kAlong]);
    const house = [[R.twelve, "twelve"], [R.eight, "eight"], [R.four, "four"], [R.button, "button"]]
      .map(([r, kind]) => { const [rx, ry] = radii(r); return { cx: r1(cx), cy: r1(cy), rx: r1(rx), ry: r1(ry), kind }; });
    const meet = Math.sqrt(R.twelve ** 2 - STRIPE_HALF_M ** 2);
    let stripe = null;
    if (meet < to) {
      const at = (x, yp) => P(x, yp).map(v => v.toFixed(1)).join(",");
      const [rx, ry] = radii(R.twelve).map(v => v.toFixed(1));
      // Sweep 0 takes the arc round the front of the 12 ft, upright or on its side.
      const d = `M${at(-STRIPE_HALF_M, to)} L${at(STRIPE_HALF_M, to)} L${at(STRIPE_HALF_M, meet)} `
              + `A${rx},${ry} 0 0 0 ${at(-STRIPE_HALF_M, meet)} Z`;
      const edge = x => { const a = pt(x, meet), b = pt(x, to); return { x1: a.x, y1: a.y, x2: b.x, y2: b.y }; };
      stripe = { d, edges: [edge(-STRIPE_HALF_M), edge(STRIPE_HALF_M)] };
    }
    paint = { house, stripe };
  }
  // The two footholds: 0.15 m wide, 0.2 m along, their centres ±HACK_X_M on the hack line.
  const holds = [-HACK_X_M, HACK_X_M].map(hx => {
    const a = pt(hx - 0.075, HACK_PAST - 0.2), b = pt(hx + 0.075, HACK_PAST);
    return { x: Math.min(a.x, b.x), y: Math.min(a.y, b.y), w: r1(Math.abs(b.x - a.x)), h: r1(Math.abs(b.y - a.y)), side: hx < 0 ? "left" : "right", used: hx < 0 ? hand === "left" : hand === "right" };
  });
  // The path: dots every sample, a faint line through them broken where the
  // camera lost the rock for a while. The dots change colour at the release.
  const rel = deliveryRelease(shot);
  const dots = pts.map(p => ({ ...pt(p.x, p.yp), fill: dotColor(p.t, rel) }));
  const steps = pts.slice(1).map((p, i) => p.t - pts[i].t).sort((a, b) => a - b);
  const stepS = steps.length ? steps[steps.length >> 1] : 0.1;
  // How often the path has a dot: a tenth of a second until pipeline
  // 2026.10.12.1, every frame (30 a second) since -- whichever of the rates a
  // path is read at its median step is nearest. Not worked out from the steps
  // alone: samples go missing where the rock is lost, and times come to the
  // hundredth (a 30 fps path's steps are 0.03 and 0.04 by turns). Denser dots
  // are smaller, or at a rock's pace they run into one band.
  const perSecond = nearestRate(stepS);
  const dotR = r1(Math.min(2.3, 2.3 * Math.sqrt(10 / perSecond)));
  // The faint line breaks where the camera lost the rock for a while: a
  // quarter of a second, at any rate, as 2.5 steps were at 10 a second.
  const gapS = Math.max(GAP_MIN_S, 2.5 / perSecond);
  const runs = [];
  let run = [];
  pts.forEach((p, i) => {
    if (i && p.t - pts[i - 1].t > gapS && p.yp - pts[i - 1].yp > 0.3) { if (run.length >= 2) runs.push(poly(run)); run = []; }
    run.push([p.x, p.yp]);
  });
  if (run.length >= 2) runs.push(poly(run));
  const aimEnd = aim.length === 2 ? pt(aim[1][0], aim[1][1]) : null;
  // Upright, the label goes on whichever side of the line's end has the room.
  const roomLeft = aimEnd && aimEnd.x > pad.l + plotW / 2;
  // The release's marker, on the plot only, as the path is.
  const relAim = rel && aimAt(rel.yp);
  const release = rel && rel.yp >= from && rel.yp <= to && rel.x >= x0 && rel.x <= x0 + win.span
    ? releaseMark(pt(rel.x, rel.yp), !!side, { x: pad.l, y: pad.t, w: plotW, h: plotH },
                  relAim != null && relAim > rel.x ? -1 : 1)
    : null;
  return {
    w, h, side: !!side,
    stretch: Math.round(kAcross / kAlong),
    plot: { x: pad.l, y: pad.t, w: plotW, h: plotH },
    lines, labels, ticks, holds,
    win: { x0: +x0.toFixed(4), span: +win.span.toFixed(4) }, minor, paint,
    aim: aim.length === 2 ? poly(aim) : null,
    aimLabel: aimEnd ? (side ? { x: r1(aimEnd.x - 3), y: r1(aimEnd.y - 4), text: "to the broom", anchor: "end" }
                               : { x: r1(aimEnd.x + (roomLeft ? -4 : 4)), y: r1(aimEnd.y + 10), text: "to the broom",
                                   anchor: roomLeft ? "end" : "start" }) : null,
    runs, dots, dotR,
    release,
    // What the dots' two colours mean; one colour needs no key.
    legend: rel ? [{ color: DELIVERY_COLORS.before, text: "before release" },
                   { color: DELIVERY_COLORS.after, text: "after release" }] : [],
    every: dotEvery(perSecond),
    start: start && start.yp >= from && start.yp <= to ? pt(start.x, start.yp) : null,
    points: pts.length,
  };
}

// The rates a delivery path is read at (linetime.DELIVERY_FPS, now and before).
const PATH_RATES = [30, 10];
const GAP_MIN_S = 0.25;

/* The path rate whose step is nearest `stepS`, as a ratio. */
function nearestRate(stepS) {
  const off = r => Math.abs(Math.log(Math.max(stepS, 1e-3) * r));
  return PATH_RATES.reduce((a, b) => (off(b) < off(a) ? b : a));
}

/* How often a delivery path has a dot, for its caption and its label. */
export function dotEvery(perSecond) {
  if (perSecond === 10) return { short: "0.1 s", words: "tenth of a second" };
  if (perSecond === 30) return { short: "frame (30 a second)", words: "frame, thirty a second" };
  return { short: `1/${perSecond} s`, words: `1/${perSecond} of a second` };
}

/* One rock's delivery in the frame every rock can share: along, metres past
 * the throwing tee, as above; across, `dx`, metres off this rock's own line
 * from its foothold to its broom (+ to the thrower's right). Raw x cannot be
 * overlaid -- the brooms sit metres apart and the footholds 30 cm -- but off
 * its own aim line a perfect delivery is dx 0 all the way, whatever it was
 * aimed at. The release, when one was read, is in the same frame. null when
 * there is no delivery, or no broom or hack to aim from. */
export function aimFrame(shot, doc) {
  if (deliveryReason(shot, doc)) return null;
  const off = (x, yp) => {
    const aim = hackAimX(shot, TEE_Y - yp);
    return aim == null ? null : x - aim;
  };
  const pts = deliveryPoints(shot).map(p => ({ t: p.t, yp: p.yp, dx: off(p.x, p.yp) }));
  if (pts.some(p => p.dx == null)) return null;
  const st = shot.line?.start;
  const start = st && Number.isFinite(st.x) && Number.isFinite(st.y)
    ? { yp: TEE_Y - st.y, dx: off(st.x, TEE_Y - st.y) } : null;
  const rel = deliveryRelease(shot);
  const relDx = rel && off(rel.x, rel.yp);
  return { pts, start: start && start.dx != null ? start : null,
           release: relDx != null ? { yp: rel.yp, dx: relDx } : null };
}

const OVERLAY_BIN_M = 0.25;
const OVERLAY_BIN_MIN = 3;               // rocks a bin needs before it has a median

/* Every rock of a group drawn in aimFrame's frame, upright, the thrower at
 * the bottom: one line per rock, the dot it sat at before the push, and the
 * median of them all where at least three rocks pass, and the point each
 * rock's hand let go -- unlabelled: their spread is what shows. `paths` are
 * [{key, pts, start, release, dim}] from aimFrame; `turn` names the sides,
 * wide and narrow, as the scatter beside it does. */
export function deliveryOverlay(paths, turn = null, box = OVERLAYBOX) {
  const { w, h, from, to, across, maxAcross, pad } = box;
  const rocks = (paths || []).map(p => ({ ...p, pts: (p.pts || []).filter(q => q.yp >= from && q.yp <= to) }))
    .filter(p => p.pts.length >= 2);
  const xs = [0, ...rocks.flatMap(p => p.pts.map(q => q.dx))];
  let win = acrossWindow(xs, across);
  if (win.span > maxAcross) win = { c: 0, span: maxAcross };
  const plotW = w - pad.l - pad.r, plotH = h - pad.t - pad.b;
  const kAlong = plotH / (to - from), kAcross = plotW / win.span;
  const x0 = win.c - win.span / 2;
  const P = (dx, yp) => [pad.l + (dx - x0) * kAcross, pad.t + (to - yp) * kAlong];
  const pt = (dx, yp) => { const [a, b] = P(dx, yp); return { x: r1(a), y: r1(b) }; };
  const poly = list => list.map(([dx, yp]) => P(dx, yp).map(v => v.toFixed(1)).join(",")).join(" ");
  const lines = [[HACK_PAST, "hack"], [0, "tee"], [HOG_PAST, "hog"]]
    .filter(([yp]) => yp >= from && yp <= to)
    .map(([yp, kind]) => { const a = pt(x0, yp), b = pt(x0 + win.span, yp); return { x1: a.x, y1: a.y, x2: b.x, y2: b.y, kind }; });
  const labels = lines.map(q => ({ x: r1(pad.l - 4), y: r1(q.y1 + 3), text: q.kind, anchor: "end", kind: q.kind }));
  const a = pt(0, from), b = pt(0, to);
  const aim = { x1: a.x, y1: a.y, x2: b.x, y2: b.y };
  // A tick every 3 in off the aim line, figured every 6 -- every foot once
  // a wild rock has widened the window past a metre.
  const ticks = inchTicks(x0, win.span, 3, win.span > 1 ? 12 : 6).map(({ c, inches, label }) => {
    const q = pt(c, from);
    return { key: inches, x1: q.x, y1: r1(pad.t + plotH), x2: q.x, y2: r1(pad.t + plotH + 4),
             label, lx: q.x, ly: r1(pad.t + plotH + 14) };
  });
  const sides = sideNames(turn);
  const onPlot = q => q.yp >= from && q.yp <= to && q.dx >= x0 && q.dx <= x0 + win.span;
  const drawn = rocks.map(p => ({ key: p.key, d: poly(p.pts.map(q => [q.dx, q.yp])), dim: !!p.dim,
    start: p.start && p.start.yp >= from && p.start.yp <= to ? pt(p.start.dx, p.start.yp) : null,
    release: p.release && onPlot(p.release) ? pt(p.release.dx, p.release.yp) : null }));
  // The median: each rock's mean dx in each bin, then the median across rocks.
  const mid = [];
  for (let lo = from; lo < to; lo += OVERLAY_BIN_M) {
    const per = rocks.map(p => p.pts.filter(q => q.yp >= lo && q.yp < lo + OVERLAY_BIN_M))
      .filter(qs => qs.length).map(qs => qs.reduce((s, q) => s + q.dx, 0) / qs.length);
    if (per.length >= OVERLAY_BIN_MIN) mid.push([median(per), lo + OVERLAY_BIN_M / 2]);
  }
  return {
    w, h, plot: { x: pad.l, y: pad.t, w: plotW, h: plotH },
    stretch: Math.round(kAcross / kAlong),
    lines, labels, aim, ticks,
    sides: [{ x: pad.l + 2, y: pad.t - 8, text: sides[0], anchor: "start" },
            { x: pad.l + plotW - 2, y: pad.t - 8, text: sides[1], anchor: "end" }],
    rocks: drawn,
    median: mid.length >= 2 ? poly(mid) : null,
    n: rocks.length,
  };
}
