/* The chrome every page shares. */
import { useEffect, useState } from "react";
import { authedFetch, signIn, signOff } from "./auth.js";
import { useAuthUser, useResource } from "./useAuth.js";

/* Signed out until the SDK says otherwise, and silent when accounts are off:
 * an empty chip is the state the whole site was in before accounts existed,
 * and every route still handles it. */
export function IdentityChip() {
  const { user, ready, accounts } = useAuthUser();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  if (!ready || !accounts) return <span id="who" className="muted" />;
  return (
    <span id="who" className="muted">
      {user ? (
        <>
          <a href="/mine">{user.displayName || user.email || "My games"}</a>{" "}
          <button onClick={() => signOff()}>Sign out</button>
        </>
      ) : (
        <button disabled={busy} onClick={async () => {
          setBusy(true);
          try { await signIn(); } catch (e) { setErr(` ${e.code || e.message}`); }
          finally { setBusy(false); }
        }}>Sign in</button>
      )}
      {err}
    </span>
  );
}

export function Header({ links = [], children }) {
  return (
    <header>
      <h1><a href="/games">Curling Chart</a></h1>
      {links.map(([href, text]) => <a key={href} href={href}>{text}</a>)}
      <span style={{ flex: 1 }} />
      <IdentityChip />
      {children}
    </header>
  );
}

export const Warn = ({ children }) => <div className="warn">{children}</div>;
export const Card = ({ children, ...rest }) => <div className="card" {...rest}>{children}</div>;
export const Pill = ({ kind = "", children }) =>
  <span className={`pill ${kind}`}>{children}</span>;

/* "Chart this as ..." -- shown only to somebody actually on a team, because
 * for everyone else there is nothing to choose. */
export function TeamPicker({ value, onChange }) {
  const { user, ready } = useAuthUser();
  const me = useResource("/api/me", { skip: !ready || !user, deps: [user?.uid] });
  const teams = me.data?.teams || [];
  if (!teams.length) return <div className="row" id="teamrow" hidden />;
  return (
    <div className="row" id="teamrow" style={{ marginBottom: 12 }}>
      <label htmlFor="teampick" style={{ margin: 0 }}>Chart as</label>
      <select id="teampick" value={value} onChange={e => onChange(e.target.value)}>
        <option value="">Just me</option>
        {teams.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
      </select>
    </div>
  );
}

/* A cell that is text until you click it, then an input until you leave it.
 *
 * The catalogue's rows are busy enough without a permanent input in every one,
 * and both of the things worth correcting -- the league, and who played -- are
 * usually already right. */
export function EditableCell({ className, title, display, editing, onEdit, children }) {
  if (!onEdit) return <td>{display}</td>;
  return (
    <td className={className}>
      {editing ? children
               : <button className="linky" title={title} onClick={onEdit}>{display}</button>}
    </td>
  );
}

/* Commit on Enter or on leaving the control entirely; abandon on Escape.
 * Moving between two boxes of the same editor is not leaving it. */
export function useCommitOnExit(save, cancel) {
  const [done, setDone] = useState(false);
  useEffect(() => setDone(false), [save, cancel]);
  const finish = ok => {
    if (done) return;
    setDone(true);
    ok ? save() : cancel();
  };
  return {
    onKeyDown: e => {
      if (e.key === "Enter") finish(true);
      if (e.key === "Escape") finish(false);
    },
    onBlur: e => {
      const box = e.currentTarget.closest("td, .teamedit") || e.currentTarget;
      setTimeout(() => { if (!box.contains(document.activeElement)) finish(true); }, 0);
    },
  };
}
