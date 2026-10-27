#!/usr/bin/env python
"""cin1: pick, cut and pre-label 100 Cincinnati overhead frames for the hand review.

From what `mine.py` saw ds17b box every 2 s:

  red-empty  the red house with only boxes on its 12-to-8 band, where the
             sponsor's lettering is: the phantom stones, worst first
  red-play   the red house with stones in play; busy houses and confident
             red boxes on the band first, so real reds on red paint are in
  blue-play  the blue house with stones in play, so the tune keeps it

Each kind is spread over the stream: its candidates are cut into as many
equal-count runs as it has frames, and each run gives its strongest frame.

Pre-labels are ds17b's boxes at PRELABEL_CONF. At 0.05, as the Granite waves
were pre-labelled, the red house carries ~14 lettering boxes a frame; real
stones here score 0.8 and up, and a missed one is for the reviewer to add.

    python scripts/cin1/build.py --mined mined.json --video VIDEO --weights ds17b.pt \
        --out DIR --manifest datasets/cin1/manifest.json
    python scripts/cin1/build.py --render-only --out DIR --manifest datasets/cin1/manifest.json
"""

import argparse
import json
from collections import Counter
from pathlib import Path

SHARES = {"red-empty": 20, "red-play": 50, "blue-play": 30}
PANEL = {"red-empty": "top", "red-play": "top", "blue-play": "bottom"}
PRELABEL_CONF, IMGSZ = 0.25, 448
SURE = 0.3  # a box that counts when choosing
BAND_M = (1.15, 1.95)  # the 12-to-8 band, with the boxes' scatter
BOX_ALL = ("Box EVERY stone in the frame, including any partly hidden under a person, a broom or a foot, "
           "and reject every box that is not a stone -- above all the sponsor's red \"Cincy Light\" lettering "
           "around the red house, and logos, heads, shoes and brooms.")


def on_band(b):
    return BAND_M[0] <= b["r"] <= BAND_M[1]


def kind_of(f):
    sure = [b for b in f["boxes"] if b["conf"] >= SURE]
    if f["panel"] == "bottom":
        return ("blue-play", len(sure)) if sure else (None, 0)
    off = [b for b in sure if not (on_band(b) and b["color"] == "red")]
    band = [b["conf"] for b in sure if on_band(b) and b["color"] == "red"]
    if not off:
        return ("red-empty", sum(band)) if band else (None, 0)
    return "red-play", len(sure) + max(band, default=0.0)


def pick(frames):
    pools = {k: [] for k in SHARES}
    for f in frames:
        k, score = kind_of(f)
        if k:
            pools[k].append((f["t"], score, f))
    chosen = []
    for k, n in SHARES.items():
        pool = sorted(pools[k], key=lambda c: c[0])
        for i in range(n):
            run = pool[i * len(pool) // n:(i + 1) * len(pool) // n]
            if run:
                t, score, f = max(run, key=lambda c: c[1])
                chosen.append({"kind": k, "panel": f["panel"], "t": t, "score": round(score, 3),
                               "mined_boxes": len([b for b in f["boxes"] if b["conf"] >= SURE]),
                               "candidates": len(pool)})
    return chosen


def stem_for(vid, panel, t):
    return f"{vid}_{panel[0]}_{t:09.2f}".replace(".", "_")


def note_for(f):
    house = "red" if f["panel"] == "top" else "blue"
    if f["kind"] == "red-empty":
        why = (f"the red house between throws: every box ds17b drew here ({f['prelabels']} at {PRELABEL_CONF}+) sits "
               f"on the sponsor's band. Reject each one that is lettering, not a stone")
    else:
        why = f"the {house} house in play: ds17b drew {f['prelabels']} boxes at {PRELABEL_CONF}+"
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
    frames_out = []
    for c in pick(mined["frames"]):
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
                "selection": {"shares": SHARES, "sure_conf": SURE, "band_m": BAND_M,
                              "mined_every_s": mined["every_s"]},
                "by_kind": dict(Counter(f["kind"] for f in frames_out)), "frames": frames_out}
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
    ap.add_argument("--scope", default="cin1:train")
    ap.add_argument("--title", default="cin1: Cincinnati's houses (rKiMmtVod0I)")
    a = ap.parse_args()
    if not a.render_only:
        build(a)
        return
    render(a.out.expanduser(), json.loads(a.manifest.read_text()))


if __name__ == "__main__":
    main()
