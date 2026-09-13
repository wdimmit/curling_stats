/* Sheet geometry: where a stone may be put, and how much of the sheet to show.
 *
 * Metres throughout, matching the SVG's user space.
 */
import { LIMIT, R } from "./constants.mjs";

/* The phone cannot show the sheet's whole length beside the video, so it shows
 * the house centred on the tee and lets the guards fall off the bottom.
 * Centring is what keeps the 12-foot whole however short the band gets.
 * `aspect` is the rendered box's width over its height; desktop passes nothing
 * that matters, because "full" is always the view it has always had. */
export function houseViewBox(mode, aspect) {
  if (mode !== "crop" || !(aspect > 0)) return "-2.6 -2.6 5.2 8.6";
  // A box taller than the whole sheet is capped, and the result is then
  // letterboxed -- its centring is the box's, no longer the sheet's.
  const h = Math.min(5.2 / aspect, 8.6);
  const n = x => String(+x.toFixed(3));
  return `-2.6 ${n(-h / 2)} 5.2 ${n(h)}`;
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

export function stoneAt(x, y, color) {
  const d = Math.hypot(x, y);
  return { color, x, y, distance_to_tee: d, in_house: d <= R.inHouse,
           confidence: 1.0, source: "manual" };
}

export const onSheet = p => Math.abs(p.x) <= R.halfWidth && p.y >= LIMIT.yLo && p.y <= R.hog;
export const clampX = x => Math.max(-LIMIT.x, Math.min(LIMIT.x, x));
export const clampY = y => Math.max(LIMIT.yLo, Math.min(LIMIT.yHi, y));
