"""Build a labelling wave for the stone handle: two points per frame, the neck
(where the handle rises out of the stone, its pivot) and the grip end (the free
end that swings round as the stone turns).

    python scripts/spin/wave.py --out ~/curling-work/spin/wave1 \\
        --manifest datasets/spin/manifest-wave1.json --scope spin:wave1 \\
        --game PHbZ3EKOhMI:s_0N8Q2sB4vY8Hv4Ooq:~/curling-work/spin-spike/out/PHbZ \\
        --game ...

Each --game is VIDEO:SLUG:DIR, DIR holding extract.py's per-rock pickles.
Frames come from after the hand is off (8 m past the throwing tee) and from
different stretches of the path, so the set spans the stone's whole range of
sizes (~45 px wide far off, ~95 px near the bottom of the frame). Each frame
gets a one-second animation of its neighbours: in it the neck stays put and
the grip end swings, which is how a person tells one end from the other.

Writes OUT/images/<stem>.png (the crop, native pixels), OUT/anim/<stem>.png
(the animation strip), OUT/items.json and OUT/index.html, and the manifest
(every frame's video, time, box and crop origin, so the pixels can be cut
again from the video). Serve OUT with scripts/spin/serve.py.
"""
import argparse
import gzip
import json
import pickle
import random
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
import page  # noqa: E402

Y_START_M = 8.0                       # past the throwing tee: the hand is off by here
BINS_M = ((8.0, 14.0), (14.0, 20.0), (20.0, 27.0))   # far, mid, near
FRAME_BOTTOM_ROW = 1066               # a box this low is leaving the frame
WIDTH_TOL = 0.10                      # off the stone's width-vs-row line by more is a box glitch
ANIM_OFFSETS_S = tuple(round(-0.5 + 0.1 * i, 1) for i in range(11))
ANIM_STONE_PX = 120                   # stone width in each animation frame
ANIM_CELL = (200, 170)                # w, h of one animation frame


def usable(recs):
    """The frames worth labelling: past the hand, inside the frame, and whose
    box fits the stone's own width-for-row line (the detector's odd frame,
    a box swallowing a broom head, is dropped rather than labelled)."""
    cand = [r for r in recs if r["y_m"] >= Y_START_M and r["box"][3] < FRAME_BOTTOM_ROW]
    if len(cand) < 10:
        return []
    by = np.array([r["box"][3] for r in cand], float)
    w = np.array([r["box"][2] - r["box"][0] for r in cand], float)
    A = np.vstack([by, np.ones_like(by)]).T
    keep = np.ones(len(cand), bool)
    for _ in range(3):
        coef, *_ = np.linalg.lstsq(A[keep], w[keep], rcond=None)
        keep = np.abs(w / (A @ coef) - 1) < WIDTH_TOL
    return [r for r, k in zip(cand, keep) if k]


def nearest(recs, t, tol=0.05):
    """The record closest to time ``t``, or None when none is within ``tol`` s."""
    best = min(recs, key=lambda r: abs(r["t"] - t))
    return best if abs(best["t"] - t) <= tol else None


def pick(recs, n, rng, rotate=0):
    """Up to ``n`` frames from different stretches of the path, each with its
    whole animation window present. ``rotate`` staggers which stretches a rock
    gives when ``n`` is less than three, so the wave as a whole stays even."""
    ok = usable(recs)
    if not ok:
        return []
    ts = sorted(r["t"] for r in recs)
    lo, hi = ts[0] - ANIM_OFFSETS_S[0], ts[-1] - ANIM_OFFSETS_S[-1]
    by_bin = [[r for r in ok if a <= r["y_m"] < b and lo <= r["t"] <= hi] for a, b in BINS_M]
    order = [(i + rotate) % len(BINS_M) for i in range(len(BINS_M))]
    out = []
    for i in order:
        if len(out) == n:
            break
        if by_bin[i]:
            out.append(rng.choice(by_bin[i]))
    return sorted(out, key=lambda r: r["t"])


def stem_for(video, meta, rec):
    return f"{video}_e{meta['end']}s{meta['shot']:02d}_{int(round(rec['t'] * 100)):07d}"


def anim_strip(recs, rec):
    """The neighbours at ANIM_OFFSETS_S, each scaled so the stone is
    ANIM_STONE_PX wide and placed on its box's centre and bottom."""
    cw, ch = ANIM_CELL
    strip = Image.new("RGB", (cw * len(ANIM_OFFSETS_S), ch), (32, 32, 32))
    for k, dt in enumerate(ANIM_OFFSETS_S):
        r = nearest(recs, rec["t"] + dt)
        if r is None:
            continue
        x0, y0, x1, y1 = r["box"]
        ox, oy = r["origin"]
        sc = ANIM_STONE_PX / (x1 - x0)
        im = Image.fromarray(r["patch"])
        im = im.resize((max(1, round(im.width * sc)), max(1, round(im.height * sc))), Image.LANCZOS)
        cx, by = ((x0 + x1) / 2 - ox) * sc, (y1 - oy) * sc
        cell = Image.new("RGB", (cw, ch), (32, 32, 32))
        cell.paste(im, (round(cw / 2 - cx), round(ch - 12 - by)))
        strip.paste(cell, (k * cw, 0))
    return strip


def build(games, out, manifest, scope, per_rock=2, seed=1):
    out = Path(out).expanduser()
    (out / "images").mkdir(parents=True, exist_ok=True)
    (out / "anim").mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    items = []
    for video, slug, directory in games:
        paths = sorted(Path(directory).expanduser().glob("e*s*.pkl.gz"))
        for k, p in enumerate(paths):
            d = pickle.load(gzip.open(p))   # extract.py's own output, written by us
            meta, recs = d["meta"], d["recs"]
            for rec in pick(recs, per_rock, rng, rotate=k):
                stem = stem_for(video, meta, rec)
                Image.fromarray(rec["patch"]).save(out / "images" / f"{stem}.png")
                anim_strip(recs, rec).save(out / "anim" / f"{stem}.png")
                x0, y0, x1, y1 = rec["box"]
                ox, oy = rec["origin"]
                items.append(dict(
                    stem=stem, video=video, slug=slug, end=meta["end"], shot=meta["shot"],
                    color=meta["color"], view=meta["view"], t=round(rec["t"], 3),
                    y_m=round(rec["y_m"], 2), box=[round(v, 1) for v in rec["box"]],
                    origin=[int(ox), int(oy)], size=[int(rec["patch"].shape[1]), int(rec["patch"].shape[0])],
                    stone_box=[round(x0 - ox, 1), round(y0 - oy, 1), round(x1 - ox, 1), round(y1 - oy, 1)]))
    (out / "items.json").write_text(json.dumps(items, indent=1) + "\n")
    page.render(out, items, scope)
    if manifest:
        Path(manifest).write_text(json.dumps(dict(
            scope=scope, seed=seed, per_rock=per_rock, y_start_m=Y_START_M, bins_m=BINS_M,
            games=[dict(video=v, slug=s) for v, s, _ in games],
            items=[{k: v for k, v in it.items() if k != "stone_box"} for it in items]),
            indent=1) + "\n")
    return items


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", action="append", required=True, help="VIDEO:SLUG:PICKLE_DIR")
    ap.add_argument("--out", required=True)
    ap.add_argument("--manifest")
    ap.add_argument("--scope", required=True)
    ap.add_argument("--per-rock", type=int, default=2)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    games = [tuple(g.split(":", 2)) for g in args.game]
    items = build(games, args.out, args.manifest, args.scope, args.per_rock, args.seed)
    per_game = {}
    for it in items:
        per_game[it["video"]] = per_game.get(it["video"], 0) + 1
    print(f"{len(items)} frames: {per_game}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
