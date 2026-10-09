#!/usr/bin/env python
"""Several overhead models over whole games, on the same decoded frames.

For every timed throw of the served timelines (as `mine_hats.py`): the throwing
panel at 10 fps from 0.3 s before the release to 3.7 s after, and per model
whether its stone track is found, its suspect boxes on the thrower (all and
conf >= 0.5) and the length of any shadow track. For every served arrival track
(as `mine_arrivals.py`): the house panel at 5 fps along the track, and per
model whether it boxes the rock within NEAR_M of where the track says it was.
Each window is decoded once and every model sees the same frames.

    python scripts/ds17/fullgames.py --timelines DIR --videos DIR --out DIR \
        --models ds16a=W ds17b-11n=W ... [--sources a,b]
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mine_arrivals as MA  # noqa: E402
import mine_hats as MH  # noqa: E402

from curling_score.detect import yolo  # noqa: E402
from curling_score.ingest import frames as F  # noqa: E402

ARRIVAL_FPS = 5.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timelines", type=Path, required=True)
    ap.add_argument("--videos", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--models", nargs="+", required=True, help="name=weights")
    ap.add_argument("--sources", default=None)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    models = {}
    for spec in a.models:
        name, w = spec.split("=", 1)
        d = yolo.YoloDetector(w, conf=MH.CONF, imgsz=MH.IMGSZ, device=0)
        d.model.overrides["half"] = True
        models[name] = d
    paths = sorted(a.timelines.glob("tl_*.json"))
    if a.sources:
        keep = set(a.sources.split(","))
        paths = [p for p in paths if p.stem[3:] in keep]
    seen_t, seen_a = set(), set()
    for p in paths:
        doc = json.loads(p.read_text())
        vid, sid = doc["source"]["video_id"], p.stem[3:]
        video, out_path = a.videos / f"{vid}.mp4", a.out / f"{sid}.json"
        if out_path.exists() or not video.is_file():
            continue
        throws, arrivals = [], []
        for gi, e, s, t_rel, how in MH.throws(doc):
            key = (vid, round(t_rel, 1))
            if key in seen_t:
                continue
            seen_t.add(key)
            panel = MH.OTHER[e["house"]]
            cal = doc["calibration"].get(panel)
            if not cal or not cal.get("rect"):
                continue
            calib = MH.calib_of(cal)
            imgs, times = [], []
            for t, img in F.window(video, t_rel - MH.BEFORE_S, t_rel + MH.AFTER_S, MH.FPS, crop=tuple(cal["rect"])):
                imgs.append(img)
                times.append(t)
            if not imgs:
                continue
            per = {}
            for name, det in models.items():
                frames = list(zip(times, det.find_stones_batch(imgs, calib)))
                stone = MH.stone_track(frames, s["color"], t_rel)
                sus = MH.suspects(frames, s["color"], stone)
                per[name] = [len(stone.ts) if stone else 0, len(sus), sum(1 for x in sus if x["conf"] >= 0.5),
                             MH.shadow(sus)]
            throws.append({"end": e["number"], "shot": s["number"], "color": s["color"], "t": round(t_rel, 2),
                           "how": how, "m": per})
        for g in doc["games"]:
            for e in g["ends"]:
                cal = doc["calibration"].get(e["house"])
                if not cal or not cal.get("rect"):
                    continue
                calib = MH.calib_of(cal)
                for s in e["shots"]:
                    tr = s.get("track") or []
                    if s["missing"] or len(tr) < 2:
                        continue
                    key = (vid, round(tr[0][0], 1))
                    if key in seen_a:
                        continue
                    seen_a.add(key)
                    t1 = min(tr[-1][0], s.get("t_rest_s") or tr[-1][0]) + MA.AFTER_S
                    imgs, times = [], []
                    for t, img in F.window(video, tr[0][0] - MA.BEFORE_S, t1, ARRIVAL_FPS, crop=tuple(cal["rect"])):
                        imgs.append(img)
                        times.append(t)
                    if not imgs:
                        continue
                    pos = [MA.at(tr, t) for t in times]
                    per = {}
                    for name, det in models.items():
                        dets = det.find_stones_batch(imgs, calib)
                        per[name] = "".join("1" if MA.best_near(d, s["color"], x, y) > 0 else "0"
                                            for d, (x, y) in zip(dets, pos))
                    arrivals.append({"end": e["number"], "shot": s["number"], "color": s["color"],
                                     "reason": s.get("reason"), "t": round(tr[0][0], 2), "m": per})
        out_path.write_text(json.dumps({"video": vid, "source": sid, "throws": throws, "arrivals": arrivals}))
        print(f"{sid} {vid}: {len(throws)} throws, {len(arrivals)} arrivals", flush=True)


if __name__ == "__main__":
    main()
