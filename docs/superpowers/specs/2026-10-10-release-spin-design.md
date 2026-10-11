# Release spin: a rock's rotation, in rpm, in-turn or out-turn

**Status:** design, 2026-10-10. Decisions are the user's, from the spike session
that measured it (memory `spin-rate-spike`).

## What it is

How fast the thrower set the rock turning, read from the long camera at the far
end -- the one facing the thrower, hogtime's camera -- shown on each rock as
**Rotation: 11 rpm, in-turn**.

## Decisions (the user's)

| question | answer |
| --- | --- |
| unit | **rpm** (turns per minute), a whole number: rounder than turns per second |
| which value | **the spin as thrown**: about a second past the hog line, "a perfect place to measure" |
| direction | **in-turn / out-turn**, from the curl and the hack call |
| live games | **included** |
| hosted games already charted | **not backfilled**: no reprocess; new games only |

## How it is read (measured in the spike, 2026-10-10)

- **The handle, by a small keypoint model.** Each frame's stone crop goes to a
  YOLO11n-pose model with two points: the **neck** (where the handle rises out of
  the stone, its pivot) and the **grip end** (the free end). Seen from the side,
  the grip sits `r·sin θ` across from the neck, so `s = (grip_x − neck_x) / stone
  width` swings once per turn. Trained on 258 hand-labelled frames over five
  2026-27 games, one per sheet (`datasets/spin/`, the spike's p5 model).
- **The rate** is the frequency of `s`: a periodogram and a robust sinusoid fit
  over the whole stretch in view, then a constant-rate fit anchored on it (within
  ±30%) to **the first ~1.25 turns from 8 m past the throwing tee** (the hand is
  off by then; 8 m is 0.7–1.1 s past the hog line), the window clipped to 5–9 s.
  Anchoring keeps a second of the rock hidden by a sweeper from pulling the early
  rate off; the quality gate uses a robust noise (median absolute residual) so a
  few stray frames do not read as noise. Dry-run on PHbZ's 92 rocks at 10 fps,
  this reads the spike's 30 fps early rates to a median 1.2% (90%: 3.7%). A slow-down fitted through the whole stretch
  and extrapolated back to the hog line was tried and rejected: it turned a steady
  0.21 turns/s into 0.27.
- **10 frames a second** is enough: against 30 fps on 92 rocks, median change
  1.1%, none over 10%. 5 fps is too thin.

## How well (spike, on rocks and games the model never trained on)

- Held-out PHbZ rocks against frame-by-frame eye readings of the first turn:
  within ~10% (e3s02 0.215 vs 0.21–0.22 turns/s, e3s14 0.137 vs 0.12–0.14, e4s14
  0.163 vs 0.167, e1s05 0.242 vs 0.233, e3s15 0.269 vs 0.25).
- Ends 5–6 of four other games (128 rocks never labelled): 126 rated, 122 pass
  the gate; the swing as clean as on the training games.
- Over five games, 388 of 416 rocks readable; early spin median 0.171 turns/s
  (**10 rpm**), 5–95% 0.10–0.27 (6–16 rpm), max 0.47 (28 rpm). The extremes were
  checked by eye and are real.
- Rocks with no reading: hogged rocks, and fast hits that leave the frame
  showing under half a turn (~5%).

## In-turn or out-turn

A curling stone curls the way it turns: clockwise seen from above curls to the
thrower's right. A right-hander throws from the **left** hack (22 of 24 players
on the replays) and their in-turn is clockwise. So:

> **in-turn** when the rock curls away from its hack's side (curls right from
> the left hack, left from the right hack); **out-turn** when it curls toward it.

The viewer has both halves already: `turnOf(shot)` (curl direction, read to the
first contact on a hit) and `hackOf(shot)` (the player's hack, `playerHacks`).
With either missing the figure says "turn not known". The curl is measured on
86% of rocks (420 of 491 over five games).

## Where it runs

- A new attach-only pass, `game/spintime.py`, after the line pass in
  `analyze.build_one_end` -- which live games run too (`live/session.py`). It
  reads the hog camera at 10 fps from 0.5 s after hogtime's crossing for 10 s,
  seeded from that crossing's track, follows the rock frame to frame, crops it
  exactly as the training crops were cut, runs the pose model and fits the rate.
- **Its own tracking.** The hog-line pass reads only a band around the hog line
  (to ~10 m) and keeps positions, not pixels; the line pass's down-sheet path
  is on the *other* camera. So this pass decodes and detects anew from ~8 m to
  the bottom of the frame: an estimated 1–1.5 s of GPU per rock (+15–25% on an
  end). Measured before it goes live (plan task 7).
- **Models:** the side model the line pass already loads (`ctx.line_model`,
  ds13c) finds the rock; the pose model ships as `weights/spin1.pt`, pinned in
  `Dockerfile.worker` -- absent means no spin, not an error. It is loaded with
  the others in `analyze.load_models` (review fix), so a pinned file that is
  missing or will not load stops the job at its start, and named in
  `processing_version` after `+line`.
- **Not read** (review fixes): the frames from where a rock slows below 0.5 m/s
  (a rock at rest keeps its handle still, which read as a slower rate); a fit
  whose early rate strays more than 25% from the whole stretch's (under half a
  turn of track, which reads 40-50% fast; real rocks stray -14% to +19%).

## Measured on real games (plan task 7, 2026-10-11)

Branch at 44f02f4 (with the review fixes) in the worker image on the Ryzen
(5950X + 3080, the production box), beside another session's ds18 training and
the production workers -- so the times read high. `scripts/spin/ab_parity.py`
against the spike's rates (same model, 30 fps, its own crops; the spike's 0SWB
game is this run's second, `--game=1`; 0SWB and VVgL cover ends 2-6 only).

| Video | Read | Spike's reads paired | Median diff | 90% | Over 10% | Spin per rock | Per end |
| --- | --- | --- | --- | --- | --- | --- | --- |
| PHbZ3EKOhMI (sheet 3) | 92/96 | 88/92 | 1.6% | 4.3% | 3 | 2.33 s | +41% on a cached rerun |
| 0SWB4g3SJoE (sheet 1) | 160/176 | 70/72 | 1.7% | 8.8% | 5 | 1.93 s | +18% cold (median end 205 s) |
| VVgLH4uBxYs (sheet 2) | 234/240 | 78/78 | 1.3% | 4.8% | 0 | 2.03 s | +24% cold (median end 161 s) |

- **Parity:** the 10 fps pass matches the spike rock for rock (median 1.3-1.7%);
  the rocks over 10% are mostly the slowest (3.9 against 4.6 rpm, under a turn
  in view) or a few at 15%.
- **Nothing else moved:** PHbZ branch against its merge-base (f9c9a07), the same
  image: every field of the two timelines is equal but `spin`,
  `schema_version` (8 -> 9), `processing_version` (now
  `...+line+spin-spin1-b00cc0bd`) and `analysed_at`.
- **Cost:** ~31-44 s an end, ~2 s a rock -- about twice the estimate above. On a
  cold end, the kind live games run, +18-24%; on a rerun with cached
  detections, where the rest of the end is cheaper, +41%. Per rock (profiled
  on PHbZ, same box under load): ffmpeg decode of the 10 s window ~38%, ds13c at
  imgsz 800 ~38%, the fit ~14% (CPU, the worker's busy main thread), the pose
  model ~10%. Levers that change no reading: decode the next rock while the GPU
  detects this one; vectorise `spinfit.fit`; imgsz 416 below row 640 as
  `linetime.find_path` does. One that does: a shorter window (7 s cuts ~25%,
  fewer turns in view).

## Data

Timeline schema 9: each shot gains `spin`, null when not read:

```json
"spin": {"rpm": 10.3, "rpm_avg": 9.9, "turns_in_view": 1.8, "samples": 74}
```

`rpm` is the release value the viewer shows; `rpm_avg` is the whole stretch in
view (a rock that slows reads up to ~15% lower there), kept for analysis.

## Out of scope

- Practice sessions (`practice/enrich.py`), other clubs' cameras, the report
  pages: later, if wanted. (My Shots shares the viewer's figure list, so its
  schema-9 games show Rotation too.)
- Direction from the side view itself (the swing is the same either way round).
