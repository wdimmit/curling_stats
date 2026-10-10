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

export function lineText(line) {
  if (!line) return null;
  if (line.miss_m != null) {
    if (Math.abs(line.miss_m) < ON_BROOM_M) return "On the broom";
    return `${feetInches(line.miss_m)} ${line.side ?? (line.miss_m < 0 ? "left" : "right")} of the broom`;
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
 * "wait" for the newest throw of a live session (the worker cuts a clip a few
 * seconds after the rock stops), else nothing. A stone nobody released has no
 * clip coming. */
export function clipState(t, { live, newest }) {
  if (t.clip) return "play";
  if (live && newest && t.t_release_s != null) return "wait";
  return null;
}

// A signed link lasts an hour (CLIP_URL_S); one older than this is asked for again.
export const CLIP_LINK_MS = 50 * 60 * 1000;

export function linkFresh(entry, nowMs) {
  return !!entry && nowMs - entry.at < CLIP_LINK_MS;
}
