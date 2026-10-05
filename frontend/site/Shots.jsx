/* My shots: every rock you threw, in the games you said you played, by shot
 * type and turn.
 *
 * The list of games comes first (/api/me/plays); then each game, graded and
 * cut down to your rocks by the server, three at a time, so the groups fill
 * in as they arrive rather than after the slowest of forty. Each game is kept
 * by what it was fetched as -- colour, slot and when the play was saved -- so
 * changing one game's play fetches that game again and nothing else.
 *
 * Every number and word in the groups is core's (core/myshots.mjs); this
 * only lays them out. */
import { useEffect, useMemo, useRef, useState } from "react";
import { playText, playerRocks, positionChoices, shotGroups, summaryText } from "../core/myshots.mjs";
import { authedFetch, signIn } from "./auth.js";
import { day } from "./fmt.js";
import { useAuthUser, useResource } from "./useAuth.js";
import { Card, Header, Warn } from "./ui.jsx";
import { DeliveryOverlay, MissScatter } from "./ShotCharts.jsx";

const AT_ONCE = 3;
const playKey = p => `${p.source_id}|${p.color}|${p.slot}|${p.updated_at}`;
const playUrl = sid => `/api/me/plays/${encodeURIComponent(sid)}`;

/* Each game's rocks, by playKey: {rows, missing, fallback}, or {failed}. */
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
          if (!res.ok) throw new Error(String(res.status));
          const { doc, fallback } = await res.json();
          one = { ...playerRocks(p, doc), fallback };
        } catch {
          one = { failed: true };
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
  return (
    <section className="card shotgrp" id={group.id}>
      <h3>{group.label} <span className="muted">{summaryText(group.summary, group.turn).count}</span></h3>
      <Summary group={group} />
      <div className="shotgrp-charts">
        <MissScatter rows={group.rows} turn={group.turn} />
        <DeliveryOverlay rows={group.rows} turn={group.turn} />
      </div>
      <details open={group.rows.length <= 12}>
        <summary>Every rock ({group.rows.length})</summary>
        <RockTable rows={group.rows} />
      </details>
    </section>
  );
}

/* One game you said you played: which rocks, changeable here, and clearable. */
function PlayRow({ p, rocks, onChanged }) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState(null);
  const teams = { red: p.teams?.red || "Red", yellow: p.teams?.yellow || "Yellow" };
  const choices = ["red", "yellow"].flatMap(c => positionChoices(p.format).map(k => ({
    value: `${c}|${k.slot}`, text: `${teams[c]} · ${k.label} (${k.role})` })));

  async function send(method, body) {
    setBusy(true);
    setNote(null);
    const res = await authedFetch(playUrl(p.source_id), {
      method, headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined });
    setBusy(false);
    if (res.ok) onChanged();
    else setNote(res.status === 429 ? "That is a lot of changes; try again later."
                                    : "Could not change it. Try again.");
  }

  const change = e => {
    const [color, slot] = e.target.value.split("|");
    send("PUT", { color, slot: Number(slot) });
  };

  const state = p.status !== "ok" ? "This game is no longer available."
    : !rocks ? "Loading…"
    : rocks.failed ? "Could not load this game."
    : `${rocks.rows.length} rock${rocks.rows.length === 1 ? "" : "s"}`
      + (rocks.missing ? `, ${rocks.missing} never seen` : "");
  return (
    <tr>
      <td className="when">{day(p.played_at)}</td>
      <td className="game">
        {p.title || "Untitled game"}
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

  const live = (plays || []).filter(p => p.status === "ok");
  const loaded = live.filter(p => rocks[playKey(p)]).length;
  const groups = useMemo(
    () => shotGroups(live.flatMap(p => rocks[playKey(p)]?.rows ?? [])),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [plays, rocks]);

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
                  <p className="muted intro">Every rock you threw in {plays.length} game
                  {plays.length === 1 ? "" : "s"}, by shot and by turn. A clockwise rock
                  curls to the thrower's right.
                  {loaded < live.length ? ` Loaded ${loaded} of ${live.length}…` : ""}</p>
                  {groups.length ? groups.map(g => <Group key={g.id} group={g} />)
                   : loaded === live.length ? <Card>None of your rocks in these games were seen.</Card>
                   : null}
                  <h2>Games you played</h2>
                  <div className="card" id="plays">
                    <table>
                      <tbody>
                        <tr><th>Played</th><th>Game</th><th>Grading</th><th>Rocks</th><th /></tr>
                        {plays.map(p => (
                          <PlayRow key={p.source_id} p={p} rocks={rocks[playKey(p)]}
                                   onChanged={list.reload} />
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
