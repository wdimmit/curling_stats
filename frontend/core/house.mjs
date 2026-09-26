/* Sheet geometry: where a stone may be put, and how much of the sheet to show.
 *
 * Metres throughout, matching the SVG's user space.
 */
import { LIMIT, R } from "./constants.mjs";
import { isBlank } from "./shots.mjs";

/* The phone cannot show the sheet's whole length beside the video, so it shows
 * the house centred on the tee and lets the guards fall off the bottom.
 * Centring is what keeps the 12-foot whole however short the band gets; a band
 * tall enough to reach past the back line starts just behind it instead.
 * `aspect` is the rendered box's width over its height; desktop passes nothing
 * that matters, because "full" is always the view it has always had. */
export function houseViewBox(mode, aspect) {
  // Everything in play, for watching on a phone: back line to hog line at
  // the sheet's full width, framed by the margin the sides already have. The
  // box is letterboxed rather than cropped, so its shape does not matter and
  // no stone in play ever falls out of view.
  if (mode === "inplay") {
    const m = 2.6 - R.halfWidth;
    const n = x => String(+x.toFixed(3));
    return `-2.6 ${n(R.back - m)} 5.2 ${n(R.hog - R.back + 2 * m)}`;
  }
  if (mode !== "crop" || !(aspect > 0)) return "-2.6 -2.6 5.2 8.6";
  // A box taller than the whole sheet is capped, and the result is then
  // letterboxed -- its centring is the box's, no longer the sheet's.
  const h = Math.min(5.2 / aspect, 8.6);
  // Centred until that would reach past the back of the ice; a taller box
  // spends the rest in front instead. Behind the back line it keeps the same
  // margin as the sides, so the ice is evenly framed and the dark band the
  // full view's 2.6 m left above it is gone. A stone past the back line is
  // cut off: those are rare, and the house is what this view is for.
  const y = Math.max(-h / 2, R.back - (2.6 - R.halfWidth));
  const n = x => String(+x.toFixed(3));
  return `-2.6 ${n(y)} 5.2 ${n(h)}`;
}

/* Whether the house band shows the crop rather than the whole sheet. Pure, and
 * separate from the drawing, because it is the branch this layout gets wrong:
 * a desktop's first, unmeasurable box once read as "short and wide", and a
 * cropped box carried across a rotation once re-derived its own crop forever.
 * `phone` is the stylesheet's gate, not merely a width -- the crop only makes
 * sense where the shell that shortens the band is actually in force. */
export function shouldCrop({ phone, editing, width, height }) {
  return !!phone && !editing && width > 0 && height > 0 && height < width * 1.3;
}

/* Where the skip's broom was held, and the stone this shot left, or null. The
 * field is absent on every chart from before schema 5 and null on a shot with
 * no pad held still; both draw nothing rather than a pad somewhere invented. */
export function broomMark(shot) {
  const b = shot?.target_broom;
  if (!b || typeof b.x !== "number" || typeof b.y !== "number") return null;
  const i = shot.delivered_stone_index;
  const st = Number.isInteger(i) ? shot.stones?.[i] : null;
  const to = st && typeof st.x === "number" && typeof st.y === "number"
    ? { x: st.x, y: st.y } : null;
  return { x: b.x, y: b.y, to };
}

// As game/shots.py MOVED_MIN_M: closer than this, a stone has not moved.
const GHOST_MATCH_M = 0.30;

/* Where the stones this shot disturbed sat before it: a moved stone with `to`,
 * where it went, and a stone knocked out of the view with `to` null. From
 * `house_delta`, which is the pipeline's diff and is not redone when the house
 * is edited by hand -- so an entry the current stones contradict is dropped: a
 * move whose stone is no longer where it went, a removal whose stone is still
 * there. Knock-ons and detection dropouts are in the diff too; they moved. */
export function ghostStones(shot) {
  const d = shot?.house_delta;
  if (!d || isBlank(shot)) return [];
  const num = (...v) => v.every(n => typeof n === "number" && Number.isFinite(n));
  const stones = (shot.stones ?? []).filter(s => num(s.x, s.y));
  const near = (color, x, y) =>
    stones.some(s => s.color === color && Math.hypot(s.x - x, s.y - y) < GHOST_MATCH_M);
  const moved = (d.moved ?? [])
    .filter(s => num(s.x, s.y, s.from_x, s.from_y) && near(s.color, s.x, s.y))
    .map(s => ({ color: s.color, x: s.from_x, y: s.from_y, to: { x: s.x, y: s.y } }));
  const removed = (d.removed ?? [])
    .filter(s => num(s.x, s.y) && !near(s.color, s.x, s.y))
    .map(s => ({ color: s.color, x: s.x, y: s.y, to: null }));
  return [...moved, ...removed];
}

export function stoneAt(x, y, color) {
  const d = Math.hypot(x, y);
  return { color, x, y, distance_to_tee: d, in_house: d <= R.inHouse,
           confidence: 1.0, source: "manual" };
}

export const onSheet = p => Math.abs(p.x) <= R.halfWidth && p.y >= LIMIT.yLo && p.y <= R.hog;
export const clampX = x => Math.max(-LIMIT.x, Math.min(LIMIT.x, x));
export const clampY = y => Math.max(LIMIT.yLo, Math.min(LIMIT.yHi, y));
