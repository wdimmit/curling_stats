/* Thinking time, league by league: which games took longest to play, and in
 * which one team did most of the deciding.
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

function readHash() {
  try { return decodeURIComponent(location.hash.slice(1)); } catch { return ""; }
}

/* "Tue Mar 3": the weekday is how a league night is remembered. */
function when(iso) {
  const d = iso ? new Date(iso) : null;
  if (!d || isNaN(d)) return "—";
  return d.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" })
    .replace(",", "");
}

function span(first, last) {
  const a = first && new Date(first), b = last && new Date(last);
  if (!a || !b) return "";
  const md = d => d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  if (a.toDateString() === b.toDateString()) return `${md(a)}, ${b.getFullYear()}`;
  return `${md(a)} – ${md(b)}, ${b.getFullYear()}`;
}

const pct = f => `${Math.round(f * 100)}%`;

function Team({ game, colour }) {
  const name = game[`team_${colour}`];
  return <span className={`team ${colour}`}>{name || (colour === "red" ? "Red" : "Yellow")}</span>;
}

/* Who played, by colour -- or, with nobody named, which game on the sheet
 * that night, as the catalogue says it. Either way a link to watch it. */
function GameCell({ game }) {
  const href = `/g/${encodeURIComponent(game.source_id)}/`;
  if (!game.team_red && !game.team_yellow)
    return (
      <td className="teams">
        <a className="muted" href={href}>
          {game.games_in_video > 1 ? `Game ${game.game_index + 1}` : "Teams not named"}
        </a>
      </td>
    );
  return (
    <td className="teams">
      <a className="plain" href={href}>
        <Team game={game} colour="red" /> v <Team game={game} colour="yellow" />
      </a>
    </td>
  );
}

function PaceTable({ rows }) {
  const top = Math.max(...rows.map(r => r.per_end_s));
  return (
    <table className="rank">
      <tbody>
        <tr><th>#</th><th>Date</th><th>Sheet</th><th>Game</th><th className="num">Ends</th>
            <th className="num">Thinking</th><th>Per end</th></tr>
        {rows.map((g, i) => (
          <tr key={g.source_id}>
            <td className="rk">{i + 1}</td>
            <td className="when">{when(g.played_at)}</td>
            <td data-label="Sheet">{g.sheet ?? "?"}</td>
            <GameCell game={g} />
            <td className="num" data-label="Ends">{g.ends}</td>
            <td className="num" data-label="Thinking">{mmss(g.total_s)}</td>
            <td className="pace">
              <div>
                <span className="val">{mmss(g.per_end_s)}</span>
                <span className="mag" aria-hidden="true">
                  <span style={{ width: `${(100 * g.per_end_s / top).toFixed(1)}%` }} />
                </span>
              </div>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function SplitTable({ rows }) {
  return (
    <table className="rank">
      <tbody>
        <tr><th>#</th><th>Date</th><th>Sheet</th><th>Game</th><th>Split of thinking time</th>
            <th>Took longer</th><th className="num">By</th></tr>
        {rows.map((g, i) => {
          const red = g.team_red || "Red", yellow = g.team_yellow || "Yellow";
          return (
            <tr key={g.source_id}>
              <td className="rk">{i + 1}</td>
              <td className="when">{when(g.played_at)}</td>
              <td data-label="Sheet">{g.sheet ?? "?"}</td>
              <GameCell game={g} />
              <td className="split">
                <span className="sbar" role="img"
                      aria-label={`${red} ${pct(g.share_red)}, ${yellow} ${pct(g.share_yellow)}`}
                      title={`${red} ${mmss(g.red_s)} · ${yellow} ${mmss(g.yellow_s)}`}>
                  <span className="r" style={{ width: `calc(${(g.share_red * 100).toFixed(2)}% - 1px)` }} />
                  <span className="y" style={{ width: `calc(${(g.share_yellow * 100).toFixed(2)}% - 1px)` }} />
                  <i className="mid" />
                </span>
                <span className="spct">{pct(g.share_red)} · {pct(g.share_yellow)}</span>
              </td>
              <td className="longer" data-label="Took longer"><Team game={g} colour={g.longer} /></td>
              <td className="num" data-label="By">{mmss(g.gap_s)}</td>
            </tr>
          );
        })}
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
      <Header links={[["/games", "All games"], ["/thinking", "Thinking time", true],
                      ["/", "Submit a link"]]} />
      <main>
        <p className="muted" style={{ marginTop: 0 }}>How long each game's teams took to decide
        their shots, grouped by league. The league is the YouTube playlist the stream was
        published in.</p>
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
                    `league average ${mmss(league.avg_per_end_s)} per end`]
                    .filter(Boolean).join(" · ")}
                </span>
              </div>
              <h2>Most thinking per end <small>both teams together, divided by ends played</small></h2>
              <PaceTable rows={league.by_pace} />
              <h2>Most lopsided <small>ranked by the larger team's share of the game's thinking time</small></h2>
              <SplitTable rows={league.by_split} />
              {league.games > league.by_pace.length ? (
                <p className="muted note">The top {league.by_pace.length} of {league.games} games
                each way.</p>
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
