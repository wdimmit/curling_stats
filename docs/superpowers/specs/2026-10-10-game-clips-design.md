# Game clips: a video of each rock you claimed

**Date:** 2026-10-10. **Status:** approved in conversation; this is the written spec.
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

## Assumptions (stated in conversation, not contradicted)

- A game clip is a practice clip: the camera facing the thrower from 3 s before the slide
  until the rock leaves its frame, then the camera behind from the far hog line to rest +
  1.5 s; portrait 600x810 H.264; capped at release + 30 s.
- The overlay is the practice overlay: broom ring, thrown line (solid), aim line (dashed),
  with the remembered Lines toggle.
- Clips are private to whoever made the play, as the play itself is.
- Clips are kept indefinitely (about 8 MB per claimed game).

## Approach

The claim queues a short `clips` job. A worker that has the video in its cache cuts the
player's rocks with the practice cutter, using the calibration and rock times the game's
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

- `JOB_KINDS` gains **`"clips"`**. A clips job has `run_id` = the run the report reads and a
  new `Job.spec` dict carrying everything the worker needs, so it never loads a timeline:
  `{"video_id", "play_id", "calibration": {"left", "right"}, "rocks": [...]}`, where each
  rock is `{"name", "house"}` (the end's) plus the rock's own `t_release_s`, `t_rest_s`,
  `t_enter_s`, `t_hog_s`, `v_hog_m_s`, `t_far_s`, `target_broom` and `line`. The API takes
  `calibration` from the document before `for_player` drops it.
- **A clips job never touches its run.** `claim_job` (both repos) sets the run to
  `processing` today; it must not for `clips`, and the clips job's completion and failure
  paths must leave the run's status, timeline and `ready_at` alone.
- **Who claims it:** the worker's one-shot loop, which today claims only `vod`, claims
  `("clips", "vod")` with clips first when `WORKER_CLIPS=1`. The live lane is unchanged.
  - Consequence: on a league night every worker holding a live stream pauses its one-shot
    loop, so a clips job waits for a free worker -- often until the night's last draw ends.
    League ends are never slowed by clips.
- **When a job is queued** (API, only with `GAME_CLIPS_ENABLED=1`):
  1. `PUT /api/me/plays/{source_id}` saves a new play, or changes its colour or slot;
  2. `GET /api/me/plays/{source_id}/doc` finds `play.clips` missing, or made for a run
     other than the one it is reading (a reprocess since; or a play saved before this
     feature -- the backfill needs no script).
- **Never twice:** at most one queued or running clips job per play and run. A rock this
  play already has a clip for on that run (`clip: true` in its `clips.rocks`) is left out
  of the next job's `rocks`; a job with no rocks left is not queued.
- The play rate limit (`PLAY_HOUR_LIMIT`/`PLAY_DAY_LIMIT`) already bounds the PUTs; the
  one-job-per-run rule bounds the doc reads.

## 3. Cutting, on the worker

New module `src/curling_score/game/clips.py` (the game side; it calls into
`practice/clips.py` and `practice/overlay.py`, which stay as they are apart from any shared
helper that moves):

1. **Find the video in the cache**: `videos/<id>.mp4`, the cached VOD or the kept whole
   live recording. Partial recordings under `kept/` are not used: where they start on the
   stream's clock is not certain. No video: complete the job with every rock
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
6. **Complete** with `{"clips": {rock_name: {"clip": true, "overlay": [...]}, ...}}`.

The job's progress posts keep its lease alive as other jobs do; about 1.5 s a rock, so
~30 s for a four-player game's 16 rocks once the video is cached.

**Clock check before relying on live recordings:** a game charted live has times on the
recording's clock. A kept whole recording starts at segment 0, so its clock should be the
stream's; this is verified on a real kept recording during the build, before live-charted
games are clipped. If it does not hold, those games get `video_gone` until it is fixed.

## 4. Timeline: the hog times a clip cuts at

The game timeline's rock record gains three fields, written for new runs only:

- `t_hog_s` -- the throwing hog line crossing (`hogtime.crossing`);
- `v_hog_m_s` -- the speed there (`hogtime.speed_at_hog`);
- `t_far_s` -- the far hog line crossing (`fartime.crossing(...).t`).

They are additive: older timelines and charts load unchanged, and nothing is reprocessed.
For runs without them the camera switch falls back to the rock's arrival in the far panel
(`t_enter_s`), a slightly less exact cut.

## 5. API and storage

- **`Play.clips`** (new, default None):
  `{"run_id", "job_id", "state": "queued"|"done", "rocks": {rock_name: {"clip", "reason"?,
  "overlay"?}}}`. `clip` follows the practice convention: `false` on its way, `true`
  uploaded, `null` none coming, with `reason` one of `no_release`, `video_gone`,
  `cut_failed`. The overlay is the practice per-piece list (~1 KB a rock).
- **Completion** (`POST /api/worker/jobs/{id}/complete` for a clips job) merges the rocks
  into the play named in `Job.spec`, if it still exists and its `clips.run_id` is still the
  job's run; otherwise the result is dropped (the uploads stay, reusable by rock name).
  A clips job that fails for good marks its rocks `cut_failed`.
- **`GET /api/me/plays/{source_id}/doc`** puts each of the player's rocks' clip state and
  overlay on the rock (`rock.clip`, `rock.overlay`), and a game-level `clips` summary
  (`state`, counts, `reason` when every rock shares one).
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
- **Polling:** while any shown play's clips are `queued`, the page re-reads those plays'
  docs every 15 s; it stops when none are.

## 7. Edge cases

| Case | What happens |
|---|---|
| Video not cached | Every rock `null`, `video_gone`; games table says so |
| Rock never released (no `t_release_s`) | That rock `null`, `no_release`; no Clip button |
| Side view without a lateral fit | Clip cut, overlay piece has no lines |
| Game reprocessed after the claim | Next /shots read sees a new run and queues a job; if the video was pruned meanwhile, `video_gone` |
| Slot or colour changed | New job for the new rocks; rocks already cut on that run reused |
| Play cleared while its job runs | Completion finds no play; result dropped |
| Two people claim the same rock | Each play has its own job and state; the second job cuts the rock again to the same key (harmless) |
| Worker restart mid-job | Lease expires, job requeued, rocks re-cut to the same keys |
| `GAME_CLIPS_ENABLED` off | Nothing queued, nothing shown |

## 8. Testing

Python unit tests:
- `throw_from_rock`: every field in the table, the `t_enter_s` fallback, a missing line or
  broom;
- `SideView` rebuilt from a calibration block matches `analyze._side_calibration`'s input
  (round trip);
- queueing: on a new play, on a changed slot, on a changed run at /doc, never twice for one
  play and run, not with the flag off;
- `claim_job` leaves a clips job's run status alone (both repos);
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
2. Worker rebuild with `WORKER_CLIPS=1` in the compose environment.
3. No reprocess; the new timeline fields arrive with new runs.

Rollback: `GAME_CLIPS_ENABLED=0` and `deploy-api.sh`; drop `WORKER_CLIPS` and `up -d`.
