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
    review,
    readOnly: c.mode === "view" || review,
  };
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

export const chartedNotice = n =>
  `Someone else charted ${n} shot${n === 1 ? "" : "s"}`;
