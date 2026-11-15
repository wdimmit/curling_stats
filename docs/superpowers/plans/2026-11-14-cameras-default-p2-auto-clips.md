# Cameras by default, phase 2: every shot clipped, a day of raw footage — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every league stream is read from the cameras unless a league says YouTube, and no YouTube copy is recorded
for a camera game. Every delivery of a camera game is clipped from the camera files whenever the box is idle, and the
raw files go once that is done and a day has passed.

**Architecture:**
- **Defaults.** `WatchedPlaylist.input` defaults to `"cameras"`. `CAMERA_KEEP_YOUTUBE` goes off.
- **Queueing.** When a camera run completes, the API queues one auto clip job per end (`ca_<run>_<g>.<e>`,
  `user_id="auto"`) as a third tier, after shot asks and play batches. An hourly step catches up runs that missed it.
- **Cutting.** The clips container runs extra loops that claim only auto jobs. The API hands one out only while no live,
  standby or practice run is recording.
- **Reading results.** Shot and play clip states read the auto jobs' results.
- **Retention.**
  - When a run's auto jobs are all finished, the API stamps `run.clipped_at`.
  - The workers' prune asks the API which kept camera games are clipped. A clipped game goes after a day; an unclipped
    one after 3 days.
  - When the disk is short, clipped games go first and unclipped games last.

**Tech Stack:** Python 3.12, FastAPI, Firestore repo (+ MemoryRepo in tests), threads, Docker compose; JS (esbuild
bundle) for one default.

**Spec:** `docs/superpowers/specs/2026-10-25-pluggable-input-design.md` ("Retention and overnight clips"), as amended
by the approved overall plan `~/.claude/plans/this-is-a-big-sparkling-gadget.md` (phase 2, user decisions 2026-11-14:
cameras default; no YouTube copy; auto clips whenever the box is idle, not night-only; ~1 day of raw footage).

## Global Constraints

- The constants and settings this phase pins:
  - auto job id `ca_<run_id>_<game>.<end>` (from `gameclips.rock_name`'s first two parts);
  - `user_id="auto"`;
  - auto `run_after` = completion + `LIVE_SETTLE_S` (600 s);
  - idle-gate cache 60 s;
  - `CLIP_AUTO_LOOPS` 5;
  - clips `cpus` "20", `cpu_shares` 256;
  - `WORKER_CAMERA_DAYS` 1 (clipped), `WORKER_CAMERA_MAX_DAYS` 3 (unclipped);
  - `CAMERA_KEEP_YOUTUBE` "0".
- Shot asks keep first place. A person's Clip… never waits behind the auto pass.
- A YouTube-input game behaves exactly as today.
- No version bump: nothing changes what a timeline says.
- The worktree's tests run as `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest <files>`,
  subsets only.
- After any `frontend/` change, rebuild with `cd frontend && npm run build`, and commit the bundles and
  `.buildstamp.json`.
- Deploy: the API first, then the workers and clips with one `gated_restart.sh` (compose changes recreate clips too).
  Only on the user's go.

## Review Focus

1. **Recording starts mid-job.** An auto job already claimed runs to its end, and the next claim is refused while the
   run is live. Task 2: `test_an_auto_claim_is_refused_while_a_game_is_recorded`.
2. **Reprocessed run.** A camera run reprocessed later gets auto jobs for its new run id. The old run's jobs don't count
   for `clipped_at`. Task 3: `test_catch_up_queues_a_run_with_none_since_it_was_ready`.
3. **Prune can't reach the API.** No game counts as clipped, so the 3-day and floor rules still apply and nothing is
   deleted early. Task 6: `test_without_word_from_the_api_nothing_is_clipped`.
4. **Practice-marked or rockless games.** No auto job is made for them, and a run with no auto jobs at all is
   `clipped_at` at once. Task 3: `test_a_run_with_nothing_to_clip_is_clipped_at_once`.
5. **Worker ahead of the API.** An old API ignores `mode`. Worker loops claiming auto from it would also take asks;
   harmless, since a job is a job. Task 5: `test_an_auto_loop_says_so_when_it_claims`.

---

### Task 1: cameras are the default input

**Files:**
- Modify: `src/curling_score/service/records.py:393` (`input: str = "cameras"`, comment)
- Modify: `frontend/core/playlists.mjs:97,113,151` (default `"cameras"`)
- Test: `tests/test_playlists.py` (or wherever `WatchedPlaylist` defaults are tested; `grep -ln "input" tests/test_*playlist*`),
  `tests/test_playlists_js.py` if it pins the form default

- [ ] **Step 1: Write the failing tests.**
  - `test_a_playlist_reads_the_cameras_unless_told_otherwise`: `WatchedPlaylist(id=..., playlist_id=..., label=...,
    created_at=T).input == "cameras"`, and `WatchedPlaylist.from_dict({...without input...}).input == "cameras"`.
  - JS: `emptyForm().input === "cameras"`, `formFrom({}).input === "cameras"`, `bodyFrom(form without input).input
    === "cameras"`.
  - Existing tests that assumed `"youtube"` as the default are updated to say `input="youtube"` explicitly.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.** Change the three JS defaults and the dataclass default. Rebuild the bundles.
- [ ] **Step 4: Run the playlist tests (Python and JS) and `tests/test_service_live.py`, expect PASS.**
- [ ] **Step 5: Commit:** `playlists: the cameras are the default input`.

### Task 2: an auto tier, claimed only when the box is idle

**Files:**
- Modify: `src/curling_score/service/gameclips.py` (`_claim_order`, `queue_ahead`, `claim`, new `AUTO_USER`,
  `recording_now`)
- Modify: `src/curling_score/service/api.py` (`clips_claim` ~:3570, `clip_quiet` ~:2689)
- Modify: `src/curling_score/service/worker.py` (`ApiClient.claim_clips(worker_id, mode="ask")`)
- Test: `tests/test_gameclips.py`, `tests/test_game_clips_api.py`

**Interfaces:**
- Produces:
  - `AUTO_USER = "auto"`.
  - `is_auto(job) -> bool`.
  - `_claim_order(job)` → `(tier, run_after, created_at)`, tier 0 = shot ask, 1 = play batch, 2 = auto.
  - `claim(repo, worker_id, now, lease_s, *, mode="ask")`: `"ask"` never takes auto jobs; `"auto"` takes only auto
    jobs. The tier filter runs before `get_run`.
  - `queue_ahead` counts only non-auto running jobs.
  - `recording_now(repo) -> bool`: any run with status in `("queued", "processing", "live")` and kind in `("live",
    "standby", "practice")`.
  - API `/api/worker/clips/claim` reads `mode` from the body (default `"ask"`). For `"auto"`, it answers 204 while
    `recording_now` holds, cached 60 s per instance. Each mode has its own `clip_quiet`.

- [ ] **Step 1: Write the failing tests.**
  - `test_an_ask_claim_never_takes_an_auto_job`.
  - `test_an_auto_claim_takes_only_auto_jobs_oldest_first`.
  - `test_auto_jobs_do_not_count_ahead_of_a_shot`: 5 auto jobs running, one shot queued, `ahead == 0` (only the
    non-auto running count).
  - `test_recording_now_sees_live_standby_and_practice_runs`: a `ready` live run → False; a `live` camera run → True;
    a `processing` practice run → True; a `processing` vod run → False.
  - API: `test_an_auto_claim_is_refused_while_a_game_is_recorded`. An auto job is queued; with a run in status live,
    an auto claim gives 204. After `clock.advance(61)` with the run ready, it gives the job.
  - API: `test_an_empty_auto_queue_does_not_quiet_the_asks`. An auto claim with nothing gives 204; then a shot is
    asked and an ask claim gives it immediately.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** per Interfaces.
- [ ] **Step 4: Run `tests/test_gameclips.py tests/test_game_clips_api.py`, expect PASS.**
- [ ] **Step 5: Commit:** `clip jobs: an auto tier, claimed only while nothing is being recorded`.

### Task 3: every delivery of a camera run queued at completion, and caught up hourly

**Files:**
- Modify: `src/curling_score/service/records.py` (`Run.auto_clips: list = []`, `Run.clipped_at: datetime | None =
  None`)
- Modify: `src/curling_score/service/gameclips.py` (new `auto_job_id`, `queue_auto`, `catch_up_auto`)
- Modify: `src/curling_score/service/api.py` (job `complete` ~:3463, after `resolve_charts_for_run`; `admin_poll`'s
  hourly part, before the index)
- Test: `tests/test_gameclips.py`, `tests/test_game_clips_api.py`

**Interfaces:**
- Produces:
  - `auto_job_id(run_id, game_index, end_identity) -> str` (`ca_<run>_<g>.<e>`).
  - `queue_auto(repo, run, doc, now) -> list[str]`:
    - only when `camera_feed_spec(run, doc)` is not None;
    - one `ClipJob` per end of each game with `practice` falsy, holding every non-missing shot with `t_release_s`
      (`rock_spec`);
    - `spec` = `_spec(calibration, rocks, feed)`, `run_after = now + LIVE_SETTLE_S`, `created_at = now`;
    - ends with no such rock get no job;
    - writes `run.auto_clips = ids`;
    - if `ids` is empty, also sets `run.clipped_at = now`;
    - returns ids.
  - `catch_up_auto(repo, load_doc, now, *, days=2) -> int`: for each run in `repo.runs_ready_since(now - days)` with
    `source == "cameras"`, status ready, and no auto job created at or after its `ready_at`
    (`not run.auto_clips or repo.get_clip_job(run.auto_clips[0]).created_at < run.ready_at`), call `queue_auto`. Returns
    how many runs were queued.
  - The API's `complete` calls `queue_auto(repo, run, run_doc(run), t)` for camera runs, inside a try that only logs.
    `admin_poll` calls `catch_up_auto` in its first-call-of-the-hour part.

- [ ] **Step 1: Write the failing tests.**
  - `test_queue_auto_makes_one_job_per_end_of_every_shown_game`. A camera doc with game 0 (2 ends: 16 rocks and 3
    rocks, one missing, one without release) and game 1 `practice: true` gives ids `["ca_r1_0.1", "ca_r1_0.2"]`. Job
    0.2's rocks are the 1 released, non-missing rock; `user_id == "auto"`; `run_after == T + 600 s`;
    `spec["feed"]["sheet"] == 3`; `run.auto_clips == ids`.
  - `test_a_youtube_run_gets_no_auto_jobs`: returns `[]`, nothing written, `clipped_at` untouched.
  - `test_a_run_with_nothing_to_clip_is_clipped_at_once`: a camera doc with only practice games gives `[]` and
    `run.clipped_at == T`.
  - `test_catch_up_queues_a_run_with_none_since_it_was_ready`. Run A (auto jobs made before `ready_at`, i.e.
    reprocessed) and run B (none) are queued. Run C (auto jobs after `ready_at`) and a YouTube run D are not.
    Returns 2.
  - API: `test_completing_a_camera_run_queues_its_clips`. After the worker's complete of a camera run, `ca_` jobs
    exist in state queued.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the clip tests and `tests/test_service_api.py -k complete`, expect PASS.**
- [ ] **Step 5: Commit:** `auto clips: every delivery of a camera run queued at completion, caught up hourly`.

### Task 4: what the auto jobs cut is seen, and a run says when it is clipped

**Files:**
- Modify: `src/curling_score/service/gameclips.py` (`shot_state`, `state_for`, `ensure`, new `cut_elsewhere`,
  `note_auto_done`, `clipped`)
- Modify: `src/curling_score/service/api.py` (`clips_complete`, `clips_fail`, `clips_later` call `note_auto_done`;
  new `GET /api/worker/clips/done`)
- Modify: `src/curling_score/service/worker.py` (`ApiClient.clips_done(video_ids) -> dict`)
- Test: `tests/test_gameclips.py`, `tests/test_game_clips_api.py`

**Interfaces:**
- Produces:
  - `cut_elsewhere(repo, run, names) -> dict[name, result]`: results with `clip: True` (or `reason: video_gone`)
    from `cs_<run>_<name>` and from that rock's `ca_` job, each only if done and created at or after
    `run.ready_at`.
  - `shot_state` returns `ready` / `gone` from the auto job when there is no shot job, or the shot job isn't done.
    A queued or running auto job leaves it `none`, so a person can still ask and jump the queue.
  - `state_for(repo, play_id, run, names=())` merges `cut_elsewhere(repo, run, names)` for names it has nothing made
    for.
  - `ensure` passes the rocks' names and leaves rocks already cut elsewhere out of `todo`.
  - `note_auto_done(repo, job, now) -> bool`: for an auto job, when every id in its run's `auto_clips` is in state
    done or failed, it sets `run.clipped_at = now` and returns True.
  - `GET /api/worker/clips/done?videos=a,b` (worker token) → `{"done": {vid: bool}}`. True when that video's newest
    ready camera run has `clipped_at`.

- [ ] **Step 1: Write the failing tests.**
  - `test_a_shot_cut_by_the_auto_pass_is_ready` and `test_a_queued_auto_job_still_lets_a_person_ask`.
  - `test_a_plays_rocks_cut_by_the_auto_pass_are_not_asked_for_again`: `ensure` queues nothing, and
    `state_for(..., names)` shows them `clip: True`.
  - `test_an_auto_job_from_before_the_run_was_analysed_again_is_ignored`.
  - `test_the_last_auto_job_done_marks_the_run_clipped` (a failed one counts as finished) and
    `test_one_auto_job_still_queued_leaves_it_unclipped`.
  - API: `test_the_workers_learn_which_kept_games_are_clipped`. One video has a clipped camera run, one has an
    unclipped one, one is unknown: `{"v1": True, "v2": False, "v3": False}`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.** The routes call `note_auto_done` after their update, inside a try that only logs.
- [ ] **Step 4: Run the clip tests, expect PASS.**
- [ ] **Step 5: Commit:** `auto clips: their results are the shot's and the play's; a run says when it is clipped`.

### Task 5: the clips container runs auto loops

**Files:**
- Modify: `src/curling_score/service/clipworker.py` (`run_forever(..., mode="ask")`, `main`)
- Test: `tests/test_clipworker.py`

**Interfaces:**
- Consumes: `ApiClient.claim_clips(worker_id, mode=...)` (Task 2).
- Produces:
  - `run_forever(api, worker_id, *, sources, workdir, cams=(), mode="ask", ...)` passes `mode` to `claim_clips`.
  - `main` starts `CLIP_AUTO_LOOPS` (env, default 0) daemon threads of `run_forever(mode="auto")`. Each thread gets its
    own `ApiClient`, id `f"{worker_id}-auto{i}"` with `release_held` called for it, and the same workdir root. The ask
    loop then runs in the main thread.

- [ ] **Step 1: Write the failing tests.**
  - `test_an_auto_loop_says_so_when_it_claims`: a fake API records the mode.
  - `test_main_starts_the_auto_loops_with_their_own_ids`: run_forever is monkeypatched to record `(wid, mode)` and
    the threads are joined; `CLIP_AUTO_LOOPS=2` gives `{("ryzen-clips", "ask"), ("ryzen-clips-auto1", "auto"),
    ("ryzen-clips-auto2", "auto")}`, and release_held was called for all three ids.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run `tests/test_clipworker.py`, expect PASS.**
- [ ] **Step 5: Commit:** `clips worker: auto loops beside the one that answers asks`.

### Task 6: a day of raw footage, clipped games first

**Files:**
- Modify: `src/curling_score/ingest/prune.py` (`kept_camera_games`, `prune_camera_games`, `prune`)
- Modify: `src/curling_score/service/worker.py` (`build_live`'s `prune_cache`; the recordings loop's prune;
  `WORKER_CAMERA_MAX_DAYS`)
- Test: `tests/test_prune.py` (or the file testing `prune_camera_games`: `grep -ln prune_camera_games tests`),
  `tests/test_worker.py`

**Interfaces:**
- Produces:
  - `kept_camera_games(cams_root)` dates each game by `clock.json`'s `kept_at`, falling back to the directory mtime.
  - `prune(..., cams_root=None, camera_days=None, camera_max_days=None, clipped=None)`, where
    `clipped(names) -> set[str]` and None means none are clipped. The order:
    1. camera games clipped and older than `camera_days`, or older than `camera_max_days`;
    2. recordings past `recording_days` (as today);
    3. while under the floor: clipped camera games, oldest first;
    4. kept recordings, oldest first (as today);
    5. unclipped camera games, oldest first, each logged `"clips lost"`;
    6. other media for the budget (as today).
  - A camera game whose `clock.json` says `trim_pending` is never deleted in step 1.
  - The worker's `prune_cache` passes `clipped=lambda names: {v for v, ok in api.clips_done(names)["done"].items()
    if ok}`, with any error meaning `set()`.
  - The recordings loop also prunes the cameras (same arguments) once an hour when idle.

- [ ] **Step 1: Write the failing tests.**
  - `test_a_clipped_game_goes_after_a_day_an_unclipped_one_after_three`.
  - `test_without_word_from_the_api_nothing_is_clipped`: `clipped=None` deletes nothing under 3 days.
  - `test_under_the_floor_clipped_games_go_before_recordings_and_unclipped_last`: free space so low that one
    deletion is needed: the clipped game goes and the recording and unclipped game stay. With more needed, the
    recording goes before the unclipped game.
  - `test_a_game_still_being_cut_is_not_aged_out`.
  - `test_kept_at_dates_a_game_not_its_directory`.
  - Worker: `test_the_live_prune_asks_the_api_what_is_clipped` (fake ApiClient), and
    `test_an_unreachable_api_means_nothing_is_clipped`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the prune and worker tests, expect PASS.**
- [ ] **Step 5: Commit:** `prune: a day of raw camera footage once clipped, three at most, clipped games first`.

### Task 7: settings and the privacy page

**Files:**
- Modify: `deploy/docker-compose.worker.yml`:
  - `CAMERA_KEEP_YOUTUBE` "0";
  - `WORKER_CAMERA_DAYS` "1", `WORKER_CAMERA_MAX_DAYS` "3";
  - clips: `cpus` "20.0", `cpu_shares` 256, `CLIP_AUTO_LOOPS` "5";
  - comments updated.
- Modify: `src/curling_score/service/static/privacy.html` (the camera paragraph)
- Test: `tests/test_service_api.py` (the static pages load), a compose sanity test if one exists
  (`grep -ln docker-compose tests`)

- [ ] **Step 1: Write the privacy paragraph.** It should say:
  - most league games are read from the club's cameras with the club's agreement;
  - for those games the YouTube video is not copied;
  - the camera recording is kept on the processing computer until a clip of every shot is made, about a day, and at
    most three days, then deleted;
  - the schedule-start sentence stays (phase 3 changes it).
- [ ] **Step 2: Edit the compose settings** as listed.
- [ ] **Step 3: Run `docker compose -f deploy/docker-compose.worker.yml config -q`** (if docker is here; else a YAML
  load) and the page tests. Expect PASS.
- [ ] **Step 4: Commit:** `deploy: no YouTube copy of camera games, a day of footage, auto clip loops; privacy page`.

### Task 8: results, and the deploy handed over

**Files:**
- Create: `docs/superpowers/plans/2026-11-14-cameras-default-p2-auto-clips.results.md`

- [ ] **Step 1: An in-process run-through with the API's TestClient** (as a test,
  `tests/test_game_clips_api.py::test_a_camera_night_end_to_end`):
  1. Complete a camera run, so `ca_` jobs are queued.
  2. An auto claim while another run is live gives 204.
  3. With that run ready, the auto claims take every job, and their completes give `clipped_at`.
  4. `/clips/done` says True, and the shot page says ready for a rock.
- [ ] **Step 2: Write the results doc and commit.**
- [ ] **Step 3: The final whole-branch review (fresh reviewer), its fixes, then hand the deploy to the user.** The
  deploy steps:
  1. API.
  2. Rsync + `compose build worker` + `gated_restart.sh`, which recreates worker, worker2 and clips.
  3. Check in-container: clips logs 6 loops; workers `keep_youtube False`.
  4. Gate: the next camera night. Every delivery clipped within ~1.5 h of the last game; no auto claims while
     recording (API logs); no `live/<id>-youtube` written; raw directories gone once clipped and a day old.
