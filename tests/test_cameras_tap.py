"""The packet tap's framing and the environment a capture process runs in."""

import io
import re
import shutil
import struct
import subprocess
from pathlib import Path

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


# The tap itself, built on this machine against a stand-in libavcodec.so.61 and
# driven the way libndi drives it: one H.264 context, and packets sent from one
# thread while nobody reads the pipe.
_TAP_C = Path(tap.__file__).with_name("ndi_tap.c")

_STUB = r"""
#include <errno.h>
#include <stdlib.h>
#include <libavcodec/avcodec.h>
AVCodecContext *avcodec_alloc_context3(const AVCodec *codec) { return calloc(1, 4096); }
int avcodec_send_packet(AVCodecContext *ctx, const AVPacket *pkt) { return 0; }
int avcodec_receive_frame(AVCodecContext *ctx, AVFrame *frame) { return AVERROR(EAGAIN); }
"""

# argv[1] is the packets to send, K for a keyframe and P for the rest, each
# 1000 bytes, and "." for a pause that lets the tap's writer catch up. The pipe
# holds one 4 KiB page. When all are sent it reads the pipe until nothing comes
# for half a second, and prints each record's flag.
_DRIVER = r"""
#define _GNU_SOURCE
#include <fcntl.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <libavcodec/avcodec.h>

int main(int argc, char **argv) {
    int p[2];
    if (pipe(p) < 0 || fcntl(p[1], F_SETPIPE_SZ, 4096) < 0)
        return 2;
    char fd[16];
    snprintf(fd, sizeof fd, "%d", p[1]);
    setenv("NDI_TAP_FD", fd, 1);
    AVCodec h264 = {.id = AV_CODEC_ID_H264};
    AVCodecContext *ctx = avcodec_alloc_context3(&h264);
    static uint8_t data[1000];
    for (const char *c = argv[1]; *c; c++) {
        if (*c == '.') {
            usleep(200000);
            continue;
        }
        AVPacket pkt;
        memset(&pkt, 0, sizeof pkt);
        pkt.data = data;
        pkt.size = sizeof data;
        pkt.flags = *c == 'K' ? AV_PKT_FLAG_KEY : 0;
        avcodec_send_packet(ctx, &pkt);
    }
    puts("sent");
    fflush(stdout);
    static uint8_t got[1 << 20];
    size_t n = 0;
    struct pollfd in = {.fd = p[0], .events = POLLIN};
    while (n < sizeof got && poll(&in, 1, 500) > 0) {
        ssize_t r = read(p[0], got + n, sizeof got - n);
        if (r <= 0)
            break;
        n += (size_t)r;
    }
    for (size_t i = 0; i + 5 <= n;) {
        uint32_t size = got[i] | got[i + 1] << 8 | got[i + 2] << 16 | (uint32_t)got[i + 3] << 24;
        putchar(got[i + 4] & 1 ? 'K' : 'P');
        i += 5 + size;
    }
    putchar('\n');
    return 0;
}
"""


@pytest.fixture(scope="module")
def tap_driver(tmp_path_factory):
    if shutil.which("gcc") is None:
        pytest.skip("no gcc")
    d = tmp_path_factory.mktemp("tap")
    (d / "stub.c").write_text(_STUB)
    (d / "driver.c").write_text(_DRIVER)
    gcc = ["gcc", "-O2", "-Wall"]
    try:
        subprocess.run([*gcc, "-shared", "-fPIC", "-o", d / "libavcodec.so.61", d / "stub.c",
                        "-Wl,-soname,libavcodec.so.61"], check=True, capture_output=True)
        # A queue of three records, so a pipe nobody reads fills it at once.
        subprocess.run([*gcc, "-shared", "-fPIC", "-DTAP_QUEUE_BYTES=3015", "-o",
                        d / "libavcodec-ndi.so.61", _TAP_C, "-Wl,--no-as-needed",
                        d / "libavcodec.so.61", "-Wl,-rpath,$ORIGIN", "-lpthread",
                        "-Wl,-soname,libavcodec-ndi.so.61"], check=True, capture_output=True)
        subprocess.run([*gcc, "-o", d / "driver", d / "driver.c", d / "libavcodec-ndi.so.61",
                        "-Wl,-rpath,$ORIGIN"], check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        if b"avcodec.h" in exc.stderr:
            pytest.skip("no libavcodec headers")
        raise AssertionError(exc.stderr.decode()) from exc
    return d / "driver"


def test_tap_never_blocks_libndi_and_drops_to_a_keyframe(tap_driver):
    """With nobody reading, every send returns at once. What the queue and the
    pipe cannot hold is dropped up to the next keyframe, so the reader gets a
    run that starts at a keyframe, then a gap, then a run from the next one."""
    out = subprocess.run([tap_driver, "K" + "P" * 30 + ".KPP"], capture_output=True,
                         text=True, timeout=10)
    lines = out.stdout.split("\n")
    assert lines[0] == "sent"
    got = lines[1]
    assert re.fullmatch(r"KP+KPP", got), got
    assert got.count("P") < 32
    assert "dropping to the next keyframe" in out.stderr
    assert "queueing again at a keyframe" in out.stderr
