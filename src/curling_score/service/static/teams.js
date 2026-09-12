"use strict";
/* "Chart this as ..." -- the one control shared by the submit form and the
 * catalogue. It is only ever shown to somebody who is actually on a team,
 * because for everyone else there is nothing to choose. */
import { authedFetch } from "./auth.js";

export function teamPicker(row) {
  let teams = [];

  async function refresh(user) {
    if (!row) return;
    if (!user) { row.hidden = true; teams = []; return; }
    const res = await authedFetch("/api/me");
    if (!res.ok) { row.hidden = true; return; }
    teams = (await res.json()).teams || [];
    if (!teams.length) { row.hidden = true; return; }
    row.hidden = false;
    row.textContent = "";
    const label = document.createElement("label");
    label.htmlFor = "teampick";
    label.style.margin = "0";
    label.textContent = "Chart as";
    const select = document.createElement("select");
    select.id = "teampick";
    // Built as nodes, not markup: a team name is whatever somebody typed.
    for (const [value, text] of [["", "Just me"], ...teams.map(t => [t.id, t.name])]) {
      const opt = document.createElement("option");
      opt.value = value;
      opt.textContent = text;
      select.append(opt);
    }
    row.append(label, select);
  }

  return { refresh, value: () => (document.getElementById("teampick") || {}).value || "" };
}
