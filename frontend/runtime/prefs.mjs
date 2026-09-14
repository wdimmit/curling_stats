/* The five settings that belong to the person, not to the chart.
 *
 * Kept under one key and type-checked on the way in: a browser that has never
 * seen this page, or one with storage blocked, must get the defaults rather
 * than a crash.
 */
const KEY = "curlchart";

export const DEFAULTS = {
  showTrack: true, autoplay: true, leadIn: 10,
  clockOpen: false, clockBars: false,
};

export function loadPrefs() {
  const out = { ...DEFAULTS };
  try {
    const p = JSON.parse(localStorage.getItem(KEY) || "{}");
    for (const [k, v] of Object.entries(DEFAULTS))
      if (typeof p[k] === typeof v) out[k] = p[k];
  } catch { /* a fresh browser, or storage blocked: defaults are fine */ }
  return out;
}

export function savePrefs(prefs) {
  try {
    localStorage.setItem(KEY, JSON.stringify(
      Object.fromEntries(Object.keys(DEFAULTS).map(k => [k, prefs[k]]))));
  } catch { /* not important enough to bother the user about */ }
}

/* Where the charter had got to, so a reload does not cost them their place.
 * Session rather than local: it is about this tab and this sitting, and a
 * week-old cursor into a game you finished is noise. */
const CURSOR = "curlchart:cursor";

export function loadCursor(slug) {
  try {
    const c = JSON.parse(sessionStorage.getItem(CURSOR) || "null");
    if (c && c.slug === slug && [c.gi, c.ei, c.si].every(Number.isInteger))
      return { gi: c.gi, ei: c.ei, si: c.si };
  } catch { /* same as above */ }
  return null;
}

export function saveCursor(slug, { gi, ei, si }) {
  try {
    sessionStorage.setItem(CURSOR, JSON.stringify({ slug, gi, ei, si }));
  } catch { /* same as above */ }
}
