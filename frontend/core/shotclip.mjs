/* A clip of the shot on screen, asked for from the ⋯ menu (api.py "shot
 * clips"): the rock's name for it, whether to offer it at all, and what the
 * dialog says while one is on its way.
 *
 * Plain ESM with no React and no DOM, so the Python suite can run it under
 * bare node -- see tests/test_viewer_js.py. */
import { cursor, endIdentity, identity } from "./timeline.mjs";
import { liveGame } from "./watch.mjs";

// How long a game's video is taken to be kept after it was analysed
// (service/gameclips.py SHOT_CLIP_DAYS). Past it, the menu has no Clip.
export const SHOT_CLIP_DAYS = 10;
const DAY_MS = 86_400_000;
// While one is on its way, the dialog asks after it this often.
export const SHOT_CLIP_POLL_MS = 2000;

/* The rock under the cursor as its clip is named, `<game>.<end>.<rock>` by
 * the numbers the run detected (gameclips.rock_name); null where there is no
 * rock, or one never seen, which has nothing to cut. */
export function shotClipName(view, ei, si) {
  const { end: e, shot, raw } = cursor(view, ei, si);
  const gi = view?.game?.index;
  if (!e || !shot || !raw || shot.missing || raw.missing || gi == null) return null;
  return `${gi}.${endIdentity(e)}.${identity(raw)}`;
}

/* Whether to offer a clip of this game's shots now: not while it is being
 * played (its video is in no cache until the stream ends), and not once its
 * video is past the days it is kept. */
export function shotClipsOffered(doc, game, nowMs) {
  if (liveGame(doc, game)) return false;
  const at = Date.parse(doc?.source?.analysed_at ?? "");
  return Number.isFinite(at) && nowMs - at <= SHOT_CLIP_DAYS * DAY_MS;
}

/* What the dialog says for the API's word on a clip, and whether to keep
 * asking. `busy` is true while one is on its way. */
export function shotClipStatus(got) {
  const s = got?.state;
  if (s === "queued") {
    const n = got.ahead ?? 0;
    return { busy: true, text: n ? `Waiting for the clips worker: ${n} ahead of this one…`
                                 : "Waiting for the clips worker…" };
  }
  if (s === "cutting") return { busy: true, text: "Cutting the clip…" };
  if (s === "ready") return { busy: false, text: null };
  if (s === "gone" || s === "expired")
    return { busy: false, text: "This game's video is no longer kept, so there is no clip of it." };
  if (s === "live") return { busy: false, text: "Clips are cut once the game's stream has ended." };
  if (s === "busy") return { busy: false, text: "This game is being analysed again. Try later." };
  if (s === "failed" && got.reason === "no_release")
    return { busy: false, text: "This rock was never seen released, so there is nothing to cut." };
  if (s === "failed") return { busy: false, text: "The clip could not be cut." };
  return { busy: false, text: null };
}
