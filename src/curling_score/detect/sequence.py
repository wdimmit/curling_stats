"""Detections for one panel over one span of a video, cached.

This is the expensive half of the pipeline -- about a minute of GPU time per
end -- and the one place three callers had each written their own batching
loop. Going through here gives all of them the cache, and keeps the batching
rule (never hold a whole end of decoded panels in memory) in a single place.
"""

from __future__ import annotations

from curling_score.detect import cache
from curling_score.ingest import frames as F

# Enough frames to keep a GPU busy, few enough that a 16-minute end at 10 fps
# does not sit in memory as 4.4 GB of decoded panels.
BATCH = 64


def detect_end(path, setup, end, fps: float, detector=None,
               use_cache: bool = True):
    """Detections for one end, with enough run-up to judge its first shot.

    Delivery detection asks what the sheet looked like *before* a stone
    arrived, reaching back half a minute. Starting at the end's own boundary
    leaves an opening shot with no history, so it is refused for want of
    evidence rather than on it -- game 1 end 2's first yellow arrives two
    seconds in, and whether it was found came down to which side of a proxy
    keyframe the boundary landed on.
    """
    from curling_score.detect.delivery import REQUIRED_LOOKBACK_S

    start = max(0.0, end.start_s - REQUIRED_LOOKBACK_S)
    return detect_span(path, setup, start, end.end_s, fps, detector, use_cache)


def detect_span(path, setup, start_s: float, end_s: float, fps: float,
                detector=None, use_cache: bool = True):
    """Detections at ``fps`` across ``[start_s, end_s]`` for one panel."""
    from curling_score.detect import rocks

    def produce():
        out = []
        for batch in F.chunked(path, start_s, end_s, fps, size=BATCH,
                               crop=setup.rect):
            if detector is None:
                out.extend((t, rocks.find_stones(img, setup.calib))
                           for t, img in batch)
            else:
                got = detector.find_stones_batch(
                    [img for _, img in batch], setup.calib
                )
                out.extend((t, r) for (t, _), r in zip(batch, got))
        return out

    if not use_cache:
        return produce()
    digest = cache.key(path, setup.rect, setup.calib, detector,
                       start_s, end_s, 1.0 / fps)
    return cache.detections(digest, produce)
