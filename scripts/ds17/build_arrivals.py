#!/usr/bin/env python
"""ds17 round 2: arriving stones the round-1 model lost, for the review to keep.

Round 1 (ds17) taught the overhead model that the sliding thrower's head is not
a stone. On the 11-video harness it then lost 11 arrivals ds16a saw -- 5 of them
read as hogged -- mostly stones with a sweeper or skip over them in the far
house. `mine_arrivals.py` ran both models along every served arrival track;
this cuts frames where ds16a boxed the arriving stone and ds17 did not, the
longest runs of misses first, and always the harness arrivals named in --must.
Pre-labelled by ds16a, which sees the stone. A fresh review scope, so round 1's
stored edits stay apart.

    python scripts/ds17/build_arrivals.py --mined DIR --timelines DIR --videos DIR --out DIR \
        --manifest datasets/ds17/manifest-arrivals.json [--must must.json]
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

PRELABEL_CONF, IMGSZ = 0.05, 448
TARGET, PER_THROW, PER_VIDEO = 120, 2, 8
APART_S = 0.5
NOTE = ("A stone is arriving in this house. Box EVERY stone, including any partly hidden "
        "under a sweeper, the skip, a broom or a foot -- a stone is still a stone where a "
        "person covers some of it. Reject boxes on people, brooms, shoes and logos.")


def stem_for(vid, panel, t):
    return f"{vid}_{panel[0]}_{t:09.2f}".replace(".", "_")


def frame_at(video, t, rect):
    for tt, img in F.window(video, t, t + 0.2, fps=30.0, crop=rect):
        return tt, img
    return None, None


def lost_times(r):
    """Up to PER_THROW lost frames: the middle of the longest run, then another."""
    fr = r["frames"]
    runs, cur = [], []
    for f in fr:
        if f[3] > 0 and f[4] == 0:
            cur.append(f)
        else:
            if cur:
                runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    runs.sort(key=len, reverse=True)
    times = []
    for run in runs:
        t = run[len(run) // 2][0]
        if all(abs(t - u) >= APART_S for u in times):
            times.append(t)
        if len(times) >= PER_THROW:
            break
    return times


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mined", type=Path, required=True)
    ap.add_argument("--timelines", type=Path, required=True)
    ap.add_argument("--videos", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--must", type=Path, default=None, help="JSON [[video, t_enter], ...] to include")
    ap.add_argument("--extra", type=Path, default=None,
                    help="JSON [[video, source, panel, t, why], ...]: frames to cut as they are, for arrivals "
                         "no served track covers")
    a = ap.parse_args()
    out = a.out.expanduser()
    img_dir, lbl_dir = out / "set/images/train", out / "set/labels/train"
    for d in (img_dir, lbl_dir):
        d.mkdir(parents=True, exist_ok=True)
    det = yolo.YoloDetector(weights_mod.default_path(), conf=PRELABEL_CONF, imgsz=IMGSZ)
    throws = [r for p in sorted(a.mined.glob("*.json")) for r in json.loads(p.read_text())["throws"]]
    must = json.loads(a.must.read_text()) if a.must else []
    forced = []
    for vid, te in must:
        c = [r for r in throws if r["video"] == vid and r["t_enter"] is not None and abs(r["t_enter"] - te) <= 3.0]
        if c:
            forced.append(min(c, key=lambda r: abs(r["t_enter"] - te)))
        else:
            print("must not found:", vid, te)
    ranked = sorted((r for r in throws if r["lost"] and r not in forced),
                    key=lambda r: (-r["lost_run"], -r["lost"]))
    per_video, picks = defaultdict(int), []
    for r in forced + ranked:
        times = lost_times(r) or [r["frames"][len(r["frames"]) // 2][0]]
        if r not in forced and per_video[r["video"]] >= PER_VIDEO:
            continue
        for t in times:
            picks.append((r, t, r in forced))
            per_video[r["video"]] += 1
        if len(picks) >= TARGET:
            break
    for vid, sid, panel, t, why in (json.loads(a.extra.read_text()) if a.extra else []):
        picks.insert(0, ({"video": vid, "source": sid, "panel": panel, "end": None, "shot": None, "color": None,
                          "reason": why, "lost": 0, "lost_run": 0}, t, True))
    cals, frames_out, meta = {}, [], {}
    for r, t, was_forced in picks:
        if r["source"] not in cals:
            cals[r["source"]] = json.loads((a.timelines / f"tl_{r['source']}.json").read_text())["calibration"]
        cal = cals[r["source"]][r["panel"]]
        tt, img = frame_at(a.videos / f"{r['video']}.mp4", t, tuple(cal["rect"]))
        if img is None:
            continue
        stem = stem_for(r["video"], r["panel"], tt)
        if any(f["stem"] == stem for f in frames_out):
            continue
        cv2.imwrite(str(img_dir / f"{stem}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 95])
        res = det.model.predict(img, conf=PRELABEL_CONF, imgsz=IMGSZ, verbose=False)[0]
        rows = [f"{int(c)} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}"
                for c, (cx, cy, w, h) in zip(res.boxes.cls.tolist(), res.boxes.xywhn.tolist())]
        (lbl_dir / f"{stem}.txt").write_text("\n".join(rows) + ("\n" if rows else ""))
        why = (f"harness arrival the round-1 model lost ({r['reason']})" if r["end"] is None else
               f"e{r['end']}s{r['shot']} {r['color']} arriving ({r['reason']}); ds16a saw it here, "
               f"the round-1 model did not ({r['lost']} frames lost, run of {r['lost_run']})")
        meta[stem] = labels.FrameMeta(note=f"{why}. {NOTE}", url=f"https://youtu.be/{r['video']}?t={int(tt)}")
        frames_out.append({"stem": stem, "video": r["video"], "source": r["source"], "panel": r["panel"],
                           "t": round(tt, 3), "end": r["end"], "shot": r["shot"], "color": r["color"],
                           "reason": r["reason"], "lost": r["lost"], "lost_run": r["lost_run"],
                           "forced": was_forced, "prelabels": len(rows)})
        print(f"{stem} {'MUST ' if was_forced else ''}{len(rows)} prelabels, run {r['lost_run']}", flush=True)
    by_video = defaultdict(int)
    for f in frames_out:
        by_video[f["video"]] += 1
    manifest = {"what": "ds17 round 2: arriving stones (often under a sweeper or skip) the round-1 model lost",
                "prelabel": {"weights": weights_mod.default_path().name, "conf": PRELABEL_CONF, "imgsz": IMGSZ},
                "selection": {"target": TARGET, "per_throw": PER_THROW, "per_video": PER_VIDEO, "must": must},
                "by_video": dict(sorted(by_video.items())), "frames": frames_out}
    a.manifest.parent.mkdir(parents=True, exist_ok=True)
    a.manifest.write_text(json.dumps(manifest, indent=1))
    frames = [f for seq in labels.load_split(out / "set", "train").values() for f in seq]
    page, n = labels.render_clickable(frames, out / "review", title="ds17 round 2: keep the arriving stone",
                                      scope="ds17b:train", per_page=200, meta=meta)
    print(f"{len(frames_out)} frames from {len(by_video)} videos: {page}")


if __name__ == "__main__":
    main()
