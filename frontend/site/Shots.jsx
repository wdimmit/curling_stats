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
 * still fetched, so putting one back is instant. Beside it, Starred narrows
 * the report to the rocks starred in its table (Play.starred), which a
 * teammate's report shows but cannot change.
 *
 * A teammate who shares theirs is read the same way at /shots?player=<id>,
 * from /api/players/<id>/plays, with nothing on it to change; the switch that
 * shares your own sits at the foot of yours.
 *
 * Every number and word in the groups is core's (core/myshots.mjs); this
 * only lays them out. */
import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import {
  clipText, pickedText, pickerRow, playText, playerRocks, positionChoices, rockClip, sharingText,
  shotGroups, starredRows, starredText, summaryText, tableColumns,
} from "../core/myshots.mjs";
import { playUrl, playsUrl, reportUrl, starUrl } from "../runtime/plays.mjs";
import { authedFetch, signIn } from "./auth.js";
import { signInTrouble } from "./signinState.mjs";
import { day } from "./fmt.js";
import { useAuthUser, useResource } from "./useAuth.js";
import { Card, Header, Warn } from "./ui.jsx";
import { ClipPlayer } from "./clip.jsx";
import { DeliveryOverlay, MissScatter } from "./ShotCharts.jsx";

const AT_ONCE = 3;
// While a game's clips are on their way, ask after them this often.
const CLIP_POLL_MS = 15000;
const playKey = p => `${p.source_id}|${p.color}|${p.slot}|${p.updated_at}`;

/* The games left out of the report, kept across visits in this browser. The
 * games left OUT, not the ones in: a game you mark later is in the report
 * until you take it out. Storage can be blocked, and then every game is in.
 * A teammate's report keeps its own, so neither filter moves the other. */
const OFF = "curlchart:shots-off";
const offKey = player => (player ? `${OFF}:${player}` : OFF);

function loadOff(key) {
  try {
    const ids = JSON.parse(localStorage.getItem(key) || "[]");
    return new Set(Array.isArray(ids) ? ids.filter(id => typeof id === "string") : []);
  } catch {
    return new Set();
  }
}

function saveOff(key, off) {
  try {
    if (off.size) localStorage.setItem(key, JSON.stringify([...off]));
    else localStorage.removeItem(key);
  } catch { /* the filter just forgets */ }
}

/* Whether the report shows only starred rocks, kept the same way. */
const STARRED = "curlchart:shots-starred";
const starredKey = player => (player ? `${STARRED}:${player}` : STARRED);

function loadStarred(key) {
  try { return localStorage.getItem(key) === "1"; } catch { return false; }
}

function saveStarred(key, on) {
  try {
    if (on) localStorage.setItem(key, "1");
    else localStorage.removeItem(key);
  } catch { /* the filter just forgets */ }
}

/* Each game's starred rocks: a Map of source id to the Set of their names. */
const starsOf = plays => new Map((plays || []).map(p => [p.source_id, new Set(p.starred || [])]));

function withStar(stars, sourceId, rock, on) {
  const next = new Map(stars);
  const set = new Set(next.get(sourceId));
  if (on) set.add(rock); else set.delete(rock);
  next.set(sourceId, set);
  return next;
}

/* Which games the report draws on: a button saying how many, opening a list
 * to tick. A dropdown on a desktop, a list in the page on a phone, where Done
 * closes it (site.css). */
function GamePicker({ plays, rocks, off, setOff, who, children }) {
  const [open, setOpen] = useState(false);
  const box = useRef(null);
  const t = pickedText(plays, off, undefined, who);

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
              <p className="muted">Remembered in this browser. Games {who ? "they" : "you"} mark later are included.</p>
            </div>
          </div>
        )}
      </div>
      {t.names && <span className="muted picker-names">{t.names}</span>}
      {!t.all && <button type="button" className="linky" onClick={() => setOff(new Set())}>Show all</button>}
      {children}
    </div>
  );
}

/* Each game's rocks, by playKey: {rows, missing, fallback}, or {failed, status}. */
function useRocks(plays, player) {
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
          const res = await authedFetch(`${reportUrl(p.source_id, player)}/doc`);
          if (res.ok) {
            const { doc, fallback, clips } = await res.json();
            one = { ...playerRocks(p, doc), fallback, clips };
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
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plays]);
  return [got, setGot];
}

/* While any game's clips are on their way, ask after just those games every
 * CLIP_POLL_MS -- /clips, no document -- and stop once none are. */
function useClipPoll(plays, rocks, setRocks, player) {
  const waiting = (plays || []).filter(p => rocks[playKey(p)]?.clips?.pending);
  const keys = waiting.map(playKey).join(",");
  useEffect(() => {
    if (!waiting.length) return undefined;
    const id = setInterval(async () => {
      for (const p of waiting) {
        try {
          const res = await authedFetch(`${reportUrl(p.source_id, player)}/clips`);
          if (!res.ok) continue;
          const { clips } = await res.json();
          setRocks(g => (g[playKey(p)] ? { ...g, [playKey(p)]: { ...g[playKey(p)], clips } } : g));
        } catch { /* asked again next time */ }
      }
    }, CLIP_POLL_MS);
    return () => clearInterval(id);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [keys]);
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

/* A rock's star: a button on your own report, just the star on a teammate's. */
function Star({ row, stars, onStar }) {
  if (!row.rock) return null;
  const on = Boolean(stars.get(row.sourceId)?.has(row.rock));
  if (!onStar) return on ? <span className="star on" title="Starred">★</span> : null;
  return (
    <button type="button" className={`star${on ? " on" : ""}`} aria-pressed={on}
            aria-label="Star this rock" title={on ? "Starred" : "Star this rock"}
            onClick={() => onStar(row, !on)}>{on ? "★" : "☆"}</button>
  );
}

function RockTable({ rows, clips, open, setOpen, player, stars, onStar }) {
  const cols = tableColumns(rows);
  return (
    <table className="rocks">
      <tbody>
        <tr><th className="star" /><th>Game</th><th>End</th><th>Rock</th><th>Shot</th>
            {cols.map(c => <th key={c.key}>{c.label}</th>)}<th /></tr>
        {rows.map(r => {
          const clip = rockClip(r, clips?.[r.sourceId]);
          const shown = clip && open === r.key;
          const figs = new Map(r.figures.map(f => [f.key, f]));
          return (
            <Fragment key={r.key}>
              <tr>
                <td className="star"><Star row={r} stars={stars} onStar={onStar} /></td>
                <td className="game">{r.game}</td>
                <td data-label="End">{r.end}</td>
                <td data-label="Rock">{r.number}</td>
                <td data-label="Shot">{r.typeName}</td>
                {cols.map(({ key }) => {
                  const f = figs.get(key);
                  // A figure this rock's game was charted too early to have.
                  if (!f) return <td key={key} className="nofig" />;
                  return (
                    <td key={key} data-label={f.label} title={f.note || undefined}
                        className={f.dim ? "dim" : undefined}>{f.value}</td>
                  );
                })}
                <td className="actions">
                  {clip && (
                    <button className="primary" aria-expanded={shown}
                            onClick={() => setOpen(shown ? null : r.key)}>▶ Clip</button>
                  )}
                  {r.href && <a className="btn watch" href={r.href}>Watch ↗</a>}
                </td>
              </tr>
              {shown && (
                <tr className="clip-row">
                  <td colSpan={cols.length + 6}>
                    <ClipPlayer src={`${reportUrl(r.sourceId, player)}/clips/${encodeURIComponent(r.rock)}`}
                                overlay={clip.overlay} burned={clip.burned} />
                  </td>
                </tr>
              )}
            </Fragment>
          );
        })}
      </tbody>
    </table>
  );
}

function Group({ group, clips, openClip, setOpenClip, player, stars, onStar }) {
  // Open to begin with when short, then the reader's: a group that grows
  // past twelve as games arrive must not snap shut under them.
  const [open, setOpen] = useState(group.rows.length <= 12);
  return (
    <section className="card shotgrp" id={group.id}>
      <h3>{group.label} <span className="muted">{summaryText(group.summary, group.turn).count}</span></h3>
      <Summary group={group} />
      <div className="shotgrp-charts">
        <MissScatter rows={group.drawn} turn={group.turn} />
        <DeliveryOverlay rows={group.drawn} turn={group.turn} />
      </div>
      <details open={open} onToggle={e => setOpen(e.currentTarget.open)}>
        <summary>Every rock ({group.rows.length})</summary>
        <RockTable rows={group.rows} clips={clips} open={openClip} setOpen={setOpenClip}
                   player={player} stars={stars} onStar={onStar} />
      </details>
    </section>
  );
}

/* One game you said you played: which rocks, changeable here, and clearable.
 * A teammate's only says which, and links to watch it. */
function PlayRow({ p, rocks, left, onChanged, readOnly }) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState(null);
  const teams = { red: p.teams?.red || "Red", yellow: p.teams?.yellow || "Yellow" };
  const threeOn = c => (p.short_handed || []).includes(c);
  const choices = ["red", "yellow"].flatMap(c => positionChoices(p.format, threeOn(c)).map(k => ({
    value: `${c}|${k.slot}`, text: `${teams[c]} · ${k.label} (${k.role})` })));
  const current = `${p.color}|${p.slot}`;

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
  const clips = rocks && !rocks.failed ? clipText(rocks.rows, rocks.clips) : null;
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
      <td data-label="Rocks">{state}{clips && <div className="muted">{clips}</div>}</td>
      <td className="actions">
        {!readOnly && (
          <>
            <select aria-label="Which rocks were yours" disabled={busy}
                    value={current} onChange={change}>
              {!choices.some(c => c.value === current) &&
                <option value={current} disabled>Pick your rocks again</option>}
              {choices.map(c => <option key={c.value} value={c.value}>{c.text}</option>)}
            </select>
            <button disabled={busy} onClick={() => send("DELETE")}>Clear</button>
          </>
        )}
        {p.view_path && <a className="btn" href={p.view_path}>Watch</a>}
      </td>
    </tr>
  );
}

/* Whose shots: yours, then each teammate sharing theirs. Plain links, so a
 * teammate's report is a page of its own to send round the team. */
function Whose({ players, theirs }) {
  return (
    <nav className="whose" aria-label="Whose shots">
      <a href="/shots" aria-current={theirs ? undefined : "page"}>Me</a>
      {players.map(p => (
        <a key={p.id} href={`/shots?player=${encodeURIComponent(p.id)}`} title={p.teams.join(", ")}
           aria-current={theirs === p.id ? "page" : undefined}>{p.name}</a>
      ))}
    </nav>
  );
}

/* Sharing your report with everyone on a team with you, or not. */
function ShareSwitch({ sharing, onChanged }) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState(null);
  const t = sharingText(sharing);

  async function flip(e) {
    const share = e.target.checked;
    setBusy(true);
    setNote(null);
    let res = null;
    try {
      res = await authedFetch("/api/me/shots-sharing", {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ share }) });
    } catch { /* offline: res stays null */ }
    if (res?.ok) onChanged(await res.json());
    else setNote(res?.status === 429 ? "That is a lot of changes; try again later."
                                     : "Could not change it. Try again.");
    setBusy(false);
  }

  return (
    <div className="card" id="share">
      <label className="share-row">
        <input type="checkbox" checked={sharing.share} disabled={busy} onChange={flip} />
        <span><b>{t.label}</b><span className="muted">{t.note}</span></span>
      </label>
      {note && <Warn>{note}</Warn>}
    </div>
  );
}

export function Shots() {
  const { user, ready, accounts } = useAuthUser();
  const [asked] = useState(() => new URL(location.href).searchParams.get("player"));
  // Your own id in the link is just your own report.
  const theirs = asked && user && asked !== user.uid ? asked : null;
  const list = useResource(playsUrl(theirs), { skip: !ready || !user, deps: [user?.uid] });
  const sharing = useResource("/api/me/shots-sharing", { skip: !ready || !user, deps: [user?.uid] });
  const players = sharing.data?.players ?? [];
  const owner = theirs ? (players.find(p => p.id === theirs)?.name || "Your teammate") : null;
  // Not shared, or not a teammate: the server will not say which.
  const denied = Boolean(theirs) && list.error?.status === 404;
  const plays = list.data?.plays ?? null;
  const [rocks, setRocks] = useRocks(plays, theirs);
  useClipPoll(plays, rocks, setRocks, theirs);
  // The one clip that is open, by row key: another rock's Clip closes it, in any group.
  const [openClip, setOpenClip] = useState(null);
  const [err, setErr] = useState("");
  const [off, setOffState] = useState(() => loadOff(offKey(asked)));
  const [onlyStarred, setOnlyStarredState] = useState(() => loadStarred(starredKey(asked)));
  const setOnlyStarred = on => { setOnlyStarredState(on); saveStarred(starredKey(theirs), on); };
  // The server's word on each game's stars, until a star here changes it.
  const [stars, setStars] = useState(new Map());
  useEffect(() => { setStars(starsOf(plays)); }, [plays]);
  const [starNote, setStarNote] = useState(null);

  // Shown starred at once, and put back if the server says no.
  async function star(row, on) {
    setStars(m => withStar(m, row.sourceId, row.rock, on));
    setStarNote(null);
    let res = null;
    try {
      res = await authedFetch(starUrl(row.sourceId), {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rock: row.rock, starred: on }) });
    } catch { /* offline: res stays null */ }
    if (res?.ok) return;
    setStars(m => withStar(m, row.sourceId, row.rock, !on));
    setStarNote(res?.status === 429 ? "That is a lot of changes; try again later."
                                    : "Could not star it. Try again.");
  }
  // Only games still listed are kept: a cleared game's id would linger for ever.
  const setOff = next => {
    const listed = new Set((plays || []).map(p => p.source_id));
    const kept = new Set([...next].filter(id => listed.has(id)));
    setOffState(kept);
    saveOff(offKey(theirs), kept);
  };

  const live = (plays || []).filter(p => p.status === "ok");
  // Each game's clip state, by game, for the rows the groups mix together.
  const clipsBy = Object.fromEntries(live.map(p => [p.source_id, rocks[playKey(p)]?.clips]));
  const loaded = live.filter(p => rocks[playKey(p)]).length;
  const pickedRows = useMemo(
    () => live.filter(p => !off.has(p.source_id)).flatMap(p => rocks[playKey(p)]?.rows ?? []),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [plays, rocks, off]);
  const starredPicked = useMemo(() => starredRows(pickedRows, stars), [pickedRows, stars]);
  const groups = useMemo(() => shotGroups(onlyStarred ? starredPicked : pickedRows),
                         [onlyStarred, starredPicked, pickedRows]);
  const picked = pickedText(plays || [], off, undefined, owner, onlyStarred);
  const starWords = starredText(starredPicked.length, owner);

  return (
    <>
      <Header links={[["/", "All games"], ["/mine", "My games"], ["/thinking", "Thinking time"], ["/ice", "Ice report"],
                       ["/shots", "My shots", true]]} />
      <main id="shots">
        {ready && !user && (
          <Card>
            <h2 style={{ marginTop: 0 }}>Your shots, across games</h2>
            <p className="muted">Sign in, then open a game you played and say which rocks
            were yours. This page gathers every one of them, by shot and by turn.</p>
            {accounts && (
              <button id="signin" className="primary"
                      onClick={() => signIn().catch(e => setErr(signInTrouble(e)))}>
                Sign in
              </button>
            )}
            {(!accounts || err) && (
              <p className="muted">{err || "Accounts are not switched on for this site."}</p>
            )}
          </Card>
        )}

        {ready && user && (
          <>
            {(theirs || players.length > 0) && <Whose players={players} theirs={theirs} />}
            <h2>{!theirs ? "My shots" : sharing.data && !denied ? `${owner}'s shots` : "Shots"}</h2>
            {list.loading && !plays ? <p className="muted">Loading…</p>
             : denied ? (
                <Card>
                  <p style={{ marginTop: 0 }}>These shots are not shared with you. They may
                  have stopped sharing them, or you are no longer on a team together.</p>
                  <a className="btn" href="/shots">My shots</a>
                </Card>
              )
             : list.error ? <Warn>Could not load {theirs ? "these" : "your"} games.</Warn>
             : !plays?.length && theirs ? <Card>{owner} has not marked any games yet.</Card>
             : !plays?.length ? (
                <Card>
                  <p style={{ marginTop: 0 }}>No games yet. Open a game you played, press
                  <b> ⋯ </b>, then <b>I played…</b>, and pick your team and the rocks you threw.</p>
                  <a className="btn primary" href="/">Find a game</a>
                </Card>
              ) : (
                <>
                  <GamePicker plays={plays} rocks={rocks} off={off} setOff={setOff} who={owner}>
                    {(onlyStarred || starredPicked.length > 0) && (
                      <button type="button" className="starchip" aria-pressed={onlyStarred}
                              onClick={() => setOnlyStarred(!onlyStarred)}>{starWords.label}</button>
                    )}
                  </GamePicker>
                  {starNote && <Warn>{starNote}</Warn>}
                  <p className="muted intro">{picked.intro}
                  {loaded < live.length ? ` Loaded ${loaded} of ${live.length}…` : ""}</p>
                  {/* Keyed by the filter too: a group shut at forty rocks opens again at three. */}
                  {groups.length ? groups.map(g => (
                    <Group key={`${g.id}${onlyStarred ? ".starred" : ""}`} group={g} clips={clipsBy}
                           openClip={openClip} setOpenClip={setOpenClip} player={theirs}
                           stars={stars} onStar={theirs ? null : star} />))
                   : picked.on && loaded === live.length
                     ? <Card>{onlyStarred ? starWords.empty
                              : `None of ${theirs ? `${owner}'s` : "your"} rocks in these games were seen.`}</Card>
                   : null}
                  <h2>Games {theirs ? `${owner} played` : "you played"}</h2>
                  <div className="card" id="plays">
                    <table>
                      <tbody>
                        <tr><th>Played</th><th>Game</th><th>Grading</th><th>Rocks</th><th /></tr>
                        {plays.map(p => (
                          <PlayRow key={p.source_id} p={p} rocks={rocks[playKey(p)]} readOnly={Boolean(theirs)}
                                   left={off.has(p.source_id)} onChanged={list.reload} />
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
            {!theirs && sharing.data?.teams.length > 0 && (
              <ShareSwitch sharing={sharing.data} onChanged={sharing.setData} />
            )}
          </>
        )}
      </main>
      <footer>{denied ? null
               : theirs ? `${owner} shares this page with their teams. Only they can change it.`
               : sharingText(sharing.data).footer}</footer>
    </>
  );
}
