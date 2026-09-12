"use strict";
/* The waiting page. It has one job: make a half-hour wait legible.
 *
 * Which stage, how long that stage has taken against what it usually takes,
 * what the worker is doing right now, and how much is left. A bare "processing"
 * tells a person nothing about whether to keep the tab open or come back later.
 */
const $ = id => document.getElementById(id);
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
const esc = s => String(s ?? "").replace(/[&<>"]/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function mmss(s) {
  if (s == null) return "";
  s = Math.max(0, Math.round(s));
  const m = Math.floor(s / 60), x = s % 60;
  return m ? `${m}m ${String(x).padStart(2, "0")}s` : `${x}s`;
}
function hms(s) {
  s = Math.max(0, Math.round(s || 0));
  const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60;
  return (h ? h + ":" : "") + String(m).padStart(h ? 2 : 1, "0") + ":" + String(x).padStart(2, "0");
}

$("copy").onclick = async () => {
  try { await navigator.clipboard.writeText(location.href); $("copy").textContent = "Copied ✓"; }
  catch { prompt("Copy this link:", location.href); }
  setTimeout(() => { $("copy").textContent = "Copy this link"; }, 1500);
};

/* Overall progress: each phase weighted by how long it usually takes, so the
 * bar moves at something like a constant rate instead of leaping through the
 * quick stages and stalling on the long ones. */
function overall(phases, fraction) {
  const total = phases.reduce((n, p) => n + p.budget_s, 0) || 1;
  let done = 0;
  for (const p of phases) {
    if (p.state === "done" || p.state === "past") done += p.budget_s;
    else if (p.state === "running") done += p.budget_s * (fraction || 0);
  }
  return Math.min(1, done / total);
}

function phaseRows(phases) {
  return phases.map(p => {
    const mark = p.state === "done" || p.state === "past" ? "✓"
               : p.state === "running" ? "▶" : "·";
    const cls = p.state === "done" || p.state === "past" ? "done"
              : p.state === "running" ? "now" : "todo";
    const took = p.took_s != null ? `<span class="muted"> — ${mmss(p.took_s)}</span>` : "";
    return `<span class="${cls}">${mark}</span><span class="${cls}">${LABEL[p.name] || p.name}${took}</span>`;
  }).join("");
}

async function tick() {
  let s;
  try { s = await (await fetch("status.json", { cache: "no-store" })).json(); }
  catch { $("body").innerHTML = `<div class="warn">Can't reach the server; retrying…</div>`; return; }
  if (s.status === "ready") { location.reload(); return; }
  $("title").textContent = s.title || "Preparing your game…";

  let html = "";
  if (s.status === "pending_approval") {
    html = `<div class="warn">Waiting for the club admin to approve this video.
      Your link is saved — this page becomes the chart once it is processed.</div>`;
  } else if (s.status === "queued" && s.stalled) {
    html = `<div class="warn"><strong>Interrupted — going back in the queue.</strong>
      The machine working on this stopped part-way through. Nothing is lost: it
      picks up from where the caches left it, which is usually much quicker than
      starting over.</div>`;
  } else if (s.status === "queued") {
    const ahead = s.position ?? 0;
    html = s.worker_online
      ? `<p><strong>Queued${ahead ? ` — ${ahead} game${ahead > 1 ? "s" : ""} ahead of yours` : ", next up"}.</strong></p>
         <p class="muted">Each game takes roughly half an hour the first time it is seen.
         You can close this tab; the link keeps working.</p>`
      : `<div class="warn"><strong>Queued.</strong> The processing machine is offline
         right now. Your link is saved and this page will fill in when it comes back.</div>`;
  } else if (s.status === "processing") {
    const frac = overall(s.phases || [], s.fraction);
    const detail = s.message ? esc(s.message)
                 : (LABEL[s.phase] || s.phase || "Working");
    html = `
      <div class="bar"><div style="width:${(frac * 100).toFixed(1)}%"></div></div>
      <div class="row" style="justify-content:space-between">
        <strong>${detail}</strong>
        <span class="muted">${(frac * 100).toFixed(0)}%</span>
      </div>
      <p class="muted" style="margin:6px 0 0">
        ${s.elapsed_s != null ? `${mmss(s.elapsed_s)} elapsed` : ""}
        ${s.eta_s != null ? ` · about ${mmss(s.eta_s)} left` : ""}
        ${s.phase_elapsed_s != null ? ` · ${mmss(s.phase_elapsed_s)} on this stage` : ""}
        ${s.attempt > 1 ? ` · attempt ${s.attempt}` : ""}
      </p>
      <div class="phases">${phaseRows(s.phases || [])}</div>
      ${s.worker_online ? "" : `<div class="warn">The processing machine has gone
        quiet. If it does not come back, this job returns to the queue on its own
        and picks up where the caches leave it.</div>`}`;
  } else if (s.status === "failed") {
    html = `<div class="warn"><strong>We couldn't process this video.</strong><br>
      ${esc(s.error || "Unknown error")}</div>
      <p class="muted">The club admin can retry it; your link stays valid.</p>`;
  }

  if (s.other_games && s.other_games.length) {
    html += `<h2>Other games in this stream</h2><ul class="muted">` +
      s.other_games.map(g => `<li>Game ${g.index + 1} — starts at ${hms(g.start_s)}
        ${g.ends ? `(${g.ends} ends)` : ""}</li>`).join("") + `</ul>`;
  }
  $("body").innerHTML = html;
}
tick();
setInterval(tick, 5000);
