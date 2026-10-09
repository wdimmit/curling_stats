"""Deliveries found while the recording is still growing, each reported once.

The recorded pipeline finds an end's deliveries in one pass over the whole end
(`analyze.build_one_end`). A practice card is due seconds after the stone
stops, so here the same finder, `detect.delivery.find_deliveries`, runs again
and again over a rolling window of one panel's detections, and a delivery is
reported the first time it is confirmed.

The finder's evidence reaches back `delivery.REQUIRED_LOOKBACK_S` before a
stone comes into view (was its resting place empty before?), so only the
deliveries with that much window behind them are judged. An older one was
judged on an earlier, fuller window; a newer one waits for its rest to be
confirmed.
"""

import bisect

from curling_score.detect import delivery

# The detections each panel keeps: a delivery's look-back (36 s), the longest
# flight from entering the panel to a confirmed rest (~25 s), and slack.
KEEP_S = 90.0
# The same delivery found again in a later window: the same colour, entering
# within this. Two stones of one colour cannot come down a sheet this close.
SAME_ENTRY_S = 3.0


class Buffer:
    """One panel's detections over the last ``keep_s`` seconds, in time order."""

    def __init__(self, keep_s: float = KEEP_S):
        self.keep_s = keep_s
        self.frames: list = []

    @property
    def start_s(self):
        return self.frames[0][0] if self.frames else None

    @property
    def head_s(self):
        return self.frames[-1][0] if self.frames else None

    def extend(self, frames) -> None:
        """Add the frames newer than any held, and forget what has aged out."""
        head = self.head_s
        self.frames.extend((t, d) for t, d in frames if head is None or t > head + 1e-6)
        if self.frames:
            floor = self.frames[-1][0] - self.keep_s
            cut = bisect.bisect_left([t for t, _ in self.frames], floor)
            del self.frames[:cut]


class ArrivalFinder:
    """One panel as the house thrown to: its deliveries, each reported once."""

    def __init__(self, setup):
        self.setup = setup
        self.reported: list = []

    def new(self, buffer: Buffer) -> list:
        """The deliveries confirmed in ``buffer`` and not reported before, in
        order of arrival."""
        if not buffer.frames:
            return []
        judged_from = buffer.start_s + delivery.REQUIRED_LOOKBACK_S
        found = delivery.find_deliveries(
            buffer.frames, view_x_limit_m=self.setup.view_x_limit_m,
            view_y_min_m=self.setup.view_y_min_m)
        out = []
        for d in sorted(found, key=lambda d: d.t_enter):
            if d.t_enter < judged_from or self._reported(d):
                continue
            self.reported.append(d)
            out.append(d)
        return out

    def _reported(self, d) -> bool:
        return any(r.color == d.color and abs(r.t_enter - d.t_enter) <= SAME_ENTRY_S
                   for r in self.reported)
