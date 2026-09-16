# Shot-Driven Side-View Frame Extraction — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Extract side-view frames from windows where the shot list says a delivery is in flight, clustered near the hog crossing, so a person can label them and a trained detector can replace `detect/longview.py`.

**Architecture:** Drive extraction from the pipeline's own shot list instead of scanning blind. `scripts/split_coverage.py` already walks a cached video into per-end shot lists; this lifts that walk into a reusable module, turns each shot into a side-view window, and samples frames by distance from the hog line.

**Tech Stack:** Python 3.11, numpy, PyAV/ffmpeg, OpenCV, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-15-side-view-frame-extraction-design.md`

## Global Constraints

- **A TASK IS NOT DONE UNTIL SOMEONE HAS LOOKED AT REAL OUTPUT.** The previous
  attempt passed five rounds of review and produced 2,864 unusable frames,
  because every test ran against synthetic fixtures and nothing rendered a real
  frame until a person did. Every task below that touches pixels must render
  real frames from the cache and report what is in them — not just that a
  function returned the expected shape.
- **Every candidate frame comes from a known delivery window.** No frame enters
  the pool because a blob happened to be somewhere. If the shot list does not
  say a delivery was in flight, the frame is not a candidate.
- **Proposals come only from the ice.** The stone racks beside the sheet are in
  every frame and are red; they must not be able to propose a box.
- **A frame where the detector found nothing is still kept**, carrying no box,
  when the shot list says a delivery was there. Those are the frames the
  replacement most needs.
- **Do not touch `detect/rocks.py`, `detect/yolo.py` or `geometry/calibrate.py`** — they form the detection cache key (`detect/cache.py:33-37`); changing any forces a full GPU re-detect of every video.
- **Do not change any gate in `detect/longview.py` or `game/split.py`.**
- `harvest/sideviews.py` and `harvest/sideframes.py` are consumed unchanged.
- Nothing durable in `/tmp`.
- Two SLOW tests fail by design (`tests/test_longview.py::TestAgainstHandMarkedCrossings`). Not yours.
- Every commit runs `pytest -m "not slow"` green. **Never add `-q`** — it is in `addopts` and `-qq` hides the summary line.
- Commit messages: lower-case prefix, a why-sentence, `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.

---

### Task 1: Turn a cached video into delivery windows

**Files:**
- Create: `src/curling_score/harvest/sideshots.py`
- Test: `tests/test_sideshots.py`

**Interfaces:**
- Produces: `Window` (frozen: `video_id`, `camera`, `color`, `t0`, `t1`, `end_number`, `shot_number`, `t_release`, `t_rest`); `windows_for_end(shots, house) -> list[Window]`; `windows_for_video(video_path, vid, root, views) -> Iterator[Window]`.
- Consumed by Task 3.

`scripts/split_coverage.py` already does this walk — read it end to end before
writing anything, and lift rather than reinvent. The parts that matter are its
setup helpers, its per-end loop (`run_up_from`, shot detection, `fit_end`), and
how it picks the camera: `hogtime.CAMERA_FOR[analyze.OTHER_HOUSE[end.house]]`.

The window itself must match what `hogtime` uses in production, so the frames
come from the same span the pipeline would search. Read `game/hogtime.py` for
`WINDOW_S` and `ARRIVAL_LOOKBACK_S` and use them; do not invent a window.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sideshots.py
from curling_score.harvest import sideshots


class FakeShot:
    def __init__(self, n, color, t_rel=None, t_rest=None):
        self.number, self.color = n, color
        self.release = type("R", (), {"t_s": t_rel})() if t_rel else None
        self.t_rest_s = t_rest


class TestWindows:
    def test_the_camera_is_the_one_that_watches_the_throwing_end(self):
        # A top-house end is thrown from the bottom, and the camera that can
        # see the bottom hog line is the one at the top -- CAMERA_FOR's job.
        (w,) = sideshots.windows_for_end([FakeShot(1, "red", t_rel=100.0)], "top")
        assert w.camera == "left"

    def test_the_other_house_gets_the_other_camera(self):
        (w,) = sideshots.windows_for_end([FakeShot(1, "red", t_rel=100.0)], "bottom")
        assert w.camera == "right"

    def test_a_window_spans_the_flight_from_the_release(self):
        (w,) = sideshots.windows_for_end([FakeShot(1, "red", t_rel=100.0)], "top")
        assert (w.t0, w.t1) == (100.0 + 2.0, 100.0 + 6.5)   # longview.WINDOW_S

    def test_a_shot_with_no_release_falls_back_to_the_arrival(self):
        (w,) = sideshots.windows_for_end([FakeShot(1, "red", t_rest=200.0)], "top")
        assert w.t0 < 200.0 and w.t1 < 200.0

    def test_a_shot_with_neither_gets_no_window(self):
        assert sideshots.windows_for_end([FakeShot(1, "red")], "top") == []

    def test_the_colour_travels_with_the_window(self):
        (w,) = sideshots.windows_for_end([FakeShot(3, "yellow", t_rel=50.0)], "top")
        assert w.color == "yellow" and w.shot_number == 3
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/test_sideshots.py`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the module**, lifting the walk from `scripts/split_coverage.py`. Keep `windows_for_end` pure so it is testable without media; `windows_for_video` does the cache walk.

- [ ] **Step 4: Run the tests**

- [ ] **Step 5: LOOK AT REAL OUTPUT.** Run `windows_for_video` over one cached
video on the worker and report: how many ends, how many shots, how many
produced a window, the distribution of window lengths, and the split between
`left` and `right` cameras. A video that yields no windows, or windows all from
one camera, is a defect — say so rather than reporting that the tests pass.

- [ ] **Step 6: Commit**

---

### Task 2: Sample frames near the line, and only from the ice

**Files:**
- Modify: `src/curling_score/harvest/sidepool.py`
- Test: `tests/test_sidepool.py`

**Interfaces:**
- Produces: `ice_bounds(view) -> tuple[int, int]`; `frames_for_window(video, view, window, *, fps, n_near, n_far) -> list[SideCandidate]`.

Two changes, both from what the first attempt got wrong.

**Confine proposals to the ice.** The racks sit outside the sheet's lateral
bounds. Derive those bounds from the calibration rather than hard-coding a
fraction of the frame: `SideView.row_for` and the sheet's known width give the
sheet's edges at a given row. Read `geometry/constants.py` for the width.

**Cluster near the line.** Where a track exists, choose frames by
`|edge_row - hog_row|` — `n_near` from closest, `n_far` from the approach.
Where no track exists at all, still take `n_near` frames from the window's
centre, with `labels=()`. The shot list says a stone was there.

- [ ] **Step 1: Write the failing tests** — including one that a rack-like red
blob outside the ice bounds proposes nothing, and one that a window with no
detectable stone still yields frames with no boxes.

- [ ] **Step 2-4: Red, green, tests pass.**

- [ ] **Step 5: LOOK AT REAL OUTPUT.** Render 30 frames from real windows on
the worker with their boxes drawn, pull them back, and **describe what is in
them**: is the box on a stone, is the stone near the line, is anything on a
rack. Report the count of frames whose box is not on a stone. This is the step
that would have caught the last failure.

- [ ] **Step 6: Commit**

---

### Task 3: Drive the propose stage from shots

**Files:**
- Modify: `src/curling_score/harvest/sidestages.py`, `src/curling_score/cli.py`
- Test: `tests/test_sidestages.py`

`stage_propose` stops walking clips and walks cached videos instead, via
`sideshots.windows_for_video` and `sidepool.frames_for_window`. `--root` now
means the pipeline cache root, not a clip directory; say so in the help.

`stage_views` keeps working on clips OR on cached videos — the nine VODs need
calibrating too. Reuse `sideviews.derive` with frames sampled from the video.

- [ ] **Step 1: Write the failing tests** — `stage_propose` over a tiny fake
window source produces a pool whose every row names a real window.

- [ ] **Step 2-4: Red, green, tests pass.**

- [ ] **Step 5: LOOK AT REAL OUTPUT.** Run the stage over one cached video end
to end and report the pool's composition: frames per colour, per camera, per
position bin, and how many carry a box. **A red:yellow ratio far from 1:1 is a
defect** — the colours alternate.

- [ ] **Step 6: Commit**

---

## After the plan

Calibrate the nine VODs, run propose over all of them, select, build, and put
~100 real frames in front of a person **before** committing to the full set.
