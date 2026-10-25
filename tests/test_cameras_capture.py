"""Capture: which camera is which, and holding a camera's picture still while
its packets stop."""

import json
import logging
import os
import signal
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from curling_score.cameras import capture, inventory

SPS_PPS_IDR = b"\x00\x00\x00\x01\x67sps\x00\x00\x00\x01\x68pps\x00\x00\x00\x01\x65idr"
P1 = b"\x00\x00\x00\x01\x41p1"
P2 = b"\x00\x00\x00\x01\x41p2"
PERIOD = 1 / 60


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def holder():
    clock, out = Clock(), []
    return capture.Holder(lambda data, at, key: out.append(data), clock=clock), clock, out


def test_sheet_cameras_long_views_by_sheet():
    assert inventory.sheet_cameras(1)["long_a"] == "1A-POV"
    assert inventory.sheet_cameras(3)["long_h"] == "3H-PTZ"
    assert inventory.sheet_cameras(5)["house_h"] == "5H-HOUSE"
    assert inventory.sheet_cameras(2) == {
        "house_a": "2A-HOUSE", "house_h": "2H-HOUSE", "long_a": "2A-PTZ", "long_h": "2H-PTZ"}
    every = [name for sheet in range(1, 6) for name in inventory.sheet_cameras(sheet).values()]
    assert sorted(every) == sorted(inventory.CAMERAS)


def test_ndi_config_lists_all_twenty_ips(tmp_path):
    inventory.ndi_config(tmp_path / "ndi")

    config = json.loads((tmp_path / "ndi" / "ndi-config.v1.json").read_text())
    ips = config["ndi"]["networks"]["ips"].split(",")
    assert sorted(ips) == [f"192.168.0.{n}" for n in range(231, 251)]
    assert sorted(ips) == sorted(inventory.CAMERAS.values())


def test_holder_passes_packets_through():
    h, clock, out = holder()
    h.packet(1, SPS_PPS_IDR)
    clock.t += PERIOD
    h.packet(0, P1)
    clock.t += PERIOD

    assert out == [SPS_PPS_IDR, P1]
    assert h.tick() is False


def test_hold_repeats_last_idr_when_packets_stop():
    h, clock, out = holder()
    h.packet(1, SPS_PPS_IDR)
    h.packet(0, P1)
    clock.t += 0.6

    assert h.tick() is True
    assert out == [SPS_PPS_IDR, P1, SPS_PPS_IDR]
    assert h.tick() is False
    clock.t += PERIOD
    assert h.tick() is True
    clock.t += PERIOD
    assert h.tick() is True
    assert out == [SPS_PPS_IDR, P1] + [SPS_PPS_IDR] * 3


def test_hold_writes_nothing_before_first_keyframe():
    """P-slices from before the first keyframe cannot be decoded."""
    h, clock, out = holder()
    h.packet(0, P1)
    clock.t += 0.6

    assert h.tick() is False
    clock.t += 5.0
    assert h.tick() is False
    assert out == []


def test_hold_stops_once_packets_resume():
    h, clock, out = holder()
    h.packet(1, SPS_PPS_IDR)
    clock.t += 0.6
    assert h.tick() is True

    clock.t += PERIOD / 2
    h.packet(1, SPS_PPS_IDR + b"2")
    h.packet(0, P2)
    assert out == [SPS_PPS_IDR, SPS_PPS_IDR, SPS_PPS_IDR + b"2", P2]
    clock.t += 0.4
    assert h.tick() is False
    clock.t += 0.2
    assert h.tick() is True
    assert out[-1] == SPS_PPS_IDR + b"2"


def test_hold_waits_for_a_keyframe_before_resuming():
    """P-frames after a gap refer to pictures that were never passed on."""
    h, clock, out = holder()
    h.packet(1, SPS_PPS_IDR)
    clock.t += 0.6
    assert h.tick() is True

    clock.t += PERIOD
    h.packet(0, P1)
    assert h.tick() is True
    assert out == [SPS_PPS_IDR, SPS_PPS_IDR, SPS_PPS_IDR]
    h.packet(1, SPS_PPS_IDR + b"2")
    h.packet(0, P2)
    clock.t += PERIOD
    assert h.tick() is False
    assert out[-2:] == [SPS_PPS_IDR + b"2", P2]


def queued(fifo):
    """What the FIFO's writer would write next, in order, without waiting."""
    out = []
    while fifo._queue:
        data = fifo._take()
        if data is not None:
            out.append(data)
    return out


def test_a_keyframe_from_before_the_fifo_opens_is_held_once_it_does(tmp_path):
    """A camera that sent its first keyframe and then nothing: the composer,
    which opens its FIFOs one at a time and probes each, must still get a
    picture from it, or it never starts."""
    clock = Clock()
    fifo = capture._Fifo(tmp_path / "cam.h264", clock=clock)
    h = capture.Holder(fifo.write, clock=clock)
    h.packet(1, SPS_PPS_IDR)
    h.packet(0, P1)
    clock.t += 5.0
    h.tick()
    assert queued(fifo) == []              # nothing reads the FIFO yet
    fifo.opened.set()                      # the composer opens it; the camera is quiet
    for _ in range(3):
        clock.t += PERIOD
        assert h.tick() is True
    assert queued(fifo) == [SPS_PPS_IDR] * 3


def test_a_live_camera_starts_at_its_newest_keyframe_once_the_fifo_opens(tmp_path):
    clock = Clock()
    fifo = capture._Fifo(tmp_path / "cam.h264", clock=clock)
    h = capture.Holder(fifo.write, clock=clock)
    h.packet(1, SPS_PPS_IDR)
    h.packet(1, SPS_PPS_IDR + b"2")
    h.packet(0, P1)
    fifo.opened.set()
    clock.t += PERIOD
    h.tick()
    h.packet(0, P2)                        # refers to pictures never passed on
    h.packet(1, SPS_PPS_IDR + b"3")
    h.packet(0, P1)
    assert queued(fifo) == [SPS_PPS_IDR + b"2", SPS_PPS_IDR + b"3", P1]


def test_the_tap_reader_gives_the_holder_packets_before_the_fifo_opens(tmp_path):
    """The reader once passed packets on only after the composer opened the
    FIFO, so a camera quiet after its first keyframe left nothing to hold."""
    read, write = os.pipe()
    with os.fdopen(write, "wb") as w:
        w.write(struct.pack("<IB", len(SPS_PPS_IDR), 1) + SPS_PPS_IDR)
        w.write(struct.pack("<IB", len(P1), 0) + P1)
    args = capture._parser().parse_args(["--camera", "2A-HOUSE", "--fifo", str(tmp_path / "f"),
                                         "--first", str(tmp_path / "first")])
    args.tap_fd, args.parent = read, os.getppid()
    cap = capture._Capture(args)
    cap._read()                            # to the end of the pipe
    assert (tmp_path / "first").read_bytes() == SPS_PPS_IDR
    assert not cap.fifo.opened.is_set() and not cap.fifo._queue
    cap.fifo.opened.set()
    assert cap.holder.tick() is True
    assert queued(cap.fifo) == [SPS_PPS_IDR]


def opened_fifo(tmp_path, clock):
    fifo = capture._Fifo(tmp_path / "cam.h264", clock=clock)
    fifo.opened.set()
    return fifo


def test_a_packet_that_waited_too_long_is_dropped_with_all_queued(tmp_path, caplog):
    """The composer stamps a packet with the wall clock when it reads it, so a
    packet's wait here is delay nothing downstream can see. None older than
    MAX_AGE_S is ever fed to it: what is queued goes, the stream starts again
    at a keyframe, and the picture is held meanwhile."""
    clock = Clock()
    fifo = opened_fifo(tmp_path, clock)
    h = capture.Holder(fifo.write, clock=clock)
    t = clock.t
    h.packet(1, SPS_PPS_IDR, t)
    h.packet(0, P1, t + PERIOD)
    h.packet(0, P2, t + 2 * PERIOD)
    clock.t = t + capture.MAX_AGE_S - 0.1
    assert fifo._take() == SPS_PPS_IDR
    clock.t = t + 1.7                      # the composer stopped reading for a while
    with caplog.at_level(logging.INFO, logger=capture.__name__):
        assert fifo._take() is None
        assert not fifo._queue
        h.packet(0, P1 + b"later", clock.t)   # refers to pictures that went
        assert queued(fifo) == []
        clock.t += PERIOD
        assert h.tick() is True            # held at once, not after hold_after_s
        h.packet(1, SPS_PPS_IDR + b"2", clock.t)
        h.packet(0, P2 + b"2", clock.t)
        assert queued(fifo) == [SPS_PPS_IDR, SPS_PPS_IDR + b"2", P2 + b"2"]
    warned = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warned == ["the composer is 1.7 s behind; dropping 2 packets and starting again "
                      "at a keyframe"]
    assert "the composer has caught up" in caplog.text


def test_a_clip_cuts_hiccup_is_waited_out_not_dropped(tmp_path):
    """A practice clip cut put the composers 0.5-0.8 s behind for a moment
    (2026-10-25). All of a sheet's cameras wait alike, so they stay in step;
    dropping instead jumped each back to its last keyframe for up to 2 s."""
    clock = Clock()
    fifo = opened_fifo(tmp_path, clock)
    h = capture.Holder(fifo.write, clock=clock)
    t = clock.t
    h.packet(1, SPS_PPS_IDR, t)
    h.packet(0, P1, t + PERIOD)
    clock.t = t + 0.8
    assert fifo._take() == SPS_PPS_IDR
    assert fifo._take() == P1


def test_a_keyframe_after_a_drop_starts_the_stream_again_at_once(tmp_path):
    clock = Clock()
    fifo = opened_fifo(tmp_path, clock)
    h = capture.Holder(fifo.write, clock=clock)
    h.packet(1, SPS_PPS_IDR, clock.t)
    h.packet(0, P1, clock.t)
    clock.t += capture.MAX_AGE_S + 0.1
    assert fifo._take() is None
    h.packet(1, SPS_PPS_IDR + b"2", clock.t)
    h.packet(0, P2, clock.t)
    assert queued(fifo) == [SPS_PPS_IDR + b"2", P2]


def test_one_warning_for_a_composer_that_stays_behind(tmp_path, caplog):
    clock = Clock()
    fifo = opened_fifo(tmp_path, clock)
    h = capture.Holder(fifo.write, clock=clock)
    with caplog.at_level(logging.WARNING, logger=capture.__name__):
        for _ in range(3):
            h.packet(1, SPS_PPS_IDR, clock.t)
            h.packet(0, P1, clock.t)
            clock.t += capture.MAX_AGE_S + 0.1
            assert fifo._take() is None
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


def test_full_queue_never_blocks_the_reader_and_resumes_at_a_keyframe(monkeypatch, tmp_path,
                                                                      caplog):
    """A composer that stops reading must not back up into the tap, and so
    into libndi: past BACKLOG packets (a guard on memory, since none older
    than MAX_AGE_S is written anyway) what does not fit is dropped, and the
    stream picks up again at a keyframe."""
    monkeypatch.setattr(capture, "BACKLOG", 3)
    clock = Clock()
    fifo = opened_fifo(tmp_path, clock)
    h = capture.Holder(fifo.write, clock=clock)

    def overflow():
        h.packet(1, SPS_PPS_IDR)
        h.packet(0, P1)
        h.packet(0, P2)
        h.packet(0, b"\x00\x00\x00\x01\x41dropped")
        h.packet(1, SPS_PPS_IDR + b"dropped")
        h.tick()

    with caplog.at_level(logging.WARNING):
        reader = threading.Thread(target=overflow, daemon=True)
        reader.start()
        reader.join(timeout=2.0)
    assert not reader.is_alive(), "Holder.packet blocked on a full queue"
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1

    assert queued(fifo) == [SPS_PPS_IDR, P1, P2]
    h.packet(0, b"\x00\x00\x00\x01\x41after")
    assert not fifo._queue
    h.packet(1, SPS_PPS_IDR + b"2")
    h.packet(0, P1)
    assert queued(fifo) == [SPS_PPS_IDR + b"2", P1]


def test_reconnects_back_off_and_start_over_once_packets_flow():
    clock = Clock()
    r = capture.Reconnects(clock=clock)
    start, tried = clock.t, []
    for _ in range(400):  # 200 s without a packet, looked at every 0.5 s
        clock.t += 0.5
        if r.due():
            tried.append(clock.t - start)
    assert tried == [5.0, 15.0, 35.0, 75.0, 135.0, 195.0]

    r.heard()
    clock.t += 4.5
    assert r.due() is False
    clock.t += 0.5
    assert r.due() is True
    clock.t += 9.5
    assert r.due() is False
    clock.t += 0.5
    assert r.due() is True


def test_without_the_tap_a_capture_exits_naming_it(monkeypatch, tmp_path):
    """A capture outside the worker image cannot hear any camera: it says why,
    so the recorder's error can say it too."""
    monkeypatch.setattr(capture.tap, "TAP_DIR", tmp_path / "ndi")
    args = capture._parser().parse_args(["--camera", "2A-HOUSE", "--fifo", str(tmp_path / "f"),
                                         "--first", str(tmp_path / "first")])
    with pytest.raises(SystemExit) as exited:
        capture._exec_with_tap(args, [])
    assert str(exited.value.code).startswith("the packet tap is not loaded (")
    assert str(tmp_path / "ndi" / "libavcodec-ndi.so.61") in str(exited.value.code)


# A capture whose camera is not there, run with libndi faked: the finder is
# stuck in a call that holds the GIL, as cyndilib's calls do, and the FIFO's
# writer is blocked opening a FIFO nobody reads.
_STUCK = """
import ctypes, os, sys, types

libc = ctypes.PyDLL(None)                 # a call through it keeps the GIL
held = ctypes.create_string_buffer(64)    # zeroed: a default pthread mutex on glibc
libc.pthread_mutex_lock(held)

class Finder:
    def wait_for_sources(self, timeout):
        print("stuck", flush=True)
        # Never returns, signal or not: libndi waiting on a camera that is not
        # there, with the GIL held.
        libc.pthread_mutex_lock(held)

    def update_sources(self):
        return []

for name in ("cyndilib", "cyndilib.finder", "cyndilib.receiver", "cyndilib.video_frame",
             "cyndilib.wrapper", "cyndilib.wrapper.ndi_recv"):
    sys.modules[name] = types.ModuleType(name)
sys.modules["cyndilib.finder"].Finder = Finder
sys.modules["cyndilib.receiver"].Receiver = sys.modules["cyndilib.receiver"].ReceiveFrameType = None
sys.modules["cyndilib.video_frame"].VideoRecvFrame = None
ndi_recv = sys.modules["cyndilib.wrapper.ndi_recv"]
ndi_recv.RecvBandwidth = ndi_recv.RecvColorFormat = None

from curling_score.cameras import capture

fifo, first = sys.argv[1:3]
args = capture._parser().parse_args(["--camera", "5H-POV", "--fifo", fifo, "--first", first])
args.tap_fd, _ = os.pipe()
args.parent = os.getppid()
sys.exit(capture._Capture(args).run())
"""


def test_sigterm_ends_a_capture_stuck_in_libndi_at_once(tmp_path):
    """A capture whose camera had gone silent ignored SIGTERM for over 5 s and
    was killed (Task 11, D8): a Python handler runs only once the call it is
    stuck in returns. The recorder gives a capture CAPTURE_EXIT_S."""
    fifo = tmp_path / "cam.h264"
    os.mkfifo(fifo)
    src = Path(capture.__file__).resolve().parents[2]
    proc = subprocess.Popen([sys.executable, "-c", _STUCK, str(fifo), str(tmp_path / "first")],
                            stdout=subprocess.PIPE, env={**os.environ, "PYTHONPATH": str(src)})
    try:
        assert proc.stdout.readline() == b"stuck\n"
        proc.send_signal(signal.SIGTERM)
        began = time.monotonic()
        proc.wait(timeout=3)
        assert time.monotonic() - began < 1.0
    finally:
        proc.kill()
        proc.wait()
