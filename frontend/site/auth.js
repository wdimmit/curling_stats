"use strict";
/* The only file that knows Firebase exists.
 *
 * Both ways in start here, Google's popup and a link emailed to them, from
 * one dialog that does not know Firebase: signInDialog.js is plain DOM, handed
 * the few functions it needs (api() below), and loaded only when somebody
 * signs in.
 *
 * The charting viewer loads this only where it has to: on a guarded /c/
 * page (a chart somebody or a team owns), whose saves carry the person's
 * token (api.py may_edit_chart), and when somebody opens Flag, I played… or
 * Name teams. A view link, a review page, a chart nobody owns and the local
 * `curling-score serve` never load it, so they keep working offline.
 *
 * Everything here fails soft. If the config says accounts are off, if the
 * network is down, if a token will not refresh, the page carries on as an
 * anonymous one: that is the state the whole site was in until accounts
 * existed, and every route still handles it.
 */
import { continueUrl, forget, isFresh, newRequestId, recall, remember, resend,
         safeNext } from "./signinState.mjs";

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
// Bumped when the signed-in User changes in place (setName), which no
// onAuthStateChanged reports: useAuth.js watches it as well as the user.
let pv = 0;
// The last link this page sent, for a browser whose storage keeps nothing.
let sent = null;
// Whether the site offers an emailed link beside Google (api.py
// Settings.email_link_signin): off until Firebase will send them.
let emailLink = false;

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
    emailLink = !!cfg.emailLink;
    // The first answer is the session restored from storage: only then is it
    // known that nobody is signed in, and worth offering the code box back.
    let first = true;
    sdk.onAuthStateChanged(auth, u => {
      user = u; ready = true; announce();
      if (first && !u) resumeWaiting();
      first = false;
    });
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
/** Whether "Email me a link" is offered. Without it the chooser is Google
 * alone; /signin still finishes a link that was sent while it was on. */
export const emailLinkOn = () => auth !== null && emailLink;

export const profileVersion = () => pv;

/** The chooser: Google, or a link emailed to them. Resolves with the User
 * once signed in either way; closing it rejects with the code a shut Google
 * popup gives, which is what every caller already expects. */
export async function signIn() {
  if (!auth || !fb) throw new Error("accounts are not configured");
  const u = await (await import("./signInDialog.js")).openSignIn(api());
  forget(store());      // whichever way in, no link is being waited on now
  return u;
}

export async function signInWithGoogle() {
  if (!auth || !fb) throw new Error("accounts are not configured");
  // Popup, not redirect: a redirect needs the auth handler to be same-origin
  // or third-party cookies to survive, and neither is true here.
  return (await fb.signInWithPopup(auth, new fb.GoogleAuthProvider())).user;
}

/** Sign in if nobody is, and resolve once this page knows who.
 *
 * The chooser can settle a moment before onAuthStateChanged has told this
 * module, and authedFetch reads the module's `user`, so the caller waits for
 * that rather than for the dialog. Throws if the dialog was closed. */
export async function ensureSignedIn() {
  if (user) return user;
  await signIn();
  if (user) return user;
  return new Promise(resolve => {
    let off = null;
    off = onUser(u => { if (u) { off?.(); resolve(u); } });
  });
}

export async function signOff() { if (auth && fb) await fb.signOut(auth); }

/* --- signing in with an emailed link ---------------------------------------
 *
 * The link carries only a request id (signinState continueUrl). The address
 * and the page to go back to stay in this browser, so a link opened anywhere
 * else has nothing to leak and nowhere to send anyone. Opened elsewhere, the
 * /signin page leaves it with the server behind a code (relayLink), and the
 * tab that asked trades the code for it (claimCode) and finishes here, with
 * the address it stored itself: a stranger's code is no use to it. */

/* localStorage, or null where even touching it throws (blocked site data).
 * signinState treats null as storage that refuses, which costs a retype. */
function store() {
  try { return localStorage; } catch { return null; }
}

/** Email a sign-in link, and remember the request: {email, id, next, at}.
 *
 * Asking again for the same address while the request is fresh keeps its id,
 * so the link in the first email still works; `next` is this page either way,
 * as safeNext has it. Asked for on /signin, that is "/": the address there
 * can be a link, oobCode and all, which has no business in storage. */
export async function sendLink(email) {
  if (!auth || !fb) throw new Error("accounts are not configured");
  const now = Date.now();
  const next = safeNext(location.pathname + location.search + location.hash);
  const prior = recall(store()) ?? sent;
  const pending = prior && prior.email === email && isFresh(prior, now)
    ? { ...resend(prior, now), next }
    : { email, id: newRequestId(), next, at: now };
  await fb.sendSignInLinkToEmail(auth, email, {
    url: continueUrl(location.origin, pending.id), handleCodeInApp: true });
  remember(store(), pending);
  sent = pending;
  return pending;
}

/** Finish an emailed link with the address it was sent to, and resolve once
 * this page knows who, as ensureSignedIn does. */
export async function finishWithLink(email, link) {
  if (!auth || !fb) throw new Error("accounts are not configured");
  const { user: who } = await fb.signInWithEmailLink(auth, email, link);
  forget(store());
  sent = null;
  if (user?.uid === who.uid) return user;
  return new Promise(resolve => {
    let off = null;
    off = onUser(u => { if (u?.uid === who.uid) { off?.(); resolve(u); } });
  });
}

export async function isLink(url) {
  await started;
  return !!(auth && fb) && fb.isSignInWithEmailLink(auth, url);
}

const failure = (what, res) => Object.assign(new Error(`${what}: ${res.status}`),
                                             { status: res.status });

/** Hold a link that opened in the wrong browser: {code, expires_at}. */
export async function relayLink(link) {
  const res = await fetch("/api/signin/relay", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ link }) });
  if (!res.ok) throw failure("relay", res);
  return res.json();
}

/** Trade a relay's code for its link: {link}, or {error} for a code that is
 * wrong, one that has run out, or too many tries from here. */
export async function claimCode(id, code) {
  const res = await fetch(`/api/signin/relay/${encodeURIComponent(id)}/claim`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code }) });
  if (res.ok) return { link: (await res.json()).link };
  const error = { 404: "wrong", 410: "gone", 429: "busy" }[res.status];
  if (error) return { error };
  throw failure("claim", res);
}

/** Whether the account has no name yet. Not knowing is no: the name step is
 * never worth holding up a sign-in for. */
export async function needsName() {
  try {
    const res = await authedFetch("/api/me");
    return res.ok && !(await res.json()).user?.name;
  } catch {
    return false;
  }
}

/** Save the name teammates see, on the account and as the Firebase display
 * name. updateProfile changes the User in place and fires nothing, hence pv. */
export async function setName(name) {
  const res = await authedFetch("/api/me/name", {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }) });
  if (!res.ok) throw failure("name", res);
  const saved = (await res.json()).name;
  try { if (user && fb) await fb.updateProfile(user, { displayName: saved }); }
  catch { /* the account has it; the header shows it from the next sign-in */ }
  pv++;
  announce();
  return saved;
}

/** The name step on its own, for /signin, which reaches the dialog only
 * through here. */
export async function askName() {
  return (await import("./signInDialog.js")).askName(api());
}

/* What the dialog may do. It is handed these rather than importing this
 * module, so it never sees Firebase and the import runs one way. */
function api() {
  return { signInWithGoogle, sendLink, claimCode, finishWithLink, onUser, needsName,
           setName, currentUser, emailLink: emailLinkOn(),
           pending: () => recall(store()) };
}

/* A phone that drops the tab while its person is in the mail app reloads it
 * on the way back. If the request it was waiting on is still fresh and was
 * made on this page, the code box comes back. The page's own action (the
 * Chart or Join that asked for a sign-in) went with the reload, so once
 * signed in the page loads again, signed in. Not while emailed links are
 * off: the chooser offers none to wait on. */
function resumeWaiting() {
  if (!emailLinkOn()) return;
  try {
    const pending = recall(store());
    if (!isFresh(pending, Date.now())) return;
    if (safeNext(pending.next).split("#")[0] !== location.pathname + location.search) return;
    import("./signInDialog.js")
      .then(d => d.openSignIn(api(), { resume: pending }))
      .then(() => { forget(store()); location.reload(); },
            () => { /* closed, or the dialog would not load: the page carries on */ });
  } catch { /* nothing to resume */ }
}

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
