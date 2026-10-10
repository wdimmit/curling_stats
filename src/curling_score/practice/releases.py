"""Throws seen leaving the thrower's house, kept until each is accounted for.

The panel over the thrower's house sees most deliveries leave it
(`detect.release.find_releases`). That gives a practice throw its release
time and its speed out of the hack, pairs it with its arrival at the other
end, and is the only sign at all of a rock that never got there.
"""

from curling_score.detect import release
from curling_score.game.sidereleases import TYPICAL_LAG_S

# The same release found again in a later window: the same colour, within this.
SAME_RELEASE_S = 1.0
# A release first seen this close to the front of the window is that window
# cutting through a climb already found: what is left of it still crosses the
# stage-1 line at a legal speed, "first seen" where the window begins. Every
# real release is first found near the head, long before the front reaches it,
# and a stone takes at most ~3.3 s from the hack to the stage-1 line.
FRONT_GUARD_S = 5.0
# A release nothing arrived from this long after it is reported as a throw
# that never arrived. Not release.MAX_LAG_S (30 s) alone: that bounds the
# release-to-entry lag, and the arrival is only confirmed once the stone has
# rested for several seconds more -- giving up at 30 s would call a slow draw
# hogged before its own arrival could claim it.
NO_ARRIVAL_S = 60.0


def on_grid(frames, fps: float) -> list:
    """``frames`` thinned to ``fps``. The release finder was tuned at
    `release.RELEASE_FPS`, and the watch detects at twice that."""
    step, out, last = 1.0 / fps, [], None
    for t, d in frames:
        if last is None or t >= last + step - 1e-6:
            out.append((t, d))
            last = t
    return out


class ReleaseBook:
    """One panel as the thrower's house: every release it showed, each given
    to at most one arrival, or else reported once as never arriving."""

    def __init__(self, setup):
        self.setup = setup
        self.releases: list = []
        self._done: set[int] = set()     # paired with an arrival, or given up on

    def update(self, frames) -> None:
        if not frames:
            return
        front = frames[0][0]
        for r in release.find_releases(on_grid(frames, release.RELEASE_FPS),
                                       self.setup.view_y_min_m, self.setup.view_x_limit_m):
            if r.t < front + FRONT_GUARD_S:
                continue
            if not any(k.color == r.color and abs(k.t - r.t) <= SAME_RELEASE_S
                       for k in self.releases):
                self.releases.append(r)

    def claim(self, arrival):
        """The release ``arrival`` was thrown from, taken so no other arrival can
        have it; None when none fits. Of several, the one whose flight is
        nearest the typical: partners alternating one colour can leave two
        candidates inside the window, and the earliest is not the likeliest."""
        best = None
        for i, r in enumerate(self.releases):
            lag = arrival.t_enter - r.t
            if (i in self._done or r.color != arrival.color
                    or not release.MIN_LAG_S <= lag <= release.MAX_LAG_S):
                continue
            if best is None or abs(lag - TYPICAL_LAG_S) < abs(best[0] - TYPICAL_LAG_S):
                best = (lag, i)
        if best is None:
            return None
        self._done.add(best[1])
        return self.releases[best[1]]

    def unarrived(self, now_s: float) -> list:
        """The releases nothing arrived from within NO_ARRIVAL_S, each given once."""
        out = []
        for i, r in enumerate(self.releases):
            if i not in self._done and now_s - r.t >= NO_ARRIVAL_S:
                self._done.add(i)
                out.append(r)
        return out
