/* The flag dialog: which rock, what is wrong, and who is saying so.
 *
 * A native <dialog> opened with showModal(), which brings focus handling,
 * Escape and a backdrop, and puts it in the top layer above the phone shell.
 * `flagging` is the snapshot App took when the button was pressed. The
 * cursor may move underneath while this is open -- a review link follows the
 * video -- and the flag must not move with it, so nothing here reads the
 * cursor.
 *
 * The form is keyed per opening, so each one starts empty: a reply still in
 * flight from the last opening lands on a form that no longer exists, and a
 * reopened dialog focuses its textarea rather than a stale "thanks". */
import { useEffect, useRef, useState } from "react";
import { NOTE_MAX, noteProblem } from "../core/index.mjs";
import { sendFlag, whoIsFlagging } from "../runtime/flag.mjs";

export function FlagDialog({ flagging, onClose }) {
  const ref = useRef(null);

  // After the form has mounted (child effects run first), so showModal()
  // finds the textarea to focus.
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (flagging && !d.open) d.showModal();
    if (!flagging && d.open) d.close();
  }, [flagging]);

  return (
    <dialog id="flagDialog" ref={ref} onClose={onClose} aria-labelledby="flagTitle">
      {flagging && <FlagForm key={flagging.opened} flagging={flagging} onClose={onClose} />}
    </dialog>
  );
}

function FlagForm({ flagging, onClose }) {
  const [note, setNote] = useState("");
  const [who, setWho] = useState(undefined);      // undefined: still asking
  const [sending, setSending] = useState(false);
  const [error, setError] = useState(null);
  const [sent, setSent] = useState(false);

  useEffect(() => {
    let live = true;
    whoIsFlagging().then(w => { if (live) setWho(w); });
    return () => { live = false; };
  }, []);

  useEffect(() => {
    if (!sent) return undefined;
    const t = setTimeout(onClose, 1500);
    return () => clearTimeout(t);
  }, [sent, onClose]);

  const send = async ev => {
    ev.preventDefault();
    if (sending || noteProblem(note)) return;
    setSending(true);
    setError(null);
    const w = who === undefined ? await whoIsFlagging() : who;
    const res = await sendFlag(
      { path: location.pathname, place: flagging.place, note: note.trim() }, w?.token);
    setSending(false);
    if (res.ok) setSent(true);
    else setError(res.error);
  };

  if (sent) return <p className="flagThanks">Thanks, flagged.</p>;

  const from = who === undefined ? "Checking sign-in…"
    : who ? `From ${who.email ?? "your account"}`
    : "Sent without your name. Sign in on the site to attach your account.";

  return (
    <form onSubmit={send}>
      <h2 id="flagTitle">Flag an issue</h2>
      <p className="flagPlace">{flagging.text}</p>
      <textarea id="flagNote" rows={5} maxLength={NOTE_MAX} value={note}
                onChange={e => setNote(e.target.value)}
                placeholder="What is wrong here?" aria-label="What is wrong" />
      <p className="flagWho">{from}</p>
      {error && <p className="flagError" role="alert">{error}</p>}
      <div className="flagButtons">
        <button type="button" onClick={onClose}>Cancel</button>
        <button type="submit" className="on" disabled={sending || !!noteProblem(note)}>
          {sending ? "Sending…" : "Send"}
        </button>
      </div>
    </form>
  );
}
