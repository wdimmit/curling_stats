# Reading the per-end score off the wall board, and dropping the inferred one

## Why

The viewer presents a score the detector inferred. On the reference VOD that
score is wrong, and demonstrably so -- the wall board disagrees with it in both
games of `out/timeline.json`:

| | board says | detection says |
|---|---|---|
| game 1 | Y3 R2 | R5 Y5 |
| game 2 | Y4 R4 | R8 Y3 |

The board is the club's own record and the detector's scoring is a by-product of
a tool whose job is shot-by-shot analysis. So the board wins, and the inferred
score comes off the screen.

The board can already be read, but only as *presence*: `read_slots` asks which
cumulative-score slots carry a card, and `scoreboard.py` says so in its own
docstring -- "the running total can be read without any OCR at all". That throws
away the digit printed on each card, which is **which end produced that score**.
Without it there is no way to key a board score to an end number, and
`per_end_scores` degrades to a list of scoring ends in board order: 5 entries
against 6 detected ends in game 2, and `null` in game 1.

With the digit, one clean read encodes the whole game. Measured on
`board_sheet2_t11000.png`, a single 1920x1080 frame:

| row | cards | reading |
|---|---|---|
| yellow | slot 1 digit "1", slot 3 digit "3" | reached 1 after end 1, 3 after end 3 |
| red | slot 2 digit "2" | reached 2 after end 2 |

which is the complete game: end 1 Y+1, end 2 R+2, end 3 Y+2. Ends with no card
anywhere were blank. Nothing is inferred and nothing is timed.

That is why the current pass sweeps the board every 450 s for the whole game
(`BOARD_INTERVAL_S`) and still cannot produce a per-end score, while one read
can.

## Decisions taken with the user

| Decision | Choice |
|---|---|
| Per-end scores | **Read the card digits.** Temporal alignment is ruled out by the module's own constraint (the club posts late, "sometimes several ends late", so the board must never time anything); assuming which ends were blank is inference. OCR is the only route where the per-end score is *read*. |
| Digit recognition | **A small trained classifier**, after template matching was measured and failed (see *Why template matching failed*). Trained on the printed row, which is self-labelling; numpy-only inference from a small shipped `.npz`, so no new runtime dependency. |
| Sampling | **Adaptive: one read, widen only if the digits come up short.** The digits say which ends are covered, so completeness is checkable rather than assumed. Typical cost one read; bounded worst case. |
| Depth of the change | **The board becomes the score.** Timeline score fields are board-sourced. Detected scores stay computed under a marked `detected` block, used only for the agreement check. The hammer chain runs off board scores. |
| Unreadable board | **No score at all.** Score fields are `null` and the viewer says the board could not be read. Some games will show no score -- an occluded or never-posted board yields nothing rather than a guess. |
| Hosted worker | **Runs the board pass.** `skip_scoreboard=True` comes out. At 1-2 reads instead of ~12 the cost that justified skipping it is gone, and without it every hosted game would be scoreless. |
| Ends 10 and above | **Out of scope.** Two-glyph card digits are rejected as unread. Club games are eight ends. |

## Why template matching failed

The original design matched each card glyph against the board's printed 1-14 row
by normalised intensity correlation, accepting a read only on a confident margin.
Measured against the 69-row labelled harvest, that approach does not work:

| | result |
|---|---|
| card digits read correctly | 32/69 = 46% |
| distinct physical cards read correctly in every frame | **0 of 11** |
| empty slots scoring above the 0.05 accept margin | 54% |
| wrong reads still accepted at any threshold keeping 95% of correct reads | 28/37 |

The 69 rows are only **11 distinct physical cards** photographed repeatedly, and
the same physical card reads differently between frames -- one reads 7 six times,
then 5, then 3, then 2 -- which points at preprocessing noise rather than glyph
shape.

The decisive measurement matched the **printed** digits, whose labels are known
by construction, against printed templates:

| | accuracy |
|---|---|
| same frame (identity check) | 189/189 = 100% |
| a different frame | 138/189 = **73%** |

The same font, board and camera, differing only by frame, still fails 27% of the
time, with the same confusions it makes on cards (2->7, 3->8, 5->3, 9->6). So the
card-versus-printed font difference was never the cause: **normalised intensity
correlation is inadequate at 22x16 px**, and it is the matcher that is wrong, not
the premise. The information is separable -- the identity check is perfect and a
human reads these crops without effort.

## The classifier, and where its training data comes from

The printed 1-14 row is in **every** board frame whether or not any cards are
hung, and it is **self-labelling**: the digit at slot *k* is *k*. Training data is
therefore free, unlimited and needs no human labelling, across every sheet and
any timestamp.

| set | source | size |
|---|---|---|
| train | auto-harvested printed digits, augmented for sub-pixel shift, scale, blur, JPEG and brightness drift | thousands, free |
| validate | printed digits from frames held out of training | 189 today, trivially extensible |
| validate (real) | the 11 distinct hand-labelled cards, never trained on | 11 |

That last row is what the hand-labelling was actually needed for, and it is the
correct split: train on printed glyphs, prove it on real cards.

Inference is numpy reading a small `.npz`, because `torch` lives only in the
`gpu` extra and a plain install has none -- a torch inference path would break
`curling-score analyze` on base dependencies. Only `analyze.py` imports
`scoreboard`; the API and timeline do not.

The model starts as an MLP over the normalised glyph, because correlation failed
specifically on alignment and scale noise and augmentation attacks that directly
by teaching invariance. A small CNN is the escalation, not the starting point.

**Gate to pass before the rest of the pipeline is built on it:** printed
cross-frame accuracy well above the matcher's 73%, and the 11 held-out real cards
read correctly and consistently, against the matcher's 0 of 11. If printed glyphs
do not transfer to card glyphs, the fallback is hand-labelling real cards across
more VODs -- the expensive path, entered only on evidence.

## Feasibility, as measured

The printed 1-14 row sits directly beneath the yellow cards, in the same frame,
the same font, the same scale and the same lighting. It is therefore a complete
exemplar set for every glyph a card can carry, rebuilt per read and free of any
per-sheet tuning.

A deliberately crude probe -- fixed crop, no median stacking, no card-tile
segmentation, no ink centring -- correlated each card glyph against those 14
printed glyphs:

| frame | card | best match | margin over 2nd |
|---|---|---|---|
| t11000 | yellow slot 1 | **1** correct | 0.251 |
| t11000 | yellow slot 3 | **3** correct | 0.169 |
| t11700 | yellow slot 1 | **1** correct | 0.332 |
| t11700 | yellow slot 3 | **3** correct | 0.091 |
| t11700 | yellow slot 4 | 9 **wrong** | **0.022** |

Four of five correct, and the miss is the one tile a spectator's hand was
covering, with its margin collapsing to 0.022 against 0.091-0.332 for the
correct reads. So the margin separates a good read from a bad one, which is the
gate the whole design leans on.

All three things the probe omitted raise that separation, and all three are in
the design: median-stacking the ~40 keyframes `read_board_at` already collects
removes the occluding hand outright; segmenting the card's bright tile replaces
a fixed band that clips the glyph tops; centring on the ink bounding box
removes translation from the correlation.

At native 1080p the numbers to design against are a 20.4 px slot pitch, an
18 px card row and a glyph 17-22 px tall. Measured across the five known board
frames, the marker separation `dy` that fixes board scale varies only 56.2-57.1
px, so the geometry is stable.

## Architecture

Five units. `find_board` and `BoardGeometry` are unchanged -- they already
locate the board on every sheet -- and everything new sits on top.

### `game/scoreboard.py` -- the glyphs

`BoardGeometry` gains a `printed_row` property beside the existing
`yellow_row`/`red_row`. The printed digits occupy the lower part of the same
band as the yellow cards, so it is derived from the same `top_line_y` to
`mid_line_y` gap.

    templates(image, geom) -> dict[int, ndarray]

crops those 14 glyphs and mean-centres and unit-normalises each. Rebuilt per
read, so lighting drift over an evening and a different board on another sheet
both come out in the wash.

    read_cards(image, geom) -> CardBoard

replaces `read_slots` in the pipeline. Card *presence* keeps the existing
brightness-range test, which is measured and works. For each present card it
then segments the bright tile within the slot, crops the glyph to its ink
bounding box, correlates against the templates, and keeps the best digit with
its margin over the runner-up. Below `MIN_MARGIN` the digit is `None`: a card is
there, but which end it records is not known.

    Card       = (end: int | None, margin: float)
    CardBoard  = {"yellow": {slot: Card}, "red": {slot: Card}}

`read_slots` stays, as `read_cards`'s own first step. `cumulative` stays too,
re-expressed over a `CardBoard` as the highest occupied slot per team.

### `game/scoreboard.py` -- cards to scores, no CV

    per_end_from_cards(cards, n_ends) -> BoardScores

Pure logic. Within a team, sort cards by end number; that end's score is this
card's slot minus the previous card's slot, since slots are cumulative. Then
every end in 1..n_ends is classified:

| class | test | certainty |
|---|---|---|
| **scored** | a card carries that digit | read |
| **blank** | no card carries it, but some card carries a *higher* digit | read -- a later end was posted, so this one was passed over |
| **unread** | no card carries it and none carries a higher digit | unknown -- cannot distinguish blank from not-yet-posted |

This is the one genuine ambiguity in the design, and it is confined to
*trailing* ends. Interior blank ends are certain and cost nothing, which is the
common case; only a blank final end is ambiguous, and it is reported as unread
rather than guessed.

The board also checks itself, with no reference to detection:

- an end digit appears **at most once across both teams** -- both teams cannot
  score in one end;
- slots **strictly increase** with end number within a team -- cumulative scores
  do not go backwards.

A misread digit usually violates one of these. Those two checks plus
`MIN_MARGIN` are the only gates; failing either makes the read unusable and the
sampler widens. `ScoreboardError` keeps its current meaning.

`consolidate`, `per_end_scores` and `split_games` are superseded and go, with
their tests. They exist to smooth flicker across a dozen presence-only readings
and to split games on a cleared board; median stacking and `per_end_from_cards`
do both better, and `split_games` has no caller outside its own tests.

### `analyze.py` -- the sampler

`BOARD_INTERVAL_S` and the whole-game sweep go. The pass searches for the
**latest non-blank board state**, because the board accumulates and so the
latest state is the most complete, under a bounded read budget:

    start just before game.end_s
      blank board          -> already cleared for the next game, step back
      non-blank, self-consistent, highest digit == n_ends
                           -> done, one read
      non-blank but short  -> try later for a late posting; if that is blank,
                              keep the latest non-blank read and report the
                              trailing ends unread

Each read median-stacks the keyframes in its window, as `read_board_at` already
does. `_printed_digit_groups` keeps its job as the occlusion check, and now also
guards the template source: no printed row, no templates, so the read is
refused and the sampler widens.

`n_ends` -- the detected end count -- is a **stopping hint only**, never a gate
on correctness. What makes a read usable is the board's own two consistency
checks plus `MIN_MARGIN`; `n_ends` only says whether it is worth spending
another read looking for more. A wrong end count therefore costs reads, never
a wrong score, which is what keeps the board independent of detection.

Typical cost one read, against roughly a dozen today. The budget caps the worst
case.

### `timeline.py` -- the document

| field | becomes |
|---|---|
| `end.score` | board per-end score, `null` for an unread end |
| `end.running` | board cumulative, `null` from the first unread end on |
| `end.score_source` | `"board"` or `null` |
| `game.final` | board total **only when every end is accounted for**; otherwise `null`, because with trailing unread ends the true final is genuinely unknown |
| `game.detected` | `{score_by_end, final}` -- still computed, never displayed, sole consumer of the agreement check |
| `game.scoreboard` | gains `read_at_s`, `reads`, `unread_ends`, and the card readings with their margins, so a disagreement is diagnosable without a re-run |

`rules.hammer_chain` is fed board scores and fills `hammer_expected` up to the
first unread end, then `null`. `build_game`'s running-score accumulation becomes
null-tolerant, as does the scoreboard recompute after overrides at
`timeline.py:417-430`.

`rules.py` keeps its scoring function: it feeds `game.detected` and therefore
the agreement check, which is the only thing that can tell a misread board from
a correct one.

### The viewer

`ChartPanel.Scoreboard` fills its per-end columns from the board and **marks
blank and unread differently**, with a key. Collapsing the two into one "-"
would put the same dishonesty back in a new place: one means nobody scored, the
other means nobody posted. The total shows only when the game is fully
accounted for.

`core/watch.mjs endSummary` returns a board-sourced running score, `null`
tolerant; `Watch.EndBar` renders no score line where it is `null`. No frontend
file reads a detected score field after this change -- verified by grep as part
of the work, since `game.final` and `end.score` keep their names.

### The worker

`skip_scoreboard=True` comes out of `worker.py:169`. `api.py:67`'s phase budget
of `"scoreboard": 0.5` is re-measured against real cost now that the pass
actually runs in the hosted path.

## Validation

`MIN_MARGIN` is the number the design leans on, and **three known frames with
cards is not enough to set it.** The first step of the implementation is a
harvest round, before any threshold is chosen:

- board frames across all five sheets and several games, including late-game
  boards with many cards, occluded boards, and dim ones;
- card digits labelled by hand;
- `MIN_MARGIN` set from the measured separation between correct and incorrect
  matches, not from the five-sample probe above.

Success criteria, to be measured on that set and reported:

| | target |
|---|---|
| digit accuracy on accepted reads | **no wrong digits.** Absolute, not a rate: a wrong score is worse than none |
| games fully accounted from one read | numeric target set from the harvest; the design is only worth its complexity if this is the common case |
| games with any score read | numeric target set from the harvest; shortfall reported as unread, never guessed |

The bar is asymmetric on purpose, and matches the decision above: refuse
anything doubtful.

### The classifier, measured against the gate

Harvest: 2052 printed glyphs over 228 readable board frames of the one cached
VOD, exactly 228 per class, self-labelled, no human labelling. Model: a
352-256-128-9 ReLU MLP, softmax cross-entropy, Adam, trained in numpy on eight
freshly augmented copies of every training glyph per epoch for 60 epochs
(~2 minutes on this box); ~465 KB of weights in a `.npz`. Split by whole blocks
of frames with a buffer wider than the median-stacking window, so no held-out
glyph shares a source frame with anything trained on.

| gate | correlation | classifier |
|---|---|---|
| held-out printed frames | 73% | **99.3%** (447/450) |
| printed digits in the 21 card frames | 73% cross-frame | **99.5%** (188/189) |
| labelled card rows | 46% (32/69) | 62% (43/69) |
| distinct physical cards right in every frame | 0 of 11 | **4 of 11** |
| empty slots reaching the digit read at all | 54% cleared the margin | 3.5% (18/519) |

**Gate 1 passes decisively and gate 2 does not.** The representation was indeed
the problem for cross-frame instability: on printed glyphs the classifier goes
from 73% to 99.3%, and unlike correlation it reads a given physical card the
same way in every frame. But the printed-to-card gap the Risks section names is
real and it is concentrated:

| true digit | rows | correct | misread as |
|---|---|---|---|
| 1 | 19 | 18 | 5 (x1) |
| 2 | 17 | **0** | **7 (x15)**, 9, 5 |
| 3 | 15 | 13 | 1, 2 |
| 4 | 11 | 8 | 9 (x2), 3 |
| 5 | 6 | 3 | 9 (x3) |
| 6 | 1 | 1 | -- |

Every card carrying a 2 is misread, almost always as a 7, and confidently
(0.79-0.97). The printed 2 in the *same frames* reads correctly, so this is not
lighting, camera, occlusion or the crop: a human reads all 69 card crops without
effort. 2->7 is also one of the confusions correlation made on cards, so the MLP
inherited it rather than introducing it. The colour split -- yellow 40/44, red
3/25 -- is an artefact of which digits each row happened to carry, not of the
row geometry.

Two probes locate the failure. 1-NN against the 2052 raw printed glyphs scores
49/69 on the cards, *better* than the trained MLP's 43/69, and 5/17 on the twos;
class-mean correlation scores 30/69, reproducing the original matcher's 46%. So
the printed manifold holds some card-2 signal and the MLP's decision boundary
discards it: the model is overfitting the printed distribution, not running out
of capacity. A card 2's nearest printed neighbour is a 7 in 9 of 17 cases, so
more augmentation of printed glyphs is unlikely to close it either.

Gate 3 is answered, but not by the model. 501 of the 519 empty slot positions
are refused by `_card_glyph` before the classifier is asked -- no bright card
tile means nothing to read -- against 54% of empty slots clearing the old
correlation margin. Of the 18 that do reach it, confidence is useless as a
presence test: median 0.946 empty against 0.991 card, and the empty distribution
reaches 1.0. Presence therefore stays with `read_slots`, as decided.

**Consequence: the classifier is not wired into `read_digit`, and nothing is
built on it.** The fallback the Risks section names -- hand-labelling real cards
across more VODs -- is now the evidenced path rather than a precaution, and it
is the user's call. A domain-adaptation route exists too (train on printed
glyphs and the labelled cards jointly, or synthesise card-like glyphs), but any
route needs card labels that are not also the validation set, because 11 cards
cannot both tune and prove.

## Risks

**Transfer from printed glyphs to card glyphs has been measured, and it does
not hold.** 4 of the 11 held-out cards read correctly in every frame, with every
card carrying a 2 misread as a 7 while the printed 2 in the same frame reads
correctly. This was the top risk on the list and it is the one that fired; see
*The classifier, measured against the gate* for the numbers and the two probes
that locate it. 11 cards is also thin -- it is every card in the one cached VOD
-- but thin was never the problem: the failure is systematic and per-digit, not
a matter of sample size.

**Card font may diverge from the printed font** on a sheet other than sheet 2.
Both known card frames are sheet 2. This was listed as a risk to the template
approach and it remains one for the classifier, mitigated by augmentation and
measured by the held-out cards.

**Superseded:** the original risk here was that `MIN_MARGIN` could not be set
until the harvest existed. The harvest was built first, exactly so this would
surface early, and it did -- it killed the template approach before anything was
built on top of it. That was the plan working, not failing.

**Some games will show no score.** A direct consequence of the decisions taken,
and correct, but it is a visible regression from a number always being present
to sometimes nothing being present. The viewer has to say *why* -- board not
read, or ends not posted -- rather than just going blank.

**The hosted path gains work.** Every job now does 1-2 full-resolution reads it
previously skipped. Bounded, and much smaller than the sweep it replaces, but it
is new cost on a path that had none.
