"""Label the harvested 2026-27 cards, ask a person about the rest, export.

    python scripts/cards2627/label.py build <harvest-dir> <manifest.json>
    python scripts/cards2627/label.py serve <harvest-dir> [--port 8787]
    python scripts/cards2627/label.py export <harvest-dir> <out-dir>

A physical card is one (source, game, colour, slot): within a game the board is
not cleared, so a card hangs untouched from its end to the game's close. Every
read of it shares one label, which comes from, in order:

* model     -- the shipped reader at >= 0.9999 in some read, every read at
               >= 0.99 agreeing, and the game's arithmetic not contradicting it;
* inferred  -- no confident read, but the arithmetic leaves one end: within a
               row the cumulative score rises with the end, so ends rise slot by
               slot, and no end appears twice on the board. Asked of a person.
* ask       -- anything else: no confident read and no unique end, confident
               reads that disagree, or a model label the arithmetic refuses.

Detection's per-end scores are checked against every label and shown on the
page, but never label a card: detection is the thing the board corrects.

`build` writes `cards.json` (one row per physical card) and `index.html`;
`serve` serves the page and saves edits to `edits/cards-<time>.json` -- copy
each to datasets/board-cards-2627/edits/ and commit it at once. `export`
writes the `cards.npz` `gate_digits.load_cards` reads, from the auto labels
overlaid with every saved edit; a card marked "x" (no card) is exported with
the label `no_card`, as the old set stores its phantoms.
"""
import argparse
import glob
import html
import json
import random
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CONF = 0.9999
AGREE = 0.99
SPOT_PER_DIGIT = 12
MAX_ODD = 8


def load_reads(root):
    rows = []
    for f in sorted(Path(root).glob("*/reads.jsonl")):
        rows += [json.loads(l) for l in open(f)]
    return rows


def physical_cards(reads):
    cards = {}
    for r in reads:
        key = (r["source_id"], r["game_index"], r["color"], r["slot"])
        cards.setdefault(key, []).append(r)
    out = []
    for key, rs in cards.items():
        rs.sort(key=lambda r: r["t_s"])
        confident = {r["digit"] for r in rs if r["digit"] is not None and r["conf"] >= CONF}
        agreeing = {r["digit"] for r in rs if r["digit"] is not None and r["conf"] >= AGREE}
        model = next(iter(confident)) if len(confident) == 1 and agreeing == confident else None
        out.append({"id": "%s_g%d_%s%02d" % (key[0], key[1], key[2][0], key[3]),
                    "source_id": key[0], "game_index": key[1], "color": key[2], "slot": key[3],
                    "video_id": rs[0]["video_id"], "sheet": rs[0]["sheet"], "date": rs[0]["date"],
                    "reads": [r["id"] for r in rs], "model": model,
                    "confident": sorted(confident), "agreeing": sorted(agreeing),
                    "best_conf": max(r["conf"] for r in rs),
                    "best_digit": max(rs, key=lambda r: r["conf"])["digit"]})
    return out


def _rows(cards):
    rows = {"yellow": [], "red": []}
    for c in cards:
        rows[c["color"]].append(c)
    for r in rows.values():
        r.sort(key=lambda c: c["slot"])
    return rows


def _fits(row_ends, used, slot, end):
    """Whether `end` can hang at `slot` beside a row's labelled (slot, end)
    pairs: ends rise slot by slot, and no end is on the board twice."""
    if end in used:
        return False
    return all((s < slot) == (e < end) for s, e in row_ends)


def arithmetic(cards, n_ends):
    """Label a game's cards from the board's own rules. Sets each card's
    `end` (None where unresolved) and `how`; returns how many model labels
    the rules refused.

    The model's labels are taken best-supported first (most reads), and one
    that clashes with those already taken is refused, not the whole game: a
    card moved mid-game or a read of the board being cleared is one card in
    one read, and must not cost every other card its label."""
    for c in cards:
        c["end"], c["how"] = None, None
    used, placed = set(), {"yellow": [], "red": []}
    refused = 0
    for c in sorted((c for c in cards if c["model"] is not None),
                    key=lambda c: (-len(c["reads"]), c["slot"])):
        if _fits(placed[c["color"]], used, c["slot"], c["model"]):
            c["end"], c["how"] = c["model"], "model"
            used.add(c["model"])
            placed[c["color"]].append((c["slot"], c["model"]))
        else:
            c["how"] = "refused"
            refused += 1
    rows = _rows([c for c in cards if c["how"] != "refused"])
    top = max([n_ends] + list(used))
    changed = True
    while changed:
        changed = False
        used = {c["end"] for c in cards if c["end"] is not None}
        for row in rows.values():
            i = 0
            while i < len(row):
                if row[i]["end"] is not None:
                    i += 1
                    continue
                j = i
                while j < len(row) and row[j]["end"] is None:
                    j += 1
                lo = row[i - 1]["end"] if i > 0 else 0
                hi = row[j]["end"] if j < len(row) else top + 1
                cand = [e for e in range(lo + 1, hi) if e not in used]
                if len(cand) == j - i:
                    for c, e in zip(row[i:j], cand):
                        c["end"], c["how"] = e, "inferred"
                    changed = True
                i = j
    return refused


def transient(c):
    """A slot seen once, with no confident digit and no end the arithmetic
    can give it: a hand, a person, or a card in motion, not a hung card."""
    return len(c["reads"]) == 1 and c["model"] is None and c["how"] != "inferred" \
        and c["best_conf"] < 0.9


def detection_slots(ends):
    """Each team's slot -> end, as detection's per-end scores would hang it."""
    out = {"yellow": {}, "red": {}}
    total = {"yellow": 0, "red": 0}
    for e in ends:
        s = e.get("detected") or {}
        for team in ("yellow", "red"):
            if s.get(team):
                total[team] += s[team]
                out[team][total[team]] = e["number"]
    return out


def build(root, manifest, seed=0):
    root = Path(root)
    games = {(g["source_id"], g["game_index"]): g for g in json.load(open(manifest))}
    reads = load_reads(root)
    by_id = {r["id"]: r for r in reads}
    cards = physical_cards(reads)
    per_game = {}
    for c in cards:
        per_game.setdefault((c["source_id"], c["game_index"]), []).append(c)
    refused_games = 0
    for key, cs in per_game.items():
        g = games[key]
        if arithmetic(cs, len(g["ends"])):
            refused_games += 1
        det = detection_slots(g["ends"])
        for c in cs:
            c["detected"] = det[c["color"]].get(c["slot"])
            c["title"] = g["title"]
            c["game_end_s"] = g["end_s"]
    for c in cards:
        c["auto"] = ("model" if c["how"] == "model" else
                     "transient" if transient(c) else "ask")
    rng = random.Random(seed)
    ask = [c for c in cards if c["auto"] == "ask"]
    spot = []
    for d in range(1, 10):
        pool = [c for c in cards if c["auto"] == "model" and c["end"] == d]
        spot += rng.sample(pool, min(SPOT_PER_DIGIT, len(pool)))
    items = []
    for c in sorted(ask, key=lambda c: (c["date"], c["sheet"], c["source_id"], c["game_index"], c["color"], c["slot"])) + spot:
        rs = [by_id[i] for i in c["reads"]]
        # Every read the model takes for another digit -- an arm or a person
        # in front, or the model wrong on a clean card, which only a person
        # can tell apart -- then the least and most confident and two between.
        order = sorted(rs, key=lambda r: r["conf"])
        odd = [r for r in rs if c["end"] is not None and r["digit"] not in (None, c["end"])
               and r["conf"] >= 0.5][:MAX_ODD]
        pick = odd + [order[0], order[len(order) // 3], order[2 * len(order) // 3], order[-1]]
        seen, shown = set(), []
        for r in pick:
            if r["id"] not in seen:
                seen.add(r["id"])
                shown.append(r)
        last = max(rs, key=lambda r: r["t_s"])
        items.append({"id": c["id"], "section": "ask" if c["auto"] == "ask" else "spot",
                      "guess": c["end"], "how": c["how"], "detected": c["detected"],
                      "title": c["title"], "color": c["color"], "slot": c["slot"],
                      "n": len(rs), "confident": c["confident"],
                      "wins": [{"id": r["id"], "src": f"{r['video_id']}/win/{r['id']}.png", "d": r["digit"],
                                "p": r["conf"], "t": r["t_s"]} for r in shown],
                      "board": f"{last['video_id']}/board/{last['video_id']}_{int(last['t_s']):06d}.jpg"})
    stats = {
        "reads": len(reads), "cards": len(cards), "games": len(per_game),
        "games_with_refusals": refused_games,
        "model": sum(c["how"] == "model" for c in cards),
        "inferred": sum(c["how"] == "inferred" for c in cards),
        "refused": sum(c["how"] == "refused" for c in cards),
        "unresolved": sum(c["how"] is None for c in cards),
        "transient": sum(c["auto"] == "transient" for c in cards),
        "ask": len(ask), "spot": len(spot),
        "model_vs_detection": [sum(1 for c in cards if c["how"] == "model" and c["detected"] == c["end"]),
                               sum(1 for c in cards if c["how"] == "model" and c["detected"] is not None)],
        "per_digit": {d: sum(1 for c in cards if c["end"] == d) for d in range(1, 10)},
    }
    (root / "cards.json").write_text(json.dumps(cards))
    (root / "index.html").write_text(PAGE.replace("__DATA__", json.dumps(items))
                                     .replace("__STATS__", html.escape(json.dumps(stats))))
    print(json.dumps(stats, indent=1))
    return stats


PAGE = r"""<!doctype html>
<meta charset="utf-8">
<title>Card review 2026-27</title>
<style>
  body { font: 14px/1.4 system-ui, sans-serif; margin: 0; padding: 12px 16px 80px; background: #f6f6f4; color: #222; }
  h1 { font-size: 18px; margin: 0 0 4px; } h2 { font-size: 15px; margin: 18px 0 6px; }
  p { margin: 4px 0 10px; color: #555; max-width: 100ch; }
  .grid { display: flex; flex-wrap: wrap; gap: 8px; }
  .card { background: #fff; border: 3px solid #ccc; border-radius: 6px; padding: 4px; width: 236px; }
  .card.set { border-color: #1a8f3c; } .card.x { border-color: #b3261e; background: #fbeaea; } .card.skip { border-color: #d68a00; }
  .card.hover { outline: 2px solid #3b6fd8; }
  .wins { display: flex; flex-wrap: wrap; gap: 3px; } .wins figure { margin: 0; width: 56px; cursor: pointer; position: relative; }
  .wins figure.bad img { opacity: .35; } .wins figure.bad::after { content: "\2715"; position: absolute; top: 2px; left: 0; right: 0;
    text-align: center; color: #b3261e; font: 700 30px/1 system-ui; }
  .wins img { width: 56px; image-rendering: pixelated; display: block; }
  .wins figcaption { font-size: 10px; color: #666; text-align: center; }
  .lab { font-size: 22px; font-weight: 600; } .m { font-size: 11px; color: #555; }
  #bar { position: fixed; bottom: 0; left: 0; right: 0; background: #222; color: #fff; padding: 8px 16px; display: flex; gap: 16px; align-items: center; }
  #bar button { font: inherit; padding: 4px 12px; }
  #board { position: fixed; top: 10px; right: 10px; width: 46vw; border: 2px solid #222; background: #fff; display: none; image-rendering: pixelated; }
</style>
<h1>Scoreboard cards, 2026-27: which end does each card say?</h1>
<p>Each box is one physical card: the same slot through one game, shown in up to four reads (least to most confident, the model's digit and confidence under each).
The big number is the proposed end -- from the arithmetic of the board ("inferred") or the model; "?" means nothing could be decided.
<b>Click a read</b> to mark it bad (an arm, a person, glare -- anything that isn't the card) and it is left out of training; the card's label still applies to its other reads.
Every read the model took for a different digit is shown, so a box can hold more than four.
A read cut through a card's edge -- half a digit at one side of the window -- comes from a board read whose slot grid was shifted: if that is the card's only read, mark the card <b>x</b>; if the card has good reads too, click the cut one bad. (Whole shifted reads are also left out of training automatically.)
Hover a box and press <b>1-9</b> to set the end, <b>x</b> if it is not a card (a hand, a person, glare), <b>s</b> to skip (can't tell), <b>Enter</b> to accept the proposal, <b>b</b> to see the board at the game's last read (Esc closes).
Detection's end for that slot is shown for reference only -- it is often wrong. Spot-check boxes are model labels: only change the wrong ones.</p>
<p id="stats"></p>
<div id="sections"></div>
<img id="board">
<div id="bar"><span id="stat"></span><button id="save">Save</button><span id="msg"></span></div>
<script>
const ITEMS = __DATA__;
const STATS = JSON.parse("__STATS__".replace(/&quot;/g, '"'));
document.getElementById("stats").textContent =
  `${STATS.cards} cards in ${STATS.games} games (${STATS.reads} reads): ${STATS.model} by the model, ${STATS.inferred} inferred, ` +
  `${STATS.refused} refused by the arithmetic, ${STATS.unresolved} unresolved (${STATS.transient} of them seen once and dropped). Model vs detection ${STATS.model_vs_detection[0]}/${STATS.model_vs_detection[1]}.`;
const KEY = "cardreview2627:" + location.pathname;
let state = {}, bad = {};
try { state = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) {}
try { bad = JSON.parse(localStorage.getItem(KEY + ":bad") || "{}"); } catch (e) {}
let hover = null;
function persist() { try { localStorage.setItem(KEY, JSON.stringify(state));
                          localStorage.setItem(KEY + ":bad", JSON.stringify(bad)); } catch (e) {} }
function shown(it) { const s = state[it.id]; return s === undefined ? (it.guess ?? "?") : s; }
function cls(it) { const s = state[it.id]; return s === undefined ? "" : s === "x" ? "x" : s === "skip" ? "skip" : "set"; }
function render() {
  const root = document.getElementById("sections"); root.innerHTML = "";
  for (const [sec, title] of [["ask", "Decide these"], ["spot", "Spot-check: the model's labels"]]) {
    const its = ITEMS.filter(i => i.section === sec);
    const h = document.createElement("h2"); h.textContent = `${title} (${its.length})`; root.appendChild(h);
    const g = document.createElement("div"); g.className = "grid"; root.appendChild(g);
    for (const it of its) {
      const d = document.createElement("div");
      d.className = "card " + cls(it) + (hover === it ? " hover" : "");
      d.innerHTML = `<div class="wins">` + it.wins.map(w =>
          `<figure data-read="${w.id}" class="${bad[w.id] ? "bad" : ""}"><img src="${w.src}"><figcaption>${w.d ?? "-"} ${w.p.toFixed(4)}</figcaption></figure>`).join("") + `</div>` +
        `<div><span class="lab">${shown(it)}</span> <span class="m">${it.how ?? "unresolved"}; det ${it.detected ?? "-"}; ${it.n} reads</span></div>` +
        `<div class="m">${it.title.replace(" League 2026-2027", "")} g${it.id.split("_g")[1].split("_")[0]} ${it.color} slot ${it.slot}</div>`;
      d.querySelectorAll("figure").forEach(f => f.onclick = () => {
        const id = f.dataset.read;
        if (bad[id]) delete bad[id]; else bad[id] = true;
        f.classList.toggle("bad", !!bad[id]); persist();
      });
      d.onmouseenter = () => { hover = it; d.classList.add("hover"); };
      d.onmouseleave = () => { if (hover === it) hover = null; d.classList.remove("hover"); };
      g.appendChild(d);
    }
  }
  document.getElementById("stat").textContent = `${Object.keys(state).length} of ${ITEMS.length} decided, ${Object.keys(bad).length} reads marked bad`;
}
document.addEventListener("keydown", e => {
  const img = document.getElementById("board");
  if (e.key === "Escape") { img.style.display = "none"; return; }
  if (!hover) return;
  if (e.key === "b") { img.src = hover.board; img.style.display = "block"; return; }
  if (/^[1-9]$/.test(e.key)) state[hover.id] = Number(e.key);
  else if (e.key === "x") state[hover.id] = "x";
  else if (e.key === "s") state[hover.id] = "skip";
  else if (e.key === "Enter" && hover.guess != null) state[hover.id] = hover.guess;
  else return;
  persist(); const keep = hover; render(); hover = keep;
});
document.getElementById("save").onclick = async () => {
  const r = await fetch("/save", { method: "POST", headers: { "Content-Type": "application/json" },
                                   body: JSON.stringify({ labels: state, bad_reads: Object.keys(bad) }) });
  document.getElementById("msg").textContent = r.ok ? "saved " + (await r.text()) : "SAVE FAILED";
};
render();
</script>
"""


def serve(root, port):
    root = Path(root)

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(root), **kw)

        def do_POST(self):
            if self.path != "/save":
                self.send_error(404)
                return
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            (root / "edits").mkdir(exist_ok=True)
            out = root / "edits" / f"cards-{time.strftime('%Y%m%d-%H%M%S')}.json"
            out.write_text(json.dumps(body))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(out.name.encode())

        def log_message(self, *a):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"http://127.0.0.1:{port}/", flush=True)
    httpd.serve_forever()


def final_labels(root):
    """Each card's label: the auto `model` label, overlaid with every saved
    edit in time order. Inferred and unresolved cards count only once a
    person has decided them; "skip" drops a card. Also returns the reads a
    person marked bad, which no card exports."""
    root = Path(root)
    cards = json.loads((root / "cards.json").read_text())
    labels = {c["id"]: c["end"] for c in cards if c["how"] == "model"}
    bad = set()
    for f in sorted(glob.glob(str(root / "edits" / "cards-*.json"))):
        edit = json.loads(Path(f).read_text())
        for k, v in edit["labels"].items():
            labels[k] = v
        # Each save carries the page's whole set, so the latest one is it.
        bad = set(edit.get("bad_reads", []))
    return cards, {k: v for k, v in labels.items() if v not in (None, "skip")}, bad


def shifted_reads(cards, reads):
    """Board reads whose slot grid looks shifted: two or more of their slots
    hold a "card" seen in no more than two reads. When `find_board` anchors a
    grid half a slot off (a person in front of a team marker), every window in
    that read straddles two slots, so cards appear for one read where none
    hang. About 0.7% of reads; a genuinely new card or two among them costs
    nothing but a frame."""
    n_reads = {rid: len(c["reads"]) for c in cards for rid in c["reads"]}
    by = {}
    for r in reads.values():
        by.setdefault((r["source_id"], r["game_index"], r["t_s"]), []).append(r)
    out = set()
    for rs in by.values():
        if sum(1 for r in rs if n_reads.get(r["id"], 0) <= 2) >= 2:
            out |= {r["id"] for r in rs}
    return out


def export(root, out):
    import cv2
    import numpy as np

    root, out = Path(root), Path(out)
    cards, labels, bad = final_labels(root)
    reads = {r["id"]: r for r in load_reads(root)}
    reviewed = set()
    for f in glob.glob(str(root / "edits" / "cards-*.json")):
        reviewed |= set(json.loads(Path(f).read_text())["labels"])
    dropped = 0
    shifted = shifted_reads(cards, reads)
    n_shifted = 0
    H, W = 38, 26
    cols = {k: [] for k in ("patches", "shape", "video_id", "date", "sheet", "frame",
                            "t_s", "color", "slot", "end", "card_id")}
    for c in cards:
        if c["id"] not in labels:
            continue
        lab = labels[c["id"]]
        end = "no_card" if lab == "x" else str(int(lab))
        for rid in c["reads"]:
            r = reads[rid]
            if rid in bad:
                continue
            # A shifted read straddles slots: no digit is trained from it,
            # though a card marked "x" keeps it as a phantom to measure.
            if rid in shifted and lab != "x":
                n_shifted += 1
                continue
            # A card nobody looked at keeps only the reads that agree with it
            # or read nothing: a read of another digit there is an arm or a
            # person as often as a hard card, and no one has said which.
            if (c["id"] not in reviewed and lab != "x" and r["digit"] is not None
                    and r["digit"] != int(lab)):
                dropped += 1
                continue
            win = cv2.imread(str(root / r["video_id"] / "win" / f"{rid}.png"), cv2.IMREAD_GRAYSCALE)
            if win is None:
                continue
            h, w = win.shape
            if h > H or w > W:
                continue
            patch = np.zeros((H, W), np.uint8)
            patch[:h, :w] = win
            cols["patches"].append(patch)
            cols["shape"].append((h, w))
            cols["video_id"].append(r["video_id"])
            cols["date"].append(r["date"])
            cols["sheet"].append(r["sheet"])
            cols["frame"].append(rid)
            cols["t_s"].append(r["t_s"])
            cols["color"].append(r["color"])
            cols["slot"].append(r["slot"])
            cols["end"].append(end)
            cols["card_id"].append(c["id"])
    out.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        out / "cards.npz",
        patches=np.stack(cols["patches"]), shape=np.array(cols["shape"], np.int16),
        video_id=np.array(cols["video_id"]), date=np.array(cols["date"]),
        sheet=np.array(cols["sheet"], np.int16), frame=np.array(cols["frame"]),
        t_s=np.array(cols["t_s"], np.float32), color=np.array(cols["color"]),
        slot=np.array(cols["slot"], np.int16), end=np.array(cols["end"]),
        card_id=np.array(cols["card_id"]))
    n_cards = len({c for c in cols["card_id"]})
    print(f"wrote {out / 'cards.npz'}: {len(cols['end'])} rows, {n_cards} cards; "
          f"{len(bad)} reads marked bad, {n_shifted} windows of shifted board "
          f"reads and {dropped} disagreeing reads of unreviewed cards left out")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build"); b.add_argument("root"); b.add_argument("manifest")
    s = sub.add_parser("serve"); s.add_argument("root"); s.add_argument("--port", type=int, default=8787)
    e = sub.add_parser("export"); e.add_argument("root"); e.add_argument("out")
    args = ap.parse_args()
    if args.cmd == "build":
        build(args.root, args.manifest)
    elif args.cmd == "serve":
        serve(args.root, args.port)
    else:
        export(args.root, args.out)


if __name__ == "__main__":
    main()
