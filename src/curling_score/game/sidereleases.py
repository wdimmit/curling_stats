"""Releases the overhead panel lost, timed from the long camera facing the thrower.

The overhead release (``detect/release.py``) is the stone seen leaving the hack
and crossing a line a foot behind the tee. A thrower who slides with head and
shoulders over the stone hides it from above: the panel sees it for the first
metre out of the hack and loses it 0.4-0.9 m short of that line. The misses
cluster on a player -- on 2026-09-27, 10 of one lead's 12 rocks -- and they cost
each rock its release time and speed, its aim line, and a real tee crossing for
the thinking clock and the broom read.

The camera at the other end (``hogtime.CAMERA_FOR[OTHER_HOUSE[house]]``, the
one that already times the throwing hog line) looks up the sheet at the
delivery, with the stone running in front of the thrower. Its slide past the
hack is near-constant speed, so a straight line through the early samples
times it at any point. Measured on three games (``~/curling-work/release-spike``):
it timed every rock the overhead timed (83/83, 89/89, 110/110) to a median
absolute deviation of 0.05-0.12 s, and 33 of the overhead's 36 misses.

Its instant is not the overhead's: the overhead times a stone's first
sighting, and a long camera can run out of step with the panel -- by -0.11 to
+0.23 s between those games, and by 0.45 s for part of one. So each end
calibrates itself: rocks both saw give the correction the filled ones get.

Fill-in only, and attach-only like ``hogtime``, ``broomtime`` and ``linetime``:
it gives a release to a shot that has none, never replaces the overhead's, and
cannot add, drop or renumber a shot. An end with nothing to fill reads no video.
"""
from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass

import numpy as np

from curling_score.detect import release as R
from curling_score.game import hogtime, split, thinking
from curling_score.geometry import constants as C

log = logging.getLogger(__name__)

# Where the slide is timed: 2 m behind the throwing tee, about where the
# overhead panel first sees a stone leave the hack.
REF_Y_M = -2.0
# The early slide only: from behind the house to a few metres past the tee,
# before sweeping and curl. Past the tee the thrower is often up and the
# sweepers alongside.
FIT_Y_M = (-3.0, 3.0)
FIT_TOL_M = 0.25
FIT_MIN_N = 4
FIT_MIN_SPAN_M = 1.0
# A delivery's slide. Good fits in the spike ran 0.83-2.55 m/s; a stone parked
# in front of the hack, or nudged into place, is far slower.
SPEED_M_S = (0.8, 4.5)
# Off the centre line is not the delivery: the next rock parked beside the
# hack, a stone kicked to the side.
X_MAX_M = 0.6
# Read from 6 s before the hog crossing (the slide takes 3-4 s from the hack)
# to just after it. With no crossing, from the arrival: the widest release-to-
# arrival lag the overhead measures, to the shortest.
WINDOW_FROM_HOG_S = (-6.0, 0.5)
FPS = 10.0
# Rocks the end calibrates against: the overhead releases with the longest
# tracks, since those are the surest first sightings. Fewer than CALIB_MIN,
# and no correction at all -- the three games spread either side of zero.
CALIB_N = 4
CALIB_MIN = 2
SOURCE = "side"


@dataclass(frozen=True)
class Slide:
    """The stone's slide past the hack, as the long camera saw it."""

    t_ref: float        # when it crossed REF_Y_M
    t_tee: float        # when it crossed the tee
    speed_m_s: float
    n: int
    span_m: float
    x_m: float
    y_max_m: float

    def t_at(self, y: float) -> float:
        return self.t_ref + (y - REF_Y_M) / self.speed_m_s


def fit_slide(points) -> Slide | None:
    """A straight y(t) through the slide in ``points`` -- (t, y, x), y past the
    throwing tee -- or None when they hold no slide.

    The line through the most samples within ``FIT_TOL_M`` of it, chosen from
    every pair; then one sample a frame, and a refit on those. A stone parked in
    front of the hack, and stray boxes, sit off any line a slide makes.
    """
    lo, hi = FIT_Y_M
    p = [(float(t), float(y), float(x)) for t, y, x in points if lo <= y <= hi]
    if len(p) < FIT_MIN_N:
        return None
    best = None
    for i in range(len(p)):
        for j in range(i + 1, len(p)):
            (t1, y1, _), (t2, y2, _) = p[i], p[j]
            if t2 == t1:
                continue
            v = (y2 - y1) / (t2 - t1)
            if not SPEED_M_S[0] <= v <= SPEED_M_S[1]:
                continue
            inl = [q for q in p if abs(q[1] - (y1 + v * (q[0] - t1))) <= FIT_TOL_M]
            if len({q[0] for q in inl}) >= FIT_MIN_N and (best is None or len(inl) > len(best)):
                best = inl
    if best is None:
        return None
    b, c = np.polyfit([q[0] for q in best], [q[1] for q in best], 1)
    frame = {}
    for q in best:
        r = abs(q[1] - (b * q[0] + c))
        if q[0] not in frame or r < frame[q[0]][0]:
            frame[q[0]] = (r, q)
    kept = [frame[t][1] for t in sorted(frame)]
    ts = np.array([q[0] for q in kept]); ys = np.array([q[1] for q in kept])
    if len(kept) < FIT_MIN_N or np.ptp(ys) < FIT_MIN_SPAN_M:
        return None
    b, c = np.polyfit(ts, ys, 1)
    if not SPEED_M_S[0] <= b <= SPEED_M_S[1]:
        return None
    t_ref = float((REF_Y_M - c) / b)
    return Slide(t_ref=t_ref, t_tee=float(-c / b), speed_m_s=float(b), n=len(kept),
                 span_m=float(np.ptp(ys)), x_m=float(np.median([q[2] for q in kept])),
                 y_max_m=float(ys.max()))


def stone_points(view, times, per_frame) -> list:
    """The side model's boxes -- per frame, (cx, bottom_row, width, conf) in the
    view's rows -- as (t, y past the throwing tee, x) on the centre line.

    The same mapping ``linetime`` uses: a box's bottom edge is the stone's near
    side, one radius short of its centre, and the camera faces the thrower, so
    its image-left is the thrower's right.
    """
    out = []
    for t, boxes in zip(times, per_frame):
        for cx, row, _w, _c in boxes:
            y = view.metres_at(row) - C.STONE_RADIUS_M
            x = -view.lateral_x(cx, view.row_for(y))
            if abs(x) <= X_MAX_M:
                out.append((float(t), float(y), float(x)))
    return out


def side_points(video, view, color, t0, t1, *, model=None, decode=None, detect=None):
    """What the long camera saw of ``color`` from 1 m behind the hack to below
    the hog line, over ``t0..t1``."""
    if decode is None:
        from curling_score.detect import longview
        decode = longview.decode
    if detect is None:
        from curling_score.detect import sidemodel
        detect = sidemodel.detect_band
    if model is None:
        from curling_score.detect import sidemodel
        model = sidemodel.default_model()
    frames, times = decode(video, view.rect, t0, t1, FPS)
    if not len(frames):
        return []
    top = max(0, int(view.row_for(-(C.TEE_TO_HACKLINE_M + 1.0))) - 30)
    bot = int(view.hog_row) + 40
    return stone_points(view, times, detect(model, frames, times, top, bot, color))


def _window(shot):
    t_hog = getattr(shot, "t_hog_s", None)
    if t_hog is not None:
        return t_hog + WINDOW_FROM_HOG_S[0], t_hog + WINDOW_FROM_HOG_S[1]
    delivery = getattr(shot, "delivery", None)
    if delivery is None:
        return None
    return delivery.t_enter - R.MAX_LAG_S, delivery.t_enter - R.MIN_LAG_S


def _slide_for(shot, video, view, points):
    w = _window(shot)
    if w is None:
        return None
    return fit_slide(points(video, view, shot.color, *w))


def time_side_releases(shots, video, view, *, points=None) -> int:
    """Give each shot the overhead lost a release from the long camera, in
    place; return how many it gave one.

    ``view`` is the camera that sees the thrower's house -- hogtime's. A no-op
    without lateral calibration, which the centre-line test needs. ``points``
    is ``(video, view, color, t0, t1) -> [(t, y, x)]``, the side model by
    default. A decode or detector error costs that rock its release, not the
    end its analysis.
    """
    if view is None or not getattr(view, "has_lateral", False):
        return 0
    todo = [s for s in shots if not getattr(s, "missing", False)
            and getattr(s, "release", None) is None]
    if not todo:
        return 0
    if points is None:
        points = side_points

    def seen(shot):
        try:
            return _slide_for(shot, video, view, points)
        except Exception:
            log.exception("long-camera release failed on shot %s; it keeps none",
                          getattr(shot, "number", "?"))
            return None

    # The end's own correction, from the overhead releases with the most samples.
    known = sorted((s for s in shots if not getattr(s, "missing", False)
                    and getattr(s, "release", None) is not None
                    and getattr(s.release, "source", "overhead") != SOURCE),
                   key=lambda s: -len(getattr(s.release, "track", ())))[:CALIB_N]
    d_rel, d_tee = [], []
    for s in known:
        got = seen(s)
        if got is None:
            continue
        d_rel.append(s.release.t - got.t_ref)
        tee = split.crossing_time(getattr(s.release, "track", ()), thinking.TEE_LINE_Y_M)
        if tee is not None:
            d_tee.append(tee - got.t_tee)
    off_rel = statistics.median(d_rel) if len(d_rel) >= CALIB_MIN else 0.0
    off_tee = statistics.median(d_tee) if len(d_tee) >= CALIB_MIN else 0.0

    n = 0
    for s in todo:
        got = seen(s)
        if got is None:
            continue
        # The climb, on the fitted line and the tee's correction: it is what the
        # thinking clock and the broom read the tee crossing from.
        track = tuple((got.t_at(y) + off_tee, got.x_m, y)
                      for y in (FIT_Y_M[0], REF_Y_M, 0.0, FIT_Y_M[1]))
        s.release = R.Release(s.color, got.t_ref + off_rel, got.y_max_m, got.speed_m_s,
                              track=track, source=SOURCE)
        s.tee_estimated = False
        n += 1
    return n
