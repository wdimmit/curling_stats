# Practice Clips Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Each practice throw gets a phone-sized video clip, cut on the worker from the
camera facing the thrower and then the camera behind, stored in Cloud Storage and played
on the session page.

**Architecture:** A new `practice/clips.py` plans each clip as pieces of the two long
camera views and cuts it with one ffmpeg run. `PracticeWatch` cuts clips as their footage
is recorded and hands each file to a `Clipper` delivery callback. The lane uploads it as
a `clips/<id>.mp4` artifact of the practice job. The API signs a one-hour GET link for a
session's owner, and the page plays it, autoplaying the newest throw's clip while a
session is live.

**Tech Stack:** Python 3.12, ffmpeg (libx264), PyAV, FastAPI, Google Cloud Storage
signed URLs, React (esbuild bundle in `frontend/`), pytest, node.

**Spec:** `docs/superpowers/specs/2026-10-10-practice-clips-design.md`

## Global Constraints

- Piece 1 starts `PUSH_LEAD_S = 5.0` s before release; a throw that never arrived is
  capped at `FRONT_MAX_S = 10.0` s after release; piece 2 ends `REST_TAIL_S = 1.5` s
  after rest.
- Output: 600x810, H.264 (libx264, `-preset veryfast`, `-crf 27`, yuv420p), 30 fps, no
  audio, `-movflags +faststart`, at most `CLIP_MAX_BYTES = 20_000_000` bytes.
- Storage key: `runs/{video_id}/{run_id}/clips/{throw_id}.mp4`, content type `video/mp4`.
- Signed GET links last 1 hour (`CLIP_URL_S = 3600`); the page reuses a link for 50
  minutes.
- Only the owner of a session whose window holds the throw may get its link.
- Practice recordings are never kept: every clip is cut while the watch runs.
- Anything run on real footage runs at `nice -n 10` (the user's rule for evals).
- Run tests as subsets (the full suite is OOM-killed on this box). In
  `tests/test_service_api.py`, deselect
  `TestCatalogueAndAdmin::test_the_pages_load`: it fails on main already ("Most
  lopsided").
- Run Python as `../../../.venv/bin/python` from the worktree
  `/home/tcuser/src/curling_score/.claude/worktrees/practice`.

## Review Focus

- A cut that fails or hangs (ffmpeg error, a truncated recording): that throw has no clip,
  the watch carries on, and later throws still get clips. Pinned in Task 4.
- A session stopped before a throw's last 1.5 s is recorded: the clip is cut with what was
  recorded, not skipped. Pinned in Task 4.
- The side view lost the rock (no hog times), or only one long camera calibrated: there is
  still a clip from what is known. Pinned in Task 2.
- A session page left open past an hour: an expired link is fetched again when the player
  errors. Pinned in Task 7.
- A throw outside the session's window, or someone else's session: a 404, never a link.
  Pinned in Task 6.

---

### Task 1: Hog times and a clip flag on the throw record

**Files:**
- Modify: `src/curling_score/practice/throws.py:64-98` (`throw_record`)
- Test: `tests/test_practice_throws.py`

**Interfaces:**
- Produces: four new keys on every throw record, all on the stream's clock:
  `"t_hog_s"` (float or None), `"v_hog_m_s"` (float or None), `"t_far_s"` (float or None)
  and `"clip"` (always `False` when built).

- [ ] **Step 1: Write the failing tests**

In `tests/test_practice_throws.py`, in `test_a_measured_throw_is_on_the_streams_clock`,
add `shot.v_hog_m_s = 2.41` after `shot.t_hog_s = 24.0`, and at the end of the test add:

```python
        # What a clip is cut on: both hog lines, on the stream's clock.
        assert got["t_hog_s"] == 1024.0 and got["v_hog_m_s"] == 2.41
        assert got["t_far_s"] == 1037.8
        assert got["clip"] is False
```

In `test_no_release_no_broom_and_no_line_are_nulls`, add at the end:

```python
        assert got["t_hog_s"] is None and got["v_hog_m_s"] is None
        assert got["t_far_s"] is None and got["clip"] is False
```

- [ ] **Step 2: Run them to see them fail**

Run: `../../../.venv/bin/python -m pytest tests/test_practice_throws.py -q -p no:warnings`
Expected: 2 failed with `KeyError: 't_hog_s'`

- [ ] **Step 3: Implement**

In `throw_record`, replace the line
`far=fartime.crossing(shot))` usage by naming the crossing once, and add the keys:

```python
def throw_record(shot, *, house: str, t0_s: float = 0.0) -> dict:
    """``shot`` as the page reads it. ``house`` is the end it was thrown to;
    ``t0_s`` is where the recording begins on the stream's clock."""
    dv, rel = shot.delivery, shot.release
    line, broom = shot.line, shot.target_broom
    far = fartime.crossing(shot)
    t_hog = hogtime.crossing(shot)
    sp = split.long_split(dv, t_hog=t_hog, v_hog=hogtime.speed_at_hog(shot), far=far)
    t_thrown = rel.t if rel is not None else dv.t_enter
    return {
        "id": f"t_{t0_s + t_thrown:.1f}",
        "t_release_s": None if rel is None else _r(t0_s + rel.t, 2),
        "t_rest_s": None if dv is None else _r(t0_s + dv.t_rest, 2),
        "house": house,
        "color": shot.color,
        "arrived": dv is not None,
        "release_source": None if rel is None else getattr(rel, "source", "overhead"),
        "split_s": None if sp is None else _r(sp.seconds, 2),
        "release_speed": None if rel is None else _r(rel.speed_m_s),
        # Where a clip cuts (practice/clips.py): the throwing end's hog line,
        # from the side view, and the destination's, from its panel.
        "t_hog_s": None if t_hog is None else _r(t0_s + t_hog, 2),
        "v_hog_m_s": _r(hogtime.speed_at_hog(shot)),
        "t_far_s": None if far is None or far.t is None else _r(t0_s + far.t, 2),
        # Set once the worker has cut and uploaded this throw's clip.
        "clip": False,
        "curl": None if line is None else line.curl,
        ... (the remaining keys exactly as they are: "broom", "line", "rest", "track")
    }
```

Keep `"broom"`, `"line"`, `"rest"` and `"track"` unchanged after `"curl"`.

- [ ] **Step 4: Run the tests**

Run: `../../../.venv/bin/python -m pytest tests/test_practice_throws.py tests/test_practice_watch.py tests/test_practice_harness.py -q -p no:warnings`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/practice/throws.py tests/test_practice_throws.py
git commit -m "practice: a throw records both hog line times, and whether it has a clip"
```

---

### Task 2: The clip plan

**Files:**
- Create: `src/curling_score/practice/clips.py`
- Test: `tests/test_practice_clips.py`

**Interfaces:**
- Consumes: the throw record keys from Task 1; `SideView.rect` and
  `SideView.metres_at(row)` (`geometry/sideview.py`); `hogtime.CAMERA_FOR`;
  `enrich.OTHER_HOUSE`.
- Produces:
  - `Piece(view: str, t0: float, t1: float)` (frozen dataclass)
  - `ClipPlan(pieces: tuple[Piece, ...])` with `.end -> float` and
    `.until(t: float) -> ClipPlan | None`
  - `plan(throw: dict, sideviews: dict | None) -> ClipPlan | None`
  - `exit_time(throw: dict, view) -> float | None`
  - constants `PUSH_LEAD_S`, `FRONT_MAX_S`, `REST_TAIL_S`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_practice_clips.py`:

```python
"""A practice throw's clip: which camera when, and the cut itself."""

from types import SimpleNamespace

import pytest

from curling_score.practice import clips as C


def view(exit_m=20.0):
    """A long camera whose bottom row is ``exit_m`` from the far tee."""
    return SimpleNamespace(rect=(0, 0, 800, 1080), metres_at=lambda row: exit_m)


def both(exit_m=20.0):
    return {"left": view(exit_m), "right": view(exit_m)}


def throw(house="top", rel=100.0, t_hog=104.0, v_hog=None, t_far=114.0, rest=126.0,
          arrived=True):
    return {"id": f"t_{rel:.1f}", "house": house, "t_release_s": rel, "t_hog_s": t_hog,
            "v_hog_m_s": v_hog, "t_far_s": t_far, "t_rest_s": rest if arrived else None,
            "arrived": arrived, "clip": False}


class TestCameras:
    def test_to_the_top_house_the_right_camera_faces_the_thrower(self):
        p = C.plan(throw(house="top"), both())
        assert [x.view for x in p.pieces] == ["right", "left"]

    def test_to_the_bottom_house_the_left_camera_faces_the_thrower(self):
        p = C.plan(throw(house="bottom"), both())
        assert [x.view for x in p.pieces] == ["left", "right"]

    def test_it_starts_before_the_push_and_ends_after_rest(self):
        p = C.plan(throw(), both())
        assert p.pieces[0].t0 == 95.0
        assert p.pieces[1].t0 == 114.0 and p.pieces[1].t1 == 127.5
        assert p.end == 127.5


class TestLeavingTheFrame:
    def test_at_a_steady_speed_by_distance(self):
        # Hog to hog is 21.945 m in 10 s; the bottom row is 13.599 m past the
        # near hog line.
        t = C.exit_time(throw(), view(20.0))
        assert t == pytest.approx(110.197, abs=0.01)

    def test_slowing_down_from_its_speed_at_the_hog_line(self):
        t = C.exit_time(throw(v_hog=2.6), view(20.0))
        assert t == pytest.approx(109.745, abs=0.01)

    def test_a_frame_that_reaches_past_the_far_hog_line_ends_there(self):
        assert C.exit_time(throw(), view(30.0)) == 114.0

    def test_without_both_hog_times_it_is_not_known(self):
        assert C.exit_time(throw(t_hog=None), view()) is None
        assert C.exit_time(throw(t_far=None), view()) is None

    def test_the_first_piece_ends_as_the_rock_leaves(self):
        p = C.plan(throw(), both(20.0))
        assert p.pieces[0].t1 == pytest.approx(110.197, abs=0.01)


class TestWhatIsMissing:
    def test_no_hog_time_cuts_over_at_the_far_hog_line(self):
        p = C.plan(throw(t_hog=None), both())
        assert p.pieces[0].t1 == 114.0 and p.pieces[1].t0 == 114.0

    def test_no_hog_times_at_all_gives_the_thrower_ten_seconds(self):
        p = C.plan(throw(t_hog=None, t_far=None), both())
        assert p.pieces[0].t1 == 110.0
        assert p.pieces[1].t0 == 110.0 and p.pieces[1].t1 == 127.5

    def test_a_throw_that_never_arrived_is_one_piece(self):
        p = C.plan(throw(arrived=False, t_far=None), both())
        assert [(x.view, x.t0, x.t1) for x in p.pieces] == [("right", 95.0, 110.0)]

    def test_only_the_camera_behind_is_one_piece_from_it(self):
        p = C.plan(throw(house="top"), {"left": view()})
        assert [(x.view, x.t0, x.t1) for x in p.pieces] == [("left", 95.0, 127.5)]

    def test_only_the_camera_facing_is_one_piece_from_it(self):
        p = C.plan(throw(house="top"), {"right": view()})
        assert [(x.view, x.t0, x.t1) for x in p.pieces] == [("right", 95.0, 127.5)]

    def test_no_release_or_no_side_views_is_no_clip(self):
        assert C.plan({**throw(), "t_release_s": None}, both()) is None
        assert C.plan(throw(), None) is None and C.plan(throw(), {}) is None


class TestUntil:
    def test_a_plan_cut_short_where_the_recording_stops(self):
        p = C.plan(throw(), both()).until(120.0)
        assert p.end == 120.0 and len(p.pieces) == 2
        assert C.plan(throw(), both()).until(112.0).pieces[-1].view == "right"
        assert C.plan(throw(), both()).until(90.0) is None
```

- [ ] **Step 2: Run them to see them fail**

Run: `../../../.venv/bin/python -m pytest tests/test_practice_clips.py -q -p no:warnings`
Expected: errors, `ModuleNotFoundError: No module named 'curling_score.practice.clips'`

- [ ] **Step 3: Implement the plan**

Create `src/curling_score/practice/clips.py`:

```python
"""A practice throw's clip: the camera facing the thrower, then the one behind.

The camera at the destination end looks back up the sheet at the thrower, so
it shows the push, the slide and the release, until the rock comes too close
and runs out of the bottom of its frame. From the far hog line on, the camera
at the throwing end, behind the thrower, follows the rock to rest.

A clip is planned as pieces of named camera views, on the stream's clock like
every other practice time. Only `cut` knows that today both views are crops of
one composite stream; with the club's own cameras each view will be a file of
its own, and nothing else changes.
"""

import math
from dataclasses import dataclass

from curling_score.game.hogtime import CAMERA_FOR
from curling_score.geometry import constants as G
from curling_score.practice.enrich import OTHER_HOUSE

# Before the release, to take in the push from the hack and the slide.
PUSH_LEAD_S = 5.0
# A throw nothing saw arrive, or with no hog line times: this long after release.
FRONT_MAX_S = 10.0
# After the rock stops, so the clip does not end on the stop itself.
REST_TAIL_S = 1.5

# The two hog lines, as metres from the throwing tee.
NEAR_HOG_M = G.TEE_TO_HOGLINE_M
FAR_HOG_M = G.TEE_TO_TEE_M - G.TEE_TO_HOGLINE_M


@dataclass(frozen=True)
class Piece:
    view: str          # "left" or "right"
    t0: float
    t1: float


@dataclass(frozen=True)
class ClipPlan:
    pieces: tuple

    @property
    def end(self) -> float:
        return self.pieces[-1].t1

    def until(self, t: float):
        """This plan, stopping at ``t``: where a recording ended early. None
        when nothing of it is left."""
        kept = tuple(Piece(p.view, p.t0, min(p.t1, t)) for p in self.pieces if p.t0 < t)
        return ClipPlan(kept) if kept else None


def exit_time(throw: dict, view) -> float | None:
    """When the rock runs out of the bottom of ``view``, the camera facing the
    thrower, or None if that cannot be told.

    The bottom row's distance from the throwing tee comes from the view's paint
    calibration (extrapolated past the hog row it was fitted at). The rock is
    taken to slow evenly between the hog lines: it leaves the near one at its
    measured speed and reaches the far one when the panel saw it. Without a
    speed, it goes at the average.
    """
    t_hog, t_far, v = throw.get("t_hog_s"), throw.get("t_far_s"), throw.get("v_hog_m_s")
    if t_hog is None or t_far is None or t_far <= t_hog:
        return None
    try:
        at = view.metres_at(view.rect[3] - 1)
    except (ZeroDivisionError, ValueError):
        return None
    if not math.isfinite(at) or at <= NEAR_HOG_M:
        return None
    if at >= FAR_HOG_M:
        return t_far
    span, gone, dist = t_far - t_hog, at - NEAR_HOG_M, FAR_HOG_M - NEAR_HOG_M
    if v is None or v <= dist / span:
        return t_hog + span * gone / dist
    slow = 2.0 * (v * span - dist) / span ** 2          # deceleration, m/s^2
    disc = v * v - 2.0 * slow * gone
    if disc < 0:
        return t_far
    return t_hog + (v - math.sqrt(disc)) / slow


def plan(throw: dict, sideviews) -> ClipPlan | None:
    """The clip for ``throw``, or None: a stone nobody released has none, and
    neither does a sheet whose long cameras were not found."""
    rel = throw.get("t_release_s")
    if rel is None or not sideviews:
        return None
    house = throw["house"]
    facing, behind = CAMERA_FOR[OTHER_HOUSE[house]], CAMERA_FOR[house]
    start = rel - PUSH_LEAD_S
    rest = throw.get("t_rest_s")
    last = rest + REST_TAIL_S if throw.get("arrived") and rest is not None else None
    if facing not in sideviews and behind not in sideviews:
        return None
    if facing not in sideviews or behind not in sideviews or last is None:
        one = facing if facing in sideviews else behind
        if last is None:
            out = exit_time(throw, sideviews[facing]) if one == facing else None
            end = min(out, rel + FRONT_MAX_S) if out is not None else rel + FRONT_MAX_S
        else:
            end = last
        return ClipPlan((Piece(one, start, end),))
    t_far = throw.get("t_far_s")
    leaves = exit_time(throw, sideviews[facing])
    if leaves is None:
        leaves = t_far if t_far is not None else rel + FRONT_MAX_S
    picks_up = t_far if t_far is not None else leaves
    pieces = [Piece(facing, start, leaves)]
    if last > picks_up:
        pieces.append(Piece(behind, picks_up, last))
    return ClipPlan(tuple(pieces))
```

- [ ] **Step 4: Run the tests**

Run: `../../../.venv/bin/python -m pytest tests/test_practice_clips.py -q -p no:warnings`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/practice/clips.py tests/test_practice_clips.py
git commit -m "practice: plan each throw's clip -- the camera facing the thrower, then the one behind"
```

---

### Task 3: The cut

**Files:**
- Modify: `src/curling_score/practice/clips.py`
- Test: `tests/test_practice_clips.py`

**Interfaces:**
- Consumes: `ClipPlan`, `Piece` from Task 2; `frames.stream_start_s(path)`,
  `frames.seek_lead(path)` (`ingest/frames.py`).
- Produces: `cut(plan, views, out_path, *, t0_s=0.0, size=(600, 810), fps=30,
  timeout_s=CUT_TIMEOUT_S, run=subprocess.run) -> None`, where `views` maps a view name
  to `(source path, (x, y, w, h) or None)`. It raises `subprocess.SubprocessError` or
  `OSError` when the cut fails. Constants `CLIP_SIZE = (600, 810)`, `CLIP_FPS = 30`,
  `CLIP_MAX_BYTES = 20_000_000`, `CUT_TIMEOUT_S = 120.0`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_practice_clips.py`:

```python
import shutil
import subprocess

import numpy as np

from curling_score.ingest import frames

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg")


@pytest.fixture(scope="module")
def composite(tmp_path_factory):
    """Ten seconds of a 320x180 'stream' at 10 fps: the left view red, then
    blue from 5 s; a grey strip; the right view lime, then white from 5 s. As
    MPEG-TS starting 30 s into its own clock, as a live recording does."""
    out = tmp_path_factory.mktemp("clips") / "rec.ts"
    graph = ("color=red:s=120x180:r=10:d=5[a];color=blue:s=120x180:r=10:d=5[b];"
             "[a][b]concat=n=2:v=1[L];color=gray:s=80x180:r=10:d=10[M];"
             "color=lime:s=120x180:r=10:d=5[c];color=white:s=120x180:r=10:d=5[d];"
             "[c][d]concat=n=2:v=1[R];[L][M][R]hstack=inputs=3[v]")
    subprocess.run(["ffmpeg", "-v", "error", "-filter_complex", graph, "-map", "[v]",
                    "-c:v", "libx264", "-g", "10", "-pix_fmt", "yuv420p",
                    "-output_ts_offset", "30", "-f", "mpegts", str(out)], check=True)
    return out


def views(src):
    return {"left": (src, (0, 0, 120, 180)), "right": (src, (200, 0, 120, 180))}


def colour_at(path, t):
    """The mean BGR of the clip's frame at ``t``."""
    _t, img = next(frames.window(path, t, t + 0.5, 10))
    return img.reshape(-1, 3).mean(axis=0)


def is_colour(bgr, want):
    return np.abs(np.array(bgr) - np.array(want)).max() < 60


@needs_ffmpeg
class TestCut:
    def test_the_fixture_starts_late_on_its_own_clock(self, composite):
        assert frames.stream_start_s(composite) > 29.0

    def test_each_piece_shows_its_view_at_its_time(self, composite, tmp_path):
        out = tmp_path / "t.mp4"
        p = C.ClipPlan((C.Piece("right", 1.0, 3.0), C.Piece("left", 6.0, 8.0)))
        C.cut(p, views(composite), out, size=(60, 90), fps=10)
        probe = frames.probe(out)
        assert (probe.width, probe.height) == (60, 90)
        assert probe.duration_s == pytest.approx(4.0, abs=0.3)
        assert is_colour(colour_at(out, 0.5), (0, 255, 0))       # right view, lime
        assert is_colour(colour_at(out, 3.2), (255, 0, 0))       # left view, blue

    def test_times_on_the_streams_clock(self, composite, tmp_path):
        out = tmp_path / "t.mp4"
        p = C.ClipPlan((C.Piece("left", 101.0, 103.0),))
        C.cut(p, views(composite), out, t0_s=100.0, size=(60, 90), fps=10)
        assert frames.probe(out).duration_s == pytest.approx(2.0, abs=0.3)
        assert is_colour(colour_at(out, 0.5), (0, 0, 255))       # left view, red

    def test_a_failed_cut_raises(self, tmp_path):
        p = C.ClipPlan((C.Piece("left", 1.0, 2.0),))
        with pytest.raises((subprocess.SubprocessError, OSError)):
            C.cut(p, views(tmp_path / "missing.ts"), tmp_path / "t.mp4")
```

- [ ] **Step 2: Run them to see them fail**

Run: `../../../.venv/bin/python -m pytest tests/test_practice_clips.py -q -p no:warnings -k Cut`
Expected: the fixture test passes; the other three fail with `AttributeError: module 'curling_score.practice.clips' has no attribute 'cut'`

- [ ] **Step 3: Implement the cut**

Add to `src/curling_score/practice/clips.py` (imports at the top: `import subprocess`,
`from pathlib import Path`, `from curling_score.ingest import frames`):

```python
# Portrait, for a phone: a long camera's panel is about 800x1080 of the stream.
CLIP_SIZE = (600, 810)
CLIP_FPS = 30
CLIP_MAX_BYTES = 20_000_000
CUT_TIMEOUT_S = 120.0


def cut(plan: ClipPlan, views: dict, out_path, *, t0_s: float = 0.0, size=CLIP_SIZE,
        fps: int = CLIP_FPS, timeout_s: float = CUT_TIMEOUT_S, run=subprocess.run) -> None:
    """Cut ``plan`` into ``out_path`` with one ffmpeg run.

    ``views`` maps a view to ``(source, crop)``: the file it is in and the
    rectangle of it, or None for a whole frame. Times are on the stream's
    clock; ``t0_s`` is where the sources begin on it.

    Timestamps are kept (``-copyts``) and each piece is trimmed by the source's
    own timestamps, the first frame's start added, which is the frame reader's
    convention (`frames.window`): a release read at t is at t here. An MPEG-TS
    recording has no index and a seek lands on the keyframe after its target,
    so each input is entered `frames.seek_lead` early. Bounded by `-fs` and a
    timeout, as `harvest/clips.py` learned to be.
    """
    sources = list(dict.fromkeys(views[p.view][0] for p in plan.pieces))
    width, height = size
    args = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-copyts"]
    offsets = {}
    for src in sources:
        offsets[src] = frames.stream_start_s(src)
        mine = [p for p in plan.pieces if views[p.view][0] == src]
        lo = min(p.t0 for p in mine) - t0_s
        hi = max(p.t1 for p in mine) - t0_s
        args += ["-ss", f"{max(0.0, lo - frames.seek_lead(src)):.3f}",
                 "-to", f"{hi + 1.0:.3f}", "-i", str(src)]
    graph, branches = [], {}
    for i, src in enumerate(sources):
        n = sum(1 for p in plan.pieces if views[p.view][0] == src)
        labels = [f"s{i}_{j}" for j in range(n)]
        branches[src] = list(labels)
        graph.append(f"[{i}:v]split={n}" + "".join(f"[{x}]" for x in labels)
                     if n > 1 else f"[{i}:v]null[{labels[0]}]")
    for k, p in enumerate(plan.pieces):
        src, crop = views[p.view]
        a = p.t0 - t0_s + offsets[src]
        b = p.t1 - t0_s + offsets[src]
        box = f"crop={crop[2]}:{crop[3]}:{crop[0]}:{crop[1]}," if crop else ""
        graph.append(f"[{branches[src].pop(0)}]trim=start={a:.3f}:end={b:.3f},"
                     f"setpts=PTS-STARTPTS,{box}scale={width}:{height},setsar=1,"
                     f"fps={fps}[p{k}]")
    graph.append("".join(f"[p{k}]" for k in range(len(plan.pieces)))
                 + f"concat=n={len(plan.pieces)}:v=1:a=0[v]")
    args += ["-filter_complex", ";".join(graph), "-map", "[v]", "-an",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "27", "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", "-fs", str(CLIP_MAX_BYTES), str(out_path)]
    run(args, check=True, capture_output=True, timeout=timeout_s)
    if not Path(out_path).is_file() or Path(out_path).stat().st_size == 0:
        raise OSError(f"ffmpeg wrote no clip at {out_path}")
```

`frames.stream_start_s` raises an `av` error on a missing file. Make `cut` raise `OSError`
for it, by wrapping the offsets loop:

```python
    for src in sources:
        try:
            offsets[src] = frames.stream_start_s(src)
        except Exception as exc:  # noqa: BLE001 - PyAV raises its own kinds
            raise OSError(f"cannot read {src}: {exc}") from exc
        ...
```

- [ ] **Step 4: Run the tests**

Run: `../../../.venv/bin/python -m pytest tests/test_practice_clips.py -q -p no:warnings`
Expected: all pass. If `test_each_piece_shows_its_view_at_its_time` shows the wrong
colours, the timestamp convention is off; check `ffprobe -show_frames` on the output
before changing the expectation, which is the point of the test.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/practice/clips.py tests/test_practice_clips.py
git commit -m "practice: cut a clip -- each view's crop over its piece of time, joined, H.264 for phones"
```

---

### Task 4: The Clipper, and the watch cutting clips as footage arrives

**Files:**
- Modify: `src/curling_score/practice/clips.py` (add `Clipper`)
- Modify: `src/curling_score/practice/watch.py` (`__init__`, `step`, `_read`, new `_clip`)
- Test: `tests/test_practice_clips.py`, `tests/test_practice_watch.py`

**Interfaces:**
- Consumes: `plan`, `cut`, `ClipPlan.until` (Tasks 2-3).
- Produces:
  - `Clipper(recording, deliver, workdir, *, keep=False, cut=cut, progress=log.info)`
    with `.plan(throw, cal) -> ClipPlan | None` and
    `.make(throw, plan, cal, *, t0_s=0.0) -> bool`. `deliver(throw_id: str, path: Path)
    -> bool` is called with the cut file; the file is deleted afterwards unless `keep`.
  - `PracticeWatch(..., clipper=None)`. A throw whose clip was made has
    `throw["clip"] is True`, and the document is published again.

- [ ] **Step 1: Write the failing Clipper tests**

Append to `tests/test_practice_clips.py`:

```python
class TestClipper:
    def cal(self):
        return SimpleNamespace(sideviews={"left": SimpleNamespace(rect=(0, 0, 120, 180)),
                                          "right": SimpleNamespace(rect=(200, 0, 120, 180))})

    def test_a_cut_clip_is_delivered_then_removed(self, tmp_path):
        got, cuts = [], []

        def fake_cut(plan, views, out, *, t0_s=0.0):
            cuts.append((views["right"], t0_s))
            out.write_bytes(b"mp4")

        def deliver(tid, path):
            got.append((tid, path.read_bytes()))
            return True

        c = C.Clipper(SimpleNamespace(path="rec.ts"), deliver, tmp_path, cut=fake_cut)
        th = throw()
        assert c.make(th, C.plan(th, both()), self.cal(), t0_s=7.0) is True
        assert got == [("t_100.0", b"mp4")]
        assert cuts == [(("rec.ts", (200, 0, 120, 180)), 7.0)]
        assert list(tmp_path.iterdir()) == []

    def test_a_failed_cut_is_not_delivered(self, tmp_path):
        def bad_cut(plan, views, out, *, t0_s=0.0):
            out.write_bytes(b"half")
            raise subprocess.CalledProcessError(1, "ffmpeg")

        got = []
        c = C.Clipper(SimpleNamespace(path="rec.ts"), lambda *a: got.append(a), tmp_path,
                      cut=bad_cut)
        th = throw()
        assert c.make(th, C.plan(th, both()), self.cal()) is False
        assert got == [] and list(tmp_path.iterdir()) == []

    def test_kept_clips_stay_where_they_were_cut(self, tmp_path):
        c = C.Clipper(SimpleNamespace(path="rec.ts"), lambda tid, p: True, tmp_path,
                      keep=True, cut=lambda plan, views, out, **k: out.write_bytes(b"x"))
        th = throw()
        assert c.make(th, C.plan(th, both()), self.cal())
        assert (tmp_path / "t_100.0.mp4").read_bytes() == b"x"

    def test_its_plan_is_the_throws_with_the_calibrations_views(self):
        c = C.Clipper(SimpleNamespace(path="rec.ts"), lambda *a: True, ".")
        assert c.plan(throw(), SimpleNamespace(sideviews=both())) is not None
        assert c.plan(throw(), SimpleNamespace(sideviews=None)) is None
```

- [ ] **Step 2: Write the failing watch tests**

Append to `tests/test_practice_watch.py`:

```python
from curling_score.practice import clips as CL


class Clips:
    """A clipper that plans from the release to ``tail`` after rest."""

    def __init__(self, works=True, tail=1.5):
        self.works, self.tail, self.made = works, tail, []

    def plan(self, throw, cal):
        if throw["t_release_s"] is None:
            return None
        end = (throw["t_rest_s"] or throw["t_release_s"]) + self.tail
        return CL.ClipPlan((CL.Piece("right", throw["t_release_s"] - 5.0, end),))

    def make(self, throw, plan, cal, *, t0_s=0.0):
        self.made.append((throw["id"], plan.end))
        return self.works


def clipped(clipper, until_s=100.0):
    rec, docs = Rec(), []
    w = W.PracticeWatch(recording=rec, pipeline=Pipeline(
                            top=sheet(thrown("red", t0=40.0), until_s=until_s),
                            bottom=sheet(hack("red", 22.0), until_s=until_s)),
                        models=SimpleNamespace(detector=None), since_s=20.0,
                        publish=docs.append, clock=lambda: 0.0, clipper=clipper)
    while rec.head < until_s:
        rec.head = min(until_s, rec.head + 1.0)
        while w.step():
            pass
    rec.over = True
    while w.step():
        pass
    return w, docs


def test_each_throw_is_clipped_once_its_footage_is_recorded():
    clips = Clips()
    w, docs = clipped(clips)
    (th,) = w.throws
    assert th["clip"] is True and docs[-1]["throws"][0]["clip"] is True
    assert len(clips.made) == 1 and clips.made[0][1] == th["t_rest_s"] + 1.5


def test_a_clip_that_fails_is_not_tried_again_and_the_watch_carries_on():
    clips = Clips(works=False)
    w, docs = clipped(clips)
    assert w.throws[0]["clip"] is False and len(clips.made) == 1
    assert docs[-1]["status"] == "ended"


def test_a_watch_that_ends_first_cuts_what_was_recorded():
    clips = Clips(tail=500.0)                  # wants far more than is recorded
    w, _ = clipped(clips)
    assert w.throws[0]["clip"] is True
    assert clips.made[0][1] == pytest.approx(w.t0_s + w.done_s)


def test_without_a_clipper_nothing_is_clipped():
    w, _ = clipped(None)
    assert w.throws[0]["clip"] is False
```

- [ ] **Step 3: Run them to see them fail**

Run: `../../../.venv/bin/python -m pytest tests/test_practice_clips.py tests/test_practice_watch.py -q -p no:warnings`
Expected: the new tests fail (`AttributeError: ... no attribute 'Clipper'`;
`TypeError: ... unexpected keyword argument 'clipper'`)

- [ ] **Step 4: Implement the Clipper**

Add to `src/curling_score/practice/clips.py` (imports: `import logging`, `import time`;
`log = logging.getLogger(__name__)`):

```python
class Clipper:
    """Cuts each throw's clip from the recording a watch is reading, and hands
    the file to ``deliver(throw_id, path) -> bool``: an upload on a worker, a
    directory on a replay. The file is removed afterwards unless ``keep``."""

    def __init__(self, recording, deliver, workdir, *, keep: bool = False, cut=cut,
                 progress=log.info):
        self.recording, self.deliver = recording, deliver
        self.workdir, self.keep = Path(workdir), keep
        self.cut, self.progress = cut, progress

    def plan(self, throw, cal):
        return plan(throw, getattr(cal, "sideviews", None))

    def make(self, throw, plan_, cal, *, t0_s: float = 0.0) -> bool:
        """Cut and deliver one clip; False if either did not happen."""
        views = {name: (self.recording.path, sv.rect) for name, sv in cal.sideviews.items()}
        self.workdir.mkdir(parents=True, exist_ok=True)
        out = self.workdir / f"{throw['id']}.mp4"
        began = time.monotonic()
        try:
            self.cut(plan_, views, out, t0_s=t0_s)
        except (subprocess.SubprocessError, OSError) as exc:
            log.warning("clip %s was not cut: %s", throw["id"], exc)
            out.unlink(missing_ok=True)
            return False
        took, size = time.monotonic() - began, out.stat().st_size
        try:
            sent = bool(self.deliver(throw["id"], out))
        finally:
            if not self.keep:
                out.unlink(missing_ok=True)
        self.progress(f"clip {throw['id']}: {plan_.end - plan_.pieces[0].t0:.1f} s of "
                      f"video, cut in {took:.1f} s, {size / 1e6:.2f} MB"
                      + ("" if sent else ", not delivered"))
        return sent
```

- [ ] **Step 5: Implement the watch's clipping**

In `src/curling_score/practice/watch.py`:

`__init__`: add the keyword `clipper=None` to the signature (after `publish=None`), and
in the body:

```python
        # Cuts each throw's clip once its footage is recorded (practice/clips.py).
        self.clipper = clipper
        self._clip_tried: set = set()
```

`step`: in the branch that ends the watch after reading, cut what is left first:

```python
        if ended or self._stop:
            self.status, self.done = "ended", True
            self._clip(final=True)
            self._publish()
            return True
```

`_read`: replace the last two lines

```python
        if added:
            self._publish()
```

with

```python
        if self._clip() or added:
            self._publish()
```

and add the method after `_read`:

```python
    def _clip(self, final: bool = False) -> bool:
        """Cut the clips whose footage is now recorded, oldest first; True if
        any was made. Each throw is tried once. ``final``: the watch is ending,
        so a clip runs to where the recording stops rather than waiting."""
        if self.clipper is None or self.cal is None:
            return False
        recorded = self.t0_s + self.done_s
        made = False
        for th in self.throws:
            if th.get("clip") or th["id"] in self._clip_tried:
                continue
            plan = self.clipper.plan(th, self.cal)
            if plan is not None and plan.end > recorded:
                if not final:
                    continue
                plan = plan.until(recorded)
            self._clip_tried.add(th["id"])
            if plan is not None and self.clipper.make(th, plan, self.cal, t0_s=self.t0_s):
                th["clip"] = True
                made = True
        return made
```

- [ ] **Step 6: Run the tests**

Run: `../../../.venv/bin/python -m pytest tests/test_practice_clips.py tests/test_practice_watch.py tests/test_practice_harness.py tests/test_practice_pipeline.py -q -p no:warnings`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add src/curling_score/practice/clips.py src/curling_score/practice/watch.py tests/test_practice_clips.py tests/test_practice_watch.py
git commit -m "practice: the watch cuts each throw's clip once its footage is recorded"
```

---

### Task 5: The lane uploads clips

**Files:**
- Modify: `src/curling_score/live/manager.py:34-50` (`Stream`)
- Modify: `src/curling_score/live/lane.py` (session creation, new `_send_clip`,
  `video_sessions`)
- Test: `tests/test_live_lane.py`

**Interfaces:**
- Consumes: `Clipper` (Task 4); `api.artifacts(job_id, worker_id, files, detcache)`,
  `api.upload(url, headers, data)` (`service/worker.py`).
- Produces: `Stream.send_clip: Callable[[str, Path], bool] | None`, set by the lane on
  practice streams; `Lane._send_clip(stream, throw_id, path) -> bool`, uploading the
  artifact named `clips/<throw_id>.mp4`.

- [ ] **Step 1: Write the failing tests**

Append to `class TestPracticeInTheLane` in `tests/test_live_lane.py`:

```python
    def test_a_clip_goes_up_as_an_artifact_of_the_job(self, tmp_path):
        api = Api()
        s = pstream(1)
        ln, _, _ = lane(api, [s], {"p_1": lambda st, pub: Watch(st, pub)})
        ln.step()
        clip = tmp_path / "t_22.0.mp4"
        clip.write_bytes(b"\x00" * 1000)
        assert s.send_clip("t_22.0", clip) is True
        assert api.artifact_files[-1] == [{"name": "clips/t_22.0.mp4", "bytes": 1000}]
        assert api.uploads[-1][1] == b"\x00" * 1000

    def test_a_clip_that_cannot_go_up_says_so(self, tmp_path):
        api = Api()
        s = pstream(1)
        ln, _, _ = lane(api, [s], {"p_1": lambda st, pub: Watch(st, pub)})
        ln.step()

        def down(*a, **k):
            raise RuntimeError("503")

        api.artifacts = down
        clip = tmp_path / "t_22.0.mp4"
        clip.write_bytes(b"x")
        assert s.send_clip("t_22.0", clip) is False

    def test_a_lost_job_while_uploading_a_clip_is_raised(self, tmp_path):
        from curling_score.service.worker import Lost

        api = Api()
        s = pstream(1)
        ln, _, _ = lane(api, [s], {"p_1": lambda st, pub: Watch(st, pub)})
        ln.step()

        def lost(*a, **k):
            raise Lost("p_1")

        api.artifacts = lost
        clip = tmp_path / "t_22.0.mp4"
        clip.write_bytes(b"x")
        with pytest.raises(Lost):
            s.send_clip("t_22.0", clip)

    def test_a_practice_watch_gets_a_clipper_that_sends_through_the_lane(self, monkeypatch):
        from curling_score import analyze
        from curling_score.practice.clips import Clipper

        monkeypatch.setattr(analyze, "load_models", lambda *a, **k: (None, None, None))
        ps = pstream(1)
        ps.send_clip = lambda tid, path: True
        watch = lane_mod.video_sessions("w.pt", progress=lambda m: None)(ps, lambda d: None)
        assert isinstance(watch.clipper, Clipper) and watch.clipper.deliver is ps.send_clip
        assert watch.clipper.keep is False
```

`Lost` (`service/worker.py:36`) is a plain `Exception`, so `Lost("p_1")` is all it needs.

- [ ] **Step 2: Run them to see them fail**

Run: `../../../.venv/bin/python -m pytest tests/test_live_lane.py -q -p no:warnings -k "clip"`
Expected: 4 failed (`AttributeError: 'Stream' object has no attribute 'send_clip'`)

- [ ] **Step 3: Implement**

`src/curling_score/live/manager.py`, in `Stream`, after `stop_requested`:

```python
    # A practice stream's way to send a throw's clip up: set by the lane.
    send_clip: object = field(default=None, repr=False)
```

`src/curling_score/live/lane.py`, where sessions are made (around line 66):

```python
        for stream in ready:
            if stream.session is None:
                if is_practice(stream.job):
                    stream.send_clip = lambda tid, path, s=stream: self._send_clip(s, tid, path)
                try:
                    stream.session = self.make_session(
                        stream, lambda doc, s=stream: self._publish(s, doc))
```

Add after `_send` (with `from pathlib import Path` at the top of the module if absent):

```python
    def _send_clip(self, stream, throw_id, path) -> bool:
        """One throw's clip, uploaded as the job's ``clips/<id>.mp4``. False
        when it did not go: that throw stays without a clip. A lost job is
        raised, as for a document."""
        from curling_score.service.worker import Lost

        job = stream.job
        try:
            data = Path(path).read_bytes()
            plan = self.api.artifacts(job["id"], self.worker_id,
                                      [{"name": f"clips/{throw_id}.mp4", "bytes": len(data)}],
                                      [])
            for up in plan["uploads"]:
                self.api.upload(up["url"], up["headers"], data)
        except Lost:
            raise
        except Exception as exc:  # noqa: BLE001 - the throw goes without a clip
            log.warning("practice job %s: clip %s did not upload (%s)", job["id"], throw_id, exc)
            return False
        return True
```

In `video_sessions`, in the practice branch, build the clipper and pass it:

```python
            clipper = None
            if getattr(stream, "send_clip", None) is not None:
                import tempfile

                from curling_score.practice.clips import Clipper
                clipper = Clipper(stream.recorder, stream.send_clip,
                                  tempfile.mkdtemp(prefix="practice-clips-"),
                                  progress=progress)
            return PracticeWatch(
                recording=stream.recorder,
                pipeline=PracticePipeline(weights=weights, skip_longview=skip_longview,
                                          line=models.line_model is not None,
                                          progress=progress),
                models=models, since_s=lookback, wall_t0=began - lookback,
                publish=publish, progress=progress, clipper=clipper)
```

- [ ] **Step 4: Run the tests**

Run: `../../../.venv/bin/python -m pytest tests/test_live_lane.py tests/test_live_manager.py -q -p no:warnings`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/live/manager.py src/curling_score/live/lane.py tests/test_live_lane.py
git commit -m "live: a practice watch's clips go up as clips/<throw>.mp4 artifacts of its job"
```

---

### Task 6: The API takes clips and signs links to them

**Files:**
- Modify: `src/curling_score/service/store.py` (add `clip_key`)
- Modify: `src/curling_score/service/api.py` (constants near `PRACTICE_THROW_ID`;
  `artifacts`; `upload_via_api`; a new clip route after `practice_hide`)
- Test: `tests/test_practice_api.py`

**Interfaces:**
- Consumes: `practice.in_window(doc, session, now)` (`service/practice.py`),
  `store.presign_get(key, expires_s)`.
- Produces: `clip_key(video_id, run_id, throw_id) -> str`;
  `GET /api/practice/sessions/{sid}/throws/{tid}/clip -> {"url": str}`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_practice_api.py`, change the import to
`from curling_score.service.store import MemoryStore, clip_key, practice_key`, and add:

```python
def clip_artifact(w, job, name, size=1000):
    return w["client"].post(f"/api/worker/jobs/{job['id']}/artifacts", headers=WORKER,
                            json={"worker_id": "ryzen", "detcache": [],
                                  "files": [{"name": name, "bytes": size}]})


class TestClipUploads:
    def test_a_practice_job_may_upload_a_throws_clip(self, w):
        start(w)
        job = claim(w)
        r = clip_artifact(w, job, "clips/t_1260.0.mp4")
        assert r.status_code == 200, r.text
        (up,) = r.json()["uploads"]
        run = w["repo"].get_run(job["run_id"])
        assert up["key"] == clip_key(run.video_id, run.id, "t_1260.0")
        assert up["key"] == f"runs/{S3}/{run.id}/clips/t_1260.0.mp4"
        assert up["headers"]["Content-Type"] == "video/mp4"

    @pytest.mark.parametrize("name", ["clips/../practice.json", "clips/t_1.mp4.exe",
                                      "t_1260.0.mp4", "clips/x.mp4", "clips/t_1260.0.mov"])
    def test_anything_else_is_refused(self, w, name):
        start(w)
        assert clip_artifact(w, claim(w), name).status_code == 400

    def test_a_clip_over_20_mb_is_refused(self, w):
        start(w)
        assert clip_artifact(w, claim(w), "clips/t_1260.0.mp4",
                             size=20_000_001).status_code == 400

    def test_the_fallback_upload_takes_a_clip(self, w):
        start(w)
        job = claim(w)
        (up,) = clip_artifact(w, job, "clips/t_1260.0.mp4").json()["uploads"]
        path = up["url"].split("chart.example", 1)[1]
        r = w["client"].put(path, content=b"mp4", headers={**WORKER, "Content-Type": "video/mp4"})
        assert r.status_code == 200, r.text
        assert w["store"].get_bytes(up["key"]) == b"mp4"


def clip_url(w, sid, tid, who=SARAH):
    return w["client"].get(f"/api/practice/sessions/{sid}/throws/{tid}/clip", headers=who)


class TestClipLinks:
    def published(self, w, clip=True):
        sid = start(w).json()["id"]
        job = claim(w)
        wall_t0 = T0.timestamp() - 1200.0
        publish(w, job, doc([{**throw(1140.0), "clip": clip}, {**throw(1260.0), "clip": clip},
                             {**throw(1320.0), "clip": False}], wall_t0))
        w["clock"].advance(300)
        return sid, job

    def test_the_owner_gets_a_link_to_a_throws_clip(self, w):
        sid, job = self.published(w)
        r = clip_url(w, sid, "t_1260.0")
        assert r.status_code == 200, r.text
        run = w["repo"].get_run(job["run_id"])
        assert r.json() == {"url": "memory://" + clip_key(run.video_id, run.id, "t_1260.0")}

    def test_a_throw_without_a_clip_yet_is_a_404(self, w):
        sid, _ = self.published(w)
        assert clip_url(w, sid, "t_1320.0").status_code == 404

    def test_a_throw_outside_the_sessions_window_is_a_404(self, w):
        sid, _ = self.published(w)
        assert clip_url(w, sid, "t_1140.0").status_code == 404

    def test_someone_elses_session_is_a_404(self, w):
        sid, _ = self.published(w)
        assert clip_url(w, sid, "t_1260.0", who=ALEX).status_code == 404

    def test_not_a_throw_is_a_404(self, w):
        sid, _ = self.published(w)
        assert clip_url(w, sid, "..%2Fpractice.json").status_code == 404
```

- [ ] **Step 2: Run them to see them fail**

Run: `../../../.venv/bin/python -m pytest tests/test_practice_api.py -q -p no:warnings -k "Clip"`
Expected: import error for `clip_key`, then failures once it exists

- [ ] **Step 3: Implement**

`src/curling_score/service/store.py`, after `practice_key`:

```python
def clip_key(video_id: str, run_id: str, throw_id: str) -> str:
    """One practice throw's clip (practice/clips.py)."""
    return f"runs/{video_id}/{run_id}/clips/{throw_id}.mp4"
```

`src/curling_score/service/api.py`:
- Import `clip_key` alongside `practice_key`.
- After `PRACTICE_THROW_ID`:

```python
# A practice throw's clip, as the worker names the artifact.
PRACTICE_CLIP = re.compile(r"^clips/(t_[0-9]{1,8}(\.[0-9])?)\.mp4$")
CLIP_MAX_BYTES = 20_000_000
# How long a signed link to a clip lasts; the page asks again after 50 minutes.
CLIP_URL_S = 3600
```

- In `artifacts`, replace the practice branch:

```python
            if job.kind == "practice":
                clip = PRACTICE_CLIP.match(name)
                if clip is not None:
                    if int(f.get("bytes") or 0) > CLIP_MAX_BYTES:
                        raise HTTPException(400, f"{name} is over {CLIP_MAX_BYTES} bytes")
                    key = clip_key(run.video_id, run.id, clip.group(1))
                    uploads.append({"name": name, "key": key, **signed(key, "video/mp4")})
                    continue
                if name != "practice.json":
                    raise HTTPException(400, "a practice job uploads practice.json or "
                                             f"clips/<throw>.mp4, not {name}")
                key = practice_key(run.video_id, run.id)
```

- In `upload_via_api`, accept a clip of a practice job's run:

```python
        allowed = (timeline_key(run.video_id, run.id), meta_key(run.video_id, run.id),
                   practice_key(run.video_id, run.id))
        clips = f"runs/{run.video_id}/{run.id}/"
        is_clip = (job.kind == "practice" and key.startswith(clips)
                   and PRACTICE_CLIP.match(key[len(clips):]) is not None)
        if key not in allowed and not is_clip and not key.startswith(f"detcache/{run.video_id}/"):
            raise HTTPException(400, "that key does not belong to this job")
        data = await request.body()
        if is_clip and len(data) > CLIP_MAX_BYTES:
            raise HTTPException(413, "that clip is too big")
```

- After `practice_hide`, the clip route:

```python
    @app.get("/api/practice/sessions/{session_id}/throws/{throw_id}/clip")
    def practice_clip(session_id: str, throw_id: str,
                      authorization: str | None = Header(default=None)):
        """A short-lived link to one throw's clip, for the session's owner, when
        the throw is in their window and its clip is up."""
        practice_on()
        s = my_session(session_id, require_user(authorization))
        if not PRACTICE_THROW_ID.match(throw_id):
            raise HTTPException(404, "no such throw")
        watch = repo.get_watch(s.watch_id)
        doc = practice_doc(watch)
        if not any(th["id"] == throw_id and th.get("clip")
                   for th, _ in practice.in_window(doc, s, now())):
            raise HTTPException(404, "no clip for that throw")
        run = repo.get_run(watch.run_id)
        return {"url": store.presign_get(clip_key(run.video_id, run.id, throw_id), CLIP_URL_S)}
```

- [ ] **Step 4: Run the tests**

Run: `../../../.venv/bin/python -m pytest tests/test_practice_api.py tests/test_service_api.py -q -p no:warnings --deselect "tests/test_service_api.py::TestCatalogueAndAdmin::test_the_pages_load"`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/service/store.py src/curling_score/service/api.py tests/test_practice_api.py
git commit -m "service: practice jobs upload clips/<throw>.mp4; a session's owner gets an hour's link to one"
```

---

### Task 7: The page plays clips

**Files:**
- Modify: `frontend/core/practice.mjs` (add `clipState`, `linkFresh`, `CLIP_LINK_MS`)
- Modify: `frontend/site/Practice.jsx` (`Clip` component; `Latest` and `Live` pass
  what it needs)
- Modify: `src/curling_score/service/static/site.css`
- Rebuild: `src/curling_score/service/static/site.js`, `frontend/.buildstamp.json`
- Test: `tests/test_practice_js.py`

**Interfaces:**
- Consumes: `GET /api/practice/sessions/{sid}/throws/{tid}/clip` (Task 6); `throw.clip`
  (Task 1).
- Produces: `clipState(t, {live, newest}) -> "play" | "wait" | null`;
  `linkFresh(entry, nowMs) -> boolean`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_practice_js.py`:

```python
def test_where_a_clip_goes():
    assert js('P.clipState({clip: true, t_release_s: 1}, {live: false, newest: false})') == "play"
    assert js('P.clipState({clip: false, t_release_s: 1}, {live: true, newest: true})') == "wait"
    assert js('P.clipState({clip: false, t_release_s: 1}, {live: true, newest: false})') is None
    assert js('P.clipState({clip: false, t_release_s: 1}, {live: false, newest: true})') is None
    assert js('P.clipState({clip: false, t_release_s: null}, {live: true, newest: true})') is None


def test_a_link_is_fetched_again_before_it_expires():
    assert js("P.linkFresh({url: 'u', at: 0}, 49 * 60 * 1000)") is True
    assert js("P.linkFresh({url: 'u', at: 0}, 51 * 60 * 1000)") is False
    assert js("P.linkFresh(undefined, 0)") is False
```

- [ ] **Step 2: Run them to see them fail**

Run: `../../../.venv/bin/python -m pytest tests/test_practice_js.py -q -p no:warnings`
Expected: 2 failed (`P.clipState is not a function`)

- [ ] **Step 3: Implement the core helpers**

Append to `frontend/core/practice.mjs`:

```js
/* What the card shows where a clip goes: "play" when the throw has one,
 * "wait" for the newest throw of a live session (the worker cuts a clip a few
 * seconds after the rock stops), else nothing. A stone nobody released has no
 * clip coming. */
export function clipState(t, { live, newest }) {
  if (t.clip) return "play";
  if (live && newest && t.t_release_s != null) return "wait";
  return null;
}

// A signed link lasts an hour (CLIP_URL_S); one older than this is asked for again.
export const CLIP_LINK_MS = 50 * 60 * 1000;

export function linkFresh(entry, nowMs) {
  return !!entry && nowMs - entry.at < CLIP_LINK_MS;
}
```

- [ ] **Step 4: Implement the player**

In `frontend/site/Practice.jsx`:

Change the core import to:

```js
import { clipState, curlText, lengthText, lineText, linkFresh, restText, shown, summary,
         weightText } from "../core/practice.mjs";
```

Add after `Delivery`:

```jsx
// Signed links to clips, by throw id: { url, at }.
const links = new Map();

async function clipLink(sid, tid, again = false) {
  const hit = links.get(tid);
  if (!again && linkFresh(hit, Date.now())) return hit.url;
  const res = await authedFetch(`/api/practice/sessions/${encodeURIComponent(sid)}/throws/`
                                + `${encodeURIComponent(tid)}/clip`);
  if (!res.ok) return null;
  const { url } = await res.json();
  links.set(tid, { url, at: Date.now() });
  return url;
}

/* The throw's clip: the camera facing the thrower, then the one behind. The
 * newest throw of a live session plays by itself, muted, as phones allow. A
 * link that has expired under an open page is asked for once more. */
function Clip({ sid, t, autoPlay }) {
  const [url, setUrl] = useState(null);
  const [again, setAgain] = useState(false);
  useEffect(() => {
    let on = true;
    setUrl(null);
    setAgain(false);
    clipLink(sid, t.id).then(u => { if (on) setUrl(u); });
    return () => { on = false; };
  }, [sid, t.id]);
  if (!url) return null;
  const retry = () => {
    if (again) return;
    setAgain(true);
    clipLink(sid, t.id, true).then(setUrl);
  };
  return (
    <video key={url} className="practice-clip" src={url} controls playsInline
           muted={autoPlay} autoPlay={autoPlay} preload="metadata" onError={retry} />
  );
}
```

Change `Latest` to take the session and where the throw stands, and show the clip first:

```jsx
function Latest({ t, n, sid, live, newest }) {
  const clip = clipState(t, { live, newest });
  return (
    <Card>
      <p className="muted" style={{ margin: 0 }}>
        Throw {n}{curlText(t.curl) ? ` · ${curlText(t.curl)}` : ""} · {ago(t.released_at)}
      </p>
      {clip === "play" && <Clip sid={sid} t={t} autoPlay={live && newest} />}
      {clip === "wait" && <p className="muted">Clip on its way…</p>}
      <Delivery t={t} n={n} />
      ... (the rest of the card exactly as it is)
```

In `Live`, pass them: replace `{card && <Latest t={card} n={cardN} />}` with

```jsx
      {card && <Latest t={card} n={cardN} sid={id} live={live} newest={picked == null} />}
```

`site.css`, after the `#practice a.plain` rule:

```css
#practice video.practice-clip { display:block; width:100%; max-height:60vh; margin:10px 0;
                                background:#000; border-radius:8px; }
```

- [ ] **Step 5: Build and run the tests**

Run: `cd frontend && nice -n 10 npm run build && cd ..`
Expected: eslint clean; `built: ... site.js`

Run: `../../../.venv/bin/python -m pytest tests/test_practice_js.py tests/test_frontend_build.py -q -p no:warnings`
Expected: all pass

- [ ] **Step 6: Check it in a headless phone**

Reuse the scratch harness in
`/tmp/claude-1000/-home-tcuser-src-curling-score/54b5597c-d8cc-4d7f-9591-22dac19d6f22/scratchpad/practice-list/`
(`serve.py`, `check.mjs`; see memory `headless-phone-check`). In a copy of `serve.py`:
- set `"clip": True` on every throw of the seeded document;
- replace `store.presign_get` with a function returning `http://127.0.0.1:8791/clipfile`;
- add `@app.get("/clipfile")`, returning a `FileResponse` of any small H.264 MP4. One is
  the output of Task 3's test, or cut one with
  `ffmpeg -f lavfi -i testsrc=s=600x810:r=30:d=4 -c:v libx264 -pix_fmt yuv420p -movflags +faststart clip.mp4`.

In a copy of `check.mjs`, open `/practice/session/ps_now` (live) and check:
- `document.querySelector("video.practice-clip")` exists;
- `.muted && .autoplay` are true on the newest throw;
- after tapping an older row, the video has `autoplay` false;
- `/practice/session/ps_morning` (ended) has no autoplay;
- no console errors and no sideways scroll at 390×844.

Take a screenshot of each and look at them. Stop the server and Chrome by PID
afterwards (memory `killing-stray-processes`).

- [ ] **Step 7: Commit**

```bash
git add frontend/core/practice.mjs frontend/site/Practice.jsx frontend/.buildstamp.json src/curling_score/service/static/site.js src/curling_score/service/static/site.css tests/test_practice_js.py
git commit -m "site: a practice throw's clip in its card; the newest plays by itself while live"
```

---

### Task 8: Clips on a replay, and the import script's backfill

**Files:**
- Modify: `src/curling_score/cli.py` (`practice-replay --clips`; `_practice_replay`)
- Modify: `scripts/practice/import_session.py` (`--replace-run`, `--clips`)
- Test: `tests/test_cli_practice_replay.py`

**Interfaces:**
- Consumes: `Clipper` (Task 4); `clip_key` (Task 6).
- Produces: `curling-score practice-replay ... --clips`, writing `<out>/clips/<id>.mp4`;
  `import_session.py --replace-run RUN [--clips DIR]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli_practice_replay.py`:

```python
def test_with_clips_a_replay_keeps_each_throws_clip_in_its_out_directory(tmp_path, monkeypatch):
    from curling_score import analyze
    from curling_score.live import replay
    from curling_score.practice import harness
    from curling_score.practice.clips import Clipper

    seen = {}

    class Rec:
        def __init__(self, source, path, speed=1.0, *, start_s=0.0, end_s=None, burst_s=0.0):
            self.path, self.t0_s = path, 0.0

        def start(self):
            return self

        def stop(self):
            pass

    class Heads:
        def start(self, rec):
            return self

        def stop(self):
            pass

        def reached(self, t):
            return 0.0

    def run(watch, **kw):
        seen["clipper"] = watch.clipper
        watch.publish({"practice": 1, "status": "ended", "t0_s": 0.0, "throws": []})
        watch.done = True

    monkeypatch.setattr(replay, "ReplayRecording", Rec)
    monkeypatch.setattr(harness, "HeadClock", Heads)
    monkeypatch.setattr(harness, "run", run)
    monkeypatch.setattr(analyze, "load_models", lambda *a, **k: (None, None, None))
    out = tmp_path / "practice"
    assert cli.main(["practice-replay", str(tmp_path / "abcdefghijk.mp4"), "--from", "3000",
                     "--to", "3100", "--out", str(out), "--weights", "none", "--clips"]) == 0
    c = seen["clipper"]
    assert isinstance(c, Clipper) and c.keep and c.workdir == out / "clips"
```

- [ ] **Step 2: Run it to see it fail**

Run: `../../../.venv/bin/python -m pytest tests/test_cli_practice_replay.py -q -p no:warnings`
Expected: FAIL, `error: unrecognized arguments: --clips` (SystemExit 2)

- [ ] **Step 3: Implement the replay option**

In `cli.py`, in the `practice-replay` parser, add:

```python
    p.add_argument("--clips", action="store_true",
                   help="cut each throw's clip into <out>/clips, as a worker would upload it")
```

In `_practice_replay`, before `watch = PracticeWatch(`:

```python
    clipper = None
    if args.clips:
        from curling_score.practice.clips import Clipper
        clipper = Clipper(rec, lambda tid, path: True, out / "clips", keep=True, progress=print)
```

and pass `clipper=clipper` to `PracticeWatch(...)`.

- [ ] **Step 4: Run the tests**

Run: `../../../.venv/bin/python -m pytest tests/test_cli_practice_replay.py -q -p no:warnings`
Expected: all pass

- [ ] **Step 5: The import script's backfill**

In `scripts/practice/import_session.py`:
- Make `--from`, `--to` and `--user` optional (`required=False`). After `parse_args`,
  require them unless `--replace-run` is given:
  `if not args.replace_run and None in (args.from_s, args.to_s, args.user): ap.error("--from, --to and --user are needed for a new session")`.
- Add the two options:

```python
    ap.add_argument("--replace-run", help="put the document (and clips) in place of this "
                                          "practice run's, keeping its run, watch and sessions")
    ap.add_argument("--clips", help="a directory of <throw id>.mp4 clips to upload with it")
```

- Add the helper, which uploads the clips the document says it has and clears the flag
  where a file is missing:

```python
def upload_clips(doc, clips_dir, video_id, run_id) -> int:
    """Upload the clips the document says it has; a throw whose file is
    missing is marked as having none. How many went up."""
    files = []
    for th in doc["throws"]:
        f = os.path.join(clips_dir, f"{th['id']}.mp4") if clips_dir else None
        if th.get("clip") and f and os.path.isfile(f):
            files.append(f)
        else:
            th["clip"] = False
    if files:
        subprocess.run(["gcloud", "storage", "cp", "--content-type=video/mp4", *files,
                        f"gs://{BUCKET}/runs/{video_id}/{run_id}/clips/"], check=True)
    return len(files)
```

- In `main`, after `doc["wall_t0"] = ...`, handle `--replace-run` and return before a new
  run is made:

```python
    if args.replace_run:
        run = repo.get_run(args.replace_run)
        watch = next(w for w in repo.watches_for_sheet(run.sheet) if w.run_id == run.id)
        ids = {th["id"] for th in doc["throws"]}
        for s in repo.sessions_for_watch(watch.id):
            gone = [h for h in s.hidden if h not in ids]
            print(f"session {s.id}: {len(s.hidden)} hidden, {len(gone)} no longer match")
        print(f"run {run.id}: {len(doc['throws'])} throw(s), "
              f"{sum(1 for th in doc['throws'] if th.get('clip'))} with a clip")
        if args.dry_run:
            return 0
        sent = upload_clips(doc, args.clips, args.video, run.id)
        path = os.path.join(os.path.dirname(os.path.abspath(args.doc)), "practice.import.json")
        with open(path, "w") as f:
            json.dump(doc, f)
        subprocess.run(["gcloud", "storage", "cp", "--content-type=application/json", path,
                        f"gs://{BUCKET}/{practice_key(args.video, run.id)}"], check=True)
        repo.update_watch(watch.id, revised_at=now, throws=len(doc["throws"]))
        print(f"{sent} clip(s) uploaded; practice.json replaced")
        return 0
```

- For a new import, call `upload_clips(doc, args.clips, args.video, run.id)` just before
  the existing `practice.json` upload, so `clip` flags match what is in the bucket.

`repo.update_watch(watch_id, **fields)` is on both repos (`firestore_repo.py:535`,
`repo.py:587`); `practice_publish` uses it the same way.

- [ ] **Step 6: Commit**

```bash
git add src/curling_score/cli.py tests/test_cli_practice_replay.py scripts/practice/import_session.py
git commit -m "practice: practice-replay --clips; import_session.py backfills a run's document and clips"
```

---

### Task 9: Real footage, today's session, and deploying

**Files:** none new; this task runs things and records results.

- [ ] **Step 1: Run the practice tests together**

Run: `../../../.venv/bin/python -m pytest tests/test_practice_*.py tests/test_cli_practice_replay.py tests/test_live_lane.py tests/test_live_manager.py tests/test_service_api.py -q -p no:warnings --deselect "tests/test_service_api.py::TestCatalogueAndAdmin::test_the_pages_load"`
Expected: all pass

- [ ] **Step 2: Replay today's session with clips, on the worker box**

```bash
rsync -a --delete --exclude .git --exclude .venv --exclude node_modules --exclude 'out*' \
  --exclude firebase_api_key.json ./ curling@100.85.240.114:/data/wdd/scratch/practice-reprocess/code/
ssh curling@100.85.240.114 'docker run -d --name reprocess-clips --gpus all \
  -e PYTHONPATH=/code/src -v /data/wdd/scratch/practice-reprocess:/work \
  -v /data/wdd/scratch/practice-reprocess/code:/code --entrypoint nice curling-worker:local \
  -n 10 python -m curling_score.cli practice-replay /work/qJhDHMMtHsE.full.mp4 \
  --from 959 --to 3290 --lookback 959 --speed 8 --out /work/out-clips --clips'
```

Wait for it to exit (`docker inspect -f '{{.State.Running}}' reprocess-clips`), then
`docker logs reprocess-clips 2>&1 | grep "^clip "` lists each clip's length, cut time and
size. Expected: a clip for every shown throw with a release, each under 3 MB, each cut in
under 15 s.

- [ ] **Step 3: Look at the clips**

Copy 4 clips here: 2 to the top house (from 2359 s on) and 2 to the bottom house, one of
them `t_1366.1` or `t_2482.1` (side-view releases). For each, take a contact sheet:
`ffmpeg -i clip.mp4 -vf "fps=1,scale=150:-1,tile=8x3" -frames:v 1 sheet.jpg`. Read them
and check:
- the thrower's push and release are in the first piece;
- the cut comes as the rock leaves the bottom of the frame;
- the second piece picks the rock up near the far hog line and holds to rest.

Write down sizes, cut times and what was seen. If the cut is early or late by more than
about 1 s, revisit `exit_time` before going on.

- [ ] **Step 4: Deploy the API, then the workers**

The API first, because it must accept clip artifacts before any worker sends one. From a
clean worktree of the merged commit, copy `firebase_api_key.json` in, then:

```bash
PATH=$HOME/google-cloud-sdk/bin:$PATH PROJECT_ID=curling-stats-508323 REGION=us-west1 \
  PUBLIC_BASE_URL=https://curling.dimmit.net nice -n 10 ./deploy/deploy-api.sh
```

Then the workers. First check no live or practice job is running:
`docker compose -f docker-compose.worker.yml logs --since 5m | grep -E "practice job|live job"`.
Then rsync to `curling@100.85.240.114:/data/wdd/curling_score/`, with the excludes in
memory `deploying-curling-chart` (keep `deploy/worker.env`), and run
`docker compose -f docker-compose.worker.yml up -d --build`. Confirm each worker has
`practice/clips.py`.

- [ ] **Step 5: Backfill today's session**

From this worktree, with `AT=$(gcloud auth print-access-token)`:

```bash
PYTHONPATH=src ../../../.venv/bin/python scripts/practice/import_session.py \
  <copied out-clips/practice.json> --video qJhDHMMtHsE --sheet 3 --stream-start 1791648022 \
  --replace-run r_1e6nO93eHjTscbqBd --clips <copied out-clips/clips> --dry-run
```

Check the dry run's hidden-id report, then run it without `--dry-run`. This writes to
production, so confirm with the user that the backfill is still wanted. Verify by reading
the session back through `practice.session_view` (as on 2026-10-10). Every throw with
`clip: true` should have its object at `runs/qJhDHMMtHsE/r_1e6nO93eHjTscbqBd/clips/`
(`gcloud storage ls`).

- [ ] **Step 6: Merge and record**

Fast-forward `main` to the branch (`git -C /home/tcuser/src/curling_score merge --ff-only practice`).
Update the memory note `practice-sessions.md` with:
- the commits;
- the deploy;
- the measured clip sizes and cut times;
- that the first live session with clips is still to be watched.
