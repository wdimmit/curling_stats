"""Fit the overhead camera geometry from the painted house.

The house is the best calibration target we could ask for: concentric circles
of exactly known radii, in saturated colours, on white ice. The club paints the
12-foot ring green and the 4-foot ring blue.

The idle house -- the one not being played to -- is clean and unoccluded for
roughly fifteen minutes of every end, which gives us free calibration frames.
"""

from dataclasses import dataclass

import cv2
import numpy as np

from curling_score.geometry import constants as C

# Hue bands in OpenCV's 0-179 scale. Validated unmodified across all five sheets.
GREEN_HSV = ((35, 60, 40), (85, 255, 255))
BLUE_HSV = ((100, 50, 30), (140, 255, 255))
# Red wraps the hue circle, so it takes two bands.
RED_HSV = (((0, 80, 50), (10, 255, 255)), ((165, 80, 50), (179, 255, 255)))
# Black paint reads as a washed-out grey on video: V ~100-113 against ice at ~240.
DARK_HSV = ((0, 0, 0), (179, 90, 150))

_MIN_RING_AREA_PX = 400
_ANGLE_BINS = 360
_MIN_FILLED_BINS = 120  # a ring may be broken, but not mostly missing
_OUTLIER_TOL = 0.15  # reject boundary points this far off the first fit


class CalibrationError(RuntimeError):
    """The house could not be located well enough to calibrate."""


@dataclass(frozen=True)
class HousePaint:
    """How a club paints one house, as unions of HSV bands.

    ``twelve_ft`` is the band between the 12- and 8-foot circles, which the
    scale is fitted on. ``four_ft`` is optional and only checks the fit: its
    ``four_ft_edge`` edge is the 4-foot circle -- "outer" for a painted 4-foot
    (Granite's blue), "inner" for a painted 8-to-4 band around a white 4-foot.
    """

    twelve_ft: tuple
    four_ft: tuple | None = None
    four_ft_edge: str = "outer"
    # Where ``twelve_ft``'s inner edge really is: the 8-foot circle, except
    # where a club paints a logo band that stops short of it.
    inner_radius_m: float = C.R_8FT_M
    # Fit the panel's height-to-width scale from the 12-foot ellipse, for a
    # broadcast that stretches its overhead tiles. Granite's are square.
    fit_aspect: bool = False


GRANITE_HOUSE = HousePaint(twelve_ft=(GREEN_HSV,), four_ft=(BLUE_HSV,))


@dataclass(frozen=True)
class Ring:
    """A painted ring, as an ellipse fitted to its outer boundary."""

    center: tuple[float, float]
    axes_px: tuple[float, float]  # full lengths, major and minor
    angle_deg: float

    @property
    def radius_px(self) -> float:
        """Mean radius. The camera is near-nadir, so the ellipse is near-circular."""
        return (self.axes_px[0] + self.axes_px[1]) / 4.0

    @property
    def eccentricity(self) -> float:
        major, minor = max(self.axes_px), min(self.axes_px)
        return 0.0 if major == 0 else 1.0 - minor / major


@dataclass(frozen=True)
class Rings:
    twelve_ft: Ring
    four_ft: Ring | None


def _largest_blob(mask: np.ndarray):
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    if n < 2:
        return None
    idx = max(range(1, n), key=lambda i: stats[i, cv2.CC_STAT_AREA])
    if stats[idx, cv2.CC_STAT_AREA] < _MIN_RING_AREA_PX:
        return None
    return (labels == idx).astype(np.uint8)


def _outer_boundary(xs, ys, cx, cy):
    """The farthest mask pixel in each angular bin about (cx, cy).

    Deliberately not contour-based. An annulus that is broken anywhere -- by a
    sweeper, a stone, or a gap in the paint -- stops being a closed ring, and
    then OpenCV's external contour wraps the *inner* edge as well, dragging a
    least-squares ellipse fit inward. Sweeping by angle instead only ever sees
    the outer edge, and simply has no data for the bins that are occluded.
    """
    ang = np.arctan2(ys - cy, xs - cx)
    rad = np.hypot(xs - cx, ys - cy)
    bins = ((ang + np.pi) / (2 * np.pi) * _ANGLE_BINS).astype(int) % _ANGLE_BINS

    best = np.full(_ANGLE_BINS, -1.0)
    np.maximum.at(best, bins, rad)
    filled = np.nonzero(best >= 0)[0]
    if filled.size < _MIN_FILLED_BINS:
        return None, filled.size

    theta = (filled + 0.5) / _ANGLE_BINS * 2 * np.pi - np.pi
    pts = np.stack(
        [cx + best[filled] * np.cos(theta), cy + best[filled] * np.sin(theta)], axis=1
    )
    return pts.astype(np.float32), filled.size


def _fit_outer_ellipse(blob: np.ndarray, name: str) -> Ring:
    """Fit an ellipse to the outer boundary of a filled or annular blob."""
    ys, xs = np.nonzero(blob)
    if xs.size < 5:
        raise CalibrationError(f"the {name} has too few pixels to fit")
    cx, cy = float(xs.mean()), float(ys.mean())

    pts, n_filled = _outer_boundary(xs, ys, cx, cy)
    if pts is None:
        raise CalibrationError(
            f"the {name} covers only {n_filled} of {_ANGLE_BINS} angles"
        )
    (ecx, ecy), (a, b), angle = cv2.fitEllipse(pts)

    # One robustifying pass: drop boundary points far off that first fit (stray
    # paint from the adjacent sheet shows up this way) and fit again.
    r_fit = (a + b) / 4.0
    keep = np.abs(np.hypot(pts[:, 0] - ecx, pts[:, 1] - ecy) - r_fit) <= _OUTLIER_TOL * r_fit
    if keep.sum() >= 5 and keep.sum() < pts.shape[0]:
        (ecx, ecy), (a, b), angle = cv2.fitEllipse(pts[keep])

    return Ring(center=(float(ecx), float(ecy)), axes_px=(float(a), float(b)),
                angle_deg=float(angle))


def _paint_blob(hsv, bands):
    """The largest blob of any of ``bands``, closed over small gaps."""
    mask = np.zeros(hsv.shape[:2], np.uint8)
    for lo, hi in bands:
        mask |= cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    return _largest_blob(mask)


def find_rings(panel, paint: HousePaint = GRANITE_HOUSE) -> Rings:
    """Locate the 12-foot and, where it is painted, the 4-foot ring in an overhead panel."""
    hsv = cv2.cvtColor(np.asarray(panel), cv2.COLOR_BGR2HSV)
    blob = _paint_blob(hsv, paint.twelve_ft)
    if blob is None:
        raise CalibrationError("could not find the twelve_ft ring")
    twelve = _fit_outer_ellipse(blob, "twelve_ft")
    if paint.four_ft is None:
        return Rings(twelve_ft=twelve, four_ft=None)
    blob = _paint_blob(hsv, paint.four_ft)
    if blob is None:
        raise CalibrationError("could not find the four_ft ring")
    if paint.four_ft_edge == "outer":
        four = _fit_outer_ellipse(blob, "four_ft")
    else:
        r = _inner_radius(blob, *twelve.center)
        four = Ring(center=twelve.center, axes_px=(2 * r, 2 * r), angle_deg=0.0)
    return Rings(twelve_ft=twelve, four_ft=four)


def _inner_radius(blob: np.ndarray, cx: float, cy: float, aspect: float = 1.0) -> float:
    """Median nearest-edge radius: the inner boundary of an annulus, in
    pixels across the image where the panel is stretched by ``aspect``."""
    ys, xs = np.nonzero(blob)
    ang = np.arctan2((ys - cy) / aspect, xs - cx)
    rad = np.hypot(xs - cx, (ys - cy) / aspect)
    bins = ((ang + np.pi) / (2 * np.pi) * _ANGLE_BINS).astype(int) % _ANGLE_BINS
    best = np.full(_ANGLE_BINS, np.inf)
    np.minimum.at(best, bins, rad)
    seen = best[np.isfinite(best)]
    if seen.size < _MIN_FILLED_BINS:
        raise CalibrationError("ring covers too few angles to find its inner edge")
    return float(np.median(seen))


@dataclass(frozen=True)
class PanelCalib:
    """Maps panel pixels to sheet metres for one overhead camera.

    Origin is the tee of that panel's house; ``+y`` runs up-sheet toward the
    delivery end and ``+x`` to the right when facing down-sheet. The two houses
    are played toward from opposite directions, so the bottom panel is rotated
    180 degrees relative to the top one and both share a single convention.
    """

    center_px: tuple[float, float]
    px_per_m: float
    edge_erosion_px: float
    residual_m: float | None  # None where the club paints no 4-foot ring to check by
    flipped: bool  # True when the delivery end is at the top of the image
    # Pixels per metre down the image over pixels per metre across it:
    # ``px_per_m`` is the scale across. 1 but for a stretched broadcast tile.
    aspect: float = 1.0

    def to_sheet(self, x_px: float, y_px: float) -> tuple[float, float]:
        dx = (x_px - self.center_px[0]) / self.px_per_m
        dy = (y_px - self.center_px[1]) / (self.px_per_m * self.aspect)
        return (-dx, -dy) if self.flipped else (dx, dy)

    def to_pixels(self, x_m: float, y_m: float) -> tuple[float, float]:
        if self.flipped:
            x_m, y_m = -x_m, -y_m
        return (
            self.center_px[0] + x_m * self.px_per_m,
            self.center_px[1] + y_m * self.px_per_m * self.aspect,
        )


def solve(panel, delivery_side: str = "bottom",
          paint: HousePaint = GRANITE_HOUSE) -> PanelCalib:
    """Fit the pixel-to-metres mapping for one overhead panel.

    ``delivery_side`` says where thrown stones enter the frame -- "bottom" for
    the upper house, "top" for the lower one.

    Scale comes from the *sum* of the green ring's two edges. Colour
    thresholding erodes the painted ring by a couple of pixels on each side, so
    the 12-foot edge reads small and the 8-foot edge reads large by the same
    amount; adding them cancels the bias exactly:

        r12 + r8 = px_per_m * (R12 + R8)

    That leaves the blue 4-foot ring completely unused by the fit, so predicting
    it is a genuine holdout check rather than a restatement of the input.
    Measured across all ten panels of the five sheets: 0.29 cm mean error,
    0.69 cm worst case.
    """
    if delivery_side not in ("top", "bottom"):
        raise ValueError("delivery_side must be 'top' or 'bottom'")

    from curling_score.geometry import lighting

    if not lighting.is_calibratable(panel):
        raise CalibrationError(
            f"panel is {lighting.classify(panel).value}; too poorly lit to calibrate"
        )

    rings = find_rings(panel, paint)
    cx, cy = rings.twelve_ft.center

    hsv = cv2.cvtColor(np.asarray(panel), cv2.COLOR_BGR2HSV)
    blob = _paint_blob(hsv, paint.twelve_ft)
    if blob is None:
        raise CalibrationError("lost the 12-foot ring while measuring its inner edge")

    r12 = rings.twelve_ft.radius_px
    aspect = 1.0
    if paint.fit_aspect:
        cx, cy, r12, aspect = _axis_aligned_outer(blob)
    r8 = _inner_radius(blob, cx, cy, aspect)
    px_per_m = (r12 + r8) / (C.R_12FT_M + paint.inner_radius_m)
    if px_per_m <= 0:
        raise CalibrationError("degenerate scale")

    erosion = px_per_m * C.R_12FT_M - r12
    residual_px = (None if rings.four_ft is None
                   else abs(px_per_m * C.R_4FT_M - rings.four_ft.radius_px))

    return PanelCalib(
        center_px=(float(cx), float(cy)),
        px_per_m=float(px_per_m),
        edge_erosion_px=float(erosion),
        residual_m=None if residual_px is None else float(residual_px / px_per_m),
        flipped=(delivery_side == "top"),
        aspect=float(aspect),
    )


def _axis_aligned_outer(blob: np.ndarray):
    """``(cx, cy, half-width, height / width)`` of an ellipse with its axes
    along the image's, fitted to the blob's outer edge: a stretched tile's
    circle, which a rotated ellipse would fit no better."""
    ys, xs = np.nonzero(blob)
    pts, n_filled = _outer_boundary(xs, ys, float(xs.mean()), float(ys.mean()))
    if pts is None:
        raise CalibrationError(f"the twelve_ft covers only {n_filled} of {_ANGLE_BINS} angles")
    x, y = pts[:, 0].astype(float), pts[:, 1].astype(float)
    keep = np.ones(len(x), bool)
    for _ in range(2):
        # x^2 + B y^2 + D x + E y + F = 0
        (B, D, E, F), *_ = np.linalg.lstsq(
            np.c_[y[keep] ** 2, x[keep], y[keep], np.ones(keep.sum())], -x[keep] ** 2, rcond=None)
        if B <= 0:
            raise CalibrationError("the twelve_ft does not fit an ellipse")
        cx, cy = -D / 2, -E / (2 * B)
        a2 = cx * cx + B * cy * cy - F
        if a2 <= 0:
            raise CalibrationError("the twelve_ft does not fit an ellipse")
        a, aspect = float(np.sqrt(a2)), float(1 / np.sqrt(B))
        r = np.hypot(x - cx, (y - cy) / aspect)
        keep = np.abs(r - a) <= _OUTLIER_TOL * a
    return float(cx), float(cy), a, aspect
