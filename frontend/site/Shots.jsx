/* My shots: every rock you threw, in the games you said you played, by shot
 * type and turn.
 *
 * The list of games comes first (/api/me/plays); then each game, graded and
 * cut down to your rocks by the server, three at a time, so the groups fill
 * in as they arrive rather than after the slowest of forty. Each game is kept
 * by what it was fetched as -- colour, slot and when the play was saved -- so
 * changing one game's play fetches that game again and nothing else.
 *
 * A filter at the top picks which games the report draws on. Every game is
 * still fetched, so putting one back is instant.
 *
 * Every number and word in the groups is core's (core/myshots.mjs); this
 * only lays them out. */
import { useEffect, useMemo, useRef, useState } from "react";
import {
  pickedText, pickerRow, playText, playerRocks, positionChoices, shotGroups, summaryText,
} from "../core/myshots.mjs";
import { playUrl } from "../runtime/plays.mjs";
import { authedFetch, signIn } from "./auth.js";
import { day } from "./fmt.js";
import { useAuthUser, useResource } from "./useAuth.js";
import { Card, Header, Warn } from "./ui.jsx";
import { DeliveryOverlay, MissScatter } from "./ShotCharts.jsx";

const AT_ONCE = 3;
const playKey = p => `${p.source_id}|${p.color}|${p.slot}|${p.updated_at}`;

/* The games left out of the report, kept across visits in this browser. The
 * games left OUT, not the ones in: a game you mark later is in the report
 * until you take it out. Storage can be blocked, and then every game is in. */
const OFF = "curlchart:shots-off";

function loadOff() {
  try {
    const ids = JSON.parse(localStorage.getItem(OFF) || "[]");
    return new Set(Array.isArray(ids) ? ids.filter(id => typeof id === "string") : []);
  } catch {
    return new Set();
  }
}

function saveOff(off) {
  try {
    if (off.size) localStorage.setItem(OFF, JSON.stringify([...off]));
    else localStorage.removeItem(OFF);
  } catch { /* the filter just forgets */ }
}

/* Which games the report draws on: a button saying how many, opening a list
 * to tick. A dropdown on a desktop, a list in the page on a phone, where Done
 * closes it (site.css). */
function GamePicker({ plays, rocks, off, setOff }) {
  const [open, setOpen] = useState(false);
  const box = useRef(null);
  const t = pickedText(plays, off);

  // Shut by Escape or by a press anywhere else, as a menu is.
  useEffect(() => {
    if (!open) return undefined;
    const away = e => { if (box.current && !box.current.contains(e.target)) setOpen(false); };
    const esc = e => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("pointerdown", away);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("pointerdown", away);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);

  const flip = id => {
    const next = new Set(off);
    if (next.has(id)) next.delete(id); else next.add(id);
    setOff(next);
  };

  return (
    <div className="picker" ref={box}>
      <span className="picker-label">Games</span>
      <div className="picker-anchor">
        <button type="button" className={`picker-btn${open ? " open" : ""}`} aria-expanded={open}
                aria-controls="pickerPanel" onClick={() => setOpen(o => !o)}>
          {t.label}
          <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
            <path d={open ? "M2 8l4-4 4 4" : "M2 4l4 4 4-4"} fill="none" stroke="currentColor" strokeWidth="1.6" />
          </svg>
        </button>
        {open && (
          <div className="picker-panel" id="pickerPanel">
            <div className="picker-head">
              <span>Show rocks from</span>
              <button type="button" onClick={() => setOff(new Set())}>All</button>
              <button type="button" onClick={() => setOff(new Set(plays.map(p => p.source_id)))}>None</button>
            </div>
            {plays.map(p => {
              const r = pickerRow(p, rocks[playKey(p)]);
              return (
                <label key={p.source_id} className="picker-row">
                  <input type="checkbox" checked={!off.has(p.source_id)} onChange={() => flip(p.source_id)} />
                  <span className="picker-when">{r.when}</span>
                  <span className="picker-game">{r.against}</span>
                  {r.position && <span className="pill">{r.position}</span>}
                  <span className="picker-rocks">{r.rocks}</span>
                </label>
              );
            })}
            <div className="picker-foot">
              <button type="button" className="picker-done primary" onClick={() => setOpen(false)}>Done</button>
              <p className="muted">Remembered in this browser. Games you mark later are included.</p>
            </div>
          </div>
        )}
      </div>
      {t.names && <span className="muted picker-names">{t.names}</span>}
      {!t.all && <button type="button" className="linky" onClick={() => setOff(new Set())}>Show all</button>}
    </div>
  );
}

/* Each game's rocks, by playKey: {rows, missing, fallback}, or {failed, status}. */
function useRocks(plays) {
  const [got, setGot] = useState({});
  const asked = useRef(new Set());
  useEffect(() => {
    if (!plays) return;
    const queue = plays.filter(p => p.status === "ok" && !asked.current.has(playKey(p)));
    queue.forEach(p => asked.current.add(playKey(p)));
    const worker = async () => {
      while (queue.length) {
        const p = queue.shift();
        let one;
        try {
          const res = await authedFetch(`${playUrl(p.source_id)}/doc`);
          if (res.ok) {
            const { doc, fallback } = await res.json();
            one = { ...playerRocks(p, doc), fallback };
          } else {
            one = { failed: true, status: res.status };
          }
        } catch {
          one = { failed: true, status: 0 };
        }
        setGot(g => ({ ...g, [playKey(p)]: one }));
      }
    };
    for (let i = 0; i < AT_ONCE; i++) worker();
  }, [plays]);
  return got;
}

function Summary({ group }) {
  const t = summaryText(group.summary, group.turn);
  return (
    <dl className="shotsum">
      <div><dt>Weight</dt><dd>{t.weight}</dd></div>
      <div><dt>At the broom</dt><dd>{t.broom}</dd></div>
      <div><dt>Typical</dt><dd>{t.typical}</dd></div>
      {t.notes.length ? <p className="muted">{t.notes.join(" · ")}</p> : null}
    </dl>
  );
}

function RockTable({ rows }) {
  const heads = rows[0].figures.map(f => f.label);
  return (
    <table className="rocks">
      <tbody>
        <tr><th>Game</th><th>End</th><th>Rock</th><th>Shot</th>
            {heads.map(h => <th key={h}>{h}</th>)}<th /></tr>
        {rows.map(r => (
          <tr key={r.key}>
            <td className="game">{r.game}</td>
            <td data-label="End">{r.end}</td>
            <td data-label="Rock">{r.number}</td>
            <td data-label="Shot">{r.typeName}</td>
            {r.figures.map(f => (
              <td key={f.key} data-label={f.label} title={f.note || undefined}
                  className={f.dim ? "dim" : undefined}>{f.value}</td>
            ))}
            <td className="actions">{r.href && <a className="btn" href={r.href}>Watch</a>}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Group({ group }) {
  // Open to begin with when short, then the reader's: a group that grows
  // past twelve as games arrive must not snap shut under them.
  const [open, setOpen] = useState(group.rows.length <= 12);
  return (
    <section className="card shotgrp" id={group.id}>
      <h3>{group.label} <span className="muted">{summaryText(group.summary, group.turn).count}</span></h3>
      <Summary group={group} />
      <div className="shotgrp-charts">
        <MissScatter rows={group.rows} turn={group.turn} />
        <DeliveryOverlay rows={group.rows} turn={group.turn} />
      </div>
      <details open={open} onToggle={e => setOpen(e.currentTarget.open)}>
        <summary>Every rock ({group.rows.length})</summary>
        <RockTable rows={group.rows} />
      </details>
    </section>
  );
}

/* One game you said you played: which rocks, changeable here, and clearable. */
function PlayRow({ p, rocks, left, onChanged }) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState(null);
  const teams = { red: p.teams?.red || "Red", yellow: p.teams?.yellow || "Yellow" };
  const choices = ["red", "yellow"].flatMap(c => positionChoices(p.format).map(k => ({
    value: `${c}|${k.slot}`, text: `${teams[c]} · ${k.label} (${k.role})` })));

  async function send(method, body) {
    setBusy(true);
    setNote(null);
    let status = 0;
    try {
      const res = await authedFetch(playUrl(p.source_id), {
        method, headers: body ? { "Content-Type": "application/json" } : {},
        body: body ? JSON.stringify(body) : undefined });
      status = res.ok ? 200 : res.status;
    } catch { /* offline: status stays 0 */ }
    setBusy(false);
    if (status === 200) onChanged();
    else setNote(status === 429 ? "That is a lot of changes; try again later."
                                : "Could not change it. Try again.");
  }

  const change = e => {
    const [color, slot] = e.target.value.split("|");
    send("PUT", { color, slot: Number(slot) });
  };

  const state = p.status !== "ok" ? "This game is no longer available."
    : !rocks ? "Loading…"
    : rocks.failed ? (rocks.status === 409 ? "Being analysed again; try later."
                                           : "Could not load this game.")
    : `${rocks.rows.length} rock${rocks.rows.length === 1 ? "" : "s"}`
      + (rocks.missing ? `, ${rocks.missing} never seen` : "");
  return (
    <tr className={left ? "left" : undefined}>
      <td className="when">{day(p.played_at)}</td>
      <td className="game">
        {p.title || "Untitled game"}
        {left && <span className="pill">Not in the report</span>}
        <div className="muted">{playText(p)}</div>
        {rocks?.fallback && <Warn>{rocks.fallback}.</Warn>}
        {note && <Warn>{note}</Warn>}
      </td>
      <td data-label="Grading">{p.graded ? "The chart's" : "As detected"}</td>
      <td data-label="Rocks">{state}</td>
      <td className="actions">
        <select aria-label="Which rocks were yours" disabled={busy}
                value={`${p.color}|${p.slot}`} onChange={change}>
          {choices.map(c => <option key={c.value} value={c.value}>{c.text}</option>)}
        </select>
        <button disabled={busy} onClick={() => send("DELETE")}>Clear</button>
        {p.view_path && <a className="btn" href={p.view_path}>Watch</a>}
      </td>
    </tr>
  );
}

export function Shots() {
  const { user, ready, accounts } = useAuthUser();
  const list = useResource("/api/me/plays", { skip: !ready || !user, deps: [user?.uid] });
  const plays = list.data?.plays ?? null;
  const rocks = useRocks(plays);
  const [err, setErr] = useState("");
  const [off, setOffState] = useState(loadOff);
  // Only games still listed are kept: a cleared game's id would linger for ever.
  const setOff = next => {
    const listed = new Set((plays || []).map(p => p.source_id));
    const kept = new Set([...next].filter(id => listed.has(id)));
    setOffState(kept);
    saveOff(kept);
  };

  const live = (plays || []).filter(p => p.status === "ok");
  const loaded = live.filter(p => rocks[playKey(p)]).length;
  const groups = useMemo(
    () => shotGroups(live.filter(p => !off.has(p.source_id))
      .flatMap(p => rocks[playKey(p)]?.rows ?? [])),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [plays, rocks, off]);
  const picked = pickedText(plays || [], off);

  return (
    <>
      <Header links={[["/", "All games"], ["/mine", "My games"], ["/thinking", "Thinking time"],
                       ["/shots", "My shots", true]]} />
      <main id="shots">
        {ready && !user && (
          <Card>
            <h2 style={{ marginTop: 0 }}>Your shots, across games</h2>
            <p className="muted">Sign in, then open a game you played and say which rocks
            were yours. This page gathers every one of them, by shot and by turn.</p>
            {accounts && (
              <button id="signin" className="primary"
                      onClick={() => signIn().catch(e => setErr(e.code || e.message))}>
                Sign in with Google
              </button>
            )}
            {(!accounts || err) && (
              <p className="muted">{err || "Accounts are not switched on for this site."}</p>
            )}
          </Card>
        )}

        {ready && user && (
          <>
            <h2>My shots</h2>
            {list.loading && !plays ? <p className="muted">Loading…</p>
             : list.error ? <Warn>Could not load your games.</Warn>
             : !plays?.length ? (
                <Card>
                  <p style={{ marginTop: 0 }}>No games yet. Open a game you played, press
                  <b> ⋯ </b>, then <b>I played…</b>, and pick your team and the rocks you threw.</p>
                  <a className="btn primary" href="/">Find a game</a>
                </Card>
              ) : (
                <>
                  <GamePicker plays={plays} rocks={rocks} off={off} setOff={setOff} />
                  <p className="muted intro">{picked.intro}
                  {loaded < live.length ? ` Loaded ${loaded} of ${live.length}…` : ""}</p>
                  {groups.length ? groups.map(g => <Group key={g.id} group={g} />)
                   : picked.on && loaded === live.length
                     ? <Card>None of your rocks in these games were seen.</Card>
                   : null}
                  <h2>Games you played</h2>
                  <div className="card" id="plays">
                    <table>
                      <tbody>
                        <tr><th>Played</th><th>Game</th><th>Grading</th><th>Rocks</th><th /></tr>
                        {plays.map(p => (
                          <PlayRow key={p.source_id} p={p} rocks={rocks[playKey(p)]}
                                   left={off.has(p.source_id)} onChanged={list.reload} />
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
          </>
        )}
      </main>
      <footer>Only you can see which games you played and this page.</footer>
    </>
  );
}
