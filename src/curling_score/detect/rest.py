"""Find the moments when the stones are at rest.

Scene motion is the wrong signal. Players stand in the house, sweep through it
and walk across it constantly between shots, so "nothing is moving" almost never
holds. What does hold is that the *stones* stop: after each delivery the
configuration settles and stays put until the next stone arrives.

Detections flicker, though. A skip standing over a stone hides it for seconds
at a time, and on the reference VOD a single settled house read 7 stones, then
6, then 7 again -- three apparent "rest states" for one configuration. Those
occlusions were measured at 5-9 seconds, so no sliding window short enough to
track real play can smooth them away.

The distinction that does work is global rather than local: **an occluded stone
comes back to the same place, a removed one never does**. So we first build a
track per stone across the whole sequence, take its lifespan from first to last
sighting, and treat gaps inside that lifespan as occlusion. The configuration
at any instant is then the set of tracks alive at that instant, and rest states
are the stretches over which that set does not change.
"""

from dataclasses import dataclass, field

# Stones are 0.284 m across; anything inside this is the same stone jittering.
MATCH_TOLERANCE_M = 0.12
# A track must be seen this often to be a stone rather than a stray detection.
MIN_TRACK_FRAMES = 3
MIN_TRACK_DENSITY = 0.25  # share of frames within its own lifespan
# A configuration must hold this long to be a rest state.
MIN_HOLD_S = 4.0


@dataclass
class RestState:
    """A settled configuration of stones, and the window over which it held."""

    t_start: float
    t_end: float
    stones: list
    frames: int = 0

    @property
    def duration_s(self) -> float:
        return self.t_end - self.t_start

    @property
    def t_mid(self) -> float:
        return (self.t_start + self.t_end) / 2.0


def configurations_match(a, b, tolerance: float = MATCH_TOLERANCE_M) -> bool:
    """Whether two detections describe the same stones in the same places."""
    if len(a) != len(b):
        return False
    remaining = list(b)
    for stone in a:
        for i, other in enumerate(remaining):
            if other.color != stone.color:
                continue
            dx = other.x_m - stone.x_m
            dy = other.y_m - stone.y_m
            if (dx * dx + dy * dy) ** 0.5 <= tolerance:
                remaining.pop(i)
                break
        else:
            return False
    return not remaining


@dataclass
class _Track:
    """One stone, followed across the whole sequence."""

    color: str
    xs: list = field(default_factory=list)
    ys: list = field(default_factory=list)
    confs: list = field(default_factory=list)
    indices: list = field(default_factory=list)

    def add(self, d, i):
        self.xs.append(d.x_m)
        self.ys.append(d.y_m)
        self.confs.append(d.confidence)
        self.indices.append(i)

    @property
    def first(self) -> int:
        return self.indices[0]

    @property
    def last(self) -> int:
        return self.indices[-1]

    def density(self) -> float:
        span = self.last - self.first + 1
        return len(self.indices) / span

    def stone(self):
        from curling_score.detect.rocks import Detection

        n = len(self.xs)
        return Detection(
            color=self.color,
            x_m=sum(self.xs) / n,
            y_m=sum(self.ys) / n,
            x_px=0.0,
            y_px=0.0,
            area_px=0.0,
            confidence=sum(self.confs) / n,
        )


def build_tracks(frames, tolerance: float = MATCH_TOLERANCE_M) -> list[_Track]:
    """Group detections across time into one track per resting stone.

    A track absorbs a detection at the same place and colour however long the
    gap since it was last seen, which is what makes a nine-second occlusion
    survive as a single stone.
    """
    tracks: list[_Track] = []
    for i, (_, detections) in enumerate(frames):
        for d in detections:
            best, best_dist = None, tolerance
            for track in tracks:
                if track.color != d.color or track.last == i:
                    continue
                dist = ((track.xs[-1] - d.x_m) ** 2 + (track.ys[-1] - d.y_m) ** 2) ** 0.5
                if dist <= best_dist:
                    best, best_dist = track, dist
            if best is None:
                best = _Track(color=d.color)
                tracks.append(best)
            best.add(d, i)
    return tracks


def find_rest_states(
    frames,
    tolerance: float = MATCH_TOLERANCE_M,
    min_hold_s: float = MIN_HOLD_S,
    min_track_frames: int = MIN_TRACK_FRAMES,
) -> list[RestState]:
    """Reduce a dense sequence of detections to the configurations that held."""
    frames = list(frames)
    if not frames:
        return []

    tracks = [
        t
        for t in build_tracks(frames, tolerance)
        if len(t.indices) >= min_track_frames and t.density() >= MIN_TRACK_DENSITY
    ]

    # Walk the timeline, keeping the set of tracks alive at each frame.
    states: list[RestState] = []
    cur_key = None
    cur_start = cur_last = frames[0][0]
    cur_tracks: list[_Track] = []
    count = 0

    for i, (t, _) in enumerate(frames):
        alive = [tr for tr in tracks if tr.first <= i <= tr.last]
        key = tuple(sorted(id(tr) for tr in alive))
        if key == cur_key:
            cur_last = t
            count += 1
            continue
        if cur_key is not None:
            states.append(
                RestState(cur_start, cur_last, [tr.stone() for tr in cur_tracks], count)
            )
        cur_key, cur_tracks = key, alive
        cur_start = cur_last = t
        count = 1
    states.append(
        RestState(cur_start, cur_last, [tr.stone() for tr in cur_tracks], count)
    )

    kept = [s for s in states if s.duration_s >= min_hold_s]
    if not kept:
        kept = [max(states, key=lambda s: s.duration_s)]

    # Dropping a transient can leave identical neighbours; fuse them.
    merged: list[RestState] = []
    for s in kept:
        if merged and configurations_match(s.stones, merged[-1].stones, tolerance):
            merged[-1].t_end = s.t_end
            merged[-1].frames += s.frames
        else:
            merged.append(s)
    return merged


def stones_in_window(frames, tolerance: float = MATCH_TOLERANCE_M,
                     presence: float = 0.5):
    """The stones settled over a span of frames, at their mean positions.

    A single frame is not trustworthy -- players flicker stones in and out -- so
    a stone counts as present only if it was seen in at least ``presence`` of
    the frames, and its position is averaged over the sightings.
    """
    frames = [(t, list(d)) for t, d in frames]
    if not frames:
        return []
    tracks = build_tracks(frames, tolerance)
    need = presence * len(frames)
    return [t.stone() for t in tracks if len(t.indices) >= need]
