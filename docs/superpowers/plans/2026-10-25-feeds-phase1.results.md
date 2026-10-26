# Feeds phase 1: results

The gate (Task 7): `analyze` on the same VOD by the code at `a2c7fc9c` (BASE) and by branch `camera-direct`
at `6498f4f4` (HEAD) must give the same timeline, and HEAD must not add a detection-cache entry.

## Stage A: dev box (done)

(The driver was extended for stage B after this ran; the stage A command still works as written, with the phases now
base-then-head per call.)

Machine: i7-12700H (20 threads), 31 GB RAM, RTX A2000 8 GB laptop GPU. One analyze at a time, `nice -n 10`.
Detector `weights/ds17b.pt` (same file for both sides; `weights/` is unchanged between the two commits), imgsz 448,
fp16, all models on (broom4, spin1, release1, side ds13e). `processing_version`
`2026.10.23.1+ds17b-cea0eccf+side-ds13e-dd248ce9+broom-broom4-246ac6f5+line+spin-spin1-b00cc0bd+release-release1-f6876b70`.

Command (BASE is a `git worktree add --detach <scratch>/base a2c7fc9c`; HEAD is this worktree's `src`):

    BASE_SRC=<scratch>/base/src HEAD_SRC=$PWD/src \
    PYTHON=/home/tcuser/src/curling_score/.venv/bin/python WORK=<scratch>/work2 \
    WEIGHTS=$PWD/weights/ds17b.pt COLD_IDS=Qzh8XQrxqww \
    SEED_PROXIES=~/.cache/curling_score/proxies \
      scripts/feeds/ab_identical.sh hOKZoeJNTpM     # then Qzh8XQrxqww, VXU9xwmugRg, uKWnmVG9mA8, one at a time

- No network and no YouTube: `VideoInfo` is built locally (title `Sheet 1`, duration probed from the file) and passed
  as `info=`. The mp4s are hard-linked from `~/.cache/curling_score/videos/` into a scratch cache root; nothing was
  written into `~/.cache/curling_score`.
- Each side was checked to import its own tree (`curling_score.__file__` is printed first in each log and asserted).
- Warm runs share ONE cache root (videos, strip proxies, `detections/`) for both sides, starting with an empty
  `detections/`. BASE runs first and fills it cold; HEAD runs second and must only read. The proxies for hOKZ and
  VXU9 were hard-linked from the existing ones (pinned mtime, so same file identity); Qzh8 and uKWn were built by
  the BASE run and reused by HEAD.
- The cold run (Qzh8XQrxqww only): each side gets its own empty detection cache (root with the video and proxy
  linked in), so every span is decoded by that side's panel readers and nothing is replayed. `analyze` has no
  per-call cache-off flag (`use_cache` is a `detect_span` argument), so an empty per-side cache is the equivalent.

| video | ends (games) | BASE wall | HEAD wall | timeline | detection-cache entries |
|---|---|---|---|---|---|
| hOKZoeJNTpM | 8 (4+4) | 1073 s | 766 s | same | after base 16, after head 16, new from head 0 |
| Qzh8XQrxqww | 4 (1) | 986 s | 322 s | same | after base 24 (cumulative with hOKZ), after head 24, new 0 |
| VXU9xwmugRg | 13 (7+6) | 1625 s | 1156 s | same | 50 / 50, new 0 |
| uKWnmVG9mA8 | 8 (6+2) | 1328 s | 639 s | same | 66 / 66, new 0 |
| Qzh8XQrxqww, cold (no replay) | 4 (1) | 491 s | 493 s | same | each side's own cache: 8 entries, the same 8 file names |

4/4 warm and 1/1 cold `same`; HEAD wrote zero new detection-cache entries (the set of file names is unchanged,
not just the count), so every key HEAD computes is one BASE wrote. The cold Qzh8 timeline is also identical to its
warm one on each side. Wall times: BASE includes the cold detection (and, for Qzh8 and uKWn, the strip proxy build);
HEAD is mostly warm, so the two are not a speed comparison. Cold-vs-cold is: 491 s vs 493 s.

### Two things the gate needed that the brief did not say

1. **`timeline.json` is not byte-identical as written: `source.analysed_at` is the wall clock** (`timeline.py:525`).
   The driver compares with that one line left out; everything else is byte-for-byte.
2. **BASE is not deterministic on its own: `PYTHONHASHSEED` moves game boundaries.** The first hOKZ attempt came out
   `DIFF`: game 2's start 3985.0 (BASE) vs 3980.0 (HEAD), end 2's start 4950.0 vs 4985.0, and two new cache entries
   from HEAD (the spans it detected for those two ends had other start times). The diff of the timelines was exactly
   those three lines plus `analysed_at`. It is not the change: a BASE run repeated against a warm cache reproduced
   BASE's 3985; the profile samples were byte-identical in every run (same sha); and
   `segment.segment_games` on the one saved profile gives `[3980, 4985]` under 3 of 8 `PYTHONHASHSEED` values and
   `[3985, 4950]` under 5 of 8, on the BASE tree. The cause is `segment._smooth`:
   `max(set(window), key=window.count)` over the strings `"top"`/`"bottom"` breaks a tie by set order, which
   string hashing randomises per process (the "`_smooth` tie-break = segmentation jitter" already noted for
   0436eed). `segment.py` is untouched by this branch. The driver now pins `PYTHONHASHSEED=0` for both sides
   (`HASHSEED`), after which all of the above is `same`. The pipeline itself was not changed; the tie-break is
   worth fixing separately (an explicit order, say), since hosted reprocesses of the same VOD can differ the same way.

Scratch (kept): the first attempt's timelines and logs, the profile-only check, and the final run's timelines and
logs under `.../scratchpad/feeds-ab/`. Strip proxies, cache roots and the BASE worktree were removed.

## Stage B: Ryzen (done, 2026-10-26)

Machine: Ryzen 5950X (32 threads), 62 GB, 2x RTX 4070 12 GB. The workers were idle (load 0.0, no jobs, no live
streams). Everything ran in throwaway containers from `curling-worker:local` (image d6cbacf63977), `docker run --rm -i
--gpus device=N --user 1000:1000 --entrypoint env`, at `nice -n 10`; BASE (`git archive a2c7fc9c src`) and HEAD (`git
archive` of this branch's HEAD, whose `src` equals `6498f4f4`'s) mounted read-only and put first on `PYTHONPATH`
(each log's first line shows the tree it imported and the driver asserts it); `/data/wdd/curling-cache` mounted
read-only. Weights are the image's own (`CURLING_SCORE_WEIGHTS=/opt/curling/weights/ds17b.pt`, side ds13e, broom4,
spin1, release1: the same `processing_version` as stage A). Scratch: `/data/wdd/scratch/camera-direct/ab/`. The
videos and the workers' strip proxies were linked into scratch roots (hard link, else symlink), never copied; the
worker's own detection cache was not used: each run started with an empty `detections/`.

Command (`launch.sh` sets the mounts and `RUNNER`; the driver is `scripts/feeds/ab_identical.sh`, run from a copy):

    COLD_IDS='PGltvgB2yIM n51rf_nFlhM' ./launch.sh w-seed0 0 3 <the 11 ids>        # HASHSEED=0, JOBS=3
    ./launch.sh w-seed1 1 2 hOKZoeJNTpM PGltvgB2yIM                                # HASHSEED=1, JOBS=2
    LIVE_VIDEO=/data/wdd/curling-cache/videos/nNa2sobjvFw.mp4 LIVE_SPEED=100000 LIVE_RUNS='base head base2' \
      ./launch.sh w-live2 0 1

### 1. All 11 harness VODs, seed 0: 11/11 same, 0 new cache entries

One shared detection cache for both sides: BASE ran all 11 cold (3 at a time), then HEAD all 11 against what BASE
wrote. After base 216 entries, after head 216, new from head 0 (the file-name sets are equal).

| video | ends (games) | BASE wall | HEAD wall | timeline |
|---|---|---|---|---|
| 4RrNWSeNnMU | 11 (5+6) | 1523 s | 878 s | same |
| AEqLTgM25Tc | 13 (6+7) | 1611 s | 879 s | same |
| entTaSBFK3A | 13 (7+6) | 1668 s | 700 s | same (see the OOM note) |
| -f3R1D3bQ0E | 12 (6+6) | 1541 s | 813 s | same |
| hOKZoeJNTpM | 8 (4+4) | 1080 s | 551 s | same |
| jgZ9wlxGYHM | 9 (9) | 1227 s | 692 s | same |
| n51rf_nFlhM | 6 (6) | 787 s | 398 s | same |
| OcyhldUbu8A | 8 (8) | 927 s | 565 s | same |
| _OGoqF5W9-I | 7 (7) | 557 s | 484 s | same (BASE rerun, see the OOM note) |
| PGltvgB2yIM | 8 (8) | 887 s | 544 s | same |
| VXU9xwmugRg | 13 (7+6) | 1151 s | 547 s | same |

Wall times are with 3 to 5 analyses sharing the box and BASE cold, HEAD warm: not a speed comparison.

### 2. Cold subset, empty detection cache on each side: 2/2 same

| video | BASE wall | HEAD wall | timeline | detection entries |
|---|---|---|---|---|
| PGltvgB2yIM | 554 s | 552 s | same | 16 each side, same names |
| n51rf_nFlhM | 448 s | 450 s | same | 12 each side, same names |

### 3. A second hash seed, `PYTHONHASHSEED=1`: 2/2 same

hOKZoeJNTpM (base 1290 s, head 819 s) and PGltvgB2yIM (1201 s, 846 s): `same`, one shared cache of 32 entries after base
and after head, 0 new. The two seeds are not compared to each other, as asked.

### 4. `live-replay`, base against head: NOT repeatable, so not a byte gate

`nNa2sobjvFw.mp4` (tonight's, 8995 s), both sides at seed 0, `--speed 30` and again at `--speed 100000` (the whole file
written before the session starts). Both ended with the same 8 ends and 128 shots, but the timelines differ, and so
does a BASE run against a second BASE run:

- base vs head: 165 structural differences (a path with one more or fewer point), 24,339 numeric ones (largest 179,
  a path point), 5,353 differing leaves once the line, spin, release, delivery and calibration blocks are left out.
- base vs base2 (same code, same seed, same input): 57 structural, 31,391 numeric, 8,551 coarse leaves, and even a
  detected score (`games[0].detected.score_by_end[2].yellow` 4 vs 3). head vs base2 is in between (124, 29,993, 7,761).
- The first difference in every pair is `calibration.top.px_per_m` (79.015 vs 79.003 vs 79.023, residuals, hog-line
  and sideline polynomial coefficients), and everything downstream (every `line`, `curl`, `spin`) follows from it.

Reading: the live session recalibrates from `F.spread_keyframes(recording, head)` every `CALIB_EVERY_S` until the
recording passes `CALIB_GIVE_UP_S`, so the calibration it ends with depends on how much of the file existed at each
look, which depends on ffmpeg's and the session's relative timing (the first publishes say `recorded to 6739.8 s` in
one run and `6789.0 s` in the other). Even the nominal `--speed 100000` does not remove that, because ffmpeg writes in
chunks while the session polls. It is a property of the live path on the base tree itself, not of this change: base
and head differ from each other no more than base differs from itself (head is, on the coarse count, nearer to base
than a second base run is). The pipeline was not changed. To gate the live path byte-for-byte, the replay needs a
fixed calibration point (calibrate once, at a stated head, then publish); until then it is a smoke test: same games,
ends and shot counts, same calibration to within a pixel fraction. `analyze`, which is what phase 1 changed for the
workers' VOD path, is the byte gate and passes.

### Notes on the run

- **GPU out-of-memory under load, twice, never a difference.** The two 4070s carry about 5 GB each for the idle deploy
  workers; five or six concurrent analyses plus a live replay ran GPU 0 out of memory. (a) `_OGoqF5W9-I` BASE died at
  end 1 (rc=1): rerun alone with `RESUME=1` (`log.failed` kept). (b) `entTaSBFK3A` HEAD finished with rc=0 but logged
  `spin pass failed on shot N; it keeps no spin` after a CUDA OOM in `spintime.py:315`, so those shots had
  `"spin": null` against BASE's values: a DIFF caused by the GPU, not by the code. Rerun alone, it is `same`. Worth
  knowing: the spin pass swallows a CUDA OOM and publishes without spin. Every other log was searched for `out of
  memory` and `spin pass failed`: none.
- The driver killed mid-run (`kill -9` of its parent after the OOM) and resumed with `RESUME=1`; BASE results were
  kept, `det.after-base` was re-snapshotted from the cache, which already held all BASE entries.
- The first live attempt (`--speed 30`) ran at the same time as the seed runs and gave the first live difference
  above; the control (`base2`) was only added after that.

### Driver changes for stage B (`scripts/feeds/ab_identical.sh`)

Phases (all BASE, snapshot, all HEAD, snapshot) with `JOBS`/`GPUS` for several ids at once; `RUNNER` for a container
(`@GPU@` placeholder); weights from the interpreter's `$CURLING_SCORE_WEIGHTS` when `WEIGHTS` is unset; cold ids run
both sides at once with `detections/` removed after; `LIVE_VIDEO`/`LIVE_SPEED`/`LIVE_RUNS` for `live-replay` with a
`base2` control; `RESUME=1`; before comparing, both timelines must exist, be non-empty and hold exactly one
`analysed_at` line (live also masks `wall_s`).

Scratch kept on the Ryzen: timelines, logs, `wall_s`, detection-entry lists and the live publishes under
`/data/wdd/scratch/camera-direct/ab/w-*`. Linked videos and proxies, cache roots and the live recordings were removed.

### practice-replay

Not run: practice recordings are never kept, so none exists to replay. The practice path's reader (`windows` with
`Cut` specs, `one_pass`) is pinned by `test_windows_cuts_one_decode`; phase 2's gate replays practice both ways from
raw files.
