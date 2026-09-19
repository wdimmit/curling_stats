# Stage 1 of a Throw Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recognise a throw's first physical event -- the stone leaving the hack and crossing a line 1 ft behind the tee -- so `find_releases` fires on 88 of 90 rocks instead of 63.

**Architecture:** Entirely inside `src/curling_score/detect/release.py`. Two gates fitted to a sample (`MIN_TRAVEL_M`, `ENTRY_MARGIN_M`) are replaced by one line crossing in sheet coordinates; fragmented tracks are merged before judging; and the tie-break between rival candidates changes from "followed furthest" to "best sampled". `Release`'s shape, and everything downstream that reads one, are untouched.

**Tech Stack:** Python 3.12, pytest, numpy. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-19-throw-stage-1-design.md`

## Global Constraints

- `STAGE1_Y_M = -0.3048` -- the stage-1 line, 1 ft behind the tee, in sheet metres. The tee is y = 0 by calibration, so this is the same number on every panel.
- `MERGE_LATERAL_M = 0.3` -- two tracks closer than this laterally, overlapping or touching in time, are one stone the detector fragmented.
- `MIN_SAMPLES = 3` -- fewest samples a track may have and still be a release.
- `MIN_SPEED_M_S = 1.0`, `MAX_SPEED_M_S = 4.5` -- unchanged, and deliberately so.
- `CENTRE_FRACTION = 0.35` -- already shipped in commit `44d2119`. Do not change it.
- Throwing side only. Nothing in this plan may touch the arriving house's detection path.
- Run tests with `PYTHONPATH=src .venv/bin/python -m pytest ... -p no:cacheprovider`. The project's `addopts` already includes `-q`; passing a second `-q` suppresses the summary line, which has confused three sessions.

---

### Task 1: Merge tracks the detector fragmented

A duplicate box in a single frame splits one delivery into two tracks. On `AEqLTgM25Tc` e1 s1 the detector returned y = -0.87 at confidence 0.90 and y = -1.19 at 0.82 in the same frame, and `_build_tracks` split the trajectory there. Neither fragment satisfies stage 1 alone.

**Files:**
- Modify: `src/curling_score/detect/release.py`
- Test: `tests/test_release.py`

**Interfaces:**
- Consumes: `curling_score.detect.delivery._Track` (attributes `color`, `ts`, `xs`, `ys`; constructor `_Track(color, t, x, y)`; method `add(t, x, y)`) and `delivery._build_tracks(frames) -> list[_Track]`.
- Produces: `release._merge_fragments(tracks: list[_Track]) -> list[_Track]`, used by `find_releases` in Task 2.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_release.py`, inside a new class placed immediately before `class TestPairing:`

First a helper, beside the other helpers at the top of the file:

```python
def track(color, samples):
    """A `_Track` from `(t, x, y)` samples, as `_build_tracks` would build it."""
    from curling_score.detect import delivery as D

    (t0, x0, y0), *rest = samples
    tk = D._Track(color, t0, x0, y0)
    for t, x, y in rest:
        tk.add(t, x, y)
    return tk
```

then the tests:

```python
class TestMergingFragments:
    """One stone the detector split in two is one track, not two.

    These drive `_merge_fragments` directly rather than through
    `find_releases`, and that is deliberate. Coaxing `_build_tracks` into
    splitting a synthetic trace is not reliable -- it re-acquires across a
    0.4 s gap and a 1.4 m jump, so a trace built to look fragmented comes
    back already joined and the test passes whether the merge exists or
    not. The samples below are e1 s1's real ones.
    """

    def test_two_fragments_of_one_stone_merge(self):
        low = track("red", [(17.0, 0.13, -1.88), (17.2, 0.11, -1.39),
                            (17.4, 0.13, -1.19)])
        high = track("red", [(17.4, 0.11, -0.87), (17.6, 0.10, -0.30),
                             (17.8, 0.12, 0.05), (18.2, 0.08, 1.33),
                             (18.4, 0.08, 1.86)])
        (merged,) = release._merge_fragments([low, high])
        assert merged.ys[0] == -1.88
        assert merged.ys[-1] == 1.86
        assert len(merged.ts) == 7          # 3 + 5, less the clash at 17.4

    def test_the_better_sampled_box_wins_a_collision(self):
        # Both fragments hold a sample at 17.4 and disagree: -0.87 against
        # -1.19. Interpolating the stone's neighbours puts it at -0.85.
        low = track("red", [(17.0, 0.13, -1.88), (17.2, 0.11, -1.39),
                            (17.4, 0.13, -1.19)])
        high = track("red", [(17.4, 0.11, -0.87), (17.6, 0.10, -0.30),
                             (17.8, 0.12, 0.05), (18.2, 0.08, 1.33),
                             (18.4, 0.08, 1.86)])
        (merged,) = release._merge_fragments([low, high])
        assert -0.87 in merged.ys
        assert -1.19 not in merged.ys

    def test_tracks_far_apart_laterally_do_not_merge(self):
        # e6 s6: 0.60 m apart and overlapping in time, genuinely two objects.
        a = track("red", [(4566.3, 0.00, -2.07), (4566.9, 0.00, -0.50),
                          (4568.1, 0.00, 1.82)])
        b = track("red", [(4566.9, -0.60, -1.14), (4568.9, -0.60, 2.97)])
        assert len(release._merge_fragments([a, b])) == 2

    def test_tracks_with_a_gap_between_them_do_not_merge(self):
        a = track("red", [(100.0, 0.05, -2.0), (100.4, 0.05, -1.0),
                          (100.8, 0.05, 0.0)])
        b = track("red", [(400.0, 0.05, -2.0), (400.4, 0.05, -1.0),
                          (400.8, 0.05, 0.0)])
        assert len(release._merge_fragments([a, b])) == 2

    def test_two_different_objects_do_not_merge(self):
        # 0.6 m apart laterally is e6 s6: a real delivery and something else
        # crossing beside it. Merging those would invent a track neither had.
        real = leaving("red", 100.0, y0=-2.0, y1=2.4, x=0.00)
        other = leaving("red", 100.2, y0=-1.1, y1=1.0, x=0.60)
        got = release.find_releases(frames(real, other), VIEW_Y_MIN)
        assert len(got) == 1
        assert len(got[0].track) == len(real)

    def test_a_gap_in_time_is_not_a_fragment(self):
        # Two separate throws on the centre line, minutes apart. Same stone
        # colour, same lane, and nothing to do with each other.
        first = leaving("red", 100.0, y0=-2.0, y1=2.4, x=0.05)
        second = leaving("red", 400.0, y0=-2.0, y1=2.4, x=0.05)
        got = release.find_releases(frames(first, second), VIEW_Y_MIN)
        assert len(got) == 2
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_release.py::TestMergingFragments -p no:cacheprovider -rf`

Expected: the four tests that call `release._merge_fragments` FAIL with `AttributeError: module 'curling_score.detect.release' has no attribute '_merge_fragments'`. `test_two_different_objects_do_not_merge` and `test_a_gap_in_time_is_not_a_fragment` go through `find_releases` and pass already; they are there to pin behaviour that must not change.

- [ ] **Step 3: Add the constant**

In `src/curling_score/detect/release.py`, immediately after the `CENTRE_FRACTION = 0.35` line:

```python
# Two tracks closer than this laterally, overlapping or touching in time, are
# one stone the detector fragmented rather than two objects. Measured on
# AEqLTgM25Tc: the one pair that is genuinely two objects (e6 s6) sits 0.60 m
# apart, and the one pair that is one stone (e2 s8) sits 0.02 m apart. There is
# nothing in between, so the threshold is not delicate.
MERGE_LATERAL_M = 0.3
```

- [ ] **Step 4: Write the merge**

Add to `src/curling_score/detect/release.py`, immediately above `def find_releases`:

```python
def _mean_x(track) -> float:
    return sum(track.xs) / len(track.xs)


def _join(a, b):
    """One track from two, preferring the better-sampled one where they collide.

    Where both tracks hold a sample at the same instant they disagree about
    where the stone was, and the better-sampled track is the one to believe: on
    e1 s1 the two boxes were y = -0.87 and y = -1.19, and interpolating that
    stone's neighbours puts it at -0.85.
    """
    long_, short_ = (a, b) if len(a.ts) >= len(b.ts) else (b, a)
    by_t = {round(t, 3): (x, y)
            for t, x, y in zip(short_.ts, short_.xs, short_.ys)}
    by_t.update({round(t, 3): (x, y)
                 for t, x, y in zip(long_.ts, long_.xs, long_.ys)})
    ts = sorted(by_t)
    out = D._Track(a.color, ts[0], *by_t[ts[0]])
    for t in ts[1:]:
        out.add(t, *by_t[t])
    return out


def _merge_fragments(tracks):
    """Join tracks that are one stone the detector split.

    A duplicate box in a single frame is enough to split a delivery, and each
    half on its own can fail stage 1 from opposite directions -- one stops
    short of the line, the other is first seen above it.

    Restarts after every join so a stone broken into three pieces collapses to
    one. There are a handful of tracks in a release window, so the quadratic
    scan costs nothing worth avoiding.
    """
    out = sorted(tracks, key=lambda tr: tr.ts[0])
    joined = True
    while joined:
        joined = False
        for i in range(len(out)):
            for j in range(i + 1, len(out)):
                a, b = out[i], out[j]
                if a.color != b.color:
                    continue
                if max(a.ts[0], b.ts[0]) > min(a.ts[-1], b.ts[-1]):
                    continue                    # a gap between them in time
                if abs(_mean_x(a) - _mean_x(b)) > MERGE_LATERAL_M:
                    continue                    # too far apart to be one stone
                out[i] = _join(a, b)
                del out[j]
                joined = True
                break
            if joined:
                break
    return out
```

- [ ] **Step 5: Call it from `find_releases`**

In `src/curling_score/detect/release.py`, change the loop header:

```python
    for track in D._build_tracks(frames):
```

to

```python
    for track in _merge_fragments(D._build_tracks(frames)):
```

- [ ] **Step 6: Run the tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_release.py tests/test_motion.py -p no:cacheprovider -rf`

Expected: all six PASS. Every other test in both files must still pass; if any regressed, the merge is joining things it should not.

- [ ] **Step 7: Commit**

```bash
git add src/curling_score/detect/release.py tests/test_release.py
git commit -m "release: merge tracks the detector fragmented

A duplicate box in one frame splits a delivery into two tracks -- on
AEqLTgM25Tc e1 s1 the detector returned y=-0.87 at confidence 0.90 and
y=-1.19 at 0.82 in the same frame, and _build_tracks split there.

Joins tracks that overlap or touch in time within MERGE_LATERAL_M of each
other laterally. The one genuinely-two-objects pair in that game sits
0.60 m apart and the one fragmented stone sits 0.02 m apart, so the
threshold has nothing near it.

Where both halves hold a sample at the same instant the better-sampled
track wins, which on e1 s1 is the box interpolation agrees with.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Replace the entry and travel gates with the stage-1 line

This is the change that takes coverage from 63 to 88. `MIN_TRAVEL_M = 3.0` finishes about a metre past the T-line, and that metre is the one the sweepers take away.

**Files:**
- Modify: `src/curling_score/detect/release.py`
- Modify: `docs/superpowers/specs/2026-09-19-throw-stage-1-design.md`
- Test: `tests/test_release.py`

**Interfaces:**
- Consumes: `release._merge_fragments` from Task 1.
- Produces: `release.STAGE1_Y_M: float`, `release.MIN_SAMPLES: int`. `find_releases(frames, view_y_min_m, view_x_limit_m=None)` keeps its signature and raises `ValueError` when `view_y_min_m >= STAGE1_Y_M`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_release.py`, **replace** `test_a_short_climb_is_not` in `class TestFindingReleases` — its premise is exactly what this task reverses — with:

```python
    def test_a_climb_that_stops_just_past_the_tee_is_a_release(self):
        # The 63 -> 88 change. MIN_TRAVEL_M used to demand three metres, which
        # finishes about a metre past the T-line, and on 30% of throws the
        # sweepers close over the stone before it gets there. Measured on
        # AEqLTgM25Tc: the rocks with no release have a median top-of-track of
        # y = 1.02 m, against 3.48 m for the rocks that do produce one.
        got = release.find_releases(frames(leaving("red", 100.0, y1=0.0)),
                                    VIEW_Y_MIN)
        assert len(got) == 1

    def test_a_climb_that_stops_short_of_the_line_is_not(self):
        got = release.find_releases(frames(leaving("red", 100.0, y1=-0.8)),
                                    VIEW_Y_MIN)
        assert got == []
```

and add a new class immediately before `class TestPairing:`

```python
class TestTheStage1Line:
    """A throw's first event: the stone leaves the hack and crosses the line."""

    def test_three_samples_are_enough(self):
        # e2 s12 on AEqLTgM25Tc is a real delivery the panel caught exactly
        # three times -- y -2.16 -> +0.57 at 2.73 m/s on the centre line. A
        # minimum of four discards it and costs the 88th rock.
        got = release.find_releases(
            frames(leaving("red", 100.0, y0=-1.0, y1=0.0)), VIEW_Y_MIN)
        assert len(got) == 1
        assert len(got[0].track) == 3

    def test_two_samples_are_not(self):
        got = release.find_releases(
            frames(leaving("red", 100.0, y0=-0.6, y1=0.0)), VIEW_Y_MIN)
        assert got == []

    def test_a_stone_crossing_faster_than_any_delivery_is_not_one(self):
        # 4.8 m/s across the line. MIN_SPEED and MAX_SPEED stay exactly as
        # they were: among tracks acquired above the T-line, deliveries run
        # 1.47-2.15 m/s and everything else 0.12-0.61, a gap with nothing in
        # it. They are discriminators, not fitted thresholds.
        got = release.find_releases(
            frames(leaving("red", 100.0, speed=4.8)), VIEW_Y_MIN)
        assert got == []

    def test_a_panel_that_cannot_see_behind_the_tee_is_an_error(self):
        # Stage 1 is unmeasurable on a crop that does not reach the hack, and
        # silently returning nothing would look like a game with no throws.
        with pytest.raises(ValueError, match="stage-1 line"):
            release.find_releases(frames(leaving("red", 100.0)),
                                  view_y_min_m=0.5)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_release.py -p no:cacheprovider -rf`

Expected: `test_a_climb_that_stops_just_past_the_tee_is_a_release`, `test_three_samples_are_enough` and `test_a_panel_that_cannot_see_behind_the_tee_is_an_error` FAIL. `test_a_climb_that_stops_short_of_the_line_is_not`, `test_two_samples_are_not` and everything from Task 1 already pass.

- [ ] **Step 3: Add the constants and delete the old ones**

In `src/curling_score/detect/release.py`, **delete** these two lines:

```python
# First seen this close to the back edge of the view, or it did not come from
# the hack.
ENTRY_MARGIN_M = 0.6
# A release crosses most of the panel before the sweepers close over it.
MIN_TRAVEL_M = 3.0
```

and put in their place:

```python
# Stage 1 of a throw: the stone leaves the hack and crosses this line. One foot
# behind the tee, in sheet metres -- the tee is y = 0 by calibration, so it is
# the same number on every panel.
#
# This replaces a travel minimum of 3.0 m, which from a back edge at y = -2.01
# (top panel) or -2.21 (bottom) finished at y = +0.79 to +0.99, about a metre
# PAST the T-line. That metre is the one the sweepers take away: measured over
# AEqLTgM25Tc, the 27 rocks with no release have a median top-of-track of
# y = 1.02 m against 3.48 m for the rocks that produce one. The gate asked for
# exactly the evidence that stops being available, and 25 of those 27 rocks
# were refused within a metre of passing.
#
# Coverage is flat at 88 of 90 anywhere between the tee and 2.75 ft behind it,
# because no track that exists fails to REACH the line -- every failure is a
# track acquired above it already. What moves is clearance: worst-case margin
# is 0.170 m at the tee, 0.475 m here, 0.489 m at 1.25 ft, and 0.006 m at 3 ft.
# 1.25 ft is the optimum and this is within 3% of it on a round number.
STAGE1_Y_M = -0.3048
# Fewest samples a track may have and still be a throw. Three, not four,
# because e2 s12 on AEqLTgM25Tc is a real delivery the panel caught exactly
# three times -- y -2.16 -> +0.57 at 2.73 m/s, on the centre line -- and four
# costs that rock and no other.
MIN_SAMPLES = 3
```

- [ ] **Step 4: Replace the gates in `find_releases`**

In `src/curling_score/detect/release.py`, replace this block:

```python
    for track in _merge_fragments(D._build_tracks(frames)):
        if len(track.ts) < 4:
            continue
        if track.ys[0] > view_y_min_m + ENTRY_MARGIN_M:
            continue  # started mid-panel: a sweeper, or a stone already in play
        up = track.ys[-1] - track.ys[0]
        dur = track.ts[-1] - track.ts[0]
        if up < MIN_TRAVEL_M or dur <= 0:
            continue
```

with:

```python
    for track in _merge_fragments(D._build_tracks(frames)):
        if len(track.ts) < MIN_SAMPLES:
            continue
        if track.ys[0] >= STAGE1_Y_M:
            continue  # first seen above the line: not watched leaving the hack
        if track.ys[-1] < STAGE1_Y_M:
            continue  # never reached it
        up = track.ys[-1] - track.ys[0]
        dur = track.ts[-1] - track.ts[0]
        if dur <= 0:
            continue
```

Note there is no longer any test on how far the stone travelled after the line. Stage 2 watches the hog line, from a camera that can see it.

- [ ] **Step 5: Make `view_y_min_m` earn its place**

`view_y_min_m` is no longer read by any gate. Rather than leave an unused parameter -- or churn nine call sites removing it -- it becomes the precondition it implies. In `find_releases`, immediately after the `if not frames: return []` line:

```python
    if view_y_min_m >= STAGE1_Y_M:
        raise ValueError(
            f"this panel sees down to y={view_y_min_m:.2f} m, which is above "
            f"the stage-1 line at {STAGE1_Y_M:.4f} m: it cannot watch a stone "
            f"leave the hack, so no throw here could ever be confirmed")
```

- [ ] **Step 6: Update the `find_releases` docstring**

Replace the first line of the docstring, `"""Every stone that left the panel up-sheet the way a delivery does.`, with:

```python
    """Every stone seen leaving the hack and crossing the stage-1 line.

    Stage 1 of a throw, and nothing more: it does not ask how far the stone
    then travelled, because the overhead panel loses about 30% of deliveries
    within a metre of the T-line and the hog line is stage 2's job, watched
    from a camera that can actually see it.
```

leaving the rest of the docstring as it is.

- [ ] **Step 7: Run the tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_release.py tests/test_motion.py tests/test_thinking.py tests/test_pool.py tests/test_sideshots.py -p no:cacheprovider -rf`

Expected: all pass, including Task 1's `test_two_fragments_of_one_stone_become_one_release`.

- [ ] **Step 8: Amend the spec**

The spec does not mention a sample minimum, and keeping the old value of 4 would have produced 87 of 90 rather than the 88 the spec claims. In `docs/superpowers/specs/2026-09-19-throw-stage-1-design.md`, in the `## The definition` section, after item 4 (`climbs at delivery speed...`), add:

```markdown
5. carries at least `MIN_SAMPLES = 3` samples.

Three rather than four: e2 s12 is a real delivery the panel caught exactly
three times -- y -2.16 -> +0.57 at 2.73 m/s on the centre line -- and a
minimum of four costs that rock and no other, taking the result to 87 of 90.
Stage 1 establishes that a throw happened; it times nothing, so a thin track
is weaker evidence than a thick one but not worse evidence of the wrong kind.
```

- [ ] **Step 9: Commit**

```bash
git add src/curling_score/detect/release.py tests/test_release.py \
        docs/superpowers/specs/2026-09-19-throw-stage-1-design.md
git commit -m "release: a throw is a stone crossing the line behind the tee

Replaces MIN_TRAVEL_M and ENTRY_MARGIN_M with one line crossing at
STAGE1_Y_M, a foot behind the tee.

MIN_TRAVEL_M = 3.0 finished about a metre past the T-line, and that metre
is the one the sweepers take away: over AEqLTgM25Tc the 27 rocks with no
release have a median top-of-track of y = 1.02 m against 3.48 m for the
rocks that produce one, and 25 of the 27 were refused within a metre of
passing. Coverage goes from 63 of 90 to 88.

The entry margin is subsumed -- a track first seen above the line was not
watched leaving the hack -- and view_y_min_m, which nothing reads now,
becomes the precondition it always implied.

MIN_SAMPLES is 3 rather than the old literal 4, because e2 s12 is a real
delivery caught exactly three times and four costs that rock and no
other. Spec amended to match; it had been silent on the minimum.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Pick the better-sampled rival, not the one followed furthest

`find_releases` already collapses two sightings inside `MIN_SEPARATION_S` to one throw, keeping the one with the larger `y_exit_m`. With shorter tracks that tie-break now picks wrongly: on e6 s6 the real delivery has eight samples and reaches y = +1.82, while the thing crossing beside it has two samples and reaches +2.97.

**Files:**
- Modify: `src/curling_score/detect/release.py`
- Test: `tests/test_release.py`

**Interfaces:**
- Consumes: `release.Release.track` (a tuple of `(t, x, y)`), already populated.
- Produces: no new names.

- [ ] **Step 1: Write the failing test**

Add to `class TestTheStage1Line` in `tests/test_release.py`:

```python
    def test_the_better_sampled_rival_wins_not_the_one_followed_furthest(self):
        # e6 s6 on AEqLTgM25Tc: the real delivery carries eight samples and
        # reaches y = +1.82; the thing crossing 0.60 m beside it carries two
        # and reaches +2.97. Keeping the larger y_exit_m picks the wrong one.
        real = leaving("red", 100.0, y0=-2.0, y1=1.9, x=0.0)    # 10 samples
        rival = leaving("red", 100.2, y0=-0.6, y1=2.0, x=0.6)   # 7, but higher
        assert len(real) > len(rival)
        got = release.find_releases(frames(real, rival), VIEW_Y_MIN)
        assert len(got) == 1
        assert len(got[0].track) == len(real)
        assert got[0].y_exit_m < 1.7        # the real one does not reach as far
```

- [ ] **Step 2: Run it to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest "tests/test_release.py::TestTheStage1Line::test_the_better_sampled_rival_wins_not_the_one_followed_furthest" -p no:cacheprovider -rf`

Expected: FAIL — the kept release is the seven-sample rival, which reaches y = +1.8 against the real delivery's +1.6, so `len(got[0].track) == 7` against `len(real) == 10`.

- [ ] **Step 3: Change the tie-break**

In `src/curling_score/detect/release.py`, replace:

```python
    # One throw at a time: of two sightings inside the separation, keep the
    # one followed further, which is the stone rather than the broom beside it.
    kept: list[Release] = []
    for r in out:
        if kept and r.t - kept[-1].t < MIN_SEPARATION_S:
            if r.y_exit_m > kept[-1].y_exit_m:
                kept[-1] = r
            continue
        kept.append(r)
```

with:

```python
    # One throw at a time: of two sightings inside the separation, keep the
    # better-sampled one, which is the stone rather than whatever crossed
    # beside it.
    #
    # This used to keep the one followed FURTHEST, which worked while a release
    # had to climb three metres and stopped working when it did not. On e6 s6
    # the real delivery carries eight samples and reaches y = +1.82 while the
    # thing 0.60 m beside it carries two and reaches +2.97, so distance now
    # picks the wrong one and sample count picks the right one.
    kept: list[Release] = []
    for r in out:
        if kept and r.t - kept[-1].t < MIN_SEPARATION_S:
            if len(r.track) > len(kept[-1].track):
                kept[-1] = r
            continue
        kept.append(r)
```

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_release.py tests/test_motion.py -p no:cacheprovider -rf`

Expected: all pass. In particular `test_two_sightings_inside_the_separation_are_one_throw` must still pass — its longer trace is also its better-sampled one, so both rules agree there and the test stays honest about what it pins.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/detect/release.py tests/test_release.py
git commit -m "release: break a tie on samples, not on distance followed

Of two sightings inside MIN_SEPARATION_S, find_releases kept the one
followed furthest. That worked while a release had to climb three metres
and stops working now that it does not: on AEqLTgM25Tc e6 s6 the real
delivery carries eight samples and reaches y = +1.82, while the thing
crossing 0.60 m beside it carries two and reaches +2.97.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Verify against the game the spec was measured on

Unit tests pin the behaviour; only the replay shows the number the spec promises. This task changes no source.

**Files:**
- Modify: none.
- Test: the full suite, plus a replay of `AEqLTgM25Tc`.

**Interfaces:**
- Consumes: everything from Tasks 1-3.
- Produces: nothing. A gate.

- [ ] **Step 1: Run the whole suite**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/ -p no:cacheprovider -rf`

Expected: **2 failed, and both of them `tests/test_longview.py::TestAgainstHandMarkedCrossings`.** Those two are a known pre-existing artefact -- `scripts/split_coverage.py::_hand_mark_agreement` runs the colour scan rather than the ds13b detector that actually ships -- and are not this plan's business. Any other failure is a regression: stop and report it rather than adjusting the test.

The suite takes about 14 minutes and roughly 275 MB. Do not run two copies at once; this box has OOM-killed the suite before, which shows up as exit 137 rather than as a test failure.

- [ ] **Step 2: Replay the game**

Requires the replay cache at `~/.cache/curling_replay` and a GPU. Takes about 15 minutes.

```bash
PYTHONPATH=src .venv/bin/python scripts/split_audit.py \
  /home/tcuser/curling-work/ds13/review_out/timeline.json \
  --cache-root ~/.cache/curling_replay \
  --out /tmp/stage1-after.json
```

- [ ] **Step 3: Check the count**

```bash
PYTHONPATH=src .venv/bin/python -c "
import json
d = json.load(open('/tmp/stage1-after.json'))
shots = [(e['end'], s) for e in d for s in e['shots']]
have = [(e, s) for e, s in shots if s['t_release_s'] is not None]
print('shots', len(shots), 'with a release', len(have))
print('missing:', ['e%ds%d' % (e, s['shot']) for e, s in shots
                   if s['t_release_s'] is None])
"
```

Expected, exactly:

```
shots 90 with a release 88
missing: ['e2s16', 'e6s2']
```

88 against the 63 this started at. If the count is 87, `MIN_SAMPLES` did not reach 3 and e2 s12 was dropped. If a rock other than e2 s16 or e6 s2 is missing, something in Tasks 1-3 is refusing a throw the measurement says is there: report which rock rather than adjusting a threshold to make the number come out.

Neither remaining failure is a stage-1 problem. e2 s16 leaves the hack 30.5 s before its arrival against `MAX_LAG_S = 30`, which is the association window and explicitly out of this spec's scope. e6 s2 has no red delivery on the panel at all and is a doubtful shot in the first place -- shot 1 rests at 4367.3 and shot 2 supposedly enters at 4370.0, 2.7 s apart against `MIN_SEPARATION_S = 10`.

- [ ] **Step 4: Check the extra 25 releases did not mispair**

`pair()` matches each release to an arrival of its colour 6-30 s later, and is unchanged by this plan. Going from 63 releases to 88 gives it 25 more chances to pick the wrong arrival, and the spec flags this as measured-by-nobody. Measure it.

```bash
PYTHONPATH=src .venv/bin/python -c "
import json
from collections import Counter
d = json.load(open('/tmp/stage1-after.json'))
shots = [s for e in d for s in e['shots']]
lags = [round(s['t_enter_s'] - s['t_release_s'], 1) for s in shots
        if s['t_release_s'] is not None and s['t_enter_s'] is not None]
lags.sort()
print('paired', len(lags), 'lag min', lags[0], 'median', lags[len(lags)//2],
      'max', lags[-1])
print('reasons:', dict(Counter(s['reason'] for s in shots)))
"
```

Expected: every lag inside 6.0-30.0 by construction, a median in the 10-20 s range the club actually throws at, and **no rock reasoned `hogged`** — this game has none. A cluster of lags hard against 6.0, or `hogged` rocks appearing where there were none, means releases are being matched to the wrong arrivals. Report the rocks rather than widening the window.

- [ ] **Step 5: Confirm nothing reads `y_exit_m` for quality**

Releases now routinely exit around y = +1 rather than +3.5, so anything treating a large `y_exit_m` as a confidence signal would silently downgrade. This was checked while planning and found clean; confirm it still is.

```bash
grep -rn "y_exit_m" --include=*.py src/ scripts/
```

Expected: matches only in `src/curling_score/detect/release.py` (the field, and the tie-break comment), `scripts/replay_end.py` (prints it) and `scripts/split_audit.py` (records it). No production consumer. If a new one has appeared in `src/` outside `release.py`, read it before going further.

- [ ] **Step 6: Commit**

Nothing to commit unless Step 5 turned something up. If the working tree is clean, say so and stop.

---

## Notes for the executor

**Do not retune anything to make a number come out.** Every constant in this plan has a measurement behind it and the measurement is in the comment beside it. If the replay disagrees with the plan, the interesting thing is the disagreement.

**`MIN_SPEED_M_S` and `MAX_SPEED_M_S` stay exactly as they are.** They look like the same kind of fitted threshold this plan is removing, and they are not: among tracks acquired above the T-line, deliveries run 1.47-2.15 m/s and everything else 0.12-0.61 m/s, a gap with nothing in it.

**The centre-line bound is already shipped** in commit `44d2119` and is what makes all of this work -- it takes the candidate tracks per shot from a mean of 1.91 down to 1.22, and leaves 86 of 90 rocks with exactly one track crossing the line. Do not change `CENTRE_FRACTION` while implementing this.
