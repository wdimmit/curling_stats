/* The house on screen as a picture to keep, saved from the ⋯ menu: what its
 * caption says and what the file is called. The drawing is
 * runtime/houseImage.mjs.
 *
 * Plain ESM with no React and no DOM, so the Python suite can run it under
 * bare node -- see tests/test_viewer_js.py. */
import { flagPlace } from "./flag.mjs";
import { typedTeamNames } from "./report.mjs";

/* Two lines over the house: who played, then where in the game this is, as
 * the flag dialog names it ("End 4 · Rock 7 (red, lead)"). A game nobody has
 * named has no first line -- "Red v Yellow" says nothing the stones do not. */
export function houseImageCaption(view, ei, si) {
  const t = typedTeamNames(view.game);
  const title = t.red || t.yellow ? `${t.red || "Red"} v ${t.yellow || "Yellow"}` : null;
  return { title, place: flagPlace(view, ei, si, view.format).text };
}

/* "house-end4-rock7.png", with the game where the page holds more than one,
 * as the caption does. */
export function houseImageName(view, ei, si) {
  const { place } = flagPlace(view, ei, si, view.format);
  const parts = ["house"];
  if (view.doc?.games?.length > 1) parts.push(`game${(place.game_index ?? view.gi) + 1}`);
  if (place.end != null) parts.push(`end${place.end}`);
  if (place.rock != null) parts.push(`rock${place.rock}`);
  return `${parts.join("-")}.png`;
}
