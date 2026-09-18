# Long-camera hog crossing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Time the throwing-end hog-line crossing from the composite's side cameras, so the long split covers most shots instead of 46% of them.

**Architecture:** Three new units mirroring the existing split of responsibilities — `geometry/sideview.py` calibrates each side view from paint once per video, `detect/longview.py` turns a video and a time window into one crossing or a refusal, and `game/hogtime.py` attaches that crossing to each shot after the rules have settled the shot list. `game/split.py` then reads the throwing-end crossing off the shot instead of deriving it from the release track.

**Tech Stack:** Python 3.12, numpy, OpenCV, ffmpeg (seek-and-decode), pytest.

**Spec:** `docs/superpowers/specs/2026-09-14-long-camera-hog-crossing-design.md`

## Global Constraints

- **Never publish a doubtful crossing.** Every gate in `longview` refuses rather than guesses. Refusal reasons are for logs and tests; they do **not** go in the timeline.
- **One method per game.** The long camera is the only source of the throwing-end crossing. If it refuses for a shot, that shot has no split — the panel tripwire is a cross-check, never a per-shot fallback.
- **No video-level fallback.** A video whose side views cannot be calibrated gets no splits and says so loudly.
- **Do not touch `detect/rocks.py`, `detect/yolo.py` or `geometry/calibrate.py`.** They form the detection cache key (`detect/cache.py:33-37`); changing any of them forces a full GPU re-detect of every video.
- **The target-end crossing is unchanged** — still the playing panel's own tripwire at `split.HOG_APPARENT_Y_M`.
- Every commit runs `pytest -m "not slow"` green and `npx eslint .` clean from `frontend/` if any `.mjs`/`.jsx` changed.
- Commit messages: lower-case component prefix, a sentence that says *why*, and the `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>` trailer.

---

### Task 1: Locate the two side views in the composite

**Files:**
- Create: `src/curling_score/geometry/sideview.py`
- Test: `tests/test_sideview.py`

**Interfaces:**
- Consumes: `geometry.layout.PanelLayout` (has `.top` and `.bottom`, each an `(x, y, w, h)` rect).
- Produces: `sideview.Rect`, `sideview.locate(layout, width, height) -> dict[str, Rect]` returning keys `"left"` and `"right"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sideview.py
"""The composite's two wide side views: where they are, and where the far
end's paint sits in them.

Each side camera watches the *other* end's house and hog line -- its own hog
line is about half a metre beneath it, out of frame. So the throwing end's
crossing is seen by the camera at the target end.
"""

import pytest

from curling_score.geometry import layout, sideview


def a_layout(x=810, w=297):
    """A panel layout the shape the club's composite actually produces."""
    return layout.PanelLayout(top=(x, 10, w, 514), bottom=(x, 554, w, 516))


class TestLocate:
    def test_the_views_are_what_the_overhead_strip_leaves_behind(self):
        got = sideview.locate(a_layout(), width=1920, height=1080)
        assert got["left"] == (0, 0, 810, 1080)
        assert got["right"] == (1107, 0, 813, 1080)

    def test_it_follows_the_strip_rather_than_assuming_where_it_sits(self):
        got = sideview.locate(a_layout(x=700, w=300), width=1920, height=1080)
        assert got["left"] == (0, 0, 700, 1080)
        assert got["right"] == (1000, 0, 920, 1080)

    def test_a_strip_touching_an_edge_leaves_no_view_that_side(self):
        with pytest.raises(sideview.SideViewError):
            sideview.locate(a_layout(x=0, w=297), width=1920, height=1080)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_sideview.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'curling_score.geometry.sideview'`

- [ ] **Step 3: Write the minimal implementation**

```python
# src/curling_score/geometry/sideview.py
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_sideview.py -q`
Expected: PASS, 3 tests

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/geometry/sideview.py tests/test_sideview.py
git commit -m "$(cat <<'EOF'
sideview: find the two wide cameras the layout throws away

layout.detect_panels searches only the middle of the frame, so the two end
cameras have never been used. They are the only view of the throwing end's
hog line that is not blocked by the thrower.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Fit the far house's tee and hog rows from paint

**Files:**
- Modify: `src/curling_score/geometry/sideview.py`
- Modify: `tests/synth.py`
- Modify: `tests/test_sideview.py`

**Interfaces:**
- Consumes: `sideview.Rect` from Task 1.
- Produces:
  - `sideview.SideView` — frozen dataclass with `rect: Rect`, `tee_row: float`, `hog_row: float`, `d_m: float`, the property `rows_tee_to_hog: float`, and the methods `row_for(x_m: float) -> float` and `metres_at(row: float) -> float` (inverse of `row_for`; Task 4's speed gate uses it).
  - `sideview.solve(plate, rect, name="side") -> SideView`, raising `SideViewError`.
  - `synth.side_view(tee_row=..., hog_row=..., w=..., h=...) -> np.ndarray` (BGR) and `synth.side_view_stone(img, row, width_px, color) -> np.ndarray`.

Background: looking down the sheet, ground distance `x` from the far tee maps to
image row `y = YH + C / (D - x)`, where `D` is the camera's distance to that tee.
Measuring the tee and hog rows fixes `C` and `YH` outright — the lateral scale
cancels:

```
u  = rows_tee_to_hog * (D - 6.401) / 6.401
C  = D * u
YH = tee_row - u
```

`D` is taken from the sheet drawing: backboard to backboard 45.720 m against
tee to tee 34.747 m puts a wall-mounted camera 40.233 m from the far tee. It
only affects curvature between the two fitted rows, and the tripwire does not
use it at all.

- [ ] **Step 1: Write the synthetic renderer**

```python
# tests/synth.py -- append

SIDE_ICE = (238, 238, 236)
SIDE_LINE = (150, 148, 140)
GREEN_PAINT = (70, 150, 70)

def side_view(tee_row=430.0, hog_row=520.0, w=810, h=1080, d_m=40.233,
              noise=0.0, seed=0):
    """An oblique view down the sheet at the far end's house.

    Returns **RGB**, unlike ``house_panel`` above, which is BGR for cv2. These
    feed ``detect/longview.py``, which reads frames ffmpeg decoded as rgb24,
    and its colour mask takes channel 0 as red. ``sideview.solve`` is unaffected
    either way -- greenness and luminance are channel-order agnostic.

    Renders only what ``sideview.solve`` reads: the green 12-ft annulus as two
    bands either side of the tee, and the hog line as a darker row. Positions
    come from the same perspective map the fit inverts, so a correct fit
    recovers ``tee_row`` and ``hog_row`` exactly.
    """
    import numpy as np

    img = np.full((h, w, 3), SIDE_ICE, dtype=np.uint8)
    rows = hog_row - tee_row
    u = rows * (d_m - C.TEE_TO_HOGLINE_M) / C.TEE_TO_HOGLINE_M
    c, yh = d_m * u, tee_row - u
    row_for = lambda x: yh + c / (d_m - x)

    # the annulus: 1.219..1.829 m either side of the tee
    for lo, hi in ((-C.R_12FT_M, -C.R_8FT_M), (C.R_8FT_M, C.R_12FT_M)):
        a, b = sorted((int(round(row_for(lo))), int(round(row_for(hi)))))
        img[a:b + 1, int(w * 0.12):int(w * 0.88)] = GREEN_PAINT

    r = int(round(hog_row))
    img[r - 1:r + 2] = SIDE_LINE

    if noise:
        rng = np.random.default_rng(seed)
        img = np.clip(img.astype(np.float32) + rng.normal(0, noise, img.shape),
                      0, 255).astype(np.uint8)
    return img


def side_view_stone(img, row, width_px=52, color="red", x=None):
    """Paint a stone on a side view: a grey body with a coloured handle. RGB."""
    import numpy as np

    out = img.copy()
    h, w = out.shape[:2]
    cx = w // 2 if x is None else int(x)
    body_h = max(4, int(width_px * 0.42))
    r = int(round(row))
    x0, x1 = cx - width_px // 2, cx + width_px // 2
    out[max(0, r - body_h):r, max(0, x0):x1] = (150, 150, 150)
    hw = max(3, width_px // 4)
    rgb = (210, 40, 40) if color == "red" else (230, 210, 40)
    out[max(0, r - body_h - hw // 2):max(0, r - body_h) + 1,
        cx - hw:cx + hw] = rgb
    return out
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_sideview.py -- append

from tests import synth


class TestSolve:
    def test_it_recovers_the_rows_it_was_drawn_with(self):
        plate = synth.side_view(tee_row=430.0, hog_row=520.0)
        got = sideview.solve(plate, (0, 0, 810, 1080))
        assert got.tee_row == pytest.approx(430.0, abs=1.5)
        assert got.hog_row == pytest.approx(520.0, abs=1.5)
        assert got.rows_tee_to_hog == pytest.approx(90.0, abs=2.0)

    def test_it_works_at_the_other_framings_the_club_actually_uses(self):
        for tee, hog in ((452.0, 533.0), (466.0, 552.0), (468.0, 557.0)):
            got = sideview.solve(synth.side_view(tee_row=tee, hog_row=hog),
                                 (0, 0, 810, 1080))
            assert got.tee_row == pytest.approx(tee, abs=1.5), (tee, hog)
            assert got.hog_row == pytest.approx(hog, abs=1.5), (tee, hog)

    def test_the_tee_is_fitted_not_taken_as_the_ring_s_centroid(self):
        """Perspective magnifies the annulus's near half, so its centroid sits
        about 2 px toward the camera. Fitting the four painted edges does not."""
        plate = synth.side_view(tee_row=430.0, hog_row=520.0)
        got = sideview.solve(plate, (0, 0, 810, 1080))
        green = (plate[:, :, 1].astype(float)
                 - (plate[:, :, 0].astype(float) + plate[:, :, 2]) / 2)
        prof = green[:, 300:560].mean(axis=1)
        weights = prof.clip(min=0)
        centroid = (weights * range(len(weights))).sum() / weights.sum()
        assert abs(got.tee_row - 430.0) < abs(centroid - 430.0)

    def test_the_map_puts_the_hog_line_where_the_rules_say(self):
        got = sideview.solve(synth.side_view(), (0, 0, 810, 1080))
        assert got.row_for(0.0) == pytest.approx(got.tee_row, abs=0.01)
        assert got.row_for(C.TEE_TO_HOGLINE_M) == pytest.approx(got.hog_row, abs=0.01)

    def test_ice_with_no_house_on_it_is_refused(self):
        import numpy as np
        blank = np.full((1080, 810, 3), 238, dtype=np.uint8)
        with pytest.raises(sideview.SideViewError):
            sideview.solve(blank, (0, 0, 810, 1080))
```

Add `from curling_score.geometry import constants as C` to the test imports.

- [ ] **Step 3: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_sideview.py -q`
Expected: FAIL — `AttributeError: module 'curling_score.geometry.sideview' has no attribute 'solve'`

- [ ] **Step 4: Write the implementation**

```python
# src/curling_score/geometry/sideview.py -- append

import numpy as np

from curling_score.geometry import constants as C

# Backboard to backboard is 45.720 m against 34.747 m tee to tee, so a tee sits
# 5.487 m from its own backboard and a camera on that wall is this far from the
# far tee. It sets only the curvature between the two fitted rows; the tripwire
# does not use it, and the depth scale at the hog line moves by under 5% across
# D = 35..45 m.
CAMERA_TO_FAR_TEE_M = 34.747 + (45.720 - 34.747) / 2

_GREEN_THRESHOLD = 1.5
_HOUSE_SEARCH = (0.33, 0.50)   # fraction of the view's height to look in
_HOG_SEARCH_PX = 140           # how far below the house the line can be


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


def _green_profile(plate, rect):
    x, y, w, h = rect
    view = np.asarray(plate, dtype=np.float32)[y:y + h, x:x + w]
    mid = view[:, int(w * 0.30):int(w * 0.70)]
    return mid[:, :, 1] - (mid[:, :, 0] + mid[:, :, 2]) / 2, view.mean(axis=(1, 2))


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
    if len(edges) >= 4:
        seen = [edges[0], edges[1], edges[-2], edges[-1]]
        want = np.array([-C.R_12FT_M, -C.R_8FT_M, C.R_8FT_M, C.R_12FT_M])
    elif len(edges) >= 2:
        # A house far enough off blurs its annulus into one run; the outer
        # edges still bracket the tee.
        seen = [edges[0], edges[-1]]
        want = np.array([-C.R_12FT_M, C.R_12FT_M])
    else:
        raise SideViewError(f"{name}: found {len(edges)} green edges, need at least 2")

    hog = _hog_row(lum, int(edges[-1]) + 12, name)

    def error(tee):
        v = SideView(rect=rect, tee_row=tee, hog_row=hog)
        return float(((np.array([v.row_for(x) for x in want]) - seen) ** 2).sum())

    tee = min(np.arange(hog - _HOG_SEARCH_PX, hog - 30, 0.05), key=error)
    return SideView(rect=rect, tee_row=float(tee), hog_row=float(hog))
```

Add `from dataclasses import dataclass` to the module imports if Task 1 did not.

- [ ] **Step 5: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_sideview.py -q`
Expected: PASS, 8 tests

- [ ] **Step 6: Run the whole fast suite**

Run: `.venv/bin/python -m pytest -m "not slow" -q`
Expected: PASS (1465 + the new ones)

- [ ] **Step 7: Commit**

```bash
git add src/curling_score/geometry/sideview.py tests/test_sideview.py tests/synth.py
git commit -m "$(cat <<'EOF'
sideview: fit the far house's tee and hog rows from paint

A tripwire needs one row, so the calibration fits two and nothing else --
the lateral scale cancels out of the row-to-metre map. The tee is fitted
from the annulus's four painted edges rather than its centroid, which
perspective drags about 2 px toward the camera.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Make the fit work on all ten real views

**Files:**
- Modify: `src/curling_score/geometry/sideview.py`
- Modify: `tests/test_sideview.py`
- Modify: `tests/conftest.py`

**Interfaces:**
- Consumes: `sideview.solve` from Task 2; `tests/conftest.py`'s `harvested_frames` fixture and `VALIDATION_VIDS` (sheet → video id, all five sheets, 12 cached frames each).
- Produces: `conftest.side_plate` fixture — `side_plate(vid) -> np.ndarray`, the median of a validation video's harvested frames, skipping if too few.

On 12-frame plates the Task 2 fit handles 7 of 10 views. The three failures and
their expected causes:

| view | symptom | likely cause |
|---|---|---|
| sheet 2 right, sheet 5 right | "found 3 green edges" | a partly-occluded annulus in a thin median crosses the threshold an odd number of times |
| sheet 1 left | fits 40 px tee-to-hog against 78-90 elsewhere | `_hog_row` locks onto something inside the house rather than the line below it |

The `len(edges) >= 2` branch in Task 2 already covers odd counts. This task adds
the plausibility gate that catches the second, and proves both against real
plates.

- [ ] **Step 1: Add the plate fixture**

```python
# tests/conftest.py -- append

@pytest.fixture(scope="session")
def side_plate(harvested_frames):
    """A clean plate of one validation video, for side-view calibration.

    The median over sampled frames removes players and stones and leaves the
    paint. Production calibrates on ``analyze.CALIB_FRAMES`` (24) frames; the
    harvested set is thinner, so this is the harder case on purpose.
    """
    import numpy as np

    def _plate(vid, minimum=8):
        frames = harvested_frames(vid, minimum=minimum)
        return np.median(np.stack([f.astype(np.float32) for f in frames]), axis=0)

    return _plate
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_sideview.py -- append

from tests.conftest import VALIDATION_VIDS

# Measured across the club's five sheets: the far tee sits 78-90 rows above
# its hog line in a side view. Anything outside says the fit found the wrong
# thing, and a wrong row is worse than no calibration.
PLAUSIBLE_ROWS = (60.0, 110.0)


@pytest.mark.slow
class TestEveryRealView:
    """Both side views of all five sheets, on plates thinner than production's."""

    @pytest.mark.parametrize("sheet,vid", sorted(VALIDATION_VIDS.items()))
    def test_both_views_calibrate(self, side_plate, sheet, vid):
        plate = side_plate(vid)
        h, w = plate.shape[:2]
        rects = sideview.locate(a_layout(), width=w, height=h)
        for name, rect in rects.items():
            got = sideview.solve(plate, rect, name=f"sheet{sheet}-{name}")
            assert PLAUSIBLE_ROWS[0] <= got.rows_tee_to_hog <= PLAUSIBLE_ROWS[1], (
                f"sheet {sheet} {name}: tee->hog {got.rows_tee_to_hog:.1f} px")

    def test_a_fit_outside_the_plausible_band_raises_rather_than_returns(self):
        """A calibration that is merely wrong is the dangerous outcome: every
        crossing afterwards is confidently mistimed."""
        import numpy as np
        plate = synth.side_view(tee_row=430.0, hog_row=470.0)   # only 40 rows
        with pytest.raises(sideview.SideViewError):
            sideview.solve(plate, (0, 0, 810, 1080))
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_sideview.py -m slow -q`
Expected: FAIL — at least sheet 1 and the plausibility test.

- [ ] **Step 4: Add the plausibility gate**

```python
# src/curling_score/geometry/sideview.py -- add near the other constants

# Measured across the club's five sheets: the far tee sits 78-90 rows above
# its hog line. A fit outside this found the wrong row, and a wrong row is far
# worse than no calibration -- every crossing after it is confidently mistimed.
PLAUSIBLE_ROWS = (60.0, 110.0)
```

and at the end of `solve`, before returning:

```python
    view = SideView(rect=rect, tee_row=float(tee), hog_row=float(hog))
    if not PLAUSIBLE_ROWS[0] <= view.rows_tee_to_hog <= PLAUSIBLE_ROWS[1]:
        raise SideViewError(
            f"{name}: tee to hog measured {view.rows_tee_to_hog:.1f} px, "
            f"outside the {PLAUSIBLE_ROWS[0]:.0f}-{PLAUSIBLE_ROWS[1]:.0f} px "
            f"every sheet falls in -- the fit found the wrong row")
    return view
```

- [ ] **Step 5: Run the slow test again**

Run: `.venv/bin/python -m pytest tests/test_sideview.py -m slow -q`
Expected: the plausibility test PASSES. Any sheet still failing is a real
finding — record which view and its measured `rows_tee_to_hog` before changing
anything else.

- [ ] **Step 6: If a sheet still fails, widen the hog search rather than the band**

The band is a safety rail and must not be loosened to make a test pass. The
tunable is where `_hog_row` starts looking: it currently begins 12 px below the
annulus's last green edge. On a thin plate that edge can be early. Change the
start to be measured from the *fitted tee* instead, which is robust to a ragged
annulus:

```python
    # a first pass for the tee, using a hog row taken far enough below the
    # house that no ring paint can be mistaken for it
    provisional = _hog_row(lum, int(edges[-1]) + 30, name)
```

Re-run Step 5. If a view still fails after this, stop and report it — that is
the video we said we would go looking for, and it changes the design rather
than the constants.

- [ ] **Step 7: Run the whole suite, fast and slow**

Run: `.venv/bin/python -m pytest -m "not slow" -q` then
`.venv/bin/python -m pytest -m slow -q -p no:randomly`
Expected: both PASS. Kill stray Chrome first — `ps -eo pid=,args= | awk '$2 ~ /(google-chrome|chrome)$/ {print $1}' | xargs -r kill` — the browser tests leave processes behind and the suite OOMs.

- [ ] **Step 8: Commit**

```bash
git add src/curling_score/geometry/sideview.py tests/test_sideview.py tests/conftest.py
git commit -m "$(cat <<'EOF'
sideview: refuse a calibration outside the band every sheet falls in

A side view that fails to calibrate costs that video its splits. A side
view that calibrates *wrongly* mistimes every crossing afterwards with
full confidence, which is worse, so the fit now checks its own answer
against the 78-90 px every one of the club's five sheets measures.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Find the stone and time the crossing

**Files:**
- Create: `src/curling_score/detect/longview.py`
- Test: `tests/test_longview.py`

**Interfaces:**
- Consumes: `sideview.SideView` from Task 2.
- Produces:
  - `longview.Crossing` — frozen dataclass with `t: float | None`, `reason: str`, `width_px: float`, and `__bool__` returning `t is not None`.
  - `longview.find_in_frames(frames, view, color, times) -> Crossing` — the pure core, taking decoded frames.
  - `longview.find_crossing(video, view, color, t0, t1, fps=30.0) -> Crossing` — seeks, decodes, delegates.
  - `longview.WINDOW_S = (2.0, 6.5)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_longview.py
"""Timing a stone across the hog line in a side view.

The overhead panel loses about 40% of throws before the hog line, under the
thrower and sweepers. From the end of the sheet the sweepers are beside the
stone rather than over it, so this view sees crossings the panel cannot --
but it also sees their boots, their brooms and the stones already in play,
so most of this module is about refusing.
"""

import numpy as np
import pytest

from curling_score.detect import longview
from curling_score.geometry import constants as C, sideview
from tests import synth

VIEW = sideview.SideView(rect=(0, 0, 810, 1080), tee_row=430.0, hog_row=520.0)


def travelling(speed_m_s=2.0, t_cross=0.5, span_s=1.0, fps=30.0, color="red",
               width_px=52, x=None, view=VIEW):
    """A stone crossing the hog line at ``speed_m_s``, timed to cross at
    ``t_cross``.

    Rows come from ``view.row_for`` rather than from a hand-picked pixel range,
    so a fixture cannot drift out of the speed gate's range without the test
    saying which bound it broke.
    """
    times = [i / fps for i in range(int(span_s * fps) + 1)]
    frames = [synth.side_view_stone(
                  synth.side_view(),
                  view.row_for(C.TEE_TO_HOGLINE_M + (t - t_cross) * speed_m_s),
                  width_px, color, x=x)
              for t in times]
    return frames, times


class TestFindingTheCrossing:
    def test_it_times_the_frame_the_stone_reaches_the_line(self):
        frames, times = travelling(speed_m_s=2.0, t_cross=0.5)
        got = longview.find_in_frames(frames, VIEW, "red", times)
        assert got, got.reason
        assert got.t == pytest.approx(0.5, abs=0.04)

    def test_it_interpolates_between_frames_rather_than_snapping(self):
        frames, times = travelling(speed_m_s=2.0, t_cross=0.517)
        got = longview.find_in_frames(frames, VIEW, "red", times)
        assert got.t == pytest.approx(0.517, abs=0.04)
        assert got.t not in times

    def test_it_finds_a_yellow_stone_too(self):
        frames, times = travelling(color="yellow")   # RGB fixture, see synth
        assert longview.find_in_frames(frames, VIEW, "yellow", times)


class TestWhatItRefuses:
    def test_a_stone_that_stops_short_of_the_line(self):
        # crosses at t = 3.0 s, well past the end of a 1 s window
        frames, times = travelling(speed_m_s=2.0, t_cross=3.0)
        got = longview.find_in_frames(frames, VIEW, "red", times)
        assert not got and "never reached" in got.reason

    def test_an_empty_stretch_of_ice(self):
        frames = [synth.side_view() for _ in range(20)]
        got = longview.find_in_frames(frames, VIEW, "red",
                                      [i / 30 for i in range(20)])
        assert not got and "no candidate" in got.reason

    def test_two_stones_crossing_in_the_same_window(self):
        """One is the throw and one is a rock already in play being cleared.
        Nothing here can tell which, so it refuses rather than pick."""
        left, times = travelling(speed_m_s=2.0, t_cross=0.5, x=250)
        right, _ = travelling(speed_m_s=2.0, t_cross=0.55, x=560)
        frames = [synth.side_view_stone(a, VIEW.hog_row, 0, "red")  # keep a copy
                  if False else a for a in left]
        for i, f in enumerate(right):
            frames[i] = np.where(f != synth.SIDE_ICE, f, frames[i])
        got = longview.find_in_frames(frames, VIEW, "red", times)
        assert not got and "two candidates" in got.reason

    def test_a_broom_pad_with_no_stone_under_it(self):
        frames, times = travelling(speed_m_s=2.0, t_cross=0.5)
        stripped = []
        for f, t in zip(frames, times):
            r = int(VIEW.row_for(C.TEE_TO_HOGLINE_M + (t - 0.5) * 2.0))
            g = f.copy()
            g[max(0, r - 22):r, 379:431] = synth.SIDE_ICE   # erase the granite
            stripped.append(g)
        got = longview.find_in_frames(stripped, VIEW, "red", times)
        assert not got and "no candidate" in got.reason

    def test_a_blob_far_too_wide_to_be_a_stone(self):
        frames, times = travelling(speed_m_s=2.0, t_cross=0.5, width_px=160)
        assert not longview.find_in_frames(frames, VIEW, "red", times)

    def test_a_stone_crawling_too_slowly_to_be_a_delivery(self):
        """A stone being nudged aside by a sweeper, or one already at rest that
        the tracker drifted onto. A delivery crosses its hog line between
        1.2 and 3.2 m/s -- the range the 27 hand marks imply."""
        frames, times = travelling(speed_m_s=0.3, t_cross=1.0, span_s=2.0)
        got = longview.find_in_frames(frames, VIEW, "red", times)
        assert not got and "speed" in got.reason

    def test_a_blur_far_too_fast_to_be_a_stone(self):
        frames, times = travelling(speed_m_s=6.0, t_cross=0.5)
        got = longview.find_in_frames(frames, VIEW, "red", times)
        assert not got and "speed" in got.reason

    def test_a_stone_drifting_the_wrong_way(self):
        frames, times = travelling(speed_m_s=-2.0, t_cross=0.5)
        got = longview.find_in_frames(frames, VIEW, "red", times)
        assert not got

    def test_the_window_covers_every_crossing_that_was_marked(self):
        """Marked by hand on 27 deliveries: release + 2.83 s to + 5.43 s."""
        assert longview.WINDOW_S[0] <= 2.83
        assert longview.WINDOW_S[1] >= 5.43
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_longview.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'curling_score.detect.longview'`

- [ ] **Step 3: Write the implementation**

```python
# src/curling_score/detect/longview.py
"""Time a delivery across the throwing end's hog line, in a side view.

The overhead panel loses the throw before the hog line on about 40% of
deliveries -- it looks straight down at a stone with the thrower and sweepers
standing over it. The camera at the far end is 22 m away and level with the
ice, so the sweepers are beside the stone rather than on top of it, and the
line it crosses is painted and fixed.

Most of this module is refusal. The same view shows boots, broom pads and the
stones already in play, and a mistimed split is worse than a missing one, so a
crossing has to survive every gate below or it does not exist.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

import numpy as np

# Measured by hand on 27 deliveries (``datasets/hogmarks``): a stone crosses
# its hog line between 2.83 s and 5.43 s after the panel first sees it leave
# the hack. The window is wider at both ends than every mark observed.
WINDOW_S = (2.0, 6.5)

# The stone reads about 52 px across at the hog line. These bounds reject a
# broom pad and a sweeper's shadow while keeping every stone seen so far.
WIDTH_BOUNDS = (0.5, 1.6)
STONE_WIDTH_AT_HOG_PX = 52.0
# A delivery crosses its hog line between 1.2 and 3.2 m/s -- the range the 27
# hand-marked crossings imply. Slower than that is a stone being nudged aside,
# or a tracker that has drifted onto one already at rest.
SPEED_BOUNDS_M_S = (1.2, 3.2)
# How much darker than the ice granite is. The painted hog line is a dip of
# about 25 levels, so the threshold has to sit well below it or the line gets
# picked up as the stone's own edge.
_BODY_DARKER_THAN_ICE = 45
_MIN_SAMPLES = 6


@dataclass(frozen=True)
class Crossing:
    """When a stone crossed the line, or why we will not say."""

    t: float | None
    reason: str
    width_px: float = 0.0

    def __bool__(self) -> bool:
        return self.t is not None


def _colour_mask(win, color):
    r, g, b = win[:, :, 0], win[:, :, 1], win[:, :, 2]
    if color == "red":
        return (r - np.maximum(g, b) > 28) & (r > 90)
    return (np.minimum(r, g) - b > 40) & (r > 120) & (g > 110)


def _runs(flags, min_len=1):
    out, start = [], None
    for i, on in enumerate(list(flags) + [False]):
        if on and start is None:
            start = i
        elif not on and start is not None:
            if i - start >= min_len:
                out.append((start, i))
            start = None
    return out


def _candidates(win, color, expect_px):
    """Every coloured blob with a granite body of the right width under it."""
    mask = _colour_mask(win, color)
    if mask.sum() < 20:
        return []
    grey = win.mean(axis=2)
    ice = np.percentile(grey, 90)
    out = []
    for x0, x1 in _runs(mask.sum(axis=0) > 0):
        sub = mask[:, x0:x1]
        if sub.sum() < 20:
            continue
        hy = int(np.median(np.nonzero(sub)[0]))
        cx = (x0 + x1) // 2
        half = int(expect_px * 0.9)
        band = grey[:, max(0, cx - half):cx + half]
        wide = (band < ice - _BODY_DARKER_THAN_ICE).sum(axis=1)
        rows = [y for y in range(hy, min(hy + int(expect_px), band.shape[0]))
                if wide[y] > expect_px * 0.45]
        if not rows:
            continue
        body = float(max(wide[y] for y in rows))
        if not WIDTH_BOUNDS[0] * expect_px <= body <= WIDTH_BOUNDS[1] * expect_px:
            continue
        out.append((float(cx), float(max(rows)), body))
    return out


def find_in_frames(frames, view, color, times) -> Crossing:
    """Time the crossing of ``view.hog_row`` in already-decoded frames."""
    x, y, _w, _h = view.rect
    tracks: dict[int, list] = {}
    for frame, t in zip(frames, times):
        win = np.asarray(frame, dtype=np.float32)[y:, x:]
        for cx, edge, body in _candidates(win, color, STONE_WIDTH_AT_HOG_PX):
            key = int(cx // 120)          # a stone never moves 120 px sideways
            tracks.setdefault(key, []).append((t, edge, body))

    moving = [tr for tr in tracks.values()
              if len(tr) >= _MIN_SAMPLES and tr[-1][1] > tr[0][1]]
    if not moving:
        return Crossing(None, "no candidate that could be a stone in flight")
    crossed = [tr for tr in moving
               if tr[0][1] <= view.hog_row <= tr[-1][1]]
    if len(crossed) > 1:
        return Crossing(None, f"two candidates crossed the line ({len(crossed)})")
    if not crossed:
        return Crossing(None, "the stone never reached the line")
    track = crossed[0]
    if any(b - a < -1.0 for (_, a, _), (_, b, _) in zip(track, track[1:])):
        return Crossing(None, "the candidate did not travel steadily")
    span = track[-1][0] - track[0][0]
    if span > 0:
        metres = abs(view.metres_at(track[-1][1]) - view.metres_at(track[0][1]))
        speed = metres / span
        if not SPEED_BOUNDS_M_S[0] <= speed <= SPEED_BOUNDS_M_S[1]:
            return Crossing(None, f"speed {speed:.2f} m/s is not a delivery")
    for (t0, r0, _), (t1, r1, _) in zip(track, track[1:]):
        if r0 <= view.hog_row <= r1 and r1 != r0:
            frac = (view.hog_row - r0) / (r1 - r0)
            return Crossing(t0 + frac * (t1 - t0), "ok",
                            width_px=float(np.median([b for _, _, b in track])))
    return Crossing(None, "the stone never reached the line")


def decode(video, rect, t0: float, t1: float, fps: float = 30.0):
    """Frames of one window, cropped to the side view. About 0.25 s a call."""
    x, y, w, h = rect
    raw = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{t0}",
         "-i", str(video), "-t", f"{t1 - t0 + 0.05}",
         "-vf", f"crop={w}:{h}:{x}:{y},fps={fps}",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True).stdout
    frames = np.frombuffer(raw, dtype=np.uint8).reshape(-1, h, w, 3)
    return frames, [t0 + i / fps for i in range(frames.shape[0])]


def find_crossing(video, view, color: str, t0: float, t1: float,
                  fps: float = 30.0) -> Crossing:
    """Seek, decode and time one crossing."""
    frames, times = decode(video, view.rect, t0, t1, fps)
    if not len(frames):
        return Crossing(None, "no frames decoded")
    shifted = type(view)(rect=(0, 0, view.rect[2], view.rect[3]),
                         tee_row=view.tee_row, hog_row=view.hog_row, d_m=view.d_m)
    return find_in_frames(frames, shifted, color, times)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_longview.py -q`
Expected: PASS, 11 tests. If a refusal test fails because the synthetic stone
does not resemble a real one closely enough, fix the *fixture*, not the
thresholds — the thresholds are measured and Task 5 is what checks them.

- [ ] **Step 5: Run the whole fast suite**

Run: `.venv/bin/python -m pytest -m "not slow" -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/curling_score/detect/longview.py tests/test_longview.py
git commit -m "$(cat <<'EOF'
longview: time a delivery across the hog line from the end of the sheet

The overhead panel loses the throw before the hog line on 40% of
deliveries, under the thrower and sweepers. The camera at the far end
sees the sweepers beside the stone instead. It also sees their boots and
broom pads, so a coloured blob only counts when there is granite of the
right width underneath it, and two candidates refuse rather than guess.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Check it against the hand-marked crossings

**Files:**
- Modify: `tests/test_longview.py`

**Interfaces:**
- Consumes: `longview.find_crossing`, `sideview.solve`, `datasets/hogmarks/VXU9xwmugRg.json`, `conftest.primary_video`.

`datasets/hogmarks/VXU9xwmugRg.json` holds 27 crossings a person marked against
the paint, with the release track the overhead panel reported alongside each.
Its shape:

```json
{"video_id": "VXU9xwmugRg",
 "ends": [{"end": 5, "throwing_panel": "top", "side_view": "left",
           "marks": [{"color": "yellow", "release_t_s": 3634.8,
                      "hog_crossing_s": 3638.767,
                      "release_track": [[t, x, y], ...]}]}]}
```

- [ ] **Step 1: Write the failing test**

```python
# tests/test_longview.py -- append

import json
from pathlib import Path

MARKS = Path(__file__).resolve().parents[1] / "datasets/hogmarks/VXU9xwmugRg.json"

# A stone moves about 1.1 px per frame at the hog line and reads ~52 px across,
# so a detector that finds the right object should land inside a couple of
# frames of where a person put it. Anything looser is finding something else.
TOLERANCE_S = 0.15


@pytest.mark.slow
class TestAgainstHandMarkedCrossings:
    """The 27 marks are the ground truth this detector answers to."""

    def _views(self, primary_video):
        from curling_score import analyze as A
        from curling_score.geometry import layout
        from curling_score.ingest import frames as F

        calib = F.sample_keyframes(primary_video, count=A.CALIB_FRAMES,
                                   stride=A.CALIB_STRIDE)
        panels = layout.detect_panels(calib)
        plate = np.median(np.stack([f.astype(np.float32) for f in calib]), axis=0)
        h, w = plate.shape[:2]
        rects = sideview.locate(panels, width=w, height=h)
        return {n: sideview.solve(plate, r, name=n) for n, r in rects.items()}

    def test_it_lands_where_a_person_marked_the_paint(self, primary_video):
        doc = json.loads(MARKS.read_text())
        views = self._views(primary_video)
        errors, refused = [], []
        for end in doc["ends"]:
            view = views[end["side_view"]]
            for m in end["marks"]:
                lo = m["release_t_s"] + longview.WINDOW_S[0]
                hi = m["release_t_s"] + longview.WINDOW_S[1]
                got = longview.find_crossing(primary_video, view, m["color"], lo, hi)
                if not got:
                    refused.append((m["release_t_s"], got.reason))
                    continue
                errors.append(got.t - m["hog_crossing_s"])
        assert errors, "every crossing was refused"
        worst = max(abs(e) for e in errors)
        assert worst <= TOLERANCE_S, (
            f"worst error {worst:.3f} s over {len(errors)} crossings; "
            f"{len(refused)} refused: {refused}")

    def test_it_finds_most_of_them(self, primary_video):
        """Coverage is the point of the whole exercise. The panel manages 46%."""
        doc = json.loads(MARKS.read_text())
        views = self._views(primary_video)
        found = total = 0
        for end in doc["ends"]:
            view = views[end["side_view"]]
            for m in end["marks"]:
                total += 1
                lo = m["release_t_s"] + longview.WINDOW_S[0]
                hi = m["release_t_s"] + longview.WINDOW_S[1]
                found += bool(longview.find_crossing(primary_video, view,
                                                     m["color"], lo, hi))
        assert found / total >= 0.85, f"found {found} of {total}"
```

- [ ] **Step 2: Run it**

Run: `.venv/bin/python -m pytest tests/test_longview.py -m slow -q`
Expected: it will either pass or report a concrete worst error and a list of
refusals. Both are useful; do not adjust `TOLERANCE_S` to make it pass.

- [ ] **Step 3: Fix what the failures actually show**

Work from the refusal reasons, in this order:

- `"no candidate that could be a stone in flight"` — the colour mask missed the
  handle. The known cause is a thrower whose hand still covers it on a long
  slide. Widen `_MIN_SAMPLES` tolerance by allowing a track to start later in
  the window, not by loosening the colour bounds.
- `"two candidates"` — the second is usually a stone already in play. Prefer the
  candidate whose *body width* best matches `STONE_WIDTH_AT_HOG_PX` at the row
  it crosses, and refuse only when two are within 15% of each other.
- A systematic offset in the errors rather than scatter — the detector is timing
  a different feature of the stone than a person did. A person marked the
  *leading edge* on the line's near edge; `_candidates` returns the lowest dark
  row, which includes the contact shadow. Subtract the median offset as a named
  constant with the measurement in its comment.

- [ ] **Step 4: Re-run until both tests pass**

Run: `.venv/bin/python -m pytest tests/test_longview.py -m slow -q`

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/detect/longview.py tests/test_longview.py
git commit -m "$(cat <<'EOF'
longview: answer to the hand-marked crossings

27 crossings marked against the paint by a person are the only ground
truth here that is not another machine's opinion. The detector now has to
land within 0.15 s of every one of them and find at least 85%.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Attach a crossing to every shot

**Files:**
- Create: `src/curling_score/game/hogtime.py`
- Test: `tests/test_hogtime.py`

**Interfaces:**
- Consumes: `longview.find_crossing`, `longview.WINDOW_S`, `sideview.SideView`.
- Produces:
  - `hogtime.CAMERA_FOR = {"top": "left", "bottom": "right"}`
  - `hogtime.time_hog_crossings(shots, video, view, *, find=longview.find_crossing) -> None` — attaches `shot.t_hog_s`.
  - `hogtime.crossing(shot) -> float | None` (deliberately not `hog_crossing` -- `split.hog_crossing` already means something else, and takes a track rather than a shot).
  - `hogtime.ARRIVAL_LOOKBACK_S = (8.0, 20.0)`.

The `find` parameter exists so tests can inject a stub instead of decoding video.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_hogtime.py
"""Giving each shot the moment it crossed the throwing end's hog line.

Runs after the rules have settled the shot list, and like
``thinking.time_shots`` it can only attach a time to a rock already in it --
nothing here may add, drop or renumber a shot.
"""

import pytest

from curling_score.detect.delivery import Delivery
from curling_score.detect.longview import Crossing
from curling_score.detect.release import Release
from curling_score.game import hogtime, shots as S
from curling_score.detect.rocks import Detection


def det(color, x, y):
    return Detection(color=color, x_m=x, y_m=y, x_px=0.0, y_px=0.0,
                     area_px=140.0, confidence=0.9)


def a_shot(n, color, t_rest, t_rel=None, t_enter=None):
    dv = Delivery(color=color, t_enter=t_enter if t_enter is not None else t_rest - 8,
                  t_rest=t_rest, entry_y_m=4.55, rest_x_m=0.0, rest_y_m=1.0,
                  travel_m=3.5, track=((t_rest - 8, 0.0, 4.55), (t_rest, 0.0, 1.0)))
    rel = None if t_rel is None else Release(
        color=color, t=t_rel, y_exit_m=3.0, speed_m_s=2.0,
        track=((t_rel, 0.05, -2.0), (t_rel + 2, 0.05, 2.0)))
    return S.Shot(number=n, color=color, stones=[det(color, 0.1, 0.2)],
                  t_rest_s=t_rest, delivery=dv, release=rel)


class Recorder:
    """Stands in for the detector, and remembers what it was asked."""

    def __init__(self, answer=None):
        self.calls = []
        self.answer = answer or (lambda c, lo, hi: Crossing(lo + 3.0, "ok"))

    def __call__(self, video, view, color, t0, t1, fps=30.0):
        self.calls.append((color, t0, t1))
        return self.answer(color, t0, t1)


class TestTiming:
    def test_a_shot_with_a_release_is_searched_around_it(self):
        shots = [a_shot(1, "red", 100.0, t_rel=80.0)]
        find = Recorder()
        hogtime.time_hog_crossings(shots, "v.mp4", object(), find=find)
        assert find.calls == [("red", 82.0, 86.5)]
        assert hogtime.crossing(shots[0]) == pytest.approx(85.0)

    def test_a_shot_with_no_release_is_searched_back_from_its_arrival(self):
        shots = [a_shot(1, "red", 100.0, t_enter=92.0)]
        find = Recorder()
        hogtime.time_hog_crossings(shots, "v.mp4", object(), find=find)
        (color, t0, t1), = find.calls
        assert color == "red"
        assert (t0, t1) == (92.0 - 20.0, 92.0 - 8.0)

    def test_a_refusal_leaves_the_shot_untimed_rather_than_guessing(self):
        shots = [a_shot(1, "red", 100.0, t_rel=80.0)]
        hogtime.time_hog_crossings(
            shots, "v.mp4", object(),
            find=Recorder(lambda c, lo, hi: Crossing(None, "two candidates")))
        assert hogtime.crossing(shots[0]) is None

    def test_a_missing_shot_is_not_searched_for_at_all(self):
        shots = [a_shot(1, "red", 100.0, t_rel=80.0)]
        shots[0].missing = True
        find = Recorder()
        hogtime.time_hog_crossings(shots, "v.mp4", object(), find=find)
        assert find.calls == []

    def test_it_never_changes_the_shot_list(self):
        shots = [a_shot(1, "red", 100.0, t_rel=80.0),
                 a_shot(2, "yellow", 160.0, t_rel=140.0)]
        before = [(s.number, s.color) for s in shots]
        hogtime.time_hog_crossings(shots, "v.mp4", object(), find=Recorder())
        assert [(s.number, s.color) for s in shots] == before
        assert len(shots) == 2


class TestWhichCamera:
    def test_the_camera_at_the_far_end_watches_the_throwing_house(self):
        assert hogtime.CAMERA_FOR["top"] == "left"
        assert hogtime.CAMERA_FOR["bottom"] == "right"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_hogtime.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'curling_score.game.hogtime'`

- [ ] **Step 3: Write the implementation**

```python
# src/curling_score/game/hogtime.py
"""When each stone crossed the throwing end's hog line, read from a side view.

This is the throwing end only. The target end's crossing comes from the
playing panel's own tripwire and is reliable -- one miss in 48 -- whereas the
throwing end's fails on 40% of shots because the thrower and sweepers stand
between the overhead camera and the stone.

Runs after the rules have settled the shot list, and like
``thinking.time_shots`` it may only attach a time to a rock already in it.
Nothing here can add, drop or renumber a shot.
"""

from curling_score.detect import longview

# Which side camera watches a given panel's hog line: the one at the *other*
# end, looking back. A camera never sees its own hog line -- that sits about
# half a metre beneath it, out of frame. Same alternation as ``OTHER_HOUSE``.
CAMERA_FOR = {"top": "left", "bottom": "right"}

# For a shot whose throw was never seen leaving the house, the arrival is the
# only anchor. Measured across three ends, a stone reaches the far panel 12-21 s
# after its release and crosses the hog line 3-5 s after that, which puts the
# crossing 8-20 s before the arrival. A wide window, so the refusals matter
# more here than anywhere else.
ARRIVAL_LOOKBACK_S = (8.0, 20.0)


def time_hog_crossings(shots, video, view, *, find=longview.find_crossing) -> None:
    """Give every shot its throwing-end hog crossing, in place."""
    for shot in shots:
        if getattr(shot, "missing", False):
            continue
        release = getattr(shot, "release", None)
        if release is not None:
            t0 = release.t + longview.WINDOW_S[0]
            t1 = release.t + longview.WINDOW_S[1]
        else:
            delivery = getattr(shot, "delivery", None)
            if delivery is None:
                continue
            t0 = delivery.t_enter - ARRIVAL_LOOKBACK_S[1]
            t1 = delivery.t_enter - ARRIVAL_LOOKBACK_S[0]
        got = find(video, view, shot.color, t0, t1)
        if got:
            shot.t_hog_s = got.t


def crossing(shot):
    """When this shot crossed the throwing end's hog line, if it was seen to.

    Not named ``hog_crossing``: ``split.hog_crossing`` already means the panel's
    own tripwire and takes a track, and two functions of that name timing the
    same line from different cameras is exactly the confusion to avoid.
    """
    return getattr(shot, "t_hog_s", None)
```

If `S.Shot` is a frozen dataclass, `shot.t_hog_s = ...` will raise; check
`src/curling_score/game/shots.py` and follow whatever `thinking.time_shots`
does to set `shot.tee_s` — mirror that mechanism exactly rather than inventing
a second one.

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_hogtime.py -q`
Expected: PASS, 6 tests

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/game/hogtime.py tests/test_hogtime.py
git commit -m "$(cat <<'EOF'
hogtime: give each shot its throwing-end crossing from the side view

Same contract as thinking.time_shots: it runs after the rules have
settled the shot list and may only time a rock already in it. A shot with
no release is searched back from its arrival instead, over a window wide
enough that the refusals carry most of the weight.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Make the split read the side view, and publish the calibration

**Files:**
- Modify: `src/curling_score/game/split.py`
- Modify: `src/curling_score/analyze.py`
- Modify: `src/curling_score/timeline.py`
- Modify: `tests/test_split.py`
- Modify: `src/curling_score/version.py`

**Interfaces:**
- Consumes: `hogtime.crossing(shot)`, `hogtime.time_hog_crossings`, `sideview.locate`, `sideview.solve`.
- Produces: `split.long_split(release, delivery, *, t_hog=None) -> Split | None`; `split.CROSS_CHECK_S = 0.25`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_split.py -- append

class TestTheSideViewIsTheSourceForTheThrowingEnd:
    """The panel keeps timing the target end, and cross-checks the other one."""

    def test_the_throwing_end_comes_from_the_side_view_when_given(self):
        r = release_at(t0=0.0, speed=2.0)
        d = delivery_at(t0=40.0, speed=0.8)
        panel = split.hog_crossing(r.track)
        s = split.long_split(r, d, t_hog=panel + 0.1)
        assert s.t_start == pytest.approx(panel + 0.1, abs=1e-9)

    def test_without_one_there_is_no_split_even_if_the_panel_saw_it(self):
        """One method per game: a shot the side view refused has no split,
        rather than a second-best number that cannot be compared with its
        neighbours."""
        r = release_at(t0=0.0, speed=2.0)
        assert split.hog_crossing(r.track) is not None
        assert split.long_split(r, delivery_at(t0=40.0), t_hog=None) is None

    def test_the_two_disagreeing_refuses_both(self):
        r = release_at(t0=0.0, speed=2.0)
        panel = split.hog_crossing(r.track)
        d = delivery_at(t0=40.0, speed=0.8)
        assert split.long_split(r, d, t_hog=panel + split.CROSS_CHECK_S * 3) is None

    def test_a_disagreement_inside_the_tolerance_is_kept(self):
        r = release_at(t0=0.0, speed=2.0)
        panel = split.hog_crossing(r.track)
        d = delivery_at(t0=40.0, speed=0.8)
        assert split.long_split(r, d, t_hog=panel + split.CROSS_CHECK_S / 2)

    def test_a_throw_the_panel_never_saw_still_gets_a_split(self):
        """This is the whole point: 40% of throws are lost before the line."""
        r = release_at(t0=0.0, speed=2.0, y1=2.5)     # lost well short
        assert split.hog_crossing(r.track) is None
        s = split.long_split(r, delivery_at(t0=40.0), t_hog=5.0)
        assert s is not None and s.t_start == pytest.approx(5.0)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_split.py -q`
Expected: FAIL — `long_split() got an unexpected keyword argument 't_hog'`

- [ ] **Step 3: Change `split.long_split`**

Add the constant beside `SPEED_TOLERANCE`:

```python
# How far the side view and the panel may disagree about the same crossing
# before both are disbelieved. They are independent cameras timing one painted
# line, so a real disagreement means one of them found the wrong object and
# nothing here can say which.
CROSS_CHECK_S = 0.25
```

and rewrite the front of `long_split`:

```python
def long_split(release, delivery, *, t_hog=None) -> Split | None:
    """The split for one shot, or None when either end could not be timed.

    The throwing end comes from the side view (``game/hogtime.py``): the
    overhead panel loses about 40% of throws before the hog line. The panel's
    own tripwire is kept only to check that answer -- never to stand in for it,
    because two methods inside one game are not comparable with each other.
    """
    if release is None or delivery is None or t_hog is None:
        return None
    panel = hog_crossing(getattr(release, "track", ()))
    if panel is not None and abs(panel - t_hog) > CROSS_CHECK_S:
        return None
    start = t_hog
    end = crossing_time(getattr(delivery, "track", ()) or (), HOG_APPARENT_Y_M)
    if end is None or end <= start:
        return None
    ...
```

Leave the speed check below it unchanged.

- [ ] **Step 4: Run the split tests**

Run: `.venv/bin/python -m pytest tests/test_split.py -q`
Expected: PASS. Existing tests that call `long_split(r, d)` with no `t_hog` now
return `None`; update each to pass `t_hog=split.hog_crossing(r.track)`, which
preserves what they were testing.

- [ ] **Step 5: Wire it into `analyze.py`**

In the per-end loop, after `fit_end` and beside `thinking.time_shots`:

```python
            if sideviews is not None:
                hogtime.time_hog_crossings(
                    built, path, sideviews[hogtime.CAMERA_FOR[OTHER_HOUSE[end.house]]])
```

and once, beside `profile.calibrate_panels`:

```python
    # The two wide side views. They watch the far end's hog line, which the
    # overhead panel loses on about 40% of throws, and they are read straight
    # from the original -- like the scoreboard, and for the same reason.
    sideviews = None
    if not skip_longview:
        h, w = calib_frames[0].shape[:2]
        plate = np.median(
            np.stack([f.astype("float32") for f in calib_frames]), axis=0)
        try:
            rects = sideview.locate(panels, width=w, height=h)
            sideviews = {n: sideview.solve(plate, r, name=n)
                         for n, r in rects.items()}
        except sideview.SideViewError as exc:
            progress(f"side views unusable, so this video has no splits: {exc}")
```

Add `skip_longview: bool = False` to `analyze`'s signature, beside
`skip_scoreboard`, documented as being for callers that do not keep the
original file.

- [ ] **Step 6: Publish the calibration and pass the crossing to the timeline**

In `timeline.py`, change the call:

```python
        sp = split.long_split(rel, dv, t_hog=hogtime.crossing(s))
```

In `analyze.py`, where the `calibration` block is built (around line 348), add
the side views beside `top` and `bottom`:

```python
        **{name: {"rect": list(v.rect), "tee_row": round(v.tee_row, 2),
                  "hog_row": round(v.hog_row, 2)}
           for name, v in (sideviews or {}).items()},
```

- [ ] **Step 7: Bump the pipeline version**

`src/curling_score/version.py`: `PIPELINE_VERSION = "2026.09.11"`.

- [ ] **Step 8: Run everything**

Run: `.venv/bin/python -m pytest -m "not slow" -q`, then `cd frontend && npx eslint .`
Expected: both clean.

- [ ] **Step 9: Commit**

```bash
git add src/curling_score/game/split.py src/curling_score/analyze.py \
        src/curling_score/timeline.py src/curling_score/version.py tests/test_split.py
git commit -m "$(cat <<'EOF'
split: take the throwing end from the side view, not the panel

The panel loses 40% of throws before the hog line, which is most of why
the split covered 46% of shots. The side view at the far end watches that
line with the sweepers beside the stone rather than over it.

The panel's tripwire stays as a cross-check and nothing more: where both
fired they must agree within 0.25 s or neither is published, and where
the side view refuses there is no split at all. Two methods inside one
game would not be comparable with each other, which is what a charter
does with these numbers.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Measure it across every video, and decide

**Files:**
- Create: `scripts/split_coverage.py`

**Interfaces:**
- Consumes: everything above.
- Produces: a report — per video and per sheet, the split coverage, agreement with the panel tripwire on the overlap, and agreement with the 27 hand marks.

This task produces a measurement, not a feature. Its output decides whether the
classical detector ships or whether `detect/longview.py` gets replaced by a
trained one — the seam is one file wide either way.

- [ ] **Step 1: Write the report script**

```python
#!/usr/bin/env python
"""How much of a game the long split now covers, and whether to believe it.

Runs the whole pipeline over every cached video and reports three things per
sheet: coverage against the 46% the overhead panels managed alone, agreement
with the panel tripwire wherever both fired, and -- on the reference VOD --
agreement with the crossings marked by hand in ``datasets/hogmarks``.

    python scripts/split_coverage.py --cache-root ~/.cache/curling_score
"""
```

The body reuses `scripts/replay_end.py`'s setup helpers (`setups_for`), adds
`sideview.locate` / `sideview.solve` on a plate built from `calib_frames`, and
for every end of every video reports:

- `splits / shots`, and the same figure with `t_hog` forced to `None` so the
  panel-only baseline is measured on identical inputs rather than quoted from
  memory
- for shots where both fired: median and worst `|t_hog - panel|`
- refusal reasons, counted

- [ ] **Step 2: Run it over every cached video**

Run: `.venv/bin/python scripts/split_coverage.py --cache-root ~/.cache/curling_score`

- [ ] **Step 3: Compare against the gate**

Ship the classical detector when all three hold:

| | gate |
|---|---|
| coverage | >= 90% of shots, against 46% today |
| panel agreement | no `\|t_hog - panel\|` above 0.25 s on the overlap |
| hand marks | every one matched within 0.1 s |

If coverage misses but agreement holds, the detector is finding the right
object and losing some — tune the refusals. If agreement misses, it is finding
the wrong object and the answer is a trained detector in
`detect/longview.py`; nothing outside that file changes.

- [ ] **Step 4: Record the result**

Write the measured numbers into the spec's "Known risks" section, replacing the
"exercised on one video" caveat with what was actually found.

- [ ] **Step 5: Commit**

```bash
git add scripts/split_coverage.py docs/superpowers/specs/2026-09-14-long-camera-hog-crossing-design.md
git commit -m "$(cat <<'EOF'
scripts: measure split coverage and whether the side view can be believed

The panel-only baseline is recomputed on the same inputs rather than
quoted, so the comparison is honest about what changed.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## After the plan

Reprocessing is what turns any of this into something a reader sees: every
number lives in the stored timeline, and the viewer only reads it. All eight
catalogued videos need a warm pass (~4 min each) once the worker and API are
deployed. None of the files in the detection cache key were touched, so
detection stays cached throughout.
