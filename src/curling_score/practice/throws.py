"""One practice throw, as the session page reads it.

A practice session has no ends, no teams and no rock numbers. Each delivery is
judged on its own: when it was released, its weight between the hog lines,
where it passed the skip's broom and where it stopped. This turns the
`game.shots.Shot` the tracker built for one delivery into that record, with
every time on the stream's clock.
"""

import math

from curling_score.game import fartime, hogtime, split
from curling_score.geometry import constants as C

# The rings a resting stone is said to be in: the smallest one it touches.
RINGS = (("button", C.R_BUTTON_M), ("4", C.R_4FT_M), ("8", C.R_8FT_M),
         ("12", C.R_12FT_M))
# A flight is thinned to about this many points a second: enough to draw it.
TRACK_HZ = 5.0


def ring_of(x_m: float, y_m: float) -> str:
    """Where a resting stone sits, in a curler's words: the smallest ring it
    touches, else short (in front of the house) or long (behind it), or out
    when it is past the back line or the sides."""
    if y_m < C.THROUGH_BACK_Y_M or abs(x_m) > C.SIDELINE_ABS_X_M:
        return "out"
    d = math.hypot(x_m, y_m)
    for name, radius in RINGS:
        if d - C.STONE_RADIUS_M <= radius:
            return name
    return "short" if y_m > 0 else "long"


def _r(v, n=3):
    return None if v is None else round(float(v), n)


def _thin(track, t0_s):
    out, last = [], None
    for i, (t, x, y) in enumerate(track):
        if last is None or t - last >= 1.0 / TRACK_HZ - 1e-6 or i == len(track) - 1:
            out.append([_r(t0_s + t, 2), _r(x), _r(y)])
            last = t
    return out


def _rest(shot):
    dv = shot.delivery
    if dv is None:
        return None
    if not dv.came_to_rest:
        return {"x": _r(dv.rest_x_m), "y": _r(dv.rest_y_m), "to_tee_m": None, "ring": "out"}
    # The house read averages the settled stone over a few seconds, which is
    # steadier than the last point of the flight.
    idx = shot.delivered_stone_index
    if idx is not None and idx < len(shot.stones):
        x, y = shot.stones[idx].x_m, shot.stones[idx].y_m
    else:
        x, y = dv.rest_x_m, dv.rest_y_m
    return {"x": _r(x), "y": _r(y), "to_tee_m": _r(math.hypot(x, y)), "ring": ring_of(x, y)}


def throw_record(shot, *, house: str, t0_s: float = 0.0) -> dict:
    """``shot`` as the page reads it. ``house`` is the end it was thrown to;
    ``t0_s`` is where the recording begins on the stream's clock."""
    dv, rel = shot.delivery, shot.release
    line, broom = shot.line, shot.target_broom
    sp = split.long_split(dv, t_hog=hogtime.crossing(shot),
                          v_hog=hogtime.speed_at_hog(shot), far=fartime.crossing(shot))
    t_thrown = rel.t if rel is not None else dv.t_enter
    return {
        "id": f"t_{t0_s + t_thrown:.1f}",
        "t_release_s": None if rel is None else _r(t0_s + rel.t, 2),
        "t_rest_s": None if dv is None else _r(t0_s + dv.t_rest, 2),
        "house": house,
        "color": shot.color,
        "arrived": dv is not None,
        "release_source": None if rel is None else getattr(rel, "source", "overhead"),
        "split_s": None if sp is None else _r(sp.seconds, 2),
        "release_speed": None if rel is None else _r(rel.speed_m_s),
        "curl": None if line is None else line.curl,
        "broom": None if broom is None else {"x": _r(broom.x_m, 4), "y": _r(broom.y_m, 4)},
        "line": None if line is None else {
            "miss_m": _r(line.miss, 4), "side": line.side,
            "hog_offset_m": _r(line.at_hog_offset, 4), "confirmed": line.confirmed,
            # Where it was aimed at the far tee: only when no broom was held.
            "aim_x": _r(getattr(line, "at_tee_x", None), 4)},
        "rest": _rest(shot),
        "track": [] if dv is None else _thin(dv.track, t0_s),
    }
