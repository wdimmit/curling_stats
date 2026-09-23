# Phase 3 of a throw: the far hog line, placed from the paint

## Why

A throw is four physical events: it leaves the hack (stage 1), crosses the
throwing end's hog line (stage 2), crosses the destination hog line (stage 3),
and comes to rest or leaves play (stage 4). A hog-to-hog split is stages 2 and
3. Stage 1 was redesigned in `2026-09-19-throw-stage-1-design.md`; stage 2
comes from the long camera (`game/hogtime.py`). This spec is stage 3.

Stage 3 is timed by a tripwire in the destination house's overhead panel:
`split.HOG_APPARENT_Y_M = 4.441`, one number in the panel's own coordinates for
every panel of every video. It fails in two ways, and the second is worse.

**It misses crossings.** On three games (`AEqLTgM25Tc`, `VXU9xwmugRg`,
`hOKZoeJNTpM`, 329 throws) 69 throws lose their split at the far end. Every one
of the 74 missing far crossings has the same cause: the panel first sees the
arriving stone already past the tripwire. The panels see only 0.12-0.23 units
past 4.441 (AEqL's top panel, 1.10, is the exception), and 63 of the 74 misses
are red stones -- `ds11a` picks red up late at the panel's far edge, while
yellow is usually acquired before the line. The red stones are plainly visible
in those frames for most of a second before the first detection.

**It is in the wrong place, differently on every panel.** Hand-marking the
destination crossing in the overhead panel (below) showed the paint sits
anywhere from 4.39 to 4.75 in panel coordinates, not at 4.441. So even the far
crossings the tripwire *observes* are biased, by up to half a second:

| game | panel | tripwire minus hand mark | paint (stone centre at the mark), panel units |
| --- | --- | --- | --- |
| AEqLTgM25Tc | top | +0.574 s | 4.745 (2 interpolable) |
| AEqLTgM25Tc | bottom | -0.148 s | 4.387 |
| VXU9xwmugRg | top | +0.372 s | 4.554 |
| VXU9xwmugRg | bottom | -0.061 s | 4.427 |
| hOKZoeJNTpM | top | +0.169 s | 4.513 |
| hOKZoeJNTpM | bottom | -0.174 s | 4.396 |

Top panels fire late and bottom panels early on every game, with no dependence
on stone colour (median -0.03 s red, -0.02 s yellow). A marking habit cannot
produce opposite signs on the two panels. Within one panel the paint position
barely moves (spread 0.008-0.024 units), so one line per panel is enough.

The likely reason, not confirmed: 4.441 was measured by comparing side-camera
hand marks against panel tracks, before it was known that the composite's
camera pairs run out of step by up to half a second. The marks below are read
in the overhead camera itself, so they share the tripwire's clock and carry no
such offset.

## Measurement behind this spec

All on the three games above, replayed from the detection caches on the GPU
laptop, `main` at the stage-1 merge.

**Hand marks.** Committed in `datasets/hogmarks/`:
`hOKZoeJNTpM-receiving.json` (8: 5 rocks the tripwire misses, 3 controls) and
`receiving-controls.json` (57 controls, 54 marked, 3 skipped as unreadable).
Each is the frame at which an arriving stone's leading edge first touched the
red hog line, read in the destination overhead panel with `scripts/mark_hog.py`'s
page. A control is a rock the panel first sees at least 0.05 units before the
tripwire, so its tripwire crossing is observed. The long camera was tried first
for marking and abandoned: from the throwing end, sweepers' feet stand between
it and the stone.

**The paint is findable.** In each panel's calibration median, pixels with
`r - max(g, b) > 25` and `r > 80` (the frames are BGR) within 140 rows of the
panel's far edge give the line's outer edge in 127-212 of about 300 columns;
a quadratic in column fits them with 0.52-0.65 px scatter.

**And it predicts the marks.** The stone's centre at the mark sits a nearly
constant distance beyond the paint's outer edge -- 0.063-0.100 units by panel,
median 0.080 -- about a stone's radius at that scale. Timing each track across
paint + 0.080, against the marks:

| tripwire | median error | p90 | within 0.10 s | within one frame |
| --- | --- | --- | --- | --- |
| today, 4.441 | 0.175 s | 0.548 s | 9 / 57 | 4 / 57 |
| paint outer edge + 0.080 | **0.049 s** | **0.147 s** | **41 / 51** | 17 / 51 |

Per panel the residual median is within +-0.03 s except VXU9 (+0.080 bottom,
-0.054 top).

**Correcting the line exposes crossings that were never observed.** Of 329
arrivals, 231 bracket 4.441 but only 207 bracket the paint line; the loss is on
the top panels where the paint sits highest (AEqL 44 -> 24, VXU9 27 -> 16).
Those were being timed at the wrong place -- the +0.37 to +0.57 s above.

**Extrapolation, scored against the marks.** Eleven marked rocks are first seen
past the paint line. Extrapolating back to it with today's four-point linear fit
(`split.far_crossing`) and with a quadratic on eight points:

| reach cap | n | linear: median | linear: worst | within 0.10 s | quadratic: median | quadratic: worst |
| --- | --- | --- | --- | --- | --- | --- |
| 0.15 u | 4 | 0.069 s | 0.154 s | 3/4 | 0.094 s | 0.163 s |
| **0.20 u** | **8** | **0.057 s** | **0.154 s** | **6/8** | 0.084 s | 0.163 s |
| 0.25 u | 10 | 0.072 s | 0.278 s | 7/10 | 0.084 s | 0.381 s |

The quadratic's apparent advantage in an earlier test came from scoring against
the panel's own crossing, which is noisy at the frame edge; against human truth
the linear fit is better, and past 0.20 units the first bad case appears.
0.20 units is roughly two feet of ice at that scale.

**Expected result.** Under this design, 285 of 329 throws get a far crossing
(207 observed, 78 extrapolated) and about 278 have both crossings for a split,
before the pairing check's refusals -- against 242 splits today.

**Rejected on the way.** Extending the 4.441 tripwire's extrapolation (45% within
0.15 s at the reach needed); timing the far line with the long camera (finds
the crossing on 251 of 329, but on AEqL only 55 of 90, one hand-marked case
0.88 s wrong, and sweepers make it very hard to read); continuing tracks
backward through sub-threshold boxes (weak same-colour boxes exist above the
line on 31 of 74 misses, but made one marked case 0.44 s worse and none better).

## Decisions taken with the user

- Ship the corrected tripwire **together with** short extrapolation, in one
  change, so correcting the line does not cost splits.
- The tripwire is **found in the paint per panel**, as a curve read at the
  stone's own lateral position, not one number per panel and not fitted from
  hand marks or the long camera.
- Both hog lines are timed at **the stone's leading edge first touching the
  paint**.
- **No fallback to 4.441** where the paint cannot be found.

## The definition

A split runs from the stone's leading edge first touching the throwing end's
hog line (its inside edge, `TEE_TO_HOGLINE_M = 6.401` from that tee -- which is
what `split.py` records the phase-2 hand marks as, and so what the long camera
was validated against) to its leading edge
first touching the destination hog line (its outer edge, 6.401 +
`HOGLINE_WIDTH_M` = 6.503 from that tee).

The leading edge covers `TEE_TO_TEE_M - TEE_TO_HOGLINE_M - (TEE_TO_HOGLINE_M +
HOGLINE_WIDTH_M)` = **21.843 m**, which replaces `BASELINE_M`'s 22.229. That
constant feeds only `Split.speed_m_s` and the pairing check, not the published
seconds.

In a panel, the tripwire at lateral position `x` is the paint's outer edge at
the column `x` projects to, plus `LEADING_EDGE_OFFSET_U = 0.080` toward the
direction the stone arrives from: the tracked centre of a stone whose leading
edge is on the paint.

## Architecture

| unit | responsibility | depends on |
| --- | --- | --- |
| `geometry/hogpaint.py` (new) | Find the hog line's outer edge in a panel's calibration median as a quadratic in panel column. `HogLine.y_at(x_m)` returns the tripwire at a lateral position. Raises `HogPaintError` with a reason when the paint cannot be trusted. | the median image, `PanelCalib` |
| `game/profile.py` | `calibrate_panel` already builds the median; it also finds the line. `PanelSetup` gains `hog_line: HogLine | None` and `hog_line_error: str | None`. | `hogpaint` |
| `game/fartime.py` (new, mirrors `game/hogtime.py`) | Per shot: the far crossing against the destination panel's line (observed, else linear four-point back-extrapolation up to `FAR_REACH_MAX_U = 0.20`), its reach, and the panel speed there; and from the throwing panel's line the release track's own crossing and speed. Attached to the shot, never altering the shot list. | `split`, both panels' `HogLine` |
| `game/split.py` | Crossing and speed functions take a `HogLine`, evaluated at each sample's lateral position. `HOG_APPARENT_Y_M` and `FAR_EXTRAPOLATION_MAX_U` are deleted. `BASELINE_M` = 21.843. `long_split` takes the far crossing as keyword-only arguments **with no default**. | -- |
| `analyze.py` | Runs `fartime` after `hogtime`; publishes each panel's line (coefficients, columns found, scatter) or its error in the timeline's calibration block. | -- |
| `timeline.py`, `game/classify.py` | Both `long_split` callers pass the shot's far-crossing values through `fartime`'s accessors. | -- |
| diagnostic scripts | `split_coverage.py`, `split_audit.py`, `render_split.py`, `ds13/review_no_far_hog.py`, and the untracked `render_phase1.py`, take the line from the panel setup. | -- |

**Why the far crossing rides on the shot.** `long_split` has two production
callers, `timeline.build_end` and `classify_shot`, and the second only ever
sees a shot. The long-camera merge updated one caller and missed the other,
which silently made `DRAW_THROUGH` unreachable (fixed in `366ba20`). Attaching
the crossing to the shot, as `hogtime` already does for stage 2, gives every
caller the same values; making the arguments required turns a forgotten caller
into a `TypeError` instead of a missing split.

**Why `geometry/calibrate.py` is not touched.** The detection cache hashes that
file (`detect/cache.py::_SOURCE_MODULES`); editing it would send every cached
video back through the GPU. `PanelSetup` is not in the hash, and the cache key
reads only its `rect` and `calib`, so a new field leaves every cached detection
valid. The line is panel-relative, so `analyze._proxy_setups` carries it
unchanged.

## Data flow

1. Calibrate: per panel, the median image -> `PanelCalib` (unchanged) and
   `HogLine` or `None` with a reason.
2. Proxy setups carry the line unchanged.
3. Per end, after the rules settle the shots: `hogtime` attaches the stage-2
   crossing; `fartime` attaches the far crossing, reach and far panel speed from
   the destination panel's line, and the release's panel crossing and speed from
   the throwing panel's line.
4. `timeline.build_end` and `classify_shot` call `long_split` with the shot's
   values.
5. The calibration block publishes both panels' lines.

## Failure handling

| situation | result |
| --- | --- |
| Destination panel's paint not found: fewer than `MIN_COLUMNS = 80` red columns, fit scatter above `MAX_SCATTER_PX = 1.5`, or the outer edge at the centre column outside `4.0 <= y <= 5.0` | No far crossings on that panel, so no splits for rocks arriving there. The reason is in the calibration block. |
| Throwing panel's paint not found | No `panel_delta` and no panel speed at that line; the pairing check uses the side view's `v_hog`, a path that already exists. |
| Arrival first seen more than 0.20 units past the line, or fewer than four track samples | No far crossing. |
| Extrapolated far crossing | Published with its reach in `long_split_far_reach_u`, which already exists. |
| A hog line of another colour | Not found, as above. Not supported until a video needs it. |

The bounds come from the measurement: good panels gave 127-212 columns and
0.52-0.65 px scatter; the outer edge sat at 4.32-4.66 units.

## Versioning and deployment

`version.PIPELINE_VERSION` is bumped. The stage-1 merge (`db182cf`) also changed
what timelines say -- more releases, splits and thinking times -- without
bumping it, so the bump covers both, and `dedupe.find_reusable_run` stops
handing back runs from before either.

Every game must be reprocessed for its splits to change. Split values move by
up to about 0.57 s on some panels, so published numbers will visibly shift.
Existing `/c/` charts stay pinned to their old runs; `/g/` links pick up the new
ones.

## Testing

**Unit tests, synthetic:**

- `hogpaint` recovers a known red curve drawn into a synthetic panel to within
  0.5 px, with noise and occluding blobs, for both panel orientations; raises on
  too few columns, a line outside the band, and excessive scatter; finds a red
  line in a BGR image and ignores a blue one (the channel order this work's own
  first probe got wrong).
- `split`: crossing a curved per-position line; the reach cap either side of
  0.20; `BASELINE_M == 21.843`; `long_split` called without the far-crossing
  arguments raises `TypeError`.
- `fartime` attaches observed and extrapolated crossings and their reach, and
  attaches nothing when a line is `None`.
- A shot carrying the far crossing produces a split through both
  `timeline.build_end` and `classify_shot`.

**Acceptance against the hand marks, in the repo, no video.** A committed
fixture holds each of the six panels' hog-line band cropped from its
calibration median, and the arrival track of each marked rock. The test runs
`hogpaint` on the crops, times each track against the line found, and scores
against `datasets/hogmarks`:

| | measured | test threshold |
| --- | --- | --- |
| observed crossings: median error | 0.049 s | <= 0.06 s |
| observed crossings: within 0.10 s | 80% | >= 75% |
| extrapolated (reach <= 0.20 u): median error | 0.057 s | <= 0.08 s |
| extrapolated (reach <= 0.20 u): worst | 0.154 s | <= 0.20 s |

**Replay check, manual.** Replay all three games on the GPU laptop, one at a
time, and report splits per game against today's 242, the observed /
extrapolated split, and how far each existing split's value moved. A report for
the user, not a gate.

## Known risks

**Small samples.** The 0.20-unit cap rests on 11 extrapolated cases and the
0.080 offset on 51; all from one club, three sheets. Marks from other sheets
would firm both up.

**The offset may want to vary.** Panel medians of the offset run 0.063-0.100;
VXU9's residuals (+0.080 s, -0.054 s) are the largest. Sizing it from each
stone's own detected box, rather than one constant, is the obvious refinement
if the acceptance test or a new sheet shows the constant is not enough.

**The median image must show the paint.** A parked stone or a player standing
on the line in most calibration frames would hide columns; the column minimum
and outlier rejection are the defence, and the reason is published when it
fails.

**The late red detections remain.** 63 of 74 misses are red stones `ds11a`
acquires late at the far edge. Extrapolation covers most of them; it does not
make the detector see them.

## Not in scope

**Retraining `ds11a`** on red stones at the panel's far edge -- the root cause of
the misses. The frames exist (`labelset_phase3/overhead-*`) and are good hard
positives; it is its own piece of work.

**Stage 2 and stage 4.** Stage 2's gates (`longview.SPEED_BOUNDS_M_S`, the
unconditional `ambiguous` refusal) are deferred by the user's choice; stage 4 is
unchecked and unmeasured.

**The long camera at the destination end.** Measured and rejected above.
