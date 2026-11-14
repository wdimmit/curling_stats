"""A kept camera game cut to begin where its stream began.

A camera game on a ``cameras`` playlist is recorded from its league's schedule
start (``service.playlists.standbys``), ahead of its stream. Only the
broadcast is kept: when the game is kept (``live.manager``), each camera's
file is copied from the keyframe before the cut on, the cut becoming the new
zero, and ``clock.json`` moved with it, so the files read as a recording that
began with the stream.

Nothing is decoded. The copy is written as a capture writes
(``capture._TsFile``): the keyframe opening it at 0, the packets between it
and the cut a tick apart after it, as a camera's first picture is held from a
recording's zero, and every later packet at its time less the cut. So every
camera's file starts on the one clock, as the readers count a file's times
from its start (``ingest.frames``).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import av

from curling_score.cameras.capture import _TsFile

log = logging.getLogger(__name__)

# A cut this close to the start is not worth copying the files for.
MIN_CUT_S = 1.0
# In a kept game's clock.json while its files are still to be cut: a reader
# (the clips worker) waits rather than read files being replaced.
PENDING = "trim_pending"


def _trim_file(path: Path, cut_s: float) -> None:
    out_path = path.with_name(path.name + ".trim")
    out = None
    try:
        with av.open(str(path)) as container:
            stream = container.streams.video[0]
            origin = (container.start_time or 0) / 1e6
            held = []                  # since the last keyframe before the cut
            for packet in container.demux(stream):
                if packet.pts is None or not packet.size:
                    continue
                t = float(packet.pts * stream.time_base) - origin
                data, key = bytes(packet), packet.is_keyframe
                if out is None:
                    if t < cut_s:
                        if key:
                            held = []
                        if key or held:
                            held.append((data, t, key))
                        continue
                    out = _TsFile(out_path)
                    for d, ht, k in held:
                        out.write(d, max(ht - cut_s, 0.0), k)
                    held = []
                    if not out.write(data, t - cut_s, key):
                        raise OSError(f"could not write {out_path}")
                elif not out.write(data, t - cut_s, key):
                    raise OSError(f"could not write {out_path}")
    finally:
        if out is not None and out._out is not None:
            out._out.close()
            out._file.close()
    if out is None:
        # Nothing at or after the cut: an empty view, as a camera that sent
        # nothing would leave.
        out_path.write_bytes(b"")
    os.replace(out_path, path)


def trim_files(directory, cut_s: float) -> float:
    """Each ``<role>.ts`` in ``directory`` cut to begin at ``cut_s`` on the
    recording's clock; returns the cut made (0.0 when it was too small to
    make: ``MIN_CUT_S``)."""
    if cut_s < MIN_CUT_S:
        return 0.0
    for path in sorted(Path(directory).glob("*.ts")):
        _trim_file(path, cut_s)
    return cut_s


def shift_clock(clock: dict | None, cut_s: float) -> dict | None:
    """A camera game's clock (``timeline.offset_at``) for its files cut at
    ``cut_s``: the same moment of the video for the same picture."""
    if not clock:
        return clock
    moved = dict(clock)
    if moved.get("offset_s") is not None:
        moved["offset_s"] = round(moved["offset_s"] + cut_s, 3)
    if moved.get("steps"):
        moved["steps"] = [[round(max(start - cut_s, 0.0), 3), round(off + cut_s, 3)]
                          for start, off in moved["steps"]]
    return moved


def _rewrite(meta_path: Path, meta: dict) -> None:
    tmp = meta_path.with_name("clock.json.tmp")
    tmp.write_text(json.dumps(meta))
    os.replace(tmp, meta_path)


def clear_pending(directory) -> None:
    """The kept game's files are as they will stay: ``PENDING`` dropped from
    its clock.json. Nothing to do when it has no flag, or no clock.json."""
    meta_path = Path(directory) / "clock.json"
    try:
        meta = json.loads(meta_path.read_text())
    except (FileNotFoundError, ValueError):
        return
    if meta.pop(PENDING, None) is not None:
        _rewrite(meta_path, meta)


def trim_kept(directory, cut_s: float) -> float:
    """A kept camera game (``<role>.ts`` and ``clock.json``) cut at ``cut_s``,
    its clock moved with it and the cut noted (``start_trimmed_s``); its
    ``PENDING`` flag dropped whether or not a cut was made."""
    directory = Path(directory)
    made = trim_files(directory, cut_s)
    if made:
        meta_path = directory / "clock.json"
        meta = json.loads(meta_path.read_text())
        meta["wall_started"] = meta["wall_started"] + made
        meta["clock"] = shift_clock(meta.get("clock"), made)
        meta["start_trimmed_s"] = made
        meta.pop(PENDING, None)
        _rewrite(meta_path, meta)
        log.info("kept cameras in %s cut at %.1f s, where the stream began", directory, made)
    else:
        clear_pending(directory)
    return made
