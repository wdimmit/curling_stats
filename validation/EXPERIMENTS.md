# Training experiments

Four models trained overnight on 2026-09-08/09, all judged against
`ground_truth.json` — the observations made by eye — rather than validation
mAP. That distinction is the single most important thing on this page.

## Validation mAP is not a measure of anything here

`ds3` scored **mAP50 0.977, mAP50-95 0.900**, better than the model in use, and
found **11 of 17** observed deliveries where that model finds 16. It also let a
confirmed phantom back in.

The metric is computed against labels the classical detector wrote, so a model
scores better by reproducing that detector's mistakes more faithfully. Without
the seventeen observations it would have shipped as an improvement.

## The four runs

| | labels | R8Y8 ends | board g1 | observed deliveries | phantoms |
|---|---|---|---|---|---|
| in use | classical, no filters | **9/13** | **4/7** | 16/17 | 5/5 |
| ds3 | + quiet cleaning + edge clipping | 5/13 | 3/7 | 11/17 | 4/5 |
| ds5 | union + quiet + play span + gap fill | 7/13 | 3/7 | 16/17 | 5/5 |
| ds6 | ds5 + edge clipping | 7/13 | 3/7 | 16/17 | 5/5 |

Nothing beat the model already in use.

### Settled: edge labels make no measurable difference

Boxes that run off the panel edge were being dropped, which excluded every
stone within ~0.14 m of the boundary from training — and a stone against the
edge can still be 1.84 m from the tee, inside the house and counting. Clipping
them adds 2034 labels. ds5 and ds6 differ by those labels and by nothing else,
and score identically on every measure.

Two guesses about these labels were wrong: first that clipping was a clear win
(ds3 said no), then that ds3's regression came from keeping the clear-up
(measurement said no — only 322 of 4486 edge detections are in the clear-up).
It is simply a wash. Clipping is left **on** for the multi-sheet set, because
if it costs nothing then labelling a countable stone is the more correct
choice.

### Settled: the union restores recall but costs end structure

Labels are now the union of the classical detector and the model. Over 3133
sampled frames the model finds 369 stones the classical detector misses (6%,
matching the rate of missing labels found by eye) and misses 141 it finds.

The union takes ground-truth recall from ds3's 11/17 back to 16/17. But ends
at exactly R8Y8 stay at 7/13 against 9/13 for the model in use. The reason is
measured below, and it is not the one that seemed obvious: the union leaves the
fitter with fewer candidates, not more.

## Filters added, and what each was worth

- **Quiet-span cleaning.** Between one stone settling and the next being
  thrown, nothing on the ice moves but people, so anything that moves is
  provably not a stone. Removes ~4% of boxes in those spans.
- **Play span.** Training frames now stop before the last delivery of an end.
  The clear-up mislabels in both directions at once — a player in a yellow
  jacket as a stone, real stones in the house missed — and the last delivery
  cannot be told apart from the clearing that follows it.
- **Gap filling.** `MAX_FILL_GAP_S` was 0.5 s against a 2 fps build, so a
  single dropped frame was a 1.0 s gap and **nothing was ever filled**:
  0 of 24 gaps over 300 s. Now 1.5 s.

## Cross-sheet: the plan's top risk looks smaller than feared

The pipeline had only ever run on sheet 2. Run across all five, picking four
ends per video and both houses:

| video | sheet | deliveries |
|---|---|---|
| EznLfUsF57w | 5 | 64/64 (100%) |
| wVkiErqeKBc | 3 | 59/64 (92%) |
| Y1VZCk9tIsg | 2 | 57/64 (89%) |
| QnWHfqaLzzc | 4 | 57/64 (89%) |
| 13REHKrIE9I | 1 | 55/64 (86%) |
| | | **292/320 (91%)** |

Sheet 5 gave four consecutive perfect ends; sheet 1, the widest camera with the
strongest distortion, still gave 86%. This is the *pipeline* generalising, not
the model — panel detection, calibration, segmentation, delivery detection and
the sixteen-shot fit all transferred untouched.

## ds7: trained on four other sheets, tested on this one

11 of the 17 observations sit on frames every model above was trained on
(training split: game 1 of the primary), so those numbers flatter. `ds7`
trains on sheets 1-4 -- 36916 labels over 6523 frames, four ends a video and
both houses -- and holds the primary out entirely. Sheet 5 is the validation
split, also unseen.

| | in use | ds5 | ds6 | ds7 |
|---|---|---|---|---|
| game 1 kept | 109/112 | 108/112 | 106/112 | 108/112 |
| game 2 kept | 93/96 | 86/96 | 88/96 | **91/96** |
| board g1 | **4/7** | 3/7 | 3/7 | **4/7** |
| multiset g1 | **5/7** | 4/7 | 4/7 | **5/7** |
| final g1 | 4-5 | 4-7 | 6-4 | **5-5** |
| R8Y8 ends | **9/13** | 7/13 | 7/13 | 7/13 |
| observed deliveries | 16/17 | 16/17 | 16/17 | **16/17** |
| observed phantoms | 5/5 | 5/5 | 5/5 | **5/5** |

A model that has never seen this video matches the one trained on it, on board
agreement, on the multiset, and on every observation made by eye. Whatever the
detector has learned is not specific to a sheet.

### It also isolates the R8Y8 regression, which is under-detection

ds5, ds6 and ds7 all reach 7 of 13 ends at exactly R8Y8 where the model in use
reaches 9 -- and ds5 and ds6 were trained on this very video, so the cause is
the labelling and not the training data.

The mechanism is the opposite of what was expected. Counted over all 13 ends:

| | detections | candidates offered | kept | surplus over 16 | R8Y8 |
|---|---|---|---|---|---|
| in use | 595861 | 228 | 202 | 21 | 9/13 |
| ds7 | 591591 | 218 | 199 | 12 | 7/13 |

ds7 makes **fewer** detections, offers **fewer** candidates and carries **half**
the surplus over sixteen -- and still ends up with two fewer complete ends. The
guess was that the union would flood the fitter with phantoms; in fact it
starves it. Ends come out at fourteen or fifteen because the candidates are not
there, not because the wrong ones are.

Which points the next attempt at *recall* rather than precision: the filters
that clean the labels are also removing stones the fitter needed. The quiet-span
rule is the obvious suspect, since it drops anything that fails to hold a
position -- and a stone that is struck and moves does exactly that.

## The quiet-span rule was deleting stones in flight

This is the most useful thing found tonight, and it was found only because the
first explanation was measured and refuted.

Quiet spans are drawn between the deliveries that were **found**. So a delivery
that was *missed* sits inside a span declared quiet -- and its stone, being in
flight, moves, so the rule deletes it as a person.

Measured over all 13 ends, of the detections the rule removed:

| | removed | in runs travelling down-sheet | share |
|---|---|---|---|
| before | 18237 | 4587 | 25.2% |
| after | 10705 | 1936 | 18.1% |

A quarter of the cleaning was erasing stones. Worse, it feeds itself: a missed
delivery deletes exactly the frames that would teach the model to catch it, so
the blind spot is written into the next training set.

The fix refuses to clean a span **too long to be one gap between deliveries** —
`QUIET_GAP_RATIO = 1.8` against the lower quartile of span lengths, the same
ratio `misses.GAP_RATIO` uses to decide a gap is hiding a shot. Not to remove
the cleaning, which would bring back the false positives it was added for: a
player's red shoes, labelled as a red stone in 48 of 55 sampled frames.

That recovers 422 labels on identical frames (`ds8`: 37338 against `ds7`'s
36916 over the same 6523 frames), which makes ds8 vs ds7 a clean test of
whether this is what cost the end structure. It was:

| | in use | ds7 | ds8 |
|---|---|---|---|
| game 1 kept | 109/112 | 108/112 | **110/112** |
| game 2 kept | **93/96** | 91/96 | 87/96 |
| R8Y8 game 1 | 5/7 | 5/7 | **6/7** |
| R8Y8 total | **9/13** | 7/13 | 8/13 |
| board g1 | 4/7 | 4/7 | 4/7 |
| observed deliveries | 16/17 | 16/17 | **17/17** |
| observed phantoms | 5/5 | 5/5 | **5/5** |

422 labels took the observations from 16/17 to **17/17** and game 1's complete
ends from 5/7 to 6/7. ds8 is the first model to find all three hard cases at
once -- the red at 2412 in end 3, the guard at 10061 in game 2 end 3, and the
guard at 871 in end 2 -- each of which some other model was missing.

It is not a clean sweep. Game 2 recall is lower (87/96 against 93), total
complete ends are 8/13 against 9, and **board agreement did not move at all**:
4/7, exactly as before. Better stones have not become better scores, which is
consistent with everything else here -- the scoring errors are in reading the
house, not in finding the stones.

**ds8 is the model to take forward.** Trained on four sheets it has never been
tested on, it beats the model in use on the only measure that is not circular.

## What to do next

1. **Game 2's weaker recall is a red-detection question, and unresolved.**
   Traced: the whole loss is in the top panel (94 -> 88; the bottom panel gains
   one), and within it only game 2's ends -- game 1's top ends are all still a
   perfect 16. On the worst of them, g2e2:

   | | detections | red | yellow |
   |---|---|---|---|
   | in use | 52146 | 32839 | 19307 |
   | ds8 | 48507 | 29170 | 19337 |

   Yellow is identical; red is down 11%. That cascades into pass 1 (17 -> 14)
   and the kept end (16 -> 12).

   It is a straightforward regression, and an argument that it might not be
   was wrong. The reasoning offered was that the model in use detects red 1.7
   times as often as yellow in an end holding eight of each, so it must be
   over-detecting red. That compares *detections over time* against *stones
   thrown*, which are different things: a red stone that sits in the house for
   400 s contributes thousands of detections and a yellow taken out early
   contributes few, so a 2:1 ratio is what eight of each looks like when red
   survives longer.

   Rendered side by side over the same 13 frames (`redab_inuse.jpg`,
   `redab_ds8.jpg`), both models ring the same real stones and ds8 simply finds
   one fewer red per frame -- R3Y2/R4Y2 against R4Y2/R5Y2. Nothing suggests the
   model in use is ringing anything it should not.

   So ds8 trades a red stone in this end for its gains elsewhere. Whether that
   trade is worth making is a judgement about which errors matter, and it is
   the open question.
2. **ds8 replaces the model in use.** 17/17 observations against 16/17, better
   game 1 recall and structure, and it generalises across sheets. Its weaker
   game 2 recall is worth a look before it is relied on.
3. **Blank ends and the house-state rework** are still the largest scoring
   errors and untouched by any of this -- see `BACKLOG.md`.
4. **The other five videos have no observations against them.** Every judgement
   of accuracy still rests on one video; the cross-sheet delivery counts above
   are structural (16 per end) rather than verified by eye.

## ds10: the validation split was measuring one camera

Every mAP figure quoted above -- ds2 through ds9 -- was computed against a
validation split made of four ends of a single video, `EznLfUsF57w`, sheet 5.
It is the worst single sheet to have picked for it: its bottom panel goes dark
when that end finishes early, its calibration residual was the highest of the
five (1.10 cm against 0.02-0.47 cm), its ends are unusually short (144-238
frames against 350-500 elsewhere), and it is where the red-shoe false
positives were found. Worse, the arrangement cannot detect the failure it most
needs to: a model that fits four sheets and generalises badly to a fifth scores
*well* on that split, because the fifth sheet is the one it is scored on.

ds10 holds out one end from every video instead -- and balances the held-out
ends across houses, since top and bottom are different cameras with their own
scale and distortion, so an all-one-house split still measures half the
problem. Same videos, same ends, same strides, same `clip` behaviour as ds8;
only the split assignment changes.

| Sheet | Video | Val end | House | Val frames |
|---|---|---|---|---|
| 3 | wVkiErqeKBc | 1 | bottom | 179 |
| 2 | Y1VZCk9tIsg | 3 | top | 197 |
| 1 | 13REHKrIE9I | 1 | bottom | 191 |
| 4 | QnWHfqaLzzc | 2 | top | 140 |
| 5 | EznLfUsF57w | 1 | bottom | 144 |

train 6144 frames / 31469 labels, val 851 / 3390. Val label density rises from
3.2 per frame to 4.0, which is the sheet-5 sparsity showing up as a number.

### It is not an improvement, and that is the useful part

| | in use | ds8 | ds10 |
|---|---|---|---|
| observed deliveries | 16/17 | **17/17** | 16/17 |
| R8Y8 complete ends | **9/13** | 8/13 | **9/13** |
| board agreement g1 | 4/7 | 4/7 | 3/7 |
| board agreement g2 | -- | -- | 0/6 |
| final score g1 | -- | -- | 5-8 vs 3-3 |
| final score g2 | -- | -- | 6-4 vs 4-4 |

The split changes what can honestly be *measured*, not what the model learns.
Its only causal route into the weights is which checkpoint is called "best",
and that moved things slightly the wrong way. Worth having anyway: the old
number was misleading, and this one is not.

The delivery ds10 loses is `g1e4 red t=3152` -- the red takeout that the
yellow-jacketed sweeper runs alongside, 1.4 s before phantom #1 at 3153.9.
ds10 correctly rejects the sweeper and loses the real stone with it;
`MIN_SEPARATION_S = 10.0` leaves the fitter no way to keep one and drop the
other at that spacing. That single shot is the whole gap between 16/17 and
17/17.

**Board agreement has never moved.** 4/7 through every variant from ds2 to ds9,
and 3/7 here. Ten dataset variants have failed to shift it, which is strong
evidence the bottleneck is not the labels and not the detector but the reading
of the house -- the rework in `BACKLOG.md`. Game 2 is the sharpest version of
this: board 0/6 while the multiset is 5/6, meaning the *set* of end scores is
nearly right and the attribution to end numbers is wrong. And game 1 computes
13 points against the board's 6, which is too many stones counted per end
rather than anything to do with deliveries.

## The red shoes are already fixed, and confidence is not a lever

`ground_truth.json` records the shoes as detected at 0.50-0.83 confidence,
tracked continuously while the person moves. That describes the model in use at
the time, not ds10. Tracking every red object through the confirmed
no-delivery window 8113-8139 s with ds10 (261 frames, conf 0.30):

|  n | x range | y range | travel | conf med | what |
|---|---|---|---|---|---|
| 261 | -0.02..-0.02 | 1.27..1.27 | 0.01 | 0.964 | real stone, stationary |
| 261 | 0.53..0.53 | 0.37..0.38 | 0.01 | 0.950 | real stone, stationary |
| 261 | -0.44..-0.42 | 0.78..0.83 | 0.05 | 0.946 | real stone, stationary |
| 1 | -0.30..-0.30 | 1.42..1.42 | 0.00 | 0.355 | the only spurious box |

The shoes now produce one box at 0.355 across 26 seconds. The added training
data worked.

A caution for anyone repeating this: a window with no *deliveries* still
contains resting stones. Counting raw red detections there gives 915 and reads
as rampant false positives; 783 of them are three real stones seen in every
frame. Cluster by position before drawing a conclusion.

**Confidence cannot separate false boxes from stones here.** Shoes and stones
overlap almost entirely (median 0.949 against 0.961); at conf >= 0.90 you keep
781 of 783 false boxes and lose 25% of real stones. Future false positives have
to be answered with training data or geometry, never a threshold.

### Two gaps left open deliberately

1. **The YOLO path dropped the classical detector's physical priors.**
   `rocks.py` gates blob area to 0.15-4.0x the *expected* handle disc at that
   sheet position and applies a circularity test. `yolo.py` filters only on
   sidelines and same-colour separation. Restoring the size gate needs no
   retraining and would reject anything the wrong apparent size for its
   distance up-sheet, which is what a shoe at 3 m is.
2. **The training set contains no negative frames.** `write_split` keeps only
   frames holding at least one stone, by explicit design. That is defensible
   for recall, but it means the model is never shown a frame whose right answer
   is "nothing" -- the signal that suppresses false positives.

Neither is worth doing until a false positive costs something.

## ds8 could not be reproduced, and three explanations were wrong

The reboot that cleared a wedged GPU also cleared `/tmp`, taking every dataset
and ds9's 12 epochs. Rebuilding ds8 from the surviving proxies produced a
different dataset: only 70 of 284 hand-corrected frames still existed, so ds9
could not be rebuilt from it.

Three explanations were offered and each was wrong:

1. **The `clip` flag.** ds8 was built as `build_multi.py <out> clip`, and the
   rebuild had it hardcoded off. True, but worth +21 labels of 7002 on sheet 3
   -- 0.3%, not the 12% gap.
2. **Calibration drift.** ds8's setups came from `prep_videos.py`, which
   derived them from the full-resolution sources and pickled them before
   deleting the videos. The pickles died with `/tmp`; setups derived from the
   proxies differ on all 10 panels (centre up to 1.0 px, scale up to 0.03
   px/m). Real, but fetching the sources back and deriving setups exactly as
   ds8 did still picks ends 1,3,5,7 for sheet 3 where ds8 used 1,2,5,6.
3. **Calibration recovered from the detection caches.** The caches store pixel
   *and* metre coordinates, so the calibration is algebraically recoverable,
   and 8 of 12 panels matched a cached calibration to 0.00 px. Circular: every
   one of those cache files was written by that same rebuild hours earlier.
   Zero predate the reboot.

What is established: `picked = ends[::len(ends)//4][:4]` makes the entire
dataset a function of how many ends segmentation finds, so anything that shifts
one end boundary reshapes the whole set. That is fragile independent of this
incident and worth replacing with an explicit end list before the next dataset
is built. `dataset.py` was also modified at 07:16, after ds8 was built at
02:14, so the frame-writing logic is not what it was either. And the evidence
being reasoned from is unreliable: `ds8-train-label-edits.json` contains 74
`EznLfUsF57w` stems, which is the *val* video, so the export mixes scopes from
the `localStorage` leak and may hold entries from earlier datasets' reviews.

Reproduction was abandoned after roughly six hours. The question ds9 was meant
to answer -- do hand-corrected labels help? -- is better asked as ds10 plus the
corrections that land, on a split that measures all five sheets.

## Persistence cannot be rescued by flight shape, and it was the wrong suspect

The g1e6 yellow at t=5209 is lost at imgsz 640 and found at 448. It was filed
as "rest confirmation under occlusion": players close over a stone as it
settles, so `_still_there` (which wants the stone visible in 40% of a window
5 s after it stops) fails on real deliveries.

That much is true -- `_still_there` rejects 9 of the 18 hand-labelled real
deliveries -- but two attempts to exploit it both failed, and the second
failure showed the diagnosis itself was wrong.

**Attempt 1: let a long clean flight stand in for persistence.** Across the
ground truth the separation looked exact: every wrongly-rejected real delivery
had run at least 3.34 m without ever moving back up-sheet, and every phantom
that reached the same test either ran under 2 m or backtracked by 30 mm or
more. Implemented as `_watched_flight` (travel >= 3.0 m, backtrack <= 15 mm)
it broke a pre-existing guard,
`test_a_long_track_that_leaves_nothing_behind_is_not_a_delivery`, which builds
a perfectly monotonic 3.4 m sweeper track that then vanishes. The guard is
right and the rule is wrong: nothing about flight shape distinguishes a stone
from a person who walked in a straight line. No travel floor separates them
either -- a real delivery at t=5150.3 travelled 3.343 m, below the synthetic
phantom's 3.4 m. Reverted; helper and tests removed.

**Attempt 2: lower the 40% bar.** The hypothesis was that an occluded stone is
seen *intermittently* while a jacket that walks off is seen *not at all*, so
some bar between the populations would keep the guard intact (a vanished track
still scores 0%). `presence.py` measured the visible fraction of the
confirmation window for every labelled candidate. It does not separate: the
real deliveries at t=181.9 and t=5208.9 both score **0.00**, exactly like the
phantom at t=3154.4. There is nothing to lower the bar to.

**What is actually wrong.** Stage traces at both sizes show the 5209 yellow is
produced as a candidate either way (`house-remove`, travel 5.7 m) and dies in
`fit_end`. What differs is a *different* shot: the red takeout at t=5282.

    448:  red track 5282.2-5284.9  n=16  y  2.95 -> -0.85   accepted, house-remove
    640:  red track 5283.1-5284.8  n=10  y  1.45 -> -0.84   rejected

At 640 the model picks the fast-moving takeout up about a second late, so the
track first appears at y = 1.45, below `MIN_ENTRY_Y_M = 2.5`. That routes it to
the late-entry path, whose only test is `_is_new_stone`. Measured, it fails two
of that test's three parts:

    still_there=False   place_was_empty=True   appeared_without_replacing=False

The second is structural. `_is_new_stone` asks whether the sheet *gained* a
stone -- but this is a takeout, and a takeout need not raise its colour's
count. The normal route has `house-remove` for exactly this evidence and
accepts the same shot at 448; the late-entry route cannot express it. So a
track that enters low is judged by a test that a takeout cannot pass.

With the red gone there is no red between the yellows at 5208.8, 5335.7 and
5397.3, and `fit_end`'s alternation constraint must drop one. It keeps the
`house-add` candidate (weight 0.9) and drops the `house-remove` one (0.8).
The yellow is collateral damage from a rejected red.

This is a 640-only failure at present: 448 is the operating point and accepts
both shots. Recorded as a robustness gap in the late-entry route rather than a
live bug.

## Pivot: from scoring to charting (2026-09-10)

The goal changed. The target is no longer "compute the score" but "help a player
chart and grade their own game", the way Curl Coach does: best-effort house
states, explicit blanks where we cannot read one, video at the moment each shot
was *called*, the stone's track drawn on the house, and a coarse auto shot type.

Scoring is explicitly de-emphasised. It was never going to be reliable -- two
independent reasons, both measured here earlier: board agreement ran at 0-4 of
13 ends, and the clearing-time occlusion that breaks the final house is exactly
when the score is decided. Neither is worth fixing for a target that no longer
needs it. The score is still computed and still emitted; it is simply demoted
to a collapsed panel and no longer treated as the acceptance test.

What this reuses rather than rebuilds:

* `shots.from_deliveries` + `rest.stones_in_window` already produced a per-shot
  house. That *is* the charting substrate; it needed no new CV.
* `delivery._house_delta` already computed added/removed per shot and threw the
  result away. Retained, it is the primary evidence for "this was a hit" --
  stronger than any velocity threshold, because a stone that removed another
  did so whatever the tracker thought of its speed.
* The flight was computed inside `find_deliveries` and discarded at the end of
  each loop iteration. `Delivery.track` now keeps it; the detection cache means
  re-emitting timelines with tracks costs no GPU time.

What is deliberately *not* automated: the fine Curl Coach types (peel, freeze,
come around, run back, tick). Those describe the shot that was *called*, and no
amount of tracking recovers intent -- a stone that removes a guard is a peel if
that was the call and a wrecked draw if it was not. The detector offers five
coarse observable categories (draw / guard / hit / through / unknown) and the
charter supplies the rest. `unknown` is a first-class answer, not a failure.

Two ground-truth-adjacent notes carried over from the debugging above:

* `_still_there` rejects 9 of the 18 hand-labelled real deliveries. The pipeline
  survives because other acceptance routes cover, but it is the *only* gate on
  the late-entry route, which is why the 640 red takeout at t=5282 vanished.
  Still open, and now lower priority: the charting UI gives a person a blank to
  fill rather than silently dropping the shot.
* ds10s (yolo11s) scores 18/18 deliveries and 8/8 phantoms against the corrected
  ground truth; ds10 (yolo11n) scores 17/18 at both 448 and 640 -- but misses a
  *different* shot at each size (g1e4 red t=3152 at 448, g1e6 yellow t=5209.8 at
  640). yolo11s wins end-to-end despite worse validation mAP (0.9113 vs 0.9213),
  which is one more datum for the standing finding that mAP against
  auto-labelled data does not predict delivery recall.

## Entry speed does not identify a takeout

`classify.py` shipped with a speed fallback: where the house showed no change,
a stone entering the panel above `HIT_SPEED_M_S = 2.5` was called a missed hit.
The threshold was asserted, not measured -- the docstring claimed draws enter at
1.1-2.0 m/s and takeouts at 2.6-4.5 "with nothing in between". That was written
from assumption and was wrong on every count.

`speeds.py` labelled all 200 accepted deliveries across both games of the
reference VOD by whether the shot moved or removed a stone -- evidence entirely
independent of velocity -- and measured `Delivery.speed_at()` for each:

    house changed    n=82   min 0.20  p10 0.42  med 0.80  p90 1.29  max 1.73
    house unchanged  n=118  min 0.01  p10 0.18  med 0.38  p90 0.52  max 3.29

Two separate refutations:

1. **No separation.** 105 of the 118 unchanged deliveries are at or above the
   slowest changed one; all 82 changed ones are at or below the fastest
   unchanged one. The distributions overlap end to end.
2. **The threshold was above the entire hit population.** Confirmed hits top out
   at 1.73 m/s, so 2.5 could never have fired on a real takeout. It could only
   ever have mislabelled the handful of unchanged tracks reading 2.5-3.3 m/s,
   which are far more likely to be tracking artifacts than shots.

The absolute values were wrong too, and the reason is geometric: the overhead
panel covers only the last few metres before the house, and a stone crossing it
takes 6-8 s to cover ~5 m. Everything is moving at well under 1 m/s by the time
we see it, takeouts included -- they have already shed their weight. The
measurement that should separate weight classes is taken at the one place on the
sheet where the weight classes have converged.

The fallback is removed. Hits are identified by the house alone; a takeout that
missed everything is reported as `draw`/`guard`/`through` on its rest position,
and the charter corrects it from the video. `entry_speed_m_s` is still emitted
and shown in the UI as "Weight", because a person can use a number that a
threshold cannot.

## Blanks: the alternation parity rule beats timing

The first attempt at marking unseen deliveries keyed on the colour sequence
repeating -- two reds running means a yellow went unseen between them. It never
fired once. `fit_end` *enforces* alternation by dropping candidates, so by the
time `from_deliveries` sees a sequence it always alternates perfectly. On the
reference VOD this produced a timeline with **0 blanks across 200 shots**, every
one of the 13 ends looking complete. Four of them were not.

A missed delivery survives `fit_end` only as an end that is *short*:

    g1e1  13 shots     g2e1  15 shots     g2e3  13 shots     g2e4  15 shots

Timing looked like the way to place them, and on its own it is wrong. Gaps
between deliveries, against each end's own median:

    g1e1  one gap at 3.1x median      (shortfall 3)
    g2e3  one gap at 2.7x median      (shortfall 3)
    g2e1  three gaps at ~2.1x median  (shortfall 1)   <-- over-predicts
    g2e4  two gaps at ~2.1x median    (shortfall 1)   <-- over-predicts

Teams stop to confer and to measure, so a long gap is weak evidence. A
gap-driven filler would have invented three blanks in g2e1 and two in g2e4.

The strong constraint is parity, and it is free. The kept sequence alternates,
so inserting a *single* blank between two neighbours is impossible -- it would
have to differ from both, and there are only two colours. Mid-end insertions
must come in pairs; an odd remainder can only belong at the end. That decides
all four ends without appeal to timing:

* shortfall 1 (g2e1, g2e4) -- odd, so it is the last rock. The long gaps are
  pauses, exactly as suspected.
* shortfall 3 (g1e1, g2e3) -- one pair plus one. Timing then only has to choose
  *which* gap holds the pair, and in both ends exactly one gap qualifies.

Result: 8 blanks, matching the 208 - 200 shortfall exactly, alternation intact
and 8 rocks per team in every filled end.

Capped at 4 blanks (`MAX_FILL`). The argument is decisive for a rock or three
and worthless when half an end is missing, where every position would be
invented. Below that the end reports `unplaced_shots` and the viewer warns that
numbering after a gap may name the wrong thrower -- an honest "we lost track"
rather than eight fabricated positions.

The four real ends are now regression fixtures in `tests/test_shots.py`,
including one that asserts timing alone would have got g2e1 wrong.

## Hosting: what the build changed from the plan (2026-09-11)

* **Pinned mtimes, not copied ones.** The plan had the proxy take its source
  video's mtime so a rebuilt proxy would still hit the detection cache. A
  re-downloaded original gets a new mtime too, which would have invalidated the
  cache all the same. Every cached media file now carries one fixed timestamp;
  the cache key's mtime component then only ever changes when the *bytes* do
  (size still participates), which is the property actually wanted.
* **Backoff belongs to whoever schedules.** `ensure_cached` can wait out a
  YouTube block in-process (the CLI does), but the worker asks for a single
  attempt and lets the queue's blocked-ladder reschedule, so a five-minute wait
  is never served twice.
* **An upload path through the API.** Signed bucket URLs are the normal route;
  when the store cannot sign (the in-memory one) the API accepts the bytes
  itself. That is what lets the multi-process local compose run with no bucket.
* **Two length limits, not one.** `max_hours` (12) is a sanity cap; 5 h is the
  threshold above which a stream needs a start time and is analysed in a window.
  The plan had a single 5–6 h figure doing both jobs, which rejected exactly the
  streams the windowing was written for.
* **yt-dlp metadata at home.** A local run has no Data API key; on a
  residential connection yt-dlp metadata is fine, so the service picks it when
  no key is set and says so in the log.
* **Entry speed is still reported, and still decides nothing** — see the
  measurement above; nothing in the service changes that.

## ds11 — a season reviewed by hand

The first dataset here whose labels a detector did not write. 1,668 frames from
114 games across a whole club season (24 Tuesdays, five sheets), auto-labelled
by `ds10_stratified` and then checked frame by frame by a person: 7,110 labels
in, 6,999 out, 325 rejected and 214 added.

Its val split — 364 frames from 25 games on five held-out dates — is therefore
the first benchmark in this project that measures something. Every number below
is against it, and all three models are measured identically.

| model | trained on | mAP50 | mAP50-95 | P | R |
|---|---|---|---|---|---|
| ds10_stratified | 6,144 detector-written | 0.9848 | 0.9484 | 0.9839 | 0.9453 |
| ds11a | 1,304 hand-reviewed | 0.9931 | 0.9551 | **0.9924** | 0.9730 |
| ds11b | both, 7,448 | **0.9933** | **0.9664** | 0.9832 | **0.9772** |

**The gains land where the review said they should.** Reviewing the 551-frame
pilot measured ds10's error rate per bin, and it spread fourfold: sparse frames
lost 9% of their stones and motion frames carried three times the false-positive
rate, while crowded houses ran at 2.6% error and 114 empty frames drew not one
correction between them. Wave 2's quota was re-cut on that evidence — sparse and
motion up, empty down to a token — and recall moved in exactly those bins:

| bin | recall: ds10 | ds11a | ds11b |
|---|---|---|---|
| sparse | 0.9232 | **0.9620** | 0.9579 |
| motion | 0.9262 | **0.9763** | 0.9691 |
| medium | 0.9536 | **0.9838** | 0.9799 |
| busy | 0.9880 | 0.9861 | **0.9948** |

The bin that was already good barely moved. That is the shape of a targeted
intervention rather than a general lift.

**Two findings worth keeping.**

*Reviewed breadth beats dense volume.* ds11a is trained on a fifth of ds10's
frames and beats it on every metric. ds10's 6,995 frames are two seconds apart
inside twenty ends of five games; ds11a's 1,304 are minutes apart across 95.

*Empty frames teach nothing.* Across both waves, 203 frames whose right answer
was "nothing" produced zero corrections — the detector never invented a stone on
one, and the reviewer never found a missed stone on one. They are safe and
uninformative, and they were 21% of the pilot's review time.

**What this does not say.** mAP is not delivery recall. The pipeline misses
about 40% of deliveries while the detector finds 98% of stones, so the loss is
downstream — tracking, occlusion, the 16-shot fit, reading the house. Nothing
here has been run end to end against `ground_truth.json`, and until it is, ds11b
is a better detector on a benchmark rather than a better answer on a game.

A residual circularity also stands: the labels began as ds10's predictions and a
person changed 7.6% of them, so whatever the reviewer also missed still counts
as agreement. Every frame was looked at, which makes this far weaker than
ds1–ds10 had, but it is not zero.

## ds12 — the same three models, a different league

ds11's val split is 25 unseen *games*, but from the same competition, weeknight
and months as the training data. ds12 asks the other question: 207 frames from
45 games of the Spring Monday Open League 2026 — different competition,
different night, shorter games, and entirely later in time than every training
frame. No overlap with anything any model has seen.

Its labels are the **union** of ds10's and ds11a's detections, reviewed by hand.
Labelling with one model hides that model's own misses, because an unlabelled
stone is easy to overlook where a wrong box is obvious. The reviewer rejected 31
of 743 and added 8 — only eight stones that *both* models missed, which is the
union working.

| model | mAP50 | mAP50-95 | P | R |
|---|---|---|---|---|
| ds10_stratified | 0.9522 | 0.9107 | 0.9861 | 0.9134 |
| ds11a | **0.9921** | 0.9436 | 0.9789 | **0.9666** |
| ds11b | 0.9805 | **0.9476** | 0.9855 | 0.9534 |

**What degrades, moving league:**

| model | ds11 val mAP50 | ds12 mAP50 | drop |
|---|---|---|---|
| ds10 | 0.9848 | 0.9522 | −3.3 |
| ds11a | 0.9931 | 0.9921 | **−0.1** |
| ds11b | 0.9933 | 0.9805 | −1.3 |

**Motion decides it.** ds10 loses one moving stone in five on this league:

| model | motion mAP50 | motion recall |
|---|---|---|
| ds10 | 0.8586 | 0.8097 |
| ds11a | 0.9773 | **0.9352** |
| ds11b | 0.9527 | 0.8981 |

A 12.6-point recall gain, the largest effect anywhere in this work, in exactly
the bin the frames were harvested for.

**More data made transfer worse.** ds11b beats ds11a on the same-league
benchmark and loses to it here. ds10's 6,144 dense frames are 82% of ds11b's
training mix and pull it back toward ds10's biases: ds11b's sparse recall on
this league (0.8859) is no better than ds10's (0.8883), where ds11a reaches
0.9234. So the choice is not "best model" but "best for what" — ds11b fits the
Super League, ds11a survives a change of league.

Caveat: ds11b was not part of the union that labelled this set, so a stone only
it would find is unlabelled unless the reviewer added it. Its numbers here are a
slight floor rather than exact.


## Two lost guards, and the tracker's borrowed history (2026-09-11)

The first game charted through the hosted site -- Rice v Casey, 5U National
Championship, draw 1 sheet 5 -- came back with end 4 reading a red draw as the
lead's first rock. The charter, watching the video, said it was rock 3: two
guards had gone before it. The chart had appended two blanks to the *end* of
the end, so every one of the fourteen shots in between named the wrong thrower.

Replayed from the worker's cached detections (`scripts/replay_end.py`, new),
both guards were plainly detected -- a red parking at (+0.36, +4.22) from
2824.6 and a yellow running from +4.60 to +3.57 from 2861.2 -- and neither was
ever a candidate. Three separate faults, two of them in the tracker:

1. **A parked stone's track was handed to a passing one.** At 3078 a red draw
   passed 0.3 m from the red guard while the sweepers hid it; the tracker
   annexed the guard's track. `_trim_borrowed_start` correctly gave the flight
   to the shooter and threw the guard's own arrival away with the history it
   cut. The head of a borrowed track is now judged as its own stone: it passes
   only by the house-appear route, which demands the spot was empty beforehand,
   so a stone parked before the footage began is still refused.
2. **A stone creeping in at the panel edge was "at rest from its first
   sighting".** The yellow's box was clipped against the top of the frame, so
   it moved 0.05 m in 0.7 s, vanished under the sweepers for half a second and
   reappeared 0.2 m on. The rest test looked only at samples inside the first
   second and called it at rest at index 0; judged as a stone that had always
   been there it was refused for not being new. "Stays put for a second" is
   now witnessed by the first sample at least a second later.
3. **Blanks went to the tail on no evidence.** The gap rule cannot see a rock
   missed before the first one seen -- there is no gap between deliveries --
   and parity alone puts the remainder last. The house read after each seen
   delivery is harder evidence: the sheet cannot hold more stones than rocks
   thrown, and the first house here held three. `_fill_short_end` now uses
   the stone counts first, then the gaps, then the tail.

A synthetic fixture for (1) exposed a fourth fault that the real footage
happened not to trigger: the shooter's orphaned first sightings, cut off at the
handover, "came to rest" a second later exactly where the parked stone showed
again -- a 0.38 m delivery with the strongest evidence there is. That is the
mirror of the borrowed start, and `_trim_borrowed_end` is its mirror fix: a
track finishing on a spot a stone of its colour occupied before it got there
did not stop, it was handed that stone. Two position-based gates were tried
first and backed out: the tracker's gate legitimately lets a stone stop far
short of its prediction, because that is what a collision looks like.

**On the game itself** (all eight ends, same detections): end 4 goes from 14
kept + 2 tail blanks to 16 kept, exactly the charter's account; the other seven
ends keep the identical deliveries, three of them with a different confirming
route (`rest` where a first sighting was previously misjudged).

**On the reference VOD**: the new code finds all 18 hand-confirmed deliveries
and rejects all 8 hand-confirmed non-deliveries. The previous timeline could
not be compared end for end -- it predates the switch to ds11a, and every
delivery shifts 2 s with the detector -- so the previous tracker was run on the
same ds11a detections instead: all 13 ends keep the identical deliveries and
blanks under both trackers (201 of 208 kept either way), and the new code
offers three of them in the first pass that the gap search used to have to
recover. Game 2 end 2's two
missing rocks (8773, 8822) are missing under both trackers: clean flights into
the house that vanish as the players close over the stone, the occlusion case
already on the list, not a regression.

**The charter's remedy.** Detection will miss rocks again, so the viewer now
lets a blank be moved: a shot patch may carry `before: <shot>`, and both
`timeline.apply_overrides` and the page put the end in that order, renumber
it, recompute thrower/label/hammer from the new numbers, recolour the blanks
by alternation and keep every correction filed under the original number
(`id`). A blank with no timestamp borrows one from the nearest seen rock, a
typical gap per shot away, so the video still opens near the right place.

`PIPELINE_VERSION` is now 2026.09.2; charts pinned to the old run keep
working, and a reprocess makes end 4 whole on a fresh link.

**Also seen:** the CLI analysis of the reference VOD hung once in the detect
phase outside Docker -- 25 min wall, 3 min CPU, GPU memory held and idle --
the same signature as the worker hang earlier today. Shared memory was not a
factor here. Still unexplained.


## A stone lost while still running (2026-09-12)

Same game, end 6: the charter said the first rock was missed the way end 4's
were. It was not, quite. The red draw was tracked from the top of the panel to
+0.63 m at 0.6 m/s, then the players closed over it for 5.7 s, and it sat 2.3 m
further on when they moved off. Two things then went wrong. The linker bridges
4 s, so the resting stone became a track that "entered" from inside the house
and was refused. And the flight's house-change check opened its after-window
1.5 s after the stone vanished -- while it was still rolling under the sweepers
-- so the stone was present in 30% of the window against the 40% that counts
as settled.

**Widening the linker was tried first and backed out.** At 8 s it finds end
6's red, and on the reference VOD it also builds a red delivery in game 2 end
3 out of a red-jacketed sweeper's three-sample fragment, a struck red's
resting place and a 6.7 s gap over which the "stone" moved 0.2 m. The fit then
kept 12 of that end's 14. Whether that red was real cannot be told from the
detections; a change that trades one end for another on evidence this thin is
not a fix.

**What was done instead follows from the stone's own speed.** A stone that
was still moving when the tracker lost it has not stopped, and how long it
needs is bounded by how fast it was going: stones slow at roughly 0.08 m/s^2
(a draw crosses the far hog at about 2 m/s and stops 27 m on, 25 s later), so
a stone lost at 0.6 m/s may run for another 7.5 s. The house-change
after-window is now held open that long for a track that ended in motion,
capped at the ten seconds no two deliveries are ever inside, so a stone that
left play can never be read as resting where the next one lands.

**On the game**: end 6 is complete; end 1's yellow at 393 is now confirmed
from its real flight at 386 rather than a late-entry fragment; every other end
keeps identical deliveries. **On the reference VOD**, the previous tracker
against this one on the same detections: 201 of 208 kept either way, all
kept sets identical except game 2 end 2, where the red at 8821 -- the very
rock the old ds10 timeline had and ds11a had lost -- is found from its flight
and displaces a weaker gap-search recovery at 8723.

**Blanks at the front no longer need a pair.** The stone-count rule inserted
pairs to keep alternation, which is right mid-end and wrong before the first
rock seen, where there is no neighbour to alternate against. End 6 was one
rock short with the count saying so at the first house; the blank now leads.
Two reference ends move their tail blank to the front on the same evidence,
and in both a stone is visibly on the sheet before the first delivery seen --
game 1 end 1 because the video starts mid-end, game 2 end 4 because a yellow
arrives twelve seconds before the segmenter's start.

`PIPELINE_VERSION` 2026.09.3.


**The hang, third time.** The redeploy for end 6 froze on the same spot with
the same two stacks, despite yesterday's restore of FFmpeg's log callback.
PyAV's own import installs a callback that does nothing; the GIL-taking one is
installed by ``av.logging.set_level``, and torchvision calls that when it is
imported -- which is when the YOLO detector is built, after ``frames`` has
loaded and restored the default. The restore now happens at every container
open, where nothing can undo it before the decode.


## A guard frozen on its own colour (2026-09-12)

End 7, rocks 8 and 9. The chart showed a blank pair there; the replay showed
a strong red candidate at 6174 -- a takeout that ran through -- dropped by the
rules as one red too many, because the next kept rock was also red and no
yellow had been found between them. The house read at rock 10 said where the
yellow went: a guard appeared at +3.79, right beside the yellow guard already
at +3.95.

That yellow was tracked in from the top edge and refused by the house-appear
route on "the spot was not empty". Two touching stones sit one diameter apart,
and one diameter is exactly the tolerance that test uses for "the same spot",
so a freeze against one's own colour can never pass it; the detector boxing
the pair as one while the arrival settled, and drifting the track onto the
parked stone, made sure. What the spot did gain is a second stone: one more
yellow within a stone's reach after than before. The arrival now passes on
that evidence and rests on the newcomer, not on the parked stone.

**On the game**: end 7 goes to 16 kept -- the red at 6174 becomes rock 8 and
the frozen yellow rock 9. **On the reference VOD**, previous tracker against
this one on the same detections: every end identical, 201 of 208.

`PIPELINE_VERSION` 2026.09.4.


## Three misses in the second hosted game (2026-09-12)

Draw 7, sheet 4 of the PNWCA playdown; the charter named three rocks. Two
share a cause and the third is a camera fact.

**Ends 2 and 3, the first rock.** Both were detected cleanly once the window
was widened -- a red into the top of the house at 986, a yellow through the
house at 1903 -- and both were thrown before the segmenter's start of the end
(1000 and 1940). The segmenter opens an end when a stone first *rests* in its
house, from a keyframe sweep with smoothing, so the boundary lands after the
first stone has settled; a first rock thrown through leaves it nothing to see
at all, so end 3 "began" with its second rock. Detection reached back only 36 s
from that boundary and the analysis discarded anything before it as the
previous end's run-up. Between the previous end closing and this one opening,
this panel holds nothing but this end's first stones -- the previous end was
played into the other house and its stones are cleared toward the hack behind
it, away from here -- so the run-up now begins where the previous end closed
(`analyze.run_up_from`), bounded by the game gap, and everything from there
on is this end's.

**End 6, the last rock.** A yellow tracked from the top of the panel to
-1.94 m at 0.75 m/s and never seen again; the house unchanged. The exit test
only knew "seen crossing the back line" (a centre at or past -1.97) or
leaving sideways. The top camera's view ends at -1.97 m, the back line itself,
and a box clipped by the image edge never reports a centre beyond it. The
panel now knows where its view ends (`PanelSetup.view_y_min_m`), and a stone
last seen within its own radius of that edge, running fast enough to have been
past the back line before the tracker gave up, has left play. A sweeper's blob
vanishing mid-house at a run is not at the edge; a stone dying against the
edge is not running.

Every end's detection span changes with the run-up, so the detection cache
misses once for every game. `PIPELINE_VERSION` 2026.09.5.


## Hogged rocks: the throw is visible where the arrival is not (2026-09-12)

The third hosted game (Skip's Choice League, sheet 5) opened end 2 with a red
that never crossed the hog line and was taken out of play. Neither house camera
can see a hogged rock: it stops between the hog lines, outside both views. The
end read as fifteen rocks starting with a yellow, every thrower one slot off,
and the end's hammer contradicted the score before it.

**The throw is in the picture.** The broadcast frame carries end-on cameras
that show the delivery plainly, but they would need a new model. The cheaper
source is the overhead panel of the *thrower's own* house: the slide starts in
the hack behind that house and the stone is released before the near hog line,
so every delivery crosses that panel from its back edge up-sheet at about
2 m/s -- and the panel already has the detector and the calibration. Measured
on cached detections: releases enter within 0.1 m of the back edge at 1.5-2.1
m/s and are followed 4.5-6.7 m; a red-jacketed sweeper beside a release is
also picked up climbing at 2 m/s but starts mid-panel, which the entry test
refuses. Release to arrival was 18-19 s on the two deliveries timed; the club
says 6 s is possible and 10-15 s usual, so the pairing window is 6-25 s.

On the hogged end itself, before any code: releases at 1109 (red), 1162
(yellow) and 1208 (red) against arrivals at 1180 (yellow) and 1226 (red). The
red at 1109 has no arrival. That is the hogged rock, timed to the second.

**What was built.** `detect/release.py` watches the thrower's panel at 5 fps
for stones leaving that way and pairs each with an arrival of its colour in
the window; a release with no arrival becomes a delivery with reason `hogged`,
timed at the release (which is also where a viewer wants the video to start),
resting beyond the hog line so the house read after it is unchanged, and typed
`hogged` by the classifier. It enters the rules as an ordinary candidate, so
the alternation fit places it and the thrower labels come out right.

**The fit no longer has to drop a real rock.** Two same-colour candidates as
neighbours used to force the fit to discard one, and every case examined
(end 7's takeout, this one) was a real rock lost between them. Now the time
between them decides: at 1.6 times the end's own median interval or more,
there was room for the other team's rock, which is counted against that
team's eight and shown as a blank. Closer than that, one of the two is still a
phantom and the weaker goes.


**Two things the first run taught.** The run-up added yesterday began where
the previous end's *house emptied*, and end 1's house was still being cleared
until 1170 -- a minute after the hogged red left the hack. Teams throw the next
end's first rock while the far house is still being cleared, so the run-up now
begins when the previous end's last rock came to rest. And an unpaired release
is not yet a hogged rock: the house camera loses arrivals too. Each is settled
by what the far house did in the half-minute after the throw -- a stone of its
colour appearing means it arrived unseen (`release-add`, rested on that stone),
a stone vanishing means it hit and ran through (`release-remove`), and no
change means hogged. Timed from the start of the slide the lag ran 11-24 s
across the ends measured, so the window is 6-30 s.

**On the hogged end**: twelve releases seen of sixteen throws, eleven paired
11-19 s to their arrivals, and the red at 1109 with no arrival and no change in
the house. It is rock 1; the end is sixteen. A second unpaired "red" at 1616 is
the yellow's release with the handle colour misread -- the yellow arrives 18 s
later, no red does, and red already has eight -- and the eight-per-team rule
drops it without help.


**Order matters.** Paired before the second pass, an unpaired release stood
in for an arrival the gap search would have recovered, and by filling the gap
it stopped the search from running: seven ends across three games swapped a
real arrival for a release-derived stand-in at the slide time. Pairing now
runs after the second pass, against everything the house offered. Release
candidates also weigh less than any arrival route, so where a release went
unpaired because its arrival came late or its handle colour was misread, the
arrival is the record and the release loses the tie.

**Across the four games**, previous pipeline against this one on the same
detections: the hogged game 92 -> 96 of 96; the playdown 141 -> 144 of 144; the
championship 128 -> 128; the reference 202 -> 204 with every hand-confirmed
delivery and non-delivery unchanged. Release detection sees 10-16 of each
end's 16 throws and pairs all but the ones it is meant to find.

**A hang, intermittent.** One local rerun of the playdown stalled twenty
minutes into a cached run -- 75 of 77 threads in futex wait, GPU idle -- and a
second run of the same command finished in two. The CLI had no way to dump
its stacks, so it does now (`diagnostics.enable_stack_dumps`, shared with the
worker). Whether this is the PyAV deadlock by another route or something else
is open until it recurs with the dump armed.

## The hog line is not in view, and the calibration is not why (2026-09-12)

Building the long split started from the assumption that a hog-to-hog time was
measurable. It is not, and the reason took two wrong turns to pin down. Both
are recorded because the second one is a trap anybody would fall into.

**Neither hog line is inside an overhead panel.** Mapping each panel's crop
corners through its own calibration gives top −2.080 … +4.639 m and bottom
−2.033 … +4.596 m, against `TEE_TO_HOGLINE_M` = 6.401. About 1.8 m short at
each end.

**The painted red line near the top of every panel is not the hog line.** It
measures a consistent +4.47 m (median 4.49 across six ds11 videos, p10 4.46,
p90 4.54). Reading it as the hog line makes the calibration look like it
under-reads the far field by 30%, and that is a very convincing story: the
README already says the oblique view compresses the far field, and the line
even bows 22 px across the panel in a clean quadratic, exactly like barrel
distortion.

**The scale was then measured directly, and it is fine.** A stone is 0.284 m
across, so its apparent size reads the local scale. Over 358 clean, unclipped
detections near the centre line of the reference VOD:

| sheet y | n | box w (px) | box h (px) |
|---|---|---|---|
| −1 m | 69 | 21.81 | 21.87 |
| 0 m | 111 | 21.71 | 21.74 |
| +1 m | 63 | 21.74 | 21.83 |
| +2 m | 49 | 21.49 | 21.48 |
| +3 m | 17 | 21.62 | 21.66 |
| +4 m | 48 | 21.32 | 19.52 |

Flat to ~2% in width across the whole range; only the last bin's height falls,
and there the stone is against the frame edge. Two candidate distortion models
fitted to the rings plus a hog line at +4.47 m were also checked against the
blue 4-foot holdout and both failed it badly — a radial cubic by 4.01 cm and a
1-D projective by 2.95 cm, against the 0.29 cm mean / 0.69 cm worst the
ring-only fit already achieves. `geometry/calibrate.py` is unchanged.

**Extrapolating the arrival to the far hog line was measured and rejected.**
Fitting constant deceleration to each of 161 tracks and predicting the crossing
just 1.0 m beyond the fitted data missed by a median of **0.713 s** (p75 0.909,
p90 1.123, max 4.52). Tracks start at a median +4.02 m, so the hog line is
2.4 m out — over twice that reach, on a quantity whose meaningful differences
are a few tenths of a second. This restates `delivery.py`'s existing warning
that a forward velocity extrapolation "does not work, and was measured
failing".

**So the split is measured over a stated baseline**: the near hog line to a
line at +3.4 m in the arrival panel, 24.946 m. Only the throwing end is
extrapolated. A release is tracked from the back edge at 1.5–2.1 m/s
essentially unchanging — the thrower is still sliding with the stone — for
4.5–6.7 m, so carrying it the last 1.7–3.9 m costs a few hundredths of a
second, and every shot reports how much was carried. `ARRIVAL_LINE_Y_M = 3.4`
comes from the p10 of track starts (+3.44), so about nine shots in ten cross it
while genuinely tracked.

## Nothing has ever been trained on a throw (2026-09-12)

`train/dataset.is_flight` computes `travel = ys[0] - ys[-1]` and requires it
positive — net *down-sheet* travel. A release climbs. So the test fails by
construction for every departing stone, and all 547 frames in ds11's `motion`
bin are arrivals. Across ds1–ds12 the detector has been shown stones coming
into the house and never one leaving the hack, which is the gap `harvest`
wave 3 exists to fill: `motion.find_throws` delegates to
`release.find_releases` so there is one definition of a departure rather than
two that drift, and the `throw` bin joins `motion` and `empty` in refusing
backfill, since a throw quota met with still frames would make the coverage of
the one thing it exists for a number nobody could trust.

## The long split on a real end, and what limits it (2026-09-12)

Game 1 end 4 of the reference VOD, replayed from cache. Sixteen shots, eleven
releases found, **three splits measured**:

| shot | reason | split | what it is |
|---|---|---|---|
| 7 yellow | rest, stops at (0.30, 0.95) | **16.82 s** | a draw into the house |
| 10 red | house-remove | **9.82 s** | a takeout |
| 12 red | house-remove | **9.78 s** | a takeout |

Draw and takeout separate cleanly, which is what the measurement is for. Three
of sixteen is the honest coverage, and three things cost the rest.

**The pairing is not precise enough to time with, and this is the big one.**
`release.pair` answers "did this throw arrive at all?" inside a 6-30 s window,
where several seconds of slop are harmless. Used as a *measurement* it is not.
On this end the clean pairs run 18-20 s from release to arrival, but shots 6, 8
and 9 paired against a release only 10-15 s earlier — and shot 8 is a draw that
stops on the button, which cannot cross 24.9 m in 8.5 s. The suspect releases
share a signature: they are followed to the very top of the panel (+4.28,
+4.36, +4.53) at 1.3-2.3 m/s, where genuine deliveries are lost among the
sweepers at +1.4 to +2.6. A player walking up-sheet from the house fits that
description better than a stone does.

So `split.long_split` now checks itself: a stone only ever slows, so its mean
speed over the baseline cannot exceed the speed it was measured sliding at
before the hog line. Both are measured independently, so the test needs no new
constant beyond a 10% allowance for noise. It removed exactly shots 6, 8 and 9
and kept the three above.

**The extrapolation cap costs three more.** Shots 1, 2 and 4 were followed only
to +1.88, +1.56 and +1.42 m, leaving 4.5-5.0 m to the hog line against a cap of
4.0. Refused rather than guessed.

**A guard that stops above the line can never have one.** Shots 13 and 14 come
to rest at y = 3.43 and 3.56, above `ARRIVAL_LINE_Y_M` = 3.4, so their tracks
never cross it. This is not tunable away: raising the line catches short guards
but loses the tracks that lock on late (starts are median +4.02, p10 +3.44),
and lowering it does the reverse. 3.4 is the compromise.

Thinking time on the same end reads from 10 of 16 shots: red 218 s, yellow
100 s. That gap is mostly coverage, not play — six red intervals were measured
against four yellow — which is why the totals never travel without their counts.
