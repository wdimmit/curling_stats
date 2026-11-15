# Cameras by default, phase 3: overlapping draws — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Each sheet's next league game is recorded from 10 min before its stream's YouTube scheduled start, even
while the earlier league's game is still on that sheet. Each game is charted whole: the earlier game is followed to
its end, up to 45 min past its stream. Every shot links to whichever stream shows it, or says no stream is available.

**Architecture:**
- **Scheduled starts.** The poll records each upcoming camera stream's `scheduledStartTime`. The club adds the day's
  broadcasts to its playlists at about 05:00 (checked 11/14: Supper 17:00, Super 19:00, Mens 19:00, Skips 19:00, all
  on time).
- **Opening.** `standbys()` opens a "lookahead" standby per upcoming stream at its scheduled start − 10 min, even on
  a busy sheet. The window-start standby stays as the fallback.
- **Ownership.** A game belongs to the run whose window it started in.
  - **Run A** (the earlier run on the sheet) decides where its game ends. Starting from the later run's opening H, or
    from its own stream's end, it finds the first changeover in its own profile and reports that as `own_until`.
  - **Run B** (the later run) holds its ends until it hears that boundary, then reads from there. It uses its opening
    instead if there is no camera run A, A failed, or 45 min pass.
  - A's recording runs past its stream end until it has decided.
- **Shot links.** A shot outside its own stream links to the neighbour run's stream, through the runs' `wall_t0`, at
  serve time. The viewer can switch videos, or show "No stream for this shot".

**Tech Stack:** Python 3.12, FastAPI, Firestore/MemoryRepo, the live lane/session/manager, React viewer (esbuild),
the YouTube iframe API.

**Spec:** `docs/superpowers/specs/2026-10-25-pluggable-input-design.md`, as amended by the approved overall plan
`~/.claude/plans/this-is-a-big-sparkling-gadget.md` (phase 3; user decisions 2026-11-14):
- look for the next game from 10 min before its YouTube scheduled start;
- early shots link to the previous stream, else "no stream available";
- the late game is followed to its end, capped at about 45 min, with its late shots linked to the next stream or "no
  stream".

## Global Constraints

- `LOOKAHEAD_S` = 600.
- Upcoming streams count only when scheduled within [now − 30 min, now + 16 h].
- Changeover: a gap between ends of at least `segment.GAME_GAP_S` (240 s), or at least
  `boardsplit.CHANGEOVER_MIN_GAP_S` (120 s) with the board cleared across it.
- `OVERRUN_MAX_S` = 45 × 60. `FLOOR_WAIT_S` = 45 × 60.
- Recording continues `BOARD_BEHIND_S` + 30 s past `own_until` before it finishes.
- The camera cap (`CAMERA_MAX_SHEETS`) counts distinct sheets, not runs. Each worker gets `LIVE_MAX_STREAMS` 5.
- No version bump: a game's timeline changes only around a changeover.
- A YouTube-input run behaves as today.
- A shot inside its own stream behaves as today.
- Worktree tests run as subsets: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest <files>`.
  Rebuild the bundles after frontend changes.
- Deploy order: API first (it sends the new fields; old workers ignore them), then the workers. Only on the user's
  go.

## Review Focus

1. **First league of the day, nothing on the sheet before it.** The floor is the opening, at once. Task 5:
   `test_a_first_game_on_its_sheet_reads_from_its_opening`.
2. **A dies (worker restart) before deciding.** B's floor falls back to H after `FLOOR_WAIT_S`, and B never holds for
   ever. Task 5: `test_a_dead_earlier_game_lets_the_later_one_go_in_the_end`.
3. **A YouTube-input or failed A.** It counts as "no camera run A". Task 5: same test file, a parametrized case.
4. **A stream that never comes for a lookahead standby.** It is given up `STANDBY_MAX_S` after its opening, and its
   recording is deleted. Task 2: `test_a_lookahead_standby_with_no_stream_is_given_up`.
5. **Negative video times in the viewer and in links.** A shot before t = 0 never links to `?t=-N`, and the
   follow/pause logic ignores the other video. Tasks 7-8: `test_a_shot_before_the_stream_has_no_negative_link`,
   `test_follow_ignores_another_video`.

---

### Task 1: upcoming streams and their scheduled starts

**Files:**
- Modify: `src/curling_score/service/youtube.py` (`VideoMeta` + `scheduled_at`, `ended_at`; `videos()` reads
  `liveStreamingDetails.scheduledStartTime` and `actualEndTime`; the fakes in the same file)
- Modify: `src/curling_score/service/records.py` (`WatchedPlaylist.upcoming: list = []`)
- Modify: `src/curling_score/service/playlists.py` (`poll`)
- Test: `tests/test_playlist_live.py`, `tests/test_youtube.py` (or wherever `VideoMeta` parsing is tested)

**Interfaces:**
- Produces:
  - `VideoMeta.scheduled_at: datetime | None`, `VideoMeta.ended_at: datetime | None`. Both default to None, so
    existing constructors still work.
  - `poll` writes `WatchedPlaylist.upcoming = [{"video_id", "sheet", "scheduled_at" (ISO)}]` for each cameras
    playlist it polls. The entries come from the metas with `live_status == "upcoming"`, a sheet in the title, and
    `scheduled_at` within the window.
  - The list is recorded before the `max_new` break and replaced on every poll of that playlist.
  - `mark_stream_ended` stores `stream_ended_at = meta.ended_at or now`.

- [ ] **Step 1: Failing tests.**
  - `test_videos_reads_the_scheduled_and_actual_end_times`.
  - `test_the_poll_remembers_each_sheets_upcoming_stream`: two upcoming streams (sheet 3 at 19:00 and one at +3 days)
    give one entry for sheet 3.
  - `test_a_stream_ended_takes_youtubes_end_time`.
- [ ] **Step 2: Run, FAIL. Step 3: Implement. Step 4: Run the playlist and YouTube tests, PASS.**
- [ ] **Step 5: Commit:** `playlists: each sheet's upcoming stream and its scheduled start`.

### Task 2: lookahead standbys, opened even on a busy sheet

**Files:**
- Modify: `src/curling_score/service/records.py`:
  - `Run.expected_video_id: str | None`;
  - `Run.opened_at: datetime | None`;
  - `Run.prev_run_id`, `Run.next_run_id: str | None`;
  - `Run.handoff_at: datetime | None`;
  - `Run.own_until: datetime | None`;
  - `Run.wall_t0: float | None`.
- Modify: `src/curling_score/service/playlists.py` (`standbys`, `_standby_for`, `_found_already`, `camera_games`,
  `_hand_over`, the poll's hand-over)
- Modify: `deploy/docker-compose.worker.yml` (`LIVE_MAX_STREAMS` "5", comment)
- Test: `tests/test_playlist_live.py`

**Interfaces:**
- Produces:
  - **Opening.** `standbys()` opens, for each playlist's `upcoming` entry with `scheduled_at − LOOKAHEAD_S <= now`
    and no run yet for that expected id (`runs_for_video` or a standby carrying it), a standby on that sheet:
    - `expected_video_id`, `opened_at=now`, `created_at=now`;
    - whatever the sheet's cameras are doing, and whatever the window says.
  - **Linking.** If a camera run (kind live/standby, status queued/processing/live) is on that sheet, the API sets
    `A.next_run_id = B`, `B.prev_run_id = A` and `A.handoff_at = now`.
  - **Window fallback.** The window-start standby opens only for sheets with no upcoming entry for that playlist.
  - **Closing.** A lookahead standby is never closed for being outside the window. It is given up `STANDBY_MAX_S`
    after `opened_at` if its stream never came.
  - **Matching.** `_standby_for(repo, playlist_id, sheet, video_id=None)` prefers the standby whose
    `expected_video_id` is the stream's.
  - **The cap.** `camera_games` counts distinct sheets.
  - `_hand_over` keeps `opened_at`.

- [ ] **Step 1: Failing tests.**
  - `test_a_lookahead_standby_opens_ten_minutes_before_the_scheduled_start` (09:49 none; 09:50 one, carrying the
    expected id).
  - `test_it_opens_on_a_sheet_still_read_by_the_game_before_and_links_them` (prev/next set, `A.handoff_at`).
  - `test_the_window_standby_skips_a_sheet_with_an_upcoming_stream`.
  - `test_its_stream_takes_it_over_by_the_expected_id`.
  - `test_a_lookahead_standby_with_no_stream_is_given_up`.
  - `test_the_cap_counts_sheets_not_runs`.
- [ ] **Step 2: FAIL. Step 3: Implement. Step 4: PASS** (playlist tests, `tests/test_service_live.py`).
- [ ] **Step 5: Commit:** `playlists: each sheet's next game recorded from 10 min before its scheduled start`.

### Task 3: the game read from its opening, and the API's word on ownership

**Files:**
- Modify: `src/curling_score/service/api.py` (job claim payload for camera jobs; `progress` reply; `progress` body
  `own_until`)
- Modify: `src/curling_score/service/worker.py` (`ApiClient.progress(..., extra=None)` adds `extra` to the body)
- Test: `tests/test_service_live.py` (or the file testing the camera claim and progress), `tests/test_worker.py`

**Interfaces:**
- Produces, in the claim payload and in the progress reply's `game` dict for camera_live jobs:
  - `"analysis_from"`: ISO; `opened_at` for a lookahead run, else `published_at − 600 s`.
  - `"handoff_at"`: ISO of `run.handoff_at` or null.
  - `"floor_at"`: ISO or null.
  - `"floor_pending"`: bool.
- B's floor:
  - no `prev_run_id`, or A not a camera run, or A failed: `floor_at = analysis_from`, not pending;
  - `A.own_until` set: `floor_at = A.own_until`;
  - `now > B.opened_at + FLOOR_WAIT_S`: `floor_at = analysis_from`;
  - otherwise `floor_pending = True`.
- A progress body may carry `"own_until"` (ISO); the API stores it on the run once.

- [ ] **Step 1: Failing tests.**
  - `test_a_lookahead_game_is_read_from_its_opening`.
  - `test_the_earlier_game_hears_when_the_next_one_opened`.
  - `test_the_later_game_waits_for_the_earlier_ones_word` (pending, then `floor_at == A.own_until` after A
    reports).
  - `test_a_first_game_on_its_sheet_reads_from_its_opening`.
  - `test_a_dead_earlier_game_lets_the_later_one_go_in_the_end`, parametrized: A failed / A YouTube / time passed.
  - `test_progress_carries_extra_fields` (worker client).
- [ ] **Step 2: FAIL. Step 3: Implement. Step 4: PASS.**
- [ ] **Step 5: Commit:** `api: a camera game's reading start and its sheet's handoff in the heartbeat`.

### Task 4: the session owns a span of the recording

**Files:**
- Modify: `src/curling_score/game/boardsplit.py` (new `first_changeover`)
- Modify: `src/curling_score/live/session.py` (`LiveSession.set_ownership`, `own_until_s`, `deciding()`, sample
  floor/ceiling, holding while the floor is pending)
- Test: `tests/test_boardsplit.py`, `tests/test_live_session.py` (or the session's test file)

**Interfaces:**
- Produces:
  - `first_changeover(ends, after_t, head_s, *, cleared=None) -> float | None`:
    1. Take the last end starting before `after_t`. If none, return `after_t`.
    2. Walk on while the next end starts within `GAME_GAP_S` of the previous end's `end_s`, and either within
       `CHANGEOVER_MIN_GAP_S` or `cleared(a.end_s, b.start_s)` is falsy.
    3. A qualifying gap returns its midpoint.
    4. No next end and `head_s − last.end_s >= GAME_GAP_S` returns `last.end_s + GAME_GAP_S / 2`.
    5. Otherwise return None (undecided).
  - `LiveSession.set_ownership(*, floor_s=None, floor_pending=False, handoff_s=None, stream_end_s=None)`, all in
    camera seconds:
    - **Floor.** Once `floor_s` is known, samples before it are dropped and `start_s = max(start_s, floor_s)`. While
      `floor_pending`, `_next_end` returns None (nothing is built).
    - **Ceiling.** With `handoff_s` or `stream_end_s` set, `own_until_s` is computed each step from the settled ends
      (`first_changeover(all ends, T*, head)`, where T* is the earlier of the two). Once set, samples at or after it
      are dropped, so ends and games after it vanish.
  - `deciding() -> bool`: a handoff or stream end is known and `own_until_s` is None.

- [ ] **Step 1: Failing tests.**
  - `first_changeover`:
    - gaps within a game (10 s) walk on;
    - a 400 s gap returns its midpoint;
    - a 150 s gap returns its midpoint only with `cleared`;
    - an open game returns None;
    - nothing before `after_t` returns `after_t`;
    - the 09/29 shape (Supper 8 ends, then 470 s, then Super's end 1 just after H) cuts before Super's end.
  - Session, with a fake pipeline (as in the session's existing tests):
    - `test_ends_past_its_own_until_are_not_built`;
    - `test_a_pending_floor_holds_every_end`;
    - `test_the_floor_drops_the_game_before_it_and_numbers_from_one`.
- [ ] **Step 2: FAIL. Step 3: Implement. Step 4: PASS.**
- [ ] **Step 5: Commit:** `live: a camera game owns its span of the sheet -- from its floor to its first changeover`.

### Task 5: the lane and manager carry it, and a late game is followed to its end

**Files:**
- Modify: `src/curling_score/live/manager.py`:
  - `stream.handoff` from every camera_live heartbeat's `game` dict;
  - `own_until` sent once in the heartbeat body;
  - `analysis_start_s(job, recorder)`, used by the trim cut.
- Modify: `src/curling_score/live/lane.py`:
  - `video_sessions.make` uses `analysis_start_s`;
  - `_camera_tick` passes ownership to the session (wall times converted with `recorder.wall_started`), notes
    `stream_end_s` at the first stop, and defers `finish_stream()` while `session.deciding()` is true, up to
    `OVERRUN_MAX_S`;
  - once `own_until_s` is known, it finishes when `head ≥ own_until_s + BOARD_BEHIND_S + 30`.
- Modify: `src/curling_score/service/api.py`. The progress reply keeps saying `stop` once the stream ended; the
  worker decides when to obey.
- Test: `tests/test_live_lane.py`

**Interfaces:**
- Consumes: Task 3's fields; Task 4's `set_ownership`, `own_until_s`, `deciding()`.
- Produces: `analysis_start_s`, and the trim cut at it.

- [ ] **Step 1: Failing tests.**
  - `test_a_lookahead_game_is_read_and_kept_from_its_opening`.
  - `test_the_lane_passes_the_handoff_and_floor_to_the_session`.
  - `test_a_game_still_going_when_its_stream_ends_keeps_recording`, then finishes once `own_until` is known plus the
    margin.
  - `test_it_gives_up_following_after_45_minutes`.
  - `test_own_until_is_sent_once`.
- [ ] **Step 2: FAIL. Step 3: Implement. Step 4: PASS** (lane, manager, worker tests).
- [ ] **Step 5: Commit:** `live: the earlier game decides where it ends and is followed there; the later reads from it`.

### Task 6: every shot linked to the stream that shows it

**Files:**
- Modify: `src/curling_score/timeline.py`:
  - `build_document(..., wall_started=None)` adds `source.wall_t0 = wall_started − offset` (the offset in effect at
    the recording's head);
  - `watch_url_at` never emits a negative t (None below 0);
  - `youtube_url` is None for t < 0.
- Modify: `src/curling_score/live/session.py`, `live/lane.py` (pass `wall_started`)
- Modify: `src/curling_score/service/api.py`:
  - publish/complete copy `doc.source.wall_t0` onto the run;
  - `game_doc` / `chart_doc` / export call `stream_links(doc, run)`.
- Modify: `src/curling_score/service/` new `streamlinks.py`
- Test: `tests/test_timeline.py`, new `tests/test_streamlinks.py`

**Interfaces:**
- Produces: `streamlinks.annotate(doc, run, get_run) -> doc`. For each shot whose wall time
  (`run.wall_t0 + t_release_s`, else `t_enter_s` / `t_rest_s`) falls outside `[run.published_at,
  run.stream_ended_at or +inf)`, it sets `shot["video"]`:
  - `{"id": n.video_id, "shift_s": run.wall_t0 − n.wall_t0}` when the prev or next run `n` (by
    `prev_run_id`/`next_run_id`) has `wall_t0` and a stream covering that wall time;
  - `None` otherwise.
  - Shots inside their own stream get no key.
  - It never raises; on any error the doc comes back as it was.

- [ ] **Step 1: Failing tests.**
  - `test_an_early_shot_links_to_the_previous_stream`.
  - `test_a_late_shot_links_to_the_next_stream`.
  - `test_a_shot_with_no_stream_says_none`.
  - `test_a_shot_inside_its_stream_is_untouched`.
  - `test_a_shot_before_the_stream_has_no_negative_link` (timeline).
  - `test_the_document_carries_wall_t0`.
- [ ] **Step 2: FAIL. Step 3: Implement. Step 4: PASS** (timeline, streamlinks, the API's game doc tests).
- [ ] **Step 5: Commit:** `links: a shot outside its stream points at the neighbouring stream, or none`.

### Task 7: the viewer plays a shot from whichever stream has it

**Files:**
- Modify: `frontend/core/shots.mjs`:
  - `shotVideo(shot, leadIn, doc) -> {id, t} | null`;
  - `shotVideoTime` stays a wrapper returning its `t` for this doc's video only, `null` otherwise.
- Modify: `frontend/core/watch.mjs` (rows carry `video`), `frontend/runtime/player.mjs` (`show(id, t, autoplay)`,
  `currentVideo()`, `noStream(on)`)
- Modify: `frontend/viewer/App.jsx`:
  - seeks go through `shotVideo` and `player.show`;
  - the YouTube link per shot;
  - "No stream for this shot" overlay.
- Modify: `frontend/viewer/Watch.jsx` (follow skips while another video is loaded), `frontend/core/flag.mjs` (the
  shot's video id)
- Test: the viewer's JS tests (`tests/test_viewer_js.py` or `tests/test_*_js.py`)

- [ ] **Step 1: Failing tests (JS through node, as the repo's JS tests do).**
  - `shotVideo` for a shot with no `video` key gives the doc's id; with `video: {id, shift_s}` gives `{id, t + shift}`;
    with `video: null` gives null.
  - `test_follow_ignores_another_video`.
  - The YouTube link for a null-video shot is hidden.
- [ ] **Step 2: FAIL. Step 3: Implement.** `player.show` calls `loadVideoById`/`cueVideoById` only when the id
  changes, else `seekTo`; the overlay is a sibling of the player element. Rebuild the bundles.
- [ ] **Step 4: PASS**, and `npm run check`.
- [ ] **Step 5: Commit:** `viewer: a shot is played from the stream that shows it, or says there is none`.

### Task 8: privacy page, results, and the deploy handed over

**Files:**
- Modify: `src/curling_score/service/static/privacy.html`. Recording starts 10 min before the stream's scheduled
  start, replacing the "up to an hour and a half before" sentence, which stays only for a league with no scheduled
  stream. A game still going when its stream ends is followed to its end, at most 45 min.
- Modify: `tests/test_service_api.py` (privacy assertions)
- Create: `docs/superpowers/plans/2026-11-14-cameras-default-p3-overlapping-draws.results.md`

- [ ] **Step 1: Privacy test and text; PASS.**
- [ ] **Step 2: Headless viewer check.** Run devserve with a seeded doc (an early shot linked to another video, a
  null shot) at 390×844 through CDP. Check the switch, the overlay and the link (memory `headless-phone-check`).
- [ ] **Step 3: Results doc; final whole-branch review; its fixes.**
- [ ] **Step 4: Hand over the deploy.** API first, then the workers via `gated_restart.sh` (compose change: 5 live
  slots). The gate is the next Tuesday Supper → Super night (or any back-to-back night):
  - Super games are whole from the first rock.
  - Supper charts have no Super tail.
  - Early shots open the Supper stream at the right moment.
  - Late Supper shots link to Super's stream, or show no stream.
