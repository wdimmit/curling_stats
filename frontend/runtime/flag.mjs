/* Sending a flag, and finding out who is sending it.
 *
 * The viewer does not load Firebase (see site/auth.js, lines 1-12). Asking
 * who is signed in imports that module on demand -- esbuild inlines it but
 * does not evaluate it until this runs -- so a charting session that never
 * opens the dialog never fetches the SDK. The session is per origin, so
 * someone signed in on the site is signed in here. All of it fails soft:
 * no answer within SIGN_IN_WAIT_MS is an anonymous flag, not a stuck one. */
import { settleWithin, settledUser } from "../core/index.mjs";

export const SIGN_IN_WAIT_MS = 3000;

// One wait for auth to decide, shared by every call: a second call after a
// slow first one picks up the answer that has arrived since.
let settled = null;

async function signedIn() {
  const auth = await import("../site/auth.js");
  await (settled ??= settledUser(auth.onUser));
  return auth.currentUser();
}

/* {email, token} for a signed-in person, null for nobody, undefined when auth
 * has not answered within `ms`. The dialog asks again at Send, so a slow
 * first answer costs the display, never the attribution. The token is asked
 * for on every call: Firebase hands back a fresh one when it is near expiry,
 * and a dialog left open past the hour would otherwise send a dead token,
 * which the server can only read as anonymous. */
export async function whoIsFlagging(ms = SIGN_IN_WAIT_MS) {
  const u = await settleWithin(signedIn(), ms, undefined);
  if (!u) return u;
  const token = await settleWithin(u.getIdToken(), ms, null);
  return token ? { email: u.email ?? null, token } : undefined;
}

const FAILED = "Could not send. Try again.";

export async function sendFlag(body, token) {
  try {
    const r = await fetch("/api/flags", {
      method: "POST",
      headers: { "Content-Type": "application/json",
                 ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: JSON.stringify(body),
    });
    const data = await r.json().catch(() => ({}));
    if (r.ok) return { ok: true, id: data.id };
    return { ok: false, error: typeof data.detail === "string" ? data.detail : FAILED };
  } catch {
    return { ok: false, error: FAILED };
  }
}
