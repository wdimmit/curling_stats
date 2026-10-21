/* /playlists: the YouTube playlists the site watches, for an admin to add,
 * change, stop and remove.
 *
 * The same calls deploy/README.md makes with the admin token, made as a
 * signed-in admin (api.py, /api/admin/playlists). The poll itself stays the
 * scheduler's. The words, and the form to and from the API, are
 * core/playlists.mjs. */
import { useState } from "react";
import { authedFetch, signIn } from "./auth.js";
import {
  DAYS, DAY_NAMES, bodyFrom, emptyForm, formFrom, lastLookText, newNight, pastMidnight,
  playlistUrl, pollText, scheduleText, statusOf, toggleDay, troubleText,
} from "../core/playlists.mjs";
import { useAuthUser, useResource } from "./useAuth.js";
import { Card, Header, Pill } from "./ui.jsx";

const API = "/api/admin/playlists";
const one = id => `${API}/${encodeURIComponent(id)}`;

/* One call; the server's own words when it refuses. */
async function send(method, url, body) {
  try {
    const res = await authedFetch(url, {
      method,
      ...(body ? { headers: { "Content-Type": "application/json" },
                   body: JSON.stringify(body) } : {}),
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok) return { ok: true, data };
    return { ok: false, error: typeof data.detail === "string" ? data.detail : "Could not save." };
  } catch {
    return { ok: false, error: "Could not save." };
  }
}

/* One league night: its days, and the hours it runs. */
function Night({ night, n, idp, onChange, onRemove }) {
  const set = patch => onChange({ ...night, ...patch });
  return (
    <fieldset className="night">
      <legend>Night {n}</legend>
      <div className="days">
        {DAYS.map(d => (
          <label key={d} className="tick">
            <input type="checkbox" checked={night.days.includes(d)}
                   onChange={() => set({ days: toggleDay(night.days, d) })} /> {DAY_NAMES[d]}
          </label>
        ))}
      </div>
      <div className="row">
        <label className="inline" htmlFor={`${idp}-start`}>From</label>
        <input id={`${idp}-start`} type="time" value={night.start}
               onChange={e => set({ start: e.target.value })} />
        <label className="inline" htmlFor={`${idp}-end`}>to</label>
        <input id={`${idp}-end`} type="time" value={night.end}
               onChange={e => set({ end: e.target.value })} />
        {pastMidnight(night) && <span className="muted">the next day</span>}
        <span style={{ flex: 1 }} />
        <button type="button" onClick={onRemove}>Remove night {n}</button>
      </div>
    </fieldset>
  );
}

/* Adding one, or changing one: the same boxes, bar the link. */
function PlaylistForm({ initial, adding, clubTz, onDone, onCancel }) {
  const [form, setForm] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const set = patch => setForm(f => ({ ...f, ...patch }));
  const setNight = (i, night) => set({ nights: form.nights.map((x, j) => j === i ? night : x) });
  const idp = adding ? "pl-new" : `pl-${initial.id}`;

  const submit = async e => {
    e.preventDefault();
    const { body, error } = bodyFrom(form, { adding });
    if (error) { setErr(error); return; }
    setBusy(true);
    setErr("");
    const got = await send(adding ? "POST" : "PATCH", adding ? API : one(initial.id), body);
    setBusy(false);
    if (got.ok) onDone(got.data);
    else setErr(got.error);
  };

  return (
    <form className="plform" onSubmit={submit}>
      {adding && <>
        <label htmlFor={`${idp}-link`}>YouTube playlist</label>
        <input id={`${idp}-link`} value={form.link} onChange={e => set({ link: e.target.value })}
               placeholder="https://www.youtube.com/playlist?list=PL…" />
      </>}
      <label htmlFor={`${idp}-label`}>Name</label>
      <input id={`${idp}-label`} value={form.label} maxLength={60}
             onChange={e => set({ label: e.target.value })} placeholder="Thursday League" />
      <p className="muted hint">The league its games are filed under
        {adding && "; left empty, the playlist's id"}.</p>
      <label className="tick">
        <input type="checkbox" checked={form.practice}
               onChange={e => set({ practice: e.target.checked })} /> Practice streams, not
        league games
      </label>
      {form.practice ? (
        <p className="muted hint">A sheet's Start looks here for that sheet's live stream. It is
        never polled for games, so it has no league nights.</p>
      ) : <>
        <h3>League nights</h3>
        <p className="muted hint">In the club's time ({clubTz}). On these it is looked at every
        few minutes, so a stream is caught as it starts.</p>
        {form.nights.map((night, i) => (
          <Night key={i} night={night} n={i + 1} idp={`${idp}-n${i}`}
                 onChange={w => setNight(i, w)}
                 onRemove={() => set({ nights: form.nights.filter((_, j) => j !== i) })} />
        ))}
        <button type="button" onClick={() => set({ nights: [...form.nights, newNight()] })}>
          Add a night</button>
        <div className="row every">
          <label className="inline" htmlFor={`${idp}-live`}>On those nights, every</label>
          <input id={`${idp}-live`} type="number" min="1" step="any" value={form.liveMin}
                 onChange={e => set({ liveMin: e.target.value })} /> min
        </div>
        <div className="row every">
          <label className="inline" htmlFor={`${idp}-idle`}>Otherwise, every</label>
          <input id={`${idp}-idle`} type="number" min="1" step="any" value={form.idleMin}
                 disabled={form.idleNever} onChange={e => set({ idleMin: e.target.value })} /> min
          <label className="tick">
            <input type="checkbox" checked={form.idleNever}
                   onChange={e => set({ idleNever: e.target.checked })} /> Never
          </label>
        </div>
      </>}
      <div className="row" style={{ marginTop: 16 }}>
        <button className="primary" type="submit" disabled={busy}>
          {adding ? "Watch it" : "Save"}</button>
        {onCancel && <button type="button" onClick={onCancel} disabled={busy}>Cancel</button>}
        {err && <span className="warn-text" role="alert">{err}</span>}
      </div>
    </form>
  );
}

function Playlist({ pl, clubTz, onChanged, onRemoved }) {
  const [editing, setEditing] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const status = statusOf(pl);
  const trouble = troubleText(pl);

  const call = async (method, body, then) => {
    setBusy(true);
    setErr("");
    const got = await send(method, one(pl.id), body);
    setBusy(false);
    if (got.ok) then(got.data);
    else setErr(got.error);
  };

  if (editing) {
    return (
      <div className="card pl">
        <h3>{pl.label}</h3>
        <PlaylistForm initial={formFrom(pl)} clubTz={clubTz}
                      onDone={data => { onChanged(data.playlist); setEditing(false); }}
                      onCancel={() => setEditing(false)} />
      </div>
    );
  }
  return (
    <div className="card pl">
      <div className="plhead">
        <h3>{pl.label}</h3>
        <Pill kind={status.kind}>{status.text}</Pill>
        {pl.practice && <Pill>practice</Pill>}
      </div>
      <p className="muted"><a href={playlistUrl(pl.playlist_id)} target="_blank"
                              rel="noopener noreferrer">{pl.playlist_id}</a></p>
      {!pl.practice && <p>{scheduleText(pl.schedule) || "No league nights"}</p>}
      <p className="muted">{pollText(pl)}</p>
      {!pl.practice && <p className="muted">{lastLookText(pl, Date.now())}</p>}
      {trouble && <div className="warn">{trouble}</div>}
      {removing ? (
        <div className="warn">
          Removing it forgets which videos it has seen. To pause it, stop watching it instead.
          <div className="row" style={{ marginTop: 8 }}>
            <button disabled={busy} onClick={() => call("DELETE", null, () => onRemoved(pl.id))}>
              Remove {pl.label}</button>
            <button disabled={busy} onClick={() => setRemoving(false)}>Keep it</button>
          </div>
        </div>
      ) : (
        <div className="row actions">
          <button disabled={busy} onClick={() => setEditing(true)}>Change</button>
          <button disabled={busy}
                  onClick={() => call("PATCH", { enabled: !pl.enabled },
                                      data => onChanged(data.playlist))}>
            {pl.enabled ? "Stop watching" : "Watch again"}</button>
          <button disabled={busy} onClick={() => setRemoving(true)}>Remove</button>
        </div>
      )}
      {err && <p className="warn-text" role="alert">{err}</p>}
    </div>
  );
}

export function Playlists() {
  const { user, ready, accounts } = useAuthUser();
  const got = useResource(API, { skip: !ready || !user, deps: [user?.uid] });
  const [added, setAdded] = useState(0);
  const list = got.data?.playlists || [];
  const clubTz = got.data?.club_tz || "";
  const replace = pl => got.setData({ ...got.data,
                                      playlists: list.map(p => p.id === pl.id ? pl : p) });
  const drop = id => got.setData({ ...got.data, playlists: list.filter(p => p.id !== id) });
  const watching = list.filter(p => p.enabled).length;

  return (
    <>
      <Header links={[["/", "All games"], ["/mine", "My games"], ["/users", "Users"],
                      ["/playlists", "Playlists", true]]} />
      <main>
        <p className="muted" style={{ marginTop: 0 }}>The YouTube playlists the site watches.
        Each new recording in one is queued for charting without anyone pasting its link, with
        the playlist's name as its league.</p>
        {ready && !user ? (
          <Card>
            <p style={{ marginTop: 0 }}>Sign in as an admin to see the playlists.</p>
            {accounts && <button className="primary" onClick={() => signIn().catch(() => {})}>
              Sign in</button>}
          </Card>
        ) : !ready || (got.loading && !got.data) ? <p className="muted">Loading…</p>
          : got.error?.status === 403 ? <p className="warn">This page is for admins.</p>
          : got.error ? <p className="warn">The playlists could not be loaded. Try again in a
              minute.</p>
          : (
            <div id="playlists">
              <p className="muted">
                {list.length} {list.length === 1 ? "playlist" : "playlists"} · {watching}{" "}
                watched</p>
              {list.map(pl => <Playlist key={pl.id} pl={pl} clubTz={clubTz}
                                        onChanged={replace} onRemoved={drop} />)}
              <h2>Watch {list.length ? "another" : "a"} playlist</h2>
              <Card>
                <PlaylistForm key={added} initial={emptyForm()} adding clubTz={clubTz}
                              onDone={() => { setAdded(n => n + 1); got.reload(); }} />
              </Card>
            </div>
          )}
      </main>
    </>
  );
}
