/* The ice maker's report: how much one league draw's ice curled, against
 * another draw's.
 *
 * The API lists each league's draws (service/ice_report.py); the curl is
 * read here, from each game's review timeline, by frontend/core/ice.mjs --
 * the viewer's own curl, which exists nowhere else. Both picks are in the URL
 * hash, so a comparison can be sent to the ice crew.
 *
 * With accounts on it is for people signed in, like Thinking time. */
import { useEffect, useMemo, useState } from "react";
import { signIn } from "./auth.js";
import { signInTrouble } from "./signinState.mjs";
import { Card, Header } from "./ui.jsx";
import { useAuthUser, useResource } from "./useAuth.js";
import { iceRocks, iceTables } from "../core/ice.mjs";
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
      .then(doc => (doc ? iceRocks(doc, sid) : null))
      .catch(() => { rocksCache.delete(sid); return null; }));
  }
  return rocksCache.get(sid);
}

/* Every game of `draw`, four at a time: { rows, read, missing, of }. */
function useDrawRocks(draw) {
  const [state, setState] = useState(null);
  useEffect(() => {
    if (!draw) { setState(null); return; }
    let live = true;
    const ids = draw.games.map(g => g.source_id);
    const got = { rows: [], read: 0, missing: 0, of: ids.length };
    setState({ ...got, done: false });
    let next = 0;
    const worker = async () => {
      while (live && next < ids.length) {
        const rows = await rocksOf(ids[next++]);
        if (rows) { got.rows = got.rows.concat(rows); got.read++; } else got.missing++;
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
              </>
            )}
          </div>
        )}
      </main>
    </>
  );
}
