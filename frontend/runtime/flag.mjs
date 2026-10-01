/* Sending a flag, and finding out who is sending it (runtime/auth.mjs). */
import { SIGN_IN_WAIT_MS, whoIsSignedIn } from "./auth.mjs";

export { SIGN_IN_WAIT_MS };

/* {email, token} for a signed-in person, null for nobody, undefined when auth
 * has not answered in time. The dialog asks again at Send, so a slow first
 * answer costs the display, never the attribution. */
export const whoIsFlagging = whoIsSignedIn;

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
