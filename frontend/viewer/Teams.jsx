/* "Name teams…": who played this game, by the colour they threw -- the same
 * names the game list takes, kept on the game, so every chart and link of it
 * shows them once saved.
 *
 * A native <dialog> opened with showModal(), like I played…'s, and keyed per
 * opening so each one starts from the names the game has now. Anyone may
 * open it; only someone signed in may save. Sign-in is asked about only here,
 * once the dialog is open -- never as the page loads, which would fetch the
 * Firebase SDK for every viewer of every game (runtime/auth.mjs). */
import { useEffect, useRef, useState } from "react";
import { accountsOn, signInNow, whoIsSignedIn } from "../runtime/auth.mjs";
import { sendTeams } from "../runtime/teams.mjs";

export function TeamsDialog({ naming, onClose, onSaved }) {
  const ref = useRef(null);

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (naming && !d.open) d.showModal();
    if (!naming && d.open) d.close();
  }, [naming]);

  return (
    <dialog id="teamsDialog" ref={ref} onClose={onClose} aria-labelledby="teamsTitle">
      {naming && <TeamsForm key={naming.opened} naming={naming} onClose={onClose} onSaved={onSaved} />}
    </dialog>
  );
}

function TeamsForm({ naming, onClose, onSaved }) {
  // "checking", then "off" (no accounts here), "slow" (sign-in did not
  // answer), "signin" (nobody), or "ready".
  const [phase, setPhase] = useState("checking");
  const [attempt, setAttempt] = useState(0);
  const [red, setRed] = useState(naming.names.red);
  const [yellow, setYellow] = useState(naming.names.yellow);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    let live = true;
    (async () => {
      if (!(await accountsOn())) { if (live) setPhase("off"); return; }
      const who = await whoIsSignedIn();
      if (live) setPhase(who ? "ready" : who === undefined ? "slow" : "signin");
    })();
    return () => { live = false; };
  }, [attempt]);

  const signIn = async () => {
    setError(null);
    try {
      await signInNow();
      setPhase("checking");
      setAttempt(a => a + 1);
    } catch {
      setError("Sign-in was closed before it finished.");
    }
  };

  // The token is asked for again at each send: a dialog left open past the
  // hour would otherwise send a dead one. The game is read again before the
  // dialog shuts, so the names are on the page by the time it has gone.
  const save = async ev => {
    ev.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    const who = await whoIsSignedIn();
    const res = who ? await sendTeams(naming.sourceId, { red: red.trim(), yellow: yellow.trim() }, who.token)
      : { ok: false, error: "You are signed out. Close this and sign in again." };
    if (res.ok) await onSaved();
    setBusy(false);
    if (res.ok) onClose();
    else setError(res.error || "Could not save. Try again.");
  };

  const head = (
    <>
      <h2 id="teamsTitle">Who played?</h2>
      <p className="tm-why">Each team's name, by the colour it threw. Every chart and link
        of this game shows them, and so does the game list.</p>
    </>
  );
  const close = <button type="button" onClick={onClose}>Close</button>;

  if (phase !== "ready") {
    const say = {
      checking: "Checking sign-in…",
      off: "Accounts are not switched on here, so there is nowhere to keep this.",
      slow: "Sign-in is slow to answer.",
      signin: "Sign in to name the teams.",
    }[phase];
    return (
      <div>
        {head}
        <p className="tm-who">{say}</p>
        {error && <p className="tm-error" role="alert">{error}</p>}
        <div className="tm-foot">
          {close}
          {phase === "signin" && <button type="button" className="on" onClick={signIn}>Sign in</button>}
          {phase === "slow" && <button type="button" className="on"
                                        onClick={() => { setPhase("checking"); setAttempt(a => a + 1); }}>Try again</button>}
        </div>
      </div>
    );
  }

  return (
    <form onSubmit={save}>
      {head}
      <label className="tm-team">
        <span className="dot red" aria-hidden="true" />Red
        <input maxLength={60} autoFocus value={red} placeholder="Team name"
               onChange={e => setRed(e.target.value)} />
      </label>
      <label className="tm-team">
        <span className="dot yellow" aria-hidden="true" />Yellow
        <input maxLength={60} value={yellow} placeholder="Team name"
               onChange={e => setYellow(e.target.value)} />
      </label>
      {error && <p className="tm-error" role="alert">{error}</p>}
      <div className="tm-foot">
        <button type="button" disabled={busy} onClick={onClose}>Cancel</button>
        <button type="submit" className="on" disabled={busy}>{busy ? "Saving…" : "Save"}</button>
      </div>
    </form>
  );
}
