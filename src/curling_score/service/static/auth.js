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
import { initializeApp } from "https://www.gstatic.com/firebasejs/12.19.0/firebase-app.js";
import {
  getAuth, GoogleAuthProvider, signInWithPopup, signOut,
  onAuthStateChanged, connectAuthEmulator,
} from "https://www.gstatic.com/firebasejs/12.19.0/firebase-auth.js";

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
  auth = getAuth(initializeApp({ apiKey: cfg.apiKey, authDomain: cfg.authDomain,
                                 projectId: cfg.projectId }));
  if (cfg.emulator) connectAuthEmulator(auth, `http://${cfg.emulator}`, { disableWarnings: true });
  onAuthStateChanged(auth, u => { user = u; ready = true; announce(); });
})();

/** Call fn(user, ready) now and on every change. */
export function onUser(fn) { listeners.add(fn); fn(user, ready); return () => listeners.delete(fn); }

export const enabled = () => auth !== null;
export const currentUser = () => user;
export const whenReady = () => started;

export async function signIn() {
  if (!auth) throw new Error("accounts are not configured");
  // Popup, not redirect: a redirect needs the auth handler to be same-origin
  // or third-party cookies to survive, and neither is true here.
  return signInWithPopup(auth, new GoogleAuthProvider());
}

export async function signOff() { if (auth) await signOut(auth); }

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
