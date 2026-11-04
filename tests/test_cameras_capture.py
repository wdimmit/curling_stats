"""Capture: which camera is which, timing its packets, and holding its picture
still while they stop."""

import contextlib
import io
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
import pytest

from curling_score.cameras import capture, inventory

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


def h264(count=12, gop=6, size=(64, 48), noise=False):
    """Real H.264 as the cameras send it, Annex-B with SPS and PPS before every
    keyframe: [(packet, keyframe)]. ``noise`` makes the frames as big as real
    ones of their size."""
    codec = av.CodecContext.create("libx264", "w")
    (codec.width, codec.height), codec.pix_fmt = size, "yuv420p"
    codec.time_base = Fraction(1, 60)
    codec.thread_count = 1
    codec.options = {"preset": "ultrafast", "x264-params":
                     f"repeat-headers=1:keyint={gop}:min-keyint={gop}:scenecut=0:bframes=0"}
    out = []
    for i in range(count):
        pixels = (np.random.default_rng(i).integers(0, 256, (size[1], size[0], 3), np.uint8)
                  if noise else np.full((size[1], size[0], 3), 8 * i, np.uint8))
        frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
        frame.pts = i
        out += [(bytes(p), p.is_keyframe) for p in codec.encode(frame)]
    return out + [(bytes(p), p.is_keyframe) for p in codec.encode(None)]


def read_ts(path):
    """[(time, keyframe, packet)] of a capture's file, read back by PyAV: the
    time is the packet's pts in seconds."""
    with av.open(str(path)) as c:
        return [(float(p.pts * p.time_base), p.is_keyframe, bytes(p))
                for p in c.demux(video=0) if p.size]


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


def test_a_packet_reads_back_at_its_recorder_time(tmp_path):
    """What the capture writes, read as every reader reads it: each packet at
    the time it was given, on the recording's clock. The first is the
    camera's first keyframe, held from the recording's zero, so the file
    starts at 0 however late the camera's first packet came, as every other
    camera's file does."""
    from curling_score.ingest import frames

    path = tmp_path / "house_a.ts"
    out = capture._TsFile(path)
    for i, (data, key) in enumerate(camera_h264(60, gop=30)):
        out.write(data, 0.613 + i / 60, key)
    assert frames.stream_start_s(path) == 0.0
    got = [t for t, _ in frames.window(path, 0.0, 1.2, 60.0)]
    assert got[0] == 0.0
    assert got[30] == pytest.approx(0.613 + 30 / 60, abs=1 / 90_000)


def test_times_never_go_back(tmp_path):
    """A held keyframe is timed by the session clock and a camera's packet by
    the camera's: the first packet after a hold can be timed before the last
    held picture. It goes a tick (1/90000 s) after it."""
    (key, _), (p, _), (p2, _) = h264(count=3)
    out = capture._TsFile(tmp_path / "c.ts")
    out.write(key, 2.0, True)
    out.write(p, 2.5, False)
    out.write(p2, 2.4995, False)
    assert [t for t, _, _ in read_ts(tmp_path / "c.ts")] == pytest.approx(
        [0.0, 2.5, 2.5 + 1 / 90_000], abs=1e-9)


def test_a_written_packet_is_readable_at_once(tmp_path):
    """A watch reads up to the head the capture reports: whatever ``write``
    has taken is on disk when it returns."""
    path = tmp_path / "c.ts"
    out = capture._TsFile(path)
    packets = h264(count=8)
    for i, (data, key) in enumerate(packets):
        assert out.write(data, 1.0 + i / 60, key) is True
        assert out.head == pytest.approx(0.0 if i == 0 else 1.0 + i / 60, abs=1 / 90_000)
        got = read_ts(path)
        assert len(got) == i + 1
        assert all(g.endswith(d) for (_, _, g), (d, _) in zip(got, packets))


def test_sheet_cameras_long_views_by_sheet():
    assert inventory.sheet_cameras(1)["long_a"] == "1A-POV"
    assert inventory.sheet_cameras(3)["long_h"] == "3H-PTZ"
    assert inventory.sheet_cameras(5)["house_h"] == "5H-HOUSE"
    assert inventory.sheet_cameras(2) == {
        "house_a": "2A-HOUSE", "house_h": "2H-HOUSE", "long_a": "2A-PTZ", "long_h": "2H-PTZ"}
    every = [name for sheet in range(1, 6) for name in inventory.sheet_cameras(sheet).values()]
    assert sorted(every) == sorted(inventory.CAMERAS)


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


# Where a test capture writes: 2A-HOUSE is sheet 2's house_a.
OUT = "house_a.ts"


def capture_reading(tmp_path, t0):
    args = capture._parser().parse_args(["--camera", "2A-HOUSE", "--out", str(tmp_path / OUT),
                                         "--first", str(tmp_path / "first"), "--t0", str(t0)])
    args.parent = os.getppid()
    return capture._Capture(args)


# --- the camera over SRT: MPEG-TS from ffmpeg ------------------------------------


def camera_h264(count=12, gop=6):
    """h264() with frames big enough that a demuxer probing PROBE's bytes
    passes them on as they come, as a real camera's (~100 KB keyframes) are."""
    return h264(count, gop, size=(320, 240), noise=True)


def mpegts(packets, pts0=0):
    """The h264() packets muxed as MPEG-TS, 1/60 s apart from pts0 (90 kHz),
    as ffmpeg sends them."""
    buf = io.BytesIO()
    with av.open(io.BytesIO(packets[0][0]), format="h264") as raw, \
            av.open(buf, "w", format="mpegts") as out:
        stream = out.add_stream_from_template(raw.streams.video[0])
        stream.time_base = Fraction(1, 90_000)
        for i, (data, key) in enumerate(packets):
            packet = av.Packet(data)
            packet.stream, packet.time_base = stream, Fraction(1, 90_000)
            packet.pts = packet.dts = pts0 + i * 1500
            packet.is_keyframe = key
            out.mux(packet)
    return buf.getvalue()


def arrivals(times):
    """A clock that gives ``times`` in turn, one per packet."""
    it = iter(times)
    return lambda: next(it)


def test_srt_url_names_the_cameras_address_port_latency_and_timeout():
    assert capture.srt_url("3H-HOUSE") == ("srt://192.168.0.248:1600?mode=caller"
                                           "&latency=500000&timeout=5000000")


def test_ffmpeg_copies_the_cameras_video_to_mpegts_on_stdout():
    cmd = capture.ffmpeg_command("3H-HOUSE")
    assert cmd[0] == "ffmpeg" and cmd[-1] == "-"
    assert cmd[cmd.index("-i") + 1] == capture.srt_url("3H-HOUSE")
    assert cmd[cmd.index("-map") + 1] == "0:v:0"
    assert cmd[cmd.index("-c") + 1] == "copy" and cmd[cmd.index("-f") + 1] == "mpegts"


def test_ffmpeg_probes_the_camera_as_little_as_it_can():
    """ffmpeg's default probe holds a new connection's first ~4 s back and
    then sends them in a burst, which CameraClock can only time by arrival
    (2A-PTZ, 2026-11-03: worst 3.6 s late; with these, 0.07 s)."""
    cmd = capture.ffmpeg_command("3H-HOUSE")
    before_input = cmd[:cmd.index("-i")]
    for option, value in capture.PROBE.items():
        assert before_input[before_input.index(f"-{option}") + 1] == value


def test_the_reader_passes_packets_on_without_waiting_for_more(tmp_path):
    """A connection's first packets go on at once: the demuxer must not wait
    for seconds of stream to probe it."""
    read, write = os.pipe()
    data = mpegts(camera_h264(6))
    assert len(data) > 2 * int(capture.PROBE["probesize"])    # a real keyframe is ~100 KB
    threading.Thread(target=os.write, args=(write, data), daemon=True).start()
    cap = capture_reading(tmp_path, time.monotonic())
    reader = threading.Thread(target=cap._read_stream, args=(os.fdopen(read, "rb"), 0),
                              daemon=True)
    reader.start()
    try:
        deadline = time.monotonic() + 2.0
        while not (tmp_path / "first").exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert (tmp_path / "first").exists()
    finally:
        os.close(write)
        reader.join(2)


@pytest.mark.parametrize("camera, delay", [("2H-HOUSE", 0.565), ("3A-HOUSE", 0.565),
                                           ("1A-POV", 0.562), ("5H-POV", 0.562),
                                           ("2H-PTZ", 0.591), ("3H-PTZ", 0.591)])
def test_each_camera_model_is_timed_where_ndi_timed_it(camera, delay):
    """SRT delivers a frame later than NDI did, by the latency and then by an
    amount of each camera model's own (2026-11-03: same frames, by clock
    value, on two cameras of each model)."""
    assert capture.delivery_delay_s(camera) == pytest.approx(delay, abs=1e-9)


def test_pts_unwrap_carries_on_across_the_33_bit_wrap():
    u = capture.PtsUnwrap()
    w = capture.PTS_WRAP
    assert [u(p) for p in (w - 3000, w - 1500, 0, 1500)] == [w - 3000, w - 1500, w, w + 1500]


def test_pts_unwrap_takes_already_unwrapped_pts_as_they_are():
    u = capture.PtsUnwrap()
    w = capture.PTS_WRAP
    assert [u(p) for p in (w - 1500, w, w + 1500)] == [w - 1500, w, w + 1500]


def test_the_srt_reader_times_packets_by_the_cameras_clock_less_the_latency(tmp_path):
    """Packets 1/60 s apart by pts that reached the Ryzen bunched up: each is
    timed as the camera spaced them, anchored to the quickest arrival, and
    the camera's delivery delay before it, in seconds since --t0."""
    t0 = time.monotonic() - 6.0
    packets = h264(3, gop=3)
    late = [0.0, 0.013, 0.001]
    cap = capture_reading(tmp_path, t0)
    delay = capture.delivery_delay_s("2A-HOUSE")
    cap.now = arrivals([t0 + 5.0 + delay + i / 60 + late[i] for i in range(3)])
    assert cap._read_stream(io.BytesIO(mpegts(packets)), 0) == 3

    # The first, a keyframe, holds the file's start at the recording's zero.
    assert [t for t, _, _ in read_ts(tmp_path / OUT)] == pytest.approx(
        [0.0, 5.0 + 1 / 60, 5.0 + 2 / 60], abs=1 / 90_000)


def test_the_srt_reader_keeps_time_across_a_pts_wrap(tmp_path):
    t0 = time.monotonic() - 6.0
    packets = h264(12)
    cap = capture_reading(tmp_path, t0)
    cap.now = arrivals([t0 + 5.0 + i / 60 for i in range(len(packets))])
    cap._read_stream(io.BytesIO(mpegts(packets, pts0=capture.PTS_WRAP - 6 * 1500)), 0)

    times = [t for t, _, _ in read_ts(tmp_path / OUT)]
    assert len(times) == len(packets)
    assert np.diff(times[1:]) == pytest.approx([1 / 60] * (len(times) - 2), abs=1 / 90_000)


def test_the_srt_reader_saves_the_first_keyframe_and_starts_the_file_with_it(tmp_path):
    packets = h264(6, gop=6)
    cap = capture_reading(tmp_path, time.monotonic())
    cap._read_stream(io.BytesIO(mpegts(packets)), 0)
    first = (tmp_path / "first").read_bytes()
    assert first.endswith(packets[0][0])   # MPEG-TS puts an access unit delimiter first
    t, key, data = read_ts(tmp_path / OUT)[0]
    assert (t, key) == (0.0, True) and data.endswith(packets[0][0])


def test_an_old_connections_reader_stops_once_a_new_one_exists(tmp_path):
    cap = capture_reading(tmp_path, time.monotonic())
    cap.generation = 1
    assert cap._read_stream(io.BytesIO(mpegts(h264(12))), 0) == 0
    assert not (tmp_path / OUT).exists() and not (tmp_path / "first").exists()


def test_the_file_carries_live_and_held_pictures_at_their_times(tmp_path):
    """From the Holder to the file: a camera's packets at the times they were
    given, then its last keyframe held at the session clock's now, and the
    camera's next keyframe after the last held picture even when its own
    time is a little before it."""
    packets = h264(count=7)                # a keyframe, five P-frames, a keyframe
    clock = Clock()
    out = capture._TsFile(tmp_path / "c.ts")
    h = capture.Holder(out.write, clock=clock)
    t = clock.t
    for i, (data, key) in enumerate(packets[:6]):
        h.packet(int(key), data, t + i * PERIOD)
    clock.t = t + 5 * PERIOD + 0.6         # the camera goes quiet: its keyframe is held
    assert h.tick() is True
    h.packet(1, packets[6][0], clock.t - 0.002)

    got = read_ts(tmp_path / "c.ts")
    want = [p for p, _ in packets[:6]] + [packets[0][0], packets[6][0]]
    assert len(got) == len(want) and all(g.endswith(w) for (_, _, g), w in zip(got, want))
    assert [k for _, k, _ in got] == [True, *[False] * 5, True, True]
    assert [x for x, _, _ in got] == pytest.approx(
        [0.0] + [t + i * PERIOD for i in range(1, 6)] + [clock.t, clock.t + 1 / 90_000],
        abs=1 / 90_000)


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


# --- the capture's ffmpeg: connecting, reconnecting, ending ----------------------

# A stand-in for ffmpeg: MODE SPAWNS [TS]. Each run notes "<pid> <time>" in
# SPAWNS. "sleep" sends nothing; "exit" exits 1 at once; "send" sends TS on its
# first run only, then sleeps (a camera that stalls); "stderr" first writes
# 1 MB of lines to stderr.
_FAKE = """
import os, sys, time
mode, spawns = sys.argv[1], sys.argv[2]
with open(spawns, "a") as f:
    f.write(f"{os.getpid()} {time.monotonic()}\\n")
first = len(open(spawns).read().splitlines()) == 1
if mode == "exit":
    sys.exit(1)
if mode == "stderr":
    for _ in range(10_000):
        sys.stderr.write("x" * 99 + "\\n")
    sys.stderr.flush()
if mode in ("send", "stderr") and first:
    time.sleep(0.3)
    sys.stdout.buffer.write(open(sys.argv[3], "rb").read())
    sys.stdout.flush()
time.sleep(3600)
"""


def fake_ffmpeg(monkeypatch, tmp_path, mode, ts=b""):
    """Make captures run the stand-in in ``mode``; the file its runs note
    themselves in."""
    fake, spawns, src = tmp_path / "fake_ffmpeg.py", tmp_path / "spawns", tmp_path / "src.ts"
    fake.write_text(_FAKE)
    src.write_bytes(ts)
    monkeypatch.setattr(capture, "ffmpeg_command",
                        lambda camera: [sys.executable, str(fake), mode, str(spawns), str(src)])
    return spawns


def spawned(spawns):
    """[(pid, time)] of the stand-in's runs."""
    if not spawns.exists():
        return []
    return [(int(p), float(t)) for p, t in (line.split() for line in spawns.read_text().splitlines())]


def alive(pid):
    """Whether ``pid`` runs: not gone, and not a zombie."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except FileNotFoundError:
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


def running_capture(tmp_path):
    return capture_reading(tmp_path, time.monotonic())


def stop_after(cap, seconds):
    timer = threading.Timer(seconds, cap.stop, (0, "test over"))
    timer.start()
    return timer


def test_a_camera_that_never_answers_ends_the_capture_after_first_s(monkeypatch, tmp_path, caplog):
    fake_ffmpeg(monkeypatch, tmp_path, "sleep")
    monkeypatch.setattr(capture, "FIRST_S", 0.3)
    cap = running_capture(tmp_path)
    began = time.monotonic()
    assert cap.run() == 1
    assert time.monotonic() - began < 2.0
    assert "no keyframe within" in caplog.text


def test_a_stalled_stream_reconnects_at_doubling_gaps(monkeypatch, tmp_path):
    spawns = fake_ffmpeg(monkeypatch, tmp_path, "send", mpegts(camera_h264(12)))
    cap = running_capture(tmp_path)
    cap.reconnects = capture.Reconnects(first_s=0.2, max_s=0.8)
    stop_after(cap, 3.5)
    cap.run()

    runs = spawned(spawns)
    assert len(runs) >= 4
    gaps = np.diff([t for _, t in runs])
    assert all(b >= a - 0.15 for a, b in zip(gaps[1:], gaps[2:]))
    assert gaps.max() <= 0.8 + 0.3
    time.sleep(0.2)
    assert not any(alive(pid) for pid, _ in runs)


def test_an_ffmpeg_that_exits_at_once_is_not_restarted_in_a_tight_loop(monkeypatch, tmp_path):
    spawns = fake_ffmpeg(monkeypatch, tmp_path, "exit")
    cap = running_capture(tmp_path)
    cap.reconnects = capture.Reconnects(first_s=0.2, max_s=0.8)
    stop_after(cap, 2.0)
    cap.run()
    assert 2 <= len(spawned(spawns)) <= 5


def test_an_old_connections_last_packets_stop_once_a_reconnect_begins(monkeypatch, tmp_path):
    """Killing the old ffmpeg ends its stream, and the demuxer then lets go
    of the packet it held. That one must not count as the camera heard (it
    would start the backoff over) nor race the new connection's packets."""
    fake_ffmpeg(monkeypatch, tmp_path, "send", mpegts(camera_h264(12)))
    cap = running_capture(tmp_path)
    after, reconnecting = [], threading.Event()
    real_packet, real_end = cap.holder.packet, cap._end_ffmpeg

    def packet(*args, **kwargs):
        after.append(reconnecting.is_set())
        return real_packet(*args, **kwargs)

    def end_and_let_it_drain():
        reconnecting.set()
        real_end()
        time.sleep(0.3)

    cap.holder.packet = packet
    cap._connect()
    deadline = time.monotonic() + 5
    while len(after) < 10 and time.monotonic() < deadline:
        time.sleep(0.02)
    time.sleep(0.3)
    assert 10 <= len(after) < 12           # the last waits for the stream's end
    cap._end_ffmpeg = end_and_let_it_drain
    try:
        cap._connect()
    finally:
        real_end()
    assert not any(after)


def test_ffmpeg_runs_under_setpriv_with_no_python_in_the_child(monkeypatch, tmp_path):
    """Python code run in a child forked from a process with threads (a
    preexec_fn) can deadlock, and then Popen never returns and the camera
    freezes. setpriv sets the parent-death signal instead."""
    fake_ffmpeg(monkeypatch, tmp_path, "sleep")
    started, real_popen = [], subprocess.Popen

    def popen(args, **kwargs):
        started.append((args, kwargs))
        return real_popen(args, **kwargs)

    monkeypatch.setattr(capture.subprocess, "Popen", popen)
    cap = running_capture(tmp_path)
    try:
        cap._connect()
    finally:
        cap._end_ffmpeg()
    (args, kwargs), = started
    assert args[:4] == ["setpriv", "--pdeathsig", "KILL", "--"]
    assert args[4:] == capture.ffmpeg_command("2A-HOUSE")
    assert kwargs.get("preexec_fn") is None


def test_packets_from_an_mpegts_source_reach_the_file_with_their_times(monkeypatch, tmp_path):
    source = camera_h264(12, gop=6)
    fake_ffmpeg(monkeypatch, tmp_path, "send", mpegts(source))
    cap = running_capture(tmp_path)
    stop_after(cap, 0.7)                   # before the Holder repeats anything
    cap.run()

    # The demuxer gives a packet once the next one starts, so the last of the
    # source waits for its end (live, the next comes 1/60 s later).
    source = source[:-1]
    got = read_ts(tmp_path / OUT)[:len(source)]
    assert [k for _, k, _ in got] == [k for _, k in source]
    assert all(data.endswith(src) for (_, _, data), (src, _) in zip(got, source))
    # The stand-in sends the file at once, a burst CameraClock times by
    # arrival; the spacing of live packets is test_the_srt_reader_times_*'s.
    assert all(np.diff([t for t, _, _ in got]) > 0)


def test_head_lines_follow_what_was_written(monkeypatch, tmp_path, capsys):
    """The recorder learns how far each camera's file reaches from these."""
    fake_ffmpeg(monkeypatch, tmp_path, "send", mpegts(camera_h264(12, gop=6)))
    cap = running_capture(tmp_path)
    stop_after(cap, 1.5)
    cap.run()
    heads = [float(line.split()[1]) for line in capsys.readouterr().out.splitlines()
             if line.startswith("@head ")]
    assert len(heads) >= 2 and all(np.diff(heads) > 0)
    assert cap.out.head - heads[-1] < 0.6


def test_a_write_error_ends_the_capture(monkeypatch, tmp_path, caplog):
    """/dev/full takes the open and refuses every write, as a full disk does."""
    spawns = fake_ffmpeg(monkeypatch, tmp_path, "send", mpegts(camera_h264(12, gop=6)))
    args = capture._parser().parse_args(["--camera", "2A-HOUSE", "--out", "/dev/full",
                                         "--first", str(tmp_path / "first"), "--t0",
                                         str(time.monotonic())])
    args.parent = os.getppid()
    cap = capture._Capture(args)
    timer = stop_after(cap, 5.0)
    began = time.monotonic()
    with caplog.at_level(logging.ERROR, logger="curling_score.cameras.capture"):
        assert cap.run() == 1
    timer.cancel()
    assert time.monotonic() - began < 3.0
    assert "cannot write /dev/full" in caplog.text and "No space left" in caplog.text
    assert not any(alive(pid) for pid, _ in spawned(spawns))


def test_ffmpegs_stderr_is_drained(monkeypatch, tmp_path):
    fake_ffmpeg(monkeypatch, tmp_path, "stderr", mpegts(camera_h264(6)))
    cap = running_capture(tmp_path)

    def stop_once_first():
        deadline = time.monotonic() + 3.0
        while not (tmp_path / "first").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        cap.stop(0, "test over")

    threading.Thread(target=stop_once_first, daemon=True).start()
    cap.run()
    assert (tmp_path / "first").exists()


# A capture run as the recorder runs it, with the stand-in for ffmpeg.
_CHILD = """
import sys
from curling_score.cameras import capture
fake, spawns, out, first = sys.argv[1:5]
capture.shutil.which = lambda name: "/usr/bin/" + name
capture.ffmpeg_command = lambda camera: [sys.executable, fake, "sleep", spawns]
capture.main(["--camera", "2A-HOUSE", "--out", out, "--first", first, "--t0", "0"])
"""


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGKILL])
def test_a_signal_ends_a_capture_and_its_ffmpeg_at_once(tmp_path, sig):
    """The recorder ends a capture with SIGTERM, and kills one that lingers. A
    capture's ffmpeg must go with it, or it keeps pulling the camera."""
    fake, spawns = tmp_path / "fake_ffmpeg.py", tmp_path / "spawns"
    fake.write_text(_FAKE)
    src = Path(capture.__file__).resolve().parents[2]
    proc = subprocess.Popen([sys.executable, "-c", _CHILD, str(fake), str(spawns),
                             str(tmp_path / OUT), str(tmp_path / "first")],
                            env={**os.environ, "PYTHONPATH": str(src)})
    try:
        deadline = time.monotonic() + 10
        while not spawned(spawns) and time.monotonic() < deadline:
            time.sleep(0.05)
        (pid, _), = spawned(spawns)
        proc.send_signal(sig)
        began = time.monotonic()
        proc.wait(timeout=3)
        assert time.monotonic() - began < 1.0
        while alive(pid) and time.monotonic() - began < 2.0:
            time.sleep(0.05)
        assert not alive(pid)
    finally:
        proc.kill()
        proc.wait()
        for pid, _ in spawned(spawns):
            with contextlib.suppress(ProcessLookupError):   # it may go as we look
                os.kill(pid, signal.SIGKILL)


def test_without_ffmpeg_a_capture_exits_naming_it(monkeypatch, tmp_path):
    """A capture outside the worker image cannot reach any camera: it says
    why, so the recorder's error can say it too."""
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(SystemExit) as exited:
        capture.main(["--camera", "2A-HOUSE", "--out", str(tmp_path / OUT),
                      "--first", str(tmp_path / "first"), "--t0", "0"])
    assert str(exited.value.code).startswith("ffmpeg is not installed")
