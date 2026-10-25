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
track per stone across the whole sequence and treat gaps in it as occlusion.
The house over a window is then the tracks seen through most of it
(`stones_in_window`), and the window ends where a settled stone is first moved
by hand (`until_disturbed`).
"""

from dataclasses import dataclass, field

# Stones are 0.284 m across; anything inside this is the same stone jittering.
MATCH_TOLERANCE_M = 0.12
# A track must be seen this often to be a stone rather than a stray detection.
MIN_TRACK_FRAMES = 3


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
    def last(self) -> int:
        return self.indices[-1]

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


# A stone has settled once it has held within STILL_M of one place for
# SETTLED_HOLD_S; it has been disturbed once it then leaves that place by more
# than DISTURBED_M. Jitter is a few centimetres and a stone is 0.28 m across.
SETTLED_HOLD_S = 1.5
STILL_M = 0.10
DISTURBED_M = 0.25
# A settled stone that a moving stone came this close to in the moment before
# it left was struck: two radii, and 16 cm for a fast stone's frame-to-frame
# step. Measured on s_0kdoX2XVKN5e2lBUF end 4 rock 11, where the struck stone
# was last seen 0.30 m away, the frame before the stone it hit moved.
STRUCK_M = 0.45
STRUCK_WITHIN_S = 0.5
# A stone of that colour still at the place in more than this share of the
# frames after it "left" never left: its track followed a stray detection off
# while a player stood over it. Nine of thirteen mid-end cuts over sixteen
# games were that.
VACATED_MAX_SHARE = 0.2


def _leavings(tr, ts, hold_s, still_m, moved_m):
    """(sighting, home) each time this track settles and is then moved away.

    A stone still rolling at the start of the window has no home until it
    stops; one knocked to a new place by a later hit gets a new home there.
    """
    n, k0 = len(ts), 0
    while k0 < n and ts[-1] >= ts[k0] + hold_s:
        held = [k for k in range(k0, n) if ts[k] <= ts[k0] + hold_s]
        hx = sum(tr.xs[k] for k in held) / len(held)
        hy = sum(tr.ys[k] for k in held) / len(held)
        dist = [((tr.xs[k] - hx) ** 2 + (tr.ys[k] - hy) ** 2) ** 0.5 for k in range(n)]
        if any(dist[k] > still_m for k in held):
            k0 += 1                        # not settled here yet
            continue
        leave = next((k for k in range(held[-1] + 1, n) if dist[k] > still_m), None)
        if leave is None:
            return
        if max(dist[leave:]) > moved_m:
            yield leave, (hx, hy)
        k0 = leave


def until_disturbed(frames, hold_s: float = SETTLED_HOLD_S,
                    still_m: float = STILL_M, moved_m: float = DISTURBED_M):
    """The frames up to the first moment a settled stone is moved by hand.

    After the last rock of an end nothing is thrown, but the house does not
    stay put: the players push the stones off a few seconds after it stops,
    and averaging over that reads a house that never existed. A pushed stone
    keeps its track -- a broom moves it a few centimetres a frame -- so the
    clearing shows as a stone that had settled leaving its place, with no
    stone moving beside it: the thing pushing it is not a stone.

    What does not end the window: an occluded stone, which comes back to the
    same place, as does a track that wandered off after a stray detection while
    the stone stayed put; a struck stone still rolling when the shooter stopped, which
    never settled before it moved; and a settled stone that such a stone runs
    into, which is still the shot.
    """
    frames = [(t, list(d)) for t, d in frames]
    tracks = build_tracks(frames)
    times = [[frames[i][0] for i in tr.indices] for tr in tracks]

    def moving(tr, k):
        # Too fast to track is a string of short tracks; tracked, it has moved
        # in the last three sightings.
        if len(tr.indices) < MIN_TRACK_FRAMES:
            return True
        j = max(0, k - 3)
        return ((tr.xs[k] - tr.xs[j]) ** 2 + (tr.ys[k] - tr.ys[j]) ** 2) ** 0.5 > still_m

    def vacated(home, i, color):
        after = frames[i:]
        stayed = sum(any(d.color == color and ((d.x_m - home[0]) ** 2
                                               + (d.y_m - home[1]) ** 2) ** 0.5 <= still_m
                         for d in dets) for _, dets in after)
        return stayed <= VACATED_MAX_SHARE * len(after)

    def struck(tr, home, t):
        for other, ts in zip(tracks, times):
            if other is tr:
                continue
            for k, tk in enumerate(ts):
                if (t - STRUCK_WITHIN_S <= tk <= t and moving(other, k)
                        and ((other.xs[k] - home[0]) ** 2
                             + (other.ys[k] - home[1]) ** 2) ** 0.5 <= STRUCK_M):
                    return True
        return False

    cut = len(frames)
    for tr, ts in zip(tracks, times):
        for k, home in _leavings(tr, ts, hold_s, still_m, moved_m):
            if vacated(home, tr.indices[k], tr.color) and not struck(tr, home, ts[k]):
                cut = min(cut, tr.indices[k])
                break
    return frames[:cut]


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
