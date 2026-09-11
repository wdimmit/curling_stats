# Backlog

Ideas parked until baseline functionality is solid, and findings that shape
them. Nothing described as parked is implemented.

Resolved and removed from this list: fitting the 16-shot structure to resolve
over-counts, now `game/fit.py`. It was not optional in the end —
`rules.throw_info` refuses a 17th shot, so an over-counted end could not be
built at all, and the rules turned out to do most of the work unaided: strict
alternation alone identified end 1's phantom as the candidate sitting 2.2 s
before a red, which no two deliveries ever are. It took impossible ends from
5/8 and 5/6 to none in either game.

## Bisect the video instead of scanning it

Deliveries are *events with distinct before/after states* — the house has one
stone more (or one fewer, on a takeout), and it stays that way until the next
delivery. That structure suits a search rather than a scan.

Instead of detecting on every frame at 10 fps, sample coarsely, and wherever the
house state differs between two samples, bisect the interval to find when it
changed. Detection cost would go roughly from *O(duration × fps)* to
*O(shots × log(interval))*.

Why it should pay off, from what has been measured here:

- Detection is now **72% of runtime** at 10 fps on the strip proxy, so cutting
  the number of detections is the main lever left.
- An end is ~16 minutes and holds 16 deliveries. At 10 fps that is ~9550 frames
  to find 16 events; bisection to ±0.5 s would need on the order of 16 × 11 ≈ 180
  probes, plus a coarse pass.

Things that will complicate it, all observed in this footage:

- The house state is not monotonic — takeouts *remove* stones, and players move
  outlying stones before the end is over, so "state changed" is not the same as
  "a delivery happened".
- Occlusion makes a single probe unreliable: the state has to be read from a
  small window (see `stones_in_window`), not one frame.
- A delivery's *rest time* is what scoring needs, and the current tracker gets
  it from following the stone. Bisection finds the interval, so the stone would
  still need dense tracking within it — probably a hybrid: bisect to locate,
  then scan locally.

Worth doing once delivery recall is good enough that the events being searched
for are actually found.

## Long camera for throw reinforcement

Both wide cameras see the thrower and the stone before it reaches the overhead
panel. Every miss diagnosed so far has been a stone the overhead panel *did*
see, so this is not the bottleneck yet — but it is the only signal independent
of the overhead view, and it would settle the cases where a delivery's entry is
never tracked at all (the red at t=183 in game 1 end 1 was first picked up at
y = 2.62, well inside the panel, so its entry was missed even though the shot
itself was recovered another way).

Needs its own calibration: the long views are oblique, not near-nadir, so the
homography work in `geometry/calibrate.py` does not transfer. The cheap version
needs no metres at all — just "a stone-coloured blob crossed the throwing end's
hog line at time t", which is a tripwire in pixel space.

## Cache the activity profile

`profile.build_profile` scans the whole video counting stones per panel per
keyframe, and it is recomputed on every run: about two minutes before any end
is looked at. `detect/cache.py` already keys detections on everything that can
change them, and the profile is only detections at a coarser sampling, so it
belongs in the same cache. Done in the measurement harness
(`scratchpad/setupenv.py`) but not in the package.

## The gap search can be told the wrong colour to look for

`secondpass.gaps_to_search` refuses to expect a colour whose team has already
thrown its eight. That is right in principle and wrong whenever a stone has
been counted twice: game 2 end 3 had yellow on eight with two of them 4 s
apart, so the one gap that actually held a yellow guard was searched for a red.

De-duplicating before the search fixes the expected colour and was measured to
make the end result **worse** — game 1 fell from 102 kept deliveries and 4/8
ends agreeing to 98 and 3/8. The reason is that `game/fit.py` already resolves
duplicates and does it from the whole end's structure, whereas any dedupe pass
must choose greedily; running one first pre-empts the better-informed choice.

So the defect is still there, and the fix belongs in `gaps_to_search`: it
should discount a count of eight that rests on same-coloured candidates closer
together than deliveries ever are, rather than have a separate pass rewrite its
input.

## Carry the house state across an end instead of re-reading it

Scoring reads the house from a window after the last shot settles. That fails
when the deciding stone is occluded soon after it stops, and at the end of an
end it usually is — the players are already closing in to clear.

Game 1 end 6, observed by eye: the last red passes over the button and stops at
about (+0.77, -1.51), **1.69 m** from the tee. It is visible at that spot for
roughly one second. Twelve seconds later a person is walking down the sheet
(detected as a string of yellows from +2.93 down to -1.17) and the red is gone
from detection, so the nearest stone reads as the yellow guard at (+0.36,
+1.67) — **1.71 m**. The end scores yellow 1; the board says red 1. A 2 cm
margin, lost to one second of visibility.

Widening or shortening the window does not fix it: the stone is simply not
visible any more. What would is keeping a house *state* through the end —
every delivery adds its own resting place, and takeouts remove or move what
they strike — so the final house is assembled from sixteen observations made
when each stone was actually visible, rather than from one window at the worst
possible moment. The delivery records already carry rest positions and
`Delivery.reason` says how well each was seen.

This also subsumes the blank-end problem below: a house state can be empty,
whereas a window read during clearing is indistinguishable from one.

## Label filters can delete the very thing they are meant to help find

A filter that removes what does not behave like a stone will remove a stone
that was not detected as a delivery, because its flight is then indistinguishable
from a person walking. The quiet-span rule did exactly that: 25% of what it
removed travelled down-sheet like a stone, from deliveries the pipeline had
missed. See `validation/EXPERIMENTS.md`.

The general shape is worth remembering, because it will recur with any filter
derived from the pipeline's own output: **the filter inherits the pipeline's
blind spots and then writes them into the training data**, so the next model
inherits them too. Any such filter needs a guard that asks whether the region
it is about to clean is one the pipeline understands — for quiet spans that is
"is this gap short enough to be a single gap between stones".

## Blank ends — now the largest single error source

Validated against the wall board as a *multiset* of end scores, which sidesteps
the board's lag entirely (it is posted several ends late, so which end a card
belongs to is unreliable, but what was scored is not):

| | game 1 | game 2 |
|---|---|---|
| end scores matching the board | 6/8 | 4/6 |
| unexplained on the board | `(0,0)`, `(0,0)` | `(0,0)`, `(0,1)` |
| final computed vs board | 5-4 vs **3-3** | 8-3 vs **4-4** |

Both of game 1's misses are blank ends: every scored end it reports is a score
the club also recorded, and its whole error is inventing a score for two ends
that scored nothing. Nothing in the pipeline can currently return "blank" —
`rules.score_end` returns whatever is nearest the tee, and the wall board hangs
no card for a blank end so it cannot be read from there either.

An end is blank when no stone finishes within `IN_HOUSE_MAX_D_M` of the tee, so
the rule itself is easy. What makes it hard is being sure the house was read at
the right moment: a blank end looks exactly like an end read after the players
have cleared, which is the failure mode that produced four empty-house scorings
before `timeline.build_end` learned to walk back to the last shot that left
stones.

## Missing deliveries come in pairs

Finding one delivery does not help until its neighbour is found too, because
alternation couples them. Game 1 end 3: a red at 2414.9, confirmed by eye, is
now detected -- and still dropped, because the yellow between it and the red at
2526.6 is missing, so the rules cannot fit two reds in a row and keep the
better-attested one.

That yellow is not merely mis-tracked, it is invisible: between 2418 and 2526
the panel holds only two static reds. The only yellow activity in those 108
seconds is a six-second fragment running from y = -0.57 out to -1.95, which
stops just short of the back-line test at -1.971 -- so it has no entry, no
resting place, and no crossing.

The practical consequence is that recall improvements are lumpy. A route that
recovers one stone can score zero on every metric until the stone next to it is
recovered as well, which makes single-shot evaluation misleading.

## Red is detected later and less often than yellow

Measured on game 1 end 2, every yellow delivery's track begins at
y >= 4.28 m and every red at y <= 3.65 m — a clean separation with no overlap.
The model picks yellow up about 0.7 m further up-sheet, presumably because a
yellow handle contrasts better against the ice.

Two consequences:

- **Never gate on entry height with a shared threshold.** It would cut red
  deliveries and keep yellow phantoms, which is exactly backwards.
- **Red recall is now the binding constraint.** Since `game/fit.py` enforces
  strict alternation, a missing red forces a real yellow to be dropped too:
  end 5 offered R6 Y10 and the rules could only keep R6 Y7.

Game 1 end 3 is the extreme case measured so far: a red takeout was not seen
at all above y = +1.69 -- below the entry gate that rejects struck stones --
while the sweepers running alongside it were picked up at +4.15. `delivery.py`
now lets such a track through when the sheet provably gained a stone, but that
is a repair, not a cure.

The fix belongs in the detector, not the logic. It most likely needs
hand-labelled frames — the current model was trained on classical-detector
output, so it inherited that detector's colour sensitivity along with its
sweeper false positives.

## Reviewing the labels, not just the detections

`curling-score labels <dataset> --split train|val` draws every label as the
model was taught it, ranked worst-first, each shown with the frames either
side. Two failures found within minutes of it existing, pulling in opposite
directions:

- **Labels that should not be there.** A player's red shoes, labelled as a red
  stone by the classical detector in 48 of 55 sampled frames, 13 of which
  survived `keep_stone_like`.
- **Labels that should be and are not.** A yellow stone at the panel edge,
  unlabelled in every frame, because `to_label` discarded any box crossing the
  image boundary. Every stone within about 0.14 m of the edge was therefore
  excluded from training, and neither model can find one. A stone against the
  edge can still be 1.84 m from the tee: inside the house and counting. Now
  clipped rather than dropped.

The ranking took two attempts, and the first is worth recording as a trap: it
flagged labels that were not in the same *place* in the neighbouring frames,
which is every stone in flight. Saved frames are a second apart and a stone
crosses about a quarter of the panel in that time. It returned 507 findings,
nearly all of them real. Ranking on how long a label *lasts* instead cut that
to 74, since a stone is on the ice for minutes whether moving or still.

Neither signal would have caught the shoes, which held their place for twelve
seconds. That took the quiet-span test, which needs the delivery structure. The
sampled spread remains the only unbiased view, and it is what turned up the
edge stone.

## What the detector fires on, and why size will not fix it

`curling-score inspect --start T --end T` rings every detection over a span so
it can be judged by eye. It exists because every automated measure of the
detector is circular: the model learned from the classical detector's labels,
so a benchmark against those labels measures agreement, not accuracy.

The first thing it showed: in game 2 end 1, 8113-8139 holds no deliveries at
all -- people standing about talking, one of them in red shoes, which the model
rings as a red stone at 0.50-0.83 confidence and follows around the sheet.

Box area looks like the fix and is not. Across all 13 ends, detections holding
a spot measure area p10 457 / median 483 / p90 508 and transient ones p10 378 /
median 475 / p90 517 -- the medians all but identical. Dropping everything
under 420 px would remove a quarter of the transients and 1.3% of the stones,
which is not a trade worth taking blind. The clean gap in that one window is
local. (The comparison is blunt in any case: "transient" includes stones in
flight as well as people.)

What does work is the path. The shoes go y = 3.03 -> 1.41 -> 2.79, and a stone
cannot travel back up-sheet -- which `MIN_NET_TRAVEL_FRACTION` already catches,
so this phantom is correctly rejected today.

## The handle-radius table does not describe the model's boxes

`rocks.expected_handle_radius_m` interpolates a measured table that shrinks
from 0.098 m at the tee to 0.046 m at y = 5.0, because an *eroded HSV handle
blob* fades with distance. The trained model's boxes do not: measured across
two ends, the median box radius is a near-constant 0.143 m at every y from
-2.0 to +4.6 — which is `STONE_RADIUS_M`, since the near-nadir camera makes a
stone subtend roughly constant pixels across the panel (the same fact that lets
one scalar `px_per_m` calibrate to 0.29 cm).

So size cannot be used to tell a stone from a person with this model, and any
ratio against that table is meaningless for it — a check of "2.72x expected at
y > 4.3" looked like a strong signal and was entirely the table's
extrapolation. Either the table needs a second version for model boxes, or the
gate needs to drop.

## Other parked ideas
- **Hammer from the score sequence.** It is read from who threw first, which is
  wrong whenever an end's opening delivery is missed. The rules give it
  independently: the team that scores throws first next end.
- **Cross-sheet validation of the trained model.** It has only been trained and
  evaluated on sheet 2. The other four sheets have harvested frames but no
  labels.
