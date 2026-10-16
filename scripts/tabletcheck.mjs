/* Charting on a portrait iPad (spec 2026-10-16): a touch device 744-1024 px wide
 * and taller than wide charts a rock with no page scroll; a landscape or mouse
 * page is the one it always was.
 *
 *   PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python scripts/devserve.py TIMELINE
 *   google-chrome --headless=new --disable-gpu --no-sandbox --hide-scrollbars \
 *     --remote-debugging-port=9371 --user-data-dir=$(mktemp -d) about:blank &
 *   CDP_PORT=9371 node scripts/tabletcheck.mjs EDIT VIEW REVIEW [--only=…] [--shots=DIR]
 *     [--blanks=EDIT_URL] [--doubles=EDIT_URL]
 *
 * EDIT, VIEW and REVIEW are the three URLs devserve prints for a timeline whose
 * rock #e=2&s=4 has a delivery and whose rock #e=3&s=3 has none (tl-s_0cYt.json).
 * The script adds each page's hash, so give the URLs without one.
 * --blanks and --doubles are the edit URL of a separate devserve run on the blanks and the
 * doubles timeline; a section without its flag prints "skip" (not a failure).
 * The doubles view-only URL is read from the edit page's own chart document.
 *
 * "Absent" throughout means not visible: no element, or one with the hidden attribute, or one
 * with no box.
 *
 * Every page opens with device metrics and touch emulation on (mobile: true,
 * maxTouchPoints 5), which is what makes any-pointer: coarse match. A tap is a
 * touchStart/touchEnd pair at the element's centre, which gives the whole
 * touch -> mousedown -> click sequence; Input.synthesizeTapGesture gives no click in headless
 * Chrome. The run always ends by switching touch emulation off and clearing the metrics, so
 * the same Chrome can go on to serve deskcheck and domsnap.
 *
 * Checks, by name:
 *   fit        four iPad sizes x three surfaces at #e=2&s=4 (and /c/ at #e=3&s=3): no scroll, the
 *              video, transport, house card, figures card and grading card above the fold and
 *              clear of each other, the video 16:9 and at least 560 px wide, the line button
 *              there only where the rock has a delivery. Prints the house's drawn size.
 *   targets    every visible button, select, input, textarea, link, shot and check-label outside
 *              the video and outside a closed dialog is at least 36 x 40 (a check-box inside its
 *              label.chk is the label's to be)
 *   menu       the header's overflow menu: opens, holds the right things, a field in it keeps it
 *              open, closes
 *   next       the Next rock button steps across an end and is disabled on the last rock
 *   sheets     the Miss, Note and Order sheets open at the top, keep the keys out, and write back
 *   rotate     a note typed in a sheet survives a turn to landscape; no sheet is left open
 *   house      tapping the house opens a full-height editor that survives Safari's bar
 *   blanks     (--blanks) the editor on a blank rock: Mark charted twice. Marks two rocks charted
 *              on devserve's in-memory chart, so it needs a FRESH blanks devserve for each run.
 *   doubles    (--doubles) fit, a one-line end box and the swap sheet on a doubles game
 *   dialogs    the line & speed view and the Thinking view as tablet pop-ups
 *   unchanged  no figures card on a landscape touch page, a mouse page or a narrow touch page
 * Exits 1 naming each failure, 2 on bad arguments. devserve is in memory, so nothing typed here
 * goes anywhere. */
import { mkdirSync, writeFileSync } from "node:fs";
import { attach } from "./cdp.mjs";

const args = process.argv.slice(2);
const opt = name => (args.find(a => a.startsWith(`--${name}=`)) || "").slice(name.length + 3);
const only = opt("only").split(",").filter(Boolean);
const shots = opt("shots");
const blanksUrl = opt("blanks");
const doublesUrl = opt("doubles");
const [edit, view, review] = args.filter(a => !a.startsWith("--"));
const NAMES = ["fit", "targets", "menu", "next", "sheets", "rotate", "house", "blanks", "doubles", "dialogs", "unchanged"];
if (!review) {
  console.error(`usage: node scripts/tabletcheck.mjs EDIT VIEW REVIEW [--only=${NAMES.join(",")}] [--shots=DIR] [--blanks=EDIT_URL] [--doubles=EDIT_URL]`);
  process.exit(2);
}
const unknown = only.filter(n => !NAMES.includes(n));
if (unknown.length) {
  console.error(`unknown check ${unknown.join(", ")}; usage: node scripts/tabletcheck.mjs EDIT VIEW REVIEW [--only=${NAMES.join(",")}] [--shots=DIR] [--blanks=EDIT_URL] [--doubles=EDIT_URL]`);
  process.exit(2);
}
if (shots) mkdirSync(shots, { recursive: true });
const want = name => !only.length || only.includes(name);
const cdp = await attach("");
const fails = [];
const fail = msg => { fails.push(msg); console.log(`FAIL ${msg}`); };
// An exception (a timeout, a missing page, an element that cannot be tapped) fails that check
// and the run goes on.
async function check(name, fn) {
  if (!want(name)) return;
  try { await fn(); } catch (err) { fail(`${name}: ${err.message}`); }
}
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function waitFor(expr, ms = 15000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    if (await cdp.eval(expr).catch(() => false)) return;
    await sleep(200);
  }
  throw new Error(`timed out waiting for ${expr}`);
}

const SIZES = [[820, 1080], [744, 1050], [834, 1110], [1024, 1290]];
const sans = url => url.replace(/#.*$/, "");
const at = (url, hash) => sans(url) + hash;
const ROCK = "#e=2&s=4";          // a rock with a delivery
const NO_LINE = "#e=3&s=3";       // a rock without one

/* The window: a tablet (touch, mobile) unless told otherwise. */
async function setSize(width, height, touch = true) {
  await cdp.send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: touch });
  await cdp.send("Emulation.setTouchEmulationEnabled", touch ? { enabled: true, maxTouchPoints: 5 } : { enabled: false });
}

async function open(url, width, height, touch = true) {
  await setSize(width, height, touch);
  await cdp.send("Page.navigate", { url: "about:blank" });
  await sleep(200);
  await cdp.send("Page.navigate", { url });
  await waitFor(`!!document.querySelector("#shots .shot")`);
  await sleep(700);    // the line chart measures its box after the first paint
}

/* In-page: the element's box, and whether it is there to be seen. `expr` is a
 * JS expression for the element (or null). */
const boxOf = expr => cdp.eval(`(() => { const e = ${expr}; if (!e) return null;
  const r = e.getBoundingClientRect(), cs = getComputedStyle(e);
  return { x: r.x, y: r.y, w: r.width, h: r.height, bottom: r.bottom, right: r.right,
           vis: !e.hidden && r.width > 0 && r.height > 0 && cs.visibility !== "hidden" && cs.display !== "none" }; })()`);
const box = sel => boxOf(`document.querySelector(${JSON.stringify(sel)})`);
const vis = async sel => !!(await box(sel))?.vis;
const page = () => cdp.eval(`({ sh: document.documentElement.scrollHeight, sw: document.documentElement.scrollWidth,
  ih: innerHeight, iw: innerWidth, hash: location.hash })`);
const isOpen = id => cdp.eval(`!!document.querySelector("#${id}[open]")`);
const data = name => cdp.eval(`document.body.dataset[${JSON.stringify(name)}] ?? null`);
const text = sel => cdp.eval(`document.querySelector(${JSON.stringify(sel)})?.textContent.trim() ?? null`);
const hashRock = async () => {
  const p = new URLSearchParams((await page()).hash.slice(1));
  return `e=${p.get("e")}&s=${p.get("s")}`;
};
/* The house as drawn: its <svg>'s box, and the drawing in it, which the viewBox letterboxes. */
const house = () => cdp.eval(`(() => {
  const e = document.getElementById("house"); if (!e) return null;
  const r = e.getBoundingClientRect(), vb = e.viewBox.baseVal;
  const k = vb && vb.width && vb.height ? Math.min(r.width / vb.width, r.height / vb.height) : 0;
  return { box: Math.round(r.height), drawn: Math.round(vb.height * k), w: Math.round(vb.width * k) }; })()`);

/* A tap: the touch pair that gives touch -> mousedown -> click. */
async function tapAt(x, y) {
  await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x, y }] });
  await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
}
/* Taps the middle of an element. Throws, naming what is wrong, when there is nothing there to
 * tap: absent, not visible, off the screen, or covered by something else. */
async function tapExpr(expr, what) {
  const r = await cdp.eval(`(() => { const e = ${expr}; if (!e) return null;
    const b = e.getBoundingClientRect(), cs = getComputedStyle(e);
    const vis = !e.hidden && b.width > 0 && b.height > 0 && cs.visibility !== "hidden" && cs.display !== "none";
    const x = b.x + b.width / 2, y = b.y + b.height / 2, top = document.elementFromPoint(x, y);
    const name = n => !n ? "nothing (off the screen)" : n.id ? "#" + n.id : n.tagName.toLowerCase() + [...n.classList].map(c => "." + c).join("");
    return { x, y, vis, hit: !!top && e.contains(top), under: name(top) }; })()`);
  if (!r) throw new Error(`${what} is missing, so it cannot be tapped`);
  if (!r.vis) throw new Error(`${what} is not visible, so it cannot be tapped`);
  if (!r.hit) throw new Error(`${what} is covered at (${Math.round(r.x)}, ${Math.round(r.y)}) by ${r.under}`);
  await tapAt(r.x, r.y);
}
const tap = sel => tapExpr(`document.querySelector(${JSON.stringify(sel)})`, sel);
/* The Done button of a sheet. */
const tapDone = id => tapExpr(`[...document.querySelectorAll("#${id} button")].find(b => b.textContent.trim() === "Done")`, `#${id}'s Done button`);
/* Closes a sheet by its Done button, and says so if it stays. */
async function closeSheet(id) {
  await tapDone(id);
  await sleep(300);
  if (await isOpen(id)) fail(`#${id} stayed open after Done`);
}

const CODES = { ArrowLeft: 37, ArrowRight: 39, Escape: 27 };
async function key(k) {
  for (const type of ["keyDown", "keyUp"])
    await cdp.send("Input.dispatchKeyEvent", { type, key: k, code: k, windowsVirtualKeyCode: CODES[k] ?? 0 });
}
async function shot(name) {
  if (!shots) return;
  const s = await cdp.send("Page.captureScreenshot", { format: "png" });
  writeFileSync(`${shots}/${name}.png`, Buffer.from(s.data, "base64"));
}

const overlaps = (a, b) => Math.min(a.right, b.right) - Math.max(a.x, b.x) > 1 && Math.min(a.bottom, b.bottom) - Math.max(a.y, b.y) > 1;

/* One page at one size. `deep` is the full fit; without it (a blank rock, where a charting page
 * grows its warnings) only the overlap and the video's size are asked. `line` says whether the
 * rock has a delivery (the line button then has to be there, and otherwise not), or leaves the
 * line button alone when it is undefined. */
async function fitAt(name, url, hash, w, h, { deep = true, line } = {}) {
  await open(at(url, hash), w, h);
  const where = `fit ${name} ${hash.slice(1)} ${w}x${h}`;
  const p = await page();
  if (deep) {
    if (p.sh > p.ih + 1) fail(`${where}: the page scrolls (${p.sh} > ${p.ih})`);
    if (p.sw > p.iw + 1) fail(`${where}: the page scrolls sideways (${p.sw} > ${p.iw})`);
  }
  const sels = ["#video", "#transport", "#houseCard", "#figCard", ...(name === "g" ? [] : ["#grading"])];
  const bs = {};
  for (const sel of sels) {
    const b = await box(sel);
    bs[sel] = b?.vis ? b : null;
    if (!bs[sel]) { if (deep || sel === "#video") fail(`${where}: ${sel} is missing`); }
    else if (deep && b.bottom > p.ih + 0.5) fail(`${where}: ${sel} ends at ${Math.round(b.bottom)}, below the fold at ${p.ih}`);
  }
  for (let i = 0; i < sels.length; i++) for (let j = i + 1; j < sels.length; j++) {
    const a = bs[sels[i]], b = bs[sels[j]];
    if (a && b && overlaps(a, b)) fail(`${where}: ${sels[i]} and ${sels[j]} overlap`);
  }
  const v = bs["#video"];
  if (v) {
    if (v.w < 560) fail(`${where}: the video is ${Math.round(v.w)} px wide`);
    if (deep && Math.abs(v.w / v.h / (16 / 9) - 1) > 0.02) fail(`${where}: the video is ${Math.round(v.w)}x${Math.round(v.h)}, not 16:9`);
  }
  if (line !== undefined) {
    const has = await vis("#lineOpen");
    if (line && !has) fail(`${where}: #lineOpen is absent on a rock with a delivery`);
    if (!line && has) fail(`${where}: #lineOpen is there on a rock with no delivery`);
  }
  const hs = await house();
  console.log(`${where}: #house ${hs ? `${hs.box} px tall, drawn ${hs.w}x${hs.drawn}` : "missing"}`);
  await shot(`fit-${name}-${hash.slice(1).replace(/[^\w]/g, "")}-${w}x${h}`);
}

try {

await check("fit", async () => {
  for (const [w, h] of SIZES) {
    for (const [name, url] of [["c", edit], ["s", view], ["g", review]]) await fitAt(name, url, ROCK, w, h, { line: true });
    await fitAt("c", edit, NO_LINE, w, h, { line: false });
  }
});

/* The names of the controls a finger has to hit, by id or by what they say. */
const TARGETS = `button, select, input, textarea, a, .shot, label.chk`;
await check("targets", async () => {
  for (const [w, h] of [[820, 1080], [744, 1050]]) {
    await open(at(edit, ROCK), w, h);
    const bad = await cdp.eval(`(() => {
      const name = e => e.id ? "#" + e.id
        : e.tagName.toLowerCase() + [...e.classList].map(c => "." + c).join("")
          + ((e.textContent || e.value || e.getAttribute("aria-label") || e.title || "").trim()
             ? \` "\${(e.textContent || e.value || e.getAttribute("aria-label") || e.title).trim().slice(0, 18)}"\` : "");
      const out = [];
      for (const e of document.querySelectorAll(${JSON.stringify(TARGETS)})) {
        if (e.closest("#video") || e.closest("dialog:not([open])")) continue;
        // A check-box inside its label.chk is the label's to be: the label is what is tapped.
        if (e.matches("input[type=checkbox], input[type=radio]") && e.closest("label.chk")) continue;
        const r = e.getBoundingClientRect(), cs = getComputedStyle(e);
        if (e.hidden || !r.width || !r.height || cs.visibility === "hidden" || cs.display === "none") continue;
        if (r.width < 36 || r.height < 40) out.push(\`\${name(e)} \${Math.round(r.width)}x\${Math.round(r.height)}\`);
      }
      return out; })()`);
    if (bad.length) fail(`targets ${w}x${h}: ${bad.length} under 36 x 40: ${bad.join(", ")}`);
  }
});

await check("menu", async () => {
  await open(at(edit, ROCK), 820, 1080);
  await tap("#menuBtn");
  await sleep(300);
  if ((await data("menu")) !== "open") fail("menu: #menuBtn did not set body[data-menu=open]");
  for (const sel of ["#game", "#copyLink", "#flagBtn", "#link"])
    if (!(await vis(sel))) fail(`menu: ${sel} is not visible in the open menu`);
  const hd = await box("header");
  for (const sel of ["#end", "#thinkBtn", "#reportBtn"]) {
    const b = await box(sel);
    if (!b?.vis) fail(`menu: ${sel} is not visible`);
    else if (!hd || b.x < hd.x - 1 || b.right > hd.right + 1 || b.y < hd.y - 1 || b.bottom > hd.bottom + 1)
      fail(`menu: ${sel} is not inside the header's box`);
  }
  await shot("menu-open-820x1080");
  await tap("#leadin");
  await sleep(300);
  if ((await data("menu")) !== "open") fail("menu: tapping #leadin closed the dropdown");
  await tap("#menuBtn");
  await sleep(300);
  if ((await data("menu")) === "open") fail("menu: #menuBtn again did not close it");
});

await check("next", async () => {
  await open(at(edit, "#e=1&s=16"), 820, 1080);
  await tap("#nextRock");
  await sleep(400);
  if ((await hashRock()) !== "e=2&s=1") fail(`next: Next rock from e1 s16 went to ${await hashRock()}, not e=2&s=1`);
  await open(at(edit, "#e=5&s=16"), 820, 1080);
  const b = await box("#nextRock");
  if (!b?.vis) fail("next: #nextRock is missing on the last rock");
  else if (!(await cdp.eval(`document.getElementById("nextRock").disabled`))) fail("next: #nextRock is not disabled on the last rock (e5 s16)");
});

await check("sheets", async () => {
  await open(at(edit, ROCK), 820, 1080);
  await tap("#missBtn");
  await sleep(300);
  if (!(await isOpen("missSheet"))) fail("sheets: #missBtn did not open #missSheet");
  else {
    const m = await box("#missSheet");
    if (m.y > 120) fail(`sheets: #missSheet's top is at ${Math.round(m.y)}, below 120`);
    await shot("sheets-miss-820x1080");
    const before = (await page()).hash;
    await key("ArrowRight");
    await sleep(300);
    if ((await page()).hash !== before) fail("sheets: → moved the rock behind #missSheet");
    await tapExpr(`document.querySelector("#missSheet .reasons button")`, "the first miss reason");
    await sleep(200);
    await closeSheet("missSheet");
    const mb = await text("#missBtn");
    if (!(mb ?? "").startsWith("Miss: ")) fail(`sheets: #missBtn reads ${JSON.stringify(mb)} after a reason, not "Miss: …"`);
  }
  await tap("#noteBtn");
  await sleep(300);
  await tap("#note");
  await cdp.send("Input.insertText", { text: "ok" });
  await closeSheet("noteSheet");
  const nb = await text("#noteBtn");
  if (nb !== "Note: ok") fail(`sheets: #noteBtn reads ${JSON.stringify(nb)} after typing "ok", not "Note: ok"`);
  await tap("#orderBtn");
  await sleep(300);
  if (!(await vis("#orderSheet #moveBefore"))) fail("sheets: #orderSheet has no visible #moveBefore");
  await closeSheet("orderSheet");

  await open(at(view, ROCK), 820, 1080);
  await tap("#missBtn");
  await sleep(300);
  if (!(await isOpen("missSheet"))) fail("sheets: /s/ #missBtn did not open #missSheet");
  else if (!(await cdp.eval(`(() => { const e = document.querySelector("#missSheet #missReason"); return !!e && (e.readOnly || e.disabled); })()`)))
    fail("sheets: /s/ #missSheet has no read-only #missReason");
  if (await vis("#orderBtn")) fail("sheets: /s/ shows #orderBtn");
});

await check("rotate", async () => {
  await open(at(edit, ROCK), 820, 1080);
  await tap("#noteBtn");
  await sleep(300);
  await tap("#note");
  await cdp.send("Input.insertText", { text: "turned" });
  await sleep(300);
  await setSize(1080, 820);
  await waitFor(`!!document.getElementById("chart")`);
  await sleep(400);
  const v = await cdp.eval(`document.getElementById("note")?.value ?? null`);
  if (v !== "turned") fail(`rotate: the note reads ${JSON.stringify(v)} after turning to 1080x820, not "turned"`);
  if (await cdp.eval(`!!document.querySelector("dialog[open]")`)) fail("rotate: a sheet is still open after turning to 1080x820");
});

await check("house", async () => {
  await open(at(edit, ROCK), 820, 1080);
  const card = await box("#houseCard");
  await tap("#house");
  await sleep(400);
  if ((await data("house")) !== "edit") fail("house: tapping #house did not set body[data-house=edit]");
  const hb = await box("#house");
  if (!card || !hb || hb.h < 1.8 * card.h)
    fail(`house: the open #house is ${Math.round(hb?.h ?? 0)} px tall, under 1.8 x the card's ${Math.round(card?.h ?? 0)}`);
  for (const sel of ["#pickRed", "#houseDone"]) if (!(await vis(sel))) fail(`house: ${sel} is not visible in the editor`);
  await shot("house-open-820x1080");
  await setSize(820, 1010);           // Safari's bar
  await sleep(400);
  if ((await data("house")) !== "edit") fail("house: the editor closed when the height went to 1010");
  await tap("#houseDone");
  await sleep(400);
  if ((await data("house")) === "edit") fail("house: #houseDone did not close the editor");

  await open(at(view, ROCK), 820, 1080);
  await tap("#house");
  await sleep(400);
  if ((await data("house")) === "edit") fail("house: /s/ tapping #house opened the editor");
  for (const sel of ["#houseOpen", "#markCharted"]) if (await vis(sel)) fail(`house: /s/ shows ${sel}`);
});

await check("blanks", async () => {
  if (!blanksUrl) { console.log("skip blanks (no --blanks)"); return; }
  await open(at(blanksUrl, "#e=2&s=5"), 820, 1080);
  // A rock the detector never saw stays "?" after it is charted, so the chart's overrides
  // are asked too: a fresh devserve has none.
  const prior = await fetch(sans(blanksUrl).replace(/\/?$/, "/") + "overrides.json").then(r => (r.ok ? r.json() : {})).catch(() => ({}));
  const blank = await cdp.eval(`(() => { const s = document.querySelector("#shots .shot.sel"); return s ? s.classList.contains("unknown") : null; })()`);
  if (blank !== true || Object.keys(prior).length) throw new Error("rock e2 s5 is already charted -- start a fresh devserve on the blanks timeline");
  await fitAt("c", blanksUrl, "#e=2&s=5", 820, 1080, { deep: false });
  await tap("#house");
  await sleep(400);
  await tap("#markCharted");
  await sleep(500);
  if ((await hashRock()) !== "e=2&s=6") fail(`blanks: Mark charted went to ${await hashRock()}, not e=2&s=6`);
  if ((await data("house")) !== "edit") fail("blanks: the editor closed after the first Mark charted");
  await tap("#markCharted");
  await sleep(500);
  if ((await data("house")) === "edit") fail("blanks: the second Mark charted did not close the editor");
  if ((await hashRock()) !== "e=2&s=6") fail(`blanks: the second Mark charted moved to ${await hashRock()}, not staying at e=2&s=6`);
});

await check("doubles", async () => {
  if (!doublesUrl) { console.log("skip doubles (no --doubles)"); return; }
  // The view-only link is in the edit page's own document.
  const doc = await (await fetch(sans(doublesUrl).replace(/\/?$/, "/") + "timeline.json")).json();
  const dview = doc.chart?.share_url;
  if (!dview) throw new Error("the doubles edit page names no view-only link");
  for (const [name, url] of [["c", doublesUrl], ["s", dview]]) {
    await fitAt(name, url, "#e=2&s=3", 820, 1080);
    const eb = await box("#endBox");
    if (!eb?.vis) fail(`doubles: /${name}/ #endBox is missing`);
    else if (eb.h > 56) fail(`doubles: /${name}/ #endBox is ${Math.round(eb.h)} px tall, over 56`);
  }
  await open(at(doublesUrl, "#e=2&s=3"), 820, 1080);
  await tap("#swapBtn");
  await sleep(300);
  if (!(await isOpen("swapSheet"))) fail("doubles: #swapBtn did not open #swapSheet");
  else {
    const n = await cdp.eval(`document.querySelectorAll("#swapSheet input[type=checkbox]").length`);
    if (n !== 2) fail(`doubles: #swapSheet has ${n} check-boxes, not 2`);
    await closeSheet("swapSheet");
  }
});

await check("dialogs", async () => {
  for (const [w, h] of [[820, 1080], [744, 1050]]) {
    const where = `dialogs ${w}x${h}`;
    try {
    await open(at(edit, ROCK), w, h);
    await tap("#lineOpen");
    await sleep(500);
    if (!(await isOpen("deliveryDialog"))) { fail(`${where}: #lineOpen did not open #deliveryDialog`); continue; }
    await shot(`dialogs-delivery-${w}x${h}`);
    // A text is as big as its font-size attribute, scaled the way its svg is drawn (the smaller
    // of the two axes, as house() does: the viewBox letterboxes). The chart's texts may not be
    // there yet, so wait for one, and count what was measured: nothing measured is a failure.
    await waitFor(`!!document.querySelector("#deliveryDialog svg text")`, 3000).catch(() => {});
    const sized = await cdp.eval(`(() => {
      const small = []; let n = 0;
      for (const svg of document.querySelectorAll("#deliveryDialog svg")) {
        const r = svg.getBoundingClientRect(), vb = svg.viewBox?.baseVal;
        if (!r.width || !r.height) continue;
        const k = vb && vb.width && vb.height ? Math.min(r.width / vb.width, r.height / vb.height) : 1;
        for (const t of svg.querySelectorAll("text")) {
          const fs = parseFloat(t.getAttribute("font-size")) || parseFloat(getComputedStyle(t).fontSize);
          if (!(fs > 0)) continue;
          n++;
          if (fs * k < 11) small.push(\`"\${t.textContent.trim().slice(0, 14)}" \${(fs * k).toFixed(1)} px\`);
        }
      }
      return { n, small }; })()`);
    if (!sized.n) fail(`${where}: no chart text measured`);
    if (sized.small.length) fail(`${where}: ${sized.small.length} text(s) under 11 px: ${sized.small.join(", ")}`);
    const esc = await text("#deliveryDialog .dd-esc");
    if (esc !== "Tap outside to close") fail(`${where}: .dd-esc reads ${JSON.stringify(esc)}, not "Tap outside to close"`);
    await tapAt(8, h - 8);              // the backdrop
    await sleep(400);
    if (await isOpen("deliveryDialog")) fail(`${where}: a tap on the backdrop did not close #deliveryDialog`);

    await tap("#thinkBtn");
    await sleep(500);
    if (!(await isOpen("thinkingDialog"))) { fail(`${where}: #thinkBtn did not open #thinkingDialog`); continue; }
    await shot(`dialogs-thinking-${w}x${h}`);
    if (!(await box("#thinkingDialog rect.bar"))) { await tap("#thinkingDialog .linky").catch(() => {}); await sleep(400); }
    // The tallest bar that is not this rock's: a rock to go to, big enough to hit.
    const target = await cdp.eval(`(() => {
      const you = document.querySelector("#thinkingDialog line.you")?.getBoundingClientRect();
      const bars = [...document.querySelectorAll("#thinkingDialog rect.bar")]
        .map(b => ({ b, r: b.getBoundingClientRect() })).filter(x => x.r.height > 3);
      const mid = x => x.r.x + x.r.width / 2;
      let mine = null;
      if (you) for (const x of bars) if (!mine || Math.abs(mid(x) - you.x) < Math.abs(mid(mine) - you.x)) mine = x;
      const others = bars.filter(x => x !== mine);
      others.sort((p, q) => q.r.height - p.r.height);
      const x = others[0]; return x ? { x: mid(x), y: x.r.y + x.r.height / 2 } : null; })()`);
    if (!target) { fail(`${where}: no bar in #thinkingDialog to tap`); continue; }
    const before = (await page()).hash;
    await tapAt(target.x, target.y);
    await sleep(600);
    if (await isOpen("thinkingDialog")) fail(`${where}: a bar did not close #thinkingDialog`);
    if ((await page()).hash === before) fail(`${where}: a bar did not go to its rock`);
    } catch (err) { fail(`${where}: ${err.message}`); }   // the other size still runs
  }
});

await check("unchanged", async () => {
  for (const [w, h, touch] of [[1180, 820, true], [820, 1080, false], [660, 1000, true]]) {
    const where = `unchanged ${w}x${h}${touch ? " touch" : " mouse"}`;
    await open(at(edit, ROCK), w, h, touch);
    if (await vis("#figCard")) fail(`${where}: #figCard is there, in a layout that was not to change`);
    for (const sel of ["#detailCard", "#chart"]) if (!(await vis(sel))) fail(`${where}: ${sel} is missing`);
  }
});

} finally {
  // The same Chrome serves deskcheck and domsnap next: no touch, no metrics override.
  await cdp.send("Emulation.setTouchEmulationEnabled", { enabled: false }).catch(() => {});
  await cdp.send("Emulation.clearDeviceMetricsOverride").catch(() => {});
}

if (fails.length) { console.log(`tabletcheck: ${fails.length} failed`); process.exit(1); }
console.log("tabletcheck: all good");
process.exit(0);
