"""The long split: how long a stone takes to run down the sheet.

A long split is conventionally hog line to hog line, and that is not something
this footage can be asked for. The two overhead panels reach about +4.6 m
up-sheet from their own tee against a hog line at 6.401 m, so *neither* hog
line is in view. There is a painted red line near the top of every panel, which
is easy to mistake for it; it measures a consistent +4.47 m and is something
else. The stone's apparent size confirms the scale is honest out there -- a
handle subtends 21.8 px at the tee and 21.3 px at +4 m -- so this is a fact
about where the cameras point, not about the calibration.

What the footage does support is a split over a *stated* baseline, and the two
ends of it are very different problems.

The arrival end is read outright. The stone is tracked from the top of the
panel down to rest, so a line placed inside that range is genuinely crossed and
the crossing is interpolated between two samples 5-10 cm apart.

The throwing end is reached by extrapolation, and that is safe here for the one
reason it is unsafe everywhere else in this codebase. ``delivery.py`` warns that
extrapolating a velocity forward "does not work, and was measured failing" --
but that concerns a noisy end-of-track estimate of a stone that is nearly
stopped. A release is the opposite: the thrower is still sliding with the stone,
so it climbs at an essentially constant 1.5-2.1 m/s over 4.5-6.7 m of clean
track. Fitting that whole climb and carrying it the last 1.7-3.9 m to the hog
line costs a few hundredths of a second.

Going the other way -- extrapolating the arrival back to the far hog line --
was measured and rejected. Fitting constant deceleration to each of 161 tracks
and predicting a crossing just 1.0 m beyond the data missed by a median of
0.71 s, and the hog line is 2.4 m beyond the median track start.
"""

from dataclasses import dataclass

from curling_score.geometry import constants as C

# Where the arrival is timed, in the playing house's own metres. Measured over
# 200 tracked shots: tracks start at a median +4.02 m, p10 +3.44, so a line
# here is crossed while genuinely tracked by about nine shots in ten. Moving it
# further up-sheet lengthens the baseline but loses the shots that lock on late.
ARRIVAL_LINE_Y_M = 3.4

# How much of the climb to the hog line may be extrapolated. Releases are
# followed to +2.5..+4.7 m, so the gap is 1.7-3.9 m; beyond that the throw was
# lost early enough that its slide speed is no longer worth carrying.
MAX_EXTRAPOLATION_M = 4.0

# The distance actually timed: tee to tee, less the hog line at the throwing
# end and the arrival line at the other.
BASELINE_M = C.TEE_TO_TEE_M - C.TEE_TO_HOGLINE_M - ARRIVAL_LINE_Y_M

# How far a measured mean speed may exceed the measured slide speed before the
# pairing behind it is disbelieved. A stone only ever slows, so in exact
# arithmetic the mean over the baseline is strictly below the slide speed and
# no allowance is needed; this covers the noise in two independent estimates,
# one of them a least-squares fit over a track the sweepers keep interrupting.
SPEED_TOLERANCE = 1.10


@dataclass(frozen=True)
class Split:
    """One stone timed over ``baseline_m`` of sheet."""

    seconds: float
    baseline_m: float
    t_start: float       # crossing the throwing end's hog line
    t_end: float         # crossing ARRIVAL_LINE_Y_M in the playing house
    extrapolated_m: float  # how much of the throwing end was not seen

    @property
    def speed_m_s(self) -> float:
        """Mean speed over the baseline -- the number that reads as ice speed."""
        return self.baseline_m / self.seconds if self.seconds else 0.0


def crossing_time(track, y_line: float) -> float | None:
    """When a track passed ``y_line``, or None if it never bracketed it.

    Linear between the two samples either side. At 10 fps a stone near the top
    of the panel moves 5-10 cm per sample, so the interpolation is worth far
    more than snapping to the nearer one.
    """
    pts = [(float(t), float(y)) for t, _x, y in track]
    for (t0, y0), (t1, y1) in zip(pts, pts[1:]):
        if (y0 - y_line) * (y1 - y_line) <= 0 and y0 != y1:
            return t0 + (y_line - y0) * (t1 - t0) / (y1 - y0)
    return None


def _slide_speed(track) -> float | None:
    """The climb rate over the whole release, by least squares.

    Deliberately the whole track and not the last pair: the point of using a
    release is that its speed is steady over metres, and a two-sample estimate
    throws that away in favour of the noise.
    """
    pts = [(float(t), float(y)) for t, _x, y in track]
    if len(pts) < 3:
        return None
    n = len(pts)
    mt = sum(t for t, _ in pts) / n
    my = sum(y for _, y in pts) / n
    var = sum((t - mt) ** 2 for t, _ in pts)
    if var <= 0:
        return None
    speed = sum((t - mt) * (y - my) for t, y in pts) / var
    return speed if speed > 0 else None


def hog_crossing(release) -> float | None:
    """When the thrown stone crossed its own hog line.

    Observed when the release was followed that far, and otherwise carried
    there at the speed it was measured climbing at -- but never further than
    ``MAX_EXTRAPOLATION_M``.
    """
    if release is None or not release.track:
        return None
    seen = crossing_time(release.track, C.TEE_TO_HOGLINE_M)
    if seen is not None:
        return seen
    t_last, _x, y_last = release.track[-1]
    gap = C.TEE_TO_HOGLINE_M - float(y_last)
    if gap <= 0 or gap > MAX_EXTRAPOLATION_M:
        return None
    speed = _slide_speed(release.track)
    if speed is None:
        return None
    return float(t_last) + gap / speed


def extrapolated_m(release) -> float:
    """How much of the climb to the hog line was not actually seen."""
    if release is None or not release.track:
        return 0.0
    if crossing_time(release.track, C.TEE_TO_HOGLINE_M) is not None:
        return 0.0
    return max(0.0, C.TEE_TO_HOGLINE_M - float(release.track[-1][2]))


def long_split(release, delivery) -> Split | None:
    """The split for one shot, or None when either end could not be timed.

    Never guessed. A shot the throwing camera lost early, or whose arrival
    locked on below the line, has no split -- and a count of those is the only
    honest way to say how much of a game this measures.
    """
    if release is None or delivery is None or not getattr(delivery, "track", ()):
        return None
    start = hog_crossing(release)
    if start is None:
        return None
    end = crossing_time(delivery.track, ARRIVAL_LINE_Y_M)
    if end is None or end <= start:
        return None
    seconds = end - start
    # The pairing behind this is only as good as a 6-30 s arrival window, and
    # on real ends it puts draws against a release 10 s earlier where the clean
    # ones run 18-20. A stone only ever slows, so a mean speed above the speed
    # it was measured sliding at did not happen: the two were not the same
    # stone. Refuse it rather than publish a number that cannot be true.
    slide = _slide_speed(release.track) or release.speed_m_s
    if slide and BASELINE_M / seconds > slide * SPEED_TOLERANCE:
        return None
    return Split(seconds=seconds, baseline_m=BASELINE_M, t_start=start,
                 t_end=end, extrapolated_m=extrapolated_m(release))
