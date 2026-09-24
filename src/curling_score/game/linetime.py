"""Was the rock thrown at the broom?

The stone's line just past the throwing hog line -- read from the side camera
that times that crossing, where the stone has certainly left the hand and has
barely begun to curl -- extended to the skip's broom. Then, from the camera
behind the thrower, where the stone actually went, which confirms the line and
says which way it curled.

Everything here is in the timeline's frame: metres from the destination tee,
+y up-sheet toward the thrower, +x the thrower's right.

Attach-only, like ``hogtime`` and ``broomtime``: it may give a shot a
``line`` and can never add, drop or renumber one. Measured on four games on
2026-09-24 (``~/curling-work/line-spike``): the line fits straight to
0.2-0.4 cm and the camera behind the thrower agrees with it to about 4 cm.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from curling_score.geometry import constants as C

TEE_Y = C.TEE_TO_TEE_M                          # the throwing tee
HOG_Y = C.TEE_TO_TEE_M - C.TEE_TO_HOGLINE_M     # the throwing hog line
# Free flight only: past the hog line the stone has certainly been released,
# and within 10 m of the tee it has barely curled.
FIT_PAST_TEE_M = (C.TEE_TO_HOGLINE_M, 10.0)
FIT_MIN_N = 15
FIT_MIN_SPAN_M = 2.5
CONFIRM_FROM_M = 12.0       # the path must be seen at least this far out
CONFIRM_FIRST_M = 4.0       # ...and its first this-many metres compared
CONFIRM_TOL_M = 0.10
CURL_MIN_M = 0.05
THIN_M = 0.5


@dataclass(frozen=True)
class Fit:
    a: float
    b: float
    n: int
    rms: float

    def x(self, y: float) -> float:
        return self.a + self.b * y


@dataclass(frozen=True)
class Line:
    start: tuple | None          # (x, y): the stone before push-off
    at_hog_x: float
    at_hog_offset: float | None  # at_hog_x less the start-to-broom line there
    at_broom_x: float
    miss: float                  # at_broom_x - broom x, signed
    curl: str | None             # "left" | "right"
    side: str | None             # "wide" | "narrow"
    confirmed: bool | None
    hog_path: tuple              # ((y, x), ...) thinned, travel order
    path: tuple                  # ((y, x), ...) thinned, travel order
    fit_n: int
    fit_rms: float


def fit_line(track) -> Fit | None:
    """A straight x(y) through the free-flight samples, or None."""
    lo, hi = FIT_PAST_TEE_M
    pts = [(y, x) for _t, x, y, yp in track if lo <= yp <= hi]
    if len(pts) < FIT_MIN_N:
        return None
    ys = np.array([p[0] for p in pts]); xs = np.array([p[1] for p in pts])
    if np.ptp(ys) < FIT_MIN_SPAN_M:
        return None
    b, a = np.polyfit(ys, xs, 1)
    return Fit(a=float(a), b=float(b), n=len(pts), rms=float(np.std(xs - (a + b * ys))))


def aim_x(start, broom, y: float) -> float:
    """The start-to-broom line's x at depth ``y``."""
    (sx, sy), (bx, by) = start, broom
    return sx + (bx - sx) * (y - sy) / (by - sy)


def curl_of(fit: Fit, end) -> str | None:
    """Which way curl took the rock: from its thrown line to where it ended."""
    if end is None:
        return None
    d = end[0] - fit.x(end[1])
    if abs(d) < CURL_MIN_M:
        return None
    return "right" if d > 0 else "left"


def side_of(miss: float, curl: str | None) -> str | None:
    """Wide is the side away from the curl; narrow the side it curls toward."""
    if curl is None:
        return None
    toward = 1.0 if curl == "right" else -1.0
    return "wide" if np.sign(miss) == -toward else "narrow"


def confirmed_by(path, fit: Fit) -> bool | None:
    """Does the camera behind the thrower see the rock on the fitted line?"""
    if not path or path[0][0] < CONFIRM_FROM_M:
        return None
    first = [(y, x) for y, x in path if y >= path[0][0] - CONFIRM_FIRST_M]
    dev = float(np.median([abs(x - fit.x(y)) for y, x in first]))
    return dev <= CONFIRM_TOL_M


def thin(points, step: float = THIN_M) -> tuple:
    """About one (y, x) per ``step`` metres of travel, the last always kept."""
    if not points:
        return ()
    out = [points[0]]
    for p in points[1:]:
        if abs(p[0] - out[-1][0]) >= step - 1e-9:     # 5 x 0.1 must count as 0.5
            out.append(p)
    if out[-1] != points[-1]:
        out.append(points[-1])
    return tuple(out)


def measure(fit: Fit, track, start, broom, rest=None, path=()) -> Line:
    """Everything the Detail pane says about one rock, from its pieces."""
    bx, by = broom
    at_hog_x = fit.x(HOG_Y)
    offset = None if start is None else at_hog_x - aim_x(start, broom, HOG_Y)
    at_broom_x = fit.x(by)
    miss = at_broom_x - bx
    end = rest if rest is not None else ((path[-1][1], path[-1][0]) if path else None)
    curl = curl_of(fit, end)
    return Line(start=start, at_hog_x=at_hog_x, at_hog_offset=offset,
                at_broom_x=at_broom_x, miss=miss, curl=curl, side=side_of(miss, curl),
                confirmed=confirmed_by(list(path), fit),
                hog_path=thin([(y, x) for _t, x, y, _yp in track]),
                path=thin(list(path)), fit_n=fit.n, fit_rms=fit.rms)
