"""A cached video played back as if it were a live stream.

It writes the same growing MPEG-TS a live recorder does, at ``speed`` times
real time, so everything downstream -- the session, the worker's lane, the
five-stream rehearsal -- can be run against footage whose right answer is
already known, without touching YouTube. A replay can also begin mid-video,
with its first ``burst_s`` written at once: a stream that has been running for
hours, picked up some minutes back in its DVR window, as a practice watch is.
"""

import subprocess
import threading
from pathlib import Path


def keyframe_at_or_before(path, t_s: float) -> float:
    """The last keyframe at or before ``t_s``, where a stream copy cut at
    ``t_s`` really begins -- on `ingest.frames`' clock, the stream's start
    being zero."""
    def probe(*args):
        return subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", *args,
                               "-of", "csv=p=0", str(path)],
                              capture_output=True, text=True, check=True).stdout
    # Each CSV line's first field: a frame's line can trail a separator.
    def values(text):
        return [f for f in (line.split(",")[0].strip() for line in text.splitlines())
                if f and f != "N/A"]

    start = values(probe("-show_entries", "stream=start_time"))
    offset = float(start[0]) if start else 0.0
    keys = [float(v) - offset for v in values(probe(
        "-skip_frame", "nokey", "-show_entries", "frame=pts_time",
        "-read_intervals", f"{max(0.0, t_s - 20.0):.3f}%{t_s + 0.001:.3f}"))]
    before = [k for k in keys if k <= t_s + 1e-3]
    return max(before) if before else 0.0


class ReplayRecording:
    """A growing recording of ``source``, written at ``speed`` x real time.

    ``head_s`` is how far the file reaches on the source's own clock, taken
    from ffmpeg's progress output; ``ended`` is true once ffmpeg has written
    the whole source and exited cleanly.
    """

    def __init__(self, source, path, speed: float = 1.0, *, start_s: float = 0.0,
                 end_s: float | None = None, burst_s: float = 0.0):
        self.source = Path(source)
        self.path = Path(path)
        self.speed = float(speed)
        self.start_s, self.end_s, self.burst_s = float(start_s), end_s, float(burst_s)
        # Where the recording begins on the source's clock; set by start().
        self.t0_s = 0.0
        self._proc = None
        self._head_s = 0.0
        self._finished = False
        self._stopped = False
        self._reader = None

    def start(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
               "-readrate", str(self.speed)]
        if self.burst_s > 0:
            cmd += ["-readrate_initial_burst", str(self.burst_s)]
        if self.start_s > 0:
            # A stream copy can only begin at a keyframe: the one at or before.
            self.t0_s = keyframe_at_or_before(self.source, self.start_s)
            cmd += ["-ss", f"{self.start_s:.3f}"]
        if self.end_s is not None:
            cmd += ["-t", f"{self.end_s - self.start_s:.3f}"]
        cmd += ["-i", str(self.source),
                "-map", "0:v:0", "-c", "copy", "-f", "mpegts", "-flush_packets", "1",
                "-progress", "pipe:1", "-stats_period", "0.2", str(self.path)]
        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True)
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
