/* The arithmetic of emailed-link sign-in, with no Firebase and no DOM.
 *
 * The sign-in dialog and the /signin page both have to decide the same small
 * things: is this text an address, is that text a code, where may we send
 * the person afterwards, is the request we remembered still worth waiting
 * for, what to say when a step fails. Keeping those here, with storage and
 * the clock handed in, means they are tested in node rather than by driving
 * a browser, and the two screens cannot drift apart.
 *
 * The emailed link carries only the request id. Everything else -- the
 * address, where to go next -- stays in this browser's localStorage, so a
 * link opened somewhere else has nothing to leak and nothing to redirect to.
 */

/* Where the pending request lives, and how long a link is worth waiting for. */
export const PENDING_KEY = "signin.pending";
export const FRESH_MS = 15 * 60 * 1000;

/* What the server accepts as a request id (service/signin.py REQUEST_ID):
 * 22 base64url characters, without "__" at either end, which Firestore
 * reserves for ids of its own. The end is checked by looking ahead for 20
 * characters and then "__", not by a lookbehind: Safari before 16.4 cannot
 * parse one, and a file holding one fails to load at all. */
const REQUEST_ID = /^(?!__)(?![A-Za-z0-9_-]{20}__$)[A-Za-z0-9_-]{22}$/;

/* A fresh request id: 16 random bytes as unpadded base64url, 22 characters.
 * `fill` is the random source, so a test can pass a fixed one. About one draw
 * in 4096 starts "__", which the server would refuse, so that one is drawn
 * again. (It can never end "__": the last character carries only two bits.) */
export function newRequestId(fill = b => globalThis.crypto.getRandomValues(b)) {
  const bytes = new Uint8Array(16);
  for (;;) {
    fill(bytes);
    const id = globalThis.btoa(String.fromCharCode(...bytes))
      .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
    if (REQUEST_ID.test(id)) return id;
  }
}

/* The address Firebase will put in the email. Only the id travels. */
export function continueUrl(origin, id) {
  return `${origin}/signin?r=${id}`;
}

/* The request id on a landing URL, or null if there is none the server
 * would accept. Firebase appends its own parameters; they are ignored. */
export function requestIdOf(href) {
  let id;
  try {
    id = new URL(href, "https://signin.invalid").searchParams.get("r");
  } catch {
    return null;
  }
  return id !== null && REQUEST_ID.test(id) ? id : null;
}

/* What was typed into the address box, trimmed, or null if it cannot be an
 * address: exactly one @, something on each side, no whitespace. Whether the
 * mailbox exists is the mail server's business. */
export function cleanEmail(text) {
  if (typeof text !== "string") return null;
  const s = text.trim();
  const parts = s.split("@");
  if (parts.length !== 2 || !parts[0] || !parts[1] || /\s/.test(s)) return null;
  return s;
}

/* The six digits, however they were typed or pasted ("482 913", "482-913"),
 * or null if what is left is not exactly six digits. */
export function parseCode(text) {
  if (typeof text !== "string") return null;
  const s = text.replace(/[\s-]/g, "");
  return /^[0-9]{6}$/.test(s) ? s : null;
}

/* The code as it is shown: two threes, easier to read out and to copy. */
export function formatCode(code) {
  return `${code.slice(0, 3)} ${code.slice(3)}`;
}

/* Where to go once signed in: `next` if it is a path on this site, else the
 * front page. `//host` and `/\host` are other sites to a browser, and
 * /signin would only bring the person back here. */
export function safeNext(next) {
  if (typeof next !== "string" || !next.startsWith("/")) return "/";
  if (next.startsWith("//") || next.startsWith("/\\")) return "/";
  if (/^\/signin(?:[/?#]|$)/.test(next)) return "/";
  return next;
}

/* What a visit to /signin is for, once the SDK has said who is here:
 *   {is: "off"}            accounts are off on this site.
 *   {is: "plain"}          no link, and nobody signed in: a Sign in button.
 *   {is: "go", next}       somebody is signed in and there is nothing to do:
 *                          no link, or a link this browser asked for, which
 *                          another tab finished or was not needed. It is left
 *                          unused, as it came.
 *   {is: "finish", next}   this browser asked for this link, and nobody is
 *                          signed in: finish it with the address it stored.
 *   {is: "relay"}          anything else: a link some other browser asked for
 *                          -- whoever is signed in here, since that browser is
 *                          waiting for the code -- or one whose `r` is missing
 *                          or malformed, which is never this browser's own.
 * `pending` is what this browser remembered (recall), `href` the address the
 * link landed on. */
export function landing({ accounts, signedIn, isLink, pending, href }) {
  if (!accounts) return { is: "off" };
  if (!isLink) return signedIn ? { is: "go", next: "/" } : { is: "plain" };
  const id = requestIdOf(href);
  if (id === null || pending?.id !== id) return { is: "relay" };
  return { is: signedIn ? "go" : "finish", next: safeNext(pending.next) };
}

/* What a Sign in button says when signIn() fails: nothing when the chooser
 * was only closed, which is changing your mind, not an error. */
export function signInTrouble(e) {
  return e?.code === "auth/popup-closed-by-user" ? "" : e?.code || e?.message || "";
}

/* A step of signing in that failed: what, and why -- Firebase's code, or the
 * status of a request to this site -- for whoever is asked about it. */
export function failed(what, e) {
  const why = e?.code ?? e?.status;
  return `${what}${why ? ` (${why})` : ""}. Try again.`;
}

/* The same for a step of the emailed link, except that Firebase refusing it
 * outright -- the provider switched off in its console -- is said plainly,
 * since trying again will not help. */
export const EMAIL_OFF = "Signing in by email isn't available right now.";
export function emailFailed(what, e) {
  return e?.code === "auth/operation-not-allowed" ? EMAIL_OFF : failed(what, e);
}

/* The pending request, as {email, id, next, at}. Storage can be missing,
 * full or refused (private windows, blocked site data), so every call is
 * guarded; losing it only costs the person a retype. */
export function remember(storage, pending) {
  try {
    storage.setItem(PENDING_KEY, JSON.stringify(pending));
  } catch { /* nothing remembered */ }
}

export function recall(storage) {
  try {
    const p = JSON.parse(storage.getItem(PENDING_KEY));
    const ok = p && typeof p === "object" && typeof p.email === "string"
      && typeof p.id === "string" && typeof p.next === "string"
      && typeof p.at === "number";
    return ok ? p : null;
  } catch {
    return null;
  }
}

export function forget(storage) {
  try {
    storage.removeItem(PENDING_KEY);
  } catch { /* nothing to forget */ }
}

/* Is the request recent enough to keep waiting on? */
export function isFresh(pending, now, maxAgeMs = FRESH_MS) {
  return !!pending && now - pending.at < maxAgeMs;
}

/* "Send again": the same request, so a link already in the inbox still
 * works, with the clock restarted. */
export function resend(pending, now) {
  return { email: pending.email, id: pending.id, next: pending.next, at: now };
}
