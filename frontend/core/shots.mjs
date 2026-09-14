/* What a single shot is, and what the page says about it. */
import { TYPE, TYPES } from "./constants.mjs";

export const isBlank = s => s && (s.state_known === false || s.missing);
export const typeOf = s => (s ? (s.shot_type || "unknown") : "unknown");
export const isGraded = s => s && typeof s.user_score === "number";
export const isUnscoredType = s => !!TYPE[typeOf(s)]?.unscored;

/* Nobody can grade a rock nobody saw. A blank's fast path is saying where it
 * was thrown and what was on the ice, so its peek carries different controls. */
export const peekMode = s => (isBlank(s) ? "order" : "grade");

/* Moving a rock renumbers all sixteen. On desktop the chip strip shows that
 * happening; the phone has no strip, so the change announces itself -- and
 * says where the colour came from, since `renumber` settles a blank's colour
 * from the alternation around it and the charter has no other way to know. */
export function renumberNotice(before, after) {
  if (!before || !after || before.number === after.number) return null;
  const why = after.color_inferred ? `, ${after.color} by alternation` : "";
  return `End renumbered — this is now rock ${after.number}${why}`;
}

/* Where in the video to start, counting the lead-in back from the moment the
 * rock is known to have been somewhere. A rest time is the rock already
 * stopped, so it needs a further eight seconds to catch the delivery. */
export function shotVideoTime(s, leadIn) {
  if (!s) return null;
  if (typeof s.t_enter_s === "number") return Math.max(0, s.t_enter_s - leadIn);
  if (typeof s.t_rest_s === "number") return Math.max(0, s.t_rest_s - leadIn - 8);
  if (typeof s.t_guess_s === "number") return Math.max(0, s.t_guess_s - leadIn);
  return null;
}

/* Every rock still needing a person, in throwing order, plus the count of
 * rocks detection could not place at all. Both drive the header pill: one is
 * work you can do, the other is work nobody can. */
export function blankQueue(view) {
  const items = [];
  view.ends.forEach(({ end, shots }, ei) => {
    shots.forEach((s, si) => {
      if (isBlank(s))
        items.push({ ei, si, label: `E${end.number} · ${s.label || `shot ${s.number}`}` });
    });
  });
  const unplaced = view.game.ends.reduce((n, e) => n + (e.unplaced_shots || 0), 0);
  return { items, unplaced };
}

/* The next blank after where the cursor is, wrapping to the first. */
export function nextBlankAfter(items, ei, si) {
  if (!items.length) return null;
  const at = items.findIndex(it => it.ei > ei || (it.ei === ei && it.si > si));
  return items[at === -1 ? 0 : at];
}

/* Which group of shot types to show on arriving at a rock, or null for none.
 *
 * A rock nothing has typed opens no group. The detector offers only the four
 * coarse categories and is often wrong -- the fine type is a statement about
 * what was *called*, which is the charter's to give -- so a pre-opened row
 * there would be suggesting an answer nobody has.
 *
 * Otherwise the row stays where the charter left it, and moves only when this
 * rock's type is not in the group already open. That keeps a stable click
 * target through a run of similar shots, while never showing a typed rock
 * with nothing highlighted, which reads as ungraded when it is not.
 *
 * Only ever consulted on arriving at a different rock. Applied on every
 * render it would fight the charter: clicking "Hit" to turn a draw into a hit
 * would snap the row back to Draw before the type could be picked.
 */
export function openGroupFor(type, openGroup) {
  if (!type || type === "unknown") return null;
  if (TYPES.some(t => t.group === openGroup && t.id === type)) return openGroup;
  return TYPES.find(t => t.id === type)?.group ?? null;
}
