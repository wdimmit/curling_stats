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
# Set once every file has been cut beside itself and clock.json moved to the
# cut: from then on the cut is finished by swapping the files in, never undone.
COMMITTING = "trim_committing"
# An interrupted cut (its worker restarted) is recovered by the worker that
# owned it when it starts again, or by any worker once it is this old.
STALE_S = 3600.0


def _write_trim(path: Path, cut_s: float) -> Path:
    """``path`` cut at ``cut_s``, written beside it as ``<name>.trim``."""
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
    return out_path


def trim_files(directory, cut_s: float) -> float:
    """Each ``<role>.ts`` in ``directory`` cut to begin at ``cut_s`` on the
    recording's clock; returns the cut made (0.0 when it was too small to
    make: ``MIN_CUT_S``). Every file is cut beside itself before any is
    replaced, so a cut that fails (a full disk) leaves all of them as they
    were, on the clock they share, never some cut and some not."""
    if cut_s < MIN_CUT_S:
        return 0.0
    paths = sorted(Path(directory).glob("*.ts"))
    written = []
    try:
        for path in paths:
            written.append((_write_trim(path, cut_s), path))
    except BaseException:
        for path in paths:
            path.with_name(path.name + ".trim").unlink(missing_ok=True)
        raise
    for out_path, path in written:
        os.replace(out_path, path)
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
        meta.pop(COMMITTING, None)
        meta.pop("trim_by", None)
        _rewrite(meta_path, meta)


def _read_meta(directory: Path) -> dict | None:
    try:
        return json.loads((directory / "clock.json").read_text())
    except (FileNotFoundError, ValueError):
        return None


def _undo(directory: Path) -> None:
    """A cut that never began swapping: its half-written files go, and the
    game's own files -- untouched, on the clock they share -- are ready."""
    for leftover in directory.glob("*.ts.trim"):
        leftover.unlink(missing_ok=True)
    clear_pending(directory)


def _commit(directory: Path) -> None:
    """Swap every cut file in, then say the game is ready. clock.json already
    holds the cut's clock (COMMITTING), so this is only ever finished."""
    for cut in sorted(directory.glob("*.ts.trim")):
        os.replace(cut, cut.with_name(cut.name[: -len(".trim")]))
    meta = _read_meta(directory) or {}
    for key in (PENDING, COMMITTING, "trim_by"):
        meta.pop(key, None)
    _rewrite(directory / "clock.json", meta)


def trim_kept(directory, cut_s: float) -> float:
    """A kept camera game (``<role>.ts`` and ``clock.json``) cut at ``cut_s``,
    its clock moved with it and the cut noted (``start_trimmed_s``).

    In two steps, so it can always be finished or undone (`recover`): every
    file is cut beside itself; then clock.json takes the cut's clock and
    COMMITTING, and the files are swapped in. A failure in the first step
    leaves the game as it was and ready; one in the second leaves it pending
    -- never read -- until `recover` finishes it."""
    directory = Path(directory)
    if cut_s < MIN_CUT_S:
        clear_pending(directory)
        return 0.0
    paths = sorted(directory.glob("*.ts"))
    try:
        for path in paths:
            _write_trim(path, cut_s)
    except BaseException:
        _undo(directory)
        raise
    meta = _read_meta(directory) or {}
    meta["wall_started"] = meta["wall_started"] + cut_s
    meta["clock"] = shift_clock(meta.get("clock"), cut_s)
    meta["start_trimmed_s"] = cut_s
    meta[PENDING] = True
    meta[COMMITTING] = True
    try:
        _rewrite(directory / "clock.json", meta)
    except BaseException:
        _undo(directory)
        raise
    _commit(directory)
    log.info("kept cameras in %s cut at %.1f s, where the stream began", directory, cut_s)
    return cut_s


def recover(directory) -> str:
    """An interrupted cut of a kept game put right: ``finished`` (it had begun
    swapping), ``undone`` (it had not; the game is as it was) or ``none``."""
    directory = Path(directory)
    meta = _read_meta(directory)
    if meta is None or not meta.get(PENDING):
        return "none"
    if meta.get(COMMITTING):
        _commit(directory)
        return "finished"
    _undo(directory)
    return "undone"


def recover_interrupted(kept_root, worker_id: str, *, now=None, stale_s: float = STALE_S
                        ) -> dict:
    """At a worker's start, every kept game whose cut was left pending by this
    worker (it cannot be cutting it now) or long enough ago by any, put right
    (`recover`); ``{name: outcome}``. Another worker's cut in progress is
    left alone."""
    import time

    now = time.time() if now is None else now
    out = {}
    root = Path(kept_root)
    if not root.is_dir():
        return out
    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        meta = _read_meta(directory)
        if not meta or not meta.get(PENDING):
            continue
        ours = meta.get("trim_by") == worker_id
        stale = now - float(meta.get("kept_at") or 0.0) > stale_s
        if not (ours or stale):
            continue
        try:
            out[directory.name] = recover(directory)
        except OSError as exc:
            log.warning("could not put right the cut of %s: %s", directory, exc)
            continue
        log.info("the interrupted cut of %s: %s", directory, out[directory.name])
    return out
