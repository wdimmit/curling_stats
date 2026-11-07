"""A live recording converted through its club's broadcast layout as it is recorded.

`ConvertingRecorder` wraps the recorder that writes the stream (yt-dlp's,
or a replay) and hands the lane, and anything that keeps or clips the
recording, a converted copy instead: ``norm.<n>.ts`` beside the raw parts,
in the canonical three-panel layout (curling_score.broadcast). The copy has
the raw recording's frame count and clock.

Each raw part is followed as it grows (``ffmpeg -follow``), from when it
exists. When the inner recorder starts a new part -- it records from the
stream's first segment again after a drop-out -- that part is converted from
its start into the next ``norm`` file at once, and takes over once it has
caught up: the head never goes back. The follow outlasts the recorder's own
patience with a stall (FOLLOW_TIMEOUT_S), and once the stream has ended the
reader is stopped as soon as nothing new has come for END_SETTLE_S. A
conversion that failed is tried again after a back-off.

`check` also watches whether the stream is this layout at all: no frame
matching a view NO_MATCH_FRAMES in, or under MIN_MATCHED of the last
WINDOW_FRAMES matching once one has, fails the job with a LiveError rather
than publishing what a wrong layout makes of it. A pre-game slate, or a
fifth-end break graphic of a quarter of an hour, is no failure.
"""

import logging
import threading
import time
from pathlib import Path

from curling_score.broadcast.convert import convert_file
from curling_score.broadcast.layout import Layout
from curling_score.live.session import LiveError

log = logging.getLogger(__name__)

FPS = 30.0
# Longer than the recorder waits out a stall before starting a new part
# (live/recorder.py STALL_S + EXIT_GRACE_S): a stall alone never ends a follow.
FOLLOW_TIMEOUT_S = 900.0
# Once the stream has ended, the reader stops when nothing new has come this long.
END_SETTLE_S = 30.0
NO_MATCH_FRAMES = 36_000      # 20 min at 30 fps with no frame a view of the layout
WINDOW_FRAMES = 54_000        # 30 min
MIN_MATCHED = 0.25
RETRY_S = (20.0, 60.0, 180.0, 300.0)   # after 1, 2, 3, 4+ failures in a row


class _Part:
    def __init__(self, n, src, dst, now):
        self.n, self.src, self.dst = n, Path(src), Path(dst)
        self.head = 0.0
        self.advanced_at = now
        self.conv = None
        self.error = None
        self.thread = None

    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def failed(self) -> bool:
        return not self.running() and (self.error is not None or self.head <= 0.0)


class ConvertingRecorder:
    def __init__(self, inner, layout_name: str, directory, *, convert=convert_file,
                 clock=time.monotonic, end_settle_s: float = END_SETTLE_S):
        self.inner = inner
        self.layout_name = layout_name
        self.layout = Layout.load(layout_name)
        self.dir = Path(directory)
        self._convert = convert
        self.clock, self.end_settle_s = clock, end_settle_s
        self._parts: list[_Part] = []
        self._lock = threading.Lock()
        self._stopped = False
        self._head = 0.0
        self._failures, self._next_try = 0, 0.0

    # --- lifecycle --------------------------------------------------------------

    def start(self):
        self.inner.start()
        self.dir.mkdir(parents=True, exist_ok=True)
        self._maybe_begin(self._source())
        return self

    def _source(self) -> Path:
        """The raw part to convert: the newest the inner recorder has begun
        (yt-dlp's names it), else the one it hands readers."""
        newest = getattr(self.inner, "newest_path", None)
        return Path(newest() if callable(newest) else self.inner.path)

    def _maybe_begin(self, src: Path) -> bool:
        try:
            ready = src.is_file() and src.stat().st_size > 0
        except OSError:
            ready = False
        if not ready or self.clock() < self._next_try:
            return False
        self._begin(src)
        return True

    def _begin(self, src):
        with self._lock:
            n = len(self._parts)
            part = _Part(n, src, self.dir / f"norm.{n}.ts", self.clock())
            self._parts.append(part)

        def on_head(seconds, conv):
            if seconds > part.head:
                part.advanced_at = self.clock()
            part.head, part.conv = seconds, conv

        def stop():
            return self._stopped or (self.inner.ended()
                                     and self.clock() - part.advanced_at >= self.end_settle_s)

        def run():
            try:
                self._convert(part.src, part.dst, self.layout, follow=True,
                              timeout_s=FOLLOW_TIMEOUT_S, fps=FPS, on_head=on_head, stop=stop)
            except Exception as exc:  # noqa: BLE001 - check() decides what it costs
                part.error = exc
                log.warning("converting %s: %s", part.src, exc)

        part.thread = threading.Thread(target=run, name=f"convert-{n}", daemon=True)
        part.thread.start()

    def check(self):
        self.inner.check()
        if self._stopped:
            return
        src = self._source()
        newest = self._parts[-1] if self._parts else None
        if newest is not None and not newest.running():
            if newest.failed() and newest.n == len(self._parts) - 1 and not getattr(newest, "counted", False):
                newest.counted = True
                self._failures += 1
                self._next_try = self.clock() + RETRY_S[min(self._failures, len(RETRY_S)) - 1]
            elif not newest.failed():
                self._failures = 0
        if newest is None or src != newest.src or (not newest.running() and not self.inner.ended()):
            # A new raw part, or the conversion of the current one ended while
            # the stream goes on: convert it (again) from its start.
            self._maybe_begin(src)
        conv = self._current().conv if self._parts else None
        if conv is None:
            return
        if conv.first_match is None:
            if conv.frames >= NO_MATCH_FRAMES:
                raise LiveError(f"broadcast layout mismatch: no frame of the first "
                                f"{conv.frames} matches a view of {self.layout_name}")
        elif conv.frames - conv.first_match >= WINDOW_FRAMES:
            _, frac = conv.stats(WINDOW_FRAMES)
            if frac < MIN_MATCHED:
                raise LiveError(f"broadcast layout mismatch: {frac:.0%} of the last "
                                f"{WINDOW_FRAMES} frames match a view of {self.layout_name}")

    def stop(self):
        self._stopped = True
        self.inner.stop()
        for part in list(self._parts):
            if part.thread is not None:
                part.thread.join(timeout=30)

    # --- what readers see ---------------------------------------------------------

    def _current(self) -> _Part:
        with self._lock:
            best = self._parts[0]
            for part in self._parts[1:]:
                if part.head >= best.head:
                    best = part
            return best

    @property
    def path(self) -> Path:
        return self._current().dst if self._parts else self.dir / "norm.0.ts"

    def head_s(self) -> float:
        if not self._parts:
            return 0.0
        self._head = max(self._head, self._current().head)
        return self._head

    def ended(self) -> bool:
        return self.inner.ended() and not any(p.running() for p in self._parts)

    def whole(self) -> bool:
        return self.inner.whole()
