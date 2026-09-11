"use strict";
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const hms = s => { s = Math.max(0, Math.round(s || 0)); const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60;
  return (h ? h + ":" : "") + String(m).padStart(h ? 2 : 1, "0") + ":" + String(x).padStart(2, "0"); };
let all = [];

async function load() {
  const data = await (await fetch("/api/games")).json();
  all = data.games;
  $("league").innerHTML = `<option value="">All leagues</option>` +
    data.leagues.map(l => `<option>${esc(l)}</option>`).join("");
  render();
}

function render() {
  const league = $("league").value;
  const games = all.filter(g => !league || g.league === league);
  if (!games.length) { $("list").innerHTML = `<span class="muted">No games yet.</span>`; return; }
  const byDate = {};
  for (const g of games) (byDate[(g.played_at || "").slice(0, 10) || "undated"] ||= []).push(g);
  $("list").innerHTML = Object.keys(byDate).sort().reverse().map(date => `
    <h2>${esc(date)}</h2>
    <table><tr><th>Sheet</th><th>League</th><th>Game</th><th>Starts</th><th>Ends</th><th>Status</th><th></th></tr>
    ${byDate[date].sort((a, b) => (a.sheet ?? 99) - (b.sheet ?? 99) || (a.start_s ?? 0) - (b.start_s ?? 0)).map(g => `
      <tr>
        <td>${g.sheet ?? "?"}</td>
        <td>${esc(g.league || "")}</td>
        <td>${g.game_index == null ? esc(g.title || g.video_id) : `Game ${g.game_index + 1}`}</td>
        <td>${g.start_s == null ? "—" : hms(g.start_s)}</td>
        <td>${g.ends ?? "—"}</td>
        <td><span class="pill ${esc(g.status || "")}">${esc(g.status || "")}</span></td>
        <td><button data-v="${esc(g.video_id)}" data-t="${g.start_s ?? ""}" ${g.status === "failed" ? "disabled" : ""}>Chart this game</button></td>
      </tr>`).join("")}
    </table>`).join("");
  for (const b of $("list").querySelectorAll("button")) b.onclick = () => chart(b.dataset.v, b.dataset.t);
}

async function chart(videoId, start) {
  const body = { url: videoId };
  if (start !== "") body.start_s = parseFloat(start);
  const res = await fetch("/api/submissions", { method: "POST",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) { alert(data.detail || res.statusText); return; }
  location.href = data.chart_url;
}

$("league").onchange = render;
load();
