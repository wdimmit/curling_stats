/* The printable summary.
 *
 * Always renders its content, even while closed. `@media print` forces
 * #report visible and hides everything else, so a component that returned
 * null when the report was shut would print a blank page -- and the person
 * printing would have no reason to suspect the button had anything to do with
 * it. Whether it is on screen is the stylesheet's business, via body.reporting
 * and #report.show.
 */
import { Fragment } from "react";
import { GROUPS, POSITIONS, TYPE, avg, clockText, pct } from "../core/index.mjs";
import { ThinkingBars, ThinkingChart } from "./Charts.jsx";

function TypeRows({ bucket }) {
  return GROUPS.map(group => {
    const ids = Object.keys(bucket.types)
      .filter(id => (TYPE[id]?.group || "Other") === group);
    if (!ids.length) return null;
    return (
      <Fragment key={group}>
        <tr><td className="name">{group}</td><td colSpan={4} /></tr>
        {ids.map(id => (
          <tr key={id}>
            <td className="sub">{TYPE[id]?.name || id}</td>
            <td>{bucket.types[id].thrown}</td>
            <td>{bucket.types[id].graded}</td>
            <td>{avg(bucket.types[id])}</td>
            <td className="pct">{pct(bucket.types[id])}</td>
          </tr>
        ))}
      </Fragment>
    );
  });
}

function TeamCard({ colour, name, stats, thinking }) {
  const total = { thrown: 0, graded: 0, sum: 0 };
  for (const p of POSITIONS) {
    total.thrown += stats[p].thrown;
    total.graded += stats[p].graded;
    total.sum += stats[p].sum;
  }
  const players = POSITIONS.filter(p => stats[p].thrown);
  return (
    <div className="card">
      <h2><span className="swatch" style={{ background: `var(--${colour})` }} />{name}</h2>
      {players.length ? players.map(p => (
        <Fragment key={p}>
          <h3>{p}</h3>
          <table>
            <tbody>
              <tr>
                <th className="name" /><th>thrown</th><th>graded</th><th>avg</th><th>%</th>
              </tr>
              <TypeRows bucket={stats[p]} />
              <tr className="total">
                <td className="name">all</td>
                <td>{stats[p].thrown}</td><td>{stats[p].graded}</td>
                <td>{avg(stats[p])}</td><td className="pct">{pct(stats[p])}</td>
              </tr>
            </tbody>
          </table>
        </Fragment>
      )) : <div className="muted">No shots.</div>}
      <table style={{ marginTop: 12 }}>
        <tbody>
          <tr className="total">
            <td className="name">team</td>
            <td>{total.thrown}</td><td>{total.graded}</td>
            <td>{avg(total)}</td><td className="pct">{pct(total)}</td>
          </tr>
          <tr><td className="name">thinking</td><td colSpan={4}>{clockText(thinking)}</td></tr>
        </tbody>
      </table>
    </div>
  );
}

export function Report({ view, stats, think, series, actions }) {
  const total = ["red", "yellow"].reduce(
    (n, c) => n + POSITIONS.reduce((m, p) => m + stats[c][p].thrown, 0), 0);
  const graded = ["red", "yellow"].reduce(
    (n, c) => n + POSITIONS.reduce((m, p) => m + stats[c][p].graded, 0), 0);

  return (
    <>
      <div className="row" style={{ marginBottom: 12 }}>
        <h1>Game {view.gi + 1} report</h1>
        <span className="muted">{graded} of {total} shots graded</span>
        <span className="grow" />
        <button className="noprint" onClick={() => print()}>Print</button>
      </div>

      {graded < total && (
        <div className="warn noprint">
          Percentages cover only the {graded} graded shots. Ungraded shots are
          counted as thrown, never as misses.
        </div>
      )}

      {(think.unmeasured || think.estimated) ? (
        <div className="warn noprint">
          Thinking time is read from {think.measured} of{" "}
          {think.measured + think.unmeasured} shots — an end's first stone has
          nothing to time from, and a few throws the camera never caught.
          {think.estimated ? ` ${think.estimated} of the ${think.measured} are `
            + "estimated: the throw was not seen leaving the house, so the clock "
            + "is stopped the usual 16 s before the rock arrived." : ""}
          {" "}Treat these as lower bounds.
        </div>
      ) : null}

      {think.measured ? (
        <div className="card clockcard" style={{ marginTop: 12 }}>
          <h2>Thinking time<span className="muted"> &mdash; cumulative, then per rock</span></h2>
          <ThinkingChart series={series} />
          <ThinkingBars series={series} onSelect={actions.goToBarFromReport} />
          <div className="key muted">
            <span><i className="sw red" />red {clockText(think.red)}</span>
            <span><i className="sw yellow" />yellow {clockText(think.yellow)}</span>
            {think.estimated ? <span><i className="sw est" />estimated interval</span> : null}
          </div>
        </div>
      ) : null}

      <div className="reportgrid" style={{ marginTop: 12 }}>
        {["red", "yellow"].map(c => (
          <TeamCard key={c} colour={c} name={view.game.teams[c].name || c}
                    stats={stats[c]} thinking={think[c]} />
        ))}
      </div>
    </>
  );
}
