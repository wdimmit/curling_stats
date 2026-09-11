"use strict";
const $ = id => document.getElementById(id);
const PHASES = [["download", "Downloading the video"], ["proxy", "Preparing the video"],
                ["calibrate", "Calibrating the cameras"], ["profile", "Finding the games"],
                ["detect", "Detecting stones"], ["rules", "Working out the shots"],
                ["scoreboard", "Reading the board"], ["upload", "Saving results"]];
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const hms = s => { s = Math.max(0, Math.round(s || 0)); const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60;
  return (h ? h + ":" : "") + String(m).padStart(h ? 2 : 1, "0") + ":" + String(x).padStart(2, "0"); };

$("copy").onclick = async () => {
  try { await navigator.clipboard.writeText(location.href); $("copy").textContent = "Copied ✓"; }
  catch { prompt("Copy this link:", location.href); }
};

async function tick() {
  let s;
  try { s = await (await fetch("status.json", { cache: "no-store" })).json(); }
  catch { $("body").innerHTML = `<div class="warn">Can't reach the server right now; retrying…</div>`; return; }
  $("title").textContent = s.title || "Preparing your game…";
  if (s.status === "ready") { location.reload(); return; }
  let html = "";
  if (s.status === "pending_approval") {
    html = `<div class="warn">Waiting for the club admin to approve this video. Your link is saved.</div>`;
  } else if (s.status === "queued") {
    const ahead = s.position ?? 0;
    html = s.worker_online
      ? `<p>Queued${ahead ? ` — ${ahead} game${ahead > 1 ? "s" : ""} ahead of yours` : ", next up"}.
         Each game takes about 30 minutes.</p>`
      : `<div class="warn">Queued. The processing machine is offline right now; your link is saved
         and this page will fill in when it comes back.</div>`;
  } else if (s.status === "processing") {
    const idx = PHASES.findIndex(p => p[0] === s.phase);
    const overall = idx < 0 ? 0 : (idx + (s.fraction || 0)) / PHASES.length;
    html = `<div class="bar"><div style="width:${(overall * 100).toFixed(0)}%"></div></div>
      <p>${esc(s.message || "")}${s.eta_s != null ? ` <span class="muted">· about ${Math.max(1, Math.round(s.eta_s / 60))} min left</span>` : ""}</p>
      <div class="phases">${PHASES.map((p, i) => `<span class="${i < idx ? "done" : i === idx ? "now" : "todo"}">${i < idx ? "✓" : i === idx ? "▶" : "·"}</span><span class="${i < idx ? "done" : i === idx ? "now" : "todo"}">${p[1]}</span>`).join("")}</div>`;
  } else if (s.status === "failed") {
    html = `<div class="warn">We couldn't process this video: ${esc(s.error || "unknown error")}.
      The club admin can retry it.</div>`;
  }
  if (s.other_games && s.other_games.length) {
    html += `<h2>Other games in this stream</h2>` + s.other_games.map(g =>
      `<div><a href="/?video=${encodeURIComponent(window.CHART?.slug || "")}" onclick="return false">Game ${g.index + 1}</a>
       starts at ${hms(g.start_s)}</div>`).join("");
  }
  $("body").innerHTML = html;
}
tick();
setInterval(tick, 5000);
