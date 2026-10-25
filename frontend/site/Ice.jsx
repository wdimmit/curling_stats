/* The ice maker's report: how much one league draw's ice curled, against
 * another draw's.
 *
 * The API lists each league's draws (service/ice_report.py); the curl is
 * read here, from each game's review timeline, by frontend/core/ice.mjs --
 * the viewer's own curl, which exists nowhere else. Both picks are in the URL
 * hash, so a comparison can be sent to the ice crew.
 *
 * With accounts on it is for people signed in, like Thinking time. */
import { useEffect, useMemo, useRef, useState } from "react";
import { signIn } from "./auth.js";
import { signInTrouble } from "./signinState.mjs";
import { Card, Header } from "./ui.jsx";
import { useAuthUser, useResource } from "./useAuth.js";
import { MIN_END_ROCKS, iceRocks, iceTables, speedReport, speedRocks } from "../core/ice.mjs";
import { feetInches } from "../core/line.mjs";

const keyOf = lg => lg.format === "fours" ? lg.league : `${lg.league} · ${lg.format}`;
const IN = 100 / 2.54;
const READINGS = [
  { key: "curl", name: "Curl figure", field: "curl_m",
    about: "sideways at rest from the thrown line, as on every chart" },
  { key: "tail", name: "Last 10 m", field: "tail_m",
    about: "how far it bent over its last 10 m, which doesn't depend on weight" },
];

function readHash() {
  try { return new URLSearchParams(location.hash.slice(1)); } catch { return new URLSearchParams(); }
}

/* "Tue Oct 13 · 7:00 pm": a draw's games start a few minutes either side of
 * the half hour it was booked for. */
export function drawName(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return "—";
  d.setMinutes(Math.round(d.getMinutes() / 30) * 30, 0, 0);
  const day = d.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
  const time = d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" }).toLowerCase();
  return `${day} · ${time}`;
}

const pick = (leagues, key, start) => {
  const lg = leagues.find(l => keyOf(l) === key);
  const draw = lg?.draws.find(d => d.start === start);
  return lg && draw ? { lg, draw } : null;
};

/* The picks in the hash, or B the newest draw and A the one before it in the
 * same league -- so B - A reads later against earlier. */
function picksFrom(leagues, hash) {
  const a = pick(leagues, hash.get("al"), hash.get("a"));
  const b = pick(leagues, hash.get("bl"), hash.get("b"));
  const lg = leagues[0];
  return {
    a: a || (lg ? { lg, draw: lg.draws[1] || lg.draws[0] } : null),
    b: b || (lg && lg.draws[1] ? { lg, draw: lg.draws[0] } : null),
  };
}

// Each game's rows, kept for the visit: a draw picked again, or the other
// side's same game, is not fetched twice. null = not processed yet.
const rocksCache = new Map();
function rocksOf(sid) {
  if (!rocksCache.has(sid)) {
    rocksCache.set(sid, fetch(`/g/${encodeURIComponent(sid)}/timeline.json`)
      .then(r => (r.ok ? r.json() : null))
      .then(doc => (doc ? { curl: iceRocks(doc, sid), speed: speedRocks(doc, sid) } : null))
      .catch(() => { rocksCache.delete(sid); return null; }));
  }
  return rocksCache.get(sid);
}

/* Every game of `draw`, four at a time: { rows, speed, read, missing, of }. */
function useDrawRocks(draw) {
  const [state, setState] = useState(null);
  useEffect(() => {
    if (!draw) { setState(null); return; }
    let live = true;
    const ids = draw.games.map(g => g.source_id);
    const got = { rows: [], speed: [], read: 0, missing: 0, of: ids.length };
    setState({ ...got, done: false });
    let next = 0;
    const worker = async () => {
      while (live && next < ids.length) {
        const rows = await rocksOf(ids[next++]);
        if (rows) {
          got.rows = got.rows.concat(rows.curl);
          got.speed = got.speed.concat(rows.speed);
          got.read++;
        } else got.missing++;
        if (live) setState({ ...got, done: got.read + got.missing === got.of });
      }
    };
    for (let i = 0; i < 4; i++) worker();
    return () => { live = false; };
  }, [draw]);
  return state;
}

function DrawPicker({ label, leagues, value, onChange }) {
  const lg = value?.lg;
  return (
    <fieldset className="drawpick">
      <legend>{label}</legend>
      <select aria-label={`${label} league`} value={lg ? keyOf(lg) : ""}
              onChange={e => {
                const l = leagues.find(x => keyOf(x) === e.target.value);
                onChange(l ? { lg: l, draw: l.draws[0] } : null);
              }}>
        {label === "Draw B" ? <option value="">None</option> : null}
        {leagues.map(l => <option key={keyOf(l)} value={keyOf(l)}>{keyOf(l)}</option>)}
      </select>
      {lg ? (
        <select aria-label={`${label} draw`} value={value.draw.start}
                onChange={e => onChange({ lg, draw: lg.draws.find(d => d.start === e.target.value) })}>
          {lg.draws.map(d => (
            <option key={d.start} value={d.start}>
              {drawName(d.start)} · {d.games.length} {d.games.length === 1 ? "game" : "games"}
            </option>
          ))}
        </select>
      ) : null}
    </fieldset>
  );
}

const inches = m => {
  const n = Math.round(m * IN);
  return `${n < 0 ? "−" : n > 0 ? "+" : ""}${Math.abs(n)} in`;
};

function Cell({ s, field }) {
  if (!s) return <td className="num">—</td>;
  const v = s[field];
  return (
    <td className="num">
      <span className="val">{v == null ? "–" : feetInches(Math.abs(v))}</span>
      {v != null && v < 0 ? <span className="muted"> the other way</span> : null}
      <small>{s.rocks} {s.rocks === 1 ? "rock" : "rocks"} · {s.games} {s.games === 1 ? "game" : "games"}</small>
    </td>
  );
}

function Change({ d }) {
  if (d === undefined) return null;
  if (!d) return <td className="num muted">too few</td>;
  const clear = d.lo > 0 || d.hi < 0;
  return (
    <td className={`num${clear ? "" : " unclear"}`}>
      <span className="val">{inches(d.diff)}</span>
      <small>{inches(d.lo)} to {inches(d.hi)}{clear ? "" : " · no clear difference"}</small>
      {d.sheets.of && d.diff !== 0 ? (
        <small>{d.diff < 0 ? `less on ${d.sheets.less}` : `more on ${d.sheets.more}`} of {d.sheets.of}{" "}
        {d.sheets.of === 1 ? "sheet" : "sheets"}</small>
      ) : null}
    </td>
  );
}

function BinTable({ title, rows, reading, both }) {
  return (
    <>
      <h2>{title}</h2>
      <table className="rank ice">
        <tbody>
          <tr><th />
            <th className="num">Draw A</th>
            {both ? <><th className="num">Draw B</th><th className="num">B − A</th></> : null}
          </tr>
          {rows.map(r => (
            <tr key={r.name}>
              <th scope="row">{r.name}</th>
              <Cell s={r.a} field={reading.field} />
              {both ? <><Cell s={r.b} field={reading.field} /><Change d={r[reading.key]} /></> : null}
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

const secs = v => (v == null ? "–" : `${v.toFixed(1)} s`);
const signedSecs = v => {
  const n = Math.round(v * 10) / 10;
  return `${n < 0 ? "−" : n > 0 ? "+" : ""}${Math.abs(n).toFixed(1)} s`;
};
const rangeOf = r => (r?.lo == null ? "" : `${r.lo.toFixed(1)}–${r.hi.toFixed(1)} s`);

/* A draw's tee-line split, with the range and the rocks behind it. */
function SpeedCell({ s }) {
  if (!s) return null;
  return (
    <td className="num">
      <span className="val">{secs(s.split)}</span>
      {s.split != null ? <small>{rangeOf(s)}</small> : null}
      <small>{s.rocks} {s.rocks === 1 ? "rock" : "rocks"} · {s.games} {s.games === 1 ? "game" : "games"}</small>
    </td>
  );
}

/* The element's width in CSS pixels, kept as it changes. */
function useWidth(fallback) {
  const ref = useRef(null);
  const [w, setW] = useState(fallback);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(([e]) => setW(Math.round(e.contentRect.width) || fallback));
    ro.observe(el);
    return () => ro.disconnect();
  }, [fallback]);
  return [ref, w];
}

/* Each end's tee-line split, both draws on one axis, with each end's range as
 * a whisker. Hovering an end names its numbers above the plot. Drawn at the
 * figure's own width, so text and marks keep their size on a phone. */
function EndChart({ a, b }) {
  const [at, setAt] = useState(null);
  const [box, W] = useWidth(600);
  const series = [["A", a, "var(--series-a)"], ...(b ? [["B", b, "var(--series-b)"]] : [])];
  const pts = series.flatMap(([, s]) => s.ends.filter(e => e.split != null));
  if (!pts.length) return null;
  const ends = [...new Set(series.flatMap(([, s]) => s.ends.map(e => e.end)))].sort((x, y) => x - y);
  const H = 220, L = 40, R = 34, T = 12, B = 30, PAD = W < 420 ? 10 : 18;
  const lo = Math.floor(Math.min(...pts.map(e => e.lo ?? e.split)) * 2) / 2;
  const hi = Math.ceil(Math.max(...pts.map(e => e.hi ?? e.split)) * 2) / 2;
  const span = W - L - R - 2 * PAD;
  const x = e => L + PAD + (ends.length < 2 ? span / 2 : (ends.indexOf(e) * span) / (ends.length - 1));
  const y = v => T + ((hi - v) * (H - T - B)) / Math.max(hi - lo, 0.5);
  const ticks = [];
  for (let v = lo; v <= hi + 1e-9; v += 0.5) ticks.push(v);
  const step = ends.length < 2 ? W : span / (ends.length - 1);
  // Direct labels at each series' last end, pushed apart when they'd touch.
  const labels = series.map(([k, s]) => {
    const got = s.ends.filter(e => e.split != null);
    return got.length ? { k, end: got[got.length - 1].end, y: y(got[got.length - 1].split) + 4 } : null;
  });
  if (labels.length === 2 && labels[0] && labels[1] && Math.abs(labels[0].y - labels[1].y) < 14) {
    const [up, down] = labels[0].y <= labels[1].y ? labels : [labels[1], labels[0]];
    const mid = (up.y + down.y) / 2;
    up.y = mid - 7;
    down.y = mid + 7;
  }
  const shown = at == null ? null : series.map(([k, s]) => [k, s.ends.find(e => e.end === at)]);
  return (
    <figure className="endchart" ref={box}>
      <figcaption>
        {shown ? (
          <>End {at}{shown.map(([k, e]) => (
            <span key={k}> · {k} {e?.split == null ? "–" : `${secs(e.split)} (${rangeOf(e)}, ${e.rocks} rocks)`}</span>
          ))}</>
        ) : (
          <span className="legend">
            {series.map(([k, , c]) => (
              <span key={k}><i style={{ background: c }} />Draw {k}</span>
            ))}
          </span>
        )}
      </figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} role="img"
           aria-label="Each end's hog-to-hog split for a tee-line draw" onMouseLeave={() => setAt(null)}>
        {ticks.map(v => (
          <g key={v} className="tick">
            <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} />
            <text x={L - 6} y={y(v) + 4} textAnchor="end">{v.toFixed(1)}</text>
          </g>
        ))}
        {ends.map(e => (
          <text key={e} className="axis" x={x(e)} y={H - 10} textAnchor="middle">{e}</text>
        ))}
        {at != null ? <line className="cross" x1={x(at)} x2={x(at)} y1={T} y2={H - B} /> : null}
        {series.map(([k, s, c], si) => {
          const got = s.ends.filter(e => e.split != null);
          const dx = series.length > 1 ? (si ? 4 : -4) : 0;
          const label = labels[si];
          return (
            <g key={k} style={{ color: c }}>
              <polyline points={got.map(e => `${x(e.end) + dx},${y(e.split)}`).join(" ")} />
              {got.map(e => (
                <g key={e.end}>
                  {e.lo != null ? <line className="whisker" x1={x(e.end) + dx} x2={x(e.end) + dx}
                                        y1={y(e.lo)} y2={y(e.hi)} /> : null}
                  <circle cx={x(e.end) + dx} cy={y(e.split)} r={at === e.end ? 5.5 : 4} />
                </g>
              ))}
              {label ? <text className="direct" x={x(label.end) + 12} y={label.y}>{k}</text> : null}
            </g>
          );
        })}
        {ends.map(e => (
          <rect key={e} className="hit" x={x(e) - step / 2} y={0} width={step} height={H}
                onMouseEnter={() => setAt(e)} onClick={() => setAt(e)} />
        ))}
      </svg>
    </figure>
  );
}

/* The ice speed: the hog-to-hog time that stops a rock on the tee line, for
 * the whole draw and end by end. */
function Speed({ report }) {
  if (!report) return null;
  const { a, b, diff } = report;
  const ends = [...new Set([...a.ends, ...(b?.ends || [])].map(e => e.end))].sort((x, y) => x - y);
  const endOf = (s, n) => s?.ends.find(e => e.end === n);
  const clear = diff && (diff.lo > 0 || diff.hi < 0);
  return (
    <>
      <h2>Ice speed <small>hog-to-hog time for a draw to the tee line</small></h2>
      <table className="rank ice">
        <tbody>
          <tr><th />
            <th className="num">Draw A</th>
            {b ? <><th className="num">Draw B</th><th className="num">B − A</th></> : null}
          </tr>
          <tr>
            <th scope="row">Whole draw</th>
            <SpeedCell s={a} />
            {b ? (
              <>
                <SpeedCell s={b} />
                {!diff ? <td className="num muted">too few</td> : (
                  <td className={`num${clear ? "" : " unclear"}`}>
                    <span className="val">{signedSecs(diff.diff)}</span>
                    <small>{signedSecs(diff.lo)} to {signedSecs(diff.hi)}
                      {clear ? (diff.diff > 0 ? " · B keener" : " · B slower") : " · no clear difference"}</small>
                  </td>
                )}
              </>
            ) : null}
          </tr>
        </tbody>
      </table>
      <EndChart a={a} b={b} />
      <table className="rank ice ends">
        <tbody>
          <tr><th>End</th><th className="num">Draw A</th>{b ? <th className="num">Draw B</th> : null}</tr>
          {ends.map(n => (
            <tr key={n}>
              <th scope="row">{n}</th>
              {[a, ...(b ? [b] : [])].map((s, i) => {
                const e = endOf(s, n);
                return (
                  <td key={i} className="num">
                    {!e ? "–" : e.split == null ? <span className="muted">too few</span> : (
                      <>
                        <span className="val">{secs(e.split)}</span>
                        <small>{rangeOf(e)} · {e.rocks} rocks</small>
                      </>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted note">Read from every rock, on all the draw's sheets, that came to rest
      without touching a stone between the far hog line and the back of the house. A rock's split is
      about half a second longer for each metre shorter it stopped; a curve through each end's rocks,
      read at the tee line, gives that end's figure. A longer time means keener ice. Sweeping isn't
      seen, so this is the ice as it was played, sweepers included. An end needs {MIN_END_ROCKS} rocks.</p>
    </>
  );
}

export function Ice() {
  const { user, ready, accounts } = useAuthUser();
  const report = useResource("/api/reports/ice",
                             { skip: !ready || (accounts && !user), deps: [user?.uid] });
  const signedOut = ready && accounts && !user;
  const [err, setErr] = useState("");
  const [hash, setHash] = useState(readHash);
  useEffect(() => {
    const on = () => setHash(readHash());
    addEventListener("hashchange", on);
    return () => removeEventListener("hashchange", on);
  }, []);
  const [readingKey, setReadingKey] = useState("curl");
  const reading = READINGS.find(r => r.key === readingKey);

  const leagues = useMemo(() => report.data?.leagues || [], [report.data]);
  const { a, b } = useMemo(() => picksFrom(leagues, hash), [leagues, hash]);
  const setPick = (side, v) => {
    const h = new URLSearchParams();
    for (const [s, p] of [["a", side === "a" ? v : a], ["b", side === "b" ? v : b]]) {
      if (p) { h.set(`${s}l`, keyOf(p.lg)); h.set(s, p.draw.start); }
    }
    location.hash = h.toString();
  };
  const ra = useDrawRocks(a?.draw), rb = useDrawRocks(b?.draw);
  const done = ra?.done && (!b || rb?.done);
  const tables = useMemo(() => (done ? iceTables(ra.rows, b ? rb.rows : null) : null),
                         [done, ra, rb, b]);
  const speed = useMemo(() => (done ? speedReport(ra.speed, b ? rb.speed : null) : null),
                        [done, ra, rb, b]);
  const reading_ = n => (n ? `${n.read + n.missing} of ${n.of}` : "");
  const missing = (ra?.missing || 0) + (b ? rb?.missing || 0 : 0);

  return (
    <>
      <Header links={[["/", "All games"], ["/thinking", "Thinking time"], ["/ice", "Ice report", true],
                      ["/submit", "Submit a link"]]} />
      <main>
        <p className="muted" style={{ marginTop: 0 }}>How much the ice curled in a league draw,
        against another draw. Only rocks that stopped in the house without touching another stone
        are counted, so their weight and depth are much alike. Each is binned by whether it curled
        away from the centre line or back toward it, and by its turns from the hog line to rest
        (the rate as thrown ÷ 2.5).</p>
        {signedOut ? (
          <Card>
            <p style={{ marginTop: 0 }}>The ice report is for signed-in members.</p>
            <button className="primary"
                    onClick={() => signIn().catch(e => setErr(signInTrouble(e)))}>
              Sign in</button>
            {err ? <p className="muted">{err}</p> : null}
          </Card>
        ) : !ready || report.loading ? <p className="muted">Loading…</p>
         : report.error ? <p className="warn">The report could not be loaded. Try again in a minute.</p>
         : !leagues.length ? <p className="muted">No league games yet.</p>
         : (
          <div id="ice" className="card">
            <div className="picks">
              <DrawPicker label="Draw A" leagues={leagues} value={a} onChange={v => setPick("a", v)} />
              <DrawPicker label="Draw B" leagues={leagues} value={b} onChange={v => setPick("b", v)} />
            </div>
            <div className="tabs" role="tablist">
              {READINGS.map(r => (
                <button key={r.key} role="tab" aria-selected={r === reading}
                        className={`tab${r === reading ? " on" : ""}`}
                        onClick={() => setReadingKey(r.key)}>{r.name}</button>
              ))}
            </div>
            <p className="muted note">{reading.name}: the median rock, {reading.about}.</p>
            {!tables ? (
              <p className="muted">Reading games… {reading_(ra)}{b ? ` · ${reading_(rb)}` : ""}</p>
            ) : (
              <>
                <BinTable title="By side of the sheet" rows={tables.sides} reading={reading} both={!!b} />
                <BinTable title="By turns, hog line to rest" rows={tables.turns} reading={reading} both={!!b} />
                {missing ? (
                  <p className="muted note">{missing} {missing === 1 ? "game isn't" : "games aren't"} processed
                  yet and {missing === 1 ? "is" : "are"} left out.</p>
                ) : null}
                <p className="muted note">B − A comes with the range it would likely fall in if the same
                draws were played again, found by drawing each draw's games again at random. A range
                that crosses zero means no clear difference: one draw is only a handful of games, enough
                to show a change of about 10% or more. Different teams threw these rocks, and sweeping
                isn't seen by the cameras.</p>
                <Speed report={speed} />
              </>
            )}
          </div>
        )}
      </main>
    </>
  );
}
