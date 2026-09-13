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

function parseClock(text) {
  text = (text || "").trim();
  if (!text) return null;
  if (/^\d+(\.\d+)?$/.test(text)) return parseFloat(text);
  const parts = text.split(":").map(Number);
  if (parts.some(isNaN)) return null;
  return parts.reduce((acc, v) => acc * 60 + v, 0);
}

/* Say back what the clock text was understood to mean.
 *
 * "2:00" is two minutes, not two hours: the parser reads h:mm:ss from the
 * right, which is unambiguous for a start time like 1:52:30 and a trap for a
 * length. Asking for a two-hour game and silently getting two minutes is a
 * failed run and a confusing one, so the interpretation is shown as it is
 * typed rather than explained in a placeholder nobody rereads. */
function describe(seconds) {
  if (seconds === null) return "";
  if (seconds < 60) return `= ${seconds} s`;
  const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60);
  if (h && m) return `= ${h} h ${m} min`;
  if (h) return `= ${h} h`;
  return `= ${m} min`;
}

$("length").addEventListener("input", () => {
  const v = $("length").value.trim();
  const n = parseClock(v);
  $("lengthecho").textContent =
    !v ? "" : (n === null || n <= 0) ? "doesn't parse — use h:mm:ss" : describe(n);
});

$("f").addEventListener("submit", async ev => {
  ev.preventDefault();
  const start = parseClock($("start").value);
  const length = parseClock($("length").value);
  if ($("start").value.trim() && start === null) {
    $("msg").innerHTML = `<div class="warn">That start time doesn't parse — use h:mm:ss or seconds.</div>`;
    return;
  }
  if ($("length").value.trim() && (length === null || length <= 0)) {
    $("msg").innerHTML = `<div class="warn">That length doesn't parse — use h:mm:ss or seconds.</div>`;
    return;
  }
  const body = { url: $("url").value.trim() };
  if (start !== null) body.start_s = start;
  // Without this the server reads the whole video whenever it is under five
  // hours, so one game out of a long night costs the whole night.
  if (length !== null) body.duration_s = length;
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
