/* The catalogue.
 *
 * Two actions per game, and the distinction matters: Watch costs nothing and
 * creates nothing, Chart starts a charting session. Pressing Chart twice on
 * the same game used to mint a second, blank chart and strand the first;
 * /api/charts now hands back the one you already have.
 *
 * Laid out the way the club books the ice: a day, then each draw -- its time
 * set large and its league said once -- then a row per sheet. People come
 * looking for "Tuesday at seven, sheet 3". The league used to lead every row,
 * and it is the longest and least telling thing there: a whole evening of rows
 * read "Tuesday Super League 2026-2027", and the draw before it "Tuesday
 * Supper League 2026-2027", one letter apart.
 */
import { useEffect, useState } from "react";
import { authedFetch } from "./auth.js";
import { clockParts, dayParts, localDay } from "./fmt.js";
import { useAuthUser, useResource } from "./useAuth.js";
import { Header, TeamPicker, useCommitOnExit } from "./ui.jsx";

const leaguesIn = games => [...new Set(games.map(g => g.league).filter(Boolean))].sort();

/* A league without its season, for the list: the day heading above it already
 * says which season, and "2026-2027" on every draw is noise. The filter and
 * the editor keep the whole name, since that is what tells two seasons apart. */
const shortLeague = league => (league || "").replace(/\s+\d{4}(?:[-/]\d{2,4})?$/, "") || league;

/* The league you last filtered to, kept across reloads: most people follow
 * one league, so it is yours rather than the page's, like the viewer's prefs.
 * Storage can be blocked, and then the filter just forgets. */
const LEAGUE = "curlchart:league";

function loadLeague() {
  try { return localStorage.getItem(LEAGUE) || ""; } catch { return ""; }
}

function saveLeague(league) {
  try {
    if (league) localStorage.setItem(LEAGUE, league);
    else localStorage.removeItem(LEAGUE);
  } catch { /* not important enough to bother the user about */ }
}

/* When a game began on the clock, in ms. played_at is when the stream went
 * live and start_s how far into it the game starts. start_s alone is no use
 * across recordings: 3:55 into the evening stream is hours after 6:00 into the
 * afternoon one. */
const startsAt = g => {
  const t = g.played_at ? Date.parse(g.played_at) : NaN;
  return isNaN(t) ? null : t + (g.start_s ?? 0) * 1000;
};

/* That start put on the half hour the schedule said. Ice is booked on the
 * hour or the half, and each sheet's game gets going a little after it --
 * sometimes a good while after, rarely much before -- so a start up to 20
 * minutes past a slot belongs to it and one up to 10 before does too. That
 * puts the games that were played together on one time, and a day ordered by
 * it, then by sheet, reads like the schedule board. In the reader's time
 * rather than UTC's, so a zone offset by :30 or :45 still lands on :00 and
 * :30. */
const slotAt = g => {
  const t = startsAt(g);
  if (t == null) return null;
  const d = new Date(t);
  d.setMinutes(Math.ceil((d.getMinutes() + d.getSeconds() / 60 - 20) / 30) * 30, 0, 0);
  return d.getTime();
};

/* The value most of `games` share for `key`, "" standing for none. A draw is
 * normally one league on every sheet; a row that differs -- the last end of
 * the draw before, or a game nobody labelled -- says its own. */
function commonest(games, key) {
  const n = new Map();
  for (const g of games) n.set(g[key] || "", (n.get(g[key] || "") || 0) + 1);
  return [...n].sort((a, b) => b[1] - a[1])[0]?.[0] ?? "";
}

/* A league, as text -- or, signed in, a button that turns into an input.
 *
 * `games` are the ones this label speaks for: a row's own game, or every game
 * of a draw that carries the draw's league. The server names a whole recording
 * at once (one sheet for one night), so one POST per recording covers them,
 * and a recording's games in the draws either side move with it. The playlist
 * watcher labels everything it queues; this is for the links pasted by hand,
 * which arrive with nothing. */
function LeagueLabel({ league, games, all, editable, onSaved }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(league || "");
  const recordings = [...new Map(games.filter(g => g.source_id)
    .map(g => [g.video_id, g])).values()];

  const save = async () => {
    const next = text.trim();
    setEditing(false);
    const done = new Set();
    for (const g of recordings) {
      const res = await authedFetch(
        `/api/games/${encodeURIComponent(g.source_id)}/league`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ league: next }),
        });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        alert(data.detail || res.statusText);
        break;
      }
      done.add(g.video_id);
    }
    if (done.size) onSaved(list => list.map(g =>
      done.has(g.video_id) ? { ...g, league: next || null } : g));
  };
  const exit = useCommitOnExit(save, () => setEditing(false));

  if (!editable || !recordings.length)
    return league ? <div className="league">{shortLeague(league)}</div> : null;
  if (!editing)
    return (
      <div className="league">
        <button className="linky"
                title={recordings.length > 1 ? "Set the league for every game in this draw"
                                             : "Set the league for every game from this recording"}
                onClick={() => { setText(league || ""); setEditing(true); }}>
          {league ? shortLeague(league) : <span className="muted">set league</span>}
        </button>
      </div>
    );
  return (
    <div className="league">
      <input list="leagues" maxLength={60} aria-label="League" autoFocus
             value={text} onChange={e => setText(e.target.value)} {...exit} />
      <datalist id="leagues">
        {leaguesIn(all).map(l => <option key={l} value={l} />)}
      </datalist>
    </div>
  );
}

/* Who played, by the colour they threw. Per game, where the league is per
 * recording -- one sheet on one night is one league and two different pairs.
 * Nothing detects it, so the cell offers itself for filling in; read-only, an
 * unnamed game shows nothing rather than a dash on nearly every row. */
function TeamsCell({ game, editable, onSaved }) {
  const [editing, setEditing] = useState(false);
  const [red, setRed] = useState(game.team_red || "");
  const [yellow, setYellow] = useState(game.team_yellow || "");

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

  if (!editable)
    return shown ? <div className="teams">{shown}</div> : null;
  if (!editing)
    return (
      <div className="teams">
        <button className="linky" title="Say who played this game"
                onClick={() => { setRed(game.team_red || "");
                                 setYellow(game.team_yellow || ""); setEditing(true); }}>
          {shown || <span className="muted">name the teams</span>}
        </button>
      </div>
    );
  // Moving between the two boxes must not count as finishing, so the blur
  // guard asks whether focus left the pair.
  return (
    <div className="teams">
      <span className="teamedit">
        <input className="red" maxLength={60} placeholder="red" aria-label="Red team"
               autoFocus value={red} onChange={e => setRed(e.target.value)} {...exit} />
        <span className="muted">v</span>
        <input className="yellow" maxLength={60} placeholder="yellow" aria-label="Yellow team"
               value={yellow} onChange={e => setYellow(e.target.value)} {...exit} />
      </span>
    </div>
  );
}

const STATUS = { live: "Live", processing: "Processing", queued: "Queued",
                 pending_approval: "Awaiting approval", failed: "Failed" };

/* The ends played, drawn as well as counted: a one-end piece left over from a
 * split game reads as short before anyone reads its number. A status shows
 * only when it is not the usual one. */
function Ends({ game }) {
  const n = game.ends;
  const odd = game.status && game.status !== "ready";
  return (
    <div className="ends">
      {n != null && (
        <>
          <span className="dots" aria-hidden="true">
            {Array.from({ length: Math.min(n, 12) }, (_, i) => <i key={i} />)}
          </span>
          <span><b>{n}</b> <span className="word">{n === 1 ? "end" : "ends"}</span></span>
        </>
      )}
      {odd && <span className={`pill ${game.status}`}>{STATUS[game.status] || game.status}</span>}
    </div>
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
    <div className="actions">
      {game.source_id && (
        <a className="btn" href={`/g/${encodeURIComponent(game.source_id)}/`}>Watch</a>
      )}
      {/* Already charting it? Say so, and go straight there. A second press
          used to hand back a blank chart with the first one's grading
          stranded behind it. */}
      {have ? <a className="btn primary" href={`/c/${have}/`}>Open your chart</a>
            : <button disabled={busy || game.status === "failed"} onClick={chart}>Chart</button>}
    </div>
  );
}

/* One draw: its time and league, then its games by sheet. */
function Draw({ at, games, all, editable, onSaved, mine, team }) {
  const league = commonest(games, "league");
  const doubles = commonest(games, "format") === "doubles";
  const clock = at == null ? { time: "—", period: "" } : clockParts(at);
  return (
    <section className="draw">
      <div className="drawhead">
        <h3>{clock.time}{clock.period && <small>{clock.period}</small>}</h3>
        <LeagueLabel league={league || null} all={all} onSaved={onSaved} editable={editable}
                     games={games.filter(g => (g.league || "") === league)} />
        {doubles && <span className="pill doubles">doubles</span>}
      </div>
      <div className="drawgames">
        {games.map(g => {
          const own = editable && !!g.source_id;
          return (
            <div className="drawrow" key={g.source_id || `${g.video_id}:${g.start_s}`}>
              <div className="sheet"><span>Sheet</span> <b>{g.sheet ?? "?"}</b></div>
              <div className="mid">
                <div className="who">
                  <TeamsCell game={g} onSaved={onSaved} editable={own} />
                  {(g.league || "") !== league && (
                    <LeagueLabel league={g.league} games={[g]} all={all}
                                 onSaved={onSaved} editable={own} />
                  )}
                  {g.format === "doubles" && !doubles &&
                    <span className="pill doubles">doubles</span>}
                </div>
                <Ends game={g} />
              </div>
              <Actions game={g} mine={mine} team={team} />
            </div>
          );
        })}
      </div>
    </section>
  );
}

export function Games() {
  const { user, ready } = useAuthUser();
  const catalogue = useResource("/api/games", { auth: false });
  const charts = useResource("/api/me/charts",
                             { skip: !ready || !user, deps: [user?.uid] });
  const [league, setLeague] = useState(loadLeague);
  const [team, setTeam] = useState("");

  const all = catalogue.data?.games || [];
  // A live game's end count grows as it is played; keep the list current.
  const anyLive = all.some(g => g.status === "live");
  useEffect(() => {
    if (!anyLive) return undefined;
    const id = setInterval(catalogue.reload, 60000);
    return () => clearInterval(id);
  }, [anyLive, catalogue.reload]);
  const setAll = fn => catalogue.setData({ ...catalogue.data, games: fn(all) });
  const mine = new Map((charts.data?.charts || [])
    .filter(c => c.source_id).map(c => [c.source_id, c.slug]));
  const editable = !!user;

  const leagues = leaguesIn(all);
  // A remembered league the catalogue no longer has -- renamed, or its season
  // gone -- would hide every game behind a select reading "All leagues".
  const active = leagues.includes(league) ? league : "";
  const shown = all.filter(g => !active || g.league === active)
    .sort((a, b) => (slotAt(a) ?? 0) - (slotAt(b) ?? 0)
                 || (a.sheet ?? 99) - (b.sheet ?? 99)
                 || (startsAt(a) ?? 0) - (startsAt(b) ?? 0));
  // Day, then draw; both keep the order of `shown`, so draws run earliest first.
  const byDate = new Map();
  for (const g of shown) {
    const date = localDay(g.played_at) || "undated";
    if (!byDate.has(date)) byDate.set(date, new Map());
    const draws = byDate.get(date);
    const at = slotAt(g);
    if (!draws.has(at)) draws.set(at, []);
    draws.get(at).push(g);
  }

  return (
    <>
      <Header links={[["/thinking", "Thinking time"], ["/", "Submit a link"]]}>
        <select id="league" value={active}
                onChange={e => { setLeague(e.target.value); saveLeague(e.target.value); }}>
          <option value="">All leagues</option>
          {leagues.map(l => <option key={l} value={l}>{l}</option>)}
        </select>
      </Header>
      <main>
        <p className="muted">Every game we've processed. <strong>Watch</strong> steps
        through a game shot by shot — no link to keep, nothing to fill in.{" "}
        <strong>Chart</strong> gets you your own charting link, private to whoever has
        it, so a team can grade a game without anyone else seeing.</p>
        <TeamPicker value={team} onChange={setTeam} />
        <div id="list">
          {catalogue.loading ? <p className="muted">Loading…</p>
           : !shown.length ? <p className="muted">No games yet.</p>
           : [...byDate.keys()].sort().reverse().map(date => {
              const draws = byDate.get(date);
              const count = [...draws.values()].reduce((n, gs) => n + gs.length, 0);
              const { weekday, date: when } = date === "undated"
                ? { weekday: "Undated", date: "" } : dayParts(date);
              return (
                <div key={date}>
                  <div className="dayhead">
                    <h2>{weekday}</h2>
                    {when && <span className="date">{when}</span>}
                    <span className="count">{count} {count === 1 ? "game" : "games"}</span>
                  </div>
                  <div className="draws">
                    {[...draws].map(([at, games]) => (
                      <Draw key={at ?? "none"} at={at} games={games} all={all}
                            editable={editable} onSaved={setAll} mine={mine} team={team} />
                    ))}
                  </div>
                </div>
              );
            })}
        </div>
      </main>
    </>
  );
}
