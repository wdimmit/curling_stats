"""Time the hog crossing with a trained detector instead of a colour scan.

``longview`` proposes stones by colour: find a saturated red or yellow blob,
walk down for granite, call the bottom of it the stone's edge. That proposer is
what this replaces. Everything downstream -- what counts as moving, what makes
two crossers ambiguous, steadiness, the speed bound, the interpolation between
the two samples that straddle the line -- comes from
``longview.crossing_from_tracks``, unchanged and shared.

That sharing is the point. The interesting question is not "can a neural net
time a crossing" but "was the *proposer* the thing holding this back", and the
only way to answer it is to change one step and nothing else.

The model reads the band crop ``harvest.sidepool.band_crop`` defines, because
that is what it was trained on: full-height views contain racks, a scoreboard
and a far wall it has never been shown. Rows come back in the crop's
coordinates and are lifted into the view's before tracking, since the gates
compare them against ``view.hog_row``.
"""

from __future__ import annotations

import numpy as np

from curling_score.detect import longview
from curling_score.harvest import sidepool

# Class indices as `train.dataset.CLASSES` orders them.
_CLASS_FOR = {"red": 0, "yellow": 1}

# Below this the detector is guessing, and a guess entering a track is worse
# than a gap: `crossing_from_tracks` interpolates between neighbouring samples,
# so one bad row moves the reported time rather than being outvoted.
CONF_MIN = 0.35

# A box more than this far from the width the perspective solve predicts for
# its row is not a stone at that distance. The cold evaluation of ds13a found
# exactly one bad box and it was 11.44x, at a confidence of 0.65 that no
# threshold on confidence alone would have caught.
WIDTH_TOL = (0.5, 2.0)


def propose(model, frames, view, color: str, times, *, conf=CONF_MIN):
    """``{track_key: [(t, edge_row, body_px), ...]}`` from a trained detector.

    Shaped exactly like the dict ``longview.find_in_frames`` builds, so the
    same gates can read it.
    """
    want = _CLASS_FOR[color]
    y0, y1 = sidepool.band_crop(view)
    x, _y, w, h = view.rect
    tracks: dict[int, list] = {}

    crops = []
    for frame in frames:
        arr = np.asarray(frame)
        # `longview.decode` already cropped to view.rect, so a frame may arrive
        # either full-width or view-width; take the band from whichever.
        win = arr if arr.shape[1] == w else arr[:, x:x + w]
        lo, hi = max(0, y0), min(win.shape[0], y1)
        crops.append(np.ascontiguousarray(win[lo:hi, :, ::-1]))   # RGB -> BGR
    if not crops:
        return tracks

    lo = max(0, y0)
    for t, res in zip(times, model.predict(crops, imgsz=800, conf=conf,
                                           verbose=False)):
        if res.boxes is None:
            continue
        for b, c, cf in zip(res.boxes.xyxy.cpu().numpy(),
                            res.boxes.cls.cpu().numpy(),
                            res.boxes.conf.cpu().numpy()):
            if int(c) != want:
                continue
            bx0, _by0, bx1, by1 = (float(v) for v in b)
            edge_row = by1 + lo                    # crop rows -> view rows
            body_px = bx1 - bx0
            expect = view.stone_width_at(edge_row,
                                         longview.STONE_WIDTH_AT_HOG_PX)
            if expect <= 1 or not (WIDTH_TOL[0] <= body_px / expect <= WIDTH_TOL[1]):
                continue
            cx = (bx0 + bx1) / 2
            key = int(cx // 120)      # a stone never moves 120 px sideways
            tracks.setdefault(key, []).append((t, edge_row, body_px))
    return tracks


# What this proposer's edge is worth against a person's eye. `longview`'s
# OFFSET_S (+0.073 s) corrects the COLOUR SCAN, whose trailing edge includes
# the stone's contact shadow and so reads early. A trained detector draws the
# granite's own edge, slightly above the contact point, and reads late instead
# -- so inheriting the colour scan's correction pushed it later still.
#
# Fitted on end 1 of datasets/hogmarks and checked on end 2; see
# scripts/ds13/compare_hogmarks.py. Set to 0.0 to measure the raw proposer.
OFFSET_S = 0.0


def find_in_frames(model, frames, view, color: str, times,
                   offset_s: float = None) -> longview.Crossing:
    """Time the crossing of ``view.hog_row`` with the model as proposer."""
    return longview.crossing_from_tracks(
        propose(model, frames, view, color, times), view,
        offset_s=OFFSET_S if offset_s is None else offset_s)


def find_crossing(model, video, view, color: str, t0: float, t1: float,
                  fps: float = 30.0) -> longview.Crossing:
    """Seek, decode and time one crossing. Mirrors ``longview.find_crossing``."""
    frames, times = longview.decode(video, view.rect, t0, t1, fps)
    if not len(frames):
        return longview.Crossing(None, "no frames decoded", longview.KEY_NO_FRAMES)
    return find_in_frames(model, frames, view, color, times)
