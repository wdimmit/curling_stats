# ds11 — a season of games, labelled by hand

A few frames from **every game of the 2025-26 Tuesday Super League** — 120 VODs
over 24 dates and five sheets — auto-labelled by `weights/ds10_stratified.pt`
and then reviewed frame by frame by a person.

It exists because ds1–ds10 share two problems that no amount of retraining
fixes. Every label was written by the colour detector, or by a model trained on
the colour detector, so validation mAP measures agreement with a known-flawed
opinion (`validation/EXPERIMENTS.md`: ds3 scored mAP50 0.977 while finding 11 of
17 hand-observed deliveries against another model's 16). And the frames are not
diverse: ds10's 6,995 frames are two seconds apart inside twenty ends of five
games.

ds11 changes both. Breadth comes from sampling every game in a season instead of
five. Honesty comes from a person looking at every frame — which also makes the
val split the project's first detection benchmark that means anything.

## What is in here

| file | what it is |
|---|---|
| `videos.json` | the season: every game, its date, sheet, resolution and split |
| `manifest.json` | every selected frame, its bin, and what each video could not supply |
| `edits/*.json` | the human corrections, one file per review session |
| `_formats.json` | what YouTube offered per video when the plan was made |

`videos.json` and `manifest.json` are the dataset. The images are derived and
live on the box with the GPU; these files plus the cached clips rebuild them.

## Rebuilding it

```bash
export PYTHONPATH=/data/wdd/curling/repo/src
cs() { /data/wdd/curling/.venv/bin/python -m curling_score.cli "$@"; }

cs harvest clips  --videos datasets/ds11/videos.json --root /data/wdd/curling/ds11/clips
cs harvest pool   --videos datasets/ds11/videos.json --root /data/wdd/curling/ds11/clips \
                  --out /data/wdd/curling/ds11/pool --weights weights/ds10_stratified.pt --device 0
cs harvest select --pool /data/wdd/curling/ds11/pool --manifest datasets/ds11/manifest.json
cs harvest build  --manifest datasets/ds11/manifest.json \
                  --pool /data/wdd/curling/ds11/pool --out /data/wdd/curling/ds11/set
cs labels /data/wdd/curling/ds11/set --split train --apply datasets/ds11/edits/*.json \
          --keep-empty --drop-unreviewed
```

Each stage reads a file and writes a file, and each is safe to run again.

## Decisions, and why

**Clips, not videos.** 120 VODs of four and a quarter hours is 364 GB to keep a
couple of thousand frames. Ten half-minute clips per video is about 9 GB, and
the clips are kept — they are the archival source, so the set rebuilds without
touching YouTube. ds8 could not be rebuilt at all.

**`yt-dlp --download-sections` is not used.** It drives ffmpeg without
`-copyts`, so every section reports `start_time=0.000000` and carries an
unrecorded keyframe lead of up to five seconds. The clip forgets where in the
VOD it came from, which breaks both the review deep-links and reproducible frame
names. We resolve the media URL and cut it ourselves. Verified: clip frames are
**bit-identical** to the same absolute moments in the full video.

**720p games are dropped.** Two Tuesdays — 10/14 and 10/21, all five sheets —
were streamed at 720p where the rest of the season is 1080p. At 720p a stone is
about 14 px across against 20 px: a different detection problem wearing the same
clothes. They are listed in `videos.json` under `dropped`, so the decision is
one parameter away from being reversed.

**The split is by date, not by video.** Two sheets of the same night share ice,
lighting and often teams, so a val video whose sibling is in train is not the
unseen game it looks like. Five whole Tuesdays are held out, spread from October
to March, covering all five sheets.

**Frames are chosen to span three things at once.** How full the house is,
whether a stone is moving, and when in the night it happened. The bin edges
follow a measurement rather than a guess: over 423 candidates the distribution
is sharply bimodal — 44% hold no stones (one house is always idle) and only 2.7%
hold ten or more, which is why "busy" is seven or more and not ten.

**Empty frames are included on purpose.** Every dataset up to ds10 skipped them
by construction, so the model has never once been shown a frame whose right
answer is "nothing". A detector calling a frame empty means only that it found
nothing; a person calling it empty is an assertion, and that is what makes the
negative worth having.

**Stones in flight are sought, not hoped for.** Delivery recall is the
pipeline's limiting factor, and `BACKLOG.md` records that the old quiet-span
filter deleted in-flight stones — a quarter of its removals were travelling
down-sheet — so "a missed delivery deletes exactly the frames that would teach
the model to catch it". Clips are half a minute because a delivery arrives about
every 60 s and crosses the panel in about 4 s, so a window of length W holds one
with probability roughly `(W+4)/60`. Measured yield: **7.1 flights per video**.

**Only reviewed frames enter the set.** `--drop-unreviewed` removes anything
nobody ticked. An unreviewed frame still carries whatever the detector said,
and letting those through silently is what would make the whole exercise
circular again.

## What the harvest found

**114 of 120 videos are usable; 6,400 candidate frames.**

The six that are not are all the same thing, and it is not a bug: on those
nights **only one of the two overhead cameras was feeding**, so the lower half
of the composite strip is a blank grey rectangle. `layout.detect_panels`
reports "expected 3 horizontal bars, found 2" and refuses rather than guessing,
which is the behaviour the README argues for — reading a band of clean ice as a
separator once put the bottom-panel crop inside the top panel.

    2025-12-09  all five sheets   Bs_z89y-hi8 G_3D95BIbK8 563I0SvTZsM
                                  dCviTBb5X-E 0M6_9wD3ofE
    2025-12-16  sheet 4           H00-hfFV7oI

All six are train videos, so the val split is untouched. They were not chased:
114 videos already yield 6,400 candidates against a budget of under 2,000
frames, so recovering five more would mean relaxing a safety check to gain
nothing.

It is worth knowing outside ds11 though. **`analyze` cannot read these VODs
either** — a single-overhead night is a real thing the club does, roughly 5% of
the season, and the pipeline currently fails on it rather than reading the one
house it can see. That is a candidate for `BACKLOG.md`.

## What the pilot measured

All 551 wave-1 frames were reviewed by hand: 64 auto-labels rejected, 57 added,
**5.5% of labels corrected**. ds10 has seen none of these 120 videos, so for
measuring *it* the ds11 train/val split is irrelevant and all 551 frames are
held out.

`weights/ds10_stratified.pt` overall: **mAP50 0.9900, mAP50-95 0.9519,
P 0.9808, R 0.9482**. Its mAP50-95 against its own circular val was 0.921, so it
does not do worse on unseen games — it does better.

That single number hides a fourfold spread:

| bin | frames | labels | err/label | mAP50-95 | P | R |
|---|---|---|---|---|---|---|
| empty | 114 | 0 | **0.0%** | — | — | — |
| sparse (1-3) | 112 | 224 | **11.2%** | 0.9215 | 0.9813 | **0.9078** |
| medium (4-6) | 111 | 551 | 7.1% | 0.9501 | 0.9886 | 0.9433 |
| busy (7+) | 103 | 875 | **2.6%** | 0.9656 | 0.9815 | **0.9869** |
| motion | 111 | 561 | 6.1% | 0.9464 | **0.9630** | 0.9455 |

Four things follow, and three of them contradict what the design assumed.

**Empty frames taught nothing.** 114 of them, and not one correction: ds10 put
no box on any empty house, and no missed stone was found on one. The negatives
are *safe* but they are not *informative*, and at 21% of the pilot they were the
single largest waste of review time. The quota should be near zero.

**Sparse frames are the worst, and that was not predicted.** A house holding one
to three stones loses **9% of them** (recall 0.908) — four times the loss on a
crowded house. The intuition that a lone stone against clean ice is the easy
case is wrong.

**Busy frames are the best.** Recall 0.987 on 875 labels in packed houses,
which is the clearest vindication yet of detecting the handle rather than the
granite: the touching-stone merge that dominates the literature simply does not
happen here.

**Motion frames have the worst precision** — 0.963 against 0.988 for static, on
561 labels, so roughly three times the false-positive rate. On the val split
alone (130 labels) this looked like noise; at 4.5x the sample it does not.

The two weaknesses point at the pipeline's actual complaint. A missed guard is a
missed delivery; a phantom stone in flight is a phantom delivery. Neither shows
up in an overall mAP of 0.99.

Caveat that still stands: the labels began as ds10's predictions and a person
changed 5.5% of them, so whatever the reviewer also missed still counts as
agreement. Every frame was looked at, which makes this far weaker circularity
than ds1-ds10 had, but it is not zero.
