/* What the Detail pane says about one rock, from schema 6's `line`: where its
 * thrown line passed the skip's broom, where it sat before the push, and where
 * the camera behind the thrower saw it go. Pure -- core/** may not touch the
 * DOM. See docs/superpowers/specs/2026-09-24-shot-line-detail-design.md. */
import { R, STRIPBOX } from "./constants.mjs";

export const LINE_SCHEMA = 6;
export const HOG_Y = 34.747 - 6.401;      // the throwing hog line, house metres
export const TEE_Y = 34.747;              // the throwing tee
export const HACK_Y = 34.747 + 3.658;     // the hack line
const ON_M = 0.10;                        // inside the measurement's own error
const HACK_CENTRE_M = 0.08;

const cm5 = m => 5 * Math.round((Math.abs(m) * 100) / 5);

/* Where this rock came to rest, when the house says which stone it was. */
export function restOf(shot) {
  const i = shot?.delivered_stone_index;
  const s = Number.isInteger(i) ? shot?.stones?.[i] : null;
  return s && typeof s.x === "number" && typeof s.y === "number" ? { x: s.x, y: s.y } : null;
}

/* The thrown line's x at depth y, through the two points the timeline gives. */
export function lineX(shot, y) {
  const l = shot?.line, b = shot?.target_broom;
  if (!l || !b || l.at_hog?.x == null || l.at_broom?.x == null) return null;
  const k = (l.at_broom.x - l.at_hog.x) / (b.y - HOG_Y);
  return l.at_hog.x + k * (y - HOG_Y);
}

/* Wide is the side away from the curl, narrow the side it curls toward; with
 * no curl direction there is only left and right. */
function sideWord(v, curl) {
  if (curl === "left" || curl === "right") {
    const toward = curl === "right" ? 1 : -1;
    return Math.sign(v) === -toward ? "wide" : "narrow";
  }
  return v > 0 ? "right" : "left";
}

function zone(p) {
  const d = Math.hypot(p.x, p.y);
  if (d <= R.button + R.stone) return "Button";
  if (d <= R.four + R.stone) return "4-foot";
  if (d <= R.eight + R.stone) return "8-foot";
  if (d <= R.inHouse) return "12-foot";
  return p.y > 0 ? "In front" : "Behind";
}

export function lineReason(shot, doc) {
  // Fail closed: undefined < 6 is false, so undated charts would pass through.
  // See boardReadable in wire.mjs for the same convention.
  if (!(Number(doc?.schema_version) >= LINE_SCHEMA)) return "This chart predates line measurement";
  if (shot?.missing) return "This rock was never seen";
  if (!shot?.target_broom) return "No broom was held still before the release";
  if (!shot?.line) return "The hog-line camera lost this rock";
  return null;
}

const fig = (key, label, value, note, extra = {}) =>
  ({ key, label, value, note, tick: null, dim: false, ...extra });

export function lineFigures(shot, doc) {
  const reason = lineReason(shot, doc);
  const predates = reason === "This chart predates line measurement";
  const l = reason ? null : shot.line;
  const weight = typeof shot?.long_split_s === "number"
    ? fig("weight", "Weight", `${shot.long_split_s.toFixed(1)} s`, "hog line to hog line")
    : fig("weight", "Weight", "–", "not timed");
  const rest = restOf(shot);
  const restFig = rest
    ? fig("rest", "Came to rest", zone(rest), `${Math.hypot(rest.x, rest.y).toFixed(1)} m from the button`)
    : fig("rest", "Came to rest", "–", "not matched to a stone");
  if (!l) {
    return { predates, reason, figures: [
      fig("broom", "At the broom", "–", reason),
      fig("hack", "Hack", "–", ""), fig("hog", "At the hog line", "–", reason),
      weight, fig("curl", "Curl", "–", ""), restFig] };
  }
  const miss = l.at_broom.miss_m;
  const tick = l.confirmed === true ? "confirmed" : l.confirmed === false ? "disagrees" : "unseen";
  const tickNote = { confirmed: "confirmed from behind the thrower",
                     unseen: "not confirmed: hidden from behind the thrower",
                     disagrees: "the camera behind the thrower disagrees" }[tick];
  const broom = fig("broom", "At the broom",
                    Math.abs(miss) < ON_M ? "On the broom" : `${cm5(miss)} cm ${sideWord(miss, l.curl)}`,
                    tickNote, { tick, dim: tick === "disagrees" });
  let hack = fig("hack", "Hack", "–", "not seen before the push");
  if (l.start) {
    const x = l.start.x;
    hack = Math.abs(x) <= HACK_CENTRE_M
      ? fig("hack", "Hack", "Centre", "stone set on the centre line")
      : fig("hack", "Hack", x < 0 ? "Left" : "Right",
            `stone set ${Math.round(Math.abs(x) * 100)} cm ${x < 0 ? "left" : "right"} of centre`);
  }
  const off = l.at_hog?.offset_m;
  const hog = off == null ? fig("hog", "At the hog line", "–", "needs the hack")
    : fig("hog", "At the hog line",
          Math.abs(off) < ON_M ? "On the line" : `${cm5(off)} cm ${sideWord(off, l.curl)}`,
          "of the hack-to-broom line");
  const end = rest ?? (l.path?.length ? { x: l.path[l.path.length - 1][1], y: l.path[l.path.length - 1][0] } : null);
  const lx = end ? lineX(shot, end.y) : null;
  const curl = end && lx != null
    ? fig("curl", "Curl", `${Math.abs(end.x - lx).toFixed(1)} m`, "from its line to where it stopped")
    : fig("curl", "Curl", "–", "no rest position");
  return { predates, reason, figures: [broom, hack, hog, weight, curl, restFig] };
}

/* The House tab's caption. */
export function houseCaption(shot) {
  const rest = restOf(shot);
  if (!rest) return null;
  const z = zone(rest);
  const where = z === "Button" ? "on the button" : z === "In front" ? "in front of the house"
    : z === "Behind" ? "behind the tee" : `in the ${z}`;
  return `Stopped ${Math.hypot(rest.x, rest.y).toFixed(1)} m from the button, ${where}`;
}

const SWIPE_MIN_PX = 50;
const SWIPE_EDGE_PX = 20;          // the browser's own back gesture lives here

/* A drag as a rock step: left for the next, right for the one before. */
export function swipeStep(dx, dy, x0) {
  if (x0 < SWIPE_EDGE_PX) return 0;
  if (Math.abs(dx) < SWIPE_MIN_PX || Math.abs(dx) <= 1.5 * Math.abs(dy)) return 0;
  return dx < 0 ? 1 : -1;
}

/* The rock `d` steps from (ei, si), across ends and over empty ones; null
 * past either end of the game. */
export function stepRock(view, ei, si, d) {
  const ends = view?.ends ?? [];
  let e = ei, s = si + d;
  while (e >= 0 && e < ends.length) {
    const n = ends[e].shots.length;
    if (s >= 0 && s < n) return { ei: e, si: s };
    if (s < 0) { e -= 1; if (e >= 0) s = ends[e].shots.length - 1; }
    else { e += 1; s = 0; }
  }
  return null;
}

const TABS = ["house", "detail", "timing"];

export function parseHash(hash) {
  const out = {};
  for (const part of String(hash || "").replace(/^#/, "").split("&")) {
    const [k, v] = part.split("=");
    if (k === "tab" && TABS.includes(v)) out.tab = v;
    else if ((k === "g" || k === "e" || k === "s") && /^\d+$/.test(v ?? "")) out[k] = Number(v);
  }
  return out;
}

export function formatHash({ tab, g, e, s }) {
  const parts = [`tab=${tab}`];
  if (g && g > 1) parts.push(`g=${g}`);
  parts.push(`e=${e}`, `s=${s}`);
  return `#${parts.join("&")}`;
}

/* A parsed hash to a cursor: game by position (1-based), end and rock by
 * their numbers as the view now has them. null when any of it is gone. */
export function cursorFromHash(parsed, viewOf, gameCount) {
  if (parsed?.e == null || parsed?.s == null) return null;
  const gi = parsed.g ? parsed.g - 1 : 0;
  if (gi < 0 || gi >= gameCount) return null;
  const view = viewOf(gi);
  const ei = view.ends.findIndex(x => x.end?.number === parsed.e);
  if (ei < 0) return null;
  const si = view.ends[ei].shots.findIndex(x => x.number === parsed.s);
  return si < 0 ? null : { gi, ei, si };
}

/* What House draws as the rock's path, [x, y] in house metres: the camera
 * behind the thrower's where there is one, else the overhead panel's. */
export function trackPoints(shot) {
  const p = shot?.line?.path;
  if (Array.isArray(p) && p.length >= 2) return p.map(([y, x]) => [x, y]);
  const t = shot?.track;
  return Array.isArray(t) && t.length >= 2 ? t.map(q => [q[1], q[2]]) : [];
}

/* The Detail strip in pixels, from house metres. null without a line. */
export function stripGeometry(shot, box = STRIPBOX) {
  const l = shot?.line, b = shot?.target_broom;
  if (!l || !b) return null;
  const { w, h, y0, y1, half } = box;
  const kx = w / (2 * half), ky = h / (y1 - y0);
  const px = x => w / 2 + x * kx, py = y => (y - y0) * ky;
  const pt = (x, y) => ({ x: +px(x).toFixed(1), y: +py(y).toFixed(1) });
  const pts = list => list.map(([x, y]) => `${px(x).toFixed(1)},${py(y).toFixed(1)}`).join(" ");
  const rings = [0, TEE_Y].flatMap(ty => [[R.twelve, "twelve"], [R.eight, "eight"], [R.four, "four"], [R.button, "button"]]
    .map(([r, kind]) => ({ cy: +py(ty).toFixed(1), rx: +(r * kx).toFixed(1), ry: +(r * ky).toFixed(1), kind })));
  const own = shot.delivered_stone_index;
  const stones = (shot.stones || []).filter((_, i) => i !== own)
    .map(s => ({ cx: +px(s.x).toFixed(1), cy: +py(s.y).toFixed(1), color: s.color }));
  const hp = (l.hog_path || []).map(([y, x]) => [x, y]);
  const lastY = hp.length ? hp[hp.length - 1][1] : HOG_Y - 3.6;
  const miss = l.at_broom.miss_m;
  const rest = restOf(shot);
  return {
    w, h, rings,
    hogs: [+py(R.hog).toFixed(1), +py(HOG_Y).toFixed(1)],
    tees: [+py(0).toFixed(1), +py(TEE_Y).toFixed(1)],
    backs: [+py(R.back).toFixed(1), +py(TEE_Y - R.back).toFixed(1)],
    hack: +py(HACK_Y).toFixed(1),
    stones,
    aim: l.start ? pts([[l.start.x, l.start.y], [b.x, b.y]]) : null,
    thrown: pts(hp),
    ext: pts([[lineX(shot, lastY), lastY], [lineX(shot, b.y), b.y]]),
    path: l.path?.length >= 2 ? pts(l.path.map(([y, x]) => [x, y])) : null,
    broom: pt(b.x, b.y),
    rest: rest ? pt(rest.x, rest.y) : null,
    start: l.start ? pt(l.start.x, l.start.y) : null,
    miss: Math.abs(miss) < ON_M ? null
      : { x1: +px(b.x).toFixed(1), x2: +px(l.at_broom.x).toFixed(1), y: +(py(b.y) - 7).toFixed(1), label: `${cm5(miss)} cm` },
  };
}
