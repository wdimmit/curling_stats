"""Frame access over a locally cached video.

Two access patterns, deliberately different:

* :func:`keyframe_sweep` decodes only keyframes (~1 per 5 s on these VODs) to
  build a cheap whole-video activity profile. Sweeping a 4-hour stream this way
  takes about a minute.
* :func:`window` decodes every frame in a short range at a requested rate, for
  the dense work of tracking a single shot.

Both yield ``(t_seconds, bgr_ndarray)`` with timestamps taken from the stream's
own PTS, so they line up with YouTube's ``?t=`` deep links.
"""

from dataclasses import dataclass
from pathlib import Path

import av
import numpy as np


@dataclass(frozen=True)
class VideoProbe:
    width: int
    height: int
    duration_s: float
    avg_fps: float


def probe(path) -> VideoProbe:
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        rate = stream.average_rate or stream.guessed_rate
        duration = float(container.duration / av.time_base) if container.duration else 0.0
        return VideoProbe(
            width=stream.codec_context.width,
            height=stream.codec_context.height,
            duration_s=duration,
            avg_fps=float(rate) if rate else 0.0,
        )


def _stream_start(stream) -> float:
    """Seconds to subtract so the first frame sits at t=0.

    Archived livestreams sometimes carry a non-zero container start time, which
    would otherwise offset the entire timeline.
    """
    if stream.start_time is None:
        return 0.0
    return float(stream.start_time * stream.time_base)


def stream_start_s(path) -> float:
    """The absolute source time of a clip's first frame.

    A clip cut with ``ffmpeg -copyts`` keeps the timestamps it had inside the
    full video, so this says where in the VOD it came from -- which is what
    lets a harvested frame name a moment. See :mod:`curling_score.harvest.clips`.
    """
    with av.open(str(path)) as container:
        return _stream_start(container.streams.video[0])


def keyframe_sweep(path, decode: bool = True, start_s=None, end_s=None):
    """Yield ``(t, frame)`` for every keyframe in the video.

    With ``decode=False`` the frames are not converted to arrays and ``None`` is
    yielded in their place, which is much faster when only timing is wanted.

    ``start_s`` seeks before iterating rather than scanning from the beginning,
    which matters for anything that samples a handful of points across a long
    video -- reaching the one-hour mark by scanning costs about ten seconds, and
    it grows from there.
    """
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        stream.codec_context.skip_frame = "NONKEY"
        offset = _stream_start(stream)
        if start_s is not None:
            target = int((start_s + offset) / stream.time_base)
            container.seek(target, backward=True, stream=stream)
        for frame in container.decode(stream):
            if frame.pts is None:
                continue
            t = float(frame.pts * stream.time_base) - offset
            if start_s is not None and t < start_s - 10.0:
                continue
            if end_s is not None and t > end_s:
                break
            yield t, (frame.to_ndarray(format="bgr24") if decode else None)


def window(path, start_s: float, end_s: float, fps: float, crop=None):
    """Yield ``(t, frame)`` sampled at ``fps`` across ``[start_s, end_s]``.

    ``crop`` is ``(x, y, w, h)`` applied before the array is handed back, which
    saves copying the ~85% of each frame we never look at.
    """
    if end_s < start_s:
        raise ValueError("end_s must not precede start_s")
    step = 1.0 / fps

    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        offset = _stream_start(stream)

        target_pts = int((start_s + offset) / stream.time_base)
        container.seek(target_pts, backward=True, stream=stream)

        next_wanted = start_s
        for frame in container.decode(stream):
            if frame.pts is None:
                continue
            t = float(frame.pts * stream.time_base) - offset
            if t < next_wanted - 1e-9:
                continue
            if t > end_s + 1e-9:
                break
            img = frame.to_ndarray(format="bgr24")
            if crop is not None:
                x, y, w, h = crop
                img = np.ascontiguousarray(img[y : y + h, x : x + w])
            yield t, img
            # Advance past the frame we just emitted so a slow source cannot
            # emit two frames for the same slot.
            while next_wanted <= t + 1e-9:
                next_wanted += step


def sample_keyframes(path, count: int = 24, stride: int = 90, with_times: bool = False):
    """A handful of keyframes spread across the video, without holding the rest.

    Calibration and layout only need a couple of dozen frames from across the
    stream. Materialising the whole sweep to pick them out costs roughly 18 GB
    of decoded frames for a four-hour video, so this stops as soon as it has
    what it was asked for.
    """
    out = []
    for i, (t, img) in enumerate(keyframe_sweep(path)):
        if i % stride:
            continue
        out.append((t, img) if with_times else img)
        if len(out) >= count:
            break
    return out


def chunked(path, start_s, end_s, fps, size=64, crop=None):
    """Yield frames in batches, so a long span never sits in memory at once.

    A 16-minute end at 10 fps is about 4.4 GB of decoded panels. Collecting
    those into a list before detecting on them is enough to push a 31 GB machine
    into swap, where it stalls with the GPU idle. Batching keeps peak memory to
    ``size`` frames while still handing the detector enough work at a time to
    use a GPU efficiently.
    """
    batch = []
    for item in window(path, start_s, end_s, fps, crop=crop):
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch
