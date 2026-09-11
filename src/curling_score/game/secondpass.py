"""A second, targeted look for deliveries the first pass rejected.

The first pass has to be strict, because a loose one counts sweepers as stones:
a player in a team-coloured jacket runs alongside the delivery and mimics a
throw in every respect except staying put afterwards. Strictness costs real
deliveries -- chiefly guards, which stop early and can go unconfirmed.

But once a conservative pass is in hand, the rules say a great deal about what
is missing. Teams alternate, so a repeated colour means exactly one delivery is
absent *and names its colour*; each team throws eight, so a team already at
eight cannot be the answer. That turns "find deliveries" into "find one red
delivery in this 40-second window", which a much weaker signal can settle
without letting phantoms in everywhere else.
"""

from dataclasses import dataclass

from curling_score.geometry import constants as C

COLORS = ("red", "yellow")
# Relaxed acceptance inside a gap: we already know a delivery is there.
GAP_MIN_TRAVEL_M = 0.35
GAP_MIN_ENTRY_Y_M = 2.0
# Enough slack to catch a stone whose flight starts just before the window.
SEARCH_PAD_S = 4.0
# Deliveries in the reference game are never closer than about fifteen
# seconds, so anything nearer than this to a delivery already found is that
# same delivery rather than another one.
DUPLICATE_S = 10.0


@dataclass(frozen=True)
class Gap:
    """A window known to contain a delivery, and its colour if determined."""

    start_s: float
    end_s: float
    expected_color: str | None = None

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


def _other(color):
    return "red" if color == "yellow" else "yellow"


def gaps_to_search(found, start_s, end_s):
    """Windows where the rules say a delivery must be, with its colour."""
    ds = sorted(found, key=lambda d: d.t_enter)
    if not ds:
        return [Gap(start_s, end_s, None)]

    thrown = {c: sum(1 for d in ds if d.color == c) for c in COLORS}
    # A team that has already thrown its eight cannot own a missing delivery.
    exhausted = {c for c in COLORS if thrown[c] >= C.STONES_PER_TEAM_PER_END}

    out: list[Gap] = []
    for a, b in zip(ds, ds[1:]):
        if a.color != b.color:
            continue
        want = _other(a.color)
        out.append(Gap(a.t_rest, b.t_enter,
                       None if want in exhausted else want))

    if len(ds) < C.STONES_PER_END and len(exhausted) == 1:
        # Only one team can still be throwing, so every remaining gap is theirs.
        want = next(c for c in COLORS if c not in exhausted)
        out = [Gap(g.start_s, g.end_s, want) for g in out]

    return [g for g in out if g.duration_s > 0]


def search(frames, gaps, known=(), min_travel_m: float = GAP_MIN_TRAVEL_M):
    """Look inside each gap for the one delivery it is known to hold."""
    from curling_score.detect import delivery as D

    frames = [(t, list(d)) for t, d in frames]
    if not frames or not gaps:
        return []
    # Same colour only. Two deliveries of *different* colours cannot be
    # seconds apart either, but excluding on that basis resolves the conflict
    # the wrong way round -- it throws away the candidate the rules asked for
    # in favour of whatever the first pass happened to put there. Leave those
    # to be flagged as the impossible pair they are.
    known_ts = sorted((d.t_enter, d.color) for d in known)

    out = []
    for gap in gaps:
        lo, hi = gap.start_s - SEARCH_PAD_S, gap.end_s + SEARCH_PAD_S
        window = [(t, d) for t, d in frames if lo <= t <= hi]
        if not window:
            continue

        best = None
        for track in D._build_tracks(window):
            # Judged against the whole sequence, not the window: the stone that
            # stayed put has to be visible after the window's end to be seen
            # staying put.
            track = D._tail(track, D._trim_borrowed_start(frames, track))
            if len(track.ts) < 3:
                continue
            if gap.expected_color and track.color != gap.expected_color:
                continue
            if track.ys[0] < GAP_MIN_ENTRY_Y_M:
                continue
            # A gap is padded so a delivery sitting just outside its edge is
            # still reachable, and that padding can reach over a delivery the
            # first pass already has. Finding it a second time reads as two
            # stones from one throw: end 1 reported the same red at
            # (-0.60, -0.53) twice, 0.2 s apart.
            if any(abs(track.ts[0] - kt) < DUPLICATE_S
                   for kt, kc in known_ts if kc == track.color):
                continue
            rest_i = D._rest_index(track)
            end_i = rest_i if (rest_i and rest_i > 0) else len(track.ts) - 1
            if end_i == 0:
                continue
            travelled = track.ys[0] - track.ys[end_i]
            if travelled < min_travel_m:
                continue
            # One delivery is missing, so keep only the most convincing
            # candidate. Distance alone is the wrong yardstick: a sweeper
            # crosses as much sheet as the stone they are following, and in a
            # gap this permissive they are the main thing to guard against. A
            # changed house is what no sweeper can fake, so that ranks first
            # and travel only breaks ties. The change is judged against the
            # whole sequence, not the trimmed window, or the before and after
            # fall outside it.
            rest, claim = D._house_change(frames, track, end_i)
            rank = (claim is not None, travelled)
            if best is None or rank > best[0]:
                best = (rank, track, end_i, rest_i, rest)

        if best is None:
            continue
        _rank, track, end_i, rest_i, rest = best
        rest_x, rest_y = track.xs[end_i], track.ys[end_i]
        if rest is not None:
            rest_x, rest_y = rest
        out.append(
            D.Delivery(
                color=track.color,
                t_enter=track.ts[0],
                t_rest=track.ts[end_i],
                entry_y_m=track.ys[0],
                rest_x_m=rest_x,
                rest_y_m=rest_y,
                travel_m=track.ys[0] - track.ys[end_i],
                came_to_rest=bool(rest is not None or (rest_i and rest_i > 0)),
                reason="gap-search",
                track=D._path(track, end_i),
            )
        )
    return sorted(out, key=lambda d: d.t_enter)
