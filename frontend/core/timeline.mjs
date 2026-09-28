/* The override merge, and the renumbering that follows a moved rock.
 *
 * This is a mirror of Python's `timeline.apply_overrides`, and the two are
 * checked against each other shot by shot by tests/test_viewer_js.py -- the
 * same document through both implementations, eight fields compared per row.
 * That parity is the reason charting work can be trusted not to disagree with
 * what the next analysis run bakes in, so treat any change here as a change
 * to the Python as well.
 *
 * Everything takes what it needs as an argument. The version this was lifted
 * from read a module-global `state`, which made every caller a hidden
 * dependency and made one `render()` cost O(ends x shots) relayouts.
 */
import { TYPICAL_GAP_S } from "./constants.mjs";
import { playerHacks } from "./line.mjs";
import { FOURS, formatOf, shotLabel, throwInfo } from "./format.mjs";

/* A shot is known by the number detection gave it. Moving one renumbers the
 * end, so `id` keeps the original where that has happened. */
export const identity = s => s.id ?? s.number;

/* Same bargain one level up: trimming the practice off the front of a game
 * renumbers every end that survives, so `id` keeps the number the end's
 * corrections were filed under. Mirrors timeline.end_identity. */
export const endIdentity = e => e.id ?? e.number;

export const keyFor = (g, e, s) => `${g.index}.${endIdentity(e)}.${identity(s)}`;

/* Mirror of timeline.apply_overrides, so what you see here is exactly what
 * the next analysis run will bake in. */
export function merge(g, e, s, overrides) {
  if (!s) return null;
  const patch = overrides[keyFor(g, e, s)];
  if (!patch) return s;
  return { ...s, ...patch, corrected: true };
}

/* The key of an end-level correction: a doubles team swapping roles. */
export const endKey = (g, e) => `${g.index}.${endIdentity(e)}`;

/* Which teams swapped roles this end. Only a format whose two players can
 * trade roles honours it, and only true counts -- turning a swap off is the
 * same as never having set it. Mirrors timeline._swapped. */
export function swappedOf(g, e, overrides, fmt = FOURS) {
  if (!fmt.swappable) return {};
  const want = overrides[endKey(g, e)]?.roles_swapped;
  if (!want || typeof want !== "object") return {};
  const out = {};
  for (const c of ["red", "yellow"]) if (want[c] === true) out[c] = true;
  return out;
}

/* Who threw each rock of an end whose order did not change. Mirrors
 * timeline._assign_throwers. */
export function assignThrowers(shots, endNo, fmt, swapped) {
  for (const s of shots) {
    if (!Number.isInteger(s.number) || s.number < 1 || s.number > fmt.delivered_per_end) continue;
    const swap = !!swapped[s.color];
    const t = throwInfo(s.number, fmt, swap);
    s.thrower_slot = t.thrower_slot;
    s.position = fmt.positions[t.thrower_slot - 1];
    s.rock_of_player = t.rock_of_player;
    s.label = shotLabel(endNo, s.number, fmt, swap);
  }
}

export function renumber(shots, endNo, fixed, fmt = FOURS, swapped = {}) {
  const anchors = [];
  shots.forEach((s, i) => {
    if (!s.color_inferred || fixed.has(identity(s))) anchors.push([i, s.color]);
  });
  shots.forEach((s, i) => {
    s.id = identity(s);
    s.number = i + 1;
    if (s.color_inferred && anchors.length && !fixed.has(s.id)) {
      let best = anchors[0];
      for (const a of anchors) if (Math.abs(a[0] - i) < Math.abs(best[0] - i)) best = a;
      s.color = (i - best[0]) % 2 === 0 ? best[1] : (best[1] === "red" ? "yellow" : "red");
    }
    const swap = !!swapped[s.color];
    const t = throwInfo(i + 1, fmt, swap);
    s.has_hammer = t.has_hammer;
    s.thrower_slot = t.thrower_slot;
    s.position = fmt.positions[t.thrower_slot - 1];
    s.rock_of_player = t.rock_of_player;
    s.label = shotLabel(endNo, i + 1, fmt, swap);
  });
}

/* A blank has no timestamp. The nearest rock that has one, a typical gap per
 * shot away, is a fair place to start the video looking for it. */
export function guessTimes(shots) {
  const timed = shots.map((s, i) => [i, s.t_enter_s]).filter(([, t]) => typeof t === "number");
  if (!timed.length) return;
  shots.forEach((s, i) => {
    if (typeof s.t_enter_s === "number" || typeof s.t_rest_s === "number") return;
    let best = timed[0];
    for (const a of timed) if (Math.abs(a[0] - i) < Math.abs(best[0] - i)) best = a;
    s.t_guess_s = Math.max(0, best[1] + (i - best[0]) * TYPICAL_GAP_S);
  });
}

/* Mirror of the rest of timeline.apply_overrides: after the patches, a shot
 * carrying `before` was thrown before the shot it names, so the end is put in
 * that order and renumbered -- thrower, label and hammer follow the number,
 * and a blank's colour follows the alternation around it. `raws` are the
 * document's own shot objects in the same order, for editing. */
export function layout(g, e, overrides, fmt = FOURS) {
  // An end with no shots array at all is an end with nothing detected. The
  // version this replaced never noticed, because only the renderers called it
  // and they only ever walked ends that had shots; buildGameView lays every
  // end out once, up front, so it is this that has to be tolerant.
  const src = e.shots || [];
  let shots = src.map(s => ({ ...merge(g, e, s, overrides) }));
  let raws = src;
  const swapped = swappedOf(g, e, overrides, fmt);
  const ids = new Set(shots.map(identity));
  const moves = shots.filter(s => Number.isInteger(s.before) &&
                                  s.before !== identity(s) && ids.has(s.before));
  if (moves.length) {
    shots = shots.filter(s => !moves.includes(s));
    for (const s of [...moves].sort((a, b) => identity(a) - identity(b))) {
      const at = shots.findIndex(o => identity(o) === s.before);
      shots.splice(at === -1 ? shots.length : at, 0, s);
    }
    const prefix = `${g.index}.${endIdentity(e)}.`;
    const fixed = new Set(Object.entries(overrides)
      .filter(([k, p]) => k.startsWith(prefix) && p && "color" in p)
      .map(([k]) => +k.slice(prefix.length)));
    renumber(shots, e.number, fixed, fmt, swapped);
    const byId = new Map(src.map(s => [identity(s), s]));
    raws = shots.map(s => byId.get(identity(s)));
  } else if (Object.keys(swapped).length) {
    assignThrowers(shots, e.number, fmt, swapped);
  }
  guessTimes(shots);
  return { shots, raws, swapped };
}

/* One relayout of the whole game, which everything else reads off.
 *
 * This is the memo boundary. The version this replaced recomputed `layout()`
 * from scratch for every call of `shot()`, and `render()` called that about
 * ten times while the queue, the clock and the report each re-laid every end
 * -- roughly 800 shot objects allocated per render of an eight-end game, on
 * every keystroke in the note field. Called once per (doc, gi, overrides),
 * this is a few hundred microseconds that nothing needs to repeat.
 */
export function buildGameView(doc, gi, overrides) {
  const game = doc.games[gi];
  const format = formatOf(doc);
  const ends = game.ends.map(e => {
    const { shots, raws, swapped } = layout(game, e, overrides, format);
    return { end: e, shots, raws, swapped };
  });
  // The house each rock was thrown into, which line.mjs's curlEnd looks for a
  // struck stone in: the last one read before it. A rock never seen, or seen
  // with no reading of the house, leaves the house as it was. Rock 1 is thrown
  // into doubles' placed stones, or an empty house.
  for (const { end, shots } of ends) {
    let house = [end.placement?.house, end.placement?.guard].filter(Boolean);
    for (const s of shots) {
      s.stones_before = house;
      if (!s.missing && s.state_known !== false) house = s.stones ?? [];
    }
  }
  // A player throws from one hack all game, so each rock is given its
  // player's -- after the overrides, which can change who threw it.
  const hacks = playerHacks(ends.flatMap(x => x.shots));
  for (const { shots } of ends) {
    for (const s of shots) {
      const h = hacks[`${s.color}|${s.thrower_slot}`];
      if (h) s.hack = h;
    }
  }
  return { doc, gi, game, ends, format };
}

/* The shot the cursor is on, merged and raw. `raw` is what an edit is keyed
 * against; `shot` is what is drawn. An end where detection found nothing still
 * has to render, so both can be null. */
export function cursor(view, ei, si) {
  const at = view.ends[ei];
  if (!at) return { end: null, shot: null, raw: null, key: null };
  const raw = at.raws[si] ?? null;
  return {
    end: at.end,
    shot: at.shots[si] ?? null,
    raw,
    key: raw ? keyFor(view.game, at.end, raw) : null,
  };
}
