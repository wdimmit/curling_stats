# ds13 Side-View Frame Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract 600 candidate frames from the composite's two side views across ds11's 120-video clip archive, so a person can label them and a trained detector can replace `detect/longview.py`.

**Architecture:** Four resumable stages mirroring `harvest/stages.py` — `views` calibrates each video's two side views from a clean plate, `propose` scans the clips with the classical detector recording per-moment geometry and per-window refusal reasons, `select` picks 600 in two halves (scene-stratified and refusal-stratified) under per-video and per-clip caps, and `build` writes the YOLO tree. Images stay on the worker; the manifest and the calibration report come back into git.

**Tech Stack:** Python 3.11, numpy, PyAV (decode), OpenCV (JPEG write), pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-15-side-view-frame-extraction-design.md`

## Global Constraints

- **The classical detector proposes; it never selects.** Half the set is chosen by what the scene contains and half by the detector's *refusals*. A selection path that draws only from frames it succeeded on is the ds11 circularity defect and must be rejected. (`datasets/ds11/README.md`)
- **A video that will not calibrate is reported, never skipped quietly.** `sideviews.json` names every failure and why.
- **A bin that cannot be filled is reported, never padded.** `shortfall` travels in the manifest beside the quota, as in `harvest/manifest.py`.
- **The train/val split holds out whole videos**, inherited verbatim from `datasets/ds11/videos.json`. No frame-level split anywhere.
- **Crops are the whole side-view rect**, exactly the band `longview.decode` uses, so training geometry equals inference geometry.
- **Do not touch `detect/rocks.py`, `detect/yolo.py` or `geometry/calibrate.py`.** They form the detection cache key (`detect/cache.py:33-37`); changing any forces a full GPU re-detect of every video.
- **`detect/longview.py`'s measured behaviour must not change.** It finds 20 of the 27 hand-marked crossings today. Task 2 adds fields; the hand-mark test results must be identical before and after.
- **No labelling tool.** This plan produces frames and a manifest. Drawing boxes from scratch is separate work.
- **Nothing durable in `/tmp`.** Manifests and reports go in the repo; images go under `/data/wdd/curling/ds13` on the worker. (`datasets/ds11/README.md`, and ds8 which cannot be rebuilt.)
- **Derive every synthetic stone's rows from a stated speed and check them against `longview.SPEED_BOUNDS_M_S` (1.2-3.2 m/s), and keep its handle clear of the annulus at rows 409-453.** This plan shipped two fixtures that violated those gates, and the sibling plan shipped one before it: round numbers that look like a stone in flight are routinely 3.3 or 5.5 m/s, which the detector refuses, so the test fails against a correct implementation.
- Every commit runs `pytest -m "not slow"` green.
- Commit messages: lower-case component prefix, a sentence that says *why*, and the `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>` trailer.

---

## File structure

| file | responsibility |
|---|---|
| `src/curling_score/harvest/sideviews.py` (new) | per-video side-view calibration and its JSON, mirroring `harvest/setups.py` |
| `src/curling_score/harvest/sideframes.py` (new) | the candidate record, the strata, the quotas and the selection, mirroring `harvest/candidates.py` |
| `src/curling_score/harvest/sidepool.py` (new) | the decode-and-propose pass over clips, mirroring `harvest/pool.py` |
| `src/curling_score/harvest/sidestages.py` (new) | the four stages, mirroring `harvest/stages.py` |
| `src/curling_score/detect/longview.py` (modify) | expose the handle's top row and a stable refusal key |
| `src/curling_score/harvest/build.py` (modify) | one keyword so the YOLO writer can walk a ds13 manifest |
| `src/curling_score/cli.py` (modify) | `curling-score sideframes {views,propose,select,build}` |

---

### Task 1: Calibrate both side views of a video, from its own clips

**Files:**
- Create: `src/curling_score/harvest/sideviews.py`
- Test: `tests/test_sideviews.py`

**Interfaces:**
- Consumes: `geometry.sideview.locate`, `geometry.sideview.solve`, `geometry.sideview.SideView`, `geometry.sideview.SideViewError`, `geometry.layout.detect_panels`, `geometry.layout.LayoutError`.
- Produces: `derive(video_id, frames) -> VideoViews`; `to_json(v) -> dict`; `from_json(d) -> VideoViews`; `is_usable(v) -> bool`; `usable_views(v) -> list[tuple[str, SideView]]`; `plate(frames) -> np.ndarray`.

`harvest/setups.py` is the model to follow exactly: a frozen dataclass per unit, an `error` string rather than an exception when a video is simply odd, and JSON in and out so nothing important lives in a pickle.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_sideviews.py
import numpy as np
import pytest

from curling_score.harvest import sideviews
from tests import synth


def _composite(tee_row=430.0, hog_row=520.0, width=1920, height=1080):
    """A whole 1920x1080 composite: two side views round an overhead strip."""
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    left = synth.side_view(tee_row=tee_row, hog_row=hog_row)
    right = synth.side_view(tee_row=tee_row, hog_row=hog_row)
    frame[:, 0:810] = left[:, 0:810]
    frame[:, 1108:1920] = right[:, 0:812]
    frame[:, 810:1108] = synth.composite_strip(298, height)
    return frame


class TestDerive:
    def test_both_views_solve(self):
        got = sideviews.derive("vid", [_composite() for _ in range(4)])
        assert got.error is None
        assert sorted(got.views) == ["left", "right"]
        for name in ("left", "right"):
            assert got.views[name].view is not None
            assert got.views[name].view.tee_row == pytest.approx(430.0, abs=2.0)

    def test_a_video_with_no_overhead_strip_is_recorded_not_raised(self):
        blank = [np.full((1080, 1920, 3), 120, np.uint8) for _ in range(4)]
        got = sideviews.derive("vid", blank)
        assert got.error is not None and "layout" in got.error
        assert not sideviews.is_usable(got)

    def test_a_view_that_will_not_solve_keeps_the_other(self):
        frames = [_composite() for _ in range(4)]
        for f in frames:                      # flatten the right view's paint
            f[:, 1108:1920] = 150
        got = sideviews.derive("vid", frames)
        assert got.views["right"].view is None
        assert got.views["right"].error
        assert got.views["left"].view is not None
        assert sideviews.is_usable(got)

    def test_json_round_trips(self):
        got = sideviews.derive("vid", [_composite() for _ in range(4)])
        back = sideviews.from_json(sideviews.to_json(got))
        assert back == got


class TestPlate:
    def test_the_median_removes_a_transient(self):
        frames = [_composite() for _ in range(5)]
        frames[2][500:540, 100:200] = 0        # one frame has a person in it
        p = sideviews.plate(frames)
        assert p[500:540, 100:200].mean() > 100
```

`tests/synth.py` gains `composite_strip(width, height)` — a grey strip with the
three horizontal separator bars `layout.detect_panels` insists on, so a whole
composite can be built without a video. Read `geometry/layout.py` for what the
bars must look like and build the smallest thing that satisfies it.

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/test_sideviews.py -v`
Expected: FAIL — `ModuleNotFoundError: curling_score.harvest.sideviews`

- [ ] **Step 3: Write the module**

```python
"""Where each video's two side views are, and where the far end's paint sits.

``harvest/setups.py`` does this for the overhead panels; this is the same job
for the wide cameras at the ends of the sheet, and it makes the same promise:
120 unseen VODs will not all work, so a video that cannot be read is recorded
with the reason rather than raising and stopping the harvest.

The reason to record rather than drop is that this is the first real test of
constants measured on ten views from five sheets -- ``PLAUSIBLE_ROWS``,
``_GREEN_THRESHOLD``, ``_HOUSE_SEARCH``, ``_HOG_SEARCH_PX`` in
``geometry/sideview.py``. How many of 240 views calibrate is the finding.
"""

from dataclasses import dataclass

import numpy as np

from curling_score.geometry import layout, sideview

MIN_PLATE_FRAMES = 2


@dataclass(frozen=True)
class ViewInfo:
    """One wide camera: where it is, where the paint is, and why not."""

    name: str
    rect: tuple
    view: sideview.SideView | None
    error: str | None = None


@dataclass(frozen=True)
class VideoViews:
    video_id: str
    frame_size: tuple
    views: dict
    error: str | None = None


def plate(frames):
    """A clean plate: the median over frames far apart in time.

    Players and stones move and the paint does not, so the median is the paint.
    Frames close together would leave a settled house in the plate and the
    house's own rings are what the tee fit looks for.
    """
    stack = np.stack([np.asarray(f, dtype=np.float32) for f in frames])
    return np.median(stack, axis=0)


def derive(video_id: str, frames) -> VideoViews:
    """Locate and solve both side views of one video."""
    frames = list(frames)
    if len(frames) < MIN_PLATE_FRAMES:
        raise ValueError(
            f"{video_id}: need at least {MIN_PLATE_FRAMES} frames spread across "
            f"the video to build a clean plate, got {len(frames)}")
    height, width = frames[0].shape[:2]
    try:
        panels = layout.detect_panels(frames)
    except layout.LayoutError as exc:
        return VideoViews(video_id, (width, height), {}, f"layout: {exc}")
    try:
        rects = sideview.locate(panels, width, height)
    except sideview.SideViewError as exc:
        return VideoViews(video_id, (width, height), {}, f"locate: {exc}")

    p = plate(frames)
    found = {}
    for name, rect in rects.items():
        try:
            v, err = sideview.solve(p, rect, name=f"{video_id}-{name}"), None
        except Exception as exc:  # noqa: BLE001 -- a bad view must not stop 119 others
            v, err = None, f"{type(exc).__name__}: {exc}"
        found[name] = ViewInfo(name=name, rect=tuple(rect), view=v, error=err)
    return VideoViews(video_id, (width, height), found, None)


def usable_views(v: VideoViews):
    return [(name, v.views[name].view) for name in ("left", "right")
            if name in v.views and v.views[name].view is not None]


def is_usable(v: VideoViews) -> bool:
    return bool(usable_views(v))
```

Add `to_json` / `from_json` in the shape `harvest/setups.py` uses: a plain dict
per view holding `name`, `rect`, `error`, and either `null` or
`{"tee_row", "hog_row", "d_m"}` for the view. Round-trip equality is what the
test checks, so `rect` must come back as a tuple and the floats unrounded.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_sideviews.py -v`
Expected: PASS

- [ ] **Step 5: Check it against the five real sheets**

Add to `tests/test_sideviews.py`, using the existing `harvested_frames` fixture
(`tests/conftest.py:45`) and `VALIDATION_VIDS`:

```python
@pytest.mark.parametrize("sheet,vid", sorted(
    __import__("tests.conftest", fromlist=["x"]).VALIDATION_VIDS.items()))
def test_real_videos_calibrate_both_views(harvested_frames, sheet, vid):
    got = sideviews.derive(vid, harvested_frames(vid))
    assert got.error is None, got.error
    assert len(sideviews.usable_views(got)) == 2, {
        n: got.views[n].error for n in got.views}
```

Import `VALIDATION_VIDS` properly (`from tests.conftest import VALIDATION_VIDS`)
rather than the `__import__` above, which is written here only to show which
symbol is meant.

Run: `.venv/bin/python -m pytest tests/test_sideviews.py -v`
Expected: PASS, or SKIP where frames are not cached.

- [ ] **Step 6: Commit**

```bash
git add src/curling_score/harvest/sideviews.py tests/test_sideviews.py tests/synth.py
git commit -m "$(cat <<'EOF'
sideviews: calibrate a video's two wide cameras, and say when they will not

Ten views calibrate today and 120 videos is 240. A video that cannot be read
is recorded with the reason rather than raising, because how many fail is the
finding this stage exists to produce.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Keep the box the scan already found

**Files:**
- Modify: `src/curling_score/detect/longview.py`
- Test: `tests/test_longview.py`

**Interfaces:**
- Produces: `candidates(win, color, expect_px=STONE_WIDTH_AT_HOG_PX) -> list[Proposal]`; `Proposal(cx, top_row, edge_row, body_px)`.
- Consumed by Task 4 (`harvest/sidepool.py`).

**This task shrank after the plan was written.** It originally also added
`Crossing.key` and `longview.KEYS`, so a dataset could stratify on which gate
refused a window without matching on prose. That landed early, in commit
`ef55616`, while fixing the split-coverage measurement — which needed the same
thing to stop its refusal histogram fragmenting into one row per distinct
speed. The keys are already there and are `ok`, `no_frames`, `no_candidate`,
`ambiguous`, `never_reached`, `unsteady`, `bad_speed`, as a `frozenset`. **Do
not re-add them.** Read them before you start; Task 3's quota is keyed on them.

What remains is the box. `candidates` (still named `_candidates`) computes the
granite body's widest dark span and its sub-pixel trailing edge, and it finds
the colour handle's extent and throws it away. A label whose bottom edge is the
ice needs the top too, and taking it from the pixels beats deriving it from an
assumed stone height.

- [ ] **Step 1: Write the failing test**

```python
class TestProposal:
    def test_a_proposal_spans_handle_to_ice(self):
        # side_view_stone takes the plate as its first argument and paints a
        # grey body above `row` with a coloured handle above that, so the
        # trailing edge is `row` and the handle's top is well above it.
        win = synth.side_view_stone(synth.side_view(), 520.0,
                                    width_px=52, color="red")
        props = longview.candidates(np.asarray(win, dtype=np.float32), "red", 52.0)
        assert len(props) == 1
        prop = props[0]
        assert prop.edge_row == pytest.approx(520.0, abs=2.0)
        assert prop.top_row < prop.edge_row - 10   # the handle is above the ice
        assert prop.body_px == pytest.approx(52.0, rel=0.2)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_longview.py -k Proposal`
Expected: FAIL — `AttributeError: module 'curling_score.detect.longview' has no attribute 'candidates'`

(Note: `pyproject.toml` already sets `addopts = "-q"`. Do **not** add another
`-q` — pytest sums them and `-qq` drops the summary line entirely, which has
already cost three sessions an hour between them. See the comment beside
`addopts`.)

- [ ] **Step 3: Make the change**

```python
@dataclass(frozen=True)
class Proposal:
    """One candidate stone, as a box a person can correct.

    Every edge is measured: ``body_px`` and ``edge_row`` from the granite's
    dark span, ``top_row`` from the colour handle's own topmost row. Nothing
    here assumes how tall a stone is.
    """

    cx: float
    top_row: float
    edge_row: float
    body_px: float
```

In `_candidates`, keep the existing computation exactly as it is and take the
handle's top beside the bottom it already has:

```python
        handle_bottom = int(np.max(np.nonzero(sub)[0]))
        handle_top = int(np.min(np.nonzero(sub)[0]))
```

Return `Proposal(float(cx), float(handle_top), _sub_row(rows, wide, lower), body)`
instead of the bare tuple. Rename `_candidates` to `candidates` — it is now part
of the module's interface — and update its one caller in `find_in_frames` to
read `p.cx`, `p.edge_row`, `p.body_px`.

- [ ] **Step 4: Run the whole longview suite, including the slow hand marks**

Run: `.venv/bin/python -m pytest tests/test_longview.py`
Expected: the new test PASSES; `TestAgainstHandMarkedCrossings` gives **exactly
the same results as before the change** — 20 of 27 found, median error 0.047 s,
worst 0.619 s, and the same two tests failing for the same reason. Record the
before and after side by side in your report. A change in those numbers means
the refactor was not behaviour-preserving and must be reverted, not explained.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/detect/longview.py tests/test_longview.py
git commit -m "$(cat <<'EOF'
longview: keep the box the scan already found

A label whose bottom edge is the ice needs the handle's top row, which the
scan computes on its way to the body and then discards.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: The candidate record, the strata and the selection

**Files:**
- Create: `src/curling_score/harvest/sideframes.py`
- Test: `tests/test_sideframes.py`

**Interfaces:**
- Consumes: `longview.KEYS`, `train.dataset.Label`.
- Produces: `SideCandidate`; `stem_for`; `position_of`; `bin_of`; `SCENE_QUOTA`; `OUTCOME_QUOTA`; `MAX_PER_VIDEO`; `MAX_PER_CLIP`; `select(pool, *, scene_quota=None, outcome_quota=None) -> (chosen, shortfall)`.
- Consumed by Tasks 4 and 5.

This is the file the spec's central claim lives in, so its comments carry the
argument: half the set is chosen without consulting whether the detector
succeeded, and the other half is weighted 4:1 toward the frames it refused.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_sideframes.py
from curling_score.harvest import sideframes as S


def cand(vid="v1", view="left", t=0.0, clip=0.0, pos="crossing",
         key="ok", color="red"):
    return S.SideCandidate(video_id=vid, view=view, t_abs=t, clip_start_s=clip,
                           position=pos, outcome=key, color=color,
                           crowding=0, labels=())


class TestPositionBins:
    def test_a_stone_short_of_the_line_is_an_approach(self):
        assert S.position_of(edge_row=480.0, hog_row=520.0) == "approach"

    def test_a_stone_on_the_line_is_a_crossing(self):
        assert S.position_of(edge_row=512.0, hog_row=520.0) == "crossing"

    def test_a_stone_past_the_line_is_past(self):
        assert S.position_of(edge_row=560.0, hog_row=520.0) == "past"

    def test_no_stone_at_all_is_not_a_position(self):
        assert S.position_of(edge_row=None, hog_row=520.0) is None


class TestQuotas:
    def test_the_two_halves_come_to_six_hundred(self):
        scene = 2 * sum(S.SCENE_QUOTA.values())      # per view
        assert scene == 300
        assert sum(S.OUTCOME_QUOTA.values()) == 300

    def test_refusals_outweigh_successes_four_to_one(self):
        found = S.OUTCOME_QUOTA["ok"]
        refused = sum(v for k, v in S.OUTCOME_QUOTA.items() if k != "ok")
        assert refused >= 4 * found

    def test_every_outcome_quota_names_a_real_refusal(self):
        from curling_score.detect import longview
        assert set(S.OUTCOME_QUOTA) <= set(longview.KEYS)


class TestSelect:
    def test_no_video_may_dominate(self):
        pool = [cand(vid="v1", t=float(i), clip=float(i)) for i in range(400)]
        chosen, _short = S.select(pool)
        assert len(chosen) <= S.MAX_PER_VIDEO

    def test_one_clip_cannot_fill_a_bin_with_near_duplicates(self):
        pool = [cand(vid=f"v{i}", t=float(i), clip=0.0) for i in range(5)] + \
               [cand(vid="v9", t=100.0 + j, clip=100.0) for j in range(50)]
        chosen, _short = S.select(pool)
        from collections import Counter
        per_clip = Counter((c.video_id, c.clip_start_s) for c in chosen)
        assert max(per_clip.values()) <= S.MAX_PER_CLIP

    def test_no_candidate_is_scarce_and_that_is_reported_not_hidden(self):
        """Measured supply is lopsided: over 13 ends the classical detector
        refused 106 windows as 55 ambiguous, 24 unsteady, 14 never_reached,
        11 bad_speed and only 2 no_candidate. The quota asks for 50 of the
        rarest anyway, deliberately -- it sweeps up whatever exists -- so this
        pins that the gap comes back as a number instead of being padded."""
        pool = [cand(vid=f"v{i}", t=float(i), clip=float(i), key="ambiguous")
                for i in range(400)]
        _chosen, shortfall = S.select(pool)
        assert shortfall.get("outcome:no_candidate") == \
            S.OUTCOME_QUOTA["no_candidate"]

    def test_a_bin_nobody_can_fill_is_reported_not_padded(self):
        pool = [cand(vid=f"v{i}", t=float(i), clip=float(i), pos="crossing")
                for i in range(200)]
        chosen, shortfall = S.select(pool)
        assert shortfall.get("scene:left:approach")
        assert all(c.position == "crossing" for c in chosen if c.half == "scene")

    def test_the_scene_half_takes_refused_frames_too(self):
        """The whole point: selection must not consult the detector's verdict."""
        pool = [cand(vid=f"v{i}", t=float(i), clip=float(i), key="no_candidate")
                for i in range(200)]
        chosen, _short = S.select(pool)
        assert any(c.half == "scene" for c in chosen)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/test_sideframes.py -v`
Expected: FAIL — `ModuleNotFoundError: curling_score.harvest.sideframes`

- [ ] **Step 3: Write the module**

```python
"""Which side-view frames become ds13, and why those.

``harvest/candidates.py`` chooses overhead frames by how full the house is.
This chooses by something else, because the failure being fixed is different:
the classical detector finds 20 of 27 hand-marked crossings, and the seven it
misses are the frames a replacement most needs.

Which is exactly the trap ``datasets/ds11/README.md`` documents -- every label
through ds10 was written by the colour detector, so validation mAP measured
agreement with a known-flawed opinion, and ds3 scored mAP50 0.977 while finding
11 of 17 hand-observed deliveries. Selecting by where this detector fired would
repeat it: the model would come out confidently good at the crossings we can
already time to 0.08 s, and blind in the same places.

So the set is two halves.

*Scene* frames are chosen by where the stone sits along its travel and nothing
else. Whether the detector could read the frame is not consulted, which is what
lets a refused frame into the set on its merits.

*Outcome* frames are chosen by which gate refused the window, weighted four to
one against the successes, with a control group of crossings it did find so the
set holds both.
"""

from dataclasses import dataclass, field

# How near the paint counts as "on it", in image rows. Near the hog line a
# delivery covers roughly 33 rows a second, so +-15 is about +-0.45 s -- wide
# enough that a 5 fps scan lands inside it, tight enough that the bin means
# what it says.
CROSSING_ROWS = 15.0

POSITIONS = ("approach", "crossing", "past", "occluded", "clear")

# Per view, so both wide cameras are represented by construction rather than by
# luck. 150 each, 300 in all.
#
# ``occluded`` is a colour blob with no granite under it -- a stone with a body
# over it, or a broom pad with nothing beneath. It is the detector's blind spot
# stated as a bin, and it is weighted like a real one. ``clear`` is the band
# with nothing in it at all, kept small: ds11's pilot reviewed 114 empty frames
# and got not one correction out of them.
SCENE_QUOTA = {"approach": 35, "crossing": 40, "past": 25,
               "occluded": 35, "clear": 15}

# By which gate refused the window. 240 refusals against 60 successes.
#
# The successes are a control group, not a target: without them nobody could
# tell a model that learned stones from one that learned hard frames.
# The keys are ``longview.KEYS`` verbatim -- ``ambiguous`` and ``bad_speed``,
# not the ``two_candidates``/``wrong_speed`` this plan first guessed at. They
# are a frozenset in ``detect/longview.py``; the test below pins the match so
# a renamed key cannot silently empty a bin.
OUTCOME_QUOTA = {"ok": 60, "no_candidate": 50, "ambiguous": 50,
                 "never_reached": 50, "unsteady": 45, "bad_speed": 45}

# 600 frames over 120 videos averages five. Eight lets a richer night fill a
# scarce bin without any one night becoming the dataset.
MAX_PER_VIDEO = 8
# Two per clip. A ds11 clip is 24 s and the frames inside one are near
# duplicates of each other; a bin filled from a single clip would be one
# moment wearing a quota's number.
MAX_PER_CLIP = 2


@dataclass(frozen=True)
class SideCandidate:
    """One side-view frame that could go into the set, and what was seen in it."""

    video_id: str
    view: str            # "left" or "right"
    t_abs: float
    clip_start_s: float
    position: str        # one of POSITIONS
    outcome: str         # a longview.KEYS entry for the window this sits in
    color: str           # the colour scan that proposed it, or "" for clear
    crowding: int        # dark spans beside the stone: 0 clear, 2+ crowded
    edge_row: float | None = None
    labels: tuple = field(default_factory=tuple)
    half: str = ""       # filled in by select(): "scene" or "outcome"

    @property
    def stem(self) -> str:
        return stem_for(self.video_id, self.view, self.t_abs)


def stem_for(video_id: str, view: str, t_abs: float) -> str:
    """The frame's name, and its identity.

    Shaped like ``harvest/candidates.stem_for`` so ``train/labels.parse_name``
    splits it the same way; ``l``/``r`` where the overhead set uses ``t``/``b``,
    which is also what keeps the two sets' names from ever colliding.
    """
    return f"{video_id}_{view[0]}_{t_abs:09.2f}".replace(".", "_")


def position_of(edge_row, hog_row) -> str | None:
    """Where a stone sits against the paint, or None if there was no stone."""
    if edge_row is None:
        return None
    if abs(edge_row - hog_row) <= CROSSING_ROWS:
        return "crossing"
    return "approach" if edge_row < hog_row else "past"
```

`select` then runs the same farthest-point spread `harvest/candidates._spread`
uses, over two passes:

1. **scene**, keyed `f"scene:{view}:{position}"`, quota `SCENE_QUOTA[position]`
   per view, drawing from the whole pool regardless of `outcome`;
2. **outcome**, keyed `f"outcome:{key}"`, quota `OUTCOME_QUOTA[key]`, drawing
   from what the first pass did not take.

Both honour `MAX_PER_VIDEO` and `MAX_PER_CLIP` across the *whole* selection, not
per bin — a cap that resets per bin is not a cap. Each chosen candidate is
returned with `half` set; `SideCandidate` is frozen, so that is
`dataclasses.replace(c, half="scene")`, never an attribute write. `shortfall` maps the bin key to how many it could not
supply, exactly as `harvest/candidates.select` does, and **nothing is
backfilled**: a scene bin topped up from an easier one would make the
composition a number nobody could trust, which is the whole reason that function
reports rather than pads.

Import `_spread` from `harvest.candidates` rather than copying it. It is keyed
on `c.t_abs` and `c.clip_start_s` and `c.stem`, all of which `SideCandidate`
has, so it works unchanged — verify that by reading it before you rely on it.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_sideframes.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/harvest/sideframes.py tests/test_sideframes.py
git commit -m "$(cat <<'EOF'
sideframes: choose half the set without asking the detector what it saw

Selecting by where the classical detector fired would train a model to be good
at the crossings we can already time and blind where it is not -- the
circularity ds11 exists to escape. Half the set is chosen by the scene, and the
other half is weighted four to one toward refusals.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Scan the clips and bank what each moment holds

**Files:**
- Create: `src/curling_score/harvest/sidepool.py`
- Test: `tests/test_sidepool.py`

**Interfaces:**
- Consumes: `longview.candidates`, `longview.find_in_frames`, `longview.STONE_WIDTH_AT_HOG_PX`, `sideframes.SideCandidate`, `sideframes.position_of`, `sideviews.VideoViews`, `harvest.pool.clip_moments`, `ingest.frames.stream_start_s`.
- Produces: `crowding(win, cx, edge_row, expect_px) -> int`; `scan_moments(video_id, moments, views, clip_start_s) -> list[SideCandidate]`; `scan_clip(video_id, clip_path, views, *, fps=5.0) -> (candidates, crops)`; `build_video_pool(video_id, clip_paths, video_views, out_dir, *, fps=5.0, max_per_clip_view=8, jpeg_quality=92) -> (candidates, stats)`.

`scan_moments` is where every judgement lives and it takes decoded arrays, not
a path. `scan_clip` is decode plus `scan_moments` and nothing else. The split
is deliberate: the interesting behaviour is then testable without ffmpeg, a
video file or a GPU, and `tests/test_sidepool.py` never needs media.

One decode per clip serves both views and both colours, exactly as
`harvest/pool.py` decodes once for both panels.

**Writes are capped.** 1197 clips at 24 s and 5 fps over two views is about
287,000 moments; writing them all would be days of JPEG for a 600-frame set.
`max_per_clip_view=8` keeps a spread across the position bins that clip-view
actually produced, so the pool is roughly 19,000 frames and selection still has
thirty times the set to choose from.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_sidepool.py
import numpy as np
import pytest

from curling_score.geometry import sideview
from curling_score.harvest import sidepool
from tests import synth

VIEW = sideview.SideView(rect=(0, 0, 810, 1080), tee_row=430.0, hog_row=520.0)


def moments(rows, *, color="red", fps=5.0, t0=100.0):
    """`(t, {"left": rgb})` with one stone at each of `rows`, or none for None."""
    out = []
    for i, row in enumerate(rows):
        plate = synth.side_view()
        img = plate if row is None else synth.side_view_stone(
            plate, row, width_px=52, color=color)
        out.append((t0 + i / fps, {"left": img}))
    return out


class TestCrowding:
    def test_a_lone_stone_is_uncrowded(self):
        win = np.asarray(synth.side_view_stone(synth.side_view(), 520.0,
                                               width_px=52), np.float32)
        assert sidepool.crowding(win, cx=405.0, edge_row=520.0, expect_px=52.0) == 0

    def test_a_body_beside_the_stone_counts(self):
        win = np.asarray(synth.side_view_stone(synth.side_view(), 520.0,
                                               width_px=52), np.float32)
        win[420:521, 520:600] = 20.0          # a sweeper: dark, tall, beside it
        assert sidepool.crowding(win, cx=405.0, edge_row=520.0, expect_px=52.0) == 1

    def test_the_stone_does_not_count_itself(self):
        win = np.asarray(synth.side_view_stone(synth.side_view(), 520.0,
                                               width_px=52), np.float32)
        assert sidepool.crowding(win, cx=405.0, edge_row=520.0, expect_px=52.0) == 0


class TestScanMoments:
    def test_a_stone_is_binned_by_where_it_sits(self):
        # 490, not 470: the synthetic 12-ft annulus runs rows 409-453, a
        # stone's handle sits 27 rows above its trailing edge, and the granite
        # scan stops where the dark span stops being stone-sized -- so a stone
        # at 470 has its handle in the paint and reads "occluded", correctly.
        got = sidepool.scan_moments("v1", moments([490.0, 520.0, 570.0]),
                                    {"left": VIEW}, clip_start_s=100.0)
        by_t = {c.t_abs: c for c in got if c.view == "left" and c.color == "red"}
        assert [by_t[t].position for t in sorted(by_t)] == \
            ["approach", "crossing", "past"]

    def test_a_moment_with_no_stone_is_still_a_candidate(self):
        """"clear" and "occluded" are bins, so empty moments must survive."""
        got = sidepool.scan_moments("v1", moments([None, None, None]),
                                    {"left": VIEW}, clip_start_s=100.0)
        assert got, "an empty band produced no candidate at all"
        assert {c.position for c in got} == {"clear"}
        assert all(c.labels == () for c in got)

    def test_a_handle_with_no_granite_under_it_is_occluded(self):
        plate = synth.side_view()
        plate[500:512, 395:415] = (210, 40, 40)      # a broom pad, no stone
        got = sidepool.scan_moments("v1", [(100.0, {"left": plate})],
                                    {"left": VIEW}, clip_start_s=100.0)
        assert [c.position for c in got if c.color == "red"] == ["occluded"]

    def test_every_candidate_carries_the_window_outcome(self):
        """A moment's outcome is its clip-view-colour verdict, not its own."""
        # 4.64 m in 2.20 s = 2.11 m/s, inside SPEED_BOUNDS_M_S (1.2, 3.2).
        # Derive a fixture's rows from a stated speed; do not pick round
        # numbers and hope. 440 -> 550 is 3.31 m/s and is refused.
        rows = [490.0 + 80.0 * i / 11 for i in range(12)]   # a clean crossing
        got = sidepool.scan_moments("v1", moments(rows), {"left": VIEW},
                                    clip_start_s=100.0)
        red = [c for c in got if c.color == "red"]
        assert {c.outcome for c in red} == {"ok"}

    def test_a_window_the_detector_refuses_keeps_its_reason(self):
        got = sidepool.scan_moments("v1", moments([470.0, 470.0, 470.0]),
                                    {"left": VIEW}, clip_start_s=100.0)
        red = [c for c in got if c.color == "red"]
        assert {c.outcome for c in red} <= {"no_candidate", "never_reached"}

    def test_a_proposal_becomes_a_box_whose_bottom_is_the_ice(self):
        got = sidepool.scan_moments("v1", moments([520.0]), {"left": VIEW},
                                    clip_start_s=100.0)
        (lab,) = [c.labels[0] for c in got if c.labels]
        bottom = (lab.cy + lab.h / 2) * 1080
        assert bottom == pytest.approx(520.0, abs=3.0)


class TestWriteCap:
    def test_writes_are_capped_per_clip_and_view(self, tmp_path):
        """A 24 s clip at 5 fps must not write 120 frames per view."""
        rows = [440.0 + 2 * i for i in range(120)]
        cands = sidepool.scan_moments("v1", moments(rows), {"left": VIEW},
                                      clip_start_s=100.0)
        kept = sidepool.pick_writes(cands, max_per_clip_view=8)
        assert len(kept) <= 8

    def test_the_cap_still_spans_the_bins_the_clip_produced(self, tmp_path):
        rows = [470.0] * 4 + [520.0] * 4 + [570.0] * 4
        cands = sidepool.scan_moments("v1", moments(rows), {"left": VIEW},
                                      clip_start_s=100.0)
        kept = sidepool.pick_writes(cands, max_per_clip_view=6)
        assert len({c.position for c in kept}) >= 3
```

`pick_writes(candidates, max_per_clip_view)` is the third public function this
module needs: it chooses which of a clip-view's moments are worth a JPEG,
spreading across the positions that clip actually produced rather than taking
the first eight. Keeping it separate from `build_video_pool` is what makes the
cap testable without touching the disk.

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/test_sidepool.py -v`
Expected: FAIL — `ModuleNotFoundError: curling_score.harvest.sidepool`

- [ ] **Step 3: Write the module**

`crowding` counts the dark spans standing beside the stone:

```python
def crowding(win, cx: float, edge_row: float, expect_px: float) -> int:
    """How many bodies stand beside the stone at the moment it is seen.

    A sweeper 22 m away is a tall dark column and a stone is a short wide one,
    so counting dark spans in the band just above the ice separates them
    without anything having to recognise a person. Recorded rather than gated:
    it says how hard a frame is, and that belongs in the manifest where a
    person choosing what to label can read it.
    """
    grey = win.mean(axis=2)
    top = max(0, int(edge_row - 2 * expect_px))
    band = grey[top:int(edge_row) + 1]
    if band.size == 0:
        return 0
    ice = np.percentile(grey, 90)
    dark = (band < ice - longview._BODY_DARKER_THAN_ICE).mean(axis=0) > 0.5
    spans = [(a, b) for a, b in _runs(dark) if b - a > 0.3 * expect_px]
    return sum(1 for a, b in spans if not a <= cx <= b)
```

`_runs` is `longview`'s; import it rather than writing a second one, and if that
means promoting it to a public name in `longview`, do that in this task and say
so in your report.

`scan_clip` decodes once via `harvest.pool.clip_moments` with
`rects={name: view.rect}`, then per view and per colour in `("red", "yellow")`:

- `longview.candidates(win, color, STONE_WIDTH_AT_HOG_PX)` per moment, keeping
  the proposal nearest `view.hog_row` when there is more than one;
- `longview.find_in_frames(frames, shifted_view, color, times)` once over the
  whole clip for that view and colour, giving the window's `key`;
- a `SideCandidate` per moment, with `position` from `position_of`, falling back
  to `"occluded"` when a colour blob was present but no proposal survived and
  `"clear"` when there was no colour blob at all, `outcome` from the window key,
  and `labels` from the proposal where there is one.

The view handed to `find_in_frames` must be shifted to the crop's own origin the
same way `longview.find_crossing` does it — read that function and copy the
shift rather than guessing.

The label written for a proposal is the tight box the spec asks for: `cx`
horizontally, `body_px` wide, top at `top_row`, **bottom at `edge_row`**, class
from `dataset.CLASSES.index(f"{color}_stone")`, normalised to the crop. It is a
starting point for a person, not the dataset — say so in the docstring.

`build_video_pool` walks a video's clips, calls `scan_clip`, picks up to
`max_per_clip_view` moments per clip-view spread across the positions that clip
produced, writes those as JPEG under `out_dir/<video_id>/<stem>.jpg`, and
returns `(candidates, stats)` with `stats` counting clips, moments, written,
and refusals by key.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_sidepool.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/harvest/sidepool.py tests/test_sidepool.py
git commit -m "$(cat <<'EOF'
sidepool: scan a clip's side views once and bank what each moment holds

Both views, both colours and both halves of the selection come out of one
decode, and writes are capped per clip so a 600-frame set does not cost
287,000 JPEGs.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: The four stages and the command that runs them

**Files:**
- Create: `src/curling_score/harvest/sidestages.py`
- Modify: `src/curling_score/harvest/build.py`
- Modify: `src/curling_score/cli.py`
- Test: `tests/test_sidestages.py`

**Interfaces:**
- Consumes: Tasks 1, 3 and 4.
- Produces: `STAGES = {"views", "propose", "select", "build"}`; `run(args)`; `curling-score sideframes <stage>`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_sidestages.py
import argparse
import json

import pytest

from curling_score import cli
from curling_score.harvest import sidestages


def parse(argv):
    """Parse without running: every stage touches the network, disk or GPU."""
    parser = argparse.ArgumentParser(prog="curling-score")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    cli._add_sideframes(sub)
    return parser.parse_args(argv)


def write_pool(tmp_path, videos):
    """A hand-written pool: {video_id: [candidate rows]}, no media anywhere."""
    pool = tmp_path / "pool"
    pool.mkdir()
    rows = {}
    for vid, n in videos.items():
        rows[vid] = [{
            "video_id": vid, "view": "left" if i % 2 else "right",
            "t_abs": 100.0 + i, "clip_start_s": 100.0 + i,
            "position": ("approach", "crossing", "past", "occluded",
                         "clear")[i % 5],
            "outcome": ("ok", "no_candidate", "two_candidates",
                        "never_reached", "unsteady", "wrong_speed")[i % 6],
            "color": "red", "crowding": 0, "edge_row": 500.0, "labels": [],
        } for i in range(n)]
    (pool / "candidates.json").write_text(json.dumps(rows))
    return pool


def write_videos(tmp_path, splits):
    path = tmp_path / "videos.json"
    path.write_text(json.dumps({"videos": [
        {"video_id": v, "split": s, "date": "2025-10-07", "sheet": 1}
        for v, s in splits.items()]}))
    return path


class TestManifest:
    def test_the_split_comes_from_ds11_and_holds_out_whole_videos(self, tmp_path):
        """Every frame of a val video is val; no video appears in both."""
        splits = {"a": "train", "b": "train", "c": "val"}
        args = argparse.Namespace(
            pool=write_pool(tmp_path, {v: 30 for v in splits}),
            videos=write_videos(tmp_path, splits),
            manifest=tmp_path / "manifest.json")
        assert sidestages.stage_select(args) == 0
        doc = json.loads((tmp_path / "manifest.json").read_text())
        assert doc["videos"]["c"]["split"] == "val"
        assert {e["split"] for e in doc["videos"].values()} == {"train", "val"}
        assert doc["summary"]["val_frames"] > 0

    def test_shortfall_travels_with_the_quota(self, tmp_path):
        """A bin the corpus could not fill is a number in the manifest."""
        splits = {"a": "train"}
        args = argparse.Namespace(
            pool=write_pool(tmp_path, {"a": 5}),      # nowhere near 600
            videos=write_videos(tmp_path, splits),
            manifest=tmp_path / "manifest.json")
        assert sidestages.stage_select(args) == 0
        doc = json.loads((tmp_path / "manifest.json").read_text())
        assert doc["summary"]["shortfall"], "600 frames came from 5 candidates"
        assert doc["quota"]

    def test_a_video_that_would_not_calibrate_is_named(self, tmp_path):
        """sideviews.json lists it with its reason; it is not merely absent."""
        from curling_score.harvest import sideviews
        import numpy as np

        blank = [np.full((1080, 1920, 3), 120, np.uint8) for _ in range(4)]
        doc = sideviews.to_json(sideviews.derive("bad", blank))
        assert doc["video_id"] == "bad"
        assert doc["error"]


class TestCli:
    @pytest.mark.parametrize("stage", ["views", "propose", "select", "build"])
    def test_every_stage_is_reachable(self, stage):
        assert parse(["sideframes", stage, "--root", "/tmp/c",
                      "--out", "/tmp/o"]).stage == stage

    def test_the_split_defaults_to_ds11s_own(self):
        args = parse(["sideframes", "select", "--pool", "/tmp/p"])
        assert args.videos == "datasets/ds11/videos.json"

    def test_the_manifest_defaults_into_ds13(self):
        args = parse(["sideframes", "select", "--pool", "/tmp/p"])
        assert args.manifest == "datasets/ds13/manifest.json"
```

Give every stage parser the arguments the parametrised reachability test
passes, or drop the ones it does not take and narrow that test accordingly —
whichever you choose, the test must exercise the real parser, not a copy.

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/test_sidestages.py -v`
Expected: FAIL — `ModuleNotFoundError: curling_score.harvest.sidestages`

- [ ] **Step 3: Write the stages**

Module docstring:

```python
"""ds13, as four resumable stages.

    views    ds11 clips              -> sideviews.json    both cameras, or why not
    propose  clips + sideviews.json  -> pool/             crops and what was seen
    select   pool/                   -> manifest.json     600 frames, and what was short
    build    manifest.json           -> images/labels     the YOLO tree

Each reads a file and writes a file, and each is safe to run again -- the
corpus is 1197 clips on a box across the network, and a job that starts over
when something hiccups is a job nobody leaves running.

The splits are ds11's, read straight out of ``datasets/ds11/videos.json``: the
same season, the same five held-out Tuesdays. A side-view model and an overhead
model measured on the same held-out games can be compared; measured on two
splits that merely look alike, they cannot.
"""
```

`stage_views` walks `videos.json`, takes one frame per clip via
`ingest.frames.keyframe_sweep` (as `harvest/stages.stage_pool` does), calls
`sideviews.derive`, saves `sideviews.json` after every video so the stage is
resumable, and prints a running count of how many videos have both views, one,
or none. **It exits non-zero only if no video at all calibrated** — a partial
corpus is a finding, not a failure.

`stage_propose` walks the same list, skips videos `sideviews.json` marks
unusable, calls `sidepool.build_video_pool`, and saves `pool/candidates.json`
after every video.

`stage_select` loads the pool and `datasets/ds11/videos.json`, calls
`sideframes.select`, and writes the manifest through a ds13 manifest builder.
Reuse `harvest/manifest.py` where its shape fits and add a `sideframes`-shaped
`candidate_to_json` / `candidate_from_json` / `iter_frames` trio next to it
rather than making the overhead pair polymorphic — two small readable pairs
beat one that has to ask what it is holding.

`stage_build` calls `harvest.build.build`, which gains one keyword so it can
walk either manifest:

```python
def build(doc, pool_dir, out_dir, splits=("train", "val"), iter_frames=None) -> dict:
    ...
    for split, cand in (iter_frames or M.iter_frames)(doc):
```

Wire the CLI as `_add_sideframes(sub)` beside `_add_harvest`, with the same
argument style: `--videos` defaulting to `datasets/ds11/videos.json`,
`--root` for the clips, `--out` for the pool, `--manifest` defaulting to
`datasets/ds13/manifest.json`, `--views` defaulting to
`datasets/ds13/sideviews.json`, and `--fps` defaulting to 5.0.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_sidestages.py tests/test_build.py -v`
Expected: PASS, including the existing build tests — the new keyword must
default to today's behaviour exactly.

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/python -m pytest -m "not slow"`
Expected: green.

- [ ] **Step 6: Commit**

```bash
git add src/curling_score/harvest/sidestages.py src/curling_score/harvest/build.py \
        src/curling_score/cli.py tests/test_sidestages.py
git commit -m "$(cat <<'EOF'
sideframes: four resumable stages over a corpus on another box

1197 clips across the network is not a job anyone reruns from the top, so each
stage reads a file and writes a file. The splits are ds11's own, because two
models measured on different held-out games cannot be compared.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Run it over the season and write down what it found

**Files:**
- Create: `datasets/ds13/README.md`, `datasets/ds13/sideviews.json`, `datasets/ds13/manifest.json`

This task is the controller's, not an implementer's: it runs on the worker box
(`administrator@10.0.0.182`, `/data/wdd/curling_score`), which is an rsync
target rather than a clone ([[deploying-curling-chart]]).

- [ ] **Step 1: Ship the code to the worker**

`rsync -a --exclude 'deploy/worker.env'` from a clean worktree of HEAD. Never
`--delete`: `worker.env` exists nowhere else.

- [ ] **Step 2: Run the four stages**

```bash
export PYTHONPATH=/data/wdd/curling_score/src
cs() { /data/wdd/curling/.venv/bin/python -m curling_score.cli "$@"; }

cs sideframes views   --videos datasets/ds11/videos.json \
                      --root /data/wdd/curling/ds11/clips \
                      --out  datasets/ds13/sideviews.json
cs sideframes propose --videos datasets/ds11/videos.json \
                      --root /data/wdd/curling/ds11/clips \
                      --views datasets/ds13/sideviews.json \
                      --out  /data/wdd/curling/ds13/pool
cs sideframes select  --pool /data/wdd/curling/ds13/pool \
                      --manifest datasets/ds13/manifest.json
cs sideframes build   --manifest datasets/ds13/manifest.json \
                      --pool /data/wdd/curling/ds13/pool \
                      --out /data/wdd/curling/ds13/set
```

- [ ] **Step 3: Bring the two JSON files back and write the README**

`datasets/ds13/README.md` follows ds11's and ds12's: what it is, how to rebuild
it, the decisions and why, and — the part that matters most — **what the
harvest found**. Specifically: how many of 240 views calibrated, which videos
failed and with what error, the realised composition against the quota, and
every bin that came up short.

If the calibration rate is materially below the ten-for-ten measured so far,
that is a finding about `geometry/sideview.py`'s constants and belongs in the
README and in `BACKLOG.md`, not smoothed over.

- [ ] **Step 4: Commit**

```bash
git add datasets/ds13/
git commit -m "$(cat <<'EOF'
data: 600 side-view frames to label, and what the season's views actually did

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## After the plan

The frames exist and nothing labels them. Next, in order, and each its own
piece of work: a tool for drawing boxes from scratch (`train/review.py` only
corrects), the labelling itself, training, evaluation against the 27 hand
marks, and the swap behind the `game/hogtime.py` seam.
