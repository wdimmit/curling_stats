# Practice phase 1: results on cached video

Plan: `2026-10-09-practice-phase1-tracker.md`. Run 2026-10-09 on branch `practice`.

## Setup

- **Machine:** RTX A2000 8 GB laptop, not the Ryzen worker. Every process ran at nice 10 at the user's request.
- **Models:** the production defaults, ds16a for detection at imgsz 448 and broom4. The spec mentions ds17b, which isn't shipped; the reference used the same ds16a.
- **Reference:** `analyze` of `VXU9xwmugRg` at this branch's HEAD, run from the cached video with no YouTube calls (metadata built locally). It found 2 games and 13 ends, with the same boundaries as before.
- **Replays:** `curling-score practice-replay`, at real time after a 1200 s lookback burst.
- **(a) Game window:** 8505-10305 s, game 1's ends 2-3, thrown both ways. The reference has 31 rocks in it, one of them hogged.
- **(b) Warm-up window:** 6480-7570 s, between the two games.

## Window (a): game 1, ends 2-3

| | Run 1 | Run 2 (after the fixes below) | Spec target |
|---|---|---|---|
| Throws reported | 34 | 31 | |
| Recall, arrived rocks | 30/30* | **30/30** | ≥ 90% |
| False throws | 4* | **1** | ≤ ~1 in 50 |
| Rest vs reference, cm | p50 1.5, p90 5.7, max 14.5 | p50 1.5, p90 **4.9**, max 20.5 | ≤ 5 |
| Hog-to-hog split vs reference, s | p50 0.04, p90 0.09, max 0.16 | p50 0.05, p90 **0.14**, max 0.32 | ≤ 0.1 |
| Miss at the broom vs reference, cm | p50 3.6, p90 5.4, max 6.0 | p50 3.6, p90 5.4, max 6.0 | — |
| Target broom present/absent agrees | 28/28 | 30/30 | — |
| Latency, s | p50 11.9, p90 15.3, max 20.0 | p50 12.2, p90 **13.0**, max 13.6 | p90 ≤ 15 |

\* Run 1 rescored with the fixed matcher.

### What run 1 found, and the fixes

**Three throws reported twice.** In each case a few frames of the same colour moved 0.3-3 s after a reported stone came to rest: a fragment of its track, or a sweeper nudging it. The house read then put the new "arrival" on that same stone. An end's one-rock-per-turn rules drop these; practice has no turns. Fixed in `finder.py`: a same-colour arrival entering within `AFTER_REST_S` (10 s) of a reported rest is that stone, not a new throw. Run 2 had none.

**Two takeouts scored as false and missed.** These stones ran out of view. The tracker's "rest" is when the stone left the view, the end pipeline's is ~5 s later, and the releases agreed to 0.06 s. Fixed in `compare_throws.py`: a throw seen released matches by its release.

### What remains in run 2

**One false throw, the reference's one hogged rock (9590 red).** The stone stopped on the far hog line and a sweeper pushed it aside. The reference calls it hogged, which is right by the rules (it never fully crossed). The tracker followed the push and reported an arrival that went out of play. This is a borderline case, not fixed.

**Rest outliers.**
- Two top-house takeouts are 10 and 20 cm off (9119, 9190). In run 1 both were "went out of view".
- The 3 s house read catches a stone still moving at the edge of the view, where an end reads for 12 s.
- Both houses read about 1.4 cm long at the median.

**Split p90 is 0.14 s against 0.1.** Four long splits (14.5-18 s) differ by 0.12-0.32 s. Run 1's p90 was 0.09 on the same footage, so this is sample-phase variability: practice reads the throwing panel at 10 fps on a grid starting where each read starts, where an end reads at 5 fps.

**The miss at the broom is systematic, not noise.**
- Bottom house: all 14 differences are negative, median -3.7 cm.
- Top house: mixed, median +2.6 cm.
- The line pass carries nothing between shots (`linetime.time_lines`), so this comes from calibration. Practice calibrates the side views from 24 keyframes of the 20-minute lookback; the reference uses the whole video.
- Which one is closer to the truth is not known. The live spike found the same thing: early calibration is fine for the panels, but the side views want a later recalibration.

### Latency

- Stones that left the view were published 8.4-10 s after leaving. Stones that came to rest took 11-14 s.
- The difference, ~3-4 s, is confirming the rest (`REST_CONFIRM_S` plus persistence).
- The remaining ~8-9 s is reading 2 s behind the head in steps of 2 s or more, plus the long-camera passes. Each of those reads re-enters the growing TS file 10 s early.
- The watch logs no per-stage timings yet. Phase 2 should log them before anything is tuned.

## Window (b): the warm-up between games

**There was almost no practice.** A frame a minute shows empty houses, players leaving, the ice being scraped and pebbled, and stones only at the very end.

**Independent check:** the long camera's slide scan (`sidereleases.scan_points` + `find_slides`, gated as `lost_rocks` gates them). It was validated on window (a) first:
- every one of the 31 reported throws had a slide;
- the 25 slides with no throw were walking pace (1.15-1.21 m/s, every 3-4 s: someone carrying stones up the sheet) or fell between ends with no reference rock.

**Two throw-speed slides in the warm-up:**
- **6513.1, yellow to the top: found.** The tracker's throw was dated 6517.9: the thrower's body hid the stone from the overhead camera, which first saw it ~4.8 s after the long camera did (frames checked).
- **7541.2, red to the bottom.** It was released 29 s before the window ended, too late to arrive and be confirmed inside it. Not a miss.

None of the three cached videos (VXU9, hOKZ, AEqL) has any real practice in it: hOKZ's game starts at 235 s and AEqL's first rock is at 31 s.

## Review Focus, as the runs showed it

1. **An arrival nothing released:** none occurred (`unreleased` n = 0 in both runs).
2. **A slow draw given up as hogged:** no false "never arrived" throw in either run.
3. **Released before Start, arriving after:** not exercised on video. Window (a) starts 52 s before the first rock. It is covered by the unit test.
4. **Same colour close together:** a different failure turned up, the same stone seen again after its rest. It is fixed.
5. **Calibration:** the first try was complete in all three replays.

## Findings for phase 2

- **Real practice footage is needed.** The practice gate found sessions on 10/04 (doubles sheet 1, Qzh8; Sunday Afternoon S5, EUpp). The workers keep recordings for 7 days, so they may still be there. Pulling them is a worker rsync, not a YouTube download, and is the user's call.
- **The overhead's release can run seconds late** when the thrower hides the stone. The long camera's slide time is the better release on a practice card, as an end already prefers it past the throwing T-line.
- **The side-view calibration** should be refreshed as the session goes on (the live session's cadence), or reused from the last league night on that sheet with a check.
- **The watch should log per-stage timings**, so the ~8 s of passes and read lag can be split.
- **Runs are not deterministic:** split p90 was 0.09 in run 1 and 0.14 in run 2 on the same footage, and latency moved too (sample phase).
