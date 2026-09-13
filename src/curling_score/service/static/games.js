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
let signedIn = false;          // whether the League cells are editable
const picker = teamPicker($("teamrow"));

async function load() {
  const data = await (await fetch("/api/games")).json();
  all = data.games;
  refreshLeagueFilter();
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

/* The league a game belongs to, editable once you are signed in.
 *
 * The playlist watcher labels everything it queues; a link pasted by hand
 * arrives with nothing, and an unlabelled game can only be found by scrolling
 * to its date. Read-only it is just text -- the row is busy enough without a
 * permanent input in it -- and a click turns it into one. */
function leagueCell(g) {
  const shown = esc(g.league || "");
  if (!signedIn || !g.source_id)
    return `<td>${shown || '<span class="muted">&mdash;</span>'}</td>`;
  return `<td class="league"><button class="linky" data-league="${esc(g.source_id)}"
    title="Set the league for every game from this recording">${
      shown || '<span class="muted">set league</span>'}</button></td>`;
}

function editLeague(cell, sourceId) {
  const game = all.find(g => g.source_id === sourceId);
  const known = [...new Set(all.map(g => g.league).filter(Boolean))];
  cell.innerHTML = `<input list="leagues" value="${esc(game?.league || "")}"
      maxlength="60" aria-label="League">
    <datalist id="leagues">${known.map(l => `<option>${esc(l)}</option>`).join("")}</datalist>`;
  const input = cell.querySelector("input");
  input.focus(); input.select();
  let done = false;
  const finish = async (save) => {
    if (done) return;
    done = true;
    if (!save) return render();
    const league = input.value.trim();
    input.disabled = true;
    const res = await authedFetch(`/api/games/${encodeURIComponent(sourceId)}/league`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ league }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      alert(data.detail || res.statusText);
      return render();
    }
    // One recording is one sheet for one night, so the server labelled every
    // game in it. Move them all here rather than reloading the catalogue.
    for (const g of all)
      if (g.video_id === game.video_id) g.league = league || null;
    refreshLeagueFilter();
    render();
  };
  input.onkeydown = e => {
    if (e.key === "Enter") finish(true);
    if (e.key === "Escape") finish(false);
  };
  input.onblur = () => finish(true);
}

function refreshLeagueFilter() {
  const chosen = $("league").value;
  const known = [...new Set(all.map(g => g.league).filter(Boolean))].sort();
  $("league").innerHTML = `<option value="">All leagues</option>` +
    known.map(l => `<option>${esc(l)}</option>`).join("");
  $("league").value = known.includes(chosen) ? chosen : "";
}

/* Who played, by the colour they threw. Per game, where the league is per
 * recording -- one sheet on one night is one league and two different pairs.
 * Nothing detects it, so the cell offers itself for filling in. */
function teamsOf(g) {
  const r = g.team_red, y = g.team_yellow;
  if (!r && !y) return "";
  return `<span class="team red">${esc(r || "red")}</span> v `
       + `<span class="team yellow">${esc(y || "yellow")}</span>`;
}

function gameCell(g) {
  const fallback = g.game_index == null
    ? esc(g.title || g.video_id) : `Game ${g.game_index + 1}`;
  const teams = teamsOf(g);
  if (!signedIn || !g.source_id)
    return `<td>${teams || fallback}</td>`;
  return `<td class="teams"><button class="linky" data-teams="${esc(g.source_id)}"
    title="Say who played this game">${
      teams || `${fallback} <span class="muted">&mdash; name the teams</span>`}</button></td>`;
}

function editTeams(cell, sourceId) {
  const game = all.find(g => g.source_id === sourceId);
  cell.innerHTML = `<span class="teamedit">
      <input class="red" value="${esc(game?.team_red || "")}" maxlength="60"
             placeholder="red" aria-label="Red team">
      <span class="muted">v</span>
      <input class="yellow" value="${esc(game?.team_yellow || "")}" maxlength="60"
             placeholder="yellow" aria-label="Yellow team"></span>`;
  const [red, yellow] = cell.querySelectorAll("input");
  red.focus(); red.select();
  let done = false;
  const finish = async (save) => {
    if (done) return;
    done = true;
    if (!save) return render();
    const body = { red: red.value.trim(), yellow: yellow.value.trim() };
    red.disabled = yellow.disabled = true;
    const res = await authedFetch(`/api/games/${encodeURIComponent(sourceId)}/teams`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      alert(data.detail || res.statusText);
      return render();
    }
    game.team_red = body.red || null;
    game.team_yellow = body.yellow || null;
    render();
  };
  // Moving between the two boxes must not count as finishing, so a blur is
  // only an end when it leaves the pair.
  for (const input of [red, yellow]) {
    input.onkeydown = e => {
      if (e.key === "Enter") finish(true);
      if (e.key === "Escape") finish(false);
    };
    input.onblur = () => setTimeout(() => {
      if (!cell.contains(document.activeElement)) finish(true);
    }, 0);
  }
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
        ${leagueCell(g)}
        ${gameCell(g)}
        <td>${g.start_s == null ? "—" : hms(g.start_s)}</td>
        <td>${g.ends ?? "—"}</td>
        <td><span class="pill ${esc(g.status || "")}">${esc(g.status || "")}</span></td>
        ${actions(g)}
      </tr>`).join("")}
    </table>`).join("");
  for (const b of $("list").querySelectorAll("button[data-v]"))
    b.onclick = () => chart(b, b.dataset.s, b.dataset.v, b.dataset.t);
  for (const b of $("list").querySelectorAll("button[data-league]"))
    b.onclick = () => editLeague(b.parentElement, b.dataset.league);
  for (const b of $("list").querySelectorAll("button[data-teams]"))
    b.onclick = () => editTeams(b.parentElement, b.dataset.teams);
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
onUser(async (user, ready) => {
  signedIn = !!user;
  if (ready) { await picker.refresh(user); await loadMine(user); }
  else render();
});
load();
