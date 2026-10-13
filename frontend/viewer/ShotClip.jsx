/* "Clip…": a clip of the rock on screen, cut by the clips worker from the
 * game's video with its lines and the delivery burned in (game/clipburn.py),
 * to watch here or save.
 *
 * A native <dialog> opened with showModal(), keyed per opening like Name
 * teams…'s. Opening it asks where a clip of this rock stands; one already cut
 * plays at once. Otherwise it is asked for -- which needs someone signed in,
 * asked about only here -- and the dialog says where it is until it is cut. */
import { useEffect, useRef, useState } from "react";
import { SHOT_CLIP_POLL_MS, shotClipStatus } from "../core/index.mjs";
import { accountsOn, signInNow, whoIsSignedIn } from "../runtime/auth.mjs";
import { askShotClip, fetchShotClip } from "../runtime/shotclips.mjs";

export function ShotClipDialog({ clipping, onClose }) {
  const ref = useRef(null);

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (clipping && !d.open) d.showModal();
    if (!clipping && d.open) d.close();
  }, [clipping]);

  return (
    <dialog id="clipDialog" ref={ref} onClose={onClose} aria-labelledby="clipTitle">
      {clipping && <ShotClip key={clipping.opened} clipping={clipping} onClose={onClose} />}
    </dialog>
  );
}

const UNREACHABLE = "Could not reach the server. Try again.";

function ShotClip({ clipping, onClose }) {
  const { path, rock, text } = clipping;
  // "looking", then "signin" (nobody), "slow" (sign-in did not answer),
  // "off" (no accounts here), "error", or "asked" -- with `got`, the API's
  // last word on the clip.
  const [phase, setPhase] = useState("looking");
  const [got, setGot] = useState(null);
  const [error, setError] = useState(null);
  // Bumped by Sign in and Try again: look, and ask, once more. Past the first
  // look, a clip that failed is asked for again rather than shown failed.
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let live = true;
    (async () => {
      const now = await fetchShotClip(path, rock);
      if (!live) return;
      if (!now.ok) { setError(now.error || UNREACHABLE); setPhase("error"); return; }
      if (now.state !== "none" && !(now.state === "failed" && attempt > 0)) {
        setGot(now); setPhase("asked"); return;
      }
      if (!(await accountsOn())) { if (live) setPhase("off"); return; }
      const who = await whoIsSignedIn();
      if (!live) return;
      if (!who) { setPhase(who === undefined ? "slow" : "signin"); return; }
      const asked = await askShotClip(path, rock, who.token);
      if (!live) return;
      if (!asked.ok) { setError(asked.error || UNREACHABLE); setPhase("error"); return; }
      setGot(asked); setPhase("asked");
    })();
    return () => { live = false; };
  }, [path, rock, attempt]);

  // While it is on its way, ask after it. A blip asks again next time round.
  const status = shotClipStatus(got);
  useEffect(() => {
    if (phase !== "asked" || !status.busy) return undefined;
    let live = true;
    const t = setTimeout(async () => {
      const next = await fetchShotClip(path, rock);
      if (live) setGot(next.ok ? next : { ...got });
    }, SHOT_CLIP_POLL_MS);
    return () => { live = false; clearTimeout(t); };
  }, [phase, status.busy, got, path, rock]);

  const again = () => { setError(null); setPhase("looking"); setAttempt(a => a + 1); };
  const signIn = async () => {
    setError(null);
    try { await signInNow(); again(); }
    catch { setError("Sign-in was closed before it finished."); }
  };

  let body, action = null;
  if (phase === "looking") {
    body = <Working text="Looking for a clip of this rock…" />;
  } else if (phase === "signin") {
    body = <p className="sc-say">Sign in to have a clip of this rock cut.</p>;
    action = <button type="button" className="on" onClick={signIn}>Sign in</button>;
  } else if (phase === "slow") {
    body = <p className="sc-say">Sign-in is slow to answer.</p>;
    action = <button type="button" className="on" onClick={again}>Try again</button>;
  } else if (phase === "off") {
    body = <p className="sc-say">Accounts are not switched on here, so no clip can be asked for.</p>;
  } else if (phase === "error") {
    body = null;
    action = <button type="button" className="on" onClick={again}>Try again</button>;
  } else if (got?.state === "ready") {
    body = (
      <>
        <video className="sc-video" src={got.url} controls playsInline autoPlay muted
               preload="metadata" />
        <p className="sc-legend">Solid: the line it was thrown on · dashed: hack to broom ·
          ring: the broom · dots: the rock from the hack to past the hog line</p>
      </>
    );
    action = <a className="sc-download on" href={got.download} download>⬇ Download</a>;
  } else if (status.busy) {
    body = <Working text={status.text} />;
  } else {
    body = <p className="sc-say">{status.text}</p>;
    if (got?.state === "failed" && got.reason !== "no_release")
      action = <button type="button" className="on" onClick={again}>Try again</button>;
  }

  return (
    <div>
      <h2 id="clipTitle">Clip of this rock</h2>
      {text && <p className="sc-what">{text}</p>}
      {body}
      {error && <p className="sc-error" role="alert">{error}</p>}
      <div className="sc-foot">
        <button type="button" onClick={onClose}>Close</button>
        {action}
      </div>
    </div>
  );
}

function Working({ text }) {
  return (
    <p className="sc-working" role="status">
      <span className="sc-spinner" aria-hidden="true" />{text}
    </p>
  );
}
