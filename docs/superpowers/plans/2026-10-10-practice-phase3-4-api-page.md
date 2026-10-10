# Practice Sessions, Phases 3-4: API, Records and the Page

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. This plan is executor-authored for a same-day deploy. It pins every interface, test and behaviour; the code is written test-first in the commits rather than duplicated here.

**Goal:** A signed-in user opens `/practice/sheet/N`, presses Start, and sees each delivery on that sheet within seconds. Several sessions share one watch per sheet stream. Everything sits behind `PRACTICE_ENABLED`.

**Architecture:**
- A practice job rides on the existing queue: a `Run(kind="practice")` plus a `Job(kind="practice")`.
- A `PracticeWatch` record ties the run, job, sheet and video together; a `PracticeSession` is a user's window on a watch. Both live in Firestore.
- Practice logic sits in a new module, `service/practice.py`, which `api.py` calls through thin routes.
- The worker contract is the one phase 2 implemented: artifacts `practice.json`, publish `{"practice": summary, "sheet"}`, heartbeat replies `{"stop": true}`, complete `{"practice": summary, "sheet"}`.

**Spec:** `docs/superpowers/specs/2026-10-09-practice-sessions-design.md`, sections 1, 3 and 4.

## Global Constraints

- **Flag:** `PRACTICE_ENABLED` (env, default off). With it off, the practice routes return 404, and `/api/features` reports `practice: false`.
- **Timeouts:** a session ends after `IDLE_S` = 1200 s with no throws, or `MAX_S` = 10800 s, or on Stop. The API enforces them on each practice heartbeat and publish. A watch with no active session gets `stop: true`.
- **Firestore:**
  - Collections `practice_watches` and `practice_sessions`.
  - Only single-field equality queries, sorted in Python, so no new indexes.
  - The claim's practice pass reuses the `jobs(state, kind, run_after, created_at)` index, which already exists.
- **Practice runs are invisible:**
  - kept out of the catalogue (`/api/games`), run reuse (`dedupe.find_reusable_run`) and the league poller;
  - a practice playlist (`WatchedPlaylist.practice = True`) is skipped by `playlists.poll`;
  - Firestore's VOD claim pass no longer takes practice jobs.
- **Ruling (against spec s3):** a session's throws are not frozen into their own document at the end. They are read from the watch's `practice.json`, which stays in the store, filtered to the session's window. Cost: history depends on the watch document staying put, which it does.
- **Ruling:** a session's window is wall time. A throw's wall time is `wall_t0 + (t_release_s, or t_rest_s - 20)`, in `[started_at, ended_at or now]`.
- **Work:** worktree `.claude/worktrees/practice`. TDD per task. Tests run in affected subsets. `npm run build` in `frontend/` after any JSX change (the buildstamp test).
- **Deploy (Task 9)** follows `deploying-curling-chart`:
  1. the API via `deploy-api.sh`, with `PRACTICE_ENABLED=1` in `cloudrun.yaml`;
  2. the workers via rsync from a clean worktree, then `up -d --build`, with `WORKER_PRACTICE=1`;
  3. `PLErN0WbLUyFI` added as a practice playlist.

  Rollback: set the flag to 0 and redeploy, or `gcloud run services update-traffic` to the previous revision; remove `WORKER_PRACTICE` from the workers.

## Review Focus

1. **A practice job claimed by a VOD-only worker,** or a league job by a practice pass. Test: the Firestore and memory claim-by-kind contract.
2. **A session whose watch's job failed or was lost** must read as ended or failed, not live for ever. Test: `test_a_session_on_a_failed_watch_reads_as_failed`.
3. **Two users starting on the same sheet at once** must share one watch, not create two. Test: `test_a_second_start_on_a_sheet_joins_its_watch`. Concurrent starts are noted, not locked: rare, and they cost one duplicate watch.
4. **Another user reading or stopping my session.** Tests: owner-only, 404 for others.
5. **A practice run reaching the games list or a VOD claim.** Tests in Task 3.

---

### Task 1: Records and repo (watch, session; playlist `practice`; kind `practice`)

- `records.py`:
  - `JOB_KINDS += ("practice",)`;
  - `PracticeWatch(id, sheet, video_id, run_id, job_id, created_at, status="starting", revised_at=None, recorded_s=None, throws=0, busy_s=None, error=None)`;
  - `PracticeSession(id, user_id, sheet, watch_id, started_at, hand="right", ended_at=None, end_reason=None, hidden=[])` with an `active` property;
  - `WatchedPlaylist.practice: bool = False`.
- `slug.py`: `new_watch_id()` (`pw_`) and `new_session_id()` (`ps_`).
- `repo.py` Protocol and `MemoryRepo`:
  - `put_watch`, `get_watch`, `update_watch`, `watches_for_sheet(sheet)`;
  - `put_session`, `get_session`, `update_session`, `sessions_for_watch(watch_id)`, `sessions_for_user(user_id, limit=50)`;
  - both added to `export_all`/`import_all`;
  - `MemoryRepo.claim_job` takes practice when `"practice"` is in `kinds`.
- `firestore_repo.py`:
  - the same methods, on `practice_watches` / `practice_sessions`;
  - a `claim_job` practice pass, and a VOD pass that excludes practice;
  - export and import.
- `restore.py` `_TIME_FIELDS` gains `started_at` and `ended_at`.
- **Tests:** `tests/test_practice_records.py`:
  - round trips;
  - `sessions_for_user` newest first;
  - the memory claim gives a practice job only to `kinds` that include practice;
  - `test_repo_contract.py` gains the new methods for both repos (Firestore runs with the emulator only).

### Task 2: Finding a sheet's live practice stream

- `service/practice.py`: `live_stream_for_sheet(repo, youtube, sheet) -> VideoMeta | None`. For each enabled practice playlist: `playlist_video_ids`, then `videos(ids)`. It returns the newest whose `live_status == "live"` and whose `source.sheet_from_title(title) == sheet`.
- **Tests:**
  - a live sheet 3 among upcoming streams and other sheets is found;
  - a league playlist is not searched;
  - nothing live gives None.

### Task 3: Starting, joining, reading, stopping, hiding

All of this is in `service/practice.py`; the API routes call it.

- `start(repo, youtube, user, sheet, hand, now, processing_version)`:
  - reuses the sheet's active watch (status not in ended/failed, and its job not failed);
  - otherwise finds the stream and creates `Run(kind="practice", status="queued", format="fours", sheet, title, video_id)`, `Job(kind="practice")` and a `PracticeWatch`;
  - always creates a `PracticeSession`;
  - raises `NoStream` when there is no live stream.
- `session_view(repo, store, session)` returns `{"session": {...}, "watch": {"status", "recorded_s", "busy_s"}, "throws": [...]}`:
  - the throws are the watch document's throws in the session's wall window, minus the hidden ones;
  - each gains `"released_at"`, an ISO wall time;
  - `status` is `active` / `ended` / `failed`, where failed means the watch's job failed.
- `stop(repo, session, now, reason)`, and `hide(repo, session, throw_id, hidden: bool)`.
- `sweep(repo, store, watch, now) -> active sessions left`: ends idle (`IDLE_S` since the last throw in the window, or since start) and over-long (`MAX_S`) sessions.
- **Exclusions:**
  - `api_games` skips `kind == "practice"`;
  - `dedupe.find_reusable_run` ignores practice runs;
  - `playlists.poll` skips `pl.practice`.
- **Tests** (`tests/test_practice_api.py`, through `create_app` with `FakeVerifier`, `MemoryStore` and `FakeYouTube`):
  - Start creates a run, a job of kind practice, a watch and a session;
  - `test_a_second_start_on_a_sheet_joins_its_watch`;
  - no live stream gives 404 `"no live stream on sheet N"`;
  - signed out gives 401;
  - the flag off gives 404;
  - GET returns throws in the session's window, filtered and hidden; it is owner-only (404 for others) and ETag-revalidated;
  - stop ends the session; a stopped session's view no longer grows;
  - hide and unhide;
  - `GET /api/me/practice` lists mine, newest first, with `active`;
  - `test_a_session_on_a_failed_watch_reads_as_failed`;
  - practice runs don't appear in `/api/games`;
  - the league poller skips a practice playlist.

### Task 4: The worker endpoints for practice jobs

In `api.py`:
- **claim:** a practice job's dict gains `lookback_s: 1200`.
- **artifacts:** `practice.json` is allowed for practice jobs only, at key `practice_key(video_id, run_id)` (`runs/{vid}/{run}/practice.json`). The `upload_via_api` allowlist gains it too.
- **publish** for a practice job:
  - requires `practice.json` in the store;
  - updates the watch (status, recorded_s, throws, busy_s, revised_at) and the run (`status="live"`, `revised_at`);
  - renews the lease;
  - sweeps, and returns `{"ok": True, "stop": not active}`.
- **progress** for a practice job: sweeps, and adds `"stop": True` when no session is active.
- **complete** for a practice job:
  - no format or timeline checks;
  - the job is done, and the run `ready` (excluded from the catalogue);
  - the watch is `ended`, or `failed` if `practice.status == "failed"`;
  - active sessions end with `stream_ended`.
- **Tests:** the worker contract end to end through the HTTP API: claim, artifacts, upload, publish, a session reading the throws, progress replying `stop` once the session is stopped or idle, complete ending the watch. A VOD-only claim never gets the practice job.

### Task 5: Flags and wiring

- `Settings.practice_enabled` from `PRACTICE_ENABLED`.
- `/api/features` gains `practice`.
- The admin playlist POST and PATCH accept `practice`.
- `cloudrun.yaml` gains `PRACTICE_ENABLED`, `"1"`, with a comment.
- **Tests:** the `Settings` flag test, the features payload, and an admin POST with `practice: true`.

### Task 6: The page shell and route

- `GET /practice/sheet/{n}` returns `page("practice.html")`.
- `static/practice.html` has `data-page="practice"`.
- The page-list tests in `test_service_api.py` gain `/practice/sheet/3`.

### Task 7: The page's display logic (`frontend/core/practice.mjs`)

Pure functions with JS tests, in the `tests/test_viewer_js.py` style:
- `turnOf(curl, hand)` → `"In-turn"`/`"Out-turn"`/`null`: right-handed, curl right = in-turn;
- `ringLabel(rest)` → "Button" / "4-foot" / "8-foot" / "12-foot" / "Short" / "Long" / "Out";
- `restText(rest)` → `"4-foot · 0.4 m long, 0.1 m left"`;
- `lineText(line, broom)` → `"12 cm narrow of the broom"`, or `"aimed 21 cm left at the tee"`, or `null`;
- `weightText(split)` → `"13.8 s"`;
- `summary(throws)` → `{count, weightMin, weightMax, within15}`;
- `shown(throws)`: arrived throws plus never-arrived ones, without arrivals nothing released (phase 1: no release means not thrown).

### Task 8: `Practice.jsx`

- The four states from the spec: signed out, ready (Start + hand toggle), live, and ended.
- It polls every 3 s, paused while the page is hidden.
- "End session" and "Not mine"; the latest throw as a card, then a table, a small SVG house with the rest and the broom; the mockups' palette.
- An entry in the `PAGES` map, and `npm run build`.
- **Checked** headless at 390×844 against a fake API, with `scripts/devserve`-style or `node`-served fixtures.

### Task 9: Deploy and try it (the user asked)

1. Merge to main.
2. Deploy the API with `PRACTICE_ENABLED=1`.
3. Rsync the worker from a clean worktree, add `WORKER_PRACTICE=1` to the compose environment, then `up -d --build`.
4. `POST /api/admin/playlists {playlist_id: PLErN0WbLUyFI, label: "Competitive Practice Ice 2026-2027", practice: true}`.
5. The user opens `/practice/sheet/3`. Watch the first session together.
6. Roll back if anything misbehaves (see Global Constraints).
