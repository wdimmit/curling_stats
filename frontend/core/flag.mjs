/* What a flag says about where it was, and whether its note can be sent.
 *
 * Plain ESM with no React and no DOM, so the Python suite can run it under
 * bare node -- see tests/test_viewer_js.py. */
import { NOTE_MAX, VIDEO_LEAD_IN_S } from "./constants.mjs";
import { FOURS, positionText } from "./format.mjs";
import { shotVideoTime } from "./shots.mjs";
import { cursor, endIdentity, identity } from "./timeline.mjs";

/* The rock the cursor is on, as a flag records it, and the line the dialog
 * shows. An end with no rocks is still somewhere to flag, and so is a game
 * with no ends -- nothing detected is the failure most worth reporting.
 * Identities are sent as text -- the detector's number unless a charter
 * renumbered -- so a flag still finds its rock after the display moves.
 *
 * The rock comes from `cursor()`, the editor's own reading of it, so the key
 * a flag records is the one an edit to that rock is saved under. `fmt` is the
 * chart's format, which only changes how the thrower reads: "red, player B"
 * in doubles, "red, lead" as ever in fours. */
export function flagPlace(view, ei, si, fmt = FOURS) {
  const { end: e, shot, raw, key } = cursor(view, ei, si);
  const label = shot
    ? [shot.color, positionText(shot.position, fmt)].filter(Boolean).join(", ") || null
    : null;
  const place = {
    game_index: view.game.index ?? null,
    end: e ? e.number ?? null : null,
    end_id: e ? String(endIdentity(e)) : null,
    rock: shot ? shot.number : null,
    rock_id: raw ? String(identity(raw)) : null,
    key,
    // The time the viewer seeks to: the release less the lead-in, as the
    // pipeline's t_video_s, and for a rock never seen arriving, its rest or
    // its guess -- those are the rocks most likely to be flagged.
    t_video_s: shotVideoTime(shot, VIDEO_LEAD_IN_S),
    label,
  };
  // A hosted page holds one game, whose number here would be 1 whichever game
  // of the video it is; the owner's list numbers games within the video. So
  // name the game only where the page has a choice of them.
  const game = view.doc?.games?.length > 1 ? `Game ${(place.game_index ?? view.gi) + 1} · ` : "";
  const text = !e ? "No ends in this game"
    : `${game}End ${place.end}` + (shot ? ` · Rock ${place.rock}${label ? ` (${label})` : ""}` : "");
  return { place, text };
}

/* Why this note cannot be sent, or null when it can. */
export function noteProblem(note) {
  const t = (note ?? "").trim();
  if (!t) return "Say what is wrong.";
  if (t.length > NOTE_MAX) return `Keep it under ${NOTE_MAX} characters.`;
  return null;
}

/* `p`'s value, or `fallback` if it rejects or has not settled within `ms`.
 * An unreachable sign-in is just another way to be anonymous. */
export function settleWithin(p, ms, fallback = null) {
  let timer;
  return Promise.race([
    Promise.resolve(p).catch(() => fallback),
    new Promise(res => { timer = setTimeout(() => res(fallback), ms); }),
  ]).finally(() => clearTimeout(timer));
}

/* The signed-in user once auth has actually decided, from an `onUser` like
 * site/auth.js's -- which calls back now and on every change with
 * (user, ready). Not whenReady(): that settles when the state listener is
 * registered, before the session is restored, and a signed-in person's first
 * flag then went anonymous. Unsubscribes once answered. */
export function settledUser(onUser) {
  return new Promise(resolve => {
    let off = null;
    let done = false;
    off = onUser((u, ready) => {
      if (!ready || done) return;
      done = true;
      if (off) off();
      resolve(u ?? null);
    });
    if (done) off();   // answered synchronously, before `off` existed
  });
}
