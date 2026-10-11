"""How fast the thrower set the rock turning, from the long camera facing them.

The camera is hogtime's -- the one at the far end, looking back at the thrower
-- and the rock comes towards it the whole way, from ~35 px wide at the hack to
~95 px at the bottom of the frame. From 0.5 s after the hog crossing it is read
at 10 fps, followed frame to frame from hogtime's own track, and each frame's
stone is cropped exactly as the training crops were cut (``scripts/spin/
extract.py``). The handle-point model finds the handle's neck and grip end;
``spinfit`` turns the grip's swing across the neck into a rate.

Its own read: hogtime keeps the band around the hog line (to ~10 m) and keeps
positions, not pixels, and the line pass's down-sheet path is on the other
camera. Measured in the spike (``~/curling-work/spin-spike``, 2026-10-10): 10
fps reads within 1% of 30 fps, the early rate is within ~10% of frame-by-frame
eye readings, and 122 of 128 rocks of four games' unlabelled ends are read.

Attach-only, like ``hogtime``, ``broomtime`` and ``linetime``: it may give a
shot a ``spin`` and can never add, drop or renumber one.
"""
from __future__ import annotations

import logging
import math

import numpy as np

from curling_score.game import linetime, spinfit
from curling_score.harvest import sidepool

log = logging.getLogger(__name__)

SPIN_FPS = 10.0
WINDOW_S = (0.5, 10.5)          # after the hog crossing
FROM_M = 8.0                    # past the throwing tee: the hand is off by here
MIN_CONF = 0.5                  # the weaker of the two points
STONE_M = 0.2896                # a stone's width
WIDTH_TOL = (0.6, 1.6)          # of STONE_M at the box's row
ABOVE_HOG_ROWS = 40             # the detector's rows start this far above the hog row
BOTTOM_PAD_PX = 14              # a box this close to the frame's bottom is leaving it
LOST_S = 0.8                    # unseen this long, the rock is let go
SEED_LOST_S = 1.0               # not found this long after the seed, give up
SEED_TAIL = 9                   # hogtime samples (30 fps) the seed's velocity comes from
CROP = (0.35, 0.5, 0.25)        # the training crop: each side, above, below


def seed_of(crossing, view, t_start):
    """Where hogtime's track has the rock at ``t_start``, in view pixels, and
    how fast it is moving there: ``(t, cx, bottom_row, vx, vy)``, or None."""
    if crossing is None or getattr(crossing, "track_key", None) is None \
            or not getattr(crossing, "samples", ()):
        return None
    edge = sidepool.band_crop(view)[1] - linetime.CROP_EDGE_ROWS
    samples = [s for s in crossing.samples if s[2] < edge]
    if not samples:
        return None
    t_seed = crossing.t if crossing.t is not None else samples[len(samples) // 2][0]
    chain = linetime.relink(samples, crossing.track_key, t_seed)
    if len(chain) < 3:
        return None
    i = min(range(len(chain)), key=lambda j: abs(chain[j][0] - t_start))
    tail = chain[max(0, i - SEED_TAIL + 1):i + 1] if i >= 2 else chain[:3]
    ts = np.array([s[0] for s in tail])
    cx = np.array([s[1] for s in tail])
    row = np.array([s[2] for s in tail])
    vx, vy = (np.polyfit(ts, cx, 1)[0], np.polyfit(ts, row, 1)[0]) if np.ptp(ts) > 0 else (0.0, 0.0)
    t, c, r = chain[i][0], chain[i][1], chain[i][2]
    return float(t), float(c), float(r), float(vx), float(vy)


def follow(times, boxes, seed, view, frame_h):
    """The rock frame to frame from ``seed``: ``(index, t, box)`` per frame it
    was found. Each frame's prediction runs the last speed on; the gate is a
    stone's width plus how far the speed could be off; a box far from a
    stone's width at its row is not the stone."""
    if seed is None:
        return []
    t_last, cx, row, vx, vy = seed
    out = []
    for i, (t, frame_boxes) in enumerate(zip(times, boxes)):
        dt = t - t_last
        if dt <= 0:
            continue
        if dt > (LOST_S if out else SEED_LOST_S):
            break
        px, py = cx + vx * dt, row + vy * dt
        best = None
        for b in frame_boxes:
            x0, _y0, x1, y1, _c = b
            want = STONE_M * view.lateral_px_per_m(y1)
            if want <= 0 or not WIDTH_TOL[0] * want <= x1 - x0 <= WIDTH_TOL[1] * want:
                continue
            d = math.hypot((x0 + x1) / 2 - px, y1 - py)
            if d < 0.6 * want + 4 + 0.5 * abs(vy) * dt and (best is None or d < best[0]):
                best = (d, b)
        if best is None:
            continue
        x0, _y0, x1, y1, _c = b = best[1]
        if y1 >= frame_h - BOTTOM_PAD_PX:
            break
        a = min(1.0, dt / 0.3)
        vx = (1 - a) * vx + a * ((x0 + x1) / 2 - cx) / dt
        vy = (1 - a) * vy + a * (y1 - row) / dt
        cx, row, t_last = (x0 + x1) / 2, y1, t
        out.append((i, t, b))
    return out


def crop_box(box, shape):
    """The training crop around a box, as slice bounds inside the frame."""
    x0, y0, x1, y1 = box[:4]
    w, h = x1 - x0, y1 - y0
    side, above, below = CROP
    H, W = shape[0], shape[1]
    return (max(0, int(x0 - side * w)), max(0, int(y0 - above * h)),
            min(W, int(x1 + side * w) + 1), min(H, int(y1 + below * h) + 1))


def read_spin(model, pose, video, view, shot, *, decode, detect, points):
    """One rock's spin from the camera facing its thrower, or None."""
    crossing = shot.hog_crossing
    t_ref = shot.t_hog_s if shot.t_hog_s is not None else getattr(crossing, "t", None)
    if t_ref is None:
        # A crossing refused for speed keeps its samples but no time: take
        # where its track met the hog row.
        rows = [s for s in crossing.samples if s[2] >= view.hog_row]
        if not rows:
            return None
        t_ref = min(rows)[0]
    frames, times = decode(video, view.rect, t_ref + WINDOW_S[0], t_ref + WINDOW_S[1], SPIN_FPS)
    if not len(frames):
        return None
    frame_h = frames.shape[1]
    boxes = detect(model, frames, times, int(view.hog_row) - ABOVE_HOG_ROWS, frame_h, shot.color,
                   imgsz=800, conf=0.25)
    tracked = follow(times, boxes, seed_of(crossing, view, times[0]), view, frame_h)
    if not tracked:
        return None
    patches = []
    for i, _t, b in tracked:
        x0, y0, x1, y1 = crop_box(b, frames.shape[1:])
        patches.append(frames[i][y0:y1, x0:x1])
    ts, ss = [], []
    for (_i, t, b), p in zip(tracked, points(pose, patches)):
        if p is None or p[2] < MIN_CONF:
            continue
        w = b[2] - b[0]
        if view.metres_at(b[3] - 0.12 * w) < FROM_M:
            continue
        ts.append(t)
        ss.append((p[1][0] - p[0][0]) / w)
    return spinfit.read(np.array(ts), np.array(ss))


def time_spins(shots, video, hog_view, *, model, pose=None, decode=None, detect=None,
               points=None) -> int:
    """Give each shot its ``spin``, in place; return how many it gave one.

    ``model`` is the side detector the line pass uses (``ctx.line_model``);
    ``pose`` defaults to the handle-point model, resolved per call so importing
    this loads nothing. A no-op without either, or without a laterally
    calibrated hog-camera view. A failure on one rock is logged and costs that
    rock its spin, not the end its analysis."""
    if model is None or hog_view is None or not hog_view.has_lateral:
        return 0
    if pose is None:
        from curling_score.detect import spinmodel
        pose = spinmodel.default_model()
        if pose is None:
            return 0
    if decode is None:
        from curling_score.detect import longview
        decode = longview.decode
    if detect is None:
        from curling_score.detect import sidemodel
        detect = sidemodel.detect_boxes
    if points is None:
        from curling_score.detect import spinmodel
        points = spinmodel.handle_points
    n = 0
    for shot in shots:
        if getattr(shot, "missing", False) or getattr(shot, "hog_crossing", None) is None:
            continue
        try:
            got = read_spin(model, pose, video, hog_view, shot,
                            decode=decode, detect=detect, points=points)
        except Exception:
            log.exception("spin pass failed on shot %s; it keeps no spin",
                          getattr(shot, "number", "?"))
            continue
        if got is not None:
            shot.spin = got
            n += 1
    return n
