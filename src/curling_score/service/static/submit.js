"use strict";
/* A module, so a submission can carry who made it -- otherwise a signed-in
 * person's own link would not show up in their list. Deferred like every
 * module, which is fine: the form it binds to is parsed by then. */
import { authedFetch, onUser } from "./auth.js";
import { teamPicker } from "./teams.js";

const $ = id => document.getElementById(id);

/* Which team this goes to, when the person is on one. */
const picker = teamPicker($("teamrow"));
onUser((user, ready) => { if (ready) picker.refresh(user); });

function parseStart(text) {
  text = (text || "").trim();
  if (!text) return null;
  if (/^\d+(\.\d+)?$/.test(text)) return parseFloat(text);
  const parts = text.split(":").map(Number);
  if (parts.some(isNaN)) return null;
  return parts.reduce((acc, v) => acc * 60 + v, 0);
}

$("f").addEventListener("submit", async ev => {
  ev.preventDefault();
  const start = parseStart($("start").value);
  if ($("start").value.trim() && start === null) {
    $("msg").innerHTML = `<div class="warn">That start time doesn't parse — use h:mm:ss or seconds.</div>`;
    return;
  }
  const body = { url: $("url").value.trim() };
  if (start !== null) body.start_s = start;
  if ($("sheet").value) body.sheet = parseInt($("sheet").value, 10);
  const team = picker.value();
  if (team) body.team_id = team;
  $("go").disabled = true;
  $("msg").innerHTML = `<div class="muted" style="margin-top:12px">Checking the video…</div>`;
  try {
    const res = await authedFetch("/api/submissions", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      $("msg").innerHTML = `<div class="warn">${escapeHtml(data.detail || res.statusText)}</div>`;
      return;
    }
    location.href = data.chart_url;
  } catch (err) {
    $("msg").innerHTML = `<div class="warn">Could not reach the server: ${escapeHtml(String(err))}</div>`;
  } finally {
    $("go").disabled = false;
  }
});

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
