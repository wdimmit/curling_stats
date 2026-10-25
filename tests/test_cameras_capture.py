"""Capture: which camera is which, timing its packets, and holding its picture
still while they stop."""

import io
import json
import logging
import os
import signal
import struct
import subprocess
import sys
import threading
import time
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
import pytest

from curling_score.cameras import capture, inventory, tap

SPS_PPS_IDR = b"\x00\x00\x00\x01\x67sps\x00\x00\x00\x01\x68pps\x00\x00\x00\x01\x65idr"
P1 = b"\x00\x00\x00\x01\x41p1"
P2 = b"\x00\x00\x00\x01\x41p2"
PERIOD = 1 / 60
# The session's zero on the monotonic clock, and a HOUSE camera's clock (years
# behind) in seconds.
T0 = 1000.0
CAM = 1_640_000_000.0


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def holder():
    clock, out = Clock(), []
    return capture.Holder(lambda data, at, key: out.append(data), clock=clock), clock, out


def h264(count=12, gop=6):
    """Real H.264 as the cameras send it, Annex-B with SPS and PPS before every
    keyframe: [(packet, keyframe)]."""
    codec = av.CodecContext.create("libx264", "w")
    codec.width, codec.height, codec.pix_fmt = 64, 48, "yuv420p"
    codec.time_base = Fraction(1, 60)
    codec.thread_count = 1
    codec.options = {"preset": "ultrafast", "x264-params":
                     f"repeat-headers=1:keyint={gop}:min-keyint={gop}:scenecut=0:bframes=0"}
    out = []
    for i in range(count):
        frame = av.VideoFrame.from_ndarray(np.full((48, 64, 3), 8 * i, np.uint8), format="rgb24")
        frame.pts = i
        out += [(bytes(p), p.is_keyframe) for p in codec.encode(frame)]
    return out + [(bytes(p), p.is_keyframe) for p in codec.encode(None)]


def demux_nut(data):
    """[(time, keyframe, packet)] read back from NUT by PyAV."""
    with av.open(io.BytesIO(data), format="nut") as c:
        return [(float(p.pts * p.time_base), p.is_keyframe, bytes(p))
                for p in c.demux() if p.size]


# --- the camera's clock, anchored to arrival ------------------------------------


def test_spacing_follows_the_camera_clock_not_arrival_jitter():
    c = capture.CameraClock(T0)
    jitter = [0.0, 0.002, 0.009, 0.001, 0.004, 0.0, 0.011, 0.003]
    times = [c.time(CAM + i / 60, T0 + 1 + i / 60 + j) for i, j in enumerate(jitter)]

    assert times == pytest.approx([1 + i / 60 for i in range(8)], abs=1e-6)


def test_the_offset_is_the_least_delay_over_the_window():
    """The packet that came quickest in the last ANCHOR_WINDOW_S anchors the
    clock; once it is older than that, the quickest since does."""
    c = capture.CameraClock(T0)
    offsets = []
    for i in range(41):                    # a packet every 0.5 s for 20 s
        delay = 0.005 if i == 4 else 0.030
        c.time(CAM + i * 0.5, T0 + i * 0.5 + delay)
        offsets.append(c.offset - (T0 - CAM))

    assert capture.ANCHOR_WINDOW_S == 10.0
    assert offsets[:4] == pytest.approx([0.030] * 4, abs=1e-6)
    # The quick one arrived at 2.005 s; it leaves the window once a packet
    # arrives more than 10 s after it, at 12.03 s.
    assert offsets[4:24] == pytest.approx([0.005] * 20, abs=1e-6)
    assert offsets[24:] == pytest.approx([0.030] * 17, abs=1e-6)


def test_a_burst_of_late_arrivals_keeps_its_true_spacing():
    """Packets held up on the way for up to 100 ms, then delivered at once,
    are timed when the camera took them."""
    c = capture.CameraClock(T0)
    arrivals = [T0 + 1 + i / 60 + 0.016 for i in range(120)]
    arrivals[60:66] = [arrivals[60] + 0.100] * 6
    times = [c.time(CAM + i / 60, a) for i, a in enumerate(arrivals)]

    assert np.diff(times) == pytest.approx([1 / 60] * 119, abs=1e-6)
    assert times[60] == pytest.approx(1 + 60 / 60 + 0.016, abs=1e-6)


@pytest.mark.parametrize("drift", [0.015, -0.015])
def test_drift_of_15_ms_an_hour_is_followed(drift):
    """The cameras' clocks drift against the Ryzen's by about 15 ms an hour
    (2026-10-25). Anchored to a fixed offset, an hour's packets would end 15 ms
    off their arrival; anchored to the window, they stay on it."""
    c = capture.CameraClock(T0)
    rate = 1 + drift / 3600
    worst = 0.0
    for i in range(36_000):                # 10 a second for an hour
        true = i / 10
        t = c.time(CAM + true * rate, T0 + true + 0.016)
        worst = max(worst, abs(t - (true + 0.016)))

    assert worst < 0.001


@pytest.mark.parametrize("step", [-86_400.0, -2.0, 2.0, 3600.0])
def test_a_clock_step_anchors_again_at_once(step, caplog):
    """A camera's clock that jumps (a reboot, NTP) is anchored again on the
    first packet after the jump, not after the window has passed."""
    c = capture.CameraClock(T0)
    for i in range(60):
        c.time(CAM + i / 60, T0 + 1 + i / 60 + 0.016)
    with caplog.at_level(logging.WARNING, logger=capture.__name__):
        after = [c.time(CAM + step + i / 60, T0 + 1 + i / 60 + 0.016) for i in range(60, 63)]

    # Unanchored, a clock set back would be timed early for a whole window.
    assert after == pytest.approx([2.016, 2.016 + 1 / 60, 2.016 + 2 / 60], abs=1e-6)
    (warned,) = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warned.startswith(f"the camera's clock jumped {step:+.3f} s")


def test_times_only_ever_increase():
    """A pts repeated, or set back by less than CLOCK_STEP_S, still gives a
    time after the last one: a microsecond after."""
    c = capture.CameraClock(T0)
    a = c.time(CAM, T0 + 1.0)
    b = c.time(CAM, T0 + 1.0 + PERIOD)
    d = c.time(CAM - 0.5, T0 + 1.0 + 2 * PERIOD)

    assert (a, b, d) == pytest.approx((1.0, 1.000001, 1.000002), abs=1e-9)


def test_a_packet_without_a_pts_is_timed_by_its_arrival():
    c = capture.CameraClock(T0)
    c.time(CAM, T0 + 1.010)

    assert c.time(None, T0 + 1.040) == pytest.approx(1.040, abs=1e-6)
    assert c.time(CAM + 2 / 60, T0 + 1.050) == pytest.approx(1.010 + 2 / 60, abs=1e-6)


def test_a_time_before_the_sessions_zero_is_zero():
    c = capture.CameraClock(T0)

    assert c.time(CAM, T0 - 0.5) == 0.0
    assert c.time(None, T0 - 0.3) == pytest.approx(1e-6, abs=1e-9)
    assert c.time(CAM + 1.0, T0 + 0.5) == pytest.approx(0.5, abs=1e-6)


# --- the FIFO's container ---------------------------------------------------------


def test_nut_gives_back_each_packets_time_to_the_microsecond():
    """What the capture writes, read back by PyAV: every packet, untouched, at
    the time it was given, whatever the spacing. NUT counts in microseconds."""
    packets = h264()
    times = [1.0 + i / 60 + (0.0213 if i >= 6 else 0.0) + i * 3.7e-7
             for i in range(len(packets))]
    nut = capture._Nut(packets[0][0])
    chunks = [nut.packet(p, t, k) for (p, k), t in zip(packets, times)]

    # Each packet's bytes come out as it goes in: nothing waits in a buffer.
    assert all(len(c) > len(p) for c, (p, _) in zip(chunks, packets))
    got = demux_nut(b"".join(chunks))
    assert [(p, k) for _, k, p in got] == packets
    assert np.abs(np.array([t for t, _, _ in got]) - times).max() <= 1e-6


def test_nut_times_always_increase():
    """A held keyframe is timed by the session clock and a camera's packet by
    the camera's: the first packet after a hold can be timed before the last
    held picture. NUT needs times that increase, so it goes a microsecond on."""
    (key, _), (p, _) = h264(count=2)
    nut = capture._Nut(key)
    got = demux_nut(nut.packet(key, 2.0, True) + nut.packet(p, 1.9995, False))

    assert [t for t, _, _ in got] == pytest.approx([2.0, 2.000001], abs=1e-9)


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


def taken(fifo):
    """``(time, packet, keyframe)`` for each packet the FIFO's writer would
    write next, in order, without waiting."""
    out = []
    while fifo._queue:
        item = fifo._take()
        if item is not None:
            out.append(item)
    return out


def queued(fifo):
    """The packets the FIFO's writer would write next."""
    return [data for _, data, _ in taken(fifo)]


def test_a_keyframe_from_before_the_fifo_opens_is_held_once_it_does(tmp_path):
    """A camera that sent its first keyframe and then nothing: the composer,
    which opens its FIFOs one at a time and probes each, must still get a
    picture from it, or it never starts."""
    clock = Clock()
    fifo = capture._Fifo(tmp_path / "cam.nut", clock=clock)
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
    fifo = capture._Fifo(tmp_path / "cam.nut", clock=clock)
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
        now = time.monotonic_ns()
        w.write(struct.pack("<IBqq", len(SPS_PPS_IDR), 1, 0, now) + SPS_PPS_IDR)
        w.write(struct.pack("<IBqq", len(P1), 0, 166_667, now) + P1)
    cap = capture_reading(tmp_path, read, time.monotonic())
    cap._read()                            # to the end of the pipe
    assert (tmp_path / "first").read_bytes() == SPS_PPS_IDR
    assert not cap.fifo.opened.is_set() and not cap.fifo._queue
    cap.fifo.opened.set()
    assert cap.holder.tick() is True
    assert queued(cap.fifo) == [SPS_PPS_IDR]


def capture_reading(tmp_path, fd, t0):
    args = capture._parser().parse_args(["--camera", "2A-HOUSE", "--fifo", str(tmp_path / "f"),
                                         "--first", str(tmp_path / "first"), "--t0", str(t0)])
    args.tap_fd, args.parent = fd, os.getppid()
    return capture._Capture(args)


def test_the_tap_reader_times_packets_by_the_cameras_clock_from_the_sessions_zero(tmp_path):
    """Three packets a frame apart that reached the Ryzen bunched up: each is
    timed as the camera spaced them, anchored to the quickest, in seconds
    since the recorder's start (--t0)."""
    t0 = time.monotonic() - 6.0
    pts0 = 16_419_840_000_000_000          # 100 ns units, as libndi gives them
    read, write = os.pipe()
    with os.fdopen(write, "wb") as w:
        for i, (data, late) in enumerate([(SPS_PPS_IDR, 0.0), (P1, 0.013), (P2, 0.001)]):
            arrived = round((t0 + 5.0 + i / 60 + late) * 1e9)
            w.write(struct.pack("<IBqq", len(data), i == 0, pts0 + i * tap.PTS_HZ // 60, arrived)
                    + data)
    cap = capture_reading(tmp_path, read, t0)
    cap.fifo.opened.set()
    cap._read()

    assert [t for t, _, _ in cap.fifo._queue] == pytest.approx([5.0, 5.0 + 1 / 60, 5.0 + 2 / 60],
                                                               abs=1e-6)


def opened_fifo(tmp_path, clock):
    fifo = capture._Fifo(tmp_path / "cam.nut", clock=clock)
    fifo.opened.set()
    return fifo


def test_a_composer_seconds_behind_only_delays_the_packets(tmp_path):
    """Each packet carries its own time to the composer, so one that has
    waited still goes where it belongs: a composer that stops reading for a
    few seconds loses nothing, and its recording is only later."""
    clock = Clock()
    fifo = opened_fifo(tmp_path, clock)
    h = capture.Holder(fifo.write, clock=clock)
    t = clock.t
    sent = [(t + i * PERIOD, data, i == 0) for i, data in enumerate([SPS_PPS_IDR, P1, P2, P1])]
    for at, data, key in sent:
        h.packet(int(key), data, at)
    clock.t = t + 4.0

    assert taken(fifo) == sent


def test_a_packet_that_waited_too_long_is_dropped_with_all_queued(tmp_path, caplog):
    """A packet's wait here only delays the recording, but the delay, and the
    memory the queue takes, have a bound: none older than MAX_AGE_S is ever
    fed to the composer. What is queued goes, the stream starts again at a
    keyframe, and the picture is held meanwhile."""
    clock = Clock()
    fifo = opened_fifo(tmp_path, clock)
    h = capture.Holder(fifo.write, clock=clock)
    t = clock.t
    h.packet(1, SPS_PPS_IDR, t)
    h.packet(0, P1, t + PERIOD)
    h.packet(0, P2, t + 2 * PERIOD)
    clock.t = t + capture.MAX_AGE_S - 0.1
    assert fifo._take()[1] == SPS_PPS_IDR
    clock.t = t + capture.MAX_AGE_S + 0.2  # the composer stopped reading for a while
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
    assert warned == ["the composer is 5.2 s behind; dropping 2 packets and starting again "
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
    assert queued(fifo) == [SPS_PPS_IDR, P1]


def test_the_fifo_carries_live_and_held_pictures_at_their_times(tmp_path):
    """From the Holder to the bytes on the FIFO: a camera's packets at the
    times they were given, then its last keyframe held at the session clock's
    now, and the camera's next keyframe after the last held picture even when
    its own time is a little before it."""
    packets = h264(count=7)                # a keyframe, five P-frames, a keyframe
    clock = Clock()
    fifo = opened_fifo(tmp_path, clock)
    h = capture.Holder(fifo.write, clock=clock)
    t = clock.t
    for i, (data, key) in enumerate(packets[:6]):
        h.packet(int(key), data, t + i * PERIOD)
    clock.t = t + 5 * PERIOD + 0.6         # the camera goes quiet: its keyframe is held
    assert h.tick() is True
    h.packet(1, packets[6][0], clock.t - 0.002)
    out = tmp_path / "out.nut"
    fd = os.open(out, os.O_WRONLY | os.O_CREAT)
    try:
        for item in taken(fifo):
            fifo._send(fd, *item)
    finally:
        os.close(fd)

    got = demux_nut(out.read_bytes())
    assert [p for _, _, p in got] == [p for p, _ in packets[:6]] + [packets[0][0], packets[6][0]]
    assert [k for _, k, _ in got] == [True, *[False] * 5, True, True]
    assert [x for x, _, _ in got] == pytest.approx(
        [t + i * PERIOD for i in range(6)] + [clock.t, clock.t + 1e-6], abs=1e-6)


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
                                         "--first", str(tmp_path / "first"), "--t0", "0"])
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
args = capture._parser().parse_args(["--camera", "5H-POV", "--fifo", fifo, "--first", first,
                                     "--t0", "0"])
args.tap_fd, _ = os.pipe()
args.parent = os.getppid()
sys.exit(capture._Capture(args).run())
"""


def test_sigterm_ends_a_capture_stuck_in_libndi_at_once(tmp_path):
    """A capture whose camera had gone silent ignored SIGTERM for over 5 s and
    was killed (Task 11, D8): a Python handler runs only once the call it is
    stuck in returns. The recorder gives a capture CAPTURE_EXIT_S."""
    fifo = tmp_path / "cam.nut"
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
