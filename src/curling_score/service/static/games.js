"use strict";
/* The catalogue.
 *
 * Two actions per game, and the distinction matters: Watch costs nothing and
 * creates nothing, Chart starts a charting session. Pressing Chart twice on
 * the same game used to mint a second, blank chart and strand the first;
 * /api/charts now hands back the one you already have.
 */
import { authedFetch, onUser } from "./auth.js";
import { teamPicker } from "./teams.js";

const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const hms = s => { s = Math.max(0, Math.round(s || 0)); const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60;
  return (h ? h + ":" : "") + String(m).padStart(h ? 2 : 1, "0") + ":" + String(x).padStart(2, "0"); };

let all = [];
let mine = new Map();          // source_id -> the chart slug I already have
const picker = teamPicker($("teamrow"));

async function load() {
  const data = await (await fetch("/api/games")).json();
  all = data.games;
  $("league").innerHTML = `<option value="">All leagues</option>` +
    data.leagues.map(l => `<option>${esc(l)}</option>`).join("");
  render();
}

async function loadMine(user) {
  mine = new Map();
  if (user) {
    const res = await authedFetch("/api/me/charts");
    if (res.ok)
      for (const c of (await res.json()).charts)
        if (c.source_id) mine.set(c.source_id, c.slug);
  }
  render();
}

function actions(g) {
  const watch = g.source_id
    ? `<a class="btn" href="/g/${encodeURIComponent(g.source_id)}/">Watch</a>` : "";
  const have = g.source_id && mine.get(g.source_id);
  // Already charting it? Say so, and go straight there. A second press used to
  // hand back a blank chart with the first one's grading stranded behind it.
  const chart = have
    ? `<a class="btn primary" href="/c/${esc(have)}/">Open your chart</a>`
    : `<button data-s="${esc(g.source_id || "")}" data-v="${esc(g.video_id)}"
         data-t="${g.start_s ?? ""}" ${g.status === "failed" ? "disabled" : ""}>Chart</button>`;
  return `<td class="actions">${watch}${chart}</td>`;
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
        ${actions(g)}
      </tr>`).join("")}
    </table>`).join("");
  for (const b of $("list").querySelectorAll("button[data-v]"))
    b.onclick = () => chart(b, b.dataset.s, b.dataset.v, b.dataset.t);
}

async function chart(button, sourceId, videoId, start) {
  button.disabled = true;
  const team = picker.value();
  // A game the catalogue knows goes through /api/charts, which queues no work
  // and so does not spend one of the five submissions an hour. One that is
  // still processing has no source yet, so it still goes through submissions.
  const [url, body] = sourceId
    ? ["/api/charts", { source_id: sourceId }]
    : ["/api/submissions", { url: videoId, ...(start !== "" ? { start_s: parseFloat(start) } : {}) }];
  if (team) body.team_id = team;
  const res = await authedFetch(url, { method: "POST",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) { button.disabled = false; alert(data.detail || res.statusText); return; }
  location.href = data.chart_url;
}

$("league").onchange = render;
onUser(async (user, ready) => { if (ready) { await picker.refresh(user); await loadMine(user); } });
load();
