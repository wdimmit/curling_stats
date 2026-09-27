"""A cached video played back as if it were a live stream.

It writes the same growing MPEG-TS a live recorder does, at ``speed`` times
real time, so everything downstream -- the session, the worker's lane, the
five-stream rehearsal -- can be run against footage whose right answer is
already known, without touching YouTube.
"""

import subprocess
import threading
from pathlib import Path


class ReplayRecording:
    """A growing recording of ``source``, written at ``speed`` x real time.

    ``head_s`` is how far the file reaches on the source's own clock, taken
    from ffmpeg's progress output; ``ended`` is true once ffmpeg has written
    the whole source and exited cleanly.
    """

    def __init__(self, source, path, speed: float = 1.0):
        self.source = Path(source)
        self.path = Path(path)
        self.speed = float(speed)
        self._proc = None
        self._head_s = 0.0
        self._finished = False
        self._stopped = False
        self._reader = None

    def start(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._proc = subprocess.Popen(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
             "-readrate", str(self.speed), "-i", str(self.source),
             "-map", "0:v:0", "-c", "copy", "-f", "mpegts", "-flush_packets", "1",
             "-progress", "pipe:1", "-stats_period", "0.2", str(self.path)],
            stdout=subprocess.PIPE, text=True)
        self._reader = threading.Thread(target=self._read_progress, daemon=True)
        self._reader.start()
        return self

    def _read_progress(self):
        for line in self._proc.stdout:
            key, _, value = line.strip().partition("=")
            if key == "out_time_us" and value.lstrip("-").isdigit():
                self._head_s = max(self._head_s, int(value) / 1e6)
            elif key == "progress" and value == "end":
                self._finished = True

    def head_s(self) -> float:
        return self._head_s

    def check(self):
        """A replay needs no keeping: it has no network to drop out of."""

    def ended(self) -> bool:
        if self._stopped or self._proc is None or self._proc.poll() is None:
            return False
        self._reader.join(timeout=5)
        return self._finished and self._proc.returncode == 0

    def stop(self):
        if self._proc is not None and self._proc.poll() is None:
            self._stopped = True
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
