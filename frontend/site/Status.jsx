/* The waiting page. It has one job: make a half-hour wait legible.
 *
 * Which stage, how long that stage has taken against what it usually takes,
 * what the worker is doing right now, and how much is left. A bare
 * "processing" tells a person nothing about whether to keep the tab open or
 * come back later.
 */
import { Fragment, useEffect, useState } from "react";
import { hms, mmss } from "./fmt.js";
import { Card, Warn } from "./ui.jsx";

const LABEL = {
  download: "Downloading the video from YouTube",
  proxy: "Preparing the video for analysis",
  calibrate: "Finding the sheet and calibrating",
  profile: "Finding the games and ends",
  detect: "Detecting stones, shot by shot",
  rules: "Working out the shots",
  scoreboard: "Reading the wall scoreboard",
  upload: "Saving the results",
};

/* Each phase weighted by how long it usually takes, so the bar moves at
 * something like a constant rate instead of leaping through the quick stages
 * and stalling on the long ones. */
function overall(phases, fraction) {
  const total = phases.reduce((n, p) => n + p.budget_s, 0) || 1;
  let done = 0;
  for (const p of phases) {
    if (p.state === "done" || p.state === "past") done += p.budget_s;
    else if (p.state === "running") done += p.budget_s * (fraction || 0);
  }
  return Math.min(1, done / total);
}

const Phases = ({ phases }) => (
  <div className="phases">
    {phases.map(p => {
      const finished = p.state === "done" || p.state === "past";
      const cls = finished ? "done" : p.state === "running" ? "now" : "todo";
      return (
        <Fragment key={p.name}>
          <span className={cls}>{finished ? "✓" : p.state === "running" ? "▶" : "·"}</span>
          <span className={cls}>
            {LABEL[p.name] || p.name}
            {p.took_s != null && <span className="muted"> — {mmss(p.took_s)}</span>}
          </span>
        </Fragment>
      );
    })}
  </div>
);

function Body({ s }) {
  if (s.status === "pending_approval")
    return <Warn>Waiting for the club admin to approve this video. Your link is
      saved — this page becomes the chart once it is processed.</Warn>;

  if (s.status === "queued" && s.stalled)
    return <Warn><strong>Interrupted — going back in the queue.</strong> The machine
      working on this stopped part-way through. Nothing is lost: it picks up from
      where the caches left it, which is usually much quicker than starting over.</Warn>;

  if (s.status === "queued") {
    const ahead = s.position ?? 0;
    return s.worker_online ? (
      <>
        <p><strong>Queued{ahead ? ` — ${ahead} game${ahead > 1 ? "s" : ""} ahead of yours`
                                : ", next up"}.</strong></p>
        <p className="muted">Each game takes roughly half an hour the first time it is
          seen. You can close this tab; the link keeps working.</p>
      </>
    ) : (
      <Warn><strong>Queued.</strong> The processing machine is offline right now. Your
        link is saved and this page will fill in when it comes back.</Warn>
    );
  }

  if (s.status === "processing") {
    const frac = overall(s.phases || [], s.fraction);
    return (
      <>
        <div className="bar"><div style={{ width: `${(frac * 100).toFixed(1)}%` }} /></div>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <strong>{s.message || LABEL[s.phase] || s.phase || "Working"}</strong>
          <span className="muted">{(frac * 100).toFixed(0)}%</span>
        </div>
        <p className="muted" style={{ margin: "6px 0 0" }}>
          {s.elapsed_s != null ? `${mmss(s.elapsed_s)} elapsed` : ""}
          {s.eta_s != null ? ` · about ${mmss(s.eta_s)} left` : ""}
          {s.phase_elapsed_s != null ? ` · ${mmss(s.phase_elapsed_s)} on this stage` : ""}
          {s.attempt > 1 ? ` · attempt ${s.attempt}` : ""}
        </p>
        <Phases phases={s.phases || []} />
        {!s.worker_online && <Warn>The processing machine has gone quiet. If it does
          not come back, this job returns to the queue on its own and picks up where
          the caches leave it.</Warn>}
      </>
    );
  }

  if (s.status === "failed")
    return (
      <>
        <Warn><strong>We couldn't process this video.</strong><br />
          {s.error || "Unknown error"}</Warn>
        <p className="muted">The club admin can retry it; your link stays valid.</p>
      </>
    );

  return null;
}

export function Status() {
  const [s, setS] = useState(null);
  const [down, setDown] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const got = await (await fetch("status.json", { cache: "no-store" })).json();
        if (!alive) return;
        if (got.status === "ready") { location.reload(); return; }
        setDown(false);
        setS(got);
      } catch { if (alive) setDown(true); }
    };
    tick();
    const id = setInterval(tick, 5000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  return (
    <>
      <header>
        <h1>Curling Chart</h1>
        <a href="/">Submit another</a>
        <a href="/games">All games</a>
      </header>
      <main>
        <Card>
          <h2 id="title" style={{ marginTop: 0 }}>{s?.title || "Preparing your game…"}</h2>
          <div id="body">
            {down ? <Warn>Can't reach the server; retrying…</Warn>
                  : s ? <Body s={s} />
                      : <span className="muted">Loading…</span>}
            {s?.other_games?.length ? (
              <>
                <h2>Other games in this stream</h2>
                <ul className="muted">
                  {s.other_games.map(g => (
                    <li key={g.index}>Game {g.index + 1} — starts at {hms(g.start_s)}
                      {g.ends ? ` (${g.ends} ends)` : ""}</li>
                  ))}
                </ul>
              </>
            ) : null}
          </div>
          <div className="row" style={{ marginTop: 14 }}>
            <button id="copy" onClick={async () => {
              try { await navigator.clipboard.writeText(location.href); setCopied(true); }
              catch { prompt("Copy this link:", location.href); }
              setTimeout(() => setCopied(false), 1500);
            }}>{copied ? "Copied ✓" : "Copy this link"}</button>
            <span className="muted" style={{ fontSize: 13 }}>
              Bookmark it — this page becomes the chart when processing finishes.
            </span>
          </div>
        </Card>
      </main>
    </>
  );
}
