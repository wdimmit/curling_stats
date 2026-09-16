"""When each stone crossed the throwing end's hog line, read from a side view.

This is the throwing end only. The target end's crossing comes from the
playing panel's own tripwire and is reliable -- one miss in 48 -- whereas the
throwing end's fails on 40% of shots because the thrower and sweepers stand
between the overhead camera and the stone.

Runs after the rules have settled the shot list, and like
``thinking.time_shots`` it may only attach a time to a rock already in it.
Nothing here can add, drop or renumber a shot.
"""

from curling_score.detect import longview, sidemodel

# Which side camera watches a given panel's hog line: the one at the *other*
# end, looking back. A camera never sees its own hog line -- that sits about
# half a metre beneath it, out of frame. Same alternation as ``OTHER_HOUSE``.
CAMERA_FOR = {"top": "left", "bottom": "right"}

# For a shot whose throw was never seen leaving the house, the arrival is the
# only anchor. Measured across three ends, a stone reaches the far panel 12-21 s
# after its release and crosses the hog line 3-5 s after that, which puts the
# crossing 8-20 s before the arrival. A wide window, so the refusals matter
# more here than anywhere else.
ARRIVAL_LOOKBACK_S = (8.0, 20.0)


def time_hog_crossings(shots, video, view, *, find=None) -> None:
    """Give every shot its throwing-end hog crossing, in place.

    ``find`` defaults to the side-view detector when one is configured, and to
    ``longview.find_crossing`` -- the colour scan -- when none is. Resolved per
    call rather than as a default argument value, so the model is not loaded by
    importing this module and a test can still pass its own.
    """
    if find is None:
        find = sidemodel.default_finder() or longview.find_crossing
    for shot in shots:
        if getattr(shot, "missing", False):
            continue
        release = getattr(shot, "release", None)
        if release is not None:
            t0 = release.t + longview.WINDOW_S[0]
            t1 = release.t + longview.WINDOW_S[1]
        else:
            delivery = getattr(shot, "delivery", None)
            if delivery is None:
                continue
            t0 = delivery.t_enter - ARRIVAL_LOOKBACK_S[1]
            t1 = delivery.t_enter - ARRIVAL_LOOKBACK_S[0]
        got = find(video, view, shot.color, t0, t1)
        if got:
            shot.t_hog_s = got.t
            # Carried so `split.long_split` can bound the pairing without a
            # release track. See `speed_at_hog` below.
            shot.v_hog_m_s = got.speed_m_s


def crossing(shot):
    """When this shot crossed the throwing end's hog line, if it was seen to.

    Not named ``hog_crossing``: ``split.hog_crossing`` already means the panel's
    own tripwire and takes a track, and two functions of that name timing the
    same line from different cameras is exactly the confusion to avoid.
    """
    return getattr(shot, "t_hog_s", None)


def speed_at_hog(shot):
    """How fast this shot was travelling at the throwing end's hog line, m/s.

    From the side view's own track through the perspective solve, so it is in
    real metres -- which is what lets it be compared against a distance.
    """
    return getattr(shot, "v_hog_m_s", None)
