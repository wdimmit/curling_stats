"""One camera's H.264, from libndi through the tap into a FIFO.

    python -m curling_score.cameras.capture --camera 1A-POV \\
        --fifo cam-long_a.h264 --first first-long_a.h264

This holds one NDI connection to one camera for as long as it runs, and writes
the camera's packets, untouched, to ``--fifo`` for a composer to read. What the
composer reads starts at the camera's newest keyframe when it opens the FIFO,
held until the next one comes (they come every 2 s), so a camera that has gone
quiet since still gives it a picture. The first keyframe is also copied to
``--first``, so whoever started the capture can check what the camera sends
before anything reads the FIFO.

**One process per camera, re-executed.** The tap (``tap.py``) keeps one H.264
context per process, so each camera needs a process of its own. libndi finds
the tap through ``LD_LIBRARY_PATH``, and the loader reads that only when a
process starts. So ``main`` writes ``ndi-config.v1.json``, makes the tap's
pipe, and execs itself with ``tap.tap_env``'s environment and
``NDI_CONFIG_DIR``. It stays one process: whoever started it signals and waits
on the PID it got, and needs no special environment of its own.

**Threads.**
- The main thread runs cyndilib's receive pump. With libndi's decode off,
  ``receive()`` never returns a video frame, but the pump keeps asking, as
  every receiver measured on 2026-10-24 did. cyndilib holds the GIL while
  ``receive()`` waits, so the pump asks without waiting and sleeps in between.
- A reader drains the tap's pipe. It must not stall, because a full pipe
  blocks libndi's own thread.
- A ticker keeps the picture still while packets stop (``Holder``).
- A writer feeds the FIFO from a queue, so a composer that is not reading
  never backs up into libndi. The composer stamps each packet with the wall
  clock as it reads it, so a packet's wait in the queue is delay nothing
  downstream can see: one that has waited more than ``MAX_AGE_S`` is never
  written. What is queued then goes, and the stream starts again at a
  keyframe while the ``Holder`` holds the picture.

**Stalls.** Liveness is judged from the tap's packets, never from
``receive()``. After ``RECONNECT_S`` without a packet the receiver reconnects.
It tries again at doubling gaps up to ``RECONNECT_MAX_S``, and starts over once
packets flow again, so a stuck receiver cannot hammer a camera the club's own
production also reads. Meanwhile the ``Holder`` repeats the last keyframe, so
the composer's picture holds still rather than stopping.

**Exits.** SIGTERM ends it at once, by the signal's default action. A Python
handler would run only once the main thread got back to Python, and a capture
whose camera has gone quiet can sit in libndi, which cyndilib calls holding
the GIL, for longer than the recorder waits (over 5 s, 2026-10-24). These exit
1, so a camera connection never outlives the session that wanted it:
- the camera is not in the finder within ``FIND_S``;
- no keyframe arrives within ``FIRST_S`` of connecting;
- the FIFO's reader goes away;
- the parent process goes away.

The parent is watched with ``getppid()``, not ``PR_SET_PDEATHSIG``: that
signal fires when the *thread* that started this process exits, not its
process. The process ends with ``os._exit``. Tearing libndi down in Python's
finalizers can wait on libndi's threads, one of which may be blocked on the
tap's pipe once the reader has stopped. The kernel closes the camera's sockets
either way.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

from curling_score.cameras import inventory, tap

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
# cannot find its camera, or hears no keyframe, gives up on the same scale
# rather than hold a connection nobody will read.
FIND_S = 15.0
FIRST_S = 15.0
# How often the finder is looked at while the camera is not in it yet.
FIND_POLL_S = 0.5
# How often the pump asks libndi for frames and checks on the camera and the
# parent.
PUMP_S = 0.1
# A packet that has waited this long for the composer is never fed to it. The
# composer times each camera by when it reads the packet, so a backlog here is
# delay head_s cannot see, and different for each camera: with 10 s of queue
# allowed, sheet 2's cameras sat ~9.5 s behind for whole runs (Task 11).
MAX_AGE_S = 0.5
# Packets queued, at most: 10 s at 60 fps, about 12 MB at a long view's ~10
# Mbit/s. Only a guard on memory, for a composer that has stopped reading
# altogether (MAX_AGE_S is judged as a packet is written). Past it, packets are
# dropped until the next keyframe: blocking would stall libndi.
BACKLOG = 600
_MODULE = "curling_score.cameras.capture"
_TAP_LIB = "libavcodec-ndi.so.61"


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

    ``write`` is given each packet with when it arrived (``clock``'s time)
    and whether it is a keyframe. ``packet`` and ``tick`` may be called from
    different threads.
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
        """A packet from the camera, which arrived at ``at`` (now if None)."""
        with self._lock:
            key = bool(flags & tap.KEYFRAME)
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


class _Fifo:
    """The FIFO's writing end, fed from a queue by a thread of its own.

    ``write`` never blocks: a full queue drops the data and says so, because a
    blocked tap reader would block libndi's own thread through the tap's pipe.
    The writer never writes a packet that arrived more than MAX_AGE_S ago: it
    drops it with everything queued behind it, and the next ``write`` that is
    not a keyframe is refused, so the stream starts again at one.
    """

    def __init__(self, path: Path, *, clock: Callable[[], float] = time.monotonic):
        self.path, self.clock = path, clock
        self.opened = threading.Event()
        self._queue: deque[tuple[float, bytes]] = deque()   # (arrived, packet)
        self._ready = threading.Condition()
        self._restart = False         # dropped as late: start again at a keyframe
        self._full = False
        self._late: tuple[int, float] | None = None   # packets dropped, at worst how late

    def write(self, data: bytes, at: float, key: bool) -> bool:
        """Queue ``data``, which arrived at ``at``. False if it was dropped:
        nothing has opened the FIFO yet, the queue was full, or late packets
        were dropped and this is not the keyframe to start again at. Called by
        one thread at a time (under the Holder's lock)."""
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
            self._queue.append((at, data))
            self._ready.notify()
            return True

    def _take(self) -> bytes | None:
        """The next packet to write, or None if there was none fit to: one
        that waited more than MAX_AGE_S goes, with all queued behind it."""
        with self._ready:
            if not self._queue:
                return None
            at, data = self._queue.popleft()
            late = self.clock() - at
            if late <= MAX_AGE_S:
                if self._late is not None:
                    log.info("the composer has caught up after dropping %d packets "
                             "(at worst %.1f s behind)", *self._late)
                    self._late = None
                return data
            dropped = 1 + len(self._queue)
            self._queue.clear()
            self._restart = True
            if self._late is None:
                log.warning("the composer is %.1f s behind; dropping %d packets and starting "
                            "again at a keyframe", late, dropped)
                self._late = (0, 0.0)
            self._late = (self._late[0] + dropped, max(self._late[1], late))
            return None

    def _next(self) -> bytes:
        """The next packet to write, waiting for one."""
        with self._ready:
            while True:
                self._ready.wait_for(lambda: self._queue)
                data = self._take()
                if data is not None:
                    return data

    def run(self) -> None:
        """Open the FIFO, which waits for its reader. Then write what is
        queued. A reader that goes away raises BrokenPipeError."""
        fd = os.open(self.path, os.O_WRONLY)
        self.opened.set()
        while True:
            view = memoryview(self._next())
            while view:
                view = view[os.write(fd, view):]


class _Capture:
    def __init__(self, args: argparse.Namespace):
        self.camera, self.first, self.parent = args.camera, args.first, args.parent
        self.tap_fd = args.tap_fd
        self.fifo = _Fifo(args.fifo)
        self.holder = Holder(self.fifo.write)
        self.reconnects = Reconnects()
        self.first_at: float | None = None
        self.exit_code: int | None = None
        self._stalled = False

    def stop(self, code: int, why: str) -> None:
        """End the process with ``code``. Called from any thread, so it takes
        no lock."""
        if self.exit_code is None:
            self.exit_code = code
            log.error("stopping: %s", why)

    def run(self) -> int:
        # The default action, whatever the parent left (see Exits).
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        from cyndilib.finder import Finder
        from cyndilib.receiver import Receiver, ReceiveFrameType
        from cyndilib.video_frame import VideoRecvFrame
        from cyndilib.wrapper.ndi_recv import RecvBandwidth, RecvColorFormat

        self._thread(self._read, "tap-reader")
        self._thread(self.fifo.run, "fifo-writer")
        finder = Finder()
        source = self._find(finder)
        if source is None:
            return self.exit_code
        video = VideoRecvFrame()
        receiver = Receiver(color_format=RecvColorFormat.UYVY_BGRA,
                            bandwidth=RecvBandwidth.highest, recv_name="curling-score")
        receiver.set_video_frame(video)
        receiver.connect_to(source)
        connected_at = time.monotonic()
        self.reconnects.heard()
        log.info("connected to %s", source.name)
        self._thread(self._tick, "ticker")
        while self.exit_code is None:
            got = receiver.receive(ReceiveFrameType.recv_video, 0)
            # Only with libndi's decode on: a frame left unread fills
            # cyndilib's buffers.
            if got & ReceiveFrameType.recv_video:
                video.skip_frames(False)
            elif got == ReceiveFrameType.recv_buffers_full:
                video.skip_frames(True)
            if self.first_at is None and time.monotonic() - connected_at > FIRST_S:
                self.stop(1, f"no keyframe within {FIRST_S:.0f} s of connecting")
            self._check_parent()
            if self.exit_code is None:
                self._check_stall(receiver, source)
                time.sleep(PUMP_S)
        return self.exit_code

    def _find(self, finder):
        deadline = time.monotonic() + FIND_S
        while self.exit_code is None:
            finder.wait_for_sources(FIND_POLL_S)
            for name in finder.update_sources():
                if name.split(" (")[0] == self.camera:
                    return finder.get_source(name)
            if time.monotonic() >= deadline:
                self.stop(1, f"not in the finder within {FIND_S:.0f} s")
            self._check_parent()
        return None

    def _check_parent(self) -> None:
        if os.getppid() != self.parent:
            self.stop(1, f"parent {self.parent} went away")

    def _check_stall(self, receiver, source) -> None:
        if self.reconnects.due():
            # Receiver.reconnect() does nothing while libndi still counts the
            # connection, which is when a camera goes quiet without dropping
            # it, so disconnect and connect again.
            log.warning("no packets for %.1f s; reconnecting", self.reconnects.quiet_s())
            receiver.disconnect()
            receiver.connect_to(source)
            self._stalled = True
        elif self._stalled and self.reconnects.quiet_s() < self.reconnects.first_s:
            log.info("packets again")
            self._stalled = False

    def _read(self) -> None:
        with open(self.tap_fd, "rb") as f:
            for flags, payload in tap.read_records(f):
                at = time.monotonic()
                self.reconnects.heard()
                if flags & tap.KEYFRAME and self.first_at is None:
                    self._save_first(payload)
                # Before the FIFO is open too: the Holder keeps the newest
                # keyframe, which is where the FIFO's stream starts.
                self.holder.packet(flags, payload, at)
        self.stop(1, "the tap's pipe closed")

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

    def _thread(self, target: Callable[[], None], name: str) -> None:
        def run():
            try:
                target()
            except BrokenPipeError:
                self.stop(1, "the FIFO's reader went away")
            except Exception as exc:  # noqa: BLE001 - any thread's failure ends the capture
                log.exception("%s failed", name)
                self.stop(1, f"{name}: {type(exc).__name__}: {exc}")

        threading.Thread(target=run, name=name, daemon=True).start()


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=f"python -m {_MODULE}", description=__doc__.split("\n\n")[0])
    p.add_argument("--camera", required=True, choices=sorted(inventory.CAMERAS))
    p.add_argument("--fifo", required=True, type=Path, help="the FIFO the packets go to")
    p.add_argument("--first", required=True, type=Path, help="where the first keyframe is copied")
    p.add_argument("--ndi-dir", type=Path,
                   help="where ndi-config.v1.json is written (default: ndi/ beside --fifo)")
    # Set by main() when it re-executes itself.
    p.add_argument("--tap-fd", type=int, help=argparse.SUPPRESS)
    p.add_argument("--parent", type=int, help=argparse.SUPPRESS)
    return p


def _exec_with_tap(args: argparse.Namespace, argv: list[str]) -> None:
    if not (tap.TAP_DIR / _TAP_LIB).exists():
        # Said on stderr as it exits; the recorder makes it its error.
        sys.exit(f"the packet tap is not loaded ({tap.TAP_DIR / _TAP_LIB} is missing: "
                 "capture runs only in the worker image)")
    ndi_dir = (args.ndi_dir or args.fifo.parent / "ndi").absolute()
    inventory.ndi_config(ndi_dir)
    read, write = os.pipe()
    os.set_inheritable(read, True)
    os.set_inheritable(write, True)
    env = {**os.environ, **tap.tap_env(write, decode=False), "NDI_CONFIG_DIR": str(ndi_dir)}
    os.execve(sys.executable,
              [sys.executable, "-m", _MODULE, *argv,
               "--tap-fd", str(read), "--parent", str(os.getppid())], env)


def main(argv: list[str] | None = None) -> NoReturn:
    argv = sys.argv[1:] if argv is None else list(argv)
    args = _parser().parse_args(argv)
    if args.tap_fd is None:
        _exec_with_tap(args, argv)
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
