# False-Positive Frame Harvest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Each hosted worker saves up to 12 whole frames per game where a trustworthy rejection check found the rock models wrong, for a later labelling round.

**Architecture:** A new package `curling_score.fpframes` works in three stages:
1. **Record.** A `Recorder`, made active through a context variable, collects *firings* at six hook sites and is stamped once per end in `build_one_end`.
2. **Choose.** After a game's ends are built, `choose` picks frames within the caps.
3. **Save.** `store.save_frames` cuts whole frames from the video into `<cache root>/harvest/fp/<video_id>/`.

The VOD worker turns recording on around `analyze()`. The live lane turns it on around each end's build and saves on the recording-keeping thread, before `live/<vid>` is deleted.

**Tech Stack:** Python 3.12, PyAV via `ingest.frames`, OpenCV (`cv2.imwrite`), pytest.

**Spec:** `docs/superpowers/specs/2026-10-21-fp-frame-harvest-design.md`. Read it first: the per-check numbers and the reasons behind every limit are there.

## Global Constraints

- **Switch:** on only when `WORKER_FP_HARVEST=1`. Disk cap from `WORKER_FP_HARVEST_GB`, default `20`.
- **Checks, in this order** (the order matters for trimming to the per-game cap):
  `side.width_far`, `side.width_off`, `side.lone`, `ovh.quiet`, `ovh.lateral`, `ovh.wander`.
- **Width bands:** a long-camera width ratio `<= 0.4` or `>= 2.0` is `side.width_far`. Any other ratio the pass itself refuses is `side.width_off`.
- **Limits:** at most 2 frames per check and 12 per game. At least 5 s apart within a check.
- **Saving:** whole frame, JPEG quality 92, nothing drawn. Files `0o644`, directories `0o755`.
- **Layout:** `<root>/harvest/fp/<video_id>/<stem>.jpg` and `<root>/harvest/fp/<video_id>/frames.jsonl`.
- **Stem:** `f"{vid}_{where}_{t:09.2f}".replace(".", "_") + "_" + check.split(".", 1)[1]`.
- **Reruns:** no frames are saved for a video whose `frames.jsonl` already has a row with the same `models` dict.
- **Quiet spans:** only between two kept rocks, from `t_rest + 6 s` to the next `t_enter - 6 s`, at least 12 s long (`train.dataset.QUIET_MARGIN_S`, `MIN_QUIET_S`). Skip a span where `settled_only` returns `[]`.
- **Output and failure:** timelines are byte-identical with the feature on or off. No `PIPELINE_VERSION` bump. No exception from `fpframes` ever reaches a job.
- **Live timing:** no frame cutting inside an end's build. On live, frames are cut on `LiveManager`'s background keep thread.
- **Where to work:** a worktree on a new branch `fp-harvest` from `main`. Another session shares the main checkout, so commit only this feature's files.
- **Tests:** run the affected test files, never the whole suite, which OOMs on the dev box (exit 137). `tests/test_longview_handmarks*` and `test_the_pages_load` already fail on main; ignore them.

## Review Focus

1. **A live end built twice.** When the calibration improves, `LiveSession._build` rebuilds an end. The second build's firings must replace the first's, not add to them. *Task 1: `test_closing_the_same_end_again_replaces_its_firings`.*
2. **Two games in one recording.** Super League streams hold two games, so the caps must apply per game, not per video. *Task 2: `test_caps_apply_per_game_not_per_video`.*
3. **A frame that can't be read at save time.** A pruned video, a failed remux or a seek past the end: that frame is skipped, the rest are saved, and nothing is raised. *Task 2: `test_an_unreadable_frame_is_skipped_and_the_rest_saved`.*
4. **A half-written `frames.jsonl` line** left by a worker killed mid-write. Reading skips it, and the rerun rule still sees the good rows. *Task 2: `test_a_torn_line_is_skipped`.*
5. **Practice sessions and the clips worker.** They must never record or save. *Task 6: `test_a_practice_session_gets_no_recorder`.*

---

### Task 1: The recorder

**Files:**
- Create: `src/curling_score/fpframes/__init__.py`
- Create: `src/curling_score/fpframes/record.py`
- Test: `tests/test_fpframes_record.py`

**Interfaces:**
- Produces in `fpframes/__init__.py`:
  - `CHECKS: tuple[str, ...]` (order as in Global Constraints), `WIDTH_FAR = (0.4, 2.0)`.
  - `@dataclass(frozen=True) class Settings: enabled: bool = False; cap_gb: float = 20.0`.
  - `settings(env=os.environ) -> Settings`.
  - `model_ids(weights) -> dict`, which is `{"overhead": version.model_id(weights), "side": version.model_id(p) if (p := weights_mod.side_path()) else None}`. Import `version` and `weights` inside the function so the package stays import-light.
- Produces in `fpframes/record.py`:
  - `@dataclass class Firing`: `check: str`, `t: float`, `camera: str` (`"overhead"` or `"side"`), `color: str | None`, `conf: float | None`, `score: float`, `info: dict`, `box: tuple | None = None` (whole-frame x0, y0, x1, y1), `top_estimated: bool = False`, `samples: tuple = ()` (overhead `(t, x, y)` before joining), `rect: tuple | None = None`, `where: str | None = None`, `house: str | None = None`, `game: int | None = None`, `end: int | None = None`. `close_end` stamps `house`, `game` and `end` on every firing, side ones included.
  - `class Recorder(video_id: str, *, models: dict, pipeline: str, job: str | None = None, title: str | None = None)`. Attributes: those, plus `video: Path | None`. Methods:
    - `overhead_track(check, track, end_i=None, **info) -> None`. `track` is a `delivery._Track`, cut at `end_i`. Score = number of samples. Keep at most 5 samples, spread evenly.
    - `side_width(view, t, box, conf, ratio, *, color, pass_, top_estimated) -> None`. `box` is in view pixels. The check comes from `width_check(ratio)`. Score = `abs(log(ratio))`. `info = {"ratio": ratio, "pass": pass_}`.
    - `side_view(view) -> ContextManager`: sets the view `side_lone` uses.
    - `side_lone(t, cx, row, conf) -> None`. Width = `0.291 * view.lateral_px_per_m(row)`, box `(cx - w/2, row - w/2, cx + w/2, row)` with an estimated top. Score = conf.
    - `begin_end() -> None`: drops anything pending.
    - `close_end(*, game, end, house, video, near_rect, near_seq, cameras) -> None`. `cameras` maps `tuple(view.rect)` to a camera name. It stamps and stores the pending firings under `(game, end)`, replacing what was there.
    - `pending() -> list[Firing]` and `firings() -> list[Firing]` (all closed ends).
  - `recording(recorder: Recorder | None) -> ContextManager`, `active() -> Recorder | None`, `width_check(ratio: float) -> str`.

**How it behaves:**
- **Thinning, to keep hooks cheap.** Side firings keep only the highest score per `(check, tuple(view.rect), round(t * 2))`. No I/O happens at a hook.
- **Joining, in `close_end`.** An overhead firing's samples are matched to `near_seq` detections by `round(t, 3)`, colour, and `|x - x_m| < 1e-6` and `|y - y_m| < 1e-6`. The firing takes the middle matched sample's `t` and confidence. Its box is centred on `(x_px + near_rect[0], y_px + near_rect[1])`, with side `sqrt(area_px)`. A firing with no match is dropped. `where = house`.
- **Side firings in `close_end`.** Their box is shifted by `view.rect[0:2]`, and `where = cameras.get(rect, "side")`.
- **Guarding.** Every public method is wrapped so that an exception is logged (`log.exception`) and swallowed.

- [ ] **Step 1: Write the failing tests** in `tests/test_fpframes_record.py`. Use `tests/test_delivery.py`'s `det`/`merge` style, `SideView` from `curling_score.geometry.sideview` for views, and a `delivery._Track` built with `.add()`.
  - `test_settings_read_the_switch_and_cap`: `settings({}) == Settings(False, 20.0)`; `settings({"WORKER_FP_HARVEST": "1", "WORKER_FP_HARVEST_GB": "5"}) == Settings(True, 5.0)`.
  - `test_nothing_is_active_by_default_and_recording_none_is_a_no_op`: `active() is None` both outside and inside `with recording(None):`.
  - `test_an_overhead_track_is_joined_to_its_detection_box`: with near_rect `(800, 554, 300, 516)` and a detection `x_px=100, y_px=50, area_px=400`, the firing's box is `(890.0, 594.0, 910.0, 614.0)`, `where == "bottom"`, and `conf` is the detection's.
  - `test_a_track_with_no_matching_detection_is_dropped`: `firings() == []`.
  - `test_width_bands`: `width_check(0.4) == width_check(2.0) == "side.width_far"`; `width_check(0.45) == width_check(1.6) == "side.width_off"`.
  - `test_side_boxes_are_thinned_per_half_second`: three `side_width` calls on one view at t = 10.0, 10.1, 10.2 with ratios 2.5, 3.0, 2.2 leave one firing with `info["ratio"] == 3.0`. Its box is shifted by the view rect, and `where` is the name `cameras` gives.
  - `test_closing_the_same_end_again_replaces_its_firings`: close `(0, 3)` with 2 firings, then `(0, 3)` again with 1. `len(firings()) == 1`.
  - `test_a_raising_hook_is_swallowed`: `overhead_track("ovh.wander", object())` does not raise.
- [ ] **Step 2: Run them and see them fail.** `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_fpframes_record.py`. Expected: `ModuleNotFoundError: curling_score.fpframes`.
- [ ] **Step 3: Implement** `fpframes/__init__.py` and `fpframes/record.py` to the interfaces above. `_ACTIVE` is a `contextvars.ContextVar("fpframes_recorder", default=None)`.
- [ ] **Step 4: Run the tests.** Expected: all pass.
- [ ] **Step 5: Commit.** `git add src/curling_score/fpframes tests/test_fpframes_record.py && git commit -m "fpframes: a recorder for the checks that find false positives"`

### Task 2: Choosing and saving frames

**Files:**
- Create: `src/curling_score/fpframes/choose.py`
- Create: `src/curling_score/fpframes/store.py`
- Test: `tests/test_fpframes_store.py`

**Interfaces:**
- Consumes: `Firing`, `Recorder`, `CHECKS` (Task 1).
- Produces in `choose.py`: `choose(firings: list[Firing], *, per_check: int = 2, per_game: int = 12, spacing_s: float = 5.0) -> list[Firing]`.
- Produces in `store.py`:
  - `video_dir(root: Path, video_id: str) -> Path`, which is `root / "harvest" / "fp" / video_id`.
  - `read_rows(root, video_id) -> list[dict]`.
  - `has_models(rows, models) -> bool`.
  - `size_gb(root) -> float`, the size of `root/harvest/fp`.
  - `stem_for(video_id, firing) -> str`.
  - `read_frame(video, t) -> np.ndarray | None`: the first frame of `ingest.frames.window(video, max(0.0, t - 1/60), t + 0.25, 60.0)`, or None.
  - `save_frames(recorder, *, root, video=None, cap_gb=20.0, reader=read_frame, now=time.time) -> int`. It returns how many frames it saved. `video` defaults to `recorder.video`.

**How `choose` picks:**
1. Drop firings with no box. Group the rest by `game`.
2. Within a game, take each check in `CHECKS` order. Sort its firings by `(-score, t)`.
   - Pass 1 picks at most one per end.
   - Pass 2 fills up to `per_check`.
   - A pick must be at least `spacing_s` from that check's other picks.
3. Merge the checks: every check's first pick in `CHECKS` order, then every check's second pick. Stop at `per_game`.
4. Return sorted by `(game, t)`.

**A row of `frames.jsonl` holds:**
- `stem`, `check`, `camera`, `where`, `t` (rounded to 0.001), `box` (rounded to 0.1), `top_estimated`, `color`, `conf`, `info`, `rect`;
- `game`, `end`, `house`, `video_id`, `title`, `pipeline`, `models`, `job`;
- `saved_at`, as ISO UTC from `now`.

**How `save_frames` behaves:**
- It saves nothing, and logs once per process, when `size_gb(root) >= cap_gb`.
- It saves nothing when `has_models(read_rows(...), recorder.models)`.
- Each frame is written with `cv2.imwrite(..., [cv2.IMWRITE_JPEG_QUALITY, 92])`, then its row is appended to `frames.jsonl` and the file chmodded.
- A frame that `reader` returns None for, or raises on, is skipped.
- The whole function never raises.

- [ ] **Step 1: Write the failing tests.** Use `tmp_path` and a fake `reader` returning `np.zeros((1080, 1920, 3), np.uint8)`.
  - `test_at_most_two_per_check_and_twelve_per_game`: 6 checks × 5 well-spaced firings in one game give 12 picks, 2 per check.
  - `test_picks_within_a_check_are_five_seconds_apart`: firings at t = 100, 102, 104, 120 (scores 4, 3, 2, 1) pick t = 100 and 120.
  - `test_the_first_pick_of_a_check_spreads_across_ends`: two high scores in end 1 and a lower one in end 2 pick one from each end.
  - `test_caps_apply_per_game_not_per_video`: two games of 14 firings each give 24 picks.
  - `test_saving_writes_jpegs_and_rows_readable_by_all`: 3 picks give 3 files plus 3 rows. Each file has mode `0o644`, and a row has the stem from `stem_for` and the recorder's `models`.
  - `test_a_rerun_with_the_same_models_saves_nothing`: the second `save_frames` returns 0. With other `models` it saves again.
  - `test_an_unreadable_frame_is_skipped_and_the_rest_saved`: a reader that raises for one t and returns None for another gives exactly the other picks.
  - `test_a_torn_line_is_skipped`: a `frames.jsonl` of one good row plus `'{"stem": "x'` gives `read_rows(...) == [good]`.
  - `test_over_the_cap_nothing_is_saved`: a 2 MB file under `harvest/fp` with `cap_gb=0.001` gives 0.
- [ ] **Step 2: Run them and see them fail.** `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_fpframes_store.py`. Expected: `ImportError`.
- [ ] **Step 3: Implement** `choose.py` and `store.py` as described.
- [ ] **Step 4: Run them.** `... tests/test_fpframes_store.py tests/test_fpframes_record.py`. Expected: all pass.
- [ ] **Step 5: Commit.** `git commit -m "fpframes: choose a game's frames and save them under harvest/fp"` (add the two modules and the test file).

### Task 3: Overhead checks and the end's lifecycle

**Files:**
- Modify: `src/curling_score/detect/delivery.py`, in `find_deliveries` (the `MIN_NET_TRAVEL_FRACTION` and `MAX_LATERAL_RATIO` rejections, about lines 1082-1085).
- Modify: `src/curling_score/analyze.py`:
  - `EndContext` (line 265);
  - `build_one_end` (line 445): its start, after `dropped =` (line 581), and before `return built, closed_s`;
  - the `EndContext(` call in `analyze()` (about line 1011).
- Test: `tests/test_fpframes_overhead.py`

**Interfaces:**
- Consumes: `record.active()` and `Recorder.overhead_track`, `begin_end`, `close_end` (Task 1).
- Produces:
  - `EndContext.frame_setups: dict | None = None`: the panels in `path`'s coordinates, which `analyze()` fills with `setups`. None means `read_setups` already are, as for a live recording.
  - `analyze._record_quiet(rec, seq, kept) -> None`.

**What to add:**
- **`find_deliveries`.** Fetch `rec = fp_record.active()` once at the top (`from curling_score.fpframes import record as fp_record`). Then:
  - at the wander `continue`: `rec.overhead_track("ovh.wander", track, end_i, net=round(travelled / path, 3) if path > 0 else None)`;
  - at the lateral `continue`: `rec.overhead_track("ovh.lateral", track, end_i, travelled=round(travelled, 3))`.
- **`build_one_end`.**
  - First line, when `rec` is set: `rec.begin_end()`.
  - After the final `kept`: `_record_quiet(rec, seq, kept)`.
  - Just before returning: `rec.close_end(game=game.index, end=end.number, house=end.house, video=path, near_rect=(ctx.frame_setups or read_setups)[end.house].rect, near_seq=seq, cameras={tuple(v.rect): n for n, v in (sideviews or {}).items()})`.
- **`_record_quiet`.** For consecutive kept rocks `a`, `b`:
  1. Take the span `(a.t_rest + DS.QUIET_MARGIN_S, b.t_enter - DS.QUIET_MARGIN_S)`. Skip it if shorter than `DS.MIN_QUIET_S`, or if `DS.settled_only(seq, lo, hi)` is `[]`.
  2. The stray boxes are those in the span missing from `settled_only`'s output, compared by identity.
  3. Group them with `delivery._build_tracks`. Each track becomes `rec.overhead_track("ovh.quiet", tr, quiet=[round(lo, 2), round(hi, 2)])`.
  4. Import `curling_score.train.dataset as DS` inside the function.

- [ ] **Step 1: Write the failing tests.**
  - `test_a_wandering_blob_is_a_wander_firing`: reuse `tests/test_delivery.py::test_ignores_a_blob_that_loiters_near_the_hog_line`'s trace under `with recording(rec):`. Then `[f.check for f in rec.pending()] == ["ovh.wander"]`, with colour yellow.
  - `test_a_track_moving_sideways_is_a_lateral_firing`: a red trace from `(x=-1.0, y=6.0)` to `(1.0, 5.0)` over 10 frames at 5 fps gives one `ovh.lateral`.
  - `test_nothing_is_recorded_without_a_recorder`: same traces outside `recording`. `find_deliveries` returns what it did before, and nothing raises.
  - For the end-level tests, monkeypatch `analyze.sequence.detect_end` and `detect_span` as `tests/test_analyze_split.py::seen` does. Return a `merge(...)` of:
    - a red `thrown` at `t0=1100` resting at `x=0.2`;
    - a yellow `thrown` at `t0=1160` resting at `x=-0.6`;
    - one stray red `det(1.5, 1.0)` with `x_px=40, y_px=60, area_px=400` at t = 1135-1136, every frame.

    The tests:
    - `test_a_stray_box_between_two_kept_rocks_is_a_quiet_firing`: after `build_one_end` under a recorder, `rec.firings()` has an `ovh.quiet` firing near t = 1135.5 whose box centre is `(40 + rect x, 60 + rect y)`, with `game == 0` and `end == 1`.
    - `test_the_end_is_byte_identical_with_and_without_a_recorder`: `json.dumps(built, sort_keys=True)` is equal both ways.
    - `test_frame_setups_place_the_box_in_the_original`: with `frame_setups` rects offset by `(807, 554)` from `read_setups`, the box centre moves by that offset.
- [ ] **Step 2: Run and see them fail.** `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_fpframes_overhead.py`.
- [ ] **Step 3: Implement** the changes above.
- [ ] **Step 4: Run them, plus the files these modules already have.** `... tests/test_fpframes_overhead.py tests/test_delivery.py tests/test_analyze_split.py tests/test_one_pass.py tests/test_analyze_side.py tests/test_analyze_stages.py`. Expected: all pass.
- [ ] **Step 5: Commit.** `git commit -m "fpframes: record wandering, sideways and quiet-span overhead boxes"`

### Task 4: Long-camera checks

**Files:**
- Modify: `src/curling_score/detect/sidemodel.py`, in `propose`, at the `WIDTH_TOL` rejection (about line 110).
- Modify: `src/curling_score/game/linetime.py`:
  - `path_points` (`PATH_WIDTH` rejection);
  - `chain` (the `PATH_START_GAP_S` skip);
  - `find_path` (wrap the `chain` call).
- Modify: `src/curling_score/game/farfollow.py`, in `follow`'s box loop.
- Modify: `src/curling_score/game/sidereleases.py`, in `stone_points`' box loop.
- Test: `tests/test_fpframes_side.py`

**Interfaces:**
- Consumes: `record.active()`, `Recorder.side_width`, `side_view`, `side_lone` (Task 1).
- Produces: nothing new.

**What each site records** (fetch `rec` once per call, and do nothing when it is None):
- **`propose`.** When `expect > 1` and the ratio is outside `WIDTH_TOL`: `rec.side_width(view, t, (bx0, by0 + lo, bx1, by1 + lo), float(cf), body_px / expect, color=color, pass_="hog", top_estimated=False)`. The x coordinates are already in view pixels (`win`).
- **`path_points`.** On a `PATH_WIDTH` rejection: `ratio = w / (0.291 * view.lateral_px_per_m(row))`, box `(cx - w/2, row - w/2, cx + w/2, row)`, `color=None`, `pass_="path"`, `top_estimated=True`.
- **`farfollow.follow`.** For each box that passes the `EDGE_ROWS` check: `ratio = w / view.stone_width_at(row, longview.STONE_WIDTH_AT_HOG_PX)`. Record when it falls outside `sidemodel.WIDTH_TOL`, with `pass_="follow"` and the follow's `color`. Accept nothing new: the pass's output must not change.
- **`sidereleases.stone_points`.** The same ratio and band, with `pass_="slide"` and `color=None`.
- **`chain`.** At the skipped start, which is also a start with `len(got) == 1` (no second sighting): `rec.side_lone(d[0], d[3], d[4], d[5])`, where `d = got[0]`. The skip itself is unchanged, apart from also skipping `len(got) == 1`, which `len(got) < 5` already does.
- **`find_path`.** `with (rec.side_view(view) if rec else nullcontext()):` around building `per`/`other` and calling `chain`.

- [ ] **Step 1: Write the failing tests.**
  - `test_a_frame_sized_box_at_the_hog_is_a_far_width_firing`: `tests/test_sidemodel.py`'s `FakeModel` with one box at 6× the expected width gives one `side.width_far` firing with `info["pass"] == "hog"`.
  - `test_a_half_width_box_on_the_path_is_an_off_width_firing`: `path_points` with `DEST_VIEW` (from `tests/test_linetime.py`) and `w = 0.55 × 0.291 × lateral_px_per_m(row)` gives `side.width_off`.
  - `test_a_lone_start_is_a_lone_firing`: `tests/test_linetime.py`'s `frames(stray_first=True)` under `rec.side_view(DEST_VIEW)` gives exactly one `side.lone` firing at t = 0.0. The chain's path is unchanged from the run without a recorder.
  - `test_the_follow_records_a_wide_box_and_still_returns_the_same_outcome`: `farfollow.follow` with a fake `decode`/`detect` yielding a 2.5× box. The `Outcome` is equal with and without a recorder, and one `side.width_far` firing has `pass == "follow"`.
  - `test_stone_points_records_a_wide_box_without_changing_its_points`: the same idea for `sidereleases.stone_points`, with `pass == "slide"`.
- [ ] **Step 2: Run and see them fail.** `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_fpframes_side.py`.
- [ ] **Step 3: Implement** the hooks above.
- [ ] **Step 4: Run them, plus the files these modules already have.** `... tests/test_fpframes_side.py tests/test_sidemodel.py tests/test_linetime.py tests/test_farfollow.py tests/test_sidereleases.py tests/test_spinfit.py`. Expected: all pass.
- [ ] **Step 5: Commit.** `git commit -m "fpframes: record long-camera boxes of the wrong width and lone path starts"`

### Task 5: The VOD worker

**Files:**
- Modify: `src/curling_score/service/worker.py`: `process_job` (line 224), `main` (about line 500).
- Test: `tests/test_worker.py` (append a class).

**Interfaces:**
- Consumes: `fpframes.Settings`, `fpframes.settings()`, `fpframes.model_ids()`, `record.Recorder`, `record.recording`, `store.save_frames` (Tasks 1-2).
- Produces: `process_job(..., fp_harvest: fpframes.Settings | None = None)`. `run_forever` forwards it through `**process_kw`.

**What to add:**
- **In `process_job`, when `fp_harvest` and `fp_harvest.enabled`:**
  - Build `Recorder(info.video_id, models=fpframes.model_ids(weights), pipeline=version.PIPELINE_VERSION, job=job["id"], title=info.title)`.
  - Call `analyze_fn` inside `with record.recording(recorder):`.
  - After `api.complete(...)` returns, call `store.save_frames(recorder, root=root, cap_gb=fp_harvest.cap_gb)` inside `try/except Exception: log.exception(...)`, then return the complete result.
- **In `main`:** `fp = fpframes.settings()`. Pass `fp_harvest=fp` to `run_forever`, and `fp_cap_gb`/`fp_harvest` on to `build_live`, which Task 6 adds.

- [ ] **Step 1: Write the failing tests** (class `TestFalsePositiveFrames`, reusing the file's `FakeApi`, `JOB` and `fake_doc`):
  - `test_with_the_switch_on_the_analysis_runs_under_a_recorder_and_frames_are_saved_after_complete`:
    - an `analyze_fn` that captures `record.active()`;
    - `store.save_frames` monkeypatched to record its call.

    Assert the captured recorder is not None and has the job's `video_id`. `save_frames` is called once, after the API's `complete`, with `root=tmp_path, cap_gb=20.0`.
  - `test_with_the_switch_off_nothing_records`: `fp_harvest=None` and `Settings(False)` both leave `active()` None inside `analyze_fn`, and `save_frames` is never called.
  - `test_a_failure_saving_frames_does_not_fail_the_job`: a `save_frames` that raises still lets `process_job` return the complete result.
- [ ] **Step 2: Run and see them fail.** `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_worker.py -k FalsePositive`.
- [ ] **Step 3: Implement** the changes above.
- [ ] **Step 4: Run the whole file.** `... tests/test_worker.py`. Expected: all pass.
- [ ] **Step 5: Commit.** `git commit -m "worker: save false-positive frames after a VOD job when WORKER_FP_HARVEST=1"`

### Task 6: The live lane

**Files:**
- Modify: `src/curling_score/live/session.py`: `LiveSession.__init__` (line 138), `_build` (line 358).
- Modify: `src/curling_score/live/lane.py`:
  - `LiveLane.__init__` (line 43);
  - `_complete` (line 231);
  - `video_sessions` (line 280).
- Modify: `src/curling_score/live/manager.py`, in `finish` (line 102).
- Modify: `src/curling_score/service/worker.py`, in `build_live`: pass the settings through.
- Test: `tests/test_live_session.py`, `tests/test_live_lane.py` (append).

**Interfaces:**
- Consumes: Tasks 1-2. `fpframes.Settings` from `main` (Task 5).
- Produces:
  - `LiveSession(..., fp_recorder=None)`, kept as `self.fp_recorder`;
  - `video_sessions(..., fp_harvest: Settings | None = None)`;
  - `LiveLane(..., fp_cap_gb: float = 20.0)`;
  - `LiveManager.finish(stream, keep=True, on_kept=None)`.

**What to add:**
- **`_build`** wraps `self.pipeline.build_end(...)` in `with record.recording(self.fp_recorder):`.
- **`video_sessions.make`.** For a non-practice job with the switch on, it passes `fp_recorder=Recorder(job["video_id"], models=fpframes.model_ids(weights), pipeline=version.PIPELINE_VERSION, job=job["id"], title=job.get("title"))`. A practice job gets none.
- **`_complete`.** When `getattr(stream.session, "fp_recorder", None)` is set, it calls `finish` with `on_kept=lambda video: store.save_frames(rec, root=self.manager.root, video=video, cap_gb=self.fp_cap_gb)`.
- **`finish`.** Inside `keep_it`'s `finally`, before `rmtree`, it calls `on_kept(kept if kept is not None else path)` when given. That's the kept `videos/<id>.mp4` or `kept/<id>.ts`, else the recording itself, which still exists at that point. The call is guarded by `try/except Exception: log.exception`. Not called when `keep` is false or there is no file.

- [ ] **Step 1: Write the failing tests.**
  - `test_live_session.py::test_an_end_is_built_under_the_sessions_recorder`: with the file's fake pipeline, `build_end` sees `record.active() is session.fp_recorder`. Without one it sees None.
  - `test_live_lane.py::test_a_completed_stream_saves_its_frames_before_its_recording_goes`:
    - Extend the `_finish` helper with `on_kept`.
    - The callback asserts `directory.exists()` and records its argument.
    - Assert it was called once with `tmp_path / "videos" / "liveVid0001.mp4"`.
  - `test_frames_are_cut_from_the_recording_when_keeping_it_failed`: with `fails=True`, it is called with `directory / "rec.0.ts"`, and the directory is still removed.
  - `test_a_raising_frame_save_still_deletes_the_recording`: `on_kept` raises, and `not directory.exists()`.
  - `test_a_sheet_nobody_played_saves_no_frames`: with `keep=False`, `on_kept` is never called.
  - `test_a_practice_session_gets_no_recorder`: `video_sessions(..., fp_harvest=Settings(True))` with `load_models` monkeypatched and a practice job. The result has no `fp_recorder`, or it is None.
  - Update the fake manager `M.finish` in `test_live_lane.py` to `finish(self, s, keep=True, on_kept=None)`, and assert `_complete` passes a callable only when the session has a recorder.
- [ ] **Step 2: Run and see them fail.** `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_live_lane.py tests/test_live_session.py`.
- [ ] **Step 3: Implement** the changes above.
- [ ] **Step 4: Run them, plus the other live tests.** `... tests/test_live_lane.py tests/test_live_session.py tests/test_live_runner.py tests/test_service_live.py tests/test_worker.py`. Expected: all pass.
- [ ] **Step 5: Commit.** `git commit -m "live: record each end and save its frames before the recording is deleted"`

### Task 7: Seeing the pile, and the switch

**Files:**
- Create: `scripts/fp_harvest.py`
- Modify: `deploy/docker-compose.worker.yml`: add `WORKER_FP_HARVEST: "1"` and `WORKER_FP_HARVEST_GB: "20"` to `worker`'s `environment` (`worker2` inherits it through `extends`), and `WORKER_FP_HARVEST: "0"` to `clips`.
- Test: `tests/test_fp_harvest_script.py`

**Interfaces:**
- Consumes: the row format (Task 2).
- Produces:
  - `load(caches: list[Path]) -> list[dict]`: rows plus a `"cache"` key;
  - `dedupe(rows) -> tuple[list[dict], list[dict]]`: keeps, per `(video_id, json.dumps(models, sort_keys=True))`, only the rows from the cache whose earliest `saved_at` is earliest;
  - `summary(rows) -> dict`: counts by `check`, by ISO week of `saved_at`, by `camera` and by league;
  - `main(argv)` with `list [--since YYYY-MM-DD] [--caches DIR ...]`, caches defaulting to `/data/wdd/curling-cache` and `/data/wdd/curling-cache-2`.
- **League:** the part of `title` after the last `" - "`, or `"?"`.
- **The printout:** the summary, the disk used per cache, and the duplicate count.

- [ ] **Step 1: Write the failing tests.**
  - `test_the_earlier_pile_wins_across_workers`: the same video and models in two caches keeps only the earlier cache's rows. The rest come back as duplicates.
  - `test_a_new_model_on_the_same_video_is_not_a_duplicate`.
  - `test_summary_counts_per_check_week_camera_and_league`: two rows from `"10/20 - Sheet 5 - Tuesday Super League 2026-2027"` count under that league.
- [ ] **Step 2: Run and see them fail.** `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_fp_harvest_script.py`. Import the script with `importlib` the way other `tests/test_*script*.py` files do.
- [ ] **Step 3: Implement** the script and the compose change.
- [ ] **Step 4: Run them.** Expected: pass. Then `docker compose -f deploy/docker-compose.worker.yml config | grep FP_HARVEST` if docker is available locally; skip it otherwise.
- [ ] **Step 5: Commit.** `git commit -m "fp_harvest list: what the workers have saved; switch it on in compose"`

### Task 8: Check it on the worker box (no deploy)

**Files:**
- Modify: `scripts/live_e2e.py`: add `--fp-harvest`, which passes `fpframes.Settings(True)` to `video_sessions` and `fp_cap_gb` to `LiveLane`.

**Steps:**
- [ ] **Step 1: Add the flag and check it.** `PYTHONPATH=src .venv/bin/python scripts/live_e2e.py --help` shows it. Commit: `git commit -m "live_e2e: --fp-harvest"`.
- [ ] **Step 2: Copy the branch's `src` and `scripts` to the Ryzen.** Use `git archive HEAD src scripts | ssh curling@100.85.240.114 'tar -x -C /data/wdd/scratch/fp-harvest/code'`. Never into `/data/wdd/curling_score`.
- [ ] **Step 3: Run one recording through the live lane.**
  - **When:** outside league hours. Check `docker ps` for live jobs first.
  - **Container:** throwaway `curling-worker:local`, with `--gpus device=0 --cpus 5` and `pip install fastapi` first.
  - **Command:** `scripts/live_e2e.py --out /fp/e2e --replay /videos/EvW9jenUR5g.mp4 --speed 20 --fp-harvest`. The cache videos are mounted read-only at `/videos`.
  - **Expected:**
    - `/fp/e2e/.../harvest/fp/EvW9jenUR5g/` holds 1-12 JPEGs and a `frames.jsonl` with as many rows;
    - the log has no `fpframes` traceback;
    - each end's `stages ... (total N s)` line shows no new stage.
- [ ] **Step 4: Run the same replay without `--fp-harvest`.**
  - Compare the two final timelines with `scripts/compare_timelines.py`. Shot lists must match to the same degree two plain replays do. Replays aren't deterministic, so judge rock by rock, not byte by byte; the byte-identical guarantee is Task 3's test.
  - Compare the median per-end total, which should be within run-to-run noise.
- [ ] **Step 5: Look at the frames.** Use `extract.py --redraw`-style crops from the rows, or open three JPEGs at their row's box, and check the box lands on the flagged thing.
- [ ] **Step 6: Report the counts, timings and anything odd to the user.** Deploying the workers is the user's call. Follow `deploying-curling-chart` when asked: clean worktree, checksum rsync, `compose build`, `up -d` only when no live stream or job runs.
