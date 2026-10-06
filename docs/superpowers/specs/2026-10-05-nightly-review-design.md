# Nightly auto-review: flag suspicious games, keep a week of recordings

**Status:** design approved section by section on 2026-10-05. The user's picks:
- Findings land in the existing flag list, about one flag per game.
- Every live recording is kept for a rolling week, as space allows.
- The checks are game structure, odd single rocks, and end-level coverage against a rolling estimate of normal. Score is out.
- The review runs as an API endpoint called by Cloud Scheduler.
- A game is flagged on one strong finding or two or more weak ones.

## Context

Problems are found today in three ways: a viewer presses ⚑ Flag, the user notices, or a detection audit is asked for (`~/curling-work/detect-report/report.py` and `brsplit.py`, run by hand for 09/28 and 10/01). Each audit found real bugs, including an end cut in two, a calibration that cost a whole end its brooms, and an empty house after rock 16. Each needed someone to ask.

This feature makes that audit nightly. Every game charted or reprocessed in the last day is read from its published timeline, checked, and flagged when something looks wrong. The flags go in the same list as viewer flags, ready to investigate.

Live games had a second gap: `LiveManager.finish` deletes the recording when the stream ends, so by morning a flagged live game has no video. Branch `live-keep` (25d675d, never merged) fixes that. It is part of this design.

The review reads timelines only. It needs no GPU and no video, and it never reprocesses or fixes anything itself.

### Backtest that set the thresholds

The data is the 126 hosted games from 2026-09-27 to 10-05: 105 fours, 21 doubles, 624 ends of 6+ rocks. The probe scripts are in this session's scratchpad. `scripts/review.py` replaces them.

| Check | Games it fires on | Note |
|---|---|---|
| Broom off the sheet | 0 | rare since the 6cf19cf cluster fix |
| Two ends in a row to the same house | 3 | all tail fragments ([10,1], [16,2], [16,4] rocks) |
| Missing rocks not explained by joining late or conceding | 18 | e.g. one end missing all 8 of one colour |
| Unplaced rock | 16 | |
| Fewer than 3 ends | 13 | Kozai Draw 5 ×5 (feed lost), 1-rock fragments, Friday S5 |
| Colours not alternating (fours) | 10 | |
| Last end ≤ 4 rocks in a 3+ end game | 5 | |
| Coverage below the p2 baseline and ≥ 4 rocks short | 6-7 per metric | EUpp S5, oDEb S2, uKWn S5 |
| Split outside 7-22 s | 22 | 17 with a single rock; p99.5 = 20.8 s |
| Hammer sequence broken | 32 | mostly the board's; note only |
| Line misses the broom by > 1 m | 84 | the tangent-extrapolation artifact; note only |

With the chosen rule (one strong finding or two or more weak ones), 38 of 126 games would be flagged, 1-12 per night. Every finding raising a flag would give 51.

Doubles ends have no brooms: 112 of 118 have broom coverage under 50%, against 11 of 556 fours ends. The baseline is therefore kept per format.

## Design

### 1. Check logic: `src/curling_score/review.py`

The module is pure, with no I/O, and is shared by the endpoint and the local runner.

- `Finding{check, strength: "strong"|"weak", end: int|None, rock: int|None, detail: str}`. `detail` is one line, e.g. `"split 23.4 s"`.
- `EndMetrics{number, rocks, broom, split, line, release}`: rocks placed (not missing) and how many of them have each.
- `Baseline` holds per-`(format, metric)` thresholds.
  - `Baseline.from_ends(rows)` takes the threshold as the 2nd percentile of the coverage of ends with 6+ rocks.
  - With fewer than `BASELINE_MIN_ENDS` (100) ends for a format, every threshold for that format is the fixed `COVERAGE_FLOOR` (0.6).
- `review_game(doc, game_index, baseline) -> GameReview{format, ends: [EndMetrics], findings: [Finding], notes: [Finding], raise_flag: bool}`.
- `raise_flag` is true when any finding is strong or at least two are weak.
- Format: `doubles` when any end expects 10 rocks, else `fours`. This is the same test the timeline's `shots_expected` makes.

**Strong checks.** In each, "real" means not `missing`.

| check | rule |
|---|---|
| `same_house` | an end goes to the same `house` as the end before it |
| `end_gap` | an end's `number` is not the previous end's + 1 |
| `missing_rocks` | an end has `missing` rocks, other than a leading run 1..k in the game's first end (joined late) or a trailing run ending at the last rock in the game's last end (conceded) |
| `not_alternating` | fours only: two consecutive real rocks in an end have the same colour |
| `team_over` | one colour has more than `shots_expected // 2` real rocks in an end |
| `short_game` | the game has fewer than 3 ends |
| `tiny_last_end` | the game has 3+ ends and its last end has ≤ 4 real rocks |
| `coverage_<metric>` | an end with 6+ real rocks has coverage < the baseline threshold for its format and metric, **and** at least 4 rocks lack that metric. Metrics are broom (`target_broom`), split (`long_split_s`), line (`line`) and release (`t_release_s`) |
| `broom_off_sheet` | `target_broom` has \|x\| > 2.375 m, y < back line - 0.3 m (-2.129), or y > hog + 1.0 m (7.401) |
| `unplaced` | an end has `unplaced_shots` > 0 |

**Weak checks.**

| check | rule |
|---|---|
| `odd_split` | `long_split_s` outside [7.0, 22.0] s |
| `colour_inferred` | a real rock's `color_inferred` is true |

**Notes.** These are listed in a raised flag but never raise one:
- `board_disagrees`: an end's `score` is not None and differs from `detected_score`. The user left score out of the flag-raising checks.
- `hammer`: the game's `hammer_consistent` is false.
- `broom_miss`: `line.at_broom.miss_m` is over 1.0 m, the known extrapolation artifact. This note is counted, not listed per rock.

All constants sit at the top of the module, each with the backtest figure that set it.

### 2. Endpoint: `POST /api/admin/review`

It uses `require_admin` like the other admin routes. The parameters are:
- `dry_run` (default false): review and return what would be written, writing nothing.
- `flag` (default true): when false, write review records but no flags. This is used to seed the baseline.
- `since` (ISO time; default now - 3 days): the lookback for runs that went ready.
- `source_id`: re-review that one game now, replacing its review record.
- `limit` (default 25).

Each call:
1. **Picks games.** It reads runs whose `ready_at >= since` through a new repo method `runs_ready_since(t)`. The method uses a range on `ready_at` alone, needing only the automatic single-field index, and keeps `status == "ready"` in Python. For each run, it takes the sources whose `current_run_id` is that run (`sources_for_video`). A source counts once there is no `reviews/{source_id}_{run_id}`. Games are taken oldest `ready_at` first, up to `limit`. Live runs are not ready, so they wait until they complete.
2. **Builds the baseline once.** It reads the `reviews` records whose `run_ready_at` lies in the 14 days before the oldest game in this batch, takes their `ends`, and calls `Baseline.from_ends`. The batch never sets its own normal.
3. **Reviews each game in turn.** It loads `run_doc(run)` and calls `review_game(doc, source.game_index, baseline)`, then writes a `Review` record. If `raise_flag` and `flag`, it also writes the flag (step 4).
4. **Writes the flag.**
   - The id is `fa_` + the first 16 hex characters of `sha256(source_id|run_id)`, deterministic so a retry can't double-flag.
   - The flag is written only when no flag with that id exists. A resolved auto-flag is never reopened, even after a restore that lost the `reviews` record.
   - `origin: "auto"`.
   - `where` is built as for a `/g/` flag: `link "g"`, `source_id`, `run_id`, `video_id`, `processing_version` and `title`, with chart fields None.
   - `place` is the first strong finding's (or else the first finding's) `{game_index, end, rock, t_video_s}`. `t_video_s` comes from the rock or the end.
   - `note` is a summary such as `"Auto-review: 3 problems — e4-e5 same house; e2 brooms 3/14 (normal ≥ 50%); e6 r9 split 23.4 s"`, capped at `MAX_FLAG_NOTE`.
   - `findings` is every finding and note as dicts.
   - If the source has an earlier auto-flag from another run, the note ends with `"earlier auto-flag f… (run r…)"`.
5. **Stops on time.** It starts no new game after 40 s (Cloud Run's timeout is 60 s).
6. **Returns** `{reviewed, flagged, errors, pending, would_flag (dry run only)}`.

**Failures.** If a game's timeline fails to load or parse, a `Review` record is still written, with `error` set and no flag. It is not retried by later calls; `source_id=` re-runs it. One game's failure never stops the batch.

### 3. Records and repo

- **`Review`** (`records.py`), collection `reviews`, id `{source_id}_{run_id}`. Fields:
  - `source_id`, `run_id`, `video_id`, `game_index`, `format`, `run_ready_at`, `reviewed_at`, `processing_version`
  - `ends: [EndMetrics as dict]`, `findings: [dict]`, `notes: [dict]`
  - `flag_id: str|None`, `error: str|None`
- **`Flag`** gains `origin: str = "viewer"` and `findings: list = []`. Stored flags without them read as viewer flags with no findings, through `_from_dict`'s defaults. `_flag_json` passes both through.
- **Repo** (Protocol, MemoryRepo, FirestoreRepo):
  - `put_review`, `get_review(id)`, `reviews_ready_between(t0, t1)` (range on `run_ready_at` alone)
  - `reviews_for_source(source_id)` (one equality; finds the earlier auto-flag for step 4's note)
  - `runs_ready_since(t)`, `get_flag(id)` if missing, `put_flag_if_absent(flag) -> bool`
  - `reviews` joins `export_all` / `import_all`.

### 4. Schedule

`deploy/scheduler.sh` gains a second job, `curling-nightly-review`:
- `0 3-6 * * *`, America/Los_Angeles, POST `${PUBLIC_BASE_URL}/api/admin/review`, the same admin bearer header, `--attempt-deadline 120s`.
- That is 2 of the 3 free Cloud Scheduler jobs.
- The latest league ends about 00:00 and its live job completes soon after. The 03:00 call does the night's work, and later calls drain a spiel day's 40 games or find nothing.

The script creates the job only when `REVIEW=1` is set. The poll job keeps working as before.

### 5. `scripts/flags.py`

- `list` marks auto-flags (`auto` beside the status) and takes `--origin auto|viewer|all` (default all), filtered client-side.
- An auto-flag prints each finding on its own line: check, `e/r`, detail, the rock link (`#e=..&s=..`) and the YouTube time when known. Notes print after the findings as one compact line.
- `resolve` is unchanged.

### 6. Local runner: `scripts/review.py`

`python scripts/review.py DIR [--baseline-from DIR2] [--any]` runs `review_game` over every timeline in DIR and prints:
- one line per game that would be flagged, with its findings
- the per-check game counts
- the baseline thresholds

The baseline defaults to DIR itself. `--any` shows the "any finding" count for comparison. It is how thresholds get tuned and backtested, and it replaces `report.py`/`brsplit.py` for audits.

### 7. Recordings: merge `live-keep` and keep a rolling week

**Rebase `live-keep` (25d675d) onto main.** Main has moved 56 commits, touching only `deploy/README.md`, `deploy/docker-compose.worker.yml` and one line of `service/worker.py`. The branch does three things:
- On finish, a whole recording is remuxed into `videos/<id>.mp4` on a background thread (`cache.keep_recording`).
- A job whose document has no games keeps nothing.
- `prune.prune` also deletes least-recently-read media until the disk has `WORKER_MIN_FREE_GB` (40) free.

**Mark and age out recordings.**
- `keep_recording` writes a sidecar `videos/<id>.kept` holding the time it was kept.
- `prune.prune` gains `recording_days` (env `WORKER_RECORDING_DAYS`, default 7). A marked video older than that is deleted with its sidecar and proxy, whatever the budget.
- Within the window, recordings take part in the usual least-recently-read pruning. They go early only when the budget or the free-disk floor demands it. Fresh atimes put them behind old downloads.
- Unmarked media (downloads, the 11 harness videos) never age out.

**Keep partial recordings for investigation.**
- A recording that isn't whole (cap, stall, stop, nonzero exit) is moved to `kept/<id>/` with the same sidecar instead of being deleted. It is never filed as `videos/<id>.mp4`, because a reprocess must not mistake a partial recording for the whole game.
- `prune` treats `kept/` the same way: 7 days, plus the floor.
- `replay_end.py` and `run_analyze` can be pointed at it.
- An empty game document still keeps nothing.
- `live/` cleanup at worker start must not touch `kept/`.

Both worker services pick this up from the image. Each keeps recordings in its own cache.

**Known gaps, not addressed.**
- A reprocess can land on the worker that lacks the recording and download it.
- Until the larger box arrives, the laptop worker's ~56 GB free will trim the window below 7 days.
- A flag cannot say whether the recording still exists.

## Testing

- **`tests/test_review.py`**, on small synthetic timelines:
  - each check fires and stays quiet at its edges
  - the joined-late leading run and the conceded trailing run raise nothing
  - doubles with no brooms raise nothing
  - the coverage rule needs both the percentile and the 4-rock gap
  - the baseline falls back below 100 ends
  - `raise_flag` follows "one strong or two or more weak"
  - notes never raise
- **Backtest fixture:** three or four real timelines pared with `scripts/pare_report_fixture.py` (an end cut in two, a coverage end, a clean game), with their expected findings.
- **Endpoint** (`tests/test_review_api.py`, MemoryRepo and the memory store):
  - reviews each ready game once, and a second call is a no-op
  - `dry_run` writes nothing
  - `flag=false` writes reviews only
  - `limit`/`pending` and the time budget (injected clock)
  - the baseline excludes the batch
  - the deterministic flag id is not rewritten when resolved
  - a broken timeline gives an error record and the batch continues
  - `source_id` re-review
  - the earlier-auto-flag note
  - the flag's `where`/`place`/`origin`/`findings`
- **`flags.py`:** rendering of auto-flags and `--origin`.
- **`live-keep`:** its existing tests, plus the sidecar, the 7-day age-out (injected now), unmarked media never aging out, a partial recording going to `kept/`, and start-up cleanup sparing `kept/`.
- The full suite is OOM-killed on this laptop, so run the affected subsets (`test_review*`, `test_flags*`, `test_cache`, `test_live_*`, `test_ingest_service`, the API flag tests).

## Rollout

The two pieces ship independently.

1. **Recordings.**
   - Rebase, build, and test on a branch.
   - Deploy the worker (build first, swap on a day with no live league and no claimed job; `up -d` recreates both services).
   - After the first live night, read the "recording X ended with the stream (exit N)" lines and check that `videos/*.kept` or `kept/` filled.
2. **Review.**
   1. Merge and deploy the API. The new collection needs no composite index.
   2. Seed: `POST /api/admin/review?since=<14 days ago>&flag=false`, repeated until `pending` is 0.
   3. Dry run: `POST /api/admin/review?dry_run=true` over the last 3 days. Show the user the would-be flags.
   4. Only after the user has seen them, run `REVIEW=1 deploy/scheduler.sh`.

## Out of scope

- Fixing or reprocessing anything automatically.
- Grouping one cause across sheets: Kozai Draw 5's lost feed gives five flags.
- Live-vs-VOD comparison.
- Telling the API which recordings a worker holds.
- Routing a reprocess to the worker that holds the video.
