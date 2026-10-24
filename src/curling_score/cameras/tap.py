"""The packet tap: H.264 straight out of libndi, before anything decodes it.

libndi decodes NDI HX by loading ``libavcodec-ndi.so.61`` ahead of FFmpeg 7's
``libavcodec.so.61``. The worker image puts a stand-in by that name in
``TAP_DIR`` (``ndi_tap.c``, built in Dockerfile.worker): it forwards everything
to the real libavcodec and writes each video packet libndi hands its decoder to
the file descriptor ``NDI_TAP_FD``. Python's own receive loop keeps only
~45-55 of a camera's 60 fps, so decoded frames could never carry 20 cameras;
the packets themselves arrive at the full rate.

Each record is a 4-byte little-endian payload length, one flags byte (bit 0 =
keyframe) and the payload: the packet's Annex-B bytes as the camera sent them.
The first packets of a connection can be P-slices from before the first
keyframe, so whoever reads them waits for a keyframe.

With decode off (the default) the stand-in also stops libndi decoding the
video it never shows us. libndi keeps passing packets at the full 60/s, and
each camera costs ~0.02 of a core instead of ~0.5 (measured 2026-10-24, see
docs/superpowers/plans/2026-10-24-practice-direct-feeds.results.md).
"""

from __future__ import annotations

import os
import struct
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

TAP_DIR = Path("/opt/ndi")
KEYFRAME = 0x01

_HEADER = struct.Struct("<IB")


def tap_env(fd: int, *, decode: bool) -> dict[str, str]:
    """The extra environment for a capture process.

    ``fd`` is the write end the tap writes to, as the capture process sees it
    (pass it with ``subprocess``'s ``pass_fds``). ``LD_LIBRARY_PATH`` must be
    set before the process starts, since the loader reads it only then.
    """
    path = os.environ.get("LD_LIBRARY_PATH")
    return {
        "LD_LIBRARY_PATH": ":".join([str(TAP_DIR)] + ([path] if path else [])),
        "NDI_TAP_FD": str(fd),
        "NDI_TAP_DECODE": "1" if decode else "0",
    }


def read_records(f: BinaryIO) -> Iterator[tuple[int, bytes]]:
    """``(flags, payload)`` for each record until ``f`` ends.

    Ends quietly at EOF between records; a record cut short is a ``ValueError``.
    """
    while True:
        head = _read_exact(f, _HEADER.size)
        if not head:
            return
        if len(head) < _HEADER.size:
            raise ValueError(f"tap record header cut short: {len(head)} of {_HEADER.size} bytes")
        size, flags = _HEADER.unpack(head)
        payload = _read_exact(f, size)
        if len(payload) < size:
            raise ValueError(f"tap record cut short: {len(payload)} of {size} bytes")
        yield flags, payload


def _read_exact(f: BinaryIO, n: int) -> bytes:
    """Up to ``n`` bytes, fewer only at EOF. An unbuffered pipe returns
    whatever has arrived, so one read is not always a whole record."""
    data = f.read(n) or b""
    if len(data) == n or not data:
        return data
    parts, got = [data], len(data)
    while got < n:
        chunk = f.read(n - got)
        if not chunk:
            break
        parts.append(chunk)
        got += len(chunk)
    return b"".join(parts)
