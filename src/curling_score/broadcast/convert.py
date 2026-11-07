"""A whole video through a `Layout`: every frame into the canonical layout, in order.

Ported from scripts/dallas/normalize_live.py. A frame that is no view (a
dissolve, a graphic) -- and GUARD frames either side of one -- repeats the last
good converted frame, so the output has the input's frame count and clock. A
dissolve is only known once it is over, so frames are held GUARD behind.
"""

import functools
import subprocess
import time
from collections import deque
from pathlib import Path

import numpy as np

from curling_score.broadcast.layout import GUARD, H, UNKNOWN, W, Layout

# How many frames of matched/unmatched history `Converter.stats` can look back over.
HISTORY_FRAMES = 36_000


class Converter:
    """Frames in, converted frames out, GUARD behind."""

    def __init__(self, layout: Layout, history: int = HISTORY_FRAMES):
        self.layout = layout
        self.counts: dict[str, int] = {}
        self._pending = deque()                    # (frame, kind) not yet emitted
        self._kinds = deque(maxlen=2 * GUARD + 1)
        self._last = layout.T.copy()
        self._matched = deque(maxlen=history)
        self._any = False
        self.frames = 0                            # frames pushed
        self.first_match = None                    # the index of the first that matched a view

    def _guarded(self, back: int) -> bool:
        """Whether the frame ``back`` from the newest is in or beside a dissolve."""
        ks = list(self._kinds)
        j = len(ks) - 1 - back
        window = ks[max(0, j - GUARD):j + GUARD + 1]
        return ks[j] == UNKNOWN or len(set(window)) > 1

    def _emit(self, frame, kind, bad):
        if not bad:
            self._last = self.layout.convert(frame, kind)
        return self._last

    def push(self, frame) -> list:
        kind = self.layout.classify(frame)
        self.counts[kind] = self.counts.get(kind, 0) + 1
        hit = kind != UNKNOWN
        if hit and self.first_match is None:
            self.first_match = self.frames
        self.frames += 1
        self._any = self._any or hit
        self._matched.append(hit)
        self._kinds.append(kind)
        self._pending.append((frame, kind))
        if len(self._pending) > GUARD:
            f, k = self._pending.popleft()
            return [self._emit(f, k, self._guarded(GUARD))]
        return []

    def flush(self) -> list:
        out = []
        n = len(self._pending)
        while self._pending:
            n -= 1
            f, k = self._pending.popleft()
            out.append(self._emit(f, k, self._guarded(n) if n < len(self._kinds) else True))
        return out

    def stats(self, window_frames: int) -> tuple[bool, float]:
        """(whether any frame has matched a view yet, the fraction of the last
        ``window_frames`` that did)."""
        recent = list(self._matched)[-window_frames:]
        return self._any, (sum(recent) / len(recent) if recent else 0.0)


@functools.cache
def _has_nvenc() -> bool:
    """Whether NVENC really opens here: listed among ffmpeg's encoders is not
    enough (a build with it, on a machine or container without the GPU's
    video capability, lists it and then fails to open it)."""
    try:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                            "-i", "color=s=256x144:d=0.2", "-c:v", "h264_nvenc", "-f", "null", "-"],
                           capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def _reader(src: Path, size, follow: bool, timeout_s: float):
    follow_args = ["-follow", "1", "-rw_timeout", str(int(timeout_s * 1_000_000))] if follow else []
    return subprocess.Popen([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", *follow_args, "-i", str(src),
        "-map", "0:v:0", "-fps_mode", "passthrough", "-vf", f"scale={size[0]}:{size[1]}",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE)


def _writer(dst: Path, fps: float, encoder: str):
    if encoder == "auto":
        encoder = "h264_nvenc" if _has_nvenc() else "libx264"
    codec = (["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "21", "-b:v", "0"]
             if encoder == "h264_nvenc" else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18"])
    fmt = ["-f", "mpegts"] if dst.suffix == ".ts" else ["-f", "mp4"]
    return subprocess.Popen([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", f"{fps}", "-i", "-",
        *codec, "-g", "60", "-pix_fmt", "yuv420p", *fmt, str(dst)], stdin=subprocess.PIPE)


def convert_file(src, dst, layout: Layout, *, follow: bool = False, timeout_s: float = 300,
                 fps: float = 30.0, on_head=None, encoder: str = "auto", stop=None) -> dict:
    """Convert ``src`` into ``dst`` (MPEG-TS when it ends .ts, MP4 otherwise).

    ``follow`` reads a file still being written, until nothing new arrives for
    ``timeout_s``. ``on_head(seconds_written, converter)`` is called about once
    a second and at the end. ``stop()`` returning true ends it early. Returns
    the count of frames per view ("?" for none).
    """
    src, dst = Path(src), Path(dst)
    conv = Converter(layout)
    w, h = layout.size
    reader, writer = _reader(src, (w, h), follow, timeout_s), _writer(dst, fps, encoder)
    size, written, said = w * h * 3, 0, 0.0

    def write(frames):
        nonlocal written
        for f in frames:
            writer.stdin.write(np.ascontiguousarray(f).tobytes())
            written += 1

    try:
        while True:
            if stop is not None and stop():
                break
            buf = reader.stdout.read(size)
            if len(buf) < size:
                break
            write(conv.push(np.frombuffer(buf, np.uint8).reshape(h, w, 3)))
            now = time.monotonic()
            if on_head is not None and now - said >= 1.0:
                on_head(written / fps, conv)
                said = now
        write(conv.flush())
    finally:
        reader.kill()
        reader.wait()
        writer.stdin.close()
        rc = writer.wait()
    if rc != 0:
        raise RuntimeError(f"converting {src} into {dst}: the encoder exited {rc}")
    if on_head is not None:
        on_head(written / fps, conv)
    return dict(conv.counts)
