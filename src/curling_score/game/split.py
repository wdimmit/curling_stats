"""The long split: hog line to hog line, timed at the paint.

Both hog lines are in view, one in each overhead panel, and a stone crosses
them at 5-10 cm per sample. So the split is two line crossings and a distance
that is exact by construction -- nothing extrapolated at either end.

This module used to say the opposite, and the mistake is worth keeping written
down because it is easy to make again. The panels *report* reaching about
+4.6 m against a hog line at 6.401, so the hog line looked out of view and the
red line across the top of every panel looked like something else at +4.47.
Both readings came from taking the panel's metres at face value that far out.
They are not trustworthy there: ``geometry/calibrate.py`` fits a single
``px_per_m`` from the house rings, and measured against the composite's side
cameras, the along-sheet scale falls to roughly a third of that by the top of
the frame. A stone the panel calls 4.44 m out is really about 6.3 m out. The
panels reach past the hog line; they simply cannot say so in metres.

None of which matters to a crossing. A tripwire needs to know where the paint
sits in the panel's own numbers, not what those numbers mean -- so the fix is
one measured constant and no model of the distortion at all. The distortion is
still there, and still wrong for anything that reads a distance up-sheet.
"""

from dataclasses import dataclass

from curling_score.geometry import constants as C

# Where the hog line's paint sits in a panel's own coordinates.
#
# Measured by hand against the paint with ``scripts/mark_hog.py``: the side
# camera at the far end sees the throwing end's hog line square on, and a
# person marked the frame each stone's leading edge touched it. Thirteen
# deliveries across both panels of the reference VOD give 4.441 +- 0.007, the
# two panels agreeing to 1.5 sigma -- one number, not one per panel. Replaying
# those ends, the tripwire reproduces every hand-marked crossing to within
# 0.05 s.
HOG_APPARENT_Y_M = 4.441

# What was marked was the stone's *leading edge* on the line's inside edge, so
# its centre -- which is what the panel tracks -- was one radius short of
# ``TEE_TO_HOGLINE_M``. Timing the same tripwire in both panels therefore
# measures tee-to-tee less that distance at each end, and measures it exactly:
# the two ends are the same physical line, so whatever the marking convention,
# it cancels.
BASELINE_M = C.TEE_TO_TEE_M - 2 * (C.TEE_TO_HOGLINE_M - C.STONE_RADIUS_M)

# How much faster a stone may appear to be crossing the second hog line than
# the first before the pairing behind it is disbelieved. In exact arithmetic
# this is 1.0 -- a stone only ever slows -- and the slack covers two estimates
# taken 20 s apart from different cameras at different sample rates.
SPEED_TOLERANCE = 1.4

# How far the side view and the panel may disagree about the same crossing
# before both are disbelieved. They are independent cameras timing one painted
# line, so a real disagreement means one of them found the wrong object and
# nothing here can say which.
#
# UNMEASURED, and worth knowing that before trusting it. 0.25 was chosen as the
# acceptance bar in the design doc -- "no |t_hog - panel| above 0.25 s on the
# overlap" -- and then reused here as a runtime veto, which is not the same
# job: a bar says what would be good enough, a veto decides what ships.
#
# It is now the single largest source of refusals. scripts/split_coverage.py
# measured 25 of 55 unpublished crossings lost to this gate on one video, and
# the pre-veto disagreement over 48 paired crossings had a median of 0.261 s --
# just above the threshold, so roughly half the comparable population fails it.
# That is a threshold sitting in the middle of its own distribution, which is
# where a number does the most damage per unit of wrongness.
#
# MEASURED, 2026-09-16, exactly as this note asked. On AEqLTgM25Tc, 60 paired
# crossings with the side view proposed by ds13b:
#
#   ...0.21 0.22 0.25 0.25 0.26 0.26 0.27 0.28 0.28 0.28 0.28 0.29
#   [ nothing whatever between 0.288 and 0.801 ]
#   0.80 0.85 0.86 0.87 0.89 0.89 0.90 0.93 0.94 0.96 0.97 0.98
#
# So it IS bimodal, the valley is 0.513 s wide and empty, and 0.25 sits on the
# near cluster's shoulder rather than in the valley: 11 of 22 refusals are
# between 0.25 and 0.29, refused by a hair.
#
# And the far cluster is not a tail of mispairings. All twelve have the same
# sign -- the panel reads 0.80-0.98 s LATE every time. Three of the refused
# shots have an independent hand mark in datasets/hogmarks, and they say which
# camera is wrong:
#
#   shot          hand mark    side view      err      panel      err
#   e2s8 yellow    8454.967    8454.953   -0.014   8455.920   +0.953
#   e3s7 yellow    9410.767    9410.774   +0.007   9410.496   -0.271
#   e5s2 red      11339.033   11339.006   -0.027  11338.717   -0.316
#
# The side view is right to 0.03 s and the panel is wrong by up to 0.95 s. This
# gate is therefore discarding CORRECT crossings because an unreliable camera
# disagrees -- and the panel's tripwire sits at row 13 of a 516-row panel, at
# the very frame edge, which is where its own docstring says the scale is
# collapsing fastest.
#
# NOT CHANGED HERE. Raising the cut into the valley would recover the 11 near
# refusals and still reject all 12 far ones, but that is a decision about what
# ships, and the ~0.9 s panel offset wants explaining first: twelve shots all
# late by the same amount is a bug with a cause, not scatter.
CROSS_CHECK_S = 0.25


@dataclass(frozen=True)
class Split:
    """One stone timed over ``baseline_m`` of sheet, hog line to hog line."""

    seconds: float
    baseline_m: float
    t_start: float       # crossing the throwing end's hog line
    t_end: float         # crossing the playing end's hog line
    # 0.0 when the far crossing was observed. Positive when it was reached for,
    # in the panel's y units, so a reader can tell an exact split from one that
    # cannot be checked against anything. See FAR_EXTRAPOLATION_MAX_U.
    far_reach: float = 0.0

    @property
    def speed_m_s(self) -> float:
        """Mean speed over the baseline -- the number that reads as ice speed."""
        return self.baseline_m / self.seconds if self.seconds else 0.0


def crossing_time(track, y_line: float) -> float | None:
    """When a track passed ``y_line``, or None if it never bracketed it.

    Linear between the two samples either side. Near the hog line a stone
    moves 0.1-0.2 of the panel's units per sample, so the interpolation is
    worth far more than snapping to the nearer one.
    """
    pts = [(float(t), float(y)) for t, _x, y in track]
    for (t0, y0), (t1, y1) in zip(pts, pts[1:]):
        if (y0 - y_line) * (y1 - y_line) <= 0 and y0 != y1:
            return t0 + (y_line - y0) * (t1 - t0) / (y1 - y0)
    return None


# How far past the end of a track the far hog crossing may be reached for, in
# the panel's own y units. NOT metres -- the along-sheet scale falls to about a
# third by the top of the frame, and it was taking those units for metres that
# made the previous extrapolation overshoot the paint by a second (b4fd75b).
#
# Measured over all 139 tracks on VXU9xwmugRg that DO cross the line, by hiding
# everything above a cut and scoring the extrapolation against the answer:
#
#     reach    median   p90     worst   within 0.15 s
#     0.011    0.028    0.110   0.653      92%
#     0.041    0.047    0.166   0.653      87%
#     0.061    0.059    0.181   0.653      82%
#     0.141    0.066    0.213   0.653      82%
#
# 0.05 keeps the reach where 9 in 10 land inside the 0.15 s a crossing is
# judged by. It is a judgement about how much unverifiable error to accept, not
# a threshold with a physical meaning: an extrapolated crossing has nothing to
# check it against, unlike the observed ones the panel tripwire corroborates.
FAR_EXTRAPOLATION_MAX_U = 0.05

# Points used for the fit. Four is what a truncated track reliably has near the
# line, and more made the tail worse rather than better -- a quadratic over ten
# reached a 1.65 s worst case against this fit's 0.65 s.
_FAR_FIT_POINTS = 4


def far_crossing(track, y_line: float, *, max_reach: float = 0.0):
    """``(t, reach)`` for a track crossing ``y_line``, extrapolating a little.

    ``reach`` is 0.0 when the crossing was observed -- the track bracketed the
    line and the time is interpolated between two real samples. It is positive
    when the track began below the line and the time was extrapolated back up
    to it, and then it says how far, so a caller can mark the result.

    Returns ``(None, 0.0)`` when the line is further than ``max_reach`` beyond
    the track's far end, which is the same refusal as before for anything the
    gate does not cover.
    """
    seen = crossing_time(track, y_line)
    if seen is not None:
        return seen, 0.0
    pts = sorted(((float(t), float(y)) for t, _x, y in track),
                 key=lambda p: -p[1])
    if len(pts) < _FAR_FIT_POINTS:
        return None, 0.0
    reach = y_line - pts[0][1]
    if not 0.0 < reach <= max_reach:
        return None, 0.0
    near = pts[:_FAR_FIT_POINTS]
    n = len(near)
    my = sum(p[1] for p in near) / n
    mt = sum(p[0] for p in near) / n
    den = sum((p[1] - my) ** 2 for p in near)
    if den == 0:
        return None, 0.0
    slope = sum((p[1] - my) * (p[0] - mt) for p in near) / den    # dt/dy
    return mt + (y_line - my) * slope, reach


def hog_crossing(track) -> float | None:
    """When a tracked stone crossed the hog line's paint.

    Never extrapolated. Carrying a speed even a tenth of a unit past the end of
    a track would be guesswork here: that is exactly where the panel's scale is
    collapsing fastest, so the apparent speed is falling steeply and means
    nothing on its own. A throw the camera lost short of the line has no split,
    and a count of those is the only honest way to say how much of a game this
    measures.
    """
    return crossing_time(track, HOG_APPARENT_Y_M) if track else None


def speed_at_line(track, y_line: float = HOG_APPARENT_Y_M) -> float | None:
    """How fast a track was crossing ``y_line``, in the panel's own units.

    Not metres per second, and deliberately so. The point of reading it here
    is that both hog lines sit at the same place in their own panel, so two
    speeds measured there are distorted the same way and can be compared
    without either being converted to anything.
    """
    pts = [(float(t), float(y)) for t, _x, y in track or ()]
    for (t0, y0), (t1, y1) in zip(pts, pts[1:]):
        if (y0 - y_line) * (y1 - y_line) <= 0 and y0 != y1 and t1 > t0:
            return abs(y1 - y0) / (t1 - t0)
    return None


def long_split(release, delivery, *, t_hog=None, v_hog=None) -> Split | None:
    """The split for one shot, or None when either end could not be timed.

    Hog line to hog line: ``t_hog`` is the throwing end, from the side view
    (``game/hogtime.py``), and the far crossing is the arriving end's overhead
    panel. The panel's own tripwire at the throwing end is kept only to check
    that answer -- never to stand in for it, because two methods inside one
    game are not comparable with each other.

    ``release`` is OPTIONAL, and that is a deliberate loosening. It feeds
    neither end of the arithmetic -- only two checks, both already conditional
    on data it may not carry. Requiring it anyway refused 35 shots on
    VXU9xwmugRg that had both crossings and would have published: the overhead
    camera loses about 40% of throws before the hog line, which is the whole
    reason the side view exists, and demanding a release put that loss back.

    What a release does buy is the mispairing check, and without one ``v_hog``
    stands in: the side view's speed at the near line, in real metres.
    """
    if delivery is None or t_hog is None:
        return None
    panel = hog_crossing(getattr(release, "track", ()) if release else ())
    if panel is not None and abs(panel - t_hog) > CROSS_CHECK_S:
        return None
    start = t_hog
    end, far_reach = far_crossing(getattr(delivery, "track", ()) or (),
                                  HOG_APPARENT_Y_M,
                                  max_reach=FAR_EXTRAPOLATION_MAX_U)
    if end is None or end <= start:
        return None
    # The pairing behind this is only as good as a 6-30 s arrival window, and
    # on real ends it puts draws against a release 10 s earlier where the clean
    # ones run 18-20. A stone only ever slows, so it cannot be crossing the far
    # hog line faster than it crossed the near one: if it is, the two were not
    # the same stone. Refuse it rather than publish a number that cannot be true.
    #
    # Comparing the two crossings, rather than a speed against a distance, is
    # what keeps this honest. The panel's metres are not trustworthy this far
    # up (see the module docstring), so the old form of this check -- mean
    # speed over the baseline against the slide speed -- had one side in real
    # metres and the other in the panel's, and threw away good splits.
    near = speed_at_line(getattr(release, "track", ())) if release else None
    far = speed_at_line(getattr(delivery, "track", ()))
    if near and far and far > near * SPEED_TOLERANCE:
        return None
    if near is None and v_hog:
        # No release track, so no panel speed to compare the far crossing
        # against. A stone only ever slows, so its mean speed over the baseline
        # cannot exceed the speed it crossed the first line at -- and `v_hog`
        # is in real metres from the side view's perspective solve, so this is
        # a distance against a speed in the same units. A delivery mispaired
        # with a stone that arrived earlier shows up here as a mean speed the
        # near crossing cannot account for.
        mean = BASELINE_M / (end - start)
        if mean > v_hog * SPEED_TOLERANCE:
            return None
    return Split(seconds=end - start, baseline_m=BASELINE_M,
                 t_start=start, t_end=end, far_reach=far_reach)
