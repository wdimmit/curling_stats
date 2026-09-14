/* The catalogue.
 *
 * Two actions per game, and the distinction matters: Watch costs nothing and
 * creates nothing, Chart starts a charting session. Pressing Chart twice on
 * the same game used to mint a second, blank chart and strand the first;
 * /api/charts now hands back the one you already have.
 */
import { useState } from "react";
import { authedFetch } from "./auth.js";
import { hms } from "./fmt.js";
import { useAuthUser, useResource } from "./useAuth.js";
import { Header, TeamPicker, useCommitOnExit } from "./ui.jsx";

const leaguesIn = games => [...new Set(games.map(g => g.league).filter(Boolean))].sort();

/* The league a game belongs to, editable once you are signed in.
 *
 * The playlist watcher labels everything it queues; a link pasted by hand
 * arrives with nothing, and an unlabelled game can only be found by scrolling
 * to its date. Read-only it is just text -- the row is busy enough without a
 * permanent input in it -- and a click turns it into one. */
function LeagueCell({ game, games, editable, onSaved }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(game.league || "");

  const save = async () => {
    const league = text.trim();
    const res = await authedFetch(
      `/api/games/${encodeURIComponent(game.source_id)}/league`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ league }),
      });
    setEditing(false);
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      alert(data.detail || res.statusText);
      return;
    }
    // One recording is one sheet for one night, so the server labelled every
    // game in it. Move them all here rather than reloading the catalogue.
    onSaved(all => all.map(g =>
      g.video_id === game.video_id ? { ...g, league: league || null } : g));
  };
  const exit = useCommitOnExit(save, () => setEditing(false));

  if (!editable) return <td>{game.league || <span className="muted">&mdash;</span>}</td>;
  if (!editing)
    return (
      <td className="league">
        <button className="linky" title="Set the league for every game from this recording"
                onClick={() => { setText(game.league || ""); setEditing(true); }}>
          {game.league || <span className="muted">set league</span>}
        </button>
      </td>
    );
  return (
    <td className="league">
      <input list="leagues" maxLength={60} aria-label="League" autoFocus
             value={text} onChange={e => setText(e.target.value)} {...exit} />
      <datalist id="leagues">
        {leaguesIn(games).map(l => <option key={l} value={l} />)}
      </datalist>
    </td>
  );
}

/* Who played, by the colour they threw. Per game, where the league is per
 * recording -- one sheet on one night is one league and two different pairs.
 * Nothing detects it, so the cell offers itself for filling in. */
function TeamsCell({ game, editable, onSaved }) {
  const [editing, setEditing] = useState(false);
  const [red, setRed] = useState(game.team_red || "");
  const [yellow, setYellow] = useState(game.team_yellow || "");

  const fallback = game.game_index == null
    ? (game.title || game.video_id) : `Game ${game.game_index + 1}`;
  const named = game.team_red || game.team_yellow;
  const shown = named ? (
    <>
      <span className="team red">{game.team_red || "red"}</span> v{" "}
      <span className="team yellow">{game.team_yellow || "yellow"}</span>
    </>
  ) : null;

  const save = async () => {
    const body = { red: red.trim(), yellow: yellow.trim() };
    const res = await authedFetch(
      `/api/games/${encodeURIComponent(game.source_id)}/teams`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    setEditing(false);
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      alert(data.detail || res.statusText);
      return;
    }
    onSaved(all => all.map(g => g.source_id === game.source_id
      ? { ...g, team_red: body.red || null, team_yellow: body.yellow || null } : g));
  };
  const exit = useCommitOnExit(save, () => setEditing(false));

  if (!editable) return <td>{shown || fallback}</td>;
  if (!editing)
    return (
      <td className="teams">
        <button className="linky" title="Say who played this game"
                onClick={() => { setRed(game.team_red || "");
                                 setYellow(game.team_yellow || ""); setEditing(true); }}>
          {shown || <>{fallback} <span className="muted">&mdash; name the teams</span></>}
        </button>
      </td>
    );
  // Moving between the two boxes must not count as finishing, so the blur
  // guard asks whether focus left the pair.
  return (
    <td className="teams">
      <span className="teamedit">
        <input className="red" maxLength={60} placeholder="red" aria-label="Red team"
               autoFocus value={red} onChange={e => setRed(e.target.value)} {...exit} />
        <span className="muted">v</span>
        <input className="yellow" maxLength={60} placeholder="yellow" aria-label="Yellow team"
               value={yellow} onChange={e => setYellow(e.target.value)} {...exit} />
      </span>
    </td>
  );
}

function Actions({ game, mine, team }) {
  const [busy, setBusy] = useState(false);
  const have = game.source_id && mine.get(game.source_id);

  async function chart() {
    setBusy(true);
    // A game the catalogue knows goes through /api/charts, which queues no
    // work and so does not spend one of the five submissions an hour. One that
    // is still processing has no source yet, so it still goes through
    // submissions.
    const [url, body] = game.source_id
      ? ["/api/charts", { source_id: game.source_id }]
      : ["/api/submissions", { url: game.video_id,
          ...(game.start_s != null ? { start_s: game.start_s } : {}) }];
    if (team) body.team_id = team;
    const res = await authedFetch(url, { method: "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { setBusy(false); alert(data.detail || res.statusText); return; }
    location.href = data.chart_url;
  }

  return (
    <td className="actions">
      {game.source_id && (
        <a className="btn" href={`/g/${encodeURIComponent(game.source_id)}/`}>Watch</a>
      )}
      {/* Already charting it? Say so, and go straight there. A second press
          used to hand back a blank chart with the first one's grading
          stranded behind it. */}
      {have ? <a className="btn primary" href={`/c/${have}/`}>Open your chart</a>
            : <button disabled={busy || game.status === "failed"} onClick={chart}>Chart</button>}
    </td>
  );
}

export function Games() {
  const { user, ready } = useAuthUser();
  const catalogue = useResource("/api/games", { auth: false });
  const charts = useResource("/api/me/charts",
                             { skip: !ready || !user, deps: [user?.uid] });
  const [league, setLeague] = useState("");
  const [team, setTeam] = useState("");

  const all = catalogue.data?.games || [];
  const setAll = fn => catalogue.setData({ ...catalogue.data, games: fn(all) });
  const mine = new Map((charts.data?.charts || [])
    .filter(c => c.source_id).map(c => [c.source_id, c.slug]));
  const editable = !!user;

  const shown = all.filter(g => !league || g.league === league);
  const byDate = {};
  for (const g of shown) (byDate[(g.played_at || "").slice(0, 10) || "undated"] ||= []).push(g);

  return (
    <>
      <Header links={[["/", "Submit a link"]]}>
        <select id="league" value={league} onChange={e => setLeague(e.target.value)}>
          <option value="">All leagues</option>
          {leaguesIn(all).map(l => <option key={l} value={l}>{l}</option>)}
        </select>
      </Header>
      <main>
        <p className="muted">Every game we've processed. <strong>Watch</strong> steps
        through a game shot by shot — no link to keep, nothing to fill in.{" "}
        <strong>Chart</strong> gets you your own charting link, private to whoever has
        it, so a team can grade a game without anyone else seeing.</p>
        <TeamPicker value={team} onChange={setTeam} />
        <div id="list" className="card">
          {catalogue.loading ? <span className="muted">Loading…</span>
           : !shown.length ? <span className="muted">No games yet.</span>
           : Object.keys(byDate).sort().reverse().map(date => (
              <div key={date}>
                <h2>{date}</h2>
                <table>
                  <tbody>
                    <tr><th>Sheet</th><th>League</th><th>Game</th><th>Starts</th>
                        <th>Ends</th><th>Status</th><th /></tr>
                    {byDate[date]
                      .sort((a, b) => (a.sheet ?? 99) - (b.sheet ?? 99)
                                   || (a.start_s ?? 0) - (b.start_s ?? 0))
                      .map(g => (
                        <tr key={g.source_id || `${g.video_id}:${g.start_s}`}>
                          <td>{g.sheet ?? "?"}</td>
                          <LeagueCell game={g} games={all} onSaved={setAll}
                                      editable={editable && !!g.source_id} />
                          <TeamsCell game={g} onSaved={setAll}
                                     editable={editable && !!g.source_id} />
                          <td>{g.start_s == null ? "—" : hms(g.start_s)}</td>
                          <td>{g.ends ?? "—"}</td>
                          <td><span className={`pill ${g.status || ""}`}>{g.status || ""}</span></td>
                          <Actions game={g} mine={mine} team={team} />
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
            ))}
        </div>
      </main>
    </>
  );
}
