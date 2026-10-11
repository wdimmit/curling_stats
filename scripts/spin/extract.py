"""Follow each rock down the long camera that faces its thrower, at 30 fps, and
keep a crop of it per frame -- the raw material for reading its rotation.

    python extract.py TIMELINE VIDEO OUTDIR [ENDS] [WEIGHTS]

ENDS is a comma list (default: all); WEIGHTS defaults to the worker image's
ds13c. One gzip pickle per rock in OUTDIR: times, boxes (view px), ice position
(x m across, y m from the far tee toward the camera), and the crop around the
box with its origin. ``wave.py`` samples labelling frames from these.

The camera is hogtime's: the one at the far end, looking back at the thrower.
The rock is seeded between the hack and just past the tee, on the sheet's
centre third, then followed frame to frame with a constant-velocity gate; it is
dropped after 0.8 s unseen, which in practice is the bottom of the frame
(~26 m past the throwing tee).

Runs on a GPU box inside curling-worker:local, ~10 s a rock on a 3070:

    docker run --rm --gpus all -v $PWD:/w -v /data/wdd/curling-cache/videos:/v:ro \
        --entrypoint python curling-worker:local /w/extract.py \
        /w/timeline.json /v/<video>.mp4 /w/out/<video> 2,3,4
"""
import gzip
import json
import pickle
import sys
import time
from pathlib import Path

import numpy as np
from ultralytics import YOLO

from curling_score.detect import longview
from curling_score.geometry import constants as C
from curling_score.geometry import sideview

CAMERA_FOR = {"top": "left", "bottom": "right"}      # hogtime.CAMERA_FOR
OTHER_HOUSE = {"top": "bottom", "bottom": "top"}
CLS = {"red": 0, "yellow": 1}
CHUNK_S = 2.0
FPS = 30.0
CONF = 0.25
MAX_GAP_S = 0.8

tl_path, video, outdir = sys.argv[1], sys.argv[2], Path(sys.argv[3])
ends = {int(e) for e in sys.argv[4].split(",")} if len(sys.argv) > 4 else None
outdir.mkdir(parents=True, exist_ok=True)
tl = json.load(open(tl_path))
model = YOLO(sys.argv[5] if len(sys.argv) > 5 else "/opt/curling/weights/ds13c.pt")


def view(name):
    d = tl["calibration"][name]
    return sideview.SideView(rect=tuple(d["rect"]), tee_row=d["tee_row"], hog_row=d["hog_row"],
                             centre_col=d.get("centre_col"),
                             lat_px_per_m_at_tee=d.get("lat_px_per_m_at_tee"),
                             centre_line=tuple(d["centre_line"]) if d.get("centre_line") else None)


def detect(frames, cls):
    out = []
    for i in range(0, len(frames), 16):
        batch = [np.ascontiguousarray(f[:, :, ::-1]) for f in frames[i:i + 16]]
        for r in model.predict(batch, imgsz=800, conf=CONF, verbose=False):
            got = []
            for b, c, cf in zip(r.boxes.xyxy.cpu().numpy(), r.boxes.cls.cpu().numpy(),
                                r.boxes.conf.cpu().numpy()):
                if int(c) == cls:
                    got.append((*[float(v) for v in b], float(cf)))
            out.append(got)
    return out


def plausible(v, b):
    """A box whose width fits a stone standing at its bottom row."""
    x0, y0, x1, y1, _ = b
    row = y1
    if row <= v.tee_row - 120:
        return False
    want = 0.2896 * v.lateral_px_per_m(row)
    return 0.6 * want <= (x1 - x0) <= 1.6 * want


for end in tl["games"][0]["ends"]:
    if ends and end["number"] not in ends:
        continue
    vname = CAMERA_FOR[OTHER_HOUSE[end["house"]]]
    v = view(vname)
    for shot in end["shots"]:
        if shot.get("missing"):
            continue
        t_rel = shot.get("t_release_s") or (shot["t_tee_s"] - 0.75 if shot.get("t_tee_s") else None)
        t_enter = shot.get("t_enter_s") or shot.get("t_rest_s")
        if t_rel is None or t_enter is None:
            continue
        t0, t1 = t_rel - 0.5, min(t_enter + 1.0, t_rel + 25.0)
        cls = CLS[shot["color"]]
        tic = time.time()
        recs = []          # (t, box, patch, origin)
        track_last = None  # (t, cx, by, w, vx, vy)
        t = t0
        while t < t1:
            frames, times = longview.decode(video, v.rect, t, min(t + CHUNK_S, t1), FPS)
            frames = frames[: int(round(CHUNK_S * FPS))]
            times = times[: len(frames)]
            boxes = detect(frames, cls)
            for f, tt, bs in zip(frames, times, boxes):
                bs = [b for b in bs if plausible(v, b)]
                pick = None
                if track_last is None:
                    # Seed: on the sheet's centre third, between the hack and just past the tee.
                    lo, hi = v.row_for(-4.5), v.row_for(1.5)
                    cand = [b for b in bs if lo <= b[3] <= hi
                            and abs(v.lateral_x((b[0] + b[2]) / 2, b[3])) < 0.8]
                    if cand:
                        pick = max(cand, key=lambda b: b[4])
                else:
                    lt, cx, by, w, vx, vy = track_last
                    dt = tt - lt
                    if dt > MAX_GAP_S:
                        break
                    px, py = cx + vx * dt, by + vy * dt
                    gate = 0.6 * w + 4 + 0.5 * abs(vy) * dt
                    cand = [(np.hypot((b[0] + b[2]) / 2 - px, b[3] - py), b) for b in bs]
                    cand = [c for c in cand if c[0] < gate]
                    if cand:
                        pick = min(cand, key=lambda c: c[0])[1]
                if pick is None:
                    continue
                x0, y0, x1, y1, cf = pick
                cx, w, h = (x0 + x1) / 2, x1 - x0, y1 - y0
                if track_last is not None:
                    lt, pcx, pby, _pw, pvx, pvy = track_last
                    dt = tt - lt
                    a = min(1.0, dt / 0.3)
                    vx = (1 - a) * pvx + a * (cx - pcx) / dt
                    vy = (1 - a) * pvy + a * (y1 - pby) / dt
                else:
                    vx = vy = 0.0
                track_last = (tt, cx, y1, w, vx, vy)
                px0 = max(0, int(x0 - 0.35 * w)); px1 = min(f.shape[1], int(x1 + 0.35 * w) + 1)
                py0 = max(0, int(y0 - 0.5 * h)); py1 = min(f.shape[0], int(y1 + 0.25 * h) + 1)
                xm, ym = v.to_house(cx, y1 - 0.12 * w)   # foot ~ a little above the box bottom
                recs.append(dict(t=tt, box=(x0, y0, x1, y1), conf=cf, x_m=xm, y_m=ym,
                                 origin=(px0, py0), patch=f[py0:py1, px0:px1].copy()))
            else:
                t += CHUNK_S
                continue
            break   # lost the rock for longer than MAX_GAP_S
        name = f"e{end['number']}s{shot['number']:02d}"
        meta = dict(video=Path(video).stem, end=end["number"], shot=shot["number"],
                    color=shot["color"], house=end["house"],
                    view=vname, view_cal=tl["calibration"][vname], t_rel=t_rel, t_enter=t_enter,
                    t_rest=shot.get("t_rest_s"), position=shot.get("position"),
                    thrower_slot=shot.get("thrower_slot"), shot_type=shot.get("shot_type"),
                    release_speed=shot.get("release_speed_m_s"), line=shot.get("line"))
        with gzip.open(outdir / f"{name}.pkl.gz", "wb") as fh:
            pickle.dump(dict(meta=meta, recs=recs), fh)
        ys = [r["y_m"] for r in recs]
        print(f"{name} {shot['color']:6s} {len(recs):4d} frames  y {min(ys, default=0):6.1f}..{max(ys, default=0):5.1f} m"
              f"  t {recs[0]['t'] - t_rel if recs else 0:+.1f}..{recs[-1]['t'] - t_rel if recs else 0:+.1f} s"
              f"  ({time.time() - tic:.1f} s)", flush=True)
