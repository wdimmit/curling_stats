"""The packet tap's framing and the environment a capture process runs in."""

import io
import re
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from curling_score.cameras import tap


NO_PTS = -(1 << 63)  # AV_NOPTS_VALUE


def record(flags, payload, pts=0, arrived_ns=0):
    return struct.pack("<IBqq", len(payload), flags, pts, arrived_ns) + payload


def test_read_records_splits_frames_flags_pts_and_arrival():
    key = b"\x00\x00\x00\x01e" + bytes(range(40))
    inter = b"\x00\x00\x00\x01A" + b"\xff" * 7
    # A HOUSE camera's clock, years off, in libndi's 100 ns units.
    f = io.BytesIO(record(1, key, 16_419_840_000_000_000, 812_345_678_901_234)
                   + record(0, inter, 16_419_840_000_166_667, 812_345_695_567_901))

    assert list(tap.read_records(f)) == [
        (1, 16_419_840_000_000_000, pytest.approx(812_345.678901234, abs=1e-9), key),
        (0, 16_419_840_000_166_667, pytest.approx(812_345.695567901, abs=1e-9), inter)]


def test_a_packet_without_a_pts_reads_as_none():
    (got,) = tap.read_records(io.BytesIO(record(0, b"\x00\x00\x00\x01A", NO_PTS, 10**9)))

    assert got == (0, None, 1.0, b"\x00\x00\x00\x01A")


def test_read_records_stops_cleanly_at_eof():
    assert list(tap.read_records(io.BytesIO(b""))) == []


@pytest.mark.parametrize("cut", [2, 5, 13, 20, 21, 25])
def test_read_records_truncated_raises(cut):
    whole = record(1, b"\x00\x00\x00\x01e" + b"\x00" * 4, 7, 8)
    f = io.BytesIO(record(0, b"\x00\x00\x00\x01A", 5, 6) + whole[:cut])

    records = tap.read_records(f)
    assert next(records) == (0, 5, 6e-9, b"\x00\x00\x00\x01A")
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

    assert [flags for flags, *_ in tap.read_records(Trickle())] == [1, 0]


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
# 1000 bytes with the pts PTS0 + its number, N for a P without a pts, F for a P
# the tap finds no memory to copy (its malloc fails), and "." for a pause that
# lets the tap's writer catch up. The pipe holds one 4 KiB
# page. It prints "S pts before after" for each send, with CLOCK_MONOTONIC in
# ns either side of it, then "sent". When all are sent it reads the pipe until
# nothing comes for half a second, and prints "R flag pts arrived" for each
# record.
_DRIVER = r"""
#define _GNU_SOURCE
#include <fcntl.h>
#include <inttypes.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <libavcodec/avcodec.h>

/* The tap's malloc, through the executable's: it fails once when asked. */
extern void *__libc_malloc(size_t);
static volatile int fail_alloc;
void *malloc(size_t n) {
    if (fail_alloc && n > 1000) {
        fail_alloc = 0;
        return NULL;
    }
    return __libc_malloc(n);
}

static int64_t now_ns(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (int64_t)t.tv_sec * 1000000000 + t.tv_nsec;
}

static uint64_t le(const uint8_t *p, int n) {
    uint64_t v = 0;
    for (int i = n - 1; i >= 0; i--)
        v = v << 8 | p[i];
    return v;
}

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
    int64_t number = 0;
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
        pkt.pts = *c == 'N' ? AV_NOPTS_VALUE : PTS0 + number;
        number++;
        fail_alloc = *c == 'F';
        int64_t before = now_ns();
        avcodec_send_packet(ctx, &pkt);
        printf("S %" PRId64 " %" PRId64 " %" PRId64 "\n", pkt.pts, before, now_ns());
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
    for (size_t i = 0; i + 21 <= n;) {
        printf("R %c %" PRId64 " %" PRId64 "\n", got[i + 4] & 1 ? 'K' : 'P',
               (int64_t)le(got + i + 5, 8), (int64_t)le(got + i + 13, 8));
        i += 21 + le(got + i, 4);
    }
    return 0;
}
"""
# A camera's clock in 100 ns units, far enough from 0 to need all 64 bits.
PTS0 = 16_419_840_000_000_000


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
        # A queue of three 1021-byte records, so a pipe nobody reads fills it
        # at once.
        # This machine's FFmpeg may not be the 7 the tap is built for in the
        # image: its packet handling is the same, so the test lets it build.
        subprocess.run([*gcc, "-shared", "-fPIC", "-DTAP_QUEUE_BYTES=3063",
                        "-DNDI_TAP_ANY_LIBAVCODEC", "-o",
                        d / "libavcodec-ndi.so.61", _TAP_C, "-Wl,--no-as-needed",
                        d / "libavcodec.so.61", "-Wl,-rpath,$ORIGIN", "-lpthread",
                        "-Wl,-soname,libavcodec-ndi.so.61"], check=True, capture_output=True)
        subprocess.run([*gcc, f"-DPTS0={PTS0}LL", "-o", d / "driver", d / "driver.c",
                        d / "libavcodec-ndi.so.61", "-Wl,-rpath,$ORIGIN"],
                       check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        if b"avcodec.h" in exc.stderr:
            pytest.skip("no libavcodec headers")
        raise AssertionError(exc.stderr.decode()) from exc
    return d / "driver"


def drive(tap_driver, packets):
    """What the driver sent, {pts: (before, after)}, and the records the
    reader got, [(flag, pts, arrived)]."""
    out = subprocess.run([tap_driver, packets], capture_output=True, text=True, timeout=10)
    lines = out.stdout.splitlines()
    sent = lines.index("sent")
    sends = {int(pts): (int(a), int(b)) for _, pts, a, b in (s.split() for s in lines[:sent])}
    records = [(flag, int(pts), int(at)) for _, flag, pts, at in
               (r.split() for r in lines[sent + 1:])]
    return sends, records, out.stderr


def test_tap_never_blocks_libndi_and_drops_to_a_keyframe(tap_driver):
    """With nobody reading, every send returns at once. What the queue and the
    pipe cannot hold is dropped up to the next keyframe, so the reader gets a
    run that starts at a keyframe, then a gap, then a run from the next one."""
    _, records, stderr = drive(tap_driver, "K" + "P" * 30 + ".KPP")
    got = "".join(flag for flag, _, _ in records)
    assert re.fullmatch(r"KP+KPP", got), got
    assert got.count("P") < 32
    assert "dropping to the next keyframe" in stderr
    assert "queueing again at a keyframe" in stderr


def test_a_record_carries_the_packets_pts_and_when_libndi_sent_it(tap_driver):
    """The arrival is taken in libndi's own call, not when the writer gets to
    the record: a Python reader can be held off the GIL. Records the pipe could
    not take until the end were still stamped as they were sent."""
    # The pipe holds four records, and the last two wait in the queue.
    sends, records, _ = drive(tap_driver, "KPN.PPP")
    assert [pts for _, pts, _ in records] == [PTS0, PTS0 + 1, NO_PTS, PTS0 + 3, PTS0 + 4,
                                             PTS0 + 5]
    for _, pts, arrived in records:
        before, after = sends[pts]
        assert before <= arrived <= after
    # The pause comes between the third packet's arrival and the fourth's.
    assert records[3][2] - records[2][2] >= 200_000_000


def test_a_packet_the_tap_has_no_memory_for_drops_to_the_next_keyframe(tap_driver):
    """Lost before it was queued, a packet would leave the P-frames after it
    without their reference: they are dropped too, up to the next keyframe."""
    _, records, stderr = drive(tap_driver, "KPFPP.KP")
    assert "".join(flag for flag, _, _ in records) == "KPKP"
    assert [pts - PTS0 for _, pts, _ in records] == [0, 1, 5, 6]
    assert "queueing again at a keyframe, 3 packets dropped" in stderr


def _libavcodec_major():
    for include in ("/usr/include", "/usr/include/x86_64-linux-gnu",
                    "/usr/include/aarch64-linux-gnu"):
        header = Path(include) / "libavcodec" / "version_major.h"
        if header.exists():
            got = re.search(r"#define LIBAVCODEC_VERSION_MAJOR\s+(\d+)", header.read_text())
            return int(got.group(1)) if got else None
    return None


def test_the_tap_refuses_to_build_against_another_libavcodec(tmp_path):
    """Built against another FFmpeg's headers, its AVPacket would not be the
    one libndi hands it: the production build stops instead."""
    if shutil.which("gcc") is None:
        pytest.skip("no gcc")
    major = _libavcodec_major()
    if major is None or major == 61:
        pytest.skip("needs libavcodec headers other than FFmpeg 7's")
    out = subprocess.run(["gcc", "-fsyntax-only", _TAP_C], capture_output=True, text=True)
    assert out.returncode != 0
    assert "the tap is built for libavcodec 61 (FFmpeg 7)" in out.stderr
