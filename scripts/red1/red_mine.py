#!/usr/bin/env python
"""red1 mining: frames for overhead-1's two red weaknesses (2026-10-29).

overhead-1 lost to ds17b on the harness two ways, both red
(memory member-labels-training-1027):

  clothing  it boxes red clothing as a red stone -- entTaSBFK3A e2, a jacket over
            the empty button. Every keyframe of every game, both panels: an
            overhead-1 red box with no Nano box of either colour within FP_M.
  release   it misses red stones on the throwing panel around the release. Each
            red throw, the throwing panel at 10 fps from 0.3 s before the release
            to 2.0 s after: a Nano red box in the centre corridor with no
            overhead-1 box within MATCH_M (or a yellow one there).

ds17b runs on the same frames so each candidate says whether the shipped model
got it right (a regression) or not (a shared weakness).

    python red_mine.py --timelines DIR --videos DIR --out DIR --new overhead-1.pt \
        --old ds17b.pt --ref nano.pth [--sources a,b]
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, "/data/curling/stone/ds18/code/scripts/ds17")
import mine_hats as MH  # noqa: E402

from curling_score.detect import yolo  # noqa: E402
from curling_score.ingest import frames as F  # noqa: E402

CONF, IMGSZ = 0.30, 448
FP_M = 0.30             # no reference box this close: the red box is on nothing
MATCH_M = 0.15          # two boxes this close are the same stone
CORRIDOR_M = 0.8
REL_BEFORE_S, REL_AFTER_S, REL_FPS = 0.3, 2.0, 10.0


def near(d, others, r):
    return [o for o in others if np.hypot(d.x_m - o.x_m, d.y_m - o.y_m) <= r]


def box(d, rect):
    """A detection as whole-frame pixels, square on its area."""
    half = float(np.sqrt(d.area_px)) / 2
    x, y = rect[0] + d.x_px, rect[1] + d.y_px
    return [round(x - half, 1), round(y - half, 1), round(x + half, 1), round(y + half, 1)]


def short(d):
    return {"color": d.color, "x": round(d.x_m, 3), "y": round(d.y_m, 3), "conf": round(float(d.confidence), 3)}


def game_windows(doc):
    out = []
    for g in doc["games"]:
        ends = g.get("ends") or []
        if ends:
            out.append((min(e["start_s"] for e in ends), max(e["end_s"] for e in ends)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timelines", type=Path, required=True)
    ap.add_argument("--videos", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--old", required=True)
    ap.add_argument("--ref", required=True)
    ap.add_argument("--sources", default=None)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    from curling_score.detect import rfdetr as RD
    dets = {}
    for k, w in (("new", a.new), ("old", a.old)):
        dets[k] = yolo.YoloDetector(w, conf=CONF, imgsz=IMGSZ, device=0)
        dets[k].model.overrides["half"] = True
    ref = RD.RfdetrDetector(a.ref, conf=CONF, imgsz=IMGSZ, device=0, fp16=True)

    def run(imgs, calib):
        return (dets["new"].find_stones_batch(imgs, calib), dets["old"].find_stones_batch(imgs, calib),
                ref.find_stones_batch(imgs, calib))

    paths = sorted(a.timelines.glob("tl_*.json"))
    if a.sources:
        keep = set(a.sources.split(","))
        paths = [p for p in paths if p.stem[3:] in keep]
    # Several sources share a video: sweep each video's keyframes once.
    swept = set()
    for p in paths:
        doc = json.loads(p.read_text())
        vid, sid = doc["source"]["video_id"], p.stem[3:]
        video, out_path = a.videos / f"{vid}.mp4", a.out / f"{sid}.json"
        if out_path.exists() or not video.is_file():
            continue
        t_start = time.time()
        cands, n = [], {"keyframes": 0, "throws": 0}
        panels = {k: c for k, c in doc["calibration"].items()
                  if k in ("top", "bottom") and c and c.get("rect") and c.get("center_px")}
        calibs = {k: MH.calib_of(c) for k, c in panels.items()}

        if vid not in swept:
            swept.add(vid)
            def flush(batch):
                for k, (times, imgs) in batch.items():
                    if not imgs:
                        continue
                    rect = [int(v) for v in panels[k]["rect"]]
                    for t, fn, fo, fr in zip(times, *run(imgs, calibs[k])):
                        for d in fn:
                            if d.color != "red" or near(d, fr, FP_M):
                                continue
                            cands.append({"kind": "clothing", "source": sid, "video": vid, "t": round(t, 3),
                                          "panel": k, "rect": rect, "box": box(d, rect), "det": short(d),
                                          "old_has": bool([o for o in near(d, fo, MATCH_M) if o.color == "red"]),
                                          "n_new": len(fn), "n_ref": len(fr)})

            for t0, t1 in game_windows(doc):
                batch = {k: ([], []) for k in panels}
                for t, img in F.keyframe_sweep(video, start_s=t0, end_s=t1):
                    n["keyframes"] += 1
                    for k, c in panels.items():
                        x, y, w, h = (int(v) for v in c["rect"])
                        batch[k][0].append(t)
                        batch[k][1].append(img[y:y + h, x:x + w])
                    if len(batch[next(iter(panels))][0]) >= 128:
                        flush(batch)
                        batch = {k: ([], []) for k in panels}
                flush(batch)

        for gi, e, s, t_rel, how in MH.throws(doc):
            if s.get("color") != "red":
                continue
            panel = MH.OTHER[e["house"]]
            if panel not in panels:
                continue
            rect = [int(v) for v in panels[panel]["rect"]]
            imgs, times = [], []
            for t, img in F.window(video, t_rel - REL_BEFORE_S, t_rel + REL_AFTER_S, REL_FPS, crop=tuple(rect)):
                imgs.append(img)
                times.append(t)
            if not imgs:
                continue
            n["throws"] += 1
            for t, fn, fo, fr in zip(times, *run(imgs, calibs[panel])):
                for d in fr:
                    if d.color != "red" or abs(d.x_m) > CORRIDOR_M:
                        continue
                    mine = near(d, fn, MATCH_M)
                    if any(o.color == "red" for o in mine):
                        continue
                    cands.append({"kind": "release", "source": sid, "video": vid, "t": round(t, 3),
                                  "t_rel": round(t_rel, 3), "how": how, "game": gi, "end": e.get("number"),
                                  "shot": s.get("number"), "panel": panel, "rect": rect, "box": box(d, rect),
                                  "det": short(d), "new_yellow": bool(mine),
                                  "old_has": bool([o for o in near(d, fo, MATCH_M) if o.color == "red"])})
        out_path.write_text(json.dumps({"source": sid, "video": vid, "n": n, "cands": cands,
                                        "secs": round(time.time() - t_start, 1)}))
        print(sid, vid, n, len(cands), f"{time.time() - t_start:.0f}s", flush=True)


if __name__ == "__main__":
    main()
