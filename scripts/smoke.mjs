/* Load a chart surface and report what the page actually built. */
import { attach } from "./cdp.mjs";
const sleep = ms => new Promise(r => setTimeout(r, ms));

const url = process.argv[2];
const cdp = await attach();
const errors = [];
cdp.ws.addEventListener("message", ev => {
  const m = JSON.parse(ev.data);
  if (m.method === "Runtime.consoleAPICalled" && m.params.type === "error")
    errors.push(m.params.args.map(a => a.value ?? a.description).join(" "));
  if (m.method === "Runtime.exceptionThrown")
    errors.push(m.params.exceptionDetails.exception?.description
                ?? m.params.exceptionDetails.text);
});
await cdp.send("Network.enable");
await cdp.send("Network.setBlockedURLs", { urls: ["*youtube.com*"] });
await cdp.send("Page.navigate", { url });
for (let i = 0; i < 120; i++) {
  if (await cdp.eval(`!!document.querySelector('#shots > *')`)) break;
  await sleep(150);
}
await sleep(600);
const report = await cdp.eval(`(() => ({
  root: !!document.getElementById("root"),
  shots: document.querySelectorAll("#shots > *").length,
  stones: document.querySelectorAll("#house .stone").length,
  typeButtons: document.querySelectorAll("#typeList button").length,
  scoreButtons: document.querySelectorAll("#scoreBtns button").length,
  label: (document.getElementById("label")||{}).textContent,
  body: document.body.dataset.mode || "(edit)",
  viewBox: (document.getElementById("house")||{}).getAttribute?.("viewBox"),
  report: document.querySelectorAll("#report .card").length,
  clock: document.querySelectorAll("#clock svg").length,
  save: (document.getElementById("save")||{}).textContent,
}))()`);
console.log(JSON.stringify(report, null, 2));
console.log(errors.length ? "CONSOLE ERRORS:\n  " + errors.slice(0, 5).join("\n  ")
                          : "no console errors");
process.exit(0);
