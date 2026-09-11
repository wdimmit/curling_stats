"""Locate the two overhead house panels inside the composite frame.

The club composite puts two near-nadir house cameras in a horizontally centred
strip, boxed in by flat grey letterbox bars. Panel bounds differ on every sheet
(strip widths of 294-302 px were measured across the five sheets), so they must
be found per video rather than hardcoded.

The bars are found by **temporal** invariance: they are the only part of the
frame that never changes. A single-frame "flat and bright" test is not safe —
clean ice is also flat and bright, and reading a band of it as a separator once
put the bottom-panel crop inside the top panel.
"""

from dataclasses import dataclass

import numpy as np

Rect = tuple[int, int, int, int]  # x, y, w, h

# A bar must not move over time. Judged at the 99th percentile rather than the
# max: compressed video leaves a few noisy pixels in an otherwise static bar,
# and a single outlier row must not disqualify the whole column. Measured on
# real footage, bars sit at <=5 and panel interiors at >=23, so 8.0 is
# comfortably between them.
_MAX_TEMPORAL_STD = 8.0
# ...and must be flat along its own direction. Bars measure <=1.0, panel
# interiors >=13.
_MAX_SPATIAL_STD = 4.0
_MIN_BAR_PX = 3
_MIN_PANEL_PX = 100
# The strip is centred; searching only the middle avoids the wide side cameras.
_SEARCH_FRACTION = (0.30, 0.70)


class LayoutError(RuntimeError):
    """The overhead strip could not be located."""


@dataclass(frozen=True)
class PanelLayout:
    """Where the two overhead house views sit in the composite frame."""

    top: Rect
    bottom: Rect

    @property
    def panels(self) -> tuple[Rect, Rect]:
        return (self.top, self.bottom)


def _runs(flags: np.ndarray, min_len: int) -> list[tuple[int, int]]:
    """Half-open [start, stop) runs of True at least ``min_len`` long."""
    out: list[tuple[int, int]] = []
    start = None
    for i, on in enumerate(flags):
        if on and start is None:
            start = i
        elif not on and start is not None:
            if i - start >= min_len:
                out.append((start, i))
            start = None
    if start is not None and len(flags) - start >= min_len:
        out.append((start, len(flags)))
    return out


def detect_panels(frames) -> PanelLayout:
    """Find the two overhead panels from a sample of frames across the video."""
    stack = np.stack([np.asarray(f, dtype=np.float32).mean(axis=2) for f in frames])
    if stack.shape[0] < 2:
        raise LayoutError("need at least 2 frames to tell static bars from ice")

    temporal = stack.std(axis=0)  # (h, w) how much each pixel moves over time
    mean = stack.mean(axis=0)
    h, w = temporal.shape

    # --- vertical bars bounding the strip ---
    lo, hi = int(w * _SEARCH_FRACTION[0]), int(w * _SEARCH_FRACTION[1])
    col_ok = (np.percentile(temporal, 99, axis=0) <= _MAX_TEMPORAL_STD) & (
        mean.std(axis=0) <= _MAX_SPATIAL_STD
    )
    col_runs = [r for r in _runs(col_ok, _MIN_BAR_PX) if lo <= r[0] <= hi]
    if len(col_runs) < 2:
        raise LayoutError(f"expected 2 vertical bars around the strip, found {len(col_runs)}")
    x0, x1 = col_runs[0][1], col_runs[-1][0]
    if x1 - x0 < _MIN_PANEL_PX:
        raise LayoutError("overhead strip is implausibly narrow")

    # --- horizontal bars splitting the strip, judged only within the strip ---
    strip_temporal = temporal[:, x0:x1]
    strip_mean = mean[:, x0:x1]
    row_ok = (np.percentile(strip_temporal, 99, axis=1) <= _MAX_TEMPORAL_STD) & (
        strip_mean.std(axis=1) <= _MAX_SPATIAL_STD
    )
    row_runs = _runs(row_ok, _MIN_BAR_PX)
    if len(row_runs) < 3:
        raise LayoutError(
            f"expected 3 horizontal bars (top, middle, bottom), found {len(row_runs)}"
        )

    panels = [
        (a[1], b[0])
        for a, b in zip(row_runs, row_runs[1:])
        if b[0] - a[1] >= _MIN_PANEL_PX
    ]
    if len(panels) != 2:
        raise LayoutError(f"expected exactly 2 overhead panels, found {len(panels)}")

    (t0, t1), (b0, b1) = panels
    width = x1 - x0
    return PanelLayout(top=(x0, t0, width, t1 - t0), bottom=(x0, b0, width, b1 - b0))
