"""One sheet's practice, watched: every delivery reported as it comes to rest.

A `PracticeWatch` owns a growing recording of one sheet's stream and turns it
into a list of throws. Each call to :meth:`step` does the one most useful
thing there is to do, and says whether it did anything:

* **Calibrate** once the recording reaches ``since_s``. Everything before it
  is the lookback: footage from before Start, there only to calibrate from
  (`live.session.VideoPipeline.calibrate`, medians of a mostly empty sheet).
  A calibration missing a hog line or a side view is tried again every
  CALIB_RETRY_S, keeping the best, until CALIB_TRIES.
* **Watch:** detect both panels from where the last step stopped to just
  behind the head, at most STEP_MAX_S at a time. Find the arrivals each panel
  confirmed and the releases each saw, pair them, measure each new throw
  (`practice.enrich`), and publish.
* **Finish** once the recording has ended and been read to its end.

Reading starts a delivery's look-back before Start, so a throw just after
Start is judged on as much as any other; a throw released before Start is
left out. Everything that touches video goes through ``pipeline``, so the
watch's own decisions are testable without any.
"""

import logging
import time

from curling_score.detect import delivery
from curling_score.geometry.calibrate import CalibrationError
from curling_score.live.session import _worth
from curling_score.practice import throws
from curling_score.practice.enrich import OTHER_HOUSE
from curling_score.practice.finder import ArrivalFinder, Buffer
from curling_score.practice.releases import ReleaseBook

log = logging.getLogger(__name__)

HOUSES = ("top", "bottom")
# The footage before Start a watch is given to calibrate from: twenty minutes
# of the stream's DVR window, which on a long stream holds about an hour.
LOOKBACK_S = 1200.0
# Both panels, twice the release finder's rate (it is thinned back for that).
DETECT_FPS = 10.0
# The last packets of a growing MPEG-TS may not decode yet.
HEAD_MARGIN_S = 2.0
# Each read enters a TS file 10 s early (frames.seek_lead), so less new
# footage than this is not worth a read; a catch-up is read in pieces so no
# single step holds the lane for long.
STEP_MIN_S = 2.0
STEP_MAX_S = 30.0
CALIB_RETRY_S = 60.0
CALIB_TRIES = 5
# A throw nothing saw released is dated this long before its arrival, as the
# recorded pipeline does (timeline.RELEASE_TO_ARRIVAL_S).
RELEASE_TO_ARRIVAL_S = 15.0


def _thrown_at(arrival, rel) -> float:
    return rel.t if rel is not None else arrival.t_enter - RELEASE_TO_ARRIVAL_S


class PracticeWatch:
    def __init__(self, *, recording, pipeline, models, since_s: float, t0_s: float = 0.0,
                 wall_t0=None, publish=None, clock=time.monotonic, progress=log.info):
        """``since_s`` is Start on the recording's clock; ``t0_s`` is where the
        recording begins on the stream's, which every published time is on.
        ``wall_t0`` is when the recording begins by the wall clock (epoch
        seconds), for a live stream whose own clock is not known."""
        self.rec, self.pipeline, self.models = recording, pipeline, models
        self.since_s, self.t0_s, self.wall_t0 = float(since_s), float(t0_s), wall_t0
        self._stop = False
        self.publish = publish or (lambda doc: None)
        self.clock, self.progress = clock, progress
        self.cal = None
        self.calib_tries = 0
        self.calib_next = float("-inf")
        self.status = "calibrating"
        self.done_s = max(0.0, self.since_s - delivery.REQUIRED_LOOKBACK_S)
        self.buffers = {h: Buffer() for h in HOUSES}
        self.finders = self.books = None
        self.throws: list = []
        self.done = False

    def request_stop(self) -> None:
        """No session is watching any more: read what is recorded, then end."""
        self._stop = True

    def step(self) -> bool:
        if self.done:
            return False
        head, ended = self.rec.head_s(), self.rec.ended()
        if self.finders is None:
            if self._stop:
                self.status, self.done = "ended", True
                self._publish()
                return True
            return self._calibrate(head, ended)
        upto = head if ended else head - HEAD_MARGIN_S
        if upto - self.done_s >= STEP_MIN_S or ((ended or self._stop) and upto > self.done_s):
            self._watch(min(upto, self.done_s + STEP_MAX_S))
            return True
        if ended or self._stop:
            self.status, self.done = "ended", True
            self._publish()
            return True
        return False

    def document(self) -> dict:
        return {"practice": 1, "status": self.status, "t0_s": self.t0_s,
                "wall_t0": self.wall_t0,
                "since_s": round(self.t0_s + self.since_s, 2),
                "recorded_s": round(self.t0_s + self.done_s, 2),
                "throws": list(self.throws)}

    # --- calibrating -------------------------------------------------------------

    def _calibrate(self, head, ended) -> bool:
        if (head < self.since_s and not ended) or self.clock() < self.calib_next:
            return False
        self.calib_tries += 1
        try:
            cal = self.pipeline.calibrate(self.rec.path, head)
        except CalibrationError as exc:
            self.progress(f"practice: calibration {self.calib_tries} failed: {exc}")
            cal = None
        if cal is not None and (self.cal is None or _worth(cal) > _worth(self.cal)):
            self.cal = cal
        self.calib_next = self.clock() + CALIB_RETRY_S
        last_try = self.calib_tries >= CALIB_TRIES or ended
        if self.cal is not None and (self.cal.complete or last_try):
            setups = self.cal.setups
            self.finders = {h: ArrivalFinder(setups[h]) for h in HOUSES}
            self.books = {h: ReleaseBook(setups[h]) for h in HOUSES}
            self.status = "watching"
        elif self.cal is None and last_try:
            self.status, self.done = "failed", True
        self._publish()
        return True

    # --- watching ----------------------------------------------------------------

    def _watch(self, upto) -> None:
        got = self.pipeline.detect(self.rec.path, self.cal.setups, self.done_s, upto,
                                   self.models.detector)
        for h in HOUSES:
            self.buffers[h].extend(got[h])
            self.books[h].update(self.buffers[h].frames)
        self.done_s = upto
        new = []
        # Arrivals claim their releases before any release is given up on.
        for h in HOUSES:
            book = self.books[OTHER_HOUSE[h]]
            new += [(h, a, book.claim(a)) for a in self.finders[h].new(self.buffers[h])]
        for throwing in HOUSES:
            new += [(OTHER_HOUSE[throwing], None, r)
                    for r in self.books[throwing].unarrived(upto)]
        added = 0
        for house, arrival, rel in sorted(new, key=lambda n: _thrown_at(n[1], n[2])):
            if _thrown_at(arrival, rel) < self.since_s:
                continue                       # thrown before Start
            shot = self.pipeline.enrich(
                self.rec.path, self.cal, self.models, house=house, arrival=arrival,
                release=rel, house_frames=self.buffers[house].frames,
                throw_frames=self.buffers[OTHER_HOUSE[house]].frames)
            self.throws.append(throws.throw_record(shot, house=house, t0_s=self.t0_s))
            added += 1
        if added:
            self._publish()

    def _publish(self) -> None:
        self.publish(self.document())
