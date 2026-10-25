/* A practice throw in a curler's words: where it stopped, its line, its
 * weight, which way it curled -- and which throws the page shows at all. Pure
 * functions of the throw records the API serves (service/practice.py), so the
 * page and its tests agree on every word. */

import { feetInches } from "./line.mjs";

const RINGS = {
  button: "Button", 4: "4-foot", 8: "8-foot", 12: "12-foot",
  short: "In front", long: "Behind", out: "Out of play",
};
// A miss this small is on the broom: under the detector's own accuracy.
const ON_BROOM_M = 0.0254;
// "On the broom" for the summary strip: within six inches.
const NEAR_BROOM_M = 0.1524;

export function ringWord(rest) {
  return rest ? RINGS[rest.ring] ?? null : null;
}

export function restText(rest) {
  if (!rest) return null;
  const ring = ringWord(rest);
  if (rest.ring === "out" || rest.to_tee_m == null) return ring;
  return `${ring} · ${feetInches(rest.to_tee_m)} from the tee`;
}

/* ``short`` is the throws table's: "5 in narrow", the broom left understood. */
export function lineText(line, { short = false } = {}) {
  if (!line) return null;
  if (line.miss_m != null) {
    if (Math.abs(line.miss_m) < ON_BROOM_M) return "On the broom";
    const off = `${feetInches(line.miss_m)} ${line.side ?? (line.miss_m < 0 ? "left" : "right")}`;
    return short ? off : `${off} of the broom`;
  }
  if (line.aim_x != null) {
    return `aimed ${feetInches(line.aim_x)} ${line.aim_x < 0 ? "left" : "right"} of centre`;
  }
  return null;
}

export function weightText(split) {
  return split == null ? null : `${split.toFixed(1)} s`;
}

export function curlText(curl) {
  return curl === "left" || curl === "right" ? `Curled ${curl}` : null;
}

/* In-turn or out-turn: the way it curled, read through the thrower's hand.
 * Seen from above, a rock turning clockwise curls to the thrower's right --
 * a right-hander's in-turn, a left-hander's out-turn. */
export function turnText(curl, hand) {
  if (curl !== "left" && curl !== "right") return null;
  return curl === (hand === "left" ? "left" : "right") ? "In" : "Out";
}

/* How fast it was set turning, in whole rpm: a range for a rock that showed
 * under half a turn in view (game/spintime.py), which is timed only that
 * closely. */
export function rpmText(spin) {
  if (typeof spin?.rpm !== "number") return null;
  const [lo, hi] = (spin.rpm_range ?? [spin.rpm, spin.rpm]).map(Math.round);
  return lo === hi ? `${lo} rpm` : `${lo}–${hi} rpm`;
}

/* The throws table's Turn: which turn and how fast, "In · 9 rpm", either
 * alone when the other was not read. */
export function rotationText(t, hand) {
  const parts = [turnText(t.curl, hand), rpmText(t.spin)].filter(Boolean);
  return parts.length ? parts.join(" · ") : null;
}

/* The throws to show: a stone that arrived with nothing seen releasing it
 * was not thrown -- on real practice footage, every one was a stone placed by
 * hand for a drill. A release that never arrived stays: that is a hog. */
export function shown(throws) {
  return (throws ?? []).filter(t => !(t.arrived && t.release_source == null));
}

export function summary(throws) {
  const mine = (throws ?? []).filter(t => !t.hidden);
  const splits = mine.map(t => t.split_s).filter(s => s != null);
  const misses = mine.map(t => t.line?.miss_m).filter(m => m != null);
  return {
    count: mine.length,
    weightMin: splits.length ? Math.min(...splits) : null,
    weightMax: splits.length ? Math.max(...splits) : null,
    onBroom: misses.filter(m => Math.abs(m) <= NEAR_BROOM_M).length,
    withBroom: misses.length,
  };
}

/* How long a session ran, for the list of sessions. */
export function lengthText(seconds) {
  const min = Math.floor(seconds / 60);
  if (min < 1) return "under a minute";
  if (min < 60) return `${min} min`;
  const h = Math.floor(min / 60), m = min % 60;
  return m ? `${h} h ${m} min` : `${h} h`;
}

/* What the card shows where a clip goes: "play" when the throw has one,
 * "wait" for the newest throw of a live session while its clip is still to be
 * cut (clip false: the worker cuts it a few seconds after the rock stops), else
 * nothing. null is a clip that is not coming; a throw with no clip field is
 * from before clips. A stone nobody released has no clip coming. */
export function clipState(t, { live, newest }) {
  if (t.clip) return "play";
  if (live && newest && t.clip === false && t.t_release_s != null) return "wait";
  return null;
}

// A signed link lasts an hour (CLIP_URL_S); one older than this is asked for again.
export const CLIP_LINK_MS = 50 * 60 * 1000;

export function linkFresh(entry, nowMs) {
  return !!entry && nowMs - entry.at < CLIP_LINK_MS;
}

/* The overlay entry for the piece of a clip playing at ``t`` seconds
 * (practice/overlay.py): the camera's broom and lines, or null. The last
 * frame belongs to the last piece. */
export function overlayAt(overlay, t) {
  if (!overlay || !overlay.length) return null;
  const hit = overlay.find(p => t >= p.t0 && t < p.t1);
  if (hit) return hit;
  const last = overlay[overlay.length - 1];
  return Math.abs(t - last.t1) < 1e-6 ? last : null;
}

/* How many of a piece's slide dots ([t, u, v, kind], in time order on the
 * clip's clock) the rock has reached at clip time ``t``. */
export function dotsShown(piece, t) {
  const dots = piece?.dots ?? [];
  let n = 0;
  while (n < dots.length && dots[n][0] <= t) n++;
  return n;
}

/* A person's own sensors' times for a throw (service/practice.py's
 * timing_view): "short 3.21 s · long 13.94 s", either left out when the app
 * did not send it. */
export function sensorText(s) {
  if (!s) return null;
  const parts = [];
  if (s.short_s != null) parts.push(`short ${s.short_s.toFixed(2)} s`);
  if (s.long_s != null) parts.push(`long ${s.long_s.toFixed(2)} s`);
  return parts.length ? parts.join(" · ") : null;
}

/* The throws table's rows in time order: each throw ({ throw, n }, n its
 * number on the page) and each sensor timing no throw paired with
 * ({ timing }), by when the throw was released or the timing crossed the hog
 * line. A timing goes after a throw at the same moment. */
export function mergeTimings(throws, timings) {
  const rows = (throws ?? []).map((t, i) => ({ throw: t, n: i + 1, at: Date.parse(t.released_at) }));
  for (const timing of timings ?? []) rows.push({ timing, at: Date.parse(timing.hog_at) });
  return rows
    .map((r, i) => ({ ...r, i }))
    .sort((a, b) => a.at - b.at || a.i - b.i)
    .map(({ at: _at, i: _i, ...r }) => r);
}
