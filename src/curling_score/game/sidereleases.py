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

One case filling in cannot reach: a rock the overhead saw at *neither* end. A
hogged rock never comes into the far house, so its release is all the evidence
it leaves, and when the thrower hides that too there is no shot to fill in.
PHbZ3EKOhMI end 6 opened with exactly that -- the red lead whose releases the
overhead lost all game hogged rock 1 at 5341 -- and with fifteen rocks seen the
missing one was placed last, which gave red the hammer and named every red
thrower one rock early. ``lost_rocks`` looks for such throws, and only in an
end the rules leave short: it scans this same camera where a rock could hide
and hands back every slide that no release and no arrival accounts for, as the
releases ``detect.release`` settles like any other.
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
    top, bot = _band(view)
    return stone_points(view, times, detect(model, frames, times, top, bot, color))


def _band(view):
    """The view's rows from 1 m behind the hack to just below the hog line."""
    top = max(0, int(view.row_for(-(C.TEE_TO_HACKLINE_M + 1.0))) - 30)
    return top, int(view.hog_row) + 40


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
        s.release = _as_release(s.color, got, off_rel, off_tee)
        s.tee_estimated = False
        n += 1
    return n


def _as_release(color, got: Slide, off_rel: float = 0.0, off_tee: float = 0.0):
    # The climb, on the fitted line and the tee's correction: it is what the
    # thinking clock and the broom read the tee crossing from.
    track = tuple((got.t_at(y) + off_tee, got.x_m, y)
                  for y in (FIT_Y_M[0], REF_Y_M, 0.0, FIT_Y_M[1]))
    return R.Release(color, got.t_ref + off_rel, got.y_max_m, got.speed_m_s,
                     track=track, source=SOURCE)


# -- Rocks the overhead saw at neither end -------------------------------------
#
# The scan's rate. Half the fill-in's: a slide at 2 m/s spends 3 s in FIT_Y_M,
# so 5 fps still gives it about 15 samples against FIT_MIN_N of 4, and on
# PHbZ3EKOhMI it found the same 94 slides at 5 fps as the fill-in's windows do
# at 10. The scan reads far more video than a fill-in, so the rate is its cost.
SCAN_FPS = 5.0
# Decoded this much at a time: the view's frames are full RGB, ~2.6 MB each.
SCAN_CHUNK_S = 20.0
# Slides are fitted in windows this long, stepped this far, and two fits of
# one colour closer than SAME_SLIDE_S are one slide, the better-sampled kept.
# A slide crosses FIT_Y_M in about 3 s; two throws are never within
# R.MIN_SEPARATION_S of each other.
SLIDE_WINDOW_S = 6.0
SLIDE_STEP_S = 1.0
SAME_SLIDE_S = 3.0
# Where to look, as multiples of the end's median time between rocks: a gap
# this long between two rocks seen can hold one that was not, and the search
# reaches this far before the first rock seen and after the last. Bounded
# rather than running to the end's edges, because between games players slide
# practice rocks, and the first end's run-up reaches back into that.
# PHbZ3EKOhMI end 6's lost rock 1 was 59 s, 1.1 of its end's median, before
# the first rock seen.
GAP_FACTOR = 1.7
# An end's median before it has two gaps to take one from.
DEFAULT_INTERVAL_S = 55.0
# A slide must be surer to add a rock than to time one: it has to run this
# far, and reach the tee. The fill-in knows a rock is there and asks only for
# FIT_MIN_SPAN_M. Scanning every end of five games (PHbZ3EKOhMI, bLkgfZaDSKw,
# 2z7vOezY9Bw and qMDNIgIGHZs of 2026-09-27, VXU9xwmugRg of the spring; 32
# ends) found 494 slides of rocks the timelines hold, 2 of them short of
# this, lost to their sweepers; 4 slides where a timeline had a blank, all
# 3.65-5.54 m and past +2.6 m; and 2 with no rock behind them at all, 1.46 m
# to -0.58 m and 1.74 m to -1.25 m.
LOST_MIN_SPAN_M = 2.0
LOST_MIN_Y_MAX_M = 0.0
# ...and slide faster than a stone pushed by hand. Of those 494, 5 slid under
# this (0.82-1.04 m/s) and the median 2.11; the four rocks found where a
# timeline had a blank slid at 1.95-2.24; the five stones seen pushed through
# a throwing house between ends slid at 0.81-1.02.
LOST_MIN_SPEED_M_S = 1.1
# The search starts this long after the end's run-up does, which is the
# previous end's last rock coming to rest. That house is this end's throwing
# house, and it is being cleared: on bLkgfZaDSKw, the 6 s after end 1's last
# rest held four slides of both colours at 0.8-1.0 m/s, the players pushing
# end 1's stones. The quickest turnaround in five games (27 of them) threw the
# next rock 32.8 s after the close, the median 55 s.
TURNAROUND_S = 20.0
# Release to arrival, for an arrival with no release to time it by: the middle
# of the 11-24 s the overhead measures.
TYPICAL_LAG_S = 17.0


def scan_points(video, view, t0, t1, *, fps=SCAN_FPS, model=None, decode=None,
                detect=None) -> dict:
    """Both colours' centre-line points over ``t0..t1``, as ``side_points``
    gives one colour's: each stretch of video is decoded once for both."""
    if decode is None:
        from curling_score.detect import longview
        decode = longview.decode
    if detect is None:
        from curling_score.detect import sidemodel
        detect = sidemodel.detect_band
    if model is None:
        from curling_score.detect import sidemodel
        model = sidemodel.default_model()
    top, bot = _band(view)
    out = {"red": [], "yellow": []}
    t = t0
    while t < t1:
        frames, times = decode(video, view.rect, t, min(t + SCAN_CHUNK_S, t1), fps)
        if len(frames):
            for color in out:
                out[color] += stone_points(
                    view, times, detect(model, frames, times, top, bot, color))
        t += SCAN_CHUNK_S
    return out


def find_slides(points, t0, t1) -> list[Slide]:
    """Every distinct slide in one colour's ``points`` over ``t0..t1``, in time
    order: ``fit_slide`` in each window, one slide per SAME_SLIDE_S."""
    points = sorted(points)
    found: list[Slide] = []
    w = t0
    while w < t1:
        got = fit_slide([q for q in points if w <= q[0] < min(w + SLIDE_WINDOW_S, t1)])
        if got is not None:
            same = [i for i, s in enumerate(found) if abs(s.t_ref - got.t_ref) < SAME_SLIDE_S]
            if not same:
                found.append(got)
            elif got.n > found[same[0]].n:
                found[same[0]] = got
        w += SLIDE_STEP_S
    return sorted(found, key=lambda s: s.t_ref)


def search_windows(rocks, t0, t1) -> list[tuple[float, float]]:
    """Where an end short of rocks could be hiding one, given ``(t, colour)``
    for when each rock it has was thrown: before the first, after the last,
    between two of a colour -- the other team's rock must lie between them --
    and inside any other gap GAP_FACTOR times the end's median. Each window
    keeps R.MIN_SEPARATION_S clear of the rocks either side of it, and none
    leaves ``t0..t1``.

    The colour rule is not the gap rule's to catch: s_1aAUMPjTB3eCfHqHM end 2
    lost a red between two yellows 87 s apart, when its reach was 88 s."""
    rocks = sorted(rocks)
    if not rocks:
        return [(t0, t1)] if t1 > t0 else []
    ts = [t for t, _c in rocks]
    gaps = [b - a for a, b in zip(ts, ts[1:])]
    interval = sorted(gaps)[len(gaps) // 2] if len(gaps) >= 2 else DEFAULT_INTERVAL_S
    reach = GAP_FACTOR * interval
    sep = R.MIN_SEPARATION_S
    spans = [(ts[0] - reach, ts[0] - sep)]
    spans += [(a + sep, b - sep) for (a, ca), (b, cb) in zip(rocks, rocks[1:])
              if b - a >= reach or ca == cb]
    spans.append((ts[-1] + sep, ts[-1] + reach))
    out = []
    for a, b in spans:
        a, b = max(a, t0), min(b, t1)
        if b - a >= SLIDE_WINDOW_S / 2:
            out.append((a, b))
    return out


def rock_times(kept, thrown_by, lags=()) -> list[tuple[float, str]]:
    """``(t, colour)`` for when each rock in ``kept`` was thrown: its release
    where one was paired with it (``thrown_by`` maps a release to its
    arrival), the arrival itself for a rock ``detect.release`` stood in for,
    whose arrival *is* its release, and otherwise the arrival less the end's
    median lag."""
    by_arrival = {id(d): r.t for r, d in thrown_by.items()}
    lags = sorted(lags)
    lag = lags[len(lags) // 2] if lags else TYPICAL_LAG_S
    out = []
    for d in kept:
        if id(d) in by_arrival:
            t = by_arrival[id(d)]
        elif getattr(d, "reason", "") in R.RELEASE_REASONS:
            t = d.t_enter
        else:
            t = d.t_enter - lag
        out.append((t, d.color))
    return out


def lost_rocks(video, view, *, kept, releases, thrown_by, arrivals, house_frames,
               t0, t1, scan=None) -> list:
    """The rocks an end the rules left short lost at both ends of the sheet, as
    deliveries ``fit.fit_end`` can place; [] when there are none.

    ``kept`` is the end as the rules built it, ``releases`` every overhead
    release, ``thrown_by`` their pairing to ``arrivals``, the far house's
    candidates, and ``house_frames`` its detections. ``t0`` is where the end's
    run-up begins, and the search starts TURNAROUND_S after it; it never
    passes ``t1``. Only a slide a throw could make counts: LOST_MIN_SPAN_M
    long, reaching LOST_MIN_Y_MAX_M, and no other slide within
    R.MIN_SEPARATION_S of it. A slide within R.MIN_SEPARATION_S of an overhead
    release is that release, whatever colour either read; one that pairs with
    an arrival no release claimed is that arrival's lost release, which the
    fill-in times later. Whatever is left is settled by ``R.unaccounted`` exactly as an
    overhead release with no arrival would be: from what the house did, a
    rock that arrived unseen, struck a stone, or was hogged.

    ``scan`` is ``(video, view, t0, t1) -> {colour: [(t, y, x)]}``,
    ``scan_points`` by default. A decode or detector error costs the end only
    what the search would have added.
    """
    if view is None or not getattr(view, "has_lateral", False):
        return []
    if scan is None:
        scan = scan_points
    lags = [d.t_enter - r.t for r, d in thrown_by.items()]
    windows = search_windows(rock_times(kept, thrown_by, lags), t0 + TURNAROUND_S, t1)
    found = []
    for a, b in windows:
        try:
            pts = scan(video, view, a, b)
        except Exception:
            log.exception("long-camera search failed over %.0f-%.0f s", a, b)
            continue
        for color, p in pts.items():
            found += [(color, s) for s in find_slides(p, a, b)
                      if s.span_m >= LOST_MIN_SPAN_M and s.y_max_m >= LOST_MIN_Y_MAX_M
                      and s.speed_m_s >= LOST_MIN_SPEED_M_S]
    # Two throws are never within R.MIN_SEPARATION_S of each other, so slides
    # that close together are stones being moved, and none of them is a throw.
    found = [(c, s) for c, s in found
             if not any(o is not s and abs(o.t_ref - s.t_ref) < R.MIN_SEPARATION_S
                        for _c, o in found)]
    known = [r.t for r in releases]
    new = [_as_release(color, s) for color, s in sorted(found, key=lambda f: f[1].t_ref)
           if all(abs(s.t_ref - t) >= R.MIN_SEPARATION_S for t in known)]
    if not new:
        return []
    claimed = {id(d) for d in thrown_by.values()}
    free = [d for d in arrivals if id(d) not in claimed]
    return R.unaccounted(new, free, house_frames)
