/* What the Detail pane says about one rock, from schema 6's `line`: where its
 * thrown line passed the skip's broom, where it sat before the push, and where
 * the camera behind the thrower saw it go. Pure -- core/** may not touch the
 * DOM. See docs/superpowers/specs/2026-09-24-shot-line-detail-design.md. */
import { R, STRIPBOX } from "./constants.mjs";
import { isSplitEstimated } from "./stats.mjs";

export const LINE_SCHEMA = 6;
export const HOG_Y = 34.747 - 6.401;      // the throwing hog line, house metres
export const TEE_Y = 34.747;              // the throwing tee
export const HACK_Y = 34.747 + 3.658;     // the hack line
const ON_M = 0.10;                        // inside the measurement's own error
// A foothold's centre. WCF R1: each hack's inside edge is 76 mm from the
// centre line and a hack is at most 152 mm wide. There is no centre hack.
export const HACK_X_M = 0.152;

/* An offset as a curler says it: feet and inches, to the nearest inch. The
 * sign is the caller's business -- it says which side in words. */
export function feetInches(m) {
  const all = Math.round(Math.abs(m) * 100 / 2.54);
  const ft = Math.floor(all / 12), inch = all % 12;
  if (!ft) return `${inch} in`;
  return inch ? `${ft} ft ${inch} in` : `${ft} ft`;
}

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

const median = xs => {
  const s = [...xs].sort((a, b) => a - b), m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
};

/* Each player's hack for a game, keyed "color|slot": a player throws from one
 * hack all game. Where their stones sat before the push says which, but the
 * turn moves it too -- a rock that curls right is set 5-10 cm further to the
 * thrower's left than one that curls left, on either hack -- so each turn's
 * median counts once, however many of each the player threw. Dead centre is
 * the left hack, which nearly everyone uses (22 of 24 players, 2026-09-25). */
export function playerHacks(shots) {
  const by = new Map();
  for (const s of shots || []) {
    const x = s?.line?.start?.x;
    if (s?.missing || typeof x !== "number" || s.thrower_slot == null) continue;
    const k = `${s.color}|${s.thrower_slot}`;
    if (!by.has(k)) by.set(k, []);
    by.get(k).push({ x, curl: s.line.curl });
  }
  const out = {};
  for (const [k, rocks] of by) {
    const turn = c => rocks.filter(r => r.curl === c).map(r => r.x);
    const l = turn("left"), r = turn("right");
    const x = l.length && r.length ? (median(l) + median(r)) / 2 : median(rocks.map(q => q.x));
    out[k] = { side: x <= 0 ? "left" : "right", x };
  }
  return out;
}

/* This rock's hack: its player's, which buildGameView sets, else the side its
 * own stone sat on. */
export function hackOf(shot) {
  if (shot?.hack?.side) return shot.hack.side;
  const x = shot?.line?.start?.x;
  return typeof x === "number" ? (x <= 0 ? "left" : "right") : null;
}

/* The hack-to-broom line's x at depth y: from the foothold this rock was
 * thrown from to the skip's broom. */
export function hackAimX(shot, y) {
  const side = hackOf(shot), b = shot?.target_broom;
  if (!side || !b) return null;
  const hx = side === "left" ? -HACK_X_M : HACK_X_M;
  return hx + (b.x - hx) * (y - HACK_Y) / (b.y - HACK_Y);
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

/* Left or Right, with where this rock's own stone sat. */
function hackFig(shot) {
  const side = hackOf(shot);
  if (!side) return fig("hack", "Hack", "–", "not seen before the push");
  const x = shot?.line?.start?.x;
  const note = typeof x !== "number" ? "as on this player's other rocks"
    : Math.round(Math.abs(x) * 100 / 2.54) === 0 ? "stone set on the centre line"
    : `stone set ${feetInches(x)} ${x < 0 ? "left" : "right"} of centre`;
  return fig("hack", "Hack", side === "left" ? "Left" : "Right", note);
}

export function lineFigures(shot, doc) {
  const reason = lineReason(shot, doc);
  const predates = reason === "This chart predates line measurement";
  const l = reason ? null : shot.line;
  const weight = typeof shot?.long_split_s === "number"
    ? fig("weight", "Weight", `${shot.long_split_s.toFixed(1)} s`,
          isSplitEstimated(shot) ? "hog line to hog line, estimated" : "hog line to hog line")
    : fig("weight", "Weight", "–", "not timed");
  const rest = restOf(shot);
  const restFig = rest
    ? fig("rest", "Came to rest", zone(rest), `${Math.hypot(rest.x, rest.y).toFixed(1)} m from the button`)
    : fig("rest", "Came to rest", "–", "not matched to a stone");
  if (!l) {
    return { predates, reason, figures: [
      fig("broom", "At the broom", "–", reason),
      hackFig(shot), fig("hog", "At the hog line", "–", reason),
      weight, fig("curl", "Curl", "–", ""), restFig] };
  }
  const miss = l.at_broom.miss_m;
  const tick = l.confirmed === true ? "confirmed" : l.confirmed === false ? "disagrees" : "unseen";
  const tickNote = { confirmed: "confirmed from behind the thrower",
                     unseen: "not confirmed: hidden from behind the thrower",
                     disagrees: "the camera behind the thrower disagrees" }[tick];
  const broom = fig("broom", "At the broom",
                    Math.abs(miss) < ON_M ? "On the broom" : `${feetInches(miss)} ${sideWord(miss, l.curl)}`,
                    tickNote, { tick, dim: tick === "disagrees" });
  const hack = hackFig(shot);
  // The same two lines as At the broom, read at the hog line: the thrown line,
  // and the line from this rock's hack to the broom. Not the pipeline's
  // `offset_m`, which starts that line at the stone instead.
  const aim = hackAimX(shot, HOG_Y);
  const off = aim == null || typeof l.at_hog?.x !== "number" ? null : l.at_hog.x - aim;
  const hog = off == null ? fig("hog", "At the hog line", "–", "needs the hack")
    : fig("hog", "At the hog line",
          Math.abs(off) < ON_M ? "On the line" : `${feetInches(off)} ${sideWord(off, l.curl)}`,
          "of the hack-to-broom line");
  const end = rest ?? (l.path?.length ? { x: l.path[l.path.length - 1][1], y: l.path[l.path.length - 1][0] } : null);
  const lx = end ? lineX(shot, end.y) : null;
  const curl = end && lx != null
    ? fig("curl", "Curl", feetInches(end.x - lx), "from its line to where it stopped")
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

/* No tab when none is given: only the phone's watching layout has tabs, and a
 * link made anywhere else should open on the recipient's own last tab rather
 * than on one its sender never saw. */
export function formatHash({ tab, g, e, s }) {
  const parts = tab ? [`tab=${tab}`] : [];
  if (g && g > 1) parts.push(`g=${g}`);
  parts.push(`e=${e}`, `s=${s}`);
  return `#${parts.join("&")}`;
}

/* A URL carrying this hash instead of whatever fragment it had. */
export function withHash(url, hash) {
  const bare = String(url ?? "").split("#")[0];
  const h = String(hash ?? "").replace(/^#/, "");
  return h ? `${bare}#${h}` : bare;
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

/* The Detail strip in pixels, from house metres. null only without a shot:
 * spec §5 wants the sheet, the other stones and the broom drawn even when
 * there is no line to put on it, so the pane never goes blank just because
 * one camera missed one rock. */
export function stripGeometry(shot, box = STRIPBOX) {
  if (shot == null) return null;
  const l = shot.line, b = shot.target_broom;
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
  const rest = restOf(shot);
  const sheet = {
    w, h, rings,
    hogs: [+py(R.hog).toFixed(1), +py(HOG_Y).toFixed(1)],
    tees: [+py(0).toFixed(1), +py(TEE_Y).toFixed(1)],
    backs: [+py(R.back).toFixed(1), +py(TEE_Y - R.back).toFixed(1)],
    hack: +py(HACK_Y).toFixed(1),
    stones,
    broom: b ? pt(b.x, b.y) : null,
    rest: rest ? pt(rest.x, rest.y) : null,
  };
  // The line parts all need the broom too: an aim line, an extension or a
  // miss distance all measure against it, and there is nothing to measure
  // without one.
  if (!l || !b) return { ...sheet, aim: null, thrown: null, ext: null, path: null, start: null, miss: null };
  const hp = (l.hog_path || []).map(([y, x]) => [x, y]);
  const lastY = hp.length ? hp[hp.length - 1][1] : HOG_Y - 3.6;
  const miss = l.at_broom.miss_m;
  const side = hackOf(shot);
  return {
    ...sheet,
    // The intended line runs from the hack, as the hog-line figure measures it.
    aim: side ? pts([[side === "left" ? -HACK_X_M : HACK_X_M, HACK_Y], [b.x, b.y]]) : null,
    thrown: pts(hp),
    ext: pts([[lineX(shot, lastY), lastY], [lineX(shot, b.y), b.y]]),
    path: l.path?.length >= 2 ? pts(l.path.map(([y, x]) => [x, y])) : null,
    start: l.start ? pt(l.start.x, l.start.y) : null,
    miss: Math.abs(miss) < ON_M ? null
      : { x1: +px(b.x).toFixed(1), x2: +px(l.at_broom.x).toFixed(1), y: +(py(b.y) - 7).toFixed(1), label: feetInches(miss) },
  };
}

/* stripGeometry's output as explicit shapes, so one renderer draws the strip
 * upright (the phone) or on its side (the desktop). Numbers and point strings
 * pass through untouched: the phone's markup must not change by a character. */
export function stripShapes(g) {
  if (!g) return null;
  const across = (y, kind) => ({ x1: 0, y1: y, x2: g.w, y2: y, kind });
  return {
    w: g.w, h: g.h,
    rings: g.rings.map(r => ({ cx: g.w / 2, cy: r.cy, rx: r.rx, ry: r.ry, kind: r.kind })),
    lines: [...g.hogs.map(y => across(y, "hog")), ...g.tees.map(y => across(y, "tee")),
            ...g.backs.map(y => across(y, "back")), across(g.hack, "hack"),
            { x1: g.w / 2, y1: 0, x2: g.w / 2, y2: g.h, kind: "centre" }],
    stones: g.stones.map(s => ({ x: s.cx, y: s.cy, color: s.color })),
    aim: g.aim, thrown: g.thrown, ext: g.ext, path: g.path,
    broom: g.broom ? { x: g.broom.x - 2, y: g.broom.y - 5, w: 4, h: 10 } : null,
    rest: g.rest, start: g.start,
    miss: g.miss ? { x1: g.miss.x1, y1: g.miss.y, x2: g.miss.x2, y2: g.miss.y, label: g.miss.label,
                     tx: (g.miss.x1 + g.miss.x2) / 2, ty: g.miss.y - 4, anchor: "middle", size: 9 } : null,
  };
}

const r1 = v => +(+v).toFixed(1);

/* A quarter turn, (x, y) -> (length - y, x): the hack at the left and the
 * thrower's left along the top. A rotation, not a mirror, so wide and narrow
 * still mean what they say. */
export function sideways(s) {
  if (!s) return null;
  const S = s.h, H = s.w;
  const t = (x, y) => [r1(S - y), r1(x)];
  const pt = p => { const [x, y] = t(p.x, p.y); return { ...p, x, y }; };
  // stripGeometry gives an empty string, not null, when there is nothing to
  // draw (an empty hog_path, say); either one means "nothing here".
  const pts = str => (str == null || str === "") ? null
    : str.split(" ").map(q => t(...q.split(",").map(Number)).join(",")).join(" ");
  let broom = null;
  if (s.broom) {
    const [cx, cy] = t(s.broom.x + s.broom.w / 2, s.broom.y + s.broom.h / 2);
    broom = { x: r1(cx - s.broom.h / 2), y: r1(cy - s.broom.w / 2), w: s.broom.h, h: s.broom.w };
  }
  let miss = null;
  if (s.miss) {
    const [x1, y1] = t(s.miss.x1, s.miss.y1), [x2, y2] = t(s.miss.x2, s.miss.y2);
    // The far house is ~30 px from the right edge, so the label cannot go
    // right of the bracket. It goes left of the broom marker (which spans
    // x1-12 .. x1-2), at the bracket's middle, haloed in ice by the renderer.
    miss = { x1, y1, x2, y2, label: s.miss.label, anchor: "end", size: 11, halo: true,
             tx: r1(x1 - 16), ty: r1(Math.min(Math.max((y1 + y2) / 2 + 4, 11), H - 3)) };
  }
  return {
    w: S, h: H,
    rings: s.rings.map(r => { const [cx, cy] = t(r.cx, r.cy); return { ...r, cx, cy, rx: r.ry, ry: r.rx }; }),
    lines: s.lines.map(l => {
      const [x1, y1] = t(l.x1, l.y1), [x2, y2] = t(l.x2, l.y2);
      return { ...l, x1, y1, x2, y2 };
    }),
    stones: s.stones.map(pt),
    aim: pts(s.aim), thrown: pts(s.thrown), ext: pts(s.ext), path: pts(s.path),
    broom, rest: s.rest && pt(s.rest), start: s.start && pt(s.start), miss,
  };
}
