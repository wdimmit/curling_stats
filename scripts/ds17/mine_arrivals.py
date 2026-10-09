#!/usr/bin/env python
"""ds17 round 2 mining: arriving stones a new model loses that the old one sees.

ds17's person negatives cost it some real stones: on the 11-video harness it
lost 11 arrivals ds16a saw (5 of them then read as hogged), mostly stones with
a sweeper or skip over them in the far house. Every served timeline holds each
rock's arrival track in that house's panel, so both models can be run along it
frame by frame: wherever the old model boxes the rock's colour within NEAR_M of
the track and the new one does not, the new model lost a stone it should keep.

    python scripts/ds17/mine_arrivals.py --timelines DIR --videos DIR --out DIR \
        --old W --new W [--sources a,b]
"""

import argparse
import json
from pathlib import Path

import numpy as np

from curling_score.detect import yolo
from curling_score.geometry.calibrate import PanelCalib
from curling_score.ingest import frames as F

CONF, IMGSZ, FPS = 0.30, 448, 10.0
NEAR_M = 0.25
BEFORE_S, AFTER_S = 0.1, 0.3


def calib_of(cal):
    return PanelCalib(center_px=tuple(cal["center_px"]), px_per_m=cal["px_per_m"],
                      edge_erosion_px=0.0, residual_m=cal.get("residual_m", 0.0), flipped=cal["flipped"])


def at(track, t):
    ts = [p[0] for p in track]
    if t <= ts[0]:
        return track[0][1], track[0][2]
    if t >= ts[-1]:
        return track[-1][1], track[-1][2]
    i = int(np.searchsorted(ts, t))
    f = (t - ts[i - 1]) / (ts[i] - ts[i - 1])
    return (track[i - 1][1] + f * (track[i][1] - track[i - 1][1]),
            track[i - 1][2] + f * (track[i][2] - track[i - 1][2]))


def best_near(dets, color, x, y):
    c = [float(d.confidence) for d in dets if d.color == color and np.hypot(d.x_m - x, d.y_m - y) <= NEAR_M]
    return max(c) if c else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timelines", type=Path, required=True)
    ap.add_argument("--videos", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--old", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--sources", default=None)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    old = yolo.YoloDetector(a.old, conf=CONF, imgsz=IMGSZ, device=0)
    new = yolo.YoloDetector(a.new, conf=CONF, imgsz=IMGSZ, device=0)
    for d in (old, new):
        d.model.overrides["half"] = True
    paths = sorted(a.timelines.glob("tl_*.json"))
    if a.sources:
        keep = set(a.sources.split(","))
        paths = [p for p in paths if p.stem[3:] in keep]
    seen = set()
    for p in paths:
        doc = json.loads(p.read_text())
        vid, sid = doc["source"]["video_id"], p.stem[3:]
        video, out_path = a.videos / f"{vid}.mp4", a.out / f"{sid}.json"
        if out_path.exists() or not video.is_file():
            continue
        recs = []
        for g in doc["games"]:
            for e in g["ends"]:
                cal = doc["calibration"].get(e["house"])
                if not cal or not cal.get("rect"):
                    continue
                calib = calib_of(cal)
                for s in e["shots"]:
                    tr = s.get("track") or []
                    if s["missing"] or len(tr) < 2:
                        continue
                    key = (vid, round(tr[0][0], 1))
                    if key in seen:
                        continue
                    seen.add(key)
                    t0 = tr[0][0] - BEFORE_S
                    t1 = min(tr[-1][0], s.get("t_rest_s") or tr[-1][0]) + AFTER_S
                    imgs, times = [], []
                    for t, img in F.window(video, t0, t1, FPS, crop=tuple(cal["rect"])):
                        imgs.append(img)
                        times.append(t)
                    if not imgs:
                        continue
                    da, db = old.find_stones_batch(imgs, calib), new.find_stones_batch(imgs, calib)
                    fr = []
                    for t, xa, xb in zip(times, da, db):
                        x, y = at(tr, t)
                        fr.append([round(t, 2), round(x, 3), round(y, 3),
                                   round(best_near(xa, s["color"], x, y), 3), round(best_near(xb, s["color"], x, y), 3)])
                    lost = [f for f in fr if f[3] > 0 and f[4] == 0]
                    gained = [f for f in fr if f[4] > 0 and f[3] == 0]
                    run = best = 0
                    for f in fr:
                        run = run + 1 if (f[3] > 0 and f[4] == 0) else 0
                        best = max(best, run)
                    recs.append({"video": vid, "source": sid, "game": g["index"], "end": e["number"], "shot": s["number"],
                                 "color": s["color"], "panel": e["house"], "reason": s.get("reason"),
                                 "t_enter": s.get("t_enter_s"), "t_rest": s.get("t_rest_s"), "frames": fr,
                                 "old_hits": sum(1 for f in fr if f[3] > 0), "new_hits": sum(1 for f in fr if f[4] > 0),
                                 "lost": len(lost), "gained": len(gained), "lost_run": best})
        out_path.write_text(json.dumps({"video": vid, "source": sid, "throws": recs}))
        print(f"{sid} {vid}: {len(recs)} arrivals, frames lost {sum(r['lost'] for r in recs)}, "
              f"gained {sum(r['gained'] for r in recs)}, throws with a 2+ run lost {sum(1 for r in recs if r['lost_run'] >= 2)}",
              flush=True)


if __name__ == "__main__":
    main()
