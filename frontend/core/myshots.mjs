/* My shots: every rock one person threw, across the games they said they
 * played, grouped by what kind of shot it was and which way it turned.
 *
 * Each game arrives graded and cut down to the player by the server
 * (timeline.for_player, /api/me/plays/{game}/doc), so it is laid out here
 * with no overrides -- the parity test in test_viewer_js.py holds that to
 * what the viewer shows. The figures are the Detail pane's own (lineNumbers,
 * lineFigures), so a rock reads the same here as on its game's page.
 *
 * Pure -- core/** may not touch the DOM. */
import { SCATTERBOX, TYPE } from "./constants.mjs";
import { roleText } from "./format.mjs";
import { aimFrame } from "./delivery.mjs";
import { ON_M, feetInches, formatHash, lineFigures, lineNumbers, narrowOf, withHash } from "./line.mjs";
import { groupOf, positionLabel } from "./report.mjs";
import { typeOf } from "./shots.mjs";
import { buildGameView } from "./timeline.mjs";

export const SHOT_GROUPS = ["Hit", "Draw", "Guard", "Other"];
const PLURAL = { Hit: "Hits", Draw: "Draws", Guard: "Guards", Other: "Other rocks" };
// Seen from above, a rock turning clockwise curls to the thrower's right.
export const TURNS = [["right", "Clockwise"], ["left", "Counter-clockwise"], [null, "Turn not measured"]];

const OTHER = { red: "yellow", yellow: "red" };
const capital = w => (w ? w[0].toUpperCase() + w.slice(1) : w);

/* Which of its team's players threw a rock, counting from 1: the slot, or on
 * a document older than slots, the position's place. Mirrors
 * timeline.slot_of. */
export function slotOf(shot, fmt) {
  const k = shot?.thrower_slot;
  if (Number.isInteger(k)) return k;
  const i = fmt.positions.indexOf(shot?.position);
  return i >= 0 ? i + 1 : null;
}

/* What "I played…" offers: each slot, by name and by the rocks it throws --
 * "Skip" and "7th & 8th", or in doubles "Player B" and "2nd–4th". */
export const positionChoices = fmt => fmt.positions.map((p, i) => ({
  slot: i + 1, label: positionLabel(p, fmt), role: roleText(fmt, i + 1),
}));

/* "Dimmit · Skip (7th & 8th)": a play as the report lists it. */
export function playText(play) {
  const team = play.teams?.[play.color] || capital(play.color);
  const fmt = play.format;
  return `${team} · ${positionLabel(fmt.positions[play.slot - 1], fmt)} (${roleText(fmt, play.slot)})`;
}

const shortDay = d => d.toLocaleDateString(undefined, { month: "short", day: "numeric" });

/* "Sep 29 v Grant": the game a rock was thrown in, from the player's side.
 * `day` formats the date, so the rest is testable without a locale. */
export function gameText(play, day = shortDay) {
  const when = play.played_at ? day(new Date(play.played_at)) : "Undated";
  const them = play.teams?.[OTHER[play.color]];
  return them ? `${when} v ${them}` : play.sheet ? `${when} · sheet ${play.sheet}` : when;
}

/* The rocks `play`'s player threw in `doc`, one row each, and how many of
 * theirs were never seen. */
export function playerRocks(play, doc, day = shortDay) {
  const view = buildGameView(doc, 0, {});
  const game = gameText(play, day);
  const rows = [];
  let missing = 0;
  for (const { end, shots } of view.ends) {
    for (const s of shots) {
      if (s.color !== play.color || slotOf(s, view.format) !== play.slot) continue;
      if (s.missing) { missing++; continue; }
      const type = typeOf(s);
      const nums = lineNumbers(s, doc);
      rows.push({
        key: `${play.source_id}.${end.number}.${s.number}`,
        sourceId: play.source_id, playedAt: play.played_at || "", game,
        end: end.number, number: s.number, rock: s.rock_of_player ?? null,
        type, typeName: TYPE[type]?.name ?? type, group: groupOf(type),
        turn: nums.turn, nums, figures: lineFigures(s, doc).figures,
        path: aimFrame(s, doc),
        href: play.view_path ? withHash(play.view_path, formatHash({ e: end.number, s: s.number })) : null,
      });
    }
  }
  return { rows, missing };
}

const median = xs => {
  const s = [...xs].sort((a, b) => a - b), m = s.length >> 1;
  return !s.length ? null : s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
};

/* A group's numbers. The broom counts are by side of the turn when there is
 * one -- `plus` narrow, `minus` wide -- and by the thrower's right and left
 * when there is not. */
export function summarize(rows, turn) {
  const splits = rows.map(r => r.nums.split_s).filter(Number.isFinite);
  const missed = rows.filter(r => typeof r.nums.miss_m === "number");
  const side = r => (turn ? narrowOf(r.nums.miss_m, turn) : r.nums.miss_m);
  const broom = { n: missed.length, median: median(missed.map(side)), on: 0, plus: 0, minus: 0 };
  for (const r of missed) {
    if (r.nums.on_broom) broom.on++;
    else if (side(r) > 0) broom.plus++;
    else broom.minus++;
  }
  return {
    n: rows.length,
    weight: { n: splits.length, median: median(splits),
              lo: splits.length ? Math.min(...splits) : null,
              hi: splits.length ? Math.max(...splits) : null },
    broom,
    noMiss: rows.length - missed.length,
    noWeight: rows.length - splits.length,
    noPath: rows.filter(r => !r.path).length,
  };
}

/* Every word the summary row shows, so the page does no arithmetic. */
export function summaryText(sum, turn) {
  const [plus, minus] = turn ? ["narrow", "wide"] : ["right", "left"];
  const w = sum.weight, b = sum.broom;
  const typical = b.n === 0 ? "–"
    : Math.abs(b.median) < ON_M ? "On the broom"
    : `${feetInches(b.median)} ${b.median > 0 ? plus : minus}`;
  const some = (k, what) => (k ? `${k} ${what}` : null);
  return {
    count: `${sum.n} rock${sum.n === 1 ? "" : "s"}`,
    weight: w.n === 0 ? "–" : `${w.median.toFixed(1)} s`
      + (w.n > 1 && w.hi > w.lo ? ` (${w.lo.toFixed(1)}–${w.hi.toFixed(1)})` : ""),
    broom: b.n === 0 ? "–" : `${b.on} on · ${b.minus} ${minus} · ${b.plus} ${plus}`,
    typical,
    notes: [some(sum.noMiss, "not measured at the broom"), some(sum.noWeight, "not timed"),
            some(sum.noPath, "with no delivery path")].filter(Boolean),
  };
}

/* The rocks in fixed order -- Hit, Draw, Guard, Other, each clockwise, then
 * counter-clockwise, then unmeasured -- with the groups nobody threw left out.
 * Within a group, newest game first, then by end and rock. */
export function shotGroups(rows) {
  const out = [];
  for (const group of SHOT_GROUPS) {
    for (const [turn, turnName] of TURNS) {
      const these = rows.filter(r => r.group === group && (r.turn ?? null) === turn);
      if (!these.length) continue;
      these.sort((a, b) => (a.playedAt < b.playedAt ? 1 : a.playedAt > b.playedAt ? -1 : 0)
        || a.end - b.end || a.number - b.number);
      out.push({ id: `${group}-${turn ?? "unmeasured"}`, group, turn,
                 label: `${PLURAL[group]} · ${turnName}`, rows: these,
                 summary: summarize(these, turn) });
    }
  }
  return out;
}

const r1 = v => +(+v).toFixed(1);
const FOOT_M = 0.3048;

/* Where each rock of a group crossed the broom against its weight. Across is
 * the miss in the thrower's frame, + right, so the sides read wide and narrow
 * by the group's turn as the overlay beside it does; down is the split, the
 * heavier (shorter) at the top. A rock past `maxFeet` sits on the edge,
 * marked clipped. */
export function missScatter(rows, turn, box = SCATTERBOX) {
  const { w, h, maxFeet, pad } = box;
  const plotted = rows.filter(r => typeof r.nums.miss_m === "number" && Number.isFinite(r.nums.split_s));
  const reach = Math.max(FOOT_M, ...plotted.map(r => Math.abs(r.nums.miss_m)));
  const feet = Math.min(maxFeet, Math.ceil(reach / FOOT_M - 1e-9));
  const half = feet * FOOT_M;
  const splits = plotted.map(r => r.nums.split_s);
  // A tick every half second, or every one or two across a group that runs
  // from guard weight to a peel: a label every few pixels reads as none.
  const least = splits.length ? Math.min(...splits) : 13.5;
  const most = splits.length ? Math.max(...splits) : 14.5;
  const step = most - least <= 3 ? 0.5 : most - least <= 6 ? 1 : 2;
  let lo = Math.floor(least / step) * step, hi = Math.ceil(most / step) * step;
  if (hi - lo < 1) { lo -= (1 - (hi - lo)) / 2; hi = lo + 1; }
  const plotW = w - pad.l - pad.r, plotH = h - pad.t - pad.b;
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const X = x => pad.l + (clamp(x, -half, half) + half) / (2 * half) * plotW;
  const Y = s => pad.t + (clamp(s, lo, hi) - lo) / (hi - lo) * plotH;
  const xTicks = [];
  // Every foot, labelled every other one past three feet a side.
  for (let k = -feet; k <= feet; k++) {
    const label = feet > 3 && k % 2 ? "" : k === 0 ? "0" : `${Math.abs(k)} ft`;
    xTicks.push({ x: r1(X(k * FOOT_M)), label, zero: k === 0 });
  }
  const yTicks = [];
  for (let s = lo; s <= hi + 1e-9; s += step) {
    yTicks.push({ y: r1(Y(s)), label: s.toFixed(step < 1 ? 1 : 0) });
  }
  const sides = turn === "right" ? ["wide", "narrow"] : turn === "left" ? ["narrow", "wide"] : ["left", "right"];
  return {
    w, h, plot: { x: pad.l, y: pad.t, w: plotW, h: plotH },
    band: { x: r1(X(-ON_M)), y: pad.t, w: r1(X(ON_M) - X(-ON_M)), h: plotH },
    xTicks, yTicks,
    sides: [{ x: pad.l + 2, y: pad.t - 8, text: sides[0], anchor: "start" },
            { x: pad.l + plotW - 2, y: pad.t - 8, text: sides[1], anchor: "end" }],
    xLabel: "at the broom", yLabel: "hog to hog, s",
    points: plotted.map(r => ({
      key: r.key, href: r.href, cx: r1(X(r.nums.miss_m)), cy: r1(Y(r.nums.split_s)),
      hollow: !!r.nums.split_estimated, dim: r.nums.tick === "disagrees",
      clipped: Math.abs(r.nums.miss_m) > half,
    })),
    skipped: rows.length - plotted.length,
  };
}
