"""Where an overhead panel's sidelines sit, found in their paint.

Toward the hog line an overhead panel sees past the sheet's sidelines, onto
the next sheet -- where stones sit and people walk -- and a stone there must
not count as one on this sheet. The metre test the detectors apply
(``|x| > SIDELINE_ABS_X_M``) cannot see that: the panels' lateral scale
collapses toward their far edge (``geometry/calibrate.py`` fits one
``px_per_m`` from the rings), so there the paint itself reads as 1.1-1.5 m
from the centre line. 10/19 Monday sheet 4: a rock on sheet 3, beyond the
painted line in the corner of the top panel, read as a yellow at x = -1.7
through the 7 pm to 9 pm changeover, kept the sheet from ever sitting empty
long enough to end the game, and the night's two games were charted as one
of fourteen ends.

So the line is found where its paint is, in the same calibration median the
rings and the hog line are read from: navy, in the panel's outer bands,
fitted as a quadratic in panel row because these lenses bow it. Where the
next sheet's line shows beside it, this sheet's is the one nearer the centre.

Every line runs off the panel's far edge, where the view is widest, and a
row with no paint in it is a row whose line is out of the frame -- so a
stone is beyond a line only from the first row its paint was seen in out to
that far edge, and nowhere nearer the house.

Not in ``geometry/calibrate.py``, and applied to detections after the
detection cache (``detect/sequence.py``, ``game/profile.count_frame``): the
cache hashes calibrate.py, and every stone it holds still stands.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from curling_score.geometry.calibrate import PanelCalib

# The paint, in BGR -- what ``ingest/frames`` decodes to. On the five sheets'
# panels of 10/19 it reads about (65, 22, 26).
BLUENESS_MIN = 18         # b - max(g, r)
DARK_MAX = 120            # mean of b, g and r
# Paint is looked for only this far from the centre line, in the panel's
# apparent metres: past the house's navy 4-foot ring (0.61 m) and the centre
# strip. Real sidelines read 1.14-2.13 m on the ten panels of 10/19.
BAND_MIN_X_M = 1.0
# A line is a curve through at least this many rows of paint, every point
# within INLIER_PX of it. The ten panels' lines had 94-461 rows at a 0.33-0.78
# px scatter; a sponsor's lettering in the band is a few dozen rows, off any
# one curve.
MIN_ROWS = 60
INLIER_PX = 2.0
MAX_SCATTER_PX = 1.5
# The paint must come within this many rows of the panel's far edge -- every
# line seen did, and nothing else in the band runs off it.
FAR_EDGE_ROWS = 60
# A line reaches from the far edge as far as its paint runs on without a gap
# longer than this. Real lines broke for up to 18 rows; a stray pixel of navy
# further on, near the house, lay on one curve that had come back into the
# frame there, 1.2 m from the centre line (10/19 sheet 2, bottom panel).
MAX_GAP_ROWS = 25
TRIALS = 400
SEED = 0


@dataclass(frozen=True)
class SideLine:
    """One painted sideline, ``col = c0*row**2 + c1*row + c2`` in panel pixels.

    ``side`` is -1 for the panel's left edge, +1 for its right. A stone is
    beyond it on rows from ``rows[0]`` to ``rows[1]`` (infinite at the far
    edge) when its centre lies past the line's centre, outward.
    """

    coef: tuple[float, float, float]
    side: int
    rows: tuple[float, float]
    support: int
    scatter_px: float

    def col_at(self, row: float) -> float:
        return float(np.polyval(self.coef, row))

    def beyond(self, x_px: float, y_px: float) -> bool:
        if not self.rows[0] <= y_px <= self.rows[1]:
            return False
        return (x_px - self.col_at(y_px)) * self.side > 0.0

    def to_json(self) -> dict:
        return {"col_coef": [round(float(c), 8) for c in self.coef],
                "side": "left" if self.side < 0 else "right",
                "rows": [None if math.isinf(r) else round(float(r), 1) for r in self.rows],
                "support": self.support,
                "scatter_px": round(float(self.scatter_px), 3)}


@dataclass(frozen=True)
class Sidelines:
    """A panel's two sidelines, either of which may not be in view."""

    left: SideLine | None
    right: SideLine | None
    # Why a side has no line, for the run's log.
    missing: tuple[str, ...] = ()

    def beyond(self, x_px: float, y_px: float) -> bool:
        return any(line is not None and line.beyond(x_px, y_px)
                   for line in (self.left, self.right))

    def to_json(self) -> dict:
        return {"left": None if self.left is None else self.left.to_json(),
                "right": None if self.right is None else self.right.to_json(),
                "missing": list(self.missing)}


def navy(plate) -> np.ndarray:
    """Where ``plate`` (BGR) shows the sidelines' navy paint."""
    img = np.asarray(plate, dtype=np.int16)
    b, g, r = img[..., 0], img[..., 1], img[..., 2]
    return ((b - np.maximum(g, r)) >= BLUENESS_MIN) & (img.mean(axis=2) <= DARK_MAX)


def _run_centres(cols) -> list[float]:
    """The centre of each run of consecutive columns in sorted ``cols``."""
    out, start = [], None
    for i, c in enumerate(cols):
        if start is None:
            start = c
        if i + 1 == len(cols) or cols[i + 1] != c + 1:
            out.append((start + c) / 2.0)
            start = None
    return out


def _curves(points, rng) -> list[tuple[np.ndarray, np.ndarray]]:
    """Quadratic curves col(row) through ``points`` -- (row, col), any number
    to a row -- each with MIN_ROWS rows on it: ``(coef, rows on it)``,
    strongest first."""
    pts = np.asarray(points, dtype=float).reshape(-1, 2)
    out = []
    while len(pts) >= MIN_ROWS:
        best, best_rows = None, 0
        for _ in range(TRIALS):
            pick = pts[rng.choice(len(pts), 3, replace=False)]
            if len(set(pick[:, 0])) < 3 or np.ptp(pick[:, 0]) < MIN_ROWS / 2:
                continue
            coef = np.polyfit(pick[:, 0], pick[:, 1], 2)
            on = np.abs(np.polyval(coef, pts[:, 0]) - pts[:, 1]) <= INLIER_PX
            n = len(np.unique(pts[on, 0]))
            if n > best_rows:
                best, best_rows = coef, n
        if best is None or best_rows < MIN_ROWS:
            break
        for _ in range(3):
            on = np.abs(np.polyval(best, pts[:, 0]) - pts[:, 1]) <= INLIER_PX
            best = np.polyfit(pts[on, 0], pts[on, 1], 2)
        on = np.abs(np.polyval(best, pts[:, 0]) - pts[:, 1]) <= INLIER_PX
        out.append((best, pts[on]))
        pts = pts[~on]
    return out


def _find_side(paint, calib: PanelCalib, side: int, rng):
    """This panel's line on one side, or ``(None, why)``."""
    h, w = paint.shape
    name = "left" if side < 0 else "right"
    # Apparent x is the column's offset from the ring centre over px_per_m.
    cx, reach = calib.center_px[0], BAND_MIN_X_M * calib.px_per_m
    points = []
    for row in range(h):
        cols = np.nonzero(paint[row])[0]
        points.extend((row, c) for c in _run_centres(cols[(cols - cx) * side >= reach]))
    far_row = 0.0 if calib.flipped else float(h - 1)
    lines = []
    for coef, on in _curves(points, rng):
        rows = _longest_run(np.unique(on[:, 0]))
        if len(rows) < MIN_ROWS:
            continue
        kept = on[np.isin(on[:, 0], rows)]
        scatter = float(np.std(np.polyval(coef, kept[:, 0]) - kept[:, 1]))
        near_far = abs(far_row - (rows.min() if calib.flipped else rows.max()))
        # Nowhere it excludes may the line stand nearer the centre than the
        # band its paint was looked for in.
        span_rows = np.arange(0.0, rows.max() + 1) if calib.flipped else np.arange(rows.min(), h)
        clear = np.all((np.polyval(coef, span_rows) - cx) * side >= reach)
        if scatter <= MAX_SCATTER_PX and near_far <= FAR_EDGE_ROWS and clear:
            lines.append((coef, rows, scatter))
    if not lines:
        return None, f"{name}: no line of navy paint runs to the far edge"
    # Where the next sheet's line shows too, this sheet's is the inner one,
    # compared at the far edge, where both are in view.
    coef, rows, scatter = min(lines, key=lambda l: abs(np.polyval(l[0], far_row) - cx))
    span = (-math.inf, float(rows.max())) if calib.flipped else (float(rows.min()), math.inf)
    return SideLine(coef=tuple(float(c) for c in coef), side=side, rows=span,
                    support=len(rows), scatter_px=scatter), None


def _longest_run(rows) -> np.ndarray:
    """The longest stretch of ``rows`` (sorted) with no gap longer than
    MAX_GAP_ROWS: the paint itself, without the odd pixel elsewhere that
    happens to lie on its curve."""
    if not len(rows):
        return np.asarray(rows, dtype=float)
    breaks = np.nonzero(np.diff(rows) > MAX_GAP_ROWS)[0] + 1
    return max(np.split(np.asarray(rows, dtype=float), breaks), key=len)


def find_sidelines(plate, calib: PanelCalib) -> Sidelines:
    """The sidelines in one panel's calibration median, a BGR ``(h, w, 3)``
    array. A side whose paint cannot be found has no line -- often a panel
    that does not reach its sideline at all -- and excludes nothing."""
    paint = navy(plate)
    rng = np.random.default_rng(SEED)
    left, why_left = _find_side(paint, calib, -1, rng)
    right, why_right = _find_side(paint, calib, +1, rng)
    return Sidelines(left=left, right=right,
                     missing=tuple(w for w in (why_left, why_right) if w))
