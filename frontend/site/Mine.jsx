import { useEffect, useState } from "react";
import { authedFetch, signIn } from "./auth.js";
import { day } from "./fmt.js";
import { useAuthUser, useResource } from "./useAuth.js";
import { Card, Header, Warn } from "./ui.jsx";

function ChartRow({ c }) {
  return (
    <tr>
      <td>{day(c.played_at)}</td>
      <td>
        {c.title || ""}{" "}
        {c.team_name && <span className="pill">{c.team_name}</span>}
        {c.duplicate_of && (
          <Warn>Your team has another chart for this game.{" "}
            <a href={`/c/${c.duplicate_of}/`}>Open it</a> — this one is kept as it is.</Warn>
        )}
      </td>
      <td>{c.sheet ?? "?"}</td>
      <td>{c.shots_charted || 0}</td>
      <td><span className={`pill ${c.status || ""}`}>{c.status || ""}</span></td>
      <td className="actions">
        <a className="btn primary" href={`/c/${c.slug}/`}>Chart</a>
        {c.review_url && <a className="btn" href={c.review_url}>Watch</a>}
      </td>
    </tr>
  );
}

function Teams({ user }) {
  const [teams, setTeams] = useState(null);
  const [invites, setInvites] = useState({});
  const [name, setName] = useState("");

  /* Each team is fetched again in full, because /api/me carries only the id
   * and name and the member list is what makes the block worth showing. */
  const load = async () => {
    const res = await authedFetch("/api/me");
    if (!res.ok) { setTeams("error"); return; }
    const me = await res.json();
    const full = [];
    for (const t of me.teams) {
      const one = await authedFetch(`/api/teams/${t.id}`);
      full.push(one.ok ? await one.json() : t);
    }
    setTeams(full);
  };
  useEffect(() => { load(); }, [user?.uid]);   // eslint-disable-line react-hooks/exhaustive-deps

  async function invite(teamId) {
    setInvites(v => ({ ...v, [teamId]: "…" }));
    const res = await authedFetch(`/api/teams/${teamId}/invites`,
      { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    if (!res.ok) return setInvites(v => ({ ...v, [teamId]: "could not make an invitation" }));
    const { url } = await res.json();
    let text = url;
    try { await navigator.clipboard.writeText(url); text += " (copied)"; } catch { /* fine */ }
    setInvites(v => ({ ...v, [teamId]: text }));
  }

  async function create() {
    if (!name.trim()) return;
    const res = await authedFetch("/api/teams", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name.trim() }) });
    if (res.ok) { setName(""); await load(); }
  }

  return (
    <>
      <h2>Teams</h2>
      <div id="teams" className="card">
        {teams === null ? <span className="muted">Loading…</span>
         : teams === "error" ? "Could not load your teams."
         : !teams.length ? <span className="muted">You are not on a team yet.</span>
         : teams.map(t => (
            <div key={t.id} style={{ marginBottom: 14 }}>
              <strong>{t.name}</strong> — <span className="muted">
                {(t.members || []).map(m =>
                  `${m.name || m.email || m.id}${m.is_owner ? " (owner)" : ""}`).join(", ")}
              </span>
              <div className="row" style={{ marginTop: 6 }}>
                <button onClick={() => invite(t.id)}>Invite someone</button>
                <span className="muted">{invites[t.id] || ""}</span>
              </div>
            </div>
          ))}
      </div>
      <Card style={{ marginTop: 12 }}>
        <label htmlFor="teamname">Start a team</label>
        <div className="row">
          <input id="teamname" placeholder="Thistles" style={{ flex: 1 }} maxLength={60}
                 value={name} onChange={e => setName(e.target.value)} />
          <button id="maketeam" onClick={create}>Create</button>
        </div>
        <p className="muted" style={{ fontSize: 13 }}>Everyone on a team shares one chart
        per game, so you are all filling in the same sheet rather than four of them.</p>
      </Card>
    </>
  );
}

function Claim({ onAdded }) {
  const [raw, setRaw] = useState("");
  const [note, setNote] = useState(null);

  async function add() {
    // Accept a whole URL or a bare slug -- people paste both.
    const slug = (raw.trim().match(/\/c\/([^/?#]+)/) || [null, raw.trim()])[1];
    if (!slug) return;
    const res = await authedFetch(`/api/me/charts/${encodeURIComponent(slug)}/claim`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    setNote(res.ok ? <div className="ok">Added.</div> : <Warn>{
      res.status === 404 ? "No chart with that link."
      : res.status === 403 ? "That chart belongs to a team you are not on."
      : "Could not add that link."}</Warn>);
    if (res.ok) { setRaw(""); onAdded(); }
  }

  return (
    <>
      <h2>Add a link you already have</h2>
      <Card>
        <div className="row">
          <input id="claimurl" placeholder="https://…/c/…" style={{ flex: 1 }}
                 value={raw} onChange={e => setRaw(e.target.value)} />
          <button id="claim" onClick={add}>Add</button>
        </div>
        <p className="muted" style={{ fontSize: 13 }}>Charted something before you signed
        in? Paste its link and it joins your list.</p>
        <div id="claimnote">{note}</div>
      </Card>
    </>
  );
}

export function Mine() {
  const { user, ready, accounts } = useAuthUser();
  const charts = useResource("/api/me/charts", { skip: !ready || !user, deps: [user?.uid] });
  const [err, setErr] = useState("");

  return (
    <>
      <Header links={[["/games", "All games"], ["/", "Submit a link"]]} />
      <main>
        {ready && !user && (
          <div id="signedout">
            <Card>
              <h2 style={{ marginTop: 0 }}>Keep your games</h2>
              <p className="muted">Sign in and the games you chart stay in one list, on
              every device. Every chart link keeps working exactly as it does now, signed
              in or not — an account only gives you a way back to them.</p>
              {accounts && (
                <button id="signin" className="primary"
                        onClick={() => signIn().catch(e => setErr(e.code || e.message))}>
                  Sign in with Google
                </button>
              )}
              {(!accounts || err) && (
                <p className="muted" id="disabled">
                  {err || "Accounts are not switched on for this site."}
                </p>
              )}
            </Card>
          </div>
        )}

        {ready && user && (
          <div id="signedin">
            <h2>My games</h2>
            <div id="charts" className="card">
              {charts.loading ? <span className="muted">Loading…</span>
               : charts.error ? "Could not load your games."
               : charts.data?.charts?.length ? (
                  <table>
                    <tbody>
                      <tr><th>Played</th><th>Game</th><th>Sheet</th><th>Charted</th>
                          <th>Status</th><th /></tr>
                      {charts.data.charts.map(c => <ChartRow key={c.slug} c={c} />)}
                    </tbody>
                  </table>
                ) : (
                  <span className="muted">Nothing yet. Pick a game from{" "}
                    <a href="/games">the catalogue</a> and press Chart.</span>
                )}
            </div>
            <Teams user={user} />
            <Claim onAdded={charts.reload} />
          </div>
        )}
      </main>
      <footer>Your links keep working whether or not you are signed in.</footer>
    </>
  );
}
