/* The desktop at 1080p (spec 2026-10-15): from 1700 px wide, charting a rock
 * needs no scroll, and below that the page is the one it always was.
 *
 *   PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python scripts/devserve.py TIMELINE
 *   google-chrome --headless=new --disable-gpu --no-sandbox --hide-scrollbars \
 *     --remote-debugging-port=9333 --user-data-dir=$(mktemp -d) about:blank &
 *   CDP_PORT=9333 node scripts/deskcheck.mjs EDIT_URL VIEW_URL REVIEW_URL [--only=…] [--shots=DIR]
 *     [--blank=EDIT_URL#e=..&s=..]…
 *
 * Give each URL a rock with a delivery: #e=2&s=4 for tl-s_0cYt.json.
 * Checks, by name:
 *   fit       five screen sizes x three surfaces: no scroll, every card above the fold,
 *             the control row and the bar inside the video's card, each bar label on its control's line;
 *             and the same on each --blank rock (a charting page on a blank: its warnings, and a never-seen
 *             rock's figures), whose house must be drawn at least 380 px tall at 1920x945. Prints the
 *             house's height at every size.
 *   short     1920x768: the page may scroll, but no card overlaps another
 *   narrow    1699x900: the old layout, untouched
 *   resize    a note typed at 1920 survives crossing to 1600 and keeps the caret: a digit typed
 *             next goes into the note, not to the score
 *   delivery  the line chart opens the large view; keys stay out; it closes three ways
 *   thinking  the header button opens it; a bar goes to its rock and closes it
 * devserve is in memory, so the note the resize check types goes nowhere.
 * Exits 1 naming each failure. */
import { mkdirSync, writeFileSync } from "node:fs";
import { attach } from "./cdp.mjs";

const args = process.argv.slice(2);
const opt = name => (args.find(a => a.startsWith(`--${name}=`)) || "").slice(name.length + 3);
const only = opt("only").split(",").filter(Boolean);
const shots = opt("shots");
// Repeatable: each is one charting URL on a blank rock (quote it: it has a # and a &).
const blanks = args.filter(a => a.startsWith("--blank=")).map(a => a.slice("--blank=".length));
const [edit, view, review] = args.filter(a => !a.startsWith("--"));
if (!review) {
  console.error("usage: node scripts/deskcheck.mjs EDIT VIEW REVIEW [--only=fit,short,narrow,resize,delivery,thinking] [--shots=DIR] [--blank=EDIT#e=..&s=..]…");
  process.exit(2);
}
const NAMES = ["fit", "short", "narrow", "resize", "delivery", "thinking"];
const unknown = only.filter(n => !NAMES.includes(n));
if (unknown.length) {
  console.error(`unknown check ${unknown.join(", ")}; usage: node scripts/deskcheck.mjs EDIT VIEW REVIEW [--only=${NAMES.join(",")}] [--shots=DIR]`);
  process.exit(2);
}
if (shots) mkdirSync(shots, { recursive: true });
const want = name => !only.length || only.includes(name);
const cdp = await attach("");
const fails = [];
const fail = msg => { fails.push(msg); console.log(`FAIL ${msg}`); };
// An exception (a timeout, a missing page) fails that check and the run goes on.
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

async function size(width, height) {
  await cdp.send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: false });
}

async function open(url, width, height) {
  await size(width, height);
  await cdp.send("Page.navigate", { url: "about:blank" });
  await sleep(200);
  await cdp.send("Page.navigate", { url });
  await waitFor(`!!document.getElementById("playCard") && !!document.querySelector("#shots .shot")`);
  await sleep(700);    // the line chart measures its box after the first paint
}

const box = sel => cdp.eval(`(() => { const e = document.querySelector(${JSON.stringify(sel)});
  if (!e) return null; const r = e.getBoundingClientRect();
  return { x: r.x, y: r.y, w: r.width, h: r.height, bottom: r.bottom, right: r.right }; })()`);
const page = () => cdp.eval(`({ sh: document.documentElement.scrollHeight, sw: document.documentElement.scrollWidth,
  ih: innerHeight, iw: innerWidth, hash: location.hash })`);
const isOpen = id => cdp.eval(`!!document.querySelector("#${id}[open]")`);
/* The house as drawn: its <svg>'s box, and the drawing in it, which the
 * viewBox letterboxes (xMidYMid meet). */
const house = () => cdp.eval(`(() => {
  const e = document.getElementById("house"); if (!e) return null;
  const r = e.getBoundingClientRect(), vb = e.viewBox.baseVal;
  const k = vb && vb.width && vb.height ? Math.min(r.width / vb.width, r.height / vb.height) : 0;
  return { box: Math.round(r.height), drawn: Math.round(vb.height * k), w: Math.round(vb.width * k) }; })()`);
const HOUSE_MIN_PX = 380;
/* Every control-row and bar item (and each item's own children) that has a
 * box but lies outside #playCard's, by more than a pixel, named. */
const strays = () => cdp.eval(`(() => {
  const card = document.getElementById("playCard")?.getBoundingClientRect();
  if (!card) return [];
  const name = e => e.id ? "#" + e.id
    : e.tagName.toLowerCase() + [...e.classList].map(c => "." + c).join("") + (e.textContent.trim() ? \` "\${e.textContent.trim().slice(0, 16)}"\` : "");
  const out = [];
  for (const e of document.querySelectorAll("#transport > *, #transport > * > *, .gbar-row > *, .gbar-row > * > *")) {
    const r = e.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    if (r.left < card.left - 1 || r.right > card.right + 1 || r.top < card.top - 1 || r.bottom > card.bottom + 1)
      out.push(\`\${name(e)} at x \${Math.round(r.left)}-\${Math.round(r.right)}, y \${Math.round(r.top)}-\${Math.round(r.bottom)} is outside #playCard \`
             + \`(x \${Math.round(card.left)}-\${Math.round(card.right)}, y \${Math.round(card.top)}-\${Math.round(card.bottom)})\`);
  }
  return out; })()`);
/* Every bar label on a different line from the control after it. */
const stranded = () => cdp.eval(`(() => {
  const out = [];
  for (const l of document.querySelectorAll(".gbar .gbar-lbl")) {
    const c = l.nextElementSibling, a = l.getBoundingClientRect(), b = c?.getBoundingClientRect();
    if (!b || !a.height || !b.height) continue;
    const mid = y => (y.top + y.bottom) / 2;
    if (Math.abs(mid(a) - mid(b)) > b.height / 2)
      out.push(\`the "\${l.textContent.trim()}" label (y \${Math.round(a.top)}) is not on the line of \${c.id ? "#" + c.id : c.tagName.toLowerCase()} (y \${Math.round(b.top)})\`);
  }
  return out; })()`);

async function clickAt(x, y) {
  for (const type of ["mousePressed", "mouseReleased"])
    await cdp.send("Input.dispatchMouseEvent", { type, x, y, button: "left", clickCount: 1 });
}
async function click(sel) {
  const b = await box(sel);
  if (!b || !b.w) return false;
  await clickAt(b.x + b.w / 2, b.y + b.h / 2);
  return true;
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

/* One page at one size: no scroll, every card above the fold, nothing in
 * the control row or the bar outside the video's card. */
async function fitAt(name, url, w, h) {
  await open(url, w, h);
  const at = `fit ${name} ${w}x${h}`;
  const p = await page();
  if (p.sh > p.ih + 1) fail(`${at}: the page scrolls (${p.sh} > ${p.ih})`);
  if (p.sw > p.iw + 1) fail(`${at}: the page scrolls sideways (${p.sw} > ${p.iw})`);
  const cards = ["#playCard", "#houseCard", "#figCard", "#lineCard", ...(name === "g" ? [] : ["#grading"])];
  for (const sel of cards) {
    const b = await box(sel);
    if (!b || !b.h) fail(`${at}: ${sel} is missing`);
    else if (b.bottom > p.ih + 0.5) fail(`${at}: ${sel} ends at ${Math.round(b.bottom)}, below the fold at ${p.ih}`);
  }
  if (name === "g") {
    const g = await box("#grading");
    if (g && g.w && g.h) fail(`${at}: #grading is visible on the review page`);
  }
  for (const sel of ["#detailCard", "#chart"]) if (await box(sel)) fail(`${at}: ${sel} is in the wide layout`);
  // The control row and the bar wrap at the narrow end; nothing in them may run past the card.
  for (const s of await strays()) fail(`${at}: ${s}`);
  for (const s of await stranded()) fail(`${at}: ${s}`);
  const v = await box("#video");
  if (v && v.w < 640) fail(`${at}: the video is ${Math.round(v.w)} px wide`);
  const hs = await house();
  console.log(`${at}: #house ${hs ? `${hs.box} px tall, drawn ${hs.w}x${hs.drawn}` : "missing"}`);
  await shot(`fit-${name}-${w}x${h}`);
  return hs;
}

await check("fit", async () => {
  // 1700 is the gate itself: the narrowest the wide layout gets, header included.
  const SIZES = [[1700, 945], [1920, 945], [1920, 860], [1920, 1032], [2560, 1305]];
  for (const [w, h] of SIZES)
    for (const [name, url] of [["c", edit], ["s", view], ["g", review]]) await fitAt(name, url, w, h);
  // A blank is where stones get placed: the house must keep its room under
  // the warnings it brings.
  for (const [i, url] of blanks.entries()) {
    for (const [w, h] of SIZES) {
      const hs = await fitAt(`blank${i + 1}`, url, w, h);
      if (w === 1920 && h === 945 && !(hs && hs.drawn >= HOUSE_MIN_PX))
        fail(`fit blank${i + 1} ${w}x${h}: the house is drawn ${hs ? hs.drawn : 0} px tall, under ${HOUSE_MIN_PX}`);
    }
  }
});

await check("short", async () => {
  await open(edit, 1920, 768);
  const p = await page();
  if (p.sw > p.iw + 1) fail("short: the page scrolls sideways");
  const sels = ["#playCard", "#houseCard", "#figCard", "#lineCard"];
  const bs = [];
  for (const sel of sels) bs.push(await box(sel));
  for (let i = 0; i < sels.length; i++) for (let j = i + 1; j < sels.length; j++) {
    const a = bs[i], b = bs[j];
    if (!a || !b) continue;
    const ox = Math.min(a.right, b.right) - Math.max(a.x, b.x), oy = Math.min(a.bottom, b.bottom) - Math.max(a.y, b.y);
    if (ox > 1 && oy > 1) fail(`short: ${sels[i]} and ${sels[j]} overlap`);
  }
  const v = await box("#video");
  if (v && v.w < 640) fail(`short: the video is ${Math.round(v.w)} px wide`);
  await shot("short-c-1920x768");
});

await check("narrow", async () => {
  await open(edit, 1699, 900);
  for (const sel of ["#detailCard", "#chart", "#transport", "#playback"]) {
    const b = await box(sel);
    if (!b || !b.h) fail(`narrow: ${sel} is missing below 1700 px`);
  }
  for (const sel of ["#figCard", "#lineCard", "#thinkBtn", "#rockName", "#playGear"])
    if (await box(sel)) fail(`narrow: ${sel} is in the old layout`);
});

await check("resize", async () => {
  await open(edit, 1920, 945);
  // Without these the check passes when the layout never switches at all.
  if (!(await box("#figCard")) || (await box("#detailCard"))) fail("resize: 1920 px is not the wide layout, so crossing proves nothing");
  const before = (await page()).hash;
  if (!(await click("#note"))) fail("resize: no note field in the bar");
  else {
    await cdp.send("Input.insertText", { text: "resize check" });
    await sleep(400);                  // past the 150 ms commit
    await size(1600, 900);
    await sleep(900);
    if (!(await box("#detailCard")) || (await box("#figCard"))) fail("resize: 1600 px is not the old layout, so the layout did not switch");
    const v = await cdp.eval(`document.getElementById("note")?.value`);
    if (v !== "resize check") fail(`resize: the note reads ${JSON.stringify(v)} after crossing 1700 px`);
    if ((await page()).hash !== before) fail("resize: the rock changed when the layout did");
    // The caret came across with it: a digit typed now is the note's, not the score's.
    const active = await cdp.eval("document.activeElement?.id || document.activeElement?.tagName");
    if (active !== "note") fail(`resize: the caret is on ${active} after crossing 1700 px, not #note`);
    const score = () => cdp.eval(`document.querySelector("#scoreBtns button.on")?.textContent ?? null`);
    const was = await score(), d = was === "1" ? "2" : "1";   // a digit that would change it
    for (const type of ["keyDown", "keyUp"])
      await cdp.send("Input.dispatchKeyEvent", { type, key: d, code: `Digit${d}`, text: type === "keyDown" ? d : undefined,
                                                 windowsVirtualKeyCode: 48 + +d });
    await sleep(400);
    const typed = await cdp.eval(`document.getElementById("note")?.value`);
    if (typed !== `resize check${d}`) fail(`resize: a ${d} typed after crossing left the note reading ${JSON.stringify(typed)}`);
    const now = await score();
    if (now !== was) fail(`resize: a ${d} typed after crossing set the score (${was} -> ${now})`);
  }
});

await check("delivery", async () => {
  await open(edit, 1920, 945);
  const before = (await page()).hash;
  await click("#lineCard svg.linechart");
  await sleep(400);
  if (!(await isOpen("deliveryDialog"))) fail("delivery: a click on the line chart did not open it");
  else {
    await shot("delivery-c-1920x945");
    if (!(await box("#deliveryDialog svg.spd"))) fail("delivery: no speed trace for a rock with a delivery");
    await key("ArrowRight");
    await sleep(400);
    if ((await page()).hash !== before) fail("delivery: → moved the rock behind the dialog");
    await key("ArrowLeft");
    await sleep(400);
    if ((await page()).hash !== before) fail("delivery: ← moved the rock behind the dialog");
    await key("Escape");
    await sleep(400);
    if (await isOpen("deliveryDialog")) fail("delivery: Escape did not close it");
    // The control: with it closed the same key must step the rock, or the
    // checks above saw nothing.
    await key("ArrowRight");
    await sleep(400);
    if ((await page()).hash === before) fail("delivery: → did not step the rock with the dialog closed (key check proves nothing)");
  }
  await click("#lineExpand");
  await sleep(400);
  if (!(await isOpen("deliveryDialog"))) fail("delivery: Expand did not open it");
  await clickAt(4, 4);                 // the backdrop
  await sleep(400);
  if (await isOpen("deliveryDialog")) fail("delivery: a click outside did not close it");
  await click("#lineExpand");
  await sleep(400);
  await click("#deliveryDialog .dd-close");
  await sleep(400);
  if (await isOpen("deliveryDialog")) fail("delivery: ✕ did not close it");
  for (const [name, url] of [["s", view], ["g", review]]) {
    await open(url, 1920, 945);
    await click("#lineExpand");
    await sleep(400);
    if (!(await isOpen("deliveryDialog"))) fail(`delivery: /${name}/ does not open it`);
    await key("Escape");
  }
});

await check("thinking", async () => {
  for (const [name, url] of [["c", edit], ["s", view], ["g", review]]) {
    await open(url, 1920, 945);
    if (!(await click("#thinkBtn"))) { fail(`thinking: /${name}/ has no Thinking button`); continue; }
    await sleep(400);
    if (!(await isOpen("thinkingDialog"))) { fail(`thinking: /${name}/ button did not open it`); continue; }
    if (name !== "c") { await key("Escape"); continue; }
    await shot("thinking-c-1920x945");
    if (!(await box("#thinkingDialog rect.bar"))) { await click("#thinkingDialog .linky"); await sleep(400); }
    // The tallest bar that is not this rock's: a rock to go to, big enough to
    // hit. This rock's bar is the one the "you" marker sits on.
    const target = await cdp.eval(`(() => {
      const you = document.querySelector("#thinkingDialog line.you")?.getBoundingClientRect();
      const bars = [...document.querySelectorAll("#thinkingDialog rect.bar")]
        .map(b => ({ b, r: b.getBoundingClientRect() })).filter(x => x.r.height > 3);
      const mid = x => x.r.x + x.r.width / 2;
      let mine = null;
      if (you) for (const x of bars) if (!mine || Math.abs(mid(x) - you.x) < Math.abs(mid(mine) - you.x)) mine = x;
      const others = bars.filter(x => x !== mine);
      others.sort((p, q) => q.r.height - p.r.height);
      const x = others[0]; return x ? { x: mid(x), y: x.r.y + x.r.height / 2, you: !!you } : null; })()`);
    const before = (await page()).hash;
    if (!target) { fail("thinking: no bar to click"); continue; }
    if (!target.you) fail("thinking: no marker on the chart, so this rock's bar cannot be excluded");
    await clickAt(target.x, target.y);
    await sleep(600);
    if (await isOpen("thinkingDialog")) fail("thinking: a bar did not close it");
    if ((await page()).hash === before) fail("thinking: a bar did not go to its rock");
  }
});

if (fails.length) { console.log(`deskcheck: ${fails.length} failed`); process.exit(1); }
console.log("deskcheck: all good");
process.exit(0);
