/* Thinking time, league by league: the teams that took longest to decide
 * their shots, per end played, one row per team per game.
 *
 * Everything is computed by the API from a summary kept on each game
 * (service/thinking_report.py); this page only lays it out. One league at a
 * time, chosen from tabs, with the choice in the URL hash so a league's page
 * can be sent to its players. */
import { useEffect, useState } from "react";
import { mmss } from "./fmt.js";
import { Header } from "./ui.jsx";
import { useResource } from "./useAuth.js";

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
  const top = rows[0].per_end_s;
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

export function Thinking() {
  const report = useResource("/api/reports/thinking", { auth: false });
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
      <Header links={[["/", "All games"], ["/thinking", "Thinking time", true],
                      ["/submit", "Submit a link"]]} />
      <main>
        <p className="muted" style={{ marginTop: 0 }}>How long a team took to decide its shots,
        divided by the ends it played, grouped by league. A team is listed once for each game it
        played. The league is the YouTube playlist the stream was published in.</p>
        {report.loading ? <p className="muted">Loading…</p>
         : report.error ? <p className="warn">The report could not be loaded. Try again in a minute.</p>
         : !league ? <p className="muted">No games with timed rocks yet.</p>
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
