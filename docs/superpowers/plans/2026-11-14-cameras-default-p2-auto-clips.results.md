# Cameras by default, phase 2: results (2026-11-14)

Branch `camera-direct`, commits 1fb6bab4..HEAD. Nothing is deployed yet.

## Built

- **Cameras by default.** `WatchedPlaylist.input` defaults to `"cameras"`, and so does the admin form.
  - The 9 playlists stored without the field read as cameras.
  - YouTube stays selectable per league, and stays every fallback.
- **The auto pass.** At a camera run's completion, every released delivery of each shown game is queued, one job an
  end (`ca_<run>_<g>.<e>`, user `auto`), `LIVE_SETTLE_S` later.
  - An hourly step re-asks runs that were reprocessed or missed.
- **When it runs.** Auto jobs are a third tier, handed only to the clips container's auto loops (`CLIP_AUTO_LOOPS`
  5), and only while no live, standby or practice run is recording. That check is cached 60 s, and each mode has its
  own idle memory.
  - Shot asks show nothing of the auto pass ahead of them.
- **What it cut is seen.**
  - The shot page reads the auto job's result.
  - A play's report records rocks already cut by a shot ask or the auto pass as a done batch, so nothing is asked for
    again once the camera files are gone.
  - A run is `clipped_at` when its last auto job finishes.
- **Retention.**
  - A clipped kept game goes after `WORKER_CAMERA_DAYS` (1); any game after `WORKER_CAMERA_MAX_DAYS` (3); a game
    whose cut is pending never goes by age.
  - Under the disk floor: clipped games first, then YouTube recordings, then unclipped games ("clips lost").
  - Games are dated by `kept_at`. An idle worker prunes hourly.
  - The prune asks the API (`/api/worker/clips/done`). With no answer, no game counts as clipped.
- **Settings.** `CAMERA_KEEP_YOUTUBE` "0". Clips container `cpus` 20, `cpu_shares` 256 (the live workers have
  1024).
- **Privacy page.** For camera games the stream's video is not copied, and the camera recording is kept while every
  shot is clipped: about a day, never more than three.

## Verified

- **Unit and API tests:** 987 pass across the 16 test files this phase touched, run together in the worktree.
- **End to end:** `test_a_camera_night_end_to_end` drives the API in-process.
  1. A camera run completes and its ends are queued.
  2. An auto claim while another game is live gives 204.
  3. With that game ready, every end is claimed and completed.
  4. The run is `clipped_at`; `/clips/done` says True; the shot's page says ready.
- **Compose:** `docker compose config` resolves. All three services carry `CAMERA_KEEP_YOUTUBE` "0" and the camera
  days. The clips service carries `CLIP_AUTO_LOOPS` 5 and `cpu_shares` 256.

## The gate, after the deploy (the next camera night)

- Every delivery is clipped within about 1.5 h of the last game.
- The API logs no auto claims answered while a game or practice is recorded.
- No `live/<id>-youtube` is written.
- Kept camera directories go once clipped and a day old.
- A play opened after that still plays its clips.
