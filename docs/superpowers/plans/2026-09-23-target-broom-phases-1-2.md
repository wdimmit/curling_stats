# Target broom, phases 1–2: lateral side-view calibration and a SAM broom set

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the long camera a paint-only lateral calibration, so a pixel in
the far house becomes a house position. Then build the tooling and frames for
labelling broom heads with SAM, ending with a served round-1 page for VXU9.

**Architecture:**
- `SideView` gains two optional fields, `centre_col` and `lat_px_per_m_at_tee`,
  plus `to_house` and `to_image`. A new `solve_lateral` fills them from the
  12-ft and 8-ft ring sides on the rows around the tee.
- The fields survive every place that rebuilds a view, and are published in the
  timeline's calibration block. Nothing that times a hog crossing changes.
- The SAM box editor and server learn a `broom` kind.
- A harvest script cuts the destination-facing camera at `t_tee − 1.0` and
  `t_tee − 0.3`.

**Tech Stack:** Python 3, numpy, OpenCV, ffmpeg (via `detect/longview.decode`),
ultralytics SAM 2.1, pytest.

**Spec:** `docs/superpowers/specs/2026-09-23-target-broom-design.md`, including
its "Phase 0 findings". Phases 3–5 (training, the pipeline pass, the viewer)
get their own plan once round-1 labels exist.

## Global Constraints

- **The detection cache must not be invalidated.** Do not edit
  `src/curling_score/detect/rocks.py`, `src/curling_score/detect/yolo.py` or
  `src/curling_score/geometry/calibrate.py`.
- `sideview.solve()` and the hog tripwire are untouched. Lateral calibration is
  additive, and a lateral failure never fails a run.
- House axes are the timeline's: origin at the tee, **+y up-sheet toward the
  thrower** (toward this camera, so a larger row), and **+x the thrower's right,
  which is image right** (the view is not mirrored).
- A point's ground position is read at the **bottom** of its box, never its
  centre: height is not foreshortened the way depth is.
- The default stone editor page must render **byte-for-byte as today**.
- Data never goes in `/tmp`. Frames go under `~/curling-work/broom/`, and every
  exported edits file is committed to `datasets/broom/edits/` straight away.
- Another session shares this checkout. **Stage only this plan's files** (`git
  add <paths>`, never `-A`), and commit on `main` as the repo does.
- The full test suite OOMs on this box. Run only the files named in each task,
  with `./.venv/bin/pytest <files> -q`.
- Commit messages follow the repo's `area: sentence` style and end with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

- **The adjacent sheet's ring peeking in at the view's edge.** On VXU9's left
  view, green crossings appear at cols 790–797. It must not be taken for this
  house's far band (Task 2 test).
- **A player standing on the ring's side in some calibration frames.** The
  median plate usually removes them. If a dark bar survives across a few of the
  sampled rows, the fit should use the other rows and not refuse (Task 2 test).
- **A far band too faint to see**, below the band floor. It must refuse with
  `SideViewError`, never fit the near band against noise (Task 2 test).
- **Older JSON without the new keys** (`datasets/ds13/sideviews.json`,
  2026.09.22 timelines). It must load, with the fields `None` (Task 3 test).
- **A shot whose window falls outside the video** (`t_tee − 1.0 < 0`, or past the
  end). ffmpeg returns no frames, and the harvest must skip that frame, not
  crash (Task 8 code path, checked in its run step).

---

### Task 1: `SideView` lateral fields and the pixel ↔ house map

**Files:**
- Modify: `src/curling_score/geometry/sideview.py`, the `SideView` dataclass
  (~L74-119)
- Test: `tests/test_sideview.py` (new class `TestLateralMap`)

**Interfaces:**
- Produces: `SideView.centre_col: float | None = None`,
  `SideView.lat_px_per_m_at_tee: float | None = None`,
  `SideView.has_lateral -> bool`, `SideView.lateral_px_per_m(row) -> float`,
  `SideView.to_house(col, row) -> tuple[float, float]` (x_m, y_m), and
  `SideView.to_image(x_m, y_m) -> tuple[float, float]` (col, row). Columns are
  the view's own, not the composite's.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sideview.py`:

```python
class TestLateralMap:
    """Across the sheet. A row is a line of constant depth -- the hog line is
    flat, which the depth fit already relies on -- so a metre across spans a
    number of pixels that scales with the same 1/(d - x) as the rows do."""

    V = sideview.SideView(rect=(0, 0, 810, 1080), tee_row=430.0, hog_row=520.0,
                          centre_col=390.0, lat_px_per_m_at_tee=148.0)

    def test_the_tee_is_the_origin(self):
        assert self.V.to_house(390.0, 430.0) == pytest.approx((0.0, 0.0), abs=1e-9)

    def test_the_12ft_ring_s_sides_on_the_tee_row_are_its_radius(self):
        for sign in (-1, 1):
            x, y = self.V.to_house(390.0 + sign * C.R_12FT_M * 148.0, 430.0)
            assert x == pytest.approx(sign * C.R_12FT_M, abs=1e-9)
            assert y == pytest.approx(0.0, abs=1e-9)

    def test_image_right_is_the_thrower_s_right(self):
        assert self.V.to_house(500.0, 430.0)[0] > 0

    def test_nearer_the_camera_is_up_sheet(self):
        assert self.V.to_house(390.0, 450.0)[1] > 0

    def test_it_round_trips(self):
        for x, y in ((-1.5, -1.2), (0.7, 0.4), (1.9, 2.5), (0.0, -1.829)):
            col, row = self.V.to_image(x, y)
            assert self.V.to_house(col, row) == pytest.approx((x, y), abs=1e-9)

    def test_a_metre_across_shrinks_with_distance_as_a_stone_does(self):
        for y in (-1.829, 0.0, 1.829, 3.0):
            row = self.V.row_for(y)
            assert (self.V.lateral_px_per_m(row) / 148.0 == pytest.approx(
                self.V.stone_width_at(row, 52.0)
                / self.V.stone_width_at(430.0, 52.0)))

    def test_a_view_without_lateral_calibration_says_so(self):
        v = sideview.SideView(rect=(0, 0, 810, 1080), tee_row=430.0, hog_row=520.0)
        assert not v.has_lateral
        with pytest.raises(sideview.SideViewError):
            v.to_house(390.0, 430.0)
        with pytest.raises(sideview.SideViewError):
            v.to_image(0.0, 0.0)
```

- [ ] **Step 2: Run them to see them fail**

Run: `./.venv/bin/pytest tests/test_sideview.py -q -k TestLateralMap`
Expected: FAIL. `SideView.__init__() got an unexpected keyword argument 'centre_col'`.

- [ ] **Step 3: Implement**

In `SideView`, after `d_m`:

```python
    # Where the far house's centre sits on the tee row, in the view's own
    # columns, and how many pixels one metre across the sheet spans there. Both
    # come from `solve_lateral`. A view without them can still time a hog
    # crossing -- that is depth alone -- but cannot place anything across the
    # sheet.
    centre_col: float | None = None
    lat_px_per_m_at_tee: float | None = None

    @property
    def has_lateral(self) -> bool:
        return self.centre_col is not None and self.lat_px_per_m_at_tee is not None

    def lateral_px_per_m(self, row: float) -> float:
        """Pixels per metre across the sheet on ``row``.

        The same 1/(d - x) law as ``stone_width_at``, so it too is a line
        through ``yh``.
        """
        self._need_lateral()
        _c, yh = self._map()
        return self.lat_px_per_m_at_tee * (row - yh) / (self.tee_row - yh)

    def to_house(self, col: float, row: float) -> tuple[float, float]:
        """A point ON THE ICE, from view pixels to house metres.

        Timeline axes: +y up-sheet toward the thrower, which is toward this
        camera; +x the thrower's right, which is image right -- the view is not
        mirrored. Anything standing up must be read at its foot: a stone's body
        rises about 17 px above its footprint at the tee.
        """
        self._need_lateral()
        return ((col - self.centre_col) / self.lateral_px_per_m(row),
                self.metres_at(row))

    def to_image(self, x_m: float, y_m: float) -> tuple[float, float]:
        """The view pixel ``(col, row)`` of a point on the ice."""
        self._need_lateral()
        row = self.row_for(y_m)
        return self.centre_col + x_m * self.lateral_px_per_m(row), row

    def _need_lateral(self):
        if not self.has_lateral:
            raise SideViewError("this view has no lateral calibration; "
                                "run solve_lateral first")
```

- [ ] **Step 4: Run the whole file**

Run: `./.venv/bin/pytest tests/test_sideview.py -q`
Expected: all pass, including the existing `TestSolve` and `TestStoneWidthAt`.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/geometry/sideview.py tests/test_sideview.py
git commit -m "sideview: a view can carry a lateral scale and map pixels to the house

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `solve_lateral`, from the ring's sides on the rows around the tee

**Files:**
- Modify: `src/curling_score/geometry/sideview.py` (new constants and
  functions after `solve()`)
- Modify: `tests/synth.py` (new `side_view_house` after `side_view`)
- Test: `tests/test_sideview.py` (new classes `TestSolveLateral` and
  `TestEveryRealViewLateral`)

**Interfaces:**
- Consumes: Task 1's fields and methods.
- Produces: `sideview.solve_lateral(plate, view: SideView, name: str = "side")
  -> SideView`. It returns a copy with `centre_col` and `lat_px_per_m_at_tee`
  set, or raises `SideViewError`. `plate` is the full composite frame, as
  `solve()` takes it. Also `sideview.PLAUSIBLE_LAT_PX_PER_M`, and
  `synth.side_view_house(...)`.

- [ ] **Step 1: Add the synthetic house**

In `tests/synth.py`, after `side_view`:

```python
def side_view_house(tee_row=430.0, hog_row=520.0, centre_col=390.0,
                    lat_px_per_m=148.0, w=810, h=1080, d_m=40.233,
                    far_green=GREEN_PAINT, noise=0.0, seed=0):
    """The far house as the side camera sees it, for the LATERAL fit.

    ``side_view`` paints the annulus as two flat bands, which is all the depth
    fit reads. Here it is the ellipse perspective makes of the ring, so a row
    through the tee has a left band and a right band with real edges. Positions
    come from the same map ``SideView.to_image`` inverts, so a correct fit
    recovers ``centre_col`` and ``lat_px_per_m`` exactly. ``far_green`` paints
    the right-hand band, which on real plates is often half as green as the
    left (VXU9's left view: peaks ~22 and ~10). RGB.
    """
    img = np.full((h, w, 3), SIDE_ICE, dtype=np.uint8)
    u = (hog_row - tee_row) * (d_m - C.TEE_TO_HOGLINE_M) / C.TEE_TO_HOGLINE_M
    c, yh = d_m * u, tee_row - u
    cols = np.arange(w)
    top = int(yh + c / (d_m + C.R_12FT_M)) - 1
    bot = int(yh + c / (d_m - C.R_12FT_M)) + 2
    for r in range(top, bot):
        y = d_m - c / (r - yh)
        x = (cols - centre_col) / (lat_px_per_m * (r - yh) / (tee_row - yh))
        rho = np.hypot(x, y)
        ring = (rho >= C.R_8FT_M) & (rho <= C.R_12FT_M)
        img[r, ring & (cols < centre_col)] = GREEN_PAINT
        img[r, ring & (cols >= centre_col)] = far_green
    rr = int(round(hog_row))
    img[rr - 1:rr + 2] = SIDE_LINE
    if noise:
        rng = np.random.default_rng(seed)
        img = np.clip(img.astype(np.float32) + rng.normal(0, noise, img.shape),
                      0, 255).astype(np.uint8)
    return img
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_sideview.py`:

```python
def _depth_only(tee=430.0, hog=520.0, w=810):
    return sideview.SideView(rect=(0, 0, w, 1080), tee_row=tee, hog_row=hog)


class TestSolveLateral:
    def test_it_recovers_the_centre_and_scale_it_was_drawn_with(self):
        plate = synth.side_view_house(centre_col=390.0, lat_px_per_m=148.0)
        got = sideview.solve_lateral(plate, _depth_only())
        assert got.centre_col == pytest.approx(390.0, abs=1.0)
        assert got.lat_px_per_m_at_tee == pytest.approx(148.0, rel=0.015)

    def test_it_works_off_centre_and_at_the_other_scales_measured(self):
        # VXU9's right view (Phase 0), and a nearer, wider framing.
        for tee, hog, centre, lat in ((436.45, 514.0, 414.7, 128.4),
                                      (452.0, 533.0, 300.0, 180.0)):
            plate = synth.side_view_house(tee_row=tee, hog_row=hog,
                                          centre_col=centre, lat_px_per_m=lat)
            got = sideview.solve_lateral(plate, _depth_only(tee, hog))
            assert got.centre_col == pytest.approx(centre, abs=1.0), (centre, lat)
            assert got.lat_px_per_m_at_tee == pytest.approx(lat, rel=0.015)

    def test_the_depth_calibration_is_left_exactly_as_it_was(self):
        view = _depth_only()
        got = sideview.solve_lateral(synth.side_view_house(), view)
        assert (got.rect, got.tee_row, got.hog_row, got.d_m) == \
            (view.rect, view.tee_row, view.hog_row, view.d_m)

    def test_a_far_band_half_as_green_still_fits(self):
        """A fixed threshold fails here: each band's edges sit at half its own peak."""
        plate = synth.side_view_house(far_green=(120, 138, 120), noise=4.0)
        got = sideview.solve_lateral(plate, _depth_only())
        assert got.centre_col == pytest.approx(390.0, abs=1.5)
        assert got.lat_px_per_m_at_tee == pytest.approx(148.0, rel=0.02)

    def test_the_neighbouring_sheet_s_ring_at_the_edge_is_not_this_house(self):
        plate = synth.side_view_house()
        plate[:, 785:810] = synth.GREEN_PAINT      # VXU9 left: crossings at 790-797
        got = sideview.solve_lateral(plate, _depth_only())
        assert got.centre_col == pytest.approx(390.0, abs=1.0)

    def test_a_player_across_a_few_rows_costs_those_rows_only(self):
        plate = synth.side_view_house()
        plate[426:429, 100:220] = (40, 40, 40)     # dark trousers over the near band
        got = sideview.solve_lateral(plate, _depth_only())
        assert got.lat_px_per_m_at_tee == pytest.approx(148.0, rel=0.015)

    def test_ice_with_no_house_is_refused(self):
        plate = np.full((1080, 810, 3), synth.SIDE_ICE, np.uint8)
        with pytest.raises(sideview.SideViewError):
            sideview.solve_lateral(plate, _depth_only())

    def test_a_far_band_too_faint_to_see_is_refused_not_guessed(self):
        plate = synth.side_view_house(far_green=(236, 239, 236))
        with pytest.raises(sideview.SideViewError):
            sideview.solve_lateral(plate, _depth_only())

    def test_an_implausible_scale_is_refused(self):
        plate = synth.side_view_house(lat_px_per_m=60.0)
        with pytest.raises(sideview.SideViewError):
            sideview.solve_lateral(plate, _depth_only())

    def test_a_view_offset_in_the_composite_reads_its_own_columns(self):
        big = np.full((1080, 1920, 3), synth.SIDE_ICE, np.uint8)
        big[:, 1107:1107 + 810] = synth.side_view_house()
        view = sideview.SideView(rect=(1107, 0, 810, 1080), tee_row=430.0,
                                 hog_row=520.0)
        got = sideview.solve_lateral(big, view)
        assert got.centre_col == pytest.approx(390.0, abs=1.0)
```

Add `import numpy as np` at the top of `tests/test_sideview.py` if it isn't
there. Then add a real-plate check beside `TestEveryRealView`, with the same
marks and parametrisation:

```python
@pytest.mark.slow
class TestEveryRealViewLateral:
    """Both side views of all five sheets, across the sheet this time."""

    @pytest.mark.parametrize("sheet,vid", sorted(VALIDATION_VIDS.items()))
    def test_both_views_calibrate_across(self, side_plate, sheet, vid):
        plate = side_plate(vid)
        h, w = plate.shape[:2]
        rects = sideview.locate(a_layout(), width=w, height=h)
        for name, rect in rects.items():
            label = f"sheet{sheet}-{name}"
            view = sideview.solve_lateral(
                plate, sideview.solve(plate, rect, name=label), name=label)
            lo, hi = sideview.PLAUSIBLE_LAT_PX_PER_M
            assert lo <= view.lat_px_per_m_at_tee <= hi, label
            assert 0.2 * rect[2] <= view.centre_col <= 0.8 * rect[2], label
```

- [ ] **Step 3: Run them to see them fail**

Run: `./.venv/bin/pytest tests/test_sideview.py -q -k "Lateral and not Map"`
Expected: FAIL. `module 'curling_score.geometry.sideview' has no attribute 'solve_lateral'`.

- [ ] **Step 4: Implement**

In `sideview.py`, add `import dataclasses` and `import math` at the top. After
`solve()`:

```python
# The lateral fit, measured on VXU9's two views on 2026-09-23. Along the tee row
# bare ice reads -6..+5 in greenness while the near band peaks ~22 and the far
# band only ~10, so a fixed threshold finds noise: each band is found above a
# low floor, then its edges are placed at half its own peak.
_BAND_FLOOR = 5.0
_BAND_MIN_PX = 10
_BAND_SMOOTH_PX = 7
# Rows either side of the tee that are read. The tee line's own rows (within
# 1.5 of it) are skipped; each row is corrected for the chord it crosses the
# ring on, which is shorter than the diameter away from the tee.
_LATERAL_ROWS = 4
_LATERAL_MIN_ROWS = 3
# The 8-ft span over the 12-ft span, against the chords' own ratio. Paint bleed
# moves it by a few hundredths; a mispaired band moves it by far more.
_RATIO_TOL = 0.08
# Pixels per metre across the sheet at the far tee. The club's views measured
# 128-149 (Phase 0) and the ring spans ~480-555 px of an ~810 px view; this is
# wide enough for any sheet's framing and narrow enough to refuse a fit that
# took one band for both.
PLAUSIBLE_LAT_PX_PER_M = (90.0, 220.0)


def _bands(prof):
    """The two green bands along one row, left then right, as (start, stop)."""
    runs, start = [], None
    for i, on in enumerate(np.append(prof > _BAND_FLOOR, False)):
        if on and start is None:
            start = i
        elif not on and start is not None:
            runs.append((start, i))
            start = None
    runs = [r for r in runs if r[1] - r[0] >= _BAND_MIN_PX]
    if len(runs) < 2:
        return None
    # The two longest: the neighbouring sheet's ring, where it shows at the
    # view's edge, is a sliver beside either of these.
    return sorted(sorted(runs, key=lambda r: r[1] - r[0])[-2:])


def _band_edges(prof, run):
    a, b = run
    half = float(prof[a:b].max()) / 2
    xs = _crossings(prof, max(0, a - 30), min(len(prof), b + 30), half)
    return (xs[0], xs[-1]) if len(xs) >= 2 else None


def solve_lateral(plate, view: SideView, name: str = "side") -> SideView:
    """Fit the far house's centre column and lateral scale from paint alone.

    A row is a line of constant depth, so where it cuts the green annulus its
    two bands' outer edges are the 12-ft ring's chord at that depth and their
    inner edges the 8-ft's. Summing the two spans cancels paint erosion, which
    narrows the outer span by as much as it widens the inner. Each row is
    divided by its own chords and by its own lateral factor before the median,
    so rows off the tee do not bias the scale low.
    """
    x0, y0, w, h = view.rect
    img = np.asarray(plate, dtype=np.float32)[y0:y0 + h, x0:x0 + w]
    green = img[:, :, 1] - (img[:, :, 0] + img[:, :, 2]) / 2
    kernel = np.ones(_BAND_SMOOTH_PX) / _BAND_SMOOTH_PX
    _c, yh = view._map()
    lats, centres = [], []
    t = int(round(view.tee_row))
    for r in range(t - _LATERAL_ROWS, t + _LATERAL_ROWS + 1):
        if abs(r - view.tee_row) < 1.5 or not 0 <= r < h:
            continue
        prof = np.convolve(green[r], kernel, mode="same")
        runs = _bands(prof)
        if runs is None:
            continue
        left, right = _band_edges(prof, runs[0]), _band_edges(prof, runs[1])
        if left is None or right is None:
            continue
        (l12, l8), (r8, r12) = left, right
        y = view.metres_at(r)
        a12 = math.sqrt(C.R_12FT_M ** 2 - y ** 2)
        a8 = math.sqrt(C.R_8FT_M ** 2 - y ** 2)
        outer, inner = r12 - l12, r8 - l8
        if outer <= 0 or inner <= 0 or abs(inner / outer - a8 / a12) > _RATIO_TOL:
            continue
        factor = (r - yh) / (view.tee_row - yh)
        lats.append((outer + inner) / (2 * (a12 + a8)) / factor)
        centres.append((l12 + l8 + r8 + r12) / 4)
    if len(lats) < _LATERAL_MIN_ROWS:
        raise SideViewError(
            f"{name}: the ring's two sides were read on only {len(lats)} rows "
            f"near the tee, need {_LATERAL_MIN_ROWS}")
    lat, centre = float(np.median(lats)), float(np.median(centres))
    if not PLAUSIBLE_LAT_PX_PER_M[0] <= lat <= PLAUSIBLE_LAT_PX_PER_M[1]:
        raise SideViewError(
            f"{name}: {lat:.1f} px/m across the sheet is outside the "
            f"{PLAUSIBLE_LAT_PX_PER_M[0]:.0f}-{PLAUSIBLE_LAT_PX_PER_M[1]:.0f} "
            f"any framing gives -- the fit paired the wrong bands")
    if not 0.2 * w <= centre <= 0.8 * w:
        raise SideViewError(f"{name}: house centre at column {centre:.0f} of {w}")
    return dataclasses.replace(view, centre_col=centre, lat_px_per_m_at_tee=lat)
```

- [ ] **Step 5: Run the whole file, then check on a real plate**

Run: `./.venv/bin/pytest tests/test_sideview.py -q`
Expected: all pass. The `slow` real-view class passes, or skips where frames
are absent. Run it explicitly too, with `-m slow -k Lateral`, since `slow` may
be deselected by default.

Then check against Phase 0's numbers on the real VXU9 plates. The Phase 0
plates are in this session's scratchpad `broom0/VXU9xwmugRg/`. If they are
gone, rebuild them with 40 `longview.decode` frames over the game and a median.

```bash
./.venv/bin/python - <<'EOF'
import cv2, numpy as np
from curling_score.geometry import sideview
P = "/tmp/claude-1000/-home-tcuser-src-curling-score/b9cf0d0f-935c-4127-986f-dc914da6e447/scratchpad/broom0/VXU9xwmugRg"
for name, tee, hog, w in (("left", 429.95, 520.0, 810), ("right", 436.45, 514.0, 813)):
    p = cv2.imread(f"{P}/plate-{name}.jpg")[:, :, ::-1]
    v = sideview.solve_lateral(p, sideview.SideView((0, 0, w, 1080), tee, hog), name)
    print(name, round(v.centre_col, 1), round(v.lat_px_per_m_at_tee, 1))
EOF
```

Expected: left ≈ 390 and ≈ 148–152 px/m; right ≈ 415 and ≈ 128–132 px/m. The
Phase 0 prototype read 148.5 and 128.4 without the per-row chord correction,
so this reads up to ~2% higher. If either is further off, stop and investigate
before committing.

- [ ] **Step 6: Commit**

```bash
git add src/curling_score/geometry/sideview.py tests/synth.py tests/test_sideview.py
git commit -m "sideview: fit the far house's centre and lateral scale from the ring's sides

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: the lateral fields survive every rebuild and round-trip through JSON

**Files:**
- Modify: `src/curling_score/detect/longview.py:364-365` (`find_crossing`)
- Modify: `src/curling_score/harvest/sidepool.py:96-97` (`_shifted`)
- Modify: `src/curling_score/harvest/sideviews.py:89-100` (`_view_to_json`,
  `_view_from_json`)
- Test: `tests/test_sidepool.py`, `tests/test_sideviews.py`

**Interfaces:**
- Consumes: Task 1's fields.
- Produces: nothing new. Existing functions stop dropping the fields.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sidepool.py`:

```python
class TestShiftedKeepsLateral:
    def test_moving_the_origin_keeps_the_across_calibration(self):
        from curling_score.geometry.sideview import SideView
        from curling_score.harvest import sidepool
        v = SideView(rect=(1107, 0, 813, 1080), tee_row=436.45, hog_row=514.0,
                     centre_col=414.7, lat_px_per_m_at_tee=128.4)
        got = sidepool._shifted(v)
        assert got.rect == (0, 0, 813, 1080)
        # centre_col is in the view's own columns, so a shift leaves it alone
        assert (got.centre_col, got.lat_px_per_m_at_tee) == (414.7, 128.4)
```

Append to `tests/test_sideviews.py`:

```python
class TestViewJsonLateral:
    def test_the_across_calibration_round_trips(self):
        from curling_score.geometry.sideview import SideView
        from curling_score.harvest import sideviews as SV
        v = SideView(rect=(0, 0, 810, 1080), tee_row=429.95, hog_row=520.0,
                     centre_col=390.1, lat_px_per_m_at_tee=148.5)
        assert SV._view_from_json(SV._view_to_json(v), v.rect) == v

    def test_json_from_before_it_existed_still_loads(self):
        from curling_score.harvest import sideviews as SV
        got = SV._view_from_json({"tee_row": 430.0, "hog_row": 520.0,
                                  "d_m": 40.2335}, (0, 0, 810, 1080))
        assert got.centre_col is None and not got.has_lateral
```

- [ ] **Step 2: Run them to see them fail**

Run: `./.venv/bin/pytest tests/test_sidepool.py tests/test_sideviews.py -q -k "Lateral"`
Expected: FAIL. `_shifted` returns `centre_col=None`, and the round-trip
compares unequal.

- [ ] **Step 3: Implement**

`harvest/sidepool.py` `_shifted`: keep the docstring, and replace the return
with:

```python
    return dataclasses.replace(view, rect=(0, 0, view.rect[2], view.rect[3]))
```

Add `import dataclasses` at the top of `sidepool.py`.

`detect/longview.py` `find_crossing`: replace the two-line `type(view)(...)`
with:

```python
    shifted = dataclasses.replace(view, rect=(0, 0, view.rect[2], view.rect[3]))
```

Add `import dataclasses` at the top of `longview.py`.

`harvest/sideviews.py`:

```python
def _view_to_json(v: sideview.SideView | None):
    if v is None:
        return None
    return {"tee_row": v.tee_row, "hog_row": v.hog_row, "d_m": v.d_m,
            "centre_col": v.centre_col,
            "lat_px_per_m_at_tee": v.lat_px_per_m_at_tee}


def _view_from_json(d, rect: tuple):
    if d is None:
        return None
    return sideview.SideView(
        rect=rect, tee_row=d["tee_row"], hog_row=d["hog_row"], d_m=d["d_m"],
        centre_col=d.get("centre_col"),
        lat_px_per_m_at_tee=d.get("lat_px_per_m_at_tee"))
```

- [ ] **Step 4: Run the affected files**

Run: `./.venv/bin/pytest tests/test_sidepool.py tests/test_sideviews.py tests/test_longview.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/detect/longview.py src/curling_score/harvest/sidepool.py \
        src/curling_score/harvest/sideviews.py tests/test_sidepool.py tests/test_sideviews.py
git commit -m "sideview: a rebuilt or reloaded view keeps its lateral calibration

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `analyze` fits the lateral scale and publishes it

**Files:**
- Modify: `src/curling_score/analyze.py`. The side-view block is ~L202-212 and
  the calibration block ~L445-447.
- Test: `tests/test_analyze_side.py` (new)

**Interfaces:**
- Consumes: `sideview.solve_lateral` (Task 2) and `SideView.has_lateral`
  (Task 1).
- Produces: `analyze._with_lateral(plate, view, name, progress) -> SideView`
  and `analyze._side_calibration(sideviews) -> dict`. The timeline's
  `calibration.left/right` gain `centre_col` and `lat_px_per_m_at_tee` when
  fitted. Phase 4 reads `sideviews[...]` with lateral fields.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_analyze_side.py`:

```python
"""The side views' lateral calibration, as analyze fits and publishes it."""
import numpy as np

from curling_score import analyze
from curling_score.geometry import sideview
from tests import synth


def _view():
    return sideview.SideView(rect=(0, 0, 810, 1080), tee_row=430.0, hog_row=520.0)


class TestWithLateral:
    def test_a_readable_ring_gives_the_view_its_across_scale(self):
        got = analyze._with_lateral(synth.side_view_house(), _view(), "left",
                                    lambda m: None)
        assert got.has_lateral

    def test_an_unreadable_one_costs_the_view_its_brooms_and_nothing_else(self):
        said = []
        plate = np.full((1080, 810, 3), synth.SIDE_ICE, np.uint8)
        got = analyze._with_lateral(plate, _view(), "left", said.append)
        assert got == _view()                  # depth calibration untouched
        assert said and "left" in said[0]


class TestSideCalibration:
    def test_lateral_fields_are_published_when_fitted(self):
        v = sideview.SideView(rect=(0, 0, 810, 1080), tee_row=429.951,
                              hog_row=520.0, centre_col=390.1234,
                              lat_px_per_m_at_tee=148.5678)
        got = analyze._side_calibration({"left": v})
        assert got == {"left": {"rect": [0, 0, 810, 1080], "tee_row": 429.95,
                                "hog_row": 520.0, "centre_col": 390.12,
                                "lat_px_per_m_at_tee": 148.568}}

    def test_a_depth_only_view_publishes_exactly_what_it_did_before(self):
        got = analyze._side_calibration({"right": _view()})
        assert got == {"right": {"rect": [0, 0, 810, 1080], "tee_row": 430.0,
                                 "hog_row": 520.0}}

    def test_no_side_views_publish_nothing(self):
        assert analyze._side_calibration(None) == {}
```

- [ ] **Step 2: Run them to see them fail**

Run: `./.venv/bin/pytest tests/test_analyze_side.py -q`
Expected: FAIL. `module 'curling_score.analyze' has no attribute '_with_lateral'`.

- [ ] **Step 3: Implement**

Add these module-level helpers in `analyze.py`, near the other private helpers
(after `OTHER_HOUSE`):

```python
def _with_lateral(plate, view, name, progress):
    """``view`` with its across-the-sheet calibration, or unchanged if the ring's
    sides could not be read -- which costs that view its brooms and nothing
    else. The hog tripwire is depth alone and never waits on this."""
    try:
        return sideview.solve_lateral(plate, view, name=name)
    except sideview.SideViewError as exc:
        progress(f"{name} view has no lateral calibration, so no brooms from it: {exc}")
        return view


def _side_calibration(sideviews) -> dict:
    out = {}
    for name, v in (sideviews or {}).items():
        d = {"rect": list(v.rect), "tee_row": round(v.tee_row, 2),
             "hog_row": round(v.hog_row, 2)}
        if v.has_lateral:
            d["centre_col"] = round(v.centre_col, 2)
            d["lat_px_per_m_at_tee"] = round(v.lat_px_per_m_at_tee, 3)
        out[name] = d
    return out
```

In the side-view block, right after the `sideviews = {n: sideview.solve(...)}`
dict comprehension, still inside the `try`:

```python
            sideviews = {n: _with_lateral(plate, v, n, progress)
                         for n, v in sideviews.items()}
```

In the calibration block, replace:

```python
        **{name: {"rect": list(v.rect), "tee_row": round(v.tee_row, 2),
                  "hog_row": round(v.hog_row, 2)}
           for name, v in (sideviews or {}).items()},
```

with:

```python
        **_side_calibration(sideviews),
```

- [ ] **Step 4: Run the affected files**

Run: `./.venv/bin/pytest tests/test_analyze_side.py tests/test_analyze_write.py tests/test_cli_analyze.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/analyze.py tests/test_analyze_side.py
git commit -m "analyze: fit each side view's lateral scale and publish it with the calibration

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: the SAM server scores a click by the kind of thing it is

**Files:**
- Modify: `src/curling_score/train/segserve.py` (`score`, `Segmenter.box_at`,
  `make_handler`, `serve`, and a new `segment_request`)
- Test: `tests/test_segserve.py`

**Interfaces:**
- Produces:
  - `segserve.SHAPES` (dict keyed `"stone"`, `"broom"`);
  - `segserve.score(box, geom, shape="stone")`;
  - `Segmenter.box_at(stem, x, y, geom, shape="stone")`;
  - `segserve.segment_request(seg, req, geoms, shapes) -> dict`;
  - `make_handler(directory, seg, geoms, shapes=("stone", "stone"))`;
  - `serve(directory, items, weights, port=8777, host="127.0.0.1",
    shapes=("stone", "stone"))`.

  `shapes[cls]` names the shape for each class index.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_segserve.py`. `GEOM` is the module's existing fixture
geometry; reuse it.

```python
class TestBroomShape:
    def test_a_pad_held_along_the_line_is_a_broom_not_a_stone(self):
        """Narrow and nearly square: a stone's width rule rejects it, a broom's
        does not. ~30 px against a stone's ~44 at the tee (Phase 0)."""
        row = 440
        expect = GEOM["k"] * (row - GEOM["yh"])
        w = 0.27 * expect
        box = (100, int(row - w * 1.1), int(100 + w - 1), row)
        assert segserve.score(box, GEOM)[0] == math.inf
        assert segserve.score(box, GEOM, shape="broom")[0] < math.inf

    def test_a_pad_across_the_line_scores_best_at_its_own_size(self):
        row = 440
        expect = 0.70 * GEOM["k"] * (row - GEOM["yh"])
        exact = (100, row - 10, int(100 + expect - 1), row)
        wide = (100, row - 10, int(100 + 1.8 * expect - 1), row)
        assert (segserve.score(exact, GEOM, shape="broom")[0]
                < segserve.score(wide, GEOM, shape="broom")[0])


class TestSegmentRequest:
    class Seen:
        def box_at(self, stem, x, y, geom, shape="stone"):
            self.call = (stem, x, y, geom, shape)
            return {"ok": True}

    def test_the_armed_class_picks_the_shape(self):
        seg = self.Seen()
        segserve.segment_request(seg, {"stem": "s", "x": 1, "y": 2, "cls": 0},
                                 {"s": {"k": 1, "yh": 0}}, ("broom",))
        assert seg.call == ("s", 1.0, 2.0, {"k": 1, "yh": 0}, "broom")

    def test_a_stone_page_is_unchanged(self):
        seg = self.Seen()
        segserve.segment_request(seg, {"stem": "s", "x": 1, "y": 2, "cls": 1},
                                 {}, ("stone", "stone"))
        assert seg.call[-1] == "stone"

    def test_a_class_the_page_does_not_have_falls_back_to_stone(self):
        seg = self.Seen()
        segserve.segment_request(seg, {"stem": "s", "x": 1, "y": 2, "cls": 7},
                                 {}, ("broom",))
        assert seg.call[-1] == "stone"
```

- [ ] **Step 2: Run them to see them fail**

Run: `./.venv/bin/pytest tests/test_segserve.py -q -k "Broom or SegmentRequest"`
Expected: FAIL. `score() got an unexpected keyword argument 'shape'`.

- [ ] **Step 3: Implement**

In `segserve.py`, after `ASPECT_TOL`:

```python
# What a click is expected to be, by the class the page armed. A broom head
# lies on the same ice as a stone, so the same perspective line bounds it,
# scaled to its size: about 30 px against a stone's ~44 at the tee (Phase 0,
# 2026-09-23). Its aspect is loose because a pad is held across the line or
# along it.
SHAPES = {
    "stone": {"scale": 1.0, "width": WIDTH_TOL, "aspect": ASPECT_TOL},
    "broom": {"scale": 0.70, "width": (0.35, 2.0), "aspect": (0.10, 1.60)},
}
```

Change `score`'s signature to `def score(box, geom, shape="stone")`. Then:
- add `sh = SHAPES[shape]` as its first line;
- make `expect = sh["scale"] * geom["k"] * (y1 - geom["yh"])`;
- replace `WIDTH_TOL` and `ASPECT_TOL` in its two range checks with
  `sh["width"]` and `sh["aspect"]`.

The docstring stays, with "How stone-like" changed to "How like a ``shape`` —
a stone by default".

Change `box_at`'s signature to `def box_at(self, stem, x, y, geom, shape="stone")`
and its scoring line to `s, ratio, aspect = score(box, geom, shape)`.

Add, above `make_handler`:

```python
def segment_request(seg, req: dict, geoms: dict, shapes) -> dict:
    """One /segment call: which frame, where, and what the click says it is.

    The page sends the armed class index; ``shapes`` names what each index is.
    An index the page should not have is treated as a stone, the editor's
    original meaning, rather than failing a person mid-session.
    """
    stem = req["stem"]
    cls = int(req.get("cls") or 0)
    shape = shapes[cls] if 0 <= cls < len(shapes) else "stone"
    return seg.box_at(stem, float(req["x"]), float(req["y"]),
                      geoms.get(stem) or req.get("geom") or {}, shape=shape)
```

In `make_handler`:
- change the signature to `def make_handler(directory, seg, geoms, shapes=("stone", "stone"))`;
- replace the `else:` branch of `do_POST` (the three lines from
  `stem = req["stem"]` to the `box_at` call) with
  `out = segment_request(seg, req, geoms, shapes)`.

In `serve`:
- change the signature to `def serve(directory, items, weights, port=8777,
  host="127.0.0.1", shapes=("stone", "stone"))`;
- pass `shapes` into `make_handler(directory, seg, geoms, shapes)`.

- [ ] **Step 4: Run the whole file**

Run: `./.venv/bin/pytest tests/test_segserve.py -q`
Expected: all pass, including every existing `TestScore` case, which uses the
default shape.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/train/segserve.py tests/test_segserve.py
git commit -m "segserve: a click is scored by what the page armed, so SAM can box a broom head

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: the box editor renders a broom page

**Files:**
- Modify: `src/curling_score/train/boxedit.py` (`render`, plus a new
  `_BROOM_EDITS` and `_as_broom`)
- Test: `tests/test_boxedit.py`

**Interfaces:**
- Produces: `boxedit.render(items, out_dir, *, scope, title="Fix the boxes",
  proposals=False, kind="stone") -> Path`. `kind="broom"` gives a one-class
  page armed with **B**, whose boxes are class 0. The default output is
  unchanged.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_boxedit.py`:

```python
def _one_item():
    return [{"stem": "v_l_000010_00", "image": "images/v_l_000010_00.jpg",
             "width": 810, "height": 245, "boxes": [],
             "geom": {"k": 0.9, "yh": -300.0}}]


class TestBroomPage:
    def test_the_stone_page_is_exactly_what_it_was(self, tmp_path):
        page = boxedit.render(_one_item(), tmp_path, scope="s").read_text()
        assert page == (boxedit._PAGE.replace("__DATA__", json.dumps(_one_item()))
                        .replace("__SCOPE__", "s")
                        .replace("__TITLE__", "Fix the boxes"))

    def test_b_arms_the_one_class(self, tmp_path):
        page = boxedit.render(_one_item(), tmp_path, scope="broom:w1",
                              kind="broom").read_text()
        assert 'k === "b"' in page and "setArm(0)" in page
        assert '+broom head (B)' in page
        assert 'k === "r" || k === "y"' not in page

    def test_it_still_calls_the_segmenter(self, tmp_path):
        from curling_score.train import segserve
        boxedit.render(_one_item(), tmp_path, scope="broom:w1", kind="broom")
        segserve.check_page(tmp_path)          # raises if "/segment" went missing

    def test_every_edit_finds_its_text_in_the_template(self):
        for old, _new in boxedit._BROOM_EDITS:
            assert boxedit._PAGE.count(old) == 1, old

    def test_an_unknown_kind_is_refused(self, tmp_path):
        with pytest.raises(ValueError):
            boxedit.render(_one_item(), tmp_path, scope="s", kind="sweeper")
```

- [ ] **Step 2: Run them to see them fail**

Run: `./.venv/bin/pytest tests/test_boxedit.py -q -k TestBroomPage`
Expected: FAIL. `render() got an unexpected keyword argument 'kind'`.

- [ ] **Step 3: Implement**

In `boxedit.py`, after `HEIGHT_RATIO`:

```python
# The broom page: one class, armed with B. SAM, dragging and export are already
# class-blind, so this restates only the parts of the page that name red and
# yellow. Each old text must occur exactly once in _PAGE -- a template edit that
# moved one would otherwise ship a page whose B key does nothing, silently --
# and a test holds that.
_BROOM_EDITS = (
    ("--red:#e03c3c;", "--red:#d02ad0;"),       # magenta: nothing on the ice is
    ('<button id="addr">+red (R)</button>\n  <button id="addy">+yellow (Y)</button>',
     '<button id="addr">+broom head (B)</button>\n'
     '  <button id="addy" hidden>+yellow (Y)</button>'),
    ("SAM: click a stone", "SAM: click a broom head"),
    ('k === "r" || k === "y"', 'k === "b"'),
    ('setArm(k === "r" ? 0 : 1)', "setArm(0)"),
    # The fallback box when SAM is unreachable: a pad is ~0.7 of a stone's width.
    ("let w = item.geom.k * (row - item.geom.yh);",
     "let w = 0.70 * item.geom.k * (row - item.geom.yh);"),
)
_BROOM_HELP = (
    '<p class="muted">Box <b>every broom head resting on the ice</b>, the '
    "skip's and anyone else's; one held in the air is not boxed. Press "
    "<b>B</b> to arm, click the pad, and SAM finds its edges. Drag or resize "
    "if it is off, <b>Delete</b> to remove. Mark each frame reviewed with "
    "<b>space</b> -- a reviewed frame with no box says no broom was down. "
    "<b>N</b>/<b>P</b> move between frames; <b>Save to server</b> writes "
    "the session beside the images.</p>")


def _as_broom(page: str) -> str:
    import re

    for old, new in _BROOM_EDITS:
        page = page.replace(old, new, 1)
    page, n = re.subn(r'<p class="muted">.*?</p>', _BROOM_HELP, page,
                      count=1, flags=re.S)
    assert n == 1
    return page
```

In `render`:
- add `kind: str = "stone"` after `proposals` in the signature;
- at its top add
  `if kind not in ("stone", "broom"): raise ValueError(f"no editor for {kind!r}")`;
- build the page as today into a local `html`, and write
  `_as_broom(html) if kind == "broom" else html`.

Update the docstring: "`kind="broom"` renders the one-class broom-head page."

- [ ] **Step 4: Run the whole file**

Run: `./.venv/bin/pytest tests/test_boxedit.py tests/test_segserve.py -q`
Expected: all pass, including the existing pinned-string tests on `_PAGE`.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/train/boxedit.py tests/test_boxedit.py
git commit -m "boxedit: a one-class page for boxing broom heads, armed with B

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: `cs labels --apply` applies boxedit's replacement boxes

**Files:**
- Modify: `src/curling_score/cli.py` (`_labels`, the `apply_edits` call ~L386)
- Test: `tests/test_cli_labels.py`

**Interfaces:**
- Consumes: `labels.Edits.boxes` and `labels.apply_edits(..., boxes=)`, both of
  which already exist.

- [ ] **Step 1: Write the failing test**

Append inside `class TestApply` in `tests/test_cli_labels.py`:

```python
    def test_a_box_editor_session_replaces_the_frame_s_boxes(self, tmp_path, dataset):
        a = session(tmp_path / "a.json",
                    boxes={"v_t_000000_00": [[0, 0.25, 0.75, 0.1, 0.05]]},
                    reviewed=["v_t_000000_00"])
        assert cli.main(["labels", str(dataset), "--split", "train",
                         "--apply", a, "--keep-empty"]) == 0
        got = (dataset / "labels" / "train" / "v_t_000000_00.txt").read_text()
        assert got.split() == ["0", "0.250000", "0.750000", "0.100000", "0.050000"]
```

If `_write_boxes` formats differently, match the format the existing tests in
this file assert. Check `_write_boxes` at `train/labels.py:403`.

- [ ] **Step 2: Run it to see it fail**

Run: `./.venv/bin/pytest tests/test_cli_labels.py -q -k replaces`
Expected: FAIL. The file still holds `0 0.500000 0.500000 0.070000 0.040000`.

- [ ] **Step 3: Implement**

In `cli.py` `_labels`, add `boxes=edits.boxes,` to the `labels.apply_edits(...)`
call. After the existing prints, add:

```python
        if c.get("replaced"):
            print(f"  {c['replaced']} frame(s) restated by the box editor")
```

- [ ] **Step 4: Run the file**

Run: `./.venv/bin/pytest tests/test_cli_labels.py tests/test_labels.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/cli.py tests/test_cli_labels.py
git commit -m "labels: --apply honours the box editor's replacement boxes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: harvest the round-1 frames and serve the page

**Files:**
- Create: `scripts/broom/harvest.py`
- Create: `scripts/broom/serve.py`
- Create: `datasets/broom/README.md`
- Create: `datasets/broom/manifest-wave1.json` (generated)

**Interfaces:**
- Consumes: the timeline JSON (`games[].ends[].shots[]` with `t_tee_s`,
  `t_tee_estimated`, `missing`, `number`, `color`; `end.house`;
  `calibration.left/right` with `rect`, `tee_row`, `hog_row`),
  `detect.longview.decode`, `boxedit.frame_geometry` / `boxedit.render(kind="broom")`,
  `harvest.sideframes.stem_for`, `segserve.serve(shapes=("broom",))`, and
  `longview.STONE_WIDTH_AT_HOG_PX`.
- Produces: `~/curling-work/broom/wave1/{images/, items.json, index.html}` and
  the committed `datasets/broom/manifest-wave1.json`. Each manifest entry
  carries `stem`, `video_id`, `view`, `t_abs`, `t_tee`, `offset`,
  `tee_estimated`, `end`, `shot`, `color`, `rect`, `tee_row`, `hog_row`,
  `crop_top`, `width` and `height`. Phase 3 needs these to map a box back
  through `to_house`.

- [ ] **Step 1: Write `scripts/broom/harvest.py`**

```python
"""Cut the destination-facing long camera just before each tee crossing, for
labelling broom heads with SAM.

Phase 0 (docs/superpowers/specs/2026-09-23-target-broom-design.md) found the
skip's pad down and still across t_tee-2.0..+0.5 on 45 of 49 shots, so two
frames per shot, 0.7 s apart inside the pipeline's own window, are two looks at
one placement rather than two placements. The camera is the one at the throwing
end, CAMERA_FOR[end.house] -- the other one from hogtime's for the same end.

    ./.venv/bin/python scripts/broom/harvest.py \\
        --timeline ~/curling-work/ds15/games/timelines/VXU9xwmugRg.json \\
        --video ~/.cache/curling_score/videos/VXU9xwmugRg.mp4 \\
        --out ~/curling-work/broom/wave1 \\
        --manifest datasets/broom/manifest-wave1.json --scope broom:wave1
"""
import argparse
import json
import sys
from pathlib import Path

import cv2

OFFSETS_S = (-1.0, -0.3)
CAMERA_FOR = {"top": "left", "bottom": "right"}     # game/hogtime.CAMERA_FOR
ABOVE_TEE_ROWS = 130        # the skip's legs and the shaft, above the house
PAST_Y_M, BELOW_PAD = 3.0, 15   # down to 3 m in front of the tee, and a margin


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeline", required=True)
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--scope", required=True)
    ap.add_argument("--every", type=int, default=1)
    args = ap.parse_args()

    from curling_score.detect import longview
    from curling_score.geometry.sideview import SideView
    from curling_score.harvest.sideframes import stem_for
    from curling_score.train import boxedit

    doc = json.loads(Path(args.timeline).expanduser().read_text())
    vid = doc["source"]["video_id"]
    cal = doc["calibration"]
    views = {n: SideView(rect=tuple(cal[n]["rect"]), tee_row=cal[n]["tee_row"],
                         hog_row=cal[n]["hog_row"]) for n in ("left", "right")}
    out = Path(args.out).expanduser()
    (out / "images").mkdir(parents=True, exist_ok=True)

    shots = [(e, s) for g in doc["games"] for e in g["ends"] for s in e["shots"]
             if not s.get("missing") and s.get("t_tee_s") is not None]
    items, manifest, skipped = [], [], 0
    for i, (end, shot) in enumerate(shots):
        if i % args.every:
            continue
        name = CAMERA_FOR[end["house"]]
        view = views[name]
        top = max(0, int(view.tee_row - ABOVE_TEE_ROWS))
        bot = min(view.rect[3], int(view.row_for(PAST_Y_M) + BELOW_PAD))
        geom = boxedit.frame_geometry(view, longview.STONE_WIDTH_AT_HOG_PX,
                                      row_offset=top)
        for off in OFFSETS_S:
            t = round(shot["t_tee_s"] + off, 2)
            if t < 0:
                skipped += 1
                continue
            frames, _ = longview.decode(Path(args.video).expanduser(), view.rect,
                                        t, t + 0.05, fps=30)
            if not len(frames):
                skipped += 1        # past the end of the video, or a bad seek
                continue
            crop = cv2.cvtColor(frames[0][top:bot], cv2.COLOR_RGB2BGR)
            stem = stem_for(vid, name, t)
            cv2.imwrite(str(out / "images" / f"{stem}.jpg"), crop,
                        [cv2.IMWRITE_JPEG_QUALITY, 92])
            items.append({"stem": stem, "image": f"images/{stem}.jpg",
                          "width": crop.shape[1], "height": crop.shape[0],
                          "boxes": [], "geom": geom})
            manifest.append({
                "stem": stem, "video_id": vid, "view": name, "t_abs": t,
                "t_tee": shot["t_tee_s"], "offset": off,
                "tee_estimated": bool(shot.get("t_tee_estimated")),
                "end": end["number"], "shot": shot["number"],
                "color": shot["color"], "rect": list(view.rect),
                "tee_row": view.tee_row, "hog_row": view.hog_row,
                "crop_top": top, "width": crop.shape[1],
                "height": crop.shape[0]})
        print(f"\r{i + 1}/{len(shots)} shots", end="", flush=True)

    (out / "items.json").write_text(json.dumps(items))
    Path(args.manifest).write_text(json.dumps(manifest, indent=1) + "\n")
    page = boxedit.render(items, out, scope=args.scope, kind="broom",
                          title=f"Broom heads -- {vid}")
    print(f"\n{len(items)} frames from {len({m['shot'] for m in manifest})} shots"
          f"{f', {skipped} skipped (outside the video)' if skipped else ''}; "
          f"page {page}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Write `scripts/broom/serve.py`**

```python
"""Serve a broom page with SAM behind it.

    ./.venv/bin/python scripts/broom/serve.py ~/curling-work/broom/wave1 \\
        --weights ~/curling-work/sam/sam2.1_b.pt

Serves on 127.0.0.1:8777. From another machine, forward the port:
ssh -L 8777:127.0.0.1:8777 <this box>. Serve over HTTP, never file:// --
the page keeps work in localStorage, and it saves through /save.
"""
import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("directory")
    ap.add_argument("--weights", required=True)
    ap.add_argument("--port", type=int, default=8777)
    args = ap.parse_args()

    from curling_score.train import segserve

    d = Path(args.directory).expanduser()
    items = json.loads((d / "items.json").read_text())
    httpd = segserve.serve(d, items, str(Path(args.weights).expanduser()),
                           port=args.port, shapes=("broom",))
    print(f"http://127.0.0.1:{args.port}/  ({len(items)} frames)", flush=True)
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: Write `datasets/broom/README.md`**

Keep it short:
- what the set is: broom heads on the ice, in the destination-facing long
  camera, at `t_tee − 1.0` and `t_tee − 0.3`;
- the labelling rule: box every pad on the ice, not one held up;
- where images live: `~/curling-work/broom/<wave>/images`, and later the worker
  at `/data/wdd/curling/broom_trees/`;
- that `edits/` holds every saved session, committed as soon as it is saved;
- the commands in the two scripts' docstrings.

- [ ] **Step 4: Run the harvest on VXU9 and inspect it**

```bash
./.venv/bin/python scripts/broom/harvest.py \
    --timeline ~/curling-work/ds15/games/timelines/VXU9xwmugRg.json \
    --video ~/.cache/curling_score/videos/VXU9xwmugRg.mp4 \
    --out ~/curling-work/broom/wave1 \
    --manifest datasets/broom/manifest-wave1.json --scope broom:wave1
```

Expected: about 2 × 110 frames from about 110 shots, and a page path. Open two
images (one per camera), for example with the Read tool on
`~/curling-work/broom/wave1/images/<stem>.jpg`. Each must show the far house
with the skip's legs above it and ice at least 3 m in front of the tee.

- [ ] **Step 5: Fetch the SAM weights**

The user approved this ~160 MB download.

```bash
mkdir -p ~/curling-work/sam && cd ~/curling-work/sam && \
  /home/tcuser/src/curling_score/.venv/bin/python -c "from ultralytics import SAM; SAM('sam2.1_b.pt')" && ls -la
```

Expected: `sam2.1_b.pt` in `~/curling-work/sam/`. If ultralytics saved it
elsewhere, move it there.

- [ ] **Step 6: Commit the scripts, README and manifest**

```bash
git add scripts/broom/harvest.py scripts/broom/serve.py datasets/broom/README.md \
        datasets/broom/manifest-wave1.json
git commit -m "broom: harvest the destination camera before each tee crossing, and serve it with SAM

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 7: Serve round 1 in the background and hand it over**

Run `./.venv/bin/python scripts/broom/serve.py ~/curling-work/broom/wave1
--weights ~/curling-work/sam/sam2.1_b.pt` as a background process.

Check that `curl -s -X POST localhost:8777/segment -d
'{"stem":"<a real stem>","x":400,"y":130,"cls":0}'` returns JSON with `"ok"`.

Then give the user:
- the URL, and the port-forward line if they are on another machine;
- the rule: box every pad on the ice, B, click, space;
- the reminder to press **Save to server** at the end of each sitting.

After each saved sitting, copy `~/curling-work/broom/wave1/edits/*.json` to
`datasets/broom/edits/` and commit it straight away.

---

## After this plan

Once round 1 is saved and committed, the next plan covers:
- **Phase 3:** the v0 train on the worker, round-2 pre-labels on AEqL and
  hOKZ, and the held-out eval against the bar.
- **Phase 4:** `detect/broommodel.py`, `game/broomtime.py`, and the timeline
  field.
- **Phase 5:** the viewer.

These are all in the spec.
