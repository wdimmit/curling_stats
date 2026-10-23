/* What a phone shows someone watching a game, rather than charting one.
 *
 * The watching surface is a list of an end's sixteen rocks, and each row
 * carries its own thinking-time bar -- so the list is the per-rock chart,
 * turned on its side and stripped of its axes. That is why this returns a
 * fraction per row rather than pixels: the row draws it as a width, and
 * nothing here needs to know how wide a phone is.
 */
import { TYPE } from "./constants.mjs";
import { isBlank, shotVideoTime, typeOf } from "./shots.mjs";
import { clockText, splitText } from "./stats.mjs";
import { boardReadable } from "./wire.mjs";

/* The rows of one end.
 *
 * `frac` is this rock's interval over the end's longest, so the bars are
 * comparable within an end and never across one -- an end played under time
 * pressure should not look quick beside a leisurely one.
 *
 * A rock with no interval is not a rock that took no time: the first rock of
 * every end is never timed (there is no previous rest to measure from), and a
 * delivery the throwing camera missed can lose its tee crossing. Those get
 * `unmeasured` and no bar at all, rather than a zero-width one that reads as
 * "instant".
 */
export function rockRows(view, ei, leadIn = 0) {
  const row = view.ends[ei];
  if (!row) return [];
  const shots = row.shots;
  const secs = shots.map(s => (typeof s.thinking_time_s === "number" &&
                               s.thinking_time_s > 0 ? s.thinking_time_s : null));
  const longest = Math.max(0, ...secs.filter(v => v != null));
  return shots.map((s, i) => {
    const t = typeOf(s);
    return {
      i,
      number: s.number,
      color: s.color,
      type: t,
      // A retired id still names itself rather than vanishing: the picker no
      // longer offers it, but a chart that used it is still readable.
      name: TYPE[t]?.name || t,
      label: s.label || null,
      position: s.position || null,
      secs: secs[i],
      text: secs[i] == null ? "—" : clockText(secs[i]),
      estimated: !!s.t_tee_estimated,
      unmeasured: secs[i] == null,
      frac: secs[i] == null || longest <= 0 ? 0 : secs[i] / longest,
      // The desktop panel's own words, estimate and all. A rock with no split
      // is null rather than a dash: every hogged rock would otherwise carry one.
      splitText: typeof s.long_split_s === "number" ? splitText(s) : null,
      blank: !!isBlank(s),
      tRest: typeof s.t_rest_s === "number" ? s.t_rest_s : null,
      tVideo: shotVideoTime(s, leadIn),
      stones: s.stones || [],
    };
  });
}

/* The rows the playhead could be "in" at time t, as [lo, hi]: lo is the row
 * after the last rest passed, and a rock never seen has no rest to end its
 * interval, so it shares that interval with the next rock that was seen (hi).
 * While following, either is right.
 */
export function rockSpan(rows, t) {
  if (!rows.length || typeof t !== "number") return null;
  let lo = 0;
  for (let i = 0; i < rows.length; i++) {
    if (rows[i].tRest == null) continue;
    if (t > rows[i].tRest) lo = Math.min(i + 1, rows.length - 1);
  }
  let hi = lo;
  while (hi < rows.length - 1 && rows[hi].tRest == null) hi++;
  return [lo, hi];
}

/* Which row the playhead is in, for a list that follows the video.
 *
 * A rock "is" the current one from the moment the previous rock stopped until
 * it stops itself, which is the interval a viewer is actually watching -- the
 * throw, not the aftermath. Before the first rest, that is rock one. Rows
 * without a rest time cannot bound anything, so they are skipped rather than
 * treated as zero, which would drag the answer back to the top of the end.
 */
export function rockAt(rows, t) {
  const span = rockSpan(rows, t);
  return span && span[1];
}

/* Which end the playhead is in, for following across ends.
 *
 * An end starts a little before its first rock seen is thrown -- the gap
 * after an end's last rest is cleanup and walking, and the house the end
 * finished on is what a viewer wants up through it. The slack is so a seek
 * to that first rock, which can land a frame or two short, still counts as
 * in the end it was sent to; it never reaches back past the previous end's
 * last rest. An end with no times at all bounds nothing and is skipped.
 */
export const END_LEAD_S = 5;

export function endAt(view, t, leadIn = 0) {
  if (typeof t !== "number") return null;
  let at = 0, lastRest = null;
  (view?.ends ?? []).forEach((_, ei) => {
    const rows = rockRows(view, ei, leadIn);
    const first = rows.find(r => typeof r.tVideo === "number")?.tVideo
      ?? rows.find(r => r.tRest != null)?.tRest;
    if (first == null) return;
    const start = lastRest == null ? first - END_LEAD_S
      : Math.max(lastRest, first - END_LEAD_S);
    if (ei > 0 && t >= start) at = ei;
    const rests = rows.map(r => r.tRest).filter(v => v != null);
    if (rests.length) lastRest = Math.max(...rests);
  });
  return at;
}

/* Pause after each rock: whether playback from `from` to `to` passed a point
 * PAUSE_AFTER_REST_S after some rock in this game came to rest. The wait is
 * for whatever it hit to stop too.
 *
 * Only ordinary playback counts -- a step forward of at most a few seconds.
 * A seek across a rest is somebody going somewhere, not a rock finishing,
 * and pausing them there would be a surprise.
 */
export const PAUSE_AFTER_REST_S = 2;
const PLAYED_STEP_S = 3;

export function restPassed(view, from, to) {
  if (typeof from !== "number" || typeof to !== "number") return false;
  if (to <= from || to - from > PLAYED_STEP_S) return false;
  return (view?.ends ?? []).some(({ shots }) => shots.some(s => {
    if (typeof s.t_rest_s !== "number") return false;
    const at = s.t_rest_s + PAUSE_AFTER_REST_S;
    return from < at && at <= to;
  }));
}

/* The end switcher's line: whose end it is so far, and who throws last. The
 * running score is the board's -- the one the end closed on, which is what a
 * viewer reading down the game wants -- and an end the board never reached
 * has none: `null`, not the zero that `end.score || {...}` used to hand back,
 * which turned "the board never said" into "nobody scored".
 *
 * A document from before schema 4 kept the detector's inferred score in
 * these same fields, so it is gated the same way regardless of what the end
 * itself carries: no score for a chart the board never had a say in.
 *
 * `scoresWithheld` is the other reason a score can be missing here, and it
 * is not "not posted": the board was read fine, but analyze.py could not
 * tell which detected end the game's first one is (a leading practice block
 * it cannot rule out), so every score came back off rather than risk one
 * attached to the wrong end. `game.scoreboard` carries that verdict for the
 * whole game, not per end, so it applies to every end alike while it holds. */
/* A game still being played: its stream is live and this game has not ended.
 * The timeline says so in `live` (the run) and `in_progress` (the game); a
 * recording's timeline has neither, and is never live. */
export function liveGame(doc, game) {
  return !!doc?.live?.in_progress && game?.in_progress !== false;
}

/* Which of two live documents to show: the newer, unless it is the same one
 * again, or a live run that restarted and is rebuilding from its first end --
 * showing fewer ends would take viewers back through the game, so the page
 * keeps what it has until the rebuild catches up. The final document always
 * wins. */
export function acceptLiveDoc(prev, next) {
  const ends = d => (d?.games || []).reduce((n, g) => n + (g.ends?.length || 0), 0);
  if (next?.live?.updated_at === prev?.live?.updated_at) return prev;
  if (next?.live?.in_progress && ends(next) < ends(prev)) return prev;
  return next;
}

/* The board's unread ends, worded for the state of the game: while it is still
 * being played the club simply has not hung those cards yet. */
export function unreadNote(unread, live) {
  if (!unread?.length) return null;
  return `ends ${unread.join(", ")} ${live ? "not posted yet" : "were never posted"}`;
}

export function endSummary(view, ei) {
  const end = view.ends[ei]?.end;
  if (!end) return null;
  const readable = boardReadable(view.doc);
  return {
    gameLive: liveGame(view.doc, view.game),
    number: end.number,
    of: view.ends.length,
    hammer: end.hammer || null,
    score: readable ? (end.score ?? null) : null,
    running: readable ? (end.running ?? null) : null,
    // Whether the running score leans on a score somebody typed in.
    entered: readable && view.ends.slice(0, ei + 1).some(x => x.end.score_source === "entered"),
    boardReadable: readable,
    scoresWithheld: readable && !!view.game.scoreboard?.scores_withheld,
    red: clockText(end.thinking_time?.red),
    yellow: clockText(end.thinking_time?.yellow),
    // Doubles: the placement names the hammer and any power play (the house
    // stone's team calls it), and a team may have swapped roles this end.
    hammerSource: end.hammer_source || null,
    powerPlay: end.placement?.power_play
      ? { color: end.placement.hammer, side: end.placement.power_play } : null,
    swapped: view.ends[ei]?.swapped || {},
  };
}
