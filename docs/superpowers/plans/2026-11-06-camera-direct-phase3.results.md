# Camera-direct phase 3 -- Gate A results

The 11/05 Thursday Morning league, sheets 3 and 4, replayed from the camera files
(`scratch/camera-direct/league-1105/s3-1`, `s4-1`) at speed 1, both sheets at once,
11:54-15:16 PST 11/06 on the Ryzen. Code 04d0ed6f (the review fixes in 703a0a31 do not
touch the replay path). Each sheet was checked against its published YouTube chart:
dbV9wuA45Z0 (S3, run r_0u0xlUwa0UbLzsACW) and kXOQ06TmD0U (S4, run r_15O9OtXwS7GBhay0a).
Outputs are in `scratch/camera-direct/p3-gate/s3`, `s4`.

## Verdict: MISS on the board; the clock passes

| Gate | S3 | S4 | |
|---|---|---|---|
| Games and end counts equal | 6 = 6 | 7 = 7 | pass (see note 1) |
| Shots matched (>= 95%) | 96/96 | 111/112 | pass |
| Board scores equal | 1/6 | 1/7 | **MISS** (note 2) |
| Splits, median abs diff <= 0.05 s | 0.165 s | 0.04 s | S3 misses as written (note 3) |
| Clock: two checks agree within 2 frames | -908.1566 | -908.9555, then -914.7215 | pass |
| S4 step placed within 10 s | -- | video ~3017 vs. ~3000 estimated | pass in effect (note 4) |
| Deep links | all 96 within 0.29 s | all 111 within 0.11 s | pass (note 5) |
| Per-end lag p90 <= 180 s | build 131-150 s; publish 446-520 s after end | build 115-129 s; publish 432-464 s | see note 6 |

The stones are read the same: the detected score is equal on every end of both sheets.

## Notes

1. **Extra games.** The camera files run to 13:00, but both YouTube streams ended after
   game 1. The replay therefore found games after it (S3 +1 game of 4 ends, S4 +2 short
   ones). Those were not compared. In production a camera game stops when its stream
   ends (`Run.stream_ended_at` -> `finish_stream`).

2. **Board: the camera path reads a neighbour's board.**
   - The camera feed's `board` view is the whole `long_h` picture (`feeds/cameras.py`:
     `"board": "long_h"`, uncut).
   - That picture shows the boards of sheets 2, 3 and 4 in full and part of 5's.
     Frames: `p3-gate/s3-longh.jpg` (11:39:26 on the wall clock) and `s4-longh.jpg`.
   - YouTube's long_h crop shows only the home board in full.
   - `scoreboard.find_board` takes the largest readable marker pair, which on the camera
     picture is often a neighbour's board. Every per-end card read differs from YouTube's;
     the 1/6 and 1/7 that agree are chance.
   - **Proposed fix:** cut the `board` view with the layout's `long_h` crop, giving the
     same pixels YouTube showed. The alternative is choosing the pair nearest the sheet's
     expected position.
   - This is analysis, not the clock.

3. **S3 splits alternate in sign with the direction of play.** The per-end median
   differences (camera - YouTube) are -0.16, +0.18, -0.18, +0.16, -0.15, +0.14.
   - A split runs from the far house's panel to the throwing end's side view, so the far
     house switches each end.
   - The skew measured between YouTube's views and the camera clock on S3 is house_a
     +165/+178 ms and house_h -40..-91 ms. That is enough to make YouTube's splits long
     one way and short the other.
   - S4's YouTube views are nearly in step, and its splits agree (median abs diff 0.04 s).
   - The camera splits come from one clock, so they are likely the right ones. Whether
     to accept this is the user's call.

4. **S4 step.** The tracker put the 5.78 s step at camera t 3932, which is video t
   ~3017.
   - The earlier "~3000" was an estimate from 600 s checks, not a measurement.
   - No shot falls between the two, and every deep link after the step agrees within
     0.11 s, so the placement is good enough for what is published.

5. **Deep links, checked by time, not by eye.**
   - Every shot's `t_video_s` was compared with the YouTube chart's. Those times come
     from the same recording, so equal times land on the same delivery.
   - S3: median +0.03 s, worst -0.29 s. S4: median -0.04 s, worst -0.11 s. This
     includes the 57 S4 shots after the step.

6. **Lag.** Two numbers:
   - **Build time per end:** S3 131-150 s, S4 115-129 s, with both sheets running.
     This is the measure the 180 s target came from (the five-stream rehearsal: median
     148 s, p90 172 s). Passes.
   - **Publish time after an end's last stone:** 432-520 s. Most of it is the session
     waiting for the end to settle (`segment.settled_ends`), a rule shared with YouTube
     games.
     - A YouTube game's settle wait was not measured here, so "the same as YouTube" is
       inferred from the shared code, not seen.
   - Box load at the end was ~4.7; the practice guard never fired.

## Still open before Gate B

- The board fix (note 2), tested against these two sheets again.
- The user's call on the S3 splits (note 3).
- The Task 5 live check on the live stream's timing (pts against the worker's own
  recording), and the +-30 s first-search window.
