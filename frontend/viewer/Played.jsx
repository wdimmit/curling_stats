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
import { playBody, positionChoices, roleText, shortOf, switchNote } from "../core/index.mjs";
import { accountsOn, signInNow, whoIsSignedIn } from "../runtime/auth.mjs";
import { clearPlay, fetchPlay, savePlay } from "../runtime/plays.mjs";

/* `onSaved` reloads the game, which a change to its switched ends re-credits. */
export function PlayedDialog({ playing, onClose, onSaved }) {
  const ref = useRef(null);

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (playing && !d.open) d.showModal();
    if (!playing && d.open) d.close();
  }, [playing]);

  return (
    <dialog id="playedDialog" ref={ref} onClose={onClose} aria-labelledby="playedTitle">
      {playing && <PlayedForm key={playing.opened} playing={playing} onClose={onClose}
                              onSaved={onSaved} />}
    </dialog>
  );
}

const FAILED = "Could not save. Try again.";

function PlayedForm({ playing, onClose, onSaved }) {
  // "checking", then "off" (no accounts here), "slow" (sign-in did not
  // answer), "signin" (nobody), "ready", or "saved".
  const [phase, setPhase] = useState("checking");
  const [attempt, setAttempt] = useState(0);
  const [current, setCurrent] = useState(null);
  const [color, setColor] = useState(null);
  const [slot, setSlot] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  // A doubles pair that switched order: whether they did (`on`), the ends
  // where this player threw the other rocks (`flips`, by end identity), and
  // whether this opening changed either -- saved only then, so a partner
  // saving their own rocks never unmarks the game's.
  const ends = playing.ends || [];
  const switchable = !!playing.format?.swappable && ends.length > 1;
  const [on, setOn] = useState(false);
  const [flips, setFlips] = useState(() => new Set());
  const [touched, setTouched] = useState(false);
  const fromGame = c => {
    const held = playing.switched?.[c] || [];
    setOn(held.length > 0);
    setFlips(new Set(held));
    setTouched(false);
  };
  const pickColor = c => { setColor(c); fromGame(c); };
  const flip = id => {
    setFlips(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
    setTouched(true);
  };

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
        const held = playing.switched?.[res.play.color] || [];
        setOn(held.length > 0);
        setFlips(new Set(held));
      }
      setPhase("ready");
    })();
    return () => { live = false; };
  }, [playing.sourceId, playing.switched, attempt]);

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
  // A team of three offers its three players. A pick the team picked now
  // does not have (Third, from the other team or a save before it was
  // marked) is no pick, so Save waits for one.
  const few = !!color && shortOf(playing.format).has(color);
  const choices = positionChoices(playing.format, few);
  const picked = choices.some(p => p.slot === slot) ? slot : null;
  const switching = switchable && on;
  const flippedNumbers = ends.filter((e, i) => i > 0 && flips.has(e.id)).map(e => e.number);

  const save = async ev => {
    ev.preventDefault();
    if (busy || !color || !picked) return;
    setBusy(true);
    setError(null);
    const who = await whoIsSignedIn();
    // The first end is never switched: it is what Player A means.
    const marked = new Set([...flips].filter(id => id !== ends[0]?.id));
    const body = playBody({ path: location.pathname, color, slot: picked,
                            touched: switchable && touched, on, flips: marked });
    const res = who ? await savePlay(playing.sourceId, body, who.token)
      : { ok: false, error: "You are signed out. Close this and sign in again." };
    setBusy(false);
    if (res.ok) {
      setCurrent(res.play);
      // The game's rocks changed hands: show them as they now are.
      if ("switched_ends" in body && onSaved) await onSaved();
      setPhase("saved");
    }
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
                  aria-pressed={color === c} onClick={() => pickColor(c)}>
            <span className={`dot ${c}`} aria-hidden="true" />{playing.teams[c]}
          </button>
        ))}
      </div>
      <div className="pl-slots" role="group" aria-label="The rocks you threw"
           style={{ "--n": choices.length }}>
        {choices.map(p => (
          <button key={p.slot} type="button" className={picked === p.slot ? "on" : undefined}
                  aria-pressed={picked === p.slot} onClick={() => setSlot(p.slot)}>
            <b>{p.label}</b><span>{switching ? `End ${ends[0].number}: ${p.role}` : p.role}</span>
          </button>
        ))}
      </div>
      {switchable && (
        <>
          <label className="pl-switch">
            <input type="checkbox" checked={on}
                   onChange={ev => { setOn(ev.target.checked); setTouched(true); }} />
            <span>We switched during the game</span>
          </label>
          {on && (
            <>
              <p className="pl-hint">Tap each end where you threw the other rocks.</p>
              <div className="pl-ends" role="group" aria-label="Ends you threw the other rocks">
                {ends.map((e, i) => {
                  const flipped = i > 0 && flips.has(e.id);
                  const mine = picked ? (flipped ? 3 - picked : picked) : null;
                  return (
                    <button key={e.id} type="button" className={flipped ? "flip" : undefined}
                            aria-pressed={flipped} disabled={i === 0} onClick={() => flip(e.id)}>
                      <b>End {e.number}</b>
                      <span>{mine ? roleText(playing.format, mine) : "—"}</span>
                    </button>
                  );
                })}
              </div>
            </>
          )}
          {color && picked && <p className="pl-hint">{switchNote(on ? flippedNumbers : [])}</p>}
        </>
      )}
      {playing.review && current?.graded &&
        <p className="pl-note">You saved this from a chart before. Saving it here reads the
          game as detected, without that chart's grading.</p>}
      {error && <p className="pl-error" role="alert">{error}</p>}
      <div className="pl-foot">
        {current && <button type="button" className="pl-clear" disabled={busy} onClick={clear}>Clear</button>}
        <button type="button" disabled={busy} onClick={onClose}>Cancel</button>
        <button type="submit" className="on" disabled={busy || !color || !picked}>
          {busy ? "Saving…" : "Save"}
        </button>
      </div>
    </form>
  );
}
