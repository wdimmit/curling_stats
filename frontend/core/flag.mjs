/* What a flag says about where it was, and whether its note can be sent.
 *
 * Plain ESM with no React and no DOM, so the Python suite can run it under
 * bare node -- see tests/test_viewer_js.py. */
import { shotVideoTime } from "./shots.mjs";
import { endIdentity, identity, keyFor } from "./timeline.mjs";

export const NOTE_MAX = 2000;   // api.MAX_FLAG_NOTE
const VIDEO_LEAD_IN_S = 10;     // timeline.VIDEO_LEAD_IN_S

/* The rock the cursor is on, as a flag records it, and the line the dialog
 * shows. An end with no rocks is still somewhere to flag: its rock is null.
 * Identities are sent as text -- the detector's number unless a charter
 * renumbered -- so a flag still finds its rock after the display moves. */
export function flagPlace(view, ei, si) {
  const at = view.ends[ei];
  if (!at) return null;
  const e = at.end;
  const raw = at.raws[si] ?? null;
  const shot = at.shots[si] ?? null;
  const label = shot ? [shot.color, shot.position].filter(Boolean).join(", ") || null : null;
  const place = {
    game_index: view.game.index ?? null,
    end: e.number ?? null,
    end_id: String(endIdentity(e)),
    rock: shot ? shot.number : null,
    rock_id: raw ? String(identity(raw)) : null,
    key: raw ? keyFor(view.game, e, raw) : null,
    // A rock never seen arriving has no t_video_s; it still has the time the
    // viewer seeks to, and those are the rocks most likely to be flagged.
    t_video_s: typeof shot?.t_video_s === "number" ? shot.t_video_s
      : shotVideoTime(shot, VIDEO_LEAD_IN_S),
    label,
  };
  const text = `Game ${(view.gi ?? 0) + 1} · End ${place.end}`
    + (shot ? ` · Rock ${place.rock}${label ? ` (${label})` : ""}` : "");
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
