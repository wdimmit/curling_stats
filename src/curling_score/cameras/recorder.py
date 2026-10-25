"""A sheet's four cameras, composed into one growing recording.

``CameraRecorder`` stands where ``live.recorder.YtDlpRecorder`` stands for a
practice watch: the manager starts, checks and stops it, and the watch reads
``path`` up to ``head_s()``. Behind it, four ``capture`` processes (one per
camera, ``capture.py``) each write a camera's H.264 into a FIFO, and one FFmpeg,
the composer, lays the four out as YouTube does (``layout.py``) and encodes a
1920x1080p60 MPEG-TS on NVENC.

**Starting.** The captures come first, then their first keyframes are checked,
then the composer starts. A capture copies its first keyframe to
``first-<role>.h264`` from its tap reader, whether or not anything has opened
its FIFO yet (its FIFO writer waits for a reader on a thread of its own), so
waiting for all four before the composer exists cannot deadlock. A camera that
sends nothing, or anything but H.264 1920x1080 (the size the layouts' crops are
in), fails the start before anything is encoded. ffprobe reads the keyframes,
in a subprocess: PyAV in a second thread has deadlocked before
(``ingest/frames.py``). A capture that exits before its first keyframe has
said why (no tap, no such camera), and its last error is the start's.

**The clock.** The recording's zero is read on ``clock`` (``time.monotonic``)
as it starts, before any capture, with ``wall_started`` beside it. Each capture
times its camera's packets in seconds since that zero (``--t0``), by the
camera's own clock anchored to their arrival (``capture.CameraClock``), and
writes them to its FIFO as NUT with their times. The composer keeps the times
(``-copyts``, nothing shifted), so a frame at t in the composite was taken at
``wall_started + t``. ``head_s()`` is how far the composite reaches, by its
frames (``composite_s``). A composer that falls behind delays the recording,
and ``head_s`` with it, but moves nothing in it. Until the cameras' first
frames come the composite is the grey base alone. Nothing is recorded from
before Start, so ``lookback_got_s`` is 0.

**A camera that stops.** A capture holds its camera's last keyframe while
packets stop and reconnects by itself. One that exits all the same is logged,
and the recording goes on: its FIFO ends, and the overlay holds its last frame.
If every capture has gone the recording ends, because the composer's grey base
is a generated source that would then run unpaced: 20 s of video in 2.8 s once
its input ended, measured.

**Ending.** The recording fails (``LiveError``) when the composer exits or its
head stalls for ``STALL_S``. A capture drops packets rather than block when the
composer falls behind, so a composer that stops reading shows up only as a
stall. A stop takes down the captures before the composer: a composer gone from
under them reads to them as their reader going away, which they report as an
error.

**Logs.** The captures log under a lock their packets also go through, so their
output is always drained, onto this module's logger. The composer's goes to a
``_Part`` for its progress, and its last lines are logged if it exits.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

from curling_score.cameras import inventory, layout
from curling_score.live.recorder import MAX_RECORD_S, WALL_MARGIN_S, _Part, progress_time
from curling_score.live.session import LiveError

log = logging.getLogger(__name__)

# What a camera must send: the size the layouts' crops are in.
CODEC, CAMERA_W, CAMERA_H = "h264", 1920, 1080
# How long the four cameras get to send a first keyframe. A capture gives up
# on the same scale (capture.FIND_S, capture.FIRST_S).
FIRST_TIMEOUT_S = 15.0
FIRST_POLL_S = 0.1
PROBE_TIMEOUT_S = 10.0
# A composer whose head has not moved for this long has stopped. Opening and
# probing its four inputs one at a time, each from a camera keyframe (every
# 2 s), took 9 s to its first progress on sheet 1; with PROBE, 1.3-1.4 s.
STALL_S = 30.0
# Threads for each camera's decoder and for each filtergraph's slices. FFmpeg's
# default is a thread per CPU, and at a live 60 fps all of them waking for
# every frame cost 9.1 cores a sheet on the Ryzen; two each cost 2.6, keeping
# up the same (sheet 1, 2026-10-24). Neither 1 nor 4 did better with a graph
# per camera (layout.filtergraphs; test patterns, 2026-10-24).
DECODE_THREADS = 2
FILTER_THREADS = 2
# How much ffmpeg reads of each input to learn what it is before it opens the
# next: as little as it can. Each FIFO's NUT header says H.264 1920x1080, its
# stream starts at a keyframe carrying SPS and PPS (capture.Holder), and each
# packet carries its time, so there is no frame rate to learn. ffmpeg's own
# probe took ~0.3 s an input, and the cameras opened first were 0.6-1.2 s
# behind once the composer read them; this takes 0.05 s, and leaves them
# 0.14-0.30 s behind (sheet 2, 2026-10-24). When a FIFO started at the first
# keyframe after it was opened, they were 7.1 / 5.0 / 3.1 / 1.4 s behind at
# +10 s.
PROBE = ["-probesize", "32", "-analyzeduration", "1"]
# How long a capture that has exited gets for the rest of its output to be read.
RELAY_JOIN_S = 2.0
# How long a process has to exit after SIGTERM before it is killed.
CAPTURE_EXIT_S = 5.0
COMPOSER_EXIT_S = 10.0
# How much of the composer's output is kept for the log should it exit.
TAIL_BYTES = 4096
TAIL_LINES = 5
_CAPTURE = "curling_score.cameras.capture"
# A capture's log line: "<date> <time> <LEVEL> capture <camera>: <message>".
_LOG_LINE = re.compile(r"^\S+ \S+ (DEBUG|INFO|WARNING|ERROR|CRITICAL) (.*)$")
# The frames so far in one of ffmpeg's progress lines: "frame= 3735 fps= 60 ...".
_FRAME_RE = re.compile(r"\bframe=\s*(\d+)")


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


def _input(fifo: Path) -> list[str]:
    """How the composer opens a capture's FIFO: NUT, whose times it keeps."""
    return ["-threads", str(DECODE_THREADS), "-thread_queue_size", "1024",
            *PROBE, "-f", "nut", "-i", str(fifo)]


def composite_s(line: str) -> float | None:
    """How far the composite reaches by one of the composer's progress lines:
    its frames so far, ``layout.FPS`` a second from the recording's zero. Not
    its ``time=``: with ``-copyts`` ffmpeg counts that from the first time it
    reports, as far after the zero as the composite had got by then."""
    m = _FRAME_RE.search(line)
    return int(m.group(1)) / layout.FPS if m else None


class _Tail:
    """A process's output on its way to a ``_Part``, keeping its last
    ``TAIL_BYTES``: what ffmpeg said before it exited."""

    def __init__(self, stream):
        self._stream, self._last = stream, b""

    def read(self, n: int) -> bytes:
        chunk = self._stream.read(n)
        self._last = (self._last + chunk)[-TAIL_BYTES:]
        return chunk

    def lines(self) -> list[str]:
        """Its last lines, without the progress lines."""
        text = self._last.decode("utf-8", "replace")
        return [line.strip() for line in re.split(r"[\r\n]", text)
                if line.strip() and progress_time(line) is None][-TAIL_LINES:]


class CameraRecorder:
    """Sheet ``sheet``'s four cameras, composed into ``directory/rec.0.ts``.

    The interface is ``YtDlpRecorder``'s, as the manager and the practice watch
    use it. The recording starts at Start, so it never holds anything from
    before (``lookback_got_s`` is 0) and is never ``whole``.
    """

    def __init__(self, sheet: int, directory: Path, *, popen=subprocess.Popen,
                 clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time,
                 first_timeout_s: float = FIRST_TIMEOUT_S,
                 probe: Callable[[Path], tuple[str, int, int] | None] = probe,
                 max_s: float = MAX_RECORD_S):
        self.sheet, self.dir = sheet, Path(directory)
        self.path = self.dir / "rec.0.ts"
        self.lookback_got_s = 0.0
        self.t0: float | None = None             # the recording's zero, on clock
        self.wall_started: float | None = None   # the same moment by wall
        self._popen, self.clock, self.wall, self._probe = popen, clock, wall, probe
        self.first_timeout_s, self.max_s = first_timeout_s, max_s
        self._cameras = inventory.sheet_cameras(sheet)       # role -> camera name
        self._captures: dict[str, subprocess.Popen] = {}     # role -> process
        self._relays: dict[str, threading.Thread] = {}       # role -> its log's reader
        self._said: dict[str, str] = {}                      # role -> its last error
        self._gone: set[str] = set()
        self._composer: _Part | None = None
        self._tail: _Tail | None = None
        # check() runs on the manager's thread, stop() may come from another;
        # a check that ends the recording stops it from inside.
        self._lock = threading.RLock()
        self._ended = self._stopped = False
        self._started_at = self._progress_at = None
        self._progress_head = 0.0

    # --- lifecycle ------------------------------------------------------------

    def start(self):
        try:
            sheet_layout = layout.load(self.sheet)
        except LookupError as exc:
            raise LiveError(str(exc)) from exc
        self.dir.mkdir(parents=True, exist_ok=True)
        # Together: a frame at t in the composite was taken at wall_started + t.
        self.t0, self.wall_started = self.clock(), self.wall()
        try:
            for role, name in self._cameras.items():
                self._spawn_capture(role, name)
            self._await_first()
            self._spawn_composer(sheet_layout)
        except BaseException:
            # No camera connection may outlive the recording that wanted it,
            # and no camera's picture either.
            self.stop()
            self._ended = True
            for role in self._cameras:
                first = self._first(role)
                for path in (first, first.with_name(first.name + ".part"), self._fifo(role)):
                    path.unlink(missing_ok=True)
            raise
        self._started_at = self._progress_at = self.clock()
        return self

    def _fifo(self, role: str) -> Path:
        return self.dir / f"cam-{role}.nut"

    def _first(self, role: str) -> Path:
        return self.dir / f"first-{role}.h264"

    def _spawn_capture(self, role: str, name: str) -> None:
        fifo, first = self._fifo(role), self._first(role)
        # Whatever an earlier recording here left is not this camera's answer.
        fifo.unlink(missing_ok=True)
        first.unlink(missing_ok=True)
        os.mkfifo(fifo)
        proc = self._popen(
            [sys.executable, "-m", _CAPTURE, "--camera", name, "--fifo", str(fifo),
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
        traceback, or exiting before logging)."""
        level = logging.INFO
        prefix = f"capture {name}: "
        for raw in iter(stream.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip()
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
        # sheet: the composer reads the FIFOs.
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

    def _spawn_composer(self, sheet_layout: layout.SheetLayout) -> None:
        inputs = {role: i for i, role in enumerate(self._cameras)}
        # -stats: the progress _Part reads, which -loglevel warning would hide.
        cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "warning", "-stats", "-y",
               "-filter_complex_threads", str(FILTER_THREADS), "-copyts"]
        for role in self._cameras:
            cmd += _input(self._fifo(role))
        # A graph per camera, each on a thread of its own (layout.filtergraphs).
        for graph in layout.filtergraphs(sheet_layout, inputs=inputs):
            cmd += ["-filter_complex", graph]
        # -t: a composer left behind by a recorder that died (the worker
        # killed) has nobody to stop it, and once its captures are gone too it
        # runs unpaced. It stops after the recording's cap, and the margin
        # check() allows the wall clock, however fast it runs.
        cmd += ["-map", "[out]",
                "-c:v", "h264_nvenc", "-b:v", "20M", "-maxrate", "25M", "-g", "60", "-bf", "0",
                "-t", str(self.max_s + WALL_MARGIN_S),
                "-f", "mpegts", str(self.path)]
        # Unbuffered: _Part reads 4 KiB at a time, and a buffered pipe waits for
        # all of it, ~20 s of progress lines, before the head can move.
        proc = self._popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, bufsize=0)
        self._tail = proc.stdout = _Tail(proc.stdout)
        self._composer = _Part(self.path, proc, progress=composite_s)
        log.info("recording sheet %d's cameras into %s", self.sheet, self.path)

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
        if self._ended or self._stopped or self._composer is None:
            return
        now, head = self.clock(), self.head_s()
        if head >= self.max_s or now - self._started_at >= self.max_s + WALL_MARGIN_S:
            log.info("sheet %d's recording reached the %.0f h cap", self.sheet, self.max_s / 3600)
            self.stop()
            self._ended = True
            return
        if head > self._progress_head:
            self._progress_head, self._progress_at = head, now
        # The composer first: its captures go down with it, and are no news.
        code = self._composer.proc.poll()
        if code is not None:
            self._composer.reader.join(timeout=5)
            said = self._tail.lines()
            self._end(f"sheet {self.sheet}'s composer exited ({code})"
                      + (f": {said[-1]}" if said else ""))
        if now - self._progress_at >= STALL_S:
            self._end(f"sheet {self.sheet}'s composer stalled at {head:.0f} s")
        for role, proc in self._captures.items():
            code = proc.poll()
            if code is not None and role not in self._gone:
                self._gone.add(role)
                log.warning("camera %s's capture exited (%s); its picture holds its last frame",
                            self._cameras[role], code)
        if len(self._gone) == len(self._captures):
            self._end(f"every camera of sheet {self.sheet} has stopped")

    def _end(self, why: str) -> NoReturn:
        said = self._tail.lines() if self._tail else []
        log.warning("%s%s", why, "".join(f"\n  ffmpeg: {line}" for line in said))
        self.stop()
        self._ended = True
        raise LiveError(why)

    def ended(self) -> bool:
        """A plain read: the manager's thread decides, in check()."""
        return self._ended

    def whole(self) -> bool:
        return False

    def stop(self):
        """Stop the captures, then the composer."""
        with self._lock:
            self._stopped = True
            captures = [p for p in self._captures.values() if p.poll() is None]
            for proc in captures:
                proc.terminate()
            for proc in captures:
                _wait(proc, CAPTURE_EXIT_S)
            if self._composer is not None and self._composer.proc.poll() is None:
                self._composer.proc.terminate()
                _wait(self._composer.proc, COMPOSER_EXIT_S)

    # --- what readers see -----------------------------------------------------

    def head_s(self) -> float:
        return self._composer.head if self._composer is not None else 0.0


def _wait(proc, timeout_s: float) -> None:
    try:
        proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
