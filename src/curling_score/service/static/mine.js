"use strict";
import { onUser, signIn, authedFetch, enabled } from "./auth.js";

const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const day = iso => (iso || "").slice(0, 10) || "—";

let teams = [];

function chartRow(c) {
  const where = c.team_name ? `<span class="pill">${esc(c.team_name)}</span>` : "";
  const dup = c.duplicate_of
    ? `<div class="warn">Your team has another chart for this game.
       <a href="/c/${esc(c.duplicate_of)}/">Open it</a> — this one is kept as it is.</div>`
    : "";
  return `<tr>
    <td>${day(c.played_at)}</td>
    <td>${esc(c.title || "")} ${where}${dup}</td>
    <td>${c.sheet ?? "?"}</td>
    <td>${c.shots_charted || 0}</td>
    <td><span class="pill ${esc(c.status || "")}">${esc(c.status || "")}</span></td>
    <td class="actions">
      <a class="btn primary" href="/c/${esc(c.slug)}/">Chart</a>
      ${c.review_url ? `<a class="btn" href="${esc(c.review_url)}">Watch</a>` : ""}
    </td></tr>`;
}

async function loadCharts() {
  const res = await authedFetch("/api/me/charts");
  if (!res.ok) { $("charts").textContent = "Could not load your games."; return; }
  const { charts } = await res.json();
  $("charts").innerHTML = charts.length
    ? `<table><tr><th>Played</th><th>Game</th><th>Sheet</th><th>Charted</th>
       <th>Status</th><th></th></tr>${charts.map(chartRow).join("")}</table>`
    : `<span class="muted">Nothing yet. Pick a game from
       <a href="/games">the catalogue</a> and press Chart.</span>`;
}

function teamBlock(t) {
  const members = (t.members || []).map(m =>
    `${esc(m.name || m.email || m.id)}${m.is_owner ? " (owner)" : ""}`).join(", ");
  return `<div style="margin-bottom:14px">
    <strong>${esc(t.name)}</strong> — <span class="muted">${esc(members)}</span>
    <div class="row" style="margin-top:6px">
      <button data-invite="${esc(t.id)}">Invite someone</button>
      <span id="inv-${esc(t.id)}" class="muted"></span>
    </div></div>`;
}

async function loadTeams() {
  const res = await authedFetch("/api/me");
  if (!res.ok) { $("teams").textContent = "Could not load your teams."; return; }
  const me = await res.json();
  teams = [];
  for (const t of me.teams) {
    const full = await authedFetch(`/api/teams/${t.id}`);
    teams.push(full.ok ? await full.json() : t);
  }
  $("teams").innerHTML = teams.length
    ? teams.map(teamBlock).join("")
    : `<span class="muted">You are not on a team yet.</span>`;
  for (const b of $("teams").querySelectorAll("[data-invite]"))
    b.onclick = () => invite(b.dataset.invite);
}

async function invite(teamId) {
  const out = $(`inv-${teamId}`);
  out.textContent = "…";
  const res = await authedFetch(`/api/teams/${teamId}/invites`,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  if (!res.ok) { out.textContent = "could not make an invitation"; return; }
  const { url } = await res.json();
  out.textContent = url + " ";
  try { await navigator.clipboard.writeText(url); out.append("(copied)"); } catch { /* fine */ }
}

$("maketeam").onclick = async () => {
  const name = $("teamname").value.trim();
  if (!name) return;
  const res = await authedFetch("/api/teams", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }) });
  if (res.ok) { $("teamname").value = ""; await loadTeams(); }
};

$("claim").onclick = async () => {
  const raw = $("claimurl").value.trim();
  // Accept a whole URL or a bare slug -- people paste both.
  const slug = (raw.match(/\/c\/([^/?#]+)/) || [null, raw])[1];
  if (!slug) return;
  const res = await authedFetch(`/api/me/charts/${encodeURIComponent(slug)}/claim`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  $("claimnote").innerHTML = res.ok
    ? `<div class="ok">Added.</div>`
    : `<div class="warn">${res.status === 404 ? "No chart with that link."
        : res.status === 403 ? "That chart belongs to a team you are not on."
        : "Could not add that link."}</div>`;
  if (res.ok) { $("claimurl").value = ""; await loadCharts(); }
};

$("signin").onclick = () => signIn().catch(err => {
  $("disabled").hidden = false;
  $("disabled").textContent = err.code || err.message;
});

onUser(async (user, ready) => {
  if (!ready) return;
  $("signedout").hidden = !!user;
  $("signedin").hidden = !user;
  if (!enabled()) { $("disabled").hidden = false; $("signin").hidden = true; }
  if (user) { await loadCharts(); await loadTeams(); }
});
