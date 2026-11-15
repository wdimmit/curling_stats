# Cameras by default, phase 1: clips of camera games cut right — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A game recorded from the club's cameras gets its clips cut from the camera files, with each view taken from
the right camera. This replaces cropping the YouTube composite with the camera timeline's view-local rects, which
shows a ceiling corner as the overhead piece and the left camera as the right.

**Architecture:**
- A camera run's clip jobs carry `feed: {kind: "cameras", sheet, clock}`.
- The clips worker finds a `ClipSource`, in order:
  1. the kept camera directory;
  2. the live camera directory, found by a `video_id` marker;
  3. the game's YouTube copy (the composite).
- It builds each view as `(file, Cut)`:
  - from camera files, the sheet layout's placements;
  - from the composite of a camera game, the rect moved to its placement's `out`.
- Each rock is cut with `t0_s` = the clock's offset at its release, so published times land on file times.
- A kept directory still being filed or trimmed is "not ready". The job goes back with no attempt used, until a bound
  past the run's `ready_at`, after which the result is `video_gone`.

**Tech Stack:** Python 3.12, ffmpeg (subprocess), FastAPI, Firestore repo (+ in-memory repo in tests), pytest.

**Spec:**
- `docs/superpowers/specs/2026-10-25-pluggable-input-design.md` ("Retention and overnight clips": clips come from the
  local camera feeds);
- the approved overall plan `~/.claude/plans/this-is-a-big-sparkling-gadget.md` (phase 1).

## Global Constraints

- Forward only: nothing is re-cut. `CLIP_STYLE` stays `2` in both `game/clips.py` and `service/gameclips.py`.
- A YouTube-input game's clips are cut exactly as today.
- A clip job queued before this deploy (its spec has no `feed`) is cut as today. No crash.
- The worker's tests run from the worktree: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest <files>`.
  Run subsets only; the full suite OOMs on this box.
- Deploy: the API first, then the clips service alone (`compose build worker` then `up -d clips`). Only with the
  user's go. No version bump.
- Kept camera directory: `<WORKER_CAMS>/kept/<video_id>/{house_a,house_h,long_a,long_h}.ts` + `clock.json`.
- Live camera directory: `<WORKER_CAMS>/live/<WORKER_ID>/<job video_id>/`.

## Review Focus

1. **Pruned files, no copy.** A camera run whose kept files are gone and that has no YouTube copy ends `video_gone`
   past the bound, not in endless retries. Task 5: `test_later_past_the_bound_is_video_gone`.
2. **Failed trim.** A trim that raises leaves no `trim_pending` behind, and clips are then cut from the uncut files on
   the uncut clock. Task 3: `test_a_failed_trim_clears_the_pending_flag`.
3. **Stepped clock.** A rock after a clock step uses the later offset. Task 1: `test_camera_time_after_a_step`.
   Task 4: `test_each_rock_is_cut_at_its_own_offset`.
4. **Changed layout.** A layout changed since the run (placement size ≠ the calibration rect's size) costs that view
   (`no_camera` for rocks needing it), not the job. Task 4: `test_a_view_whose_size_changed_is_left_out`.
5. **Worker ahead of the API.** The worker running against an API without `/later` falls back to `clip_fail`.
   Task 6: `test_not_ready_falls_back_to_fail_on_an_older_api`.

---

### Task 1: `timeline.camera_time`, the inverse of `offset_at`

**Files:**
- Modify: `src/curling_score/timeline.py` (beside `offset_at`, ~:521)
- Test: `tests/test_timeline_clock.py` (create if absent; else the file that tests `offset_at`, found with
  `grep -ln offset_at tests`)

**Interfaces:**
- Produces: `camera_time(clock, v: float) -> float`, the camera time whose `t + offset_at(clock, t)` is `v`;
  `clock_offset_for(clock, v: float) -> float`, the offset in effect at published `v`.

- [ ] **Step 1: Write the failing tests**

```python
from curling_score import timeline

def test_camera_time_one_offset():
    assert timeline.camera_time({"offset_s": -908.157}, 100.0) == pytest.approx(1008.157)

def test_camera_time_after_a_step():
    clock = {"offset_s": -908.94, "steps": [[0.0, -908.94], [3932.0, -914.72]]}
    # before the step: camera 3000 -> video 2091.06
    assert timeline.camera_time(clock, 2091.06) == pytest.approx(3000.0)
    # after it: camera 4000 -> video 3085.28
    assert timeline.camera_time(clock, 3085.28) == pytest.approx(4000.0)
    for t in (10.0, 3931.0, 3932.0, 5000.0):
        assert timeline.camera_time(clock, t + timeline.offset_at(clock, t)) == pytest.approx(t)

def test_camera_time_bare_number():
    assert timeline.camera_time(-5.0, 10.0) == 15.0

def test_clock_offset_for_matches_camera_time():
    clock = {"offset_s": -908.94, "steps": [[0.0, -908.94], [3932.0, -914.72]]}
    assert timeline.clock_offset_for(clock, 3085.28) == pytest.approx(-914.72)
```

- [ ] **Step 2: Run, expect FAIL** (`AttributeError: ... camera_time`):
  `PYTHONPATH=src .../python -m pytest tests/test_timeline_clock.py -q`.
- [ ] **Step 3: Implement `camera_time` and `clock_offset_for` in `timeline.py`.** Use the same `steps` default as
  `offset_at`. Pick the last step i with `v - off_i >= start_i`, falling back to the first step.
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit:** `timeline: camera_time, video time back to the camera's`.

### Task 2: clipburn cuts on a source clock (`t0_s`)

**Files:**
- Modify: `src/curling_score/game/clipburn.py` (`render` :329, `cut` :421)
- Test: `tests/test_clipburn.py`

**Interfaces:**
- Consumes: `practice_clips.decode_args(plan, views, *, t0_s=0.0, ...)` (exists).
- Produces: `render(..., t0_s: float = 0.0)` and `cut(plan, views, out_path, *, throw=None, sideviews=None,
  panels=None, t0_s: float = 0.0, **kw)`. Plan, marks and dot times stay on the plan's (published) clock; only the
  decoder reads `t0_s`.

- [ ] **Step 1: Write the failing test.** Use the module's `stream` fixture: 6 s, left view red above and blue below.
  Add a module fixture `halves`, a 6 s 120x90 file that is red for t<3 and blue for t≥3.

```python
@needs_ffmpeg
def test_a_source_on_another_clock_is_cut_where_the_plan_says(halves, tmp_path):
    plan = P.ClipPlan((P.Piece("left", 11.0, 12.0),))
    out = tmp_path / "c.mp4"
    B.render(plan, {"left": (halves, None)}, out, [{}], t0_s=10.0)   # file 1-2 s: red
    assert near(frame_at(out, 0.5)[45, 60], (0, 0, 255))
    B.render(plan, {"left": (halves, None)}, out, [{}], t0_s=7.5)    # file 3.5-4.5 s: blue
    assert near(frame_at(out, 0.5)[45, 60], (255, 0, 0))
```

- [ ] **Step 2: Run, expect FAIL** (`unexpected keyword argument 't0_s'`).
- [ ] **Step 3: Implement.** `render` passes `t0_s=t0_s` to `decode_args`; `cut` passes it on to `render`.
- [ ] **Step 4: Run `tests/test_clipburn.py`, expect PASS.**
- [ ] **Step 5: Commit:** `clipburn: cut from a source on its own clock (t0_s)`.

### Task 3: kept and live camera directories say what they are

**Files:**
- Modify: `src/curling_score/live/manager.py` (`_file_camera_game` :242, `_begin` :333, `_hand_over` :365)
- Modify: `src/curling_score/cameras/trim.py` (`trim_kept` :99)
- Test: `tests/test_cameras_trim.py`, `tests/test_live_lane.py` (beside the test asserting the kept layout, ~:1196)

**Interfaces:**
- Produces:
  - `clock.json` gains `kept_at` (unix seconds) and, while a trim is to follow, `"trim_pending": true`.
  - `trim.clear_pending(directory) -> None` drops the flag.
  - Each live camera directory holds a `video_id` text file with the job's real video id, written by `_begin`
    (camera jobs) and by `_hand_over`, and removed with the other non-`.ts` files at keep.
  - Constant `trim.PENDING = "trim_pending"`.

- [ ] **Step 1: Write the failing tests.**
  - `test_trim_kept_clears_the_pending_flag` (test_cameras_trim): `clock.json` with `trim_pending: true`, then
    `trim_kept(d, 5.0)`, then the key is absent, `start_trimmed_s == 5.0`, and `kept_at` is kept.
  - `test_a_cut_too_small_still_clears_the_flag`: `trim_kept(d, 0.2)` returns 0.0 and the flag is gone.
  - `test_a_failed_trim_clears_the_pending_flag` (test_live_lane, standby game with `cut ≥ MIN_CUT_S`, `trim_kept`
    monkeypatched to raise): after the background runs, `clock.json` has no `trim_pending` and has `kept_at`.
  - `test_a_standby_game_is_kept_with_its_trim_pending_until_cut`: with `self.background` captured and not yet run,
    `clock.json["trim_pending"] is True`; after it runs, absent.
  - `test_a_camera_recording_names_its_video`: after `_begin` of a `camera_live` job, `<dir>/video_id` reads the
    job's video id. After `_hand_over` of a standby, it reads the game's id.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.**
  - `_file_camera_game` writes `kept_at=time.time()` always, and `trim_pending: True` only when
    `cut >= trim.MIN_CUT_S`.
  - Its `trim_then_prune` calls `trim.clear_pending(dest)` in a `finally`.
  - `trim_kept` drops the key when it rewrites, including when `made` is 0, through `clear_pending`.
  - The marker is written with `Path(rec.dir) / "video_id"` when the recorder has a `dir`; a missing `dir` is
    skipped.
- [ ] **Step 4: Run both test files, expect PASS.**
- [ ] **Step 5: Commit:** `cameras: kept games say when their trim is pending, live ones name their video`.

### Task 4: `game/clips` cuts from a `ClipSource`

**Files:**
- Modify: `src/curling_score/game/clips.py`
- Test: `tests/test_game_clips.py`

**Interfaces:**
- Consumes:
  - `timeline.clock_offset_for` (Task 1);
  - `clipburn.cut(..., t0_s=)` (Task 2);
  - the `clock.json` keys (Task 3);
  - `feeds.cameras.{VIEW_ROLE, placement_for, view_cut}`, `cameras.layout.load(sheet)`.
- Produces:

```python
class NotReady(RuntimeError): ...   # files expected but not there yet, or still being trimmed

@dataclass(frozen=True)
class ClipSource:
    kind: str                 # "youtube" | "composite" (a camera game's YouTube copy) | "cameras"
    path: Path                # the composite file, or the camera directory
    sheet: int | None = None
    clock: dict | None = None # "cameras": maps the rocks' published t to the files' t

    def t0_at(self, v: float) -> float: ...   # 0.0 unless "cameras": clock_offset_for(clock, v)

def find_source(video_id: str, roots, cams_roots=(), *, feed: dict | None = None) -> ClipSource | None
def source_views(source: ClipSource, sideviews: dict, panels: dict) -> dict  # name -> (file, rect | Cut)
def clip_rocks(rocks, source, sideviews, workdir, deliver, *, panels=None, cut=clipburn.cut, on_rock=None) -> dict
```

`clip_rocks` still accepts a bare `Path` as `source`, read as `ClipSource("youtube", path)`.

**`find_source` order.**
- With `feed` (a camera game), for each `cams` root:
  1. `cams/kept/<vid>/`:
     - with `clock.json` and no `trim_pending`: `ClipSource("cameras", dir, feed["sheet"], clock_json["clock"])`;
     - otherwise (no `clock.json` yet, or pending): raise `NotReady`.
  2. A directory `cams/live/*/*/` whose `video_id` file reads `vid` and that holds `long_a.ts`:
     `ClipSource("cameras", dir, sheet, feed["clock"])`.
  3. `videos/<vid>.mp4` in `roots`: `ClipSource("composite", path, sheet)`.
  4. Nothing: raise `NotReady`. The API decides between waiting and gone.
- Without `feed`: today's `find_video` lookup, wrapped as `"youtube"`, or None.
- `kept/<id>.ts` partials stay unused, as today.

**`source_views`.**
- `"youtube"`: `{name: (path, sv.rect)}` for the side views, plus each panel `(path, panel.rect)`.
- `"composite"`: the same rects with their origin moved to `placement_for(layout, name).out[:2]`, and only when
  `rect[2:] == out[2:]`.
- `"cameras"`: `(path / f"{VIEW_ROLE[name]}.ts", view_cut(name, placement))`, and only when
  `view_cut(...).out_size == rect[2:]`.
- A view failing its guard, or with no layout, is left out with a warning.
- `clip_rocks` cuts each rock with `cut(..., t0_s=source.t0_at(rock["t_release_s"]))`. The overhead entry is
  `views[house]` in place of `(video, panel.rect)`.

- [ ] **Step 1: Write the failing tests** (in `TestFindSource`, `TestSourceViews`, and the clip_rocks class).
  - `test_a_camera_game_prefers_its_kept_cameras(tmp_path)`: kept dir + `clock.json` + a `videos/<vid>.mp4` gives
    `kind == "cameras"`, with the clock from `clock.json`.
  - `test_a_pending_trim_is_not_ready` and `test_a_kept_dir_without_its_clock_is_not_ready`:
    `pytest.raises(clips.NotReady)`.
  - `test_a_live_camera_recording_is_found_by_its_marker`: `cams/live/w1/standby-3-x/` with `video_id` = vid and
    `long_a.ts` gives `kind == "cameras"` and `clock == feed["clock"]`.
  - `test_a_camera_game_with_only_its_youtube_copy_is_the_composite`: gives `kind == "composite"`.
  - `test_a_camera_game_with_nothing_is_not_ready`.
  - `test_a_youtube_game_is_found_as_before`: no `feed` gives `kind == "youtube"`. With nothing it gives `None`.
  - `test_camera_views_come_from_each_camera`: sheet 3 layout, calibration rects `[0,0,*out]`. Then
    `views["right"][0].name == "long_h.ts"`, `views["top"][0].name == "house_h.ts"`, and
    `views["top"][1] == view_cut("top", placement_for(layout.load(3), "top"))`.
  - `test_composite_views_move_to_the_placements`: `views["right"][1] == (out.x, out.y, 800, 1080)` for sheet 3's
    long_h placement, and the same for top.
  - `test_a_view_whose_size_changed_is_left_out`: a rect of `[0,0,801,1080]` leaves `"right"` absent and `"left"`
    present.
  - `test_each_rock_is_cut_at_its_own_offset`: fake `cut` recording `t0_s`. With a two-step clock, a rock before
    the step gets the first offset and one after gets the second.
  - `test_a_camera_clip_shows_the_house_camera_overhead`: a fake camera dir of 4 tiny ffmpeg colour files (house_h
    lime, long_a red, long_h blue, house_a yellow), a 5 s rock thrown to `top`, with the real `clipburn.cut`. Frames
    early in the clip are not lime (a long camera); late frames are lime (house_h). Skip without ffmpeg, like the
    module's other cut tests.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** per the Interfaces block. Keep `find_video` as a thin wrapper used by the `"youtube"`
  branch.
- [ ] **Step 4: Run `tests/test_game_clips.py tests/test_clipburn.py`, expect PASS.**
- [ ] **Step 5: Commit:** `game clips: cut a camera game from its cameras, or its copy placed by the layout`.

### Task 5: the API puts the feed in the spec, and puts a not-ready job back

**Files:**
- Modify: `src/curling_score/service/gameclips.py` (`ask_shot`, `ensure`, new `camera_feed_spec`, new `later`)
- Modify: `src/curling_score/service/api.py` (`ask_for_clips` ~:2703, the shot ask ~:2838, new route beside
  `/api/worker/clips/{job_id}/fail` ~:3648)
- Modify: `src/curling_score/service/worker.py` (`ApiClient.clip_later`, beside `clip_fail` ~:166)
- Test: `tests/test_gameclips.py`, `tests/test_game_clips_api.py`

**Interfaces:**
- Produces:
  - `camera_feed_spec(run, doc: dict) -> dict | None`: `{"kind": "cameras", "sheet": int, "clock": dict}` when
    `run.source == "cameras"` (or `doc["source"]["clock"]["kind"] == "cameras"`), the sheet is known
    (`run.sheet or doc["source"]["sheet"]`) and the doc has a clock; else None.
  - `ask_shot(..., calibration, now, *, feed=None)` and `ensure(..., calibration, now, *, again=False, feed=None)`
    put `"feed": feed` in the spec when given.
  - `NOT_READY_MAX_S = 3 * 3600`, `NOT_READY_WAIT_S = 120`.
  - `later(repo, job, run, reason: str, now) -> bool`: True when put back. While `run.ready_at` is None or
    `now < run.ready_at + NOT_READY_MAX_S`, it puts the job back: `queued`, `run_after = now + NOT_READY_WAIT_S`,
    `attempts = max(0, job.attempts - 1)`, no worker or lease. Past the bound, it finishes the job `done`, every
    rock `{"clip": None, "reason": "video_gone"}`, and returns False.
  - `POST /api/worker/clips/{job_id}/later {worker_id, reason}` → `{"ok": True, "again": bool}`.
  - `ApiClient.clip_later(job_id, worker_id, reason)`.

- [ ] **Step 1: Write the failing tests.**
  - `test_camera_feed_spec_for_a_camera_run` / `_none_for_a_youtube_run` / `_none_without_a_clock`.
  - `test_ask_shot_carries_the_feed`: the spec has `feed`.
  - `test_ensure_carries_the_feed`.
  - `test_later_puts_it_back_without_an_attempt`: attempts 1 → 0, `run_after == now + 120 s`.
  - `test_later_past_the_bound_is_video_gone`: `now = ready_at + 3 h + 1 s` gives state `done`, every rock
    `video_gone`, and returns False.
  - API: `test_a_worker_can_put_a_clip_job_back` (claim, then `/later`, then queued with `again: True`), and
    `test_the_shot_ask_of_a_camera_run_carries_its_feed`. The camera run's doc has
    `source: {clock: {kind: "cameras", ...}, sheet: 3}`, so the stored job's `spec["feed"]["sheet"] == 3`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.** Both API call sites pass `feed=gameclips.camera_feed_spec(run, doc)`. The route uses
  `owned_clip_job`, like `/fail`.
- [ ] **Step 4: Run `tests/test_gameclips.py tests/test_game_clips_api.py`, expect PASS.**
- [ ] **Step 5: Commit:** `clip jobs: a camera run's feed in the spec; not-ready jobs go back without an attempt`.

### Task 6: the clips worker reads the cameras

**Files:**
- Modify: `src/curling_score/service/clipworker.py`
- Test: `tests/test_clipworker.py`

**Interfaces:**
- Consumes: `find_source`, `NotReady`, `clip_rocks(source=...)` (Task 4); `ApiClient.clip_later` (Task 5).
- Produces:
  - `process(job, api, worker_id, *, sources, workdir, cams=(), cut=None, sleep=...)`;
  - `run_forever(..., cams=())`;
  - `main` reads `WORKER_CAMS` (one path, may be unset);
  - the startup check passes when any source has `videos/` or `cams/kept` exists.

- [ ] **Step 1: Write the failing tests** (using the module's fake API, extended with `clip_later`).
  - `test_a_camera_job_is_cut_from_its_kept_cameras`: a kept dir with 4 files + `clock.json`, a fake `cut`. The cut
    sees `views["right"][0].name == "long_h.ts"` and `t0_s` from the clock.
  - `test_a_not_ready_camera_job_goes_back`: kept dir with `trim_pending` gives `api.later_calls == [job_id]`, no
    complete, no fail.
  - `test_not_ready_falls_back_to_fail_on_an_older_api`: the fake `clip_later` raises `HTTPStatusError` 404, so
    `clip_fail` is called.
  - `test_an_old_job_without_a_feed_is_cut_as_before`: a camera run's job with no `feed` and a `videos/<id>.mp4` is
    cut from the composite with the spec's rects unchanged.
  - `test_starts_with_only_the_cameras` (main): sources without `videos/` but `WORKER_CAMS` with `kept/` doesn't
    return 2.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.**
  - `_process` calls `find_source(..., feed=job["spec"].get("feed"))`.
  - On `NotReady` it calls `api.clip_later(job["id"], worker_id, str(exc))` and returns `{}`. If that raises
    `httpx.HTTPStatusError` with status 404, it raises to the caller, which reports `clip_fail` as today.
  - `None` without a feed stays `video_gone`. The `live` spec case is unchanged.
- [ ] **Step 4: Run `tests/test_clipworker.py tests/test_game_clips.py`, expect PASS.**
- [ ] **Step 5: Commit:** `clips worker: camera games cut from the cameras`.

### Task 7: offline gate on the Ryzen, and the results

**Files:**
- Create: `docs/superpowers/plans/2026-11-14-cameras-default-p1-clips.results.md`
- Scratch (not committed): `/data/wdd/scratch/camera-direct/p1-clips/` on the Ryzen

- [ ] **Step 1: Pick the sources.**
  - The newest kept camera directory on the Ryzen (`ls -t /data/wdd/curling-cams/kept`). If none is kept yet, wait
    for the next camera night (Sunday 11/15 Skips).
  - One 11/10 camera game's YouTube copy: `47svJJgf804` (Supper sheet 4, run `r_03Cd78KraRUbGcC5n`, timeline in the
    session scratchpad `tl1110/`).
- [ ] **Step 2: Cut in a throwaway container.**
  - Mount the branch's `src` into `curling-worker:local` with `--cpus 4`, the caches and `/data/cams` read-only.
  - Use a script `cut_sample.py <timeline.json> <rock names...>` that builds the spec with `camera_feed_spec`,
    `find_source` and `clip_rocks` with a local `deliver` copying into `out/`.
  - Cut at least: rocks 1 and 2 of an end thrown to each house, on every sheet available.
  - Do not run while a league or practice is being recorded (memory `no-camera-tests-during-practice`).
- [ ] **Step 3: Check frames.**
  - Take the first and last frames of each clip (ffmpeg `-ss 1` / `-sseof -1.5`).
  - Piece one is the long camera facing the thrower. The last piece is the house camera over the target house, the
    house upright. The delivery marks sit on the stone.
  - Compare with the broken 11/10 clips (`0.1.1`, `0.1.2`).
- [ ] **Step 4: Write the results doc** (sources, rocks, frames, verdict) and commit:
  `docs: phase 1 clip gate results`.
- [ ] **Step 5: Hand the deploy to the user.**
  - The steps: API from a clean worktree of the branch with `firebase_api_key.json` copied in; the clips service
    alone via rsync + `compose build worker` + `up -d clips`.
  - Check the newest Cloud Run revision against the branch first.
  - Deploy only on the user's go.
