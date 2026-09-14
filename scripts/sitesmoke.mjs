/* Load every site page and report what it built, and what it complained about.
 *
 * esbuild bundles an undefined identifier happily -- `shot is not defined`
 * reached a browser once already -- so nothing here is trusted until a real
 * page has rendered it.
 */
import { attach } from "./cdp.mjs";
const sleep = ms => new Promise(r => setTimeout(r, ms));

const base = process.argv[2];
const statusUrl = process.argv[3];
const cdp = await attach();
let errors = [];
cdp.ws.addEventListener("message", ev => {
  const m = JSON.parse(ev.data);
  if (m.method === "Runtime.exceptionThrown")
    errors.push(m.params.exceptionDetails.exception?.description
                ?? m.params.exceptionDetails.text);
  if (m.method === "Runtime.consoleAPICalled" && m.params.type === "error")
    errors.push(m.params.args.map(a => a.value ?? a.description).join(" "));
});
// Firebase is external and this is an offline box; accounts stay switched off,
// which is the anonymous state every one of these pages has to work in.
await cdp.send("Network.enable");
if (process.env.BLOCK_CDN) await cdp.send("Network.setBlockedURLs", { urls: ["*gstatic.com*", "*googleapis.com*"] });

const out = {};
for (const [name, url] of [
  ["submit", `${base}/`],
  ["games", `${base}/games`],
  ["mine", `${base}/mine`],
  ["join", `${base}/join/i_nosuch`],
  ["status", statusUrl],
]) {
  errors = [];
  await cdp.send("Page.navigate", { url });
  for (let i = 0; i < 80; i++) {
    if (await cdp.eval(`document.querySelectorAll("#root *").length > 3`)) break;
    await sleep(150);
  }
  await sleep(900);
  out[name] = {
    dataPage: await cdp.eval(`document.body.dataset.page`),
    rendered: await cdp.eval(`document.querySelectorAll("#root *").length`),
    heading: await cdp.eval(`(document.querySelector("#root h1,#root h2")||{}).textContent||""`),
    text: await cdp.eval(`document.body.innerText.replace(/\\s+/g," ").trim().slice(0,90)`),
    errors: errors.slice(0, 2),
  };
}
console.log(JSON.stringify(out, null, 2));
process.exit(0);
