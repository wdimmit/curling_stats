/* Thinking time, league by league: the teams that took longest to decide
 * their shots, per end played, one row per team per game.
 *
 * Everything is computed by the API from a summary kept on each game
 * (service/thinking_report.py); this page only lays it out. One league at a
 * time, chosen from tabs, with the choice in the URL hash so a league's page
 * can be sent to its players.
 *
 * With accounts on it is for people signed in (api.py api_thinking_report):
 * it ranks teams by name, slowest first. Anyone else is offered sign-in. */
import { useEffect, useState } from "react";
import { signIn } from "./auth.js";
import { signInTrouble } from "./signinState.mjs";
import { mmss } from "./fmt.js";
import { Card, Header } from "./ui.jsx";
import { useAuthUser, useResource } from "./useAuth.js";

const keyOf = lg => lg.format === "fours" ? lg.league : `${lg.league} · ${lg.format}`;
const other = { red: "yellow", yellow: "red" };
const named = colour => colour === "red" ? "Red" : "Yellow";

function readHash() {
  try { return decodeURIComponent(location.hash.slice(1)); } catch { return ""; }
}

/* "Mar 3". No weekday: a league plays on the same night every week, and the
 * tab already says which. */
function when(iso) {
  const d = iso ? new Date(iso) : null;
  if (!d || isNaN(d)) return "—";
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function span(first, last) {
  const a = first && new Date(first), b = last && new Date(last);
  if (!a || !b) return "";
  const md = d => d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  if (a.toDateString() === b.toDateString()) return `${md(a)}, ${b.getFullYear()}`;
  return `${md(a)} – ${md(b)}, ${b.getFullYear()}`;
}

/* The ranked team first, in its colour, then who it played -- by name, or by
 * colour with nobody named. A link to watch the game either way. */
function TeamCell({ row }) {
  const nameless = !row.team && !row.opponent;
  return (
    <td className="teams">
      <a className="plain" href={`/g/${encodeURIComponent(row.source_id)}/`}>
        <span className={`team ${row.colour}`}>{row.team || named(row.colour)}</span>
        <span className="vs"> v {row.opponent || named(other[row.colour])}</span>
        {nameless && row.games_in_video > 1
          ? <span className="vs"> · game {row.game_index + 1}</span> : null}
      </a>
    </td>
  );
}

function PaceTable({ rows }) {
  const top = Math.max(...rows.map(r => r.per_end_s));
  return (
    <table className="rank">
      <tbody>
        <tr><th>#</th><th>Date</th><th>Sheet</th><th>Team</th><th className="num">Ends</th>
            <th className="num">Thinking</th><th>Per end</th></tr>
        {rows.map((r, i) => (
          <tr key={`${r.source_id}/${r.colour}`}>
            <td className="rk">{i + 1}</td>
            <td className="when">{when(r.played_at)}</td>
            <td data-label="Sheet">{r.sheet ?? "?"}</td>
            <TeamCell row={r} />
            <td className="num" data-label="Ends">{r.ends}</td>
            <td className="num" data-label="Thinking">{mmss(r.thinking_s)}</td>
            <td className="pace">
              <div>
                <span className="val">{mmss(r.per_end_s)}</span>
                <span className="mag" aria-hidden="true">
                  <span style={{ width: `${(100 * r.per_end_s / top).toFixed(1)}%` }} />
                </span>
              </div>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/* Each named team once: its thinking over every end it played in the league,
 * and how many games that is. */
function TeamTable({ rows }) {
  const top = Math.max(...rows.map(r => r.per_end_s));
  return (
    <table className="rank">
      <tbody>
        <tr><th>#</th><th>Team</th><th className="num">Games</th><th>Per end</th></tr>
        {rows.map((r, i) => (
          <tr key={r.team}>
            <td className="rk">{i + 1}</td>
            <td className="teams">{r.team}</td>
            <td className="num" data-label="Games">{r.games}</td>
            <td className="pace">
              <div>
                <span className="val">{mmss(r.per_end_s)}</span>
                <span className="mag" aria-hidden="true">
                  <span style={{ width: `${(100 * r.per_end_s / top).toFixed(1)}%` }} />
                </span>
              </div>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function Thinking() {
  const { user, ready, accounts } = useAuthUser();
  // Asked only once auth has decided, and then with the person's token.
  const report = useResource("/api/reports/thinking",
                             { skip: !ready || (accounts && !user), deps: [user?.uid] });
  const signedOut = ready && accounts && !user;
  const [err, setErr] = useState("");
  const [picked, setPicked] = useState(readHash);
  useEffect(() => {
    const on = () => setPicked(readHash());
    addEventListener("hashchange", on);
    return () => removeEventListener("hashchange", on);
  }, []);

  const leagues = report.data?.leagues || [];
  const league = leagues.find(lg => keyOf(lg) === picked) || leagues[0];
  const choose = lg => { location.hash = encodeURIComponent(keyOf(lg)); };

  return (
    <>
      <Header links={[["/", "All games"], ["/thinking", "Thinking time", true], ["/ice", "Ice report"],
                      ["/submit", "Submit a link"]]} />
      <main>
        <p className="muted" style={{ marginTop: 0 }}>How long a team took to decide its shots,
        divided by the ends it played, grouped by league. The first two tables list a team once for
        each game it played; the third averages each named team over all its games. The league is the YouTube playlist the stream was published in. Leagues with no
        game in the last two weeks are left off.</p>
        {signedOut ? (
          <Card>
            <p style={{ marginTop: 0 }}>Thinking time is for signed-in members.</p>
            <button className="primary"
                    onClick={() => signIn().catch(e => setErr(signInTrouble(e)))}>
              Sign in</button>
            {err ? <p className="muted">{err}</p> : null}
          </Card>
        ) : !ready || report.loading ? <p className="muted">Loading…</p>
         : report.error ? <p className="warn">The report could not be loaded. Try again in a minute.</p>
         : !league ? <p className="muted">No league has played a timed game in the last two weeks.</p>
         : (
          <>
            <div className="tabs" role="tablist">
              {leagues.map(lg => (
                <button key={keyOf(lg)} role="tab" aria-selected={lg === league}
                        className={`tab${lg === league ? " on" : ""}`} onClick={() => choose(lg)}>
                  {lg.league} <b>{lg.games}</b>
                  {lg.format === "doubles" ? <span className="pill doubles">doubles</span> : null}
                </button>
              ))}
            </div>
            <div id="thinking" className="card">
              <div className="lhead">
                <h1>{league.league}</h1>
                <span className="muted">
                  {[`${league.games} ${league.games === 1 ? "game" : "games"}`,
                    span(league.first_played_at, league.last_played_at),
                    `a team averages ${mmss(league.avg_per_end_s)} per end`]
                    .filter(Boolean).join(" · ")}
                </span>
              </div>
              <h2>Slowest teams per end <small>a team's thinking time, divided by ends played</small></h2>
              <PaceTable rows={league.teams} />
              {league.games * 2 > league.teams.length ? (
                <p className="muted note">The slowest {league.teams.length} of {league.games * 2}:
                each of the league's {league.games} games has two teams.</p>
              ) : null}
              <h2>Fastest teams per end <small>the same measure, quickest first</small></h2>
              <PaceTable rows={league.fastest} />
              {league.games * 2 > league.fastest.length ? (
                <p className="muted note">The fastest {league.fastest.length} of {league.games * 2}:
                each of the league's {league.games} games has two teams.</p>
              ) : null}
              <h2>Teams on average <small>every game a team played, slowest first</small></h2>
              {league.by_team?.length ? <TeamTable rows={league.by_team} />
                : <p className="muted note">No team in this league has been named yet.</p>}
              {league.by_team?.length && league.unnamed_rows ? (
                <p className="muted note">Teams are matched by name, so {league.unnamed_rows} of
                the league's {league.games * 2} team-games, with no name given, are left out.</p>
              ) : null}
              <p className="muted note">Thinking time runs from when the previous rock comes to rest
              (plus 5 s) until the next rock crosses the tee line at the throwing end. The first rock
              of each end isn't timed, so every figure is a lower bound.
              {league.from !== "playlist" ? " This league's name is read from the stream titles: its"
                + " streams are not in a club playlist yet." : ""}</p>
            </div>
            {report.data.pending ? (
              <p className="muted note">{report.data.pending} more{" "}
              {report.data.pending === 1 ? "game is" : "games are"} still being summarised.</p>
            ) : null}
          </>
        )}
      </main>
    </>
  );
}
