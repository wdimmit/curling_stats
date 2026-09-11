"""Keep the media cache inside a disk budget.

A worker that processes a game a day accumulates three gigabytes of video and
half a gigabyte of proxy for each one, and nothing ever asks for most of them
again. Detections are tiny and are kept; the media is what goes, least recently
used first, until the caches fit.
"""

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


def prune(root, keep_gb: float, now=None) -> list[Path]:
    """Delete least-recently-read media until the caches fit; return what went.

    Partially written files and anything read within the last hour are left
    alone -- a job in progress must never find its input gone.
    """
    now = time.time() if now is None else now
    budget = keep_gb * 1e9
    files = []
    for p in media_files(root):
        if ".part" in p.name:
            continue
        st = p.stat()
        files.append((st.st_atime, st.st_size, p))
    total = sum(size for _a, size, _p in files)
    removed = []
    for atime, size, path in sorted(files):
        if total <= budget:
            break
        if now - atime < RECENT_S:
            continue
        path.unlink(missing_ok=True)
        removed.append(path)
        total -= size
    return removed
