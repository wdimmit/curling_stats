"""Find the frames where a stone is actually moving.

Delivery recall is the pipeline's limiting factor, and a stone in flight is the
case it handles worst -- so those frames are the ones worth labelling. They are
also the ones the old label pipeline threw away: ``BACKLOG.md`` records that the
quiet-span filter deleted in-flight stones, a quarter of its removals
travelling down-sheet, and that "a missed delivery deletes exactly the frames
that would teach the model to catch it".

Chance will not supply them. Deliveries arrive about every 60 s and a stone
crosses the panel in about 4 s, so a window of length W holds a flight with
probability roughly ``(W + 4) / 60`` -- 13% for a 4 s clip against 27% for a
12 s one. That is why the clips are half a minute long: ten of them per video
should hold four or five flights.

The flight test itself is not new. :func:`curling_score.train.dataset.is_flight`
already encodes it, tuned on this footage, and reusing it means there is one
definition of what a moving stone looks like rather than two that can drift.
"""

from dataclasses import dataclass

from curling_score.train import dataset

# The sample floor in dataset.is_flight was tuned at the 2 fps of the label
# pass, where eight samples span three and a half seconds. Sampling a clip
# faster makes that floor meaningless, so the duration is stated outright.
MIN_FLIGHT_S = 1.6
DETECT_FPS = 5.0


@dataclass(frozen=True)
class Flight:
    """One stone tracked while it was moving, in absolute video time."""

    color: str
    ts: tuple
    xs: tuple
    ys: tuple

    @property
    def travel_m(self) -> float:
        """How far down-sheet it got, net."""
        return self.ys[0] - self.ys[-1]

    @property
    def duration_s(self) -> float:
        return self.ts[-1] - self.ts[0]

    @property
    def path_m(self) -> float:
        return sum(abs(self.ys[i + 1] - self.ys[i]) for i in range(len(self.ys) - 1))

    @property
    def net_fraction(self) -> float:
        return self.travel_m / self.path_m if self.path_m else 0.0


def find_flights(sequence, min_seconds: float = MIN_FLIGHT_S) -> list[Flight]:
    """Every stone seen travelling down-sheet in one detection sequence.

    ``sequence`` is ``(absolute_t, detections)`` in time order. Times must be
    absolute source time: a clip's own timeline cannot name a frame or link to
    the video.
    """
    from curling_score.detect.delivery import _build_tracks

    window = [(t, list(dets)) for t, dets in sequence]
    found = []
    for track in _build_tracks(window):
        if not dataset.is_flight(track, min_seconds=min_seconds):
            continue
        found.append(Flight(color=track.color, ts=tuple(track.ts),
                            xs=tuple(track.xs), ys=tuple(track.ys)))
    found.sort(key=lambda f: f.ts[0])
    return found


def find_throws(sequence, view_y_min_m: float) -> list[Flight]:
    """Every stone seen *leaving* the thrower's house in one sequence.

    The counterpart to :func:`find_flights`, and the frames the harvest has
    never been able to offer. ``dataset.is_flight`` measures
    ``ys[0] - ys[-1]`` and requires it positive, so it accepts only net
    down-sheet travel; a release climbs, and fails the test by construction.
    Every frame in the motion bin is therefore an arrival, and the model has
    never been shown the throw itself.

    Like ``find_flights`` delegating to ``dataset.is_flight``, this delegates
    to :func:`curling_score.detect.release.find_releases` rather than restating
    what a departing stone looks like -- one definition that the pipeline and
    the training set share, instead of two that drift apart. That also buys the
    entry test, which is the only thing separating a delivery from the
    red-jacketed sweeper running up-sheet beside it.

    ``sequence`` is ``(absolute_t, detections)`` in time order, and
    ``view_y_min_m`` is the panel's back edge -- the same value
    ``find_releases`` is given in the analysis pipeline.
    """
    from curling_score.detect import release

    window = [(t, list(dets)) for t, dets in sequence]
    found = [
        Flight(color=r.color,
               ts=tuple(t for t, _x, _y in r.track),
               xs=tuple(x for _t, x, _y in r.track),
               ys=tuple(y for _t, _x, y in r.track))
        for r in release.find_releases(window, view_y_min_m)
        if r.track
    ]
    found.sort(key=lambda f: f.ts[0])
    return found


def flight_times(flight: Flight, n: int = 3) -> list[float]:
    """``n`` moments spanning a flight, ends included.

    Spread rather than clustered: at about 2 m/s and 62 px/m a stone moves some
    124 px a second and is only 18 px across, so evenly spaced picks show it in
    genuinely different places rather than three times in the same spot.
    """
    if n < 1:
        raise ValueError("need at least one frame")
    samples = list(flight.ts)
    if n >= len(samples):
        return samples
    if n == 1:
        return [samples[len(samples) // 2]]
    step = (len(samples) - 1) / (n - 1)
    return [samples[round(i * step)] for i in range(n)]
