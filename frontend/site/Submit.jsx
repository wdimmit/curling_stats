import { useState } from "react";
import { authedFetch } from "./auth.js";
import { describe, parseClock } from "./fmt.js";
import { Card, Header, TeamPicker, Warn } from "./ui.jsx";

export function Submit() {
  const [url, setUrl] = useState("");
  const [start, setStart] = useState("");
  const [length, setLength] = useState("");
  const [sheet, setSheet] = useState("");
  const [team, setTeam] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);

  const lengthSecs = parseClock(length);
  const lengthEcho = !length.trim() ? ""
    : (lengthSecs === null || lengthSecs <= 0) ? "doesn't parse — use h:mm:ss"
    : describe(lengthSecs);

  async function submit(ev) {
    ev.preventDefault();
    const startSecs = parseClock(start);
    if (start.trim() && startSecs === null)
      return setMsg(<Warn>That start time doesn't parse — use h:mm:ss or seconds.</Warn>);
    if (length.trim() && (lengthSecs === null || lengthSecs <= 0))
      return setMsg(<Warn>That length doesn't parse — use h:mm:ss or seconds.</Warn>);

    const body = { url: url.trim() };
    if (startSecs !== null) body.start_s = startSecs;
    // Without this the server reads the whole video whenever it is under five
    // hours, so one game out of a long night costs the whole night.
    if (lengthSecs !== null) body.duration_s = lengthSecs;
    if (sheet) body.sheet = parseInt(sheet, 10);
    if (team) body.team_id = team;

    setBusy(true);
    setMsg(<div className="muted" style={{ marginTop: 12 }}>Checking the video…</div>);
    try {
      const res = await authedFetch("/api/submissions", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) return setMsg(<Warn>{data.detail || res.statusText}</Warn>);
      location.href = data.chart_url;
    } catch (err) {
      setMsg(<Warn>Could not reach the server: {String(err)}</Warn>);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Header links={[["/games", "All games"]]} />
      <main>
        <Card>
          <p>Paste the YouTube link to a club stream. We find the game, work out every
          shot and where the stones came to rest, and give you a link where your team
          can watch each shot, fix what we missed, and grade it.</p>
          <form id="f" onSubmit={submit}>
            <label htmlFor="url">YouTube link</label>
            <input id="url" name="url" required autoFocus value={url}
                   onChange={e => setUrl(e.target.value)}
                   placeholder="https://www.youtube.com/watch?v=… (a ?t= start time is kept)" />
            <div className="row">
              <div style={{ flex: 1 }}>
                <label htmlFor="start">Game starts at (optional)</label>
                <input id="start" name="start" value={start}
                       onChange={e => setStart(e.target.value)}
                       placeholder="e.g. 1:52:30 or 6750 — for a stream with several games" />
              </div>
              <div style={{ width: 170 }}>
                <label htmlFor="length">Length (optional)</label>
                <input id="length" name="length" value={length}
                       onChange={e => setLength(e.target.value)}
                       placeholder="e.g. 2:15:00 — just this game" />
                <div id="lengthecho" className="muted"
                     style={{ fontSize: 12, marginTop: 4 }}>{lengthEcho}</div>
              </div>
              <div style={{ width: 140 }}>
                <label htmlFor="sheet">Sheet (optional)</label>
                <input id="sheet" name="sheet" type="number" min="1" max="8"
                       placeholder="from title" value={sheet}
                       onChange={e => setSheet(e.target.value)} />
              </div>
            </div>
            <TeamPicker value={team} onChange={setTeam} />
            <div className="row" style={{ marginTop: 16 }}>
              <button className="primary" id="go" type="submit" disabled={busy}>Get my link</button>
              <span className="muted">Processing takes about half an hour the first time a video is seen.</span>
            </div>
          </form>
          <div id="msg">{msg}</div>
        </Card>
      </main>
      <footer>
        This service downloads the video from YouTube in order to analyse it, which is
        against YouTube's Terms of Service; it does so from a single residential
        connection belonging to the operator, only for the club's own videos, and keeps
        no copy beyond what processing needs. Playback on the chart page uses YouTube's
        own player. Do not submit videos you do not have the right to analyse.
      </footer>
    </>
  );
}
