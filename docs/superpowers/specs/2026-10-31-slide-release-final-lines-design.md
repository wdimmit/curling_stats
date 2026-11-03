# Slide, release and final lines

2026-10-31. Branch `line-windows`, to land through a PR.

## Why

Today a delivered rock has one line: a straight fit to the far camera's track
6.40-10.0 m past the throwing tee, extended to the skip's broom (`line.miss_m`,
the "Line" / "At the broom" figure). The user wants to see where the throw went
wrong, which needs three lines read at the broom:

- **Slide line**: where the slide was aimed.
- **Release line**: where the release sent the rock.
- **Final line**: where the rock was travelling once free of the hand.

Each is shown against the broom. Slide against release is shown too: how much
the release itself moved the line (PR #2 called this the "set").

PR #2 (leilani-oti, "Slide, release and hog-to-hog figures") computed a slide
line and the set in the browser (`frontend/core/slide.mjs`) for the practice
card only. This branch replaces it: the lines are computed once in the
pipeline, stored in the timeline, and shown on both the game shot detail and
the practice card. PR #2 is expected to be closed in favour of this.

## Definitions (the user's)

Distances are metres past the throwing tee along the sheet (the timeline's own
frame is metres from the destination tee, `y = TEE_TO_TEE_M - d`).

| Line | Window | Data |
| --- | --- | --- |
| Slide | from halfway between the hack line and the back line (d = -(3.658 + 1.829) / 2 = **-2.743 m**) to the **release point** | delivery path, 30 fps |
| Release | the first **9 ft (2.743 m) past the release point** | delivery path, continued by the hog-line track where the window runs past the delivery path's end, 30 fps |
| Final | the first **9 ft past the hog line**: d = 6.401 to **9.144 m** | the hog-line track (as today), 30 fps |

- **Release point**: unchanged (`line.release`, `game/releasetime.py`).
- **Actual path and curl**: unchanged (camera behind the thrower, 5 fps, 1 s
  past the hog line to 1 s past the rest).
- **Line check** (`linetime.confirmed_by`, `CONFIRM_*`, `line.confirmed`):
  deprecated. Stop computing it and remove its tick and note from the viewer.
- **Curl, and wide/narrow, stay measured against the final line.** The final
  line keeps today's role and fields; only its window shrinks.
- **Release vs final is not a figure.** It would mostly read early curl and
  noise.

A rock with no release point read has a final line only: no slide line, no
release line, no set.

## Goals

1. Each rock with a release point carries a slide line and a release line in
   the timeline (and the practice throw record), each extended to the broom
   (miss, wide/narrow) and, for a broomless doubles rock, pinned where it
   crosses the destination tee (as the final line does today with `at_tee`).
2. The final line uses the 6.401-9.144 m window.
3. The game shot detail (`frontend/core/line.mjs` `lineFigures`, rendered by
   `frontend/viewer/Detail.jsx`) and the practice card
   (`frontend/site/Practice.jsx`) show Slide, Release and Final at the broom,
   plus slide vs release when it is real (see "Set shown").
4. The line check is gone from the pipeline and the viewer.
5. A one-off rotation study (below) reports whether a 9 ft window past the hog
   line could replace today's rpm. Research only; nothing ships from it.

## Non-goals

- No change to the release point, the delivery path, the actual path, the
  hack call, weight, or rotation as shipped.
- No backfill: hosted charts are not reprocessed by this branch. Reprocessing
  is the user's call at deploy time.
- No new camera reads beyond what the windows need (see Constraints).

## Anti-goals

- A slide or release figure shown where its fit is too thin to trust. Missing
  is better than wrong (the rule the spin gates follow).
- Old charts showing different numbers for the same rock without a reprocess.
- The game page and the practice card disagreeing on a rock's figures: both
  read the same stored numbers.

## Decided

- **Slide/release/final are computed in Python** and stored on the line, not
  derived in the browser. PR #2's `slide.mjs` is not merged. Its card layout is
  reimplemented to read the stored figures.
- **Timeline schema 9 -> 10**, `PIPELINE_VERSION` bumped. The final line keeps
  today's field names (`at_broom.miss_m`, `at_hog`, `side`, `curl`, `fit`,
  `at_tee`), so stats, My Shots, the reports and the ice report keep working.
  Their numbers for new charts move slightly because the window is shorter.
  New fields hold the slide and release lines. Builder's call on the exact
  shape. Suggested: `line.slide` and `line.release_line`, each
  `{at_broom: {x, miss_m} | null, at_tee: {x} | absent, side, fit: {n, rms_m, se_broom_m}}`.
  `line.release` stays the release point.
- **`line.confirmed` is dropped** from new timelines. The viewer must still
  render older timelines that carry it, ignoring it.
- **Charts made before the change** keep their Line figure as it is and show
  no slide or release figure. The figure list must not show empty Slide and
  Release slots on them that read as failures; say "predates" or leave them
  out (builder's call; matches how the viewer treats pre-feature charts today).
- **Live games** get the new lines too. The same pass runs live and post-game.
- **Practice**: `practice/throws.throw_record` carries the same new fields, and
  `practice/enrich.py`'s stages produce them in the same order as games.
- **PR #2's practice card features are carried over too (user, 2026-10-31).**
  Reference: PR #2's head `3212ead8`, local branch `pr2-merge`, mainly
  `frontend/site/Practice.jsx`, `service/static/site.css`, `frontend/core/slide.mjs`
  and `tests/test_slide_js.py`. Reimplement these on the card, under the clip:
  - the figure grid in place of the rows of text (two by two on a phone);
  - **Hog to hog**: the weight, shown only for a rock that arrived;
  - **Came to rest**: the ring and the distance from the tee;
  - **Sensor short** and **Sensor long**: the sensor app's times, on a
    session the app sends to;
  - the throws table's Line column in inch marks (`9"`), agreeing with the
    card's figure.

  The slide/release/set numbers come from the stored lines, not from
  `slide.mjs`'s browser-side fit. The table's Line column is the final line,
  the same as the game page's "At the broom". Credit the contributor
  (leilani-oti) in the PR description.

## When the final window can't fit (Claude's call)

Today a rock whose `fit_line` fails gets no `line` at all, and so loses its
delivery path, release point, start (the hack call), path, and curl. The final
fit is also used inside the pass: `chain` seeds the path behind the thrower
from it, and `thrown_at` picks among several held brooms with it, which sets
`target_broom`. To keep the non-goals intact:

- **Today's 6.40-10.0 m fit stays as the pass's internal line.** It decides
  whether a rock has a `line` at all, seeds `chain`, and drives `thrown_at`.
  Nothing outside the line figures moves.
- **The 9 ft final fit is fitted alongside it** and supplies the reported
  final-line figures: `at_broom`, `at_hog`, `at_tee`, `side`, `curl`, `fit`.
- When the 9 ft fit fails and the internal one doesn't, the rock keeps its
  line. Its final-line figures are null, and the viewer shows "–" with a reason
  ("too few sightings past the hog line"). This state needs its own marker so
  that it isn't confused with `at_broom: null`, which means "no broom held".
  Builder's call on the shape. The A/B reports how many rocks land here.

## Set shown (Claude's call)

Slide vs release (`set`) is release-at-broom minus slide-at-broom, signed
toward wide. It is shown only when it exceeds 2 standard errors of the
difference. Each line's standard error at the broom starts from its own fit
(residual rms, n, and the extrapolation distance, as `slide.mjs`'s `fitAt`
did), replacing PR #2's fixed `SIGMA_R_IN = 1.5"`, whose backing spec was
never supplied. Consecutive 30 fps frames are not independent, so that formula
understates the error. Correct it, for example with an effective sample count
from the residuals' autocorrelation, or a floor, and calibrate the correction
on the A/B's rocks. Also set a ceiling: a Slide or Release figure whose
standard error at the broom exceeds it is not shown ("–", with a reason).
Choose the ceiling from the A/B's distribution and report it. Inches rounded to whole numbers, "to the broom" within 1"
(PR #2's `ON_BROOM_IN`). Keep PR #2's wording conventions (`9"`, wide/narrow,
`+7"` / `-7"` for the set) unless the builder finds the game page's existing
`feetInches` convention must win for consistency there. On the game page,
match the page's existing convention and note the choice.

## Constraints and facts the builder needs

- **Order of passes.** `linetime.time_lines` runs before
  `releasetime.time_releases`, which needs the delivery path and then attaches
  `line.release` with `dataclasses.replace`. The slide and release lines need
  the release point, so they are computed after `time_releases` (in it, or in
  a small pass right after it). In practice, `enrich.Stages` has the same
  order.
- **The release line needs the 30 fps track past the delivery path.**
  `delivery_path` stops just past `DELIVERY_END_M` (hog + 1.5 m = 7.90 m,
  nothing past `DELIVERY_KEEP_M` 8.2 m). A release at 5.5 m needs data to
  8.24 m, and a late release more. Continue it with the hog-line track
  (`hog_track`, 30 fps). `Line.hog_path` is thinned to 0.5 m (`thin`), so it
  is not enough. The raw track must be kept on the in-memory `Line` (not
  serialised) or the release line fitted inside `time_lines` once the release
  point is known. Watch the seam between the delivery path and the band
  (`_seam_offset` exists for this).
- **Minimum fit sizes (Claude's call, builder may tune with data):** the slide
  needs at least 8 points over at least 2.0 m (PR #2's values). Release and
  final need at least 15 points over at least 2.0 m. Today's final uses 15
  points over 2.5 m, but the new window is only 2.743 m long, so 2.5 m would
  refuse rocks whose band starts a little late. Report how many rocks each
  gate refuses in the A/B.
- **Known risk: the slide window starts where PR #2 went wrong.** On
  off-centre brooms, PR #2's slide figure read 55-95" off on 3 of 4 fixture
  rocks. The first ~2 m out of the hack moves sideways about three times faster
  than later, and extending that 30 m explodes. The cause is undetermined:
  either the real slide arc or the long camera's lateral calibration near the
  hack (`overhead-panel-scale` and `long-camera-as-a-ruler` notes). The new
  window starts at -2.743 m, inside that stretch. The A/B must report slide
  miss against broom offset.
- **Phasing (Claude's call).** Build and A/B the pipeline first; the viewer
  comes after. If slide reads track the broom's offset from centre as PR #2's
  did, hold back the slide line and the set: don't show them in the viewer, but
  keep computing and storing them so they can be studied. The release line,
  the 9 ft final line, and the removal of the line check go ahead. Bring the
  numbers to the user, with one quick check of the cause using the long camera
  as a ruler on a few off-centre rocks, before doing any more on the slide.
- **Shared checkout.** Another session works in `/home/tcuser/src/curling_score`.
  All work stays in `.claude/worktrees/line-windows`. Subagents must never
  write outside it.
- **Tests**: `tests/test_linetime.py`, `tests/test_releasetime.py`, the viewer
  JS tests (`tests/test_viewer_js.py` and friends), `tests/test_timeline*.py`,
  the practice tests. The full suite OOMs on this laptop; run the affected
  subset.
- **Bundles**: the viewer and site bundles are built
  (`frontend/build.mjs` -> `service/static/`, `.buildstamp.json`) and committed.

## Validation

1. **A/B on cached games** (no new VOD downloads), base = main, new = branch,
   with full `analyze()` runs on the worker (`run_ab.sh`), run outside league
   hours. Use at least two this-season fours games with release points,
   including one with off-centre brooms. Candidates: 10/20 S2 Grause v Dimmit
   `dmljQzQr1xo` (s_1TH0, has hand marks) and 10/06 Tuesday Super League
   `2rOCyiAiroU`. Check that they are cached first. Report:
   - Final line: per-rock change in miss vs today's (expect a few cm toward
     the hog-line heading, larger on light rocks), and rocks gained or lost
     by the gates.
   - Release and slide lines: how many rocks get each, fit rms, standard
     error at the broom, and the distribution of slide/release/final misses.
   - Slide miss vs the broom's lateral offset (the PR #2 risk above).
   - Only `line` fields differ in the timeline, apart from known segmentation
     jitter.
2. **Practice**: one cached practice session through `enrich` with the
   branch, and the card checked at phone width (headless check).
3. **Viewer**: the game shot detail and practice card checked at phone and
   desktop widths on one new-schema and one old-schema chart.

## Rotation study (one-off)

**Question**: is a rate fitted over the first 9 ft past the hog line
(d = 6.401-9.144 m) a usable replacement for today's rpm, which comes from the
first ~1.25 turns read from max(8 m, hog + 0.5 s) on (`spintime`, `spinfit`)?

**Expectation to test, stated up front**: a draw covers 9 ft in about 1.2 s.
At 10-15 rpm that is 0.2-0.3 of a turn, under `HELD_MIN_TURNS` (0.3), below
which even a range isn't trusted. The handle-point model was also trained
mostly on crops from 8 m on, and at 6.4-8 m the stone is smaller in camera A's
frame. The study should find out, not assume, how many rocks it can read.

**Method**:
- Games already cached on the workers. No downloads. Prefer games with eye-read
  rocks (PHbZ3EKOhMI from the spin spike) plus two or three this-season
  fours games.
- Read camera A from the hog crossing to d = 9.144 m at 30 fps (the window is
  short, so take every frame) with the shipped spin model (`weights/spin1.pt`),
  following the rock as `spintime` does.
- Fit with the shipped fits (`spinfit.fit`, `fit_held` with the end's swing),
  applying the shipped gates. Record: read / range / refused, turns in view,
  rate.
- Compare with the same rocks' shipped `spin.rpm` (the production read on the
  same video): count readable, median and p90 relative difference, by weight
  band (split time) and colour.
- Spot-check a handful by eye (contact sheets), especially disagreements.

**Output**: a short report (markdown or a page) with the counts, the
agreement, and a recommendation. Scripts live outside the repo
(`~/curling-work/spin-9ft/`) unless the user asks to keep them. No
production change.

## Frame rate (user, 2026-10-31)

Wherever a window comes up short of readings, raising its frame rate is an
accepted fix. Examples: the fit minimums refusing rocks, the rotation study's
short window, or the extend read (`EXTEND_FPS`). Measure the added cost per
end on the worker and report it with the change.

## Left to the builder

- Module layout (a new `game/aimlines.py` vs extending `linetime` /
  `releasetime`), exact field names, and how the raw track is carried.
- Figure order and wording on both pages, within the constraints above. Slide,
  Release and Final sit together; Weight, Rotation, Curl and Came to rest keep
  their places.
- Whether `at_hog` (offset from the hack-to-broom line at the hog line) stays
  as today, read from the final line. Default: yes, unchanged.
