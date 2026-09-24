# Shot Line — Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish a per-shot `line` field (schema 6). It says where the rock's
thrown line passed the skip's broom, where the rock sat before push-off, and
where the camera behind the thrower saw it go.

**Architecture:**
- Three changes to existing code:
  - The side-view calibration gains a painted centre line, so lateral figures
    are measured from the paint.
  - The side detector keeps each box's column, which it computes today and
    throws away.
  - The hog-line tripwire stops losing stones whose track is split across a
    column bin at the hog row.
- A new attach-only pass, `game/linetime.py`, runs after hogtime and
  broomtime. It reuses hogtime's detections and adds three decodes of its own:
  the window extension, the start point, and the path from behind the thrower.
- `timeline.build_end` publishes the result.
- Nothing can add, drop or renumber a shot.

**Tech Stack:** Python 3.12, numpy, ultralytics YOLO (ds13b, via
`sidemodel`), ffmpeg (via `longview.decode`), pytest.

**Spec:** `docs/superpowers/specs/2026-09-24-shot-line-detail-design.md`
(sections 4 and 6). A second plan,
`docs/superpowers/plans/2026-09-24-shot-line-viewer.md`, covers the viewer.

## Global Constraints

- **Coordinates.** `line` uses the timeline's frame: metres from the
  destination tee, +y up-sheet toward the thrower, +x the thrower's right.
  - The throwing tee is at y = `C.TEE_TO_TEE_M` (34.747).
  - The throwing hog line is at y = 34.747 − 6.401 = 28.346.
  - The hack line is at y = 34.747 + 3.658 = 38.405.
- **The hog-crossing camera** is `sideviews[CAMERA_FOR[OTHER_HOUSE[end.house]]]`.
  It looks back at the thrower, so image-left is the thrower's right.
  - Destination x = −(its `lateral_x`).
  - Destination y = 34.747 − (its metres from the throwing tee).
- **The destination camera** is `sideviews[CAMERA_FOR[end.house]]`. Its
  `to_house` frame *is* the destination frame, with no flip.
- **Box to stone centre.** A box's bottom row is the stone's footprint at its
  near edge. The stone's centre is `C.STONE_RADIUS_M` further from the camera.
  - Hog camera (the stone comes toward it): `y′_c = metres_at(bottom) − R`.
  - Destination camera (the stone moves away): `y_c = metres_at(bottom) − R`.
- **The line fit.**
  - It uses only samples 6.401–10.0 m past the throwing tee.
  - It needs at least 15 samples spanning at least 2.5 m.
  - It applies no speed gate.
- **Crop edge.** Hog-camera samples whose box bottom is within 6 rows of
  `sidepool.band_crop(view)[1]` are dropped.
- **Window extension.** Decode `t_release + 6.5 … 9.0` s only when the linked
  track ends short of 10 m past the throwing tee.
- **Start point.**
  - Window: `t_release − 3.0 … −0.2` s at 5 fps.
  - Accept stones 2.058–4.258 m behind the throwing tee, with |x| ≤ 0.6 m.
  - Take the median of at least 3 boxes.
- **Path from behind the thrower.**
  - Window: `t_hog + 1 … t_rest + 1` s at 5 fps.
  - Two crops: `tee_row − 80` to 660 at imgsz 800, and 620 to the bottom at
    imgsz 416.
  - Confidence at least 0.3.
  - Box width 0.65–1.5 × 0.291 m × `lateral_px_per_m(row)`.
  - Chain gaps of up to 8 s.
- **`confirmed`.**
  - `true`: the path was seen from at least 12 m out, and the median distance
    of its first 4 m from the fitted line is at most 0.10 m.
  - `false`: it was seen, and that median distance is over 0.10 m.
  - `null`: it was not seen.
- **`curl`.** The sign of `x_rest − fit(y_rest)`: `"right"` if positive,
  `"left"` if negative, `null` below 0.05 m.
- **`side`.** `"wide"` when `sign(miss) == −sign(curl)`, else `"narrow"`;
  `null` when `curl` is `null`.
- **Paths.** `hog_path` and `path` are `[y, x]` pairs thinned to one per
  0.5 m, with the last point always kept.
- **`line` is `null`** when the shot is missing, has no `target_broom`, has no
  release, or has no model-made hog crossing, or when the fit fails.
- **Schema and version.** `SCHEMA_VERSION` 5 → 6 and `PIPELINE_VERSION`
  "2026.09.24". `processing_version` gains `+line` when the pass ran.
- **Checkout.** Another session shares it: stage only this plan's files, with
  explicit paths.
- **Tests.** The full suite gets OOM-killed on this box, so run only the files
  each task names.
- **Commits.** Message style `area: sentence`, ending with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Big-weight hits** (over 3.2 m/s). The line pass must measure them even
   though the hog-line timing refuses them. hogtime stores its crossing
   whenever it carries samples, refused or not (Task 3), and the line pass
   relinks from those samples, whatever the verdict. Pinned in Task 8 by a
   `bad_speed` crossing with samples that still gets a line.
2. **A slow guard whose hog-camera track stops at about 8.6 m.** It must get
   the extension decode and then a fit. Pinned in Task 8, which asserts that
   the extension is decoded exactly when the track ends short of 10 m.
3. **A skip's broom on the centre line, or a miss of about 0.** `side` must
   still be well defined, and the viewer shows "On the broom". Pinned in
   Task 4: `miss` 0 with `curl` "left" gives `"narrow"`, and no exception.
4. **Two stones near the line behind the thrower** (one resting, one moving).
   The chain must follow the moving one. Pinned in Task 7.
5. **A view whose centre line cannot be traced** (a logo, faint paint). It
   must fall back to the ring centre and never fail the run. Pinned in Task 1
   by a plate with no line, which raises `SideViewError`, and Task 1's
   analyze-side fallback.

---

### Task 1: Painted centre line on `SideView`

**Files:**
- Modify: `src/curling_score/geometry/sideview.py`
- Modify: `src/curling_score/analyze.py` (`_with_lateral`, `_side_calibration`)
- Modify: `src/curling_score/harvest/sideviews.py` (`_view_to_json`, `_view_from_json`)
- Test: `tests/test_sideview.py`, `tests/test_sideviews.py`

**Interfaces:**
- Produces:
  - the `SideView.centre_line: tuple[float, float] | None`, which is `(a, b)`
    in `col = a + b·row`;
  - `SideView.centre_col_at(row) -> float`;
  - `SideView.lateral_x(col, row) -> float`;
  - `solve_centre_line(plate, view, name="side") -> SideView`, which raises
    `SideViewError`.
- `to_house` and `to_image` use `centre_col_at`.

- [ ] **Step 1: Write the failing tests.** Append this to `tests/test_sideview.py`:

```python
def _plate_with_centre_line(a=380.0, b=0.01, side_px=200.0, logo=True, line=True, seed=0):
    """A grey plate with the painted centre line col = a + b*row below the house,
    a parallel line side_px to its right, and optionally a noisy centre-ice logo."""
    rng = np.random.default_rng(seed)
    plate = np.full((1080, 1920, 3), 150.0) + rng.normal(0, 2.0, (1080, 1920, 3))
    for row in range(470, 1080):
        for col in ((a + b * row, a + b * row + side_px) if line else ()):
            c = int(round(col))
            plate[row, c - 1:c + 2, :] -= 14.0
    if logo:
        plate[700:800, 280:520, :] = 150.0 + rng.normal(0, 18.0, (100, 240, 3))
    return plate


class TestSolveCentreLine:
    VIEW = SideView(rect=(0, 0, 810, 1080), tee_row=430.0, hog_row=520.0,
                    centre_col=384.0, lat_px_per_m_at_tee=150.0)

    def test_it_recovers_the_painted_line_through_a_logo(self):
        got = sideview.solve_centre_line(_plate_with_centre_line(), self.VIEW)
        a, b = got.centre_line
        for row in (500.0, 1000.0):
            assert a + b * row == pytest.approx(380.0 + 0.01 * row, abs=0.6)

    def test_ice_with_no_line_is_refused(self):
        with pytest.raises(sideview.SideViewError):
            sideview.solve_centre_line(_plate_with_centre_line(line=False), self.VIEW)

    def test_a_view_without_lateral_calibration_is_refused(self):
        flat = SideView(rect=(0, 0, 810, 1080), tee_row=430.0, hog_row=520.0)
        with pytest.raises(sideview.SideViewError):
            sideview.solve_centre_line(_plate_with_centre_line(), flat)

    def test_the_rest_of_the_calibration_is_left_as_it_was(self):
        got = sideview.solve_centre_line(_plate_with_centre_line(), self.VIEW)
        assert (got.tee_row, got.hog_row, got.centre_col, got.lat_px_per_m_at_tee) == (
            430.0, 520.0, 384.0, 150.0)


class TestLateralFromThePaint:
    V = SideView(rect=(0, 0, 810, 1080), tee_row=430.0, hog_row=520.0,
                 centre_col=384.0, lat_px_per_m_at_tee=150.0, centre_line=(380.0, 0.01))

    def test_the_centre_line_is_x_zero_on_every_row(self):
        for row in (450.0, 700.0, 1050.0):
            assert self.V.lateral_x(380.0 + 0.01 * row, row) == pytest.approx(0.0, abs=1e-9)

    def test_without_a_centre_line_the_ring_centre_is_used(self):
        v = SideView(rect=(0, 0, 810, 1080), tee_row=430.0, hog_row=520.0,
                     centre_col=384.0, lat_px_per_m_at_tee=150.0)
        assert v.centre_col_at(900.0) == 384.0

    def test_to_house_and_to_image_round_trip_with_the_line(self):
        col, row = self.V.to_image(0.7, 5.0)
        x, y = self.V.to_house(col, row)
        assert (x, y) == pytest.approx((0.7, 5.0), abs=1e-6)
```

Also add this to the existing JSON round-trip test in `tests/test_sideviews.py`.
If that test builds a `SideView`, pass `centre_line=(380.0, 0.01)` and assert
that it survives `_view_from_json(_view_to_json(v), v.rect)`. If there is no
such test, add one:

```python
def test_a_centre_line_survives_the_json_round_trip():
    from curling_score.geometry.sideview import SideView
    from curling_score.harvest import sideviews as sv
    v = SideView(rect=(0, 0, 810, 1080), tee_row=430.0, hog_row=520.0,
                 centre_col=384.0, lat_px_per_m_at_tee=150.0, centre_line=(380.0, 0.01))
    assert sv._view_from_json(sv._view_to_json(v), v.rect).centre_line == (380.0, 0.01)
```

- [ ] **Step 2: Run them to make sure they fail.**

Run: `.venv/bin/python -m pytest tests/test_sideview.py tests/test_sideviews.py -k "CentreLine or FromThePaint or centre_line" -x`

Expected: FAIL, with `TypeError` (the unexpected keyword `centre_line`) or
`AttributeError` (no `solve_centre_line`).

- [ ] **Step 3: Implement it.**

In `sideview.py`, add this field to `SideView` after `lat_px_per_m_at_tee`:

```python
    # The painted centre line, col = a + b*row, traced below the house by
    # `solve_centre_line`. The ring fit's centre is a column only on the tee
    # row, and on VXU9's left camera it sits 4.5 px (3 cm) off the paint; the
    # line itself leans up to 13 px over the rows below the house. None falls
    # back to `centre_col` everywhere.
    centre_line: tuple[float, float] | None = None
```

Replace the bodies of `to_house` and `to_image`, and add the two methods:

```python
    def centre_col_at(self, row: float) -> float:
        """The centre line's column on ``row``: the paint where it was traced,
        else the ring's centre."""
        if self.centre_line is not None:
            a, b = self.centre_line
            return a + b * row
        self._need_lateral()
        return self.centre_col

    def lateral_x(self, col: float, row: float) -> float:
        """Metres across the sheet of a point on the ice at (col, row)."""
        self._need_lateral()
        return (col - self.centre_col_at(row)) / self.lateral_px_per_m(row)

    def to_house(self, col: float, row: float) -> tuple[float, float]:
        """(keep the existing docstring)"""
        self._need_lateral()
        return self.lateral_x(col, row), self.metres_at(row)

    def to_image(self, x_m: float, y_m: float) -> tuple[float, float]:
        """The view pixel ``(col, row)`` of a point on the ice."""
        self._need_lateral()
        row = self.row_for(y_m)
        return self.centre_col_at(row) + x_m * self.lateral_px_per_m(row), row
```

Append this after `solve_lateral`:

```python
# The painted centre line below the house. Measured 2026-09-24 on the club's
# sheets 1, 2, 3 and 5: a straight image line to under 1 px (the side cameras
# have no lens distortion to bend it), leaning up to 13 px over the rows below
# the house, and interrupted on sheet 3 by a centre-ice logo. Traced twice:
# widely around the ring's centre, then narrowly around that first fit.
_CL_BELOW_TEE_ROWS = 40
_CL_STEP_ROWS = 2
_CL_WIDE_PX = 16
_CL_NARROW_PX = 4
_CL_MARGIN_PX = 12
_CL_MIN_DIP = 3.0
_CL_OUTLIER_PX = 1.5
_CL_MIN_ROWS = 100
_CL_MAX_RMS_PX = 1.5
_CL_MAX_SLOPE = 0.03


def _trace_dips(lum, rows, guide, half):
    """On each row, the darkest sub-pixel column within ``half`` of ``guide(row)``."""
    rs, cs = [], []
    w = lum.shape[1]
    kernel = np.ones(21) / 21
    for r in rows:
        g = int(round(guide(r)))
        lo, hi = g - half - _CL_MARGIN_PX, g + half + _CL_MARGIN_PX + 1
        if lo < 0 or hi > w:
            continue
        band = lum[r - 1:r + 2, lo:hi].mean(axis=0)
        seg = (band - np.convolve(band, kernel, mode="same"))[_CL_MARGIN_PX:-_CL_MARGIN_PX]
        i = int(np.argmin(seg))
        if seg[i] > -_CL_MIN_DIP or i in (0, len(seg) - 1):
            continue
        a, b, c = seg[i - 1], seg[i], seg[i + 1]
        den = a - 2 * b + c
        rs.append(r)
        cs.append(lo + _CL_MARGIN_PX + i + (0.5 * (a - c) / den if den else 0.0))
    return np.array(rs, float), np.array(cs, float)


def _robust_line(rs, cs):
    keep = np.ones(len(rs), bool)
    q = None
    for _ in range(5):
        if keep.sum() < 2:
            return None, keep
        q = np.polyfit(rs[keep], cs[keep], 1)
        keep = np.abs(cs - np.polyval(q, rs)) < _CL_OUTLIER_PX
    return q, keep


def solve_centre_line(plate, view: SideView, name: str = "side") -> SideView:
    """Trace the painted centre line below the house and fit it as a line."""
    view._need_lateral()
    x0, y0, w, h = view.rect
    lum = np.asarray(plate, dtype=np.float32)[y0:y0 + h, x0:x0 + w].mean(axis=2)
    rows = np.arange(int(view.tee_row) + _CL_BELOW_TEE_ROWS, h - 2, _CL_STEP_ROWS)
    rs, cs = _trace_dips(lum, rows, lambda r: view.centre_col, _CL_WIDE_PX)
    q, _ = _robust_line(rs, cs)
    if q is None:
        raise SideViewError(f"{name}: no painted centre line near column {view.centre_col:.0f}")
    rs, cs = _trace_dips(lum, rows, lambda r: np.polyval(q, r), _CL_NARROW_PX)
    q, keep = _robust_line(rs, cs)
    if q is None or keep.sum() < _CL_MIN_ROWS:
        raise SideViewError(f"{name}: the centre line was traced on only "
                            f"{int(keep.sum())} rows, need {_CL_MIN_ROWS}")
    rms = float(np.std(cs[keep] - np.polyval(q, rs[keep])))
    if rms > _CL_MAX_RMS_PX or abs(q[0]) > _CL_MAX_SLOPE:
        raise SideViewError(f"{name}: centre line fit {rms:.2f} px RMS, slope {q[0]:.4f}")
    return dataclasses.replace(view, centre_line=(float(q[1]), float(q[0])))
```

In `analyze.py`, replace `_with_lateral` with:

```python
def _with_lateral(plate, view, name, progress):
    """``view`` with its across-the-sheet calibration, or unchanged if the ring's
    sides could not be read -- which costs that view its brooms and lines and
    nothing else. The hog tripwire is depth alone and never waits on this.
    The painted centre line is then traced; a view whose line cannot be read
    keeps the ring's centre."""
    try:
        view = sideview.solve_lateral(plate, view, name=name)
    except sideview.SideViewError as exc:
        progress(f"{name} view has no lateral calibration, so no brooms from it: {exc}")
        return view
    try:
        return sideview.solve_centre_line(plate, view, name=name)
    except sideview.SideViewError as exc:
        progress(f"{name} view: no painted centre line, lateral figures from the ring's centre: {exc}")
        return view
```

In `_side_calibration`, inside `if v.has_lateral:`, add:

```python
            if v.centre_line is not None:
                d["centre_line"] = [round(v.centre_line[0], 3), round(v.centre_line[1], 6)]
```

In `harvest/sideviews.py`:
- `_view_to_json` adds
  `"centre_line": list(v.centre_line) if v.centre_line is not None else None`.
- `_view_from_json` passes
  `centre_line=tuple(d["centre_line"]) if d.get("centre_line") else None`.

- [ ] **Step 4: Run the tests to make sure they pass.**

Run: `.venv/bin/python -m pytest tests/test_sideview.py tests/test_sideviews.py tests/test_broomtime.py -x`

Expected: PASS. The broom tests use views without a `centre_line`, so they
are unchanged.

- [ ] **Step 5: Commit.**

```bash
git add src/curling_score/geometry/sideview.py src/curling_score/analyze.py src/curling_score/harvest/sideviews.py tests/test_sideview.py tests/test_sideviews.py
git commit -m "sideview: measure across the sheet from the painted centre line, not the ring's centre

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: A track split at a column bin on the hog row is joined, not lost

**Files:**
- Modify: `src/curling_score/detect/longview.py`
- Test: `tests/test_longview.py`

**Interfaces:**
- Produces:
  - `Crossing.track_key: int | None = None`, the key of the timed track;
  - `Crossing.samples: tuple = ()`, filled by Task 3.
- `crossing_from_tracks` accepts track entries of 3 or more elements and reads
  them by index.

- [ ] **Step 1: Write the failing test.** Append this to `tests/test_longview.py`:

```python
class TestATrackSplitAtABinBoundary:
    """AEqL game 2, end 3, rock 3: the stone's column crossed 480 -- a 120-px
    key boundary -- exactly on the hog row, so its track came apart there and
    neither half straddled the line. It was timed "never reached" and the
    shot lost its split."""

    def _halves(self):
        pts = [(10.0 + i * 0.1, 504.0 + i * 3.3, 52.0) for i in range(12)]
        return {3: [p for p in pts if p[1] < 520.0], 4: [p for p in pts if p[1] >= 520.0]}

    def test_the_halves_are_joined_and_timed(self):
        got = longview.crossing_from_tracks(self._halves(), VIEW, offset_s=0.0)
        assert got.key == longview.KEY_OK, got.reason
        assert got.t == pytest.approx(10.0 + 0.4 + (520.0 - 517.2) / 3.3 * 0.1, abs=1e-6)
        assert got.track_key == 3

    def test_the_later_half_alone_still_never_reaches(self):
        got = longview.crossing_from_tracks({4: self._halves()[4]}, VIEW, offset_s=0.0)
        assert got.key == longview.KEY_NEVER_REACHED

    def test_a_crossing_within_one_key_is_timed_from_it_alone(self):
        one = {3: [(10.0 + i * 0.1, 504.0 + i * 3.3, 52.0) for i in range(12)]}
        got = longview.crossing_from_tracks(one, VIEW, offset_s=0.0)
        assert got.key == longview.KEY_OK and got.track_key == 3

    def test_entries_may_carry_a_column_after_the_width(self):
        one = {3: [(10.0 + i * 0.1, 504.0 + i * 3.3, 52.0, 300.0 + i) for i in range(12)]}
        assert longview.crossing_from_tracks(one, VIEW, offset_s=0.0).key == longview.KEY_OK
```

- [ ] **Step 2: Run it to make sure it fails.**

Run: `.venv/bin/python -m pytest tests/test_longview.py -k SplitAtABinBoundary -x`

Expected: FAIL. `test_the_halves_are_joined_and_timed` gets `never_reached`,
and the 4-tuple test raises `ValueError: too many values to unpack`.

- [ ] **Step 3: Implement it.**

In `Crossing`, after `speed_m_s`, add:

```python
    # Which key of the tracks dict was timed -- the left one when two
    # neighbours were joined. Read by `game/linetime.py` to find the stone
    # again among all the samples.
    track_key: int | None = None
    # Every accepted detection of the rock's colour in the window, as
    # (t, cx, edge_row, body_px), for the trained proposer only (Task 3).
    samples: tuple = ()
```

Replace `_crossing_index` with:

```python
def _crossing_index(track, hog_row):
    """Index ``i`` such that ``track[i], track[i + 1]`` straddle ``hog_row``."""
    for i, (a, b) in enumerate(zip(track, track[1:])):
        r0, r1 = a[1], b[1]
        if r0 <= hog_row <= r1 and r1 != r0:
            return i
    return None
```

In `crossing_from_tracks`, replace everything from `moving = [...]` through
`track = crossed[0]` with the block below. Keep the existing long comment on
the two-crossers refusal. This also adds a module-level helper just above the
function:

```python
def _moving_and_crossed(tracks, view):
    moving = [(k, tr) for k, tr in tracks.items()
              if len(tr) >= _MIN_SAMPLES and tr[-1][1] > tr[0][1]]
    crossed = [(k, tr) for k, tr in moving if tr[0][1] <= view.hog_row <= tr[-1][1]]
    return moving, crossed
```

```python
    moving, crossed = _moving_and_crossed(tracks, view)
    if not crossed:
        # A stone's column can cross one of the 120-px keys' boundaries right
        # at the hog row -- AEqL game 2, end 3, rock 3 did, at column 480 --
        # and then neither half of its track straddles the line. Only when
        # nothing crossed, join each key to its neighbour and ask again; a
        # join that finds exactly one crosser is that stone.
        joined = {k: sorted(tracks[k] + tracks[k + 1]) for k in tracks if k + 1 in tracks}
        m2, c2 = _moving_and_crossed(joined, view)
        if len(c2) == 1:
            moving, crossed = m2, c2
    if not moving:
        return Crossing(None, "no candidate that could be a stone in flight", KEY_NO_CANDIDATE)
    if len(crossed) > 1:
        # (existing comment unchanged)
        return Crossing(None, f"two candidates crossed the line ({len(crossed)})", KEY_AMBIGUOUS)
    if not crossed:
        return Crossing(None, "the stone never reached the line", KEY_NEVER_REACHED)
    key, track = crossed[0]
```

Make the rest of the function index-based:
- the steadiness check becomes
  `if any(q[1] - p[1] < -1.0 for p, q in zip(local, local[1:])):`;
- the interpolation becomes
  `(t0, r0), (t1, r1) = track[idx][:2], track[idx + 1][:2]`;
- the width median becomes `np.median([p[2] for p in track])`;
- every return after `key, track = crossed[0]` gains `track_key=key`: the
  `unsteady` and `bad_speed` refusals as well as the successful one. That way
  a refused big-weight hit still names its stone.

- [ ] **Step 4: Run the tests to make sure they pass, then run the neighbours.**

Run: `.venv/bin/python -m pytest tests/test_longview.py tests/test_hogtime.py -x --deselect tests/test_longview.py::TestAgainstHandMarkedCrossings`

Expected: PASS. `TestAgainstHandMarkedCrossings` already fails on main, as
the longview-handmark-tests-fail memory note says, so leave it deselected.

- [ ] **Step 5: Commit.**

```bash
git add src/curling_score/detect/longview.py tests/test_longview.py
git commit -m "longview: a stone split across a column key at the hog row is joined and timed, not lost

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The side detector keeps each box's column; hogtime keeps the crossing

**Files:**
- Modify: `src/curling_score/detect/sidemodel.py`
- Modify: `src/curling_score/game/hogtime.py`, `src/curling_score/game/shots.py`
- Modify: `scripts/ds13/measure_hogrow.py:69` (the tuple unpack)
- Create: `tests/test_sidemodel.py`
- Test: `tests/test_hogtime.py`

**Interfaces:**
- Consumes: from Task 2, `Crossing.samples` and `Crossing.track_key`.
- Produces:
  - `propose(...)` returns entries `(t, edge_row, body_px, cx)`.
  - `find_in_frames(...)` returns a `Crossing` whose `samples` are
    `(t, cx, edge_row, body_px)`, sorted by t.
  - `detect_band(model, frames, times, lo, hi, color, *, imgsz=800, conf=CONF_MIN) -> list[list[tuple]]`
    returns, per frame, `[(cx, bottom_row, width, conf), ...]` in view rows.
  - `default_model()` returns the YOLO object or `None`.
  - `Shot.hog_crossing: object = None` is set by hogtime whenever the
    crossing carries samples, even when it was refused.

- [ ] **Step 1: Write the failing tests.** Create `tests/test_sidemodel.py`:

```python
"""The trained side-view proposer: what it keeps from each box."""
import numpy as np
import pytest

from curling_score.detect import longview, sidemodel
from curling_score.geometry.sideview import SideView
from curling_score.harvest import sidepool

VIEW = SideView(rect=(0, 0, 810, 1080), tee_row=430.0, hog_row=520.0)


class _Arr:
    def __init__(self, a):
        self.a = np.asarray(a, float)

    def cpu(self):
        return self

    def numpy(self):
        return self.a


class _Boxes:
    def __init__(self, rows):
        rows = np.asarray(rows, float).reshape(-1, 6)
        self.xyxy, self.cls, self.conf = _Arr(rows[:, :4]), _Arr(rows[:, 4]), _Arr(rows[:, 5])


class _Res:
    def __init__(self, rows):
        self.boxes = _Boxes(rows)


class FakeModel:
    """Returns, for frame k, the boxes ``per_frame[k]`` as (x0,y0,x1,y1,cls,conf) in CROP rows."""

    def __init__(self, per_frame):
        self.per_frame, self.i = per_frame, 0

    def predict(self, crops, imgsz, conf, verbose):
        out = [_Res(self.per_frame[self.i + k]) for k in range(len(crops))]
        self.i += len(crops)
        return out


def _moving_stone(n=12, cls=0):
    lo = max(0, sidepool.band_crop(VIEW)[0])
    frames = [np.zeros((1080, 810, 3), np.uint8)] * n
    times = [10.0 + i * 0.1 for i in range(n)]
    per = []
    for i in range(n):
        edge = 504.0 + i * 3.3
        w = VIEW.stone_width_at(edge, longview.STONE_WIDTH_AT_HOG_PX)
        cx = 300.0 + 2.0 * i
        per.append([[cx - w / 2, edge - lo - 30, cx + w / 2, edge - lo, cls, 0.9]])
    return frames, times, per


class TestItKeepsTheColumn:
    def test_each_track_entry_carries_the_box_centre(self):
        frames, times, per = _moving_stone()
        tracks = sidemodel.propose(FakeModel(per), frames, VIEW, "red", times)
        (entries,) = tracks.values()
        assert [e[3] for e in entries] == pytest.approx([300.0 + 2.0 * i for i in range(12)])

    def test_the_crossing_carries_every_sample_and_its_key(self):
        frames, times, per = _moving_stone()
        got = sidemodel.find_in_frames(FakeModel(per), frames, VIEW, "red", times)
        assert got.key == longview.KEY_OK, got.reason
        assert got.track_key == 2          # int(300 // 120)
        assert len(got.samples) == 12
        t, cx, row, w = got.samples[0]
        assert (t, cx, row) == pytest.approx((10.0, 300.0, 504.0))


class TestDetectBand:
    def test_boxes_come_back_per_frame_in_view_rows_for_the_colour_asked(self):
        frames = [np.zeros((1080, 810, 3), np.uint8)] * 2
        per = [[[100, 10, 150, 40, 0, 0.9], [200, 10, 250, 40, 1, 0.8]], []]
        got = sidemodel.detect_band(FakeModel(per), frames, [0.0, 0.2], 300, 700, "red")
        assert got == [[pytest.approx((125.0, 340.0, 50.0, 0.9))], []]
```

Append this to `tests/test_hogtime.py`, reusing its existing helpers for
building a shot and an explicit `find`. The test below builds them inline:

```python
def test_the_crossing_is_kept_on_the_shot_even_when_refused():
    from types import SimpleNamespace
    from curling_score.detect import longview
    from curling_score.game import hogtime
    from curling_score.geometry.sideview import SideView
    view = SideView(rect=(0, 0, 810, 1080), tee_row=430.0, hog_row=520.0)
    refused = longview.Crossing(None, "speed 3.44 m/s is not a delivery", longview.KEY_BAD_SPEED,
                                track_key=3, samples=((1.0, 300.0, 510.0, 52.0),))
    shot = SimpleNamespace(missing=False, release=SimpleNamespace(t=0.0), delivery=None,
                           color="red", t_hog_s=None, v_hog_m_s=None, hog_crossing=None)
    hogtime.time_hog_crossings([shot], "v.mp4", view, find=lambda *a: refused)
    assert shot.hog_crossing is refused and shot.t_hog_s is None
```

- [ ] **Step 2: Run them to make sure they fail.**

Run: `.venv/bin/python -m pytest tests/test_sidemodel.py tests/test_hogtime.py -x`

Expected: FAIL. The entries have 3 elements, there is no `detect_band`, and
`hog_crossing` is never set.

- [ ] **Step 3: Implement it.**

In `sidemodel.propose`:
- the docstring's return becomes `{track_key: [(t, edge_row, body_px, cx), ...]}`;
- the append becomes `tracks.setdefault(key, []).append((t, edge_row, body_px, cx))`.

Replace `find_in_frames` with:

```python
def find_in_frames(model, frames, view, color: str, times,
                   offset_s: float = None) -> longview.Crossing:
    """Time the crossing of ``view.hog_row`` with the model as proposer, and
    keep every sample -- the column included -- for the line pass."""
    tracks = propose(model, frames, view, color, times)
    got = longview.crossing_from_tracks(
        tracks, view, offset_s=OFFSET_S if offset_s is None else offset_s)
    samples = tuple(sorted((p[0], p[3], p[1], p[2]) for tr in tracks.values() for p in tr))
    if got.track_key is None and samples:
        # A refusal still names the stone's key -- the longest rising track --
        # so the line pass can find it, e.g. a big-weight hit over the speed bound.
        rising = [(len(tr), k) for k, tr in tracks.items() if tr[-1][1] > tr[0][1]]
        if rising:
            got = dataclasses.replace(got, track_key=max(rising)[1])
    return dataclasses.replace(got, samples=samples)
```

Add `import dataclasses` to the imports. Then add:

```python
def detect_band(model, frames, times, lo, hi, color, *, imgsz=800, conf=CONF_MIN):
    """Every box of ``color`` in rows ``lo:hi`` of each frame, in view rows:
    per frame, a list of (cx, bottom_row, width, conf). The line pass reads the
    hack, the window past hogtime's and the destination camera through this."""
    want = _CLASS_FOR[color]
    lo = max(0, int(lo))
    crops = []
    for frame in frames:
        arr = np.asarray(frame)
        crops.append(np.ascontiguousarray(arr[lo:min(arr.shape[0], int(hi)), :, ::-1]))
    out = []
    for i in range(0, len(crops), BATCH):
        for res in model.predict(crops[i:i + BATCH], imgsz=imgsz, conf=conf, verbose=False):
            boxes = []
            if res.boxes is not None:
                for b, c, cf in zip(res.boxes.xyxy.cpu().numpy(), res.boxes.cls.cpu().numpy(),
                                    res.boxes.conf.cpu().numpy()):
                    if int(c) != want or float(cf) < conf:
                        continue
                    bx0, _by0, bx1, by1 = (float(v) for v in b)
                    boxes.append(((bx0 + bx1) / 2, by1 + lo, bx1 - bx0, float(cf)))
            out.append(boxes)
    return out


def default_model():
    """The side-view detector itself, or None when none is configured."""
    from curling_score import weights as weights_mod
    path = weights_mod.side_path()
    return None if path is None else _load(str(path))
```

In `game/shots.py`, after `t_hog_s`, add:

```python
    # hogtime's whole verdict on the side view, samples included -- kept even
    # when it refused to time the crossing, because the line pass
    # (`linetime`) measures a big-weight hit the speed bound turns away.
    hog_crossing: object = None
```

In `hogtime.time_hog_crossings`, replace `got = find(...)` and the `if got:`
block with:

```python
        got = find(video, view, shot.color, t0, t1)
        if getattr(got, "samples", ()):
            shot.hog_crossing = got
        if got:
            shot.t_hog_s = got.t
            # Carried so `split.long_split` can bound the pairing without a
            # release track. See `speed_at_hog` below.
            shot.v_hog_m_s = got.speed_m_s
```

In `scripts/ds13/measure_hogrow.py:69`, change
`(t0, r0, _), (t1, r1, _) = pair` to `(t0, r0, *_), (t1, r1, *_) = pair`.

- [ ] **Step 4: Run the tests to make sure they pass.**

Run: `.venv/bin/python -m pytest tests/test_sidemodel.py tests/test_hogtime.py tests/test_longview.py -x --deselect tests/test_longview.py::TestAgainstHandMarkedCrossings`

Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add src/curling_score/detect/sidemodel.py src/curling_score/game/hogtime.py src/curling_score/game/shots.py scripts/ds13/measure_hogrow.py tests/test_sidemodel.py tests/test_hogtime.py
git commit -m "sidemodel: keep each box's column, and hogtime keeps its whole verdict on the shot

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `linetime`: the line, the miss, curl and side (pure)

**Files:**
- Create: `src/curling_score/game/linetime.py`
- Test: `tests/test_linetime.py`

**Interfaces:**
- Produces:
  - `Fit(a, b, n, rms)` with `.x(y)`;
  - `Line` (frozen dataclass, fields below);
  - `fit_line(track) -> Fit | None`, where track is `[(t, x, y, y_past_tee)]`
    in the destination frame;
  - `aim_x(start, broom, y)`;
  - `curl_of(fit, end_xy)`;
  - `side_of(miss, curl)`;
  - `confirmed_by(path_yx, fit)`;
  - `thin(points_yx, step=THIN_M)`;
  - `measure(fit, track, start, broom, rest=None, path=()) -> Line`.

  `path` is `[(y, x)]` in travel order.

- [ ] **Step 1: Write the failing tests.** Create `tests/test_linetime.py`:

```python
"""Was the rock thrown at the broom: the line past the hog line, against it."""
import pytest

from curling_score.game import linetime as L
from curling_score.geometry import constants as C

TEE = C.TEE_TO_TEE_M


def straight(a, b, lo=6.5, hi=9.9, n=40):
    """A track on x = a + b*y, sampled between lo and hi metres past the throwing tee."""
    out = []
    for i in range(n):
        yp = lo + (hi - lo) * i / (n - 1)
        y = TEE - yp
        out.append((i / 30.0, a + b * y, y, yp))
    return out


class TestFitLine:
    def test_it_recovers_a_straight_line(self):
        fit = L.fit_line(straight(0.3, -0.02))
        assert (fit.a, fit.b, fit.n) == (pytest.approx(0.3), pytest.approx(-0.02), 40)
        assert fit.rms == pytest.approx(0.0, abs=1e-9)

    def test_samples_before_the_hog_line_are_left_out(self):
        slide = [(0.0, 5.0, TEE - 3.0, 3.0)]            # the slide: in the hand, not free
        assert L.fit_line(straight(0.3, -0.02) + slide).a == pytest.approx(0.3)

    def test_too_few_or_too_short_is_no_fit(self):
        assert L.fit_line(straight(0.3, -0.02, n=10)) is None
        assert L.fit_line(straight(0.3, -0.02, lo=6.5, hi=8.5)) is None


class TestMeasure:
    START, BROOM = (0.0, 38.405), (1.0, 0.0)

    def aimed(self, shift=0.0):
        b = (self.BROOM[0] - self.START[0]) / (self.BROOM[1] - self.START[1])
        a = self.START[0] - b * self.START[1] + shift
        return straight(a, b)

    def test_a_rock_thrown_at_the_broom_misses_by_nothing(self):
        track = self.aimed()
        line = L.measure(L.fit_line(track), track, self.START, self.BROOM)
        assert line.miss == pytest.approx(0.0, abs=1e-9)
        assert line.at_hog_offset == pytest.approx(0.0, abs=1e-9)

    def test_a_parallel_line_ten_centimetres_out_misses_by_ten(self):
        track = self.aimed(0.10)
        line = L.measure(L.fit_line(track), track, self.START, self.BROOM)
        assert (line.miss, line.at_hog_offset) == (pytest.approx(0.10), pytest.approx(0.10))

    def test_no_start_means_no_offset_at_the_hog_line(self):
        track = self.aimed()
        assert L.measure(L.fit_line(track), track, None, self.BROOM).at_hog_offset is None

    def test_wide_is_the_side_away_from_the_curl(self):
        track = self.aimed(0.30)                        # 30 cm right of a broom on the right
        rest = (0.2, 0.5)                               # ...and it curled back left to rest
        line = L.measure(L.fit_line(track), track, self.START, self.BROOM, rest=rest)
        assert (line.curl, line.side) == ("left", "wide")

    def test_narrow_is_the_side_the_rock_curls_toward(self):
        track = self.aimed(-0.30)
        line = L.measure(L.fit_line(track), track, self.START, self.BROOM, rest=(0.2, 0.5))
        assert (line.curl, line.side) == ("left", "narrow")

    def test_a_miss_of_nothing_is_still_a_side_and_no_error(self):
        assert L.side_of(0.0, "left") == "narrow"

    def test_no_curl_direction_is_no_side(self):
        track = self.aimed(0.30)
        line = L.measure(L.fit_line(track), track, self.START, self.BROOM)
        assert (line.curl, line.side) == (None, None)


class TestConfirmedBy:
    FIT = L.Fit(a=0.5, b=0.01, n=40, rms=0.002)

    def path(self, off=0.0, top=20.0):
        return [(y, self.FIT.x(y) + off) for y in [top - 0.25 * i for i in range(40)]]

    def test_a_path_on_the_line_confirms_it(self):
        assert L.confirmed_by(self.path(), self.FIT) is True

    def test_a_path_thirty_centimetres_off_disagrees(self):
        assert L.confirmed_by(self.path(0.30), self.FIT) is False

    def test_a_path_first_seen_too_near_the_house_says_nothing(self):
        assert L.confirmed_by(self.path(top=10.0), self.FIT) is None
        assert L.confirmed_by([], self.FIT) is None


class TestThin:
    def test_about_one_point_per_half_metre_and_the_last_kept(self):
        pts = [(20.0 - 0.1 * i, 0.0) for i in range(100)]
        got = L.thin(pts)
        assert len(got) == 21 and got[-1] == pts[-1]
```

- [ ] **Step 2: Run them to make sure they fail.**

Run: `.venv/bin/python -m pytest tests/test_linetime.py -x`

Expected: FAIL with `ModuleNotFoundError: curling_score.game.linetime`.

- [ ] **Step 3: Implement it.** Create `src/curling_score/game/linetime.py`:

```python
"""Was the rock thrown at the broom?

The stone's line just past the throwing hog line -- read from the side camera
that times that crossing, where the stone has certainly left the hand and has
barely begun to curl -- extended to the skip's broom. Then, from the camera
behind the thrower, where the stone actually went, which confirms the line and
says which way it curled.

Everything here is in the timeline's frame: metres from the destination tee,
+y up-sheet toward the thrower, +x the thrower's right.

Attach-only, like ``hogtime`` and ``broomtime``: it may give a shot a
``line`` and can never add, drop or renumber one. Measured on four games on
2026-09-24 (``~/curling-work/line-spike``): the line fits straight to
0.2-0.4 cm and the camera behind the thrower agrees with it to about 4 cm.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from curling_score.geometry import constants as C

TEE_Y = C.TEE_TO_TEE_M                          # the throwing tee
HOG_Y = C.TEE_TO_TEE_M - C.TEE_TO_HOGLINE_M     # the throwing hog line
# Free flight only: past the hog line the stone has certainly been released,
# and within 10 m of the tee it has barely curled.
FIT_PAST_TEE_M = (C.TEE_TO_HOGLINE_M, 10.0)
FIT_MIN_N = 15
FIT_MIN_SPAN_M = 2.5
CONFIRM_FROM_M = 12.0       # the path must be seen at least this far out
CONFIRM_FIRST_M = 4.0       # ...and its first this-many metres compared
CONFIRM_TOL_M = 0.10
CURL_MIN_M = 0.05
THIN_M = 0.5


@dataclass(frozen=True)
class Fit:
    a: float
    b: float
    n: int
    rms: float

    def x(self, y: float) -> float:
        return self.a + self.b * y


@dataclass(frozen=True)
class Line:
    start: tuple | None          # (x, y): the stone before push-off
    at_hog_x: float
    at_hog_offset: float | None  # at_hog_x less the start-to-broom line there
    at_broom_x: float
    miss: float                  # at_broom_x - broom x, signed
    curl: str | None             # "left" | "right"
    side: str | None             # "wide" | "narrow"
    confirmed: bool | None
    hog_path: tuple              # ((y, x), ...) thinned, travel order
    path: tuple                  # ((y, x), ...) thinned, travel order
    fit_n: int
    fit_rms: float


def fit_line(track) -> Fit | None:
    """A straight x(y) through the free-flight samples, or None."""
    lo, hi = FIT_PAST_TEE_M
    pts = [(y, x) for _t, x, y, yp in track if lo <= yp <= hi]
    if len(pts) < FIT_MIN_N:
        return None
    ys = np.array([p[0] for p in pts]); xs = np.array([p[1] for p in pts])
    if np.ptp(ys) < FIT_MIN_SPAN_M:
        return None
    b, a = np.polyfit(ys, xs, 1)
    return Fit(a=float(a), b=float(b), n=len(pts), rms=float(np.std(xs - (a + b * ys))))


def aim_x(start, broom, y: float) -> float:
    """The start-to-broom line's x at depth ``y``."""
    (sx, sy), (bx, by) = start, broom
    return sx + (bx - sx) * (y - sy) / (by - sy)


def curl_of(fit: Fit, end) -> str | None:
    """Which way curl took the rock: from its thrown line to where it ended."""
    if end is None:
        return None
    d = end[0] - fit.x(end[1])
    if abs(d) < CURL_MIN_M:
        return None
    return "right" if d > 0 else "left"


def side_of(miss: float, curl: str | None) -> str | None:
    """Wide is the side away from the curl; narrow the side it curls toward."""
    if curl is None:
        return None
    toward = 1.0 if curl == "right" else -1.0
    return "wide" if np.sign(miss) == -toward else "narrow"


def confirmed_by(path, fit: Fit) -> bool | None:
    """Does the camera behind the thrower see the rock on the fitted line?"""
    if not path or path[0][0] < CONFIRM_FROM_M:
        return None
    first = [(y, x) for y, x in path if y >= path[0][0] - CONFIRM_FIRST_M]
    dev = float(np.median([abs(x - fit.x(y)) for y, x in first]))
    return dev <= CONFIRM_TOL_M


def thin(points, step: float = THIN_M) -> tuple:
    """About one (y, x) per ``step`` metres of travel, the last always kept."""
    if not points:
        return ()
    out = [points[0]]
    for p in points[1:]:
        if abs(p[0] - out[-1][0]) >= step - 1e-9:     # 5 x 0.1 must count as 0.5
            out.append(p)
    if out[-1] != points[-1]:
        out.append(points[-1])
    return tuple(out)


def measure(fit: Fit, track, start, broom, rest=None, path=()) -> Line:
    """Everything the Detail pane says about one rock, from its pieces."""
    bx, by = broom
    at_hog_x = fit.x(HOG_Y)
    offset = None if start is None else at_hog_x - aim_x(start, broom, HOG_Y)
    at_broom_x = fit.x(by)
    miss = at_broom_x - bx
    end = rest if rest is not None else ((path[-1][1], path[-1][0]) if path else None)
    curl = curl_of(fit, end)
    return Line(start=start, at_hog_x=at_hog_x, at_hog_offset=offset,
                at_broom_x=at_broom_x, miss=miss, curl=curl, side=side_of(miss, curl),
                confirmed=confirmed_by(list(path), fit),
                hog_path=thin([(y, x) for _t, x, y, _yp in track]),
                path=thin(list(path)), fit_n=fit.n, fit_rms=fit.rms)
```

- [ ] **Step 4: Run the tests to make sure they pass.**

Run: `.venv/bin/python -m pytest tests/test_linetime.py -x`

Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add src/curling_score/game/linetime.py tests/test_linetime.py
git commit -m "linetime: the thrown line past the hog line, the miss at the broom, curl and side

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: `linetime`: the stone's track from the hog camera

**Files:**
- Modify: `src/curling_score/game/linetime.py`
- Test: `tests/test_linetime.py`

**Interfaces:**
- Consumes:
  - `SideView.lateral_x`, `metres_at` and `row_for` (Task 1);
  - `Crossing.samples` and `track_key` (Tasks 2 and 3);
  - `sidepool.band_crop`.
- Produces:
  - `to_destination(view, cx, edge_row) -> (x, y, y_past_tee)`;
  - `relink(samples, key, t_seed, fps=30.0, max_gap=6)`, where samples are
    `(t, cx, edge_row, body_px)`;
  - `hog_track(crossing, view, extra=()) -> [(t, x, y, y_past_tee)]`;
  - `CROP_EDGE_ROWS = 6`.

- [ ] **Step 1: Write the failing tests.** Append this to `tests/test_linetime.py`:

```python
from curling_score.detect import longview
from curling_score.geometry.sideview import SideView
from curling_score.harvest import sidepool

HOG_VIEW = SideView(rect=(0, 0, 810, 1080), tee_row=430.0, hog_row=520.0,
                    centre_col=390.0, lat_px_per_m_at_tee=150.0, centre_line=(386.0, 0.004))


def sample_at(view, x_dest, yp_centre, t):
    """The (t, cx, edge_row, body_px) the proposer gives for a stone centred at
    destination x and yp metres past the throwing tee."""
    edge = view.row_for(yp_centre + C.STONE_RADIUS_M)
    rc = view.row_for(yp_centre)
    cx = view.centre_col_at(rc) + (-x_dest) * view.lateral_px_per_m(rc)
    return (t, cx, edge, 52.0)


def crossing_for(samples, t=10.3):
    key = int(samples[len(samples) // 2][1] // 120)
    return longview.Crossing(t, "ok", longview.KEY_OK, track_key=key, samples=tuple(samples))


class TestHogTrack:
    def stone(self, n=60, x0=-0.2, dx=-0.03):
        return [sample_at(HOG_VIEW, x0 + dx * (3.0 + 0.12 * i), 3.0 + 0.12 * i, 9.0 + i / 30)
                for i in range(n)]

    def test_it_recovers_the_stone_in_the_destination_frame(self):
        track = L.hog_track(crossing_for(self.stone()), HOG_VIEW)
        _t, x, y, yp = track[10]
        assert yp == pytest.approx(3.0 + 0.12 * 10, abs=1e-6)
        assert y == pytest.approx(TEE - yp, abs=1e-6)
        assert x == pytest.approx(-0.2 - 0.03 * yp, abs=1e-6)

    def test_another_stone_far_across_is_not_linked(self):
        other = [sample_at(HOG_VIEW, 1.5, 5.0, 9.0 + i / 30) for i in range(60)]
        track = L.hog_track(crossing_for(self.stone() + other), HOG_VIEW)
        assert all(x < 0.5 for _t, x, _y, _yp in track)

    def test_samples_at_the_crop_s_bottom_edge_are_dropped(self):
        bottom = sidepool.band_crop(HOG_VIEW)[1]
        edge = (99.0, 300.0, bottom - 2.0, 52.0)
        track = L.hog_track(crossing_for(self.stone() + [edge]), HOG_VIEW)
        assert all(t != 99.0 for t, *_ in track)

    def test_a_colour_scan_crossing_has_no_track(self):
        assert L.hog_track(longview.Crossing(10.0, "ok", longview.KEY_OK), HOG_VIEW) == []
```

- [ ] **Step 2: Run them to make sure they fail.**

Run: `.venv/bin/python -m pytest tests/test_linetime.py -k HogTrack -x`

Expected: FAIL with `AttributeError: ... has no attribute 'hog_track'`.

- [ ] **Step 3: Implement it.** Add this to `linetime.py`. The import goes at
the top and the rest after `measure`.

```python
from curling_score.harvest import sidepool

CROP_EDGE_ROWS = 6          # the box is clipped at the band's bottom edge


def to_destination(view, cx: float, edge_row: float):
    """A hog-camera box's bottom-centre as the stone's centre, in the
    destination frame, with its distance past the throwing tee. The camera
    faces the thrower: its image-left is the thrower's right, so x flips."""
    yp = view.metres_at(edge_row) - C.STONE_RADIUS_M
    x_view = view.lateral_x(cx, view.row_for(yp))
    return -x_view, TEE_Y - yp, yp


def relink(samples, key, t_seed, fps: float = 30.0, max_gap: int = 6):
    """The stone's samples frame to frame by continuity, both ways from the
    one nearest ``t_seed`` in column key ``key`` -- not by key, which a stone
    drifting across the sheet leaves."""
    by_t: dict = {}
    for s in samples:
        by_t.setdefault(round(s[0], 4), []).append(s)
    ts = sorted(by_t)
    seeds = [s for s in samples if int(s[1] // 120) == key]
    if not seeds:
        return []
    seed = min(seeds, key=lambda s: abs(s[0] - t_seed))
    chain = [seed]
    for direction in (1, -1):
        cur, i, gap = seed, ts.index(round(seed[0], 4)), 0
        while 0 <= i + direction < len(ts):
            i += direction
            steps = abs(ts[i] - cur[0]) * fps
            ok = [s for s in by_t[ts[i]]
                  if abs(s[1] - cur[1]) <= 8 + 3 * steps
                  and -3 * steps <= (s[2] - cur[2]) * direction <= 8 * steps + 4]
            if not ok:
                gap += 1
                if gap > max_gap:
                    break
                continue
            gap = 0
            cur = min(ok, key=lambda s: abs(s[1] - cur[1]) + abs(s[2] - cur[2]))
            chain.append(cur)
    return sorted(chain)


def hog_track(crossing, view, extra=()):
    """The rock through the throwing hog line as [(t, x, y, y_past_tee)]."""
    # `is None`, not `not crossing`: a refused crossing is falsy (no time) but
    # may still carry the stone's samples -- a big-weight hit over the speed bound.
    if crossing is None or getattr(crossing, "track_key", None) is None \
            or not getattr(crossing, "samples", ()):
        return []
    edge = sidepool.band_crop(view)[1] - CROP_EDGE_ROWS
    samples = [s for s in list(crossing.samples) + list(extra) if s[2] < edge]
    t_seed = crossing.t if crossing.t is not None else samples[len(samples) // 2][0]
    return [(t, *to_destination(view, cx, row)) for t, cx, row, _w in
            relink(samples, crossing.track_key, t_seed)]
```


- [ ] **Step 4: Run the tests to make sure they pass.**

Run: `.venv/bin/python -m pytest tests/test_linetime.py -x`

Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add src/curling_score/game/linetime.py tests/test_linetime.py
git commit -m "linetime: the rock through the throwing hog line, in the destination frame

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: `linetime`: where the stone sat before push-off

**Files:**
- Modify: `src/curling_score/game/linetime.py`
- Test: `tests/test_linetime.py`

**Interfaces:**
- Consumes: `sidemodel.detect_band` (Task 3), passed in as `detect`.
- Produces:
  - `pick_start(boxes, view) -> (x, y) | None`, where boxes are
    `(cx, bottom_row, width, conf)`;
  - `find_start(model, video, view, color, t_release, *, decode, detect)`.

- [ ] **Step 1: Write the failing tests.** Append this to `tests/test_linetime.py`:

```python
class TestPickStart:
    def box_at(self, x_dest, behind_tee_m):
        yp = -behind_tee_m
        rc = HOG_VIEW.row_for(yp)
        cx = HOG_VIEW.centre_col_at(rc) + (-x_dest) * HOG_VIEW.lateral_px_per_m(rc)
        return (cx, HOG_VIEW.row_for(yp + C.STONE_RADIUS_M), 40.0, 0.9)

    def test_the_stone_in_front_of_the_left_hack(self):
        got = L.pick_start([self.box_at(-0.15, 3.2)] * 5, HOG_VIEW)
        assert got == (pytest.approx(-0.15, abs=1e-6), pytest.approx(TEE + 3.2, abs=1e-6))

    def test_the_slide_and_the_far_side_are_ignored(self):
        boxes = [self.box_at(-0.15, 3.2)] * 3 + [self.box_at(-0.1, 1.0)] * 4 + [self.box_at(0.9, 3.2)] * 4
        assert L.pick_start(boxes, HOG_VIEW)[0] == pytest.approx(-0.15, abs=1e-6)

    def test_fewer_than_three_sightings_is_no_start(self):
        assert L.pick_start([self.box_at(-0.15, 3.2)] * 2, HOG_VIEW) is None
```

- [ ] **Step 2: Run them to make sure they fail.**

Run: `.venv/bin/python -m pytest tests/test_linetime.py -k PickStart -x`

Expected: FAIL (no `pick_start`).

- [ ] **Step 3: Implement it.** Add this to `linetime.py`:

```python
START_WINDOW_S = (-3.0, -0.2)       # before the release: the stone at rest in front of the hack
START_FPS = 5.0
START_BEHIND_TEE_M = (C.TEE_TO_HACKLINE_M - 1.6, C.TEE_TO_HACKLINE_M + 0.6)
START_MAX_X_M = 0.6
START_MIN_N = 3


def pick_start(boxes, view):
    """The median of the stones sitting in front of the hack, or None."""
    lo, hi = -START_BEHIND_TEE_M[1], -START_BEHIND_TEE_M[0]
    xs, ys = [], []
    for cx, row, _w, _c in boxes:
        yp = view.metres_at(row) - C.STONE_RADIUS_M
        if not lo <= yp <= hi:
            continue
        x_view = view.lateral_x(cx, view.row_for(yp))
        if abs(x_view) > START_MAX_X_M:
            continue
        xs.append(-x_view); ys.append(TEE_Y - yp)
    if len(xs) < START_MIN_N:
        return None
    return float(np.median(xs)), float(np.median(ys))


def find_start(model, video, view, color, t_release, *, decode, detect):
    """Read the stone at rest before the push, behind the throwing tee."""
    frames, times = decode(video, view.rect, t_release + START_WINDOW_S[0],
                           t_release + START_WINDOW_S[1], START_FPS)
    if not len(frames):
        return None
    top = int(view.row_for(-(C.TEE_TO_HACKLINE_M + 1.0))) - 30
    bot = int(view.tee_row) + 10
    return pick_start([b for per in detect(model, frames, times, top, bot, color) for b in per], view)
```

- [ ] **Step 4: Run the tests to make sure they pass.**

Run: `.venv/bin/python -m pytest tests/test_linetime.py -x`

Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add src/curling_score/game/linetime.py tests/test_linetime.py
git commit -m "linetime: where the stone sat before the push, which says which hack

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: `linetime`: the path from behind the thrower

**Files:**
- Modify: `src/curling_score/game/linetime.py`
- Test: `tests/test_linetime.py`

**Interfaces:**
- Consumes: the `Fit` from Task 4; the destination `SideView`.
- Produces:
  - `path_points(boxes, times, view) -> list[list[tuple]]`, one per frame, of
    `(t, x, y, cx, row, conf)`;
  - `chain(times, per, fit) -> [(y, x)]` in travel order;
  - `find_path(model, video, view, color, t_hog, t_rest, fit, *, decode, detect) -> [(y, x)]`.

- [ ] **Step 1: Write the failing tests.** Append this to `tests/test_linetime.py`:

```python
class TestChain:
    FIT = L.Fit(a=0.3, b=0.02, n=40, rms=0.002)

    def frames(self, n=80, gap=(), static=None, stray_first=False):
        times = [i * 0.2 for i in range(n)]
        per = []
        for i, t in enumerate(times):
            dets = []
            y = 23.5 - 2.2 * t + 0.05 * t * t                  # slowing down
            if y > 1.0 and i not in gap:
                dets.append((t, self.FIT.x(y) - 0.002 * t * t, y, 0, 0, 0.9))
            if static is not None:
                dets.append((t, *static, 0, 0, 0.8))
            if stray_first and i == 0:
                dets = [(t, self.FIT.x(21.0), 21.0, 0, 0, 0.6)]
            per.append(dets)
        return times, per

    def test_it_follows_the_moving_stone_not_one_at_rest_near_the_line(self):
        times, per = self.frames(static=(self.FIT.x(20.0) + 0.2, 20.0))
        path = L.chain(times, per, self.FIT)
        ys = [y for y, _x in path]
        assert len(path) > 40 and ys == sorted(ys, reverse=True) and ys[-1] < 5.0

    def test_it_carries_on_across_a_four_second_gap(self):
        times, per = self.frames(gap=range(20, 40))
        assert L.chain(times, per, self.FIT)[-1][0] < 5.0

    def test_a_lone_false_start_is_skipped(self):
        times, per = self.frames(stray_first=True)
        assert len(L.chain(times, per, self.FIT)) > 40

    def test_nothing_near_the_line_is_no_path(self):
        times = [0.0, 0.2]
        assert L.chain(times, [[(0.0, 2.0, 20.0, 0, 0, 0.9)], []], self.FIT) == []
```

- [ ] **Step 2: Run them to make sure they fail.**

Run: `.venv/bin/python -m pytest tests/test_linetime.py -k Chain -x`

Expected: FAIL (no `chain`).

- [ ] **Step 3: Implement it.** Add this to `linetime.py`:

```python
PATH_FPS = 5.0
PATH_SPLIT_ROW = 640            # the far part at imgsz 800, the near part at 416
PATH_CONF = 0.3
PATH_WIDTH = (0.65, 1.5)        # x 0.291 m x lateral_px_per_m(row)
PATH_GAP_S = 8.0                # the delivery team can hide the rock for 6 s
PATH_SEED_Y = 21.5


def path_points(boxes, times, view):
    """Destination-camera boxes as (t, x, y, cx, row, conf) per frame. The rock
    moves away from this camera, so its centre is behind the box bottom."""
    out = []
    for t, frame_boxes in zip(times, boxes):
        pts = []
        for cx, row, w, conf in frame_boxes:
            if not PATH_WIDTH[0] < w / (0.291 * view.lateral_px_per_m(row)) < PATH_WIDTH[1]:
                continue
            y = view.metres_at(row) - C.STONE_RADIUS_M
            pts.append((t, view.lateral_x(cx, view.row_for(y)), y, cx, row, conf))
        out.append(pts)
    return out


def _follow(times, per, is_seed, near, max_gap_s):
    path, cur, vel, last_t = [], None, -2.0, None
    for t, dets in zip(times, per):
        if cur is None:
            c = [d for d in dets if is_seed(d)]
            if c:
                cur = min(c, key=near); path.append(cur); last_t = t
            continue
        dt = t - last_t
        if dt > max_gap_s:
            break
        ypred = cur[2] + vel * dt
        c = [d for d in dets if abs(d[2] - ypred) < 0.6 + 0.5 * dt
             and abs(d[1] - cur[1]) < 0.12 + 0.12 * dt and d[2] <= cur[2] + 0.2]
        if not c:
            continue
        nxt = min(c, key=lambda d: abs(d[2] - ypred) + abs(d[1] - cur[1]))
        vel = 0.6 * vel + 0.4 * (nxt[2] - cur[2]) / dt
        cur = nxt; path.append(cur); last_t = t
    return path


def chain(times, per, fit: Fit):
    """The rock's path to rest as [(y, x)], started from the detection nearest
    the fitted line -- first where the rock enters this camera's view, then
    anywhere -- and moved on to the next candidate when a start leads nowhere
    (a sweeper's broom, a resting stone near the line)."""
    near = lambda d: abs(d[1] - fit.x(d[2]))
    rules = (lambda d: abs(d[2] - PATH_SEED_Y) < 2.5 and near(d) < 0.35,
             lambda d: 3.0 < d[2] < 24.0 and near(d) < 0.45)
    best = []
    for rule in rules:
        for i, dets in enumerate(per):
            if not any(rule(d) for d in dets):
                continue
            got = _follow(times[i:], per[i:], rule, near, PATH_GAP_S)
            if len(got) > len(best):
                best = got
            if len(got) >= 5:
                return [(d[2], d[1]) for d in got]
    return [(d[2], d[1]) for d in best] if len(best) >= 5 else []


def find_path(model, video, view, color, t_hog, t_rest, fit, *, decode, detect):
    """Where the rock went, seen from behind the thrower."""
    if view is None or not view.has_lateral or t_hog is None:
        return []
    t1 = (t_rest if t_rest is not None else t_hog + 24.0) + 1.0
    frames, times = decode(video, view.rect, t_hog + 1.0, t1, PATH_FPS)
    if not len(frames):
        return []
    far = detect(model, frames, times, int(view.tee_row) - 80, PATH_SPLIT_ROW + 20, color,
                 imgsz=800, conf=PATH_CONF)
    near_boxes = detect(model, frames, times, PATH_SPLIT_ROW - 20, view.rect[3], color,
                        imgsz=416, conf=PATH_CONF)
    boxes = [a + b for a, b in zip(far, near_boxes)]
    return chain(times, path_points(boxes, times, view), fit)
```

- [ ] **Step 4: Run the tests to make sure they pass.**

Run: `.venv/bin/python -m pytest tests/test_linetime.py -x`

Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add src/curling_score/game/linetime.py tests/test_linetime.py
git commit -m "linetime: the rock's path to rest, from the camera behind the thrower

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: The line pass, wired into `analyze`

**Files:**
- Modify: `src/curling_score/game/linetime.py` (`time_lines`)
- Modify: `src/curling_score/game/shots.py` (the `line` field)
- Modify: `src/curling_score/analyze.py`, `src/curling_score/cli.py`
- Test: `tests/test_linetime.py`

**Interfaces:**
- Consumes: Tasks 3–7.
- Produces:
  - `time_lines(shots, video, hog_view, dest_view, *, model=None, decode=None, detect=None) -> None`,
    which sets `shot.line`;
  - `Shot.line: object = None`;
  - `analyze(..., skip_line=False)`;
  - the CLI flag `--no-line`;
  - `EXTEND_WINDOW_S = (6.5, 9.0)` and `EXTEND_IF_SHORT_OF_M = 10.0`.

- [ ] **Step 1: Write the failing tests.** Append this to `tests/test_linetime.py`:

```python
from types import SimpleNamespace

from curling_score.game.broomtime import TargetBroom

DEST_VIEW = SideView(rect=(1110, 0, 810, 1080), tee_row=465.0, hog_row=547.0,
                     centre_col=411.0, lat_px_per_m_at_tee=140.0, centre_line=(411.0, 0.0))


class TestTimeLines:
    def shot(self, crossing, **kw):
        base = dict(missing=False, color="red", release=SimpleNamespace(t=100.0),
                    hog_crossing=crossing, t_hog_s=103.8, t_rest_s=120.0,
                    target_broom=TargetBroom(1.0, 0.0, 1.0, 0.9), stones=[],
                    delivered_stone_index=None, line=None)
        base.update(kw)
        return SimpleNamespace(**base)

    def stone_to(self, hi_yp):
        n = int((hi_yp - 3.0) / 0.12)
        return [sample_at(HOG_VIEW, 0.0 + 0.026 * (3.0 + 0.12 * i), 3.0 + 0.12 * i, 100.0 + i / 30)
                for i in range(n)]

    def run(self, shots, extra=()):
        decoded = []

        def decode(video, rect, t0, t1, fps):
            decoded.append((round(t0 - 100.0, 2), round(t1 - 100.0, 2), fps))
            return [object()] * 3, [t0, t0 + 0.1, t0 + 0.2]

        def detect(model, frames, times, lo, hi, color, imgsz=800, conf=0.35):
            return [list(extra) if i == 0 else [] for i in range(len(frames))]

        L.time_lines(shots, "v.mp4", HOG_VIEW, DEST_VIEW, model=object(),
                     decode=decode, detect=detect)
        return decoded

    def test_a_shot_with_a_crossing_and_a_broom_gets_a_line(self):
        s = self.shot(crossing_for(self.stone_to(11.5)))
        self.run([s])
        assert s.line is not None and s.line.fit_n >= 15

    def test_the_window_is_extended_only_for_a_track_that_stops_short(self):
        long_, short = self.shot(crossing_for(self.stone_to(11.5))), self.shot(crossing_for(self.stone_to(8.7)))
        assert (6.5, 9.0, 30.0) not in self.run([long_])
        assert (6.5, 9.0, 30.0) in self.run([short])

    def test_a_refused_crossing_with_samples_is_still_measured(self):
        c = crossing_for(self.stone_to(11.5))
        refused = longview.Crossing(None, "speed 3.44 m/s is not a delivery", longview.KEY_BAD_SPEED,
                                    track_key=c.track_key, samples=c.samples)
        s = self.shot(refused)
        self.run([s])
        assert s.line is not None

    def test_no_broom_no_release_missing_or_no_model_means_no_line(self):
        c = crossing_for(self.stone_to(11.5))
        shots = [self.shot(c, target_broom=None), self.shot(c, release=None), self.shot(c, missing=True)]
        self.run(shots)
        assert all(s.line is None for s in shots)
        s = self.shot(c)
        L.time_lines([s], "v.mp4", HOG_VIEW, DEST_VIEW, model=None)
        assert s.line is None


class TestAnalyzeCallsIt:
    def test_it_runs_after_the_broom_with_both_cameras(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "src/curling_score/analyze.py").read_text()
        assert src.index("linetime.time_lines(") > src.index("broomtime.time_target_brooms(")
        call = src[src.index("linetime.time_lines("):][:260]
        assert "sideviews[hogtime.CAMERA_FOR[OTHER_HOUSE[end.house]]]" in call
        assert "sideviews[hogtime.CAMERA_FOR[end.house]]" in call

    def test_a_shot_starts_with_no_line(self):
        from curling_score.game.shots import Shot
        assert Shot(number=1, color="red", stones=[], t_rest_s=0.0).line is None
```

- [ ] **Step 2: Run them to make sure they fail.**

Run: `.venv/bin/python -m pytest tests/test_linetime.py -k "TimeLines or AnalyzeCallsIt" -x`

Expected: FAIL (no `time_lines`).

- [ ] **Step 3: Implement it.** Add this to `linetime.py`:

```python
EXTEND_WINDOW_S = (6.5, 9.0)        # after the release, past hogtime's window
EXTEND_IF_SHORT_OF_M = 10.0         # ...for a track that stops short of here


def _rest(shot):
    i = getattr(shot, "delivered_stone_index", None)
    stones = getattr(shot, "stones", None) or []
    if i is None or not 0 <= i < len(stones):
        return None
    s = stones[i]
    return float(s.x_m), float(s.y_m)


def time_lines(shots, video, hog_view, dest_view, *, model=None, decode=None, detect=None) -> None:
    """Give each shot its ``line``, in place. A no-op without a model or a
    laterally calibrated hog-camera view; a shot it cannot measure keeps None."""
    if model is None or hog_view is None or not hog_view.has_lateral:
        return
    if decode is None:
        from curling_score.detect import longview
        decode = longview.decode
    if detect is None:
        from curling_score.detect import sidemodel
        detect = sidemodel.detect_band
    for shot in shots:
        broom = getattr(shot, "target_broom", None)
        rel = getattr(shot, "release", None)
        crossing = getattr(shot, "hog_crossing", None)
        if getattr(shot, "missing", False) or broom is None or rel is None or crossing is None:
            continue
        track = hog_track(crossing, hog_view)
        if track and max(p[3] for p in track) < EXTEND_IF_SHORT_OF_M:
            extra = _extend_for(shot, hog_view, video, rel.t, model, decode, detect)
            track = hog_track(crossing, hog_view, extra)
        fit = fit_line(track)
        if fit is None:
            continue
        start = find_start(model, video, hog_view, shot.color, rel.t, decode=decode, detect=detect)
        path = find_path(model, video, dest_view, shot.color, getattr(shot, "t_hog_s", None),
                         getattr(shot, "t_rest_s", None), fit, decode=decode, detect=detect)
        shot.line = measure(fit, track, start, (broom.x_m, broom.y_m), rest=_rest(shot), path=path)
```

Add the helper that decodes past hogtime's window. It takes the rock's colour
from the shot, since a `Crossing` doesn't carry one:

```python
def _extend_for(shot, view, video, t_release, model, decode, detect):
    """Detections in the seconds past hogtime's window, as proposer samples."""
    from curling_score.detect import longview, sidemodel
    frames, times = decode(video, view.rect, t_release + EXTEND_WINDOW_S[0],
                           t_release + EXTEND_WINDOW_S[1], 30.0)
    if not len(frames):
        return []
    lo, hi = sidepool.band_crop(view)
    out = []
    for t, boxes in zip(times, detect(model, frames, times, lo, hi, shot.color)):
        for cx, row, w, _c in boxes:
            expect = view.stone_width_at(row, longview.STONE_WIDTH_AT_HOG_PX)
            if expect > 1 and sidemodel.WIDTH_TOL[0] <= w / expect <= sidemodel.WIDTH_TOL[1]:
                out.append((t, cx, row, w))
    return out
```

In `shots.py`, after `target_broom`, add:

```python
    # Where this rock's thrown line passed the skip's broom, where it sat
    # before the push and where it went -- a `linetime.Line` -- or None when
    # it could not be measured.
    line: object = None
```

In `analyze.py`:
- Import `linetime` beside `broomtime`, and `sidemodel` from `curling_score.detect`.
- Add the `skip_line: bool = False` keyword to `analyze()` and document it in
  the docstring, beside `skip_longview`.
- After the `broom_model = ...` block, add:

```python
    # The rock's thrown line against the broom: the side model again, which
    # hogtime has already loaded (`sidemodel._load` is cached).
    line_model = None if (skip_longview or skip_line) else sidemodel.default_model()
```

Inside `if sideviews is not None:`, right after the `broomtime.time_target_brooms(...)`
call, add:

```python
                # Where the rock's thrown line passed the skip's broom -- the
                # hog-crossing camera for the line, the destination camera for
                # where it went. Needs hogtime's crossing and broomtime's broom.
                linetime.time_lines(
                    shots, path, sideviews[hogtime.CAMERA_FOR[OTHER_HOUSE[end.house]]],
                    sideviews[hogtime.CAMERA_FOR[end.house]], model=line_model)
```

In `cli.py`:
- Add
  `p.add_argument("--no-line", action="store_true", help="skip measuring where each rock's line passed the broom")`
  after `--no-longview`.
- Pass `skip_line=args.no_line` in `_analyze`.

- [ ] **Step 4: Run the tests to make sure they pass.**

Run: `.venv/bin/python -m pytest tests/test_linetime.py tests/test_broomtime.py tests/test_hogtime.py -x`

Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add src/curling_score/game/linetime.py src/curling_score/game/shots.py src/curling_score/analyze.py src/curling_score/cli.py tests/test_linetime.py
git commit -m "analyze: measure each rock's line against the broom after the broom is found

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Publish `line`: schema 6

**Files:**
- Modify: `src/curling_score/timeline.py`, `src/curling_score/version.py`, `src/curling_score/analyze.py:496`
- Test: `tests/test_timeline.py`, `tests/test_version.py`

**Interfaces:**
- Consumes: `linetime.Line` (Task 4).
- Produces:
  - the timeline shot key `"line"`, with the shape in the spec's section 4;
  - `SCHEMA_VERSION = 6`;
  - `processing_version(..., line=False)`.

- [ ] **Step 1: Write the failing tests.** Append this to `tests/test_timeline.py`:

```python
class TestLine:
    def _end(self, line):
        s = S.Shot(number=1, color="red", stones=[], t_rest_s=10.0)
        s.line = line
        return timeline.build_end(number=1, house="top", start_s=0.0, end_s=900.0, shots=[s])

    def test_a_measured_line_is_published_in_house_metres(self):
        from curling_score.game.linetime import Line
        line = Line(start=(-0.23151, 38.0712), at_hog_x=-0.75712, at_hog_offset=-0.16333,
                    at_broom_x=-2.35912, miss=-0.71234, curl="right", side="wide",
                    confirmed=True, hog_path=((28.3461, -0.75712),), path=((20.0124, -1.18049),),
                    fit_n=53, fit_rms=0.00412)
        got = self._end(line)["shots"][0]["line"]
        assert got == {"start": {"x": -0.2315, "y": 38.071},
                       "at_hog": {"x": -0.7571, "offset_m": -0.1633},
                       "at_broom": {"x": -2.3591, "miss_m": -0.7123},
                       "side": "wide", "curl": "right", "confirmed": True,
                       "hog_path": [[28.35, -0.757]], "path": [[20.01, -1.18]],
                       "fit": {"n": 53, "rms_m": 0.0041}}

    def test_no_line_is_null_not_absent(self):
        shot = self._end(None)["shots"][0]
        assert "line" in shot and shot["line"] is None
```

In `test_the_schema_version_says_the_shape_changed`, change the assertion to
`assert timeline.SCHEMA_VERSION == 6`, and add this to its docstring: "6 adds
`line` to every shot: where the rock's thrown line passed the broom, or null.
A 5 has no such key, which readers must treat as not measured."

Append this to `tests/test_version.py`:

```python
def test_the_line_pass_is_named_when_it_ran(tmp_path):
    from curling_score import version
    w, side, broom = (tmp_path / n for n in ("w.pt", "side.pt", "broom.pt"))
    for i, f in enumerate((w, side, broom)):
        f.write_bytes(bytes([i]))           # model_id hashes the file
    assert version.processing_version(w, side, broom, line=True).endswith("+line")
    assert not version.processing_version(w, side, broom).endswith("+line")
    assert not version.processing_version(w, None, None, line=True).endswith("+line")
```

- [ ] **Step 2: Run them to make sure they fail.**

Run: `.venv/bin/python -m pytest tests/test_timeline.py tests/test_version.py -k "Line or schema_version or line_pass" -x`

Expected: FAIL. There is no `"line"` key, the schema is 5, and there is no
`line` keyword.

- [ ] **Step 3: Implement it.**

In `timeline.py`:
- set `SCHEMA_VERSION = 6`;
- add this after `_broom`:

```python
def _line(l):
    """A `linetime.Line` in house metres, or None."""
    if l is None:
        return None
    r = lambda v, n=4: None if v is None else round(float(v), n)
    return {"start": None if l.start is None else {"x": r(l.start[0]), "y": r(l.start[1], 3)},
            "at_hog": {"x": r(l.at_hog_x), "offset_m": r(l.at_hog_offset)},
            "at_broom": {"x": r(l.at_broom_x), "miss_m": r(l.miss)},
            "side": l.side, "curl": l.curl, "confirmed": l.confirmed,
            "hog_path": [[r(y, 2), r(x, 3)] for y, x in l.hog_path],
            "path": [[r(y, 2), r(x, 3)] for y, x in l.path],
            "fit": {"n": int(l.fit_n), "rms_m": r(l.fit_rms)}}
```

- in `build_end`, after `"target_broom": ...`, add:

```python
                # Where the rock's thrown line passed the broom, where it sat
                # before the push and where it went; null when not measured.
                "line": _line(getattr(s, "line", None)),
```

In `version.py`:
- set `PIPELINE_VERSION = "2026.09.24"`, with a dated comment in the file's
  style: "2026.09.24: every shot carries `line`, the thrown line against the
  broom (schema 6); the side views measure across the sheet from the painted
  centre line, which moves `target_broom.x` by up to 3 cm; a stone split
  across a column key at the hog row is timed, not lost."
- replace the signature and the last line of `processing_version`:

```python
def processing_version(weights, side_weights=None, broom_weights=None, line=False) -> str:
    ...
    out = out if broom_weights is None else f"{out}+broom-{model_id(broom_weights)}"
    return f"{out}+line" if line and side_weights is not None else out
```

Adjust the local variable names to fit the existing body, and add one line to
its docstring about `line`.

In `analyze.py:496`, pass `line=line_model is not None`.

- [ ] **Step 4: Run the tests to make sure they pass.**

Run: `.venv/bin/python -m pytest tests/test_timeline.py tests/test_version.py tests/test_linetime.py -x`

Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add src/curling_score/timeline.py src/curling_score/version.py src/curling_score/analyze.py tests/test_timeline.py tests/test_version.py
git commit -m "timeline: every shot carries its line against the broom, schema 6

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Replay the local games and check against the spike

**Files:**
- Create: `scripts/line/validate_lines.py`

**Interfaces:**
- Consumes: schema-6 timelines from `curling-score analyze`.

- [ ] **Step 1: Write the validation script.** Create
`scripts/line/validate_lines.py`:

```python
#!/usr/bin/env python
"""How much of a timeline got a `line`, how wide the throws read, and -- against
a baseline made with --no-line -- that nothing else moved.

    python scripts/line/validate_lines.py out/line-vxu9/timeline.json \
        --baseline out/line-base-vxu9/timeline.json
"""
import argparse
import json
import statistics

IGNORED_TOP = {"processing_version", "analysed_at"}
LINE_KEYS = {"line"}


def shots(doc):
    for g in doc["games"]:
        for e in g["ends"]:
            for s in e["shots"]:
                yield g["index"], e["number"], s


def summary(doc):
    broomed = [s for _g, _e, s in shots(doc) if s.get("target_broom") and not s.get("missing")]
    lined = [s for s in broomed if s.get("line")]
    out = [s["line"]["at_broom"]["miss_m"] * (1 if s["target_broom"]["x"] >= 0 else -1)
           for s in lined if abs(s["target_broom"]["x"]) > 0.3]
    conf = [s["line"]["confirmed"] for s in lined]
    print(f"shots with a broom {len(broomed)}, with a line {len(lined)} "
          f"({len(lined) / max(1, len(broomed)):.0%})")
    if out:
        print(f"  line at the broom, + outside: median {100 * statistics.median(out):+.0f} cm "
              f"over {len(out)} shots with |broom x| > 0.3")
    print(f"  confirmed {conf.count(True)}, disagrees {conf.count(False)}, unseen {conf.count(None)}")


def diff(doc, base):
    bad = 0
    for k in set(doc) | set(base):
        if k not in IGNORED_TOP and k not in ("games", "calibration", "schema_version") and doc.get(k) != base.get(k):
            print(f"  top-level {k} differs"); bad += 1
    for (g, e, s), (_g, _e, b) in zip(shots(doc), shots(base)):
        for k in set(s) | set(b):
            if k in LINE_KEYS:
                continue
            if s.get(k) != b.get(k):
                print(f"  game {g} end {e} shot {s['number']}: {k} differs"); bad += 1
    print(f"  {bad} differences outside `line`")
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("timeline")
    ap.add_argument("--baseline")
    a = ap.parse_args()
    doc = json.load(open(a.timeline))
    summary(doc)
    if a.baseline:
        raise SystemExit(1 if diff(doc, json.load(open(a.baseline))) else 0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Replay VXU9's first 3000 s with and without the line pass.**
Run each replay alone, never concurrently, because of the OOM note.

```bash
.venv/bin/curling-score analyze https://www.youtube.com/watch?v=VXU9xwmugRg --end 3000 --no-scoreboard --no-line --out out/line-base-vxu9
.venv/bin/curling-score analyze https://www.youtube.com/watch?v=VXU9xwmugRg --end 3000 --no-scoreboard --out out/line-vxu9
.venv/bin/python scripts/line/validate_lines.py out/line-vxu9/timeline.json --baseline out/line-base-vxu9/timeline.json
```

Expected:
- coverage of at least 90% of broomed shots;
- a median of about +16 cm outside (the spike measured +16 over 45 shots);
- 0 differences outside `line`, with exit code 0.

- [ ] **Step 3: Check against the timeline from before this work.**

```bash
.venv/bin/python - <<'EOF'
import json
old = json.load(open("/home/tcuser/curling-work/broom/verify/broom1/timeline.json"))
new = json.load(open("out/line-vxu9/timeline.json"))
def sh(d): return [s for g in d["games"] for e in g["ends"] for s in e["shots"]]
dx = [abs(a["target_broom"]["x"] - b["target_broom"]["x"]) for a, b in zip(sh(old), sh(new))
      if a.get("target_broom") and b.get("target_broom")]
print("broom x moved: max", round(max(dx), 3), "m")
changed = [(a["number"], a["long_split_s"], b["long_split_s"]) for a, b in zip(sh(old), sh(new))
           if a["long_split_s"] != b["long_split_s"]]
print("splits that changed:", changed)
EOF
```

If the two shot lists differ in length, match shots by `t_enter_s` instead of
by position. The memory note on the ds15 retrain says renumbering shifts every
later shot.

Expected:
- the broom x moves by at most about 0.04 m, the painted-centre correction;
- every changed split goes from `None` to a value (one the bin join
  recovered), never from one value to another.

- [ ] **Step 4: Replay hOKZ (and AEqL's second game, windowed).** Use the same
pair of commands as Step 2:
- hOKZ: `https://www.youtube.com/watch?v=hOKZoeJNTpM`, full, into
  `out/line-hokz` and `out/line-base-hokz`.
- AEqL: `https://www.youtube.com/watch?v=AEqLTgM25Tc --start 7200 --end 10300 --cache-root ~/.cache/curling_replay`,
  into `out/line-aeql2` and `out/line-base-aeql2`.

Run `validate_lines.py` on each.

Expected:
- hOKZ: a median of about +33 cm outside, coverage of at least 90%;
- AEqL end 3, by time: about +39 cm outside;
- zero differences outside `line`.

If a median differs from the spike by more than 10 cm, stop and compare
per-shot against `~/curling-work/line-spike/score_*.json` before going on.

- [ ] **Step 5: Commit the script, and report the numbers to the user.**

```bash
git add scripts/line/validate_lines.py
git commit -m "line: validate a timeline's lines against the spike and a --no-line baseline

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Deployment (building the worker image, the API, and requeueing the hosted
videos) is **not** part of this plan. It waits for the viewer plan and for an
explicit go from the user. It follows the deploying-curling-chart and
concurrent-session-commits notes.
