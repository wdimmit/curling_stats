"""Detections for one panel over one span of a video, cached.

This is the expensive half of the pipeline -- about a minute of GPU time per
end -- and the one place three callers had each written their own batching
loop. Going through here gives all of them the cache, and keeps the batching
rule (never hold a whole end of decoded panels in memory) in a single place.
"""

from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext

from curling_score.detect import cache
from curling_score.ingest import frames as F

# Enough frames to keep a GPU busy, few enough that a 16-minute end at 10 fps
# does not sit in memory as 4.4 GB of decoded panels.
BATCH = 64
# Batches waiting on the detection thread behind the one it is running. One
# is enough to keep the decoder and the GPU both busy; each is ~30 MB of
# full-resolution panels.
IN_FLIGHT = 1


class _DetectBehind:
    """The detector runs on a thread of its own, a batch behind the decode.

    Decoding stays on the caller's thread -- the only one that ever touches
    PyAV (see ``frames._quiet_ffmpeg``) -- and keeps going while the GPU works
    on the batch before. Each batch's stones land in its ``sink`` in the order
    the batches were handed over, and the batches are the ones a serial loop
    would make, so nothing a detector returns can differ. An end on a live
    recording spent as long decoding as detecting, one after the other.
    """

    def __init__(self, detector):
        self.detector = detector
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="detect")
        self.pending = deque()

    def submit(self, sink, batch, calib):
        got = self.pool.submit(self.detector.find_stones_batch,
                               [img for _, img in batch], calib)
        self.pending.append((sink, [t for t, _ in batch], got))
        self._collect(IN_FLIGHT)

    def _collect(self, keep):
        while len(self.pending) > keep:
            sink, times, got = self.pending.popleft()
            sink.extend(zip(times, got.result()))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *_):
        try:
            if exc_type is None:
                self._collect(0)
        finally:
            self.pool.shutdown(wait=True)


def detect_end(path, setup, end, fps: float, detector=None,
               use_cache: bool = True, from_s: float | None = None):
    """Detections for one end, with enough run-up to judge its first shot.

    Delivery detection asks what the sheet looked like *before* a stone
    arrived, reaching back half a minute. Starting at the end's own boundary
    leaves an opening shot with no history, so it is refused for want of
    evidence rather than on it -- game 1 end 2's first yellow arrives two
    seconds in, and whether it was found came down to which side of a proxy
    keyframe the boundary landed on.

    ``from_s`` reaches further back still, to where the caller knows this
    end's run-up begins (see ``analyze.run_up_from``).
    """
    from curling_score.detect.delivery import REQUIRED_LOOKBACK_S

    start = max(0.0, end.start_s - REQUIRED_LOOKBACK_S)
    if from_s is not None:
        start = max(0.0, min(start, from_s))
    return detect_span(path, setup, start, end.end_s, fps, detector, use_cache)


def detect_span(path, setup, start_s: float, end_s: float, fps: float,
                detector=None, use_cache: bool = True):
    """Detections at ``fps`` across ``[start_s, end_s]`` for one panel."""
    from curling_score.detect import rocks

    def produce():
        out = []
        batches = F.chunked(path, start_s, end_s, fps, size=BATCH, crop=setup.rect)
        if detector is None:
            for batch in batches:
                out.extend((t, rocks.find_stones(img, setup.calib))
                           for t, img in batch)
            return out
        with _DetectBehind(detector) as behind:
            for batch in batches:
                behind.submit(out, batch, setup.calib)
        return out

    if not use_cache:
        return produce()
    digest = cache.key(path, setup.rect, setup.calib, detector,
                       start_s, end_s, 1.0 / fps)
    return cache.detections(digest, produce)


def detect_spans(path, specs, end_s: float, detector=None):
    """Detections for several panels from one decode of ``path``.

    ``specs`` are ``(setup, start_s, fps)``; the result is a list per spec, as
    ``detect_span(..., use_cache=False)`` would give it. For a live recording,
    which has no strip proxy and no detection cache: decoding each frame once
    for both panels halves the dominant cost of a live end.
    """
    from curling_score.detect import rocks

    out = [[] for _ in specs]
    batches = [[] for _ in specs]
    behind = _DetectBehind(detector) if detector is not None else None

    def flush(i):
        setup, batch = specs[i][0], batches[i]
        if behind is None:
            out[i].extend((t, rocks.find_stones(img, setup.calib)) for t, img in batch)
        else:
            behind.submit(out[i], batch, setup.calib)
        batches[i] = []

    with behind or nullcontext():
        for i, t, img in F.windows(path, [(start, fps, setup.rect)
                                          for setup, start, fps in specs], end_s):
            batches[i].append((t, img))
            if len(batches[i]) >= BATCH:
                flush(i)
        for i in range(len(specs)):
            if batches[i]:
                flush(i)
    return out
