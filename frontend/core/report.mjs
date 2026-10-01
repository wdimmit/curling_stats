/* What the game report prints, worked out from the game view.
 *
 * Framework-free like stats.mjs, so tests/test_viewer_js.py runs it under
 * node. viewer/Report.jsx only lays these numbers out: every percentage,
 * total and sentence on the page is decided here, where it can be tested
 * against a real game rather than eyeballed in one. See
 * docs/superpowers/specs/2026-10-01-game-report-and-entered-scores-design.md.
 */
import { GROUPS, TYPE, TYPES } from "./constants.mjs";
import { FOURS, positionText } from "./format.mjs";
import { isGraded } from "./shots.mjs";
import { boardReadable } from "./wire.mjs";

const COLORS = ["red", "yellow"];
const zero = () => ({ thrown: 0, graded: 0, sum: 0 });
const addShot = (r, s) => {
  r.thrown++;
  if (isGraded(s)) { r.graded++; r.sum += s.user_score; }
};
const addInto = (r, x) => {
  if (x) { r.thrown += x.thrown; r.graded += x.graded; r.sum += x.sum; }
  return r;
};
// The rocks gatherStats counts: a colour it knows and a position.
const counted = s => COLORS.includes(s?.color) && !!s.position;

/* pct()'s number: graded rocks only, rounded as toFixed(0) rounds, so the
 * report and every older surface can never disagree by one. */
export const pctOf = r =>
  (r?.graded ? Number((100 * r.sum / (4 * r.graded)).toFixed(0)) : null);

/* "Lead", or "Player A" where a team is two players. */
export function positionLabel(p, fmt = FOURS) {
  const t = positionText(p, fmt) || "";
  return t.charAt(0).toUpperCase() + t.slice(1);
}

/* "end 1", "ends 1 and 3", "ends 1, 2 and 4". */
export function endList(numbers) {
  if (!numbers.length) return "";
  if (numbers.length === 1) return `end ${numbers[0]}`;
  return `ends ${numbers.slice(0, -1).join(", ")} and ${numbers[numbers.length - 1]}`;
}

/* Who played, or the colour they threw when nobody has said. */
export function teamNames(game) {
  return { red: game?.teams?.red?.name || "Red", yellow: game?.teams?.yellow?.name || "Yellow" };
}

/* The line over the title: league, sheet, the day it was played, and the
 * game when a recording holds more than one. `day` formats the date, so
 * the rest stays testable without a locale. */
export function reportMeta(doc, gi, day = d => d.toLocaleDateString(undefined,
  { weekday: "short", day: "numeric", month: "short", year: "numeric" })) {
  const parts = [];
  if (doc?.chart?.league) parts.push(doc.chart.league);
  if (doc?.source?.sheet != null) parts.push(`Sheet ${doc.source.sheet}`);
  const at = doc?.chart?.played_at ? new Date(doc.chart.played_at) : null;
  if (at && !Number.isNaN(at.getTime())) parts.push(day(at));
  if ((doc?.games?.length || 0) > 1) parts.push(`Game ${gi + 1} of ${doc.games.length}`);
  parts.push("Game report");
  return parts.join(" · ");
}

/* The by-end table: per end, the score, who had the hammer, and each team's
 * shooting and thinking, plus the totals.
 *
 * `status` says whether there is a score to show at all: "predates" for a
 * chart from before board reading (its ends hold the detector's guesses,
 * which nothing shows), "withheld" for a board read but not placed, "ok"
 * otherwise. A score of null is an end the board never gave one. The score
 * total adds the ends that have one; `complete` says whether that is all. */
export function byEnd(view) {
  const readable = boardReadable(view.doc);
  const withheld = readable && !!view.game.scoreboard?.scores_withheld;
  const team = { red: zero(), yellow: zero() };
  const clock = { red: 0, yellow: 0 };
  const ends = view.ends.map(({ end, shots }) => {
    const shooting = { red: zero(), yellow: zero() };
    for (const s of shots) {
      if (!counted(s)) continue;
      addShot(shooting[s.color], s);
      addShot(team[s.color], s);
    }
    const t = end.thinking_time || {};
    for (const c of COLORS) clock[c] += t[c] || 0;
    return {
      number: end.number,
      hammer: end.hammer || null,
      score: readable ? (end.score ?? null) : null,
      entered: readable && end.score_source === "entered",
      shooting: { red: pctOf(shooting.red), yellow: pctOf(shooting.yellow) },
      thinking: { red: t.red ?? null, yellow: t.yellow ?? null },
    };
  });
  const known = ends.filter(e => e.score);
  return {
    status: !readable ? "predates" : withheld ? "withheld" : "ok",
    boardRead: !!view.game.scoreboard,
    ends,
    total: {
      score: known.length
        ? Object.fromEntries(COLORS.map(c => [c, known.reduce((n, e) => n + (e.score[c] || 0), 0)]))
        : null,
      complete: ends.length > 0 && known.length === ends.length,
      shooting: { red: pctOf(team.red), yellow: pctOf(team.yellow) },
      thinking: clock,
    },
  };
}

/* The positions a team is reported under: the format's, plus any other a
 * rock was thrown from (gatherStats keeps those in their own bucket). */
const positionsOf = (stats, fmt) => {
  const extra = COLORS.flatMap(c => Object.keys(stats[c]))
    .filter(p => !fmt.positions.includes(p) && COLORS.some(c => stats[c][p]?.thrown));
  return [...fmt.positions, ...new Set(extra)];
};

const groupOf = id => TYPE[id]?.group || "Other";
const groupTotal = (bucket, group) => Object.entries(bucket?.types || {})
  .filter(([id]) => groupOf(id) === group)
  .reduce((r, [, x]) => addInto(r, x), zero());

/* The two teams head to head: by position, the team, and by shot type. */
export function headToHead(stats, fmt = FOURS) {
  const positions = positionsOf(stats, fmt);
  const teamOf = c => positions.reduce((r, p) => addInto(r, stats[c][p]), zero());
  const graded = new Set(), thrown = new Set();
  for (const c of COLORS) for (const p of fmt.positions) {
    graded.add(stats[c][p]?.graded ?? 0);
    thrown.add(stats[c][p]?.thrown ?? 0);
  }
  const types = ["Draw", "Guard", "Hit"].map(g => {
    const r = Object.fromEntries(COLORS.map(c =>
      [c, positions.reduce((t, p) => addInto(t, groupTotal(stats[c][p], g)), zero())]));
    return { id: g, label: g, red: pctOf(r.red), yellow: pctOf(r.yellow),
             redN: r.red.graded, yellowN: r.yellow.graded };
  });
  const other = [];
  for (const c of COLORS) {
    const byType = {};
    for (const p of positions)
      for (const [id, x] of Object.entries(stats[c][p]?.types || {}))
        if (groupOf(id) === "Other") byType[id] = (byType[id] || 0) + x.thrown;
    for (const [id, n] of Object.entries(byType))
      other.push({ color: c, type: TYPE[id]?.name || id, thrown: n });
  }
  return {
    positions: positions.map(p => ({ id: p, label: positionLabel(p, fmt),
                                     red: pctOf(stats.red[p]), yellow: pctOf(stats.yellow[p]) })),
    team: { red: pctOf(teamOf("red")), yellow: pctOf(teamOf("yellow")) },
    types,
    perPlayer: graded.size === 1 && thrown.size === 1
      ? { graded: [...graded][0], thrown: [...thrown][0] } : null,
    other,
  };
}

/* One team's position-by-type table. A group row always; a row per type
 * only where the group holds more than one, so "Draw" never sits over a
 * lone "Draw". A cell is null where nothing of that kind was thrown. */
export function detailRows(stats, color, fmt = FOURS) {
  const team = stats[color];
  const positions = positionsOf(stats, fmt);
  const cell = r => (r && r.thrown ? { pct: pctOf(r), graded: r.graded, thrown: r.thrown } : null);
  const thrownIds = new Set(positions.flatMap(p => Object.keys(team[p]?.types || {})));
  const sum = (list, p) => list.reduce((r, id) => addInto(r, team[p]?.types[id]), zero());
  const across = list => positions.reduce((r, p) => addInto(r, sum(list, p)), zero());
  const rows = [];
  for (const group of GROUPS) {
    const ids = TYPES.filter(t => t.group === group && thrownIds.has(t.id)).map(t => t.id);
    if (group === "Other") ids.push(...[...thrownIds].filter(id => !TYPE[id]));
    if (!ids.length) continue;
    rows.push({ kind: "group", label: group,
                cells: positions.map(p => cell(sum(ids, p))), all: cell(across(ids)) });
    if (ids.length > 1)
      for (const id of ids)
        rows.push({ kind: "type", label: TYPE[id]?.name || id,
                    cells: positions.map(p => cell(team[p]?.types[id])), all: cell(across([id])) });
  }
  return {
    positions: positions.map(p => ({ id: p, label: positionLabel(p, fmt) })),
    rows,
    all: { cells: positions.map(p => cell(team[p])),
           all: cell(positions.reduce((r, p) => addInto(r, team[p]), zero())) },
  };
}

/* The longest thinks, longest first, with what the report says of each. */
export function longestThinks(view, series, n = 5) {
  return series.points
    .filter(p => p.secs != null)
    .sort((a, b) => b.secs - a.secs)
    .slice(0, n)
    .map(p => {
      const s = view.ends[p.ei]?.shots[p.si];
      return { secs: p.secs, color: p.color, ei: p.ei, si: p.si, end: p.end,
               number: s?.number ?? null, position: s?.position ?? null,
               type: TYPE[s?.shot_type]?.name ?? null, estimated: !!p.estimated };
    });
}

/* How much of the game is graded, which ends have nothing graded, and the
 * first rock still to grade -- where the pill takes you. */
export function coverage(view) {
  let graded = 0, thrown = 0, first = null;
  const ungradedEnds = [];
  view.ends.forEach(({ end, shots }, ei) => {
    let any = false, some = false;
    shots.forEach((s, si) => {
      if (!counted(s)) return;
      some = true;
      thrown++;
      if (isGraded(s)) { graded++; any = true; } else if (!first) first = { ei, si };
    });
    if (some && !any) ungradedEnds.push(end.number);
  });
  return { graded, thrown, ungradedEnds, first };
}

/* The pill's words, or null once every rock is graded. */
export function coverageText(cov) {
  if (!cov.thrown || cov.graded === cov.thrown) return null;
  if (!cov.graded) return "No rocks graded yet";
  const left = cov.ungradedEnds.length ? ` · ${endList(cov.ungradedEnds)} still to grade` : "";
  return `${cov.graded} of ${cov.thrown} rocks graded${left}`;
}

/* "About these numbers": what the percentages and the clock are read from. */
export function reportNotes(cov, think, endCount) {
  const notes = [];
  if (cov.thrown && cov.graded === cov.thrown) {
    notes.push("Every rock is graded.");
  } else {
    const ends = cov.ungradedEnds;
    const which = ends.length
      ? `. ${endList(ends).replace(/^e/, "E")} ${ends.length === 1 ? "hasn’t" : "haven’t"} been graded`
      : "";
    notes.push(`Percentages come from graded rocks only: ${cov.graded} of ${cov.thrown}${which}. `
             + "A rock nobody graded counts as thrown, never as a miss.");
  }
  if (think.measured) {
    const missed = think.unmeasured - endCount;
    notes.push(`Thinking time is read for ${think.measured} of ${think.measured + think.unmeasured} rocks. `
             + "An end’s first rock has nothing to time from"
             + (missed > 0 ? `, and the camera missed ${missed} more` : "") + "."
             + (think.estimated
               ? ` ${think.estimated} ${think.estimated === 1 ? "is" : "are"} estimated (outlined).`
               : "")
             + " Treat the totals as lower bounds.");
  }
  return notes;
}

/* What the score picker offers for one end: a blank end, then each team
 * scoring one up to every stone it has (8 in fours, 6 in doubles). Only one
 * team scores in an end, so there is nothing to type. */
export function scoreChoices(fmt = FOURS) {
  const most = fmt.stones_per_team || FOURS.stones_per_team;
  const out = [{ label: "Blank end", red: 0, yellow: 0 }];
  for (const c of COLORS)
    for (let n = 1; n <= most; n++)
      out.push({ color: c, n, red: c === "red" ? n : 0, yellow: c === "yellow" ? n : 0 });
  return out;
}

/* What the picker says when a save does not land. */
export function scoreError(status) {
  if (status === 409) return "The board has a score for this end now";
  if (status === 401) return "Sign in again to save";
  return "Couldn't save. Try again.";
}
