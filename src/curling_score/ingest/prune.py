"""Keep the media cache inside a disk budget.

A worker that processes a game a day accumulates three gigabytes of video and
half a gigabyte of proxy for each one, and nothing ever asks for most of them
again. Detections are tiny and are kept; the media is what goes, least recently
used first, until the caches fit -- and until the disk has room to spare, since
the budget is set once while the disk also holds whatever else the box keeps,
and a live night records several streams at once before any of them is kept.
"""

import shutil
import time
from pathlib import Path

# Anything touched this recently may belong to a job that is still running.
RECENT_S = 3600.0
MEDIA_DIRS = ("videos", "proxies")


def media_files(root) -> list[Path]:
    root = Path(root)
    out = []
    for name in MEDIA_DIRS:
        d = root / name
        if d.is_dir():
            out.extend(p for p in d.iterdir() if p.is_file())
    return out


def prune(root, keep_gb: float, now=None, *, min_free_gb: float = 0.0,
          free_bytes=None) -> list[Path]:
    """Delete least-recently-read media until the caches fit in ``keep_gb`` and
    the disk has ``min_free_gb`` free; return what went.

    Partially written files and anything read within the last hour are left
    alone -- a job in progress must never find its input gone. ``free_bytes``
    is the disk's free space, measured when not given.
    """
    now = time.time() if now is None else now
    budget, floor = keep_gb * 1e9, min_free_gb * 1e9
    free = shutil.disk_usage(root).free if free_bytes is None else free_bytes
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
    removed = []
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
