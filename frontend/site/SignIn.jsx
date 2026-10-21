/* /signin: where an emailed link lands.
 *
 * The link carries only a request id (signinState continueUrl). If this
 * browser is the one that asked -- it remembers a request with that id -- the
 * link is finished here, with the address it remembered, and the person goes
 * back to the page they started on. Anywhere else, most often the browser a
 * phone's mail app keeps to itself, the link is left with the server behind
 * a code (auth.js relayLink), and the page shows the code, to be typed where
 * the sign-in started.
 *
 * Only the browser that asked uses the link on load. Mail scanners open links
 * before anybody taps them; one that runs this page gets a code nobody will
 * type, and the link still works for the person it was sent to. Being
 * signed in here changes none of that: a member signed in on the phone may
 * have asked on a computer, which is waiting for the code (signinState
 * landing).
 */
import { useEffect, useRef, useState } from "react";
import { askName, emailLinkOn, finishWithLink, isLink, needsName, relayLink,
         signIn } from "./auth.js";
import { EMAIL_OFF, cleanEmail, emailFailed, failed, forget, formatCode, landing, recall,
         requestIdOf, signInTrouble } from "./signinState.mjs";
import { useAuthUser } from "./useAuth.js";
import { Card, Header, Warn } from "./ui.jsx";

const WRONG_ADDRESS = "That isn't the address the link was sent to.";
const BUSY = "Too many tries. Try again later.";

// Firebase's words for a link that has been used, or has run out.
const DEAD = new Set(["auth/invalid-action-code", "auth/expired-action-code"]);

/* localStorage, or null where touching it throws; recall() takes null as
 * nothing remembered. */
function store() {
  try { return localStorage; } catch { return null; }
}

/* A link that will never work: off the address bar, so a reload does not
 * offer it to Firebase again. */
function dead() {
  history.replaceState(null, "", "/signin");
  return { is: "dead" };
}

/* Finish the link with `email`, ask for a name if the account has none, and
 * go to `next`. A failure a second try might mend goes back to `back`. */
async function finish(email, next, back, setView) {
  setView({ is: "finishing" });
  try {
    // Which also forgets the request this browser remembered (auth.js).
    await finishWithLink(email, location.href);
  } catch (e) {
    if (DEAD.has(e?.code)) {
      // Spent. If this browser asked for it, the request it remembered has
      // nothing left to wait on, and the next Sign in starts at the chooser.
      if (recall(store())?.id === requestIdOf(location.href)) forget(store());
      setView(dead());
      return;
    }
    setView(e?.code === "auth/invalid-email" ? { is: "here", say: WRONG_ADDRESS }
      : { ...back, say: emailFailed("Couldn't sign in", e) });
    return;
  }
  // Spent. Off the address bar before anything else can fail, so neither a
  // reload nor coming back to this page tries it again.
  history.replaceState(null, "", "/signin");
  try { if (await needsName()) await askName(); }
  catch { /* the name step would not load: signed in either way */ }
  location.replace(next);
}

/* What this visit is, decided once the SDK has said who is here. */
async function arrive(user, accounts, setView) {
  const pending = recall(store());
  const to = landing({ accounts, signedIn: !!user, isLink: await isLink(location.href),
                       pending, href: location.href });
  if (to.is === "off" || to.is === "plain") { setView({ is: to.is }); return; }
  // Nobody to sign in: the link, if any, is left unused, as it came.
  if (to.is === "go") { location.replace(to.next); return; }

  // This browser asked: finish with the address it stored when it did, and
  // no other. The link's own id is only what picks this path.
  if (to.is === "finish") {
    await finish(pending.email, to.next, { is: "failed" }, setView);
    return;
  }

  // Somewhere else: the server holds it, and the code fetches it back.
  setView({ is: "relaying" });
  try {
    const { code } = await relayLink(location.href);
    setView({ is: "code", code });
  } catch (e) {
    setView(e?.status === 422 ? dead()          // not a link of ours
      : e?.status === 503 ? { is: "off" }
      : { is: "nocode", say: e?.status === 429 ? BUSY : failed("Couldn't get a code", e) });
  }
}

export function SignIn() {
  const { user, ready, accounts } = useAuthUser();
  const [view, setView] = useState({ is: "looking" });
  const [email, setEmail] = useState("");
  const [note, setNote] = useState("");
  const begun = useRef(false);

  // Once. The page's own sign-in changes `user`, and must not start it over.
  useEffect(() => {
    if (!ready || begun.current) return;
    begun.current = true;
    arrive(user, accounts, setView);
  }, [ready, user, accounts]);

  function chooser() {
    setNote("");
    signIn().then(() => location.replace("/"), e => setNote(signInTrouble(e)));
  }

  // The address they type here, and only that: Firebase checks it is the one
  // the link was sent to.
  function here(ev) {
    ev.preventDefault();
    const typed = cleanEmail(email);
    if (typed) finish(typed, "/", { is: "here" }, setView);
  }

  const signInButton = (
    <>
      <div className="row">
        <button className="primary" onClick={chooser}>Sign in</button>
      </div>
      {note && <Warn>{note}</Warn>}
    </>
  );
  // Not for somebody signed in already: that would be switching accounts.
  const instead = !user && (
    <p>
      <button type="button" className="linky" onClick={() => setView({ is: "here" })}>
        Sign in on this screen instead
      </button>
    </p>
  );
  const again = (
    <div className="row">
      <button onClick={() => location.reload()}>Try again</button>
    </div>
  );

  let body;
  switch (view.is) {
    case "off":
      body = <><h2>Sign in</h2><p className="muted">Sign-in isn't available here.</p></>;
      break;
    case "plain":
      body = (
        <>
          <h2>Sign in</h2>
          <p className="muted">
            {emailLinkOn() ? "With Google, or with a link emailed to you." : "With Google."}
          </p>
          {signInButton}
        </>
      );
      break;
    case "dead":
      body = (
        <>
          <h2>Sign in</h2>
          <p>This link has run out or has already been used.</p>
          {signInButton}
        </>
      );
      break;
    case "finishing":
      body = <h2>Signing you in…</h2>;
      break;
    case "failed":
      // Firebase refusing emailed links outright will not mend on a reload.
      body = <><h2>Sign in</h2><Warn>{view.say}</Warn>{view.say !== EMAIL_OFF && again}</>;
      break;
    case "relaying":
      body = <><h2>Almost there</h2><p className="muted">Getting your code…</p></>;
      break;
    case "code":
      body = (
        <>
          <h2>Almost there</h2>
          <p>Type this code where you asked to sign in:</p>
          <p className="code" id="code">{formatCode(view.code)}</p>
          <p className="muted">Works for 10 minutes.</p>
          {/* Somebody who typed a member's address may ask them for it. */}
          <p>Didn't ask to sign in? Close this page, and never read this code to anyone.</p>
          {instead}
        </>
      );
      break;
    case "nocode":
      body = <><h2>Almost there</h2><Warn>{view.say}</Warn>{again}{instead}</>;
      break;
    case "here":
      body = (
        <>
          <h2>Sign in on this screen</h2>
          <form onSubmit={here}>
            <p className="muted">Type the address the link was sent to.</p>
            <label htmlFor="signinEmail">Email</label>
            <input id="signinEmail" type="email" autoComplete="email" required autoFocus
                   value={email} onChange={e => setEmail(e.target.value)} />
            <div className="row">
              <button type="submit" className="primary">Sign in</button>
            </div>
          </form>
          {view.say && <Warn>{view.say}</Warn>}
        </>
      );
      break;
    default:
      body = <><h2>Sign in</h2><p className="muted">One moment…</p></>;
  }

  return (
    <>
      <Header />
      <main>
        <Card id="signin">{body}</Card>
      </main>
    </>
  );
}
