"""When each stone crossed the DESTINATION hog line, read from that end's panel.

Stage 3 of a throw; stage 2, the throwing end's line, is ``game/hogtime.py``.
Runs after the rules have settled the shot list and, like ``hogtime``, may only
attach timings to shots already in it -- nothing here adds, drops or renumbers
a shot.

The crossing rides on the shot rather than being computed where a split is
built, because ``split.long_split`` has two callers -- ``timeline.build_end``
and ``classify.classify_shot`` -- and the second only ever sees a shot.
"""

import dataclasses

import numpy as np

from curling_score.game import split


def time_far_crossings(shots, *, near_line, far_line) -> None:
    """Attach a ``split.FarCrossing`` to every shot that is not a placeholder.

    ``far_line`` is the destination panel's ``HogLine`` and ``near_line`` the
    throwing panel's. Either may be None when that panel's paint could not be
    found; whatever depends on it is then simply absent.
    """
    for shot in shots:
        if getattr(shot, "missing", False):
            continue
        dv = getattr(shot, "delivery", None)
        rel = getattr(shot, "release", None)
        arriving = tuple(getattr(dv, "track", None) or ()) if dv is not None else ()
        leaving = tuple(getattr(rel, "track", None) or ()) if rel is not None else ()
        t, reach, v_far = None, 0.0, None
        if arriving and far_line is not None:
            t, reach = split.far_crossing(arriving, far_line,
                                          max_reach=split.FAR_REACH_MAX_U)
            v_far = split.speed_at_line(arriving, far_line)
        t_near, v_near = None, None
        if leaving and near_line is not None:
            # The throwing panel's line is crossed from the house side, not
            # the side an arrival meets it from.
            t_near = split.line_crossing(leaving, near_line, departing=True)
            v_near = split.speed_at_line(leaving, near_line, departing=True)
        shot.far_crossing = split.FarCrossing(t=t, reach=reach, v_far=v_far,
                                              t_near_panel=t_near, v_near=v_near)


def crossing(shot):
    """The ``split.FarCrossing`` attached to this shot, or None if none was."""
    return getattr(shot, "far_crossing", None)


# An end camera (`geometry/endcam.py`) sits above the destination's hog line,
# looking back at its house, so it sees the stone come in over that line --
# where a house-only overhead, like Cincinnati's, has no line to time. Its view
# ends just past the line: the stone's near edge, the bottom of its box, comes
# into view at about the line, and the crossing is the moment its leading edge,
# a stone's width further in, met it. That is read off a straight line fitted
# to the first FIT_S of the stone in view, reached back at most MAX_REACH_M.
END_WINDOW_S = (5.0, 20.0)        # after the throwing end's crossing
END_WINDOW_FROM_RELEASE_S = (8.0, 25.0)
END_FPS = 30.0
EDGE_ROWS = 6                     # a box this near the view's edge is cut by it
ENTER_BAND_M = 1.0                # first seen this near the view's edge
FIT_S = 0.6
FIT_MIN = 5
MIN_SPEED_M_S = 0.5
MAX_REACH_M = 0.8
ENTER_COL_PX = 60
ENTER_SPEED_M_S = 2.0              # a pace to look for it at, before it shows its own
FAST_M_S = 4.0                    # no delivered stone is faster at the far hog line


def end_camera_crossing(tracks, view, t_from):
    """``(t, speed_m_s)`` of the stone coming in over the hog line of ``view``,
    from ``sidemodel.propose``'s tracks, or None.

    The first sighting near the view's edge with a stone moving in behind it:
    boxes within ENTER_COL_PX of the column it came in at, and no further in
    than it could have come. A stone parked there does not move.
    """
    from curling_score.geometry import constants as C

    edge = view.rect[3] - EDGE_ROWS
    enter_m = view.metres_at(edge) - ENTER_BAND_M
    line_m = C.TEE_TO_HOGLINE_M + 2 * C.STONE_RADIUS_M      # its near edge, then
    seen = sorted((t, view.metres_at(row), cx) for track in tracks.values()
                  for t, row, _w, cx in track if row < edge and t >= t_from)
    for t0, y0, c0 in seen:
        if y0 < enter_m:
            continue
        near = {}
        for t, y, cx in seen:
            if t0 < t <= t0 + FIT_S and abs(cx - c0) <= ENTER_COL_PX \
                    and y0 - FAST_M_S * (t - t0) - 0.3 <= y <= y0 + 0.3:
                near.setdefault(t, []).append(y)
        # Frame by frame, the box nearest where the stone should be by now, at
        # the pace it has come so far: a stone it passes is standing still.
        ts, ys, v = [t0], [y0], -ENTER_SPEED_M_S
        for t in sorted(near):
            want = ys[-1] + v * (t - ts[-1])
            y = min(near[t], key=lambda c: abs(c - want))
            if y <= ys[-1] + 0.15:
                if len(ts) >= 2:
                    v = (y - ys[0]) / (t - ts[0])
                ts.append(t)
                ys.append(y)
        if len(ts) < FIT_MIN:
            continue
        slope, icpt = np.polyfit(ts, ys, 1)
        if -slope < MIN_SPEED_M_S or line_m - (slope * ts[0] + icpt) > MAX_REACH_M:
            continue
        return float((line_m - icpt) / slope), float(-slope)
    return None


def time_far_crossings_from_end_camera(shots, video, view, *, model=None, decode=None, propose=None) -> int:
    """For each shot the panels gave no far crossing, the end camera's. In
    place; returns how many it timed. The window hangs off the throwing end's
    crossing, or the release without one."""
    from curling_score.detect import longview, sidemodel

    if view is None or not getattr(view, "end_camera", False):
        return 0
    model = model or sidemodel.default_model()
    if model is None:
        return 0
    decode = decode or longview.decode
    propose = propose or sidemodel.propose
    n = 0
    for shot in shots:
        if getattr(shot, "missing", False):
            continue
        have = crossing(shot)
        if have is not None and have.t is not None:
            continue
        t_hog = getattr(shot, "t_hog_s", None)
        rel = getattr(shot, "release", None)
        if t_hog is not None:
            t0, t1 = t_hog + END_WINDOW_S[0], t_hog + END_WINDOW_S[1]
        elif rel is not None:
            t0, t1 = rel.t + END_WINDOW_FROM_RELEASE_S[0], rel.t + END_WINDOW_FROM_RELEASE_S[1]
        else:
            continue
        frames, times = decode(video, view.rect, t0, t1, END_FPS)
        if not len(frames):
            continue
        tracks = sidemodel.split_at_gaps(propose(model, frames, view, shot.color, times),
                                         sidemodel.STRADDLE_GAP_MAX_S)
        got = end_camera_crossing(tracks, view, t0)
        if got is None:
            continue
        old = have or split.FarCrossing(t=None)
        shot.far_crossing = dataclasses.replace(old, t=got[0], reach=0.0)
        n += 1
    return n


# --- from the long camera behind the thrower ---------------------------------------------
#
# A club whose overheads show only the houses (DFW) has no destination hog line
# in a panel, and no end camera. The long camera at the throwing end looks down
# the sheet at the destination house and has that hog line in view: the stone
# goes away from it, across the paint, small but tracked by the side model.

LONG_WINDOW_S = (4.0, 20.0)           # after the throwing end's crossing
LONG_WINDOW_FROM_RELEASE_S = (6.0, 24.0)
LONG_FPS = 10.0
# Samples this close to the line, either side, are fitted for the crossing.
LONG_FIT_M = 1.5
# A thrown stone at the far hog line: slower than a hit can be, faster than a
# stone a few metres from stopping. Outside it, the track is something else.
LONG_SPEED_M_S = (0.6, 5.0)
LONG_MIN_SAMPLES = 3


def long_camera_crossing(tracks, view, t_from):
    """``(t, speed_m_s)`` of a stone's leading edge reaching the destination
    hog line's outer edge, seen from the long camera behind the thrower, or
    None. A track's rows are its near edge (toward the camera), so the leading
    edge is a stone's width further on. Fitted, track by track, to the samples
    within LONG_FIT_M of the line: a stone going away from the camera at a
    thrown stone's pace; the earliest such crossing after ``t_from`` wins."""
    from curling_score.geometry import constants as C

    line = C.TEE_TO_HOGLINE_M + C.HOGLINE_WIDTH_M
    _c, horizon = view._map()                # rows at or above it are no point on the ice
    best = None
    for track in tracks.values():
        pts = [(t, view.metres_at(row) - 2 * C.STONE_RADIUS_M) for t, row, *_ in track
               if t >= t_from and row > horizon + 1]
        near = [(t, y) for t, y in pts if abs(y - line) <= LONG_FIT_M]
        if len(near) < LONG_MIN_SAMPLES:
            continue
        ts = np.array([t for t, _ in near])
        ys = np.array([y for _, y in near])
        if ys.max() <= line or ys.min() >= line or np.ptp(ts) <= 0:
            continue                       # the line was not crossed within the samples
        b, a = np.polyfit(ts, ys, 1)
        if not (LONG_SPEED_M_S[0] <= -b <= LONG_SPEED_M_S[1]):
            continue
        t = (line - a) / b
        if t < t_from or not (ts.min() - 0.5 <= t <= ts.max() + 0.5):
            continue
        if best is None or t < best[0]:
            best = (float(t), float(-b))
    return best


def time_far_crossings_from_long_camera(shots, video, view, *, model=None, decode=None,
                                        propose=None) -> int:
    """For each shot nothing else timed at the far hog line, the long camera
    behind the thrower's (`long_camera_crossing`). In place; how many it timed."""
    from curling_score.detect import longview, sidemodel

    if view is None or not getattr(view, "has_lateral", True):
        return 0
    model = model or sidemodel.default_model()
    if model is None:
        return 0
    decode = decode or longview.decode
    propose = propose or sidemodel.propose
    n = 0
    for shot in shots:
        if getattr(shot, "missing", False):
            continue
        have = crossing(shot)
        if have is not None and have.t is not None:
            continue
        t_hog = getattr(shot, "t_hog_s", None)
        rel = getattr(shot, "release", None)
        if t_hog is not None:
            t0, t1 = t_hog + LONG_WINDOW_S[0], t_hog + LONG_WINDOW_S[1]
        elif rel is not None:
            t0, t1 = rel.t + LONG_WINDOW_FROM_RELEASE_S[0], rel.t + LONG_WINDOW_FROM_RELEASE_S[1]
        else:
            continue
        frames, times = decode(video, view.rect, t0, t1, LONG_FPS)
        if not len(frames):
            continue
        tracks = sidemodel.split_at_gaps(propose(model, frames, view, shot.color, times),
                                         sidemodel.STRADDLE_GAP_MAX_S)
        got = long_camera_crossing(tracks, view, t0)
        if got is None:
            continue
        old = have or split.FarCrossing(t=None)
        shot.far_crossing = dataclasses.replace(old, t=got[0], reach=0.0)
        n += 1
    return n
