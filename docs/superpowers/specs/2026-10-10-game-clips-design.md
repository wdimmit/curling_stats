# Game clips: a video of each rock you claimed

**Date:** 2026-10-10. **Status:** approved in conversation; this is the written spec.
Revised the same day at the user's ask: a dedicated clips worker (section 3a).
**Builds on:** `2026-10-04-my-shots-design.md` (plays and the /shots report) and
`2026-10-10-practice-clips-design.md` (the clip cutter, the overlay, signed links).

## Goal

A curler who says which rocks they threw in a league game ("⋯ → I played…") can watch each
of those rocks as a short clip on the /shots report, with the skip's broom, the thrown line
and the aim line drawn over it, the way a practice throw's clip is shown today.

## What the user asked for (2026-10-10)

- When a player claims their rocks for a game, clips of those rocks are generated, and they
  play with the rendered line overlays.
- **Where:** on the /shots report only. The game's Watch page (/g/, /s/, /c/) is unchanged.
- **Old games:** clips are made only while the game's video is still in the worker's cache.
  No video is downloaded again for clips; a game whose video is gone gets none, and the
  report says so.
- **Approach A** (below): a clip job queued by the claim, cutting only that player's rocks.
- **A dedicated clips worker** beside the three main workers, since cutting is light
  (asked during spec review).

## Assumptions (stated in conversation, not contradicted)

- A game clip is a practice clip: the camera facing the thrower from 3 s before the slide
  until the rock leaves its frame, then the camera behind from the far hog line to rest +
  1.5 s; portrait 600x810 H.264; capped at release + 30 s.
- The overlay is the practice overlay: broom ring, thrown line (solid), aim line (dashed),
  with the remembered Lines toggle.
- Clips are private to whoever made the play, as the play itself is.
- Clips are kept indefinitely (about 8 MB per claimed game).

## Approach

The claim queues a short `clips` job. A dedicated clips worker, which can read every main
worker's cache, cuts the player's rocks with the practice cutter, using the calibration and rock times the game's
timeline already holds, and uploads them; the API keeps each rock's clip state on the play,
and the report asks for a signed link when a clip is opened.

Rejected:
- **B, pre-cut every rock nightly.** Claims would be instant and late claims would still get
  clips, but it cuts ~64 rocks a game that nobody may watch, as a standing nightly load.
  It can be added later on top of the same cutter if late claims turn out to matter.
- **C, crop the YouTube embed in the browser.** No worker or storage, but a quarter-frame
  crop of an adaptive embed is blurry, the camera cannot switch cleanly, and covering or
  cropping the player is against YouTube's embed terms.

## 1. Which rocks

The API decides, from the same document the report reads, so the clips are of exactly the
rocks /shots shows as the player's:

- the play's chart (graded, `timeline.apply_overrides`) when `play.chart_id` is set and
  live, else the game's current run (`game_doc`), as `api_my_play_doc` does today;
- cut down with `timeline.for_player(doc, color, slot)`;
- each rock named by **`<game>.<end>.<id>`**: the game index, the end number, and the
  rock's `id` -- the number it had in the run, which `apply_overrides` keeps when grading
  reorders and renumbers an end -- or its `number` where grading set no `id`. A clip is
  therefore tied to the rock in the run, not to its place in a graded order.

Rocks that were never seen (`missing`) are not asked for.

## 2. The job and the queue

- **A queue of its own.** A new `ClipJob` record in a new `clip_jobs` collection, with its
  own worker routes (`/api/worker/clips/...`), rather than a fourth `Job` kind: the job
  routes and both repos write a run's status from its job in eight places (`claim_job`,
  `requeue_expired`, the claim, fail and yield routes, `job_for_run`, `queue_position`,
  `count_queued`), and every one would have needed a clips exception. A `ClipJob` has
  `run_id` = the run the report reads and a `spec` dict carrying everything the worker
  needs, so it never loads a timeline:
  `{"calibration": {"left", "right"}, "rocks": [...]}` (the video and play are fields of
  the job), where each
  rock is `{"name", "house"}` (the end's) plus the rock's own `t_release_s`, `t_rest_s`,
  `t_enter_s`, `t_hog_s`, `v_hog_m_s`, `t_far_s`, `target_broom` and `line`. The API takes
  `calibration` from the document before `for_player` drops it.
- **A clips job never touches its run**: nothing in the clip routes writes a run.
- **Who claims it:** only the dedicated clips worker (section 3a). The three main workers
  keep claiming `vod` in their one-shot loop and `live`/`practice` in their lanes, and
  never see a clips job. If the clips worker is down, clips jobs wait in the queue.
- **Leases:** the worker renews its lease after each rock (`/progress`); a lease that runs
  out puts the job back in the queue, and one that has had `max_attempts` (3) fails.
- **Not while the game is still live.** A live-charted game's recording reaches the cache's
  `videos/` only when its stream ends (`LiveManager.finish` remuxes it then). The clips
  claim skips a job whose run's status is `live`, pushing its `run_after` 5 minutes on; it
  becomes claimable once the run is `ready`. A claim made mid-stream therefore gets its clips minutes after the stream ends,
  not a wrong "video no longer kept".
- **When a job is queued** (API, only with `GAME_CLIPS_ENABLED=1`):
  1. `PUT /api/me/plays/{source_id}` saves a new play, or changes its colour or slot;
  2. `GET /api/me/plays/{source_id}/doc` finds rocks of the player's that no clip job of
     this play has asked for on the run it is reading (a reprocess since; or a play saved
     before this feature -- the backfill needs no script).
- **Never twice:** a rock this play has any clip state for on that run (on its way, made,
  or none coming) is left out of the next job's `rocks`; a job with no rocks left is not
  queued. A rock whose clip failed is not tried again on that run.
- The play rate limit (`PLAY_HOUR_LIMIT`/`PLAY_DAY_LIMIT`) already bounds the PUTs; the
  one-job-per-run rule bounds the doc reads.

## 3. Cutting, on the worker

New module `src/curling_score/game/clips.py` (the game side; it calls into
`practice/clips.py` and `practice/overlay.py`, which stay as they are apart from any shared
helper that moves):

1. **Find the video in any worker's cache**: `<cache>/videos/<id>.mp4` in each of the
   caches listed in `WORKER_CLIP_SOURCES` (section 3a), first found wins -- the cached VOD
   or the kept whole live recording, on whichever main worker ran the game. Partial
   recordings under `kept/` are not used: where they start on the stream's clock is not
   certain. In none of them: complete the job with every rock
   `{"clip": null, "reason": "video_gone"}`. Nothing is downloaded.
2. **Rebuild the `left` and `right` `SideView`s** from the spec's `calibration` (`rect`, `tee_row`,
   `hog_row`, `centre_col`, `lat_px_per_m_at_tee`, `centre_line`) -- the inverse of
   `analyze._side_calibration`. A view without `centre_col` has no lateral fit: its clip
   piece is cut, with no lines.
3. **Turn each rock into a practice throw record** (`throw_from_rock(rock)`):

   | throw field | from the game rock |
   |---|---|
   | `id` | the rock name `<game>.<end>.<id>` |
   | `house` | the end's `house` (`"top"`/`"bottom"`), carried on the spec's rock |
   | `t_release_s`, `t_rest_s` | the same fields |
   | `arrived` | `t_enter_s` is not null |
   | `t_hog_s`, `v_hog_m_s`, `t_far_s` | the new timeline fields (section 4); when absent, `t_far_s` = `t_enter_s` and the others null |
   | `broom` | `target_broom` `{x, y}` |
   | `line.at_hog_x` | `line.at_hog.x` |
   | `line.miss_m` | `line.at_broom.miss_m` |
   | `line.aim_x` | `line.at_tee.x` |
   | `line.start` | `line.start` |

4. **Plan, cut, overlay** with `practice/clips.plan`, `practice/clips.cut` (views map each
   side view to the cached file and its `rect`) and `practice/overlay.overlay`.
   A rock with no release gets `{"clip": null, "reason": "no_release"}`; a cut that raises
   gets `{"clip": null, "reason": "cut_failed"}` and the job goes on.
5. **Upload** each clip through the existing artifacts flow to
   `store.clip_key(video_id, run_id, rock_name)` =
   `runs/<video>/<run>/clips/<game>.<end>.<id>.mp4`.
6. **Complete** with `{"rocks": {rock_name: {"clip": true, "overlay": [...]}, ...}}`.
   A plan that comes out empty for a released rock (no long camera in the calibration)
   gets `{"clip": null, "reason": "no_camera"}`.

The job's progress posts keep its lease alive as other jobs do; about 1.5 s a rock, so
~30 s for a four-player game's 16 rocks once the video is cached.

**Clock check before relying on live recordings:** a game charted live has times on the
recording's clock. A kept whole recording starts at segment 0, so its clock should be the
stream's; this is verified on a real kept recording during the build, before live-charted
games are clipped. If it does not hold, those games get `video_gone` until it is fixed.

## 3a. The clips worker

A fourth service in `deploy/docker-compose.worker.yml`, beside `worker`, `worker2` and
`worker3`, running the same image:

- **Why its own:** each main worker has a cache of its own (`curling-cache`,
  `curling-cache-2`, `curling-cache-3`), so a main worker could only clip the games it ran
  itself. The clips worker mounts all three, **read-only**, and finds a game's video
  wherever it is. It also never waits behind a VOD run or a league night's streams.
- **What it runs:** `WORKER_ROLE=clips` makes `worker.main` run the clips loop
  (`service/clipworker.py`) instead of the pipeline loop: it loads no model and starts no
  live lane, and it claims only from the clip queue. `WORKER_LIVE` and `WORKER_PRACTICE`
  are set to `"0"` on this service too (it shares `worker.env`).
- **Its own scratch:** `CURLING_SCORE_CACHE` is a small volume of its own
  (`/data/wdd/curling-clips`); cut clips are written there and deleted once uploaded. It
  prunes nothing and empties nothing in the other caches -- the read-only mounts make sure.
- **Sources:** `WORKER_CLIP_SOURCES=/data/src1:/data/src2:/data/src3`, the three caches.
- **Light:** no GPU reservation (`deploy: !reset {}`), no `pot` dependency (it never
  talks to YouTube), and `cpus: "2"` so a burst of cuts cannot crowd the main workers'
  CPU-bound lanes on a league night.
- **Its id:** `WORKER_ID: ryzen-clips`.
- **A file pruned mid-cut:** a main worker may prune a video the clips worker is reading.
  An open file survives being unlinked, so a cut in progress finishes; a cut that starts
  after finds no file, and that rock is `cut_failed`.

## 4. Timeline: the hog times a clip cuts at

The game timeline's rock record gains three fields, written for new runs only:

- `t_hog_s` -- the throwing hog line crossing (`hogtime.crossing`);
- `v_hog_m_s` -- the speed there (`hogtime.speed_at_hog`);
- `t_far_s` -- the far hog line crossing (`fartime.crossing(...).t`).

They are additive: older timelines and charts load unchanged, and nothing is reprocessed.
For runs without them the camera switch falls back to the rock's arrival in the far panel
(`t_enter_s`), a slightly less exact cut.

## 5. API and storage

- **The clip jobs are the record.** A finished `ClipJob` keeps what came of each rock in
  `rocks`: `{rock_name: {"clip", "reason"?, "overlay"?}}`. Nothing is written to the play,
  so saving a play again can never wipe its clips. A play's clip state on a run is read
  from its clip jobs on that run, oldest first (`service/gameclips.state_for`): a queued or
  running job's rocks are `{"clip": false}`, a finished job's are its results, a failed
  job's are `cut_failed`, and a rock once `true` stays `true`.
- `clip` follows the practice convention: `false` on its way, `true` uploaded, `null` none
  coming, with `reason` one of `no_release`, `no_camera` (the timeline has no long-camera
  calibration to cut from), `video_gone`, `cut_failed` (the cut or its upload failed).
  The overlay is the practice per-piece list (~1 KB a rock; over 16 KB it is dropped).
- **Completion** (`POST /api/worker/clips/{id}/complete`) stores the results, kept to the
  rocks the job asked for and the shapes above. A play cleared meanwhile leaves them on
  the job, harmlessly, keyed by its play id.
- **`GET /api/me/plays/{source_id}/doc`** marks each of the player's rocks with its name
  (`rock.rock`) and returns the play's clip state beside the document:
  `{"doc", "fallback", "clips": {"rocks": {...}, "pending": bool}}`.
- **`GET /api/me/plays/{source_id}/clips`** returns that `clips` object alone, cheaply (no
  document is loaded), for the page to poll while `pending`.
- **`GET /api/me/plays/{source_id}/clips/{rock_name}`** returns `{"url"}`, a 1-hour signed
  GCS link, only to the play's owner and only for a rock whose `clip` is `true`; 404
  otherwise. Same signing as `practice_clip`.
- **`GAME_CLIPS_ENABLED`** (API env, default off): off, no jobs are queued, `/doc` carries
  no clip fields and the page shows nothing new.
- **Clearing a play** leaves its clips in the bucket; they are keyed by run and rock and
  cost almost nothing to keep.

## 6. The /shots page

- **Rocks table** (`RockTable` in `frontend/site/Shots.jsx`): the actions cell gets a
  **Clip** button beside Watch when the rock's `clip` is `true`. Tapping it opens the clip
  in a row under that rock, full width on a phone; tapping again, or another rock's Clip,
  closes it.
- **The player** is the practice one: `<video>` muted, inline, with controls; the broom
  ring, thrown line and dashed aim line over it as SVG, switched by the same remembered
  **Lines** toggle (`practice.lines`). `ClipLines`, the signed-link cache and the
  expired-link retry move out of `frontend/site/Practice.jsx` into a shared module
  (`frontend/site/clip.jsx`, with `overlayAt` staying in `core/practice.mjs`) and both
  pages import them.
- **Games table** (`PlayRow`): the Rocks cell adds the clip status -- "clips on their way",
  "14 of 16 clips", or "no clips: video no longer kept".
- **Polling:** while any shown play's clips are `pending`, the page reads those plays'
  `/clips` every 15 s; it stops when none are.

## 7. Edge cases

| Case | What happens |
|---|---|
| Video not cached | Every rock `null`, `video_gone`; games table says so |
| Rock never released (no `t_release_s`) | That rock `null`, `no_release`; no Clip button |
| Side view without a lateral fit | Clip cut, overlay piece has no lines |
| Game reprocessed after the claim | Next /shots read sees a new run and queues a job; if the video was pruned meanwhile, `video_gone` |
| Slot or colour changed | New job for the new rocks; rocks already cut on that run reused |
| Play cleared while its job runs | The results stay on the job; a new play by the same person on that game (same play id) finds them again |
| Two people claim the same rock | Each play has its own job and state; the second job cuts the rock again to the same key (harmless) |
| Worker restart mid-job | Lease expires, job requeued, rocks re-cut to the same keys |
| Claimed while the game's stream is still live | Job waits (run `live`), runs once the run is `ready` and the recording is kept |
| Video on another main worker's cache | Found there: the clips worker reads all three |
| Video pruned while being cut | The cut in progress finishes; a later one is `cut_failed` |
| Clips worker down | Jobs wait in the queue; the page keeps saying "clips on their way" |
| `GAME_CLIPS_ENABLED` off | Nothing queued, nothing shown |

## 8. Testing

Python unit tests:
- `throw_from_rock`: every field in the table, the `t_enter_s` fallback, a missing line or
  broom;
- `SideView` rebuilt from a calibration block matches `analyze._side_calibration`'s input
  (round trip);
- queueing: on a new play, on a changed slot, on a changed run at /doc, never twice for one
  play and run, not with the flag off;
- the clips claim, progress, complete and fail routes leave the run untouched, and the
  claim skips a job whose run is `live`;
- an expired lease requeues a clip job, and fails it after `max_attempts`;
- `WORKER_ROLE=clips` runs the clips loop and nothing else;
- the video lookup across several cache roots: first found, none found, `kept/` ignored;
- the worker clips job with a fake cache: video missing, a rock with no release, a cut that
  raises, a good cut (with `cut` stubbed);
- completion merges into the play; a cleared or re-run play drops the result;
- the clip link route: owner gets a URL, another user and a `null` rock get 404;
- the three new timeline fields are written and older timelines still load.

JS: a core test for clip states (Clip button shown only for `true`; games-table text for
queued, partial, done, video gone).

End to end, before shipping:
- run the clips job against VXU9 (cached locally) for one colour and slot, and watch a
  handful of clips with lines in the local /shots page (`~/curling-work/my-shots-check`
  fake-Firebase setup, 390x844 phone view);
- check the clock on one kept live recording on the worker (section 3).

## 9. Rollout

1. API deploy with `GAME_CLIPS_ENABLED=1`.
2. On the worker box: create `/data/wdd/curling-clips`, rsync, then
   `docker compose -f docker-compose.worker.yml up -d --build`, which adds the `clips`
   service (and recreates the three main workers, so do it with no stream in hand).
3. No reprocess; the new timeline fields arrive with new runs.

Rollback: `GAME_CLIPS_ENABLED=0` and `deploy-api.sh`; `docker compose stop clips`.
