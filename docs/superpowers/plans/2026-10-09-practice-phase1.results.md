# Practice phase 1: results on cached video

Plan: `2026-10-09-practice-phase1-tracker.md`. Run 2026-10-09 on branch `practice`.

## Setup

- **Machine:** RTX A2000 8 GB laptop, not the Ryzen worker. Every process ran at nice 10 at the user's request.
- **Models:** the production defaults, ds16a for detection at imgsz 448 and broom4. The spec mentions ds17b, which isn't shipped; the reference used the same ds16a.
- **Reference:** `analyze` of `VXU9xwmugRg` at this branch, run from the cached video with no YouTube calls (metadata built locally). It found 2 games and 13 ends.
- **Replays:** `curling-score practice-replay`, at real time after a 1200 s lookback burst.
- **(a) Game window:** 8505-10305 s, game 1's ends 2-3, thrown both ways. The reference has 31 rocks in it, one of them hogged.
- **(b) Warm-up window:** 6480-7570 s, between the two games.

## Window (a): game 1, ends 2-3

Three runs. Run 1 was the plan's code. Run 2 added the two fixes below. Run 3 added the code review's fixes, including the replay clock, so **run 3 is the one whose latency is real**.

| | Run 1 | Run 2 | **Run 3 (final code)** | Spec target |
|---|---|---|---|---|
| Throws reported | 34 | 31 | 32 | |
| Recall, arrived rocks | 30/30* | 30/30 | **30/30** | ≥ 90% |
| False throws | 4* | 1 | **2** | ≤ ~1 in 50 |
| Rest vs reference, cm | p50 1.5, p90 5.7 | p50 1.5, p90 4.9 | p50 **1.5**, p90 **12.9**, max 100 | ≤ 5 |
| Hog-to-hog split vs reference, s | p50 0.04, p90 0.09 | p50 0.05, p90 0.14 | p50 **0.04**, p90 **0.12**, max 0.32 | ≤ 0.1 |
| Miss at the broom vs reference, cm | p50 3.6, p90 5.4 | p50 3.6, p90 5.4 | p50 3.6, p90 5.4, max 6.0 | — |
| Target broom present/absent agrees | 28/28 | 30/30 | 30/30 | — |
| Latency as logged, s | p50 11.9, p90 15.3 | p50 12.2, p90 13.0 | p50 **11.9**, p90 **13.8**, max 15.7 | p90 ≤ 15 |
| Release sources | | | overhead 31, none 1 | |

\* Run 1 rescored with the fixed matcher.

**Latency in runs 1-2 was wrong by ~4.8 s.** It was understated, and the watch was also slowed by the same amount (found by the code review). With a mid-video start, the replay's head was on ffmpeg's `-ss` clock, not the recording's. Run 3's latencies are on the right clock: the watch read to 10304.97 s of a file ending at 10305.03 s.

### Fixes made

1. **The same stone reported twice** (3 of 34 in run 1). A few frames of the same colour came into view mid-panel 0.3-3 s after a reported stone came to rest: a fragment of its track, a sweeper nudging it, or a stone it struck. The house read then put the "arrival" on that very stone. An end's one-rock-per-turn rules drop these; practice has no turns.
   - `finder.py`: an arrival of the same colour, entering within 10 s of a reported rest **and first seen mid-panel** (below 4.0 m), is not a new throw.
   - Every real arrival came into view at 4.44-4.57 m. So a partner's throw arriving from the top of the view seconds after the last stone stopped still counts.
2. **The scorer** matched takeouts that ran out of view by their "rest" (when they left the view, 5 s off the end's). It now matches by release when both sides have one.
3. **The replay's head** is now on the recording's clock.
4. **A failed ffmpeg** now stops the harness instead of leaving it waiting for ever.
5. **A release re-found at the window's front edge.** When the 90 s window's front cuts through a release's climb, what is left still crosses the stage-1 line and was booked as a new release. It would have become a phantom "hogged?" card 60 s later. The reviewer reproduced it in 7 of 84 synthetic cases; it never happened on video. Releases first seen within 5 s of the front are now ignored.
6. **The scorer counts release sources.**

### What remains in run 3

**Two false throws.**
- **9590 red: the reference's one hogged rock.** It stopped on the far hog line and a sweeper pushed it aside. The reference calls it hogged, which is right by the rules. The tracker followed the push and reported an arrival that went out of play. Borderline; not fixed.
- **9224.7 red: no release from either camera,** "resting" at the very top edge of the view (y = 4.53 m). This is what `release_source: null` exists to flag: a stone that didn't come out of a hack. The page should not show such a stone as a throw (phase 4).

**Rest is unreliable after a hit.** Three top-house takeouts were 13, 53 and 100 cm off in run 3: 9119, 9190 and 9003. The same throws were 10, 20 and 0 cm off in run 2.
- The house is read for 3 s after the shooter stops. In a hit, the struck stones are often still moving, and the stone the house read credits to the throw can be a struck one: 9003's track stopped at (0.64, 0.41), but it was given a stone at (1.42, -0.16).
- An end reads for 12 s.
- **For phase 2:** when the house changed by more than the thrown stone (a hit), read it again ~10 s on and update the card. Draws keep their fast card.

**The miss at the broom differs from the reference systematically.**
- Bottom house: all 14 differences are negative, median -3.7 cm.
- Top house: mixed, median +2.6 cm.
- The line pass carries nothing between shots, so this comes from calibration. Practice calibrates the side views from 24 keyframes of the 20-minute lookback; the reference uses the whole video. Which one is closer to the truth is not known.

**Split p90 is 0.12 s against 0.1.** A few long splits (14.5-18 s) differ by 0.12-0.32 s, and the set changes from run to run. That's sample-phase variability: practice reads the throwing panel at 10 fps on a grid starting where each read starts, where an end reads at 5 fps.

**A raise reported as the struck stone (9771, run 2).** The thrown red stopped short; the red it struck went on into the 12-foot. In run 2 the tracker reported the struck stone's path as the throw, so that card shows the wrong rest. The reference has no rest for this rock, so the scorer could not see it.

### Latency (run 3)

- Stones that left the view were published 8-10 s after leaving. Stones that came to rest took 11-14 s, with one at 15.7.
- The difference, ~3-4 s, is confirming the rest (`REST_CONFIRM_S` plus persistence).
- The remaining ~8-9 s is reading 2 s behind the head in steps of 2 s or more, plus the long-camera passes. Each of those reads re-enters the growing TS file 10 s early.
- The watch logs no per-stage timings yet. Phase 2 should log them before anything is tuned.

## Window (b): the warm-up between games

**There was almost no practice.** A frame a minute shows empty houses, players leaving, the ice being scraped and pebbled, and stones only at the very end.

**Independent check:** the long camera's slide scan (`sidereleases.scan_points` + `find_slides`, gated as `lost_rocks` gates them). It was validated on window (a) first:
- every one of the 31 throws reported in run 2 had a slide;
- the 25 slides with no throw were walking pace (1.15-1.21 m/s, every 3-4 s: someone carrying stones up the sheet) or fell between ends with no reference rock.

**Two throw-speed slides in the warm-up:**
- **6513.1, yellow to the top: found.** The tracker's throw was dated 6517.9: the thrower's body hid the stone from the overhead camera, which first saw it ~4.8 s after the long camera did (frames checked).
- **7541.2, red to the bottom.** It was released 29 s before the window ended, too late to arrive and be confirmed inside it. Not a miss.

None of the three cached videos (VXU9, hOKZ, AEqL) has any real practice in it: hOKZ's game starts at 235 s and AEqL's first rock is at 31 s.

## Review Focus, as the runs showed it

1. **An arrival nothing released.** Run 3 had one (9224.7, `none`), and it was not a throw.
   - A stone pushed by hand could also come out as `side`: the long camera fills the release of any arrival the overhead missed, and a push can pass for a slide.
   - Clearing the house by pushing stones up the centre line could even look like an overhead release, because practice runs the release finder on both panels all the time.
   - Neither window had house-clearing along the centre line, so this is untested.
2. **A slow draw given up as hogged:** no false "never arrived" throw in any run.
3. **Released before Start, arriving after:** not exercised on video. Window (a) starts 52 s before the first rock. It is covered by the unit test.
4. **Same colour close together:** a different failure turned up, the same stone seen again after its rest. It is fixed, without dropping a real throw from the top of the view.
5. **Calibration:** the first try was complete in every replay. A replay ffmpeg cannot start now fails instead of waiting.

## Findings for phase 2

- **Real practice footage is needed**, preferably with house-clearing. The practice gate found sessions on 10/04 (doubles sheet 1, Qzh8; Sunday Afternoon S5, EUpp). The workers keep recordings for 7 days, so they may still be there. Pulling them is a worker rsync, not a YouTube download, and is the user's call.
- **Re-read the house after a hit** (see above); the 3 s read is what makes rest p90 miss.
- **Prefer the long camera's slide as a practice card's release.** The overhead's can run seconds late when the thrower hides the stone, and an overhead "release" the slide cannot corroborate (a push) is suspect.
- **Refresh the side-view calibration** as the session goes on, or reuse the last league night's for that sheet with a check.
- **Log per-stage timings.**
- **Decide what happens at session end:** releases younger than 60 s and arrivals not yet confirmed are currently dropped.
- **Runs are not deterministic** (sample phase): split p90 was 0.09, 0.14 and 0.12 over three runs of the same footage, and rest after hits moved by tens of cm.
