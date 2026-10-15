/* Whether this person may edit the chart a guarded page shows (api.py
 * access.json), asked once by the boot before it renders. Answers the
 * server's reply, or null for no answer -- which accessMode reads as
 * "unchecked", never as "signed out": a slow or missing sign-in SDK must not
 * tell the owner their own chart is someone else's. */
import { accountsOn, whoIsSignedIn } from "./auth.mjs";

export async function checkAccess({ who: ask = whoIsSignedIn, accounts = accountsOn } = {}) {
  const who = await ask();
  if (who === undefined) return null;
  // Nobody, and accounts reading as off: the server only marks a page guarded
  // when accounts are on, so this is the SDK or its config failing to load
  // (blocked, offline), not a signed-out person -- and a Sign in button would
  // never work. Only "couldn't check" is true.
  if (who === null && !(await accounts())) return null;
  try {
    const r = await fetch("access.json",
      who ? { headers: { Authorization: `Bearer ${who.token}` } } : undefined);
    return r.ok ? await r.json() : null;
  } catch {
    return null;
  }
}
