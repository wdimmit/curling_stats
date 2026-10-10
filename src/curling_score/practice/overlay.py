"""Where a throw's broom and lines fall in each piece of its clip.

The page draws them over the playing clip (frontend/site/Practice.jsx), so the
worker hands it, for each piece, the camera's view of three things on the ice:
the broom; the thrown line -- the straight line through the rock's free flight
(game/linetime.py), from about where it was released to the broom's depth, or
to the far tee when no broom was held; and the aim line, from where the rock
sat in the hack to the broom. Points are fractions of the camera's panel, so
they hold whatever size the clip is scaled to.

The long cameras are calibrated from the paint at the far end of each one's
view (geometry/sideview.py), so a line is surest there and drifts toward the
camera. The camera facing the thrower sees the sheet mirrored, as
`linetime.to_destination` has it.
"""

from curling_score.game.hogtime import CAMERA_FOR
from curling_score.game.linetime import HOG_Y, TEE_Y
from curling_score.practice.enrich import OTHER_HOUSE

# The thrown line is drawn from this far behind the throwing hog line: about
# where the rock leaves the hand, where its free flight begins.
RELEASE_BEHIND_HOG_M = 2.0
# Points this close to a camera, or behind it, are left out of its view.
NEAR_CAMERA_M = 1.0
SAMPLES = 24


def to_view(view, facing: bool, x: float, y: float):
    """A point on the ice in the destination frame as ``[u, v]``, fractions of
    ``view``'s panel; None when it is at or behind the camera, or the view has
    no lateral calibration."""
    if not getattr(view, "has_lateral", False):
        return None
    xv, yv = (-x, TEE_Y - y) if facing else (x, y)
    if yv >= view.d_m - NEAR_CAMERA_M:
        return None
    col, row = view.to_image(xv, yv)
    return [round(col / view.rect[2], 4), round(row / view.rect[3], 4)]


def _polyline(view, facing, a, b):
    """The straight line from ``a`` to ``b`` on the ice, as the view sees it:
    sampled, so a line running out of the picture keeps its visible part."""
    (ax, ay), (bx, by) = a, b
    pts = [to_view(view, facing, ax + (bx - ax) * i / (SAMPLES - 1),
                   ay + (by - ay) * i / (SAMPLES - 1)) for i in range(SAMPLES)]
    pts = [p for p in pts if p is not None]
    return pts if len(pts) >= 2 else None


def _lines(throw):
    """The thrown line's two ends and the aim line's, in the destination frame;
    either None."""
    line, broom = throw.get("line") or {}, throw.get("broom")
    thrown = aim = None
    hog_x = line.get("at_hog_x")
    if broom is not None and line.get("miss_m") is not None:
        end = (broom["x"] + line["miss_m"], broom["y"])
    elif line.get("aim_x") is not None:
        end = (line["aim_x"], 0.0)
    else:
        end = None
    if hog_x is not None and end is not None and end[1] < HOG_Y:
        y0 = HOG_Y + RELEASE_BEHIND_HOG_M
        x0 = hog_x + (hog_x - end[0]) * (y0 - HOG_Y) / (HOG_Y - end[1])
        thrown = ((x0, y0), end)
    start = line.get("start")
    if broom is not None and start is not None:
        aim = ((start["x"], start["y"]), (broom["x"], broom["y"]))
    return thrown, aim


def overlay(throw, plan, sideviews):
    """One entry per piece of ``plan``: its start and end on the clip's own
    clock, and where the thrown line, the aim line and the broom fall in that
    piece's camera. None when the throw has neither a line nor a broom."""
    if not throw.get("line") and not throw.get("broom"):
        return None
    thrown, aim = _lines(throw)
    broom = throw.get("broom")
    facing_view = CAMERA_FOR[OTHER_HOUSE[throw["house"]]]
    out, t = [], 0.0
    for p in plan.pieces:
        span = p.t1 - p.t0
        item = {"t0": round(t, 2), "t1": round(t + span, 2)}
        view = (sideviews or {}).get(p.view)
        if getattr(view, "has_lateral", False):
            facing = p.view == facing_view
            item["thrown"] = None if thrown is None else _polyline(view, facing, *thrown)
            item["aim"] = None if aim is None else _polyline(view, facing, *aim)
            item["broom"] = (None if broom is None
                             else to_view(view, facing, broom["x"], broom["y"]))
        out.append(item)
        t += span
    return out
