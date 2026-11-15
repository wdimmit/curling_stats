# Practice: the card about 5-6 s after the stop

Status: design, agreed in conversation on 2026-11-07. Branch `camera-direct`.

## Why

A player on a practice sheet wants the card for their throw while they are still by the house.

- **Today.** The card publishes ~15 s after the stone stops, plus up to 3 s for the page's poll.
  - Live on 11/07: p50 15.5 s, p90 18.7 s, over 30 arrivals.
- **Five sheets at once.** Practice now allows five sheets (`PRACTICE_MAX_CAMERA_WATCHES` 5). The sheets split 3/2 across
  the two workers. Each watch keeps its worker busy 0.27 (live) to 0.52 (replay) of the time.
  - Three on one worker may not keep up. Cheaper reads are part of the goal, not only a faster card.

Where today's time goes: 11/05 S3 camera files, camera t 2000-2600, replayed through the real `PracticeWatch`
(`scratch/camera-direct/latsplit/latsplit.py` on the Ryzen). Replay p50 12.9 s.

| Part | Time |
|---|---|
| Enrich (the long-camera passes, `practice/enrich.enrich`) | ~7.8 s (6.2-9.5) |
| `HEAD_MARGIN_S` plus the step cadence | 2.0 s |
| Footage after the rest, before the arrival is confirmed | 0.1-3.9 s |
| The detect step | ~0.8 s (0.40 s of compute per second of footage) |

Enrich runs only after the stop, yet nearly all of it reads footage recorded while the stone was still travelling.

## Goals

1. **Measure early (#2).** The long-camera work that does not depend on where the stone stops runs while it travels.
   At the stop only a little is left, so the whole card publishes about 5-6 s after the stop instead of ~13-15 s.
2. **Cheaper reads (#3).** Stop decoding 10 s of footage ahead of every read of a camera file. This lowers each watch's
   share of its worker.
3. **The same card.** These things stay as they are today:
   - which throws are found;
   - rest positions, colours and the house contents;
   - the line, all of it, including the stretch to the stop;
   - clips.

## Non-goals (offered on 11/07, not chosen)

- Publishing in two parts (house first, long-camera figures after).
- Shortening `HEAD_MARGIN_S`.
- A faster page poll, or a push (`Practice.jsx` `POLL_MS` stays 3000).
- An early answer for throws that never arrive. They still wait `releases.NO_ARRIVAL_S` (60 s).
- GPU decode (NVDEC), and any change to league lanes or YouTube reads beyond what #3 says below.
- A persistent decoder that stays open on the growing house files between watch steps (the user, 2026-11-07: "leave
  it alone for now"). It would carry the 10 fps sample grid across steps, which moves release and stop times and
  so, slightly, rest positions and lines. If five sheets do not keep up after this work, it comes back as its own
  change, with its own allowances.

## Anti-goals: a failure even if it is fast

- A card that differs from today's. A different line, a moved time, or a figure from the wrong throw counts as a
  failure.
- One sheet's card held up behind another sheet's early work on the same worker.
- A throw lost or published twice because the early work went wrong.

## Decided by the user (2026-11-07)

- **One stone moving at a time.** "There will never be two stones moving at once." The release seen leaving the
  hack is the stone that will stop. The early work needs no handling of ambiguous pairings.
- **The line is measured to the stop, at the stop.** "Let's spend that extra second on #2's second bullet." The
  line's last stretch to the stop is read once the stop is known, so the line is identical to today's. This costs
  about a second.
- **#3 is the shorter seek lead only.** The persistent decoder is left out (see Non-goals), so every card stays
  exactly the same. The user had accepted time shifts of up to ~0.1 s and a missed frame at a read's start, but with
  the decoder left out neither should happen: the lead does not move the grid, and a late start is re-read.
- **Fast enough:** about 5-6 s from the stop to the card. The user agreed this target ("This looks good").

## How it works

### #2: measuring early

**When a throw qualifies.** A throw qualifies for early work when its overhead release is known (`ReleaseBook`) and
that release's track crosses the throwing end's tee.

- The tee crossing anchors the broom window (`thinking.tee_crossing` uses the release track, and otherwise the
  arrival).
- Any other throw is measured at the stop, exactly as today.

**The early passes.** Each runs once its footage is recorded, in the same order and with the same arguments as today
(`enrich.enrich`):

| Pass | Needs footage up to roughly |
|---|---|
| hog (`hogtime`) | release + `longview.WINDOW_S` |
| side release (`sidereleases`, window from the hog crossing) | — |
| `releasetime.follow_releases` | — |
| broom (`broomtime`, the second before the tee crossing; `all_held`) | — |
| line, the delivery part: hog track, fit, extension if short, `thrown_at` among held brooms, `read_delivery`, `delivery_path` | release + ~9 s |
| line, the destination camera's path (`find_path` over `path_window`) | read up to "now" as the footage arrives, only up to the stop (see below) |
| release point (`releasetime`, from the lane keeper's kept strip and the line's delivery) | — |
| spin (`spintime`, hog crossing + 0.5..10.5 s, +5 s if the rock is still in view) | hog crossing + 10.5-15.5 s |

**At the stop.** When the arrival is confirmed, the stop work is:

1. `build_shot` (the house before entry and after rest).
2. The tee pass.
3. The path's last stretch, from where the early read got to through `path_until(shot)` + 1 s.
4. The parts of the line that need the rest (`path_until`, `_rest`, `measure`).
5. The aim lines.
6. The far hog crossing.
7. `throw_record`, then publish.

Together these should take about a second.

**The same answer as today.** For every qualifying throw, each figure must be the one today's `enrich` would give on
the same footage.

- Most of today's `shot.line` is assembled by `measure()`, which needs the rest. The release-point read
  (`time_releases`) reads `line.delivery`, and the aim lines read `line.release`.
- The builder reshapes these so the expensive reads happen early, and the assembly that needs the rest happens at the
  stop. How is the builder's call. The constraint: an `enrich` that runs everything at the stop (today's path) still
  exists and gives today's results. The early path must give the same results.

**Fallbacks.** Each of these is measured at the stop, today's way:

| Case | What happens |
|---|---|
| A throw that does not qualify | measured at the stop, today's way |
| A pass whose result depends on the arrival because the early read came up empty (for example, `sidereleases._window` falls back to `delivery.t_enter` when the hog pass found nothing) | that pass re-runs at the stop with the arrival |
| The arrival paired at the stop is not the release the early work used (a cheap safety net; the user says it should never happen) | the early results are dropped and the throw takes today's time |
| An early pass whose footage is not yet recorded at the stop (likely spin on a fast hit: my guess) | that pass runs at the stop |

**Throws that never arrive.**
- They still publish after `NO_ARRIVAL_S`, with today's figures.
- Today such a throw gets no lane keeper and no release-point read. If the early work did those, their results are
  dropped so the card matches today's.
- The early path read, which today runs to `t_hog + 24 s` when there is no rest, can be reused.

**Sharing the lane.**
- The lane runs one watch step at a time per worker. Today a step that finds a throw runs ~7.8 s of enrich, and every
  other sheet on that worker waits behind it.
- Early work is the lane's lowest-priority work (my call, from the anti-goal). It runs only when no watch on that
  worker has a detect step or stop work due. It is not tied to a watch's own detect steps, so it also runs while that
  watch is waiting for footage.
- It is cut into pieces of about one pass each, so a detect or a stop that comes due waits at most one piece.
- Early work that has not run by the stop runs at the stop, as in the fallbacks above. A busy worker therefore loses
  speed but never correctness.
- This moves the scheduling into `live/lane.py`. The watch says what early work it has; the lane decides when it runs.

### #3: cheaper reads

**The seek lead follows the file's keyframes.**
   - `frames.TS_SEEK_LEAD_S` (10 s) was set for YouTube's 5 s keyframes. MPEG-TS has no index, so a seek lands on the
     keyframe after the target.
   - The cameras key every 2 s. For the camera files (`feeds/cameras`), the lead becomes about one keyframe interval
     plus a margin (my call: 3 s).
   - A read whose first decoded frame comes after its start re-reads from further back. A short lead can then cost a
     second read, but never a missed start.
   - The lead does not move the sample grid. `_windows_of` anchors the grid on the read's own start, not on where the
     seek landed, and decoding from any keyframe gives the same pictures. So this change alone should give identical
     output, and the gate holds it to that.
   - YouTube recordings keep 10 s.
   - This applies to every read of a camera file: practice, and league games on a `cameras` playlist. My call: one
     rule per file type.
## Checking it (the gate)

All heavy runs are on the Ryzen, in throwaway containers, and never while a practice session is live or after 17:00
PST on a league day.

1. **A/B replay.**
   - Run the real watch over kept camera files on a simulated head, as `latsplit.py` does, both ways: today's
     camera-direct code, and the new code.
   - Footage: `league-1105` S3 and S4, and `srt-s3-1102`. My call: at least 60 throws in all.
   - Compare throw by throw. These must be equal:
     - the throws found;
     - rest positions;
     - colours;
     - house contents;
     - every time on the card;
     - the line, every figure.
   - "Equal" allows float noise only (my call: 1 mm and 0.01 s), with anything beyond that explained.
   - The changes are compared one at a time: today against #2, then #2 against #2 with the shorter lead. Both
     comparisons are held to this equality.
2. **Latency.** Stop to publish in the same replay. Target p50 ≤ 6 s and p90 ≤ 8 s (my call, from the agreed
   5-6 s). The new split is reported per part, as in the table above.
2b. **League reads with the shorter lead** (my call, since it touches `cameras` playlists). Run `analyze` on
   `league-1105` game 1, sheets 3 and 4, with the old lead and the new. The timelines must be identical.
3. **Load.**
   - Each watch's busy fraction: today, with #2, and with #2 plus the shorter lead.
   - Then three watches at once on one worker, the five-sheet case. The per-sheet p90 must still meet item 2.
4. **Live, after the user's go.**
   - Deploy to the workers through `gated_restart.sh` (run with `sh`).
   - Read one real practice session's stop-to-publish times from the logs, as on 11/07.

## Constraints

- **Branch.** Work stays on `camera-direct`. Nothing merges to main before the camera-direct project is done.
- **Deploy.** Only with the user's go.
  - The camera-file lead also changes league reads on `cameras` playlists, so it goes out after the 11/09 Gate B
    comparison, not before.
  - Ask before a version bump. Bumps requeue streams; do them in the morning.
- **Tests.** Run the affected subsets only; the full suite runs out of memory here.

## Left to the builder

- How `enrich` is split into the early part and the stop part, and where the early state lives (the watch, the
  `ReleaseBook`, or a new per-throw object).
- The size of each piece and the order of the pieces within the rules above.
- Whether the line's early path detections are cached per frame or re-run. Either way, the line must equal today's.
