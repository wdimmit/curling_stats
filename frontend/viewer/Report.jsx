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
import { useEffect, useMemo, useRef, useState } from "react";
import {
  byEnd, clockText, coverage, coverageText, detailRows, endList, headToHead, liveGame,
  longestThinks, positionText, reportMeta, reportNotes, scoreChoices, scoreError, teamNames,
} from "../core/index.mjs";
import { accountsOn, signInNow, whoIsSignedIn } from "../runtime/auth.mjs";
import { sendScore } from "../runtime/scores.mjs";
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

function ScoreCell({ end, c, entry }) {
  const n = end.score ? (end.score[c] || 0) : null;
  const shown = n == null ? "?" : n;
  const cls = `${n == null ? "none" : n ? "won" : "zero"}${end.entered ? " entered" : ""}`;
  if (entry?.can && (n == null || end.entered))
    return (
      <button type="button" className={`sc-q ${cls}`} onClick={() => entry.open(end)}
              aria-label={n == null ? `Add end ${end.number}’s score` : `Change end ${end.number}’s score`}>
        {shown}
      </button>
    );
  if (entry?.signIn && n == null)
    return (
      <button type="button" className="sc-q none" title="Sign in to add the score"
              aria-label="Sign in to add the score" onClick={entry.signIn}>?</button>
    );
  return <span className={cls}>{shown}</span>;
}

/* Whether the score rows show: always where the board can speak, and on a
 * withheld board once there is something entered or a way to enter it. */
const showsScores = (table, entry) => table.status === "ok"
  || (table.status === "withheld" && (!!entry?.can || table.ends.some(e => e.entered)));

function ByEnd({ table, names, entry }) {
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
          {!showsScores(table, entry) ? (
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
                  <ScoreCell end={e} c={c} entry={entry} />
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

function ByEndKey({ table, live, entry }) {
  const unread = table.ends.filter(e => !e.score).map(e => e.number);
  const ungraded = table.ends
    .filter(e => e.shooting.red == null && e.shooting.yellow == null).map(e => e.number);
  const ok = table.status === "ok";
  return (
    <div className="rpt-key">
      {ok ? <span><Hammer />had the hammer</span> : null}
      {table.ends.some(e => e.entered) ? <span><u className="entered">3</u> entered by hand</span> : null}
      {ok && unread.length && entry?.signIn ? <span>sign in to add missing scores</span> : null}
      {table.status === "withheld" && showsScores(table, entry)
        ? <span>the wall board&rsquo;s scores couldn&rsquo;t be matched to these ends</span> : null}
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

/* Who may enter a score: "in", "out", or null while unknown or where
 * accounts are off -- then there is no button at all, only the "?". */
function useWho(config) {
  const [who, setWho] = useState(null);
  useEffect(() => {
    if (!config?.hosted) return undefined;
    let live = true;
    (async () => {
      if (!(await accountsOn())) return;
      const w = await whoIsSignedIn();
      if (live && w !== undefined) setWho(w ? "in" : "out");
    })();
    return () => { live = false; };
  }, [config?.hosted]);
  return [who, setWho];
}

/* The picker for one end. A modal <dialog>, like the flag dialog: in the
 * top layer, above the phone shell, and never inside a hidden parent. */
function ScoreDialog({ at, names, fmt, busy, error, onPick, onClear, onClose }) {
  const ref = useRef(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (at && !d.open) d.showModal();
    if (!at && d.open) d.close();
  }, [at]);
  const choices = scoreChoices(fmt);
  return (
    <dialog ref={ref} id="scoreDialog" onClose={onClose} aria-labelledby="scoreTitle">
      {at ? (
        <>
          <h2 id="scoreTitle">End {at.number} score</h2>
          <p className="sc-why">
            For an end the wall board didn&rsquo;t read. Everyone who opens this game sees it,
            marked as entered by hand.
          </p>
          <button type="button" className="sc-blank" disabled={busy}
                  onClick={() => onPick(choices[0])}>Blank end</button>
          {["red", "yellow"].map(c => (
            <div key={c} className="sc-row">
              <Team c={c} names={names} />
              <div className="sc-nums">
                {choices.filter(x => x.color === c).map(x => (
                  <button key={x.n} type="button" disabled={busy} aria-label={`${names[c]} ${x.n}`}
                          className={at.score && at.score[c] === x.n ? "on" : undefined}
                          onClick={() => onPick(x)}>{x.n}</button>
                ))}
              </div>
            </div>
          ))}
          {error ? <p className="sc-error" role="alert">{error}</p> : null}
          <div className="sc-foot">
            {at.entered ? <button type="button" disabled={busy} onClick={onClear}>Clear</button> : null}
            <span className="grow" />
            <button type="button" disabled={busy} onClick={onClose}>Cancel</button>
          </div>
        </>
      ) : null}
    </dialog>
  );
}

export function Report({ view, stats, think, series, actions, config }) {
  const names = teamNames(view.game);
  const table = useMemo(() => byEnd(view), [view]);
  const h2h = useMemo(() => headToHead(stats, view.format), [stats, view.format]);
  const cov = useMemo(() => coverage(view), [view]);
  const detail = useMemo(() => COLORS.map(c => detailRows(stats, c, view.format)),
                         [stats, view.format]);
  const pill = coverageText(cov);
  const notes = reportNotes(cov, think, view.ends.length);
  const [who, setWho] = useWho(config);
  const [at, setAt] = useState(null);       // the end being entered, or null
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const canEnter = who === "in" && !!view.doc.chart?.source_id && table.status !== "predates";
  const close = () => { setAt(null); setError(null); };
  const save = async body => {
    setBusy(true);
    setError(null);
    const w = await whoIsSignedIn();
    if (!w) {
      setBusy(false);
      setError(scoreError(401));
      if (w === null) setWho("out");
      return;
    }
    const r = await sendScore(view.doc.chart.source_id, { end: at.number, ...body }, w.token);
    if (r.ok || r.status === 409) await actions.reloadDoc();
    setBusy(false);
    if (r.ok) close();
    else setError(scoreError(r.status));
  };
  const entry = canEnter
    ? { can: true, open: e => { setError(null); setAt(e); } }
    : who === "out" && view.doc.chart?.source_id && table.status !== "predates"
      ? { signIn: async () => { try { await signInNow(); setWho("in"); } catch { /* shut */ } } }
      : null;
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
          <ByEnd table={table} names={names} entry={entry} />
          <ByEndKey table={table} live={liveGame(view.doc, view.game)} entry={entry} />
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
      <ScoreDialog at={at} names={names} fmt={view.format} busy={busy} error={error}
                   onPick={x => save({ red: x.red, yellow: x.yellow })}
                   onClear={() => save({ clear: true })} onClose={close} />
    </div>
  );
}
