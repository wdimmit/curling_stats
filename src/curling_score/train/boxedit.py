"""An editor for fixing boxes by hand, one frame at a time.

``labels.render`` makes contact sheets to *read*: a reviewer looks, decides a
box is wrong, and writes its key into an edits file somewhere else. That is
enough to reject a box and enough to add one by naming a point, and it was
enough while the question was "is this a stone or a knee".

It is not enough now. The shot-driven wave's proposals land on the right stone
about 70% of the time, and a box on the right stone can still be the wrong
size; a preliminary detector taught from loose boxes proposes loose boxes, and
this set exists to be taught from. So this renders the frames themselves, with
their boxes as things you can drag.

**Sizing a box needs no model.** A stone's apparent width scales with the same
1/(d - x) the row spacing does, so ``SideView.stone_width_at`` turns one
measured width at the hog line into the correct width at any row -- a line
through ``yh``, one multiply. Clicking where a stone is therefore also says how
big it is, and a new box arrives already the right size. Dragging is for the
cases geometry cannot know: a stone half behind a broom, or one the reviewer
wants tighter than its nominal footprint.

The export is a ``boxes`` map: for every frame touched, the boxes that frame
should have, replacing whatever was there. That subsumes reject, add and
resize in one idempotent statement, which matters because the three compose
badly -- "reject this box and add one 4 px left" is two edits that must both
land, and a replacement is one that cannot half-land.
"""

from __future__ import annotations

import html as _html
import json
from pathlib import Path

# Nominal height as a fraction of width. A stone is 0.114 m tall against
# 0.284 m across; the handle adds a little, and the box is drawn to the
# granite's top rather than the handle's, so this stays near the physical 0.40.
HEIGHT_RATIO = 0.42


def frame_geometry(view, width_at_hog: float, row_offset: int = 0) -> dict:
    """The two numbers the page needs to size a box at any row.

    ``stone_width_at`` is linear in the row -- ``k * (row - yh)`` -- so the
    whole perspective model reaches the browser as a slope and an intercept
    rather than as a solver.

    ``row_offset`` is the first row of the image the page will show. Training
    frames are cropped to the ice band, so a click's y is in the crop's rows,
    not the view's; shifting the intercept is the whole correction, since the
    slope is unchanged by a translation.
    """
    c, yh = view._map()
    from curling_score.geometry import constants as C

    k = width_at_hog * (view.d_m - C.TEE_TO_HOGLINE_M) / c
    return {"k": k, "yh": yh - row_offset}


def render(items, out_dir, *, scope: str, title: str = "Fix the boxes",
           proposals: bool = False) -> Path:
    """Write the editor page.

    ``proposals`` defaults to False: each frame opens EMPTY. The colour
    detector's boxes were measured at about 70% on a stone and none of them
    were usable, so presenting them cost a reviewer more in deleting than they
    saved in keeping -- and with SAM a correct box is one click away. Pass True
    only to review an existing set's labels rather than to build one.

    ``items`` is one dict per frame: ``stem``, ``image`` (a path relative to
    the page), ``width``, ``height``, ``boxes`` as ``[cls, cx, cy, w, h]`` in
    normalised coordinates, and ``geom`` from :func:`frame_geometry`.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    page = out_dir / "index.html"
    if not proposals:
        items = [{**it, "boxes": []} for it in items]
    data = json.dumps(items)
    page.write_text(_PAGE.replace("__DATA__", data)
                    .replace("__SCOPE__", _html.escape(scope))
                    .replace("__TITLE__", _html.escape(title)))
    return page


_PAGE = r"""<!doctype html>
<meta charset="utf-8">
<title>__TITLE__</title>
<style>
  :root { --bg:#f7f7f5; --line:#ddd; --muted:#666; --red:#e03c3c; --yel:#e8c020; }
  body { background:var(--bg); font:14px/1.5 system-ui, sans-serif; margin:0;
         padding:12px 16px 120px; }
  h1 { font-size:18px; margin:0 0 4px; }
  p.muted { color:var(--muted); margin:0 0 12px; max-width:80ch; }
  figure { background:#fff; border:1px solid var(--line); border-radius:8px;
           padding:8px; margin:0 0 14px; }
  figcaption { display:flex; gap:10px; align-items:center; flex-wrap:wrap;
               margin-bottom:6px; font-size:13px; }
  code { background:rgba(0,0,0,.06); padding:1px 6px; border-radius:4px; }
  .stage { position:relative; display:inline-block; max-width:100%;
           touch-action:none; }
  .stage img { display:block; width:100%; height:auto; border-radius:6px; }
  .bx { position:absolute; border:2px solid var(--red); box-sizing:border-box;
        cursor:move; }
  .bx.c1 { border-color:var(--yel); }
  .bx.sel { box-shadow:0 0 0 2px #fff, 0 0 0 4px #333; }
  .bx.iffy { border-style:dashed; }
  .h { position:absolute; width:11px; height:11px; background:#fff;
       border:1px solid #333; border-radius:2px; }
  .h.nw{left:-6px;top:-6px;cursor:nwse-resize} .h.ne{right:-6px;top:-6px;cursor:nesw-resize}
  .h.sw{left:-6px;bottom:-6px;cursor:nesw-resize} .h.se{right:-6px;bottom:-6px;cursor:nwse-resize}
  .done { outline:3px solid #2a7; }
  .bar { position:fixed; left:0; right:0; bottom:0; background:#fff;
         border-top:1px solid var(--line); padding:8px 16px; display:flex;
         gap:10px; align-items:center; flex-wrap:wrap; }
  button { font:inherit; padding:5px 12px; border-radius:6px;
           border:1px solid var(--line); background:#fff; cursor:pointer; }
  button.on { background:#333; color:#fff; }
  textarea { width:100%; height:90px; font:12px/1.4 ui-monospace, monospace; }
</style>
<h1>__TITLE__ &mdash; <code>__SCOPE__</code></h1>
<p class="muted">Drag a box to move it, a corner to resize. Click a box then
<b>Delete</b> to remove it. With <b>+red</b> or <b>+yellow</b> armed, click the
ice where a stone is and <b>SAM segments it</b> -- the click says what it is,
the model says where its edges are, and the perspective solve picks whichever
of the model's readings is stone-sized at that row. Without the segmenter
running, the box still arrives correctly sized by geometry alone. <b>R</b> arms red and <b>Y</b> arms yellow, so a frame is
r-click-y-click-space without leaving the keyboard. Frames open <b>empty</b>:
you are adding the stones you see, not correcting a detector's guesses. Mark a
frame reviewed with <b>space</b> and it turns green &mdash; only reviewed
frames are exported, because a frame nobody looked at says nothing, and a
reviewed frame with no boxes says something useful: no stone in the band.
<b>N</b>/<b>P</b> jump between frames; work is kept in this browser as you go.</p>

<div id="frames"></div>

<div class="bar">
  <button id="addr">+red (R)</button>
  <button id="addy">+yellow (Y)</button>
  <span id="count" class="muted"></span>
  <span id="segstate" class="muted">SAM: click a stone</span>
  <button id="export">Export JSON</button>
  <button id="dl">Download</button>
  <textarea id="out" placeholder="export appears here"></textarea>
</div>

<script>
const ITEMS = __DATA__;
const SCOPE = "__SCOPE__";
const KEY = "boxedit:" + SCOPE;
const HEIGHT_RATIO = 0.42;

let state = {};
try { state = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) { state = {}; }
let arm = null, sel = null, cur = 0;

function save() {
  try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) {}
  const n = Object.values(state).filter(s => s.reviewed).length;
  document.getElementById("count").textContent =
    n + " / " + ITEMS.length + " reviewed";
}

function st(item) {
  if (!state[item.stem]) {
    state[item.stem] = {boxes: item.boxes.map(b => b.slice()), reviewed: false};
  }
  return state[item.stem];
}

function drawBoxes(fig, item) {
  const stage = fig.querySelector(".stage");
  stage.querySelectorAll(".bx").forEach(e => e.remove());
  st(item).boxes.forEach((b, i) => {
    const d = document.createElement("div");
    d.className = "bx c" + b[0] + (b[5] ? " iffy" : "");
    d.style.left = (b[1] - b[3] / 2) * 100 + "%";
    d.style.top = (b[2] - b[4] / 2) * 100 + "%";
    d.style.width = b[3] * 100 + "%";
    d.style.height = b[4] * 100 + "%";
    d.dataset.i = i;
    for (const c of ["nw", "ne", "sw", "se"]) {
      const h = document.createElement("div");
      h.className = "h " + c; h.dataset.corner = c;
      d.appendChild(h);
    }
    stage.appendChild(d);
  });
  fig.classList.toggle("done", !!st(item).reviewed);
}

function build() {
  const host = document.getElementById("frames");
  ITEMS.forEach((item, idx) => {
    const fig = document.createElement("figure");
    fig.id = "f" + idx;
    fig.innerHTML =
      '<figcaption><b>#' + (idx + 1) + '</b><code>' + item.stem + '</code>' +
      '<button class="rev">reviewed (R)</button>' +
      '<span class="muted">' + item.width + '&times;' + item.height + '</span>' +
      '</figcaption><div class="stage"><img loading="lazy" src="' +
      item.image + '" alt=""></div>';
    host.appendChild(fig);
    fig.querySelector(".rev").onclick = () => {
      const s = st(item); s.reviewed = !s.reviewed; save(); drawBoxes(fig, item);
    };
    const stage = fig.querySelector(".stage");
    stage.addEventListener("pointerdown", ev => onDown(ev, fig, item, idx));
    drawBoxes(fig, item);
  });
  save();
}

// A click with a colour armed. The click says WHAT; SAM says WHERE the edges
// are. If the segmenter is not there -- the page opened as a plain file, or
// the server is down -- fall back to the geometric box, which is the right
// size for that row even though it cannot know the stone's exact position.
function placeGeometric(item, nx, ny, cls) {
  const row = ny * item.height;
  let w = item.geom.k * (row - item.geom.yh);
  if (!(w > 2)) w = 52;                      // off the map: nominal hog width
  const h = w * HEIGHT_RATIO;
  st(item).boxes.push([cls, nx, ny, w / item.width, h / item.height]);
}

let segOK = true;          // flips false the first time /segment is unreachable

function note(msg) {
  const n = document.getElementById("segstate");
  if (n) n.textContent = msg;
}

async function place(item, nx, ny, cls, fig) {
  if (!segOK) { placeGeometric(item, nx, ny, cls); save(); drawBoxes(fig, item); return; }
  const stage = fig.querySelector(".stage");
  stage.style.cursor = "progress";
  try {
    const r = await fetch("/segment", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({stem: item.stem, x: nx * item.width,
                            y: ny * item.height, cls: cls}),
    });
    const j = await r.json();
    if (j.ok && j.box) {
      note("SAM " + j.ratio + "x expected, aspect " + j.aspect +
           (j.plausible ? "" : " - geometry doubts this"));
      const [x0, y0, x1, y1] = j.box;
      const w = (x1 - x0 + 1) / item.width, h = (y1 - y0 + 1) / item.height;
      // The "geometry doubts this" flag rides as a SIXTH element, not as a
      // property on the array: JSON.stringify drops properties on arrays, so
      // as a property it survived in memory and vanished on reload, and the
      // dashed border silently became solid. `payload()` takes only the first
      // five, so the export shape is unchanged.
      st(item).boxes.push([cls, (x0 + x1 + 1) / 2 / item.width,
                           (y0 + y1 + 1) / 2 / item.height, w, h,
                           j.plausible ? 0 : 1]);
    } else {
      // Say so. A silent fall back to the geometric box is indistinguishable
      // from segmentation that is working badly, and that is exactly how a
      // page that never called /segment at all went unnoticed.
      note("SAM found nothing here - box sized by geometry" +
           (j.reason ? " (" + j.reason + ")" : ""));
      placeGeometric(item, nx, ny, cls);
    }
  } catch (e) {
    segOK = false;                       // say so once, then stop trying
    note("segmenter offline - boxes sized by geometry");
    placeGeometric(item, nx, ny, cls);
  } finally {
    stage.style.cursor = "";
    save(); drawBoxes(fig, item);
  }
}

let drag = null;

function onDown(ev, fig, item, idx) {
  cur = idx;
  const stage = fig.querySelector(".stage");
  const r = stage.getBoundingClientRect();
  const nx = (ev.clientX - r.left) / r.width, ny = (ev.clientY - r.top) / r.height;
  const bx = ev.target.closest(".bx");

  if (!bx) {
    if (arm !== null) { place(item, nx, ny, arm, fig); }
    sel = null;
    return;
  }
  sel = {fig, item, i: +bx.dataset.i};
  document.querySelectorAll(".bx.sel").forEach(e => e.classList.remove("sel"));
  bx.classList.add("sel");
  const b = st(item).boxes[sel.i];
  drag = {fig, item, i: sel.i, corner: ev.target.dataset.corner || null,
          nx, ny, start: b.slice(), rect: r};
  stage.setPointerCapture(ev.pointerId);
  ev.preventDefault();
}

window.addEventListener("pointermove", ev => {
  if (!drag) return;
  const r = drag.rect;
  const nx = (ev.clientX - r.left) / r.width, ny = (ev.clientY - r.top) / r.height;
  const dx = nx - drag.nx, dy = ny - drag.ny;
  const b = st(drag.item).boxes[drag.i], s = drag.start;
  if (!drag.corner) { b[1] = s[1] + dx; b[2] = s[2] + dy; }
  else {
    let x0 = s[1] - s[3] / 2, x1 = s[1] + s[3] / 2;
    let y0 = s[2] - s[4] / 2, y1 = s[2] + s[4] / 2;
    if (drag.corner.includes("w")) x0 += dx; else x1 += dx;
    if (drag.corner.includes("n")) y0 += dy; else y1 += dy;
    b[1] = (x0 + x1) / 2; b[2] = (y0 + y1) / 2;
    b[3] = Math.abs(x1 - x0); b[4] = Math.abs(y1 - y0);
  }
  drawBoxes(drag.fig, drag.item);
  const el = drag.fig.querySelector('.bx[data-i="' + drag.i + '"]');
  if (el) el.classList.add("sel");
});

window.addEventListener("pointerup", () => { if (drag) { save(); drag = null; } });

window.addEventListener("keydown", ev => {
  if (ev.target.tagName === "TEXTAREA") return;
  const k = ev.key.toLowerCase();
  if ((ev.key === "Delete" || ev.key === "Backspace") && sel) {
    st(sel.item).boxes.splice(sel.i, 1); save(); drawBoxes(sel.fig, sel.item);
    sel = null; ev.preventDefault();
  } else if (k === "r" || k === "y") {
    setArm(k === "r" ? 0 : 1);
    ev.preventDefault();
  } else if (ev.key === " " || k === "d") {
    const item = ITEMS[cur], fig = document.getElementById("f" + cur);
    const s = st(item); s.reviewed = !s.reviewed; save(); drawBoxes(fig, item);
    ev.preventDefault();               // space would otherwise scroll the page
  } else if (k === "n" || k === "p") {
    cur = Math.max(0, Math.min(ITEMS.length - 1, cur + (k === "n" ? 1 : -1)));
    document.getElementById("f" + cur).scrollIntoView({block: "center"});
  }
});

function setArm(cls) {
  arm = (arm === cls) ? null : cls;
  document.getElementById("addr").classList.toggle("on", arm === 0);
  document.getElementById("addy").classList.toggle("on", arm === 1);
}
document.getElementById("addr").onclick = () => setArm(0);
document.getElementById("addy").onclick = () => setArm(1);

function payload() {
  const boxes = {}, reviewed = [];
  for (const it of ITEMS) {
    const s = state[it.stem];
    if (!s || !s.reviewed) continue;           // never export what nobody saw
    reviewed.push(it.stem);
    boxes[it.stem] = s.boxes.map(b => [b[0], +b[1].toFixed(6), +b[2].toFixed(6),
                                       +b[3].toFixed(6), +b[4].toFixed(6)]);
  }
  return {scope: SCOPE, boxes: boxes, reviewed: reviewed};
}

document.getElementById("export").onclick = () => {
  document.getElementById("out").value = JSON.stringify(payload(), null, 1);
};
document.getElementById("dl").onclick = () => {
  const blob = new Blob([JSON.stringify(payload(), null, 1)],
                        {type: "application/json"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = SCOPE.replace(/[^a-z0-9]+/gi, "-") + "-boxes.json";
  a.click();
};

build();
</script>
"""
