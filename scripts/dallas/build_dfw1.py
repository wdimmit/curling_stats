#!/usr/bin/env python
"""dfw1: pick, cut and pre-label 100 DFW overhead frames for the hand review.

DFW Curling Club plays green and yellow stones (vvy26k01ZcI, 2026-11-06), and
overhead-3 has only ever seen red and yellow: it misses the greens or reads
them as yellow. Green is to be the red_stone class. The images are cut from
the normalised video in its true colours (`normalize.py`, what analyze
reads); the pre-labels are overhead-3's boxes on the same frame of the
green-to-red copy, which it reads at 0.87-0.95 -- pixel for pixel the same
frame, so the boxes carry over.

From what `scripts/cin1/mine.py` saw overhead-3 box every 2 s on the
green-to-red copy:

  empty  a house with no sure box: what green jackets, brooms and the club's
         logo on the button must not become
  play   a house with stones in it, the busiest and the most doubtful first
         (boxes between DOUBT_LO and SURE count double)

A score-view frame shows one house; the other panel there is the empty plate
the normaliser filled in, not a picture, and is never chosen. Nor is a frame
the normaliser repeated over a dissolve. Each kind and panel is spread over
the stream as in mv1: equal-count runs, the strongest frame of each.

    python build_dfw1.py --mined mined.json --layout norm/c0.json ... --video full.mp4 \\
        --g2r full-g2r.mp4 --weights overhead-3.pt --out DIR --manifest manifest.json
    python build_dfw1.py --render-only --out DIR --manifest manifest.json
"""

import argparse
import json
from collections import Counter
from pathlib import Path

SHARES = {("empty", "top"): 10, ("empty", "bottom"): 10, ("play", "top"): 40, ("play", "bottom"): 40}
PRELABEL_CONF, IMGSZ = 0.25, 448
SURE, DOUBT_LO = 0.5, 0.1
GUARD_S = 1.0  # stay this far from a layout change
BOX_ALL = ("GREEN stones are the RED class: box each green stone as red (r), and every yellow as yellow (y). "
           "Box EVERY stone in the frame, including any partly hidden under a person, a broom or a foot, and "
           "any cut off at the panel's edge. Reject every box that is not a stone -- green or yellow jackets, "
           "shoes, broom heads, the club's logo on the button -- and fix any green stone boxed as yellow.")


def load_layout(paths):
    """Per-frame layout over the whole video, from the normaliser's chunk logs, in order."""
    kinds, fps = [], 30.0
    for p in paths:
        d = json.loads(Path(p).read_text())
        fps = d["fps"]
        kinds += d["kinds"]
    return kinds, fps


def usable(t, panel, kinds, fps):
    i = int(round(t * fps))
    lo, hi = max(0, i - int(GUARD_S * fps)), min(len(kinds), i + int(GUARD_S * fps) + 1)
    near = set(kinds[lo:hi])
    if len(near) != 1:
        return None  # a dissolve or a cut nearby
    k = near.pop()
    if k == "?":
        return None
    if (k == "S0" and panel == "bottom") or (k == "S1" and panel == "top"):
        return None  # the plate, not a picture
    return k


def kind_of(f):
    sure = [b for b in f["boxes"] if b["conf"] >= SURE]
    doubt = [b for b in f["boxes"] if DOUBT_LO <= b["conf"] < SURE]
    if not sure:
        return ("empty", -len(doubt)) if not doubt else (None, 0)
    return "play", len(sure) + 2 * len(doubt)


def pick(frames, kinds, fps):
    pools = {k: [] for k in SHARES}
    for f in frames:
        layout = usable(f["t"], f["panel"], kinds, fps)
        if layout is None:
            continue
        k, score = kind_of(f)
        if k:
            pools[(k, f["panel"])].append((f["t"], score, f, layout))
    chosen = []
    for (k, panel), n in SHARES.items():
        pool = sorted(pools[(k, panel)], key=lambda c: c[0])
        for i in range(n):
            run = pool[i * len(pool) // n:(i + 1) * len(pool) // n]
            if run:
                t, score, f, layout = max(run, key=lambda c: c[1])
                chosen.append({"kind": k, "panel": panel, "t": t, "score": score, "layout": layout,
                               "mined_sure": len([b for b in f["boxes"] if b["conf"] >= SURE]),
                               "candidates": len(pool)})
    return chosen


def stem_for(vid, panel, t):
    return f"{vid}_{panel[0]}_{t:09.2f}".replace(".", "_")


def note_for(f):
    view = "three-panel view" if f["layout"] == "T" else "score view, scaled down"
    if f["kind"] == "empty":
        why = f"the {f['panel']} house with no sure stone in it ({view}); overhead-3 drew {f['prelabels']} boxes"
    else:
        why = f"the {f['panel']} house in play ({view}); overhead-3 drew {f['prelabels']} boxes at {PRELABEL_CONF}+"
    return f"{why}. {BOX_ALL}"


def _frame(cap, i, rect):
    import cv2
    cap.set(cv2.CAP_PROP_POS_FRAMES, i)
    ok, f = cap.read()
    if not ok:
        return None
    x, y, w, h = rect
    return f[y:y + h, x:x + w]


def build(a):
    import cv2
    import numpy as np
    from ultralytics import YOLO

    mined = json.loads(a.mined.read_text())
    kinds, fps = load_layout(a.layout)
    out = a.out.expanduser()
    img_dir, lbl_dir = out / "set/images/train", out / "set/labels/train"
    for d in (img_dir, lbl_dir):
        d.mkdir(parents=True, exist_ok=True)
    model = YOLO(a.weights)
    true_cap, g2r_cap = cv2.VideoCapture(str(a.video)), cv2.VideoCapture(str(a.g2r))
    vid = a.video_id
    frames_out = []
    for c in pick(mined["frames"], kinds, fps):
        rect = tuple(mined["rects"][c["panel"]])
        i = int(round(c["t"] * fps))
        img, swap = _frame(true_cap, i, rect), _frame(g2r_cap, i, rect)
        if img is None or swap is None:
            print("no frame", c["t"])
            continue
        # The two copies must be the same frame: away from the green, the same pixels.
        g1, g2 = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.cvtColor(swap, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        notgreen = ~((hsv[..., 0] >= 50) & (hsv[..., 0] <= 100) & (hsv[..., 1] >= 40))
        drift = float(np.abs(g1.astype(np.float32) - g2)[notgreen].mean())
        if drift > 4.0:
            print(f"t={c['t']}: copies differ by {drift:.1f}; skipped")
            continue
        t = i / fps
        stem = stem_for(vid, c["panel"], t)
        cv2.imwrite(str(img_dir / f"{stem}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 95])
        h, w = img.shape[:2]
        res = model.predict(swap, conf=PRELABEL_CONF, imgsz=IMGSZ, verbose=False)[0]
        rows = [f"{int(k)} {(x0 + x1) / 2 / w:.6f} {(y0 + y1) / 2 / h:.6f} {(x1 - x0) / w:.6f} {(y1 - y0) / h:.6f}"
                for k, (x0, y0, x1, y1) in zip(res.boxes.cls.tolist(), res.boxes.xyxy.tolist())]
        (lbl_dir / f"{stem}.txt").write_text("\n".join(rows) + ("\n" if rows else ""))
        frames_out.append({**c, "video": vid, "stem": stem, "t": round(t, 3), "frame": i,
                           "prelabels": len(rows), "copy_drift": round(drift, 2)})
        print(f"{stem} {c['kind']} {c['layout']}: {len(rows)} boxes (drift {drift:.2f})", flush=True)
    manifest = {"what": a.title, "scope": a.scope,
                "images": "normalised video, true colours (scripts/dallas/normalize.py)",
                "prelabel": {"weights": Path(a.weights).name, "conf": PRELABEL_CONF, "imgsz": IMGSZ,
                             "on": "the green-to-red copy of the same frame",
                             "classes": {0: "red_stone (DFW: green)", 1: "yellow_stone"}},
                "selection": {"shares": {f"{k}-{p}": n for (k, p), n in SHARES.items()}, "sure_conf": SURE,
                              "doubt_conf": DOUBT_LO, "guard_s": GUARD_S, "mined_every_s": mined["every_s"]},
                "by_kind": dict(Counter(f"{f['kind']}-{f['panel']}" for f in frames_out)),
                "by_layout": dict(Counter(f["layout"] for f in frames_out)), "frames": frames_out}
    a.manifest.parent.mkdir(parents=True, exist_ok=True)
    a.manifest.write_text(json.dumps(manifest, indent=1))
    print(f"{len(frames_out)} frames: {manifest['by_kind']} {manifest['by_layout']}")


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
    ap.add_argument("--layout", nargs="+", help="the normaliser's per-chunk logs, in order")
    ap.add_argument("--video", type=Path, help="normalised video, true colours")
    ap.add_argument("--g2r", type=Path, help="normalised video, green-to-red")
    ap.add_argument("--weights")
    ap.add_argument("--video-id", default="vvy26k01ZcI", help="names the frames and links them to YouTube")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--render-only", action="store_true")
    ap.add_argument("--scope", default="dfw1:train")
    ap.add_argument("--title", default="dfw1: DFW's green and yellow stones (vvy26k01ZcI)")
    a = ap.parse_args()
    if not a.render_only:
        build(a)
        return
    render(a.out.expanduser(), json.loads(a.manifest.read_text()))


if __name__ == "__main__":
    main()
