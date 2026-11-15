"""Keep the media cache inside a disk budget.

A worker that processes a game a day accumulates three gigabytes of video and
half a gigabyte of proxy for each one, and nothing ever asks for most of them
again. Detections are tiny and are kept; the media is what goes, least recently
used first, until the caches fit.

Live recordings kept past their stream (``cache.KEPT_DIR``) are kept for a
rolling window of days, space allowing: the last week of league nights, for
investigating what the nightly review flags. They give way first, oldest kept
first, whenever the budget needs room -- and they are all that goes to keep the
disk's free floor, since the budget is set once while the disk also holds
whatever else the box keeps, and a live night records several streams at once
before any of them is kept. A download is never taken for the floor.
"""

import glob
import json
import logging
import shutil
import time
from pathlib import Path

from curling_score.ingest import cache

log = logging.getLogger(__name__)

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


def kept_recordings(root) -> list[tuple[str, dict]]:
    """(video id, marker) for every kept recording whose marker can be read,
    oldest kept first. A marker that cannot be read is left out -- it may be
    half written."""
    kept = Path(root) / cache.KEPT_DIR
    out = []
    if kept.is_dir():
        for marker in kept.glob("*.json"):
            try:
                info = json.loads(marker.read_text())
                info["kept_at"] = float(info["kept_at"])
            except (OSError, ValueError, KeyError, TypeError):
                continue
            out.append((marker.stem, info))
    return sorted(out, key=lambda pair: pair[1]["kept_at"])


def recording_files(root, vid: str, info: dict) -> list[Path]:
    """The media that exists for one kept recording and is still its own.

    A partial recording is ``kept/<id>.ts``, which nothing else writes. A
    whole one is ``videos/<id>.mp4`` with any proxies built from it -- but only
    while that file is the size the marker recorded: a VOD downloaded after
    the recording went (or after a partial was kept) is not the recording,
    and must never age out with it."""
    root = Path(root)
    out = [cache.partial_path(vid, root)]
    video = cache.video_path(vid, root)
    try:
        ours = (bool(info.get("whole")) and info.get("size") is not None
                and video.stat().st_size == info["size"])
    except FileNotFoundError:
        ours = False
    if ours:
        out.append(video)
        # A proxy is named "<vid>.<key>.mp4"; ids never hold a ".", so this
        # prefix is the video's own. escape(): treat the id literally.
        out += sorted((root / "proxies").glob(f"{glob.escape(vid)}.*"))
    return [p for p in out if p.is_file()]


def expire_recordings(root, days: float, now: float | None = None) -> list[tuple[Path, int]]:
    """Delete live recordings kept more than ``days`` ago; return what went
    and how big it was.

    A recording read within the last hour stays until it is not: a reprocess
    may be reading it. A marker whose media has already gone is cleared."""
    now = time.time() if now is None else now
    removed = []
    for vid, info in kept_recordings(root):
        if now - info["kept_at"] < days * DAY_S:
            continue
        files = []
        for p in recording_files(root, vid, info):
            try:
                files.append((p, p.stat()))
            except FileNotFoundError:
                continue
        if any(now - st.st_atime < RECENT_S for _p, st in files):
            continue
        for p, st in files:
            p.unlink(missing_ok=True)
            removed.append((p, st.st_size))
        cache.kept_marker(vid, root).unlink(missing_ok=True)
    return removed


# cameras/trim.PENDING: a kept game whose cut has not finished.
_TRIM_PENDING = "trim_pending"


def kept_camera_games(cams_root) -> list[tuple[float, Path, int, dict]]:
    """``(kept at, directory, bytes, clock.json)`` of every camera game kept
    under ``cams_root/kept/``, oldest first. A game is dated by when it was
    kept (clock.json's ``kept_at``), not its directory, which its cut touches."""
    kept = Path(cams_root) / "kept"
    if not kept.is_dir():
        return []
    out = []
    for d in kept.iterdir():
        if not d.is_dir():
            continue
        try:
            files = list(d.iterdir())
            size = sum(f.stat().st_size for f in files)
            mtime = d.stat().st_mtime
        except FileNotFoundError:     # pruned by the other worker just now
            continue
        try:
            meta = json.loads((d / "clock.json").read_text())
        except (OSError, ValueError):
            meta = {}
        meta = meta if isinstance(meta, dict) else {}
        try:
            kept_at = float(meta.get("kept_at") or mtime)
        except (TypeError, ValueError):
            kept_at = mtime
        out.append((kept_at, d, size, meta))
    return sorted(out, key=lambda e: e[0])


def _clipped_names(clipped, names) -> set:
    """Which of ``names`` have had every delivery clipped; none when nobody
    could say (``clipped`` None, or it failed)."""
    if clipped is None or not names:
        return set()
    try:
        return set(clipped(list(names)))
    except Exception as exc:  # noqa: BLE001 - keep everything rather than guess
        log.warning("could not learn which camera games are clipped: %s", exc)
        return set()


def prune(root, keep_gb: float, now=None, *, min_free_gb: float = 0.0,
          free_bytes=None, recording_days: float | None = None,
          cams_root=None, camera_days: float | None = None,
          camera_max_days: float | None = None, clipped=None) -> list[Path]:
    """Delete live recordings past ``recording_days``; then, while the caches
    are over ``keep_gb`` or the disk has less than ``min_free_gb`` free, more
    recordings, oldest kept first; then, for the budget alone, other media,
    least recently read first. Return what went.

    Recordings are kept "space allowing", so they give way before anything
    else. The floor never takes a download: the 11 harness videos the parity
    runs and training waves read are downloads, and getting any of them back
    means asking YouTube again. Partially written files and anything read
    within the last hour are left alone -- a job in progress must never find
    its input gone. ``free_bytes`` is the disk's free space, measured when not
    given.

    The camera games kept under ``cams_root`` are their games' only video
    until every delivery is clipped (``clipped(names) -> set``, the API's
    word; None: none is). A clipped game goes after ``camera_days``, any game
    after ``camera_max_days`` (``camera_days`` if not given), and none while
    its cut is pending. Under the floor, clipped games go first, then the
    recordings, and unclipped games last -- their clips are lost with them.
    """
    now = time.time() if now is None else now
    budget, floor = keep_gb * 1e9, min_free_gb * 1e9
    measured = None
    if free_bytes is None:
        disk = cams_root if cams_root is not None and Path(cams_root).exists() else root
        measured = shutil.disk_usage(disk).free if Path(disk).exists() else 0
    free = free_bytes if free_bytes is not None else measured
    removed = []
    games = kept_camera_games(cams_root) if cams_root is not None else []
    done = _clipped_names(clipped, [d.name for _k, d, _s, _m in games])
    max_days = camera_max_days if camera_max_days is not None else camera_days

    def drop_game(d, size, why):
        nonlocal free
        shutil.rmtree(d, ignore_errors=True)
        removed.append(d)
        free += size
        log.info("pruned the kept cameras of %s (%s)", d.name, why)

    left = []
    for kept_at, d, size, meta in games:
        age = now - kept_at
        if not meta.get(_TRIM_PENDING) and (
                (d.name in done and camera_days is not None and age > camera_days * 86400)
                or (max_days is not None and age > max_days * 86400)):
            drop_game(d, size, "clipped" if d.name in done else "past its days, clips lost")
        else:
            left.append((d, size))
    for d, size in [g for g in left if g[0].name in done]:
        if free >= floor:
            break
        drop_game(d, size, "clipped, the disk is short")
    left = [g for g in left if g[0].exists()]
    expired = expire_recordings(root, recording_days, now) if recording_days else []
    removed += [p for p, _size in expired]
    free += sum(size for _p, size in expired)
    media = {}
    for p in media_files(root):
        if ".part" in p.name:
            continue
        try:
            st = p.stat()
        except FileNotFoundError:     # pruned by the other thread just now
            continue
        media[p] = (st.st_atime, st.st_size)
    total = sum(size for _a, size in media.values())

    def take(path, size):
        nonlocal total, free
        path.unlink(missing_ok=True)
        removed.append(path)
        total -= size
        free += size

    for vid, info in kept_recordings(root):
        for path in recording_files(root, vid, info):
            if total <= budget and free >= floor:
                break
            if path not in media:
                continue
            atime, size = media.pop(path)
            if now - atime >= RECENT_S:
                take(path, size)
    for d, size in left:
        if free >= floor:
            break
        drop_game(d, size, "the disk is short, clips lost")
    for path, (atime, size) in sorted(media.items(), key=lambda kv: kv[1][0]):
        if total <= budget:
            break
        if now - atime >= RECENT_S:
            take(path, size)
    return removed
