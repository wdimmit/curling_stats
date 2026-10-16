/* Pure helpers for the portrait iPad layout (spec 2026-10-16). Nothing here
 * touches the DOM. */

/* The label on a sheet button (the ⋯ sheet's Miss, Note, Swapped roles...):
 * "Label…" when nothing is set, "Set label: value" when it is, the value cut
 * at a word (or at `max`) with "…" when it runs long. */
export function sheetButtonText(label, value, setLabel = label, max = 16) {
  if (!value) return `${label}…`;
  let v = value;
  if (v.length > max) {
    const head = v.slice(0, max + 1);
    const at = head.lastIndexOf(" ");
    v = `${(at > 0 ? head.slice(0, at) : v.slice(0, max))}…`;
  }
  return `${setLabel}: ${v}`;
}

/* The "next blank" button on a rock sheet: the next item after (ei, si) in
 * `items`' order, wrapping round, never (ei, si) itself; null when there is none. */
export function nextBlankOther(items, ei, si) {
  const at = items.findIndex(it => it.ei === ei && it.si === si);
  const n = items.length;
  for (let k = 1; k <= n; k++) {
    const it = items[((at < 0 ? -1 : at) + k) % n];
    if (!(it.ei === ei && it.si === si)) return it;
  }
  return null;
}

/* The one-line end summary in the tablet's rock sheet header; `summary` is
 * endSummary(view, ei) or null, `name` maps a colour to its team's name. */
export function endLineText(summary, endNumber, name) {
  let t = `End ${endNumber}`;
  if (!summary) return t;
  if (summary.hammer) {
    t += ` · ${name(summary.hammer)} has hammer`
      + (summary.hammerSource === "placement" ? " (from the placement)" : "");
  }
  if (summary.powerPlay) t += ` · power play, ${summary.powerPlay.color}, ${summary.powerPlay.side}`;
  return t;
}

/* The "Swapped roles" button's value: the names of the swapped colours, red
 * first, joined with ", "; null when none is swapped. */
export function swapText(swapped, name) {
  const names = ["red", "yellow"].filter(c => swapped?.[c]).map(name);
  return names.length ? names.join(", ") : null;
}
