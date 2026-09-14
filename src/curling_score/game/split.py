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


@dataclass(frozen=True)
class Split:
    """One stone timed over ``baseline_m`` of sheet, hog line to hog line."""

    seconds: float
    baseline_m: float
    t_start: float       # crossing the throwing end's hog line
    t_end: float         # crossing the playing end's hog line

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


def long_split(release, delivery) -> Split | None:
    """The split for one shot, or None when either end could not be timed.

    Never guessed. A shot the throwing camera lost before the hog line, or one
    that entered the playing panel already past it, has no split.
    """
    if release is None or delivery is None:
        return None
    start = hog_crossing(getattr(release, "track", ()))
    end = crossing_time(getattr(delivery, "track", ()) or (), HOG_APPARENT_Y_M)
    if start is None or end is None or end <= start:
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
    near = speed_at_line(getattr(release, "track", ()))
    far = speed_at_line(getattr(delivery, "track", ()))
    if near and far and far > near * SPEED_TOLERANCE:
        return None
    return Split(seconds=end - start, baseline_m=BASELINE_M,
                 t_start=start, t_end=end)
