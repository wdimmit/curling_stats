/* What the page and the server say to each other about corrections.
 *
 * The config used to be four module-level `const`s computed from window.CHART
 * at import time, which meant a test could only choose a mode by setting a
 * global before the module loaded. It is an argument now.
 */

/* Served by the hosted API the page is mounted at /c/{slug}/ and told its
 * mode; served locally there is no window.CHART and everything is editable.
 *
 * Review is the public /g/{source}/ surface: a game with no chart behind it,
 * so there is nothing to save and nothing to load. It is read-only for the
 * same reason a view link is, and then some -- the server exposes no route it
 * could write to -- so it implies readOnly rather than sitting beside it. */
export function modeOf(chart) {
  const c = chart || { mode: "edit", slug: null };
  const review = c.mode === "review";
  return {
    mode: c.mode || "edit",
    slug: c.slug ?? null,
    source: c.source ?? null,
    hosted: !!chart,
    shared: !!c.shared,
    // Whether this server understands a per-key merge. The local
    // `curling-score serve` does not -- it sets no window.CHART at all -- so
    // the page keeps sending whole documents there, exactly as it always has.
    merge: !!c.merge,
    // Whether this /c/ page's chart is somebody's or a team's, so the page
    // asks access.json before it unlocks editing (accessMode).
    guarded: !!c.guarded,
    // Why the page is read-only when access.json said so; null otherwise.
    viewOnly: null,
    review,
    readOnly: c.mode === "view" || review,
  };
}

/* The config a guarded /c/ page renders with, from its access.json answer.
 * Asked once, before the first render, so editing never appears and then
 * goes. Anything but a clear yes is a page to watch: no answer means sign-in
 * was slow or the network was down, and an editable page whose saves would
 * all be refused is worse than one that says so. */
export function accessMode(config, access) {
  if (!access || typeof access.edit !== "boolean")
    return { ...config, readOnly: true, viewOnly: { reason: "unchecked", team: null } };
  if (access.edit) return { ...config, viewOnly: null };
  return { ...config, readOnly: true,
           viewOnly: { reason: access.reason, team: access.team?.name ?? null } };
}

/* The header's line on a page that may not edit (accessMode's viewOnly). */
export function viewOnlyText(why) {
  if (!why) return "";
  if (why.reason === "signin")
    return "View only — sign in to edit if this chart is yours or your team's";
  if (why.reason === "not-yours")
    return why.team ? `View only — only team ${why.team} can change this chart`
                    : "View only — only the person or team who made this chart can change it";
  return "View only — couldn't check sign-in. Reload to try again.";
}

/* The body of a merge: every dirty shot, and null for one edited away. A
 * tombstone only has to survive the request -- the server turns it into a
 * field delete and nothing is left behind. */
export function dirtyPayload(overrides, dirty) {
  const body = {};
  for (const k of dirty) body[k] = overrides[k] ?? null;
  return body;
}

export function saveUrl(config, version) {
  const q = [];
  if (config.merge) q.push("merge=1");
  if (version !== null && version !== undefined) q.push(`v=${version}`);
  return "overrides.json" + (q.length ? `?${q.join("&")}` : "");
}

/* The shot the charter has their hands on right now. Nothing arriving from
 * the server may overwrite it: the miss reason and the note patch on every
 * keystroke, so there is a moment after each debounced save where the key is
 * clean and redrawing it would take the caret with it. Skipped keys are not
 * lost -- the next poll brings them once focus has moved on. */
export const busyKeyOf = ({ dragging, editingField }, key) =>
  (dragging || editingField ? key : null);

/* Fold the server's map into ours, keeping anything we have not saved yet.
 *
 * Returns a new map and the keys that actually moved -- or the map we were
 * given, unchanged and with the same identity, when nothing did. That
 * identity is load-bearing: it is the memo key the whole game view hangs off,
 * so a poll that brings no news must not invalidate it. */
export function reconcile(overrides, dirty, busy, serverMap) {
  const next = { ...overrides };
  const touched = [];
  for (const k of Object.keys(serverMap)) {
    if (dirty.has(k) || k === busy) continue;
    if (JSON.stringify(next[k]) === JSON.stringify(serverMap[k])) continue;
    next[k] = serverMap[k];
    touched.push(k);
  }
  for (const k of Object.keys(overrides)) {
    if (k in serverMap || dirty.has(k) || k === busy) continue;
    delete next[k];                               // a teammate cleared it
    touched.push(k);
  }
  return touched.length ? { overrides: next, touched } : { overrides, touched };
}

/* A beacon cannot read the reply, so it sends no version and saves
 * unconditionally: better a last-writer save than losing the last minute of
 * grading. On a merge that costs nobody else anything -- only the shots in
 * hand go up, so a closing tab can no longer erase a teammate's work. It is
 * also what keeps the payload inside the ~64 KiB a beacon is allowed, which a
 * whole hand-placed game is not. */
export function unloadBeacon(config, overrides, dirty) {
  if (config.readOnly || !dirty.size) return null;
  return {
    url: config.merge ? "overrides.json?merge=1" : "overrides.json",
    body: config.merge ? dirtyPayload(overrides, dirty) : overrides,
  };
}

/* What a poll or a save that brought a teammate's work says, from the keys
 * that moved. Only a shot key -- three parts, game.end.rock -- is a shot
 * somebody charted; a two-part end key is a doubles team's role swap, and
 * calling that "charted 1 shot" names a shot nobody touched. */
export function chartedNotice(keys) {
  const shots = keys.filter(k => String(k).split(".").length === 3).length;
  if (shots) return `Someone else charted ${shots} shot${shots === 1 ? "" : "s"}`;
  const ends = keys.length;
  return ends === 1 ? "Someone else changed who threw in an end"
                    : `Someone else changed who threw in ${ends} ends`;
}

/* Schema 4 is where `end.score` and `game.final` became the board's own
 * figures. Before that they held the detector's *inferred* score in those
 * same fields -- the exact number this feature exists to stop showing. A
 * chart built by an older run gets no board score rendered at all, rather
 * than risk one being mistaken for the club's. Mirrors timeline.SCHEMA_VERSION
 * in src/curling_score/timeline.py.
 *
 * Fails closed: `Number(x) >= 4` is false for a missing, null, or
 * non-numeric `schema_version`, not just a low one. A document we cannot
 * date is more likely to predate board reading than not, so an unreadable
 * version number is refused rather than assumed current -- the same bet
 * `doc.schema_version < 4` got backwards, since `undefined < 4` is false
 * and its negation waved an undated document through as readable. */
export const boardReadable = doc => Number(doc?.schema_version) >= 4;
