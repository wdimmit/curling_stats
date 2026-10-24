"""Capture: which camera is which, and holding a camera's picture still while
its packets stop."""

import json
import logging
import threading

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
    return capture.Holder(out.append, clock=clock), clock, out


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


def test_full_queue_never_blocks_the_reader_and_resumes_at_a_keyframe(monkeypatch, tmp_path,
                                                                      caplog):
    """A composer that stops reading must not back up into the tap, and so
    into libndi: what does not fit is dropped, and the stream picks up again
    at a keyframe."""
    monkeypatch.setattr(capture, "BACKLOG", 3)
    fifo = capture._Fifo(tmp_path / "cam.h264")
    clock = Clock()
    h = capture.Holder(fifo.write, clock=clock)

    def overflow():
        h.packet(1, SPS_PPS_IDR)
        h.packet(0, P1)
        h.packet(0, P2)
        h.packet(0, b"\x00\x00\x00\x01\x41dropped")
        h.packet(1, SPS_PPS_IDR + b"dropped")
        clock.t += 0.6
        h.tick()

    with caplog.at_level(logging.WARNING):
        reader = threading.Thread(target=overflow, daemon=True)
        reader.start()
        reader.join(timeout=2.0)
    assert not reader.is_alive(), "Holder.packet blocked on a full queue"
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1

    queued = [fifo._queue.get_nowait() for _ in range(3)]
    assert queued == [SPS_PPS_IDR, P1, P2]
    h.packet(0, b"\x00\x00\x00\x01\x41after")
    assert fifo._queue.empty()
    h.packet(1, SPS_PPS_IDR + b"2")
    h.packet(0, P1)
    assert [fifo._queue.get_nowait() for _ in range(2)] == [SPS_PPS_IDR + b"2", P1]


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
