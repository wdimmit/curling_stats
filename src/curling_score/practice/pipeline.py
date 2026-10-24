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
# more at a keyframe a second, so with about ten there it is the first eight;
# the median over them drops whoever is walking across the house.
CHECK_FRAMES = 8


class PracticePipeline(VideoPipeline):
    def __init__(self, *, fps: float = DETECT_FPS, **kw):
        super().__init__(**kw)
        self.fps = fps

    def check_saved(self, path, until_s: float, cal) -> bool:
        """Whether ``cal`` still fits the first ``until_s`` seconds of ``path``
        (`calstore.house_agrees`)."""
        from curling_score.ingest import frames as F

        return calstore.house_agrees(cal, F.spread_keyframes(path, until_s,
                                                              count=CHECK_FRAMES))

    def detect(self, path, setups, from_s, until_s, detector) -> dict:
        got = sequence.detect_spans(path, [(setups[h], from_s, self.fps) for h in HOUSES],
                                    until_s, detector)
        return dict(zip(HOUSES, got))

    def enrich(self, path, cal, models, **kw):
        return enrich_mod.enrich(path, cal.setups, cal.sideviews, models, **kw)
