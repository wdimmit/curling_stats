# Timing the throwing-end hog crossing from the long camera

## Why

The split is now two tripwires at the painted hog line, one in each overhead
panel (`game/split.py`, 2026.09.10). It is accurate -- every crossing lands
within 0.055 s of a hand mark -- but it only covers about half the shots.

Measured over 48 shots, three ends of the reference VOD, by *why* there is no
split:

| | shots | |
|---|---|---|
| split measured | 22 | 46% |
| **release lost before the hog line** | **19** | **40%** |
| no release seen at all | 6 | 12% |
| arrival entered already past the far line | 1 | 2% |

The 19 are throws the overhead panel tracked and then lost, at y = 1.36 to
4.36 and mostly around 2.5-3.0, where the thrower and sweepers close over the
stone. The panel cannot be made to see them; it is looking straight down at a
stone with people standing over it.

The composite's two wide side views can. Each sits at one end and watches the
*other* end's hog line square on from about 22 m, where the stone reads ~52 px
across, the sweepers stand beside it rather than over it, and the line itself
is painted and fixed. The six shots with no release at all still have clean
arrivals, so the throwing-end crossing is the only missing piece on every one
of the 48.

Target: coverage from 46% toward 100%, with no wrong splits.

## Decisions taken with the user

| Decision | Choice |
|---|---|
| Source for the throwing-end crossing | The long camera, **always**. The panel tripwire stays as a cross-check, never as a per-shot fallback, so every split in a game comes from one method and two rocks are comparable. |
| Detector | **Classical now**, behind an interface, measured across all videos; swap for a trained detector only if the measurement says so. |
| Coverage vs. correctness | **Refuse anything doubtful.** Expect 85-95%, not 100%, with the shortfall reported as unmeasured. |
| Video-level fallback | **Not built.** If a video's side views cannot be calibrated it gets no splits and says so; we go looking for such a video rather than designing around a hypothetical one. |

## Geometry, and which camera

Each side camera sees the far end's house and hog line, and **never its own** --
its own hog line is about half a metre beneath it, out of frame. So the
throwing-end crossing is watched by the camera at the *target* end, and which
physical camera that is alternates with the end, exactly as `OTHER_HOUSE` does:

    CAMERA_FOR = {"top": "left", "bottom": "right"}

Fitted from paint on the reference VOD: the camera sits ~40 m from the far tee
at ~2.9 m, giving 16.5 px per metre along the sheet at the hog line and a stone
that reads 53.6 px wide against ~52 measured. A stone at 2 m/s moves 1.1 px per
frame at 30 fps, so localising its edge to +-2 px times the crossing to ~0.06 s.

## Architecture

Three units, mirroring the existing split of responsibilities.

### `geometry/sideview.py` -- calibration, once per video

Locates each side view in the composite and fits, per view:

- the **tee row**, by least squares over the four painted edges of the green
  12-ft annulus at -+1.829 and -+1.219 m. Not the annulus's centroid:
  perspective magnifies its near half and drags a centroid about 2 px toward
  the camera.
- the **hog row**, as the darkest full-width row below the house.

That is the whole calibration a tripwire needs. It deliberately does not fit a
distance model: the lateral scale cancels out of the row-to-metre map, and the
crossing is a row, not a distance.

Runs on `calib_frames` -- the 24 full frames `analyze` already samples for
`profile.calibrate_panels` -- so it costs no extra decode. Publishes into the
timeline's `calibration` block beside `top` and `bottom`.

### `detect/longview.py` -- one crossing, or a refusal

    find_crossing(video, sideview, color, t0, t1) -> Crossing | None

Seeks the original, crops the hog-line band, tracks the stone and interpolates
the crossing of the calibrated hog row. Returns the time and how it was found,
or `None` with a reason.

This is the only file a swap to a trained detector touches.

The classical detector, already prototyped: the handle's saturated colour
proposes candidates, and a dark run of the right width directly beneath
confirms a stone rather than a broom pad -- the check that took one end from
10 of 13 tracked to 13 of 13.

### `game/hogtime.py` -- attach a crossing to each shot

    time_hog_crossings(shots, video, sideviews, throwing_panel) -> None

Runs after `fit_end` has settled the shot list, in the slot
`thinking.time_shots` occupies, under the same contract: it may attach a time
to a rock already in the list and may never add, drop or renumber one.

`split.long_split` then reads the throwing-end crossing off the shot rather
than deriving it from the release track. The target-end crossing is unchanged:
still the playing panel's own tripwire at `HOG_APPARENT_Y_M`.

## Data flow, per shot

1. Pick the camera: `CAMERA_FOR[throwing panel]`.
2. Pick the window. From the 27 hand-marked crossings, a stone crosses between
   **release + 2.83 s and release + 5.43 s**, so the window is release + 2.0 to
   + 6.5. For a shot with no release, work back from the arrival instead -- a
   wider window, and correspondingly stricter about ambiguity.
3. Seek, crop, track, time the crossing.
4. Attach `t_hog_s` and how it was found.

Cost, measured: **0.25 s** to seek and decode a 3 s window cropped to the band,
so ~30 s of decode for a whole game. On-demand seeks into the original beat a
second proxy outright -- no extra transcode pass, no new cache key. Like the
scoreboard stage this needs the original file, and inherits the same escape
hatch for callers that do not keep it.

## What makes it refuse

A crossing must survive all of:

- **exactly one** plausible candidate in the window; two and it refuses
- monotone travel toward the camera, with real samples bracketing the line --
  never an extrapolation
- apparent width between **0.5x and 1.6x** what the calibration predicts for a
  stone at that row. Those are the prototype's bounds, which rejected broom
  pads while keeping every stone; the gate below is what confirms or moves them
- speed between **1.2 and 3.2 m/s**, the range the 27 hand-marked crossings
  imply once converted through the row-to-metre map
- **agreement with the panel tripwire wherever both fired, within 0.25 s.** A
  disagreement beyond that refuses *both*: it means one of them found the wrong
  object and we do not know which.

The last rule is what makes the 22 overlap shots earn their keep permanently
rather than only during validation.

## Testing

The rig this needs already exists in `tests/conftest.py`: `primary_video`,
`known_frame` (full 1920x1080 composites), `harvested_frames`, and
`VALIDATION_VIDS` keyed by sheet. All five sheets have 12 sampled frames
cached, so cross-sheet calibration is testable without fetching a video.

- **`sideview` against stored plates**, one per sheet: assert the tee and hog
  rows and that the four ring edges land where the fit says. Skips if frames
  are not harvested, as the existing geometry tests do.
- **The tracker on synthetic bands**, extending `tests/synth.py`: a stone of
  known size crossing a painted row, plus the cases that must be refused -- a
  broom pad with no body, two candidates, a track that stops short.
- **Against the hand marks.** The 27 marked crossings become a regression
  fixture, the same shape as `TestAgainstHandMarkedCrossings` in
  `test_split.py`. Marked `slow` and skipped without the cached video.
- **Coverage and agreement**, as a reported measurement rather than an
  assertion.

## The gate before it becomes the source

Run over every end of all eight catalogued videos and report:

- coverage, against today's 46%
- agreement with the panel tripwire on the overlap
- agreement with the 27 hand marks

Ship when coverage is >= 90%, no disagreement with the panel exceeds 0.25 s,
and the hand marks are matched within 0.1 s.

Fail any of those and a trained detector goes in behind the seam
`game/hogtime.py` establishes. **In production that seam is one symbol wide**:
`hogtime` imports `longview` only for `WINDOW_S` and the default
`find=longview.find_crossing`, so the pipeline changes by passing a different
`find`.

It is *not* true that a trained detector touches `detect/longview.py` alone --
this document said so, and ds13 has since made it wrong.
`harvest/sidepool.py` depends on eight of that module's symbols (`candidates`,
`find_in_frames`, `colour_mask`, `runs`, `BODY_DARKER_THAN_ICE`,
`STONE_WIDTH_AT_HOG_PX`, `Proposal`, `KEYS`) and `harvest/sideframes.py` pins
its refusal quota to `longview.KEYS`. That is deliberate: ds13 exists to mine
the classical detector's *refusals*, so it needs the classical detector to go
on existing. Rewriting `longview.py` in place would break it. Adding the
trained detector beside it and pointing `hogtime`'s `find=` at the new one
would not.

## Known risks

- ~~**Side-view calibration is not yet robust.**~~ **CLOSED.** It fitted 7 of
  10 views on 12-frame plates when this was written. All 10 now fit, on those
  same thin plates. Two commits did it: `c051a0c` fits the tee from the
  annulus's outermost pair only -- the edge counts across the ten views are
  `[4,4,4,4,4,6,4,4,4,4]`, and with six edges the second and second-to-last are
  not the 8-ft boundary -- and `e7d6839` set `_GREEN_THRESHOLD` from the real
  plates' measured annulus peaks (6.44-13.27) rather than from a synthetic
  fixture. Pinned by `tests/test_sideview.py::TestEveryRealView` and
  `tests/test_sideviews.py::test_real_videos_calibrate_both_views`, both of
  which run against all five sheets.
- **The classical detector is measured on one video** (`VXU9xwmugRg`, sheet
  2 -- the only video cached where this was run; the other seven catalogued
  videos live in the worker box's cache). `scripts/split_coverage.py` runs
  the gate above over all 13 ends (208 shots) of that video and none of the
  three bars hold, but the shape of the shortfall matters as much as the
  headline number:
  - **Raw detection is 47.6% (99/208), published coverage is 21.2% (44/208).**
    The side-view detector finds *some* crossing on nearly half of all shots,
    before any downstream gate gets a say; more than half of those finds
    (55 of 99) are then refused. Of those 55: 25 by the panel cross-check, 15
    because the shot had no release track at all (a separate failure from a
    missing panel reading -- `long_split` requires a release unconditionally),
    13 because the delivery track never reached (or reached only
    non-increasingly) the far hog line, and 2 by the speed-tolerance veto.
    Panel-only coverage recomputed on the same inputs is 28.4% (59/208, not
    the 46% the design doc previously quoted from memory -- see below).
  - **27 of the 44 published splits (61%) were never cross-checked against
    anything.** The cross-check only runs when the panel's own tripwire has a
    reading (`panel is not None`); when it does not, `long_split` skips the
    check entirely and publishes uncorroborated. Only 17 of 44 (39%) were
    ever compared against an independent reading. This matters directly for
    a detector-replacement decision: on the panel-blind shots the feature
    exists to rescue, the cross-check is inactive by construction, so most of
    today's published coverage carries no corroboration at all.
  - **Two different agreement numbers, two different populations -- do not
    conflate them.** Wherever both a side-view and a panel reading exist,
    whether or not a split was published (n=48, a population that both
    includes shots that can never be published and excludes 27 of the 44
    published splits): median |diff| = 0.261 s, worst 0.787 s, against a
    <= 0.25 s bar. Restricted to the 17 published splits that did have a
    panel reading (post-veto, n=17): median |diff| = 0.052 s, worst 0.231 s.
    The second number is **not independent evidence of accuracy** -- it is
    <= 0.25 s by construction, since a published split's panel reading (when
    one exists) is definitionally the one that survived the cross-check.
  - Hand-mark agreement: 20 of 27 found, worst error 0.619 s among those,
    against a <= 0.1 s bar.

  An earlier version of this risk (and of `task-8-report.md`) said the 57
  crossings `longview` found but did not publish were all vetoed by the
  panel cross-check. That was arithmetically impossible -- only 48 shots ever
  had a panel reading to check against -- and has been superseded by the
  measured breakdown above (25/15/13/2 of 55, not 57). This report does not
  draw a ship/no-ship conclusion from these numbers; that adjudication was
  made on other grounds before this task ran. Full tables in
  `.superpowers/sdd/2026-09-14-long-camera-hog-crossing/task-8-report.md`.
- **The 46% -> 28.4% gap has two candidate causes; only one is measured.**
  Recomputing panel-only coverage across all 13 ends of the identical video
  with the identical current pipeline gives 28.4% (59/208), not the 46% this
  doc previously quoted from memory. Candidate cause 1, **measured**: the 46%
  figure was computed on a hand-picked ~3-end/48-shot subset, not the whole
  13-end game -- a much smaller and differently-composed population, and this
  alone would produce a different number. Candidate cause 2, **not measured**:
  `src/curling_score/game/split.py` (lines 139-144) documents that the
  speed-tolerance check changed form since 46% was measured -- the old
  version compared mean speed over the baseline in real metres against slide
  speed in the panel's own (distorted) units, a mismatch that "threw away
  good splits"; the current version compares two crossing-time speeds read in
  the panel's own units directly. The 28.4% figure runs today's algorithm,
  not the one that produced 46%, so some of the gap could come from the
  check itself changing rather than only from the population changing. This
  was not settled by re-running the original subset: that subset is not
  reproducible from anything in this repository (no script, dataset, or
  commit predating the coverage measurement records which three ends or how
  the 48 shots were chosen; the only hand-identified ends in the repo,
  `datasets/hogmarks/VXU9xwmugRg.json`, are ends 5 and 6, 27 marks, a
  different and smaller set used for a different measurement). Both
  candidates stand; do not treat either as settled on its own.
- **`HOG_APPARENT_Y_M` was measured on sheet 2 only.** It is the panel
  tripwire's constant, so it governs the cross-check rule; if it does not
  transfer, the cross-check will fire spuriously on other sheets. Marking one
  end per sheet settles it and the tool exists.

## Not in scope

- The overhead panels' nonlinear metre scale. Untouched; anything reading a
  distance up-sheet is still wrong.
- The target-end crossing, which the playing panel reads reliably (1 miss in
  48).
- Using the side views for anything else -- trajectory, sweeping, curl,
  hogged-rock detection. The seam does not preclude it; this change does not
  pursue it.
