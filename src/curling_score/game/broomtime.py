"""The skip's target broom, read in the second before the thrower crosses the tee.

The skip holds the pad on the ice as the aim for the whole delivery. Phase 0
found it down and still across t_tee -2.0..+0.5 s on 45 of 49 shots, so the
second before the tee crossing (the user's window) sits well inside the hold,
even with the camera and the panel up to ~0.9 s out of step. What counts is a
pad held STILL: a cluster seen in at least half the frames. A pad that moves
or lifts gives no marker, never an average of where it went. Two held pads --
the skip's and one resting by the sideline -- go to the one nearest the tee.

Like `hogtime` and `fartime`, this only attaches data to shots that exist.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from curling_score.detect import broommodel
from curling_score.game import thinking
from curling_score.geometry import constants as C

WINDOW_S = 1.0
FPS = 10
MIN_SEEN = 0.5
CLUSTER_M = 0.15
X_MAX_M = 2.2                   # inside the sheet's 2.375 half-width
BEHIND_M = 0.15                 # past the back line is the other skip's ground


@dataclass(frozen=True)
class TargetBroom:
    x_m: float
    y_m: float
    seen: float                 # fraction of the window's frames it was in
    confidence: float           # median detector confidence over those frames


def window_for(shot):
    """``(t0, t1)``: the second before this rock crossed the throwing end's tee,
    observed or estimated; None for a placeholder or an untimed shot."""
    if getattr(shot, "missing", False):
        return None
    t = thinking.tee_crossing(shot)
    return None if t is None else (t - WINDOW_S, t)


def pick_target(samples, n_frames: int) -> TargetBroom | None:
    """The pad held still, from ``(frame_index, x_m, y_m, conf)`` samples."""
    keep = [p for p in samples
            if abs(p[1]) <= X_MAX_M
            and -C.R_12FT_M - BEHIND_M <= p[2] <= C.TEE_TO_HOGLINE_M]
    clusters: list[list] = []
    for p in sorted(keep, key=lambda q: -q[3]):
        for c in clusters:
            cx = float(np.median([q[1] for q in c]))
            cy = float(np.median([q[2] for q in c]))
            if math.hypot(p[1] - cx, p[2] - cy) <= CLUSTER_M:
                c.append(p)
                break
        else:
            clusters.append([p])
    best = None
    for c in clusters:
        frames_in = len({q[0] for q in c})
        seen = frames_in / max(1, n_frames)
        if seen < MIN_SEEN:
            continue
        x = float(np.median([q[1] for q in c]))
        y = float(np.median([q[2] for q in c]))
        rank = (-frames_in, math.hypot(x, y))
        if best is None or rank < best[0]:
            best = (rank, TargetBroom(x, y, seen, float(np.median([q[3] for q in c]))))
    return None if best is None else best[1]


def time_target_brooms(shots, video, view, *, model=None, decode=None) -> None:
    """Give every shot its target broom, in place.

    A no-op without a model or a laterally calibrated view -- no brooms, not a
    failure. A shot with no pad held still keeps None.
    """
    if model is None or view is None or not view.has_lateral:
        return
    if decode is None:
        from curling_score.detect import longview
        decode = longview.decode
    for shot in shots:
        win = window_for(shot)
        if win is None:
            continue
        frames, _times = decode(video, view.rect, win[0], win[1], FPS)
        if not len(frames):
            continue
        samples = []
        for i, pads in enumerate(broommodel.find(model, frames, view)):
            for p in pads:
                x, y = view.to_house(p.col, p.row)
                samples.append((i, x, y, p.conf))
        shot.target_broom = pick_target(samples, len(frames))
