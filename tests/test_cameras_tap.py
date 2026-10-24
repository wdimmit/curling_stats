"""The packet tap's framing and the environment a capture process runs in."""

import io
import struct

import pytest

from curling_score.cameras import tap


def record(flags, payload):
    return struct.pack("<IB", len(payload), flags) + payload


def test_read_records_splits_frames_and_flags():
    key = b"\x00\x00\x00\x01e" + bytes(range(40))
    inter = b"\x00\x00\x00\x01A" + b"\xff" * 7
    f = io.BytesIO(record(1, key) + record(0, inter))

    assert list(tap.read_records(f)) == [(1, key), (0, inter)]


def test_read_records_stops_cleanly_at_eof():
    assert list(tap.read_records(io.BytesIO(b""))) == []


@pytest.mark.parametrize("cut", [2, 4, 5, 9])
def test_read_records_truncated_raises(cut):
    whole = record(1, b"\x00\x00\x00\x01e" + b"\x00" * 4)
    f = io.BytesIO(record(0, b"\x00\x00\x00\x01A") + whole[:cut])

    records = tap.read_records(f)
    assert next(records) == (0, b"\x00\x00\x00\x01A")
    with pytest.raises(ValueError):
        next(records)


def test_read_records_reads_a_pipe_in_pieces():
    """A pipe hands back what has arrived, not whole records."""
    data = record(1, b"\x00\x00\x00\x01e" * 3) + record(0, b"\x00\x00\x00\x01A")

    class Trickle(io.RawIOBase):
        def __init__(self):
            self.left = data

        def readable(self):
            return True

        def readinto(self, b):
            n = min(len(b), 3, len(self.left))
            b[:n], self.left = self.left[:n], self.left[n:]
            return n

    assert [flags for flags, _ in tap.read_records(Trickle())] == [1, 0]


def test_tap_env_puts_tap_dir_first_and_sets_decode(monkeypatch):
    monkeypatch.setenv("LD_LIBRARY_PATH", "/usr/local/lib")
    env = tap.tap_env(7, decode=False)

    assert env["LD_LIBRARY_PATH"].split(":") == ["/opt/ndi", "/usr/local/lib"]
    assert env["NDI_TAP_FD"] == "7"
    assert env["NDI_TAP_DECODE"] == "0"
    assert tap.tap_env(7, decode=True)["NDI_TAP_DECODE"] == "1"


def test_tap_env_without_a_library_path(monkeypatch):
    monkeypatch.delenv("LD_LIBRARY_PATH", raising=False)

    assert tap.tap_env(3, decode=False)["LD_LIBRARY_PATH"] == "/opt/ndi"
