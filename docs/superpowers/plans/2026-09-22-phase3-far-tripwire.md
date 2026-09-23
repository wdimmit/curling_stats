# Phase 3: the Far Hog Line Placed from the Paint -- Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Time the destination hog-line crossing against each overhead panel's own painted line instead of one global constant, with a short back-extrapolation for stones first seen past it.

**Architecture:** A new `geometry/hogpaint.py` finds the red hog line in each panel's calibration median; `PanelSetup` carries it. A new `game/fartime.py` (mirroring `game/hogtime.py`) attaches a `split.FarCrossing` to every shot. `split.long_split` consumes it through required keyword arguments, and both of its callers (`timeline.build_end`, `classify.classify_shot`) pass it from the shot. The global tripwire `split.HOG_APPARENT_Y_M` is deleted.

**Tech Stack:** Python 3.12, numpy, OpenCV (scripts and fixtures only), pytest.

**Spec:** `docs/superpowers/specs/2026-09-22-phase3-far-tripwire-design.md`

## Global Constraints

- `geometry/calibrate.py` must NOT be edited. `detect/cache.py::_SOURCE_MODULES` hashes it; any change invalidates every cached detection.
- Calibration frames and panel medians are **BGR**. Red is channel index 2.
- `hogpaint` constants, verbatim: `BAND_ROWS = 140`, `REDNESS_MIN = 25`, `RED_MIN = 80`, `OUTLIER_PX = 2.5`, `MIN_COLUMNS = 80`, `MAX_SCATTER_PX = 1.5`, `PLAUSIBLE_Y = (4.0, 5.0)`, `LEADING_EDGE_OFFSET_U = 0.080`.
- `split.FAR_REACH_MAX_U = 0.20`; `split._FAR_FIT_POINTS = 4` (unchanged); `split.BASELINE_M = C.TEE_TO_TEE_M - C.TEE_TO_HOGLINE_M - (C.TEE_TO_HOGLINE_M + C.HOGLINE_WIDTH_M)` = 21.843 m.
- `split.SPEED_TOLERANCE = 1.4` and `split.CROSS_CHECK_S = 0.25` and their comments are unchanged.
- After Task 3, no file in `src/` or `tests/` may reference `HOG_APPARENT_Y_M`, `FAR_EXTRAPOLATION_MAX_U` or `split.hog_crossing`. After Task 4, nor may any tracked file in `scripts/`.
- `split.long_split(delivery, *, t_hog, v_hog, far)`: all three keywords REQUIRED, no defaults.
- Run tests as `PYTHONPATH=src .venv/bin/python -m pytest <paths> -p no:cacheprovider -rf`. The project's `addopts` already has `-q`; do not add another. **Never run the whole `tests/` directory**: it OOM-kills this laptop. Run the files each task names.
- Commit messages end with exactly: `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`
- Never `git add -A`. The working tree holds unrelated untracked files (`labelset_*`, `ds1*.json`, some scripts); stage only the paths each task names.

## Review Focus

1. **A setup pickled before this change** loads with `hog_line=None` and no error. Every replay script reading such a pickle would silently time no far crossings. Expected: `replay_end.setups_for` treats it as stale and recomputes (Task 4, test).
2. **`analyze._proxy_setups` rebuilds `PanelSetup` from `rect` and `calib` only.** If it drops `hog_line`, every proxy-read panel silently has no line. Expected: the line survives translation (Task 2, test).
3. **A flipped (bottom) panel read off the centre line.** `to_pixels` negates x on a flipped panel, so a sign slip would read the bow on the wrong side. Expected: `outer_edge_y(x)` equals the fitted row mapped through `to_sheet`, for x != 0 on a flipped panel (Task 1, test).
4. **An extrapolated far crossing has no far-line speed** (`speed_at_line` needs a bracket), so the panel-speed pairing check cannot run. Expected: the split still publishes, marked by `far_reach > 0`, rather than being refused or crashing (Task 3, test).
5. **A panel whose paint is not found must not stop calibration.** Expected: `calibrate_panel` returns a setup with `hog_line=None` and a reason naming the panel (Task 2, test), and `fartime` then attaches `t=None` for that end (Task 3, test).

---

### Task 1: Find the hog line in the paint

**Files:**
- Create: `src/curling_score/geometry/hogpaint.py`
- Create: `tests/synth_hogline.py`
- Create: `tests/test_hogpaint.py`

**Interfaces:**
- Consumes: `curling_score.geometry.calibrate.PanelCalib(center_px: tuple[float, float], px_per_m: float, edge_erosion_px: float, residual_m: float, flipped: bool)` with `.to_sheet(x_px, y_px) -> (x_m, y_m)` and `.to_pixels(x_m, y_m) -> (x_px, y_px)`.
- Produces:
  - `hogpaint.HogLine(coef: tuple[float, float, float], calib: PanelCalib, columns: int, scatter_px: float)` with `.outer_edge_y(x_m) -> float`, `.y_at(x_m) -> float`, `.to_json() -> dict`.
  - `hogpaint.find_hog_line(plate, calib) -> HogLine`, raising `hogpaint.HogPaintError`.
  - `hogpaint.LEADING_EDGE_OFFSET_U = 0.080`.
  - `tests.synth_hogline.TOP`, `BOTTOM` (`PanelCalib`s), `plate_with_line(...)`, `line_at(y_tripwire, calib=TOP) -> HogLine`.

- [ ] **Step 1: Write the synthetic helpers**

Create `tests/synth_hogline.py`:

```python
"""Synthetic overhead panels with a painted hog line, for hogpaint and split tests.

Geometry follows the club's real panels: a top panel 534 rows tall with its tee
at row 161, and a flipped bottom panel whose tee sits at row 345. At 80 and 75
px per panel unit, a painted line at rows 516 and 15 sits at y ~= 4.44, where
real panels put theirs.
"""

import numpy as np

from curling_score.geometry import hogpaint
from curling_score.geometry.calibrate import PanelCalib

TOP = PanelCalib(center_px=(150.0, 161.0), px_per_m=80.0, edge_erosion_px=0.0,
                 residual_m=0.0, flipped=False)
BOTTOM = PanelCalib(center_px=(150.0, 345.0), px_per_m=75.0, edge_erosion_px=0.0,
                    residual_m=0.0, flipped=True)

RED_BGR = (60, 60, 200)
BLUE_BGR = (200, 60, 60)


def plate_with_line(h=534, w=300, flipped=False, outer_row=516.0, bow=0.0004,
                    colour=RED_BGR, thick=4, cols=None, jitter=None):
    """A BGR ice plate with a painted line whose OUTER edge is at
    ``outer_row + bow * (col - w/2)**2 (+ jitter(col))``.

    The paint extends from the outer edge toward the house: to smaller rows on
    a top panel, larger rows on a flipped one.
    """
    img = np.full((h, w, 3), 225, np.uint8)
    for c in (range(w) if cols is None else cols):
        o = outer_row + bow * (c - w / 2) ** 2 + (0.0 if jitter is None else jitter(c))
        o = int(round(o))
        rows = range(o, o + thick) if flipped else range(o - thick + 1, o + 1)
        for r in rows:
            if 0 <= r < h:
                img[r, c] = colour
    return img


def line_at(y_tripwire, calib=TOP):
    """A flat HogLine whose tripwire ``y_at(x)`` is ``y_tripwire`` everywhere."""
    y_outer = y_tripwire - hogpaint.LEADING_EDGE_OFFSET_U
    row = calib.to_pixels(0.0, y_outer)[1]
    return hogpaint.HogLine(coef=(0.0, 0.0, float(row)), calib=calib,
                            columns=300, scatter_px=0.0)
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_hogpaint.py`:

```python
"""The hog line, found where its paint is in each overhead panel."""

import numpy as np
import pytest

from curling_score.geometry import hogpaint
from curling_score.geometry.calibrate import PanelCalib
from tests.synth_hogline import BLUE_BGR, BOTTOM, TOP, plate_with_line


def expected_outer(c, outer_row, bow=0.0004, w=300):
    return outer_row + bow * (c - w / 2) ** 2


class TestFindingThePaint:
    def test_finds_a_bowed_line_on_a_top_panel(self):
        line = hogpaint.find_hog_line(plate_with_line(), TOP)
        for c in (20, 150, 280):
            assert np.polyval(line.coef, c) == pytest.approx(expected_outer(c, 516.0), abs=0.5)

    def test_finds_it_on_a_flipped_bottom_panel(self):
        plate = plate_with_line(h=516, flipped=True, outer_row=15.0)
        line = hogpaint.find_hog_line(plate, BOTTOM)
        for c in (20, 150, 280):
            assert np.polyval(line.coef, c) == pytest.approx(expected_outer(c, 15.0), abs=0.5)

    def test_a_red_blob_between_the_paint_and_the_frame_edge_does_not_pull_the_fit(self):
        plate = plate_with_line()
        yy, xx = np.mgrid[0:534, 0:300]
        plate[(yy - 526) ** 2 + (xx - 100) ** 2 <= 7 ** 2] = (40, 40, 210)
        line = hogpaint.find_hog_line(plate, TOP)
        assert np.polyval(line.coef, 100) == pytest.approx(expected_outer(100, 516.0), abs=0.5)

    def test_it_records_how_much_paint_it_saw(self):
        line = hogpaint.find_hog_line(plate_with_line(), TOP)
        assert line.columns >= 290
        assert line.scatter_px < 0.6


class TestRefusingPaintItCannotTrust:
    def test_too_few_columns(self):
        with pytest.raises(hogpaint.HogPaintError, match="columns"):
            hogpaint.find_hog_line(plate_with_line(cols=range(0, 50)), TOP)

    def test_a_line_outside_the_far_band_is_not_found(self):
        with pytest.raises(hogpaint.HogPaintError, match="columns"):
            hogpaint.find_hog_line(plate_with_line(outer_row=300.0), TOP)

    def test_a_ragged_edge_is_refused(self):
        with pytest.raises(hogpaint.HogPaintError, match="scatter"):
            hogpaint.find_hog_line(plate_with_line(jitter=lambda c: 2 if c % 2 else -2), TOP)

    def test_paint_at_an_implausible_position_is_refused(self):
        coarse = PanelCalib(center_px=(150.0, 161.0), px_per_m=100.0,
                            edge_erosion_px=0.0, residual_m=0.0, flipped=False)
        with pytest.raises(hogpaint.HogPaintError, match="outside"):
            hogpaint.find_hog_line(plate_with_line(), coarse)


class TestItReadsBgr:
    """Calibration frames arrive BGR. Read as RGB, a first probe of this found
    the line in 8 of 298 columns -- this pins the channel order."""

    def test_red_paint_in_a_bgr_frame_is_found(self):
        assert hogpaint.find_hog_line(plate_with_line(), TOP).columns >= 290

    def test_blue_paint_is_not_a_hog_line(self):
        with pytest.raises(hogpaint.HogPaintError, match="columns"):
            hogpaint.find_hog_line(plate_with_line(colour=BLUE_BGR), TOP)


class TestTheTripwire:
    def test_y_at_adds_the_leading_edge_offset(self):
        line = hogpaint.find_hog_line(plate_with_line(), TOP)
        assert line.y_at(0.0) == pytest.approx(line.outer_edge_y(0.0) + hogpaint.LEADING_EDGE_OFFSET_U)
        assert hogpaint.LEADING_EDGE_OFFSET_U == 0.080

    def test_it_follows_the_bow_off_the_centre_line(self):
        line = hogpaint.find_hog_line(plate_with_line(), TOP)
        assert line.outer_edge_y(1.0) > line.outer_edge_y(0.0) + 0.02

    @pytest.mark.parametrize("calib,plate", [
        (TOP, plate_with_line()),
        (BOTTOM, plate_with_line(h=516, flipped=True, outer_row=15.0)),
    ], ids=["top", "flipped-bottom"])
    def test_an_off_centre_reading_is_the_fitted_row_at_that_column(self, calib, plate):
        """A flipped panel negates x in to_pixels; reading the bow on the wrong
        side would mistime every curled stone on that panel."""
        line = hogpaint.find_hog_line(plate, calib)
        for x_m in (-1.2, 0.7):
            col, _ = calib.to_pixels(x_m, 0.0)
            want = calib.to_sheet(col, float(np.polyval(line.coef, col)))[1]
            assert line.outer_edge_y(x_m) == pytest.approx(want, abs=1e-9)

    def test_it_publishes_what_it_found(self):
        got = hogpaint.find_hog_line(plate_with_line(), TOP).to_json()
        assert set(got) == {"outer_edge_row_coef", "columns", "scatter_px", "offset_u"}
        assert got["offset_u"] == hogpaint.LEADING_EDGE_OFFSET_U
```

- [ ] **Step 3: Run them to verify they fail**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_hogpaint.py -p no:cacheprovider -rf`
Expected: collection error, `ModuleNotFoundError: No module named 'curling_score.geometry.hogpaint'`.

- [ ] **Step 4: Write the module**

Create `src/curling_score/geometry/hogpaint.py`:

```python
"""Where the hog line's paint sits in an overhead panel, found in the paint.

The panels cannot say where a hog line is in metres -- ``geometry/calibrate.py``
fits one ``px_per_m`` from the house rings, and by the top of the frame the real
scale has fallen to about a third of it -- so a crossing is timed against where
the paint sits in the panel's own coordinates. That used to be one number for
every panel of every video (``split.HOG_APPARENT_Y_M = 4.441``), and hand marks
read in the overhead panels put the paint anywhere from 4.39 to 4.75: top panels
fired 0.17-0.57 s late and bottom panels 0.06-0.17 s early, on all three games
measured (``datasets/hogmarks/receiving-controls.json``).

So the line is found where it is: the red paint near the panel's far edge, in
the same calibration median the rings are fitted on, as the paint's OUTER edge
-- the side a stone arrives from -- fitted as a quadratic in panel column,
because these lenses bow it. See
``docs/superpowers/specs/2026-09-22-phase3-far-tripwire-design.md``.

Not in ``geometry/calibrate.py`` on purpose: the detection cache hashes that
file, and editing it would send every cached video back through the GPU.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from curling_score.geometry.calibrate import PanelCalib

# The paint is looked for only this close to the panel's far edge. Over the
# six panels of three games its outer edge sat 9-88 rows inside it.
BAND_ROWS = 140
# Red, in BGR -- which is what ``ingest/frames`` decodes to. A first probe read
# the frames as RGB and found the line in 8 of 298 columns.
REDNESS_MIN = 25          # r - max(g, b)
RED_MIN = 80
# Columns further than this from a first quadratic are dropped before the
# refit: a parked stone or a player in the median sits well off the curve.
OUTLIER_PX = 2.5
# Below these the line is not trusted. Good panels gave 127-212 columns, a
# 0.52-0.65 px scatter, and an outer edge at 4.32-4.66 on the centre line.
MIN_COLUMNS = 80
MAX_SCATTER_PX = 1.5
PLAUSIBLE_Y = (4.0, 5.0)
# From the paint's outer edge to the tracked centre of a stone whose leading
# edge is on the paint -- the moment a split is timed at. Measured against 51
# hand marks over six panels: panel medians 0.063-0.100, median 0.080.
LEADING_EDGE_OFFSET_U = 0.080


class HogPaintError(RuntimeError):
    """The hog line's paint could not be found well enough to time against."""


@dataclass(frozen=True)
class HogLine:
    """One panel's hog line: the paint's outer edge, ``row = c0*col**2 + c1*col + c2``."""

    coef: tuple[float, float, float]
    calib: PanelCalib
    columns: int
    scatter_px: float

    def outer_edge_y(self, x_m: float) -> float:
        """The paint's outer edge in panel coordinates, at lateral position ``x_m``."""
        col, _row = self.calib.to_pixels(x_m, 0.0)
        return self.calib.to_sheet(col, float(np.polyval(self.coef, col)))[1]

    def y_at(self, x_m: float) -> float:
        """The tripwire at ``x_m``: where a stone's tracked centre is when its
        leading edge first touches the paint."""
        return self.outer_edge_y(x_m) + LEADING_EDGE_OFFSET_U

    def to_json(self) -> dict:
        return {"outer_edge_row_coef": [round(float(c), 8) for c in self.coef],
                "columns": self.columns,
                "scatter_px": round(float(self.scatter_px), 3),
                "offset_u": LEADING_EDGE_OFFSET_U}


def find_hog_line(plate, calib: PanelCalib) -> HogLine:
    """The hog line in one panel's calibration median, a BGR ``(h, w, 3)`` array.

    Raises ``HogPaintError`` with the reason when the paint cannot be trusted.
    There is deliberately no fallback: the old global tripwire was up to half a
    second wrong, and a missing split is better than a wrong one.
    """
    img = np.asarray(plate, dtype=np.float32)
    h, w = img.shape[:2]
    b, g, r = img[..., 0], img[..., 1], img[..., 2]
    red = (r - np.maximum(g, b) > REDNESS_MIN) & (r > RED_MIN)
    lo, hi = (0, min(BAND_ROWS, h)) if calib.flipped else (max(0, h - BAND_ROWS), h)
    cols, outer = [], []
    for c in range(w):
        rows = np.nonzero(red[lo:hi, c])[0]
        if len(rows) >= 2:
            cols.append(c)
            # The edge away from the house is the one nearest the frame's far edge.
            outer.append(lo + (rows.min() if calib.flipped else rows.max()))
    if len(cols) < MIN_COLUMNS:
        raise HogPaintError(
            f"red paint in only {len(cols)} of {w} columns within {BAND_ROWS} "
            f"rows of the far edge (need {MIN_COLUMNS})")
    cols = np.asarray(cols, dtype=float)
    outer = np.asarray(outer, dtype=float)
    coef = np.polyfit(cols, outer, 2)
    keep = np.abs(outer - np.polyval(coef, cols)) < OUTLIER_PX
    if int(keep.sum()) < MIN_COLUMNS:
        raise HogPaintError(
            f"only {int(keep.sum())} columns lie on one curve (need {MIN_COLUMNS})")
    coef = np.polyfit(cols[keep], outer[keep], 2)
    scatter = float(np.std(outer[keep] - np.polyval(coef, cols[keep])))
    if scatter > MAX_SCATTER_PX:
        raise HogPaintError(
            f"the paint's edge scatters {scatter:.2f} px about its fit "
            f"(limit {MAX_SCATTER_PX})")
    line = HogLine(coef=tuple(float(c) for c in coef), calib=calib,
                   columns=int(keep.sum()), scatter_px=scatter)
    centre = line.outer_edge_y(0.0)
    if not PLAUSIBLE_Y[0] <= centre <= PLAUSIBLE_Y[1]:
        raise HogPaintError(
            f"paint found at y={centre:.3f} on the centre line, outside {PLAUSIBLE_Y}")
    return line
```

- [ ] **Step 5: Run the tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_hogpaint.py -p no:cacheprovider -rf`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/curling_score/geometry/hogpaint.py tests/synth_hogline.py tests/test_hogpaint.py
git commit -m "hogpaint: find each panel's hog line in its paint

The destination tripwire has been one number for every panel of every
video, and hand marks put the paint at 4.39-4.75 instead of 4.441: top
panels fire up to 0.57 s late, bottom panels up to 0.17 s early. This
finds the red line itself in a panel's calibration median, as its outer
edge fitted as a quadratic in column, and refuses it rather than guess
when the paint is thin, ragged or in the wrong place.

Kept out of geometry/calibrate.py: the detection cache hashes that file.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Each panel carries its hog line

**Files:**
- Modify: `src/curling_score/game/profile.py` (imports, `PanelSetup`, `calibrate_panel`)
- Modify: `src/curling_score/analyze.py` (`_proxy_setups`, the calibration block)
- Create: `tests/test_panel_hog_line.py`

**Interfaces:**
- Consumes: `hogpaint.find_hog_line`, `hogpaint.HogPaintError`, `HogLine.to_json()` from Task 1; `tests.synth_hogline.TOP`, `plate_with_line`.
- Produces:
  - `profile.PanelSetup.hog_line: HogLine | None = None` and `profile.PanelSetup.hog_line_error: str | None = None`.
  - `profile.panel_median(frames, rect, name="panel") -> np.ndarray` (uint8 BGR median).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_panel_hog_line.py`:

```python
"""Each panel's calibration also finds its hog line, and nothing downstream drops it."""

import numpy as np

from curling_score import analyze
from curling_score.game import profile
from tests.synth_hogline import TOP, plate_with_line


def _calibrate(monkeypatch, plate):
    monkeypatch.setattr(profile.lighting, "is_calibratable", lambda p: True)
    monkeypatch.setattr(profile.calibrate, "solve", lambda median, side: TOP)
    frames = [plate.copy() for _ in range(3)]
    return profile.calibrate_panel(frames, (0, 0, plate.shape[1], plate.shape[0]),
                                   "bottom", name="top")


def test_a_panel_whose_paint_is_found_carries_its_line(monkeypatch):
    setup = _calibrate(monkeypatch, plate_with_line())
    assert setup.hog_line is not None
    assert setup.hog_line_error is None
    assert setup.hog_line.calib == TOP


def test_a_panel_whose_paint_is_not_found_says_why_and_still_calibrates(monkeypatch):
    setup = _calibrate(monkeypatch, np.full((534, 300, 3), 225, np.uint8))
    assert setup.calib == TOP
    assert setup.hog_line is None
    assert setup.hog_line_error.startswith("top panel:")
    assert "columns" in setup.hog_line_error


def test_a_setup_made_without_one_defaults_to_none():
    """A PanelSetup pickled before hog lines existed loads with these defaults."""
    s = profile.PanelSetup(rect=(0, 0, 300, 534), calib=TOP)
    assert s.hog_line is None and s.hog_line_error is None


def test_moving_a_panel_into_proxy_coordinates_keeps_its_line(monkeypatch):
    """analyze._proxy_setups rebuilds each PanelSetup; dropping the line there
    would silently leave every proxy-read panel with no far tripwire."""
    setup = _calibrate(monkeypatch, plate_with_line())
    moved = analyze._proxy_setups({"top": setup}, (0, 0, 300, 1060))["top"]
    assert moved.hog_line is setup.hog_line
    assert moved.hog_line_error == setup.hog_line_error


def test_panel_median_is_the_median_of_the_lit_frames(monkeypatch):
    monkeypatch.setattr(profile.lighting, "is_calibratable", lambda p: True)
    a = np.zeros((4, 4, 3), np.uint8)
    b = np.full((4, 4, 3), 100, np.uint8)
    got = profile.panel_median([a, b, b], (0, 0, 4, 4))
    assert got.dtype == np.uint8 and int(got[0, 0, 0]) == 100
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_panel_hog_line.py -p no:cacheprovider -rf`
Expected: FAIL — `PanelSetup` has no `hog_line`, and `profile` has no `panel_median`.

- [ ] **Step 3: Carry the line on `PanelSetup`**

In `src/curling_score/game/profile.py`, change the import line

```python
from curling_score.geometry import calibrate, layout, lighting
```

to

```python
from curling_score.geometry import calibrate, hogpaint, layout, lighting
```

and in `class PanelSetup`, directly after the `calib: calibrate.PanelCalib` field, add:

```python
    # The panel's own hog line, found in its paint (``geometry/hogpaint.py``),
    # or None with the reason in ``hog_line_error``. The destination crossing,
    # and the throwing panel's cross-check, are timed against it; there is no
    # fallback. Defaulted so a setup pickled before this existed still loads --
    # though it then has no line, which ``scripts/replay_end.setups_for``
    # treats as stale.
    hog_line: hogpaint.HogLine | None = None
    hog_line_error: str | None = None
```

- [ ] **Step 4: Find it where the median is built**

In `src/curling_score/game/profile.py`, replace the body of `calibrate_panel` after its docstring. The current body is:

```python
    import numpy as np

    x, y, w, h = rect
    lit = [f[y : y + h, x : x + w] for f in frames]
    lit = [p for p in lit if lighting.is_calibratable(p)]
    if not lit:
        raise calibrate.CalibrationError(f"{name} panel: no well-lit frames")
    median = np.median(np.stack(lit), axis=0).astype("uint8")
    return PanelSetup(rect=rect, calib=calibrate.solve(median, delivery_side))
```

Replace it with:

```python
    median = panel_median(frames, rect, name)
    calib = calibrate.solve(median, delivery_side)
    # The same median the rings were read from shows the hog line's paint. A
    # panel whose paint cannot be trusted keeps its calibration and says why;
    # it simply times no far crossings.
    try:
        line, err = hogpaint.find_hog_line(median, calib), None
    except hogpaint.HogPaintError as exc:
        line, err = None, f"{name} panel: {exc}"
    return PanelSetup(rect=rect, calib=calib, hog_line=line, hog_line_error=err)
```

and add this function immediately above `def calibrate_panel`:

```python
def panel_median(frames, rect, name: str = "panel"):
    """The idle house's median over well-lit frames, BGR, cropped to ``rect``.

    What the rings and the hog line's paint are both read from. Split out of
    ``calibrate_panel`` so a test fixture can be built from exactly the image
    the pipeline sees.
    """
    import numpy as np

    x, y, w, h = rect
    lit = [f[y : y + h, x : x + w] for f in frames]
    lit = [p for p in lit if lighting.is_calibratable(p)]
    if not lit:
        raise calibrate.CalibrationError(f"{name} panel: no well-lit frames")
    return np.median(np.stack(lit), axis=0).astype("uint8")
```

- [ ] **Step 5: Keep it through proxy translation, and publish it**

In `src/curling_score/analyze.py`, in `_proxy_setups`, replace

```python
        name: profile.PanelSetup(
            rect=proxy.translate(s.rect, strip), calib=s.calib
        )
```

with

```python
        name: profile.PanelSetup(
            rect=proxy.translate(s.rect, strip), calib=s.calib,
            # The line is in panel pixels, so moving the panel does not move it.
            hog_line=s.hog_line, hog_line_error=s.hog_line_error,
        )
```

In the calibration block near the end of `analyze.py` (the dict built per panel with `"rect"`, `"px_per_m"`, `"center_px"`, `"residual_m"`, `"flipped"`), add two entries after `"flipped": s.calib.flipped,`:

```python
                "hog_line": None if s.hog_line is None else s.hog_line.to_json(),
                "hog_line_error": s.hog_line_error,
```

- [ ] **Step 6: Run the tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_panel_hog_line.py tests/test_hogpaint.py tests/test_setups.py tests/test_calibrate.py tests/test_analyze_write.py -p no:cacheprovider -rf`
Expected: all pass. If `tests/test_analyze_write.py` asserts the calibration block's exact keys, extend its expectation with `hog_line` and `hog_line_error` rather than dropping the new keys.

- [ ] **Step 7: Commit**

```bash
git add src/curling_score/game/profile.py src/curling_score/analyze.py tests/test_panel_hog_line.py
git commit -m "profile: every panel carries the hog line found in its paint

calibrate_panel already builds the median image the rings are read from;
it now finds the hog line there too, or records why not. The line rides
on PanelSetup, which the detection cache does not hash, and survives
analyze._proxy_setups, which rebuilds each setup and would otherwise drop
it. Published per panel in the timeline's calibration block.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: The far crossing rides on the shot, timed at the paint

This task is one atomic interface change: `split.long_split`'s signature, both of its callers, and the tests that drive them move together, or the suite is red in between.

**Files:**
- Modify: `src/curling_score/game/split.py`
- Create: `src/curling_score/game/fartime.py`
- Modify: `src/curling_score/game/shots.py` (the `Shot` dataclass)
- Modify: `src/curling_score/timeline.py`
- Modify: `src/curling_score/game/classify.py`
- Modify: `src/curling_score/analyze.py` (the per-end block and its imports)
- Modify: `src/curling_score/version.py`
- Modify: `src/curling_score/game/hogtime.py` (the `crossing` docstring only)
- Replace: `tests/test_split.py`
- Create: `tests/test_fartime.py`
- Modify: `tests/test_classify.py` (the `paired` helper and `class TestOutOfPlay`)
- Modify: `tests/test_timeline.py` (`TestTimingFields._shot` and one docstring)

**Interfaces:**
- Consumes: `hogpaint.HogLine.y_at(x_m)` (Task 1); `PanelSetup.hog_line` (Task 2); `tests.synth_hogline.line_at`, `TOP`.
- Produces:
  - `split.FarCrossing(t: float | None, reach: float = 0.0, v_far: float | None = None, t_near_panel: float | None = None, v_near: float | None = None)`.
  - `split.line_crossing(track, line) -> float | None`.
  - `split.far_crossing(track, line, *, max_reach: float) -> tuple[float | None, float]`.
  - `split.speed_at_line(track, line) -> float | None`.
  - `split.FAR_REACH_MAX_U = 0.20`; `split.BASELINE_M` (21.843).
  - `split.long_split(delivery, *, t_hog, v_hog, far) -> Split | None`.
  - `fartime.time_far_crossings(shots, *, near_line, far_line) -> None`; `fartime.crossing(shot) -> FarCrossing | None`.
  - `Shot.far_crossing: object = None`.
  - `classify.classify(delivery, house_delta=None, *, t_hog=None, v_hog=None, far=None)`.

- [ ] **Step 1: Replace `tests/test_split.py`**

Replace the whole file with:

```python
"""The long split: hog line to hog line, each timed at its panel's own paint."""

import pytest

from curling_score.game import split
from curling_score.geometry import constants as C
from curling_score.geometry import hogpaint
from tests.synth_hogline import TOP, line_at

LINE = line_at(4.44)       # a flat painted line whose tripwire sits at 4.44


def climbing(t0=0.0, y0=-2.0, y1=4.8, speed=2.0, fps=5.0, x=0.05):
    """A release track climbing up-sheet at a steady speed."""
    out, t, y = [], t0, y0
    while y <= y1 + 1e-9:
        out.append((round(t, 3), x, round(y, 4)))
        y += speed / fps
        t += 1.0 / fps
    return tuple(out)


def arriving(t0=20.0, y0=4.55, y1=0.2, speed=0.8, fps=10.0, x=0.0):
    """An arrival track running down-sheet toward the tee."""
    out, t, y = [], t0, y0
    while y >= y1 - 1e-9:
        out.append((round(t, 3), x, round(y, 4)))
        y -= speed / fps
        t += 1.0 / fps
    return tuple(out)


class TestCrossingTime:
    """The scalar crossing is still what the thinking-time clock uses at the tee."""

    def test_finds_the_moment_a_descending_track_passes_a_line(self):
        tr = arriving(t0=100.0, y0=4.5, y1=0.5, speed=1.0, fps=10.0)
        assert split.crossing_time(tr, 3.4) == pytest.approx(101.1, abs=0.02)

    def test_finds_the_moment_an_ascending_track_passes_a_line(self):
        tr = climbing(t0=50.0, y0=-2.0, y1=3.6, speed=2.0, fps=5.0)
        assert split.crossing_time(tr, 0.0) == pytest.approx(51.0, abs=0.02)

    def test_interpolates_between_samples_rather_than_snapping(self):
        tr = arriving(t0=0.0, y0=4.0, y1=1.0, speed=1.0, fps=2.0)
        t = split.crossing_time(tr, 3.25)
        assert t == pytest.approx(0.75, abs=0.01)
        assert t not in [p[0] for p in tr]

    def test_a_line_the_track_never_reaches_is_not_invented(self):
        assert split.crossing_time(arriving(y0=4.0, y1=2.0), 6.401) is None

    def test_an_empty_track_crosses_nothing(self):
        assert split.crossing_time((), 3.4) is None


class TestLineCrossing:
    def test_times_a_track_across_a_flat_painted_line(self):
        tr = arriving(t0=20.0, y0=4.8, speed=1.0, fps=10.0)
        assert split.line_crossing(tr, LINE) == pytest.approx(20.36, abs=0.01)

    def test_reads_a_bowed_line_at_the_stone_s_own_position(self):
        """These lenses bow the paint, and a curled stone crosses it well off
        the centre line: the tripwire it meets is the one at its own x."""
        bowed = hogpaint.HogLine(coef=(0.0004, -0.12, 525.0), calib=TOP,
                                 columns=300, scatter_px=0.0)
        assert bowed.y_at(1.0) > bowed.y_at(0.0) + 0.02
        tr = arriving(t0=20.0, y0=4.8, speed=1.0, fps=10.0, x=1.0)
        t = split.line_crossing(tr, bowed)
        assert t == pytest.approx(20.0 + (4.8 - bowed.y_at(1.0)) / 1.0, abs=0.01)

    def test_a_track_that_never_reaches_the_line_crosses_nothing(self):
        assert split.line_crossing(arriving(y0=4.0), LINE) is None
        assert split.line_crossing((), LINE) is None
        assert split.line_crossing(None, LINE) is None


class TestFarCrossing:
    """An arrival first seen past its painted line is reached back for, a little."""

    def test_an_observed_crossing_is_not_extrapolated(self):
        t, reach = split.far_crossing(arriving(y0=4.8), LINE, max_reach=split.FAR_REACH_MAX_U)
        assert t is not None and reach == 0.0

    def test_a_track_beginning_just_past_the_line_is_reached_for(self):
        tr = arriving(t0=20.0, y0=4.34, speed=1.0, fps=10.0)
        t, reach = split.far_crossing(tr, LINE, max_reach=split.FAR_REACH_MAX_U)
        assert reach == pytest.approx(0.10, abs=1e-6)
        assert t == pytest.approx(19.90, abs=0.01)
        assert t < tr[0][0]

    def test_the_cap_is_0_20_units(self):
        assert split.FAR_REACH_MAX_U == 0.20
        inside = arriving(t0=20.0, y0=4.241, speed=1.0, fps=10.0)     # reach 0.199
        outside = arriving(t0=20.0, y0=4.239, speed=1.0, fps=10.0)    # reach 0.201
        assert split.far_crossing(inside, LINE, max_reach=0.20)[0] is not None
        assert split.far_crossing(outside, LINE, max_reach=0.20) == (None, 0.0)

    def test_too_little_track_to_fit_is_refused(self):
        tr = arriving(t0=20.0, y0=4.34, y1=4.10, speed=1.0, fps=10.0)[:3]
        assert split.far_crossing(tr, LINE, max_reach=0.20) == (None, 0.0)


class TestSpeedAtLine:
    def test_reads_the_rate_across_the_paint(self):
        assert split.speed_at_line(arriving(y0=4.8, speed=0.8), LINE) == pytest.approx(0.8, abs=0.01)

    def test_no_crossing_means_no_speed(self):
        assert split.speed_at_line(arriving(y0=4.0), LINE) is None
        assert split.speed_at_line(None, LINE) is None


class TestTheBaseline:
    def test_it_is_what_the_leading_edge_covers(self):
        """Leading edge first touching each line: the throwing line's inside
        edge, the destination line's outer edge."""
        assert split.BASELINE_M == pytest.approx(21.843, abs=1e-3)
        assert split.BASELINE_M == pytest.approx(
            C.TEE_TO_TEE_M - C.TEE_TO_HOGLINE_M - (C.TEE_TO_HOGLINE_M + C.HOGLINE_WIDTH_M))


def far(t=30.0, reach=0.0, v_far=None, t_near_panel=None, v_near=None):
    return split.FarCrossing(t=t, reach=reach, v_far=v_far,
                             t_near_panel=t_near_panel, v_near=v_near)


DELIVERY = object()


class TestLongSplit:
    def test_measures_between_the_two_crossings(self):
        s = split.long_split(DELIVERY, t_hog=10.0, v_hog=None, far=far(t=30.0))
        assert s.seconds == pytest.approx(20.0)
        assert s.t_start == 10.0 and s.t_end == 30.0
        assert s.baseline_m == pytest.approx(split.BASELINE_M)

    def test_every_input_is_required(self):
        """The long-camera merge added a required input and updated one of
        long_split's two callers; the other silently lost every draw-through.
        A forgotten argument must now fail loudly."""
        with pytest.raises(TypeError):
            split.long_split(DELIVERY, t_hog=10.0, v_hog=None)
        with pytest.raises(TypeError):
            split.long_split(DELIVERY, v_hog=None, far=far())
        with pytest.raises(TypeError):
            split.long_split(DELIVERY, t_hog=10.0, far=far())

    def test_either_end_missing_means_no_split(self):
        assert split.long_split(None, t_hog=10.0, v_hog=None, far=far()) is None
        assert split.long_split(DELIVERY, t_hog=None, v_hog=None, far=far()) is None
        assert split.long_split(DELIVERY, t_hog=10.0, v_hog=None, far=None) is None
        assert split.long_split(DELIVERY, t_hog=10.0, v_hog=None, far=far(t=None)) is None

    def test_a_backwards_split_is_refused(self):
        assert split.long_split(DELIVERY, t_hog=40.0, v_hog=None, far=far(t=30.0)) is None

    def test_a_split_records_how_far_it_reached(self):
        assert split.long_split(DELIVERY, t_hog=10.0, v_hog=None, far=far(reach=0.12)).far_reach == 0.12
        assert split.long_split(DELIVERY, t_hog=10.0, v_hog=None, far=far()).far_reach == 0.0

    def test_the_split_reports_its_own_mean_speed(self):
        s = split.long_split(DELIVERY, t_hog=10.0, v_hog=None, far=far(t=24.0))
        assert s.speed_m_s == pytest.approx(split.BASELINE_M / 14.0)


class TestThePanelCrossCheckIsRecordedNotObeyed:
    def test_a_split_records_the_panel_disagreement(self):
        s = split.long_split(DELIVERY, t_hog=10.0, v_hog=None, far=far(t_near_panel=9.95))
        assert s.panel_delta == pytest.approx(0.05)

    def test_a_large_disagreement_is_recorded_not_refused(self):
        s = split.long_split(DELIVERY, t_hog=10.0, v_hog=None,
                             far=far(t_near_panel=10.0 - 3 * split.CROSS_CHECK_S))
        assert s is not None
        assert s.panel_delta == pytest.approx(3 * split.CROSS_CHECK_S)

    def test_no_panel_reading_is_none_not_zero(self):
        assert split.long_split(DELIVERY, t_hog=10.0, v_hog=None, far=far()).panel_delta is None


class TestItRefusesAPhysicallyImpossibleSplit:
    """A stone only ever slows, so it cannot cross the far line faster than the
    near one; both are read in their own panel's units, distorted alike."""

    def test_the_tolerance_forgives_measurement_noise_only(self):
        assert 1.0 < split.SPEED_TOLERANCE <= 1.5

    def test_a_stone_apparently_faster_at_the_far_line_is_refused(self):
        assert split.long_split(DELIVERY, t_hog=10.0, v_hog=None,
                                far=far(v_near=1.0, v_far=1.5)) is None

    def test_a_stone_that_slowed_is_kept(self):
        assert split.long_split(DELIVERY, t_hog=10.0, v_hog=None,
                                far=far(v_near=2.0, v_far=1.2)) is not None

    def test_inside_the_tolerance_is_kept(self):
        assert split.long_split(DELIVERY, t_hog=10.0, v_hog=None,
                                far=far(v_near=1.0, v_far=1.3)) is not None

    def test_without_a_near_panel_speed_the_side_view_speed_bounds_the_mean(self):
        # 21.843 m in 2 s is ~10.9 m/s against a 2.0 m/s near crossing.
        assert split.long_split(DELIVERY, t_hog=10.0, v_hog=2.0, far=far(t=12.0)) is None
        assert split.long_split(DELIVERY, t_hog=10.0, v_hog=2.5, far=far(t=20.0)) is not None

    def test_with_neither_speed_the_split_is_published_unchecked(self):
        assert split.long_split(DELIVERY, t_hog=10.0, v_hog=None, far=far(t=12.0)) is not None

    def test_an_extrapolated_crossing_with_no_far_speed_still_publishes(self):
        """speed_at_line needs a bracket, so a reached-for crossing has no far
        speed and the panel check cannot run; far_reach is the reader's mark."""
        s = split.long_split(DELIVERY, t_hog=10.0, v_hog=None,
                             far=far(reach=0.1, v_far=None, v_near=2.0))
        assert s is not None and s.far_reach == 0.1
```

- [ ] **Step 2: Write `tests/test_fartime.py`**

```python
"""The destination crossing, attached to each shot from both panels' lines."""

from types import SimpleNamespace

import pytest

from curling_score.game import fartime, split
from tests.synth_hogline import line_at

FAR = line_at(4.44)
NEAR = line_at(4.44)


def arriving(t0=20.0, y0=4.8, speed=1.0, fps=10.0, n=30):
    return tuple((round(t0 + i / fps, 3), 0.0, round(y0 - i * speed / fps, 4)) for i in range(n))


def leaving(t0=0.0, y0=-2.0, speed=2.0, fps=5.0, n=40):
    return tuple((round(t0 + i / fps, 3), 0.05, round(y0 + i * speed / fps, 4)) for i in range(n))


def shot(arrival=None, release=None, missing=False):
    return SimpleNamespace(
        missing=missing,
        delivery=None if arrival is None else SimpleNamespace(track=arrival),
        release=None if release is None else SimpleNamespace(track=release))


def test_an_observed_crossing_is_attached():
    s = shot(arrival=arriving())
    fartime.time_far_crossings([s], near_line=NEAR, far_line=FAR)
    fc = fartime.crossing(s)
    assert fc.t == pytest.approx(split.line_crossing(arriving(), FAR))
    assert fc.reach == 0.0
    assert fc.v_far == pytest.approx(1.0, abs=0.01)


def test_a_reached_for_crossing_is_attached_with_its_reach():
    s = shot(arrival=arriving(y0=4.34))
    fartime.time_far_crossings([s], near_line=NEAR, far_line=FAR)
    fc = fartime.crossing(s)
    assert fc.t is not None and fc.t < 20.0
    assert fc.reach == pytest.approx(0.10, abs=1e-6)
    assert fc.v_far is None


def test_an_arrival_beyond_the_cap_has_no_crossing():
    s = shot(arrival=arriving(y0=4.10))
    fartime.time_far_crossings([s], near_line=NEAR, far_line=FAR)
    assert fartime.crossing(s).t is None


def test_the_throwing_panel_gives_its_own_crossing_and_speed():
    s = shot(arrival=arriving(), release=leaving())
    fartime.time_far_crossings([s], near_line=NEAR, far_line=FAR)
    fc = fartime.crossing(s)
    assert fc.t_near_panel == pytest.approx(split.line_crossing(leaving(), NEAR))
    assert fc.v_near == pytest.approx(2.0, abs=0.01)


def test_a_panel_with_no_line_times_nothing_there():
    s = shot(arrival=arriving(), release=leaving())
    fartime.time_far_crossings([s], near_line=None, far_line=None)
    fc = fartime.crossing(s)
    assert fc.t is None and fc.v_far is None
    assert fc.t_near_panel is None and fc.v_near is None


def test_a_shot_with_no_delivery_has_no_far_crossing():
    s = shot(release=leaving())
    fartime.time_far_crossings([s], near_line=NEAR, far_line=FAR)
    assert fartime.crossing(s).t is None


def test_a_placeholder_shot_is_left_alone():
    s = shot(arrival=arriving(), missing=True)
    fartime.time_far_crossings([s], near_line=NEAR, far_line=FAR)
    assert fartime.crossing(s) is None


def test_it_never_changes_the_shot_list():
    shots = [shot(arrival=arriving()), shot(), shot(missing=True)]
    fartime.time_far_crossings(shots, near_line=NEAR, far_line=FAR)
    assert len(shots) == 3
```

- [ ] **Step 3: Rewrite the classify tests' split fixtures**

In `tests/test_classify.py`, replace the whole `def paired(...)` function with:

```python
def paired(split_s, near_speed=2.0, far_speed=1.2, rest_y_m=-2.5):
    """An arrival, its side-view crossing, and its far crossing, ``split_s`` apart.

    Returns ``(delivery, t_hog, far)``. The far crossing is what
    ``game/fartime.py`` attaches to a shot; the panel speeds are carried in it
    for the pairing check.
    """
    dv = delivery(track=flight(y0=5.6, y1=rest_y_m, speed=far_speed),
                  came_to_rest=False, reason="left-view", rest_y_m=rest_y_m)
    far_t = dv.track[1][0]
    t_hog = far_t - split_s
    far = split_mod.FarCrossing(t=far_t, v_far=far_speed, v_near=near_speed,
                                t_near_panel=t_hog)
    return dv, t_hog, far
```

and replace the whole of `class TestOutOfPlay:` (up to, not including, `class TestRefusingToGuess:`) with:

```python
class TestOutOfPlay:
    """A rock that left play having touched nothing, named by its long split.

    It was either a draw thrown far too heavy or a takeout that flashed, and
    hog to hog those are minutes apart in kind: a takeout crosses in well under
    12.5 s, a draw in well over. That is the one measurement that separates
    them -- see ``TestWeightDecidesNothing`` for the one that does not.
    """

    def test_a_slow_stone_that_ran_out_the_back_was_a_draw_thrown_through(self):
        dv, t_hog, far = paired(18.0)
        got, conf = classify.classify(dv, None, t_hog=t_hog, far=far)
        assert got == classify.DRAW_THROUGH
        assert conf == classify.CONF_CLEAR_REST

    def test_a_quick_stone_that_ran_out_the_back_was_a_takeout_that_flashed(self):
        dv, t_hog, far = paired(9.0)
        got, conf = classify.classify(dv, None, t_hog=t_hog, far=far)
        assert got == classify.FLASHED
        assert conf == classify.CONF_CLEAR_REST

    def test_a_split_exactly_on_the_threshold_reads_as_a_flash(self):
        dv, t_hog, far = paired(classify.SPLIT_HIT_MAX_S)
        got, _ = classify.classify(dv, None, t_hog=t_hog, far=far)
        assert got == classify.FLASHED

    def test_entry_speed_still_decides_nothing_here(self):
        dv, t_hog, far = paired(18.0, near_speed=4.0, far_speed=3.5)
        assert dv.speed_at() > 3.0
        got, _ = classify.classify(dv, None, t_hog=t_hog, far=far)
        assert got == classify.DRAW_THROUGH

    def test_a_stone_that_stopped_past_the_back_line_is_out_of_play_too(self):
        dv, t_hog, far = paired(9.0, rest_y_m=-2.5)
        got, _ = classify.classify(replace(dv, reason="rest", came_to_rest=True),
                                   None, t_hog=t_hog, far=far)
        assert got == classify.FLASHED

    def test_without_a_split_it_is_a_flash_and_says_so(self):
        got, conf = classify.classify(
            delivery(speed=1.4, came_to_rest=False, reason="left-view",
                     rest_y_m=-2.5), None)
        assert got == classify.FLASHED
        assert conf == classify.CONF_BOUNDARY

    def test_no_side_view_crossing_leaves_no_split(self):
        dv, _t_hog, far = paired(18.0)
        assert classify.classify(dv, None, t_hog=None, far=far) == (
            classify.FLASHED, classify.CONF_BOUNDARY)

    def test_no_far_crossing_leaves_no_split(self):
        dv, t_hog, _far = paired(18.0)
        assert classify.classify(dv, None, t_hog=t_hog, far=None) == (
            classify.FLASHED, classify.CONF_BOUNDARY)

    def test_the_split_needs_no_release(self):
        dv, t_hog, far = paired(18.0)
        bare = split_mod.FarCrossing(t=far.t)
        assert classify.classify(dv, None, t_hog=t_hog, far=bare)[0] == classify.DRAW_THROUGH

    def test_a_shot_is_classified_with_both_crossings_it_carries(self):
        from curling_score.game.shots import Shot

        dv, t_hog, far = paired(18.0)
        s = Shot(number=5, color="red", stones=[], t_rest_s=dv.t_rest, delivery=dv)
        s.t_hog_s = t_hog
        s.far_crossing = far
        assert classify.classify_shot(s)[0] == classify.DRAW_THROUGH

    def test_a_shot_that_drops_the_side_view_crossing_reads_as_a_flash(self):
        from curling_score.game.shots import Shot

        dv, _t_hog, far = paired(18.0)
        s = Shot(number=5, color="red", stones=[], t_rest_s=dv.t_rest, delivery=dv)
        s.far_crossing = far
        assert classify.classify_shot(s) == (classify.FLASHED, classify.CONF_BOUNDARY)

    def test_a_shot_that_drops_the_far_crossing_reads_as_a_flash(self):
        """The stage-3 version of the regression classify_shot exists to stop."""
        from curling_score.game.shots import Shot

        dv, t_hog, _far = paired(18.0)
        s = Shot(number=5, color="red", stones=[], t_rest_s=dv.t_rest, delivery=dv)
        s.t_hog_s = t_hog
        assert classify.classify_shot(s) == (classify.FLASHED, classify.CONF_BOUNDARY)
```

If `Release` or `climb` in `tests/test_classify.py` are no longer used after this, delete their imports/definitions.

- [ ] **Step 4: Rewrite the timeline tests' shot fixture**

In `tests/test_timeline.py`, in `class TestTimingFields`, replace the whole `def _shot(...)` method with:

```python
    def _shot(self, n, color, t_rest, t_rel=None, y_enter=4.5, side_view_saw_it=True,
              far_seen=True):
        from curling_score.detect.delivery import Delivery
        from curling_score.detect.release import Release
        tr, t, y = [], t_rest - 6.0, y_enter
        while y >= 0.2:
            tr.append((round(t, 3), 0.0, round(y, 4)))
            y -= 0.08
            t += 0.1
        dv = Delivery(color=color, t_enter=tr[0][0], t_rest=t_rest,
                      entry_y_m=y_enter, rest_x_m=0.0, rest_y_m=tr[-1][2],
                      travel_m=y_enter - tr[-1][2], track=tuple(tr))
        rel = None
        if t_rel is not None:
            rt, tt, yy = [], t_rel, -2.0
            while yy <= 4.8:
                rt.append((round(tt, 3), 0.05, round(yy, 4)))
                yy += 0.4
                tt += 0.2
            rel = Release(color=color, t=rt[0][0], y_exit_m=rt[-1][2],
                          speed_m_s=2.0, track=tuple(rt))
        # Standing in for the two passes analyze runs after the rules: the side
        # view's throwing-end crossing (``hogtime``), ~3.2 s after the release
        # at this track's pace, and the destination crossing (``fartime``) just
        # after the arrival is first seen. ``side_view_saw_it`` and
        # ``far_seen`` turn each off.
        t_hog = t_rel + 3.2 if rel is not None and side_view_saw_it else None
        far = split.FarCrossing(t=tr[0][0] + 0.1) if far_seen else None
        return S.Shot(number=n, color=color, stones=[det(color, 0.1, 0.2)],
                      t_rest_s=t_rest, delivery=dv, release=rel, t_hog_s=t_hog,
                      far_crossing=far)
```

In the same class, in the docstring of `test_a_panel_crossing_with_no_side_view_hog_has_no_split`, replace the text "``split.hog_crossing`` would return a time for it" with "the throwing panel's own tripwire would see it", and "back to ``split.hog_crossing(rel.track)``" with "to the panel's own crossing".

Then add this test to the class:

```python
    def test_a_shot_with_no_far_crossing_has_no_split(self):
        end = timeline.build_end(
            number=1, house="top", start_s=0.0, end_s=900.0,
            shots=[self._shot(1, "red", 100.0, t_rel=70.0, far_seen=False)])
        assert end["shots"][0]["long_split_s"] is None
        assert end["splits_measured"] == 0
```

- [ ] **Step 5: Run the new tests to verify they fail**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_split.py tests/test_fartime.py tests/test_classify.py tests/test_timeline.py -p no:cacheprovider -rf`
Expected: FAIL — `split` has no `FarCrossing`/`line_crossing`, no module `fartime`, and `Shot` has no `far_crossing`.

- [ ] **Step 6: Rewrite the top of `split.py`**

In `src/curling_score/game/split.py`, replace everything from the first line of the file down to and including the line

```python
BASELINE_M = C.TEE_TO_TEE_M - 2 * (C.TEE_TO_HOGLINE_M - C.STONE_RADIUS_M)
```

with:

```python
"""The long split: hog line to hog line, timed at the paint.

Both hog lines are in view, one in each overhead panel, and a stone crosses
them at 5-10 cm per sample. The split is two line crossings and a distance.

The panels cannot say where either line is in metres. ``geometry/calibrate.py``
fits a single ``px_per_m`` from the house rings, and the along-sheet scale falls
to roughly a third of that by the top of the frame, so the paint reports at
about +4.4 to +4.7 against a real 6.4 m. None of which matters to a crossing: a
tripwire needs where the paint sits in the panel's own numbers. For a while
that was one measured constant for every panel of every video, and hand marks
read in the overhead panels showed it was up to half a second wrong -- each
panel's paint sits somewhere different. It is now found per panel, in the paint
(``geometry/hogpaint.py``), and read at the stone's own lateral position.

The throwing end's crossing comes from the long camera (``game/hogtime.py``);
the destination's from that end's panel, attached to the shot by
``game/fartime.py``. See
``docs/superpowers/specs/2026-09-22-phase3-far-tripwire-design.md``.
"""

from dataclasses import dataclass

from curling_score.geometry import constants as C

# Both lines are timed at the stone's leading edge FIRST touching the paint: the
# throwing line's inside edge, ``TEE_TO_HOGLINE_M`` from that tee (what the
# phase-2 hand marks, and so the long camera, were validated against), and the
# destination line's outer edge, one line-width further out. So the leading
# edge covers tee-to-tee less one of each: 34.747 - 6.401 - 6.503 = 21.843 m.
#
# This replaced 22.229 m, which assumed the stone's centre one radius short of
# the inside edge at both ends. Only ``Split.speed_m_s`` and the pairing check
# read it; a published split's seconds never did.
BASELINE_M = C.TEE_TO_TEE_M - C.TEE_TO_HOGLINE_M - (C.TEE_TO_HOGLINE_M + C.HOGLINE_WIDTH_M)
```

Leave everything from `# How much faster a stone may appear` (`SPEED_TOLERANCE`) through `CROSS_CHECK_S = 0.25` exactly as it is.

- [ ] **Step 7: Rewrite the rest of `split.py`**

Replace everything from `@dataclass(frozen=True)` / `class Split:` to the end of the file with:

```python
@dataclass(frozen=True)
class Split:
    """One stone timed over ``baseline_m`` of sheet, hog line to hog line."""

    seconds: float
    baseline_m: float
    t_start: float       # leading edge on the throwing end's hog line (side view)
    t_end: float         # leading edge on the destination hog line (its panel)
    # 0.0 when the far crossing was observed. Positive when it was reached for,
    # in the panel's own y units, so a reader can tell an exact split from one
    # that cannot be checked against anything. See FAR_REACH_MAX_U.
    far_reach: float = 0.0
    # How far the throwing panel's own crossing was from the side view's
    # answer, or None when the panel had no reading. Kept rather than acted on:
    # while the composite's sources are out of step this measures the desync,
    # not the detector, and `scripts/ds13/sync_report.py` reads it that way.
    panel_delta: float | None = None

    @property
    def speed_m_s(self) -> float:
        """Mean speed over the baseline -- the number that reads as ice speed."""
        return self.baseline_m / self.seconds if self.seconds else 0.0


def crossing_time(track, y_line: float) -> float | None:
    """When a track passed a fixed ``y_line``, or None if it never bracketed it.

    For a line that sits at one y everywhere -- the tee line the thinking-time
    clock uses. Hog lines are not that; they use ``line_crossing``.
    """
    pts = [(float(t), float(y)) for t, _x, y in track]
    for (t0, y0), (t1, y1) in zip(pts, pts[1:]):
        if (y0 - y_line) * (y1 - y_line) <= 0 and y0 != y1:
            return t0 + (y_line - y0) * (t1 - t0) / (y1 - y0)
    return None


@dataclass(frozen=True)
class FarCrossing:
    """What one shot's two panels say about their hog lines (``game/fartime.py``).

    ``t`` is the destination crossing a split ends on, or None; ``reach`` is 0.0
    when it was observed, otherwise how far it was reached for. The rest are only
    ever checks: the panel speed across each line, for the pairing test, and the
    throwing panel's own crossing, recorded against the side view's.
    """

    t: float | None
    reach: float = 0.0
    v_far: float | None = None
    t_near_panel: float | None = None
    v_near: float | None = None


def line_crossing(track, line) -> float | None:
    """When a track's centre passed a panel's hog line, or None if it never did.

    ``line`` is a ``geometry.hogpaint.HogLine``, read at each sample's own
    lateral position: the paint bows in these lenses, and a curled stone
    crosses it well off the centre line.
    """
    pts = [(float(t), float(x), float(y)) for t, x, y in track or ()]
    for (t0, x0, y0), (t1, x1, y1) in zip(pts, pts[1:]):
        d0, d1 = y0 - line.y_at(x0), y1 - line.y_at(x1)
        if d0 * d1 <= 0 and d0 != d1:
            return t0 + d0 / (d0 - d1) * (t1 - t0)
    return None


# How far past the start of an arrival's track the destination crossing may be
# reached for, in the panel's own y units -- about two feet of ice at that scale.
#
# Measured against hand marks (`datasets/hogmarks`) on the eleven marked rocks
# the destination panel first sees already past its painted line, reaching
# back with the four-point linear fit below:
#
#     cap     n   median   worst   within 0.10 s
#     0.15    4   0.069    0.154      3/4
#     0.20    8   0.057    0.154      6/8
#     0.25   10   0.072    0.278      7/10
#
# 0.20 is where the first bad case would enter. A quadratic on eight points did
# worse against the marks (median 0.084, worst 0.163 at 0.20); it only looked
# better scored against the panel's own crossing, which is itself noisy at the
# frame edge. Eleven cases is a small sample -- see the spec's known risks.
FAR_REACH_MAX_U = 0.20

# Points used for the fit. Four is what a truncated track reliably has near the
# line, and more made the tail worse rather than better -- a quadratic over ten
# reached a 1.65 s worst case against this fit's 0.65 s.
_FAR_FIT_POINTS = 4


def far_crossing(track, line, *, max_reach: float):
    """``(t, reach)`` for an arrival crossing ``line``, reaching back a little.

    ``reach`` is 0.0 when the crossing was observed. Otherwise the track began
    past the line, and the time is extrapolated back to it from the four
    samples nearest it, with ``reach`` saying how far, so the split is marked.
    ``(None, 0.0)`` when the line is further back than ``max_reach`` or there is
    too little track to fit.
    """
    seen = line_crossing(track, line)
    if seen is not None:
        return seen, 0.0
    pts = sorted(((float(t), float(x), float(y)) for t, x, y in track or ()),
                 key=lambda p: -p[2])
    if len(pts) < _FAR_FIT_POINTS:
        return None, 0.0
    y_line = line.y_at(pts[0][1])
    reach = y_line - pts[0][2]
    if not 0.0 < reach <= max_reach:
        return None, 0.0
    near = pts[:_FAR_FIT_POINTS]
    n = len(near)
    my = sum(p[2] for p in near) / n
    mt = sum(p[0] for p in near) / n
    den = sum((p[2] - my) ** 2 for p in near)
    if den == 0:
        return None, 0.0
    slope = sum((p[2] - my) * (p[0] - mt) for p in near) / den    # dt/dy
    return mt + (y_line - my) * slope, reach


def speed_at_line(track, line) -> float | None:
    """How fast a track was crossing ``line``, in the panel's own units per second.

    Not metres per second, deliberately: both hog lines are read in their own
    panel's units, so two speeds measured there are distorted alike and can be
    compared without converting either.
    """
    pts = [(float(t), float(x), float(y)) for t, x, y in track or ()]
    for (t0, x0, y0), (t1, x1, y1) in zip(pts, pts[1:]):
        d0, d1 = y0 - line.y_at(x0), y1 - line.y_at(x1)
        if d0 * d1 <= 0 and d0 != d1 and t1 > t0:
            return abs(y1 - y0) / (t1 - t0)
    return None


def long_split(delivery, *, t_hog, v_hog, far) -> Split | None:
    """The split for one shot, or None when either end could not be timed.

    ``t_hog`` and ``v_hog`` are the throwing end, from the side view
    (``game/hogtime.py``); ``far`` is the ``FarCrossing`` that
    ``game/fartime.py`` attached to the shot. All three are keyword-only and
    REQUIRED -- pass None for "not measured". A caller that forgets one gets a
    TypeError instead of a missing split: the long-camera merge added a
    required input and updated one of this function's two callers, and the
    other silently lost every draw-through classification until 366ba20.
    """
    if delivery is None or t_hog is None or far is None or far.t is None:
        return None
    start, end = t_hog, far.t
    if end <= start:
        return None
    # The side view is the throwing end's timing source, so the throwing
    # panel's own crossing is RECORDED against it, never obeyed: most
    # recordings have a camera pair out of step, so the panel is a clock that
    # disagrees, not a second opinion about the same instant.
    panel_delta = None if far.t_near_panel is None else t_hog - far.t_near_panel
    # A stone only ever slows, so it cannot cross the far hog line faster than
    # it crossed the near one; if it appears to, the two were not the same
    # stone. Both speeds are in their own panel's units, distorted alike.
    if far.v_near and far.v_far and far.v_far > far.v_near * SPEED_TOLERANCE:
        return None
    if far.v_near is None and v_hog:
        # No throwing-panel speed, so bound the mean speed over the baseline by
        # the side view's speed at the near line -- both in real metres.
        mean = BASELINE_M / (end - start)
        if mean > v_hog * SPEED_TOLERANCE:
            return None
    return Split(seconds=end - start, baseline_m=BASELINE_M, t_start=start,
                 t_end=end, far_reach=far.reach, panel_delta=panel_delta)
```

- [ ] **Step 8: Create `game/fartime.py`**

```python
"""When each stone crossed the DESTINATION hog line, read from that end's panel.

Stage 3 of a throw; stage 2, the throwing end's line, is ``game/hogtime.py``.
Runs after the rules have settled the shot list and, like ``hogtime``, may only
attach timings to shots already in it -- nothing here adds, drops or renumbers
a shot.

The crossing rides on the shot rather than being computed where a split is
built, because ``split.long_split`` has two callers -- ``timeline.build_end``
and ``classify.classify_shot`` -- and the second only ever sees a shot.
"""

from curling_score.game import split


def time_far_crossings(shots, *, near_line, far_line) -> None:
    """Attach a ``split.FarCrossing`` to every shot that is not a placeholder.

    ``far_line`` is the destination panel's ``HogLine`` and ``near_line`` the
    throwing panel's. Either may be None when that panel's paint could not be
    found; whatever depends on it is then simply absent.
    """
    for shot in shots:
        if getattr(shot, "missing", False):
            continue
        dv = getattr(shot, "delivery", None)
        rel = getattr(shot, "release", None)
        arriving = tuple(getattr(dv, "track", None) or ()) if dv is not None else ()
        leaving = tuple(getattr(rel, "track", None) or ()) if rel is not None else ()
        t, reach, v_far = None, 0.0, None
        if arriving and far_line is not None:
            t, reach = split.far_crossing(arriving, far_line,
                                          max_reach=split.FAR_REACH_MAX_U)
            v_far = split.speed_at_line(arriving, far_line)
        t_near, v_near = None, None
        if leaving and near_line is not None:
            t_near = split.line_crossing(leaving, near_line)
            v_near = split.speed_at_line(leaving, near_line)
        shot.far_crossing = split.FarCrossing(t=t, reach=reach, v_far=v_far,
                                              t_near_panel=t_near, v_near=v_near)


def crossing(shot):
    """The ``split.FarCrossing`` attached to this shot, or None if none was."""
    return getattr(shot, "far_crossing", None)
```

- [ ] **Step 9: Give `Shot` the field**

In `src/curling_score/game/shots.py`, in the `Shot` dataclass, directly after the `t_hog_s: float | None = None` field and its comment, add:

```python
    # When this rock's leading edge reached the DESTINATION hog line, from that
    # end's panel, with the throwing panel's own reading beside it -- a
    # ``split.FarCrossing`` attached by ``fartime.time_far_crossings``. None
    # until that pass has run.
    far_crossing: object = None
```

- [ ] **Step 10: Pass it from both callers**

In `src/curling_score/timeline.py`, change the import

```python
from curling_score.game import classify, hogtime, rules, shots as shots_mod, split, thinking
```

to

```python
from curling_score.game import classify, fartime, hogtime, rules, shots as shots_mod, split, thinking
```

and replace

```python
        sp = split.long_split(rel, dv, t_hog=hogtime.crossing(s),
                              v_hog=hogtime.speed_at_hog(s))
```

with

```python
        sp = split.long_split(dv, t_hog=hogtime.crossing(s),
                              v_hog=hogtime.speed_at_hog(s),
                              far=fartime.crossing(s))
```

In `src/curling_score/game/classify.py`:

1. Change the signature `def classify(delivery, house_delta=None, release=None, *,` / `t_hog=None, v_hog=None):` to

```python
def classify(delivery, house_delta=None, *, t_hog=None, v_hog=None, far=None):
```

2. In its docstring, replace every paragraph after the `house_delta` paragraph with:

```
    ``t_hog`` and ``v_hog`` are the throwing end's crossing and speed from the
    side view (``game/hogtime.py``), and ``far`` is the destination crossing
    ``game/fartime.py`` attached to the shot. Together they time the long
    split; without all of them there is no split, and every shot that left
    play reads as a flash -- the common case, and a policy rather than
    evidence, which ``CONF_BOUNDARY`` admits.

    Keyword-only because passing inputs by position is how this broke once: the
    long-camera merge added the side-view crossing, this function kept calling
    ``long_split`` without it, and ``DRAW_THROUGH`` became unreachable until
    366ba20.
```

3. Replace `sp = split.long_split(release, delivery, t_hog=t_hog, v_hog=v_hog)` with

```python
        sp = split.long_split(delivery, t_hog=t_hog, v_hog=v_hog, far=far)
```

4. In `classify_shot`, replace the import and return:

```python
    from curling_score.game import hogtime

    return classify(shot.delivery, shot.house_delta, shot.release,
                    t_hog=hogtime.crossing(shot),
                    v_hog=hogtime.speed_at_hog(shot))
```

with

```python
    from curling_score.game import fartime, hogtime

    return classify(shot.delivery, shot.house_delta,
                    t_hog=hogtime.crossing(shot),
                    v_hog=hogtime.speed_at_hog(shot),
                    far=fartime.crossing(shot))
```

and in `classify_shot`'s docstring replace "This is where the side view's crossing reaches the classifier." with "This is where both hog-line crossings reach the classifier."

- [ ] **Step 11: Run `fartime` in `analyze`**

In `src/curling_score/analyze.py`, add `fartime` to the `from curling_score.game import (...)` list. Then, directly after the block

```python
            if sideviews is not None:
                hogtime.time_hog_crossings(
                    shots, path, sideviews[hogtime.CAMERA_FOR[OTHER_HOUSE[end.house]]])
```

add:

```python
            # Stage 3, the destination hog line, from that panel's painted line.
            # In this block ``setup`` is the destination panel and ``far`` is
            # the THROWING panel -- far from the house being played to.
            fartime.time_far_crossings(shots, near_line=far.hog_line,
                                       far_line=setup.hog_line)
```

Before adding it, confirm by reading upward that `setup` is still `read_setups[end.house]` and `far` is `read_setups[OTHER_HOUSE[end.house]]` at that point; if either has been reassigned, use those two expressions directly.

- [ ] **Step 12: Bump the pipeline version, and retire two stale references**

In `src/curling_score/game/hogtime.py`, replace the docstring of `crossing` with:

```python
    """When this shot crossed the throwing end's hog line, if it was seen to.

    Not named ``hog_crossing``: the panels time their own hog lines too
    (``split.line_crossing``), and two functions of one name timing the same
    line from different cameras is exactly the confusion to avoid.
    """
```

In `src/curling_score/version.py`, in the existing 2026.09.16 comment, replace `within FAR_EXTRAPOLATION_MAX_U` with `within a 0.05-unit cap` (the constant no longer exists; the comment is history).

Then replace the line `PIPELINE_VERSION = "2026.09.16"` with:

```python
# 2026.09.22: the destination hog line is placed per panel from its paint
# rather than the global 4.441, the far crossing may be reached for up to 0.20
# panel units, and BASELINE_M is the 21.843 m a stone's leading edge covers.
# Also covers the stage-1 release changes (db182cf), which altered releases,
# splits and thinking times without bumping this. Split values move by up to
# ~0.57 s on some panels; timelines from before this must not be reused.
PIPELINE_VERSION = "2026.09.22"
```

- [ ] **Step 13: Run the tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_split.py tests/test_fartime.py tests/test_classify.py tests/test_timeline.py tests/test_hogtime.py tests/test_thinking.py tests/test_release.py tests/test_hogpaint.py tests/test_panel_hog_line.py tests/test_analyze_write.py tests/test_viewer_js.py tests/test_sideshots.py -p no:cacheprovider -rf`
Expected: all pass.

Then: `grep -rn "HOG_APPARENT_Y_M\|FAR_EXTRAPOLATION_MAX_U\|hog_crossing(" src/ tests/`
Expected: no matches. (`tests/test_longview.py` reads a JSON key named `hog_crossing_s`; that is not a call and is fine.)

- [ ] **Step 14: Commit**

```bash
git add src/curling_score/game/split.py src/curling_score/game/fartime.py \
        src/curling_score/game/shots.py src/curling_score/timeline.py \
        src/curling_score/game/classify.py src/curling_score/analyze.py \
        src/curling_score/version.py tests/test_split.py tests/test_fartime.py \
        tests/test_classify.py tests/test_timeline.py
git commit -m "split: time the destination hog line at each panel's own paint

The far crossing moves onto the shot, like the side view's: fartime
attaches a FarCrossing from the destination panel's painted line
(observed, or reached back for up to 0.20 panel units) with the throwing
panel's own crossing and speed beside it, and long_split reads it through
required keyword arguments, so a caller that forgets one fails loudly
instead of losing splits. Both callers, timeline and classify_shot, pass
it from the shot.

HOG_APPARENT_Y_M, FAR_EXTRAPOLATION_MAX_U and hog_crossing are gone.
BASELINE_M is the 21.843 m a leading edge covers between the two first
touches. PIPELINE_VERSION bumped, covering the stage-1 merge as well.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: The diagnostic scripts use each panel's line

**Files:**
- Modify: `scripts/replay_end.py`
- Modify: `scripts/split_coverage.py`
- Modify: `scripts/split_audit.py`
- Modify: `scripts/render_split.py`
- Modify: `scripts/ds13/review_no_far_hog.py`
- Modify, NOT committed (untracked): `scripts/render_phase1.py`
- Create: `tests/test_replay_setups.py`

**Interfaces:**
- Consumes: `PanelSetup.hog_line` / `.hog_line_error` (Task 2); `fartime.time_far_crossings`, `fartime.crossing`, `split.long_split(delivery, *, t_hog, v_hog, far)`, `split.FarCrossing` (Task 3).
- Produces: `replay_end.setups_for(video, vid, root)` that recomputes a pickle lacking hog lines.

- [ ] **Step 1: Write the failing test for stale pickles**

Create `tests/test_replay_setups.py`:

```python
"""A setup pickled before panels carried a hog line must not be trusted."""

import pickle
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import replay_end  # noqa: E402
from curling_score.game import profile  # noqa: E402
from tests.synth_hogline import TOP, line_at  # noqa: E402


def _pickle(root, setups):
    (root / "setups-v.pkl").write_bytes(pickle.dumps((setups, "panels")))


def test_a_pickle_without_hog_lines_is_recomputed(tmp_path, monkeypatch):
    _pickle(tmp_path, {"top": profile.PanelSetup(rect=(0, 0, 1, 1), calib=TOP)})
    fresh = {"top": profile.PanelSetup(rect=(0, 0, 1, 1), calib=TOP, hog_line=line_at(4.44))}
    monkeypatch.setattr(replay_end, "_compute_setups", lambda video: (fresh, "panels"))
    setups, _ = replay_end.setups_for("v.mp4", "v", tmp_path)
    assert setups["top"].hog_line is not None


def test_a_pickle_with_a_line_or_a_reason_is_kept(tmp_path, monkeypatch):
    kept = {"top": profile.PanelSetup(rect=(0, 0, 1, 1), calib=TOP, hog_line=line_at(4.44)),
            "bottom": profile.PanelSetup(rect=(0, 0, 1, 1), calib=TOP,
                                         hog_line_error="bottom panel: no paint")}
    _pickle(tmp_path, kept)
    monkeypatch.setattr(replay_end, "_compute_setups",
                        lambda video: (_ for _ in ()).throw(AssertionError("recomputed")))
    setups, _ = replay_end.setups_for("v.mp4", "v", tmp_path)
    assert setups["bottom"].hog_line_error == "bottom panel: no paint"
```

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_replay_setups.py -p no:cacheprovider -rf`
Expected: FAIL — `replay_end` has no `_compute_setups`.

- [ ] **Step 2: `replay_end.py`**

Replace `setups_for` with:

```python
def _compute_setups(video):
    calib_frames = F.sample_keyframes(video, count=analyze_mod.CALIB_FRAMES,
                                      stride=analyze_mod.CALIB_STRIDE)
    panels = layout.detect_panels(calib_frames)
    return profile.calibrate_panels(calib_frames, panels), panels


def _has_hog_lines(setups) -> bool:
    """False for a pickle written before panels carried their painted hog line:
    such a setup loads with neither a line nor a reason, and would silently
    time no far crossings at all."""
    return all(getattr(s, "hog_line", None) is not None
               or getattr(s, "hog_line_error", None) for s in setups.values())


def setups_for(video, vid, root):
    """Panel calibration, computed once per video and kept beside the caches."""
    pkl = Path(root) / f"setups-{vid}.pkl"
    if pkl.exists():
        setups, panels = pickle.loads(pkl.read_bytes())
        if _has_hog_lines(setups):
            return setups, panels
    setups, panels = _compute_setups(video)
    pkl.write_bytes(pickle.dumps((setups, panels)))
    return setups, panels
```

Then, where the replay builds `built = shots_mod.from_deliveries(...)`, run the far pass directly after `thinking.time_shots(built, far_seq, far.view_y_min_m)`:

```python
    # `setup` is the house being played to; `far` is the throwing house.
    fartime.time_far_crossings(built, near_line=far.hog_line, far_line=setup.hog_line)
```

and replace

```python
        sp = split.long_split(getattr(sh, "release", None),
                              getattr(sh, "delivery", None))
```

with

```python
        sp = split.long_split(getattr(sh, "delivery", None), t_hog=None,
                              v_hog=None, far=fartime.crossing(sh))
```

Add `fartime` to the script's `from curling_score.game import (...)` list. Confirm `setup` in that function is the destination panel's read setup (the one passed to `find_deliveries`); if it is not in scope there, use the same expression that built it.

- [ ] **Step 3: `split_coverage.py`**

1. Add `fartime` to its `from curling_score.game import ...` imports.
2. Replace `panel_only_split` and `side_split` with:

```python
def panel_only_split(shot):
    fc = fartime.crossing(shot)
    if fc is None or fc.t_near_panel is None:
        return None
    return split.long_split(getattr(shot, "delivery", None), t_hog=fc.t_near_panel,
                            v_hog=None, far=fc)


def side_split(shot):
    return split.long_split(getattr(shot, "delivery", None),
                            t_hog=hogtime.crossing(shot),
                            v_hog=hogtime.speed_at_hog(shot),
                            far=fartime.crossing(shot))
```

3. Replace the gate constants block (`GATE_NO_RELEASE` through the `GATES = (...)` tuple) and `attribute_side_refusal` with:

```python
GATE_NO_DELIVERY = "no_delivery"         # long_split needs a delivery
GATE_NO_FAR_HOG = "no_far_hog_crossing"  # no far crossing, or not after t_hog
GATE_SPEED_TOLERANCE = "speed_tolerance"  # far line crossed faster than the near
GATE_MEAN_SPEED = "mean_speed"           # no near panel speed; mean beyond v_hog
GATE_UNACCOUNTED = "unaccounted"         # none of the above: a bug in this mirror

GATES = (GATE_NO_DELIVERY, GATE_NO_FAR_HOG, GATE_SPEED_TOLERANCE,
         GATE_MEAN_SPEED, GATE_UNACCOUNTED)


def attribute_side_refusal(delivery, t_hog, fc, v_hog):
    """Which check in split.long_split stopped a side-view crossing we have.

    Mirrors long_split's control flow in the same order, reading its
    thresholds off ``split``. Call only when ``t_hog is not None`` and
    ``side_split`` returned None for the shot.
    """
    if delivery is None:
        return GATE_NO_DELIVERY
    if fc is None or fc.t is None or fc.t <= t_hog:
        return GATE_NO_FAR_HOG
    if fc.v_near and fc.v_far and fc.v_far > fc.v_near * split.SPEED_TOLERANCE:
        return GATE_SPEED_TOLERANCE
    if fc.v_near is None and v_hog and split.BASELINE_M / (fc.t - t_hog) > v_hog * split.SPEED_TOLERANCE:
        return GATE_MEAN_SPEED
    return GATE_UNACCOUNTED
```

Delete the comment block directly above the old gate constants that describes mirroring `long_split` with `HOG_APPARENT_Y_M`; its replacement is the new docstring.

4. In the replay loop (where `hogtime.time_hog_crossings` is run per end), run `fartime.time_far_crossings(shots, near_line=<throwing panel setup>.hog_line, far_line=<destination panel setup>.hog_line)` directly after it, using the loop's own variables for those two setups.
5. In that loop replace each `split.hog_crossing(getattr(r, "track", ())) if r else None` with `(fartime.crossing(shot).t_near_panel if fartime.crossing(shot) else None)`; each `split.crossing_time(tr, split.HOG_APPARENT_Y_M)` with `(fartime.crossing(shot).t if fartime.crossing(shot) else None)`; the `near`/`far_v` `speed_at_line` reads with `fc.v_near` / `fc.v_far` of `fc = fartime.crossing(shot)`; the `attribute_side_refusal(r, delivery, t_hog)` call with `attribute_side_refusal(delivery, t_hog, fartime.crossing(shot), hogtime.speed_at_hog(shot))`; and the output field `"hog_apparent_y_m": split.HOG_APPARENT_Y_M` with `"far_reach_u": (fartime.crossing(shot).reach if fartime.crossing(shot) else None)`.
6. Remove the `cross_check_*` fields that read `split.CROSS_CHECK_S` only if they now reference names that no longer exist; `split.CROSS_CHECK_S` itself still exists and may stay.

- [ ] **Step 4: `split_audit.py`**

1. Make it load setups with lines: replace its local `setups_for(vid, root)` pickle-only loader with a call to `replay_end.setups_for(video, vid, root)`, adding a required `--video` argument to `main()` for the path, and `sys.path.insert(0, str(Path(__file__).parent))` before `import replay_end`.
2. After shots are built in `audit_end`, run `fartime.time_far_crossings(shots, near_line=far.hog_line, far_line=setup.hog_line)`.
3. Replace the per-shot fields that call `split.hog_crossing`, `split.crossing_time(..., split.HOG_APPARENT_Y_M)`, `_bracket_gap(..., split.HOG_APPARENT_Y_M)` and `split.speed_at_line(...)` with the attached `FarCrossing`: `"release_crossed": fc.t_near_panel is not None`, `"arrival_crossed": fc.t is not None and fc.reach == 0.0`, `"t_start_s": fc.t_near_panel`, `"t_end_s": fc.t`, `"far_reach_u": fc.reach`, `"near_speed": fc.v_near`, `"far_speed": fc.v_far` (rounded as before), where `fc = fartime.crossing(s)`. Delete `_bracket_gap` and the `near_gap_s`/`far_gap_s` fields.
4. In `all_releases`, replace `"crossed": split.hog_crossing(list(r.track)) is not None` with `"crossed": far.hog_line is not None and split.line_crossing(r.track, far.hog_line) is not None`.

- [ ] **Step 5: `render_split.py`**

Replace `sp = split.long_split(shot.release, shot.delivery)` and `HOG = split.HOG_APPARENT_Y_M` so the render reads the same crossings the pipeline does: run `fartime.time_far_crossings(shots, near_line=throw_setup.hog_line, far_line=play_setup.hog_line)` after `shots` is built, then `sp = split.long_split(shot.delivery, t_hog=None, v_hog=None, far=fartime.crossing(shot))`. For drawing, replace every use of `HOG` as a y value with the destination line's `play_setup.hog_line.y_at(x)` or the throwing line's `throw_setup.hog_line.y_at(x)` at the relevant sample's x; the tripwire row for drawing is `setup.calib.to_pixels(0.0, setup.hog_line.y_at(0.0))[1]`. The local `bracket()` helper becomes: find the bracketing pair using `split.line_crossing` logic against the right line (copy its loop with `line.y_at(x)`), and return `(None, None)` handled by the caller skipping the interpolation drawing when either is None.

- [ ] **Step 6: `ds13/review_no_far_hog.py`**

Load setups through `replay_end.setups_for` (it has `--video`). Replace the block that draws `split.HOG_APPARENT_Y_M` with drawing the panel's painted outer edge:

```python
            line = setup.hog_line
            if line is not None:
                pts = [(int(c), int(round(np.polyval(line.coef, c)))) for c in range(0, rect[2], 4)]
                for k in range(len(imgs)):
                    off = k * rect[2]
                    for (c0, r0), (c1, r1) in zip(pts, pts[1:]):
                        cv2.line(row, (off + c0, r0), (off + c1, r1), (0, 0, 255), 1)
                cv2.putText(row, "hog (paint outer edge)", (4, 14),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)
            else:
                cv2.putText(row, f"no hog line: {setup.hog_line_error}", (6, 18),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 200, 255), 1)
```

and in the caption replace `{split.HOG_APPARENT_Y_M - r['y_max']:.3f} m short of the line` with `first sighting`. Update the module docstring's reference to `split.HOG_APPARENT_Y_M` to say the line is now each panel's painted hog line. Remove the `split` import if unused.

- [ ] **Step 7: `render_phase1.py` (untracked; edit, do not commit)**

Replace `r_hog = panel_row(split.HOG_APPARENT_Y_M)` with `r_hog = panel_row(st.hog_line.y_at(0.0)) if st.hog_line is not None else panel_row(4.441)` — the fallback is acceptable only here, in an untracked throwaway renderer.

- [ ] **Step 8: Verify**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_replay_setups.py -p no:cacheprovider -rf` — expected pass.

Run: `for f in scripts/replay_end.py scripts/split_coverage.py scripts/split_audit.py scripts/render_split.py scripts/ds13/review_no_far_hog.py scripts/render_phase1.py; do PYTHONPATH=src .venv/bin/python -m py_compile $f && echo ok $f; done` — expected `ok` six times.

Run: `git grep -n "HOG_APPARENT_Y_M\|FAR_EXTRAPOLATION_MAX_U\|hog_crossing(" -- scripts/` — expected: no matches outside comments that describe history.

Then one real replay on this laptop, alone, on a cached game:

```bash
CURLING_SCORE_CACHE=/home/tcuser/.cache/curling_score PYTHONPATH=src timeout 1800 \
  .venv/bin/python scripts/replay_end.py out/timeline.json 2 --cache-root /home/tcuser/.cache/curling_score
```

Expected: it runs to completion. `setups-VXU9xwmugRg.pkl` predates hog lines, so the first run recomputes and rewrites it.

- [ ] **Step 9: Commit**

```bash
git add scripts/replay_end.py scripts/split_coverage.py scripts/split_audit.py \
        scripts/render_split.py scripts/ds13/review_no_far_hog.py tests/test_replay_setups.py
git commit -m "scripts: the diagnostics time far crossings the way the pipeline does

Each takes its panels' painted hog lines and the shot's FarCrossing
instead of the deleted global tripwire. replay_end.setups_for treats a
pickle written before panels carried a hog line as stale and recomputes
it: loaded as-is it would silently time no far crossings. split_coverage's
refusal attribution now mirrors the new long_split.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Acceptance against the hand marks

**Files:**
- Create: `scripts/phase3/build_far_tripwire_fixture.py`
- Create: `tests/fixtures/far_tripwire/` (six `plate-<video>-<panel>.png`, `cases.json`, `README.md`)
- Create: `tests/test_far_tripwire_acceptance.py`

**Interfaces:**
- Consumes: `profile.panel_median`, `profile.calibrate_panels`-produced calibrations (Task 2); `hogpaint.find_hog_line` (Task 1); `split.line_crossing`, `split.far_crossing`, `split.FAR_REACH_MAX_U` (Task 3); `datasets/hogmarks/receiving-controls.json` and `datasets/hogmarks/hOKZoeJNTpM-receiving.json` (committed).
- Produces: a CI-runnable acceptance test.

- [ ] **Step 1: Write the fixture builder**

Create `scripts/phase3/build_far_tripwire_fixture.py`:

```python
#!/usr/bin/env python
"""Build the acceptance fixture for the painted far tripwire.

For each of the six panels of the three hand-marked games: the panel's
calibration median (exactly what ``profile.calibrate_panel`` sees) as a PNG,
and its calibration. For each hand-marked rock: its arrival track. Tracks come
from the phase-3 probe outputs (``<cache>/<video>-phase3.json``), written by a
replay on 2026-09-22; the marks are ``datasets/hogmarks``.

    PYTHONPATH=src python scripts/phase3/build_far_tripwire_fixture.py
"""

import json
import pickle
from pathlib import Path

import cv2

from curling_score import analyze as A
from curling_score.game import profile
from curling_score.ingest import frames as F

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "tests" / "fixtures" / "far_tripwire"
GAMES = {"AEqLTgM25Tc": Path.home() / ".cache/curling_replay",
         "VXU9xwmugRg": Path.home() / ".cache/curling_score",
         "hOKZoeJNTpM": Path.home() / ".cache/curling_score"}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    marks = [m for m in json.loads((REPO / "datasets/hogmarks/receiving-controls.json").read_text())["marks"]]
    marks += [dict(m, video="hOKZoeJNTpM") for m in
              json.loads((REPO / "datasets/hogmarks/hOKZoeJNTpM-receiving.json").read_text())["marks"]]
    panels_out, cases = {}, []
    for vid, root in GAMES.items():
        setups, _p = pickle.loads((root / f"setups-{vid}.pkl").read_bytes())
        frames = F.sample_keyframes(root / "videos" / f"{vid}.mp4",
                                    count=A.CALIB_FRAMES, stride=A.CALIB_STRIDE)
        for name, st in setups.items():
            median = profile.panel_median(frames, st.rect, name)
            png = f"plate-{vid}-{name}.png"
            cv2.imwrite(str(OUT / png), median)
            c = st.calib
            panels_out[f"{vid}/{name}"] = {
                "plate": png, "center_px": list(c.center_px), "px_per_m": c.px_per_m,
                "edge_erosion_px": c.edge_erosion_px, "residual_m": c.residual_m,
                "flipped": c.flipped}
        tracks = {(e["end"], r["shot"]): r["track"]
                  for e in json.loads((root / f"{vid}-phase3.json").read_text()) for r in e["shots"]}
        for m in marks:
            if m["video"] != vid or m.get("crossing_s") is None:
                continue
            cases.append({"video": vid, "panel": m["destination_panel"], "end": m["end"],
                          "shot": m["shot"], "mark_s": m["crossing_s"],
                          "track": tracks[(m["end"], m["shot"])]})
    (OUT / "cases.json").write_text(json.dumps({"panels": panels_out, "cases": cases}))
    print(f"wrote {len(panels_out)} panels, {len(cases)} cases to {OUT}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Build it**

Run (on this laptop; decodes calibration frames from the three cached videos, CPU only):

```bash
PYTHONPATH=src .venv/bin/python scripts/phase3/build_far_tripwire_fixture.py
du -sh tests/fixtures/far_tripwire
```

Expected: `wrote 6 panels, N cases` with N about 62 (the 54 marked controls plus the 8 hOKZ marks; any mark without `crossing_s` is skipped). Total under 2 MB. Report N; do not edit the marks. If a mark's `(end, shot)` is missing from the phase-3 tracks, stop and report which.

Then write `tests/fixtures/far_tripwire/README.md`:

```markdown
# Far tripwire acceptance fixture

Six overhead panels' calibration medians (BGR PNG, exactly what
`profile.calibrate_panel` sees) with their calibrations, and the arrival track
of every hand-marked rock in `datasets/hogmarks/receiving-controls.json` and
`datasets/hogmarks/hOKZoeJNTpM-receiving.json`. Built by
`scripts/phase3/build_far_tripwire_fixture.py` from the three cached games;
tracks came from a replay on 2026-09-22. Used by
`tests/test_far_tripwire_acceptance.py`. Do not hand-edit.
```

- [ ] **Step 3: Write the acceptance test**

Create `tests/test_far_tripwire_acceptance.py`:

```python
"""The painted far tripwire, scored against hand marks on three games.

Paint found in each panel's real calibration median, each marked rock's real
arrival track timed against it, errors against a person's mark. Thresholds
are from the spec: measured 0.049 s median / 80% within 0.10 s observed, and
0.057 s median / 0.154 s worst extrapolated.
"""

import json
import statistics
from pathlib import Path

import cv2
import pytest

from curling_score.game import split
from curling_score.geometry import hogpaint
from curling_score.geometry.calibrate import PanelCalib

FIX = Path(__file__).parent / "fixtures" / "far_tripwire"


@pytest.fixture(scope="module")
def scored():
    data = json.loads((FIX / "cases.json").read_text())
    lines = {}
    for key, p in data["panels"].items():
        calib = PanelCalib(center_px=tuple(p["center_px"]), px_per_m=p["px_per_m"],
                           edge_erosion_px=p["edge_erosion_px"], residual_m=p["residual_m"],
                           flipped=p["flipped"])
        lines[key] = hogpaint.find_hog_line(cv2.imread(str(FIX / p["plate"])), calib)
    observed, extrapolated = [], []
    for c in data["cases"]:
        line = lines[f"{c['video']}/{c['panel']}"]
        track = [tuple(p) for p in c["track"]]
        t = split.line_crossing(track, line)
        if t is not None:
            observed.append(abs(t - c["mark_s"]))
            continue
        t, reach = split.far_crossing(track, line, max_reach=split.FAR_REACH_MAX_U)
        if t is not None:
            extrapolated.append(abs(t - c["mark_s"]))
    return lines, observed, extrapolated


def test_the_paint_is_found_on_all_six_panels(scored):
    lines, _, _ = scored
    assert len(lines) == 6


def test_observed_crossings_land_on_the_marks(scored):
    _, observed, _ = scored
    assert len(observed) >= 45
    assert statistics.median(observed) <= 0.06
    assert sum(e <= 0.10 for e in observed) / len(observed) >= 0.75


def test_reached_for_crossings_land_near_the_marks(scored):
    _, _, extrapolated = scored
    assert len(extrapolated) >= 6
    assert statistics.median(extrapolated) <= 0.08
    assert max(extrapolated) <= 0.20
```

- [ ] **Step 4: Run it**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_far_tripwire_acceptance.py -p no:cacheprovider -rf`
Expected: all three pass. If a threshold fails, **report the measured numbers and stop**; do not change a threshold, the offset or the cap to make it pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/phase3/build_far_tripwire_fixture.py tests/fixtures/far_tripwire tests/test_far_tripwire_acceptance.py
git commit -m "tests: score the painted far tripwire against hand marks, no video needed

Six real calibration medians and the arrival tracks of every hand-marked
rock on three games. The test finds the paint, times each track against
it, and holds the spec's thresholds against a person's marks: observed
crossings median <= 0.06 s with >= 75% within 0.10 s, reached-for ones
median <= 0.08 s and worst <= 0.20 s.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Replay report (manual, no source change)

**Files:**
- Create: `scripts/phase3/split_report.py`

**Interfaces:**
- Consumes: everything above; the cached games; the pre-change per-rock splits saved at `~/.cache/curling_replay/AEqLTgM25Tc-splits-branch.json`, `~/.cache/curling_score/VXU9xwmugRg-splits-branch.json`, `~/.cache/curling_score/hOKZoeJNTpM-splits-branch.json` (`[{"end", "shots": [{"shot", "split_s", ...}]}]`).
- Produces: a printed report.

- [ ] **Step 1: Write the report script**

Create `scripts/phase3/split_report.py`. It replays every end of one game exactly as `analyze` does from the caches: the same steps as `scripts/split_audit.py`'s `audit_end`, plus `hogtime.time_hog_crossings` on the side view (built with `sideview.locate` / `sideview.solve` from the calibration median, as `analyze` does) and `fartime.time_far_crossings`. Then it calls `split.long_split(dv, t_hog=hogtime.crossing(s), v_hog=hogtime.speed_at_hog(s), far=fartime.crossing(s))` per shot, and prints:

- rocks, far crossings observed / reached for / none, splits;
- against the baseline JSON given by `--before`: splits then and now, splits gained and lost, and for rocks split both times the distribution (median, p90, max) of `|now - then|` seconds.

Arguments: `timeline`, `--cache-root`, `--video`, `--before`. Load setups through `replay_end.setups_for`.

- [ ] **Step 2: Run it, one game at a time, nothing else running**

```bash
for spec in "AEqLTgM25Tc /home/tcuser/.cache/curling_replay /home/tcuser/curling-work/ds13/review_out/timeline.json" \
            "VXU9xwmugRg /home/tcuser/.cache/curling_score out/timeline.json" \
            "hOKZoeJNTpM /home/tcuser/.cache/curling_score /home/tcuser/.cache/curling_score/timeline-hOKZoeJNTpM.json"; do
  set -- $spec
  CURLING_SCORE_CACHE=$2 PYTHONPATH=src timeout 3000 .venv/bin/python scripts/phase3/split_report.py \
    $3 --cache-root $2 --video $2/videos/$1.mp4 --before $2/$1-splits-branch.json
done
```

Expected, approximately: 285 of 329 far crossings (207 observed, 78 reached for) and about 278 splits in total, against 242 before. Report the actual numbers; this is a report, not a gate.

- [ ] **Step 3: Commit**

```bash
git add scripts/phase3/split_report.py
git commit -m "scripts: a before-and-after split report for a replayed game

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```
