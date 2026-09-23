"""When each stone crossed the DESTINATION hog line, read from that end's panel.

Stage 3 of a throw; stage 2, the throwing end's line, is ``game/hogtime.py``.
Runs after the rules have settled the shot list and, like ``hogtime``, may only
attach timings to shots already in it -- nothing here adds, drops or renumbers
a shot.

The crossing rides on the shot rather than being computed where a split is
built, because ``split.long_split`` has two callers -- ``timeline.build_end``
and ``classify.classify_shot`` -- and the second only ever sees a shot.
"""

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
            t_near = split.line_crossing(leaving, near_line)
            v_near = split.speed_at_line(leaving, near_line)
        shot.far_crossing = split.FarCrossing(t=t, reach=reach, v_far=v_far,
                                              t_near_panel=t_near, v_near=v_near)


def crossing(shot):
    """The ``split.FarCrossing`` attached to this shot, or None if none was."""
    return getattr(shot, "far_crossing", None)
