/* The numbers the report and the clock panel are read off.
 *
 * Everything takes the game view built by timeline.buildGameView, so one
 * relayout serves the report, the clock, the queue and the strip.
 */
import { POSITIONS, TYPE } from "./constants.mjs";
import { isGraded, typeOf } from "./shots.mjs";

/* mm:ss, because a thinking-time budget is quoted in minutes. */
export function clockText(seconds) {
  if (seconds == null) return "—";
  const t = Math.max(0, Math.round(seconds));
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")}`;
}

/* The clock, marked when it rests on an assumed tee crossing rather than a
 * seen one -- the throwing camera missed that delivery, so the interval is the
 * typical throw-to-arrival lag taken off the arrival. Worth showing (a shot
 * with no number silently shortens its team's total) and worth marking. */
export function thinkText(s) {
  if (s?.thinking_time_s == null) return "—";
  return clockText(s.thinking_time_s) + (s.t_tee_estimated ? " (est.)" : "");
}

/* A split is only meaningful next to how much of it was actually seen: the
 * throwing end is reached by carrying the slide the last stretch to the hog
 * line, and a shot the camera lost early says so rather than looking exact. */
export function splitText(s) {
  if (s?.long_split_s == null) return "—";
  const extra = s.long_split_extrapolated_m;
  const note = extra > 0.05 ? ` (${extra.toFixed(1)} m est.)` : "";
  return `${s.long_split_s.toFixed(1)} s${note}`;
}

/* Per team: the clock, and how much of the game it was read from. Ends carry
 * the totals already, so this is a sum rather than a re-derivation. */
export function gatherThinking(view) {
  const out = { red: 0, yellow: 0, measured: 0, unmeasured: 0, estimated: 0 };
  for (const { end } of view.ends) {
    const t = end.thinking_time;
    if (!t) continue;
    out.red += t.red || 0;
    out.yellow += t.yellow || 0;
    out.measured += t.measured_shots || 0;
    out.unmeasured += t.unmeasured_shots || 0;
    out.estimated += t.estimated_shots || 0;
  }
  return out;
}

/* Each team's clock as the game goes on, rock by rock.
 *
 * Cumulative rather than per shot. A per-shot series at sixteen rocks an end is
 * mostly noise -- one long discussion looks like a trend -- whereas what a
 * coach is actually after is the shape: which team is drawing ahead on the
 * clock, and the end where it started. A team's line only steps on its own
 * rocks and is flat through the other team's, so the gap between the lines at
 * any point is the difference in what they have spent so far.
 *
 * An interval nobody could read adds nothing, which makes both lines lower
 * bounds; the count that says how much was read is right above the chart. */
export function cumulativeThinking(view) {
  const points = [{ i: 0, red: 0, yellow: 0, estimated: false }];
  const bounds = [];
  let i = 0, red = 0, yellow = 0;
  view.ends.forEach(({ end, shots }, ei) => {
    shots.forEach((s, k) => {
      i++;
      const secs = s.thinking_time_s;
      if (secs != null && s.color === "red") red += secs;
      else if (secs != null && s.color === "yellow") yellow += secs;
      // The interval itself travels with the running total, so the bars and
      // the lines are one walk over the game and share an x axis exactly.
      points.push({ i, red, yellow, color: s.color, secs,
                    ei, si: k, end: end.number,
                    label: s.label || `shot ${s.number}`,
                    estimated: secs != null && !!s.t_tee_estimated });
    });
    bounds.push({ i, number: end.number });
  });
  const spent = points.map(p => p.secs).filter(s => s != null).sort((a, b) => a - b);
  return { points, bounds, red, yellow,
           // What "long" means here, rather than in the abstract: teams take
           // as long as the game is, and a slow rock is slow against its own.
           median: spent.length ? spent[Math.floor(spent.length / 2)] : 0,
           longest: spent.length ? spent[spent.length - 1] : 0 };
}

/* Shooting percentages, by team and position, and broken down by shot type.
 *
 * An unscored type -- Curl Coach's "non scored shots" -- is counted as thrown
 * but never graded, so a rock thrown away does not drag a percentage down for
 * a shot nobody was trying to make. */
export function gatherStats(view) {
  const out = {};
  for (const c of ["red", "yellow"]) {
    out[c] = {};
    for (const p of POSITIONS) out[c][p] = { thrown: 0, graded: 0, sum: 0, types: {} };
  }
  for (const { shots } of view.ends)
    for (const s of shots) {
      const bucket = out[s.color]?.[s.position];
      if (!bucket) continue;
      const id = typeOf(s);
      const row = (bucket.types[id] ||= { thrown: 0, graded: 0, sum: 0 });
      bucket.thrown++; row.thrown++;
      if (isGraded(s) && !TYPE[id]?.unscored) {
        bucket.graded++; bucket.sum += s.user_score;
        row.graded++; row.sum += s.user_score;
      }
    }
  return out;
}

export const pct = r => (r.graded ? `${(100 * r.sum / (4 * r.graded)).toFixed(0)}%` : "—");
export const avg = r => (r.graded ? (r.sum / r.graded).toFixed(2) : "—");
