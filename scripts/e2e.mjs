/* Does the built viewer still do the job, on every surface it is served on? */
import { attach } from "./cdp.mjs";
const sleep = ms => new Promise(r => setTimeout(r, ms));
const [edit, view, review] = process.argv.slice(2);

const cdp = await attach();
const errors = [];
cdp.ws.addEventListener("message", ev => {
  const m = JSON.parse(ev.data);
  if (m.method === "Runtime.exceptionThrown")
    errors.push(m.params.exceptionDetails.exception?.description ?? m.params.exceptionDetails.text);
});
await cdp.send("Network.enable");
await cdp.send("Network.setBlockedURLs", { urls: ["*youtube.com*"] });

async function open(url) {
  await cdp.send("Page.navigate", { url });
  for (let i = 0; i < 120; i++) {
    if (await cdp.eval(`!!document.querySelector('#shots > *')`)) break;
    await sleep(150);
  }
  await sleep(500);
}

const out = {};

// --- edit: grade a shot, then type it, and read it back off the server -----
await open(edit);
await cdp.eval(`document.querySelector('#shots > *:nth-child(3)').click()`);
await sleep(400);
await cdp.eval(`document.querySelector('#scoreBtns button[data-v="3"]').click()`);
await sleep(200);
const picked = await cdp.eval(
  `(() => { const b=[...document.querySelectorAll('#typeList button')]
      .find(x=>!x.classList.contains('on')); b.click(); return b.dataset.t; })()`);
await sleep(2500);
out.edit = {
  clickedType: picked,
  onServer: await cdp.eval(`fetch("overrides.json",{cache:"no-store"}).then(r=>r.json())`),
  savePill: await cdp.eval(`document.getElementById("save").textContent`),
};

// --- a bar in the report still goes to its rock ----------------------------
await cdp.eval(`document.getElementById("reportBtn").click()`);
await sleep(600);
out.reportBars = await cdp.eval(`document.querySelectorAll('#report rect[data-shot]').length`);
out.hollowHittable = await cdp.eval(
  `(() => { const b=document.querySelector('#report rect.bar.est');
     return b ? getComputedStyle(b).pointerEvents : "none found"; })()`);
await cdp.eval(`(() => { const b=document.querySelector('#report rect[data-shot]');
  b.dispatchEvent(new MouseEvent("click", { bubbles: true })); })()`);
await sleep(700);
out.afterBarClick = {
  label: await cdp.eval(`document.getElementById("label").textContent`),
  reportStillOpen: await cdp.eval(`document.body.classList.contains("reporting")`),
};

// --- view and review are read-only ----------------------------------------
for (const [name, url] of [["view", view], ["review", review]]) {
  await open(url);
  out[name] = {
    mode: await cdp.eval(`document.body.dataset.mode || null`),
    gradingPresent: await cdp.eval(`!!document.getElementById("grading")`),
    gradingVisible: await cdp.eval(
      `(() => { const g=document.getElementById("grading");
         return g ? getComputedStyle(g).display !== "none" : null; })()`),
    clockReachable: await cdp.eval(`!!document.querySelector("#clockBox")`),
    post: await cdp.eval(
      `fetch("overrides.json",{method:"POST",headers:{"Content-Type":"application/json"},
         body:"{}"}).then(r=>r.status).catch(()=>"blocked")`),
  };
}

console.log(JSON.stringify(out, null, 2));
console.log(errors.length ? "EXCEPTIONS: " + errors.slice(0,3).join(" | ") : "no exceptions");
process.exit(0);
