"use strict";
/* The identity chip in a page header.
 *
 * Every page renders signed out and this fills it in a moment later, once the
 * SDK has read its own storage. There is no way to know sooner: the server
 * renders these pages by string replacement and has never been told who is
 * asking.
 */
import { onUser, signIn, signOff, enabled } from "./auth.js";

const el = document.getElementById("who");

function render(user, ready) {
  if (!el) return;
  if (!ready) { el.textContent = ""; return; }
  if (!enabled()) { el.textContent = ""; return; }   // accounts are switched off
  el.textContent = "";
  if (user) {
    const name = document.createElement("a");
    name.href = "/mine";
    name.textContent = user.displayName || user.email || "My games";
    const out = document.createElement("button");
    out.textContent = "Sign out";
    out.onclick = () => signOff();
    el.append(name, " ", out);
  } else {
    const btn = document.createElement("button");
    btn.textContent = "Sign in";
    btn.onclick = async () => {
      btn.disabled = true;
      try { await signIn(); }
      catch (err) { el.append(` ${err.code || err.message}`); }
      finally { btn.disabled = false; }
    };
    el.append(btn);
  }
}

onUser(render);
