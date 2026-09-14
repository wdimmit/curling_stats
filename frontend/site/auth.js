"use strict";
/* The only file that knows Firebase exists.
 *
 * Deliberately not loaded by the charting viewer. Saving grading is
 * unauthenticated on purpose -- the link is the permission -- so the page a
 * person spends an hour in stays free of all of this, and keeps working
 * offline under `curling-score serve`.
 *
 * Everything here fails soft. If the config says accounts are off, if the
 * network is down, if a token will not refresh, the page carries on as an
 * anonymous one: that is the state the whole site was in until accounts
 * existed, and every route still handles it.
 */
/* Loaded on demand, not at the top.
 *
 * A static import of a CDN module makes that CDN a hard dependency of the
 * whole bundle: if gstatic cannot be reached the module never evaluates, and
 * with one bundle for the site that is every page blank rather than every
 * page anonymous. Which contradicts the promise three lines up. Imported
 * inside the async setup below, an unreachable SDK is just another way for
 * accounts to be off.
 *
 * esbuild leaves these alone (--external:https://*), so the CDN copy is still
 * shared and cached rather than inlined into the bundle. */
const SDK = "https://www.gstatic.com/firebasejs/12.19.0/";
let fb = null;

let auth = null;
let user = null;
let ready = false;
const listeners = new Set();

function announce() {
  for (const fn of listeners) { try { fn(user, ready); } catch (err) { console.error(err); } }
}

const started = (async () => {
  let cfg = {};
  try { cfg = await (await fetch("/api/auth/config")).json(); } catch { /* offline */ }
  if (!cfg.enabled) { ready = true; announce(); return; }
  try {
    const [app, sdk] = await Promise.all([
      import(/* @vite-ignore */ `${SDK}firebase-app.js`),
      import(/* @vite-ignore */ `${SDK}firebase-auth.js`),
    ]);
    fb = sdk;
    auth = sdk.getAuth(app.initializeApp({ apiKey: cfg.apiKey, authDomain: cfg.authDomain,
                                           projectId: cfg.projectId }));
    if (cfg.emulator)
      sdk.connectAuthEmulator(auth, `http://${cfg.emulator}`, { disableWarnings: true });
    sdk.onAuthStateChanged(auth, u => { user = u; ready = true; announce(); });
  } catch {
    // The SDK could not be fetched. Same outcome as accounts being switched
    // off, which every route already handles.
    auth = null;
    ready = true;
    announce();
  }
})();

/** Call fn(user, ready) now and on every change. */
export function onUser(fn) { listeners.add(fn); fn(user, ready); return () => listeners.delete(fn); }

export const enabled = () => auth !== null;
export const currentUser = () => user;
export const whenReady = () => started;

export async function signIn() {
  if (!auth || !fb) throw new Error("accounts are not configured");
  // Popup, not redirect: a redirect needs the auth handler to be same-origin
  // or third-party cookies to survive, and neither is true here.
  return fb.signInWithPopup(auth, new fb.GoogleAuthProvider());
}

export async function signOff() { if (auth && fb) await fb.signOut(auth); }

/** fetch(), carrying who you are if you are anybody. Never breaks the page. */
export async function authedFetch(url, opts = {}) {
  const headers = new Headers(opts.headers || {});
  if (user) {
    try { headers.set("Authorization", "Bearer " + await user.getIdToken()); }
    catch { /* offline, or a refresh that failed: go on as anonymous */ }
  }
  const res = await fetch(url, { ...opts, headers });
  if (res.status !== 401 || !user) return res;
  // One forced refresh, then give up. A stale token is the only 401 worth retrying.
  try { headers.set("Authorization", "Bearer " + await user.getIdToken(true)); }
  catch { return res; }
  return fetch(url, { ...opts, headers });
}
