"""Detect curling stones in an overhead panel.

We detect the **handle**, not the granite body. The handle is a small saturated
disc of the team's colour, and crucially it stays separated when stones touch:
on a nine-stone cluster packed into the 4-foot, the bodies were in contact but
the ~10 px handles were still ~20 px apart. Body detection would have merged
them. This sidesteps the touching-stone failure that dominates the published
literature (0.97 -> 0.72 mAP under heavy occlusion).

Hue bands were validated unmodified across all five sheets.
"""

from dataclasses import dataclass

import cv2
import numpy as np

from curling_score.geometry import constants as C

# OpenCV hue is 0-179, so red wraps around the ends.
RED_HSV = [((0, 110, 80), (8, 255, 255)), ((172, 110, 80), (179, 255, 255))]
YELLOW_HSV = [((18, 110, 110), (38, 255, 255))]

# Apparent handle radius as a function of up-sheet position, measured over 274
# detections across all five sheets. The camera sits above the house, so a
# handle out by the hog line is seen obliquely and reads much smaller. Within
# the scoring region (|d| <= 1.971 m) it is essentially flat, which is what
# matters; positions far up-sheet are approximate until the oblique view is
# modelled properly.
_RADIUS_BY_Y_M = (
    (-2.0, 0.082),
    (-0.5, 0.095),
    (0.5, 0.098),
    (1.5, 0.093),
    (2.5, 0.080),
    (3.5, 0.056),
    (5.0, 0.046),
)
HANDLE_RADIUS_M = 0.095  # nominal, at the house

_MIN_AREA_FRACTION = 0.15  # of the expected handle disc at that position
_MAX_AREA_FRACTION = 4.0
_SPLIT_AREA_FRACTION = 1.8  # above this, try to split a merged blob
# Only rejects pathological slivers. Genuine handles measure just 0.53-0.76:
# at ~13 px across, pixelation inflates the perimeter and drags circularity
# down, so anything near 0.55 starts discarding real stones in tight clusters.
_MIN_CIRCULARITY = 0.30


def expected_handle_radius_m(y_m: float) -> float:
    """Apparent handle radius at a given up-sheet position, in metres."""
    ys = [p[0] for p in _RADIUS_BY_Y_M]
    rs = [p[1] for p in _RADIUS_BY_Y_M]
    return float(np.interp(y_m, ys, rs))


def _circularity(part: np.ndarray) -> float:
    """4*pi*A / P^2 -- 1.0 for a perfect disc, lower for ragged shapes."""
    contours, _ = cv2.findContours(part, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return 0.0
    c = max(contours, key=cv2.contourArea)
    perimeter = cv2.arcLength(c, True)
    if perimeter <= 0:
        return 0.0
    return float(min(1.0, 4.0 * np.pi * cv2.contourArea(c) / (perimeter**2)))


@dataclass(frozen=True)
class Detection:
    """A stone found in one panel, positioned in sheet metres."""

    color: str
    x_m: float
    y_m: float
    x_px: float
    y_px: float
    area_px: float
    confidence: float

    @property
    def distance_to_tee(self) -> float:
        return (self.x_m**2 + self.y_m**2) ** 0.5


def _color_mask(hsv: np.ndarray, bands) -> np.ndarray:
    mask = np.zeros(hsv.shape[:2], np.uint8)
    for lo, hi in bands:
        mask |= cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8))
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))


def _split_blob(mask: np.ndarray, expect_area: float):
    """Split a blob that holds several handles, using a distance transform.

    Stones cannot physically overlap, so peaks in the distance transform are
    genuine separate handles.
    """
    dist = cv2.distanceTransform(mask, cv2.DIST_L2, 3)
    expect_r = max(1.0, (expect_area / np.pi) ** 0.5)
    _, peaks = cv2.threshold(dist, 0.55 * dist.max(), 255, cv2.THRESH_BINARY)
    peaks = peaks.astype(np.uint8)
    n, markers = cv2.connectedComponents(peaks)
    if n <= 2:
        return [mask]
    markers = markers.astype(np.int32) + 1
    markers[mask == 0] = 1
    rgb = cv2.cvtColor(mask * 255, cv2.COLOR_GRAY2BGR)
    cv2.watershed(rgb, markers)
    out = []
    for label in range(2, n + 1):
        part = ((markers == label) & (mask > 0)).astype(np.uint8)
        if part.sum() > 0:
            out.append(part)
    return out or [mask]


def _centroid(part: np.ndarray) -> tuple[float, float, float]:
    m = cv2.moments(part, binaryImage=True)
    area = m["m00"]
    if area <= 0:
        return 0.0, 0.0, 0.0
    return m["m10"] / area, m["m01"] / area, area


def enforce_separation(detections, min_gap_m: float | None = None):
    """Drop same-colour detections that are physically too close together.

    A stone is 0.284 m across, so two same-colour detections nearer than that
    are not two stones. In practice one is a piece of something else -- a
    sweeper's yellow jacket reads as pairs of "yellow stones" 0.02 m apart as
    they run alongside a delivery. Keeping the more confident of each group
    costs nothing when the detections are genuinely one stone.

    Opposite colours are left alone: stones of different teams touch all the
    time, and it is their handles we localise, which stay apart.
    """
    if min_gap_m is None:
        min_gap_m = 2.0 * C.STONE_RADIUS_M
    out: list[Detection] = []
    for d in sorted(detections, key=lambda d: -d.confidence):
        for kept in out:
            if kept.color != d.color:
                continue
            gap = ((kept.x_m - d.x_m) ** 2 + (kept.y_m - d.y_m) ** 2) ** 0.5
            if gap < min_gap_m:
                break
        else:
            out.append(d)
    return out


def find_stones(panel, calib, colors=("red", "yellow")) -> list[Detection]:
    """Find every stone handle in a panel, in sheet metres."""
    arr = np.asarray(panel)
    hsv = cv2.cvtColor(arr, cv2.COLOR_BGR2HSV)

    # A handle's apparent area at the house, from this panel's own scale. The
    # per-detection expectation is refined once we know where the blob sits.
    nominal_area = np.pi * (HANDLE_RADIUS_M * calib.px_per_m) ** 2
    min_area = _MIN_AREA_FRACTION * nominal_area
    max_area = _MAX_AREA_FRACTION * nominal_area

    bands = {"red": RED_HSV, "yellow": YELLOW_HSV}
    out: list[Detection] = []

    for color in colors:
        mask = _color_mask(hsv, bands[color])
        n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
        for i in range(1, n):
            area = float(stats[i, cv2.CC_STAT_AREA])
            if area < min_area or area > max_area:
                continue
            blob = (labels == i).astype(np.uint8)
            parts = (
                _split_blob(blob, nominal_area)
                if area > _SPLIT_AREA_FRACTION * nominal_area
                else [blob]
            )
            for part in parts:
                cx, cy, part_area = _centroid(part)
                if part_area < min_area:
                    continue
                x_m, y_m = calib.to_sheet(cx, cy)
                # The adjacent sheet is visible at the edge of some panels; a
                # stone over there is not part of this game.
                if abs(x_m) > C.SIDELINE_ABS_X_M:
                    continue

                circ = _circularity(part)
                if circ < _MIN_CIRCULARITY:
                    continue
                expect = np.pi * (expected_handle_radius_m(y_m) * calib.px_per_m) ** 2
                ratio = part_area / expect if expect > 0 else 0.0
                # Penalise the log-ratio so twice-too-big and half-too-big are
                # treated alike.
                area_score = float(np.exp(-abs(np.log(max(ratio, 1e-6))) / 0.7))
                confidence = float(max(0.0, min(1.0, 0.5 * circ + 0.5 * area_score)))
                out.append(
                    Detection(
                        color=color,
                        x_m=float(x_m),
                        y_m=float(y_m),
                        x_px=float(cx),
                        y_px=float(cy),
                        area_px=float(part_area),
                        confidence=confidence,
                    )
                )
    return enforce_separation(out)
