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

Also resolved and removed (2026-10-25):

- **Blank ends.** `rules.score_end` returns 0-0 when no stone is in the house,
  and the game's score is the wall board's, not the detected one.
- **The long camera for throw reinforcement.** The side-view detector
  (`weights.SIDE_NAME`) times the throwing hog crossing, `game/sidereleases.py`
  recovers releases and rocks the overhead view lost, and `release1` reads the
  hand coming off the handle.
- **Cross-sheet validation of the trained model.** Every overhead model since
  ds11a trains on reviewed frames from all five sheets (`datasets/ds11/` on).

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

## The painted line near the top of a panel *is* the hog line

This section used to say the opposite, and it is kept, corrected, because the
trap is easy to fall back into. Every overhead panel shows a red line across
the sheet a little below its top edge. Measured against the rings'
calibration it sits at about **+4.47 m** from the tee, against a hog line at
`TEE_TO_HOGLINE_M` = 6.401, which reads as proof that the panels stop short of
the hog line. They do not. The along-sheet scale falls to about a third of the
rings' by the top of the frame (`game/split.py`, `geometry/hogpaint.py`), so
the real 6.4 m line reads as 4.4-4.7.

What seemed to rule that out was a stone-size check: box widths flat to about
2% from y = -1 m to +4 m. Width runs across the sheet, the axis that falloff
leaves alone. The tell was in the same measurement: only the box *height* fell
in the last bin. `game/split.py` now times both hog lines at their paint,
found per panel by `geometry/hogpaint.py`, and the split is hog to hog.

## Release pairing is too loose to time with

`release.pair` matches a throw to an arrival inside a 6-30 s window, which is
the right question for "did this rock arrive, or was it hogged?" -- several
seconds of slop change no answer. It was once asked to carry the long split
as well, and there the slop was the measurement: three splits from sixteen
shots on game 1 end 4. That was settled by going round it. A split is now two
line crossings, the throwing one read by the long camera, and needs no release
at all (`game/split.py`).

What is left is hogged-rock detection, which still rides on the pairing. The
suspect releases on that end shared a signature: followed to the very top of
the panel (+4.28 to +4.53) at 1.3-2.3 m/s, where a genuine delivery is lost
among the sweepers by +1.4 to +2.6. A player walking up-sheet from the house
fits that better than a stone. That was measured before a release was
redefined as a stone crossing a line a foot behind the tee
(`detect/release.py`, 2026-09-19), and has not been re-measured since.

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

A house state can also be empty, whereas a window read during clearing is
indistinguishable from one. (The score itself now comes from the wall board,
so this matters for `detected_score` and the houses drawn, not for the score.)

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

## More training data for the card-digit reader

The shipped card-digit reader is a small conv net. Until 2026-10-23 it was
trained on 2052 self-labelled glyphs from the board's printed 1-14 strip plus
**54 hand-labelled real cards** from 10 videos. The retrain of 2026-10-23
(b592f7a3) added `board-cards-2627`: 1,157 of this season's cards from the
worker cache, 13,196 digit rows. Held out a league night at a time
(`scripts/cards2627/gate2627.py`, 5 folds x 2 seeds) it reads 96.3-97.2% of
frames at `MIN_CONFIDENCE`, against 91.1% for the old weights, with 1-2 frames read wrong.
Two gaps remain.

**Digits 8 and 9 are still all but unseen.** This is structural, not a sampling
failure: only one team scores per end, so a video yields *at most one* card per
digit, and "cards of digit d" is precisely "videos whose game ran d scoring
ends". Collecting more ordinary league VODs barely raises the ceiling; only
longer games do. The 2026-27 set has 113 rows of a 7 and 3 of an 8; 9 is known
only from the printed strip.

**A purpose-shot video would break that ceiling outright.** A single clip panning
a board with every card hung in a range of slots — and ideally in both the yellow
and red rows, at a few different lighting levels — would supply every digit many
times over in minutes, including the 8s and 9s that league play almost never
produces. That is worth far more per minute of footage than any number of game
recordings, and the user has offered to shoot one. It is the single highest-value
input this model could receive.

**The shipped weights have no held-out data.** They are trained on everything,
which is the right recipe for a shipped artefact; the generalisation figure is
the by-night fold measurement above, not the tests that read known cards.

So the focused clip should be **split before it is used**, not after: hold back a
portion — ideally a different lighting level or a different board — as a genuine
validation set, and never train on it. Without that there is nothing left to
detect a regression against.

What to do with new data when it arrives: keep the split by video and by sheet,
never by card — one physical card appears in dozens of frames and a row-level
split leaks badly. Re-run `scripts/gate_digits.py`, which reports a seed range
rather than a single run, because the zero-wrong coverage metric is set by the
single most confident wrong read and swings tens of points between seeds on
identical inputs.

## Two-digit card numbers, for ends 10 and above

`read_digit` classifies one glyph per card and `card_window`/`glyph_in_window`
localise exactly one ink blob inside the card's tile -- both built around the
single-digit end numbers that cover a normal eight-end game. An extra end
called to break a tie hangs a card reading "10", "11" or higher, which is two
glyphs in one tile, not one. Nothing crashes: the classifier's top class
probability collapses toward uniform against a shape none of its training
classes look like, `MIN_CONFIDENCE` refuses it the same way it refuses an
occluded glyph, and the end comes back unread rather than misread -- the same
fail-closed behaviour the confidence gate exists for. So it is safe today, but
it is a real gap: any game that goes to an extra end currently loses board
scoring for every end from 10 on, silently, because "refused" and "never
called" look identical from outside `per_end_from_cards`.

Currently out of scope and rejected as unread. Fixing it needs the card
window widened to fit two glyphs, a segmentation step that splits the tile
into its digits before classifying either one, and training data for it --
which the reference VOD's two games do not supply, since neither ran past six
or seven ends. The purpose-shot video proposed above for digits 8 and 9 could
supply this too, if it hangs a card reading "10" or higher in a slot.

## Persistence as the next defence against phantom cards

A person standing in front of the board can read as a card: it is a bright
tile with internal contrast, exactly what `glyph_in_window` looks for. Two
layers currently stand between that and a wrong score. `is_readable`'s
row-occlusion check catches 20 of 23 labelled phantoms by requiring each card
row to read close to the printed row's own brightness (see
`_ROW_OCCLUSION_MARGIN`); the digit reader's confidence gate (`MIN_CONFIDENCE`)
then refuses the other 3, because whatever gets localised as "ink" inside a
phantom's tile never lands a class probability above 0.9999. Measured, zero of
23 labelled phantoms reach a score today.

**The measurement, because it is the valuable part.** Cards are hung and stay
hung; people move. Tracked as the fraction of sampled frames a slot holds a
card across the frames *after* it is first seen, real cards appear in
0.75-1.00 of them, while phantoms reach at most 0.63 -- a clear margin that
separates 14 of 14 labelled phantoms from 51 of 51 real cards. Bare "did it
ever disappear from a frame" does **not** separate them: 19% of real cards also
vanish from a frame or two through ordinary missed detection (a hand, a bad
median window). It is the persistence *fraction*, not a single disappearance,
that does the work.

**What it would cost.** A second board read per game, checked against the
current one -- `read_game_board` costs one read in the ordinary case, and a
persistence check needs the board sampled and compared across time, which
roughly doubles the read cost of every game. That is why it was not spent now:
the two layers already in place measure zero phantoms reaching a score, so the
second read buys defence against an error class not currently observed to
occur.

**Where the old implementation went.** `consolidate` enforced this same
physical invariant -- a slot seen once and then gone was noise -- for the
retired presence-only design, requiring a slot to be seen `_CONFIRM_READINGS`
times running before it was believed. It operated on raw `BoardReading` sweeps,
not on `read_cards`' `CardBoard`, so it could not simply be kept and reused
here; it, `per_end_scores`, `split_games`, `read_board` and `read_board_at`
were deleted with the presence-only path in the commit that retired it. The
implementation and its tests remain in git history for whoever picks this up.

## Other parked ideas
- **Hammer from the score sequence.** It is read from who threw first, which is
  wrong whenever an end's opening delivery is missed. The board gives it
  independently: the team that scores throws first next end. That chain is now
  computed (`hammer_expected` per end, `hammer_consistent` per game, in
  `timeline.build_game`), but only checked against the read, never used in
  place of it.
