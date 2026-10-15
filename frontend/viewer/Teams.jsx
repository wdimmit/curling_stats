/* "Teams…": who played this game, and whether a team played with three, by the colour they threw -- the same
 * names the game list takes, kept on the game, so every chart and link of it
 * shows them once saved.
 *
 * A native <dialog> opened with showModal(), like I played…'s, and keyed per
 * opening so each one starts from the names the game has now. Anyone may
 * open it; only a validated account (User.role) may save, and anyone else
 * signed in is told so rather than shown the form. Sign-in is asked about
 * only here, once the dialog is open -- never as the page loads, which would
 * fetch the Firebase SDK for every viewer of every game (runtime/auth.mjs). */
import { useEffect, useId, useRef, useState } from "react";
import { accountsOn, signInNow, whoIsSignedIn } from "../runtime/auth.mjs";
import { canNameTeams, fetchRole, fetchTeamNames, sendTeams } from "../runtime/teams.mjs";
import { NameBox } from "./NameBox.jsx";

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
  // answer), "signin" (nobody), "notvalidated" (signed in, but the account
  // may not name teams), or "ready".
  const [phase, setPhase] = useState("checking");
  const [attempt, setAttempt] = useState(0);
  const [red, setRed] = useState(naming.names.red);
  const [yellow, setYellow] = useState(naming.names.yellow);
  const [short, setShort] = useState(() => new Set(naming.short || []));
  const three = (c, on) => setShort(s => {
    const next = new Set(s);
    if (on) next.add(c); else next.delete(c);
    return next;
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  // The names the league's other games go by, offered in both boxes so the
  // same team is not a new spelling every night. Asked for alongside the
  // sign-in check, so they are there by the time the boxes are.
  const [known, setKnown] = useState([]);
  const box = useId();

  useEffect(() => {
    let live = true;
    fetchTeamNames(naming.sourceId).then(names => { if (live) setKnown(names); });
    return () => { live = false; };
  }, [naming.sourceId]);

  useEffect(() => {
    let live = true;
    (async () => {
      if (!(await accountsOn())) { if (live) setPhase("off"); return; }
      const who = await whoIsSignedIn();
      if (!who) { if (live) setPhase(who === undefined ? "slow" : "signin"); return; }
      // A role the server did not give is left to the save to find out.
      const role = await fetchRole(who.token);
      if (live) setPhase(role === undefined || canNameTeams(role) ? "ready" : "notvalidated");
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
    // A doubles team is two players: the box is not shown, and nothing is sent.
    const body = { red: red.trim(), yellow: yellow.trim(),
                   ...(naming.doubles ? {} : { short_handed: ["red", "yellow"].filter(c => short.has(c)) }) };
    const who = await whoIsSignedIn();
    const res = who ? await sendTeams(naming.sourceId, body, who.token)
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
        of this game shows them, and so does the game list. A team of three: its lead and
        second threw three rocks each, its skip two.</p>
      {/* Names are often people's surnames: say they are public, and that a
          change is kept with who made it, before anyone types one. */}
      <p className="tm-public">Names are public, here and in the league's reports. Validated
        accounts can change them, and each change is kept with the account that made
        it. <a href="/terms" target="_blank" rel="noopener">Terms</a> · <a href="/privacy"
        target="_blank" rel="noopener">Privacy</a></p>
    </>
  );
  const close = <button type="button" onClick={onClose}>Close</button>;

  if (phase !== "ready") {
    const say = {
      checking: "Checking sign-in…",
      off: "Accounts are not switched on here, so there is nowhere to keep this.",
      slow: "Sign-in is slow to answer.",
      signin: "Sign in to name the teams.",
      notvalidated: "Naming teams is open to accounts the site has validated.",
    }[phase];
    return (
      <div>
        {head}
        <p className="tm-who">{say}</p>
        {phase === "notvalidated" && (
          <p className="tm-who">To have yours validated, write to <a
            href="mailto:will@ultimatetrip.net">will@ultimatetrip.net</a>.</p>
        )}
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
      {/* A div with a label in it, not a label around the box: the names
          listed under the box would otherwise be read out as its name. */}
      <div className="tm-team">
        <span className="dot red" aria-hidden="true" />
        <label htmlFor={`${box}red`}>Red</label>
        <NameBox id={`${box}red`} names={known} maxLength={60} autoFocus value={red}
                 placeholder="Team name" onValue={setRed} />
      </div>
      {!naming.doubles && (
        <label className="tm-three">
          <input type="checkbox" checked={short.has("red")} aria-label="Red team played with three"
                 onChange={e => three("red", e.target.checked)} />
          Played with three
        </label>
      )}
      <div className="tm-team">
        <span className="dot yellow" aria-hidden="true" />
        <label htmlFor={`${box}yellow`}>Yellow</label>
        <NameBox id={`${box}yellow`} names={known} maxLength={60} value={yellow}
                 placeholder="Team name" onValue={setYellow} />
      </div>
      {!naming.doubles && (
        <label className="tm-three">
          <input type="checkbox" checked={short.has("yellow")} aria-label="Yellow team played with three"
                 onChange={e => three("yellow", e.target.checked)} />
          Played with three
        </label>
      )}
      {error && <p className="tm-error" role="alert">{error}</p>}
      <div className="tm-foot">
        <button type="button" disabled={busy} onClick={onClose}>Cancel</button>
        <button type="submit" className="on" disabled={busy}>{busy ? "Saving…" : "Save"}</button>
      </div>
    </form>
  );
}
