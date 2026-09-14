/* Photograph the rendered DOM across every state the stylesheet cares about.
 *
 *   node scripts/domsnap.mjs <edit-url> <view-url> <review-url> --out FILE
 *   node scripts/domsnap.mjs --diff BEFORE.json AFTER.json
 *
 * Why this exists: style.css is 548 lines whose selectors ARE the application's
 * state machine -- body[data-mode|peek|sheet|house|menu], body.reporting,
 * `#save:not(.bad)`, and nine elements that read-only mode hides with the
 * `hidden` attribute so the phone rules can say `:not([hidden])`. Nothing in
 * 1483 tests looks at the rendered DOM. Porting to React means reproducing all
 * of it from JSX, and a silent regression there is a layout that breaks on a
 * phone at a rink, three states deep, where nobody will see it until a charter
 * cannot reach a control.
 *
 * So: capture the old page, port, capture the new one, diff. Anything that
 * moves is either intended or a bug, and both are worth looking at.
 *
 * Every state is reached by CLICKING what a person would click, never by
 * poking internals -- otherwise the script would only work against the vanilla
 * build and would be useless for the thing it exists to check.
 */
import { readFile, writeFile } from "node:fs/promises";
import { attach } from "./cdp.mjs";

const sleep = ms => new Promise(r => setTimeout(r, ms));

const DESKTOP = { width: 1280, height: 900 };
const PHONE = { width: 390, height: 844 };

/* Real mouse input at the element's centre, not el.click(): SVG elements have
 * no click() at all (the house is an <svg>), and the house editor listens for
 * pointerdown/up, which only a dispatched input event produces.
 *
 * Returns false when the control is missing, hidden, disabled or has no box --
 * recorded rather than ignored, so a state whose entry click did nothing is
 * visibly `reached: false` instead of silently duplicating the base state. */
const BOX = sel => `(() => {
  const el = document.querySelector(${JSON.stringify(sel)});
  if (!el || el.hidden || el.disabled) return null;
  const r = el.getBoundingClientRect();
  if (!r.width || !r.height) return null;
  return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
})()`;

async function clickOn(cdp, sel) {
  const at = await cdp.eval(BOX(sel));
  if (!at) return false;
  const base = { ...at, button: "left", clickCount: 1, buttons: 1 };
  await cdp.send("Input.dispatchMouseEvent", { ...base, type: "mousePressed" });
  await cdp.send("Input.dispatchMouseEvent", { ...base, type: "mouseReleased", buttons: 0 });
  return true;
}

const STATES = [
  { name: "desktop/edit", url: "edit", size: DESKTOP, steps: [] },
  { name: "desktop/edit/shot2", url: "edit", size: DESKTOP,
    steps: ["#shots > *:nth-child(2)"] },
  { name: "desktop/edit/scored", url: "edit", size: DESKTOP,
    steps: ['#scoreBtns button[data-v="3"]'] },
  { name: "desktop/edit/report", url: "edit", size: DESKTOP, steps: ["#reportBtn"] },
  { name: "desktop/edit/queue-open", url: "edit", size: DESKTOP,
    steps: ["#queueBox > summary"] },
  { name: "desktop/edit/clock-open", url: "edit", size: DESKTOP,
    steps: ["#clockBox > summary"] },
  { name: "desktop/view", url: "view", size: DESKTOP, steps: [] },
  { name: "desktop/view/report", url: "view", size: DESKTOP, steps: ["#reportBtn"] },
  { name: "desktop/review", url: "review", size: DESKTOP, steps: [] },
  { name: "desktop/review/clock-open", url: "review", size: DESKTOP,
    steps: ["#clockBox > summary"] },
  { name: "phone/edit", url: "edit", size: PHONE, steps: [] },
  { name: "phone/edit/sheet-open", url: "edit", size: PHONE, steps: ["#sheetHandle"] },
  { name: "phone/edit/house", url: "edit", size: PHONE, steps: ["#house"] },
  { name: "phone/edit/menu", url: "edit", size: PHONE, steps: ["#menuBtn"] },
  { name: "phone/edit/report", url: "edit", size: PHONE, steps: ["#menuBtn", "#reportBtn"] },
  { name: "phone/view", url: "view", size: PHONE, steps: [] },
  { name: "phone/review", url: "review", size: PHONE, steps: [] },
];

/* Normalise away everything that legitimately differs run to run, so a diff
 * shows structure and not noise: the clock in the save pill, the YouTube
 * iframe the player builds for itself, blob/object URLs, and whitespace.
 *
 * Three things are normalised specifically so the vanilla page and the React
 * one can be compared at all, none of which is a real difference:
 *   - #root is unwrapped. React needs a mount container; `display:contents`
 *     means it generates no box, and keeping it would shift every line.
 *   - adjacent text nodes are merged. `sheet {n} ·` is one text node when
 *     written with innerHTML and three when rendered from JSX.
 *   - #report is captured separately, because it is always rendered now
 *     (printing a closed report must not print a blank page) where before it
 *     was built only when opened. Comparing it inside the tree would drown
 *     every other state in an intended change.
 */
const SERIALISE = `(() => {
  const VOLATILE = [
    [/saved \\d{2}:\\d{2}/g, "saved HH:MM"],
    [/https:\\/\\/www\\.youtube\\.com\\/embed\\/[^"']*/g, "YOUTUBE_EMBED"],
    [/blob:[^"']*/g, "BLOB_URL"],
  ];
  const skip = new Set(["IFRAME", "SCRIPT"]);
  const transparent = el => el.id === "root";

  function kids(el, depth) {
    const out = [];
    let text = "";
    const flush = () => {
      const t = text.replace(/\\s+/g, " ").trim();
      if (t) out.push("  ".repeat(depth + 1) + "#text " + t);
      text = "";
    };
    for (const n of el.childNodes) {
      if (n.nodeType === 3) { text += n.textContent; continue; }
      flush();
      if (n.nodeType !== 1) continue;
      if (transparent(n)) out.push(...kids(n, depth));
      else out.push(walk(n, depth + 1));
    }
    flush();
    return out;
  }

  function walk(el, depth) {
    if (skip.has(el.tagName)) return "  ".repeat(depth) + "<" + el.tagName.toLowerCase() + " …/>";
    const attrs = [...el.attributes]
      .filter(a => a.name !== "style" || a.value.trim() !== "")
      .map(a => {
        let v = a.value.replace(/\\s+/g, " ").trim();
        // Inline style is compared by what it says, not how it is punctuated:
        // setting el.style serialises as "width:58px" and React's style prop
        // as "width: 58px;". Same declaration, and normalising it is what
        // keeps this tool a gate rather than a wall of known noise.
        if (a.name === "style")
          v = v.split(";").map(d => d.trim()).filter(Boolean)
               .map(d => d.replace(/\\s*:\\s*/, ":")).sort().join(";");
        return a.name + '="' + v + '"';
      })
      .sort();
    const head = "  ".repeat(depth) + "<" + el.tagName.toLowerCase()
               + (attrs.length ? " " + attrs.join(" ") : "") + ">";
    return [head, ...kids(el, depth)].join("\\n");
  }

  const clean = s => { for (const [re, to] of VOLATILE) s = s.replace(re, to); return s; };
  const report = document.getElementById("report");
  const detached = report && report.parentNode;
  if (detached) report.remove();
  const dom = clean(walk(document.body, 0));
  if (detached) detached.appendChild(report);
  return { dom, report: report ? clean(walk(report, 0)) : "" };
})()`;

async function snapshot(cdp, urls, state) {
  await cdp.send("Network.enable");
  await cdp.send("Network.setBlockedURLs",
    { urls: ["*youtube.com*", "*ytimg.com*", "*googlevideo.com*"] });
  await cdp.send("Emulation.setDeviceMetricsOverride",
    { ...state.size, deviceScaleFactor: 1, mobile: state.size === PHONE });
  await cdp.send("Page.navigate", { url: urls[state.url] });
  // Wait for the app to have painted something it owns, not just for load.
  for (let i = 0; i < 120; i++) {
    if (await cdp.eval(`!!document.querySelector('#shots *, #house *, #report *')`)) break;
    await sleep(150);
  }
  await sleep(400);
  const reached = [];
  for (const sel of state.steps) {
    reached.push({ sel, ok: await clickOn(cdp, sel) });
    await sleep(350);
  }
  return { reached, ...(await cdp.eval(SERIALISE)) };
}

function diff(before, after) {
  const names = [...new Set([...Object.keys(before), ...Object.keys(after)])].sort();
  let bad = 0;
  for (const name of names) {
    const a = before[name], b = after[name];
    if (!a) { console.log(`+ ${name}  (only in AFTER)`); bad++; continue; }
    if (!b) { console.log(`- ${name}  (only in BEFORE)`); bad++; continue; }
    const parts = [["tree", a.dom, b.dom]];
    // Only where the old page actually built one; it built none when closed.
    if (a.report) parts.push(["report", a.report, b.report]);
    const off = parts.filter(([, x, y]) => x !== y);
    if (!off.length) { console.log(`  ${name}  identical`); continue; }
    bad++;
    for (const [what, x, y] of off) {
      const xs = new Set(x.split("\n").map(l => l.trim()));
      const ys = new Set(y.split("\n").map(l => l.trim()));
      const gone = [...xs].filter(l => !ys.has(l));
      const came = [...ys].filter(l => !xs.has(l));
      console.log(`~ ${name} [${what}]  -${gone.length} +${came.length}`);
      for (const l of gone.slice(0, 8)) console.log(`    - ${l.slice(0, 120)}`);
      for (const l of came.slice(0, 8)) console.log(`    + ${l.slice(0, 120)}`);
    }
  }
  console.log(`\n${names.length - bad} identical, ${bad} differing`);
  return bad;
}

const argv = process.argv.slice(2);
if (argv[0] === "--diff") {
  const [before, after] = await Promise.all(
    [argv[1], argv[2]].map(async p => JSON.parse(await readFile(p, "utf8")).states));
  process.exit(diff(before, after) ? 1 : 0);
}

const out = argv[argv.indexOf("--out") + 1];
const urls = { edit: argv[0], view: argv[1], review: argv[2] };
const cdp = await attach(); // any page target; we navigate it ourselves
const states = {};
for (const state of STATES) {
  states[state.name] = await snapshot(cdp, urls, state);
  const missed = states[state.name].reached.filter(r => !r.ok).map(r => r.sel);
  console.log(`  ${state.name.padEnd(28)} ${states[state.name].dom.split("\n").length
    .toString().padStart(4)} lines${missed.length ? "   unreachable: " + missed.join(", ") : ""}`);
}
await writeFile(out, JSON.stringify({ urls, states }, null, 2) + "\n");
console.log(`\nwrote ${out}`);
process.exit(0);
