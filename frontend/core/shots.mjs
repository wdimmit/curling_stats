/* What a single shot is, and what the page says about it. */
import { TYPE } from "./constants.mjs";

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
