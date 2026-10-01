/* Who is signed in, asked for on demand.
 *
 * The viewer does not load Firebase (see site/auth.js, lines 1-12). Asking
 * who is signed in imports that module on demand -- esbuild inlines it but
 * does not evaluate it until this runs -- so a charting session that never
 * asks never fetches the SDK. The session is per origin, so someone signed
 * in on the site is signed in here. All of it fails soft: no answer within
 * SIGN_IN_WAIT_MS is "not known", never a stuck page.
 *
 * Moved out of flag.mjs when the score picker came to need the same answer. */
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

/* Whether accounts exist here at all: not under `curling-score serve`, nor
 * when the site's config turns them off. */
export async function accountsOn(ms = SIGN_IN_WAIT_MS) {
  const auth = await settleWithin(import("../site/auth.js"), ms, null);
  if (!auth) return false;
  await settleWithin(auth.whenReady(), ms, null);
  return auth.enabled();
}

/* {email, token} for a signed-in person, null for nobody, undefined when auth
 * has not answered within `ms`. The token is asked for on every call:
 * Firebase hands back a fresh one when it is near expiry, and a page left
 * open past the hour would otherwise send a dead token, which the server can
 * only read as anonymous. */
export async function whoIsSignedIn(ms = SIGN_IN_WAIT_MS) {
  const u = await settleWithin(signedIn(), ms, undefined);
  if (!u) return u;
  const token = await settleWithin(u.getIdToken(), ms, null);
  return token ? { email: u.email ?? null, token } : undefined;
}

/* Google's sign-in popup. Resolves once signed in; throws if it was shut. */
export async function signInNow() {
  const auth = await import("../site/auth.js");
  return auth.signIn();
}
