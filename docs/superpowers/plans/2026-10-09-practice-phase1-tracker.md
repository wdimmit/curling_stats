# Practice Sessions, Phase 1: Per-Throw Tracker and Replay Harness

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Find every delivery on a sheet while the recording is still growing, measure each one (release, hog-to-hog split, target broom, line, rest) within seconds of it coming to rest, and measure recall and latency offline against the per-end pipeline on cached videos.

**Architecture:** A new package, `src/curling_score/practice/`. A `PracticeWatch` steps through a growing recording, like `live.session.LiveSession`:
- it calibrates from the lookback footage;
- it detects both overhead panels in one decode into rolling buffers;
- it runs the existing delivery and release finders over those buffers, reporting each delivery once;
- it measures each throw by running the existing per-rock passes over a one-shot list.

Video work goes through a pipeline object, so the watch is tested with fakes. `curling-score practice-replay` plays a cached video as a stream that has been running for hours (a burst of lookback, then real time) and logs each throw's latency. `scripts/practice/compare_throws.py` scores the throws against a timeline from `curling-score analyze`.

**Tech Stack:** Python 3, pytest, PyAV and ffmpeg (already used by `ingest/frames.py` and `live/replay.py`), and the existing YOLO and side models. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-09-practice-sessions-design.md`. This plan is its phase 1, sections 2 and 5. Phases 2-5 get their own plans after the phase 1 numbers have gone to the user.

## Global Constraints

- **Phase 1 is offline.**
  - Do not touch `service/`, `frontend/`, `deploy/`, `live/lane.py`, `live/manager.py` or `live/recorder.py`.
  - `live/replay.py` only gains a window, a burst and `t0_s`.
- **No downloads.** Use only the cached videos `~/.cache/curling_score/videos/{VXU9xwmugRg,hOKZoeJNTpM}.mp4` and `~/.cache/curling_replay/videos/AEqLTgM25Tc.mp4`. If any command starts fetching from YouTube, stop it.
- **Pipeline stages are reused unchanged.** No constant in `detect/`, `game/` or `geometry/` changes.
- **Detection:** the default weights (`weights.default_path()`), imgsz 448, `DETECT_FPS` 10 on both panels.
- **From the spec:**
  - `LOOKBACK_S` is 1200 s.
  - The house is read over 3 s (`HOUSE_WINDOW_S`).
  - Each panel's buffer holds 90 s (`KEEP_S`).
  - Reading stops `HEAD_MARGIN_S` = 2 s behind the head.
  - Only throws released at or after Start (`since_s`) count.
- **Two deliberate refinements of the spec, both documented in code:**
  - `NO_ARRIVAL_S` is **60 s**, not 30. Release-to-entry can take 30 s (`release.MAX_LAG_S`), and the arrival is confirmed up to ~25 s after that. Giving up at 30 s would call slow draws hogged.
  - The long camera's release offset, which `sidereleases.time_side_releases` takes as a median over an end's overhead releases, is **0** for a practice throw.
- **Commits:** another session shares the main checkout. Work in a worktree on branch `practice` (superpowers:using-git-worktrees), and stage only the files a task names.
- **Tests:** run the affected files only; the full suite OOMs on this box (exit 137 is the OOM killer). Every command below names its files. Don't add `-q`, because pyproject already sets it.
- **Style:** match the repo. Module docstrings say why. Comments are full sentences. Constants sit at the top of the module with a comment on where the number came from. Test names are sentences (`test_a_..._is_...`).
- **Commit messages:** a short lowercase prefix (`practice:`, `replay:`, `cli:`), then the attribution line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **A stone nothing released**, such as one pushed down the sheet by hand. It should never pass silently as an ordinary throw. Phase 1 reports it with `release_source: null`, and the compare script counts these separately. *Test:* Task 5, `test_an_arrival_nothing_released_is_reported_without_a_release`.
2. **A slow draw confirmed long after its release.** Its release must not be given up as "hogged?" before the arrival claims it. *Tests:* Task 3, `test_a_release_waits_a_minute_for_its_arrival`; Task 5 claims arrivals before giving up on releases.
3. **A rock thrown just before Start, arriving after it.** It must not appear in the session. *Test:* Task 5, `test_a_throw_released_before_start_is_left_out`.
4. **Two of the same colour thrown close together** (partners alternating). Each arrival takes the release nearest a typical flight, not simply the earliest. *Test:* Task 3, `test_an_arrival_takes_the_release_nearest_a_typical_flight`.
5. **Calibration that never completes, or a recording that ends during it.** The watch must end as `failed`, not spin. *Test:* Task 5, `test_it_gives_up_after_its_calibration_tries`.

---

### Task 1: The throw record

**Files:**
- Create: `src/curling_score/practice/__init__.py`
- Create: `src/curling_score/practice/throws.py`
- Test: `tests/test_practice_throws.py`

**Interfaces:**
- Consumes: `game.shots.Shot`, `game.split.long_split`, `game.hogtime.crossing`/`speed_at_hog`, `game.fartime.crossing`, `geometry.constants`.
- Produces:
  - `throws.ring_of(x_m: float, y_m: float) -> str`, one of `"button" | "4" | "8" | "12" | "short" | "long" | "out"`.
  - `throws.throw_record(shot, *, house: str, t0_s: float = 0.0) -> dict`, with keys `id, t_release_s, t_rest_s, house, color, arrived, release_source, split_s, release_speed, curl, broom, line, rest, track`. All times are on the stream's clock (`t0_s` + recording time).

- [ ] **Step 1: Write the failing tests**

`tests/test_practice_throws.py`:

```python
"""A practice throw as the session page reads it (`practice.throws`)."""

import pytest

from curling_score.detect.delivery import Delivery
from curling_score.detect.release import Release
from curling_score.detect.rocks import Detection
from curling_score.game import split
from curling_score.game.broomtime import TargetBroom
from curling_score.game.linetime import Line
from curling_score.game.shots import Shot
from curling_score.practice import throws


def stone(color, x, y):
    return Detection(color=color, x_m=x, y_m=y, x_px=0.0, y_px=0.0,
                     area_px=140.0, confidence=0.9)


def arrival(color="red", t_enter=40.0, x=0.1, y=-0.4, came_to_rest=True):
    # 95 samples 0.1 s apart, coming down the sheet from 3.8 m.
    track = tuple((t_enter + i * 0.1, x, 3.8 - i * 0.05) for i in range(95))
    return Delivery(color=color, t_enter=t_enter, t_rest=t_enter + 9.4, entry_y_m=3.8,
                    rest_x_m=x, rest_y_m=y, travel_m=4.2, came_to_rest=came_to_rest,
                    track=track)


def line(**kw):
    base = dict(start=(0.0, -3.6), at_hog_x=0.1, at_hog_offset=0.08, at_broom_x=-0.57,
                miss=-0.12, curl="left", side="narrow", confirmed=True, hog_path=(),
                path=(), fit_n=12, fit_rms=0.01)
    return Line(**{**base, **kw})


class TestRingOf:
    @pytest.mark.parametrize("x, y, ring", [
        (0.0, 0.0, "button"), (0.0, 0.25, "button"), (0.0, 0.6, "4"), (0.5, 0.9, "8"),
        (0.0, 1.9, "12"), (0.0, 2.5, "short"), (1.5, -1.5, "long"),
        (0.0, -2.0, "out"), (2.3, 0.0, "out")])
    def test_the_smallest_ring_a_stone_touches(self, x, y, ring):
        assert throws.ring_of(x, y) == ring


class TestThrowRecord:
    def test_a_measured_throw_is_on_the_streams_clock(self):
        dv = arrival()
        shot = Shot(number=1, color="red",
                    stones=[stone("yellow", 0.8, 0.2), stone("red", 0.12, -0.41)],
                    t_rest_s=dv.t_rest, delivery=dv,
                    release=Release("red", 22.0, 1.0, 2.4), delivered_stone_index=1)
        shot.t_hog_s = 24.0
        shot.far_crossing = split.FarCrossing(t=37.8)
        shot.target_broom = TargetBroom(x_m=-0.45, y_m=0.0, seen=0.9, confidence=0.8)
        shot.line = line()

        got = throws.throw_record(shot, house="top", t0_s=1000.0)

        assert got["id"] == "t_1022.0"
        assert got["t_release_s"] == 1022.0 and got["t_rest_s"] == 1049.4
        assert (got["house"], got["color"], got["arrived"]) == ("top", "red", True)
        assert got["release_source"] == "overhead"
        assert got["release_speed"] == 2.4
        assert got["split_s"] == pytest.approx(13.8)
        assert got["curl"] == "left"
        assert got["broom"] == {"x": -0.45, "y": 0.0}
        assert got["line"] == {"miss_m": -0.12, "side": "narrow", "hog_offset_m": 0.08,
                               "confirmed": True}
        # Where it rests is the house read's stone, not the track's last point.
        assert got["rest"] == {"x": 0.12, "y": -0.41, "to_tee_m": 0.427, "ring": "4"}

    def test_the_track_is_thinned_to_five_points_a_second(self):
        dv = arrival()
        shot = Shot(number=1, color="red", stones=[], t_rest_s=dv.t_rest, delivery=dv)
        track = throws.throw_record(shot, house="top")["track"]
        assert track[0] == [40.0, 0.1, 3.8]
        assert track[-1][0] == pytest.approx(49.4)
        assert all(b[0] - a[0] >= 0.19 for a, b in zip(track, track[1:-1]))
        assert len(track) < 60

    def test_a_throw_that_never_arrived(self):
        shot = Shot(number=1, color="yellow", stones=[], t_rest_s=float("nan"),
                    release=Release("yellow", 30.0, 1.0, 2.0), state_known=False)
        got = throws.throw_record(shot, house="top")
        assert got["id"] == "t_30.0"
        assert got["arrived"] is False and got["t_rest_s"] is None
        assert got["rest"] is None and got["track"] == [] and got["split_s"] is None

    def test_no_release_no_broom_and_no_line_are_nulls(self):
        dv = arrival()
        shot = Shot(number=1, color="red", stones=[], t_rest_s=dv.t_rest, delivery=dv)
        got = throws.throw_record(shot, house="bottom")
        assert got["id"] == "t_40.0"              # dated by its arrival
        assert got["t_release_s"] is None and got["release_source"] is None
        assert got["broom"] is None and got["line"] is None and got["curl"] is None
        # No house read: the rest comes from the delivery itself.
        assert got["rest"]["x"] == 0.1 and got["rest"]["ring"] == "4"

    def test_a_stone_that_ran_out_of_play_is_out(self):
        dv = arrival(came_to_rest=False, y=-2.3)
        shot = Shot(number=1, color="red", stones=[], t_rest_s=dv.t_rest, delivery=dv)
        rest = throws.throw_record(shot, house="top")["rest"]
        assert rest["ring"] == "out" and rest["to_tee_m"] is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_practice_throws.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'curling_score.practice'`

- [ ] **Step 3: Write the implementation**

`src/curling_score/practice/__init__.py`:

```python
"""Practice sessions: every delivery on a sheet, reported as it comes to rest.

See docs/superpowers/specs/2026-10-09-practice-sessions-design.md.
"""
```

`src/curling_score/practice/throws.py`:

```python
"""One practice throw, as the session page reads it.

A practice session has no ends, no teams and no rock numbers. Each delivery is
judged on its own: when it was released, its weight between the hog lines,
where it passed the skip's broom and where it stopped. This turns the
`game.shots.Shot` the tracker built for one delivery into that record, with
every time on the stream's clock.
"""

import math

from curling_score.game import fartime, hogtime, split
from curling_score.geometry import constants as C

# The rings a resting stone is said to be in: the smallest one it touches.
RINGS = (("button", C.R_BUTTON_M), ("4", C.R_4FT_M), ("8", C.R_8FT_M),
         ("12", C.R_12FT_M))
# A flight is thinned to about this many points a second: enough to draw it.
TRACK_HZ = 5.0


def ring_of(x_m: float, y_m: float) -> str:
    """Where a resting stone sits, in a curler's words: the smallest ring it
    touches, else short (in front of the house) or long (behind it), or out
    when it is past the back line or the sides."""
    if y_m < C.THROUGH_BACK_Y_M or abs(x_m) > C.SIDELINE_ABS_X_M:
        return "out"
    d = math.hypot(x_m, y_m)
    for name, radius in RINGS:
        if d - C.STONE_RADIUS_M <= radius:
            return name
    return "short" if y_m > 0 else "long"


def _r(v, n=3):
    return None if v is None else round(float(v), n)


def _thin(track, t0_s):
    out, last = [], None
    for i, (t, x, y) in enumerate(track):
        if last is None or t - last >= 1.0 / TRACK_HZ - 1e-6 or i == len(track) - 1:
            out.append([_r(t0_s + t, 2), _r(x), _r(y)])
            last = t
    return out


def _rest(shot):
    dv = shot.delivery
    if dv is None:
        return None
    if not dv.came_to_rest:
        return {"x": _r(dv.rest_x_m), "y": _r(dv.rest_y_m), "to_tee_m": None, "ring": "out"}
    # The house read averages the settled stone over a few seconds, which is
    # steadier than the last point of the flight.
    idx = shot.delivered_stone_index
    if idx is not None and idx < len(shot.stones):
        x, y = shot.stones[idx].x_m, shot.stones[idx].y_m
    else:
        x, y = dv.rest_x_m, dv.rest_y_m
    return {"x": _r(x), "y": _r(y), "to_tee_m": _r(math.hypot(x, y)), "ring": ring_of(x, y)}


def throw_record(shot, *, house: str, t0_s: float = 0.0) -> dict:
    """``shot`` as the page reads it. ``house`` is the end it was thrown to;
    ``t0_s`` is where the recording begins on the stream's clock."""
    dv, rel = shot.delivery, shot.release
    line, broom = shot.line, shot.target_broom
    sp = split.long_split(dv, t_hog=hogtime.crossing(shot),
                          v_hog=hogtime.speed_at_hog(shot), far=fartime.crossing(shot))
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
        "curl": None if line is None else line.curl,
        "broom": None if broom is None else {"x": _r(broom.x_m, 4), "y": _r(broom.y_m, 4)},
        "line": None if line is None else {
            "miss_m": _r(line.miss, 4), "side": line.side,
            "hog_offset_m": _r(line.at_hog_offset, 4), "confirmed": line.confirmed},
        "rest": _rest(shot),
        "track": [] if dv is None else _thin(dv.track, t0_s),
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_practice_throws.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/practice/__init__.py src/curling_score/practice/throws.py tests/test_practice_throws.py
git commit -m "practice: a throw as the session page reads it

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Rolling buffer and the arrival finder

**Files:**
- Create: `src/curling_score/practice/finder.py`
- Test: `tests/test_practice_finder.py`

**Interfaces:**
- Consumes: `detect.delivery.find_deliveries`, `delivery.REQUIRED_LOOKBACK_S` (36 s), and a panel setup with `.view_x_limit_m` and `.view_y_min_m` (`game.profile.PanelSetup`).
- Produces:
  - `finder.KEEP_S = 90.0` and `finder.SAME_ENTRY_S = 3.0`.
  - `finder.Buffer(keep_s=KEEP_S)`, with `.frames: list[(t, [Detection])]`, `.start_s`, `.head_s` and `.extend(frames) -> None`.
  - `finder.ArrivalFinder(setup)`, with `.new(buffer) -> list[Delivery]`: the deliveries not reported before, in order of arrival.

- [ ] **Step 1: Write the failing tests**

`tests/test_practice_finder.py`:

```python
"""Deliveries found while the recording is still growing, each reported once."""

from types import SimpleNamespace

from curling_score.practice.finder import ArrivalFinder, Buffer
from tests.test_delivery import det, thrown

SETUP = SimpleNamespace(view_x_limit_m=None, view_y_min_m=None)


def sheet(*traces, until_s, fps=5.0):
    """Every frame from 0 to ``until_s``, each holding what ``traces`` put there."""
    frames = {round(i / fps, 3): [] for i in range(int(until_s * fps) + 1)}
    for tr in traces:
        for t, d in tr:
            frames.setdefault(round(t, 3), []).append(d)
    return sorted(frames.items())


def departing(color, t0, fps=5.0, speed=2.0):
    """A stone leaving this house for the other end, as at a release."""
    out, t, y = [], t0, -3.6
    while y < 6.5:
        out.append((t, det(color, 0.0, y)))
        y += speed / fps
        t += 1.0 / fps
    return out


def feed(frames, step_s=1.0):
    """Hand ``frames`` over a second at a time, as the watch does, and collect
    everything the finder reports."""
    buf, finder, got = Buffer(), ArrivalFinder(SETUP), []
    t = frames[0][0]
    while t <= frames[-1][0] + step_s:
        buf.extend([f for f in frames if f[0] <= t])
        got.extend(finder.new(buf))
        t += step_s
    return got


class TestBuffer:
    def test_it_keeps_only_newer_frames_and_forgets_old_ones(self):
        buf = Buffer(keep_s=10.0)
        buf.extend([(0.0, []), (1.0, [])])
        buf.extend([(1.0, ["again"]), (2.0, [])])
        assert [t for t, _ in buf.frames] == [0.0, 1.0, 2.0]
        buf.extend([(t / 1.0, []) for t in range(3, 15)])
        assert buf.start_s == 4.0 and buf.head_s == 14.0


class TestArrivalFinder:
    def test_a_delivery_is_reported_once_as_the_window_slides(self):
        got = feed(sheet(thrown("red", t0=40.0), until_s=90.0))
        assert len(got) == 1
        assert got[0].color == "red" and abs(got[0].rest_y_m - (-0.9)) < 0.11

    def test_two_deliveries_are_reported_in_order(self):
        got = feed(sheet(thrown("red", t0=40.0, x=0.2),
                         thrown("yellow", t0=80.0, x=-0.5, y1=0.5), until_s=130.0))
        assert [d.color for d in got] == ["red", "yellow"]

    def test_one_with_too_little_window_behind_it_is_not_judged(self):
        # The buffer starts at 0, so a stone entering at 10 s has no look-back.
        assert feed(sheet(thrown("red", t0=10.0), until_s=60.0)) == []

    def test_a_stone_leaving_for_the_other_end_is_not_a_delivery(self):
        assert feed(sheet(departing("red", t0=40.0), until_s=60.0)) == []

    def test_the_same_colour_entering_within_seconds_is_the_same_delivery(self):
        finder = ArrivalFinder(SETUP)
        a = SimpleNamespace(color="red", t_enter=40.0)
        b = SimpleNamespace(color="red", t_enter=41.5)
        c = SimpleNamespace(color="yellow", t_enter=41.5)
        finder.reported.append(a)
        assert finder._reported(b) and not finder._reported(c)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_practice_finder.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'curling_score.practice.finder'`

- [ ] **Step 3: Write the implementation**

`src/curling_score/practice/finder.py`:

```python
"""Deliveries found while the recording is still growing, each reported once.

The recorded pipeline finds an end's deliveries in one pass over the whole end
(`analyze.build_one_end`). A practice card is due seconds after the stone
stops, so here the same finder, `detect.delivery.find_deliveries`, runs again
and again over a rolling window of one panel's detections, and a delivery is
reported the first time it is confirmed.

The finder's evidence reaches back `delivery.REQUIRED_LOOKBACK_S` before a
stone comes into view (was its resting place empty before?), so only the
deliveries with that much window behind them are judged. An older one was
judged on an earlier, fuller window; a newer one waits for its rest to be
confirmed.
"""

import bisect

from curling_score.detect import delivery

# The detections each panel keeps: a delivery's look-back (36 s), the longest
# flight from entering the panel to a confirmed rest (~25 s), and slack.
KEEP_S = 90.0
# The same delivery found again in a later window: the same colour, entering
# within this. Two stones of one colour cannot come down a sheet this close.
SAME_ENTRY_S = 3.0


class Buffer:
    """One panel's detections over the last ``keep_s`` seconds, in time order."""

    def __init__(self, keep_s: float = KEEP_S):
        self.keep_s = keep_s
        self.frames: list = []

    @property
    def start_s(self):
        return self.frames[0][0] if self.frames else None

    @property
    def head_s(self):
        return self.frames[-1][0] if self.frames else None

    def extend(self, frames) -> None:
        """Add the frames newer than any held, and forget what has aged out."""
        head = self.head_s
        self.frames.extend((t, d) for t, d in frames if head is None or t > head + 1e-6)
        if self.frames:
            floor = self.frames[-1][0] - self.keep_s
            cut = bisect.bisect_left([t for t, _ in self.frames], floor)
            del self.frames[:cut]


class ArrivalFinder:
    """One panel as the house thrown to: its deliveries, each reported once."""

    def __init__(self, setup):
        self.setup = setup
        self.reported: list = []

    def new(self, buffer: Buffer) -> list:
        """The deliveries confirmed in ``buffer`` and not reported before, in
        order of arrival."""
        if not buffer.frames:
            return []
        judged_from = buffer.start_s + delivery.REQUIRED_LOOKBACK_S
        found = delivery.find_deliveries(
            buffer.frames, view_x_limit_m=self.setup.view_x_limit_m,
            view_y_min_m=self.setup.view_y_min_m)
        out = []
        for d in sorted(found, key=lambda d: d.t_enter):
            if d.t_enter < judged_from or self._reported(d):
                continue
            self.reported.append(d)
            out.append(d)
        return out

    def _reported(self, d) -> bool:
        return any(r.color == d.color and abs(r.t_enter - d.t_enter) <= SAME_ENTRY_S
                   for r in self.reported)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_practice_finder.py -v`
Expected: PASS (6 tests). If `test_two_deliveries_are_reported_in_order` finds nothing for yellow, compare its frames with `tests/test_delivery.py::TestFindDeliveries::test_finds_both_colours_in_order`. Change the synthetic throw, not `delivery.py`.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/practice/finder.py tests/test_practice_finder.py
git commit -m "practice: find deliveries over a rolling window, each reported once

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The release book

**Files:**
- Create: `src/curling_score/practice/releases.py`
- Test: `tests/test_practice_releases.py`

**Interfaces:**
- Consumes:
  - `detect.release.find_releases(frames, view_y_min_m, view_x_limit_m)`, plus `release.RELEASE_FPS` (5), `MIN_LAG_S` (6) and `MAX_LAG_S` (30);
  - `game.sidereleases.TYPICAL_LAG_S` (17);
  - an arrival with `.color` and `.t_enter`.
- Produces:
  - `releases.on_grid(frames, fps) -> list`;
  - `releases.NO_ARRIVAL_S = 60.0`;
  - `releases.ReleaseBook(setup)`, with:
    - `.update(frames) -> None`;
    - `.claim(arrival) -> Release | None`;
    - `.unarrived(now_s) -> list[Release]`;
    - `.releases: list[Release]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_practice_releases.py`:

```python
"""Releases seen leaving the thrower's house, each accounted for once."""

from types import SimpleNamespace

from curling_score.detect.release import Release
from curling_score.practice import releases as R

SETUP = SimpleNamespace(view_y_min_m=-4.0, view_x_limit_m=2.2)


def book(monkeypatch, *found):
    """A book whose panel showed ``found``; each update hands over the next batch."""
    batches = [list(b) for b in found]
    monkeypatch.setattr(R.release, "find_releases",
                        lambda frames, y_min, x_limit=None: batches.pop(0) if batches else [])
    b = R.ReleaseBook(SETUP)
    for _ in found:
        b.update([(0.0, [])])
    return b


def rel(color, t):
    return Release(color, t, 1.0, 2.0)


def arrival(color, t_enter):
    return SimpleNamespace(color=color, t_enter=t_enter)


def test_frames_are_thinned_to_the_rate_the_finder_was_tuned_at():
    frames = [(i / 10, []) for i in range(20)]
    assert [t for t, _ in R.on_grid(frames, 5.0)] == [i / 5 for i in range(10)]


def test_a_release_found_again_is_kept_once(monkeypatch):
    b = book(monkeypatch, [rel("red", 10.0)], [rel("red", 10.4), rel("yellow", 10.2)])
    assert [(r.color, r.t) for r in b.releases] == [("red", 10.0), ("yellow", 10.2)]


def test_an_arrival_claims_its_release_once(monkeypatch):
    b = book(monkeypatch, [rel("red", 22.0)])
    assert b.claim(arrival("red", 40.0)).t == 22.0
    assert b.claim(arrival("red", 41.0)) is None


def test_an_arrival_takes_the_release_nearest_a_typical_flight(monkeypatch):
    b = book(monkeypatch, [rel("red", 15.0), rel("red", 22.0), rel("yellow", 23.0)])
    assert b.claim(arrival("red", 40.0)).t == 22.0       # lags 25 and 18: 18 is nearer 17


def test_no_claim_outside_the_lag_window(monkeypatch):
    b = book(monkeypatch, [rel("red", 22.0)])
    assert b.claim(arrival("red", 25.0)) is None          # 3 s: too soon to have flown
    assert b.claim(arrival("red", 60.0)) is None          # 38 s: too long


def test_a_release_waits_a_minute_for_its_arrival(monkeypatch):
    b = book(monkeypatch, [rel("yellow", 30.0)])
    assert b.unarrived(89.0) == []
    assert [r.t for r in b.unarrived(90.0)] == [30.0]
    assert b.unarrived(200.0) == []                       # given up on once


def test_a_claimed_release_is_never_given_up(monkeypatch):
    b = book(monkeypatch, [rel("red", 22.0)])
    b.claim(arrival("red", 40.0))
    assert b.unarrived(1000.0) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_practice_releases.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'curling_score.practice.releases'`

- [ ] **Step 3: Write the implementation**

`src/curling_score/practice/releases.py`:

```python
"""Throws seen leaving the thrower's house, kept until each is accounted for.

The panel over the thrower's house sees most deliveries leave it
(`detect.release.find_releases`). That gives a practice throw its release
time and its speed out of the hack, pairs it with its arrival at the other
end, and is the only sign at all of a rock that never got there.
"""

from curling_score.detect import release
from curling_score.game.sidereleases import TYPICAL_LAG_S

# The same release found again in a later window: the same colour, within this.
SAME_RELEASE_S = 1.0
# A release nothing arrived from this long after it is reported as a throw
# that never arrived. Not release.MAX_LAG_S (30 s) alone: that bounds the
# release-to-entry lag, and the arrival is only confirmed once the stone has
# rested for several seconds more -- giving up at 30 s would call a slow draw
# hogged before its own arrival could claim it.
NO_ARRIVAL_S = 60.0


def on_grid(frames, fps: float) -> list:
    """``frames`` thinned to ``fps``. The release finder was tuned at
    `release.RELEASE_FPS`, and the watch detects at twice that."""
    step, out, last = 1.0 / fps, [], None
    for t, d in frames:
        if last is None or t >= last + step - 1e-6:
            out.append((t, d))
            last = t
    return out


class ReleaseBook:
    """One panel as the thrower's house: every release it showed, each given
    to at most one arrival, or else reported once as never arriving."""

    def __init__(self, setup):
        self.setup = setup
        self.releases: list = []
        self._done: set[int] = set()     # paired with an arrival, or given up on

    def update(self, frames) -> None:
        for r in release.find_releases(on_grid(frames, release.RELEASE_FPS),
                                       self.setup.view_y_min_m, self.setup.view_x_limit_m):
            if not any(k.color == r.color and abs(k.t - r.t) <= SAME_RELEASE_S
                       for k in self.releases):
                self.releases.append(r)

    def claim(self, arrival):
        """The release ``arrival`` was thrown from, taken so no other arrival can
        have it; None when none fits. Of several, the one whose flight is
        nearest the typical: partners alternating one colour can leave two
        candidates inside the window, and the earliest is not the likeliest."""
        best = None
        for i, r in enumerate(self.releases):
            lag = arrival.t_enter - r.t
            if (i in self._done or r.color != arrival.color
                    or not release.MIN_LAG_S <= lag <= release.MAX_LAG_S):
                continue
            if best is None or abs(lag - TYPICAL_LAG_S) < abs(best[0] - TYPICAL_LAG_S):
                best = (lag, i)
        if best is None:
            return None
        self._done.add(best[1])
        return self.releases[best[1]]

    def unarrived(self, now_s: float) -> list:
        """The releases nothing arrived from within NO_ARRIVAL_S, each given once."""
        out = []
        for i, r in enumerate(self.releases):
            if i not in self._done and now_s - r.t >= NO_ARRIVAL_S:
                self._done.add(i)
                out.append(r)
        return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_practice_releases.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/practice/releases.py tests/test_practice_releases.py
git commit -m "practice: releases kept until an arrival claims them or a minute passes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Measuring one throw

**Files:**
- Create: `src/curling_score/practice/enrich.py`
- Test: `tests/test_practice_enrich.py`

**Interfaces:**
- Consumes:
  - `game.shots.Shot`, `shots.house_delta` and `shots._delivered_index` (private, imported on purpose: it is exactly the matching `from_deliveries` uses);
  - `detect.rest.stones_in_window`;
  - `thinking.time_shots(shots, frames, view_y_min_m)`;
  - `hogtime.time_hog_crossings(shots, video, view)`;
  - `sidereleases.time_side_releases(shots, video, view)`;
  - `broomtime.time_target_brooms(shots, video, view, model=)`;
  - `linetime.time_lines(shots, video, hog_view, dest_view, model=)`;
  - `fartime.time_far_crossings(shots, near_line=, far_line=)`;
  - `hogtime.CAMERA_FOR = {"top": "left", "bottom": "right"}`.
- Produces:
  - `enrich.OTHER_HOUSE`, `enrich.HOUSE_WINDOW_S = 3.0`;
  - `enrich.Stages(hog, side_release, broom, line)` and `enrich.real_stages() -> Stages`;
  - `enrich.build_shot(arrival, release, house_frames) -> Shot`, where `arrival` may be None for a throw that never arrived;
  - `enrich.enrich(video, setups, sideviews, models, *, house, arrival, release, house_frames, throw_frames, stages=None) -> Shot`.

- [ ] **Step 1: Write the failing tests**

`tests/test_practice_enrich.py`:

```python
"""One throw measured by the per-rock passes, over a list of one."""

from types import SimpleNamespace

from curling_score.detect.delivery import Delivery
from curling_score.detect.release import Release
from curling_score.practice import enrich as E
from tests.test_delivery import det

SIDEVIEWS = {"left": SimpleNamespace(name="left"), "right": SimpleNamespace(name="right")}
SETUPS = {h: SimpleNamespace(view_y_min_m=-4.0, view_x_limit_m=2.2, hog_line=None)
          for h in ("top", "bottom")}
MODELS = SimpleNamespace(broom_model="broom", line_model="line")


def arrival(color="red", t_enter=40.0, x=0.1, y=-0.4):
    return Delivery(color=color, t_enter=t_enter, t_rest=t_enter + 9.4, entry_y_m=3.8,
                    rest_x_m=x, rest_y_m=y, travel_m=4.2,
                    track=((t_enter, x, 3.8), (t_enter + 9.4, x, y)))


def recording(calls, fail=()):
    def stage(name):
        def run(shots, video, *views, model=None):
            calls.append((name, *[v.name for v in views], model))
            if name in fail:
                raise RuntimeError(f"{name} fell over")
        return run
    return E.Stages(hog=stage("hog"), side_release=stage("side"),
                    broom=stage("broom"), line=stage("line"))


def measure(calls, *, house="top", dv=None, rel=None, fail=(), sideviews=SIDEVIEWS):
    return E.enrich("rec.ts", SETUPS, sideviews, MODELS, house=house, arrival=dv,
                    release=rel, house_frames=[], throw_frames=[],
                    stages=recording(calls, fail))


def test_the_passes_run_in_order_on_the_cameras_an_end_uses():
    calls = []
    measure(calls, dv=arrival(), rel=Release("red", 22.0, 1.0, 2.0))
    # Thrown to the top house: the camera facing the thrower is the bottom
    # end's ("right"), the one seeing the house thrown to is "left".
    assert calls == [("hog", "right", None), ("side", "right", None),
                     ("broom", "left", "broom"), ("line", "right", "left", "line")]


def test_a_throw_that_never_arrived_gets_no_line():
    calls = []
    shot = measure(calls, rel=Release("yellow", 30.0, 1.0, 2.0))
    assert [c[0] for c in calls] == ["hog", "side", "broom"]
    assert shot.delivery is None and shot.color == "yellow"


def test_one_pass_failing_costs_only_its_own_figure():
    calls = []
    measure(calls, dv=arrival(), fail=("hog",))
    assert [c[0] for c in calls] == ["hog", "side", "broom", "line"]


def test_without_side_views_only_the_overhead_passes_run():
    calls = []
    shot = measure(calls, dv=arrival(), sideviews=None)
    assert calls == [] and shot.delivery is not None


def test_the_house_is_read_just_before_the_arrival_and_just_after_the_rest():
    dv = arrival()                                   # enters 40.0, rests 49.4
    frames = []
    for i in range(30 * 5, 60 * 5):
        t = i / 5
        here = [det("yellow", 0.8, 0.2)]
        if t >= dv.t_rest:
            here.append(det("red", 0.1, -0.4))
        if t >= 53.0:                                # after the 3 s house read
            here.append(det("yellow", -1.0, 1.0))
        frames.append((t, here))
    shot = E.build_shot(dv, None, frames)
    assert sorted((s.color, round(s.x_m, 2)) for s in shot.stones) == [
        ("red", 0.1), ("yellow", 0.8)]
    assert shot.stones[shot.delivered_stone_index].color == "red"
    assert [s["color"] for s in shot.house_delta["added"]] == ["red"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_practice_enrich.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'curling_score.practice.enrich'`

- [ ] **Step 3: Write the implementation**

`src/curling_score/practice/enrich.py`:

```python
"""Everything measured about one delivery, by the passes an end already runs.

`analyze.build_one_end` runs each per-rock pass over an end's shot list. Each
reads one rock at a time, anchored on that rock's own release, crossing or
rest, so here they run over a list of one: the same passes, in the same order,
on the same cameras. Left out is everything that needs an end -- numbering,
the fit, blanks, scores -- and the long camera's release offset, which
`sidereleases.time_side_releases` takes as a median over an end's overhead
releases: alone, a practice throw takes it as zero (the offset measured
-0.11..+0.23 s across games).
"""

import logging
import math
from dataclasses import dataclass

from curling_score.detect import rest
from curling_score.game import (broomtime, fartime, hogtime, linetime, sidereleases,
                                thinking)
from curling_score.game import shots as shots_mod
from curling_score.game.shots import Shot

log = logging.getLogger(__name__)

OTHER_HOUSE = {"top": "bottom", "bottom": "top"}
# The house is read over this long just before the stone arrives and just after
# it rests. An end reads 12 s after each rest, up to the next throw
# (shots.SETTLE_WINDOW_S); a practice card cannot wait that long.
HOUSE_WINDOW_S = 3.0


@dataclass
class Stages:
    """The per-rock passes that read the long cameras, each run in place over a
    list of shots. A test passes its own; `real_stages` gives the pipeline's."""

    hog: object
    side_release: object
    broom: object
    line: object


def real_stages() -> Stages:
    return Stages(hog=hogtime.time_hog_crossings,
                  side_release=sidereleases.time_side_releases,
                  broom=broomtime.time_target_brooms, line=linetime.time_lines)


def _house(frames, t0, t1):
    window = [(t, d) for t, d in frames if t0 <= t <= t1]
    return rest.stones_in_window(window) if window else []


def build_shot(arrival, release, house_frames) -> Shot:
    """The `Shot` for one delivery, as `shots.from_deliveries` builds one in an
    end, from the house just before it arrived and just after it rested. A
    throw that never arrived has no house to read."""
    if arrival is None:
        return Shot(number=1, color=release.color, stones=[], t_rest_s=math.nan,
                    release=release, confidence=0.5, state_known=False)
    before = _house(house_frames, arrival.t_enter - HOUSE_WINDOW_S, arrival.t_enter)
    after = _house(house_frames, arrival.t_rest, arrival.t_rest + HOUSE_WINDOW_S)
    idx = shots_mod._delivered_index(after, arrival, before)
    return Shot(
        number=1, color=arrival.color, stones=after, t_rest_s=arrival.t_rest,
        confidence=1.0 if arrival.came_to_rest else 0.8, delivery=arrival,
        release=release, state_known=bool(after) or not arrival.came_to_rest,
        house_delta=shots_mod.house_delta(before, after, thrower=arrival.color,
                                          delivered=idx),
        delivered_stone_index=idx)


def _run(name, fn, *args, **kw):
    try:
        fn(*args, **kw)
    except Exception:  # noqa: BLE001 - one pass failing costs that figure only
        log.exception("practice: the %s pass failed; the throw goes without it", name)


def enrich(video, setups, sideviews, models, *, house, arrival, release, house_frames,
           throw_frames, stages=None) -> Shot:
    """One throw to ``house``, measured. ``arrival`` is None for a throw that
    never arrived; ``release`` is None when the overhead missed it."""
    stages = stages or real_stages()
    throwing = OTHER_HOUSE[house]
    shot = build_shot(arrival, release, house_frames)
    shots = [shot]
    # The tee crossing for a rock with no release, as an end's clock gets it:
    # the broom window hangs off it.
    _run("tee", thinking.time_shots, shots, throw_frames, setups[throwing].view_y_min_m)
    if sideviews is not None:
        hog_view = sideviews[hogtime.CAMERA_FOR[throwing]]
        dest_view = sideviews[hogtime.CAMERA_FOR[house]]
        _run("hog", stages.hog, shots, video, hog_view)
        _run("side release", stages.side_release, shots, video, hog_view)
        _run("broom", stages.broom, shots, video, dest_view, model=models.broom_model)
        if arrival is not None:
            # The line follows the stone to where it stopped.
            _run("line", stages.line, shots, video, hog_view, dest_view,
                 model=models.line_model)
    _run("far hog", fartime.time_far_crossings, shots,
         near_line=setups[throwing].hog_line, far_line=setups[house].hog_line)
    return shot
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_practice_enrich.py -v`
Expected: PASS (5 tests). A logged traceback from `test_one_pass_failing_costs_only_its_own_figure` is expected.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/practice/enrich.py tests/test_practice_enrich.py
git commit -m "practice: measure one throw with the passes an end runs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: The watch

**Files:**
- Create: `src/curling_score/practice/watch.py`
- Test: `tests/test_practice_watch.py`

**Interfaces:**
- Consumes:
  - from Tasks 1-4: `throws.throw_record`, `finder.Buffer`/`ArrivalFinder`, `releases.ReleaseBook`, `enrich.OTHER_HOUSE`;
  - `live.session.Calibration` (`.setups`, `.sideviews`, `.complete`) and `live.session._worth(cal) -> int`;
  - `geometry.calibrate.CalibrationError`.
- The recording needs `.path`, `.head_s() -> float` and `.ended() -> bool`.
- The pipeline needs:
  - `.calibrate(path, until_s) -> Calibration`, which may raise CalibrationError;
  - `.detect(path, setups, from_s, until_s, detector) -> {"top": frames, "bottom": frames}`;
  - `.enrich(path, cal, models, *, house, arrival, release, house_frames, throw_frames) -> Shot`.
- Produces:
  - `watch.PracticeWatch(*, recording, pipeline, models, since_s, t0_s=0.0, publish=None, clock=time.monotonic, progress=log.info)`, with `.step() -> bool`, `.document() -> dict`, `.done`, `.status` (`calibrating | watching | ended | failed`) and `.throws`;
  - `watch.DETECT_FPS = 10.0`, `watch.HOUSES = ("top", "bottom")`.
- `document()` returns `{"practice": 1, "status", "t0_s", "since_s", "recorded_s", "throws"}`. The last three are on the stream's clock.

- [ ] **Step 1: Write the failing tests**

`tests/test_practice_watch.py`:

```python
"""One sheet's practice, watched step by step, with no video."""

from types import SimpleNamespace

import pytest

from curling_score.detect.release import Release
from curling_score.geometry.calibrate import CalibrationError
from curling_score.live.session import Calibration
from curling_score.practice import enrich as E
from curling_score.practice import releases as R
from curling_score.practice import watch as W
from tests.test_delivery import det, thrown
from tests.test_practice_finder import sheet


def cal(complete=True):
    setups = {h: SimpleNamespace(hog_line="paint", view_x_limit_m=2.2, view_y_min_m=-4.0)
              for h in W.HOUSES}
    if not complete:
        setups["bottom"].hog_line = None
    return Calibration(panels=None, setups=setups, sideviews=None, until_s=0.0,
                       side_expected=False)


class Rec:
    path = "rec.ts"

    def __init__(self, head=0.0):
        self.head, self.over = head, False

    def head_s(self):
        return self.head

    def ended(self):
        return self.over


class Pipeline:
    def __init__(self, top=(), bottom=(), cals=None):
        self.frames = {"top": list(top), "bottom": list(bottom)}
        self.cals = list(cals) if cals else [cal()]
        self.calibrated, self.spans = [], []

    def calibrate(self, path, until_s):
        self.calibrated.append(until_s)
        got = self.cals.pop(0) if len(self.cals) > 1 else self.cals[0]
        if isinstance(got, Exception):
            raise got
        return got

    def detect(self, path, setups, from_s, until_s, detector):
        self.spans.append((from_s, until_s))
        return {h: [f for f in self.frames[h] if from_s <= f[0] <= until_s] for h in W.HOUSES}

    def enrich(self, path, cal, models, *, house, arrival, release, house_frames,
               throw_frames):
        return E.build_shot(arrival, release, house_frames)


@pytest.fixture(autouse=True)
def releases_behind_the_hack(monkeypatch):
    """A stand-in for the release finder: a release wherever a frame holds a
    stone behind the hack line, which only a throwing panel shows here."""
    monkeypatch.setattr(R.release, "find_releases", lambda frames, y, x=None: [
        Release(d.color, t, 1.0, 2.0) for t, ds in frames for d in ds if d.y_m < -3.5])


def hack(color, t):
    return [(t, det(color, 0.0, -3.6))]


def watched(pipeline, since_s=20.0, until_s=100.0, t0_s=0.0, clock=None):
    rec, docs = Rec(), []
    w = W.PracticeWatch(recording=rec, pipeline=pipeline, models=SimpleNamespace(detector=None),
                        since_s=since_s, t0_s=t0_s, publish=docs.append,
                        clock=clock or (lambda: 0.0))
    while rec.head < until_s:
        rec.head = min(until_s, rec.head + 1.0)
        while w.step():
            pass
    rec.over = True
    while w.step():
        pass
    return w, docs


def test_a_throw_is_published_once_with_its_release():
    w, docs = watched(Pipeline(top=sheet(thrown("red", t0=40.0), until_s=100.0),
                               bottom=sheet(hack("red", 22.0), until_s=100.0)),
                      t0_s=1000.0)
    assert len(w.throws) == 1
    th = w.throws[0]
    assert (th["house"], th["color"], th["arrived"]) == ("top", "red", True)
    assert th["t_release_s"] == 1022.0 and th["release_source"] == "overhead"
    assert docs[-1]["status"] == "ended" and w.done
    assert docs[-1]["since_s"] == 1020.0 and docs[-1]["t0_s"] == 1000.0


def test_a_release_that_never_arrives_becomes_a_throw_a_minute_later():
    w, _ = watched(Pipeline(top=sheet(until_s=100.0),
                            bottom=sheet(hack("yellow", 30.0), until_s=100.0)))
    assert [(t["house"], t["color"], t["arrived"]) for t in w.throws] == [
        ("top", "yellow", False)]


def test_an_arrival_nothing_released_is_reported_without_a_release():
    w, _ = watched(Pipeline(top=sheet(thrown("red", t0=40.0), until_s=100.0),
                            bottom=sheet(until_s=100.0)))
    assert len(w.throws) == 1
    assert w.throws[0]["release_source"] is None and w.throws[0]["t_release_s"] is None


def test_a_throw_released_before_start_is_left_out():
    top = sheet(thrown("red", t0=66.0, x=0.2), thrown("red", t0=96.0, x=-0.6), until_s=140.0)
    bottom = sheet(hack("red", 50.0), hack("red", 80.0), until_s=140.0)
    w, _ = watched(Pipeline(top=top, bottom=bottom), since_s=60.0, until_s=140.0)
    assert [t["t_release_s"] for t in w.throws] == [80.0]


def test_it_calibrates_only_once_the_lookback_is_there():
    p, rec = Pipeline(), Rec(head=10.0)
    w = W.PracticeWatch(recording=rec, pipeline=p, models=None, since_s=20.0,
                        clock=lambda: 0.0)
    assert w.step() is False and p.calibrated == []
    rec.head = 20.0
    assert w.step() is True
    assert p.calibrated == [20.0] and w.status == "watching"


def test_an_incomplete_calibration_is_tried_again_a_minute_later():
    now = [0.0]
    p, rec = Pipeline(cals=[cal(complete=False), cal()]), Rec(head=20.0)
    w = W.PracticeWatch(recording=rec, pipeline=p, models=None, since_s=20.0,
                        clock=lambda: now[0])
    assert w.step() is True and w.status == "calibrating"
    assert w.step() is False                       # not yet a minute
    now[0] = 60.0
    assert w.step() is True
    assert len(p.calibrated) == 2 and w.status == "watching" and w.cal.complete


def test_it_gives_up_after_its_calibration_tries():
    now, docs = [0.0], []
    p, rec = Pipeline(cals=[CalibrationError("no panels")]), Rec(head=20.0)
    w = W.PracticeWatch(recording=rec, pipeline=p, models=None, since_s=20.0,
                        publish=docs.append, clock=lambda: now[0])
    for _ in range(W.CALIB_TRIES):
        assert w.step() is True
        now[0] += W.CALIB_RETRY_S
    assert w.done and w.status == "failed" and docs[-1]["status"] == "failed"
    assert w.step() is False


def test_a_long_catch_up_is_read_in_pieces_from_the_look_back():
    p = Pipeline()
    rec = Rec(head=500.0)
    w = W.PracticeWatch(recording=rec, pipeline=p, models=SimpleNamespace(detector=None),
                        since_s=100.0, clock=lambda: 0.0)
    while w.step():
        pass
    assert p.spans[0][0] == 64.0                   # Start less a delivery's look-back
    assert all(b - a <= W.STEP_MAX_S for a, b in p.spans)
    assert all(p.spans[i][1] == p.spans[i + 1][0] for i in range(len(p.spans) - 1))
    assert p.spans[-1][1] == 498.0                 # just behind the head
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_practice_watch.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'curling_score.practice.watch'`

- [ ] **Step 3: Write the implementation**

`src/curling_score/practice/watch.py`:

```python
"""One sheet's practice, watched: every delivery reported as it comes to rest.

A `PracticeWatch` owns a growing recording of one sheet's stream and turns it
into a list of throws. Each call to :meth:`step` does the one most useful
thing there is to do, and says whether it did anything:

* **Calibrate** once the recording reaches ``since_s``. Everything before it
  is the lookback: footage from before Start, there only to calibrate from
  (`live.session.VideoPipeline.calibrate`, medians of a mostly empty sheet).
  A calibration missing a hog line or a side view is tried again every
  CALIB_RETRY_S, keeping the best, until CALIB_TRIES.
* **Watch:** detect both panels from where the last step stopped to just
  behind the head, at most STEP_MAX_S at a time. Find the arrivals each panel
  confirmed and the releases each saw, pair them, measure each new throw
  (`practice.enrich`), and publish.
* **Finish** once the recording has ended and been read to its end.

Reading starts a delivery's look-back before Start, so a throw just after
Start is judged on as much as any other; a throw released before Start is
left out. Everything that touches video goes through ``pipeline``, so the
watch's own decisions are testable without any.
"""

import logging
import time

from curling_score.detect import delivery
from curling_score.geometry.calibrate import CalibrationError
from curling_score.live.session import _worth
from curling_score.practice import throws
from curling_score.practice.enrich import OTHER_HOUSE
from curling_score.practice.finder import ArrivalFinder, Buffer
from curling_score.practice.releases import ReleaseBook

log = logging.getLogger(__name__)

HOUSES = ("top", "bottom")
# Both panels, twice the release finder's rate (it is thinned back for that).
DETECT_FPS = 10.0
# The last packets of a growing MPEG-TS may not decode yet.
HEAD_MARGIN_S = 2.0
# Each read enters a TS file 10 s early (frames.seek_lead), so less new
# footage than this is not worth a read; a catch-up is read in pieces so no
# single step holds the lane for long.
STEP_MIN_S = 2.0
STEP_MAX_S = 30.0
CALIB_RETRY_S = 60.0
CALIB_TRIES = 5
# A throw nothing saw released is dated this long before its arrival, as the
# recorded pipeline does (timeline.RELEASE_TO_ARRIVAL_S).
RELEASE_TO_ARRIVAL_S = 15.0


def _thrown_at(arrival, rel) -> float:
    return rel.t if rel is not None else arrival.t_enter - RELEASE_TO_ARRIVAL_S


class PracticeWatch:
    def __init__(self, *, recording, pipeline, models, since_s: float, t0_s: float = 0.0,
                 publish=None, clock=time.monotonic, progress=log.info):
        """``since_s`` is Start on the recording's clock; ``t0_s`` is where the
        recording begins on the stream's, which every published time is on."""
        self.rec, self.pipeline, self.models = recording, pipeline, models
        self.since_s, self.t0_s = float(since_s), float(t0_s)
        self.publish = publish or (lambda doc: None)
        self.clock, self.progress = clock, progress
        self.cal = None
        self.calib_tries = 0
        self.calib_next = float("-inf")
        self.status = "calibrating"
        self.done_s = max(0.0, self.since_s - delivery.REQUIRED_LOOKBACK_S)
        self.buffers = {h: Buffer() for h in HOUSES}
        self.finders = self.books = None
        self.throws: list = []
        self.done = False

    def step(self) -> bool:
        if self.done:
            return False
        head, ended = self.rec.head_s(), self.rec.ended()
        if self.finders is None:
            return self._calibrate(head, ended)
        upto = head if ended else head - HEAD_MARGIN_S
        if upto - self.done_s >= STEP_MIN_S or (ended and upto > self.done_s):
            self._watch(min(upto, self.done_s + STEP_MAX_S))
            return True
        if ended:
            self.status, self.done = "ended", True
            self._publish()
            return True
        return False

    def document(self) -> dict:
        return {"practice": 1, "status": self.status, "t0_s": self.t0_s,
                "since_s": round(self.t0_s + self.since_s, 2),
                "recorded_s": round(self.t0_s + self.done_s, 2),
                "throws": list(self.throws)}

    # --- calibrating -------------------------------------------------------------

    def _calibrate(self, head, ended) -> bool:
        if (head < self.since_s and not ended) or self.clock() < self.calib_next:
            return False
        self.calib_tries += 1
        try:
            cal = self.pipeline.calibrate(self.rec.path, head)
        except CalibrationError as exc:
            self.progress(f"practice: calibration {self.calib_tries} failed: {exc}")
            cal = None
        if cal is not None and (self.cal is None or _worth(cal) > _worth(self.cal)):
            self.cal = cal
        self.calib_next = self.clock() + CALIB_RETRY_S
        last_try = self.calib_tries >= CALIB_TRIES or ended
        if self.cal is not None and (self.cal.complete or last_try):
            setups = self.cal.setups
            self.finders = {h: ArrivalFinder(setups[h]) for h in HOUSES}
            self.books = {h: ReleaseBook(setups[h]) for h in HOUSES}
            self.status = "watching"
        elif self.cal is None and last_try:
            self.status, self.done = "failed", True
        self._publish()
        return True

    # --- watching ----------------------------------------------------------------

    def _watch(self, upto) -> None:
        got = self.pipeline.detect(self.rec.path, self.cal.setups, self.done_s, upto,
                                   self.models.detector)
        for h in HOUSES:
            self.buffers[h].extend(got[h])
            self.books[h].update(self.buffers[h].frames)
        self.done_s = upto
        new = []
        # Arrivals claim their releases before any release is given up on.
        for h in HOUSES:
            book = self.books[OTHER_HOUSE[h]]
            new += [(h, a, book.claim(a)) for a in self.finders[h].new(self.buffers[h])]
        for throwing in HOUSES:
            new += [(OTHER_HOUSE[throwing], None, r)
                    for r in self.books[throwing].unarrived(upto)]
        added = 0
        for house, arrival, rel in sorted(new, key=lambda n: _thrown_at(n[1], n[2])):
            if _thrown_at(arrival, rel) < self.since_s:
                continue                       # thrown before Start
            shot = self.pipeline.enrich(
                self.rec.path, self.cal, self.models, house=house, arrival=arrival,
                release=rel, house_frames=self.buffers[house].frames,
                throw_frames=self.buffers[OTHER_HOUSE[house]].frames)
            self.throws.append(throws.throw_record(shot, house=house, t0_s=self.t0_s))
            added += 1
        if added:
            self._publish()

    def _publish(self) -> None:
        self.publish(self.document())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_practice_watch.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Run every practice test together**

Run: `python -m pytest tests/test_practice_throws.py tests/test_practice_finder.py tests/test_practice_releases.py tests/test_practice_enrich.py tests/test_practice_watch.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/curling_score/practice/watch.py tests/test_practice_watch.py
git commit -m "practice: the watch -- calibrate from the lookback, report each throw

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: The real pipeline

**Files:**
- Create: `src/curling_score/practice/pipeline.py`
- Test: `tests/test_practice_pipeline.py`

**Interfaces:**
- Consumes:
  - `live.session.VideoPipeline` for `calibrate`;
  - `detect.sequence.detect_spans(path, specs, end_s, detector)`, where `specs` is `[(setup, start_s, fps)]` and the result is a list per spec;
  - `enrich.enrich` (Task 4);
  - `watch.DETECT_FPS`, `watch.HOUSES` (Task 5).
- Produces: `pipeline.PracticePipeline(*, weights=None, skip_longview=False, line=True, progress=log.info, fps=DETECT_FPS)`, with `.calibrate(path, until_s)` (inherited), `.detect(path, setups, from_s, until_s, detector) -> dict`, and `.enrich(path, cal, models, **kw) -> Shot`.

- [ ] **Step 1: Write the failing tests**

`tests/test_practice_pipeline.py`:

```python
"""The watch's pipeline for real: one decode for both panels, then the passes."""

from types import SimpleNamespace

from curling_score.detect import sequence
from curling_score.practice import enrich as enrich_mod
from curling_score.practice.pipeline import PracticePipeline


def test_both_panels_come_from_one_decode(monkeypatch):
    asked = {}

    def spans(path, specs, end_s, detector):
        asked.update(path=path, specs=specs, end_s=end_s, detector=detector)
        return [["top frames"], ["bottom frames"]]

    monkeypatch.setattr(sequence, "detect_spans", spans)
    setups = {"top": "T", "bottom": "B"}
    got = PracticePipeline().detect("rec.ts", setups, 64.0, 94.0, "yolo")
    assert got == {"top": ["top frames"], "bottom": ["bottom frames"]}
    assert asked == {"path": "rec.ts", "specs": [("T", 64.0, 10.0), ("B", 64.0, 10.0)],
                     "end_s": 94.0, "detector": "yolo"}


def test_a_throw_is_measured_with_the_calibrations_panels_and_views(monkeypatch):
    seen = {}
    monkeypatch.setattr(enrich_mod, "enrich",
                        lambda video, setups, sideviews, models, **kw: seen.update(
                            video=video, setups=setups, sideviews=sideviews, **kw) or "shot")
    cal = SimpleNamespace(setups="S", sideviews="V")
    assert PracticePipeline().enrich("rec.ts", cal, "M", house="top", arrival=None,
                                     release="r", house_frames=[], throw_frames=[]) == "shot"
    assert (seen["video"], seen["setups"], seen["sideviews"], seen["house"]) == (
        "rec.ts", "S", "V", "top")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_practice_pipeline.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'curling_score.practice.pipeline'`

- [ ] **Step 3: Write the implementation**

`src/curling_score/practice/pipeline.py`:

```python
"""The watch's pipeline for real: every call reads the recording.

Calibration is the live session's (`live.session.VideoPipeline`): the same
medians of the same keyframes, from whatever the recording holds so far.
Detection reads the growing recording itself -- no strip proxy, no detection
cache, as for a live end -- and decodes each frame once for both panels.
"""

import logging

from curling_score.detect import sequence
from curling_score.live.session import VideoPipeline
from curling_score.practice import enrich as enrich_mod
from curling_score.practice.watch import DETECT_FPS, HOUSES

log = logging.getLogger(__name__)


class PracticePipeline(VideoPipeline):
    def __init__(self, *, fps: float = DETECT_FPS, **kw):
        super().__init__(**kw)
        self.fps = fps

    def detect(self, path, setups, from_s, until_s, detector) -> dict:
        got = sequence.detect_spans(path, [(setups[h], from_s, self.fps) for h in HOUSES],
                                    until_s, detector)
        return dict(zip(HOUSES, got))

    def enrich(self, path, cal, models, **kw):
        return enrich_mod.enrich(path, cal.setups, cal.sideviews, models, **kw)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_practice_pipeline.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/practice/pipeline.py tests/test_practice_pipeline.py
git commit -m "practice: the real pipeline -- one decode for both panels

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: A replay that starts mid-video with a lookback burst

**Files:**
- Modify: `src/curling_score/live/replay.py` (the `ReplayRecording.__init__` and `start`, plus a new module function)
- Test: `tests/test_live_replay.py` (append to `TestReplayRecording`)

**Interfaces:**
- Produces:
  - `replay.keyframe_at_or_before(path, t_s) -> float`, on the same clock `ingest.frames` uses (stream start = 0);
  - `ReplayRecording(source, path, speed=1.0, *, start_s=0.0, end_s=None, burst_s=0.0)`, gaining `.t0_s`, where the recording begins on the source's clock. It is known after `start()`.
- Existing callers pass only `(source, path, speed=)` and are unchanged.

- [ ] **Step 1: Write the failing tests**

Append to `class TestReplayRecording` in `tests/test_live_replay.py`:

```python
    def test_a_window_begins_at_the_keyframe_before_its_start(self, tmp_path, clip):
        rec = replay.ReplayRecording(clip, tmp_path / "rec.ts", speed=8.0,
                                     start_s=5.5, end_s=12.0)
        rec.start()
        try:
            deadline = time.monotonic() + 30
            while not rec.ended() and time.monotonic() < deadline:
                time.sleep(0.1)
        finally:
            rec.stop()
        assert rec.t0_s == pytest.approx(5.0, abs=0.02)   # keyframes every second
        # The recording's clock is the source's, less t0_s, to the frame.
        got = next(iter(F.window(rec.path, 2.0, 2.01, 30.0)))[1]
        want = next(iter(F.window(clip, rec.t0_s + 2.0, rec.t0_s + 2.01, 30.0)))[1]
        assert (got == want).all()
        assert rec.head_s() >= 6.0

    def test_a_lookback_burst_arrives_at_once_then_real_time(self, tmp_path, clip):
        rec = replay.ReplayRecording(clip, tmp_path / "rec.ts", speed=1.0, burst_s=10.0)
        rec.start()
        try:
            began = time.monotonic()
            while rec.head_s() < 9.0 and time.monotonic() - began < 5.0:
                time.sleep(0.05)
            assert rec.head_s() >= 9.0 and time.monotonic() - began < 4.0
            assert rec.head_s() < 16.0                     # not the whole clip at once
        finally:
            rec.stop()

    def test_a_replay_from_the_beginning_starts_at_zero(self, tmp_path, clip):
        rec = replay.ReplayRecording(clip, tmp_path / "rec.ts", speed=8.0).start()
        rec.stop()
        assert rec.t0_s == 0.0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_live_replay.py -v`
Expected: FAIL with `TypeError: ReplayRecording.__init__() got an unexpected keyword argument 'start_s'`

- [ ] **Step 3: Write the implementation**

In `src/curling_score/live/replay.py`, extend the module docstring's first paragraph with one sentence:

```python
"""A cached video played back as if it were a live stream.

It writes the same growing MPEG-TS a live recorder does, at ``speed`` times
real time, so everything downstream -- the session, the worker's lane, the
five-stream rehearsal -- can be run against footage whose right answer is
already known, without touching YouTube. A replay can also begin mid-video,
with its first ``burst_s`` written at once: a stream that has been running for
hours, picked up some minutes back in its DVR window, as a practice watch is.
"""
```

Add `keyframe_at_or_before` above the class:

```python
def keyframe_at_or_before(path, t_s: float) -> float:
    """The last keyframe at or before ``t_s``, where a stream copy cut at
    ``t_s`` really begins -- on `ingest.frames`' clock, the stream's start
    being zero."""
    def probe(*args):
        return subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", *args,
                               "-of", "csv=p=0", str(path)],
                              capture_output=True, text=True, check=True).stdout
    start = probe("-show_entries", "stream=start_time").strip()
    offset = float(start) if start not in ("", "N/A") else 0.0
    keys = [float(v) - offset for v in probe(
        "-skip_frame", "nokey", "-show_entries", "frame=pts_time",
        "-read_intervals", f"{max(0.0, t_s - 20.0):.3f}%{t_s + 0.001:.3f}").split()
        if v.strip() and v.strip() != "N/A"]
    before = [k for k in keys if k <= t_s + 1e-3]
    return max(before) if before else 0.0
```

Change `__init__` and `start`:

```python
    def __init__(self, source, path, speed: float = 1.0, *, start_s: float = 0.0,
                 end_s: float | None = None, burst_s: float = 0.0):
        self.source = Path(source)
        self.path = Path(path)
        self.speed = float(speed)
        self.start_s, self.end_s, self.burst_s = float(start_s), end_s, float(burst_s)
        # Where the recording begins on the source's clock; set by start().
        self.t0_s = 0.0
        self._proc = None
        self._head_s = 0.0
        self._finished = False
        self._stopped = False
        self._reader = None

    def start(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
               "-readrate", str(self.speed)]
        if self.burst_s > 0:
            cmd += ["-readrate_initial_burst", str(self.burst_s)]
        if self.start_s > 0:
            # A stream copy can only begin at a keyframe: the one at or before.
            self.t0_s = keyframe_at_or_before(self.source, self.start_s)
            cmd += ["-ss", f"{self.start_s:.3f}"]
        if self.end_s is not None:
            cmd += ["-t", f"{self.end_s - self.start_s:.3f}"]
        cmd += ["-i", str(self.source),
                "-map", "0:v:0", "-c", "copy", "-f", "mpegts", "-flush_packets", "1",
                "-progress", "pipe:1", "-stats_period", "0.2", str(self.path)]
        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True)
        self._reader = threading.Thread(target=self._read_progress, daemon=True)
        self._reader.start()
        return self
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_live_replay.py tests/test_cli_live_replay.py -v`
Expected: PASS. The frame-equality check is the one that matters.
- If it fails by exactly one keyframe interval, ffmpeg began the copy at a different keyframe than `keyframe_at_or_before` picked. Fix `t0_s`'s computation, for example by reading the first packet's pts from the written TS with ffprobe and adding `start_s` minus ffmpeg's shift. **Never loosen the equality.**
- If it fails by a fraction of a frame, check B-frame reordering against `frames._stream_start`.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/live/replay.py tests/test_live_replay.py
git commit -m "replay: begin mid-video, with a lookback burst, on the source's clock

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: `curling-score practice-replay`, with each throw's latency

**Files:**
- Create: `src/curling_score/practice/harness.py`
- Modify: `src/curling_score/cli.py` (add `_practice_replay` after `_live_replay`; register the parser after `live-replay`'s, around line 691)
- Test: `tests/test_practice_harness.py`, `tests/test_cli_practice_replay.py`

**Interfaces:**
- Consumes: `PracticeWatch` (Task 5), `PracticePipeline` (Task 6), `ReplayRecording(..., start_s, end_s, burst_s)` with `.t0_s` (Task 7), `releases.NO_ARRIVAL_S` (Task 3), `analyze.load_models`, `live.session.Models`.
- Produces:
  - `harness.HeadClock(clock=time.monotonic, every_s=0.2)`, with `.sample(head, wall=None)`, `.reached(t) -> float | None`, `.start(rec) -> self` and `.stop()`;
  - `harness.Sink(out, heads, clock=time.monotonic, show=print)`, with `.publish(doc)`. It writes `out/practice.json` and appends each new throw once to `out/throws.jsonl`, adding `latency_s`;
  - `harness.run(watch, *, sleep=time.sleep, idle_s=0.25)`;
  - the CLI command `curling-score practice-replay VIDEO --from S --to E [--lookback 1200] [--speed 1] [--out out/practice/run] [--weights] [--imgsz 448] [--device] [--no-longview] [--no-line]`.
- Latency is the publish's wall time minus when the moment it could first be known was readable in the recording. That moment is the rest for an arrival, and release + `NO_ARRIVAL_S` for a throw that never arrived.

- [ ] **Step 1: Write the failing tests**

`tests/test_practice_harness.py`:

```python
"""The practice replay's harness: when footage arrived, and each throw's latency."""

import json

import pytest

from curling_score.practice import harness


def heads(*samples):
    h = harness.HeadClock()
    for wall, head in samples:
        h.sample(head, wall=wall)
    return h


class TestHeadClock:
    def test_a_moment_is_reached_between_samples_at_a_steady_rate(self):
        h = heads((0.0, 0.0), (10.0, 10.0), (20.0, 12.0))
        assert h.reached(5.0) == pytest.approx(5.0)
        assert h.reached(11.0) == pytest.approx(15.0)
        assert h.reached(-1.0) == 0.0
        assert h.reached(13.0) is None

    def test_a_head_that_did_not_move_adds_no_sample(self):
        h = heads((0.0, 5.0), (1.0, 5.0))
        assert h.samples == [(0.0, 5.0)]


def doc(*throws, t0=1000.0):
    return {"practice": 1, "status": "watching", "t0_s": t0, "throws": list(throws)}


def throw(id, t_rest=None, t_release=None, arrived=True):
    return {"id": id, "t_rest_s": t_rest, "t_release_s": t_release, "arrived": arrived,
            "house": "top", "color": "red", "split_s": None, "line": None, "rest": None}


def test_each_throw_is_written_once_with_its_latency(tmp_path):
    now = [160.0]
    sink = harness.Sink(tmp_path, heads((100.0, 0.0), (200.0, 100.0)),
                        clock=lambda: now[0], show=lambda line: None)
    sink.publish(doc(throw("t_1030.0", t_rest=1050.0)))
    now[0] = 185.0
    sink.publish(doc(throw("t_1030.0", t_rest=1050.0),
                     throw("t_1010.0", t_release=1010.0, arrived=False)))
    lines = [json.loads(x) for x in (tmp_path / "throws.jsonl").read_text().splitlines()]
    assert [x["id"] for x in lines] == ["t_1030.0", "t_1010.0"]
    assert lines[0]["latency_s"] == 10.0          # rested at 50 s, readable at 150 s
    assert lines[1]["latency_s"] == 15.0          # given up at 10 + 60 s, readable at 170 s
    assert json.loads((tmp_path / "practice.json").read_text())["throws"][1]["id"] == "t_1010.0"


def test_run_steps_until_the_watch_is_done_and_rests_when_idle():
    class Watch:
        done, steps = False, [True, False, True]

        def step(self):
            did = self.steps.pop(0)
            self.done = not self.steps
            return did

    slept = []
    harness.run(Watch(), sleep=slept.append, idle_s=0.25)
    assert slept == [0.25]
```

`tests/test_cli_practice_replay.py`:

```python
"""`curling-score practice-replay`: a cached video, watched as a practice session."""

import json

from curling_score import cli


def test_a_replay_begins_a_lookback_before_start_and_writes_its_throws(tmp_path, monkeypatch):
    from curling_score import analyze
    from curling_score.live import replay
    from curling_score.practice import harness

    made = {}

    class Rec:
        def __init__(self, source, path, speed=1.0, *, start_s=0.0, end_s=None, burst_s=0.0):
            made.update(source=str(source), speed=speed, start_s=start_s, end_s=end_s,
                        burst_s=burst_s)
            self.path, self.t0_s = path, 1795.0

        def start(self):
            return self

        def stop(self):
            made["stopped"] = True

    class Heads:
        def start(self, rec):
            return self

        def stop(self):
            pass

        def reached(self, t):
            return 0.0

    def run(watch, **kw):
        made.update(since_s=watch.since_s, t0_s=watch.t0_s)
        watch.publish({"practice": 1, "status": "ended", "t0_s": 1795.0, "throws": []})
        watch.done = True

    monkeypatch.setattr(replay, "ReplayRecording", Rec)
    monkeypatch.setattr(harness, "HeadClock", Heads)
    monkeypatch.setattr(harness, "run", run)
    monkeypatch.setattr(analyze, "load_models", lambda *a, **k: (None, None, None))
    out = tmp_path / "practice"
    assert cli.main(["practice-replay", str(tmp_path / "abcdefghijk.mp4"), "--from", "3000",
                     "--to", "4800", "--out", str(out), "--weights", "none"]) == 0
    assert made == {"source": str(tmp_path / "abcdefghijk.mp4"), "speed": 1.0,
                    "start_s": 1800.0, "end_s": 4800.0, "burst_s": 1200.0,
                    "since_s": 1205.0, "t0_s": 1795.0, "stopped": True}
    assert json.loads((out / "practice.json").read_text())["status"] == "ended"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_practice_harness.py tests/test_cli_practice_replay.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'curling_score.practice.harness'`

- [ ] **Step 3: Write the harness**

`src/curling_score/practice/harness.py`:

```python
"""Running a practice watch over a replay, and timing what it publishes.

A throw's latency is how long after it could first have been known the watch
published it: for an arrival, the moment its rest was written into the
recording; for a throw that never arrived, the moment its release had waited
`releases.NO_ARRIVAL_S` with nothing coming. When each moment was written is
read off the replay's head, sampled from a thread, since the watch's own
steps can take seconds.
"""

import json
import threading
import time
from pathlib import Path

from curling_score.practice.releases import NO_ARRIVAL_S


class HeadClock:
    """When each moment of a recording first became readable, by the wall clock.

    Between samples it is interpolated: a replay is written at a steady rate."""

    def __init__(self, clock=time.monotonic, every_s: float = 0.2):
        self.clock, self.every_s = clock, every_s
        self.samples: list = []                     # (wall, head), head increasing
        self._lock, self._stop, self._thread = threading.Lock(), threading.Event(), None

    def sample(self, head: float, wall: float | None = None) -> None:
        wall = self.clock() if wall is None else wall
        with self._lock:
            if not self.samples or head > self.samples[-1][1]:
                self.samples.append((wall, head))

    def reached(self, t: float):
        with self._lock:
            s = list(self.samples)
        if s and t <= s[0][1]:
            return s[0][0]
        for (w0, h0), (w1, h1) in zip(s, s[1:]):
            if h0 <= t <= h1:
                return w0 + (w1 - w0) * (t - h0) / (h1 - h0)
        return None

    def start(self, rec):
        def run():
            while not self._stop.is_set():
                self.sample(rec.head_s())
                self._stop.wait(self.every_s)

        self._thread = threading.Thread(target=run, name="head-clock", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()


class Sink:
    """What the watch publishes: the latest document, and each throw once, with
    its latency, as a line of ``throws.jsonl``."""

    def __init__(self, out, heads, clock=time.monotonic, show=print):
        self.out, self.heads, self.clock, self.show = Path(out), heads, clock, show
        self.out.mkdir(parents=True, exist_ok=True)
        self.seen: set = set()

    def publish(self, doc) -> None:
        now, t0 = self.clock(), doc.get("t0_s", 0.0)
        with open(self.out / "throws.jsonl", "a") as fh:
            for th in doc["throws"]:
                if th["id"] in self.seen:
                    continue
                self.seen.add(th["id"])
                known = th["t_rest_s"] if th["arrived"] else th["t_release_s"] + NO_ARRIVAL_S
                at = self.heads.reached(known - t0)
                rec = dict(th, latency_s=None if at is None else round(now - at, 2))
                fh.write(json.dumps(rec) + "\n")
                rest = (th.get("rest") or {}).get("ring", "-")
                self.show(f"{th['id']} {th['house']:6} {th['color']:6} "
                          f"split {th.get('split_s')} line {th.get('line') and th['line']['miss_m']} "
                          f"rest {rest}  latency {rec['latency_s']} s")
        (self.out / "practice.json").write_text(json.dumps(doc))


def run(watch, *, sleep=time.sleep, idle_s: float = 0.25) -> None:
    """Step ``watch`` until it is done, resting whenever it has nothing to do."""
    while not watch.done:
        if not watch.step():
            sleep(idle_s)
```

- [ ] **Step 4: Add the command to the CLI**

In `src/curling_score/cli.py`, add after `_live_replay`:

```python
def _practice_replay(args) -> int:
    """A cached video watched as a practice session: the stream picked up a
    lookback before Start, each throw reported as it comes to rest, and how
    long after that each one was published."""
    from curling_score.live import replay, session as live
    from curling_score.practice import harness
    from curling_score.practice.pipeline import PracticePipeline
    from curling_score.practice.watch import PracticeWatch

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    weights = None if (args.weights or "").lower() in ("", "none", "classical") \
        else args.weights
    detector, broom_model, line_model = analyze_mod.load_models(
        weights, args.imgsz, args.device, skip_longview=args.no_longview,
        skip_line=args.no_line, progress=print)
    start = max(0.0, args.from_s - args.lookback)
    rec = replay.ReplayRecording(args.video, out / "recording.ts", speed=args.speed,
                                 start_s=start, end_s=args.to_s,
                                 burst_s=args.from_s - start).start()
    heads = harness.HeadClock().start(rec)
    sink = harness.Sink(out, heads)
    watch = PracticeWatch(
        recording=rec,
        pipeline=PracticePipeline(weights=weights, skip_longview=args.no_longview,
                                  line=line_model is not None, progress=print),
        models=live.Models(detector=detector, broom_model=broom_model,
                           line_model=line_model),
        since_s=args.from_s - rec.t0_s, t0_s=rec.t0_s, publish=sink.publish,
        progress=print)
    try:
        harness.run(watch)
    finally:
        rec.stop()
        heads.stop()
    print(f"{len(sink.seen)} throw(s); see {out / 'throws.jsonl'}")
    return 0
```

Register the parser right after `live-replay`'s `p.set_defaults(func=_live_replay)`:

```python
    p = sub.add_parser("practice-replay",
                       help="replay a cached video as a practice session, one throw at a time")
    p.add_argument("video", help="a cached video file")
    p.add_argument("--from", dest="from_s", type=float, required=True,
                   help="where the session starts (Start), in seconds into the video")
    p.add_argument("--to", dest="to_s", type=float, required=True,
                   help="where the replay stops, in seconds into the video")
    p.add_argument("--lookback", type=float, default=1200.0,
                   help="footage before Start to calibrate from, written at once (default: 1200)")
    p.add_argument("--speed", type=float, default=1.0,
                   help="playback speed against real time after the lookback (default: 1)")
    p.add_argument("--out", default="out/practice/run", help="output directory")
    p.add_argument("--weights", default=_default_weights(),
                   help="a trained YOLO model to detect with, or 'none'")
    p.add_argument("--imgsz", type=int, default=448)
    p.add_argument("--device", default=None)
    p.add_argument("--no-longview", action="store_true",
                   help="skip the side views (hog times, brooms, lines)")
    p.add_argument("--no-line", action="store_true",
                   help="skip measuring where each rock's line passed the broom")
    p.set_defaults(func=_practice_replay)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_practice_harness.py tests/test_cli_practice_replay.py tests/test_cli_live_replay.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Commit**

```bash
git add src/curling_score/practice/harness.py src/curling_score/cli.py tests/test_practice_harness.py tests/test_cli_practice_replay.py
git commit -m "cli: practice-replay -- a cached video watched as a session, with latency

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Scoring a replay against the per-end pipeline

**Files:**
- Create: `scripts/practice/compare_throws.py`
- Test: `tests/test_practice_compare.py`

**Interfaces:**
- Consumes: `throws.jsonl` lines (Task 8: Task 1's record plus `latency_s`), and a timeline document from `curling-score analyze` (`games[].ends[].house` and `shots[]` with `color, missing, reason, t_rest_s, t_release_s, stones, delivered_stone_index, long_split_s, target_broom, line.at_broom.miss_m`).
- Produces:
  - `compare_throws.reference(doc, t_from, t_to) -> list[dict]`;
  - `compare_throws.match(throws, refs) -> (pairs, false, missed)`;
  - `compare_throws.summarize(pairs, false, missed, throws) -> dict`;
  - `main(argv) -> int`, which prints the report and writes `--out` JSON.

- [ ] **Step 1: Write the failing tests**

`tests/test_practice_compare.py`:

```python
"""scripts/practice/compare_throws.py: a practice replay against the end pipeline."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "practice"))
import compare_throws as ct  # noqa: E402


def shot(color, t_rest, t_release=None, x=0.0, y=0.0, reason="rest", split=None,
         broom=True, miss=None):
    return {"color": color, "missing": False, "reason": reason, "t_rest_s": t_rest,
            "t_release_s": t_release, "stones": [{"color": color, "x": x, "y": y}],
            "delivered_stone_index": 0, "long_split_s": split,
            "target_broom": {"x": 0.0, "y": 0.0} if broom else None,
            "line": {"at_broom": {"miss_m": miss}} if miss is not None else None}


def doc(house, *shots):
    return {"games": [{"ends": [{"house": house, "shots": list(shots)}]}]}


def throw(color, t_rest, *, house="top", x=0.0, y=0.0, arrived=True, t_release=None,
          split=None, broom=True, miss=None, latency=10.0, source="overhead"):
    return {"id": f"t_{t_rest or t_release}", "color": color, "house": house,
            "t_rest_s": t_rest, "t_release_s": t_release, "arrived": arrived,
            "release_source": source,
            "rest": {"x": x, "y": y} if arrived else None, "split_s": split,
            "broom": {"x": 0.0, "y": 0.0} if broom else None,
            "line": {"miss_m": miss} if miss is not None else None, "latency_s": latency}


def test_reference_keeps_the_window_and_skips_placeholders():
    d = doc("top", shot("red", 120.0, t_release=100.0), shot("red", 400.0, t_release=380.0),
            {"missing": True, "color": "yellow"})
    refs = ct.reference(d, 50.0, 300.0)
    assert [(r["color"], r["t_rest_s"], r["house"]) for r in refs] == [("red", 120.0, "top")]


def test_a_throw_matches_a_shot_of_its_colour_house_time_and_place():
    refs = ct.reference(doc("top", shot("red", 120.0, t_release=100.0, x=0.1, y=-0.4)),
                        0.0, 1000.0)
    pairs, false, missed = ct.match([throw("red", 121.5, x=0.15, y=-0.38)], refs)
    assert len(pairs) == 1 and false == [] and missed == []
    pairs, false, missed = ct.match([throw("red", 121.5, x=1.5, y=1.0)], refs)
    assert pairs == [] and len(false) == 1 and len(missed) == 1
    pairs, _, _ = ct.match([throw("red", 121.5, house="bottom")], refs)
    assert pairs == []


def test_a_throw_that_never_arrived_matches_a_hogged_shot_by_its_release():
    refs = ct.reference(doc("top", shot("yellow", None, t_release=100.0, reason="hogged")),
                        0.0, 1000.0)
    pairs, _, _ = ct.match([throw("yellow", None, arrived=False, t_release=101.0)], refs)
    assert len(pairs) == 1


def test_the_summary_counts_and_measures():
    refs = ct.reference(doc("top",
                            shot("red", 120.0, t_release=100.0, x=0.1, y=-0.4, split=13.8,
                                 miss=-0.10),
                            shot("yellow", 180.0, t_release=160.0, broom=False)), 0.0, 1000.0)
    ths = [throw("red", 120.5, x=0.13, y=-0.40, split=13.85, miss=-0.12, latency=12.0),
           throw("red", 300.0, x=-1.0, y=1.0, latency=20.0, source=None)]
    pairs, false, missed = ct.match(ths, refs)
    s = ct.summarize(pairs, false, missed, ths)
    assert s["reference_arrived"] == 2 and s["matched_arrived"] == 1
    assert s["recall_arrived"] == 0.5 and s["false_throws"] == 1
    assert s["rest_cm"]["p50"] == 3.0
    assert s["split_s"]["p50"] == 0.05
    assert s["miss_cm"]["p50"] == 2.0
    assert s["broom"] == {"both": 1, "neither": 0, "throw_only": 0, "reference_only": 0}
    assert s["latency_s"] == {"n": 2, "p50": 12.0, "p90": 20.0, "max": 20.0}
    assert s["unreleased"] == {"n": 1, "matched": 0}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_practice_compare.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'compare_throws'`

- [ ] **Step 3: Write the script**

`scripts/practice/compare_throws.py`:

```python
"""How a practice replay's throws compare with the end pipeline's shots.

    python scripts/practice/compare_throws.py out/practice/vxu9/throws.jsonl \
        out/practice/ref-VXU9/timeline.json --from 2400 --to 4200

The reference is the per-end pipeline's timeline of the same video (`curling-
score analyze`). A throw matches the reference shot of its colour and house
that came to rest within DT_S of it and within POS_M of where it did; a throw
that never arrived matches a shot the pipeline settled as hogged, released
within DT_S. Shots thrown in the last TAIL_S before --to are left out of both
sides: the replay stops at --to, before they could be confirmed.
"""

import argparse
import json
import math
import sys
from pathlib import Path

DT_S = 3.0
POS_M = 0.5
TAIL_S = 45.0
# A shot with no release is dated this long before its rest.
REST_TO_THROW_S = 20.0


def _pct(values, q):
    v = sorted(values)
    if not v:
        return None
    return round(v[min(len(v) - 1, math.ceil(q * len(v)) - 1)], 3)


def _stats(values) -> dict:
    return {"n": len(values), "p50": _pct(values, 0.5), "p90": _pct(values, 0.9),
            "max": None if not values else round(max(values), 3)}


def _thrown(t_release, t_rest):
    if t_release is not None:
        return t_release
    return None if t_rest is None else t_rest - REST_TO_THROW_S


def reference(doc, t_from, t_to) -> list:
    out = []
    for game in doc["games"]:
        for end in game["ends"]:
            for s in end["shots"]:
                if s.get("missing"):
                    continue
                thrown = _thrown(s.get("t_release_s"), s.get("t_rest_s"))
                if thrown is None or not t_from <= thrown <= t_to - TAIL_S:
                    continue
                idx, stones = s.get("delivered_stone_index"), s.get("stones") or []
                stone = stones[idx] if idx is not None and idx < len(stones) else None
                at_broom = (s.get("line") or {}).get("at_broom") or {}
                out.append({"house": end["house"], "color": s["color"],
                            "t_rest_s": s.get("t_rest_s"), "t_release_s": s.get("t_release_s"),
                            "arrived": s.get("reason") != "hogged",
                            "rest": None if stone is None else (stone["x"], stone["y"]),
                            "split_s": s.get("long_split_s"),
                            "broom": s.get("target_broom") is not None,
                            "miss_m": at_broom.get("miss_m")})
    return out


def _gap(th, r):
    if r["house"] != th["house"] or r["color"] != th["color"] or r["arrived"] != th["arrived"]:
        return None
    if th["arrived"]:
        if r["t_rest_s"] is None or abs(r["t_rest_s"] - th["t_rest_s"]) > DT_S:
            return None
        if r["rest"] and th["rest"] and math.dist(
                r["rest"], (th["rest"]["x"], th["rest"]["y"])) > POS_M:
            return None
        return abs(r["t_rest_s"] - th["t_rest_s"])
    if r["t_release_s"] is None or th["t_release_s"] is None:
        return None
    gap = abs(r["t_release_s"] - th["t_release_s"])
    return gap if gap <= DT_S else None


def match(throws, refs):
    pairs, used, matched = [], set(), set()
    for k, th in enumerate(throws):
        best = None
        for i, r in enumerate(refs):
            if i in used:
                continue
            gap = _gap(th, r)
            if gap is not None and (best is None or gap < best[0]):
                best = (gap, i)
        if best is not None:
            used.add(best[1])
            matched.add(k)
            pairs.append((th, refs[best[1]]))
    false = [th for k, th in enumerate(throws) if k not in matched]
    missed = [r for i, r in enumerate(refs) if i not in used]
    return pairs, false, missed


def summarize(pairs, false, missed, throws) -> dict:
    arrived = [(th, r) for th, r in pairs if th["arrived"]]
    n_ref = len(arrived) + sum(1 for r in missed if r["arrived"])
    rest = [100 * math.dist(r["rest"], (th["rest"]["x"], th["rest"]["y"]))
            for th, r in arrived if r["rest"] and th["rest"]]
    split = [abs(th["split_s"] - r["split_s"]) for th, r in arrived
             if th["split_s"] is not None and r["split_s"] is not None]
    miss = [100 * abs(th["line"]["miss_m"] - r["miss_m"]) for th, r in arrived
            if th.get("line") and th["line"].get("miss_m") is not None
            and r["miss_m"] is not None]
    broom = {"both": 0, "neither": 0, "throw_only": 0, "reference_only": 0}
    for th, r in arrived:
        mine = th.get("broom") is not None
        broom[{(True, True): "both", (False, False): "neither", (True, False): "throw_only",
               (False, True): "reference_only"}[(mine, r["broom"])]] += 1
    latency = [th["latency_s"] for th in throws if th.get("latency_s") is not None]
    unreleased = [th for th in throws if th.get("release_source") is None and th["arrived"]]
    paired_ids = {id(th) for th, _ in pairs}
    return {
        "reference_arrived": n_ref, "matched_arrived": len(arrived),
        "recall_arrived": None if not n_ref else round(len(arrived) / n_ref, 3),
        "reference_hogged": sum(1 for r in missed if not r["arrived"])
        + sum(1 for th, _ in pairs if not th["arrived"]),
        "matched_hogged": sum(1 for th, _ in pairs if not th["arrived"]),
        "false_throws": len(false), "throws": len(throws),
        "rest_cm": _stats(rest), "split_s": _stats(split), "miss_cm": _stats(miss),
        "broom": broom,
        "latency_s": _stats(latency),
        "unreleased": {"n": len(unreleased),
                       "matched": sum(1 for th in unreleased if id(th) in paired_ids)},
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("throws", help="throws.jsonl from curling-score practice-replay")
    p.add_argument("timeline", help="timeline.json from curling-score analyze")
    p.add_argument("--from", dest="t_from", type=float, required=True)
    p.add_argument("--to", dest="t_to", type=float, required=True)
    p.add_argument("--out", default=None, help="write the report here as JSON")
    a = p.parse_args(argv)
    throws = [json.loads(x) for x in Path(a.throws).read_text().splitlines() if x.strip()]
    throws = [th for th in throws
              if a.t_from <= (_thrown(th["t_release_s"], th["t_rest_s"]) or -1) <= a.t_to - TAIL_S]
    refs = reference(json.loads(Path(a.timeline).read_text()), a.t_from, a.t_to)
    pairs, false, missed = match(throws, refs)
    report = summarize(pairs, false, missed, throws)
    report["missed"] = missed
    report["false"] = [{k: th[k] for k in ("id", "house", "color", "t_rest_s", "t_release_s")}
                       for th in false]
    print(json.dumps({k: v for k, v in report.items() if k not in ("missed", "false")},
                     indent=2))
    if a.out:
        Path(a.out).write_text(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_practice_compare.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/practice/compare_throws.py tests/test_practice_compare.py
git commit -m "scripts: score a practice replay's throws against the end pipeline

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Measure on cached videos and report

No new code; this task produces the numbers phase 1 exists for. Run each replay alone: one 8 GB GPU, and the box OOMs under load. Outputs go under `out/practice/`, which is gitignored like the rest of `out/`.

**Files:**
- Create: `docs/superpowers/plans/2026-10-09-practice-phase1.results.md`

- [ ] **Step 1: Make the reference timeline at this branch's HEAD**

Run (cached video, no download; stop it if it starts fetching):

```bash
curling-score analyze https://www.youtube.com/watch?v=VXU9xwmugRg --out out/practice/ref-VXU9 2>&1 | tee out/practice/ref-VXU9.log
```

Expected: `wrote out/practice/ref-VXU9/timeline.json`, with 2 games.

- [ ] **Step 2: Pick two windows from it**

```bash
python - <<'EOF'
import json
d = json.load(open("out/practice/ref-VXU9/timeline.json"))
for g in d["games"]:
    print("game", g["index"], g["start_s"], g["end_s"])
    for e in g["ends"]:
        print("  end", e["number"], e["house"], e["start_s"], e["end_s"], len(e["shots"]))
EOF
```

Choose:
- **(a) A game window:** from game 0's end 2 `start_s` − 60 to that + 1800 (30 min, both directions, ~30 rocks with a reference).
- **(b) The warm-up before game 0:** from `max(600, game0.start_s − 1500)` to `game0.start_s − 30`. This is real practice with no reference. If the stream begins less than 10 minutes before the game, skip (b) and use `hOKZoeJNTpM` the same way (its timeline needs Step 1 run for it too).

Write both windows into the results file.

- [ ] **Step 3: Replay window (a) at real time**

```bash
curling-score practice-replay ~/.cache/curling_score/videos/VXU9xwmugRg.mp4 \
    --from <A_FROM> --to <A_TO> --out out/practice/vxu9-game 2>&1 | tee out/practice/vxu9-game.log
```

Expected: "calibrating", then one line per throw, each with a latency. It runs ~30 min.

- [ ] **Step 4: Score it**

```bash
python scripts/practice/compare_throws.py out/practice/vxu9-game/throws.jsonl \
    out/practice/ref-VXU9/timeline.json --from <A_FROM> --to <A_TO> \
    --out out/practice/vxu9-game/report.json
```

Expected: a JSON summary with `recall_arrived`, `false_throws`, `rest_cm`, `split_s`, `miss_cm`, `broom` and `latency_s`.

- [ ] **Step 5: Look at every miss and every false throw**

For each entry in `report.json`'s `missed` and `false`, save the full frame at the rest (or release + 3 s) and look at it:

```bash
ffmpeg -v error -ss <T> -i ~/.cache/curling_score/videos/VXU9xwmugRg.mp4 -frames:v 1 out/practice/vxu9-game/look-<T>.jpg
```

Read each image. Classify it as:
- the finder never confirmed it;
- deduped wrongly;
- paired with the wrong release;
- a returning or pushed stone;
- the reference was wrong.

Note the counts.

- [ ] **Step 6: Replay window (b), the warm-up, and hand-check it**

```bash
curling-score practice-replay ~/.cache/curling_score/videos/VXU9xwmugRg.mp4 \
    --from <B_FROM> --to <B_TO> --lookback <B_FROM> --out out/practice/vxu9-warmup 2>&1 | tee out/practice/vxu9-warmup.log
```

There is no reference timeline. Take 10 throws spread through `throws.jsonl`. For each:
- look at the overhead and long-camera frame at its `t_rest_s`, and at `t_release_s` + 3 s;
- decide whether it was a real delivery, with the right house and colour, and whether the rest ring is right.

Also scrub the log's quiet stretches for throws it never reported: save a frame every 20 s with `ffmpeg -vf fps=1/20` over the window and look for stones moving down the sheet with no throw near that time.

- [ ] **Step 7: Write the results file and commit it**

`docs/superpowers/plans/2026-10-09-practice-phase1.results.md` must contain:
- the windows;
- the machine (RTX A2000 laptop, not the Ryzen worker);
- the full `report.json` summary;
- the miss and false classification;
- the warm-up hand check (n real / n checked, misses found);
- latency p50/p90/max against the 15 s target;
- each Review Focus item and what the runs showed;
- one paragraph: what limits latency, read from the log timings (rest confirmation vs the long-camera passes vs decode lead).

```bash
git add docs/superpowers/plans/2026-10-09-practice-phase1.results.md
git commit -m "docs: practice phase 1 results -- recall and latency on cached videos

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 8: Report to the user and stop**

Give the user recall, false throws, rest/split/miss deltas and latency p50/p90 against the spec's targets (recall ≥ 90%, false ≤ ~1 in 50, rest ≤ 5 cm, split ≤ 0.1 s, p90 ≤ 15 s), and the biggest cause of each miss. **Do not start phase 2.** Phase 2's plan is written after the user has seen these numbers.
