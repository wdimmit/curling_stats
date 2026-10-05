/* "I played…": which rocks of this game were yours, for the My shots report.
 *
 * A native <dialog> opened with showModal(), like the flag's, and keyed per
 * opening so each one starts from what the server says. `playing` is the
 * snapshot App took when the button was pressed: the game, its team names
 * and its format.
 *
 * Sign-in is asked about only here, once the dialog is open -- never as the
 * page loads, which would fetch the Firebase SDK for every viewer of every
 * game (runtime/auth.mjs). */
import { useEffect, useRef, useState } from "react";
import { positionChoices } from "../core/index.mjs";
import { accountsOn, signInNow, whoIsSignedIn } from "../runtime/auth.mjs";
import { clearPlay, fetchPlay, savePlay } from "../runtime/plays.mjs";

export function PlayedDialog({ playing, onClose }) {
  const ref = useRef(null);

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (playing && !d.open) d.showModal();
    if (!playing && d.open) d.close();
  }, [playing]);

  return (
    <dialog id="playedDialog" ref={ref} onClose={onClose} aria-labelledby="playedTitle">
      {playing && <PlayedForm key={playing.opened} playing={playing} onClose={onClose} />}
    </dialog>
  );
}

const FAILED = "Could not save. Try again.";

function PlayedForm({ playing, onClose }) {
  // "checking", then "off" (no accounts here), "slow" (sign-in did not
  // answer), "signin" (nobody), "ready", or "saved".
  const [phase, setPhase] = useState("checking");
  const [attempt, setAttempt] = useState(0);
  const [current, setCurrent] = useState(null);
  const [color, setColor] = useState(null);
  const [slot, setSlot] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    let live = true;
    (async () => {
      if (!(await accountsOn())) { if (live) setPhase("off"); return; }
      const who = await whoIsSignedIn();
      if (!live) return;
      if (!who) { setPhase(who === undefined ? "slow" : "signin"); return; }
      const res = await fetchPlay(playing.sourceId, who.token);
      if (!live) return;
      if (res.ok && res.play) {
        setCurrent(res.play);
        setColor(res.play.color);
        setSlot(res.play.slot);
      }
      setPhase("ready");
    })();
    return () => { live = false; };
  }, [playing.sourceId, attempt]);

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
  // hour would otherwise send a dead one.
  const save = async ev => {
    ev.preventDefault();
    if (busy || !color || !slot) return;
    setBusy(true);
    setError(null);
    const who = await whoIsSignedIn();
    const res = who ? await savePlay(playing.sourceId, { path: location.pathname, color, slot }, who.token)
      : { ok: false, error: "You are signed out. Close this and sign in again." };
    setBusy(false);
    if (res.ok) { setCurrent(res.play); setPhase("saved"); }
    else setError(res.error || FAILED);
  };

  const clear = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    const who = await whoIsSignedIn();
    const res = who ? await clearPlay(playing.sourceId, who.token) : { ok: false };
    setBusy(false);
    if (res.ok) { setCurrent(null); setColor(null); setSlot(null); }
    else setError(res.error || "Could not clear it. Try again.");
  };

  const head = (
    <>
      <h2 id="playedTitle">Which rocks were yours?</h2>
      <p className="pl-why">Pick your team and the rocks you threw. My shots then shows
        every rock you threw, across all the games you mark.</p>
    </>
  );
  const close = <button type="button" onClick={onClose}>Close</button>;

  if (phase === "saved") {
    return (
      <div>
        <p className="pl-saved">Saved. <a href="/shots">See My shots</a></p>
        <div className="pl-foot">{close}</div>
      </div>
    );
  }
  if (phase !== "ready") {
    const say = {
      checking: "Checking sign-in…",
      off: "Accounts are not switched on here, so there is nowhere to keep this.",
      slow: "Sign-in is slow to answer.",
      signin: "Sign in to keep this with your account.",
    }[phase];
    return (
      <div>
        {head}
        <p className="pl-who">{say}</p>
        {error && <p className="pl-error" role="alert">{error}</p>}
        <div className="pl-foot">
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
      <div className="pl-teams" role="group" aria-label="Your team">
        {["red", "yellow"].map(c => (
          <button key={c} type="button" className={color === c ? "on" : undefined}
                  aria-pressed={color === c} onClick={() => setColor(c)}>
            <span className={`dot ${c}`} aria-hidden="true" />{playing.teams[c]}
          </button>
        ))}
      </div>
      <div className="pl-slots" role="group" aria-label="The rocks you threw">
        {positionChoices(playing.format).map(p => (
          <button key={p.slot} type="button" className={slot === p.slot ? "on" : undefined}
                  aria-pressed={slot === p.slot} onClick={() => setSlot(p.slot)}>
            <b>{p.label}</b><span>{p.role}</span>
          </button>
        ))}
      </div>
      {playing.review && current?.graded &&
        <p className="pl-note">You saved this from a chart before. Saving it here reads the
          game as detected, without that chart's grading.</p>}
      {error && <p className="pl-error" role="alert">{error}</p>}
      <div className="pl-foot">
        {current && <button type="button" className="pl-clear" disabled={busy} onClick={clear}>Clear</button>}
        <button type="button" disabled={busy} onClick={onClose}>Cancel</button>
        <button type="submit" className="on" disabled={busy || !color || !slot}>
          {busy ? "Saving…" : "Save"}
        </button>
      </div>
    </form>
  );
}
