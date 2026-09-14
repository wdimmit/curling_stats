import { useState } from "react";
import { authedFetch, signIn } from "./auth.js";
import { useAuthUser, useResource } from "./useAuth.js";
import { Card, Header, Warn } from "./ui.jsx";

export function Join() {
  const token = location.pathname.split("/").filter(Boolean).pop();
  const { user, ready } = useAuthUser();
  const invite = useResource(`/api/invites/${encodeURIComponent(token)}`, { auth: false });
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState(null);

  const bad = invite.error;
  const usable = invite.data?.usable;

  async function join() {
    setBusy(true);
    const res = await authedFetch(`/api/invites/${encodeURIComponent(token)}/accept`,
      { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    if (res.ok) { location.href = "/mine"; return; }
    setBusy(false);
    setNote(<Warn>{
      res.status === 410 ? "That invitation has expired."
      : res.status === 401 ? "Sign in first."
      : "Could not join that team."}</Warn>);
  }

  return (
    <>
      <Header />
      <main>
        <Card>
          <h2 style={{ marginTop: 0 } } id="title">
            {bad ? "That invitation is not valid"
                 : invite.data ? `Join ${invite.data.team}` : "Join a team"}
          </h2>
          <p className="muted" id="blurb">
            {bad ? "Ask whoever sent it for a fresh link."
             : !invite.data ? "Checking that invitation…"
             : usable
               ? `Everyone on ${invite.data.team} shares one chart per game, and sees the same list.`
               : "That invitation has expired. Ask for a fresh link."}
          </p>
          <div className="row">
            {usable && ready && user && (
              <button id="join" className="primary" disabled={busy} onClick={join}>Join</button>
            )}
            {usable && ready && !user && (
              <button id="signin" className="primary"
                      onClick={() => signIn().catch(err =>
                        setNote(<Warn>{err.code || err.message}</Warn>))}>
                Sign in with Google to join
              </button>
            )}
          </div>
          <div id="note">{note}</div>
        </Card>
      </main>
    </>
  );
}
