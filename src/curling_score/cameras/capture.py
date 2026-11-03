"""One camera's H.264, over SRT into a FIFO.

    python -m curling_score.cameras.capture --camera 1A-POV \\
        --fifo cam-long_a.nut --first first-long_a.h264 --t0 81234.567

This holds one SRT connection to one camera for as long as it runs, and writes
the camera's packets, untouched and each with its time, to ``--fifo`` for a
composer to read. What the composer reads starts at the camera's newest
keyframe when it opens the FIFO, held until the next one comes (they come
every 2 s), so a camera that has gone quiet since still gives it a picture.
The first keyframe is also copied to ``--first``, so whoever started the
capture can check what the camera sends before anything reads the FIFO.

**Time.** Every packet is timed here, in seconds since ``--t0``, the
recorder's start on ``time.monotonic``'s clock, and its time goes with it into
the FIFO, as NUT (``_Nut``), for the composer to keep. A composer that falls
behind then only delays the recording; it never moves a frame in it. The time
is the camera's own clock (the packet's 90 kHz MPEG-TS pts, unwrapped at
2^33), anchored to when the packets reached the Ryzen (``CameraClock``), and
put the camera's ``delivery_delay_s`` before arrival, where NDI put them. The
camera's clock spaces the frames as it took them, where arrivals bunch up; it
is trusted for nothing more. A HOUSE camera's pts is its encoder's uptime, and
the long cameras' NTP times disagree with each other by 10-90 ms from day to
day, while each camera's delay to the Ryzen holds to 1-2 ms ("Camera sync" in
docs/superpowers/specs/2026-10-29-practice-srt-capture-design.md). A held
keyframe is timed when it is written.

**The connection.** Every camera runs an SRT listener on UDP 1600
(``SRT_PORT``). ``ffmpeg`` is the caller (``ffmpeg_command``): it copies the
camera's video, untouched, to MPEG-TS on its stdout, and PyAV demuxes that
here. PyAV's own FFmpeg has no ``srt`` (checked 2026-11-03). A second caller
to a camera gets its own stream and leaves ours alone (2026-11-03).

**Threads.**
- The main thread starts each ``ffmpeg`` and checks on the camera and the
  parent every ``PUMP_S``.
- A reader per connection passes its packets on (``_read_stream``), and stops
  once a newer connection replaces it.
- A relay per connection logs ``ffmpeg``'s own lines, so it never blocks on a
  full stderr.
- A ticker keeps the picture still while packets stop (``Holder``).
- A writer feeds the FIFO from a queue, so a composer that is not reading
  never backs up into the reader. A packet's wait in the queue only delays the
  recording, but one that has waited more than ``MAX_AGE_S`` is never
  written. What is queued then goes, and the stream starts again at a
  keyframe while the ``Holder`` holds the picture.

**Stalls.** Liveness is judged from packets. After ``RECONNECT_S`` without one
-- a stalled stream, or an ``ffmpeg`` that has exited -- the capture kills its
``ffmpeg`` and starts a new one. It tries again at doubling gaps up to
``RECONNECT_MAX_S``, and starts over once packets flow again, so a camera that
is down is never hammered: the club's own production reads it too. Meanwhile
the ``Holder`` repeats the last keyframe, so the composer's picture holds
still rather than stopping. Each new connection's pts start again (ffmpeg
rebases them), which ``CameraClock`` takes as a clock step and anchors anew.

**Exits.** SIGTERM ends it at once, by the signal's default action. These exit
1, so a camera connection never outlives the session that wanted it:
- no keyframe arrives within ``FIRST_S`` of starting;
- the FIFO's reader goes away;
- the parent process goes away.

The parent is watched with ``getppid()``. ``ffmpeg`` runs under ``setpriv
--pdeathsig KILL``, so it dies with the capture however the capture ends, even
by SIGKILL. That signal fires when the *thread* that started the child exits,
so only the main thread, which lives until the end, starts one. The process ends with ``os._exit``: the FIFO's
writer may be blocked on its pipe once the reader has stopped.
"""

from __future__ import annotations

import argparse
import io
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from fractions import Fraction
from pathlib import Path
from typing import BinaryIO, NoReturn

import av

from curling_score.cameras import inventory

log = logging.getLogger(__name__)

# Reconnect after this long without a packet, then at doubling gaps up to
# RECONNECT_MAX_S. Each new connection makes the camera send a burst of
# keyframes (3 in a row, measured 2026-10-24). A camera re-keying over and over
# degraded all its receivers (docs/superpowers/plans/
# 2026-10-24-practice-direct-feeds.results.md), and the club's production is
# one of them.
RECONNECT_S = 5.0
RECONNECT_MAX_S = 60.0
# The recorder gives a camera 15 s to send its first keyframe. A capture that
# hears no keyframe gives up on the same scale rather than hold a connection
# nobody will read.
FIRST_S = 15.0
# How often the capture checks on the camera and the parent.
PUMP_S = 0.1
# A camera's clock is anchored to the quickest of its packets over this long.
# Arrivals jitter over the quickest by 1-3 ms at the median and up to ~100 ms
# in bursts, and the clocks drift ~15 ms an hour against the Ryzen's: 0.04 ms
# over a window (1A-HOUSE and 1A-POV, 2026-10-25).
ANCHOR_WINDOW_S = 10.0
# A camera's clock that moves this much more or less than the arrivals between
# two packets has been set (a reboot, NTP), and is anchored again at once.
CLOCK_STEP_S = 1.0
# A packet that has waited this long for the composer is never fed to it. Its
# time goes with it, so waiting only delays the recording, which head_s shows;
# this bounds the delay and what the queue holds. A practice clip cut puts the
# composers 0.5-0.8 s behind for a moment (2026-10-25), and a hold-up of a few
# seconds is waited out too: dropping froze each camera on an old keyframe for
# up to 2 s.
MAX_AGE_S = 5.0
# Packets queued, at most: 10 s at 60 fps, about 12 MB at a long view's ~10
# Mbit/s. Only a guard on memory, for a composer that has stopped reading
# altogether (MAX_AGE_S is judged as a packet is written). Past it, packets are
# dropped until the next keyframe: blocking would stall the reader.
BACKLOG = 600
# Each camera serves SRT on this UDP port; we connect as a caller. Latency 500
# ms ran clean on sheet 4 during a league, where 200 ms once lost packets on
# 4H-PTZ (2026-10-29). A connection that hears nothing for SRT_TIMEOUT_S ends,
# rather than hang.
SRT_PORT = 1600
SRT_LATENCY_S = 0.5
SRT_TIMEOUT_S = 5.0
# How much later than NDI each camera model's frames reach us over SRT, past
# SRT_LATENCY_S: the same frames (NDI timecode = SRT pts) received both ways,
# 5 min on each of two cameras per model, agreed to 2 ms (2026-11-03). Taking
# it off puts every camera where NDI put it, and the PTZs 27 ms earlier
# against the house cameras than SRT's arrivals alone would, as hits showed
# (house -0.03 s against a PTZ, 2026-10-30).
SRT_EXTRA_S = {"HOUSE": 0.065, "POV": 0.062, "PTZ": 0.091}
# How little ffmpeg and our demuxer probe a new connection before passing
# packets on. By default each held a connection's first ~4 s back and then sent
# them in a burst, which CameraClock can only time by arrival: worst 3.6 s
# late on 2A-PTZ; with these on both, first packets in 0.7 s and none more than
# 0.07 s late (2026-11-03).
PROBE = {"probesize": "32768", "analyzeduration": "0", "fpsprobesize": "0"}
# MPEG-TS pts are 90 kHz and wrap at 2^33 (~26.5 h).
PTS_WRAP = 1 << 33
_PTS_HZ = 90_000
# Holder's flag for a keyframe.
KEYFRAME = 0x01
_MODULE = "curling_score.cameras.capture"
# What ffmpeg runs under: the kernel kills it when the capture ends, however it
# ends (see Exits). setpriv sets that in the child, where a Python preexec_fn
# could deadlock in a process that has threads.
_DIE_WITH_PARENT = ["setpriv", "--pdeathsig", "KILL", "--"]
# What the FIFO's times count in.
_TIME_BASE = Fraction(1, 1_000_000)


class Holder:
    """Passes a camera's packets on, and holds its picture still while they
    stop.

    Once nothing has come for ``hold_after_s``, ``tick`` writes the last
    keyframe again every ``period_s``. The composer keeps getting frames, of a
    still picture. A keyframe can be decoded alone, because these cameras send
    SPS and PPS with every IDR.

    What is written always starts at a keyframe: nothing before the first one.
    After a hold, nothing is written until the next keyframe, because the
    P-frames in between refer to pictures that were never passed on. The same
    goes after ``write`` returns False, which says it dropped what it was given
    -- as it does until the composer opens the FIFO. The newest keyframe is
    kept all the same, so the picture starts from it, held, once ``write``
    takes it; and after a drop the picture is held at once, until a keyframe
    comes.

    ``write`` is given each packet with its time and whether it is a
    keyframe; a held keyframe's time is ``clock``'s now. ``packet`` and
    ``tick`` may be called from different threads.
    """

    def __init__(self, write: Callable[[bytes, float, bool], bool | None], *,
                 hold_after_s: float = 0.5, period_s: float = 1 / 60,
                 clock: Callable[[], float] = time.monotonic):
        self._write = write
        self.hold_after_s, self.period_s, self.clock = hold_after_s, period_s, clock
        self._lock = threading.Lock()
        self._key: bytes | None = None
        self._hold_at = 0.0          # when tick() next writes the held keyframe
        self._need_key = True

    def packet(self, flags: int, payload: bytes, at: float | None = None) -> None:
        """A packet from the camera, timed ``at`` on ``clock`` (now if None)."""
        with self._lock:
            key = bool(flags & KEYFRAME)
            if self._need_key and not key:
                return
            if key:
                self._key = payload
            if self._write(payload, self.clock() if at is None else at, key) is False:
                self._need_key = True
                self._hold_at = self.clock()
                return
            self._need_key = False
            self._hold_at = self.clock() + self.hold_after_s

    def tick(self) -> bool:
        """Write the held keyframe if it is due. True if it was written."""
        with self._lock:
            now = self.clock()
            if self._key is None or now < self._hold_at:
                return False
            self._need_key = True
            self._hold_at = now + self.period_s
            return self._write(self._key, now, True) is not False


def srt_url(camera: str) -> str:
    """Where ffmpeg calls ``camera``: its SRT listener, with our latency and
    timeout (in microseconds, as ffmpeg's srt protocol takes them)."""
    return (f"srt://{inventory.CAMERAS[camera]}:{SRT_PORT}?mode=caller"
            f"&latency={round(SRT_LATENCY_S * 1e6)}&timeout={round(SRT_TIMEOUT_S * 1e6)}")


def delivery_delay_s(camera: str) -> float:
    """How long after NDI's time for a frame ``camera``'s reaches us over SRT;
    its times are put that much before arrival."""
    return SRT_LATENCY_S + SRT_EXTRA_S[camera.split("-", 1)[1]]


def ffmpeg_command(camera: str) -> list[str]:
    """ffmpeg as the SRT caller: the camera's video copied, untouched, to
    MPEG-TS on stdout."""
    probe = [arg for option, value in PROBE.items() for arg in (f"-{option}", value)]
    return ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "warning", *probe, "-i", srt_url(camera),
            "-map", "0:v:0", "-c", "copy", "-f", "mpegts", "-"]


class PtsUnwrap:
    """MPEG-TS pts, which wrap at 2^33, as ticks that do not.

    A pts more than half the wrap below the one before has wrapped. One the
    demuxer has already unwrapped is taken modulo the wrap first, so it reads
    the same.
    """

    def __init__(self):
        self._last: int | None = None
        self._base = 0

    def __call__(self, pts: int) -> int:
        pts %= PTS_WRAP
        if self._last is not None and pts < self._last - PTS_WRAP // 2:
            self._base += PTS_WRAP
        self._last = pts
        return self._base + pts


class Reconnects:
    """When to reconnect a camera that has gone quiet.

    The first attempt is ``first_s`` after the last packet. Each later attempt
    comes after twice the previous gap, up to ``max_s``. A packet starts it all
    over.
    """

    def __init__(self, *, first_s: float = RECONNECT_S, max_s: float = RECONNECT_MAX_S,
                 clock: Callable[[], float] = time.monotonic):
        self.first_s, self.max_s, self.clock = first_s, max_s, clock
        self.heard_at = clock()
        self._gap = self._due = first_s   # quiet seconds at which to try next

    def heard(self) -> None:
        """A packet came. The reader calls this from its own thread."""
        self.heard_at = self.clock()

    def quiet_s(self) -> float:
        return self.clock() - self.heard_at

    def due(self) -> bool:
        """Whether to reconnect now. Each True counts as an attempt."""
        quiet = self.quiet_s()
        if quiet < self.first_s:
            self._gap = self._due = self.first_s
            return False
        if quiet < self._due:
            return False
        self._gap = min(self._gap * 2, self.max_s)
        self._due = quiet + self._gap
        return True


class CameraClock:
    """A camera's packets on the session clock: spaced by the camera's own
    clock, anchored to when they reached the Ryzen.

    A packet's time is its pts plus ``offset``, the least ``arrived_s -
    pts_s`` over the last ``window_s`` of arrivals: the packet that came
    quickest sets it, so the network's delays and bursts are taken out of the
    rest, and the window follows the camera clock's drift. A camera's clock
    that moves more than ``step_s`` more or less than the arrivals between two
    packets has been set, and is anchored again from the packet after the
    jump, as if new. A packet without a pts is timed by its arrival.

    A smaller step is not seen as one. A clock set forward is followed at once
    (the minimum drops); one set back by up to ``step_s`` times the camera that
    much early until the old minimum leaves the window, then jumps by it. A
    stall longer than ``step_s`` followed by a burst reads as a step, and the
    burst is timed by its arrival.

    Times are seconds since ``t0``, on the arrivals' clock. None is below 0,
    and each is at least a microsecond after the one before.
    """

    def __init__(self, t0: float, *, window_s: float = ANCHOR_WINDOW_S,
                 step_s: float = CLOCK_STEP_S):
        self.t0, self.window_s, self.step_s = t0, window_s, step_s
        # (arrived_s, arrived_s - pts_s) for each packet in the window that may
        # yet set the offset: each is less than every one after it.
        self._window: deque[tuple[float, float]] = deque()
        self._last: tuple[float, float] | None = None   # the last pts_s, arrived_s
        self._time: float | None = None                 # the last time given

    @property
    def offset(self) -> float | None:
        """What takes a pts onto the arrivals' clock; None before a pts."""
        return self._window[0][1] if self._window else None

    def time(self, pts_s: float | None, arrived_s: float) -> float:
        """The time of a packet whose pts is ``pts_s`` seconds (None if it
        has none) and which arrived at ``arrived_s``."""
        if pts_s is None:
            t = arrived_s
        else:
            if self._last is not None:
                jump = (pts_s - self._last[0]) - (arrived_s - self._last[1])
                if abs(jump) > self.step_s:
                    log.warning("the camera's clock jumped %+.3f s; anchoring it again", jump)
                    self._window.clear()
            self._last = pts_s, arrived_s
            offset = arrived_s - pts_s
            while self._window and self._window[-1][1] >= offset:
                self._window.pop()
            self._window.append((arrived_s, offset))
            while self._window[0][0] < arrived_s - self.window_s:
                self._window.popleft()
            t = pts_s + self._window[0][1]
        t = max(t - self.t0, 0.0)
        if self._time is not None:
            t = max(t, self._time + 1e-6)
        self._time = t
        return t


class _Nut:
    """A camera's packets as NUT, each with its time: what the composer reads.

    NUT keeps each time exactly, here in microseconds, with no wrap and no
    offset of its own. Nothing is encoded: the stream is copied from
    ``template``, a keyframe opened as raw H.264, whose SPS gives the codec and
    size the composer needs before its first frame. ``packet`` gives back
    each packet's bytes at once.
    """

    def __init__(self, template: bytes):
        self._parts: list[bytes] = []
        self._last = -1                     # the last time written, in microseconds
        with av.open(io.BytesIO(template), format="h264") as raw:
            self._out = av.open(self, "w", format="nut", options={"flush_packets": "1"})
            self._stream = self._out.add_stream_from_template(raw.streams.video[0])
        self._stream.time_base = _TIME_BASE

    def write(self, data: bytes) -> int:
        """Where PyAV writes the NUT."""
        self._parts.append(data)
        return len(data)

    def packet(self, data: bytes, at: float, key: bool) -> bytes:
        """The NUT for ``data``, a keyframe or not, at ``at`` seconds. Times
        must increase: one at or before the last goes a microsecond after it
        (a camera's first packet after a hold can be timed before the last
        held picture, which was timed when it was written)."""
        us = max(round(at * 1_000_000), self._last + 1)
        self._last = us
        packet = av.Packet(data)
        packet.stream, packet.time_base = self._stream, _TIME_BASE
        packet.pts = packet.dts = us
        packet.is_keyframe = key
        self._out.mux(packet)
        out = b"".join(self._parts)
        self._parts.clear()
        return out


class _Fifo:
    """The FIFO's writing end, fed from a queue by a thread of its own, as
    NUT.

    ``write`` never blocks: a full queue drops the data and says so, because a
    blocked reader would hold up the camera's packets.
    The writer never writes a packet more than MAX_AGE_S old (by ``clock``,
    the clock its times are on): it drops it with everything queued behind it,
    and the next ``write`` that is not a keyframe is refused, so the stream
    starts again at one.
    """

    def __init__(self, path: Path, *, clock: Callable[[], float] = time.monotonic):
        self.path, self.clock = path, clock
        self.opened = threading.Event()
        self._queue: deque[tuple[float, bytes, bool]] = deque()   # (time, packet, keyframe)
        self._ready = threading.Condition()
        self._restart = False         # dropped as late: start again at a keyframe
        self._full = False
        self._late: tuple[int, float] | None = None   # packets dropped, at worst how late
        self._nut: _Nut | None = None

    def write(self, data: bytes, at: float, key: bool) -> bool:
        """Queue ``data``, timed ``at``. False if it was dropped: nothing has
        opened the FIFO yet, the queue was full, or late packets were dropped
        and this is not the keyframe to start again at. Called by one thread
        at a time (under the Holder's lock)."""
        with self._ready:
            if not self.opened.is_set():
                return False
            if self._restart:
                self._restart = False
                if not key:
                    return False
            if len(self._queue) >= BACKLOG:
                if not self._full:
                    log.warning("the composer is %d packets behind; dropping to the next keyframe",
                                BACKLOG)
                    self._full = True
                return False
            if self._full:
                log.info("the composer is reading again")
                self._full = False
            self._queue.append((at, data, key))
            self._ready.notify()
            return True

    def _take(self) -> tuple[float, bytes, bool] | None:
        """The next ``(time, packet, keyframe)`` to write, or None if there
        was none fit to: one more than MAX_AGE_S old goes, with all queued
        behind it."""
        with self._ready:
            if not self._queue:
                return None
            item = self._queue.popleft()
            late = self.clock() - item[0]
            if late <= MAX_AGE_S:
                if self._late is not None:
                    log.info("the composer has caught up after dropping %d packets "
                             "(at worst %.1f s behind)", *self._late)
                    self._late = None
                return item
            dropped = 1 + len(self._queue)
            self._queue.clear()
            self._restart = True
            if self._late is None:
                log.warning("the composer is %.1f s behind; dropping %d packets and starting "
                            "again at a keyframe", late, dropped)
                self._late = (0, 0.0)
            self._late = (self._late[0] + dropped, max(self._late[1], late))
            return None

    def _next(self) -> tuple[float, bytes, bool]:
        """The next ``(time, packet, keyframe)`` to write, waiting for one."""
        with self._ready:
            while True:
                self._ready.wait_for(lambda: self._queue)
                item = self._take()
                if item is not None:
                    return item

    def run(self) -> None:
        """Open the FIFO, which waits for its reader. Then write what is
        queued. A reader that goes away raises BrokenPipeError."""
        fd = os.open(self.path, os.O_WRONLY)
        self.opened.set()
        while True:
            self._send(fd, *self._next())

    def _send(self, fd: int, at: float, data: bytes, key: bool) -> None:
        if self._nut is None:
            # The stream starts at a keyframe (Holder), which says what it is.
            self._nut = _Nut(data)
        view = memoryview(self._nut.packet(data, at, key))
        while view:
            view = view[os.write(fd, view):]


class _Capture:
    def __init__(self, args: argparse.Namespace):
        self.camera, self.first, self.parent = args.camera, args.first, args.parent
        t0 = args.t0

        # Every time here sits the camera's delivery delay before arrival,
        # where NDI's were.
        delay = delivery_delay_s(self.camera)

        def session() -> float:
            return time.monotonic() - t0 - delay

        self.times = CameraClock(t0 + delay)
        self.now: Callable[[], float] = time.monotonic   # when a packet arrived
        self.generation = 0                               # the current connection's
        # Taken to pass a packet on, and to start a new connection: a reader
        # whose connection has been replaced passes nothing more.
        self._passing = threading.Lock()
        self.fifo = _Fifo(args.fifo, clock=session)
        self.holder = Holder(self.fifo.write, clock=session)
        self.reconnects = Reconnects()
        self.first_at: float | None = None
        self.exit_code: int | None = None
        self._ffmpeg: subprocess.Popen | None = None

    def stop(self, code: int, why: str) -> None:
        """End the process with ``code``. Called from any thread, so it takes
        no lock."""
        if self.exit_code is None:
            self.exit_code = code
            log.error("stopping: %s", why)

    def run(self) -> int:
        # The default action, whatever the parent left (see Exits).
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        began = time.monotonic()
        self._thread(self.fifo.run, "fifo-writer")
        self._thread(self._tick, "ticker")
        self._connect()
        try:
            while self.exit_code is None:
                if self.first_at is None and time.monotonic() - began > FIRST_S:
                    self.stop(1, f"no keyframe within {FIRST_S:.0f} s")
                self._check_parent()
                if self.exit_code is None:
                    if self.reconnects.due():
                        log.warning("no packets for %.1f s; reconnecting", self.reconnects.quiet_s())
                        self._connect()
                    time.sleep(PUMP_S)
        finally:
            self._end_ffmpeg()
        return self.exit_code

    def _connect(self) -> None:
        """A new SRT connection: a new ffmpeg, whose MPEG-TS a reader of its
        own passes on. Only ever on the main thread (see Exits)."""
        with self._passing:
            self.generation += 1
        self._end_ffmpeg()
        log.info("connecting to %s", srt_url(self.camera))
        proc = subprocess.Popen([*_DIE_WITH_PARENT, *ffmpeg_command(self.camera)],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        self._ffmpeg = proc
        generation = self.generation
        self._thread(lambda: self._read_stream(proc.stdout, generation), f"reader-{generation}",
                     quiet=True)
        self._thread(lambda: self._relay(proc.stderr), f"ffmpeg-log-{generation}", quiet=True)

    def _end_ffmpeg(self) -> None:
        if self._ffmpeg is not None:
            self._ffmpeg.kill()
            self._ffmpeg.wait()
            self._ffmpeg = None

    @staticmethod
    def _relay(stream: BinaryIO) -> None:
        """ffmpeg's own lines, so it never blocks on a full stderr."""
        for line in stream:
            log.warning("ffmpeg: %s", line.decode(errors="replace").rstrip())

    def _check_parent(self) -> None:
        if os.getppid() != self.parent:
            self.stop(1, f"parent {self.parent} went away")

    def _read_stream(self, stream: BinaryIO, generation: int) -> int:
        """Pass on the video packets of one connection's MPEG-TS, until it
        ends or a newer connection (``generation``) replaces it. How many
        were passed on."""
        unwrap, n = PtsUnwrap(), 0
        try:
            with av.open(stream, format="mpegts", options=PROBE) as ts:
                video = ts.streams.video[0]
                for packet in ts.demux(video):
                    if not packet.size:
                        continue
                    arrived = self.now()
                    payload, key = bytes(packet), packet.is_keyframe
                    pts_s = None if packet.pts is None else unwrap(packet.pts) / _PTS_HZ
                    with self._passing:
                        if generation != self.generation:
                            break
                        self.reconnects.heard()
                        if key and self.first_at is None:
                            self._save_first(payload)
                        # Before the FIFO is open too: the Holder keeps the
                        # newest keyframe, which is where the FIFO's stream
                        # starts.
                        self.holder.packet(KEYFRAME if key else 0, payload,
                                           self.times.time(pts_s, arrived))
                    n += 1
        except av.error.FFmpegError as exc:
            log.warning("the stream ended: %s", exc)
        return n

    def _save_first(self, payload: bytes) -> None:
        part = self.first.with_name(self.first.name + ".part")
        part.write_bytes(payload)
        os.replace(part, self.first)
        self.first_at = time.monotonic()
        log.info("first keyframe, %d bytes, in %s", len(payload), self.first)

    def _tick(self) -> None:
        while self.exit_code is None:
            self.holder.tick()
            time.sleep(self.holder.period_s)

    def _thread(self, target: Callable[[], object], name: str, *, quiet: bool = False) -> None:
        """Run ``target`` on a daemon thread. Its failure ends the capture,
        unless ``quiet``: one connection's reader or log ending is no reason
        to (a dead connection shows as silence, which reconnects)."""
        def run():
            try:
                target()
            except BrokenPipeError:
                self.stop(1, "the FIFO's reader went away")
            except Exception as exc:  # noqa: BLE001 - any thread's failure ends the capture
                log.exception("%s failed", name)
                if not quiet:
                    self.stop(1, f"{name}: {type(exc).__name__}: {exc}")

        threading.Thread(target=run, name=name, daemon=True).start()


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=f"python -m {_MODULE}", description=__doc__.split("\n\n")[0])
    p.add_argument("--camera", required=True, choices=sorted(inventory.CAMERAS))
    p.add_argument("--fifo", required=True, type=Path, help="the FIFO the packets go to")
    p.add_argument("--first", required=True, type=Path, help="where the first keyframe is copied")
    p.add_argument("--t0", required=True, type=float,
                   help="the session's zero on time.monotonic's clock: packets are timed from it")
    return p


def main(argv: list[str] | None = None) -> NoReturn:
    argv = sys.argv[1:] if argv is None else list(argv)
    args = _parser().parse_args(argv)
    if shutil.which("ffmpeg") is None:
        # Said on stderr as it exits; the recorder makes it its error.
        sys.exit("ffmpeg is not installed: capture runs only in the worker image")
    args.parent = os.getppid()
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format=f"%(asctime)s %(levelname)s capture {args.camera}: %(message)s")
    try:
        code = _Capture(args).run()
    except Exception:  # noqa: BLE001 - still exit through os._exit
        log.exception("capture failed")
        code = 1
    os._exit(code)


if __name__ == "__main__":
    main()
