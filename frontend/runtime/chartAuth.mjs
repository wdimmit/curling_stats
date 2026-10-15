/* The token a guarded chart's saves carry (api.py may_edit_chart).
 *
 * `fresh()` is asked before every save. `last()` is what the close-of-tab
 * save sends: pagehide cannot wait for a promise, so the token has to be in
 * hand already, and keepFresh() keeps it so. Firebase renews a token only
 * within 30 seconds of its end when asked, and its own early refresh runs only
 * for other Firebase products, so the tick reads the token's expiry and
 * renews it itself once less than RENEW_BEFORE_MS is left: the next tick,
 * five minutes on (or six, in a throttled background tab), still finds it
 * valid. */
import { idToken } from "./auth.mjs";

export const TOKEN_TICK_MS = 5 * 60 * 1000;
export const RENEW_BEFORE_MS = 7 * 60 * 1000;

/* When an ID token runs out, in ms, read from its own payload; null for
 * anything that is not one. */
export function expiresAt(token) {
  try {
    const part = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    const exp = JSON.parse(atob(part)).exp;
    return typeof exp === "number" ? exp * 1000 : null;
  } catch {
    return null;
  }
}

export function chartAuth(get = idToken) {
  let last = null;

  async function fresh(force = false) {
    const t = await get({ force });
    if (t === undefined) return last;   // no answer: keep what we had
    last = t;                           // a token, or null once signed out
    return last;
  }

  async function keepCurrent() {
    const t = await fresh();
    const end = t ? expiresAt(t) : null;
    if (end !== null && end - Date.now() < RENEW_BEFORE_MS) await fresh(true);
  }

  /* Started once by the page; the returned function stops it. */
  function keepFresh() {
    const id = setInterval(() => { keepCurrent(); }, TOKEN_TICK_MS);
    const onShow = () => { if (document.visibilityState === "visible") keepCurrent(); };
    addEventListener("visibilitychange", onShow);
    return () => { clearInterval(id); removeEventListener("visibilitychange", onShow); };
  }

  return { fresh, last: () => last, keepFresh };
}
