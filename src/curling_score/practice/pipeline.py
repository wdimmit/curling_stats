"""The watch's pipeline for real: every call reads the recording.

Calibration is the live session's (`live.session.VideoPipeline`): the same
medians of the same keyframes, from whatever the recording holds so far.
Detection reads the growing recording itself -- no strip proxy, no detection
cache, as for a live end -- and decodes each frame once for both panels.
"""

import logging

from curling_score.detect import sequence
from curling_score.live.session import VideoPipeline
from curling_score.practice import enrich as enrich_mod
from curling_score.practice.watch import DETECT_FPS, HOUSES

log = logging.getLogger(__name__)


class PracticePipeline(VideoPipeline):
    def __init__(self, *, fps: float = DETECT_FPS, **kw):
        super().__init__(**kw)
        self.fps = fps

    def detect(self, path, setups, from_s, until_s, detector) -> dict:
        got = sequence.detect_spans(path, [(setups[h], from_s, self.fps) for h in HOUSES],
                                    until_s, detector)
        return dict(zip(HOUSES, got))

    def enrich(self, path, cal, models, **kw):
        return enrich_mod.enrich(path, cal.setups, cal.sideviews, models, **kw)
