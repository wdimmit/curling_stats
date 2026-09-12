"use strict";
import { onUser, signIn, authedFetch } from "./auth.js";

const $ = id => document.getElementById(id);
const token = location.pathname.split("/").filter(Boolean).pop();
let invite = null;

async function load() {
  const res = await fetch(`/api/invites/${encodeURIComponent(token)}`);
  if (!res.ok) {
    $("title").textContent = "That invitation is not valid";
    $("blurb").textContent = "Ask whoever sent it for a fresh link.";
    return;
  }
  invite = await res.json();
  $("title").textContent = `Join ${invite.team}`;
  $("blurb").textContent = invite.usable
    ? `Everyone on ${invite.team} shares one chart per game, and sees the same list.`
    : "That invitation has expired. Ask for a fresh link.";
  paint();
}

function paint() {
  if (!invite || !invite.usable) return;
  const user = window.__joinUser;
  $("join").hidden = !user;
  $("signin").hidden = !!user;
}

$("signin").onclick = () => signIn().catch(err => {
  $("note").innerHTML = `<div class="warn">${err.code || err.message}</div>`;
});

$("join").onclick = async () => {
  $("join").disabled = true;
  const res = await authedFetch(`/api/invites/${encodeURIComponent(token)}/accept`,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  if (res.ok) { location.href = "/mine"; return; }
  $("join").disabled = false;
  $("note").innerHTML = `<div class="warn">${
    res.status === 410 ? "That invitation has expired."
    : res.status === 401 ? "Sign in first."
    : "Could not join that team."}</div>`;
};

onUser((user, ready) => { if (ready) { window.__joinUser = user; paint(); } });
load();
