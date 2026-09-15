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
and the hand marks are matched within 0.1 s. Fail any of those and the seam
means swapping in a trained detector touches `detect/longview.py` alone.

## Known risks

- **Side-view calibration is not yet robust.** On 12-frame plates it fits 7 of
  10 views; sheet 1's left view fits at 40 px against 78-90 elsewhere, and
  sheets 2 and 5's right views find an odd number of ring edges. Production
  would have 24 frames and cleaner medians, so these are plausibly thin-plate
  artefacts -- but that is unproven and is the first thing to settle.
- **The classical detector is measured on one video** (`VXU9xwmugRg`, sheet
  2 -- the only video cached where this was run; the other seven catalogued
  videos live in the worker box's cache). `scripts/split_coverage.py` runs
  the gate above over all 13 ends (208 shots) of that video and none of the
  three bars hold: side-view coverage 21.2% (44/208) against a >= 90% bar,
  panel-only coverage recomputed on the same inputs 28.4% (59/208, not the
  46% the design doc previously quoted from memory -- that figure was
  measured on a hand-picked 3-end/48-shot subset and does not reproduce
  across the whole game), agreement wherever both fired median 0.261 s /
  worst 0.787 s against a <= 0.25 s bar (n=48), and hand-mark agreement 20 of
  27 found with worst error 0.619 s among those against a <= 0.1 s bar. The
  agreement numbers -- a median disagreement already past the cross-check's
  own tolerance -- read as "finding the wrong object" rather than "finding
  the right object and losing some," which is why the trained detector
  behind the `detect/longview.py` seam is the next step rather than tuning
  the classical one. Full tables in `.superpowers/sdd/2026-09-14-long-camera-hog-crossing/task-8-report.md`.
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
