/* The token a guarded chart's saves carry (api.py may_edit_chart).
 *
 * `fresh()` is asked before every save. `last()` is what the close-of-tab
 * save sends: pagehide cannot wait for a promise, so the token has to be in
 * hand already, and keepFresh() keeps it so. Firebase renews a token only in
 * the last five minutes of its hour, and a five-minute tick is sure to land
 * in that window. */
import { idToken } from "./auth.mjs";

export const TOKEN_TICK_MS = 5 * 60 * 1000;

export function chartAuth(get = idToken) {
  let last = null;

  async function fresh(force = false) {
    const t = await get({ force });
    if (t === undefined) return last;   // no answer: keep what we had
    last = t;                           // a token, or null once signed out
    return last;
  }

  /* Started once by the page; the returned function stops it. */
  function keepFresh() {
    const id = setInterval(() => { fresh(); }, TOKEN_TICK_MS);
    const onShow = () => { if (document.visibilityState === "visible") fresh(); };
    addEventListener("visibilitychange", onShow);
    return () => { clearInterval(id); removeEventListener("visibilitychange", onShow); };
  }

  return { fresh, last: () => last, keepFresh };
}
