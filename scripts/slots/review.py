"""Card-or-blank review of scoreboard slot windows, for the slot presence model.

    python scripts/slots/review.py <survey-dir> [--port 8779]

<survey-dir> holds what the slot survey wrote on the worker: `slots.jsonl` (one
row per slot per board read, with the presence statistics and the digit
model's read) and `win/` (each slot's raw card window, `<id>.png`, and each
read's board, `board_<vid>_<t>.jpg`).

Every slot gets an automatic label where the evidence is strong, and the page
asks a person about the rest:

* card  -- the digit model reads it at >= 0.9999, the box's or window's
  brightest 2% sit at least 12 above the row (a white tile), and 3-35% of the
  box is ink (a digit's worth: a thin "1" is 8%, a person or an edge 40%+);
* blank -- no confident digit, and either next to no ink in the box (every
  card has some; a thin "1" is 8%) or over 40% dark (a person or an edge in
  front) -- never a slot today's presence rule calls a card;
* ask   -- everything else.

Asked slots come first; each auto group shows a spot-check sample. Clicking a
tile cycles card -> blank -> skip; `b` on the page opens the board it came from.
Saving writes `<survey-dir>/edits/slots-<time>.json` ({"labels": {id: label}})
through the server -- serve over HTTP, never file://, as with every labelling
page here. Copy each saved file to datasets/slots/edits/ and commit it at once.
"""
import argparse
import html
import json
import random
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CONF = 0.9999


def auto_label(r):
    confident = r.get("digit") is not None and r["conf"] >= CONF
    if confident and max(r.get("b98", -99), r.get("wb98") or -99) >= 12 and 0.03 <= r["ink"] <= 0.35:
        return "card"
    if r.get("old"):
        return "ask"            # today's rule calls it a card: a person to look at
    if not confident and r["ink"] < 0.02:
        return "blank"          # no ink in the box, and the digit reader sees none in the window
    if not confident and r["ink"] > 0.40:
        return "blank"          # a person or an edge in front: nothing readable hangs there
    return "ask"


def build(root: Path, sample: int = 60, seed: int = 0, cap: int = 350) -> Path:
    rows = [json.loads(l) for l in open(root / "slots.jsonl")]
    rows = [r for r in rows if (root / "win" / f"{r['id']}.png").exists()]
    for r in rows:
        r["auto"] = auto_label(r)
    rng = random.Random(seed)
    ask = _runs([r for r in rows if r["auto"] == "ask"])
    cards = [r for r in rows if r["auto"] == "card"]
    blanks = [r for r in rows if r["auto"] == "blank"]
    spot = rng.sample(cards, min(sample, len(cards))) + rng.sample(blanks, min(sample, len(blanks)))
    # Every disagreement with today's rule and every confident digit first; the
    # rest of the ask pile sampled evenly over the videos up to the cap.
    must = [r for r in ask if r["old"] or (r["digit"] is not None and r["conf"] >= CONF)]
    rest = [r for r in ask if r not in must]
    rng.shuffle(rest)
    rest = sorted(rest, key=lambda r: r["vid"])
    per_vid = {}
    for r in rest:
        per_vid.setdefault(r["vid"], []).append(r)
    room = max(0, cap - len(must))
    picked = []
    while room > 0 and any(per_vid.values()):
        for v in list(per_vid):
            if per_vid[v] and room > 0:
                picked.append(per_vid[v].pop())
                room -= 1
    for r in must + picked:
        r["guess"] = "card" if (r["old"] or (r["digit"] is not None and r["conf"] >= CONF)) else "blank"
    shown = sorted(must + picked, key=lambda r: (r["vid"], r["t"], r["color"], r["slot"])) + spot
    items = [{"id": r["id"], "members": r.get("members", [r["id"]]), "auto": r["auto"], "guess": r.get("guess", r["auto"]), "vid": r["vid"], "t": r["t"], "color": r["color"],
              "slot": r["slot"], "digit": r.get("digit"), "conf": r["conf"], "ink": r["ink"],
              "b98": r.get("b98"), "board": f"win/board_{r['vid']}_{int(r['t']):06d}.jpg"} for r in shown]
    counts = {"ask": sum(len(r["members"]) for r in ask), "ask_tiles": len(ask), "card": len(cards), "blank": len(blanks), "total": len(rows)}
    page = root / "index.html"
    page.write_text(PAGE.replace("__DATA__", json.dumps(items)).replace("__COUNTS__", html.escape(json.dumps(counts))))
    (root / "labels-auto.json").write_text(json.dumps({r["id"]: r["auto"] for r in rows}))
    return page


def _runs(ask):
    """One tile per run: the same slot on consecutive reads, reading the same
    digit (or none) with its ink within 0.03, is one decision. The tile is the
    run's first read; its label goes to every member."""
    out = []
    ask = sorted(ask, key=lambda r: (r["vid"], r["color"], r["slot"], r["t"]))
    for r in ask:
        prev = out[-1] if out else None
        if (prev is not None and (prev["vid"], prev["color"], prev["slot"]) == (r["vid"], r["color"], r["slot"])
                and prev["digit"] == r["digit"] and prev["old"] == r["old"]
                and abs(prev["last_ink"] - r["ink"]) <= 0.03):
            prev["members"].append(r["id"])
            prev["last_ink"] = r["ink"]
            continue
        out.append(dict(r, members=[r["id"]], last_ink=r["ink"]))
    return out


PAGE = r"""<!doctype html>
<meta charset="utf-8">
<title>Slot review</title>
<style>
  body { font: 14px/1.4 system-ui, sans-serif; margin: 0; padding: 12px 16px 80px; background: #f6f6f4; }
  h1 { font-size: 18px; margin: 0 0 4px; }
  p { margin: 4px 0 10px; color: #555; max-width: 90ch; }
  .grid { display: flex; flex-wrap: wrap; gap: 6px; }
  .tile { width: 112px; background: #fff; border: 3px solid #ccc; border-radius: 6px; padding: 3px; cursor: pointer; }
  .tile img { width: 100%; image-rendering: pixelated; display: block; }
  .tile .m { font-size: 11px; color: #555; white-space: nowrap; overflow: hidden; }
  .card { border-color: #1a8f3c; } .blank { border-color: #999; background: #eee; } .skip { border-color: #d68a00; }
  .done { outline: 2px solid #3b6fd8; }
  h2 { font-size: 15px; margin: 18px 0 6px; }
  #bar { position: fixed; bottom: 0; left: 0; right: 0; background: #222; color: #fff; padding: 8px 16px; display: flex; gap: 16px; align-items: center; }
  #bar button { font: inherit; padding: 4px 12px; }
  #board { position: fixed; top: 10px; right: 10px; max-width: 45vw; border: 2px solid #222; background: #fff; display: none; }
</style>
<h1>Scoreboard slots: card or blank?</h1>
<p>Green = card (a hung card with a digit), grey = blank (empty slot, a person or arm in front, glare, the board's edge), amber = skip (can't tell).
Click a tile to cycle. Each tile opens with a guess (the ones to decide: card where today's reader or a confident digit says so, else blank) -- fix the wrong ones; every tile on the page counts as reviewed once you save.
Hover a tile and press <b>b</b> to see the whole board it came from. Counts: <span id="counts"></span></p>
<div id="sections"></div>
<img id="board">
<div id="bar"><span id="stat"></span><button id="save">Save</button><span id="msg"></span></div>
<script>
const ITEMS = __DATA__;
const COUNTS = JSON.parse("__COUNTS__".replace(/&quot;/g, '"'));
document.getElementById("counts").textContent = `${COUNTS.ask_tiles} tiles to decide (${COUNTS.ask} slot reads), ${COUNTS.card} auto card, ${COUNTS.blank} auto blank, of ${COUNTS.total}`;
const KEY = "slotreview:" + location.pathname;
let state = {};
try { state = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) {}
const order = ["card", "blank", "skip"];
let hover = null;
function label(it) { return state[it.id] || it.guess; }
function render() {
  const secs = [["Decide these", ITEMS.filter(i => i.auto === "ask")],
                ["Spot-check: auto card", ITEMS.filter(i => i.auto === "card")],
                ["Spot-check: auto blank", ITEMS.filter(i => i.auto === "blank")]];
  const root = document.getElementById("sections"); root.innerHTML = "";
  for (const [title, its] of secs) {
    const h = document.createElement("h2"); h.textContent = `${title} (${its.length})`; root.appendChild(h);
    const g = document.createElement("div"); g.className = "grid"; root.appendChild(g);
    for (const it of its) {
      const d = document.createElement("div");
      d.className = "tile " + label(it) + (state[it.id] ? " done" : "");
      d.innerHTML = `<img src="win/${it.id}.png"><div class="m">${it.vid.slice(0,4)} ${Math.round(it.t)} ${it.color[0]}${it.slot}</div>` +
                    `<div class="m">d=${it.digit ?? "-"} ${it.conf.toFixed(4)} ink ${it.ink}${it.members.length > 1 ? " x" + it.members.length : ""}</div>`;
      d.onclick = () => { state[it.id] = order[(order.indexOf(label(it)) + 1) % 3]; persist(); render(); };
      d.onmouseenter = () => { hover = it; }; d.onmouseleave = () => { hover = null; };
      g.appendChild(d);
    }
  }
  const n = Object.keys(state).length;
  document.getElementById("stat").textContent = `${n} tiles touched`;
}
function persist() { try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) {} }
document.addEventListener("keydown", e => {
  const img = document.getElementById("board");
  if (e.key === "b" && hover) { img.src = hover.board; img.style.display = "block"; }
  if (e.key === "Escape") img.style.display = "none";
});
document.getElementById("save").onclick = async () => {
  const labels = {}; for (const it of ITEMS) for (const m of it.members) labels[m] = label(it);
  const r = await fetch("/save", { method: "POST", headers: { "Content-Type": "application/json" },
                                   body: JSON.stringify({ labels, reviewed: ITEMS.flatMap(i => i.members) }) });
  document.getElementById("msg").textContent = r.ok ? "saved " + (await r.text()) : "SAVE FAILED";
};
render();
</script>
"""


def serve(root: Path, port: int):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(root), **kw)

        def do_POST(self):
            if self.path != "/save":
                self.send_error(404)
                return
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            (root / "edits").mkdir(exist_ok=True)
            out = root / "edits" / f"slots-{time.strftime('%Y%m%d-%H%M%S')}.json"
            out.write_text(json.dumps(body))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(out.name.encode())

        def log_message(self, *a):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"http://127.0.0.1:{port}/", flush=True)
    httpd.serve_forever()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--port", type=int, default=8779)
    ap.add_argument("--build-only", action="store_true")
    args = ap.parse_args()
    root = Path(args.root).expanduser()
    page = build(root)
    print("page", page)
    if not args.build_only:
        serve(root, args.port)


if __name__ == "__main__":
    main()
