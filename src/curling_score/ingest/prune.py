"""Keep the media cache inside a disk budget.

A worker that processes a game a day accumulates three gigabytes of video and
half a gigabyte of proxy for each one, and nothing ever asks for most of them
again. Detections are tiny and are kept; the media is what goes, least recently
used first, until the caches fit -- and until the disk has room to spare, since
the budget is set once while the disk also holds whatever else the box keeps,
and a live night records several streams at once before any of them is kept.

Live recordings kept past their stream (``cache.KEPT_DIR``) also go after a
rolling window of days, so the disk holds the last week of league nights, for
investigating what the nightly review flags, rather than whatever was read
least recently.
"""

import glob
import json
import shutil
import time
from pathlib import Path

from curling_score.ingest import cache

# Anything touched this recently may belong to a job that is still running.
RECENT_S = 3600.0
MEDIA_DIRS = ("videos", "proxies", cache.KEPT_DIR)
DAY_S = 86400.0


def media_files(root) -> list[Path]:
    root = Path(root)
    out = []
    for name in MEDIA_DIRS:
        d = root / name
        if d.is_dir():
            out.extend(p for p in d.iterdir()
                       if p.is_file() and p.suffix not in (".json", ".tmp"))
    return out


def recording_files(root, vid: str) -> list[Path]:
    """The media that exists for one kept recording: the video or the partial
    recording, and any proxies built from it."""
    root = Path(root)
    out = [cache.video_path(vid, root), cache.partial_path(vid, root)]
    # A proxy is named "<vid>.<key>.mp4"; ids never hold a ".", so this prefix
    # is the video's own. escape(): treat the id literally, whatever it holds.
    out += sorted((root / "proxies").glob(f"{glob.escape(vid)}.*"))
    return [p for p in out if p.is_file()]


def expire_recordings(root, days: float, now: float | None = None) -> list[tuple[Path, int]]:
    """Delete live recordings kept more than ``days`` ago; return what went
    and how big it was.

    A recording read within the last hour stays until it is not: a reprocess
    may be reading it. A marker that cannot be read is left alone -- it may be
    half written -- and one whose media has already gone is cleared."""
    now = time.time() if now is None else now
    kept = Path(root) / cache.KEPT_DIR
    removed = []
    if not kept.is_dir():
        return removed
    for marker in sorted(kept.glob("*.json")):
        try:
            kept_at = float(json.loads(marker.read_text())["kept_at"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if now - kept_at < days * DAY_S:
            continue
        files = []
        for p in recording_files(root, marker.stem):
            try:
                st = p.stat()
            except FileNotFoundError:
                continue
            files.append((p, st))
        if any(now - st.st_atime < RECENT_S for _p, st in files):
            continue
        for p, st in files:
            p.unlink(missing_ok=True)
            removed.append((p, st.st_size))
        marker.unlink(missing_ok=True)
    return removed


def prune(root, keep_gb: float, now=None, *, min_free_gb: float = 0.0,
          free_bytes=None, recording_days: float | None = None) -> list[Path]:
    """Delete live recordings past ``recording_days``, then least-recently-read
    media until the caches fit in ``keep_gb`` and the disk has ``min_free_gb``
    free; return what went.

    Partially written files and anything read within the last hour are left
    alone -- a job in progress must never find its input gone. ``free_bytes``
    is the disk's free space, measured when not given.
    """
    now = time.time() if now is None else now
    expired = expire_recordings(root, recording_days, now) if recording_days else []
    removed = [p for p, _size in expired]
    budget, floor = keep_gb * 1e9, min_free_gb * 1e9
    free = (shutil.disk_usage(root).free if free_bytes is None
            else free_bytes + sum(size for _p, size in expired))
    files = []
    for p in media_files(root):
        if ".part" in p.name:
            continue
        try:
            st = p.stat()
        except FileNotFoundError:     # pruned by the other thread just now
            continue
        files.append((st.st_atime, st.st_size, p))
    total = sum(size for _a, size, _p in files)
    for atime, size, path in sorted(files):
        if total <= budget and free >= floor:
            break
        if now - atime < RECENT_S:
            continue
        path.unlink(missing_ok=True)
        removed.append(path)
        total -= size
        free += size
    return removed
