/* Sending a flag, and finding out who is sending it.
 *
 * The viewer does not load Firebase (see site/auth.js, lines 1-12). Asking
 * who is signed in imports that module on demand -- esbuild inlines it but
 * does not evaluate it until this runs -- so a charting session that never
 * opens the dialog never fetches the SDK. The session is per origin, so
 * someone signed in on the site is signed in here. All of it fails soft:
 * no answer within SIGN_IN_WAIT_MS is an anonymous flag, not a stuck one. */
import { settleWithin } from "../core/index.mjs";

export const SIGN_IN_WAIT_MS = 3000;

export function whoIsFlagging() {
  return settleWithin((async () => {
    const auth = await import("../site/auth.js");
    await auth.whenReady();
    const u = auth.currentUser();
    if (!u) return null;
    return { email: u.email ?? null, token: await u.getIdToken() };
  })(), SIGN_IN_WAIT_MS, null);
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
