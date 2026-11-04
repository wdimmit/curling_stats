#!/usr/bin/env python
"""mv1: pick, cut and pre-label 100 Martensville overhead frames for the hand review.

Martensville's houses are a brewery's logo, and overhead-3 reads parts of it --
the hop cones under the face, a letter of the band -- as yellow stones at
0.8-0.9, in both houses, all game (ZJG9RZ69Wg4, 2026-11-04). The house never
empties and the ends run together. From what `scripts/cin1/mine.py` saw
overhead-3 box every 2 s:

  static  the spots the phantoms sit on: a cell of the panel that holds a sure
          box in at least STATIC_SHARE of the frames -- no stone stays put a
          whole game
  empty   a house with only static boxes in it: the phantoms alone, worst first
  play    a house with stones in play, the busiest and those with a stone on
          or by a phantom's spot first, so a real yellow on the logo stays in

Each kind and panel is spread over the stream: its candidates are cut into as
many equal-count runs as it has frames, and each run gives its strongest frame.
Pre-labels are overhead-3's boxes at PRELABEL_CONF.

    python scripts/mv1/build.py --mined mined.json --video VIDEO --weights overhead-3.pt \\
        --out DIR --manifest datasets/mv1/manifest.json
    python scripts/mv1/build.py --render-only --out DIR --manifest datasets/mv1/manifest.json
"""

import argparse
import json
import math
from collections import Counter
from pathlib import Path

SHARES = {("empty", "top"): 15, ("empty", "bottom"): 15, ("play", "top"): 35, ("play", "bottom"): 35}
PRELABEL_CONF, IMGSZ = 0.25, 448
SURE = 0.3  # a box that counts when choosing
CELL_PX = 12  # static spots are found on this grid
STATIC_SHARE = 0.3
NEAR_SPOT_PX = 40  # a real stone this near a phantom's spot is worth having
BOX_ALL = ("Box EVERY stone in the frame, including any partly hidden under a person, a broom or a foot, "
           "and reject every box that is not a stone -- above all the yellow boxes on the brewery logo itself "
           "(the green hop cones under the face, the letters of the yellow band) -- and heads, shoes and brooms.")


def centre(b):
    x0, y0, x1, y1 = b["xyxy"]
    return (x0 + x1) / 2, (y0 + y1) / 2


def cell(b):
    x, y = centre(b)
    return int(x // CELL_PX), int(y // CELL_PX)


def static_cells(frames):
    """Per panel, the grid cells (and their neighbours) a sure box sits in for
    at least STATIC_SHARE of the frames."""
    out = {}
    for panel in ("top", "bottom"):
        fs = [f for f in frames if f["panel"] == panel]
        seen = Counter()
        for f in fs:
            seen.update({cell(b) for b in f["boxes"] if b["conf"] >= SURE})
        hot = {c for c, n in seen.items() if n >= STATIC_SHARE * len(fs)}
        out[panel] = {(cx + dx, cy + dy) for cx, cy in hot for dx in (-1, 0, 1) for dy in (-1, 0, 1)}
    return out


def spots_px(static):
    """Each panel's static spots as pixel centres, for the near-a-spot score."""
    return {p: [((cx + 0.5) * CELL_PX, (cy + 0.5) * CELL_PX) for cx, cy in cells] for p, cells in static.items()}


def kind_of(f, static, spots):
    sure = [b for b in f["boxes"] if b["conf"] >= SURE]
    phantom = [b for b in sure if cell(b) in static[f["panel"]]]
    real = [b for b in sure if cell(b) not in static[f["panel"]]]
    if not real:
        return ("empty", sum(b["conf"] for b in phantom)) if phantom else (None, 0)
    near = sum(1 for b in real
               if min(math.dist(centre(b), s) for s in spots[f["panel"]] or [(1e9, 1e9)]) <= NEAR_SPOT_PX)
    return "play", len(real) + 2 * near


def pick(frames):
    static = static_cells(frames)
    spots = spots_px(static)
    pools = {k: [] for k in SHARES}
    for f in frames:
        k, score = kind_of(f, static, spots)
        if k:
            pools[(k, f["panel"])].append((f["t"], score, f))
    chosen = []
    for (k, panel), n in SHARES.items():
        pool = sorted(pools[(k, panel)], key=lambda c: c[0])
        for i in range(n):
            run = pool[i * len(pool) // n:(i + 1) * len(pool) // n]
            if run:
                t, score, f = max(run, key=lambda c: c[1])
                chosen.append({"kind": k, "panel": panel, "t": t, "score": round(score, 3),
                               "mined_boxes": len([b for b in f["boxes"] if b["conf"] >= SURE]),
                               "candidates": len(pool)})
    return chosen, {p: sorted(c) for p, c in static.items()}


def stem_for(vid, panel, t):
    return f"{vid}_{panel[0]}_{t:09.2f}".replace(".", "_")


def note_for(f):
    if f["kind"] == "empty":
        why = (f"the {f['panel']} house between throws: every box overhead-3 drew here ({f['prelabels']} at "
               f"{PRELABEL_CONF}+) sits on the logo. Reject each one that is the logo, not a stone")
    else:
        why = f"the {f['panel']} house in play: overhead-3 drew {f['prelabels']} boxes at {PRELABEL_CONF}+"
    return f"{why}. {BOX_ALL}"


def build(a):
    import cv2

    from curling_score.detect import yolo
    from curling_score.ingest import frames as F

    mined = json.loads(a.mined.read_text())
    out = a.out.expanduser()
    img_dir, lbl_dir = out / "set/images/train", out / "set/labels/train"
    for d in (img_dir, lbl_dir):
        d.mkdir(parents=True, exist_ok=True)
    det = yolo.YoloDetector(a.weights, conf=PRELABEL_CONF, imgsz=IMGSZ, device=0)
    vid = mined["video"]
    chosen, static = pick(mined["frames"])
    frames_out = []
    for c in chosen:
        rect = tuple(mined["rects"][c["panel"]])
        tt, img = next(iter(F.window(a.video, c["t"], c["t"] + 0.2, fps=30.0, crop=rect)), (None, None))
        if img is None:
            print("no frame", c["t"])
            continue
        stem = stem_for(vid, c["panel"], tt)
        cv2.imwrite(str(img_dir / f"{stem}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 95])
        h, w = img.shape[:2]
        res = det.model.predict(img, conf=PRELABEL_CONF, imgsz=IMGSZ, verbose=False)[0]
        rows = [f"{int(k)} {(x0 + x1) / 2 / w:.6f} {(y0 + y1) / 2 / h:.6f} {(x1 - x0) / w:.6f} {(y1 - y0) / h:.6f}"
                for k, (x0, y0, x1, y1) in zip(res.boxes.cls.tolist(), res.boxes.xyxy.tolist())]
        (lbl_dir / f"{stem}.txt").write_text("\n".join(rows) + ("\n" if rows else ""))
        frames_out.append({**c, "video": vid, "stem": stem, "t": round(tt, 3), "prelabels": len(rows)})
        print(f"{stem} {c['kind']}: {len(rows)} boxes", flush=True)
    manifest = {"what": a.title, "scope": a.scope,
                "prelabel": {"weights": Path(a.weights).name, "conf": PRELABEL_CONF, "imgsz": IMGSZ,
                             "classes": {0: "red_stone", 1: "yellow_stone"}},
                "selection": {"shares": {f"{k}-{p}": n for (k, p), n in SHARES.items()}, "sure_conf": SURE,
                              "cell_px": CELL_PX, "static_share": STATIC_SHARE, "static_cells": static,
                              "mined_every_s": mined["every_s"]},
                "by_kind": dict(Counter(f"{f['kind']}-{f['panel']}" for f in frames_out)), "frames": frames_out}
    a.manifest.parent.mkdir(parents=True, exist_ok=True)
    a.manifest.write_text(json.dumps(manifest, indent=1))
    print(f"{len(frames_out)} frames: {manifest['by_kind']}")


def render(out, manifest):
    """The review pages, from the set on disk and the manifest's notes. Labels
    are not touched, so a review in progress keeps every edit."""
    from curling_score.train import labels

    meta = {f["stem"]: labels.FrameMeta(note=note_for(f), url=f"https://youtu.be/{f['video']}?t={int(f['t'])}")
            for f in manifest["frames"]}
    frames = [f for seq in labels.load_split(out / "set", "train").values() for f in seq]
    page, n = labels.render_clickable(frames, out / "review", title=manifest["what"],
                                      scope=manifest["scope"], per_page=200, meta=meta)
    print(f"{len(frames)} frames rendered: {page}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mined", type=Path)
    ap.add_argument("--video", type=Path)
    ap.add_argument("--weights")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--render-only", action="store_true")
    ap.add_argument("--scope", default="mv1:train")
    ap.add_argument("--title", default="mv1: Martensville's logo houses (ZJG9RZ69Wg4)")
    a = ap.parse_args()
    if not a.render_only:
        build(a)
        return
    render(a.out.expanduser(), json.loads(a.manifest.read_text()))


if __name__ == "__main__":
    main()
