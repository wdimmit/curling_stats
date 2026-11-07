"""A live recording converted through its club's broadcast layout as it is recorded.

`ConvertingRecorder` wraps the recorder that writes the stream (yt-dlp's,
or a replay) and hands the lane, and anything that keeps or clips the
recording, a converted copy instead: ``norm.<n>.ts`` beside the raw parts,
in the canonical three-panel layout (curling_score.broadcast). The copy has
the raw recording's frame count and clock.

Each raw part is followed as it grows (``ffmpeg -follow``). When the inner
recorder starts a new part -- it records from the stream's first segment
again after a drop-out -- that part is converted from its start into the next
``norm`` file, which takes over once it has caught up: the head never goes
back. A conversion that gave up while its part was still current (no new
frame for FOLLOW_TIMEOUT_S, a long stall) starts again the same way.

`check` also watches whether the stream is this layout at all: no frame
matching a view NO_MATCH_FRAMES in, or under MIN_MATCHED of the last
WINDOW_FRAMES matching once one has, fails the job with a LiveError rather
than publishing what a wrong layout makes of it. A pre-game slate or an
intermission graphic shorter than that is no failure.
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
FOLLOW_TIMEOUT_S = 120.0
NO_MATCH_FRAMES = 36_000      # 20 min at 30 fps with no frame a view of the layout
WINDOW_FRAMES = 18_000        # 10 min
MIN_MATCHED = 0.40


class _Part:
    def __init__(self, n, src, dst):
        self.n, self.src, self.dst = n, Path(src), Path(dst)
        self.head = 0.0
        self.conv = None
        self.error = None
        self.thread = None

    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()


class ConvertingRecorder:
    def __init__(self, inner, layout_name: str, directory, *, convert=convert_file):
        self.inner = inner
        self.layout_name = layout_name
        self.layout = Layout.load(layout_name)
        self.dir = Path(directory)
        self._convert = convert
        self._parts: list[_Part] = []
        self._lock = threading.Lock()
        self._stopped = False
        self._head = 0.0

    # --- lifecycle --------------------------------------------------------------

    def start(self):
        self.inner.start()
        self.dir.mkdir(parents=True, exist_ok=True)
        self._begin(self.inner.path)
        return self

    def _begin(self, src):
        with self._lock:
            n = len(self._parts)
            part = _Part(n, src, self.dir / f"norm.{n}.ts")
            self._parts.append(part)

        def run():
            def on_head(seconds, conv):
                part.head, part.conv = seconds, conv
            try:
                self._convert(part.src, part.dst, self.layout, follow=True,
                              timeout_s=FOLLOW_TIMEOUT_S, fps=FPS, on_head=on_head,
                              stop=lambda: self._stopped)
            except Exception as exc:  # noqa: BLE001 - check() decides what it costs
                part.error = exc
                log.warning("converting %s: %s", part.src, exc)

        part.thread = threading.Thread(target=run, name=f"convert-{n}", daemon=True)
        part.thread.start()

    def check(self):
        self.inner.check()
        if self._stopped:
            return
        newest = self._parts[-1]
        src = Path(self.inner.path)
        if src != newest.src or (not newest.running() and not self.inner.ended()):
            # A new raw part, or the conversion of the current one gave up
            # while the stream goes on: convert it (again) from its start.
            self._begin(src)
        conv = self._current().conv
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
        return self._current().dst

    def head_s(self) -> float:
        if not self._parts:
            return 0.0
        self._head = max(self._head, self._current().head)
        return self._head

    def ended(self) -> bool:
        return self.inner.ended() and not any(p.running() for p in self._parts)

    def whole(self) -> bool:
        return self.inner.whole()
