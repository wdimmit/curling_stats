# Release point on the delivery charts

**Status:** approved by the user, 2026-10-22. Decided with the user:
- Mark where the thrower's hand came off the handle on **every delivery chart**, labelled **"Release"**, nothing more for now.
- Read it with the hand-on/hand-off model trained on the user's own marks (below), not the pose model.
- **Reuse the frames the worker already decodes.** No new decode of the long camera.
- **The rock's dots change colour at the release**: one solid colour before, another after, everywhere the delivery dots are drawn -- the charts, the speed line under the large view, and the burned-in game clips. The colour ramp by distance goes.
- **Shipping waits for a fresh check**: 20 rocks of a Super League game not in the training set, marked by the user, scored against the retrained model; it ships if at least 15 of 20 are within 0.5 m, and below that the user decides.
- Later the user may fit an additional line from this point; that is not part of this work.

## Why

The delivery chart shows the rock from the hack to 1.5 m past the hog line, a dot every frame. Players read it to see what the hand did to the rock before it let go, and that question needs the moment of letting go on the chart. A first pass with a COCO pose model (the wrist rising off the handle) turned out to be late against hand marks: about half of throwers open the hand and let it hover over the handle while they slide, so the wrist never moves when the hand comes off.

## What the spike found

Work in `~/curling-work/body-spike` (scripts) and the 3070 laptop's `/data/wdd/scratch/body` (data, runs), 2026-10-21/22.

**Ground truth.** `scripts/mark_release.py` serves a scrubber over the camera facing the thrower, cropped to follow the rock, from the tee to a second past the delivery path, with nothing a model saw drawn. The user marked the first frame the hand is clearly off the handle on 220 rocks from 11 games, 6 of them Tuesday Super League (`datasets/releasemarks/`, README there). 20 rocks marked a second time blind came back a median **0.06 m** from the first mark (max 0.38 m).

**The model.** A ResNet-18 (ImageNet start) classifies each frame "hand on" or "hand off":
- Input: the rock-centred crop 0.5 m either side of the rock, 0.55 m above and 0.12 m below its ice point, at 160x107 px (about the camera's own resolution there), grey, three frames stacked as channels (t-4, t-2, t at 30 fps), upsampled 2x to 320x214, normalised (x - 0.45) / 0.225.
- Labels: frames before the mark "on", from it "off", two frames either side left out.
- The release is the best single on-to-off step through the per-frame probabilities: the frame k minimising sum(p before k) + sum(1 - p from k). A step at the very end means "still on".
- Trained with shift, flip and brightness augmentation, 5 epochs (`hand_data.py`, `hand_train.py --mode stack --scale 2`).

**How good it is** (each game held out of training in turn):

| | median miss | within 0.5 m | within 1 m | off by > 1 m |
|---|---|---|---|---|
| Pose model (wrist rule), 10/20 game | 0.70 m | 44% | | |
| Super League games, no league data in training | 0.27 m | 66% | 86% | 14 of 99 |
| **Super League games, other league games in training** | **0.22 m** | **83%** | **95%** | **5 of 99** |

Red rocks read a little worse than yellow (0.24 against 0.20 m median). The model's own confidence does not flag its big misses: most are confidently wrong, so a drawn range would not contain the truth. Single frames, colour, ensembles and a temporal-convolution sequence model were all no better.

**Cost** (laptop, 12 rocks): the model is 0.05 s a rock on the GPU at half precision (1.85 s on CPU only); cutting crops 0.06 s; a fresh decode of the frames would be 0.54 s, which this design avoids.

**The spike's weights** are trained on all 217 marked rocks: laptop `/data/wdd/scratch/body/hand/sl/hand_stack_x2.pt` (45 MB), copy in `~/curling-work/body-spike/hand/sl/`. They are **not** the weights that ship (next section).

**The spike's crops are not production's.** Its training crops were cut from the marking tool's saved frames: upscaled to 400 px/m (bicubic), saved as JPEG (quality 88), cropped, shrunk to 160 px, then greyed by averaging RGB. Production would cut from the raw decoded frames at the camera's own scale, greyed as luminance (`bench_release.py` does). A yellow handle greys quite differently the two ways, and nobody has measured accuracy on production-style crops.

## Goals

1. For every rock with a delivery path, on VOD, live and practice runs, read the release and store it in the timeline.
2. Draw it on every delivery chart, and colour the rock's dots by before/after the release there and in the burned-in clips.
3. Ship the model the way the side, broom and spin models ship: pinned weights in the worker image.
4. Make training repeatable from the repo plus kept data, so more marks can retrain it.

## Non-goals

- The additional line fit from the release.
- A distance in the label ("1.2 m before the hog line").
- Any hog-line-violation judgement. A late release is shown where it was read and nothing is said about it.
- Per-player release statistics or reports.
- Reprocessing hosted charts. Old charts show no marker until the user requeues them.

## Anti-goals

- **No new decode** of the long camera, and no measurable slowdown of a live end: the target is about 0.1 s a rock.
- **Attach-only**, like `hogtime`, `linetime` and `spintime`: the release read may add a field to a shot. It can never add, drop or renumber a shot, or move any other number. A failure leaves the rock without a release and is logged.
- **Not a hog-line judgement.** Nothing on the page frames a release past the hog line as a violation.

## The read (worker)

**Frames.** The long camera facing the thrower (`hogtime.CAMERA_FOR[OTHER_HOUSE[house]]`) is already decoded at 30 fps over the whole view twice per rock:
- `hogtime`'s crossing read: about 1.0 to 6.5 s after `t_release` (`longview.WINDOW_S`), which covers the rock from about 2 m past the tee to well past the hog line;
- `linetime.read_delivery`: from 3 s before `t_release` to just past the band's first sample (capped at +5 s), covering the hack to about 4.5 m.

Together they cover the tee to the end of the delivery path, which is the span the model reads. Neither keeps pixels today. The crop is centred on the rock's position from the delivery path, which exists only once `linetime` has built it, after both reads, and full frames are about 280 MB a rock. So something small has to be kept from those reads for the release read to cut its crops from. *My suggestion:* each read keeps, per rock, a grey strip of the lane at the camera's own scale (rows from about 0.6 m above the tee's row to below the hog +1.5 m row, columns over ±1.5 m of the centre line), a few MB a rock, dropped once the release is read. The builder chooses the mechanism and must measure the memory held per end, with up to three live streams on a worker.

**Span.** From the rock 0.5 m behind the tee to the delivery path's last sample. The marking window started at the tee and five marks sit 0.09-0.31 m past it, on its 2nd-4th frames, so a few releases may come at or just before the tee; a step on the read's first few frames is dropped as unseen (*my call*). The crop follows the path, interpolated per frame, placed as `mark_release.extract` places it.

**One way of cutting crops (my call): production's.** A single function cuts a rock's crops from decoded frames (or the kept strips) at the camera's own scale, greyed one fixed way, and both the worker and training use it. The model is retrained on crops cut that way from the marked games' videos and rescored one game held out at a time before it ships; the spike's weights do not ship. The laptop holds its own copies of all 16 marked games' recordings (`/data/wdd/scratch/body/videos`), so the worker's 21-day recording expiry does not bite; keep those copies until the production-style crops are cut and kept (below).

**Model.** The stack x2 ResNet-18 above, on the worker's GPU at half precision, one batch per rock. The model file goes in `weights/` with an `ARG` in `Dockerfile.worker` and a `weights.py` name and env var, as `SPIN_MODEL` does. A side-camera model does not change the API's `MODEL_ID`. Without a GPU (local runs), the read may be skipped rather than spending ~2 s a rock (*builder's call*).

**Output.** A new optional field on the shot's line, the rock's point on the delivery path at the release, in the delivery's own convention: `line.release = [t, y, x]`, t seconds from `t_release_s`, y house metres from the destination tee, x metres to the thrower's right (*my call*; the builder may add the model's version). Absent when there is no delivery path, when the read fails, or when the step fit says the hand was still on at the path's end. Additive, so no schema bump (*my call*); a viewer that does not know the field ignores it. `PIPELINE_VERSION` moves.

**Practice.** Practice throws (`practice/throws.py`) build their delivery the same way; they get the release too if the same pass runs for them, and their record carries the field through to the practice page.

## The charts (viewer and site)

Every chart that draws a delivery path draws the release on it:
- the phone's Delivery tab (`DELIVERYBOX`), the desktop detail card (`DESKDELIVERYBOX`), the line chart's close-up (`LineChart`), the large Delivery view (`BIGDELIVERYBOX`, `TABLETDELIVERYBOX`), and the practice page (`site/Practice.jsx`) -- a marker on the path at the release, labelled "Release";
- the My shots overlay (`deliveryOverlay`, `site/ShotCharts.jsx`), which lays many paths over each other -- a small marker on each path, *without* labels, so the spread of release points shows (*my call*). The API needs no change: `timeline.for_player` passes a rock's `line` through whole, so the new field reaches My shots by itself; the site's path mapping picks it up.

Also in the large Delivery view: a matching vertical line on the speed chart under the delivery, labelled "Release", at the same distance (*my call*).

**Dot colours (the user's decision).** The path's dots stop shading by time or distance (`DELIVERY_RAMP`, `rampColor`, `rampSpan` in `core/delivery.mjs`, used by the dots and by the speed line's segments in `core/speed.mjs`). Instead:
- dots up to the release in one solid colour, dots from it on in another, so the change of colour shows the release by itself;
- the speed line in the large view coloured the same way, split at the same moment;
- *my call* for the colours: the ramp's own ends, navy `#2d1e6b` before and gold `#a07a00` after (gold is already what the strip draws the rock's path in), and the ramp's middle teal `#1f7a8c` for every dot of a rock with **no** release read (old charts, a failed read), so no chart implies a release it doesn't have;
- the legend becomes two swatches, before and after the release, and the captions and aria labels that say "dark at the push-off to gold past the hog line" / "darker earlier" say what the colours now mean (wording the builder's).

**Burned-in game clips.** `game/clipburn.py` draws the delivery dots into a shot's ⋯ Clip… video in the chart's ramp (`RAMP`, `ramp`). It takes the same two colours split at the release (teal without one), so clips and charts agree. No "Release" text in the video (*my call*): the colour change marks it. Clips already cut keep their old look; new cuts, and backfills the user asks for, get the new one.

The marker's look is the builder's, within the chart's existing style: it must be told apart from the path's dots and from the start ring, read in both orientations (upright on the phone, on its side on desktop), and fit the narrow charts. Geometry belongs in `core/delivery.mjs` (and `core/speed.mjs`), tested under node, as the rest of the delivery geometry is.

## Training, kept repeatable

- Move `hand_data.py`, `hand_train.py` (and `score_marks`-style scoring) from the spike into the repo (`scripts/release/`), cutting crops with the production function above, so `datasets/releasemarks` + the videos reproduce the model. `mark_release.py` stays the marking tool only.
- Live recordings expire after 21 days, and most marked games are live recordings. Keep the production-style crops (about 1-2 GB) on the training box (`/data/curling/release/`) so a retrain does not depend on videos that are gone.
- A new marking round = cut frames for new rocks, the user marks, append, retrain, rescore one game held out at a time.

## Checking it before it ships

- Every marked game's recording that still exists: run the pipeline and compare `line.release` with the marks. The shipped model was trained on those rocks, so this checks the plumbing (crops and timing match the spike), not the accuracy; expect much better than the held-out numbers. A mismatch beyond a frame or two means the production crop differs from the training crop.
- **The ship gate (the user's):** after the retrain, the user marks 20 rocks of a Super League game that is not in the training set (one already played will do, e.g. 10/20 S5 Hamzeh v Yalowicki, `s_0hx8wuVKeMmy4evaI`), scored the same way. **At least 15 of 20 within 0.5 m ships; fewer goes back to the user.** These marks join `datasets/releasemarks` afterwards.
- Full-game A/B on the worker (`run_ab.sh`): only `line.release` differs; per-end time measured with `liveend_t.py` on the Ryzen outside league windows.
- The charts: headless phone and desktop checks (devserve + `cdp.mjs`) of a chart with and without a release.

## Left to the builder

- How the two reads hand their pixels to the release read, within the memory limit.
- The marker's look.
- Module layout (`game/releasetime.py` alongside `spintime.py` is the obvious home) and where it runs in `analyze.build_one_end` (after `linetime`).
- Batch sizes, precision, and what a read logs.
