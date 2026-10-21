/* The arithmetic of emailed-link sign-in, with no Firebase and no DOM.
 *
 * The sign-in dialog and the /signin page both have to decide the same small
 * things: is this text an address, is that text a code, where may we send
 * the person afterwards, is the request we remembered still worth waiting
 * for. Keeping those here, with storage and the clock handed in, means they
 * are tested in node rather than by driving a browser, and the two screens
 * cannot drift apart.
 *
 * The emailed link carries only the request id. Everything else -- the
 * address, where to go next -- stays in this browser's localStorage, so a
 * link opened somewhere else has nothing to leak and nothing to redirect to.
 */

/* Where the pending request lives, and how long a link is worth waiting for. */
export const PENDING_KEY = "signin.pending";
export const FRESH_MS = 15 * 60 * 1000;

/* What the server accepts as a request id (service/signin.py REQUEST_ID). */
const REQUEST_ID = /^[A-Za-z0-9_-]{22}$/;

/* A fresh request id: 16 random bytes as unpadded base64url, 22 characters.
 * `fill` is the random source, so a test can pass a fixed one. */
export function newRequestId(fill = b => globalThis.crypto.getRandomValues(b)) {
  const bytes = new Uint8Array(16);
  fill(bytes);
  return globalThis.btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
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
