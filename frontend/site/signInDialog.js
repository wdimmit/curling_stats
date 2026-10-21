/* The sign-in dialog: Google, or a link emailed to you.
 *
 * Plain DOM on a native <dialog>, not React. auth.js signIn() opens it, and
 * signIn() is called from both bundles -- the site's pages and the charting
 * viewer, which inlines auth.js but evaluates it only on demand
 * (runtime/auth.mjs) -- so neither React tree has to host it, and a viewer
 * session nobody signs in from never builds it. showModal() brings the focus
 * trap, Escape and the backdrop, and the top layer: above the phone shell,
 * and above another dialog that asked for the sign-in (Name teams, I played…).
 *
 * It never touches Firebase. Everything it does goes through the `api`
 * auth.js hands it (auth.js api()), and the small decisions -- is this an
 * address, is that a code -- are signinState's.
 *
 * Three views. Choose: Google, or an address to email a link to -- the
 * address only once the site has switched emailed links on (api.emailLink),
 * and until then Google alone. Waiting: for that link, with a box for the
 * code the link shows when it opens in some other browser (a mail app's own,
 * often, on a phone). Name: asked once, after this tab finishes a link
 * sign-in for an account that has none. */
import { cleanEmail, emailFailed, failed, isFresh, parseCode } from "./signinState.mjs";

const WRONG = "That code isn't right.";
const GONE = "That code has run out — send a new link.";
const LINKED = "This address signs in with an emailed link.";
const BUSY = "Too many tries. Try again later.";

// A Google popup shut, or replaced by another: the dialog stays, so they can
// switch to the email instead. Only closing the dialog itself is a no.
const SHUT = new Set(["auth/popup-closed-by-user", "auth/cancelled-popup-request",
                      "auth/user-cancelled"]);
// Firebase's words for a link that has been used, or has run out.
const DEAD = new Set(["auth/invalid-action-code", "auth/expired-action-code"]);

/* What closing the dialog rejects with: the code a shut Google popup gives,
 * which every caller of signIn() already takes as "never mind". */
function closed() {
  return Object.assign(new Error("sign-in was closed"), { code: "auth/popup-closed-by-user" });
}

/* Its own look, from the colour tokens both stylesheets define, because the
 * two pages' base styles for buttons, inputs and headings differ. The boxes
 * are 16px so a phone does not zoom in on them, and the dialog sits high so
 * the keyboard does not cover it. */
const STYLE = `
#signInDialog { box-sizing:border-box; width:100%; max-width:min(360px, calc(100vw - 32px));
  max-height:calc(100vh - 32px); max-height:calc(100dvh - 32px); overflow:auto;
  margin:min(12vh, 96px) auto auto; padding:18px 16px 16px;
  border:1px solid var(--line); border-radius:10px; background:var(--panel); color:var(--ink);
  font:15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
#signInDialog::backdrop { background:rgb(0 0 0 / .35); }
#signInDialog * { box-sizing:border-box; }
#signInDialog h2 { margin:0 40px 14px 0; font:700 19px/1.25 var(--display, inherit);
  color:var(--ink); text-transform:none; letter-spacing:normal; }
#signInDialog h2 .si-hint { display:block; margin-top:2px;
  font:400 15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; color:var(--muted); }
#signInDialog p { margin:0 0 12px; }
#signInDialog label { display:block; margin:0 0 6px; font-weight:600; font-size:14px; }
#signInDialog input { display:block; width:100%; min-width:0; min-height:44px; margin:0;
  padding:0 12px; font:inherit; font-size:16px; color:var(--ink); background:var(--panel);
  border:1px solid var(--line); border-radius:7px; }
#signInDialog button { min-height:44px; margin:0; padding:0 16px; font:inherit; color:var(--ink);
  background:var(--panel); border:1px solid var(--line); border-radius:7px; cursor:pointer; }
#signInDialog button:disabled { opacity:.5; cursor:default; }
#signInDialog .si-primary { background:var(--accent); color:var(--onaccent);
  border-color:transparent; }
#signInDialog .si-wide { display:block; width:100%; }
#signInDialog form .si-wide { margin-top:8px; }
#signInDialog .si-row { display:flex; gap:8px; }
#signInDialog .si-row input { flex:1; }
#signInDialog .si-row button { flex:none; }
#signInDialog .si-or { display:flex; align-items:center; gap:10px; margin:14px 0;
  color:var(--muted); font-size:13px; }
#signInDialog .si-or::before, #signInDialog .si-or::after { content:""; flex:1;
  border-top:1px solid var(--line); }
#signInDialog .si-sent strong { overflow-wrap:anywhere; }
#signInDialog .si-say { margin:10px 0 0; font-weight:600; color:var(--warn, var(--ink)); }
#signInDialog .si-say.si-note { font-weight:400; color:var(--muted); }
#signInDialog .si-say:empty { display:none; }
#signInDialog .si-more { display:flex; flex-wrap:wrap; gap:0 18px; margin:6px 0 0; }
#signInDialog .si-link { padding:0; border:0; background:none; color:var(--ink);
  text-decoration:underline; text-underline-offset:3px; }
#signInDialog .si-x { position:absolute; top:6px; right:6px; width:44px; padding:0; border:0;
  background:none; font-size:24px; line-height:1; color:var(--muted); }
`;

function injectStyle() {
  if (!document.getElementById("signin-style"))
    document.head.append(h("style", { id: "signin-style" }, STYLE));
}

/* An element, its attributes, and its children; strings become text nodes,
 * so an address typed in is never read as markup. */
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  el.append(...kids);
  return el;
}

// The dialog on screen, so a second Sign in while it is open waits on it.
let showing = null;

/** Resolve with the User once signed in, by Google or by link, and after the
 * name step when this tab finished the link. Reject, as a shut popup does,
 * when the dialog is closed. `resume` is a remembered request: start at the
 * code box (auth.js resumeWaiting). Opened without one, it starts there all
 * the same for a request still worth waiting on, with nobody signed in. */
export function openSignIn(api, { resume } = {}) {
  showing ??= run(api, resume ? "wait" : "choose", resume)
    .finally(() => { showing = null; });
  return showing;
}

/** The name step alone, for /signin once it has finished a link. Closing it
 * is allowed: they are signed in either way. */
export function askName(api) {
  return run(api, "name").then(() => undefined);
}

function run(api, view, resumed = null) {
  injectStyle();
  return new Promise((resolve, reject) => {
    const body = h("div");
    const x = h("button", { type: "button", class: "si-x", "aria-label": "Close" }, "×");
    const d = h("dialog", { id: "signInDialog", "aria-labelledby": "signInDialogTitle" }, x, body);
    document.body.append(d);

    // The request being waited on, held here as well as in storage: a browser
    // that keeps nothing (a private window) can still finish with the code.
    let pending = resumed;
    // A code is being traded for its link: the dialog is held open meanwhile,
    // or a close would reject a sign-in that then goes on to happen.
    let finishing = false;
    // Closed during the trade all the same: a second Escape, or Android's
    // Back, closes a dialog that refused the first. The trade goes on, and
    // its outcome is the answer once it is done.
    let gone = false;
    // The User, once this tab has finished a link; for the name step alone
    // there is nothing to hand back, but they are signed in.
    let signedIn = view === "name" ? true : null;
    let over = false;
    let off = () => {};

    const end = (ok, value) => {
      if (over) return;
      over = true;
      off();
      if (ok) resolve(value); else reject(value);
      if (d.open) d.close();
    };
    d.addEventListener("cancel", ev => { if (finishing) ev.preventDefault(); });
    d.addEventListener("close", () => {
      if (finishing) gone = true;
      else if (signedIn) end(true, signedIn);
      else end(false, closed());
      d.remove();
    });
    x.addEventListener("click", () => { if (!finishing) d.close(); });

    // Signed in some other way while this is open -- most often the link,
    // opened in another tab of this browser, which Firebase tells every tab --
    // closes it. Not while this tab finishes a link itself: that still has the
    // name to ask. The first call is the answer as it stood, which whoever
    // called signIn() wanted changed, so it is let pass.
    if (view !== "name") {
      let asOpened = true;
      off = api.onUser(u => { if (!asOpened && u && !finishing && !signedIn) end(true, u); });
      asOpened = false;
    }

    const busy = on => {
      for (const el of body.querySelectorAll("button, input")) el.disabled = on;
    };
    const show = (focus, ...nodes) => {
      if (over) return;
      body.replaceChildren(...nodes);
      if (!d.open) d.showModal();
      focus.focus();
    };
    const title = (...kids) => h("h2", { id: "signInDialogTitle" }, ...kids);
    const sayLine = () => h("p", { class: "si-say", role: "alert" });

    function choose(email = "") {
      const google = h("button", { type: "button", class: "si-primary si-wide" },
                       "Continue with Google");
      // Until the site switches emailed links on (auth.js emailLinkOn), the
      // chooser is Google alone: offered before Firebase will send them,
      // every "Email me a link" would fail.
      const input = api.emailLink ? h("input", { id: "signInDialogEmail", type: "email",
                                                 autocomplete: "email", required: "" }) : null;
      const say = sayLine();

      // Only this button waits on the popup. Firebase takes some seconds to
      // notice a popup was shut, and the email is the way out meanwhile; if it
      // is taken, whatever the popup says later lands on a view that has gone.
      google.addEventListener("click", () => {
        say.textContent = "";
        google.disabled = true;
        // Straight from the click, with nothing awaited first, or a phone's
        // browser may block the popup as one nobody asked for.
        api.signInWithGoogle().then(u => end(true, u), e => {
          google.disabled = false;
          if (e?.code === "auth/account-exists-with-different-credential") {
            if (input) input.value = e.customData?.email || input.value;
            say.textContent = LINKED;
            input?.focus();
          } else if (!SHUT.has(e?.code)) {
            say.textContent = failed("Couldn't sign in with Google", e);
          }
        });
      });
      if (!input) { show(google, title("Sign in"), google, say); return; }

      input.value = email;
      const form = h("form", {},
        h("label", { for: "signInDialogEmail" }, "Email"), input,
        h("button", { type: "submit", class: "si-wide" }, "Email me a link"));
      form.addEventListener("submit", async ev => {
        ev.preventDefault();
        const to = cleanEmail(input.value);
        if (!to) { input.focus(); return; }
        say.textContent = "";
        busy(true);
        try {
          pending = await api.sendLink(to);
          wait();
        } catch (e) {
          busy(false);
          say.textContent = emailFailed("Couldn't send the link", e);
        }
      });

      show(input, title("Sign in"), google, h("p", { class: "si-or" }, "or"), form, say);
      if (email) input.select();
    }

    function wait(note = "") {
      const code = h("input", { id: "signInDialogCode", inputmode: "numeric",
                                autocomplete: "one-time-code" });
      const say = sayLine();
      const tell = (text, quiet = false) => {
        say.classList.toggle("si-note", quiet);
        say.textContent = text;
      };
      tell(note, true);
      const form = h("form", {},
        h("label", { for: "signInDialogCode" },
          "If the link opens in another app, it shows a code. Type it here:"),
        h("div", { class: "si-row" }, code,
          h("button", { type: "submit", class: "si-primary" }, "Sign in")));
      const again = h("button", { type: "button", class: "si-link" }, "Send again");
      const other = h("button", { type: "button", class: "si-link" }, "Use a different address");
      // A link claimed whose finish failed for a reason a second try might
      // mend (the network, mostly). Claiming took it off the server for good,
      // so the next Sign in finishes this one rather than claim again.
      let held = null;

      form.addEventListener("submit", async ev => {
        ev.preventDefault();
        const typed = parseCode(code.value);
        if (!typed && !held) { tell(WRONG); code.focus(); return; }
        tell("");
        busy(true);
        finishing = true;
        x.disabled = true;
        let said = "";
        try {
          if (!held) {
            const got = await api.claimCode(pending.id, typed);
            if (got.link) held = got.link;
            else said = { wrong: WRONG, gone: GONE, busy: BUSY }[got.error] ?? WRONG;
          }
          // With the address this browser stored when it asked, never one the
          // link brings or anyone types after the claim: that is what makes
          // a stranger's code useless here.
          if (held) {
            signedIn = await api.finishWithLink(pending.email, held);
            held = null;
          }
        } catch (e) {
          if (DEAD.has(e?.code)) held = null;
          said = DEAD.has(e?.code) ? GONE : emailFailed("Couldn't sign in", e);
        }
        finishing = false;
        x.disabled = false;
        // Closed while that went on: the dialog has gone, so whatever came of
        // it is the answer now, and the name waits for the next link sign-in.
        if (gone) {
          if (signedIn) end(true, signedIn); else end(false, closed());
          return;
        }
        if (signedIn) {
          if (await api.needsName()) name(); else end(true, signedIn);
          return;
        }
        busy(false);
        tell(said);
        code.focus();
        code.select();
      });
      // The same request, so the link in the first email still works too.
      again.addEventListener("click", async () => {
        tell("");
        busy(true);
        try {
          pending = await api.sendLink(pending.email);
          wait("Sent again.");
        } catch (e) {
          busy(false);
          tell(emailFailed("Couldn't send the link", e));
        }
      });
      other.addEventListener("click", () => choose(pending.email));

      show(code,
        title("Check your email"),
        h("p", { class: "si-sent" }, "We sent a link to ", h("strong", {}, pending.email), "."),
        form, say,
        h("p", { class: "si-more" }, again, other));
    }

    function name() {
      const input = h("input", { id: "signInDialogName", autocomplete: "name", maxlength: "60",
                                 required: "", "aria-labelledby": "signInDialogTitle" });
      const say = sayLine();
      const form = h("form", {}, h("div", { class: "si-row" }, input,
        h("button", { type: "submit", class: "si-primary" }, "Save")));
      form.addEventListener("submit", async ev => {
        ev.preventDefault();
        const typed = input.value.trim();
        if (!typed) { input.focus(); return; }
        say.textContent = "";
        busy(true);
        try {
          await api.setName(typed);
          end(true, signedIn);
        } catch (e) {
          busy(false);
          say.textContent = failed("Couldn't save", e);
          input.focus();
        }
      });
      show(input, title("Your name ", h("span", { class: "si-hint" }, "what your teammates see")),
           form, say);
    }

    if (view === "wait") wait();
    else if (view === "name") name();
    else {
      // A request still worth waiting on, with nobody signed in, opens at its
      // code box: most often a viewer chart or a review page, which load
      // auth.js only when somebody signs in, reloaded while its person was in
      // the mail app. "Use a different address" leads back to the chooser.
      const last = api.emailLink ? api.pending() : null;
      const live = isFresh(last, Date.now());
      if (live && !api.currentUser()) { pending = last; wait(); }
      else choose(live ? last.email : "");
    }
  });
}
