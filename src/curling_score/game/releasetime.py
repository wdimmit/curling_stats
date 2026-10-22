"""When the thrower's hand let go of the handle, read from the long camera.

The delivery path already says where the rock was at every moment of the
throw, and the long camera faces the thrower, so the hand is always in the
same place relative to the rock. Cutting a small crop around the rock in each
frame turns the problem into one a small model can answer -- is the hand still
on the handle? -- and the release is the frame where that answer flips from
yes to no and stays there. This module is the part that needs no model: the
crops, the stacks of frames the model reads, and the step fit that turns its
per-frame answers into one time and one point on the path.
"""
from __future__ import annotations

import cv2
import numpy as np

from curling_score.game import linetime

# A crop is this far either side of the rock, above it (toward the thrower's
# head) and below it, in metres at the rock, and is resampled to CROP_PX
# (width, height) so the hand is the same size whether the throw is near or far.
# That puts the rock at (80, 88): half the width across, 0.55 m of 0.67 down
# (the height is (UP + DOWN) at the 160 px/m the width gives).
CROP_ACROSS_M = 0.5
CROP_UP_M = 0.55
CROP_DOWN_M = 0.12
CROP_PX = (160, 107)

# The model sees each frame with two earlier ones, so a hand moving off the
# handle shows as motion: seconds from the frame, oldest first.
STACK_S = (-4 / 30, -2 / 30, 0.0)

# Crops start this far past the throwing tee (negative: behind it), where the
# hack end of the lane begins to show the hand.
FROM_M = -0.5

# A step on one of the first frames is the model seeing a hand already off, or
# nothing at all, not a release.
FIRST_UNSEEN = 2


def rock_at(delivery, o: float) -> tuple[float, float] | None:
    """``(y, x)`` of the rock ``o`` seconds after the release, interpolated on
    the path's ``[t, y, x]`` samples; None before the first or after the last."""
    ts = [s[0] for s in delivery]
    if not ts[0] <= o <= ts[-1]:
        return None
    return (float(np.interp(o, ts, [s[1] for s in delivery])),
            float(np.interp(o, ts, [s[2] for s in delivery])))


def hand_crops(strips, times, origin, view, delivery, t_release):
    """Crops of the hand's neighbourhood: ``(crops, o, yp)`` with ``crops``
    ``(N, 107, 160)`` uint8, ``o`` each one's seconds after the release and
    ``yp`` the rock's metres past the throwing tee.

    One per strip whose rock is on the path and no further behind the tee than
    ``FROM_M``. The strips are the lane keeper's, so a point in the view is
    ``origin`` less in a strip; the camera faces the thrower, so the rock's
    ``x`` metres to his right are ``-x`` in the view.
    """
    width, height = CROP_PX
    col0, row0 = origin
    crops, os_, yps = [], [], []
    for strip, t in zip(strips, times):
        o = float(t) - t_release
        at = rock_at(delivery, o)
        if at is None:
            continue
        y, x = at
        yp = linetime.TEE_Y - y
        if yp < FROM_M:
            continue
        col, row = view.to_image(-x, yp)
        ppm = view.lateral_px_per_m(view.row_for(yp))
        z = width / (2 * CROP_ACROSS_M * ppm)
        m = np.float32([[z, 0, width / 2 - z * (col - col0)],
                        [0, z, CROP_UP_M * ppm * z - z * (row - row0)]])
        crops.append(cv2.warpAffine(strip, m, CROP_PX, flags=cv2.INTER_LINEAR,
                                    borderMode=cv2.BORDER_CONSTANT, borderValue=0))
        os_.append(o)
        yps.append(yp)
    return (np.array(crops, np.uint8).reshape(-1, height, width),
            np.array(os_, float), np.array(yps, float))


def stacks(crops, o) -> np.ndarray:
    """``(N, 3, 107, 160)`` float32 in 0..1: for each frame, the crops nearest
    ``o + s`` for ``s`` in ``STACK_S``. Nearest by time, clamped to the
    sequence, so a thinned sequence gives the frames the same time back."""
    o = np.asarray(o, float)
    out = np.empty((len(crops), len(STACK_S)) + crops.shape[1:], np.float32)
    for k, s in enumerate(STACK_S):
        want = o + s
        after = np.clip(np.searchsorted(o, want), 1, len(o) - 1)
        nearer_before = want - o[after - 1] <= o[after] - want
        out[:, k] = crops[np.where(nearer_before, after - 1, after)] / 255.0
    return out


def step_fit(p) -> int:
    """The frame the hand comes off: ``k`` minimising ``sum(p[:k]) +
    sum(1 - p[k:])`` for ``p`` the per-frame chance the hand is off. ``k == 0``
    is off throughout and ``k == len(p)`` is never off."""
    p = np.asarray(p, float)
    cost_on = np.concatenate([[0.0], np.cumsum(p)])
    cost_off = np.concatenate([np.cumsum((1 - p)[::-1])[::-1], [0.0]])
    return int(np.argmin(cost_on + cost_off))


def release_at(p, o, delivery) -> tuple[float, float, float] | None:
    """``(t, y, x)`` of the release: the rock's point on the path at the
    frame ``step_fit`` picks. None when the step is on one of the first
    ``FIRST_UNSEEN`` frames or never comes."""
    k = step_fit(p)
    if k < FIRST_UNSEEN or k >= len(p):
        return None
    at = rock_at(delivery, float(o[k]))
    return None if at is None else (float(o[k]), *at)
