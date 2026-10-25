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

Each piece also carries the slide: the rock from the hack to 1.5 m past the hog
line (``line.delivery``), a dot each for the page to show once the clip reaches
it, coloured as the Delivery chart colours them -- before the hand let go,
after it, or unknown when no release was read (``line.release``). A game's clip
burns the same dots into its pixels (game/clipburn.py).
"""

import math
import numbers

from curling_score.game.hogtime import CAMERA_FOR
from curling_score.game.linetime import HOG_Y, TEE_Y
from curling_score.practice.clips import CLIP_SIZE
from curling_score.practice.enrich import OTHER_HOUSE

# The thrown line is drawn from this far behind the throwing hog line: about
# where the rock leaves the hand, where its free flight begins.
RELEASE_BEHIND_HOG_M = 2.0
# Points this close to a camera, or behind it, are left out of its view.
NEAR_CAMERA_M = 1.0
SAMPLES = 24
# Slide dots closer than this in the clip's picture are one (game/clipburn.py's
# DOT_GAP_WHOLE, for a picture of the whole camera).
DOT_GAP_PX = 5


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


def lines_on_ice(throw):
    """The thrown line's two ends and the aim line's, in the destination frame;
    either None. A game clip draws them over the overhead panel too
    (game/clipburn.py)."""
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


def _finite_number(v) -> bool:
    """What the viewer's ``Number.isFinite`` passes: a finite number, and
    not a string or a bool that would convert to one."""
    return isinstance(v, numbers.Real) and not isinstance(v, bool) and math.isfinite(v)


def release_t(throw) -> float | None:
    """The release's time on the delivery's clock (``line.release``), or None
    when the throw has none or its first three entries are not finite numbers.
    Judged as the viewer judges it (`deliveryRelease`), so a clip and the chart
    beside it split their dots alike; entries past the third are not looked
    at, so the record can grow."""
    release = (throw.get("line") or {}).get("release")
    if not isinstance(release, (list, tuple)) or len(release) < 3:
        return None
    if not all(_finite_number(v) for v in release[:3]):
        return None
    return float(release[0])


def delivery(throw) -> list:
    """``line.delivery``'s samples, ``[t, y, x]`` with ``t`` in seconds from
    the release; any that are not at least that are left out."""
    d = (throw.get("line") or {}).get("delivery")
    return [p for p in d if isinstance(p, (list, tuple)) and len(p) >= 3] if d else []


def dot_kind(t: float, release: float | None) -> str:
    """A slide dot's colour, by name: before the release, after it, or unknown
    when ``release`` is None. Both are on the delivery's clock."""
    if release is None:
        return "unknown"
    return "before" if t < release else "after"


def slide_dots(throw, view, facing: bool) -> list:
    """The delivery in ``view`` as ``(t, u, v, kind)``, ``t`` on the stream's
    clock: none without the release's time on it or a lateral calibration.
    Points may lie outside the picture."""
    d, rel = delivery(throw), throw.get("t_release_s")
    if not d or rel is None or not getattr(view, "has_lateral", False):
        return []
    split = release_t(throw)
    out = []
    for t, y, x in (q[:3] for q in d):
        uv = to_view(view, facing, x, y)
        if uv is not None:
            out.append((rel + float(t), uv[0], uv[1], dot_kind(float(t), split)))
    return out


def thin(dots, sx: float, sy: float, gap: float) -> list:
    """``dots`` less those within ``gap`` px of the last one kept, at ``sx``
    by ``sy`` px per unit of the panel; the last is always kept."""
    out = []
    for i, dot in enumerate(dots):
        if (not out or i == len(dots) - 1
                or math.hypot((dot[1] - out[-1][1]) * sx, (dot[2] - out[-1][2]) * sy) >= gap):
            out.append(dot)
    return out


def _piece_dots(throw, view, facing, piece, t_clip) -> list:
    """The slide dots inside ``piece``'s picture as ``[t, u, v, kind]``, ``t``
    on the clip's clock: when the rock reaches each, or the piece's start for
    those it had passed before the cut to it."""
    dots = [d for d in slide_dots(throw, view, facing)
            if d[0] <= piece.t1 and 0.0 <= d[1] <= 1.0 and 0.0 <= d[2] <= 1.0]
    return [[round(t_clip + max(0.0, t - piece.t0), 2), u, v, kind]
            for t, u, v, kind in thin(dots, *CLIP_SIZE, DOT_GAP_PX)]


def overlay(throw, plan, sideviews):
    """One entry per piece of ``plan``: its start and end on the clip's own
    clock, and where the thrown line, the aim line, the broom and the slide's
    dots fall in that piece's camera. None when the throw has neither a line
    nor a broom."""
    if not throw.get("line") and not throw.get("broom"):
        return None
    thrown, aim = lines_on_ice(throw)
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
            item["dots"] = _piece_dots(throw, view, facing, p, t)
        out.append(item)
        t += span
    return out
