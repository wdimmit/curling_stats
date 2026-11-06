# Camera-direct phase 3: league games from the cameras -- Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A league playlist set to `cameras` has its live games read from each sheet's four camera files instead of the YouTube stream, published on the YouTube video's clock, with reprocess from the kept camera files.

**Architecture:** The live pipeline already reads a `CameraFeed` (phases 1-2). This phase adds the clock (feed time -> the YouTube video's t, found by matching one HLS segment of the live stream against the `long_a` camera), the service plumbing (a playlist `input` setting, a `camera_live` job kind only camera workers claim, a stop when the stream ends), keeping the four files for reprocess, and two gates: an offline replay of the 11/05 league recording against its published YouTube charts, then one live league night.

**Tech Stack:** Python 3.12, PyAV/FFmpeg, FastAPI service + Firestore, the worker's docker compose on the Ryzen.

**Spec:** `docs/superpowers/specs/2026-10-25-pluggable-input-design.md` ("League games on cameras", "Phases and gates" 3). Phase 2's plan and results: `docs/superpowers/plans/2026-11-04-camera-direct-phase2{,.results}.md`.

## Global Constraints

- Branch `camera-direct`, worktree `.claude/worktrees/camera-direct`; nothing merges to main before the project is done. Deploys go out from a clean worktree of the branch, carrying main's back-line commits (`e14b951b`, `de1dda13`) -- the workers run `de1dda13` today and a deploy without them rolls back pipeline version 2026.11.04.1.
- League games stay keyed to the club's YouTube video: viewer, deep links, dedupe, `played_at` and the playlist trigger unchanged.
- The YouTube stream is not downloaded for a camera game. The only YouTube media fetched is the HLS playlist and at most four 5 s segments per run (ask YouTube little: memory `youtube-gentle`).
- "Everything published (timeline t, `youtube_url`, clip times) uses the published clock": `published_t = feed_t + offset_s`.
- If the segment match fails, fall back to the stream's `actualStartTime` (`Run.published_at`) and flag the run `aligned: "start_time"`.
- A camera run that fails falls back as a failed live run does today (the poller queues the archived video).
- Only a worker with `WORKER_CAMERAS=1` claims camera work.
- No pipeline version bump in this phase (a bump requeues streams; memory `youtube-gentle`).
- Tests: `/home/tcuser/src/curling_score/.venv/bin/python -m pytest <files> -q` from the worktree; run affected files only (the full suite OOMs on the dev box). Firestore changes run `tests/test_repo_contract.py` against the emulator first (memory `firestore-emulator`).
- Times quoted to the user in Seattle time. No heavy runs on the Ryzen while a practice session is live or in league hours (from 17:00); no new VOD downloads without the user's OK.

## Review Focus

1. **An ambiguous match** (an empty sheet, a still scene, a camera re-aimed): the match margin is low (phase 0: 1.2-2.5). A wrong offset moves every deep link of the game. Expected: two segments must agree within 2 frames, else more tries, else `start_time` -- never a single unconfirmed match. Pinned in Task 2 and Task 8.
2. **No segment 0** (a stream restarted, or older than YouTube's rewind window): the segment's video time cannot be computed. Expected: fall back to `start_time`, never guess from `EXT-X-MEDIA-SEQUENCE x 5`. Pinned in Task 5.
3. **The stream ends but nobody says so** (a poll failing, YouTube slow to report): the cameras never stop by themselves. Expected: the recorder's 5 h cap ends it, the run is published and completed, and the files are not kept (not whole). Pinned in Task 8.
4. **More sheets live than the cap, or a sheet with no number in its title, or one outside `CAMERA_SHEETS`:** Expected: that game goes to YouTube exactly as today. Pinned in Task 6.
5. **A worker that cannot see the cameras** (the laptop stand-in): Expected: it is never handed `camera_live` or `camera_vod`, however the queue is ordered. Pinned in Task 7.

---

### Task 1: The published clock

**Files:**
- Modify: `src/curling_score/timeline.py` (`build_document`, new `VIDEO_TIME_KEYS`, `DURATION_KEYS`, `shift_times`)
- Modify: `src/curling_score/live/session.py` (`LiveSession.clock`, `document()`)
- Create: `tests/fixtures/timelines/live-dbV9wuA45Z0.json` (the published 11/05 sheet 3 chart, `gs://curling-chart-curling-stats-508323/runs/dbV9wuA45Z0/*/timeline.json`)
- Test: `tests/test_timeline.py`, `tests/test_live_session.py`

**Interfaces:**
- Produces: `timeline.VIDEO_TIME_KEYS: frozenset[str]` = `{"start_s", "end_s", "t_release_s", "t_hog_s", "t_tee_s", "t_far_s", "t_enter_s", "t_rest_s", "t_video_s", "read_at_s"}`; `timeline.DURATION_KEYS: frozenset[str]` (every other numeric `*_s` key in the fixture: `long_split_s`, `long_split_panel_delta_s`, `thinking_time_s`, `split_s`, `entry_speed_m_s`, `release_speed_m_s`, `v_hog_m_s`, `recorded_s`, `duration_s`, ...).
- Produces: `timeline.shift_times(games: list[dict], offset_s: float) -> list[dict]` -- a deep copy with every numeric value under a `VIDEO_TIME_KEYS` key, at any depth, plus `offset_s`, rounded to 2 places; `None` stays `None`.
- Produces: `build_document(..., clock: dict | None = None)`; with a clock `{"kind": "cameras", "offset_s": float, "aligned": "segment" | "start_time"}` it shifts the games before building `youtube_url`, writes `duration_s` + offset, and adds `source["clock"] = clock`. Without one, the document is byte-identical to today's.
- Produces: `LiveSession.clock: dict | None` (attribute, default None, settable by the lane at any time); `document()` passes it on and writes `live.recorded_s` on the published clock.

- [ ] **Step 1: Write the failing tests**

```python
def test_every_seconds_field_is_classified():
    """A new *_s field must be put in VIDEO_TIME_KEYS or DURATION_KEYS, or a
    camera game publishes it on the wrong clock."""
    doc = json.loads((FIXTURES / "timelines/live-dbV9wuA45Z0.json").read_text())
    seen = {k for k, v in walk_items(doc) if k.endswith("_s") and isinstance(v, (int, float))}
    assert seen <= timeline.VIDEO_TIME_KEYS | timeline.DURATION_KEYS
    assert not timeline.VIDEO_TIME_KEYS & timeline.DURATION_KEYS

def test_shift_moves_times_not_durations():
    games = [{"start_s": 100.0, "end_s": 200.0, "ends": [{"start_s": 100.0, "end_s": 150.0,
              "shots": [{"t_release_s": 110.0, "t_rest_s": 130.0, "t_hog_s": None,
                         "long_split_s": 14.37, "thinking_time_s": 13.25}]}],
              "scoreboard": {"read_at_s": 190.0}}]
    out = timeline.shift_times(games, 2410.66)
    shot = out[0]["ends"][0]["shots"][0]
    assert (shot["t_release_s"], shot["t_rest_s"], shot["t_hog_s"]) == (2520.66, 2540.66, None)
    assert (shot["long_split_s"], shot["thinking_time_s"]) == (14.37, 13.25)
    assert out[0]["scoreboard"]["read_at_s"] == 2600.66
    assert games[0]["start_s"] == 100.0          # the input is untouched

def test_document_with_a_clock_links_on_the_video_clock():
    clock = {"kind": "cameras", "offset_s": 600.0, "aligned": "segment"}
    doc = timeline.build_document("vid", "u", 3, 1000.0, {}, games_with_shot_at(t_video_s=86.7),
                                  clock=clock)
    shot = doc["games"][0]["ends"][0]["shots"][0]
    assert shot["t_video_s"] == 686.7 and shot["youtube_url"].endswith("t=686")
    assert doc["source"]["clock"] == clock and doc["source"]["duration_s"] == 1600.0

def test_document_without_a_clock_is_unchanged():
    a = timeline.build_document("vid", "u", 3, 1000.0, {}, games_with_shot_at(t_video_s=86.7))
    assert "clock" not in a["source"] and a["games"][0]["ends"][0]["shots"][0]["t_video_s"] == 86.7
```

In `tests/test_live_session.py`: `test_a_session_with_a_clock_publishes_on_it` -- an existing session fixture with one built end, `session.clock = {"kind": "cameras", "offset_s": 50.0, "aligned": "segment"}`; `document()` has every shot's `t_rest_s` 50 s later than without, and `doc["live"]["recorded_s"] == head + 50`.

- [ ] **Step 2: Run them -- expect FAIL** (`AttributeError: VIDEO_TIME_KEYS`, unexpected keyword `clock`).
- [ ] **Step 3: Implement** the keys, `shift_times` (recursive over dicts and lists), `build_document(clock=)`, `LiveSession.clock`. Fill `DURATION_KEYS` from the fixture until the first test passes; if a key there is a time of the video, it goes in `VIDEO_TIME_KEYS` instead and the shift test gains it.
- [ ] **Step 4: Run** `tests/test_timeline.py tests/test_live_session.py` -- expect PASS.
- [ ] **Step 5: Commit** `timeline: a published clock for camera games (feed t + offset)`.

### Task 2: Matching a piece of the YouTube picture to the long_a camera

**Files:**
- Create: `src/curling_score/feeds/align.py`
- Test: `tests/test_feeds_align.py`

**Interfaces:**
- Consumes: `cameras/layout.load(sheet)`, the `long_a` `Placement` (`crop` in the camera's pixels, `out` in the composite's).
- Produces: `@dataclass(frozen=True) class Match: offset_s: float; margin: float` -- `published_t = camera_t + offset_s`; `margin` = second-best error / best error (outside +-3 frames of the best).
- Produces: `align.match(piece: Path, piece_t_s: float, camera: Path, guess_s: float, sheet: int, *, search_s: float = 30.0) -> Match | None` -- `piece` is a composite video (an HLS segment, or a cut of a kept YouTube recording) whose first frame is at `piece_t_s` on the video clock; `camera` is a `long_a.ts`; `guess_s` is the expected offset. None when the camera file does not cover `piece_t_s - guess_s +- search_s`.
- Produces: `align.agree(a: Match, b: Match, frames: int = 2) -> bool` (within `frames / 60` s), `MIN_MARGIN = 1.15`.

Algorithm (from phase 0's `scratch/camera-direct/align.py`, which held within a frame over 2 h): both sides through `crop -> scale 80x108 (area) -> gray -> 60 fps`, each frame normalised to zero mean and unit sd; mean absolute difference at every frame offset; parabola through the minimum for the sub-frame; a match with `margin < MIN_MARGIN` returns None.

- [ ] **Step 1: Failing tests.** Build a synthetic `long_a.ts` (60 fps, 1920x1080, moving bars, 20 s) and a "composite" piece: 5 s of it, its `long_a` crop placed at the placement's `out` rect of a 1920x1080 frame, starting 7.25 s in, re-encoded.

```python
def test_match_finds_the_offset_to_a_frame(tmp_path):
    cam, piece = synthetic_pair(tmp_path, piece_from_s=7.25, sheet=3)
    m = align.match(piece, piece_t_s=1007.25, camera=cam, guess_s=1003.0, sheet=3, search_s=6)
    assert m is not None and abs(m.offset_s - 1000.0) < 1 / 60 and m.margin >= align.MIN_MARGIN

def test_a_still_picture_matches_nothing(tmp_path):
    cam, piece = synthetic_pair(tmp_path, piece_from_s=7.25, sheet=3, still=True)
    assert align.match(piece, 1007.25, cam, 1003.0, sheet=3, search_s=6) is None

def test_outside_the_camera_file_is_none(tmp_path):
    cam, piece = synthetic_pair(tmp_path, piece_from_s=7.25, sheet=3)
    assert align.match(piece, 1007.25, cam, guess_s=900.0, sheet=3, search_s=6) is None

def test_agree():
    assert align.agree(align.Match(600.0, 2.0), align.Match(600.03, 1.5))
    assert not align.agree(align.Match(600.0, 2.0), align.Match(600.05, 1.5))
```

- [ ] **Step 2: Run -- FAIL** (no module). **Step 3: Implement.** **Step 4: Run -- PASS.**
- [ ] **Step 5: Real check (no commit gate, results noted in the ledger).** On the Ryzen, quiet hours: `match` a 5 s cut of the kept `dbV9wuA45Z0.mp4` (sheet 3, 11/05) at three times 30 min apart against `league-1105/s3-1/long_a.ts`, guess from `anchor.json`'s `wall_started` against the video's `actualStartTime`. Expected: three offsets within 2 frames of each other, margin >= 1.15.
- [ ] **Step 6: Commit** `feeds/align: a piece of the YouTube picture against the long_a camera`.

### Task 3: `live-replay --cameras`

**Files:**
- Modify: `src/curling_score/cli.py` (`live-replay` parser and `_live_replay`)
- Test: `tests/test_cli_live_replay.py`

**Interfaces:**
- Consumes: `replay.CameraReplay(source_dir, out_dir, sheet, speed)` (exists), `align.match`/`agree` (Task 2), `LiveSession.clock` (Task 1).
- Produces: `curling-score live-replay --cameras DIR --sheet N [--video-id ID] (--offset-s X | --align-to VIDEO --guess-s G)`. `video` becomes optional (exactly one of it and `--cameras`). `--offset-s` sets the offset directly. `--align-to` is a kept YouTube recording of the same game: the offset is the `match` (guess `G`, `search_s=30`) of 5 s pieces of it at 25% and 50% of the camera files' length, which must `agree` (exit 1 otherwise). For Gate A, `G` = the camera recording's `wall_started` (`DIR/anchor.json`) minus the stream's `actualStartTime`. The clock goes on the session before the first publish, `aligned: "segment"`.

- [ ] **Step 1: Failing tests** (fakes for `CameraReplay` and the session, as the practice-replay camera tests do): `test_live_replay_cameras_replays_the_four_files` (the session's recording is a `CameraReplay` of `DIR` for `--sheet`, its publishes land in `--out/publishes`); `test_live_replay_cameras_needs_a_sheet` (exit 2); `test_live_replay_offset_goes_on_the_session` (`--offset-s 600` -> `session.clock == {"kind": "cameras", "offset_s": 600.0, "aligned": "segment"}`); `test_align_to_that_disagrees_exits_1` (a fake `match` giving 600.0 then 600.2).
- [ ] **Step 2: FAIL. Step 3: Implement. Step 4: PASS** with `tests/test_cli_live_replay.py tests/test_cli_practice_replay.py`.
- [ ] **Step 5: Commit** `live-replay --cameras: a sheet's camera files through the live session`.

### Task 4: Gate A -- the 11/05 Thursday Morning league from the cameras

No product code. Results go in `docs/superpowers/plans/2026-11-06-camera-direct-phase3.results.md`.

Inputs on the Ryzen: `scratch/camera-direct/league-1105/s3-1` and `s4-1` (09:45-13:00 PST, no gaps), the kept YouTube recordings `curling-cache/videos/dbV9wuA45Z0.mp4` (sheet 3) and `curling-cache-2/videos/kXOQ06TmD0U.mp4` (sheet 4), and their published charts (fours).

- [ ] **Step 1:** Build a throwaway image from the branch head (`docker build -t curling-worker:p3`), not the deployed tag.
- [ ] **Step 2:** In quiet hours (no practice, before 17:00), run both sheets at once, speed 1, as the league would load the box: `live-replay --cameras .../s3-1 --sheet 3 --video-id dbV9wuA45Z0 --align-to .../dbV9wuA45Z0.mp4 --out out-s3` and the same for sheet 4. Record per-end publish lag (`publishes/NNN.json` `live.wall_s` against each new end's `end_s`) and the box's CPU.
- [ ] **Step 3:** `scripts/compare_timelines.py out-sN/timeline.json <published chart>` for both sheets.
- [ ] **Step 4:** Check the deep links of 5 shots per sheet by hand: the kept YouTube recording at each shot's `t_video_s` shows that delivery.

**Gate (all must hold):** games and end counts equal; >= 95% of shots matched (colour, thrown, rest within 10 cm); board scores equal on every end the YouTube chart has one; splits median |diff| <= 0.05 s; the two pieces' offsets agree within 2 frames; deep links 10/10; per-end publish lag p90 <= 180 s with both sheets running. A miss is written up with frames and stops the plan for the user's call; Tasks 5-11 may proceed meanwhile only if the miss is in analysis, not in the clock.

- [ ] **Step 5:** Commit the results doc.

### Task 5: The live stream's segments on the video's clock

**Files:**
- Create: `src/curling_score/live/hls.py`
- Modify: `src/curling_score/live/recorder.py` (`YtDlpRecorder._resolve_hls`/`_fetch_text`/`first_media_sequence` move to `hls`; the recorder calls them)
- Test: `tests/test_live_hls.py`, `tests/test_live_recorder.py` (unchanged behaviour)

**Interfaces:**
- Produces: `@dataclass(frozen=True) class Segment: seq: int; uri: str; extinf_s: float`.
- Produces: `hls.segments(playlist_text: str, base_url: str) -> list[Segment]` (absolute URIs; `EXT-X-MEDIA-SEQUENCE` numbers the first).
- Produces: `hls.first_pts_s(ts: bytes) -> float` -- the first video packet's pts in seconds, unwrapped against nothing (a 2^33 wrap inside one stream's first hours is not expected; if pts(seg k) < pts(seg 0), add 2^33/90000).
- Produces: `hls.video_time_s(seg0: bytes, segk: bytes) -> float` = `first_pts_s(segk) - first_pts_s(seg0)`.
- Produces: `hls.media_playlist_url(video_id: str, *, pot_provider, cookies) -> str | None` (one yt-dlp extraction, the m3u8 format <= 1080p, as the recorder resolves it today).
- Rule: a playlist whose first segment is not `seq 0` gives no video time (`NoStart` raised); the caller falls back.

- [ ] **Step 1: Failing tests:** `segments()` on a stored playlist text (sequence 0, three `#EXTINF:5.005,` entries, relative URIs) gives seqs 0-2 with absolute URIs; `video_time_s` on two small TS byte strings made with PyAV at pts 126000 and 126000 + 90000*15.015 gives 15.015; a playlist starting at `#EXT-X-MEDIA-SEQUENCE:7` raises `hls.NoStart`.
- [ ] **Step 2: FAIL. Step 3: Implement; move the recorder's helpers. Step 4: PASS** with `tests/test_live_hls.py tests/test_live_recorder.py`.
- [ ] **Step 5: Live check during a league (light; ledgered, not a commit gate).** On the Ryzen, for one sheet live on YouTube: fetch the playlist, segment 0 and a segment ~10 min in; `align.match`-style compare that segment's full picture against the worker's own yt-dlp recording of the same stream (`curling-cache*/live/<id>/`) at `video_time_s`. Expected: 0 +- 1 frame. This proves the pts arithmetic without cameras.
- [ ] **Step 6: Commit** `live/hls: a live segment's time on the video's clock`.

### Task 6: A playlist's input, and the poller's choice

**Files:**
- Modify: `src/curling_score/service/records.py` (`WatchedPlaylist.input: str = "youtube"`; `Run.stream_ended_at: datetime | None = None`; `JOB_KINDS += ("camera_live", "camera_vod")`, `CAMERA_KINDS = ("practice", "camera_live", "camera_vod")`)
- Modify: `src/curling_score/service/playlists.py` (`poll(..., camera_sheets: set[int], camera_max_sheets: int)`)
- Modify: `src/curling_score/service/api.py` (`playlist_fields` accepts `input` in `{"youtube", "cameras"}` on POST and PATCH; settings `CAMERA_SHEETS` (default `"1,2,3,4,5"`), `CAMERA_MAX_SHEETS` (default 2), passed to `poll`)
- Modify: `src/curling_score/service/static/playlists.html`, `site.js` (an Input select per playlist: YouTube / Cameras)
- Test: `tests/test_playlist_live.py`, `tests/test_admin_playlists.py`, `tests/test_playlists_js.py`

**Interfaces:**
- Produces: on a `cameras` playlist, a live stream whose title gives a sheet in `camera_sheets`, while fewer than `camera_max_sheets` camera live runs are `queued`/`processing`/`live`, gets `Run(kind="live", source="cameras")` and `Job(kind="camera_live")`. Otherwise the run and job are today's (`source=None`, `kind="live"`).
- Produces: each poll, a video with a camera live run in `processing`/`live` whose `live_status` is no longer `"live"` gets `Run.stream_ended_at = now` (once), before `validate_submission` can skip it as `still_live`/`no_duration`.
- `PRACTICE_MAX_CAMERA_WATCHES` is unchanged (raising it is the user's call); the spec's merge of the two caps is not done here.

- [ ] **Step 1: Failing tests:**
  - `test_cameras_playlist_queues_a_camera_live_job` (sheet 3 in the title -> `run.source == "cameras"`, job kind `camera_live`);
  - `test_camera_cap_sends_the_rest_to_youtube` (`camera_max_sheets=2`, three sheets live -> two camera runs, the third `kind="live"`, `source=None`);
  - `test_no_sheet_in_the_title_goes_to_youtube`; `test_sheet_outside_camera_sheets_goes_to_youtube`;
  - `test_youtube_playlist_is_unchanged` (default `input`);
  - `test_stream_end_is_marked_once` (two polls after `live_status` turns `"none"`: `stream_ended_at` set on the first, unchanged on the second);
  - admin: `PATCH {"input": "cameras"}` saves it; `{"input": "ndi"}` -> 422.
- [ ] **Step 2: FAIL. Step 3: Implement. Step 4: PASS** with the three test files and `tests/test_service_api.py -k playlist`.
- [ ] **Step 5: Commit** `playlists: an input setting; camera live runs up to the cap`.

### Task 7: Claim and heartbeat for camera work

**Files:**
- Modify: `src/curling_score/service/api.py` (`/api/worker/claim`, the job heartbeat `/api/worker/jobs/{id}/progress`)
- Modify: `src/curling_score/service/repo.py`, `firestore_repo.py` (`claim_job` orders `live` and `camera_live` first, then `practice`, then the rest)
- Test: `tests/test_service_live.py`, `tests/test_practice_api.py`, `tests/test_repo_contract.py`

**Interfaces:**
- Produces: a claim without `cameras: true` never receives a `CAMERA_KINDS` job (filter as practice is filtered today).
- Produces: the claim response for `camera_live`/`camera_vod` carries `"source": "cameras"`, and for `camera_live` also `"published_at"` (ISO, `Run.published_at`, the stream's start).
- Produces: the heartbeat of a `camera_live` job whose run has `stream_ended_at` returns `{"ok": True, ..., "stop": True}`.

- [ ] **Step 1: Failing tests:** `test_a_worker_without_cameras_never_gets_camera_work` (queue: camera_live then live then camera_vod; a no-cameras claim gets the `live`, then 204); `test_camera_live_is_claimed_before_vod` (cameras claim, queue vod then camera_live -> camera_live); `test_camera_live_claim_says_cameras_and_start`; `test_heartbeat_stops_a_camera_game_whose_stream_ended`; the repo contract test for the order, both repos.
- [ ] **Step 2: FAIL. Step 3: Implement** (Firestore: one ready query per kind in the order above, as `live` and `practice` are queried today). **Step 4: PASS** (emulator first for the contract test).
- [ ] **Step 5: Commit** `claim: camera work for camera workers only; stop a camera game when its stream ends`.

### Task 8: The worker's camera league game

**Files:**
- Modify: `src/curling_score/live/manager.py` (`_recorder`, `is_camera(job)`, begin/keep for `camera_live`)
- Modify: `src/curling_score/live/lane.py` (league path: `stop_requested` on a `camera_live` stream -> `recorder.finish_stream()`; the aligner's offset onto `session.clock`)
- Create: `src/curling_score/live/clock.py` (`Aligner`)
- Modify: `src/curling_score/cameras/recorder.py` (`finish_stream()`, `whole()`)
- Modify: `src/curling_score/service/worker.py` (kinds: `camera_live` added when `WORKER_CAMERAS=1`)
- Test: `tests/test_live_lane.py`, `tests/test_live_clock.py`, `tests/test_cameras_recorder.py`, `tests/test_worker.py`

**Interfaces:**
- Consumes: `hls.*` (Task 5), `align.match`/`agree`/`MIN_MARGIN` (Task 2), `LiveSession.clock` (Task 1), the claim's `source`/`published_at` (Task 7).
- Produces: `is_camera(job) -> bool` = `job.get("source") == "cameras"`; `_recorder` gives a `CameraRecorder(sheet, root/"live"/video_id)` for any camera job (practice keeps its own path through `is_practice`).
- Produces: `class Aligner(video_id, sheet, feed_dir: Path, wall_started: float, published_at: datetime, *, fetch=hls..., match=align.match, now=time.time)` with `step() -> dict | None` -- call it every lane tick; once the recording reaches `ALIGN_AFTER_S = 150`, it fetches the playlist and segment 0 once, then up to `ALIGN_TRIES = 4` segments 60 s apart, each about `ALIGN_BEHIND_S = 90` behind the live edge; it returns `{"kind": "cameras", "offset_s": o, "aligned": "segment"}` when two consecutive matches `agree`, or, after the tries (or on `NoStart`, or any fetch error), `{"kind": "cameras", "offset_s": wall_started - published_at.timestamp(), "aligned": "start_time"}`. Until then None. Guess for `match`: `wall_started - published_at`; `search_s = 30`. At most 1 playlist + 1 + `ALIGN_TRIES` segment fetches per run.
- Produces: `LiveSession(..., wait_for_clock: bool = False)`; with it set (camera jobs), `publish` is not called while `clock is None` -- ends still build, and the first publish after the clock carries them all (the first end takes 10+ minutes, the clock ~3).
- Produces: `CAMERA_KEEP_YOUTUBE=1` (worker env, user 2026-11-06: capture the YouTube video at the same time): a `camera_live` stream also runs a `YtDlpRecorder` of its video (`Stream.reference`), started and checked with the camera recorder and stopped when it stops; on finish it is kept exactly as a YouTube live game's recording is today (`cache.keep_recording` when whole, else `keep_partial`). It never feeds the session. A reference recorder that fails is logged and dropped; the camera game goes on.
- Produces: `CameraRecorder.finish_stream()` -- stop, as the stream is over; `whole()` is True only after it, when the recording never ended by itself (no stall, not every camera gone, not the cap).

- [ ] **Step 1: Failing tests:**
  - `test_camera_live_job_gets_camera_recorder` (beside the practice one);
  - Aligner, with fakes for fetch and match: two agreeing matches -> `segment`; four disagreeing -> `start_time` with `wall_started - published_at`; `NoStart` -> `start_time` at once; nothing fetched before 150 s recorded; never more than 6 fetches;
  - lane: a `camera_live` stream with `stop_requested` calls `finish_stream()` once, the session finishes and completes; publishes wait for the clock, then carry it;
  - recorder: `finish_stream()` -> `whole()`; a stall then `finish_stream()` -> not whole; the cap -> not whole (Review Focus 3);
  - reference: with `CAMERA_KEEP_YOUTUBE=1` a `camera_live` stream has a `YtDlpRecorder` beside its camera recorder, both stopped on finish, the YouTube one kept as `videos/<id>.mp4` when whole; without the setting, none; a reference that raises on start leaves the camera game running;
  - worker: `WORKER_CAMERAS=1` adds `camera_live` to the live lane's kinds; without it, not.
- [ ] **Step 2: FAIL. Step 3: Implement. Step 4: PASS** with the four test files and `tests/test_live_session.py`.
- [ ] **Step 5: Commit** `worker: league games from the cameras, on the video's clock`.

### Task 9: Keeping a camera game's files

**Files:**
- Modify: `src/curling_score/live/manager.py` (`finish` for a camera league game)
- Modify: `src/curling_score/ingest/prune.py` (`prune(..., cams_root, camera_days, min_free_gb)`)
- Modify: `deploy/docker-compose.worker.yml` (a shared `/data/wdd/curling-cams:/data/cams` in `worker` and `worker2`; `:ro` in `clips`; `WORKER_CAMS=/data/cams`, `WORKER_CAMERA_DAYS=3`)
- Test: `tests/test_live_lane.py`, `tests/test_ingest_service.py` (where `prune` is tested)

**Interfaces:**
- Produces: a finished `camera_live` stream with `keep` and `whole()` moves `<role>.ts` x4 into `$WORKER_CAMS/kept/<video_id>/` and writes `clock.json` (`{"sheet", "wall_started", "clock"}`, the session's clock). Not whole: deleted, like practice. Same filesystem: a rename, not a copy.
- Produces: `prune` deletes kept camera games older than `camera_days` first, then the oldest while the disk is under `min_free_gb`, before any YouTube recording.
- Shared, so a reprocess finds the files whichever worker takes it; phase 4's clips worker reads them from the same place.

- [ ] **Step 1: Failing tests:** whole -> four files and `clock.json` in `kept/<id>/`, `live/<id>/` gone; partial -> nothing kept; prune: a 4-day-old kept game goes, a 1-day-old stays; under the floor the oldest kept game goes before any `videos/*.mp4`.
- [ ] **Step 2: FAIL. Step 3: Implement. Step 4: PASS.**
- [ ] **Step 5: Commit** `keep a camera game's four files for three days, in a shared place`.

### Task 10: Reprocess from the kept files

**Files:**
- Modify: `src/curling_score/analyze.py` (`analyze(..., feed=None, clock=None)`)
- Modify: `src/curling_score/service/api.py` (`POST /api/admin/reprocess` accepts `"from": "cameras"`)
- Modify: `src/curling_score/service/worker.py` (main loop claims `camera_vod` when `WORKER_CAMERAS=1`; `process_job` runs it from `$WORKER_CAMS/kept/<video_id>/`)
- Test: `tests/test_cli_analyze.py` (or `tests/test_analyze_feed.py`), `tests/test_service_api.py`, `tests/test_worker.py`

**Interfaces:**
- Produces: `analyze(url, ..., feed: Feed | None = None, clock: dict | None = None)`: with a camera `feed` there is no download and no strip proxy; calibration is `feeds.cameras.calibrate(feed, until_s=<feed length>)`; everything else reads the feed as the live session does; `build_document(clock=clock)`.
- Produces: reprocess with `"from": "cameras"` on a run whose `source == "cameras"` queues a `camera_vod` job (422 for any other run). Without `from`, reprocess is today's (YouTube).
- Produces: a `camera_vod` job with no kept files fails permanent: "the camera files of <id> are no longer kept" -- never a YouTube download.

- [ ] **Step 1: Failing tests:** analyze with a fake camera feed calls no `ensure_cached`, no `proxy.ensure_proxy`, and passes the clock to the document; the API test for the job kind and the 422; the worker test for the kept-files path and the permanent failure.
- [ ] **Step 2: FAIL. Step 3: Implement. Step 4: PASS.**
- [ ] **Step 5: Commit** `reprocess a camera game from its kept files`.

### Task 11: The privacy page and the settings

**Files:**
- Modify: `src/curling_score/service/static/privacy.html` ("Game video")
- Modify: `deploy/cloudrun.yaml` (`CAMERA_SHEETS`, `CAMERA_MAX_SHEETS`)
- Test: `tests/test_service_api.py` (the page serves; the settings parse)

Copy, after the paragraph's first two sentences (the user reviews it with this plan):

> Some league games are read from the club's cameras directly, with the club's agreement, instead of the stream. That video is recorded only while the game's stream is live, is kept on the processing computer for up to three days so the game can be read again, and is then deleted. Clips of shots are kept as for other games.

- [ ] Steps: failing test that the page contains "read from the club's cameras directly" and that `CAMERA_MAX_SHEETS` defaults to 2; implement; pass; commit `privacy: league games read from the cameras`.

### Task 12: Gate B -- one league night

Not code. Needs the user's go for the deploy (API + workers) and the night.

- [ ] Deploy from a clean worktree of the branch with main's back-line commits; API with `CAMERA_SHEETS=3,4`, `CAMERA_MAX_SHEETS=2`; workers with `CAMERA_KEEP_YOUTUBE=1`; the night's playlist set to `cameras`.
- [ ] During the night: watch claims, the Aligner's result per run (`aligned` must be `segment`), per-end publish lag, decode errors, the two workers' CPU.
- [ ] Next morning, against the YouTube path on the same games -- `analyze` of the YouTube recordings kept that night (no download): `compare_timelines.py`, board, splits; deep links checked on a phone (devserve + cdp, memory `headless-phone-check`). Gate as Task 4, plus `aligned: "segment"` on both runs.
- [ ] Results appended to the results doc; the playlist goes back to `youtube` if anything fails.

## Not in this plan

- The sync watchdog (spec, "Sync watchdog"): reported-only readings; a follow-up once camera games run.
- Clip... and claimed-rock game clips of camera games without `CAMERA_KEEP_YOUTUBE`: they need the clips worker to read `kept/` (phase 4).
- The nightly clip pass (phase 4), NVDEC decode (only if Gate A's lag misses), merging the practice and league caps.

## Decided

- Gate B's reference (user, 2026-11-06): the worker records the YouTube stream of each camera game at the same time (`CAMERA_KEEP_YOUTUBE=1`, Task 8). With that recording kept, Clip... also works for those games as for any live game.
