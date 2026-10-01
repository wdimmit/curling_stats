/* The game report as a person sees it: nothing scrolls sideways at 1280 or
 * 390 px, the title shows, and it prints on one letter page.
 *
 *   PYTHONPATH=src .venv/bin/python scripts/devserve.py timeline.json --overrides overrides.json
 *   google-chrome --headless=new --disable-gpu --no-sandbox \
 *     --remote-debugging-port=9333 --user-data-dir=$(mktemp -d) about:blank &
 *   CDP_PORT=9333 node scripts/reportcheck.mjs http://127.0.0.1:PORT/s/SHARE/
 *
 * The view-only link, because review (/g/) hides the Report button.
 * Exits 1 naming each failure. */
import { execFileSync } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { attach } from "./cdp.mjs";

const url = process.argv[2];
if (!url) { console.error("usage: node scripts/reportcheck.mjs <view-only url>"); process.exit(2); }
const cdp = await attach("");
const fails = [];
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function waitFor(expr, ms = 15000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    if (await cdp.eval(expr).catch(() => false)) return;
    await sleep(200);
  }
  throw new Error(`timed out waiting for ${expr}`);
}

async function openReport(width, height, mobile) {
  await cdp.send("Emulation.setDeviceMetricsOverride",
                 { width, height, deviceScaleFactor: 1, mobile });
  await cdp.send("Emulation.setTouchEmulationEnabled", { enabled: mobile });
  await cdp.send("Page.navigate", { url });
  await waitFor(`!!document.getElementById("reportBtn")`);
  await cdp.eval(`document.getElementById("reportBtn").click()`);
  await waitFor(`!!document.querySelector("#report.show .rpt-byend")`);
  await cdp.eval("document.fonts.ready.then(() => true)");
  await sleep(600);   // the clock's ResizeObserver, then a render
}

for (const [w, h, mobile] of [[1280, 900, false], [390, 844, true]]) {
  await openReport(w, h, mobile);
  const r = await cdp.eval(`({
    sw: document.documentElement.scrollWidth, iw: innerWidth,
    tall: document.documentElement.scrollHeight,
    title: getComputedStyle(document.querySelector(".rpt-title")).display })`);
  console.log(`${w}px: ${r.sw}px wide in a ${r.iw}px window, ${r.tall}px tall, title ${r.title}`);
  if (r.sw > r.iw) fails.push(`${w}px: the page scrolls sideways (${r.sw} > ${r.iw})`);
  if (r.title === "none") fails.push(`${w}px: the report title is hidden`);
}

await openReport(1280, 900, false);
const pdf = await cdp.send("Page.printToPDF", { printBackground: false, preferCSSPageSize: true });
const file = join(mkdtempSync(join(tmpdir(), "reportcheck-")), "report.pdf");
writeFileSync(file, Buffer.from(pdf.data, "base64"));
const pages = Number(/Pages:\s+(\d+)/.exec(execFileSync("pdfinfo", [file]).toString())?.[1]);
console.log(`printed: ${pages} page(s) -> ${file}`);
if (pages !== 1) fails.push(`it prints on ${pages} pages, not 1`);

if (fails.length) { console.error(fails.join("\n")); process.exit(1); }
console.log("ok");
process.exit(0);
