# Stage 1 of a throw: the stone leaving the hack

## Why

A throw is four physical events, and the pipeline currently recognises the
first of them through a set of gates fitted to a sample rather than to the
physics:

1. the stone moves from the hack across the throwing end's T-line, seen by that
   end's overhead panel;
2. it crosses the throwing end's hog line, seen by the long camera;
3. it crosses the destination hog line, seen by the far panel;
4. it comes to rest or passes out of play.

This spec covers stage 1 only. Stages 2 and 3 are in place -- `detect/longview.py`
and `game/split.py` respectively -- and stage 4 is unchecked today and unmeasured.

Stage 1 is worth doing on its own because it is where the pipeline loses the
most: `release.find_releases` fires on **63 of 90** rocks on `AEqLTgM25Tc`, and
the panel actually sees the stone leave the hack on **88 of 90**. The gap is
not detection. It is one constant.

### What the constant costs

`MIN_TRAVEL_M = 3.0` asks a release to climb three metres from first sighting.
The panel's back edge sits at y = -2.01 m (top panel) and -2.21 m (bottom), so
three metres of travel finishes at **y = +0.79 to +0.99 -- about a metre past
the T-line**. That last metre is precisely the metre that is missing: the sweepers
close over the stone as it passes the house, and on the 27 rocks with no
release the tracker's median top-of-track is **y = 1.02 m**, against 3.48 m for
the rocks that do produce one.

So the gate asks for the one thing the camera cannot reliably supply, and it
asks for it *just* beyond the point where the evidence stops. Every failure
sits within a metre of passing.

### Measurement behind this spec

All figures are `AEqLTgM25Tc`, 6 ends, 90 rocks, replayed from the detection
cache. Reproduced by `scripts/render_phase1.py` for the individual cases; the
sweep itself was a throwaway probe over `detect.delivery._build_tracks` output.

| test | rocks passing |
| --- | --- |
| today: `find_releases` fires | 63 / 90 |
| any track crossing the T-line from below | 88 / 90 |
| any track crossing 1 ft behind the tee from below | 88 / 90 |
| any track crossing 3 ft behind the tee from below | 87 / 90 |

An earlier version of this measurement said 84/90 and named six failures. That
was an artefact of choosing each shot's candidate by *highest point reached*,
which on e1 s3 picked a low-confidence broom head at x = +1.0 over the stone
running down the centre line at confidence 0.86-0.92. The question a gate
answers is "does **any** track satisfy this", not "does my favourite one".

## Decisions taken with the user

- The line sits **1 ft behind the tee**, not at it and not 3 ft back.
- Stage 1 does **not** require the stone to reach the hog line on this camera.
- The lateral bound applies to the **throwing side only**.
- Stage 1 **does not refuse** when more than one track qualifies. It resolves.
- `MIN_TRAVEL_M` and `ENTRY_MARGIN_M` are deleted, not retuned.

## Where the line goes, and why not further back

Pass count is flat at 88 anywhere from the tee to 2.75 ft behind it, because
the number of tracks that *fail to reach* the line is zero everywhere in that
range. Every failure is a track acquired too late -- above the line already.
Moving the line back therefore cannot buy coverage; it can only shrink the
runway between the panel's back edge and the line, and eventually costs rocks.

What does move is clearance. Taking each qualifying track's margin as
`min(line - y_start, y_end - line)`:

| line | passes | worst margin | p10 margin |
| --- | --- | --- | --- |
| T-line | 88 | 0.170 m | 0.810 m |
| 0.75 ft back | 88 | 0.399 m | 0.939 m |
| **1 ft back** | **88** | **0.475 m** | **1.015 m** |
| 1.25 ft back | 88 | 0.489 m | 1.029 m |
| 2 ft back | 88 | 0.260 m | 0.820 m |
| 3 ft back | 87 | 0.006 m | 0.566 m |

1.25 ft is the optimum and 1 ft is within 3% of it on both columns, so the line
is **y = -0.3048 m** for being a round number in the units the sheet is painted
in. 3 ft is the worst choice in the range: it clears e3 s5 by six millimetres
and drops e1 s1 entirely.

## The definition

A shot's stage 1 is a track on the throwing end's overhead panel that

1. carries the shot's colour;
2. lies within `release.CENTRE_FRACTION` of the centre line (already shipped,
   commit `44d2119`);
3. begins below `STAGE1_Y_M = -0.3048` and reaches it; and
4. climbs at delivery speed, `MIN_SPEED_M_S <= v <= MAX_SPEED_M_S`.
5. carries at least `MIN_SAMPLES = 3` samples.

Three rather than four: e2 s12 is a real delivery the panel caught exactly
three times -- y -2.16 -> +0.57 at 2.73 m/s on the centre line -- and a
minimum of four costs that rock and no other, taking the result to 87 of 90.
Stage 1 establishes that a throw happened; it times nothing, so a thin track
is weaker evidence than a thick one but not worse evidence of the wrong kind.

Nothing about how far it then travels, and nothing about the hog line. Stage 2
watches the hog line from a camera that can actually see it.

### Resolving more than one candidate

After the lateral bound, **86 of 90** rocks have exactly one track crossing the
line, two have two, and two have none. There is no ambiguity to speak of, which
is why stage 1 resolves rather than refuses -- `longview`'s unconditional
`KEY_AMBIGUOUS` would be refusing a population that does not exist here.

Both two-crosser cases resolve mechanically:

- **e2 s8** -- two tracks 0.02 m apart laterally, overlapping in time. One
  stone, split into two tracks by a duplicate box. **Merge.**
- **e6 s6** -- 0.60 m apart, but the rival carries n=2 samples against the real
  track's n=8. **Take the longer.**

So the rule is: merge tracks that overlap in time and lie within
`MERGE_LATERAL_M = 0.3` of each other, then take the survivor with the most
samples. Deterministic, and no branch returns "cannot say".

The duplicate-box case is worth naming because it is the same failure that
costs a rock at the 3 ft line. On e1 s1 the detector returned two boxes for one
stone in a single frame -- y = -0.87 at confidence 0.90 and y = -1.19 at 0.82 --
and `_build_tracks` split the trajectory there. Merging is not a tidy-up; it is
repairing a track the detector fragmented.

## What this deletes

| constant | today | after |
| --- | --- | --- |
| `MIN_TRAVEL_M` | 3.0 m of climb | gone |
| `ENTRY_MARGIN_M` | first seen within 0.6 m of the back edge | subsumed by "begins below the line" |
| `MIN_SPEED_M_S`, `MAX_SPEED_M_S` | 1.0-4.5 m/s | kept, unchanged |

`MIN_SPEED_M_S` survives because it is a real discriminator rather than a
fitted threshold: among tracks acquired above the T-line, deliveries run
1.47-2.15 m/s and everything else 0.12-0.61, a gap with nothing in it. That is
the opposite of `longview.SPEED_BOUNDS_M_S`'s upper bound, which sits in the
middle of its own distribution and discards hand-verified crossings.

## Architecture

Confined to `detect/release.py`. No new module, and no change to what a
`Release` is or to anything downstream that reads one.

### `detect/release.py`

- `STAGE1_Y_M = -0.3048` and `MERGE_LATERAL_M = 0.3`, beside `CENTRE_FRACTION`.
- `_merge_fragments(tracks)` -- joins tracks overlapping in time within
  `MERGE_LATERAL_M`, returning tracks in the same shape `_build_tracks` gives.
- `find_releases` replaces its entry and travel tests with the stage-1
  definition above, and merges before judging.

`Release.y_exit_m` keeps its present meaning -- how far up the panel the stone
was followed -- and will now often be small. Callers that treat a large
`y_exit_m` as a quality signal must be checked; `split.long_split` does not,
since the merge that made the side view primary stopped it reading the release
for timing at all.

### Unchanged

`harvest/motion.py` and `harvest/pool.py` take the new definition for free:
they already delegate to `find_releases` precisely so the training selector and
the pipeline cannot drift.

## Testing

Against `tests/test_release.py`'s existing synthetic frames:

- a stone from the back edge reaching 1 ft behind the tee is a release, where
  today it is not (this is the 63 -> 88 case, and the test should say so);
- a stone acquired above the line is refused however far it then climbs;
- a stone that starts below the line but stops short of it is refused;
- two fragments of one delivery, overlapping in time and 0.02 m apart, merge
  into one release rather than becoming two or being refused as ambiguous;
- two genuinely separate crossers resolve to the one with more samples;
- `MIN_SPEED_M_S`/`MAX_SPEED_M_S` still refuse a drift and a jitter.

Then the coverage check that motivates all of it: replaying `AEqLTgM25Tc`
should yield 88 releases against today's 63, and the two remaining failures
should be e2 s16 and e6 s2 and no others.

## Known risks

**`y_exit_m` shrinks.** Releases will now routinely exit around y = +1 rather
than +3.5. Anything reading it as a proxy for confidence would silently
downgrade. Grep before landing.

**More releases means more pairing.** `pair()` matches releases to arrivals in
a 6-30 s lag window; going from 63 to 88 candidates gives it more chances to
mispair. The pairing is unchanged by this spec, and its behaviour on the extra
25 needs measuring rather than assuming.

**One rock may be two tracks and stay that way.** Merging is keyed on time
overlap; two fragments separated by a gap in detections -- e1 s1 has a frame
with nothing at all at t=18.00 -- do not overlap and will not merge. In e1 s1's
case the second fragment alone satisfies stage 1, so it does not matter there.
It is not established that this holds generally.

## Not in scope

**Turning a stage-1 pass into a release on a shot.** This spec measures whether
the panel SEES the throw, and 88 of 90 is that number. It is not the number of
rocks that end up carrying a release: that also needs `pair()` to bind the
release to a delivery and `fit.fit_end` to keep that delivery, and on the
reference game two rocks clear stage 1 and fall there. e1 s5 is the clean
example -- the panel offers two near-duplicate red arrivals 0.4 s apart, the
release binds to the first, and the fit keeps the second. Measured after
implementation: 86 of 90 rocks carry a release, against 63 before.

That gap is the pairing-versus-fit ORDERING, not the association window below,
and it is equally out of scope here. Closing it means pairing after the fit, or
re-binding orphaned releases.

**The association window.** e2 s16 is the one stage-1 failure that this spec
does not fix and is not about geometry: the stone leaves the hack 30.5 s before
its arrival, against `MAX_LAG_S = 30`, and is excluded by half a second. That
is the same class as e6 #7's crossing missed by 0.052 s at `WINDOW_S`'s edge.
Both belong to a separate decision about how a throw is associated with its
arrival, which is where the remaining tuning in this pipeline appears to live.

**e6 s2.** No red delivery exists on the panel at all, and the shot itself is
doubtful -- shot 1 rests at 4367.3 and shot 2 supposedly enters at 4370.0, 2.7 s
apart against `MIN_SEPARATION_S = 10`, recovered by `gap-search` on ten arrival
samples. Stage 1 did not fail here so much as decline to invent a throw, which
is the behaviour wanted. Whether the rock is real is a rules question.

**Stage 4.** Unchecked today, unmeasured, and the honest reason this spec stops
at stage 1.

**Retraining ds11.** The lateral bound cannot touch a box on the thrower's
trailing arm, which sits directly behind the stone on the centre line --
x = +0.24 against the stone's +0.25, confidence 0.32 against 0.42. Frames are
banked in `datasets/ds11/hardneg`.
