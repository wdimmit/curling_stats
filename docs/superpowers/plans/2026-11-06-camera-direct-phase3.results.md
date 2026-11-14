# Camera-direct phase 3 -- Gate A results

The 11/05 Thursday Morning league, sheets 3 and 4, replayed from the camera files
(`scratch/camera-direct/league-1105/s3-1`, `s4-1`) at speed 1, both sheets at once,
11:54-15:16 PST 11/06 on the Ryzen. Code 04d0ed6f (the review fixes in 703a0a31 do not
touch the replay path). Each sheet was checked against its published YouTube chart:
dbV9wuA45Z0 (S3, run r_0u0xlUwa0UbLzsACW) and kXOQ06TmD0U (S4, run r_15O9OtXwS7GBhay0a).
Outputs are in `scratch/camera-direct/p3-gate/s3`, `s4`.

## Verdict: passes after the board fix (61b6a3fd, ef1a12db); the clock passed as run

| Gate | S3 | S4 | |
|---|---|---|---|
| Games and end counts equal | 6 = 6 | 7 = 7 | pass (see note 1) |
| Shots matched (>= 95%) | 96/96 | 111/112 | pass |
| Board scores equal | 1/6, after the fix 5/5 | 1/7, after the fix 6/6 | pass after the fix (note 2) |
| Splits, median abs diff <= 0.05 s | 0.165 s | 0.04 s | S3 accepted by the user (note 3) |
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
   - **Fixed (61b6a3fd):** the camera feed's board is read through the layout's `long_h`
     crop (`feeds.cameras.board_cut`, `frames.board_sweep`), the same pixels YouTube
     showed.
   - **Second fix (ef1a12db), found by the re-read:** sheet 4's end 4 card was still
     missed.
     - `read_cards_at` medians the first 40 keyframes of +-90 s. That assumed YouTube's
       5 s keyframes; the cameras key every 2 s, so a read covered only t-90..t-12.
     - On 11/05, two players stood over the red row through those seconds
       (`p3-gate/s4-board-median.png`).
     - Frames are now taken at least 4.5 s apart, so a read spans its whole window. At
       5 s every keyframe is still taken, so YouTube reads are unchanged.
   - **Re-read** (`p3-gate/board_reread.py`, game 1's final board read, camera feed):
     S3 ends 1-5 and S4 ends 1-6 equal YouTube's. The last end is unread on both paths,
     as before.
     - Only the final read was re-run, not the per-end reads a live game makes, so the
       next full replay or league night is the full check.
   - This is analysis, not the clock.

3. **S3 splits alternate in sign with the direction of play.** The per-end median
   differences (camera - YouTube) are -0.16, +0.18, -0.18, +0.16, -0.15, +0.14.
   - A split runs from the far house's panel to the throwing end's side view, so the far
     house switches each end.
   - The skew measured between YouTube's views and the camera clock on S3 is house_a
     +165/+178 ms and house_h -40..-91 ms. That is enough to make YouTube's splits long
     one way and short the other.
   - S4's YouTube views are nearly in step, and its splits agree (median abs diff 0.04 s).
   - The camera splits come from one clock, so they are likely the right ones.
   - The user accepted the splits on 11/06.

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

## The live stream's timing (Task 5 step 5, 11/06 evening)

Checked on two of the Friday league's live streams while they played, 18:46-19:00 PST:
AP8h9VwexkY (worker 1) and 3KTI93qJwoc (worker 2). Script and output:
`scratch/camera-direct/livecheck/`.

- Both playlists still began at segment 0, about 17 minutes in.
- Segments at 600 s and near the live edge (860 s, 870 s) were placed on the video's clock
  from their pts against segment 0's. Each one's picture is in the worker's own recording
  (`rec.0.ts`) at exactly that time: 0 frames off, with an identical match.
- YouTube's PROGRAM-DATE-TIME was 6.1 s and 7.5 s before the listed start
  (actualStartTime). The clock's first search, +-30 s around the start, covers that.

**Pass.** Nothing is left open before Gate B except the user's go.

## Gate B: Sunday Skips, 2026-11-08, sheets 3 and 4 live from the cameras

Live charts: N3g-IC3MqeI (S3, r_1dr5CBBOjw5F8gCgy) and u1SZyYuCVzc (S4, r_1A7I8jVrVWa8FrBSN),
both on worker2, recorded 19:03-21:30 PST, ready 81 s (S4) and 104 s (S3) after the stream
ended. The reference is each game's own YouTube copy (CAMERA_KEEP_YOUTUBE), analysed after
the ice closed (22:19-22:40 PST) with the deployed image in throwaway containers,
`scratch/camera-direct/gateb-1108/yt/`. Comparison script and frames: session scratchpad
`gateb/gb_compare.py`.

| | S3 | S4 |
|---|---|---|
| Clock | `segment`, 190.468 then 190.501 s | `segment`, 188.281 s |
| Ends | 6 = 6 | 8 = 8 |
| Shots matched (compare_timelines) | 96/96 | 120/128 |
| Board scores | 5/5 read, all equal | camera 0 read; YouTube 3 (ends 1-3) |
| Detected score per end | 6/6 equal | 7/8 (end 6: camera red 1, YouTube red 3) |
| Rest, median / worst (paired) | 0.9 / 1.7 cm | 0.9 cm; outliers 138 cm (e7 s11), 15 cm (e8 s12) |
| Hog crossing (long camera) | -0.01 to -0.07 s | 0.0 to -0.09 s |
| Release, rest, deep link (overhead) | -0.2 s every end | +0.2 to +0.3 s every end |
| Camera capture | no reconnects or warnings | same |

Findings:

1. **The cameras start when the poll finds the stream.** The streams opened at 19:00:18;
   the 3-minute poll (`live_poll_s` 180) made the runs at 19:03:04 and recording began
   19:03:20. YouTube can go back to segment 0; the cameras cannot. S3's game began after
   that, so it lost nothing. S4 lost end 1's first 5 rocks (`joined_late` 5).
2. **S4's board: the blank-end card covers the red marker.** End 1 was blanked, and its
   "1" card hangs on the hook over the red team marker. `scoreboard.find_board` needs both
   markers, so it found no board in any frame after that (probe: `gateb-1108/board/`). The
   board is found at camera t 5 and 60 s, before the card went up. The YouTube read also
   failed after end 3 ("None in 13 reads, ends 4-8 not posted"). This is the board finder,
   not the cameras; S3's board read every end.
3. **S4 end 6, last rock: the camera read the house in the clearing.** Camera t_rest
   5696.35, YouTube 5688.03. YouTube frames show the stones in place at 5686, being swept
   off at 5690, and gone by 5698. The camera chart kept 2 stones and scored red 1. The
   board's cards (red to 5 after end 6) agree with YouTube's red 3. This is the known
   last-rock read in the clearing.
4. **S4 end 7, shot 11: the camera is right.** YouTube put the rest at 6270.95, but the
   thrown red is still running at 6274-6276; the camera's 6278.56 rest at (1.11, 0.70) is
   after it stopped. Both charts end with the same stones.
5. **Times.** The long-camera hog crossings agree within 0.1 s, so the clock is right.
   Overhead times differ by a constant per sheet (S3 -0.2 s, S4 +0.3 s), with no drift
   and no change with direction. That fits YouTube's house panels being out of step with
   its long views, as measured at Gate A; the camera times come from one clock. Deep links
   are within 0.32 s (S3) and 0.33 s per-end median (S4).
6. **S4 rejects many offered deliveries** (camera 4-10 an end, YouTube 3-8). That is the
   sheet, not the cameras, and both still count 16 per end.

**Verdict:** S3 passes on every measure. S4 matches except finding 1 (a start-up gap
specific to the cameras) and finding 3 (one end's score). Findings 2 and 3 also affect
YouTube games.
