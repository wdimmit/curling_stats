"""The handle-point labelling page: one frame at a time, two clicks each.

A frame is one stone, cut from the long camera that faces its thrower. The
person marks the NECK (where the handle comes up out of the stone -- the pivot,
which stays put while the stone turns) and the GRIP END (the free end, which
swings round). Beside it loops a second of the same stone either side of the
frame; that motion is what makes the two ends tell apart when the handle is
small.

Work is kept in localStorage and, on every finished frame, saved through the
server to OUT/edits/<scope>-<sitting>.json -- one file per sitting, rewritten
as the sitting goes, so a closed tab loses nothing and a bad sitting can be
dropped alone. Serve over HTTP (scripts/spin/serve.py), never file://.

Coordinates are the crop's own native pixels, as floats.
"""
import json
from pathlib import Path

STONE_PX = 360          # the stone's width on screen
ANIM_SCALE = 1.5


def render(out, items, scope):
    slim = [dict(stem=it["stem"], size=it["size"], stone_box=it["stone_box"], color=it["color"],
                 video=it["video"], end=it["end"], shot=it["shot"], y_m=it["y_m"]) for it in items]
    html = (_PAGE.replace("__ITEMS__", json.dumps(slim))
                 .replace("__SCOPE__", json.dumps(scope))
                 .replace("__STONE_PX__", str(STONE_PX))
                 .replace("__ANIM_SCALE__", str(ANIM_SCALE)))
    Path(out, "index.html").write_text(html)


_PAGE = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>Handle points</title>
<style>
 body { font: 14px/1.4 system-ui, sans-serif; margin: 0; background: #1d1f22; color: #e6e6e6; }
 header { padding: 8px 14px; background: #2a2d31; display: flex; gap: 14px; align-items: center; flex-wrap: wrap; }
 header b { font-size: 15px; }
 button { background: #3b4048; color: #eee; border: 1px solid #555; border-radius: 4px; padding: 4px 10px; cursor: pointer; }
 button:hover { background: #4a505a; }
 #note { color: #9fd49f; }
 main { display: flex; gap: 18px; padding: 14px; align-items: flex-start; flex-wrap: wrap; }
 #view { cursor: crosshair; background: #000; }
 .side { display: flex; flex-direction: column; gap: 8px; max-width: 380px; }
 #anim { background: #000; }
 .help { background: #26292d; border: 1px solid #3a3d42; border-radius: 6px; padding: 8px 10px; font-size: 13px; }
 .help kbd { background: #3b4048; border-radius: 3px; padding: 0 4px; font-size: 12px; }
 .neck { color: #4fd1ff; } .grip { color: #ffa040; }
 #meta { color: #aaa; font-size: 12px; }
 #flag { font-weight: bold; color: #ff7070; }
</style></head>
<body>
<header>
 <b>Handle points</b>
 <span id="progress"></span>
 <button id="prev">&larr; Prev</button>
 <button id="next">Next &rarr;</button>
 <button id="todo">Next to do</button>
 <button id="save">Save now</button>
 <button id="dl">Download</button>
 <span id="note"></span>
</header>
<main>
 <canvas id="view"></canvas>
 <div class="side">
  <canvas id="anim"></canvas>
  <div id="animlabel" style="font-size:12px;color:#aaa"></div>
  <div id="meta"></div><div id="flag"></div>
  <div class="help">
   <p><b class="neck">1. Neck</b>: where the handle comes up out of the top of the stone.
   It is the pivot, so it stays put in the animation.</p>
   <p><b class="grip">2. Grip end</b>: the free end of the handle, the tip the hand holds.
   It swings round in the animation.</p>
   <p>Click the neck, then the grip end. Drag either to adjust. When the handle points
   at or away from the camera the two are close together; place both anyway.</p>
   <p><kbd>Enter</kbd> / <kbd>&rarr;</kbd> next &nbsp; <kbd>&larr;</kbd> back &nbsp;
   <kbd>1</kbd>/<kbd>2</kbd> re-place neck / grip &nbsp; <kbd>Backspace</kbd> clear<br>
   <kbd>U</kbd> can't tell which end is which &nbsp; <kbd>O</kbd> handle covered (broom, foot, hand)<br>
   <kbd>Space</kbd> pause animation &nbsp; <kbd>,</kbd> <kbd>.</kbd> step it when paused</p>
  </div>
 </div>
</main>
<script>
const ITEMS = __ITEMS__;
const SCOPE = __SCOPE__;
const STONE_PX = __STONE_PX__, ANIM_SCALE = __ANIM_SCALE__;
const ANIM_N = 11, ANIM_W = 200, ANIM_H = 170, ANIM_NOW = 5;
const KEY = "spin:" + SCOPE;
const SITTING = new Date().toISOString().replace(/[-:]/g, "").slice(0, 15);
let state = {};
try { state = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) { state = {}; }
let idx = 0, armed = null, drag = null, S = 1, playing = true, animK = 0, dirty = false;
const view = document.getElementById("view"), vctx = view.getContext("2d");
const anim = document.getElementById("anim"), actx = anim.getContext("2d");
anim.width = ANIM_W * ANIM_SCALE; anim.height = ANIM_H * ANIM_SCALE;
const img = new Image(), sprite = new Image();
img.onload = draw; sprite.onload = drawAnim;

function st(it) { return state[it.stem] || (state[it.stem] = {}); }
function done(it) { const s = state[it.stem]; return !!(s && (s.flag || (s.neck && s.grip))); }
function keep() {
  try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) {}
  dirty = true;
}
function note(t) { document.getElementById("note").textContent = t; }

function show(i) {
  idx = Math.max(0, Math.min(ITEMS.length - 1, i));
  armed = null;
  const it = ITEMS[idx];
  S = STONE_PX / (it.stone_box[2] - it.stone_box[0]);
  view.width = Math.round(it.size[0] * S); view.height = Math.round(it.size[1] * S);
  img.src = "images/" + it.stem + ".png";
  sprite.src = "anim/" + it.stem + ".png";
  animK = ANIM_NOW;
  status();
}

function status() {
  const n = ITEMS.filter(done).length;
  document.getElementById("progress").textContent =
    "frame " + (idx + 1) + " / " + ITEMS.length + "  ·  " + n + " done";
  const it = ITEMS[idx], s = state[it.stem] || {};
  document.getElementById("meta").textContent =
    it.video + "  end " + it.end + " rock " + it.shot + "  ·  " + it.color +
    "  ·  " + it.y_m.toFixed(1) + " m past the tee  ·  stone " +
    Math.round(it.stone_box[2] - it.stone_box[0]) + " px";
  document.getElementById("flag").textContent =
    s.flag === "unclear" ? "marked: can't tell" : s.flag === "occluded" ? "marked: covered" : "";
}

function dot(ctx, p, col, label) {
  const x = p[0] * S, y = p[1] * S;
  ctx.lineWidth = 2; ctx.strokeStyle = "#000";
  ctx.beginPath(); ctx.arc(x, y, 7, 0, 2 * Math.PI); ctx.stroke();
  ctx.strokeStyle = col; ctx.lineWidth = 2.5;
  ctx.beginPath(); ctx.arc(x, y, 6, 0, 2 * Math.PI); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(x - 2, y); ctx.lineTo(x + 2, y); ctx.moveTo(x, y - 2); ctx.lineTo(x, y + 2); ctx.stroke();
  ctx.font = "bold 14px system-ui"; ctx.fillStyle = col; ctx.fillText(label, x + 9, y - 8);
}

function draw() {
  const it = ITEMS[idx], s = state[it.stem] || {};
  vctx.imageSmoothingEnabled = true; vctx.imageSmoothingQuality = "high";
  vctx.drawImage(img, 0, 0, view.width, view.height);
  if (s.neck && s.grip) {
    vctx.strokeStyle = "rgba(255,255,255,0.7)"; vctx.lineWidth = 1.5;
    vctx.beginPath(); vctx.moveTo(s.neck[0] * S, s.neck[1] * S); vctx.lineTo(s.grip[0] * S, s.grip[1] * S); vctx.stroke();
  }
  if (s.neck) dot(vctx, s.neck, "#4fd1ff", "neck");
  if (s.grip) dot(vctx, s.grip, "#ffa040", "grip");
  if (armed) { vctx.font = "bold 15px system-ui"; vctx.fillStyle = armed === "neck" ? "#4fd1ff" : "#ffa040";
               vctx.fillText("click to place the " + armed, 8, 20); }
  status();
}

function drawAnim() {
  if (!sprite.complete || !sprite.naturalWidth) return;
  actx.imageSmoothingEnabled = true;
  actx.drawImage(sprite, animK * ANIM_W, 0, ANIM_W, ANIM_H, 0, 0, anim.width, anim.height);
  if (animK === ANIM_NOW) { actx.strokeStyle = "#ffe14f"; actx.lineWidth = 4; actx.strokeRect(2, 2, anim.width - 4, anim.height - 4); }
  const dt = ((animK - ANIM_NOW) / 10).toFixed(1);
  document.getElementById("animlabel").textContent =
    (animK === ANIM_NOW ? "this frame" : (dt > 0 ? "+" : "") + dt + " s") + (playing ? "" : "  (paused)");
}
setInterval(() => { if (playing) { animK = (animK + 1) % ANIM_N; drawAnim(); } }, 160);

function at(ev) {
  const r = view.getBoundingClientRect();
  return [(ev.clientX - r.left) * (view.width / r.width) / S, (ev.clientY - r.top) * (view.height / r.height) / S];
}
function near(p, q) { return q && Math.hypot((p[0] - q[0]) * S, (p[1] - q[1]) * S) < 11; }

view.addEventListener("mousedown", ev => {
  const it = ITEMS[idx], s = st(it), p = at(ev);
  if (!armed) {
    if (near(p, s.grip)) { drag = "grip"; return; }
    if (near(p, s.neck)) { drag = "neck"; return; }
  }
  const which = armed || (!s.neck ? "neck" : !s.grip ? "grip" :
                (Math.hypot(p[0] - s.neck[0], p[1] - s.neck[1]) < Math.hypot(p[0] - s.grip[0], p[1] - s.grip[1]) ? "neck" : "grip"));
  s[which] = [+p[0].toFixed(2), +p[1].toFixed(2)];
  delete s.flag;
  armed = null; drag = which;
  keep(); draw();
});
view.addEventListener("mousemove", ev => {
  if (!drag) return;
  const p = at(ev), s = st(ITEMS[idx]);
  s[drag] = [+p[0].toFixed(2), +p[1].toFixed(2)];
  draw();
});
window.addEventListener("mouseup", () => { if (drag) { drag = null; keep(); } });

function next() {
  if (done(ITEMS[idx])) autosave();
  show(idx + 1);
}
function todo() {
  for (let k = 1; k <= ITEMS.length; k++) {
    const j = (idx + k) % ITEMS.length;
    if (!done(ITEMS[j])) { show(j); return; }
  }
  note("every frame is done - press Save now");
}
function flag(f) {
  const s = st(ITEMS[idx]);
  delete s.neck; delete s.grip; s.flag = f;
  keep(); draw(); next();
}

window.addEventListener("keydown", ev => {
  if (ev.target.tagName === "INPUT" || ev.target.tagName === "TEXTAREA") return;
  const s = st(ITEMS[idx]);
  switch (ev.key) {
    case "Enter": case "ArrowRight": next(); break;
    case "ArrowLeft": show(idx - 1); break;
    case "1": armed = "neck"; draw(); break;
    case "2": armed = "grip"; draw(); break;
    case "u": case "U": flag("unclear"); break;
    case "o": case "O": flag("occluded"); break;
    case "Backspace": case "Delete": delete s.neck; delete s.grip; delete s.flag; keep(); draw(); break;
    case " ": playing = !playing; drawAnim(); break;
    case ",": if (!playing) { animK = (animK + ANIM_N - 1) % ANIM_N; drawAnim(); } break;
    case ".": if (!playing) { animK = (animK + 1) % ANIM_N; drawAnim(); } break;
    default: return;
  }
  ev.preventDefault();
});

function payload() {
  const points = {}, flags = {};
  for (const it of ITEMS) {
    const s = state[it.stem];
    if (!s) continue;
    if (s.flag) flags[it.stem] = s.flag;
    else if (s.neck && s.grip) points[it.stem] = {neck: s.neck, grip: s.grip};
  }
  return {scope: SCOPE, sitting: SITTING, points: points, flags: flags};
}
async function saveNow(quiet) {
  const p = payload();
  if (!Object.keys(p.points).length && !Object.keys(p.flags).length) {
    if (!quiet) note("nothing to save yet");
    return;
  }
  try {
    const r = await fetch("/save", {method: "POST", headers: {"Content-Type": "application/json"},
                                    body: JSON.stringify(p)});
    const j = await r.json();
    if (j.ok) { dirty = false; note("saved " + j.points + " frames + " + j.flags + " flagged -> " + j.path); }
    else note("SAVE FAILED: " + j.reason);
  } catch (e) {
    note("SAVE FAILED - is serve.py running? Your work is still in this browser; use Download.");
  }
}
let pending = null;
function autosave() { clearTimeout(pending); pending = setTimeout(() => saveNow(true), 800); }
document.getElementById("save").onclick = () => saveNow(false);
document.getElementById("next").onclick = next;
document.getElementById("prev").onclick = () => show(idx - 1);
document.getElementById("todo").onclick = todo;
document.getElementById("dl").onclick = () => {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([JSON.stringify(payload(), null, 1)], {type: "application/json"}));
  a.download = SCOPE.replace(/[^a-z0-9]+/gi, "-") + "-" + SITTING + ".json";
  a.click();
};
window.addEventListener("beforeunload", ev => { if (dirty) { saveNow(true); } });

const first = ITEMS.findIndex(it => !done(it));
show(first < 0 ? 0 : first);
</script>
</body></html>
"""
