# Game Clips Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a player says which rocks they threw in a game ("I played…"), clips of those rocks are cut from the game's cached video by a dedicated clips worker and play on the /shots report with the broom and lines drawn over them.

**Architecture:** The API queues a `ClipJob` (a queue of its own, apart from `Job`) carrying each rock's times, line and broom and the run's long-camera calibration. A fourth worker service (`WORKER_ROLE=clips`) reads all three main workers' caches read-only, cuts each rock with the practice cutter (`practice/clips.py`) and overlay (`practice/overlay.py`), uploads the clips, and completes the job with each rock's result. /shots reads the clip state beside each game's document, polls a light `/clips` route while any are on their way, and plays a clip under its rock's row with the practice player, moved into a shared module.

**Tech Stack:** Python 3.12, FastAPI, Firestore/GCS (MemoryRepo/MemoryStore in tests), ffmpeg via `practice/clips.cut`, React 19 + esbuild (`frontend/`), docker compose on the worker box.

**Spec:** `docs/superpowers/specs/2026-10-10-game-clips-design.md` (main 171bf7a, c77676a, and the revision committed with this plan).

## Global Constraints

- Work in a worktree: `.claude/worktrees/game-clips` on branch `game-clips` from main. Another session shares the main checkout; never commit there.
- Run Python tests from the worktree root as `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest …` (the venv's editable install points at the main checkout; `PYTHONPATH=src` makes the worktree's code win). Run only the files named; the whole suite OOMs on this box (exit 137).
- Frontend: in the worktree, `ln -s /home/tcuser/src/curling_score/frontend/node_modules frontend/node_modules` once, then `cd frontend && npm run build` (lint + build + build stamp). Commit the built `src/curling_score/service/static/site.js` and `frontend/.buildstamp.json` with the sources.
- `frontend/site/Shots.jsx` must contain no `toFixed` and no `Math.` (tests/test_viewer_js.py `TestTheShotsPage`): every number and word on it comes from `frontend/core/myshots.mjs`.
- Rock names are `<game>.<end>.<shot>` with `timeline.end_identity(end)` and `timeline.shot_identity(shot)`: the detected numbers, which grading and start trims keep in `id`.
- Clip reasons are exactly `no_release`, `no_camera`, `video_gone`, `cut_failed`.
- `clip` values: `false` on its way, `true` uploaded, `null` none coming.
- Clip storage key: `store.clip_key(video_id, run_id, rock_name)` = `runs/<video>/<run>/clips/<rock>.mp4`; signed links last `CLIP_URL_S` (3600 s).
- `GAME_CLIPS_ENABLED` (API env, default off) gates everything player-facing; `WORKER_ROLE=clips` selects the clips loop on a worker.
- Nothing in the clip routes or `service/gameclips.py` writes a `Run`.
- The clips worker never downloads anything and never writes into the main workers' caches.
- Comments and docstrings follow the repo: plain sentences saying why, not what.
- Commit messages: `area: what changed, in a sentence`, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **A graded chart that reordered rocks, or a start trim that renumbered ends:** clip names must follow the detected numbers (`id`), so a clip never plays under the wrong rock. Pinned by Task 4 `test_names_follow_the_detected_numbers`.
2. **A claim made while the game's stream is still live:** the job must wait for the run to be ready, not finish as `video_gone`. Pinned by Task 4 `test_a_live_run_waits` and Task 5 `test_the_claim_leaves_a_live_run_alone`.
3. **Saving the same play again, or reading the report again:** nothing is queued twice and no made clip is lost. Pinned by Task 6 `test_saving_the_same_rocks_again_queues_nothing` and `test_reading_again_queues_nothing`.
4. **Someone else, or a rock with no clip:** the link route answers 404. Pinned by Task 6 `test_the_link_is_the_owners_alone`.
5. **The clips worker's lease runs out mid-job (slow cuts):** the job is requeued, the late worker's complete gets 409 and is treated as lost, and the re-cut writes the same keys. Pinned by Task 5 `test_a_lapsed_lease_is_requeued_and_the_late_complete_refused` and Task 7 `test_a_complete_refused_as_lost_is_left_alone`.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/curling_score/timeline.py` (modify) | Rock record gains `t_hog_s`, `v_hog_m_s`, `t_far_s` |
| `src/curling_score/game/clips.py` (create) | Worker side: a game rock as a practice throw, side views from calibration, finding the video, cutting a list of rocks |
| `src/curling_score/service/records.py` (modify) | `ClipJob` record |
| `src/curling_score/service/repo.py`, `firestore_repo.py` (modify) | `clip_jobs` storage, the atomic take, export/import |
| `src/curling_score/service/gameclips.py` (create) | API side: rock specs from a document, a play's clip state, queueing, claiming, cleaning results, failing |
| `src/curling_score/service/api.py` (modify) | Flag, worker routes `/api/worker/clips/*`, player routes, queueing on save and on report read |
| `src/curling_score/service/worker.py` (modify) | `ApiClient` clip methods; `WORKER_ROLE=clips` in `main` |
| `src/curling_score/service/clipworker.py` (create) | The clips worker loop |
| `frontend/site/clip.jsx` (create) | The clip player with lines, shared by practice and My shots |
| `frontend/site/Practice.jsx`, `frontend/site/Shots.jsx`, `frontend/core/myshots.mjs` (modify) | Use the shared player; Clip button, status, polling; core words |
| `src/curling_score/service/static/site.css` (modify) | Clip styles unscoped from `#practice`; the clip row |
| `deploy/docker-compose.worker.yml`, `deploy/cloudrun.yaml` (modify) | The `clips` service; the API flag |
| `tests/test_timeline.py`, `tests/test_game_clips.py`, `tests/test_repo_contract.py`, `tests/test_gameclips.py`, `tests/test_game_clips_api.py`, `tests/test_clipworker.py`, `tests/test_viewer_js.py` | Tests |

---

### Task 1: The hog times a clip cuts at, on the game timeline

**Files:**
- Modify: `src/curling_score/timeline.py` (the per-shot loop in `build_end`, around lines 151-240)
- Test: `tests/test_timeline.py`

**Interfaces:**
- Produces: every shot record in a timeline end has `t_hog_s` (float, 2 dp, or None), `v_hog_m_s` (float, 3 dp, or None), `t_far_s` (float, 2 dp, or None). Task 4 copies them into rock specs; Task 2 reads them.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_timeline.py`:

```python
class TestTheHogTimesAClipCutsAt:
    """game/clips.py switches cameras at the far hog line and times the rock
    leaving the facing camera from the throwing one: both go on the rock."""

    def test_a_rock_carries_them(self):
        s = shot(1, "red", [])
        s.t_hog_s = 103.456
        s.v_hog_m_s = 3.2104
        s.far_crossing = split.FarCrossing(t=117.891)
        got = timeline.build_end(number=1, house="top", start_s=0.0, end_s=900.0,
                                 shots=[s])["shots"][0]
        assert (got["t_hog_s"], got["v_hog_m_s"], got["t_far_s"]) == (103.46, 3.21, 117.89)

    def test_unmeasured_they_are_null(self):
        got = timeline.build_end(number=1, house="top", start_s=0.0, end_s=900.0,
                                 shots=[shot(1, "red", [])])["shots"][0]
        assert (got["t_hog_s"], got["v_hog_m_s"], got["t_far_s"]) == (None, None, None)

    def test_a_far_crossing_with_no_time_is_null(self):
        s = shot(1, "red", [])
        s.far_crossing = split.FarCrossing(t=None)
        got = timeline.build_end(number=1, house="top", start_s=0.0, end_s=900.0,
                                 shots=[s])["shots"][0]
        assert got["t_far_s"] is None
```

`shot` and `split` are already imported at the top of `tests/test_timeline.py`.

- [ ] **Step 2: Run the test to see it fail**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_timeline.py -k HogTimes -v`
Expected: FAIL with `KeyError: 't_hog_s'`.

- [ ] **Step 3: Write the code**

In `build_end`, replace

```python
        sp = split.long_split(dv, t_hog=hogtime.crossing(s),
                              v_hog=hogtime.speed_at_hog(s),
                              far=fartime.crossing(s))
```

with

```python
        t_hog, v_hog, far = hogtime.crossing(s), hogtime.speed_at_hog(s), fartime.crossing(s)
        sp = split.long_split(dv, t_hog=t_hog, v_hog=v_hog, far=far)
```

and in the shot dict, directly after the `"release_source": (...)` entry, add:

```python
                # Where a clip of this rock switches cameras and how long the
                # facing camera keeps it (game/clips.py, practice/clips.py):
                # the throwing hog line from the side view and the speed
                # there, and the far hog line from the destination panel.
                "t_hog_s": None if t_hog is None else round(float(t_hog), 2),
                "v_hog_m_s": None if v_hog is None else round(float(v_hog), 3),
                "t_far_s": None if far is None or far.t is None else round(float(far.t), 2),
```

`SCHEMA_VERSION` stays 8: the fields are additive, and documents without them still load.

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_timeline.py tests/test_delivery.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/timeline.py tests/test_timeline.py
git commit -m "timeline: each rock carries its hog line times, where a clip of it cuts

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: A game rock's clip, on the worker (`game/clips.py`)

**Files:**
- Create: `src/curling_score/game/clips.py`
- Test: `tests/test_game_clips.py`

**Interfaces:**
- Consumes: `practice.clips.plan(throw, sideviews) -> ClipPlan | None`, `practice.clips.cut(plan, views, out_path, *, t0_s=0.0, …)`, `practice.overlay.overlay(throw, plan, sideviews) -> list | None`, `geometry.sideview.SideView`.
- Produces (used by Task 7 and Task 10):
  - `throw_from_rock(rock: dict) -> dict`, the practice throw record for a rock spec (see Task 4 for the spec's shape).
  - `sideviews_from_calibration(cal: dict | None) -> dict[str, SideView]`, the `left`/`right` views.
  - `find_video(video_id: str, roots) -> Path | None`.
  - `clip_rocks(rocks: list[dict], video: Path, sideviews: dict, workdir: Path, deliver, *, cut=practice_clips.cut, on_rock=None) -> dict[str, dict]`. `deliver(name, path) -> bool`; `on_rock(name, result)`; each result is `{"clip": True, "overlay"?: list}` or `{"clip": None, "reason": str}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_game_clips.py`:

```python
"""A claimed rock's clip, cut from the game's video (game/clips.py)."""

import subprocess
from pathlib import Path

import pytest

from curling_score import analyze
from curling_score.game import clips as gc
from curling_score.geometry.sideview import SideView

# The left camera with a lateral fit, the right without: the right's piece
# of a clip is cut but has no lines.
LEFT = SideView(rect=(0, 0, 810, 1080), tee_row=429.95, hog_row=520.0, centre_col=390.23,
                lat_px_per_m_at_tee=150.923, centre_line=(381.563, 0.008215))
RIGHT = SideView(rect=(1110, 0, 810, 1080), tee_row=431.0, hog_row=522.0)
CAL = analyze._side_calibration({"left": LEFT, "right": RIGHT})


def rock(**kw):
    """A rock spec as the API hands it over (service/gameclips.player_rocks)."""
    base = {"name": "0.3.5", "house": "top", "t_release_s": 1000.0, "t_rest_s": 1020.0,
            "t_enter_s": 1013.0, "t_hog_s": None, "v_hog_m_s": None, "t_far_s": None,
            "target_broom": {"x": 0.4, "y": 0.0, "seen": 3.0, "confidence": 0.9},
            "line": {"start": {"x": -0.1, "y": 38.2}, "at_hog": {"x": 0.12, "offset_m": 0.01},
                     "at_broom": {"x": 0.45, "miss_m": 0.05}}}
    base.update(kw)
    return base


def made(plan, views, out, **kw):
    Path(out).write_bytes(b"mp4")


class TestThrowFromRock:
    def test_every_field_the_practice_cutter_reads(self):
        t = gc.throw_from_rock(rock(t_hog_s=1003.1, v_hog_m_s=3.2, t_far_s=1015.5))
        assert t == {"id": "0.3.5", "house": "top", "t_release_s": 1000.0, "t_rest_s": 1020.0,
                     "arrived": True, "t_hog_s": 1003.1, "v_hog_m_s": 3.2, "t_far_s": 1015.5,
                     "broom": {"x": 0.4, "y": 0.0},
                     "line": {"at_hog_x": 0.12, "miss_m": 0.05, "aim_x": None,
                              "start": {"x": -0.1, "y": 38.2}}}

    def test_without_the_far_hog_time_the_arrival_stands_in(self):
        assert gc.throw_from_rock(rock())["t_far_s"] == 1013.0

    def test_a_broomless_line_is_aimed_at_the_tee(self):
        t = gc.throw_from_rock(rock(target_broom=None,
                                    line={"at_hog": {"x": 0.1}, "at_broom": None,
                                          "at_tee": {"x": -0.2}}))
        assert t["broom"] is None
        assert t["line"] == {"at_hog_x": 0.1, "miss_m": None, "aim_x": -0.2, "start": None}

    def test_no_line_and_never_arrived(self):
        t = gc.throw_from_rock(rock(line=None, t_enter_s=None))
        assert t["line"] is None and t["arrived"] is False and t["t_far_s"] is None


class TestSideviewsFromCalibration:
    def test_the_runs_record_comes_back_as_the_views(self):
        got = gc.sideviews_from_calibration({**CAL, "top": {"rect": [1, 2, 3, 4], "px_per_m": 50.0}})
        assert set(got) == {"left", "right"}
        assert got["left"].rect == LEFT.rect and got["left"].has_lateral
        assert got["left"].tee_row == pytest.approx(LEFT.tee_row, abs=0.01)
        assert got["left"].centre_line == pytest.approx(LEFT.centre_line, abs=1e-6)
        assert got["left"].lat_px_per_m_at_tee == pytest.approx(150.923)
        assert not got["right"].has_lateral

    def test_nothing_calibrated(self):
        assert gc.sideviews_from_calibration(None) == {}
        assert gc.sideviews_from_calibration({"top": {"rect": [0, 0, 1, 1]}}) == {}


class TestFindVideo:
    def test_the_first_cache_that_has_it(self, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        (a / "kept").mkdir(parents=True)
        (a / "kept" / "VID.ts").write_bytes(b"partial")
        (b / "videos").mkdir(parents=True)
        (b / "videos" / "VID.mp4").write_bytes(b"x")
        assert gc.find_video("VID", [a, b]) == b / "videos" / "VID.mp4"

    def test_nowhere(self, tmp_path):
        assert gc.find_video("VID", [tmp_path, tmp_path / "missing"]) is None


class TestClipRocks:
    VIEWS = {"left": LEFT, "right": RIGHT}

    def test_a_clip_is_cut_from_both_cameras_delivered_and_overlaid(self, tmp_path):
        cuts, sent = [], []

        def cut(plan, views, out, **kw):
            cuts.append((plan, views))
            Path(out).write_bytes(b"mp4")

        def deliver(name, path):
            sent.append((name, Path(path).read_bytes()))
            return True

        video = tmp_path / "v.mp4"
        got = gc.clip_rocks([rock()], video, self.VIEWS, tmp_path / "w", deliver, cut=cut)
        assert sent == [("0.3.5", b"mp4")]
        assert cuts[0][1] == {"left": (video, LEFT.rect), "right": (video, RIGHT.rect)}
        # Thrown to the top house: the right camera faces the thrower, then the
        # left one follows to rest. With no far hog time the switch is at the
        # arrival (1013), the end 1.5 s after rest.
        pieces = [(p.view, p.t0, p.t1) for p in cuts[0][0].pieces]
        assert pieces == [("right", 997.0, 1013.0), ("left", 1013.0, 1021.5)]
        overlay = got["0.3.5"]["overlay"]
        assert got["0.3.5"]["clip"] is True
        assert [(p["t0"], p["t1"]) for p in overlay] == [(0.0, 16.0), (16.0, 24.5)]
        assert "thrown" not in overlay[0] and "broom" in overlay[1]
        assert list((tmp_path / "w").iterdir()) == []

    def test_no_release_no_camera_and_a_failed_cut(self, tmp_path):
        def cut(plan, views, out, **kw):
            raise subprocess.CalledProcessError(1, "ffmpeg")

        got = gc.clip_rocks([rock(name="0.1.1", t_release_s=None), rock(name="0.1.3")],
                            tmp_path / "v.mp4", self.VIEWS, tmp_path / "w",
                            lambda n, p: True, cut=cut)
        assert got == {"0.1.1": {"clip": None, "reason": "no_release"},
                       "0.1.3": {"clip": None, "reason": "cut_failed"}}
        got = gc.clip_rocks([rock()], tmp_path / "v.mp4", {}, tmp_path / "w",
                            lambda n, p: True, cut=cut)
        assert got == {"0.3.5": {"clip": None, "reason": "no_camera"}}

    def test_a_clip_that_did_not_upload_is_no_clip(self, tmp_path):
        got = gc.clip_rocks([rock()], tmp_path / "v.mp4", self.VIEWS, tmp_path / "w",
                            lambda n, p: False, cut=made)
        assert got == {"0.3.5": {"clip": None, "reason": "cut_failed"}}

    def test_each_rock_is_reported_as_it_is_done(self, tmp_path):
        seen = []
        gc.clip_rocks([rock(name="0.1.1", t_release_s=None), rock(name="0.1.3")],
                      tmp_path / "v.mp4", self.VIEWS, tmp_path / "w", lambda n, p: True,
                      cut=made, on_rock=lambda n, r: seen.append((n, r["clip"])))
        assert seen == [("0.1.1", None), ("0.1.3", True)]

    def test_a_delivery_that_raises_stops_the_job_and_cleans_up(self, tmp_path):
        def deliver(name, path):
            raise RuntimeError("the job is not ours any more")

        with pytest.raises(RuntimeError):
            gc.clip_rocks([rock()], tmp_path / "v.mp4", self.VIEWS, tmp_path / "w", deliver,
                          cut=made)
        assert list((tmp_path / "w").iterdir()) == []
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_game_clips.py -q`
Expected: FAIL at collection, `ImportError: cannot import name 'clips' from 'curling_score.game'`.

- [ ] **Step 3: Write the module**

Create `src/curling_score/game/clips.py`:

```python
"""A claimed game rock's clip: the practice clip, cut from the game's video.

The clips worker (service/clipworker.py) is handed each rock as the game's
timeline has it (service/gameclips.player_rocks) and the long cameras as the
run calibrated them. A rock is turned into the practice throw record, so the
practice cutter plans its pieces -- the camera facing the thrower, then the one
behind -- and the practice overlay places the broom and lines in each.

A game's times are on its video's own clock, so the cut needs no offset. The
far hog line is the camera switch; timelines from before it was recorded
switch at the rock's arrival in the far panel instead, a little early.
"""

import logging
import subprocess
import time
from pathlib import Path

from curling_score.geometry.sideview import SideView
from curling_score.practice import clips as practice_clips
from curling_score.practice import overlay as practice_overlay

log = logging.getLogger(__name__)

SIDE_VIEWS = ("left", "right")


def throw_from_rock(rock: dict) -> dict:
    """``rock`` as the practice cutter and overlay read a throw."""
    line = rock.get("line") or {}
    broom = rock.get("target_broom")
    t_far = rock.get("t_far_s")
    if t_far is None:
        t_far = rock.get("t_enter_s")
    out_line = None
    if line:
        out_line = {"at_hog_x": (line.get("at_hog") or {}).get("x"),
                    "miss_m": (line.get("at_broom") or {}).get("miss_m"),
                    "aim_x": (line.get("at_tee") or {}).get("x"),
                    "start": line.get("start")}
    return {
        "id": rock["name"], "house": rock["house"],
        "t_release_s": rock.get("t_release_s"), "t_rest_s": rock.get("t_rest_s"),
        "arrived": rock.get("t_enter_s") is not None,
        "t_hog_s": rock.get("t_hog_s"), "v_hog_m_s": rock.get("v_hog_m_s"), "t_far_s": t_far,
        "broom": None if broom is None else {"x": broom["x"], "y": broom["y"]},
        "line": out_line,
    }


def sideviews_from_calibration(cal: dict | None) -> dict:
    """The long cameras as a run calibrated them -- the timeline's record of
    them (analyze._side_calibration) read back. A camera the record lacks is
    left out; one without a lateral fit can be cut from but not drawn on."""
    out = {}
    for name in SIDE_VIEWS:
        d = (cal or {}).get(name)
        if not d or "tee_row" not in d or "hog_row" not in d:
            continue
        line = d.get("centre_line")
        out[name] = SideView(
            rect=tuple(int(v) for v in d["rect"]), tee_row=float(d["tee_row"]),
            hog_row=float(d["hog_row"]), centre_col=d.get("centre_col"),
            lat_px_per_m_at_tee=d.get("lat_px_per_m_at_tee"),
            centre_line=None if line is None else (float(line[0]), float(line[1])))
    return out


def find_video(video_id: str, roots) -> Path | None:
    """The game's video in the first of ``roots`` (the main workers' caches)
    that has it whole: ``videos/<id>.mp4``, a downloaded VOD or a kept live
    recording. A partial recording under ``kept/`` is not used: where it
    starts on the stream's clock is not certain."""
    for root in roots:
        path = Path(root) / "videos" / f"{video_id}.mp4"
        if path.is_file():
            return path
    return None


def clip_rocks(rocks, video, sideviews, workdir, deliver, *, cut=practice_clips.cut,
               on_rock=None) -> dict:
    """Cut, deliver and overlay each rock's clip, one at a time.

    ``deliver(name, path) -> bool`` sends a cut clip on; the file is removed
    afterwards whatever happens. ``on_rock(name, result)`` hears of each rock
    as it is done -- the worker renews its lease there. A failed cut or
    upload costs that rock; an exception from ``deliver`` (the job is no
    longer ours) ends the lot."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    views = {name: (video, sv.rect) for name, sv in sideviews.items()}
    results = {}
    for rock in rocks:
        throw = throw_from_rock(rock)
        if throw["t_release_s"] is None:
            got = {"clip": None, "reason": "no_release"}
        else:
            plan = practice_clips.plan(throw, sideviews)
            got = ({"clip": None, "reason": "no_camera"} if plan is None
                   else _one(throw, plan, views, sideviews, workdir, deliver, cut))
        results[rock["name"]] = got
        if on_rock is not None:
            on_rock(rock["name"], got)
    return results


def _one(throw, plan, views, sideviews, workdir, deliver, cut) -> dict:
    out = workdir / f"{throw['id']}.mp4"
    began = time.monotonic()
    try:
        cut(plan, views, out)
    except (subprocess.SubprocessError, OSError) as exc:
        log.warning("clip %s was not cut: %s", throw["id"], exc)
        out.unlink(missing_ok=True)
        return {"clip": None, "reason": "cut_failed"}
    try:
        sent = bool(deliver(throw["id"], out))
    finally:
        out.unlink(missing_ok=True)
    if not sent:
        return {"clip": None, "reason": "cut_failed"}
    log.info("clip %s: %.1f s of video, cut in %.1f s", throw["id"],
             plan.end - plan.pieces[0].t0, time.monotonic() - began)
    try:
        lines = practice_overlay.overlay(throw, plan, sideviews)
    except Exception as exc:  # noqa: BLE001 - the clip stands without its lines
        log.warning("overlay %s failed: %r", throw["id"], exc)
        lines = None
    return {"clip": True, "overlay": lines} if lines else {"clip": True}
```

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_game_clips.py tests/test_practice_clips.py tests/test_practice_overlay.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/game/clips.py tests/test_game_clips.py
git commit -m "clips: a claimed game rock is cut and overlaid as a practice throw is

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The clip queue's record and storage

**Files:**
- Modify: `src/curling_score/service/records.py` (add `ClipJob` after `Play`)
- Modify: `src/curling_score/service/repo.py` (Protocol, `MemoryRepo.__init__`, new methods, `export_all`, `import_all`)
- Modify: `src/curling_score/service/firestore_repo.py` (collection name, methods, export/import, records import)
- Test: `tests/test_repo_contract.py` (append; runs against MemoryRepo, and against Firestore when `FIRESTORE_EMULATOR_HOST` is set)

**Interfaces:**
- Produces (used by Tasks 4-6, 10):
  - `records.ClipJob(id, play_id, user_id, source_id, run_id, video_id, state, created_at, run_after, spec={}, attempts=0, max_attempts=3, worker_id=None, lease_expires_at=None, finished_at=None, error=None, rocks={})`
  - `repo.put_clip_job(job)`, `get_clip_job(id) -> ClipJob | None`, `update_clip_job(id, **fields) -> ClipJob | None`, `clip_jobs_for_play(play_id) -> list[ClipJob]` (oldest first), `clip_jobs_in_state(state) -> list[ClipJob]` (oldest first), `take_clip_job(job_id, worker_id, now, lease_s) -> ClipJob | None` (atomic: only a queued job is taken; state running, attempts +1, lease set).
  - Export key `"clip_jobs"`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_repo_contract.py`, add `ClipJob` to the records import, add `"clip_jobs"` to the collection list `_firestore_repo` empties, and append:

```python
def clip_job(id="cj_1", play="p1", state="queued", seconds=0, **kw):
    return ClipJob(id=id, play_id=play, user_id="u", source_id="s", run_id="r", video_id="v",
                   state=state, created_at=at(seconds), run_after=at(seconds),
                   spec={"rocks": [{"name": "0.1.1"}]}, **kw)


class TestTheClipQueue:
    """Clip jobs (records.ClipJob): a queue of their own for the clips worker."""

    def test_a_queued_job_is_taken_once(self, repo):
        repo.put_clip_job(clip_job())
        got = repo.take_clip_job("cj_1", "clips", T0, 600.0)
        assert (got.state, got.worker_id, got.attempts) == ("running", "clips", 1)
        assert got.lease_expires_at == T0 + timedelta(seconds=600)
        assert repo.get_clip_job("cj_1").state == "running"
        assert repo.take_clip_job("cj_1", "other", T0, 600.0) is None
        assert repo.take_clip_job("nope", "clips", T0, 600.0) is None

    def test_by_play_and_by_state_oldest_first(self, repo):
        repo.put_clip_job(clip_job("cj_b", seconds=5))
        repo.put_clip_job(clip_job("cj_a"))
        repo.put_clip_job(clip_job("cj_c", play="p2", state="done"))
        assert [j.id for j in repo.clip_jobs_for_play("p1")] == ["cj_a", "cj_b"]
        assert [j.id for j in repo.clip_jobs_in_state("queued")] == ["cj_a", "cj_b"]
        assert [j.id for j in repo.clip_jobs_in_state("done")] == ["cj_c"]

    def test_an_update_changes_only_what_it_names(self, repo):
        repo.put_clip_job(clip_job())
        got = repo.update_clip_job("cj_1", state="done", rocks={"0.1.1": {"clip": True}})
        assert (got.state, got.rocks, got.play_id) == ("done", {"0.1.1": {"clip": True}}, "p1")
        assert repo.update_clip_job("nope", state="done") is None

    def test_a_backup_keeps_them(self, repo):
        repo.put_clip_job(clip_job(rocks={"0.1.1": {"clip": True}}))
        fresh = MemoryRepo()
        fresh.import_all(repo.export_all())
        got = fresh.get_clip_job("cj_1")
        assert (got.play_id, got.state, got.rocks, got.spec) == \
            ("p1", "queued", {"0.1.1": {"clip": True}}, {"rocks": [{"name": "0.1.1"}]})
```

- [ ] **Step 2: Run them to see them fail**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_repo_contract.py -q -k ClipQueue`
Expected: FAIL at collection, `ImportError: cannot import name 'ClipJob'` (the Firestore half skips without the emulator).

- [ ] **Step 3: Write the record**

In `src/curling_score/service/records.py`, after the `Play` class, add:

```python
CLIP_JOB_STATES = ("queued", "running", "done", "failed")


@dataclass
class ClipJob:
    """Clips of some of one play's rocks, to be cut from one run's video by
    the clips worker (service/clipworker.py) -- the only thing that claims
    these. A queue of its own, apart from `Job`: a `Job` stands for its run,
    and the job routes write the run's status from it, where a clip job only
    reads the run it was cut for.

    ``spec`` is all the worker cuts from: ``{"calibration": {"left",
    "right"}, "rocks": [rock specs]}`` (service/gameclips.player_rocks), so
    it never loads a timeline. ``rocks`` is what came of each once done:
    name -> ``{"clip": True, "overlay"?}`` or ``{"clip": None, "reason"}``.
    The jobs are the record of a play's clips; nothing is written to the
    play, so saving it again never loses them.
    """

    id: str
    play_id: str
    user_id: str
    source_id: str
    run_id: str
    video_id: str
    state: str
    created_at: datetime
    run_after: datetime
    spec: dict = field(default_factory=dict)
    attempts: int = 0
    max_attempts: int = 3
    worker_id: str | None = None
    lease_expires_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    rocks: dict = field(default_factory=dict)

    to_dict = asdict
    from_dict = classmethod(_from_dict)
```

- [ ] **Step 4: Write the memory repo**

In `src/curling_score/service/repo.py`:

1. Add `ClipJob` to the `from curling_score.service.records import (...)` list.
2. In the `Repo` Protocol, after the `# plays` block, add:

```python
    # clip jobs -- clips of a play's rocks, a queue of their own (service/gameclips.py)
    def put_clip_job(self, job: ClipJob) -> None: ...
    def get_clip_job(self, job_id: str) -> ClipJob | None: ...
    def update_clip_job(self, job_id: str, **fields) -> ClipJob | None: ...
    def clip_jobs_for_play(self, play_id: str) -> list[ClipJob]: ...
    def clip_jobs_in_state(self, state: str) -> list[ClipJob]: ...
    def take_clip_job(self, job_id: str, worker_id: str, now: datetime,
                      lease_s: float) -> ClipJob | None: ...
```

3. In `MemoryRepo.__init__`, after `self.plays: dict[str, Play] = {}`, add `self.clip_jobs: dict[str, ClipJob] = {}`.
4. After `MemoryRepo.plays_for_user`, add:

```python
    # ---- clip jobs ----------------------------------------------------
    def put_clip_job(self, job):
        with self._lock:
            self.clip_jobs[job.id] = job

    def get_clip_job(self, job_id):
        return self.clip_jobs.get(job_id)

    def update_clip_job(self, job_id, **fields):
        with self._lock:
            job = self.clip_jobs.get(job_id)
            if job is None:
                return None
            for k, v in fields.items():
                setattr(job, k, v)
            return job

    def clip_jobs_for_play(self, play_id):
        return sorted((j for j in self.clip_jobs.values() if j.play_id == play_id),
                      key=lambda j: j.created_at)

    def clip_jobs_in_state(self, state):
        return sorted((j for j in self.clip_jobs.values() if j.state == state),
                      key=lambda j: j.created_at)

    def take_clip_job(self, job_id, worker_id, now, lease_s):
        with self._lock:
            job = self.clip_jobs.get(job_id)
            if job is None or job.state != "queued":
                return None
            job.state, job.worker_id = "running", worker_id
            job.attempts += 1
            job.lease_expires_at = now + timedelta(seconds=lease_s)
            return job
```

5. In `MemoryRepo.export_all`, after the `"plays"` line, add `"clip_jobs": [j.to_dict() for j in self.clip_jobs.values()],`.
6. In `MemoryRepo.import_all`, after the plays loop, add:

```python
            for d in data.get("clip_jobs", []):
                self.put_clip_job(ClipJob.from_dict(d))
```

- [ ] **Step 5: Write the Firestore repo**

In `src/curling_score/service/firestore_repo.py`:

1. Add `ClipJob` to the records import, and after the line `FLAGS, PLAYLIST_INDEX, PLAYS = "flags", "yt_playlists", "plays"` add `CLIP_JOBS = "clip_jobs"`.
2. After `plays_for_user`, add:

```python
    # ---- clip jobs ----------------------------------------------------
    # One equality filter each, sorted here: no composite index to keep. The
    # queue is short -- a job per claimed game, done in seconds.
    def put_clip_job(self, job):
        self._col(CLIP_JOBS).document(job.id).set(job.to_dict())

    def get_clip_job(self, job_id):
        return self._get(CLIP_JOBS, job_id, ClipJob)

    def update_clip_job(self, job_id, **fields):
        if not self._col(CLIP_JOBS).document(job_id).get().exists:
            return None
        return self._update(CLIP_JOBS, job_id, ClipJob, fields)

    def clip_jobs_for_play(self, play_id):
        docs = [ClipJob.from_dict(d.to_dict())
                for d in self._where(CLIP_JOBS, "play_id", "==", play_id).stream()]
        return sorted(docs, key=lambda j: j.created_at)

    def clip_jobs_in_state(self, state):
        docs = [ClipJob.from_dict(d.to_dict())
                for d in self._where(CLIP_JOBS, "state", "==", state).stream()]
        return sorted(docs, key=lambda j: j.created_at)

    def take_clip_job(self, job_id, worker_id, now, lease_s):
        ref = self._col(CLIP_JOBS).document(job_id)

        @self._fs.transactional
        def _take(tx):
            snap = ref.get(transaction=tx)
            if not snap.exists:
                return None
            data = snap.to_dict()
            if data.get("state") != "queued":
                return None
            update = {"state": "running", "worker_id": worker_id,
                      "attempts": int(data.get("attempts", 0)) + 1,
                      "lease_expires_at": now + timedelta(seconds=lease_s)}
            tx.update(ref, update)
            return ClipJob.from_dict({**data, **update})

        return _take(self.db.transaction())
```

3. In `export_all`, after `"plays": dump(PLAYS),` add `"clip_jobs": dump(CLIP_JOBS),`; in `import_all`, after the plays loop, add:

```python
        for d in data.get("clip_jobs", []):
            self.put_clip_job(ClipJob.from_dict(d))
```

Check `timedelta` is imported at the top of `firestore_repo.py` (it is used by `claim_job`).

- [ ] **Step 6: Run the tests**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_repo_contract.py tests/test_service_core.py tests/test_practice_records.py tests/test_plays.py -q`
Expected: all pass (Firestore cases skip without the emulator).

- [ ] **Step 7: Commit**

```bash
git add src/curling_score/service/records.py src/curling_score/service/repo.py \
        src/curling_score/service/firestore_repo.py tests/test_repo_contract.py
git commit -m "service: a queue of clip jobs of its own, apart from the runs' jobs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: What a play's clips are, and asking for them (`service/gameclips.py`)

**Files:**
- Create: `src/curling_score/service/gameclips.py`
- Test: `tests/test_gameclips.py`

**Interfaces:**
- Consumes: Task 3's repo methods and `ClipJob`; `timeline.end_identity`, `timeline.shot_identity`, `timeline.slot_of`; `format_mod.of_document`; `slug.new_slug`, `slug.SHORT_BYTES`.
- Produces (used by Tasks 5, 6, 10):
  - `GAME_ROCK` (compiled regex for a rock name), `REASONS`, `LIVE_RETRY_S = 300.0`, `RETRY_S = 60.0`, `MAX_OVERLAY_BYTES = 16_000`.
  - `rock_name(game, end, shot) -> str`
  - `player_rocks(doc, color, slot) -> list[dict]`. Marks each of the player's shots in `doc` with `shot["rock"] = name`. Spec keys: `name`, `house`, `t_release_s`, `t_rest_s`, `t_enter_s`, `t_hog_s`, `v_hog_m_s`, `t_far_s`, `target_broom`, `line` (only `start`, `at_hog`, `at_broom`, `at_tee`, or None).
  - `state_for(repo, play_id, run_id) -> {"rocks": {name: {...}}, "pending": bool}`
  - `ensure(repo, play, run, rocks, calibration, now) -> dict` (the state after queueing)
  - `claim(repo, worker_id, now, lease_s) -> ClipJob | None`
  - `clean_results(job, got) -> dict`
  - `fail(repo, job, error, now) -> bool` (True when final)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gameclips.py`:

```python
"""A play's clips on the API side (service/gameclips.py): which rocks, what
has come of them, queueing them once, and handing them to the clips worker."""

from datetime import datetime, timedelta, timezone

from curling_score.service import gameclips
from curling_score.service.records import ClipJob, Play, Run
from curling_score.service.repo import MemoryRepo

T = datetime(2026, 10, 10, 18, 0, tzinfo=timezone.utc)
CAL = {"left": {"rect": [0, 0, 810, 1080], "tee_row": 430.0, "hog_row": 520.0},
       "right": {"rect": [1110, 0, 810, 1080], "tee_row": 431.0, "hog_row": 522.0},
       "top": {"rect": [810, 0, 300, 540], "px_per_m": 50.0}}


def shot(n, color, slot, **kw):
    s = {"number": n, "color": color, "thrower_slot": slot, "missing": False,
         "t_release_s": 100.0 * n, "t_rest_s": 100.0 * n + 20, "t_enter_s": 100.0 * n + 13,
         "t_hog_s": None, "v_hog_m_s": None, "t_far_s": None,
         "target_broom": {"x": 0.3, "y": 0.0, "seen": 2.0, "confidence": 0.9},
         "line": {"start": {"x": 0.1, "y": 38.0}, "at_hog": {"x": 0.2, "offset_m": 0.0},
                  "at_broom": {"x": 0.35, "miss_m": 0.05}, "path": [[1.0, 2.0]],
                  "delivery": [[0.0, 38.0, 0.1]]},
         "stones": [], "track": [[1.0, 0.0, 1.0]]}
    s.update(kw)
    return s


def a_doc(shots, end_id=None):
    end = {"number": 2, "house": "bottom", "shots": shots}
    if end_id is not None:
        end["id"] = end_id
    return {"schema_version": 8, "calibration": CAL, "games": [{"index": 1, "ends": [end]}]}


def world(status="ready"):
    repo = MemoryRepo()
    run = Run(id="r1", video_id="VID", processing_version="p", status=status, created_at=T)
    repo.put_run(run)
    play = Play(id="p1", user_id="u1", source_id="s1", color="red", slot=4,
                created_at=T, updated_at=T)
    return repo, run, play


def queued(repo, rocks, *, state="queued", results=None, at=T, run_id="r1", id="cj_x"):
    repo.put_clip_job(ClipJob(id=id, play_id="p1", user_id="u1", source_id="s1",
                              run_id=run_id, video_id="VID", state=state, created_at=at,
                              run_after=at, spec={"rocks": [{"name": n} for n in rocks]},
                              rocks=results or {}))


class TestPlayerRocks:
    def test_the_players_rocks_with_what_a_clip_needs(self):
        doc = a_doc([shot(13, "red", 4), shot(14, "yellow", 4), shot(15, "red", 4),
                     shot(11, "red", 3)])
        got = gameclips.player_rocks(doc, "red", 4)
        assert [r["name"] for r in got] == ["1.2.13", "1.2.15"]
        assert got[0] == {"name": "1.2.13", "house": "bottom", "t_release_s": 1300.0,
                          "t_rest_s": 1320.0, "t_enter_s": 1313.0, "t_hog_s": None,
                          "v_hog_m_s": None, "t_far_s": None,
                          "target_broom": {"x": 0.3, "y": 0.0, "seen": 2.0, "confidence": 0.9},
                          "line": {"start": {"x": 0.1, "y": 38.0},
                                   "at_hog": {"x": 0.2, "offset_m": 0.0},
                                   "at_broom": {"x": 0.35, "miss_m": 0.05}}}
        shots = doc["games"][0]["ends"][0]["shots"]
        assert [s.get("rock") for s in shots] == ["1.2.13", None, "1.2.15", None]

    def test_names_follow_the_detected_numbers(self):
        # Grading moved rock 15 ahead and a start trim renumbered the end:
        # the clip stays with the rock the run detected.
        doc = a_doc([shot(13, "red", 4, id=15), shot(15, "red", 4, id=13)], end_id=5)
        assert [r["name"] for r in gameclips.player_rocks(doc, "red", 4)] == ["1.5.15", "1.5.13"]

    def test_a_rock_never_seen_or_without_a_line(self):
        doc = a_doc([shot(13, "red", 4, missing=True), shot(15, "red", 4, line=None)])
        got = gameclips.player_rocks(doc, "red", 4)
        assert [(r["name"], r["line"]) for r in got] == [("1.2.15", None)]


class TestStateFor:
    def test_on_its_way_made_none_coming_and_failed(self):
        repo, _, _ = world()
        queued(repo, ["1.2.13"], state="done", id="cj_a",
               results={"1.2.13": {"clip": True, "overlay": [{"t0": 0.0, "t1": 1.0}]}})
        queued(repo, ["1.2.15"], state="running", id="cj_b", at=T + timedelta(seconds=1))
        queued(repo, ["1.2.11"], state="failed", id="cj_c", at=T + timedelta(seconds=2))
        queued(repo, ["1.2.9"], state="done", id="cj_d", at=T + timedelta(seconds=3),
               results={"1.2.9": {"clip": None, "reason": "video_gone"}})
        got = gameclips.state_for(repo, "p1", "r1")
        assert got == {"rocks": {"1.2.13": {"clip": True, "overlay": [{"t0": 0.0, "t1": 1.0}]},
                                 "1.2.15": {"clip": False},
                                 "1.2.11": {"clip": None, "reason": "cut_failed"},
                                 "1.2.9": {"clip": None, "reason": "video_gone"}},
                       "pending": True}

    def test_only_this_run_and_a_made_clip_stays_made(self):
        repo, _, _ = world()
        queued(repo, ["1.2.13"], state="done", id="cj_a", results={"1.2.13": {"clip": True}})
        queued(repo, ["1.2.13"], state="queued", id="cj_b", at=T + timedelta(seconds=1))
        queued(repo, ["1.2.15"], state="queued", id="cj_c", run_id="r0")
        assert gameclips.state_for(repo, "p1", "r1") == {"rocks": {"1.2.13": {"clip": True}},
                                                          "pending": False}


class TestEnsure:
    def test_queues_the_rocks_once(self):
        repo, run, play = world()
        rocks = gameclips.player_rocks(a_doc([shot(13, "red", 4), shot(15, "red", 4)]), "red", 4)
        got = gameclips.ensure(repo, play, run, rocks, CAL, T)
        assert got == {"rocks": {"1.2.13": {"clip": False}, "1.2.15": {"clip": False}},
                       "pending": True}
        (job,) = repo.clip_jobs_for_play("p1")
        assert (job.state, job.run_id, job.video_id, job.user_id) == ("queued", "r1", "VID", "u1")
        assert set(job.spec["calibration"]) == {"left", "right"}
        assert [r["name"] for r in job.spec["rocks"]] == ["1.2.13", "1.2.15"]
        gameclips.ensure(repo, play, run, rocks, CAL, T + timedelta(seconds=5))
        assert len(repo.clip_jobs_for_play("p1")) == 1

    def test_only_rocks_with_nothing_yet(self):
        repo, run, play = world()
        queued(repo, ["1.2.13"], state="done", results={"1.2.13": {"clip": True}})
        rocks = gameclips.player_rocks(a_doc([shot(13, "red", 4), shot(15, "red", 4)]), "red", 4)
        gameclips.ensure(repo, play, run, rocks, CAL, T)
        newest = repo.clip_jobs_for_play("p1")[-1]
        assert [r["name"] for r in newest.spec["rocks"]] == ["1.2.15"]


class TestClaim:
    def test_oldest_first_and_taken(self):
        repo, _, _ = world()
        queued(repo, ["1.2.13"], id="cj_b", at=T + timedelta(seconds=1))
        queued(repo, ["1.2.15"], id="cj_a")
        got = gameclips.claim(repo, "clips", T + timedelta(seconds=2), 600.0)
        assert (got.id, got.state, got.worker_id) == ("cj_a", "running", "clips")
        assert repo.get_run("r1").status == "ready"

    def test_a_live_run_waits(self):
        repo, _, _ = world(status="live")
        queued(repo, ["1.2.13"])
        assert gameclips.claim(repo, "clips", T, 600.0) is None
        job = repo.get_clip_job("cj_x")
        assert job.state == "queued"
        assert job.run_after == T + timedelta(seconds=gameclips.LIVE_RETRY_S)
        repo.update_run("r1", status="ready")
        assert gameclips.claim(repo, "clips", T + timedelta(seconds=301), 600.0).id == "cj_x"
        assert repo.get_run("r1").status == "ready"

    def test_not_before_its_time(self):
        repo, _, _ = world()
        queued(repo, ["1.2.13"], at=T + timedelta(seconds=60))
        assert gameclips.claim(repo, "clips", T, 600.0) is None

    def test_a_run_that_is_gone_fails_its_jobs(self):
        repo, _, _ = world()
        queued(repo, ["1.2.13"], run_id="r_gone")
        assert gameclips.claim(repo, "clips", T, 600.0) is None
        assert repo.get_clip_job("cj_x").state == "failed"

    def test_a_lapsed_lease_goes_back_and_fails_at_the_last_attempt(self):
        repo, _, _ = world()
        queued(repo, ["1.2.13"])
        gameclips.claim(repo, "clips", T, 600.0)
        later = T + timedelta(seconds=601)
        assert gameclips.claim(repo, "clips", later, 600.0).attempts == 2
        repo.update_clip_job("cj_x", attempts=3, lease_expires_at=T)
        assert gameclips.claim(repo, "clips", later, 600.0) is None
        assert repo.get_clip_job("cj_x").state == "failed"


class TestResults:
    def test_kept_to_the_rocks_asked_and_the_shapes_the_page_reads(self):
        job = ClipJob(id="cj_x", play_id="p1", user_id="u1", source_id="s1", run_id="r1",
                      video_id="VID", state="running", created_at=T, run_after=T,
                      spec={"rocks": [{"name": "1.2.13"}, {"name": "1.2.15"}, {"name": "1.2.9"},
                                      {"name": "1.2.7"}]})
        got = gameclips.clean_results(job, {
            "1.2.13": {"clip": True, "overlay": [{"t0": 0.0, "t1": 2.0}], "extra": 1},
            "1.2.15": {"clip": None, "reason": "video_gone"},
            "1.2.9": {"clip": True, "overlay": [{"t0": 0.0, "pts": "x" * 20_000}]},
            "1.2.99": {"clip": True}})
        assert got == {"1.2.13": {"clip": True, "overlay": [{"t0": 0.0, "t1": 2.0}]},
                       "1.2.15": {"clip": None, "reason": "video_gone"},
                       "1.2.9": {"clip": True},
                       "1.2.7": {"clip": None, "reason": "cut_failed"}}
        assert gameclips.clean_results(job, {"1.2.13": {"clip": None, "reason": "nonsense"}})[
            "1.2.13"] == {"clip": None, "reason": "cut_failed"}

    def test_a_failure_is_tried_again_then_given_up(self):
        repo, _, _ = world()
        queued(repo, ["1.2.13"])
        job = gameclips.claim(repo, "clips", T, 600.0)
        assert gameclips.fail(repo, job, "boom", T) is False
        again = repo.get_clip_job("cj_x")
        assert (again.state, again.run_after) == ("queued", T + timedelta(seconds=gameclips.RETRY_S))
        repo.update_clip_job("cj_x", attempts=3)
        assert gameclips.fail(repo, repo.get_clip_job("cj_x"), "boom", T) is True
        assert repo.get_clip_job("cj_x").state == "failed"
```

- [ ] **Step 2: Run them to see them fail**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_gameclips.py -q`
Expected: FAIL, `ImportError: cannot import name 'gameclips'`.

- [ ] **Step 3: Write the module**

Create `src/curling_score/service/gameclips.py`:

```python
"""Clips of the rocks a person says they threw in a game, on the API side.

Saying which rocks were yours, or opening your report, asks for clips of
them: a `ClipJob` carrying each rock as the game's timeline has it and the
long cameras' calibration, so the clips worker (service/clipworker.py) needs
nothing else. What has come of them is read back from the play's clip jobs on
the run the report reads; nothing is written to the play or the run.

A rock is named ``<game>.<end>.<shot>`` by the numbers the run detected,
which grading and a start trim keep in ``id``: a clip stays with its rock
whatever order the chart puts the end in.
"""

import json
import re
from datetime import timedelta

from curling_score import timeline
from curling_score.game import format as format_mod
from curling_score.service import slug
from curling_score.service.records import ClipJob

GAME_ROCK = re.compile(r"^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$")
REASONS = ("no_release", "no_camera", "video_gone", "cut_failed")
# A game still being played is recorded until its stream ends, and only then
# is its video in a cache: ask again this much later.
LIVE_RETRY_S = 300.0
# A clip job that failed is tried again this much later.
RETRY_S = 60.0
# An overlay is ~1 KB a rock; one far bigger is not one.
MAX_OVERLAY_BYTES = 16_000

_ROCK_KEYS = ("t_release_s", "t_rest_s", "t_enter_s", "t_hog_s", "v_hog_m_s", "t_far_s",
              "target_broom")
# What the cutter and overlay read of a line; its paths are left behind.
_LINE_KEYS = ("start", "at_hog", "at_broom", "at_tee")


def rock_name(game: dict, end: dict, shot: dict) -> str:
    return f"{game['index']}.{timeline.end_identity(end)}.{timeline.shot_identity(shot)}"


def player_rocks(doc: dict, color: str, slot: int) -> list[dict]:
    """What a clip of each rock one player threw is cut from, from ``doc`` --
    graded, and not yet cut down by `timeline.for_player`. Each of the
    player's rocks in ``doc`` is marked with its name as ``rock``, which
    for_player keeps, so the report can find its clip. A rock never seen has
    nothing to cut."""
    fmt = format_mod.of_document(doc)
    out = []
    for game in doc.get("games", []):
        for end in game.get("ends", []):
            for s in end.get("shots") or []:
                if (s.get("color") != color or timeline.slot_of(s, fmt) != slot
                        or s.get("missing")):
                    continue
                name = rock_name(game, end, s)
                s["rock"] = name
                line = s.get("line") or {}
                kept = {k: line[k] for k in _LINE_KEYS if k in line}
                out.append({"name": name, "house": end.get("house"),
                            **{k: s.get(k) for k in _ROCK_KEYS}, "line": kept or None})
    return out


def state_for(repo, play_id: str, run_id: str) -> dict:
    """What has come of this play's clips on ``run_id``, from its clip jobs
    there, oldest first: a queued or running job's rocks are on their way, a
    finished one's are its results, a failed one's failed. A rock once made
    stays made."""
    rocks = {}
    jobs = [j for j in repo.clip_jobs_for_play(play_id) if j.run_id == run_id]
    for job in sorted(jobs, key=lambda j: j.created_at):
        for r in job.spec.get("rocks", []):
            name = r["name"]
            if (rocks.get(name) or {}).get("clip") is True:
                continue
            if job.state in ("queued", "running"):
                rocks[name] = {"clip": False}
            elif job.state == "done":
                rocks[name] = job.rocks.get(name) or {"clip": None, "reason": "cut_failed"}
            else:
                rocks[name] = {"clip": None, "reason": "cut_failed"}
    return {"rocks": rocks, "pending": any(v.get("clip") is False for v in rocks.values())}


def ensure(repo, play, run, rocks: list[dict], calibration: dict | None, now) -> dict:
    """Ask for clips of those of ``rocks`` this play has nothing for on
    ``run`` yet -- none twice, and a failed one not again on that run -- and
    say where they all stand."""
    state = state_for(repo, play.id, run.id)
    todo = [r for r in rocks if r["name"] not in state["rocks"]]
    if not todo:
        return state
    sides = {k: v for k, v in (calibration or {}).items() if k in ("left", "right")}
    repo.put_clip_job(ClipJob(
        id=slug.new_slug(slug.SHORT_BYTES, "cj_"), play_id=play.id, user_id=play.user_id,
        source_id=play.source_id, run_id=run.id, video_id=run.video_id, state="queued",
        created_at=now, run_after=now, spec={"calibration": sides, "rocks": todo}))
    return state_for(repo, play.id, run.id)


def claim(repo, worker_id: str, now, lease_s: float):
    """The next clip job for the clips worker, or None.

    A lease that ran out puts its job back (or fails it, at its last try). A
    job whose game is still live waits: its video reaches a cache only when
    the stream ends. A job whose run is gone fails. None of it touches a run.
    """
    for job in repo.clip_jobs_in_state("running"):
        if job.lease_expires_at is not None and job.lease_expires_at < now:
            if job.attempts >= job.max_attempts:
                repo.update_clip_job(job.id, state="failed", worker_id=None,
                                     lease_expires_at=None, finished_at=now,
                                     error="the clips worker stopped answering")
            else:
                repo.update_clip_job(job.id, state="queued", worker_id=None,
                                     lease_expires_at=None, run_after=now)
    ready = sorted((j for j in repo.clip_jobs_in_state("queued") if j.run_after <= now),
                   key=lambda j: (j.run_after, j.created_at))
    for job in ready:
        run = repo.get_run(job.run_id)
        if run is None:
            repo.update_clip_job(job.id, state="failed", finished_at=now, error="its run is gone")
            continue
        if run.status == "live":
            repo.update_clip_job(job.id, run_after=now + timedelta(seconds=LIVE_RETRY_S))
            continue
        got = repo.take_clip_job(job.id, worker_id, now, lease_s)
        if got is not None:
            return got
    return None


def clean_results(job, got) -> dict:
    """The worker's word on each rock the job asked for, in the shapes the
    page reads: a clip with its overlay, or none and why. Anything else it
    says is dropped; a rock it says nothing of failed."""
    out = {}
    for r in job.spec.get("rocks", []):
        name = r["name"]
        one = (got or {}).get(name)
        if isinstance(one, dict) and one.get("clip") is True:
            item = {"clip": True}
            lines = one.get("overlay")
            if isinstance(lines, list) and len(json.dumps(lines)) <= MAX_OVERLAY_BYTES:
                item["overlay"] = lines
            out[name] = item
        else:
            reason = one.get("reason") if isinstance(one, dict) else None
            out[name] = {"clip": None, "reason": reason if reason in REASONS else "cut_failed"}
    return out


def fail(repo, job, error: str, now) -> bool:
    """A clip job the worker could not finish: tried again shortly, or at its
    last try given up. True when it was given up."""
    if job.attempts >= job.max_attempts:
        repo.update_clip_job(job.id, state="failed", error=error, finished_at=now,
                             worker_id=None, lease_expires_at=None)
        return True
    repo.update_clip_job(job.id, state="queued", error=error, worker_id=None,
                         lease_expires_at=None, run_after=now + timedelta(seconds=RETRY_S))
    return False
```

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_gameclips.py tests/test_repo_contract.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/service/gameclips.py tests/test_gameclips.py
git commit -m "service: a play's clips, asked for once per rock and run, read back from its clip jobs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: The clips worker's routes

**Files:**
- Modify: `src/curling_score/service/api.py`: imports; a `GAME_CLIP` constant beside `PRACTICE_CLIP` (~line 122); new routes after `detcache_list` (~line 2406).
- Test: `tests/test_game_clips_api.py` (create)

**Interfaces:**
- Consumes: `gameclips.claim/clean_results/fail` (Task 4); Task 3's repo methods; `store.clip_key`, `CLIP_MAX_BYTES`.
- Produces (used by Task 7's `ApiClient`), all with the worker token:
  - `POST /api/worker/clips/claim {worker_id}` → 204, or `{"job": {"id", "run_id", "video_id", "spec", "attempt", "lease_s"}}`
  - `POST /api/worker/clips/{id}/progress {worker_id}` → `{"ok", "lease_expires_at"}`; 409 when not this worker's
  - `POST /api/worker/clips/{id}/artifacts {worker_id, files: [{name: "<rock>.mp4", bytes}]}` → `{"uploads": [{name, key, url, headers}]}`
  - `PUT /api/worker/clips/{id}/upload?key=` (the memory-store fallback)
  - `POST /api/worker/clips/{id}/complete {worker_id, rocks}` → `{"ok"}`
  - `POST /api/worker/clips/{id}/fail {worker_id, error}` → `{"ok", "final"}`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_game_clips_api.py`:

```python
"""Clips of the rocks a person says they threw: the clips worker's routes,
and the player's (service/api.py, service/gameclips.py)."""

from datetime import timedelta

from curling_score.service.records import ClipJob, play_id
from tests.test_plays import a_played_game, get, put  # noqa: F401
from tests.test_service_api import T0, VID, WORKER
from tests.test_service_auth import ALEX, SARAH, w  # noqa: F401  (fixtures)

W = "ryzen-clips"


def a_clip_job(w, src, rocks=("0.1.13", "0.1.15"), state="queued"):  # noqa: F811
    run = w["repo"].get_run(w["repo"].get_source(src).current_run_id)
    job = ClipJob(id="cj_1", play_id="p1", user_id="uid-sarah", source_id=src, run_id=run.id,
                  video_id=run.video_id, state=state, created_at=T0, run_after=T0,
                  spec={"calibration": {}, "rocks": [{"name": n, "house": "top"} for n in rocks]})
    w["repo"].put_clip_job(job)
    return job, run


def wpost(w, path, body):  # noqa: F811
    return w["client"].post(path, json={"worker_id": W, **body}, headers=WORKER)


def claim(w):  # noqa: F811
    return w["client"].post("/api/worker/clips/claim", json={"worker_id": W}, headers=WORKER)


class TestTheClipsWorkersRoutes:
    def test_needs_the_worker_token(self, w):  # noqa: F811
        assert w["client"].post("/api/worker/clips/claim", json={}).status_code in (401, 403)

    def test_nothing_queued(self, w):  # noqa: F811
        assert claim(w).status_code == 204

    def test_a_claim_hands_over_the_spec_and_leaves_the_run_alone(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        job, run = a_clip_job(w, src)
        before = (run.status, run.ready_at, run.error)
        r = claim(w)
        assert r.status_code == 200, r.text
        got = r.json()["job"]
        assert (got["id"], got["run_id"], got["video_id"], got["attempt"]) == \
            ("cj_1", run.id, VID, 1)
        assert [x["name"] for x in got["spec"]["rocks"]] == ["0.1.13", "0.1.15"]
        run = w["repo"].get_run(run.id)
        assert (run.status, run.ready_at, run.error) == before
        assert w["repo"].job_for_run(run.id).kind == "vod"

    def test_the_claim_leaves_a_live_run_alone(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        _, run = a_clip_job(w, src)
        w["repo"].update_run(run.id, status="live")
        assert claim(w).status_code == 204
        assert w["repo"].get_clip_job("cj_1").state == "queued"
        assert w["repo"].get_run(run.id).status == "live"

    def test_uploads_only_the_rocks_it_asked_for(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        _, run = a_clip_job(w, src)
        claim(w)
        r = wpost(w, "/api/worker/clips/cj_1/artifacts",
                  {"files": [{"name": "0.1.13.mp4", "bytes": 100}]})
        assert r.status_code == 200, r.text
        (up,) = r.json()["uploads"]
        assert up["key"] == f"runs/{VID}/{run.id}/clips/0.1.13.mp4"
        assert wpost(w, "/api/worker/clips/cj_1/artifacts",
                     {"files": [{"name": "0.1.14.mp4", "bytes": 100}]}).status_code == 400
        assert wpost(w, "/api/worker/clips/cj_1/artifacts",
                     {"files": [{"name": "0.1.13.mp4", "bytes": 30_000_000}]}).status_code == 400
        put_r = w["client"].put("/api/worker/clips/cj_1/upload", params={"key": up["key"]},
                                content=b"mp4", headers=WORKER)
        assert put_r.status_code == 200, put_r.text
        assert w["store"].get_bytes(up["key"]) == b"mp4"
        assert w["client"].put("/api/worker/clips/cj_1/upload",
                               params={"key": f"runs/{VID}/{run.id}/timeline.json"},
                               content=b"{}", headers=WORKER).status_code == 400

    def test_complete_keeps_the_results(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        _, run = a_clip_job(w, src)
        claim(w)
        r = wpost(w, "/api/worker/clips/cj_1/complete", {"rocks": {
            "0.1.13": {"clip": True, "overlay": [{"t0": 0.0, "t1": 3.0}]},
            "0.1.15": {"clip": None, "reason": "no_release"}}})
        assert r.status_code == 200, r.text
        job = w["repo"].get_clip_job("cj_1")
        assert job.state == "done" and job.finished_at is not None
        assert job.rocks == {"0.1.13": {"clip": True, "overlay": [{"t0": 0.0, "t1": 3.0}]},
                             "0.1.15": {"clip": None, "reason": "no_release"}}
        assert w["repo"].get_run(run.id).status == "ready"
        assert wpost(w, "/api/worker/clips/cj_1/complete", {"rocks": {}}).json()["already"]

    def test_progress_renews_the_lease(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        a_clip_job(w, src)
        claim(w)
        w["clock"].advance(500)
        assert wpost(w, "/api/worker/clips/cj_1/progress", {}).status_code == 200
        assert w["repo"].get_clip_job("cj_1").lease_expires_at == \
            w["clock"]() + timedelta(seconds=w["settings"].lease_s)

    def test_a_lapsed_lease_is_requeued_and_the_late_complete_refused(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        a_clip_job(w, src)
        claim(w)
        w["clock"].advance(w["settings"].lease_s + 1)
        r = w["client"].post("/api/worker/clips/claim", json={"worker_id": "other"},
                             headers=WORKER)
        assert r.json()["job"]["attempt"] == 2
        assert wpost(w, "/api/worker/clips/cj_1/complete", {"rocks": {}}).status_code == 409
        assert wpost(w, "/api/worker/clips/cj_1/progress", {}).status_code == 409

    def test_a_failure_goes_back_then_gives_up(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        _, run = a_clip_job(w, src)
        claim(w)
        r = wpost(w, "/api/worker/clips/cj_1/fail", {"error": "ffmpeg died"})
        assert r.json() == {"ok": True, "final": False}
        assert w["repo"].get_clip_job("cj_1").state == "queued"
        w["repo"].update_clip_job("cj_1", attempts=2)
        w["clock"].advance(61)
        claim(w)
        assert wpost(w, "/api/worker/clips/cj_1/fail", {"error": "again"}).json()["final"]
        assert w["repo"].get_clip_job("cj_1").state == "failed"
        assert w["repo"].get_run(run.id).status == "ready"
```

- [ ] **Step 2: Run them to see them fail**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_game_clips_api.py -q`
Expected: FAIL, 404s on `/api/worker/clips/claim`. (`test_needs_the_worker_token` may already pass, since an unknown route without a token is also refused.)

- [ ] **Step 3: Write the routes**

In `src/curling_score/service/api.py`:

1. Add `gameclips` to `from curling_score.service import (dedupe, playlist_index, playlists, practice, slug, …)` and `ClipJob` to the `from curling_score.service.records import (...)` list.
2. Under `CLIP_MAX_BYTES = 20_000_000` add:

```python
# A claimed game rock's clip, as the clips worker names the artifact
# (service/gameclips.py names the rock).
GAME_CLIP = re.compile(r"^([0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3})\.mp4$")
```

3. After the `detcache_list` route, add:

```python
    # ------------------------------------------------------------ clip jobs
    # The clips worker's own queue (service/gameclips.py): clips of the rocks
    # people say they threw. Nothing here writes a run -- a clip job only
    # reads the run it is cut from.
    def owned_clip_job(job_id: str, worker_id: str | None) -> ClipJob:
        job = repo.get_clip_job(job_id)
        if job is None:
            raise HTTPException(404, "no such clip job")
        if job.state != "running" or (worker_id and job.worker_id != worker_id):
            raise HTTPException(409, "this clip job is not yours any more")
        return job

    def clip_names(job: ClipJob) -> set:
        return {r["name"] for r in job.spec.get("rocks", [])}

    @app.post("/api/worker/clips/claim")
    async def clips_claim(request: Request, authorization: str | None = Header(default=None)):
        require_worker(authorization)
        body = await request.json()
        job = gameclips.claim(repo, str(body.get("worker_id", "clips")), now(), settings.lease_s)
        if job is None:
            return Response(status_code=204)
        return {"job": {"id": job.id, "run_id": job.run_id, "video_id": job.video_id,
                        "spec": job.spec, "attempt": job.attempts, "lease_s": settings.lease_s}}

    @app.post("/api/worker/clips/{job_id}/progress")
    async def clips_progress(job_id: str, request: Request,
                             authorization: str | None = Header(default=None)):
        require_worker(authorization)
        body = await request.json()
        job = owned_clip_job(job_id, body.get("worker_id"))
        lease = now() + timedelta(seconds=settings.lease_s)
        repo.update_clip_job(job.id, lease_expires_at=lease)
        return {"ok": True, "lease_expires_at": _iso(lease)}

    @app.post("/api/worker/clips/{job_id}/artifacts")
    async def clips_artifacts(job_id: str, request: Request,
                              authorization: str | None = Header(default=None)):
        require_worker(authorization)
        body = await request.json()
        job = owned_clip_job(job_id, body.get("worker_id"))
        asked = clip_names(job)
        uploads = []
        for f in body.get("files", []):
            name = str(f.get("name", ""))
            m = GAME_CLIP.match(name)
            if m is None or m.group(1) not in asked:
                raise HTTPException(400, f"{name} is not a clip this job asked for")
            if int(f.get("bytes") or 0) > CLIP_MAX_BYTES:
                raise HTTPException(400, f"{name} is over {CLIP_MAX_BYTES} bytes")
            key = clip_key(job.video_id, job.run_id, m.group(1))
            target = store.presign_put(key, "video/mp4")
            if target["url"].startswith("memory://"):
                # No real bucket behind this store: take the bytes ourselves.
                target = {"url": url_for(f"/api/worker/clips/{job.id}/upload?key={key}"),
                          "headers": {"Content-Type": "video/mp4"}}
            uploads.append({"name": name, "key": key, **target})
        return {"uploads": uploads}

    @app.put("/api/worker/clips/{job_id}/upload")
    async def clips_upload(job_id: str, request: Request, key: str = Query(...),
                           authorization: str | None = Header(default=None)):
        """The fallback upload path, used only when the store cannot sign URLs."""
        require_worker(authorization)
        job = repo.get_clip_job(job_id)
        if job is None or job.state != "running":
            raise HTTPException(409, "this clip job is not running")
        if key not in {clip_key(job.video_id, job.run_id, n) for n in clip_names(job)}:
            raise HTTPException(400, "that key does not belong to this clip job")
        data = await request.body()
        if len(data) > CLIP_MAX_BYTES:
            raise HTTPException(413, "that clip is too big")
        store.put_bytes(key, data, "video/mp4")
        return {"ok": True, "bytes": len(data)}

    @app.post("/api/worker/clips/{job_id}/complete")
    async def clips_complete(job_id: str, request: Request,
                             authorization: str | None = Header(default=None)):
        require_worker(authorization)
        body = await request.json()
        job = repo.get_clip_job(job_id)
        if job is None:
            raise HTTPException(404, "no such clip job")
        if job.state == "done":
            return {"ok": True, "already": True}
        job = owned_clip_job(job_id, body.get("worker_id"))
        repo.update_clip_job(job.id, state="done", finished_at=now(), lease_expires_at=None,
                             rocks=gameclips.clean_results(job, body.get("rocks")))
        return {"ok": True}

    @app.post("/api/worker/clips/{job_id}/fail")
    async def clips_fail(job_id: str, request: Request,
                         authorization: str | None = Header(default=None)):
        require_worker(authorization)
        body = await request.json()
        job = owned_clip_job(job_id, body.get("worker_id"))
        final = gameclips.fail(repo, job, str(body.get("error", ""))[:500], now())
        return {"ok": True, "final": final}
```


- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_game_clips_api.py tests/test_plays.py tests/test_practice_api.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/service/api.py tests/test_game_clips_api.py
git commit -m "api: the clips worker claims, uploads and completes clip jobs without touching a run

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: The player's side: asking for clips, reading them, a link to one

**Files:**
- Modify: `src/curling_score/service/api.py`: `Settings` and `from_env`; `api_save_play`; `api_my_play_doc`; new helpers and routes beside the plays routes.
- Test: `tests/test_game_clips_api.py` (append)

**Interfaces:**
- Consumes: `gameclips.player_rocks/ensure/state_for/GAME_ROCK` (Task 4); the worker routes (Task 5, in tests).
- Produces (used by Task 8):
  - `Settings.game_clips_enabled` (env `GAME_CLIPS_ENABLED`).
  - `GET /api/me/plays/{source_id}/doc` → `{"doc", "fallback", "clips"?}`. With clips on, the player's rocks in `doc` carry `rock`, and `clips` is `{"rocks": {name: {"clip", "reason"?, "overlay"?}}, "pending": bool}`.
  - `GET /api/me/plays/{source_id}/clips` → `{"clips": {...same...}}`
  - `GET /api/me/plays/{source_id}/clips/{rock}` → `{"url"}`
  - All three answer 404 with clips off, and the doc then carries no `clips` and no `rock`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_game_clips_api.py`:

```python
from tests.test_service_api import ADMIN, work_through  # noqa: E402
from tests.test_plays import played_doc  # noqa: E402

G = "/g/{}/"


def claimed(w, slot=4, color="red"):  # noqa: F811
    _, _, src = a_played_game(w)
    w["settings"].game_clips_enabled = True
    r = put(w, src, {"path": G.format(src), "color": color, "slot": slot})
    assert r.status_code == 200, r.text
    return src


def jobs(w, src):  # noqa: F811
    return w["repo"].clip_jobs_for_play(play_id("uid-sarah", src))


def worker_finishes(w, results):  # noqa: F811
    job = claim(w).json()["job"]
    assert wpost(w, f"/api/worker/clips/{job['id']}/complete",
                 {"rocks": results}).status_code == 200
    return job


class TestAskingForClips:
    def test_off_nothing_is_asked_and_nothing_shown(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        put(w, src, {"path": G.format(src), "color": "red", "slot": 4})
        assert jobs(w, src) == []
        body = get(w, f"/api/me/plays/{src}/doc").json()
        assert "clips" not in body
        assert all("rock" not in s for s in body["doc"]["games"][0]["ends"][0]["shots"])
        assert get(w, f"/api/me/plays/{src}/clips").status_code == 404
        assert get(w, f"/api/me/plays/{src}/clips/0.1.13").status_code == 404

    def test_saving_a_play_asks_for_its_rocks(self, w):  # noqa: F811
        src = claimed(w)
        (job,) = jobs(w, src)
        assert [r["name"] for r in job.spec["rocks"]] == ["0.1.13", "0.1.15"]
        assert job.run_id == w["repo"].get_source(src).current_run_id

    def test_saving_the_same_rocks_again_queues_nothing(self, w):  # noqa: F811
        src = claimed(w)
        worker_finishes(w, {"0.1.13": {"clip": True}, "0.1.15": {"clip": True}})
        put(w, src, {"path": G.format(src), "color": "red", "slot": 4})
        assert len(jobs(w, src)) == 1
        assert get(w, f"/api/me/plays/{src}/clips").json()["clips"]["pending"] is False

    def test_other_rocks_are_asked_for_and_the_old_clips_kept(self, w):  # noqa: F811
        src = claimed(w)
        worker_finishes(w, {"0.1.13": {"clip": True}, "0.1.15": {"clip": True}})
        put(w, src, {"color": "yellow", "slot": 4})
        assert [r["name"] for r in jobs(w, src)[-1].spec["rocks"]] == ["0.1.14", "0.1.16"]
        put(w, src, {"color": "red", "slot": 4})
        assert len(jobs(w, src)) == 2

    def test_the_report_marks_the_rocks_and_says_where_they_stand(self, w):  # noqa: F811
        src = claimed(w)
        body = get(w, f"/api/me/plays/{src}/doc").json()
        shots = body["doc"]["games"][0]["ends"][0]["shots"]
        assert [s.get("rock") for s in shots if s.get("rock")] == ["0.1.13", "0.1.15"]
        assert body["clips"] == {"rocks": {"0.1.13": {"clip": False}, "0.1.15": {"clip": False}},
                                 "pending": True}

    def test_reading_again_queues_nothing(self, w):  # noqa: F811
        src = claimed(w)
        get(w, f"/api/me/plays/{src}/doc")
        get(w, f"/api/me/plays/{src}/doc")
        assert len(jobs(w, src)) == 1

    def test_a_play_from_before_clips_is_asked_for_when_the_report_is_read(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        put(w, src, {"path": G.format(src), "color": "red", "slot": 4})
        w["settings"].game_clips_enabled = True
        assert get(w, f"/api/me/plays/{src}/doc").json()["clips"]["pending"] is True
        assert len(jobs(w, src)) == 1

    def test_a_reprocess_asks_again_on_the_new_run(self, w):  # noqa: F811
        src = claimed(w)
        worker_finishes(w, {"0.1.13": {"clip": True}, "0.1.15": {"clip": True}})
        first = w["repo"].get_source(src).current_run_id
        w["client"].post("/api/admin/reprocess", headers=ADMIN, json={"video_id": VID})
        work_through(w, doc=played_doc(), games=1)
        assert w["repo"].get_source(src).current_run_id != first
        clips = get(w, f"/api/me/plays/{src}/doc").json()["clips"]
        assert clips["pending"] is True
        assert jobs(w, src)[-1].run_id == w["repo"].get_source(src).current_run_id


class TestReadingClips:
    def test_the_state_as_the_worker_left_it(self, w):  # noqa: F811
        src = claimed(w)
        worker_finishes(w, {"0.1.13": {"clip": True, "overlay": [{"t0": 0.0, "t1": 2.0}]},
                            "0.1.15": {"clip": None, "reason": "video_gone"}})
        got = get(w, f"/api/me/plays/{src}/clips").json()["clips"]
        assert got == {"rocks": {"0.1.13": {"clip": True, "overlay": [{"t0": 0.0, "t1": 2.0}]},
                                 "0.1.15": {"clip": None, "reason": "video_gone"}},
                       "pending": False}

    def test_the_link_is_the_owners_alone(self, w):  # noqa: F811
        src = claimed(w)
        job = worker_finishes(w, {"0.1.13": {"clip": True},
                                  "0.1.15": {"clip": None, "reason": "no_release"}})
        r = get(w, f"/api/me/plays/{src}/clips/0.1.13")
        assert r.status_code == 200, r.text
        assert r.json()["url"] == f"memory://runs/{VID}/{job['run_id']}/clips/0.1.13.mp4"
        assert get(w, f"/api/me/plays/{src}/clips/0.1.15").status_code == 404
        assert get(w, f"/api/me/plays/{src}/clips/0.1.13", headers=ALEX).status_code == 404
        assert get(w, f"/api/me/plays/{src}/clips/..%2Fx").status_code == 404
        assert get(w, f"/api/me/plays/{src}/clips/0.1.13", headers=None).status_code == 401
```

The saves without a `path` change an existing play's colour and slot, as the report page does (`test_from_the_report_page_only_the_colour_and_slot_change`).

- [ ] **Step 2: Run them to see them fail**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_game_clips_api.py -q -k "Asking or Reading"`
Expected: FAIL: no clip jobs are queued (`jobs(w, src) == []`), the doc has no `clips`, and the `/clips` routes answer 404.

- [ ] **Step 3: The flag**

In `Settings`, after `practice_enabled: bool = False`, add:

```python
    # Clips of the rocks a person says they threw, on My shots: asked for when
    # a play is saved or its report read, cut by the clips worker. Off until a
    # worker running WORKER_ROLE=clips is up.
    game_clips_enabled: bool = False
```

and in `from_env`, after the `practice_enabled=` line:

```python
            game_clips_enabled=env("GAME_CLIPS_ENABLED", "0") in ("1", "true", "yes"),
```

- [ ] **Step 4: The report's document, shared**

Above `@app.get("/api/me/plays/{source_id}/doc")`, add the helpers. The body of `play_doc` is the existing route's code from `doc, fallback = None, None` through `doc = game_doc(...)`, moved verbatim, with `run` returned:

```python
    def play_doc(play: Play, src: Source) -> tuple[dict, Run, str | None]:
        """The game as this play's report reads it -- graded by the chart the
        play was saved from, if it was -- not yet cut down to the player:
        (doc, the run it is from, why it is ungraded when it should be)."""
        doc, fallback = None, None
        if play.chart_id:
            chart = live_chart(repo.get_chart(play.chart_id))
            run = repo.get_run(chart.run_id) if chart else None
            if chart and run and chart.game_index is not None:
                if run.status != "ready":
                    # Being analysed again in place. Not gone: its grading
                    # comes back with it, and its share link still opens it.
                    raise HTTPException(409, "this game is being analysed again; try later")
                # read_only: the chart block must not carry the edit link.
                doc = timeline.apply_overrides(chart_doc(chart, run, read_only=True),
                                               chart.overrides)
            else:
                fallback = ("the chart this was saved from is gone, so this is the "
                            "game as detected, without its grading")
        if doc is None:
            src, run = lookup_source(src.id)
            doc = game_doc(run, src.game_index, src, src.play_start_s)
        return doc, run, fallback

    def report_run(play: Play, src: Source) -> Run | None:
        """The run play_doc reads, without loading its document."""
        if play.chart_id:
            chart = live_chart(repo.get_chart(play.chart_id))
            if chart and chart.game_index is not None:
                run = repo.get_run(chart.run_id)
                if run is not None:
                    return run
        return repo.get_run(src.current_run_id)

    def ask_for_clips(play: Play, doc: dict, run: Run) -> dict | None:
        """Clips of the rocks this play names in ``doc`` (service/gameclips.py),
        asked for if they have not been, and where they stand; None if that
        went wrong -- the report and the play stand without them. Marks the
        player's rocks in ``doc`` with their names."""
        try:
            rocks = gameclips.player_rocks(doc, play.color, play.slot)
            return gameclips.ensure(repo, play, run, rocks, doc.get("calibration"), now())
        except Exception:  # noqa: BLE001
            log.exception("clips for play %s were not asked for", play.id)
            return None

    def clips_on():
        if not settings.game_clips_enabled:
            raise HTTPException(404, "clips are not switched on")
```

Then replace the body of `api_my_play_doc` after its 404 check with:

```python
        doc, run, fallback = play_doc(play, src)
        clips = ask_for_clips(play, doc, run) if settings.game_clips_enabled else None
        doc = timeline.for_player(doc, play.color, play.slot)
        out = {"doc": doc, "fallback": fallback}
        if clips is not None:
            out["clips"] = clips
        return json_revalidated(request, out, "private")
```

- [ ] **Step 5: Asking when a play is saved**

In `api_save_play`, after the `if existing is not None and existing.source_id != src.id: repo.delete_play(...)` lines and before the `log.info`, add:

```python
        if settings.game_clips_enabled:
            # Asked for now, while the game's video is surely still cached;
            # the report asks again for anything this misses.
            try:
                doc, run, _ = play_doc(play, src)
                ask_for_clips(play, doc, run)
            except HTTPException as exc:
                log.info("clips for play %s not asked for: %s", play.id, exc.detail)
```

- [ ] **Step 6: Reading clips, and a link to one**

After `api_my_play_doc`, add:

```python
    @app.get("/api/me/plays/{source_id}/clips")
    def api_my_play_clips(source_id: str, authorization: str | None = Header(default=None)):
        """Where this play's clips stand, without the document: the page asks
        this while any are on their way."""
        clips_on()
        me = require_user(authorization)
        play, src = find_play(me.id, source_id)
        run = report_run(play, src) if play and src else None
        if run is None:
            raise HTTPException(404, "you have not said which rocks you threw in that game")
        return {"clips": gameclips.state_for(repo, play.id, run.id)}

    @app.get("/api/me/plays/{source_id}/clips/{rock}")
    def api_my_play_clip(source_id: str, rock: str,
                         authorization: str | None = Header(default=None)):
        """A short-lived link to one of this play's clips, for the play's owner."""
        clips_on()
        me = require_user(authorization)
        play, src = find_play(me.id, source_id)
        run = report_run(play, src) if play and src else None
        if run is None or not gameclips.GAME_ROCK.match(rock):
            raise HTTPException(404, "no clip for that rock")
        got = gameclips.state_for(repo, play.id, run.id)["rocks"].get(rock) or {}
        if got.get("clip") is not True:
            raise HTTPException(404, "no clip for that rock")
        return {"url": store.presign_get(clip_key(run.video_id, run.id, rock), CLIP_URL_S)}
```

- [ ] **Step 7: Run the tests**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_game_clips_api.py tests/test_plays.py tests/test_service_auth.py -q`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/curling_score/service/api.py tests/test_game_clips_api.py
git commit -m "api: saving a play or reading its report asks for clips of its rocks; links for its owner

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: The clips worker

**Files:**
- Create: `src/curling_score/service/clipworker.py`
- Modify: `src/curling_score/service/worker.py` (`ApiClient` methods; `main`)
- Test: `tests/test_clipworker.py`

**Interfaces:**
- Consumes: `game.clips.find_video/sideviews_from_calibration/clip_rocks` (Task 2); the routes of Task 5; `worker.Lost`, `worker.ApiClient.upload`.
- Produces:
  - `ApiClient.claim_clips(worker_id)`, `clip_progress(job_id, worker_id)`, `clip_artifacts(job_id, worker_id, files)`, `clip_complete(job_id, worker_id, rocks)`, `clip_fail(job_id, worker_id, error)`.
  - `clipworker.process(job, api, worker_id, *, sources, workdir, cut=None) -> dict`
  - `clipworker.run_forever(api, worker_id, *, sources, workdir, sleep=time.sleep, once=False, cut=None)`
  - `clipworker.sources_from_env(value: str | None) -> list[Path]`
  - `worker.main` runs the clips loop when `WORKER_ROLE=clips`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_clipworker.py`:

```python
"""The clips worker loop, against a fake API (service/clipworker.py)."""

from pathlib import Path

import pytest

from curling_score.service import clipworker, worker
from tests.test_game_clips import CAL, made, rock


class FakeClipApi:
    def __init__(self, jobs, lost_on=None):
        self.jobs, self.lost_on = list(jobs), lost_on
        self.completed, self.failed, self.uploads, self.progress = [], [], {}, 0

    def claim_clips(self, worker_id):
        return self.jobs.pop(0) if self.jobs else None

    def clip_progress(self, job_id, worker_id):
        if self.lost_on == "progress":
            raise worker.Lost("given to another worker")
        self.progress += 1
        return {"ok": True}

    def clip_artifacts(self, job_id, worker_id, files):
        return {"uploads": [{"name": f["name"], "url": f"memory://{f['name']}", "headers": {}}
                            for f in files]}

    def upload(self, url, headers, data):
        self.uploads[url] = data

    def clip_complete(self, job_id, worker_id, rocks):
        if self.lost_on == "complete":
            raise worker.Lost("given to another worker")
        self.completed.append((job_id, rocks))
        return {"ok": True}

    def clip_fail(self, job_id, worker_id, error):
        self.failed.append((job_id, error))
        return {"ok": True}


def a_job(*rocks):
    return {"id": "cj_1", "run_id": "r1", "video_id": "VID",
            "spec": {"calibration": CAL, "rocks": list(rocks) or [rock()]}}


@pytest.fixture
def cache(tmp_path):
    (tmp_path / "c2" / "videos").mkdir(parents=True)
    (tmp_path / "c2" / "videos" / "VID.mp4").write_bytes(b"video")
    return [tmp_path / "c1", tmp_path / "c2"]


class TestOneJob:
    def test_video_gone_means_no_clips_and_no_cutting(self, tmp_path):
        api = FakeClipApi([])
        got = clipworker.process(a_job(rock(), rock(name="0.3.7")), api, "w",
                                 sources=[tmp_path], workdir=tmp_path / "w", cut=made)
        assert got == {"0.3.5": {"clip": None, "reason": "video_gone"},
                       "0.3.7": {"clip": None, "reason": "video_gone"}}
        assert api.completed == [("cj_1", got)] and api.uploads == {}

    def test_clips_are_cut_from_whichever_cache_has_the_video(self, cache, tmp_path):
        seen = []

        def cut(plan, views, out, **kw):
            seen.append(views["left"][0])
            Path(out).write_bytes(b"mp4")

        api = FakeClipApi([])
        got = clipworker.process(a_job(rock(), rock(name="0.3.7", t_release_s=None)), api, "w",
                                 sources=cache, workdir=tmp_path / "w", cut=cut)
        assert seen == [cache[1] / "videos" / "VID.mp4"]
        assert api.uploads == {"memory://0.3.5.mp4": b"mp4"}
        assert got["0.3.5"]["clip"] is True and got["0.3.7"]["reason"] == "no_release"
        assert api.progress == 2
        assert api.completed == [("cj_1", got)]

    def test_an_upload_that_fails_costs_that_rock(self, cache, tmp_path):
        api = FakeClipApi([])

        def broken(url, headers, data):
            raise OSError("network")

        api.upload = broken
        got = clipworker.process(a_job(), api, "w", sources=cache, workdir=tmp_path / "w",
                                 cut=made)
        assert got == {"0.3.5": {"clip": None, "reason": "cut_failed"}}


class TestTheLoop:
    def test_a_job_lost_mid_way_is_walked_away_from(self, cache, tmp_path):
        api = FakeClipApi([a_job()], lost_on="progress")
        clipworker.run_forever(api, "w", sources=cache, workdir=tmp_path / "w",
                               sleep=lambda s: None, once=True, cut=made)
        assert api.completed == [] and api.failed == []

    def test_a_complete_refused_as_lost_is_left_alone(self, cache, tmp_path):
        api = FakeClipApi([a_job()], lost_on="complete")
        clipworker.run_forever(api, "w", sources=cache, workdir=tmp_path / "w",
                               sleep=lambda s: None, once=True, cut=made)
        assert api.failed == []

    def test_a_job_that_breaks_is_reported(self, tmp_path, monkeypatch):
        api = FakeClipApi([a_job()])

        def boom(*a, **kw):
            raise ValueError("bad spec")

        monkeypatch.setattr(clipworker.game_clips, "find_video", boom)
        clipworker.run_forever(api, "w", sources=[tmp_path], workdir=tmp_path / "w",
                               sleep=lambda s: None, once=True)
        assert api.failed == [("cj_1", "ValueError: bad spec")]

    def test_nothing_to_do(self, tmp_path):
        naps = []
        clipworker.run_forever(FakeClipApi([]), "w", sources=[tmp_path], workdir=tmp_path,
                               sleep=naps.append, once=True)
        assert naps == []


def test_the_caches_from_the_environment():
    assert clipworker.sources_from_env("/data/src1:/data/src2::") == [
        Path("/data/src1"), Path("/data/src2")]
    assert clipworker.sources_from_env(None) == []


def test_worker_role_clips_runs_the_clips_loop_and_nothing_else(tmp_path, monkeypatch):
    got = {}
    monkeypatch.setenv("API_URL", "http://api")
    monkeypatch.setenv("WORKER_TOKEN", "t")
    monkeypatch.setenv("CURLING_SCORE_CACHE", str(tmp_path))
    monkeypatch.setenv("WORKER_ID", "ryzen-clips")
    monkeypatch.setenv("WORKER_ROLE", "clips")
    monkeypatch.setenv("WORKER_CLIP_SOURCES", "/data/src1:/data/src2")
    monkeypatch.setattr(clipworker, "run_forever", lambda api, wid, **kw: got.update(wid=wid, **kw))
    monkeypatch.setattr(worker, "run_forever", lambda *a, **kw: pytest.fail("pipeline loop ran"))
    monkeypatch.setattr(worker, "resolve_weights", lambda: pytest.fail("a model was loaded"))
    assert worker.main([]) == 0
    assert got["wid"] == "ryzen-clips"
    assert got["sources"] == [Path("/data/src1"), Path("/data/src2")]
    assert got["workdir"] == tmp_path / "clips"
```

- [ ] **Step 2: Run them to see them fail**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_clipworker.py -q`
Expected: FAIL, `ImportError: cannot import name 'clipworker'`.

- [ ] **Step 3: The API client**

In `src/curling_score/service/worker.py`, add to `ApiClient` after `fail`:

```python
    # The clips worker's queue (service/clipworker.py).
    def claim_clips(self, worker_id):
        r = self._http.post(f"{self.base}/api/worker/clips/claim", headers=self._headers,
                            json={"worker_id": worker_id, "version": version.PIPELINE_VERSION})
        if r.status_code == 204:
            return None
        r.raise_for_status()
        return r.json()["job"]

    def clip_progress(self, job_id, worker_id):
        return self._post(f"/api/worker/clips/{job_id}/progress", {"worker_id": worker_id},
                          timeout=PROGRESS_TIMEOUT_S)

    def clip_artifacts(self, job_id, worker_id, files):
        return self._post(f"/api/worker/clips/{job_id}/artifacts",
                          {"worker_id": worker_id, "files": files})

    def clip_complete(self, job_id, worker_id, rocks):
        return self._post(f"/api/worker/clips/{job_id}/complete",
                          {"worker_id": worker_id, "rocks": rocks})

    def clip_fail(self, job_id, worker_id, error):
        return self._post(f"/api/worker/clips/{job_id}/fail",
                          {"worker_id": worker_id, "error": error})
```

- [ ] **Step 4: The loop**

Create `src/curling_score/service/clipworker.py`:

```python
"""The clips worker: cuts clips of the rocks people say they threw.

A service of its own beside the main workers (deploy/docker-compose.worker.yml
`clips`). Each main worker keeps its own cache, so a game's video is wherever
the worker that ran it keeps it; this one reads all of them, read-only, and
writes only to a scratch of its own. It loads no model, starts no live lane
and never talks to YouTube: a game whose video has left every cache gets no
clips (`video_gone`), not a download.
"""

import logging
import time
from pathlib import Path

from curling_score.game import clips as game_clips
from curling_score.service.worker import Lost

log = logging.getLogger("curling_score.clipworker")

POLL_IDLE_S = 30.0    # a clip job is asked for when a play is saved: seldom
POLL_BUSY_S = 2.0     # after one, look again soon: a report opened asks for several


def sources_from_env(value: str | None) -> list[Path]:
    """``WORKER_CLIP_SOURCES``: the caches to look in, colon-separated."""
    return [Path(p) for p in (value or "").split(":") if p]


def process(job: dict, api, worker_id: str, *, sources, workdir, cut=None) -> dict:
    """Cut, upload and report one clip job's rocks; what came of each."""
    rocks = job["spec"].get("rocks", [])
    video = game_clips.find_video(job["video_id"], sources)
    if video is None:
        results = {r["name"]: {"clip": None, "reason": "video_gone"} for r in rocks}
        api.clip_complete(job["id"], worker_id, results)
        return results
    sideviews = game_clips.sideviews_from_calibration(job["spec"].get("calibration"))

    def deliver(name, path):
        data = Path(path).read_bytes()
        try:
            plan = api.clip_artifacts(job["id"], worker_id,
                                      [{"name": f"{name}.mp4", "bytes": len(data)}])
            for up in plan["uploads"]:
                api.upload(up["url"], up["headers"], data)
        except Lost:
            raise
        except Exception as exc:  # noqa: BLE001 - that rock goes without a clip
            log.warning("clip job %s: %s did not upload (%s)", job["id"], name, exc)
            return False
        return True

    def on_rock(name, result):
        try:
            api.clip_progress(job["id"], worker_id)
        except Lost:
            raise
        except Exception as exc:  # noqa: BLE001 - the lease has minutes in hand
            log.warning("clip job %s: progress not delivered (%s)", job["id"], exc)

    kw = {} if cut is None else {"cut": cut}
    results = game_clips.clip_rocks(rocks, video, sideviews, Path(workdir) / job["id"],
                                    deliver, on_rock=on_rock, **kw)
    api.clip_complete(job["id"], worker_id, results)
    return results


def run_forever(api, worker_id: str, *, sources, workdir, sleep=time.sleep, once=False,
                cut=None):
    """Claim and cut clip jobs until stopped."""
    log.info("clips worker %s: caches %s, scratch %s", worker_id,
             [str(s) for s in sources], workdir)
    while True:
        try:
            job = api.claim_clips(worker_id)
        except Exception as exc:  # noqa: BLE001 - the API may be down; keep trying
            log.warning("clips claim failed: %s", exc)
            job = None
        if job is None:
            if once:
                return
            sleep(POLL_IDLE_S)
            continue
        try:
            got = process(job, api, worker_id, sources=sources, workdir=workdir, cut=cut)
            log.info("clip job %s: %d of %d rocks clipped", job["id"],
                     sum(1 for r in got.values() if r.get("clip")), len(got))
        except Lost as exc:
            log.warning("clip job %s lost: %s", job["id"], exc)
        except Exception as exc:  # noqa: BLE001
            log.exception("clip job %s failed", job["id"])
            try:
                api.clip_fail(job["id"], worker_id, f"{type(exc).__name__}: {exc}"[:500])
            except Exception as exc2:  # noqa: BLE001 - the lease will return it
                log.warning("could not report clip job %s failing: %s", job["id"], exc2)
        if once:
            return
        sleep(POLL_BUSY_S)
```

- [ ] **Step 5: `WORKER_ROLE=clips` in `main`**

In `worker.main`, move `worker_id = os.environ.get("WORKER_ID") or socket.gethostname()` up to just after `os.environ["CURLING_SCORE_CACHE"] = str(root)`, and directly after it (before `weights = resolve_weights()`) add:

```python
    if os.environ.get("WORKER_ROLE") == "clips":
        # The clips worker: no model, no live lane, only the clip queue.
        from curling_score.service import clipworker

        clipworker.run_forever(
            ApiClient(api_url, token), worker_id,
            sources=clipworker.sources_from_env(os.environ.get("WORKER_CLIP_SOURCES")),
            workdir=root / "clips", once="--once" in (argv or sys.argv[1:]))
        return 0
```

`main(argv=None)` reads `sys.argv[1:]` when `argv` is None; the test passes `[]`, so `once` is False there. The test stubs `run_forever`, so the loop does not run.

- [ ] **Step 6: Run the tests**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_clipworker.py tests/test_worker.py tests/test_game_clips.py -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/curling_score/service/clipworker.py src/curling_score/service/worker.py tests/test_clipworker.py
git commit -m "worker: WORKER_ROLE=clips cuts claimed rocks from whichever cache holds the game

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Clips on My shots

**Files:**
- Create: `frontend/site/clip.jsx`
- Modify: `frontend/site/Practice.jsx` (remove the clip player, use the shared one)
- Modify: `frontend/core/myshots.mjs` (`playerRocks` rows carry `rock`; `rockClip`, `clipText`)
- Modify: `frontend/site/Shots.jsx` (Clip button and row; games-table status; polling)
- Modify: `src/curling_score/service/static/site.css` (clip styles unscoped; the clip row)
- Build output: `src/curling_score/service/static/site.js`, `frontend/.buildstamp.json`
- Test: `tests/test_viewer_js.py` (append)

**Interfaces:**
- Consumes: Task 6's `/doc` `clips` and `rock`, `/clips`, `/clips/{rock}`.
- Produces: `ClipPlayer({ src, overlay, autoPlay })` from `frontend/site/clip.jsx`; core `rockClip(row, clips)` and `clipText(rows, clips)`; `playerRocks` rows gain `rock`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_viewer_js.py`:

```python
class TestClipsOnTheShotsPage:
    """My shots plays a clip of each rock that has one; core says where the
    clips stand, the page only lays it out."""
    ROOT = Path(__file__).resolve().parents[1]

    def test_each_row_knows_its_rock(self):
        mine = aimed(13, "red", 4)
        mine["rock"] = "0.1.13"
        d = doc([mine, aimed(14, "yellow", 4), aimed(15, "red", 4)])
        d["schema_version"] = 8
        got = run_js(f"out(playerRocks({json.dumps(play())}, {json.dumps(d)}).rows"
                     ".map(r => r.rock));")
        assert got == ["0.1.13", None]

    def test_only_a_made_clip_plays(self):
        clips = {"rocks": {"0.1.13": {"clip": True, "overlay": [{"t0": 0, "t1": 1}]},
                           "0.1.15": {"clip": None, "reason": "no_release"},
                           "0.1.11": {"clip": False}}, "pending": True}
        got = run_js(f"const c = {json.dumps(clips)};"
                     "out(['0.1.13', '0.1.15', '0.1.11', null].map(k => rockClip({rock: k}, c))"
                     ".concat([rockClip({rock: '0.1.13'}, undefined)]));")
        assert got == [{"clip": True, "overlay": [{"t0": 0, "t1": 1}]}, None, None, None, None]

    @pytest.mark.parametrize("states,want", [
        ([{"clip": False}, {"clip": True}], "Clips on their way…"),
        ([None, {"clip": True}], "Clips on their way…"),
        ([{"clip": True}, {"clip": True}], "2 clips"),
        ([{"clip": True}, {"clip": None, "reason": "no_release"}], "1 of 2 clips"),
        ([{"clip": None, "reason": "video_gone"}] * 2, "No clips: the video is no longer kept"),
        ([{"clip": None, "reason": "no_release"}, {"clip": None, "reason": "video_gone"}],
         "No clips"),
    ])
    def test_the_games_table_says_where_they_stand(self, states, want):
        rows = [{"rock": f"0.1.{n}"} for n in range(len(states))]
        clips = {"rocks": {f"0.1.{n}": s for n, s in enumerate(states) if s is not None}}
        assert run_js(f"out(clipText({json.dumps(rows)}, {json.dumps(clips)}));") == want

    def test_with_clips_off_it_says_nothing(self):
        assert run_js("out([clipText([{rock: '0.1.1'}], undefined), clipText([], {rocks: {}})]);") \
            == [None, None]

    def test_the_page_polls_only_while_clips_are_on_their_way(self):
        page = (self.ROOT / "frontend/site/Shots.jsx").read_text()
        assert "const CLIP_POLL_MS = 15000;" in page
        assert "`${playUrl(p.source_id)}/clips`" in page
        assert "?.clips?.pending" in page

    def test_practice_and_my_shots_share_one_player(self):
        for rel in ("frontend/site/Practice.jsx", "frontend/site/Shots.jsx"):
            assert 'import { ClipPlayer } from "./clip.jsx";' in (self.ROOT / rel).read_text()
        assert "function ClipLines" not in (self.ROOT / "frontend/site/Practice.jsx").read_text()
```

- [ ] **Step 2: Run them to see them fail**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_viewer_js.py -q -k ClipsOnTheShotsPage`
Expected: FAIL, `ReferenceError: rockClip is not defined` and missing page strings.

- [ ] **Step 3: Core words**

In `frontend/core/myshots.mjs`, in `playerRocks`'s `rows.push({...})`, add after `sourceId: play.source_id,`:

```js
        // Its name for its clip (service/gameclips.py), when clips are on.
        rock: s.rock ?? null,
```

and append to the file:

```js
/* A rock's clip from its game's clip state (/api/me/plays/{id}/doc's
 * `clips`), when one can be played: {clip: true, overlay?}; else null. */
export function rockClip(row, clips) {
  const c = row.rock ? clips?.rocks?.[row.rock] : null;
  return c && c.clip === true ? c : null;
}

/* The games table's word on a game's clips, over the rocks the report lists
 * for it; null when clips are not on. */
export function clipText(rows, clips) {
  if (!clips || !rows.length) return null;
  const states = rows.map(r => (r.rock && clips.rocks?.[r.rock]) || null);
  if (states.some(c => !c || c.clip === false)) return "Clips on their way…";
  const made = states.filter(c => c.clip === true).length;
  if (made === rows.length) return `${made} clip${made === 1 ? "" : "s"}`;
  if (made) return `${made} of ${rows.length} clips`;
  if (states.every(c => c.reason === "video_gone")) return "No clips: the video is no longer kept";
  return "No clips";
}
```

- [ ] **Step 4: The shared player**

Create `frontend/site/clip.jsx`, moved from `Practice.jsx` with the link cache keyed by the API path rather than the throw id:

```jsx
/* A clip of one throw with the broom and lines drawn over it -- the practice
 * pages' and My shots'. `src` is the API path that answers with a short-lived
 * link ({url}); `overlay` is the worker's per-piece list (practice/overlay.py).
 * A link that has expired under an open page is asked for once more. */
import { useEffect, useRef, useState } from "react";
import { authedFetch } from "./auth.js";
import { linkFresh, overlayAt } from "../core/practice.mjs";

const LINES_KEY = "practice.lines";
// A clip's picture, as the worker scales it (practice/clips.py CLIP_SIZE).
const CLIP_W = 600, CLIP_H = 810;

// Signed links to clips, by the API path they came from: { url, at }.
const links = new Map();

async function clipLink(src, again = false) {
  const hit = links.get(src);
  if (!again && linkFresh(hit, Date.now())) return hit.url;
  const res = await authedFetch(src);
  if (!res.ok) return null;
  const { url } = await res.json();
  links.set(src, { url, at: Date.now() });
  return url;
}

function savedLines() {
  try { return localStorage.getItem(LINES_KEY) !== "off"; } catch { return true; }
}

function saveLines(on) {
  try { localStorage.setItem(LINES_KEY, on ? "on" : "off"); } catch { /* private window */ }
}
```

Then move `function ClipLines({ overlay, video }) { … }` from `Practice.jsx` into `clip.jsx` unchanged, with its comment. After it add:

```jsx
export function ClipPlayer({ src, overlay, autoPlay = false }) {
  const [url, setUrl] = useState(null);
  const [again, setAgain] = useState(false);
  const [lines, setLines] = useState(savedLines);
  const video = useRef(null);
  useEffect(() => {
    let on = true;
    setUrl(null);
    setAgain(false);
    clipLink(src).then(u => { if (on) setUrl(u); });
    return () => { on = false; };
  }, [src]);
  if (!url) return null;
  const retry = () => {
    if (again) return;
    setAgain(true);
    clipLink(src, true).then(setUrl);
  };
  const toggle = () => { setLines(!lines); saveLines(!lines); };
  return (
    <>
      <div className="clip-box" key={url}>
        <video ref={video} className="practice-clip" src={url} controls playsInline
               muted={autoPlay} autoPlay={autoPlay} preload="metadata" onError={retry} />
        {lines && overlay && <ClipLines overlay={overlay} video={video} />}
      </div>
      {overlay && (
        <p className="clip-tools muted">
          <button onClick={toggle}>{lines ? "Hide lines" : "Show lines"}</button>
          {lines && <> Solid: the line it was thrown on · dashed: hack to broom · ring: the broom</>}
        </p>
      )}
    </>
  );
}
```

- [ ] **Step 5: Practice uses it**

In `frontend/site/Practice.jsx`:
- delete `LINES_KEY`, `CLIP_W, CLIP_H`, `savedLines`, `saveLines`, `links`, `clipLink`, `ClipLines` and `Clip`;
- remove `linkFresh, overlayAt` from the `../core/practice.mjs` import and `useRef` from the `react` import (lint fails on unused names);
- add `import { ClipPlayer } from "./clip.jsx";`;
- replace `{clip === "play" && <Clip sid={sid} t={t} autoPlay={live && newest} />}` with

```jsx
      {clip === "play" && (
        <ClipPlayer src={`/api/practice/sessions/${encodeURIComponent(sid)}/throws/`
                         + `${encodeURIComponent(t.id)}/clip`}
                    overlay={t.overlay} autoPlay={live && newest} />
      )}
```

- [ ] **Step 6: My shots**

In `frontend/site/Shots.jsx`:

1. Imports: `import { Fragment, useEffect, useMemo, useRef, useState } from "react";`; add `clipText, rockClip,` to the `../core/myshots.mjs` import; add `import { ClipPlayer } from "./clip.jsx";`.
2. Under `const AT_ONCE = 3;` add:

```jsx
// While a game's clips are on their way, ask after them this often.
const CLIP_POLL_MS = 15000;
```

3. In `useRocks`, read the clips and hand back the setter:

```jsx
          if (res.ok) {
            const { doc, fallback, clips } = await res.json();
            one = { ...playerRocks(p, doc), fallback, clips };
          } else {
```

and end the hook with `return [got, setGot];`.

4. After `useRocks`, add:

```jsx
/* While any game's clips are on their way, ask after just those games every
 * CLIP_POLL_MS -- /clips, no document -- and stop once none are. */
function useClipPoll(plays, rocks, setRocks) {
  const waiting = (plays || []).filter(p => rocks[playKey(p)]?.clips?.pending);
  const keys = waiting.map(playKey).join(",");
  useEffect(() => {
    if (!waiting.length) return undefined;
    const id = setInterval(async () => {
      for (const p of waiting) {
        try {
          const res = await authedFetch(`${playUrl(p.source_id)}/clips`);
          if (!res.ok) continue;
          const { clips } = await res.json();
          setRocks(g => (g[playKey(p)] ? { ...g, [playKey(p)]: { ...g[playKey(p)], clips } } : g));
        } catch { /* asked again next time */ }
      }
    }, CLIP_POLL_MS);
    return () => clearInterval(id);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [keys]);
}
```

5. Replace `RockTable` with:

```jsx
function RockTable({ rows, clips }) {
  const [open, setOpen] = useState(null);
  const heads = rows[0].figures.map(f => f.label);
  return (
    <table className="rocks">
      <tbody>
        <tr><th>Game</th><th>End</th><th>Rock</th><th>Shot</th>
            {heads.map(h => <th key={h}>{h}</th>)}<th /></tr>
        {rows.map(r => {
          const clip = rockClip(r, clips?.[r.sourceId]);
          const shown = clip && open === r.key;
          return (
            <Fragment key={r.key}>
              <tr>
                <td className="game">{r.game}</td>
                <td data-label="End">{r.end}</td>
                <td data-label="Rock">{r.number}</td>
                <td data-label="Shot">{r.typeName}</td>
                {r.figures.map(f => (
                  <td key={f.key} data-label={f.label} title={f.note || undefined}
                      className={f.dim ? "dim" : undefined}>{f.value}</td>
                ))}
                <td className="actions">
                  {clip && (
                    <button aria-expanded={shown}
                            onClick={() => setOpen(shown ? null : r.key)}>Clip</button>
                  )}
                  {r.href && <a className="btn" href={r.href}>Watch</a>}
                </td>
              </tr>
              {shown && (
                <tr className="clip-row">
                  <td colSpan={heads.length + 5}>
                    <ClipPlayer src={`${playUrl(r.sourceId)}/clips/${encodeURIComponent(r.rock)}`}
                                overlay={clip.overlay} />
                  </td>
                </tr>
              )}
            </Fragment>
          );
        })}
      </tbody>
    </table>
  );
}
```

6. `Group` takes and passes the clips: `function Group({ group, clips })` and `<RockTable rows={group.rows} clips={clips} />`.
7. In `PlayRow`, after the `const state = …;` statement add

```jsx
  const clips = rocks && !rocks.failed ? clipText(rocks.rows, rocks.clips) : null;
```

and change the Rocks cell to `<td data-label="Rocks">{state}{clips && <div className="muted">{clips}</div>}</td>`.
8. In `Shots()`: `const [rocks, setRocks] = useRocks(plays);` then `useClipPoll(plays, rocks, setRocks);`. After `const live = …;` add

```jsx
  // Each game's clip state, by game, for the rows the groups mix together.
  const clipsBy = Object.fromEntries(live.map(p => [p.source_id, rocks[playKey(p)]?.clips]));
```

and render groups as `groups.map(g => <Group key={g.id} group={g} clips={clipsBy} />)`.

- [ ] **Step 7: Styles**

In `src/curling_score/service/static/site.css`, replace the block from `/* A clip at its own shape,` through `#practice .clip-tools { … }` with:

```css
/* A clip at its own shape, so the lines drawn over it (practice/overlay.py)
   sit where the worker placed them. Practice and My shots. */
.clip-box { position:relative; width:min(100%, calc(60vh * 600 / 810));
            aspect-ratio:600 / 810; margin:10px auto 4px; background:#000;
            border-radius:8px; overflow:hidden; }
.clip-box video.practice-clip { position:absolute; inset:0; width:100%; height:100%;
                                object-fit:fill; display:block; }
svg.clip-lines { position:absolute; inset:0; width:100%; height:100%; pointer-events:none; }
svg.clip-lines polyline, svg.clip-lines circle {
  fill:none; stroke-width:3; stroke-linecap:round; vector-effect:non-scaling-stroke; }
svg.clip-lines .under { stroke:rgba(0,0,0,.45); stroke-width:6; stroke-dasharray:none; }
svg.clip-lines .thrown, svg.clip-lines .broom { stroke:#ff2fd0; }
svg.clip-lines .aim { stroke:#2fd8ff; stroke-dasharray:9 7; }
.clip-tools { font-size:13px; margin:0 0 8px; text-align:center; }
/* My shots: a rock's clip opens in a row of its own under the rock. */
#shots tr.clip-row td { padding:0 0 10px; }
```

- [ ] **Step 8: Build, lint and test**

Run: `cd frontend && npm run build && cd ..`
Expected: lint clean; `site.js` rebuilt; build stamp updated.

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_viewer_js.py tests/test_practice_js.py -q`
Expected: all pass (including `TestTheShotsPage`'s no-arithmetic check).

- [ ] **Step 9: Commit**

```bash
git add frontend/site/clip.jsx frontend/site/Practice.jsx frontend/site/Shots.jsx \
        frontend/core/myshots.mjs src/curling_score/service/static/site.css \
        src/curling_score/service/static/site.js frontend/.buildstamp.json tests/test_viewer_js.py
git commit -m "shots: a Clip button plays the rock with its lines; the games table says where clips stand

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: The clips service and the API switch

**Files:**
- Modify: `deploy/docker-compose.worker.yml`
- Modify: `deploy/cloudrun.yaml`

**Interfaces:**
- Consumes: `WORKER_ROLE=clips`, `WORKER_CLIP_SOURCES` (Task 7); `GAME_CLIPS_ENABLED` (Task 6).

- [ ] **Step 1: The compose service**

In `deploy/docker-compose.worker.yml`, after the `worker3` service and before the `pot` comment, add:

```yaml
  # Cuts the clips of the rocks people say they threw (service/clipworker.py),
  # for My shots. Light: no GPU, no YouTube, two CPUs, so a burst of cuts never
  # crowds the lanes above on a league night. Each worker above keeps its own
  # cache, and a game's video is in whichever ran it, so this reads all three
  # -- read-only: it prunes nothing and empties nothing there -- and writes
  # only to a scratch of its own. A game whose video has left every cache gets
  # no clips; nothing is downloaded for one.
  clips:
    extends: { service: worker }
    build: !reset null
    pull_policy: never
    depends_on: !reset []
    environment:
      WORKER_ID: ryzen-clips
      WORKER_ROLE: clips
      WORKER_LIVE: "0"
      WORKER_PRACTICE: "0"
      WORKER_CLIP_SOURCES: /data/src1:/data/src2:/data/src3
    volumes:
      - /data/wdd/curling-clips:/data/cache
      - /data/wdd/curling-cache:/data/src1:ro
      - /data/wdd/curling-cache-2:/data/src2:ro
      - /data/wdd/curling-cache-3:/data/src3:ro
    cpus: "2.0"
    deploy: !reset null
```

- [ ] **Step 2: Check that compose reads it**

Run:

```bash
mkdir -p $SCRATCH/compose && cp deploy/docker-compose.worker.yml $SCRATCH/compose/ && touch $SCRATCH/compose/worker.env
docker compose -f $SCRATCH/compose/docker-compose.worker.yml config --services
docker compose -f $SCRATCH/compose/docker-compose.worker.yml config | sed -n '/^  clips:/,/^  [a-z]/p'
```

(`$SCRATCH` is the session's scratchpad directory.) Expected: services `clips pot worker worker2 worker3`. The clips block has `WORKER_ROLE: clips`, the four volumes (the `/data/cache` mount is the clips scratch, not `curling-cache`), `cpus: 2`, and no `deploy:` GPU reservation and no `depends_on`. If the base `/data/cache` mount is still listed beside the clips one, replace `volumes:` with `volumes: !override` and run again.

- [ ] **Step 3: The API switch, off until the worker runs**

In `deploy/cloudrun.yaml`, after the `PRACTICE_ENABLED` entry, add:

```yaml
            # GAME_CLIPS_ENABLED ("1", "true" or "yes") asks for clips of the
            # rocks a person says they threw, and shows them on My shots. Only
            # with the worker box running the `clips` service (WORKER_ROLE=clips):
            # clip jobs no worker claims just wait.
            - { name: GAME_CLIPS_ENABLED, value: "0" }
```

- [ ] **Step 4: Commit**

```bash
git add deploy/docker-compose.worker.yml deploy/cloudrun.yaml
git commit -m "deploy: a clips service reading every worker's cache, and the API switch (off)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: End-to-end checks before shipping

Scratch work: nothing here is committed. Put the scripts in `~/curling-work/game-clips/`. `WT` is the worktree path.

**Files:**
- Create (scratch): `~/curling-work/game-clips/cut_vxu9.py`, `stills.py`, `serve.py`, `shots.mjs`

- [ ] **Step 1: Cut a player's rocks from the cached VXU9 video**

Pick a colour and slot with releases:

```bash
TL=/home/tcuser/src/curling_score/.claude/worktrees/practice/out/practice/ref-VXU9/timeline.json
python3 -c "
import json,collections; d=json.load(open('$TL')); c=collections.Counter()
for e in d['games'][0]['ends']:
    for s in e['shots']:
        if s.get('t_release_s') is not None: c[(s['color'], s.get('thrower_slot'))]+=1
print(c.most_common())"
```

Write `~/curling-work/game-clips/cut_vxu9.py`:

```python
"""Scratch: cut one player's rocks from the cached VXU9 video as the clips worker does."""
import json
import shutil
import sys
from pathlib import Path

WT, TL, COLOR, SLOT, OUT = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], int(sys.argv[4]), Path(sys.argv[5])
sys.path[:0] = [str(WT / "src")]
from curling_score.game import clips as gc  # noqa: E402
from curling_score.ingest import cache  # noqa: E402
from curling_score.service import gameclips  # noqa: E402

doc = json.loads(TL.read_text())
doc["games"] = doc["games"][:1]
rocks = gameclips.player_rocks(doc, COLOR, SLOT)
video = gc.find_video(doc["source"]["video_id"], [cache.default_root()])
assert video is not None, "VXU9 is not in the local cache"
OUT.mkdir(parents=True, exist_ok=True)


def deliver(name, path):
    shutil.copy(path, OUT / f"{name}.mp4")
    return True


got = gc.clip_rocks(rocks, video, gc.sideviews_from_calibration(doc["calibration"]),
                    OUT / "work", deliver)
(OUT / "results.json").write_text(json.dumps(got, indent=1))
(OUT / "rocks.json").write_text(json.dumps(rocks, indent=1))
print({k: v.get("reason", "clip") for k, v in got.items()})
```

Run it with `nice -n 10`: `nice -n 10 /home/tcuser/src/curling_score/.venv/bin/python ~/curling-work/game-clips/cut_vxu9.py $WT $TL <colour> <slot> ~/curling-work/game-clips/out`.
Expected: a clip for each released rock, about 1-3 s each, 13-33 s long, under 1 MB.

- [ ] **Step 2: Look at the lines on stills**

Write `~/curling-work/game-clips/stills.py`:

```python
"""Scratch: one still per clip piece, mid-piece, with the overlay drawn as the page draws it."""
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(sys.argv[1])
for name, r in json.loads((OUT / "results.json").read_text()).items():
    for i, p in enumerate(r.get("overlay") or []):
        png = OUT / f"{name}-{i}.png"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{(p['t0'] + p['t1']) / 2:.2f}",
                        "-i", str(OUT / f"{name}.mp4"), "-frames:v", "1", str(png)], check=True)
        im = Image.open(png).convert("RGB")
        d, (W, H) = ImageDraw.Draw(im), im.size
        for key, colour in (("thrown", (255, 47, 208)), ("aim", (47, 216, 255))):
            if p.get(key):
                d.line([(u * W, v * H) for u, v in p[key]], fill=colour, width=3)
        if p.get("broom"):
            u, v = p["broom"]
            d.ellipse([u * W - 13, v * H - 13, u * W + 13, v * H + 13], outline=(255, 47, 208), width=3)
        im.save(png)
print("stills in", OUT)
```

Run it with the venv's python and open a handful of PNGs (Read tool).
Expected:
- In the behind-camera piece, the ring sits on the skip's broom.
- In the facing piece, the rock rides the solid line after release.
- The camera switches about when the rock reaches the far house. VXU9's timeline predates `t_far_s`, so the switch is at `t_enter_s`.

If the lines are offset from the ice, check that the calibration `rect`s are in the cached video's pixel size (1080 rows).

- [ ] **Step 3: Watch them on the local /shots page, phone-sized**

Write `~/curling-work/game-clips/serve.py`:

```python
"""Scratch: /shots with one claimed VXU9 game whose clips cut_vxu9.py cut."""
import json
import pathlib
import sys

WT, CLIPS, TL = (pathlib.Path(a) for a in sys.argv[1:4])
COLOR, SLOT, PORT = sys.argv[4], int(sys.argv[5]), 8791
sys.path[:0] = [str(WT), str(WT / "src")]

import uvicorn  # noqa: E402
from starlette.routing import Mount  # noqa: E402
from starlette.staticfiles import StaticFiles  # noqa: E402

from curling_score.service.api import create_app  # noqa: E402
from curling_score.service.auth import FakeVerifier  # noqa: E402
from curling_score.service.records import ClipJob, Play, Run, Source, play_id  # noqa: E402
from curling_score.service.repo import MemoryRepo  # noqa: E402
from curling_score.service.store import MemoryStore, timeline_key  # noqa: E402
from tests.test_service_api import CLUB, Clock, FakeYouTube, Settings, T0  # noqa: E402


class Store(MemoryStore):
    def presign_get(self, key, expires_s=3600):
        return f"http://127.0.0.1:{PORT}/_clips/{pathlib.Path(key).name}"


repo, store = MemoryRepo(), Store()
auth = FakeVerifier()
auth.add("tok-will", "uid-will", "will@example.org", "Will")
settings = Settings(public_base_url=f"http://127.0.0.1:{PORT}", worker_token="w", admin_token="a",
                    allowed_channels={CLUB}, model_id="m", firebase_project="proj",
                    firebase_api_key="AIzaFake", firebase_auth_domain="proj.firebaseapp.com",
                    game_clips_enabled=True)
app = create_app(repo, store, FakeYouTube(), settings, now=Clock(), auth=auth)
app.router.routes.insert(0, Mount("/_clips", app=StaticFiles(directory=CLIPS)))

doc = json.loads(TL.read_text())
doc["games"] = doc["games"][:1]
vid, g = doc["source"]["video_id"], doc["games"][0]
store.put_bytes(timeline_key(vid, "r_0"), json.dumps(doc).encode())
repo.put_run(Run(id="r_0", video_id=vid, processing_version="p", status="ready", created_at=T0,
                 ready_at=T0, timeline_key=timeline_key(vid, "r_0"), title="VXU9",
                 games=[{"index": g["index"], "start_s": g["start_s"], "end_s": g["end_s"],
                         "ends": len(g["ends"])}], sheet=doc["source"].get("sheet"), published_at=T0))
repo.put_source(Source(id="s0", video_id=vid, game_start_s=g["start_s"], game_end_s=g["end_s"],
                       current_run_id="r_0", game_index=g["index"], created_at=T0, title="VXU9",
                       played_at=T0, team_red="Dimmit", team_yellow="Grant"))
play = Play(id=play_id("uid-will", "s0"), user_id="uid-will", source_id="s0", color=COLOR,
            slot=SLOT, created_at=T0, updated_at=T0)
repo.put_play(play)
repo.put_clip_job(ClipJob(id="cj_0", play_id=play.id, user_id="uid-will", source_id="s0",
                          run_id="r_0", video_id=vid, state="done", created_at=T0, run_after=T0,
                          spec={"rocks": json.loads((CLIPS / "rocks.json").read_text())},
                          rocks=json.loads((CLIPS / "results.json").read_text())))
uvicorn.run(app, host="127.0.0.1", port=PORT)
```

Run it in the background: `/home/tcuser/src/curling_score/.venv/bin/python ~/curling-work/game-clips/serve.py $WT ~/curling-work/game-clips/out $TL <colour> <slot>`.

Copy `~/curling-work/my-shots-check/check.mjs` to `~/curling-work/game-clips/shots.mjs`:
- change the `attach` import to `$WT/scripts/cdp.mjs`;
- set `BASE` to `http://127.0.0.1:8791`;
- replace everything from its `/shots` navigation on with:

```js
await cdp.send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
await cdp.send("Emulation.setTouchEmulationEnabled", { enabled: true });
await cdp.send("Page.navigate", { url: `${BASE}/shots` });
await new Promise(r => setTimeout(r, 4000));
const ev = expr => cdp.send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
console.log((await ev(`[...document.querySelectorAll("#plays td[data-label=Rocks]")].map(t => t.innerText)`)).result.value);
await ev(`document.querySelector("section.shotgrp details").open = true`);
await ev(`[...document.querySelectorAll(".rocks .actions button")].find(b => b.textContent === "Clip").click()`);
await new Promise(r => setTimeout(r, 4000));
await ev(`document.querySelector("video.practice-clip").currentTime = 18`);
await new Promise(r => setTimeout(r, 1500));
await ev(`document.querySelector("tr.clip-row").scrollIntoView()`);
const shot = await cdp.send("Page.captureScreenshot", { format: "png" });
writeFileSync(`${OUT}/shots-clip.png`, Buffer.from(shot.data, "base64"));
console.log("errors:", errors);
```

Run it with Chrome's remote debugging up, as `~/curling-work/my-shots-check` did (see [[headless-phone-check]]): `OUT=~/curling-work/game-clips node ~/curling-work/game-clips/shots.mjs`.
Expected:
- The games table says "N clips" (or "N of M clips").
- The screenshot shows the clip under its rock with the magenta line and ring over it.
- `errors: []`.

- [ ] **Step 4: The clock of a kept live recording (worker box, read-only)**

Tell the user before starting; this reads files on the worker box and writes nothing there.
- `ssh curling@100.85.240.114`.
- Pick a game that was charted live and whose whole recording is still kept: list `/data/wdd/curling-cache*/kept/*.json` with `"whole": true`, and `videos/<id>.mp4` for the same id.
- Fetch its timeline from `https://curling.dimmit.net/g/<source>/timeline.json`.
- Copy `cut_vxu9.py` to the box's scratch (`/data/wdd/scratch/game-clips/`).
- Run it in a throwaway container from `curling-worker:local`, with the code under test mounted and the cache read-only:

  ```bash
  docker run --rm -v <scratch>:/s -v <wt rsync>:/wt:ro -v /data/wdd/curling-cache-N:/c:ro \
    --entrypoint python curling-worker:local /s/cut_vxu9.py /wt /s/timeline.json <colour> <slot> /s/out
  ```

  Set `cache.default_root()` to `/c` by exporting `CURLING_SCORE_CACHE=/c`.
- Pull `out/` back and run `stills.py` on it.

Expected: the facing piece opens with the thrower in the crouch about 3 s before the slide, as on VXU9. If the rock is seconds off instead, the recording's clock is not the timeline's. Stop there and report it: the spec says live-charted games then get `video_gone` until that is fixed.

- [ ] **Step 5: The whole affected test set, once more**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_timeline.py tests/test_game_clips.py tests/test_repo_contract.py tests/test_gameclips.py tests/test_game_clips_api.py tests/test_clipworker.py tests/test_worker.py tests/test_plays.py tests/test_practice_api.py tests/test_practice_clips.py tests/test_practice_overlay.py tests/test_viewer_js.py tests/test_practice_js.py tests/test_service_auth.py -q`
Expected: all pass. `tests/test_service_api.py::test_the_pages_load` fails on main already ("Most lopsided"); it is not this branch's.

Report results with the stills and the screenshot. Rollout (worker `up -d --build` with no stream in hand, then `GAME_CLIPS_ENABLED=1` + `deploy-api.sh`) waits for the user's go-ahead.
