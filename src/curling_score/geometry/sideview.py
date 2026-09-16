"""The composite's two wide side views, and the far end's paint within them.

`geometry/layout.py` finds the overhead strip and ignores everything else;
everything else is two wide cameras, one at each end, each looking down the
sheet at the *other* end's house. Neither sees its own hog line -- that sits
about half a metre beneath the camera, out of frame -- so the throwing end's
hog line is watched by the camera at the target end, and which physical camera
that is alternates with the end, exactly as ``OTHER_HOUSE`` does.

They are worth the trouble because the overhead panel loses the throw before
the hog line on about 40% of deliveries: it is looking straight down at a
stone with the thrower and sweepers standing over it. From the end of the
sheet the sweepers are beside the stone, not on top of it.
"""

from dataclasses import dataclass

import numpy as np

from curling_score.geometry import constants as C

Rect = tuple[int, int, int, int]

# The minimum width worth calling a view; below this the strip is against the
# frame edge and there is no camera that side.
_MIN_VIEW_PX = 200


class SideViewError(RuntimeError):
    """A side view could not be located or read."""


def locate(layout, width: int, height: int) -> dict[str, Rect]:
    """The two wide views either side of the overhead strip."""
    x0 = min(layout.top[0], layout.bottom[0])
    x1 = max(layout.top[0] + layout.top[2], layout.bottom[0] + layout.bottom[2])
    views = {"left": (0, 0, x0, height), "right": (x1, 0, width - x1, height)}
    for name, (_x, _y, w, _h) in views.items():
        if w < _MIN_VIEW_PX:
            raise SideViewError(
                f"{name} view is only {w} px wide; the overhead strip runs to the edge"
            )
    return views


# Backboard to backboard is 45.720 m against 34.747 m tee to tee, so a tee sits
# 5.487 m from its own backboard and a camera on that wall is this far from the
# far tee. It sets only the curvature between the two fitted rows; the tripwire
# does not use it, and the depth scale at the hog line moves by under 5% across
# D = 35..45 m.
CAMERA_TO_FAR_TEE_M = C.TEE_TO_TEE_M + (C.BACKBOARD_TO_BACKBOARD_M - C.TEE_TO_TEE_M) / 2

# Measured on the clean plates of all ten of the club's side views (5 sheets,
# left and right), the annulus's greenness peak runs 6.44 to 13.27. 2.5 sits
# 2.6x below the weakest of those while staying clear of ice noise, and the
# hog line cannot be mistaken for paint at any threshold here: its greenness
# is negative, not merely small.
_GREEN_THRESHOLD = 2.5
# Fitted over both side views of all five club sheets, the tee lands at rows
# 410.2-489.7 of a 1080-row frame, i.e. 0.380-0.453. (0.33, 0.50) brackets that
# with about 0.05 spare either side.
_HOUSE_SEARCH = (0.33, 0.50)
# Over the same ten views, the hog line sits 57-66 px below the annulus's last
# green edge. 140 is a little over twice the largest gap seen.
_HOG_SEARCH_PX = 140

# Measured across the club's five sheets: the far tee sits 78-90 rows above
# its hog line. A fit outside this found the wrong row, and a wrong row is far
# worse than no calibration -- every crossing after it is confidently
# mistimed.
PLAUSIBLE_ROWS = (60.0, 110.0)


@dataclass(frozen=True)
class SideView:
    """One wide side view, and where the far end's paint sits in it."""

    rect: Rect
    tee_row: float
    hog_row: float
    d_m: float = CAMERA_TO_FAR_TEE_M

    @property
    def rows_tee_to_hog(self) -> float:
        return self.hog_row - self.tee_row

    def _map(self) -> tuple[float, float]:
        u = self.rows_tee_to_hog * (self.d_m - C.TEE_TO_HOGLINE_M) / C.TEE_TO_HOGLINE_M
        return self.d_m * u, self.tee_row - u

    def row_for(self, x_m: float) -> float:
        """The image row of a point ``x_m`` from the far tee, toward the camera."""
        c, yh = self._map()
        return yh + c / (self.d_m - x_m)

    def metres_at(self, row: float) -> float:
        """How far from the far tee a given image row is."""
        c, yh = self._map()
        return self.d_m - c / (row - yh)

    def stone_width_at(self, row: float, width_at_hog: float) -> float:
        """How many pixels wide a stone is when its edge sits on ``row``.

        The one lateral fact this class can honestly supply. It carries no
        lateral scale of its own -- ``row_for``/``metres_at`` are depth only --
        but a stone's apparent width scales with the same 1/(d - x) the row
        spacing does, so one measured width pins the rest.

        Substituting ``metres_at`` into that ratio collapses to a line through
        ``yh``::

            w(row) = width_at_hog * (d - TEE_TO_HOGLINE) * (row - yh) / c

        which is worth noticing: sizing a box by hand needs one multiply, not
        a segmentation model. A reviewer who says where a stone is has, by
        saying it, also said how big it is.
        """
        c, yh = self._map()
        return width_at_hog * (self.d_m - C.TEE_TO_HOGLINE_M) * (row - yh) / c


def _green_profile(plate, rect):
    x, y, w, h = rect
    view = np.asarray(plate, dtype=np.float32)[y:y + h, x:x + w]
    mid = view[:, int(w * 0.30):int(w * 0.70)]
    green = (mid[:, :, 1] - (mid[:, :, 0] + mid[:, :, 2]) / 2).mean(axis=1)
    return green, view.mean(axis=(1, 2))


def _crossings(sig, lo, hi, thresh):
    out = []
    for i in range(lo, min(hi, len(sig)) - 1):
        a, b = sig[i], sig[i + 1]
        if (a - thresh) * (b - thresh) < 0 and a != b:
            out.append(i + (thresh - a) / (b - a))
    return out


def _hog_row(lum, start, name):
    stop = min(start + _HOG_SEARCH_PX, len(lum) - 6)
    if stop <= start:
        raise SideViewError(f"{name}: no ice below the house to look for a hog line")
    dips = [lum[i] - lum[max(0, i - 9):i - 4].mean() for i in range(start, stop)]
    return start + int(np.argmin(dips))


def solve(plate, rect: Rect, name: str = "side") -> SideView:
    """Fit the far house's tee and hog rows from paint alone.

    The tee is fitted, not taken as the green annulus's centroid: perspective
    magnifies its near half and drags a centroid about 2 px toward the camera.
    The hog line is the darkest full-width row below the house.
    """
    green, lum = _green_profile(plate, rect)
    h = rect[3]
    lo, hi = int(h * _HOUSE_SEARCH[0]), int(h * _HOUSE_SEARCH[1])
    edges = _crossings(green, lo, hi, _GREEN_THRESHOLD)
    if len(edges) < 2:
        raise SideViewError(f"{name}: found {len(edges)} green edges, need at least 2")

    # Fit from the outermost pair alone. A noisy plate can throw extra
    # crossings inside the annulus -- one of the club's ten views does,
    # yielding six -- and picking the 8-ft pair by position (edges[1],
    # edges[-2]) then mispairs silently: the fit still lands in the plausible
    # band, which is worse than failing. The inner edges were also the less
    # trustworthy pair regardless: paint bleed widens the band inward by
    # about 7 px against 4 px at the outer edge, so preferring them over the
    # outer pair is not a trade worth making even when they are genuine.
    seen = [edges[0], edges[-1]]
    want = np.array([-C.R_12FT_M, C.R_12FT_M])

    hog = _hog_row(lum, int(edges[-1]) + 12, name)

    def error(tee):
        v = SideView(rect=rect, tee_row=tee, hog_row=hog)
        return float(((np.array([v.row_for(x) for x in want]) - seen) ** 2).sum())

    tee = min(np.arange(hog - _HOG_SEARCH_PX, hog - 30, 0.05), key=error)
    view = SideView(rect=rect, tee_row=float(tee), hog_row=float(hog))
    if not PLAUSIBLE_ROWS[0] <= view.rows_tee_to_hog <= PLAUSIBLE_ROWS[1]:
        raise SideViewError(
            f"{name}: tee to hog measured {view.rows_tee_to_hog:.1f} px, "
            f"outside the {PLAUSIBLE_ROWS[0]:.0f}-{PLAUSIBLE_ROWS[1]:.0f} px "
            f"every sheet falls in -- the fit found the wrong row")
    return view
