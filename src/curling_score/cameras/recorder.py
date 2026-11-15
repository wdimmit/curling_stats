"""A sheet's four cameras, each recorded into a growing file of its own.

``CameraRecorder`` stands where ``live.recorder.YtDlpRecorder`` stands for a
practice watch: the manager starts, checks and stops it, and the watch reads
``path`` up to ``head_s()``. Behind it, four ``capture`` processes (one per
camera, ``capture.py``) each write a camera's H.264 into ``<role>.ts``. Nothing
composes them: ``path`` is a camera feed (``feeds/cameras.py``), and the
tracker reads each view from its camera's file, cut as the sheet's layout
says. That saves the composer's ~2.7 cores and an NVENC session a sheet.

**Starting.** The captures come first, then their first keyframes are checked.
A capture copies its first keyframe to ``first-<role>.h264`` from its SRT
reader. A camera that sends nothing, or anything but H.264 1920x1080 (the size
the layouts' crops are in), fails the start, and every file it had begun is
deleted. ffprobe reads the keyframes, in a subprocess: PyAV in a second
thread has deadlocked before (``ingest/frames.py``). A capture that exits
before its first keyframe has said why (no ffmpeg, a camera that never
answers), and its last error is the start's.

**The clock.** The recording's zero is read on ``clock`` (``time.monotonic``)
as it starts, before any capture, with ``wall_started`` beside it. Each capture
times its camera's packets in seconds since that zero (``--t0``), by the
camera's own clock anchored to their arrival (``capture.CameraClock``), and
writes them at those times; each file starts at the zero. So a frame at t in
any of the four was taken at ``wall_started + t``. Each capture says how far
its file reaches (``@head <s>`` lines), and ``head_s()`` is the least of them:
how far every view can be read. Nothing is recorded from before Start.

**A camera that stops.** A capture holds its camera's last keyframe while
packets stop and reconnects by itself, so its file goes on. One that exits all
the same is logged, and the recording goes on: that view's file ends there,
and ``head_s`` follows the cameras still running. If every capture has gone
the recording ends.

**Ending.** The recording fails (``LiveError``) when its head stalls for
``STALL_S``, or every capture has gone.

**Logs.** The captures log onto this module's logger, at their own levels;
their head lines are read, not logged.
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

from curling_score.cameras import inventory, layout
from curling_score.feeds.cameras import camera_feed
from curling_score.live.recorder import MAX_RECORD_S, WALL_MARGIN_S
from curling_score.live.session import LiveError

log = logging.getLogger(__name__)

# What a camera must send: the size the layouts' crops are in.
CODEC, CAMERA_W, CAMERA_H = "h264", 1920, 1080
# How long the four cameras get to send a first keyframe. A capture gives up
# on the same scale (capture.FIRST_S).
FIRST_TIMEOUT_S = 15.0
FIRST_POLL_S = 0.1
PROBE_TIMEOUT_S = 10.0
# A recording whose head has not moved for this long has stopped. A capture
# that hears nothing reconnects on its own and its Holder keeps its file going,
# so a head that stands still this long is every camera stuck at once.
STALL_S = 30.0
# How long a capture that has exited gets for the rest of its output to be read.
RELAY_JOIN_S = 2.0
# How long a process has to exit after SIGTERM before it is killed.
CAPTURE_EXIT_S = 5.0
_CAPTURE = "curling_score.cameras.capture"
# A capture's log line: "<date> <time> <LEVEL> capture <camera>: <message>".
_LOG_LINE = re.compile(r"^\S+ \S+ (DEBUG|INFO|WARNING|ERROR|CRITICAL) (.*)$")
# How far a capture's file reaches: "@head 12.345".
_HEAD_LINE = re.compile(r"^@head (\d+(?:\.\d+)?)$")


def probe(path: Path) -> tuple[str, int, int] | None:
    """The codec, width and height of the H.264 in ``path``, by ffprobe in a
    subprocess; None if it cannot be read."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-f", "h264", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name,width,height", "-of", "default=nw=1",
             str(path)],
            capture_output=True, text=True, timeout=PROBE_TIMEOUT_S, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    fields = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    try:
        return fields["codec_name"], int(fields["width"]), int(fields["height"])
    except (KeyError, ValueError):
        return None


class CameraRecorder:
    """Sheet ``sheet``'s four cameras, each into ``directory/<role>.ts``.

    The interface is ``YtDlpRecorder``'s, as the manager and the practice watch
    use it, but ``path`` is a camera feed: never one camera's file. The
    recording starts at Start, so it never holds anything from before. It is
    ``whole`` only when a league game's stream ended it (``finish_stream``).
    """

    def __init__(self, sheet: int, directory: Path, *, popen=subprocess.Popen,
                 clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time,
                 first_timeout_s: float = FIRST_TIMEOUT_S,
                 probe: Callable[[Path], tuple[str, int, int] | None] = probe,
                 max_s: float = MAX_RECORD_S):
        self.sheet, self.dir = sheet, Path(directory)
        self.path = camera_feed(self.dir, sheet)
        self.t0: float | None = None             # the recording's zero, on clock
        self.wall_started: float | None = None   # the same moment by wall
        self._popen, self.clock, self.wall, self._probe = popen, clock, wall, probe
        self.first_timeout_s, self.max_s = first_timeout_s, max_s
        self._cameras = inventory.sheet_cameras(sheet)       # role -> camera name
        self._captures: dict[str, subprocess.Popen] = {}     # role -> process
        self._relays: dict[str, threading.Thread] = {}       # role -> its log's reader
        self._said: dict[str, str] = {}                      # role -> its last error
        self._heads: dict[str, float] = {}                   # role -> how far its file reaches
        self._gone: set[str] = set()
        self._head = 0.0                                     # the last head_s() of any camera
        # check() runs on the manager's thread, stop() may come from another;
        # a check that ends the recording stops it from inside.
        self._lock = threading.RLock()
        self._ended = self._stopped = False
        self._whole = False
        self._started_at = self._progress_at = None
        self._progress_head = 0.0

    # --- lifecycle ------------------------------------------------------------

    def start(self):
        try:
            layout.load(self.sheet)
        except LookupError as exc:
            raise LiveError(str(exc)) from exc
        self.dir.mkdir(parents=True, exist_ok=True)
        # Together: a frame at t in any camera's file was taken at wall_started + t.
        self.t0, self.wall_started = self.clock(), self.wall()
        try:
            for role, name in self._cameras.items():
                self._spawn_capture(role, name)
            self._await_first()
        except BaseException:
            # No camera connection may outlive the recording that wanted it,
            # and no camera's picture either.
            self.stop()
            self._ended = True
            for role in self._cameras:
                first = self._first(role)
                for path in (first, first.with_name(first.name + ".part"), self._out(role)):
                    path.unlink(missing_ok=True)
            raise
        self._started_at = self._progress_at = self.clock()
        log.info("recording sheet %d's cameras into %s", self.sheet, self.dir)
        return self

    def _out(self, role: str) -> Path:
        return self.dir / f"{role}.ts"

    def _first(self, role: str) -> Path:
        return self.dir / f"first-{role}.h264"

    def _spawn_capture(self, role: str, name: str) -> None:
        out, first = self._out(role), self._first(role)
        # Whatever an earlier recording here left is not this camera's.
        out.unlink(missing_ok=True)
        first.unlink(missing_ok=True)
        proc = self._popen(
            [sys.executable, "-m", _CAPTURE, "--camera", name, "--out", str(out),
             "--first", str(first), "--t0", str(self.t0)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self._captures[role] = proc
        relay = threading.Thread(target=self._relay, args=(role, name, proc.stdout),
                                 name=f"capture-log-{name}", daemon=True)
        relay.start()
        self._relays[role] = relay

    def _relay(self, role: str, name: str, stream) -> None:
        """Pass a capture's log lines on, at their own level, keeping the last
        error it said: a line at ERROR, or one said outside its log (a
        traceback, or exiting before logging). Its head lines are taken, not
        logged."""
        level = logging.INFO
        prefix = f"capture {name}: "
        for raw in iter(stream.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip()
            head = _HEAD_LINE.match(line)
            if head:
                self._heads[role] = float(head.group(1))
                continue
            m = _LOG_LINE.match(line)
            if m:
                level, line = logging.getLevelName(m.group(1)), m.group(2)
                if level >= logging.ERROR:
                    self._said[role] = line.removeprefix(prefix)
            elif line:
                self._said[role] = line
                line = prefix + line
            if line:
                log.log(level, "%s", line)

    def _await_first(self) -> None:
        """Wait for every camera's first keyframe, then check what they are."""
        deadline = self.clock() + self.first_timeout_s
        waiting = list(self._cameras)
        while True:
            waiting = [role for role in waiting if not self._first(role).exists()]
            if not waiting:
                break
            dead = [role for role in waiting if self._captures[role].poll() is not None]
            if dead or self.clock() >= deadline:
                self._refuse((dead or waiting)[0])
            time.sleep(FIRST_POLL_S)
        for role in self._cameras:
            if self._probe(self._first(role)) != (CODEC, CAMERA_W, CAMERA_H):
                self._refuse(role)
        # They were only for this check, and are frames of whoever is on the
        # sheet: readers read the cameras' files.
        for role in self._cameras:
            self._first(role).unlink()

    def _refuse(self, role: str) -> NoReturn:
        name, code = self._cameras[role], self._captures[role].poll()
        if code is not None:
            self._relays[role].join(timeout=RELAY_JOIN_S)
            said = self._said.get(role)
            raise LiveError(f"camera {name}'s capture exited ({code})"
                            + (f": {said}" if said else ""))
        raise LiveError(f"camera {name} sent no H.264 {CAMERA_W}x{CAMERA_H} "
                        f"within {self.first_timeout_s:g} s")

    def check(self):
        """Keep an eye on the recording. Called every few seconds by the
        manager's thread; a call while another, or a stop, is under way
        returns at once."""
        if not self._lock.acquire(blocking=False):
            return
        try:
            self._check()
        finally:
            self._lock.release()

    def _check(self) -> None:
        if self._ended or self._stopped or self._started_at is None:
            return
        for role, proc in self._captures.items():
            code = proc.poll()
            if code is not None and role not in self._gone:
                self._relays[role].join(timeout=RELAY_JOIN_S)
                self._gone.add(role)
                log.warning("camera %s's capture exited (%s); its view stops at %.1f s",
                            self._cameras[role], code, self._heads.get(role, 0.0))
        if len(self._gone) == len(self._captures):
            self._end(f"every camera of sheet {self.sheet} has stopped")
        now, head = self.clock(), self.head_s()
        if head >= self.max_s or now - self._started_at >= self.max_s + WALL_MARGIN_S:
            log.info("sheet %d's recording reached the %.0f h cap", self.sheet, self.max_s / 3600)
            self.stop()
            self._ended = True
            return
        if head > self._progress_head:
            self._progress_head, self._progress_at = head, now
        if now - self._progress_at >= STALL_S:
            self._end(f"sheet {self.sheet}'s recording stalled at {head:.0f} s")

    def _end(self, why: str) -> NoReturn:
        log.warning("%s", why)
        self.stop()
        self._ended = True
        raise LiveError(why)

    def ended(self) -> bool:
        """A plain read: the manager's thread decides, in check()."""
        return self._ended

    def whole(self) -> bool:
        """The whole game: stopped because its stream ended (``finish_stream``),
        never before by itself -- a stall, every camera gone, the cap."""
        return self._whole

    def finish_stream(self):
        """The game's stream is over: stop recording, and the recording is
        whole if nothing had ended it before."""
        with self._lock:
            whole = self._started_at is not None and not self._ended and not self._stopped
            self.stop()
            self._ended, self._whole = True, whole

    def stop(self):
        """Stop the captures."""
        with self._lock:
            self._stopped = True
            captures = [p for p in self._captures.values() if p.poll() is None]
            for proc in captures:
                proc.terminate()
            for proc in captures:
                _wait(proc, CAPTURE_EXIT_S)

    # --- what readers see -----------------------------------------------------

    def head_s(self) -> float:
        """How far every view can be read: the least of the heads of the
        cameras still running (nothing until each has said), or of them all
        once none is. It never goes back: a slow camera that stops leaves the
        others' reach, and no reader has read past what it was told."""
        running = [role for role in self._cameras if role not in self._gone] or list(self._cameras)
        if all(role in self._heads for role in running):
            self._head = max(self._head, min(self._heads[role] for role in running))
        return self._head


def _wait(proc, timeout_s: float) -> None:
    try:
        proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
