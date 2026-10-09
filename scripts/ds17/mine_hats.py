#!/usr/bin/env python
"""ds17 mining: boxes on the thrower during a delivery -- hats, heads, hands.

ds16a reads some throwers' heads as stones. On _OGoqF5W9-I a thrower in a red
toque slid down the centre line over their yellow stone, the toque read red at
0.63-0.88 for 1.2 s, and that "red release" outvoted the real yellow one. ds16
taught the model one yellow toque on one sheet; this looks for the pattern
anywhere it can.

For every throw a served timeline times, the throwing panel is read at 10 fps
from 0.3 s before the release to 3.7 s after, exactly as the pipeline reads it
(the overhead detector, conf 0.30, imgsz 448). The stone is the track of the
rock's own colour that leaves the hack at the release. A *suspect* is any
other box in the centre corridor during the slide:

  * the other colour, anywhere along the stone's line -- a hat over the stone;
  * the rock's own colour, more than 0.25 m behind the stone -- a hat or a hand
    following it.

A throw whose suspects form a track (three or more, moving up the sheet) is a
*shadow*: the kind that can outvote the stone. Writes one JSON record per
throw with every suspect, and a contact sheet of the worst throws for a look
before anything is labelled.

    python scripts/ds17/mine_hats.py --timelines DIR --videos DIR --out DIR [--sources a,b]
"""

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from curling_score import weights as weights_mod
from curling_score.detect import delivery as D, yolo
from curling_score.geometry.calibrate import PanelCalib
from curling_score.ingest import frames as F

CONF = 0.30              # what the pipeline keeps
IMGSZ = 448
FPS = 10.0
BEFORE_S, AFTER_S = 0.3, 3.7
CORRIDOR_M = 0.8         # |x| of the centre corridor when the stone is not seen
NEAR_LINE_M = 0.6        # a suspect's lateral distance from the stone's line
BEHIND_M = 0.25          # a same-colour box further back than this is not the stone
OTHER = {"top": "bottom", "bottom": "top"}


def calib_of(cal):
    return PanelCalib(center_px=tuple(cal["center_px"]), px_per_m=cal["px_per_m"],
                      edge_erosion_px=0.0, residual_m=cal.get("residual_m", 0.0),
                      flipped=cal["flipped"])


# A rock whose release was lost -- the very thing a hat on the line does -- is
# still timed at the tee; the stone crosses it about this long after leaving.
RELEASE_TO_TEE_S = 0.8


def throws(doc):
    """(game index, end, shot, release time, how the time was had)."""
    for g in doc["games"]:
        for e in g["ends"]:
            for s in e["shots"]:
                if s["missing"]:
                    continue
                if s.get("t_release_s") is not None:
                    yield g["index"], e, s, s["t_release_s"], s.get("release_source") or "overhead"
                elif s.get("t_tee_s") is not None:
                    yield g["index"], e, s, s["t_tee_s"] - RELEASE_TO_TEE_S, "from-tee"


def stone_track(frames, color, t_rel):
    """The rock's own track leaving the hack at the release, or None."""
    own = [(t, [d for d in dets if d.color == color and abs(d.x_m) < CORRIDOR_M]) for t, dets in frames]
    best = None
    for tr in D._build_tracks(own):
        if abs(tr.ts[0] - t_rel) > 0.6 or tr.ys[0] > 0.0 or len(tr.ts) < 3:
            continue
        if best is None or len(tr.ts) > len(best.ts):
            best = tr
    return best


def at(track, t):
    """The track's (x, y) at ``t``, held at its ends and interpolated between."""
    ts = track.ts
    if t <= ts[0]:
        return track.xs[0], track.ys[0]
    if t >= ts[-1]:
        return track.xs[-1], track.ys[-1]
    i = int(np.searchsorted(ts, t))
    f = (t - ts[i - 1]) / (ts[i] - ts[i - 1])
    return (track.xs[i - 1] + f * (track.xs[i] - track.xs[i - 1]),
            track.ys[i - 1] + f * (track.ys[i] - track.ys[i - 1]))


def suspects(frames, color, stone):
    on = {(round(t, 3), round(y, 4)) for t, y in zip(stone.ts, stone.ys)} if stone else set()
    out = []
    for t, dets in frames:
        sx, sy = at(stone, t) if stone else (0.0, None)
        for d in dets:
            if (round(t, 3), round(d.y_m, 4)) in on and d.color == color:
                continue
            if stone is None and (abs(d.x_m) > CORRIDOR_M or d.color == color):
                continue                    # unseen stone: its own colour may be it
            if stone is not None and abs(d.x_m - sx) > NEAR_LINE_M:
                continue
            if d.color == color and sy is not None and d.y_m > sy - BEHIND_M:
                continue                    # the stone itself, or ahead of it
            out.append({"t": round(t, 2), "color": d.color, "x": round(d.x_m, 3), "y": round(d.y_m, 3),
                        "conf": round(float(d.confidence), 3), "x_px": round(d.x_px, 1),
                        "y_px": round(d.y_px, 1), "area": round(d.area_px)})
    return out


def shadow(sus):
    """The longest track the suspects make that moves up the sheet."""
    if not sus:
        return 0
    by_t = {}
    for s in sus:
        by_t.setdefault(s["t"], []).append(s)
    frames = [(t, [_Det(s) for s in v]) for t, v in sorted(by_t.items())]
    best = 0
    for tr in D._build_tracks(frames):
        if len(tr.ts) >= 3 and tr.ys[-1] - tr.ys[0] > 0.5:
            best = max(best, len(tr.ts))
    return best


class _Det:
    def __init__(self, s):
        self.color, self.x_m, self.y_m = s["color"], s["x"], s["y"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timelines", type=Path, required=True)
    ap.add_argument("--videos", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--sources", default=None, help="comma-separated source ids (default: all)")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    det = yolo.YoloDetector(weights_mod.default_path(), conf=CONF, imgsz=IMGSZ, device=0)
    det.model.overrides["half"] = True
    paths = sorted(args.timelines.glob("tl_*.json"))
    if args.sources:
        keep = set(args.sources.split(","))
        paths = [p for p in paths if p.stem[3:] in keep]
    seen = set()
    for p in paths:
        doc = json.loads(p.read_text())
        vid, sid = doc["source"]["video_id"], p.stem[3:]
        video = args.videos / f"{vid}.mp4"
        out_path = args.out / f"{sid}.json"
        if out_path.exists() or not video.is_file():
            continue
        recs = []
        for gi, e, s, t_rel, how in throws(doc):
            key = (vid, round(t_rel, 1))
            if key in seen:
                continue
            seen.add(key)
            panel = OTHER[e["house"]]
            cal = doc["calibration"].get(panel)
            if not cal or not cal.get("rect"):
                continue
            calib = calib_of(cal)
            t0, t1 = t_rel - BEFORE_S, t_rel + AFTER_S
            imgs, times = [], []
            for t, img in F.window(video, t0, t1, FPS, crop=tuple(cal["rect"])):
                imgs.append(img)
                times.append(t)
            if not imgs:
                continue
            frames = list(zip(times, det.find_stones_batch(imgs, calib)))
            stone = stone_track(frames, s["color"], t_rel)
            sus = suspects(frames, s["color"], stone)
            recs.append({"video": vid, "source": sid, "game": gi, "end": e["number"], "shot": s["number"],
                         "color": s["color"], "position": s.get("position"), "panel": panel,
                         "t_release": round(t_rel, 2), "release_source": how,
                         "stone_n": len(stone.ts) if stone else 0, "suspects": sus,
                         "shadow_n": shadow(sus), "max_conf": max((x["conf"] for x in sus), default=0.0)})
        out_path.write_text(json.dumps({"video": vid, "source": sid, "throws": recs}))
        n_sus = sum(1 for r in recs if r["suspects"])
        n_sh = sum(1 for r in recs if r["shadow_n"])
        print(f"{sid} {vid}: {len(recs)} throws, {n_sus} with suspects, {n_sh} shadows", flush=True)


if __name__ == "__main__":
    main()
