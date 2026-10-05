/* The delivery, close up: the thrown rock from where it sat in front of the
 * hack to 1.5 m past the throwing hog line, from above, as the hog-line camera
 * saw it frame by frame (schema 8's `line.delivery`, [t, y, x] with t seconds
 * from the release, y house metres from the destination tee, x metres to the
 * thrower's right). The Detail strip shows the whole sheet at ×1.5-3 across;
 * this shows the throwing end at ×4-8, where the hand moves the rock
 * sideways before it lets go.
 *
 * Pure -- core/** may not touch the DOM. */
import { DELIVERY_RAMP, OVERLAYBOX } from "./constants.mjs";
import { HACK_X_M, HACK_Y, TEE_Y, hackAimX, hackOf, lineReason, median, sideNames } from "./line.mjs";

export const DELIVERY_SCHEMA = 8;
// Fail closed, as LINE_SCHEMA does: undefined >= 8 is false.
export const deliveryReadable = doc => Number(doc?.schema_version) >= DELIVERY_SCHEMA;

const HACK_PAST = -(HACK_Y - TEE_Y);          // -3.658: the hack, in metres past the throwing tee
const HOG_PAST = 6.401;
const MARGIN_M = 0.06;                        // room between the data and the window's edge
const SNAP_M = 0.1;

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

const hex = c => [1, 3, 5].map(i => parseInt(c.slice(i, i + 2), 16));
const toHex = rgb => "#" + rgb.map(v => Math.round(v).toString(16).padStart(2, "0")).join("");

/* A colour along the ramp, f in 0..1: dark while the rock sits, gold past
 * the hog line -- the gold the strip draws the rock's path in. */
export function rampColor(f) {
  const stops = DELIVERY_RAMP.map(hex);
  const u = Math.min(1, Math.max(0, Number.isFinite(f) ? f : 0)) * (stops.length - 1);
  const i = Math.min(stops.length - 2, Math.floor(u)), k = u - i;
  return toHex(stops[i].map((v, j) => v + (stops[i + 1][j] - v) * k));
}

const r1 = v => +(+v).toFixed(1);

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
  // Ticks every 20 cm across, labelled in cm off the centre line.
  const ticks = [];
  for (let c = Math.ceil(x0 / 0.2) * 0.2; c <= x0 + win.span + 1e-9; c += 0.2) {
    const v = Math.round(c * 100);
    const a = pt(c, from);
    ticks.push(side
      ? { x1: r1(pad.l + plotW), y1: a.y, x2: r1(pad.l + plotW + 4), y2: a.y,
          label: `${v > 0 ? "+" : ""}${v}`, lx: r1(pad.l + plotW + 6), ly: r1(a.y + 3), anchor: "start" }
      : { x1: a.x, y1: r1(pad.t + plotH), x2: a.x, y2: r1(pad.t + plotH + 4),
          label: `${v > 0 ? "+" : ""}${v}`, lx: a.x, ly: r1(pad.t + plotH + 14), anchor: "middle" });
  }
  // The two footholds: 0.15 m wide, 0.2 m along, their centres ±HACK_X_M on the hack line.
  const holds = [-HACK_X_M, HACK_X_M].map(hx => {
    const a = pt(hx - 0.075, HACK_PAST - 0.2), b = pt(hx + 0.075, HACK_PAST);
    return { x: Math.min(a.x, b.x), y: Math.min(a.y, b.y), w: r1(Math.abs(b.x - a.x)), h: r1(Math.abs(b.y - a.y)), side: hx < 0 ? "left" : "right", used: hx < 0 ? hand === "left" : hand === "right" };
  });
  // The path: dots every sample, a faint line through them broken where the
  // camera lost the rock for a while. Colour runs from the push-off (the
  // first sample clearly moving) to the last; the rock at rest is the first stop.
  const moving = pts.findIndex(p => p.yp > pts[0].yp + 0.15);
  const t0 = moving >= 0 ? pts[moving].t : pts.length ? pts[0].t : 0;
  const t1 = pts.length ? pts[pts.length - 1].t : 1;
  const dots = pts.map((p, i) => ({ ...pt(p.x, p.yp), fill: rampColor(moving < 0 || i < moving ? 0 : (p.t - t0) / Math.max(1e-6, t1 - t0)) }));
  const steps = pts.slice(1).map((p, i) => p.t - pts[i].t).sort((a, b) => a - b);
  const gapS = 2.5 * (steps.length ? steps[steps.length >> 1] : 0.1);
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
  return {
    w, h, side: !!side,
    stretch: Math.round(kAcross / kAlong),
    plot: { x: pad.l, y: pad.t, w: plotW, h: plotH },
    lines, labels, ticks, holds,
    aim: aim.length === 2 ? poly(aim) : null,
    aimLabel: aimEnd ? (side ? { x: r1(aimEnd.x - 3), y: r1(aimEnd.y - 4), text: "to the broom", anchor: "end" }
                               : { x: r1(aimEnd.x + (roomLeft ? -4 : 4)), y: r1(aimEnd.y + 10), text: "to the broom",
                                   anchor: roomLeft ? "end" : "start" }) : null,
    runs, dots,
    start: start && start.yp >= from && start.yp <= to ? pt(start.x, start.yp) : null,
    points: pts.length,
  };
}

/* One rock's delivery in the frame every rock can share: along, metres past
 * the throwing tee, as above; across, `dx`, metres off this rock's own line
 * from its foothold to its broom (+ to the thrower's right). Raw x cannot be
 * overlaid -- the brooms sit metres apart and the footholds 30 cm -- but off
 * its own aim line a perfect delivery is dx 0 all the way, whatever it was
 * aimed at. null when there is no delivery, or no broom or hack to aim from. */
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
  return { pts, start: start && start.dx != null ? start : null };
}

const OVERLAY_BIN_M = 0.25;
const OVERLAY_BIN_MIN = 3;               // rocks a bin needs before it has a median

/* Every rock of a group drawn in aimFrame's frame, upright, the thrower at
 * the bottom: one line per rock, the dot it sat at before the push, and the
 * median of them all where at least three rocks pass. `paths` are
 * [{key, pts, start, dim}] from aimFrame; `turn` names the sides, wide and
 * narrow, as the scatter beside it does. */
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
  // Every 10 cm off the aim line, labelled in cm.
  const ticks = [];
  for (let c = Math.ceil((x0 - 1e-9) / 0.1) * 0.1; c <= x0 + win.span + 1e-9; c += 0.1) {
    const v = Math.round(c * 100), q = pt(c, from);
    ticks.push({ x1: q.x, y1: r1(pad.t + plotH), x2: q.x, y2: r1(pad.t + plotH + 4),
                 label: `${v > 0 ? "+" : ""}${v}`, lx: q.x, ly: r1(pad.t + plotH + 14) });
  }
  const sides = sideNames(turn);
  const drawn = rocks.map(p => ({ key: p.key, d: poly(p.pts.map(q => [q.dx, q.yp])), dim: !!p.dim,
    start: p.start && p.start.yp >= from && p.start.yp <= to ? pt(p.start.dx, p.start.yp) : null }));
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
