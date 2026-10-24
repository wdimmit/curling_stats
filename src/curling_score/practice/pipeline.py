"""The watch's pipeline for real: every call reads the recording.

Calibration is the live session's (`live.session.VideoPipeline`): the same
medians of the same keyframes, from whatever the recording holds so far. A
saved calibration is checked against the house rings in a few of them
(:meth:`PracticePipeline.check_saved`) instead of calibrating anew.
Detection reads the growing recording itself -- no strip proxy, no detection
cache, as for a live end -- and decodes each frame once for both panels.
"""

import logging

from curling_score.cameras import calstore
from curling_score.detect import sequence
from curling_score.live.session import VideoPipeline
from curling_score.practice import enrich as enrich_mod
from curling_score.practice.watch import DETECT_FPS, HOUSES

log = logging.getLogger(__name__)

# How many keyframes a saved calibration is checked against. They are spread
# over what the recording holds at the check, which is CHECK_MIN_S seconds or
# more at a keyframe a second (CHECK_RETRY_S for a check made again), so with
# about ten there it is the first eight; the median over them drops whoever is
# walking across the house.
CHECK_FRAMES = 8


class PracticePipeline(VideoPipeline):
    def __init__(self, *, fps: float = DETECT_FPS, **kw):
        super().__init__(**kw)
        self.fps = fps

    def check_saved(self, path, from_s: float, until_s: float, cal) -> calstore.HouseCheck:
        """Whether ``cal`` still fits ``path`` between ``from_s`` and
        ``until_s`` (`calstore.check_house`): a check made again reads only
        the frames recorded since the one before."""
        from curling_score.ingest import frames as F

        frames = F.spread_keyframes(path, until_s, count=CHECK_FRAMES, start_s=from_s or None)
        return calstore.check_house(cal, frames)

    def detect(self, path, setups, from_s, until_s, detector) -> dict:
        got = sequence.detect_spans(path, [(setups[h], from_s, self.fps) for h in HOUSES],
                                    until_s, detector)
        return dict(zip(HOUSES, got))

    def enrich(self, path, cal, models, **kw):
        return enrich_mod.enrich(path, cal.setups, cal.sideviews, models, **kw)
