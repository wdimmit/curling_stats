#!/usr/bin/env python
"""ds17: the sliding thrower's head, hat or hood, seen from above.

ds16a reads a round head right behind (or over) the stone as a stone, at up to
0.98. Mined over 6,593 throws of 50 cached videos (`mine_hats.py`): 1,089 have a
box on the thrower during the slide, and 304 a run of them moving with the stone
that can outvote the real release -- _OGoqF5W9-I's yellow third in a red toque
lost three of their releases that way at 10 fps. It is not one hat: 34
throwers have three or more such throws, in both seasons.

Frames are cut from the throwing panel at the moments the mined suspects were
seen, spread over throwers and videos, and pre-labelled by the pipeline's
detector at a low confidence; the review decides. Writes the set (images/labels,
split ``train``), the review pages, and the manifest -- the committed half of
the dataset.

    python scripts/ds17/build_hats.py --mined DIR --timelines DIR --videos DIR --out DIR \
        --manifest datasets/ds17/manifest.json
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path

import cv2

from curling_score import weights as weights_mod
from curling_score.detect import yolo
from curling_score.ingest import frames as F
from curling_score.train import labels

PRELABEL_CONF = 0.05          # as ds15/ds16: the reviewer sees what the model nearly saw
IMGSZ = 448                   # what the pipeline detects at
TARGET = 180                  # frames in all
PER_THROW, PER_THROWER, PER_VIDEO = 2, 6, 10
APART_S = 0.5                 # a throw's second frame, this far from its first

HAT = ("The thrower slides behind the stone: their head, hat, toque, hood or hair seen "
       "from above is a PERSON, not a stone -- reject any box on it. Box every real "
       "stone (granite with a handle), the one being delivered included, even where the "
       "thrower half covers it.")


def stem_for(vid, panel, t):
    return f"{vid}_{panel[0]}_{t:09.2f}".replace(".", "_")


def frame_at(video, t, rect):
    for tt, img in F.window(video, t, t + 0.2, fps=30.0, crop=rect):
        return tt, img
    return None, None


def pick(throws):
    """(throw, time) to cut: shadows first, then the most confident suspects,
    spread so no thrower, video or throw dominates."""
    order = sorted((r for r in throws if r["suspects"]),
                   key=lambda r: (-(r["shadow_n"] > 0), -r["max_conf"]))
    per_thrower, per_video, out = defaultdict(int), defaultdict(int), []
    for r in order:
        who = (r["video"], r["color"], r["position"])
        if per_thrower[who] >= PER_THROWER or per_video[r["video"]] >= PER_VIDEO:
            continue
        sus = sorted(r["suspects"], key=lambda s: -s["conf"])
        times = [sus[0]["t"]]
        for s in sus[1:]:
            if len(times) >= PER_THROW:
                break
            if all(abs(s["t"] - t) >= APART_S for t in times):
                times.append(s["t"])
        for t in times[: min(PER_THROW, PER_THROWER - per_thrower[who], PER_VIDEO - per_video[r["video"]])]:
            out.append((r, t))
            per_thrower[who] += 1
            per_video[r["video"]] += 1
        if len(out) >= TARGET:
            break
    return out[:TARGET]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mined", type=Path, required=True)
    ap.add_argument("--timelines", type=Path, required=True)
    ap.add_argument("--videos", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--manifest", type=Path, required=True)
    args = ap.parse_args()
    out = args.out.expanduser()
    img_dir, lbl_dir = out / "set/images/train", out / "set/labels/train"
    for d in (img_dir, lbl_dir):
        d.mkdir(parents=True, exist_ok=True)
    det = yolo.YoloDetector(weights_mod.default_path(), conf=PRELABEL_CONF, imgsz=IMGSZ)
    throws = [r for p in sorted(args.mined.glob("*.json")) for r in json.loads(p.read_text())["throws"]]
    cal_cache = {}
    frames_out, meta = [], {}
    for r, t in pick(throws):
        if r["source"] not in cal_cache:
            cal_cache[r["source"]] = json.loads((args.timelines / f"tl_{r['source']}.json").read_text())["calibration"]
        cal = cal_cache[r["source"]][r["panel"]]
        tt, img = frame_at(args.videos / f"{r['video']}.mp4", t, tuple(cal["rect"]))
        if img is None:
            print("no frame", r["video"], t)
            continue
        stem = stem_for(r["video"], r["panel"], tt)
        if any(f["stem"] == stem for f in frames_out):
            continue
        cv2.imwrite(str(img_dir / f"{stem}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 95])
        res = det.model.predict(img, conf=PRELABEL_CONF, imgsz=IMGSZ, verbose=False)[0]
        rows = [f"{int(c)} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}"
                for c, (cx, cy, w, h) in zip(res.boxes.cls.tolist(), res.boxes.xywhn.tolist())]
        (lbl_dir / f"{stem}.txt").write_text("\n".join(rows) + ("\n" if rows else ""))
        sus = [s for s in r["suspects"] if abs(s["t"] - t) < 0.01]
        why = (f"e{r['end']}s{r['shot']} {r['color']} {r['position'] or ''} sliding; the miner saw "
               + ", ".join(f"a {s['color']} box at {s['conf']:.2f}" for s in sus) + " on the thrower")
        meta[stem] = labels.FrameMeta(note=f"{why}. {HAT}", url=f"https://youtu.be/{r['video']}?t={int(tt)}")
        frames_out.append({"stem": stem, "video": r["video"], "source": r["source"], "panel": r["panel"],
                           "t": round(tt, 3), "end": r["end"], "shot": r["shot"], "color": r["color"],
                           "position": r["position"], "shadow_n": r["shadow_n"],
                           "suspects": sus, "prelabels": len(rows)})
        print(f"{stem} {len(rows)} prelabels, suspects {[(s['color'], s['conf']) for s in sus]}", flush=True)

    by_video = defaultdict(int)
    for f in frames_out:
        by_video[f["video"]] += 1
    manifest = {"what": "ds17: the sliding thrower's head, hat or hood read as a stone",
                "prelabel": {"weights": weights_mod.default_path().name, "conf": PRELABEL_CONF,
                             "imgsz": IMGSZ, "classes": det.model.names},
                "selection": {"target": TARGET, "per_throw": PER_THROW, "per_thrower": PER_THROWER,
                              "per_video": PER_VIDEO},
                "by_video": dict(sorted(by_video.items())), "frames": frames_out}
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, indent=1))
    frames = [f for seq in labels.load_split(out / "set", "train").values() for f in seq]
    page, n = labels.render_clickable(frames, out / "review", title="ds17: the thrower's head is not a stone",
                                      scope="ds17:train", per_page=200, meta=meta)
    print(f"{len(frames_out)} frames from {len(by_video)} videos, {n} page(s): {page}")


if __name__ == "__main__":
    main()
