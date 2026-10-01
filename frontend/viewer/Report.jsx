/* The game report: the printable summary.
 *
 * Always renders its content, even while closed. `@media print` forces
 * #report visible and hides everything else, so a component that returned
 * null when the report was shut would print a blank page -- and the person
 * printing would have no reason to suspect the button had anything to do with
 * it. Whether it is on screen is the stylesheet's business, via body.reporting
 * and #report.show.
 *
 * Every number comes from core/report.mjs; this file only lays them out, as
 * on board B of the "Game Report Readability" canvas. See
 * docs/superpowers/specs/2026-10-01-game-report-and-entered-scores-design.md.
 */
import { useMemo } from "react";
import {
  byEnd, clockText, coverage, coverageText, detailRows, endList, headToHead, liveGame,
  longestThinks, positionText, reportMeta, reportNotes, teamNames,
} from "../core/index.mjs";
import { Dot, ReportClock } from "./Charts.jsx";

const COLORS = ["red", "yellow"];
const other = c => (c === "red" ? "yellow" : "red");
const better = (a, b) => a != null && (b == null || a >= b);
const percent = v => (v == null ? "—" : `${v}%`);

function Team({ c, names, size }) {
  return <span className="rpt-team"><Dot c={c} size={size} />{names[c]}</span>;
}

function Hammer() {
  return (
    <svg className="ham" viewBox="0 0 6 6" role="img" aria-label="had the hammer">
      <circle cx="3" cy="3" r="3" />
    </svg>
  );
}

function ScoreCell({ end, c }) {
  if (!end.score) return <span className="none">?</span>;
  const n = end.score[c] || 0;
  return <span className={n ? "won" : "zero"}>{n}</span>;
}

function ByEnd({ table, names }) {
  const { ends, total, status } = table;
  const span = ends.length + 2;
  const rowsFor = cells => COLORS.map(c => (
    <tr key={c}>
      <th scope="row"><Team c={c} names={names} /></th>
      {cells(c)}
    </tr>
  ));
  return (
    <div className="rpt-scroll">
      <table className="rpt-byend">
        <thead>
          <tr>
            <td />
            {ends.map(e => <th key={e.number} scope="col">End {e.number}</th>)}
            <th scope="col">Total</th>
          </tr>
        </thead>
        <tbody>
          <tr className="blk"><th colSpan={span}>Score</th></tr>
          {status !== "ok" ? (
            <tr>
              <td className="rpt-noscore" colSpan={span}>
                {status === "predates"
                  ? "This chart predates board reading, so no score is shown."
                  : "The wall board was read, but its scores could not be matched to these "
                    + "ends. Setting this game’s start time places them."}
              </td>
            </tr>
          ) : rowsFor(c => (
            <>
              {ends.map(e => (
                <td key={e.number}>
                  {e.hammer === c ? <Hammer /> : null}
                  <ScoreCell end={e} c={c} />
                </td>
              ))}
              <td className={`tot${total.complete ? "" : " partial"}`}>
                {total.score ? total.score[c] : "—"}
              </td>
            </>
          ))}
          <tr className="blk"><th colSpan={span}>Shooting</th></tr>
          {rowsFor(c => (
            <>
              {ends.map(e => (
                <td key={e.number}
                    className={e.shooting[c] == null ? "none"
                      : better(e.shooting[c], e.shooting[other(c)]) ? "won" : undefined}>
                  {percent(e.shooting[c])}
                </td>
              ))}
              <td className="tot">{percent(total.shooting[c])}</td>
            </>
          ))}
          <tr className="blk"><th colSpan={span}>Thinking time</th></tr>
          {rowsFor(c => (
            <>
              {ends.map(e => <td key={e.number}>{clockText(e.thinking[c])}</td>)}
              <td className="tot">{clockText(total.thinking[c])}</td>
            </>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ByEndKey({ table, live }) {
  const unread = table.ends.filter(e => !e.score).map(e => e.number);
  const ungraded = table.ends
    .filter(e => e.shooting.red == null && e.shooting.yellow == null).map(e => e.number);
  const ok = table.status === "ok";
  return (
    <div className="rpt-key">
      {ok ? <span><Hammer />had the hammer</span> : null}
      {ok && unread.length ? (
        <span>
          ? {endList(unread)} {!table.boardRead ? "— the wall board couldn’t be read"
            : live ? "not posted yet" : "not read from the wall board"}
        </span>
      ) : null}
      {ok && unread.length && table.total.score ? <span>totals count the ends with a score</span> : null}
      {ungraded.length ? <span>— {endList(ungraded)} not graded</span> : null}
      <span><b>bold</b> = the better of the two</span>
    </div>
  );
}

function Bar({ v, c, side }) {
  const w = v == null ? 0 : v;
  return (
    <svg className="duel-bar" viewBox="0 0 100 14" preserveAspectRatio="none" aria-hidden="true">
      <rect className="track" width="100" height="14" />
      <rect className={c} x={side === "l" ? 100 - w : 0} width={w} height="14" />
    </svg>
  );
}

function Num({ v, against, n, side }) {
  return (
    <span className={`duel-num ${side}${better(v, against) ? " better" : ""}`}>
      {percent(v)}
      {n != null ? <small>{n} rock{n === 1 ? "" : "s"}</small> : null}
    </span>
  );
}

function Duel({ rows, names }) {
  return (
    <div className="duel">
      <div className="duel-head">
        <Team c="red" names={names} />
        <Team c="yellow" names={names} />
      </div>
      {rows.map(r => (
        <div key={r.id} className={`duel-row${r.total ? " total" : ""}`}>
          <Num v={r.red} against={r.yellow} n={r.redN} side="l" />
          <Bar v={r.red} c="red" side="l" />
          <span className="duel-label">{r.label}</span>
          <Bar v={r.yellow} c="yellow" side="r" />
          <Num v={r.yellow} against={r.red} n={r.yellowN} side="r" />
        </div>
      ))}
    </div>
  );
}

function HeadToHead({ h2h, names }) {
  const rows = [...h2h.positions, { id: "team", label: "Team", ...h2h.team, total: true }];
  const others = h2h.other.map(o => `${names[o.color]} ${o.thrown} ${o.type.toLowerCase()}`);
  return (
    <div className="rpt-h2h-body">
      <div>
        <h2>By position</h2>
        <p className="sub">Shooting percentage, player against player</p>
        <Duel rows={rows} names={names} />
        <p className="rpt-cap">
          {h2h.perPlayer
            ? `Each player: ${h2h.perPlayer.graded} of ${h2h.perPlayer.thrown} rocks graded`
            : "Graded rocks only"}
        </p>
      </div>
      <div>
        <h2>By shot type</h2>
        <Duel rows={h2h.types} names={names} />
        {others.length
          ? <p className="rpt-cap">Other — {others.join(", ")} — is in the detail below.</p>
          : null}
      </div>
    </div>
  );
}

function Thinking({ view, series, names, onSelect }) {
  const longest = useMemo(() => longestThinks(view, series), [view, series]);
  return (
    <section className="card rpt-think">
      <h2>Thinking time</h2>
      <p className="sub">Running total through the game, then each rock. Click a bar to watch that rock.</p>
      <div className="rpt-think-body">
        <ReportClock series={series} names={names} onSelect={onSelect} />
        <div className="rpt-long screenonly">
          <h3>Longest thinks</h3>
          {longest.map(l => (
            <button key={`${l.ei}.${l.si}`} type="button" onClick={() => onSelect(l)}>
              <span className="t">{clockText(l.secs)}</span>
              <span className="who">
                <Dot c={l.color} size={10} />{names[l.color]} {positionText(l.position, view.format)}
              </span>
              <span className="where">
                End {l.end}, rock {l.number}{l.type ? ` · ${l.type.toLowerCase()}` : ""}
              </span>
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}

function Cell({ cell }) {
  if (!cell) return <span className="empty">·</span>;
  return (
    <>
      <span className={cell.pct == null ? "none" : "p"}>{percent(cell.pct)}</span>
      <span className="n">{cell.graded === cell.thrown ? cell.graded : `${cell.graded}/${cell.thrown}`}</span>
    </>
  );
}

function Matrix({ table, c, names }) {
  return (
    <div>
      <h3 className="rpt-mhead"><Team c={c} names={names} size={14} /></h3>
      <div className="rpt-scroll">
        <table className="rpt-matrix">
          <thead>
            <tr>
              <td />
              {table.positions.map(p => <th key={p.id} scope="col">{p.label}</th>)}
              <th scope="col">All</th>
            </tr>
          </thead>
          <tbody>
            {table.rows.map((r, i) => (
              <tr key={i} className={r.kind === "group" ? "grp" : "type"}>
                <th scope="row">{r.label}</th>
                {r.cells.map((cell, k) => <td key={k}><Cell cell={cell} /></td>)}
                <td><Cell cell={r.all} /></td>
              </tr>
            ))}
            <tr className="all">
              <th scope="row">All</th>
              {table.all.cells.map((cell, k) => <td key={k}><Cell cell={cell} /></td>)}
              <td><Cell cell={table.all.all} /></td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function Report({ view, stats, think, series, actions }) {
  const names = teamNames(view.game);
  const table = useMemo(() => byEnd(view), [view]);
  const h2h = useMemo(() => headToHead(stats, view.format), [stats, view.format]);
  const cov = useMemo(() => coverage(view), [view]);
  const detail = useMemo(() => COLORS.map(c => detailRows(stats, c, view.format)),
                         [stats, view.format]);
  const pill = coverageText(cov);
  const notes = reportNotes(cov, think, view.ends.length);
  return (
    <div className="rpt">
      <div className="rpt-head">
        <div className="grow">
          <div className="rpt-meta">{reportMeta(view.doc, view.gi)}</div>
          <h1 className="rpt-title">
            <Dot c="red" size={18} />{names.red}<span className="v">v</span>
            <Dot c="yellow" size={18} />{names.yellow}
          </h1>
        </div>
        {pill ? (
          <button type="button" className="rpt-cover" disabled={!cov.first}
                  onClick={() => cov.first && actions.goToBarFromReport(cov.first)}>
            {pill}<span className="noprint" aria-hidden="true">→</span>
          </button>
        ) : null}
        <button type="button" className="noprint" onClick={() => print()}>Print</button>
      </div>

      <div className="rpt-top">
        <section className="card rpt-end">
          <h2>By end</h2>
          <p className="sub">Score, shooting percentage and thinking time, end by end</p>
          <ByEnd table={table} names={names} />
          <ByEndKey table={table} live={liveGame(view.doc, view.game)} />
          <hr className="rpt-rule" />
          <h3>About these numbers</h3>
          <div className="rpt-notes">{notes.map((t, i) => <p key={i}>{t}</p>)}</div>
        </section>
        <section className="card rpt-h2h"><HeadToHead h2h={h2h} names={names} /></section>
      </div>

      {think.measured
        ? <Thinking view={view} series={series} names={names} onSelect={actions.goToBarFromReport} />
        : null}

      <section className="card rpt-detail">
        <h2>Detail</h2>
        <p className="sub">
          Position by shot type. Each cell: percentage, then rocks graded (of thrown, where
          some weren&rsquo;t graded).
        </p>
        <div className="rpt-detail-body">
          {COLORS.map((c, i) => <Matrix key={c} table={detail[i]} c={c} names={names} />)}
        </div>
      </section>
    </div>
  );
}
