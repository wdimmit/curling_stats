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


def keyframes_near(path, t_s: float, span_s: float = 20.0) -> list:
    """The keyframes within ``span_s`` either side of ``t_s``, on
    `ingest.frames`' clock (the stream's start being zero), from the packets'
    key flags. Not from decoding with -skip_frame: with B-frames that labels a
    keyframe with the wrong time (uKWnmVG9mA8, five seconds out)."""
    def probe(*args):
        return subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", *args,
                               "-of", "csv=p=0", str(path)],
                              capture_output=True, text=True, check=True).stdout
    start = [f for f in probe("-show_entries", "stream=start_time").split() if f != "N/A"]
    offset = float(start[0].split(",")[0]) if start else 0.0
    keys = set()
    for line in probe("-show_entries", "packet=pts_time,flags", "-read_intervals",
                      f"{max(0.0, t_s - span_s):.3f}%{t_s + span_s:.3f}").splitlines():
        fields = line.split(",")
        if len(fields) >= 2 and "K" in fields[-1] and fields[0] not in ("", "N/A"):
            keys.add(float(fields[0]) - offset)
    return sorted(keys)


def keyframe_at_or_before(path, t_s: float) -> float:
    """The last keyframe at or before ``t_s``: where a replay from ``t_s``
    begins, on `ingest.frames`' clock."""
    before = [k for k in keyframes_near(path, t_s) if k <= t_s + 1e-3]
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
        # Where the recording begins on the source's clock, and where ffmpeg
        # was asked to seek to; set by start().
        self.t0_s = 0.0
        self._seek_s = None
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
            # Aimed exactly at a keyframe whose frames are reordered, ffmpeg
            # began at the keyframe before it (uKWnmVG9mA8 at 6360.0 began at
            # 6355.0), so the seek aims half a second into its group instead.
            keys = keyframes_near(self.source, self.start_s)
            before = [k for k in keys if k <= self.start_s + 1e-3]
            self.t0_s = max(before) if before else 0.0
            after = [k for k in keys if k > self.t0_s + 1e-3]
            self._seek_s = self.t0_s + min(0.5, (after[0] - self.t0_s) / 2 if after else 0.5)
            cmd += ["-ss", f"{self._seek_s:.3f}"]
        if self.end_s is not None:
            cmd += ["-t", f"{self.end_s - (self._seek_s or 0.0):.3f}"]
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
        # ffmpeg's progress counts from the -ss target; the file, and so the
        # recording's clock, starts at the keyframe before it.
        return self._head_s + (0.0 if self._seek_s is None else self._seek_s - self.t0_s)

    def check(self):
        """A replay needs no keeping: it has no network to drop out of."""

    def ended(self) -> bool:
        if self._stopped or self._proc is None or self._proc.poll() is None:
            return False
        self._reader.join(timeout=5)
        return self._finished and self._proc.returncode == 0

    def failed(self) -> bool:
        """ffmpeg gave up: a source it could not read, an option it lacks
        (-readrate_initial_burst needs ffmpeg 6). Without this a replay that
        never began looks exactly like one still waiting for its first bytes."""
        return (not self._stopped and self._proc is not None
                and self._proc.poll() is not None and self._proc.returncode != 0)

    def stop(self):
        if self._proc is not None and self._proc.poll() is None:
            self._stopped = True
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
