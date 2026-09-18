# ds13 — candidate frames from the side views

**Status:** revised 2026-09-15 after the first extraction attempt produced an
unusable set. The original design and what it got wrong are kept below, because
the mistake is more instructive than the correction.

## Why

The classical side-view detector in `detect/longview.py` finds 20 of the 27
hand-marked hog-line crossings, worst error 0.619 s, against a bar of all 27
within 0.1 s. It is being replaced by a trained detector behind the seam
`game/hogtime.py` establishes — in production that seam is one symbol wide,
`hogtime`'s `find=`.

A trained detector needs frames. This says which, where they come from, and
what a person draws on them.

## What the first attempt got wrong

The first design scanned ds11's clip archive **blind**: 1197 clips, no shot
list, every moment of every clip a candidate, binned by where a detected blob
sat relative to the hog line.

It produced 2,864 frames before anyone looked at them. Every one was unusable.
Two independent causes, both in the design:

**Position without travel.** The spec said "stratified on position along
travel"; the implementation binned *position* and never required *travel*.
`longview.candidates` has no motion or direction filter — it finds coloured
blobs with granite-ish bodies — so a stone at rest, a player's shoe, or a
broom head was filed as "crossing" purely by where it sat in frame.

The arithmetic was in `datasets/ds11/README.md` the whole time: a delivery
arrives about every 60 s and crosses in about 4 s, so a 24-second clip holds
one only about 47% of the time, and even then it is 4 seconds of 24. **About
97% of sampled moments contained no delivery at all** — and the measured
outcome tally agreed exactly: 85 of 2,864 frames came from a window where any
crossing was found.

**Off-ice furniture proposes.** The racks of stones stored beside the sheet are
in frame in every frame of every clip, and they are red. That, not any
imbalance in play, is why the pool ran 2,584 red against 256 yellow — a
stationary rack re-detected 120 times a clip.

Neither was caught by five rounds of review, because both are properties of
*what the frames contain*, and every test in the plan ran against synthetic
fixtures. **Nothing looked at a real frame until a person did.**

## The correction: let the shot list say when to look

The pipeline already knows when a delivery happened. `scripts/split_coverage.py`
walks a cached video — profile, segment games and ends, detect shots per end —
and yields, per shot, the release time, the arrival time, the stone's colour,
and which end is throwing. `game/hogtime.CAMERA_FOR` says which side camera
watches that end's hog line, and the window arithmetic says when.

**Every candidate frame comes from a window where a delivery is known to be in
flight, in the camera that can see it, with the colour known from the shot.**

That is ground truth about *existence* that no detector can supply, and it is
precisely what the blind scan threw away. It also turns the detector's failures
into the most valuable frames in the set: when the shot list says a delivery
was there and the classical detector found nothing, that frame is exactly what
the replacement has to learn, and we can now say so with confidence rather than
hoping.

### Source

The nine full VODs cached on the worker at `/data/wdd/curling-cache`, with warm
detection caches. Not ds11's clips.

**This is narrower than the original design and the debt is real.** Nine videos
against 120 across 24 dates and five sheets. `datasets/ds11/README.md` is
explicit that narrow sets are how this project produced detectors that scored
well and saw poorly. The decision is to train a *focused* model first and
diversify in a later wave; that is a deliberate trade, recorded here so the
next wave is a plan rather than a discovery.

### Frames cluster at the line

Almost all frames come from within a short distance of the hog crossing, with a
few from the approach. The model's job is to time that crossing, so that is
what it is shown. A set spread evenly across the flight would train a better
tracker and a worse timer.

Where a track exists, frames are chosen by `|edge_row - hog_row|`. Where the
detector found nothing at all, frames are taken from the window's centre
anyway, carrying **no proposed box** — the shot list says a stone was there, so
the frame is kept and a person draws it.

### Detection is confined to the ice

Proposals come only from the ice surface. The stone racks, benches and
scoreboard sit outside it and must not be able to propose a box.

## What a person draws

**A tight box around the stone, its bottom edge at the ice** — unchanged. The
contact row is the only one the perspective solve turns into a distance, so
making it the label's own quantity trains the model on the number the pipeline
reads.

## Split

Whole videos held out, never frames. With nine videos the split is coarse and
must be stated as such in the README.

## Scope boundary

This produces frames and a manifest. It does not produce the labelling tool,
and it does not train anything.

## What survives from the first attempt

`harvest/sideviews.py` — calibration, verified against 225 of 240 real views,
unchanged. `harvest/sideframes.py` — the quotas, the caps, the two halves and
the shortfall reporting, unchanged: it selects from whatever pool it is given.
`harvest/sidepool.py`'s blind clip scan is what is replaced.
