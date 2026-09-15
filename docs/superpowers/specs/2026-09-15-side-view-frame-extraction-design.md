# ds13 — candidate frames from the side views

**Status:** approved 2026-09-15. Queued behind Task 8 of
`docs/superpowers/plans/2026-09-14-long-camera-hog-crossing.md`.

## Why

The classical side-view detector in `detect/longview.py` finds 20 of the 27
hand-marked hog-line crossings, worst error 0.619 s, against a bar of all 27
within 0.1 s. Neither candidate fix closed the gap, so it is being replaced by
a trained detector behind the seam `game/hogtime.py` already establishes —
`hogtime` imports `longview` only for `WINDOW_S` and the default
`find=longview.find_crossing`, so in production the swap is one symbol wide:
point that `find=` at the trained detector.

It is not a *file*-wide swap, and this document originally said it was. This
plan itself is the reason -- `harvest/sidepool.py` borrows eight symbols from
`detect/longview.py` and `harvest/sideframes.py` pins its refusal quota to
`longview.KEYS`, because mining that detector's refusals is the whole point of
ds13. The classical detector has to keep existing for this dataset to be
rebuildable. The trained one goes in beside it, not over it.

A trained detector needs frames. This document says which 600, where they come
from, and what a person will draw on them.

## The trap this design is shaped around

`datasets/ds11/README.md` records why ds1–ds10 are worth less than their
numbers suggest:

> every label was written by the colour detector, or by a model trained on the
> colour detector, so validation mAP measures agreement with a known-flawed
> opinion

and cites ds3 scoring mAP50 0.977 while finding 11 of 17 hand-observed
deliveries.

Selecting 600 frames by *where the classical detector fired* walks straight
back into it. The frames a replacement most needs are the ones this detector
fails on, and those are precisely the frames it would not propose. So the
detector is used as a **proposer, never as the selector**: half the set is
chosen by what the scene contains, and the other half is chosen by the
detector's *refusals*, deliberately over-weighted toward them.

## Source

ds11's clip archive on the worker: **1197 clips across 120 videos, 24 dates,
five sheets, 1920×1080 full-frame**, at `/data/wdd/curling/ds11/clips`. ds11
cut those clips for the overhead detector but kept the whole composite, so
both side views are already in them. Nothing is fetched from YouTube and no
VOD is re-decoded.

ds13 reuses `datasets/ds11/videos.json` verbatim — the same season, the same
five held-out dates — so any later comparison between an overhead model and a
side-view model is on the same split rather than two that merely look alike.

## Shape

Four resumable stages, each reading a file and writing a file, mirroring
`harvest/stages.py`:

| stage | in | out |
|---|---|---|
| `views` | ds11 clips | `sideviews.json` — per video, both views calibrated, or why not |
| `propose` | clips + `sideviews.json` | `pool/candidates.json` + crops |
| `select` | pool | `datasets/ds13/manifest.json` |
| `build` | manifest + pool | the YOLO tree on the worker |

Images live on the worker as ds11's do. The manifest, the calibration report
and the README come back into git, so the set rebuilds from the archive
without anyone's scratch directory
([[curling-score-tmp-data-loss]]: ds8 cannot be rebuilt at all).

### views

Build a clean plate per video from one frame per clip — the median removes
players and stones and leaves the paint — then `sideview.locate` and
`sideview.solve` for each of the two views.

**A video whose side views will not calibrate is reported, not skipped
quietly.** That list is itself a finding: `PLAUSIBLE_ROWS`, `_GREEN_THRESHOLD`,
`_HOUSE_SEARCH` and `_HOG_SEARCH_PX` were measured on ten views from five
sheets, and 120 videos is the first real test of whether they generalise.

### propose

Scan each clip's two side views with the classical detector at reduced frame
rate, for both stone colours, recording per moment: whether a candidate was
found, its trailing-edge row against `view.hog_row`, its body width, how
crowded the band around it is, and — where the detector refused — which gate
refused it.

### select

600 frames in two halves, quotas fixed in advance, shortfall recorded beside
them rather than padded:

**~300 scene-stratified**, by view × where the stone sits along its travel
(approach / crossing / past the line / nothing there at all). This half does
not care whether the detector succeeded.

**~300 by detector outcome**, over-weighted toward refusals: each refusal
reason gets a quota, with a smaller control group of crossings the detector
did find, so the set contains both what it gets right and what it does not.

Both halves are capped per video and per clip, so no single night dominates
and near-duplicate frames from one 24-second clip cannot fill a bin.

### build

Crops match the band `longview.decode` already uses — the whole side-view
rect — so training geometry and inference geometry are identical rather than
nearly so.

## What a person draws

**A tight box around the stone, its bottom edge at the ice.**

The thing that failed here was never finding the stone; it was locating the
stone's contact with the ice, which is the only row the perspective solve
turns into a distance. A box whose bottom edge is the contact line makes that
row the label's own quantity, so the model is trained on the number the
pipeline actually reads.

Where the classical detector found a candidate, the manifest carries its box
as a **proposal for a person to correct**: left and right from the granite
body's widest dark span, bottom from the sub-pixel trailing edge, top from the
colour handle's own top row. Every one of those is measured from the pixels,
not derived from an assumed stone size. Frames the detector refused arrive
with no box at all, which is the honest starting state for them.

## Split

**The train/val split holds out whole videos**, inherited from ds11's five
held-out Tuesdays. Frames from one 24-second clip are near-duplicates of each
other; splitting at frame level would leak and produce a validation number
that means nothing — the other half of what ds11's README is complaining
about.

## Scope boundary

This produces frames and a manifest. **It does not produce the labelling
tool.** `train/review.py` is a *correction* workflow — it reviews proposed
labels — and roughly half of these frames are ones the detector refused, so
they arrive with nothing to correct. Drawing boxes from scratch is a different
tool, scoped separately rather than discovered half-built.

It also does not train anything. Training, evaluation and the swap behind the
`hogtime` seam are later work.

## Known risks

**The calibration may not generalise past five sheets.** Ten views calibrate
today (80.70–90.30 px tee-to-hog). 120 videos is 240 views. The `views` stage
reports every failure rather than dropping it, so the answer is a number in
`sideviews.json` rather than a silence.

**ds11's clips carry no shot list**, so no clip is *known* to contain a
delivery. Neither half needs one: the scene half is chosen by what is in the
band, and the refusal half by which gate refused — both are properties of the
footage, not of a throw having been charted. The eight catalogued videos do
have shot lists and, for two ends, hand marks, but they are not in ds11's 120
and wiring them in is a second source for a set that does not yet need one.
If the harvest comes back thin, that is where the next frames come from.

**The proposer is the detector being replaced**, so its refusal reasons are
its own opinion. They are used to *stratify*, never to decide what a frame
contains — that is what the person drawing the boxes is for.
