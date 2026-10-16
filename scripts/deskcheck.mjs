/* The desktop at 1080p (spec 2026-10-15): from 1700 px wide, charting a rock
 * needs no scroll, and below that the page is the one it always was.
 *
 *   PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python scripts/devserve.py TIMELINE
 *   google-chrome --headless=new --disable-gpu --no-sandbox --hide-scrollbars \
 *     --remote-debugging-port=9333 --user-data-dir=$(mktemp -d) about:blank &
 *   CDP_PORT=9333 node scripts/deskcheck.mjs EDIT_URL VIEW_URL REVIEW_URL [--only=…] [--shots=DIR]
 *
 * Give each URL a rock with a delivery: #e=2&s=4 for tl-s_0cYt.json.
 * Checks, by name:
 *   fit       five screen sizes x three surfaces: no scroll, every card above the fold
 *   short     1920x768: the page may scroll, but no card overlaps another
 *   narrow    1699x900: the old layout, untouched
 *   resize    a note typed at 1920 survives crossing to 1600
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
const [edit, view, review] = args.filter(a => !a.startsWith("--"));
if (!review) {
  console.error("usage: node scripts/deskcheck.mjs EDIT VIEW REVIEW [--only=fit,short,narrow,resize,delivery,thinking] [--shots=DIR]");
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

await check("fit", async () => {
  // 1700 is the gate itself: the narrowest the wide layout gets, header included.
  for (const [w, h] of [[1700, 945], [1920, 945], [1920, 860], [1920, 1032], [2560, 1305]]) {
    for (const [name, url] of [["c", edit], ["s", view], ["g", review]]) {
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
      const v = await box("#video");
      if (v && v.w < 640) fail(`${at}: the video is ${Math.round(v.w)} px wide`);
      await shot(`fit-${name}-${w}x${h}`);
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
