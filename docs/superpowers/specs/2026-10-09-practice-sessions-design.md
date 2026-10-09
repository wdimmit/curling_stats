# Practice sessions: every delivery on a sheet, within seconds

**Status:** design approved section by section on 2026-10-09. The user's picks:
- Video comes from a practice playlist: one long-running YouTube stream per sheet, ahead of the direct camera feeds.
- Each throw reaches the page within ~15 s of the stone stopping (a new per-throw path, not the per-end build).
- A session records every delivery on its sheet between Start and Stop; the user can hide the ones that aren't theirs.
- Several sessions may run on one sheet at once. One watch per sheet stream does the work, and every active session sees its throws.
- The page is a new phone list in the style of the practice mockups (canvas EVXrqPJCCTtED9KtmVzSJA, "3 · Live A"), not the game viewer.

## Context

The practice mockups (2026-09-26) assumed a computer at the club with direct camera access. That is still the goal, but the per-throw analysis, the session model and the page can all be built now against YouTube, with the stream as a stand-in for the camera feeds. When the feeds arrive, only the recorder changes.

Today nothing runs per throw. The live path (`live/session.py`) builds an end only once the next end has run 300 s (`segment.settled_ends`), then runs `analyze.build_one_end` over the whole end, ~130 s of lane per end. Most of what a practice card needs is already per-rock inside that build, each part anchored on its own release, arrival or rest:

| Stage | Code | Ready by |
|---|---|---|
| Delivery confirmed at rest (overhead, destination panel) | `detect/delivery.find_deliveries` | rest + 3-8 s (`REST_CONFIRM_S` 3 s, persistence ~5 s) |
| Release (overhead, throwing panel) | `detect/release.find_releases` | release + 1-2 s |
| Release from the long camera, when the overhead missed it | `game/sidereleases` | hog crossing + 0.5 s |
| Throwing-end hog crossing (long camera, ds13c) | `game/hogtime` | release + 6.5 s |
| Far hog crossing (overhead tracks) | `game/fartime` | arrival |
| Target broom (destination-facing long camera, broom4) | `game/broomtime` | tee crossing + ~0 s |
| Delivery path and thrown line | `game/linetime.read_delivery`, `find_path` | release + 5 s; rest + 1 s |
| House after the rock | `game/shots` | rest + 12 s (practice uses ~3 s) |

The parts that need a whole end are left out of practice: rock numbering, `fit_end`, blanks, scores, hammer, team colours, `drop_clearing`, `lost_rocks`, and (in v1) shot type.

Calibration reads empty-sheet paint through medians of idle frames (`analyze.calibrate_from`). A practice sheet is mostly empty, which suits it. The recorder records only from segment 0 today (`live/recorder.py`, `live_start_index 0`, `_require_first_segment`), but YouTube keeps ~1 h of a long stream (720 × 5 s segments), so a practice recorder can start some minutes back and calibrate from that lookback almost at once, since a recording catches up at ~15x.

## 1. Architecture and session lifecycle

**Finding the stream.** The admin adds the practice playlist as a watched playlist with `practice: true`. The league poller skips practice playlists. On Start, the API lists that playlist's live videos (YouTube Data API, ~2 quota units) and takes the one whose title names the sheet (`source.sheet_from_title`). Nothing per sheet is stored ahead of time.

**Watches and sessions.** A `PracticeWatch` is the work on one live sheet stream; a `PracticeSession` is one user's window on it.
- The first Start on a sheet creates the watch and its job (kind `practice`). A later Start on the same sheet adds a session to the running watch.
- The watch publishes every throw, stamped with stream time and wall time (the stream's actual start + t).
- A session's throws are the watch's throws released between that session's Start and its end, so every active session sees them, and overlapping sessions each see their own window.
- The watch stops once its last session ends. Each session's throws are then frozen into its own document, so history never depends on the watch.

**Start is refused** (404) when the sheet has no live stream in the practice playlist.

**Worker.**
- `LiveManager` claims `practice` jobs alongside `live` ones, from the same `MAX_STREAMS` budget.
- The practice recorder starts `LOOKBACK_S` = 1200 s back in the DVR window (`live_start_index -240`). Stream time stays sequence × 5 s.
- The watch calibrates from the lookback, then watches. Only throws released after the earliest active session's Start count; the lookback is for calibration only.
- A worker restart resumes the recording while the session's start is still in the DVR hour. The first-hour rule does not apply.

**Lane priority.** Practice work goes before league ends: a card is due in seconds, an end has minutes. On a full league night each practice throw delays league ends by ~8 s of lane.

**Publishing.** After each throw the worker uploads the watch document through the existing artifacts and publish calls. The page polls its session every 3 s, revalidated by ETag as the viewer does.

**A session ends** on the first of:
- Stop on the page;
- 20 min with no throws (`IDLE_S` 1200);
- 3 h (`MAX_S` 10800);
- the stream ending, or the watch failing.

The worker finishes the throws it has in hand and publishes a final document before completing.

## 2. The per-throw tracker

A new package, `src/curling_score/practice/`, beside `live/`. `PracticeWatch.step()` does one unit of work per lane call, like `LiveSession.step()`. Video work goes through a pipeline object so the decisions are testable without video.

**Rolling detection.**
- Each step decodes from where the last one stopped to the recording head minus ~2 s. It is one decode for both overhead panels (`frames.windows`, as `EndContext.one_pass` does), with ds17b at 448 on both panels at 10 fps.
- Detections go into a rolling buffer of the last ~90 s per panel.
- Either panel may be the destination, because practice is thrown both ways.

**Finding a delivery.**
- `delivery.find_deliveries` runs over the buffer's trailing window on each panel.
- A delivery whose rest is confirmed and that has not been emitted yet becomes a throw. Deliveries are deduped by rest time and rest position across overlapping windows.
- Whether `find_deliveries` is stable on a sliding window, with no end boundaries, is the first thing phase 1 tests.

**Per-throw stages.** These are existing per-rock code, given the direction from the panel the stone came to rest in:
- **Release:** `release.find_releases` on the other panel's buffer, the nearest release 6-30 s before arrival. Failing that, `sidereleases` from the long camera facing the thrower (the long camera is preferred past the throwing T-line).
- **Weight:** `hogtime` for the throwing hog crossing and `fartime` for the far one. Their difference is the hog-to-hog split.
- **Broom:** `broomtime`. Null when no broom was held still.
- **Line:** `linetime.read_delivery` and `find_path`. They give the miss at the broom, the hog-line offset, the side (narrow or wide) and the curl direction.
- **Rest:** the house over ~3 s after rest. It gives position, ring and distance to the tee.

**Throws that never arrive.** An overhead release with no arrival within 30 s becomes a throw with `arrived: false`, shown as "Released, didn't arrive (hogged?)". This is v1's stand-in for `farfollow`.

**Latency budget.** Rest confirmation takes 3-8 s and the stages 5-8 s. Each long-camera ffmpeg read of a growing TS pays a 10 s seek lead (`frames.seek_lead`), so p90 may land nearer 20 s than 15. Phase 1 measures it before anything is tuned.

## 3. Data and API

### Records (`service/records.py`, Firestore)

`PracticeWatch`:
- `id` (`pw_…`), `sheet`, `video_id`, `playlist_id`, `job_id`;
- `status`: starting | calibrating | watching | ended | failed;
- `stream_start`: YouTube's actual start time, for wall times;
- `doc_key`, `revised_at`, `error`.

`PracticeSession`:
- `id` (`ps_…`), `user_id`, `sheet`, `watch_id`;
- `hand`: right | left, so curl becomes in-turn or out-turn;
- `started_at`, `ended_at`;
- `end_reason`: stop | idle | max | stream_ended | failed;
- `hidden`: the throw ids the user hid;
- `doc_key`: the frozen throws, set when the session ends.

### The watch document (GCS, `practice/{watch_id}.json`)

```json
{
  "practice": 1, "sheet": 1, "video_id": "…", "status": "watching",
  "recorded_s": 5321.0, "updated_at": "2026-10-09T18:32:05Z",
  "throws": [{
    "id": "t_4870.2", "t_release_s": 4870.2, "released_at": "…", "t_rest_s": 4893.6,
    "published_at": "…", "house": "top", "color": "red", "arrived": true,
    "split_s": 13.8, "release_speed": 2.41, "curl": "left",
    "broom": {"x": -0.45, "y": 0.0},
    "line": {"miss_m": -0.12, "side": "narrow", "hog_offset_m": 0.08, "confirmed": true},
    "rest": {"x": -0.1, "y": 0.4, "to_tee_m": 0.41, "ring": "4"},
    "track": [[0.0, 0.0, 3.2], [0.5, -0.02, 2.6]]
  }]
}
```

- Coordinates are in metres in the destination house's frame, as in the timeline.
- `ring` is one of button | 4 | 8 | 12 | short | long | out.
- `broom` and `line` are null when absent.
- `track` is thinned to a few points per second.

### API (`service/api.py`)

Everything sits behind `PRACTICE_ENABLED`, as `LIVE_ENABLED` does.
- `POST /api/practice/sessions {sheet, hand}`: signed in, rate-limited (`bump_rate_limit("practice:"+user)`). Joins the sheet's running watch or creates one, and returns the session. 404 when the sheet has no live stream.
- `GET /api/practice/sessions/{id}`: owner only. Returns the session plus the watch's throws in its window, minus hidden ones, through `json_revalidated`.
- `POST /api/practice/sessions/{id}/stop`: owner only.
- `PATCH /api/practice/sessions/{id}/throws/{tid} {hidden}`: owner only.
- `GET /api/me/practice`: my sessions, newest first. The page uses it to resume an active session.

### Worker side

- Practice jobs reuse the existing artifacts, publish, progress and complete calls. For a `practice` job, a publish updates the watch, not Sources.
- The API keeps session timeouts. On each heartbeat (60 s) and each publish it ends the sessions idle for `IDLE_S` or past `MAX_S`. Once no session is active, the response carries `stop: true`, and the worker finishes the throws in hand and completes.
- On completion each session's throws are frozen into `practice/sessions/{id}.json`.

### Admin

`practice: true` is set on a playlist through the existing `POST`/`PATCH /api/admin/playlists`. The league poller skips such playlists, and Start reads them.

## 4. The page: `/practice/sheet/{n}`

A site page:
- `service/static/practice.html` (`data-page="practice"`) and a route in `api.py` returning `page("practice.html")`;
- `frontend/site/Practice.jsx`, plus an entry in `PAGES` in `frontend/site/main.jsx`;
- the display logic in `frontend/core/practice.mjs`, with unit tests: turn from curl and hand, ring labels, "cm narrow/wide", the summary figures.

It uses the mockups' palette and Source Sans 3, like the game list. It has four states:

1. **Signed out:** "Sign in to practise on sheet N", with the usual Firebase sign-in.
2. **Ready.**
   - "Sheet N", whether its stream is live, and a right-/left-handed toggle remembered in localStorage (wrapped in try/catch).
   - **Start session**, with the note "Ends by itself after 20 minutes with no throws".
   - When others are practising on the sheet: "Others are practising here; you'll share throws."
   - If I already have an active session on this sheet, the page opens it instead, so a reload or a locked phone loses nothing.
3. **Live**, after the mockups' "Live A":
   - a header: Sheet N, clock, **End session**;
   - a status line: "Calibrating… (~2 min)", then "Watching for throw N";
   - the latest throw as a card: turn, then Line ("12 cm narrow of the broom · confirmed", or "No broom"), Weight ("13.8 s hog to hog · out-turns so far 14.3") and Came to rest ("4-foot, 0.4 m long, 0.1 m left"), with a small SVG house showing the rest and the broom;
   - a summary strip: throws, weight range, how many within 15 cm of the broom;
   - a table of earlier throws (Turn, Weight, Line, Rest), each with a "Not mine" action that hides it;
   - throws that never arrived read "Released, didn't arrive (hogged?)".

   It polls every 3 s and pauses while the page is hidden.
4. **Ended:** the same list under "Session ended" with the reason (you stopped / 20 min without a throw / 3 h limit / the stream ended), and **Start another**.

**Not in v1:**
- the mockups' setup choices (drill, partner, target);
- clips and replay;
- a history page;
- "Missed one?";
- shot type;
- face-based attribution.

## 5. Testing and phases

### Test data

Only cached VODs and committed datasets are used, with no new downloads.
- **Full games** give hundreds of throws in both directions, with the per-end timeline as the reference.
- **The ~20 min warm-up before each game** is real practice. It has no reference timeline, so a sample is hand-checked against the side camera.

### Replay harness

- `curling-score practice-replay <video> --from S --to E` plays a cached video into a growing TS at real time (`live/replay.ReplayRecording`). It runs a watch on it and logs each throw with its latency: publish time minus rest time, on the replay clock.
- `scripts/practice/compare.py` matches its throws to the timeline's shots by rest time and position. It reports:
  - recall of arrived rocks and false throws;
  - deltas in rest position and hog-to-hog split;
  - broom present/absent agreement and miss at the broom;
  - latency p50 and p90.

**Rough-in targets:**

| Measure | Target |
|---|---|
| Recall of arrived rocks | ≥ 90% |
| False throws | ≤ ~1 in 50 |
| Rest position vs the timeline | within 5 cm |
| Split vs the timeline | within 0.1 s |
| Latency p90 | ≤ 15 s; reported as measured if it misses |

### Unit tests

Run in affected subsets, because the full suite OOMs on this box.
- The tracker's emit and dedupe logic, on synthetic detection buffers.
- The recorder's lookback arguments and clock.
- The API, with `FakeVerifier`: start/join, window filtering, hide, idle and max timeouts, `stop: true`, freezing on completion, owner-only reads, the 404 for a sheet with no stream.
- `practice.mjs`.

### Phases

Each phase is its own plan step and commit run.
1. **Tracker and replay harness, offline.** This is the risk. The recall and latency numbers go to the user before phase 2.
2. **Recorder lookback and the `practice` job in the worker lane.** Run against a real practice stream from a script, with no API.
3. **Records, API and the admin playlist flag**, with `PRACTICE_ENABLED` off.
4. **The page**, checked headless at 390×844 with touch emulation.
5. **Deploy, only when the user asks.** Order:
   1. the API with the flag off;
   2. the workers (from a clean worktree);
   3. the practice playlist added;
   4. the flag on.

   Acceptance is a real session on the ice with a broom held.

### Needed from the user later

- the practice playlist's URL;
- whether the per-sheet streams are running now or still to come.
