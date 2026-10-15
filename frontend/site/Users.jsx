/* /users: every account, newest first, for an admin to validate.
 *
 * A validated account may rename a game's teams (api.py, User.role); being
 * signed in does everything else. An admin's row has no box: admins are made
 * and unmade only by the site's owner, with scripts/users.py, and the server
 * refuses the rest (api.py, admin_set_role). */
import { useState } from "react";
import { authedFetch, signIn } from "./auth.js";
import { day } from "./fmt.js";
import { useAuthUser, useResource } from "./useAuth.js";
import { Card, Header } from "./ui.jsx";

function Row({ u, onChanged }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const set = async on => {
    setBusy(true);
    setErr("");
    try {
      const res = await authedFetch(`/api/admin/users/${encodeURIComponent(u.id)}/role`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ role: on ? "validated" : null }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) onChanged(data);
      else setErr(typeof data.detail === "string" ? data.detail : "Could not save.");
    } catch {
      setErr("Could not save.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <tr>
      <td className="who">
        {u.name || u.email || u.id}
        {u.name && u.email && <span className="email">{u.email}</span>}
      </td>
      <td data-label="Joined">{day(u.created_at)}</td>
      <td data-label="Last seen">{day(u.last_seen_at)}</td>
      <td className="box">
        {u.role === "admin" ? <span className="pill">admin</span> : (
          <label>
            <input type="checkbox" checked={u.role === "validated"} disabled={busy}
                   onChange={e => set(e.target.checked)} /> Validated
          </label>
        )}
        {err && <span className="muted" role="alert"> {err}</span>}
      </td>
    </tr>
  );
}

export function Users() {
  const { user, ready, accounts } = useAuthUser();
  const users = useResource("/api/admin/users", { skip: !ready || !user, deps: [user?.uid] });
  const list = users.data?.users || [];
  const replace = got => users.setData({ ...users.data,
                                         users: list.map(u => u.id === got.id ? got : u) });
  const naming = list.filter(u => u.role === "validated" || u.role === "admin").length;

  return (
    <>
      <Header links={[["/", "All games"], ["/mine", "My games"], ["/users", "Users", true]]} />
      <main>
        <p className="muted" style={{ marginTop: 0 }}>Every account, newest first. A validated
        account can name a game's teams; everything else is open to anyone signed in. Only the
        site's owner makes admins.</p>
        {ready && !user ? (
          <Card>
            <p style={{ marginTop: 0 }}>Sign in as an admin to see the accounts.</p>
            {accounts && <button className="primary" onClick={() => signIn().catch(() => {})}>
              Sign in with Google</button>}
          </Card>
        ) : !ready || users.loading ? <p className="muted">Loading…</p>
          : users.error?.status === 403 ? <p className="warn">This page is for admins.</p>
          : users.error ? <p className="warn">The accounts could not be loaded. Try again in a
              minute.</p>
          : (
            <div id="users" className="card">
              <p className="muted" style={{ marginTop: 0 }}>
                {list.length} {list.length === 1 ? "account" : "accounts"} · {naming} can name
                teams</p>
              <table>
                <tbody>
                  <tr><th>Account</th><th>Joined</th><th>Last seen</th><th /></tr>
                  {list.map(u => <Row key={u.id} u={u} onChanged={replace} />)}
                </tbody>
              </table>
            </div>
          )}
      </main>
    </>
  );
}
