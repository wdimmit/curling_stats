/* The ice maker's report: how much the ice curled in one draw, and whether
 * another draw curled more or less.
 *
 * Only rocks that came to rest in the house without touching a stone are
 * read: their weight and depth are much alike, so what is left to differ is
 * the ice. Each is read two ways --
 *   curl     the viewer's Curl figure: sideways at rest from the thrown line,
 *            what players see on every chart;
 *   last 10  how far it bent over its last 10 m: sideways at rest from the
 *            path's tangent 10 m (along the sheet) before rest. A rock's last
 *            10 m look alike at any weight, so this one does not lean on
 *            weight at all.
 * Both + when the rock curled the way it was turned.
 *
 * Rocks are binned by the side of the sheet they curled on -- away from the
 * centre line or back toward it -- and by turns from the hog line to rest.
 *
 * The unit of evidence is the game, not the rock: the four players of a team
 * throw a game's rocks, and a draw is a handful of games. So the interval on
 * a difference comes from drawing whole games again, and one draw against
 * another only shows a change of about 10% or more. */

import { buildGameView } from "./timeline.mjs";
import { curlEnd, lineNumbers, median, restOf } from "./line.mjs";
import { isSplitEstimated } from "./stats.mjs";
import { R } from "./constants.mjs";

export const TAIL_M = 10;
// Down the middle: the rock's last 10 m sat this close to the centre line on
// average, so neither side of the sheet is the one it curled on.
export const MIDDLE_M = 0.15;
// Rotations from the hog line to rest, from the rate as thrown: the club's
// rule of thumb, which takes in both the run time and the spin slowing.
export const RPM_PER_TURN = 2.5;
// Above and below 4 turns. On 10/13 Super League, three in four house rocks
// had 4 or more; finer bands left each too few rocks to compare.
export const TURN_BANDS = [4];

export const SIDES = [
  { key: "away", name: "Away from centre" },
  { key: "toward", name: "Toward centre" },
  { key: "middle", name: "Down the middle" },
];

export const TURN_BINS = [
  { key: "t0", name: `Under ${TURN_BANDS[0]} turns` },
  ...TURN_BANDS.slice(0, -1).map((b, i) => ({ key: `t${i + 1}`, name: `${b}–${TURN_BANDS[i + 1]} turns` })),
  { key: `t${TURN_BANDS.length}`, name: `${TURN_BANDS[TURN_BANDS.length - 1]}+ turns` },
  { key: "unread", name: "Rotation not read" },
];

/* Least squares: the b minimising |X b - y|, through the normal equations;
 * null when they are singular. */
export function lstsq(X, y) {
  const k = X[0]?.length ?? 0;
  const S = Array.from({ length: k }, () => new Array(k + 1).fill(0));
  X.forEach((row, n) => {
    for (let i = 0; i < k; i++) {
      for (let j = 0; j < k; j++) S[i][j] += row[i] * row[j];
      S[i][k] += row[i] * y[n];
    }
  });
  for (let i = 0; i < k; i++) {
    let p = i;
    for (let r = i + 1; r < k; r++) if (Math.abs(S[r][i]) > Math.abs(S[p][i])) p = r;
    [S[i], S[p]] = [S[p], S[i]];
    if (Math.abs(S[i][i]) < 1e-12) return null;
    for (let r = 0; r < k; r++) {
      if (r === i) continue;
      const f = S[r][i] / S[i][i];
      for (let c = i; c <= k; c++) S[r][c] -= f * S[i][c];
    }
  }
  return S.map((row, i) => row[k] / row[i]);
}

/* dx/dy of `path` ([[y, x], ...]) at y0, and its x there, from a quadratic
 * through the points within `half` m; null without enough of them. */
export function tangentAt(path, y0, half = 2.0) {
  const pts = path.filter(([y]) => Math.abs(y - y0) <= half);
  if (pts.length < 5) return null;
  const ys = pts.map(p => p[0]);
  if (Math.max(...ys) - Math.min(...ys) < 1.5 * half) return null;
  // x = a + b u + c u^2, u = y - y0.
  const b = lstsq(pts.map(([y]) => [1, y - y0, (y - y0) ** 2]), pts.map(p => p[1]));
  return b ? { slope: b[1], x: b[0] } : null;
}

/* How far the rock bent over its last `d` m: its x at rest against where the
 * path's tangent `d` m before rest would have taken it. */
export function tailBend(path, rest, d = TAIL_M) {
  if (!Array.isArray(path) || !rest) return null;
  const t = tangentAt(path, rest.y + d);
  return t ? rest.x - (t.x - t.slope * d) : null;
}

/* Which side of the sheet the rock curled on: the mean x of its last
 * `TAIL_M` of path (its rest when the path has none there), against the way
 * it turned. */
export function sideOf(path, rest, turn) {
  const pts = (Array.isArray(path) ? path : []).filter(([y]) => y >= rest.y && y <= rest.y + TAIL_M);
  const x = pts.length ? pts.reduce((a, p) => a + p[1], 0) / pts.length : rest.x;
  if (Math.abs(x) < MIDDLE_M) return "middle";
  return (x > 0) === (turn === "right") ? "away" : "toward";
}

/* The rock's rate as thrown -- a range's middle when only a range was read. */
export function rpmOf(spin) {
  if (typeof spin?.rpm === "number") return spin.rpm;
  const r = spin?.rpm_range;
  return Array.isArray(r) && r.length === 2 ? (r[0] + r[1]) / 2 : null;
}

export function turnBin(turns) {
  if (turns == null) return "unread";
  return `t${TURN_BANDS.filter(b => turns >= b).length}`;
}

/* One row per rock of `doc`'s games that stopped in the house untouched and
 * whose turn is known. `game` names the game the rows are counted under. */
export function iceRocks(doc, game = doc?.chart?.source_id ?? "") {
  const rows = [];
  for (let gi = 0; gi < (doc?.games?.length ?? 0); gi++) {
    const view = buildGameView(doc, gi, {});
    for (const { end, shots } of view.ends) {
      for (const s of shots) {
        if (s.missing || !s.line) continue;
        const n = lineNumbers(s, doc);
        const c = n.curl, rest = n.rest;
        if (!c || c.hit !== false || typeof c.m !== "number" || !rest) continue;
        if (Math.hypot(rest.x, rest.y) > R.inHouse) continue;
        if (n.turn !== "left" && n.turn !== "right") continue;
        const sign = n.turn === "right" ? 1 : -1;
        const bend = tailBend(s.line.path, rest);
        const rpm = rpmOf(s.spin);
        const turns = rpm == null ? null : rpm / RPM_PER_TURN;
        rows.push({
          game: gi ? `${game}#${gi}` : game, sheet: doc?.source?.sheet ?? null,
          end: end.number, number: s.number, turn: n.turn,
          side: sideOf(s.line.path, rest, n.turn),
          curl_m: sign * c.m, tail_m: bend == null ? null : sign * bend,
          rpm, turns, turnBin: turnBin(turns),
        });
      }
    }
  }
  return rows;
}

const finite = xs => xs.filter(Number.isFinite);

/* A bin's numbers: rocks, games, and the medians of both readings. */
export function binSummary(rows) {
  return {
    rocks: rows.length,
    games: new Set(rows.map(r => r.game)).size,
    curl_m: median(finite(rows.map(r => r.curl_m))),
    tail_m: median(finite(rows.map(r => r.tail_m))),
  };
}

// A difference is not offered on fewer than these in either draw.
export const MIN_GAMES = 3;
export const MIN_ROCKS = 8;

function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const byGame = rows => {
  const g = new Map();
  for (const r of rows) g.set(r.game, [...(g.get(r.game) || []), r]);
  return [...g.values()];
};

function quantile(sorted, q) {
  const i = (sorted.length - 1) * q, lo = Math.floor(i), hi = Math.ceil(i);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (i - lo);
}

/* B - A on one reading (`key`), with a 95% interval from drawing each draw's
 * games again with replacement; and, over the sheets both draws played on,
 * how many curled less in B and how many more. null when either side is too thin to say. */
export function difference(rowsA, rowsB, key, { reps = 2000, seed = 1 } = {}) {
  const a = rowsA.filter(r => Number.isFinite(r[key])), b = rowsB.filter(r => Number.isFinite(r[key]));
  const ga = byGame(a), gb = byGame(b);
  if (ga.length < MIN_GAMES || gb.length < MIN_GAMES || a.length < MIN_ROCKS || b.length < MIN_ROCKS) return null;
  const med = rows => median(rows.map(r => r[key]));
  const rand = rng(seed);
  const draw = games => games.flatMap(() => games[Math.floor(rand() * games.length)]);
  const diffs = [];
  for (let i = 0; i < reps; i++) diffs.push(med(draw(gb)) - med(draw(ga)));
  diffs.sort((x, y) => x - y);
  const sheets = { less: 0, more: 0, of: 0 };
  const bySheet = rows => {
    const m = new Map();
    for (const r of rows) if (r.sheet != null) m.set(r.sheet, [...(m.get(r.sheet) || []), r]);
    return m;
  };
  const sa = bySheet(a), sb = bySheet(b);
  for (const [sheet, ra] of sa) {
    const rb = sb.get(sheet);
    if (!rb || ra.length < 3 || rb.length < 3) continue;
    sheets.of++;
    if (med(rb) < med(ra)) sheets.less++;
    else if (med(rb) > med(ra)) sheets.more++;
  }
  return { diff: med(b) - med(a), lo: quantile(diffs, 0.025), hi: quantile(diffs, 0.975), sheets };
}

/* B - A as if both draws had thrown the same rotation: the medians' gap,
 * less what the draws' different mix of turns accounts for. More rotation
 * curls less (10/13 Super: about 2 in on the Curl figure per turn), so a draw
 * that threw less of it reads as curling more on the same ice. How much curl
 * a turn is worth is fitted on these rocks, within each draw; the range draws
 * each draw's games again, that fit included. Only rocks with a rotation
 * read. null when either side is too thin to say. */
export function adjustedDifference(rowsA, rowsB, key, { reps = 1000, seed = 2 } = {}) {
  const ok = r => Number.isFinite(r[key]) && Number.isFinite(r.turns);
  const a = rowsA.filter(ok), b = rowsB.filter(ok);
  const ga = byGame(a), gb = byGame(b);
  if (ga.length < MIN_GAMES || gb.length < MIN_GAMES || a.length < MIN_ROCKS || b.length < MIN_ROCKS) return null;
  const mean = rs => rs.reduce((s, r) => s + r.turns, 0) / rs.length;
  const med = rs => median(rs.map(r => r[key]));
  const one = (ra, rb) => {
    const fit = lstsq([...ra.map(r => [1, 0, r.turns]), ...rb.map(r => [1, 1, r.turns])],
                      [...ra, ...rb].map(r => r[key]));
    if (!fit) return null;
    const shift = fit[2] * (mean(rb) - mean(ra));
    return { diff: med(rb) - med(ra) - shift, perTurn: fit[2], shift };
  };
  const got = one(a, b);
  if (!got) return null;
  const rand = rng(seed);
  const draw = games => games.flatMap(() => games[Math.floor(rand() * games.length)]);
  const diffs = [];
  for (let i = 0; i < reps; i++) {
    const r = one(draw(ga), draw(gb));
    if (r) diffs.push(r.diff);
  }
  diffs.sort((x, y) => x - y);
  return { ...got, lo: quantile(diffs, 0.025), hi: quantile(diffs, 0.975),
           turnsA: mean(a), turnsB: mean(b) };
}

/* The page's two tables, each row a bin with both draws' numbers and, for
 * each reading, B - A. The side table's rows also carry B - A adjusted for
 * rotation; the turns table's rows are already split by it. `b` may be null:
 * one draw on its own. */
export function iceTables(a, b) {
  const row = (name, pick, adjust) => {
    const ra = a.filter(pick), rb = b ? b.filter(pick) : null;
    return {
      name, a: binSummary(ra), b: rb ? binSummary(rb) : null,
      curl: rb ? difference(ra, rb, "curl_m") : null,
      tail: rb ? difference(ra, rb, "tail_m") : null,
      curlAdj: rb && adjust ? adjustedDifference(ra, rb, "curl_m") : null,
      tailAdj: rb && adjust ? adjustedDifference(ra, rb, "tail_m") : null,
    };
  };
  return {
    sides: [...SIDES.map(s => row(s.name, r => r.side === s.key, true)), row("All", () => true, true)],
    turns: TURN_BINS.map(t => row(t.name, r => r.turnBin === t.key, false)),
  };
}

/* --- ice speed --------------------------------------------------------------
 * The hog-to-hog time that would stop a rock on the tee line, end by end.
 *
 * Every rock that came to rest untouched in the house carries its own split
 * and where it stopped. The split runs about half a second longer per metre
 * shorter it stopped, so a straight line fitted through all of an end's rocks
 * -- every sheet's -- and read at the tee line is that end's draw weight. The
 * house rocks lie either side of the tee line, so the reading is from rocks
 * stopped near it; guards were left out because over their 8 m a line no
 * longer fits, and a curve's shape moved the tee-line reading. The slope is
 * shared by the whole draw; each end has its own level. A longer split means
 * keener ice: the rock needed less weight to get there.
 *
 * Sweeping is not seen, and a swept rock went further than its split says, so
 * the figure is the ice as the players used it, sweepers included. */

// A hog-to-hog time outside these is a misread, not a throw.
export const SPLIT_MIN_S = 11;
export const SPLIT_MAX_S = 20;
// An end is read only from at least this many rocks across the sheets.
export const MIN_END_ROCKS = 6;

/* One row per rock of `doc`'s games that came to rest untouched in the
 * house, with a measured split. */
export function speedRocks(doc, game = doc?.chart?.source_id ?? "") {
  const rows = [];
  for (let gi = 0; gi < (doc?.games?.length ?? 0); gi++) {
    const view = buildGameView(doc, gi, {});
    for (const { end, shots } of view.ends) {
      for (const s of shots) {
        if (s.missing) continue;
        const split = s.long_split_s;
        if (!Number.isFinite(split) || split <= SPLIT_MIN_S || split >= SPLIT_MAX_S) continue;
        if (isSplitEstimated(s)) continue;
        const rest = restOf(s);
        if (!rest || Math.hypot(rest.x, rest.y) > R.inHouse) continue;
        if (curlEnd(s)?.hit !== false) continue;
        const rpm = rpmOf(s.spin);
        rows.push({ game: gi ? `${game}#${gi}` : game, sheet: doc?.source?.sheet ?? null,
                    end: end.number, number: s.number, rest_y: rest.y, split,
                    turns: rpm == null ? null : rpm / RPM_PER_TURN });
      }
    }
  }
  return rows;
}

/* The draw's tee-line split, and each end's: split ~ level + b y, with one
 * level for the draw, then one per end. null when the rocks can't carry the
 * line. */
export function fitSpeed(rows) {
  if (rows.length < MIN_ROCKS) return null;
  const draw = lstsq(rows.map(r => [1, r.rest_y]), rows.map(r => r.split));
  if (!draw) return null;
  const ends = [...new Set(rows.map(r => r.end))].sort((a, b) => a - b);
  const byEnd = lstsq(rows.map(r => [...ends.map(e => (e === r.end ? 1 : 0)), r.rest_y]),
                      rows.map(r => r.split));
  const count = e => rows.filter(r => r.end === e).length;
  return {
    split: draw[0],
    perMetre: draw[1],
    ends: byEnd ? ends.map((end, i) => ({ end, split: byEnd[i], rocks: count(end) })) : [],
  };
}

/* Both draws' speed, each with a 95% range from drawing its games again, and
 * B - A on the whole draw. Ends with too few rocks have no figure. `b` may be
 * null. */
export function speedReport(a, b, { reps = 1000, seed = 1 } = {}) {
  const rand = rng(seed);
  const one = rows => {
    const games = byGame(rows);
    const fit = fitSpeed(rows);
    if (!fit || games.length < MIN_GAMES) {
      return { rocks: rows.length, games: games.length, split: null, ends: [], draws: [] };
    }
    const draws = [], perEnd = new Map();
    for (let i = 0; i < reps; i++) {
      const f = fitSpeed(games.flatMap(() => games[Math.floor(rand() * games.length)]));
      if (!f) continue;
      draws.push(f.split);
      for (const e of f.ends) {
        if (e.rocks < MIN_END_ROCKS / 2) continue;
        perEnd.set(e.end, [...(perEnd.get(e.end) || []), e.split]);
      }
    }
    const range = xs => {
      const s = [...xs].sort((x, y) => x - y);
      return s.length ? { lo: quantile(s, 0.025), hi: quantile(s, 0.975) } : { lo: null, hi: null };
    };
    return {
      rocks: rows.length, games: games.length, split: fit.split, ...range(draws), draws,
      ends: fit.ends.map(e => (e.rocks < MIN_END_ROCKS
        ? { end: e.end, rocks: e.rocks, split: null }
        : { ...e, ...range(perEnd.get(e.end) || []) })),
    };
  };
  const ra = one(a), rb = b ? one(b) : null;
  let diff = null;
  if (rb && ra.split != null && rb.split != null) {
    const n = Math.min(ra.draws.length, rb.draws.length);
    const d = Array.from({ length: n }, (_, i) => rb.draws[i] - ra.draws[i]).sort((x, y) => x - y);
    diff = { diff: rb.split - ra.split, lo: quantile(d, 0.025), hi: quantile(d, 0.975) };
  }
  const strip = r => r && (({ draws, ...rest }) => rest)(r);
  return { a: strip(ra), b: strip(rb), diff };
}
