"""Classify how well an overhead panel is lit.

Sheets finish at different times and the club kills the lights over a finished
end while other sheets play on. A dark panel must never be reported as an empty
house: that would invent a blank end that never happened. A *dim* panel is a
third real state -- Sheet 4 at t=9000 had one end's lights off and the other
merely dimmed, which left the house visible but degraded ring detection badly
(the 12-foot ring measured 124 px instead of ~280). Dim panels are still good
enough to read activity from, but must not be used for calibration.

Judged on the 95th percentile of the HSV value channel, which is robust to a
few dark players in an otherwise lit frame. Measured: dark p95 ~4, dim ~137,
lit ~185-204.
"""

from enum import Enum

import cv2
import numpy as np

_DARK_MAX_P95 = 60.0  # far above the measured 4, far below the measured 137
_LIT_MIN_P95 = 165.0  # above the measured dim 137, below the measured lit 185


class Lighting(Enum):
    DARK = "dark"  # lights out: no play, no data
    DIM = "dim"  # visible but degraded: activity only
    LIT = "lit"  # fully usable, including calibration


def brightness(panel) -> float:
    """The panel's 95th-percentile HSV value."""
    arr = np.asarray(panel)
    if arr.ndim == 3 and arr.shape[2] == 3:
        v = cv2.cvtColor(arr, cv2.COLOR_BGR2HSV)[..., 2]
    else:
        v = arr
    return float(np.percentile(v, 95))


def classify(panel) -> Lighting:
    p95 = brightness(panel)
    if p95 <= _DARK_MAX_P95:
        return Lighting.DARK
    if p95 < _LIT_MIN_P95:
        return Lighting.DIM
    return Lighting.LIT


def is_playable(panel) -> bool:
    """Whether the panel can be read for activity at all."""
    return classify(panel) is not Lighting.DARK


def is_calibratable(panel) -> bool:
    """Whether the panel is lit well enough to fit house geometry from."""
    return classify(panel) is Lighting.LIT
