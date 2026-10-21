# False-positive frames from the workers, for labelling

**Status:** for the user's approval, 2026-10-21. Decided with the user:
- Use the pipeline's own rejection checks to find frames where the model was wrong, on both the overhead and the long cameras.
- Keep both kinds of frame. A box with nothing under it is a negative example. A real rock with a bad box or the wrong colour is a frame to relabel.
- Frames pile up on the worker's own disk until a labelling round harvests them. Nothing is uploaded.
- Which checks to harvest comes from the user's tags on 194 spike frames (below).

## Why

Every training round so far needed hand-mined frames. ds18 (`scripts/ds18/mine.py`), ds17 and the broom shoe negatives were each an offline script, run on the worker box over a week of cached videos. Meanwhile the hosted pipeline throws boxes away as bogus on every game and records only counts (`deliveries_dropped` and friends). Those rejections are the evidence of a model mistake, and they're lost.

This feature has each worker save a few whole frames per game where a trustworthy check fired, with a record of what fired and where. A labelling round then starts from a ready pile instead of a mining run.

## What the spike showed

Branch `fp-spike` (not for merging) logged every rejection while 10/20's nine recordings (14 games, 97 ends) were replayed through `live-replay`. The user then tagged frames on a review page, in two batches:
- **Nothing there:** the box had nothing under it.
- **Bad box:** a real rock, but the box or colour was wrong.
- **Real:** a real rock, box fine.

The tags are in `datasets/fp-spike/review{1,2}/` on that branch. In the table, **model wrong** = (nothing + bad box) / judged, with unsure frames left out.

| Check (spike name) | Where it fires | Judged | Nothing / bad box / real | Model wrong | Harvest? |
|---|---|---|---|---|---|
| Long camera, box width ≥2× or ≤0.4× a stone's (`side.size_*_far`) | `sidemodel.propose`, `linetime.path_points`, `farfollow.follow`, `sidereleases.stone_points` | 22 | 16 / 6 / 0 | 100% | **yes** |
| Long camera, box width slightly off (`side.size_*`) | same four places | 42 | 8 / 34 / 0 | 100% | **yes** |
| Long camera, a path start never seen again within 1 s (`side.lone`) | `linetime.chain` | 14 | 10 / 3 / 1 | 93% | **yes** |
| Overhead, box in a quiet spell between two rocks, neither on a settled stone nor in flight (`ovh.quiet`) | new, end-level, using `train.dataset.settled_only` | 27 | 22 / 1 / 4 | 85% | **yes** |
| Overhead, track that moved further sideways than down the sheet (`ovh.lateral`) | `delivery.find_deliveries` | 8 | 6 / 0 / 2 | 75% | **yes** |
| Overhead, track whose net travel is < 70% of its path (`ovh.wander`) | `delivery.find_deliveries` | 18 | 13 / 0 / 5 | 72% | **yes** |
| Overhead, candidate the end fit dropped within 10 s of a kept rock (`ovh.fit_close`) | after `fit.fit_end` | 25 | 10 / 1 / 14 | 44% | no |
| Long camera, one stone read as both colours (`side.two_colour`) | `linetime._follow` | 8 | 0 / 3 / 5 | 38% | no |
| Overhead, track that vanished with no house change (`ovh.phantom`) | `delivery.find_deliveries` | 15 | 3 / 0 / 12 | 20% | no |
| Overhead, fit drop for any other reason (`ovh.fit_other`) | after `fit.fit_end` | 5 | 1 / 0 / 4 | 20% | no |
| Overhead, sweepers claiming a house change a closer track took (`ovh.follower`) | `delivery._one_per_change` | 2 | 2 / 0 / 0 | — | not yet: too few |
| Overhead, short-run rest the camera didn't confirm (`ovh.short_rest`) | `farfollow.place` | 2 | 0 / 0 / 2 | — | no |
| Overhead, feet settling on a parked stone (`ovh.feet`) | `delivery._rests_on_parked` | 0 (1 unsure) | — | — | not yet: too few |
| Throwing panel, release colour misread (`ovh.colour_release`) | `release.unaccounted` | 0 (never fired) | — | — | not yet: too few |

**The rule used for "Harvest?":** a check is harvested when at least 5 of its frames have been judged and the model was wrong in 60% or more of them. Every harvested frame is reviewed before training, so a wrong pick costs a few seconds. A check that fires too rarely to judge stays off. A later tagging round can turn it on (*my call*).

Other things the spike showed:
- None of the overhead checks ever turned up a bad box. They find either nothing at all or a real stone.
- The long camera's slightly-off widths are mostly real stones: a box on the handle only, part of a stone, or two touching stones in one box.
- The far-off widths are mostly people: boxes over groups of players, or tiny ones on a leg or broom.
- `side.lone` was mostly heads and backs of people in the foreground.

## Goals

1. On every hosted game, VOD and live, record each time a harvested check fires. Those are:
   - the long-camera width check in all four passes;
   - `side.lone`;
   - `ovh.quiet`, `ovh.lateral`, `ovh.wander`.
2. Save a few whole frames per game for those firings, at most 2 per check and 12 per game, spaced at least 5 s apart within a check. Each frame comes with a record of what to look at.
3. Make the pile easy to see: what has accumulated, per check and per week, across both workers.
4. Never change what a game's timeline says, and never fail or noticeably slow a job.

## Non-goals

- **Building the labelling set.** The first harvest round will build it. Crops, pre-labels and the review tool depend on which model is being trained next (overhead stone model or ds13 side model). Goal 3 is a listing, not a set builder. *My call.*
- **Changing what the pipeline accepts.** The spike found two long-camera passes with no width check at all: `farfollow.follow` and `sidereleases.stone_points`. The pipeline uses boxes 2× too wide there. That's a separate fix. Here those two passes only record.
- **The checks marked "no" or "not yet" above.** Leaving them out also keeps every hook off the far (throwing) panel and out of `linetime._follow`'s candidate filter.
- **Practice sessions.** They run a different pipeline (`practice/pipeline.py`).
- **Any upload, site UI or Firestore record.**
- **Deleting frames.** At about 4 MB a game, a year is about 20 GB. A size cap (below) is the only bound.

## Anti-goals

These would make it a failure even if it worked:
- A live end gets measurably slower. The live lane runs at about 80% of real time on a five-sheet night.
- A timeline differs with the feature on.
- An exception from this code reaches a job.
- The worker disk fills.
- A pile that is mostly real rocks with good boxes, which is what the dropped checks would add.

## Design

### Recording the firings

Each harvested check calls a small recorder at its rejection site with what it already has:
- **Overhead** (`ovh.lateral`, `ovh.wander`, house panel only): colour and the track's `(t, x, y)` samples. The recorder joins those back to the house panel's `Detection` for `x_px`, `y_px`, `area_px` and confidence, by exact `(t, colour, x, y)`, the way `fpnotes.flush` does on the spike.
- **`ovh.quiet`:** end-level, computed in `build_one_end` from `seq` and `kept`.
  - Use only the spans between two kept rocks: `t_rest + 6 s` to the next `t_enter - 6 s`, at least 12 s long (`train.dataset.QUIET_MARGIN_S`, `MIN_QUIET_S`).
  - Not the span before rock 1 or after the last rock. Stones are moved by hand there.
  - Skip a span where `settled_only` can establish nothing settled. It returns `[]`, and every box would wrongly look stray.
  - The stray boxes are grouped into tracks with `delivery._build_tracks`, one firing per track.
- **Long camera:** the time, the side view's rect, the box, the confidence, and the ratio to the expected stone width.
  - `sidemodel.propose` uses `WIDTH_TOL` (0.5–2.0) against `view.stone_width_at(bottom, 52.0)`.
  - `linetime.path_points` uses `PATH_WIDTH` (0.65–1.5) against `0.291 m × lateral_px_per_m(row)`.
  - `farfollow.follow` and `sidereleases.stone_points` apply no gate, so they record against `WIDTH_TOL`.
  - `side.lone` records the start that `linetime.chain` skips: the path's first point has no second within `PATH_START_GAP_S`, or no second at all.

At the end of `build_one_end` the recorder stamps the end's context on what it collected:
- video id, game index, end number, house;
- the panel rects and side-view names;
- pipeline version and model ids.

It then hands the end's firings on. Outside the worker (CLI, tests, scripts) the recorder is off and costs nothing. It never raises: every entry point catches and logs.

The overhead `Detection` has no box width or height, only `area_px`. The record stores the centre and a square of side √area, which suits a stone seen from above. Long-camera band boxes (`detect_band(s)`) carry only the bottom row and width, so the record stores those, and the box top is estimated at half the width. `propose` has the whole box and stores it. None of this changes `Detection` or the detection cache format.

### Choosing and saving frames

Frames are chosen and cut **per game, after the game's ends are built**, never inside an end. A live end's stage budget is printed by `StageClock`, and the frame work must not appear in it.
- **VOD:** after `build_games`, while the original video is certainly on disk.
- **Live:** once a game is closed, or at the latest in `LiveManager.finish` before `live/<vid>` is deleted. The recording is on the archived video's clock (`cache.keep_recording`), so a frame time in the record is the same time in the kept `videos/<id>.mp4`.

Choosing:
- At most 2 per check and 12 per game.
- At least 5 s apart within a check.
- Spread across ends.
- Within those limits, prefer long-camera width checks with the ratio furthest from 1, and longer overhead tracks.

Saving:
- Read the frame at the record's time from the file the pipeline read: `ctx.path`, not the strip proxy. Use `ingest.frames.window`, which knows the MPEG-TS seek lead.
- Write the whole frame as JPEG at quality 92 (about 300–400 KB), with no box drawn.
- Overhead box coordinates are converted to whole-frame pixels with the panel rect, side ones with the view rect, so the record stands on its own.

**Layout**, under each worker's cache root (`/data/wdd/curling-cache` and `/data/wdd/curling-cache-2`; `/data/cache` inside the container):

```
harvest/fp/<video_id>/<stem>.jpg
harvest/fp/<video_id>/frames.jsonl     one row per saved frame
```

- **Stem:** follows the existing convention, `f"{vid}_{where}_{t:09.2f}".replace(".", "_")` plus the check. `where` is the panel's house or the side camera's name.
- **A row holds:**
  - stem, check, camera kind (`overhead` or `side`), panel or camera name, t;
  - box in whole-frame pixels, plus whether its top is estimated;
  - colour, confidence, ratio (width checks), track length (overhead);
  - game, end, house, rects;
  - pipeline version, model ids, the run or job id, and the time saved.
- **The pruner** (`ingest/prune.py`) deletes only inside `videos/`, `proxies/` and `kept/`, so `harvest/` is never pruned. `live/` is wiped at worker start and is not a safe place.
- **Permissions:** containers write as root. Make the files world-readable so the `curling` user can read them on the host.

### When a game is processed again (*my call*)

A game is processed more than once routinely:
- a reprocess after a pipeline change;
- a live game reprocessed later from its kept recording, which reads the strip proxy and gets different detections;
- the job landing on the other worker.

**The rule:** a rerun with the same model ids adds no frames. A rerun with a different overhead or side model collects that model's mistakes too.
- **On one worker:** before choosing, the worker reads that video's `frames.jsonl`. If any row carries the current model ids, it saves nothing for that video.
- **Across workers:** neither worker mounts the other's cache, so both may save a pile for the same video and model. The listing script keeps one pile per (video, model ids), the earliest saved, and reports the rest as duplicates. That avoids a cross-mount.

### Switch and limits

- **Switch:** the feature is on when `WORKER_FP_HARVEST=1`, like `WORKER_LIVE`, set in `deploy/docker-compose.worker.yml` for both workers.
- **Size cap:** `WORKER_FP_HARVEST_GB` (default 20). The worker stops saving new frames once `harvest/` is over it and logs it once. It never deletes.
- **Clips worker:** it shares the image but never builds ends, so it does nothing here.

### Seeing the pile

A script, `scripts/fp_harvest.py list [--since DATE] [--caches DIR...]`, reads every `frames.jsonl` across the given cache roots. It prints:
- counts per check, per week, per league and per camera;
- the disk used;
- duplicates across workers, per the rule above.

It needs no GPU, and the labelling round's set builder starts from its output.

## Constraints

- **Pipeline version:** timelines are unchanged, so no `PIPELINE_VERSION` bump. The worker image changes, so both workers are redeployed (`deploying-curling-chart`: clean worktree, checksum rsync, `compose build`, `up -d` only when no live stream or job is running).
- **Shared checkout:** another session works in the same checkout. Build on a branch in a worktree, and commit only this feature's files.
- **Test suite:** running it whole OOMs on the dev box. Run the affected test files.
- **Not from the spike:** the hooks are new code on a new branch, not a cherry-pick of `fp-spike`. The spike's module records every rejection and is written for one replay at a time.
- **Cost of the spike:** the spike's `side.size_path` fired about 37,000 times in 76 ends, at every frame of every off-width box. Record cheaply: no JSON or I/O at the hook, and thin out repeats within a few frames before anything is kept.

## Left to the builder

- Module and function names, and how the recorder is passed or scoped. The live lane runs several sessions in one process, so a recorder can't assume one game at a time.
- Exactly where the live path closes a game and cuts its frames, inside the anti-goal on live end time.
- The selection details within the stated limits.
- The row fields beyond those listed.
- Tests. At least:
  - a timeline is byte-identical with the feature on and off over a replayed end;
  - a raising recorder doesn't fail the end;
  - caps, spacing and the same-model rerun rule hold;
  - the size cap stops saving.

## Calls I made

These weren't asked of the user; they can be changed:
- The caps: 2 per check, 12 per game, 5 s apart, 20 GB.
- The harvest rule: at least 5 judged frames, at least 60% model wrong. It puts `ovh.fit_close` (44%) out and keeps `ovh.lateral` (75% of 8) in.
- A same-model rerun adds nothing; a new model's mistakes are collected.
- The labelling-set builder waits for the first harvest round.
- Frames are cut after the game, not as each end is built.
