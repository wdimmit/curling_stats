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

_MIN_RING_AREA_PX = 400
_ANGLE_BINS = 360
_MIN_FILLED_BINS = 120  # a ring may be broken, but not mostly missing
_OUTLIER_TOL = 0.15  # reject boundary points this far off the first fit


class CalibrationError(RuntimeError):
    """The house could not be located well enough to calibrate."""


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
    four_ft: Ring


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


def find_rings(panel) -> Rings:
    """Locate the 12-foot and 4-foot rings in an overhead panel."""
    hsv = cv2.cvtColor(np.asarray(panel), cv2.COLOR_BGR2HSV)
    found = {}
    for name, (lo, hi) in (("twelve_ft", GREEN_HSV), ("four_ft", BLUE_HSV)):
        mask = cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        blob = _largest_blob(mask)
        if blob is None:
            raise CalibrationError(f"could not find the {name} ring")
        found[name] = _fit_outer_ellipse(blob, name)
    return Rings(**found)


def _inner_radius(blob: np.ndarray, cx: float, cy: float) -> float:
    """Median nearest-edge radius: the inner boundary of an annulus."""
    ys, xs = np.nonzero(blob)
    ang = np.arctan2(ys - cy, xs - cx)
    rad = np.hypot(xs - cx, ys - cy)
    bins = ((ang + np.pi) / (2 * np.pi) * _ANGLE_BINS).astype(int) % _ANGLE_BINS
    best = np.full(_ANGLE_BINS, np.inf)
    np.minimum.at(best, bins, rad)
    seen = best[np.isfinite(best)]
    if seen.size < _MIN_FILLED_BINS:
        raise CalibrationError("green ring covers too few angles to find its inner edge")
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
    residual_m: float
    flipped: bool  # True when the delivery end is at the top of the image

    def to_sheet(self, x_px: float, y_px: float) -> tuple[float, float]:
        dx = (x_px - self.center_px[0]) / self.px_per_m
        dy = (y_px - self.center_px[1]) / self.px_per_m
        return (-dx, -dy) if self.flipped else (dx, dy)

    def to_pixels(self, x_m: float, y_m: float) -> tuple[float, float]:
        if self.flipped:
            x_m, y_m = -x_m, -y_m
        return (
            self.center_px[0] + x_m * self.px_per_m,
            self.center_px[1] + y_m * self.px_per_m,
        )


def solve(panel, delivery_side: str = "bottom") -> PanelCalib:
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

    rings = find_rings(panel)
    cx, cy = rings.twelve_ft.center

    hsv = cv2.cvtColor(np.asarray(panel), cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, np.array(GREEN_HSV[0], np.uint8), np.array(GREEN_HSV[1], np.uint8))
    green = cv2.morphologyEx(green, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    blob = _largest_blob(green)
    if blob is None:
        raise CalibrationError("lost the green ring while measuring its inner edge")

    r12 = rings.twelve_ft.radius_px
    r8 = _inner_radius(blob, cx, cy)
    px_per_m = (r12 + r8) / (C.R_12FT_M + C.R_8FT_M)
    if px_per_m <= 0:
        raise CalibrationError("degenerate scale")

    erosion = px_per_m * C.R_12FT_M - r12
    residual_px = abs(px_per_m * C.R_4FT_M - rings.four_ft.radius_px)

    return PanelCalib(
        center_px=(float(cx), float(cy)),
        px_per_m=float(px_per_m),
        edge_erosion_px=float(erosion),
        residual_m=float(residual_px / px_per_m),
        flipped=(delivery_side == "top"),
    )
