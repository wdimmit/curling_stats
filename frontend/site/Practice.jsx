/* The practice pages, told apart by path:
 *   /practice             every session of mine, on any sheet
 *   /practice/sheet/N     start a session on a sheet; see each throw a few
 *                         seconds after it stops
 *   /practice/session/ID  one session's throws, running or long over
 * The words for a throw are core/practice.mjs's; the API is
 * service/practice.py's. */
import { useCallback, useEffect, useState } from "react";
import { authedFetch, signIn } from "./auth.js";
import { useAuthUser, useResource } from "./useAuth.js";
import { Card, Header, Pill, Warn } from "./ui.jsx";
import { clipState, curlText, lengthText, lineText, restText, shown,
         summary, turnText, weightText } from "../core/practice.mjs";
import { DELIVERYBOX, deliveryGeometry } from "../core/index.mjs";
import { ClipPlayer } from "./clip.jsx";
import { DeliveryChart } from "../viewer/Delivery.jsx";

const POLL_MS = 3000;
const HAND_KEY = "practice.hand";
const ENDED = {
  stop: "you ended it", idle: "20 minutes without a throw", max: "the 3-hour limit",
  stream_ended: "the stream ended", failed: "the stream could not be watched",
};

// The club's sheets, for starting a session from the list of them.
const SHEETS = [1, 2, 3, 4, 5];

function route() {
  const p = location.pathname;
  const sheet = p.match(/^\/practice\/sheet\/(\d+)/);
  if (sheet) return { sheet: Number(sheet[1]) };
  const session = p.match(/^\/practice\/session\/([^/]+)/);
  if (session) return { session: decodeURIComponent(session[1]) };
  return {};
}

function dayText(iso) {
  return new Date(iso).toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
}

function timeText(iso) {
  return new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function savedHand() {
  try { return localStorage.getItem(HAND_KEY) === "left" ? "left" : "right"; }
  catch { return "right"; }
}

function saveHand(hand) {
  try { localStorage.setItem(HAND_KEY, hand); } catch { /* private window */ }
}

function ago(iso) {
  const s = Math.max(0, (Date.now() - Date.parse(iso)) / 1000);
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  return new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

/* A small house, from the thrower's end: where it stopped, and the broom. */
function House({ rest, broom }) {
  const rings = [[1.829, "#2e6fd8"], [1.219, "#fff"], [0.61, "#d83a2e"], [0.152, "#fff"]];
  return (
    <svg viewBox="-2.4 -2.4 4.8 4.8" width="120" height="120" aria-hidden="true"
         style={{ background: "var(--ice, #eef3f6)", borderRadius: 8 }}>
      {rings.map(([r, fill]) => <circle key={r} r={r} fill={fill} stroke="#9aa" strokeWidth="0.02" />)}
      <line x1="-2.4" x2="2.4" y1="0" y2="0" stroke="#9aa" strokeWidth="0.02" />
      {broom && <rect x={broom.x - 0.08} y={broom.y - 0.08} width="0.16" height="0.16"
                      fill="#222" />}
      {rest && rest.ring !== "out" &&
        <circle cx={rest.x} cy={rest.y} r="0.145" fill="#f2c200" stroke="#333" strokeWidth="0.03" />}
    </svg>
  );
}

/* The viewer's Delivery chart: the rock from the hack to past the hog line. */
function Delivery({ t, n }) {
  if (!(t.line?.delivery?.length >= 2)) return null;
  const g = deliveryGeometry({ line: t.line, target_broom: t.broom }, DELIVERYBOX);
  return g ? <DeliveryChart g={g} fluid label={`Throw ${n}: the delivery from the hack`} /> : null;
}

function Latest({ t, n, sid, live, newest }) {
  const clip = clipState(t, { live, newest });
  return (
    <Card>
      <p className="muted" style={{ margin: 0 }}>
        Throw {n}{curlText(t.curl) ? ` · ${curlText(t.curl)}` : ""} · {ago(t.released_at)}
      </p>
      {clip === "play" && (
        <ClipPlayer src={`/api/practice/sessions/${encodeURIComponent(sid)}/throws/`
                         + `${encodeURIComponent(t.id)}/clip`}
                    overlay={t.overlay} autoPlay={live && newest} />
      )}
      {clip === "wait" && <p className="muted">Clip on its way…</p>}
      <Delivery t={t} n={n} />
      {!t.arrived ? <p><strong>Released, didn't arrive</strong> (hogged?)</p> : (
        <div style={{ display: "flex", gap: 16, alignItems: "center" }}>
          <dl style={{ flex: 1, margin: 0 }}>
            <dt className="muted">Line</dt>
            <dd>{lineText(t.line) ?? "not measured"}</dd>
            <dt className="muted">Weight</dt>
            <dd>{weightText(t.split_s) ? `${weightText(t.split_s)} hog to hog` : "not timed"}</dd>
            <dt className="muted">Came to rest</dt>
            <dd>{restText(t.rest) ?? "not seen"}</dd>
          </dl>
          <House rest={t.rest} broom={t.broom} />
        </div>
      )}
    </Card>
  );
}

/* One session: its throws, kept up to date while it runs. ``onDone`` is the
 * sheet page's way to start another; without it, that is a link to the sheet.
 * ``heading`` says which sheet and when, for a page that has not. */
function Live({ id, onDone, heading }) {
  const [view, setView] = useState(null);
  const [err, setErr] = useState("");
  // The throw the card shows: the latest unless one was picked from the table.
  const [picked, setPicked] = useState(null);
  const load = useCallback(async () => {
    const res = await authedFetch(`/api/practice/sessions/${encodeURIComponent(id)}`,
                                  { cache: "no-cache" });
    if (res.ok) { setView(await res.json()); setErr(""); }
    else setErr(res.status === 404 ? "No such session." : `Could not load the session (${res.status}).`);
  }, [id]);

  // Polled while it runs; an ended session never changes again.
  const loaded = view != null;
  const running = view?.session.status === "active";
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (loaded && !running) return undefined;
    const t = setInterval(() => { if (!document.hidden) load(); }, POLL_MS);
    return () => clearInterval(t);
  }, [load, loaded, running]);

  if (!view) return <p className="muted">{err || "Loading…"}</p>;
  const { session, watch } = view;
  const throws = shown(view.throws);
  const mine = throws.filter(t => !t.hidden);
  const shownIndex = picked != null ? throws.findIndex(t => t.id === picked) : -1;
  const card = shownIndex >= 0 ? throws[shownIndex] : mine[mine.length - 1];
  const cardN = shownIndex >= 0 ? shownIndex + 1 : throws.indexOf(card) + 1;
  const sum = summary(throws);
  const live = session.status === "active";
  const hide = async (t, hidden) => {
    await authedFetch(`/api/practice/sessions/${id}/throws/${encodeURIComponent(t.id)}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ hidden }) });
    load();
  };
  const stop = async () => {
    await authedFetch(`/api/practice/sessions/${id}/stop`, { method: "POST" });
    load();
  };
  const calibrating = !watch.status || ["starting", "calibrating"].includes(watch.status);
  return (
    <>
      {heading && <h2>Sheet {session.sheet} · {dayText(session.started_at)}, {timeText(session.started_at)}</h2>}
      <p className="muted">
        {live ? (calibrating ? "Getting ready: learning the sheet from the stream (a minute or two)…"
                             : `Watching for throw ${sum.count + 1}`)
              : `Session ended: ${ENDED[session.end_reason] ?? ENDED[session.status] ?? "ended"}.`}
        {live && <> <button onClick={stop}>End session</button></>}
        {!live && onDone && <> <button className="primary" onClick={onDone}>Start another</button></>}
        {!live && !onDone && <> <a className="btn primary" href={`/practice/sheet/${session.sheet}`}>Practise on sheet {session.sheet} again</a></>}
      </p>
      {err && <Warn>{err}</Warn>}
      {card && <Latest t={card} n={cardN} sid={id} live={live} newest={picked == null} />}
      {picked != null && <p><button onClick={() => setPicked(null)}>Show the latest throw</button></p>}
      {sum.count > 0 && (
        <p>
          <strong>{sum.count} throw{sum.count === 1 ? "" : "s"}</strong>
          {sum.weightMin != null && <> · {weightText(sum.weightMin)}–{weightText(sum.weightMax)} weight</>}
          {sum.withBroom > 0 && <> · {sum.onBroom} of {sum.withBroom} within 6 in of the broom</>}
        </p>
      )}
      {throws.length > 0 && (
        <table className="practice-throws">
          <thead><tr><th>#</th><th>Turn</th><th>Weight</th><th>Line</th><th>Rest</th><th /></tr></thead>
          <tbody>
            {throws.map((t, i) => [t, i]).reverse().map(([t, i]) => (
              <tr key={t.id} className={t.hidden ? "muted" : undefined}
                  onClick={() => setPicked(t.id)} style={{ cursor: "pointer",
                    background: t.id === card?.id ? "rgba(242,194,0,0.18)" : undefined }}>
                <td>{i + 1}</td>
                <td>{turnText(t.curl, session.hand) ?? "–"}</td>
                <td>{weightText(t.split_s) ?? "–"}</td>
                <td>{t.arrived ? lineText(t.line, { short: true }) ?? "–" : "didn't arrive"}</td>
                <td>{t.arrived ? restText(t.rest) ?? "–" : "–"}</td>
                <td><button onClick={e => { e.stopPropagation(); hide(t, !t.hidden); }}>
                  {t.hidden ? "Mine after all" : "Not mine"}</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}

function Start({ sheet, onStarted }) {
  const [hand, setHand] = useState(savedHand);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const go = async () => {
    setBusy(true); setErr("");
    try {
      const res = await authedFetch("/api/practice/sessions", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sheet, hand }) });
      const body = await res.json().catch(() => ({}));
      if (res.ok) onStarted(body.id);
      else setErr(res.status === 404 ? `There's no live stream on sheet ${sheet} right now.`
                                       : body.detail || `Could not start (${res.status}).`);
    } finally { setBusy(false); }
  };
  return (
    <Card>
      <p>You throw{" "}
        {["right", "left"].map(h => (
          <label key={h} style={{ marginRight: 12 }}>
            <input type="radio" name="hand" checked={hand === h}
                   onChange={() => { setHand(h); saveHand(h); }} /> {h}-handed
          </label>
        ))}
      </p>
      <p><button className="primary" disabled={busy} onClick={go}>
        {busy ? "Starting…" : "Start session"}</button></p>
      <p className="muted">Every throw on sheet {sheet} counts until you end it. It ends by
        itself after 20 minutes with no throws. If someone else throws, tap "Not mine".</p>
      {err && <Warn>{err}</Warn>}
    </Card>
  );
}

/* Every session of mine, newest first, by day. */
function Sessions() {
  const mine = useResource("/api/me/practice");
  const sessions = mine.data?.sessions ?? [];
  const days = [];
  for (const s of sessions) {
    const day = dayText(s.started_at);
    if (days[days.length - 1]?.day !== day) days.push({ day, sessions: [] });
    days[days.length - 1].sessions.push(s);
  }
  return (
    <>
      <h2>My practice</h2>
      <p style={{ marginBottom: 6 }}>Start a session on sheet</p>
      <p className="sheets">
        {SHEETS.map(n => <a key={n} className="btn" href={`/practice/sheet/${n}`}>{n}</a>)}
      </p>
      <Card>
        {mine.loading && !mine.data ? <span className="muted">Loading…</span>
         : mine.error ? "Could not load your sessions."
         : !sessions.length ? <span className="muted">No sessions yet.</span>
         : (
          <table className="practice-sessions">
            <tbody>
              {days.map(({ day, sessions: ss }) => [
                <tr key={day}><th colSpan={4}>{day}</th></tr>,
                ...ss.map(s => {
                  const end = s.ended_at ? Date.parse(s.ended_at) : Date.now();
                  return (
                    <tr key={s.id} onClick={() => { location.href = `/practice/session/${s.id}`; }}
                        style={{ cursor: "pointer" }}>
                      <td><a className="plain" href={`/practice/session/${s.id}`}>{timeText(s.started_at)}</a></td>
                      <td>Sheet {s.sheet}</td>
                      <td>{s.throws} throw{s.throws === 1 ? "" : "s"}</td>
                      <td>{s.active ? <Pill kind="live">Live</Pill>
                           : lengthText((end - Date.parse(s.started_at)) / 1000)}</td>
                    </tr>
                  );
                })])}
            </tbody>
          </table>
        )}
      </Card>
    </>
  );
}

function SignIn({ what }) {
  const { accounts } = useAuthUser();
  const [err, setErr] = useState("");
  return (
    <Card>
      <p>{what}</p>
      {accounts && <button className="primary"
        onClick={() => signIn().catch(e => setErr(e.code || e.message))}>Sign in</button>}
      {(!accounts || err) && <p className="muted">{err || "Accounts are not switched on for this site."}</p>}
    </Card>
  );
}

function Sheet({ sheet }) {
  const mine = useResource("/api/me/practice");
  const [sessionId, setSessionId] = useState(null);
  const active = mine.data?.sessions?.find(s => s.active && s.sheet === sheet);
  const current = sessionId ?? active?.id ?? null;
  return (
    <>
      <h2>Sheet {sheet}</h2>
      {mine.loading && !mine.data ? <p className="muted">Loading…</p>
        : current ? <Live id={current} onDone={() => { setSessionId(null); mine.reload(); }} />
        : <Start sheet={sheet} onStarted={setSessionId} />}
    </>
  );
}

export function Practice() {
  const where = route();
  const { user, ready } = useAuthUser();
  const links = [["/", "All games"], ["/practice", "My practice", where.sheet == null && !where.session],
                 ["/shots", "My shots"]];
  if (where.sheet != null) links.push([location.pathname, `Sheet ${where.sheet}`, true]);
  const ask = where.sheet != null
    ? `Sign in to practise on sheet ${where.sheet}: every throw you make shows up here a few seconds after it stops.`
    : "Sign in to see your practice sessions.";
  return (
    <>
      <Header links={links} />
      <main id="practice">
        {!ready && <p className="muted">Loading…</p>}
        {ready && !user && <SignIn what={ask} />}
        {ready && user && (where.sheet != null ? <Sheet key={user.uid} sheet={where.sheet} />
          : where.session ? <Live key={user.uid} id={where.session} heading />
          : <Sessions key={user.uid} />)}
      </main>
    </>
  );
}
