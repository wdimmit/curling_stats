# Feeds phase 1: results

The gate (Task 7): `analyze` on the same VOD by the code at `a2c7fc9c` (BASE) and by branch `camera-direct`
at `6498f4f4` (HEAD) must give the same timeline, and HEAD must not add a detection-cache entry.

## Stage A: dev box (done)

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

## Stage B (Ryzen): pending

The 11 harness videos in the worker cache with warm detection caches (a copy for each side), plus one `live-replay`
of a kept live recording. The driver takes the same parameters (`BASE_SRC`, `HEAD_SRC`, `PYTHON`, `WORK`, `VIDEOS`,
`WEIGHTS`) and an id list; `live-replay` and `practice-replay` are not yet in it. Not run: the Ryzen is busy with a
live league.
