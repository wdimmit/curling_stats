# Keep a Rolling Week of Live Recordings: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep every live recording on the worker for about a week, whole recordings as the cached video and partial ones for investigation, so a game the nightly review flags can still be replayed.

**Architecture:**
- Branch `live-keep` (25d675d) already remuxes a whole recording into `videos/<id>.mp4` at stream end and prunes to a free-disk floor. This plan rebases it onto main.
- Every kept recording gets a marker, `kept/<id>.json`. Partial recordings are moved to `kept/<id>.ts`.
- The pruner deletes marked recordings older than `WORKER_RECORDING_DAYS`, and also lets `kept/` media go least-recently-read first under the existing budget and floor.

**Tech Stack:** Python 3, pytest, ffmpeg (remux only), docker compose on the worker box.

**Spec:** `docs/superpowers/specs/2026-10-05-nightly-review-design.md`, section 7 ("Recordings"). This plan is the recordings half of that spec. The review half is `docs/superpowers/plans/2026-10-05-nightly-review.md`; the two ship independently.

**Refinement of the spec (deliberate).** The spec puts a sidecar at `videos/<id>.kept` and partials in `kept/<id>/`. The plan puts every marker in `kept/<id>.json` and partials at `kept/<id>.ts`, because `prune.media_files` treats every file in `videos/` as media and would count, and delete, a sidecar there. The behaviour is the same.

## Global Constraints

- Work in a git worktree at `/home/tcuser/src/curling_score/.claude/worktrees/live-week` on branch `live-week`. **Never write under `/home/tcuser/src/curling_score` outside that worktree.** Another session shares the main checkout.
- Run tests with `./.venv/bin/pytest` from the main checkout's venv, pointed at the worktree: `cd <worktree> && /home/tcuser/src/curling_score/.venv/bin/pytest <files>`. `pyproject.toml` sets `pythonpath = ["src"]`, so the worktree's source is the one tested. Do not add `-q`; it is already set.
- The full suite is OOM-killed on this laptop (exit 137). Run only the files named in each task.
- Keep only whole recordings as `videos/<id>.mp4`. A partial recording must never be filed there, because a reprocess would take it for the whole game.
- Never prune a file read in the last hour (`prune.RECENT_S`): a job may be reading it.
- Unmarked media (downloads, the 11 harness videos) must never age out.
- `WORKER_RECORDING_DAYS` default: `7`. `WORKER_MIN_FREE_GB` default: `40`.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Match the surrounding code: docstrings that say why, comments sparing, names like the neighbours'.

## Review Focus

1. **A recording past 7 days that a reprocess is reading right now.** It is not aged out until it has gone an hour unread. Test in Task 3.
2. **A marker whose recording the budget pruner already took.** Ageing it out removes the marker and raises nothing. Test in Task 3.
3. **A stream whose recorder never started (no parts), let go.** `finish` deletes the directory and keeps nothing, without an `IndexError` from `recorder.path`. Test in Task 4.
4. **A video id starting with `-`** (several club videos do). Marker, partial and proxy paths still resolve, and pruning a recording never deletes another video's proxies. Test in Task 3.
5. **A partial recording of a video already cached** (the VOD was downloaded earlier). Nothing is kept and the marker is not written. Test in Task 2.

---

### Task 1: Rebase `live-keep` onto main in a worktree

**Files:**
- Modify (conflict resolution only): `deploy/docker-compose.worker.yml`, `deploy/README.md`, `src/curling_score/service/worker.py`

**Interfaces:**
- Produces: branch `live-week` = `live-keep`'s single commit replayed on main, with these unchanged from `live-keep`:
  - `cache.keep_recording(recording, vid, root=None, *, run=subprocess.run) -> Path | None`
  - `prune.prune(root, keep_gb, now=None, *, min_free_gb=0.0, free_bytes=None) -> list[Path]`
  - `LiveManager.finish(stream, keep=True)`
  - `worker.run_forever(..., cache_gb, min_free_gb=0.0, ...)`
  - `worker.build_live(..., cache_gb=None, min_free_gb=0.0)`

- [ ] **Step 1: Create the worktree on a new branch from `live-keep`**

```bash
cd /home/tcuser/src/curling_score
git worktree add -b live-week .claude/worktrees/live-week live-keep
cd .claude/worktrees/live-week
git rebase main
```

Expected: conflicts in `deploy/docker-compose.worker.yml` and maybe `deploy/README.md` and `src/curling_score/service/worker.py`.

- [ ] **Step 2: Resolve the conflicts**

- `deploy/docker-compose.worker.yml`: keep main's file whole, including the `worker2` service, `LIVE_MAX_STREAMS: "3"` and main's volume comment. Add only the floor to `worker`'s `environment`, after `WORKER_CACHE_GB: "300"`:

```yaml
      # Media also goes until the disk has this much free: a live night records
      # five streams at once, ~4 GB each, before any is kept in the cache.
      WORKER_MIN_FREE_GB: "40"
```

  `worker2` inherits it through `extends`. Both caches are on the same disk, and each prunes to the same floor.

- `src/curling_score/service/worker.py`: in the `resolve_weights` docstring keep main's wording (`weights.DEFAULT_NAME`). Take `live-keep`'s `run_forever`, `build_live` and `main` changes.
- `deploy/README.md`: take main's text plus `live-keep`'s replacement paragraph about `WORKER_CACHE_GB` / `WORKER_MIN_FREE_GB`.

```bash
git add deploy/docker-compose.worker.yml deploy/README.md src/curling_score/service/worker.py
GIT_EDITOR=true git rebase --continue
```

- [ ] **Step 3: Run the branch's tests**

Run: `cd /home/tcuser/src/curling_score/.claude/worktrees/live-week && /home/tcuser/src/curling_score/.venv/bin/pytest tests/test_cache.py tests/test_ingest_service.py tests/test_live_lane.py tests/test_live_recorder.py tests/test_worker.py`

Expected: all pass.

- [ ] **Step 4: Confirm the compose file still parses**

Run: `cd /home/tcuser/src/curling_score/.claude/worktrees/live-week/deploy && touch worker.env && docker compose -f docker-compose.worker.yml config >/dev/null && echo ok; rm -f worker.env`

Expected: `ok`. If `docker` is unavailable here, skip this step and say so in the task report. `worker.env` is gitignored, so the empty stand-in must not be committed.

---

### Task 2: Mark kept recordings, and keep partial ones

**Files:**
- Modify: `src/curling_score/ingest/cache.py` (after `keep_recording`)
- Test: `tests/test_cache.py`

**Interfaces:**
- Consumes: `cache.keep_recording`, `cache.is_cached`, `cache.video_path`, `cache.default_root` (Task 1).
- Produces:
  - `cache.KEPT_DIR = "kept"`
  - `cache.kept_marker(vid: str, root: Path | None = None) -> Path`, which is `root / "kept" / f"{vid}.json"`
  - `cache.partial_path(vid: str, root: Path | None = None) -> Path`, which is `root / "kept" / f"{vid}.ts"`
  - `cache.mark_kept(vid: str, root: Path | None = None, *, whole: bool, now: float | None = None) -> Path`, writing `{"kept_at": <epoch s>, "whole": <bool>}`
  - `cache.keep_recording(...)`: now also calls `mark_kept(vid, root, whole=True)` when it files a video
  - `cache.keep_partial(recording, vid: str, root: Path | None = None, *, now: float | None = None) -> Path | None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cache.py`. Match its existing imports. It already has `from pathlib import Path` and `from curling_score.ingest import cache`; add `import json` at the top.

```python
class TestMarkingKeptRecordings:
    """A kept live recording carries a marker, so the pruner can age it out
    after a week; a downloaded video carries none and never ages out."""

    def _remux(self, cmd, check):
        Path(cmd[-1]).write_bytes(b"mp4")

    def test_a_whole_recording_is_marked_as_one(self, tmp_path):
        rec = tmp_path / "live" / "vid1" / "rec.0.ts"
        rec.parent.mkdir(parents=True)
        rec.write_bytes(b"ts")
        cache.keep_recording(rec, "vid1", tmp_path, run=self._remux)
        marker = json.loads(cache.kept_marker("vid1", tmp_path).read_text())
        assert marker["whole"] is True and marker["kept_at"] > 0

    def test_a_recording_of_a_cached_video_writes_no_marker(self, tmp_path):
        cache.video_path("vid1", tmp_path).parent.mkdir(parents=True)
        cache.video_path("vid1", tmp_path).write_bytes(b"download")
        rec = tmp_path / "rec.0.ts"
        rec.write_bytes(b"ts")
        assert cache.keep_recording(rec, "vid1", tmp_path, run=self._remux) is None
        assert not cache.kept_marker("vid1", tmp_path).exists()


class TestKeepingAPartialRecording:
    """A recording cut short is kept for investigation, never as the video."""

    def _rec(self, tmp_path, vid="vid1"):
        rec = tmp_path / "live" / vid / "rec.0.ts"
        rec.parent.mkdir(parents=True)
        rec.write_bytes(b"partial ts")
        return rec

    def test_it_moves_to_kept_and_is_marked_partial(self, tmp_path):
        rec = self._rec(tmp_path)
        got = cache.keep_partial(rec, "vid1", tmp_path, now=1234.0)
        assert got == cache.partial_path("vid1", tmp_path) == tmp_path / "kept" / "vid1.ts"
        assert got.read_bytes() == b"partial ts" and not rec.exists()
        assert json.loads(cache.kept_marker("vid1", tmp_path).read_text()) == {
            "kept_at": 1234.0, "whole": False}

    def test_it_never_becomes_the_cached_video(self, tmp_path):
        cache.keep_partial(self._rec(tmp_path), "vid1", tmp_path)
        assert not cache.is_cached("vid1", tmp_path)

    def test_a_video_already_cached_keeps_nothing(self, tmp_path):
        cache.video_path("vid1", tmp_path).parent.mkdir(parents=True)
        cache.video_path("vid1", tmp_path).write_bytes(b"download")
        rec = self._rec(tmp_path)
        assert cache.keep_partial(rec, "vid1", tmp_path) is None
        assert not cache.partial_path("vid1", tmp_path).exists()
        assert not cache.kept_marker("vid1", tmp_path).exists()

    def test_an_empty_recording_keeps_nothing(self, tmp_path):
        rec = self._rec(tmp_path)
        rec.write_bytes(b"")
        assert cache.keep_partial(rec, "vid1", tmp_path) is None
        assert not cache.kept_marker("vid1", tmp_path).exists()

    def test_an_id_that_starts_with_a_dash(self, tmp_path):
        got = cache.keep_partial(self._rec(tmp_path, "-f3RabcdEF"), "-f3RabcdEF", tmp_path)
        assert got == tmp_path / "kept" / "-f3RabcdEF.ts" and got.is_file()
        assert cache.kept_marker("-f3RabcdEF", tmp_path).is_file()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/tcuser/src/curling_score/.claude/worktrees/live-week && /home/tcuser/src/curling_score/.venv/bin/pytest tests/test_cache.py`

Expected: FAIL with `AttributeError: module 'curling_score.ingest.cache' has no attribute 'kept_marker'` (and `keep_partial`).

- [ ] **Step 3: Implement**

In `src/curling_score/ingest/cache.py`, add `import json` beside the other imports. Add this block after `keep_recording`:

```python
# Live recordings kept past their stream: a marker for each, and the partial
# ones themselves. Kept for a rolling week (WORKER_RECORDING_DAYS) so a game
# flagged overnight can still be replayed; see ingest/prune.py.
KEPT_DIR = "kept"


def kept_marker(vid: str, root: Path | None = None) -> Path:
    root = Path(root) if root is not None else default_root()
    return root / KEPT_DIR / f"{vid}.json"


def partial_path(vid: str, root: Path | None = None) -> Path:
    root = Path(root) if root is not None else default_root()
    return root / KEPT_DIR / f"{vid}.ts"


def mark_kept(vid: str, root: Path | None = None, *, whole: bool,
              now: float | None = None) -> Path:
    """Note that ``vid``'s media is a live recording, and when it was kept.

    A marker rather than a timestamp on the media: the video's mtime is pinned
    for the detection cache and its atime moves whenever it is read, so neither
    can say how old the recording is."""
    marker = kept_marker(vid, root)
    marker.parent.mkdir(parents=True, exist_ok=True)
    tmp = marker.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"kept_at": time.time() if now is None else now,
                               "whole": whole}))
    tmp.replace(marker)
    return marker


def keep_partial(recording, vid: str, root: Path | None = None, *,
                 now: float | None = None) -> Path | None:
    """Keep a live recording that is not the whole stream, for investigation.

    A recording cut short by the cap, a stall, a stop or a failed exit is not
    the game, so it never becomes ``videos/<id>.mp4`` -- a reprocess would take
    it for the whole one. It is moved, as it is, to ``kept/<id>.ts``, where
    replay tools can be pointed at it. None when the video is cached already
    or there is nothing recorded."""
    recording = Path(recording)
    if is_cached(vid, root):
        return None
    if not (recording.is_file() and recording.stat().st_size > 0):
        return None
    dest = partial_path(vid, root)
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.replace(recording, dest)
    os.utime(dest)              # read just now, as far as the pruner can tell
    mark_kept(vid, root, whole=False, now=now)
    return dest
```

In `keep_recording`, mark the video once it is in place. Add the call after the `os.utime(dest, ...)` line and before `return dest`:

```python
    mark_kept(vid, root, whole=True)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /home/tcuser/src/curling_score/.claude/worktrees/live-week && /home/tcuser/src/curling_score/.venv/bin/pytest tests/test_cache.py`

Expected: PASS, the earlier `keep_recording` tests included.

- [ ] **Step 5: Commit**

```bash
cd /home/tcuser/src/curling_score/.claude/worktrees/live-week
git add src/curling_score/ingest/cache.py tests/test_cache.py
git commit -m "live: mark kept recordings, and keep a partial one for investigation

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Age kept recordings out after a week

**Files:**
- Modify: `src/curling_score/ingest/prune.py`
- Test: `tests/test_ingest_service.py` (class `TestPrune` and a new class after it)

**Interfaces:**
- Consumes: `cache.KEPT_DIR`, `cache.kept_marker`, `cache.partial_path`, `cache.video_path` (Task 2).
- Produces:
  - `prune.MEDIA_DIRS = ("videos", "proxies", "kept")`
  - `prune.media_files(root)`, which now skips `*.json` and `*.tmp`
  - `prune.recording_files(root, vid: str) -> list[Path]`
  - `prune.expire_recordings(root, days: float, now: float | None = None) -> list[tuple[Path, int]]`
  - `prune.prune(root, keep_gb, now=None, *, min_free_gb=0.0, free_bytes=None, recording_days: float | None = None) -> list[Path]`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_ingest_service.py`. It already imports `os`, `cache` and `prune`; add `import json` if absent.

```python
class TestRecordingsAgeOut:
    """Live recordings stay a rolling week, then go -- whatever the budget;
    downloads never age out."""

    DAY = 86400.0

    def _media(self, root, rel, size, read_ago, now):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * size)
        os.utime(p, (now - read_ago, now - read_ago))
        return p

    def _mark(self, root, vid, kept_ago, now, whole=True):
        return cache.mark_kept(vid, root, whole=whole, now=now - kept_ago)

    def test_a_recording_older_than_the_window_goes_with_its_proxy(self, tmp_path):
        now = 10 * self.DAY
        video = self._media(tmp_path, "videos/old.mp4", 100, 2 * self.DAY, now)
        proxy = self._media(tmp_path, "proxies/old.ab12.mp4", 10, 2 * self.DAY, now)
        marker = self._mark(tmp_path, "old", 8 * self.DAY, now)
        removed = prune.prune(tmp_path, keep_gb=1.0, now=now, recording_days=7)
        assert set(removed) == {video, proxy}
        assert not video.exists() and not proxy.exists() and not marker.exists()

    def test_a_recording_inside_the_window_stays(self, tmp_path):
        now = 10 * self.DAY
        video = self._media(tmp_path, "videos/new.mp4", 100, 2 * self.DAY, now)
        self._mark(tmp_path, "new", 6 * self.DAY, now)
        assert prune.prune(tmp_path, keep_gb=1.0, now=now, recording_days=7) == []
        assert video.exists()

    def test_a_download_never_ages_out(self, tmp_path):
        now = 400 * self.DAY
        video = self._media(tmp_path, "videos/harness.mp4", 100, 300 * self.DAY, now)
        assert prune.prune(tmp_path, keep_gb=1.0, now=now, recording_days=7) == []
        assert video.exists()

    def test_a_partial_recording_ages_out_too(self, tmp_path):
        now = 10 * self.DAY
        part = self._media(tmp_path, "kept/cut.ts", 100, 2 * self.DAY, now)
        self._mark(tmp_path, "cut", 8 * self.DAY, now, whole=False)
        assert prune.prune(tmp_path, keep_gb=1.0, now=now, recording_days=7) == [part]

    def test_an_old_recording_being_read_is_left_until_it_is_not(self, tmp_path):
        now = 10 * self.DAY
        video = self._media(tmp_path, "videos/busy.mp4", 100, 60, now)
        marker = self._mark(tmp_path, "busy", 8 * self.DAY, now)
        assert prune.prune(tmp_path, keep_gb=1.0, now=now, recording_days=7) == []
        assert video.exists() and marker.exists()

    def test_a_marker_whose_recording_is_already_gone_is_cleared(self, tmp_path):
        now = 10 * self.DAY
        marker = self._mark(tmp_path, "gone", 8 * self.DAY, now)
        assert prune.prune(tmp_path, keep_gb=1.0, now=now, recording_days=7) == []
        assert not marker.exists()

    def test_an_unreadable_marker_is_left_alone(self, tmp_path):
        now = 10 * self.DAY
        video = self._media(tmp_path, "videos/odd.mp4", 100, 2 * self.DAY, now)
        bad = tmp_path / "kept" / "odd.json"
        bad.write_text("{not json")
        assert prune.prune(tmp_path, keep_gb=1.0, now=now, recording_days=7) == []
        assert video.exists() and bad.exists()

    def test_proxies_of_a_dashed_id_take_no_other_video_s(self, tmp_path):
        now = 10 * self.DAY
        self._media(tmp_path, "videos/-f3R.mp4", 100, 2 * self.DAY, now)
        mine = self._media(tmp_path, "proxies/-f3R.ab12.mp4", 10, 2 * self.DAY, now)
        other = self._media(tmp_path, "proxies/-f3Rx.ab12.mp4", 10, 2 * self.DAY, now)
        self._mark(tmp_path, "-f3R", 8 * self.DAY, now)
        removed = prune.prune(tmp_path, keep_gb=1.0, now=now, recording_days=7)
        assert mine in removed and other.exists()

    def test_markers_are_not_media(self, tmp_path):
        now = 10 * self.DAY
        self._mark(tmp_path, "x", 1 * self.DAY, now)
        assert prune.media_files(tmp_path) == []

    def test_kept_partials_count_against_the_budget(self, tmp_path):
        now = 10 * self.DAY
        part = self._media(tmp_path, "kept/cut.ts", 600, 3 * self.DAY, now)
        self._mark(tmp_path, "cut", 1 * self.DAY, now, whole=False)
        assert prune.prune(tmp_path, keep_gb=100e-9, now=now, recording_days=7) == [part]

    def test_freed_space_counts_toward_the_floor(self, tmp_path):
        # The expired recording frees 600 bytes; the floor of 500 is then met,
        # so the other (younger) video stays.
        now = 10 * self.DAY
        self._media(tmp_path, "videos/old.mp4", 600, 2 * self.DAY, now)
        self._mark(tmp_path, "old", 8 * self.DAY, now)
        keep = self._media(tmp_path, "videos/other.mp4", 600, 3 * self.DAY, now)
        prune.prune(tmp_path, keep_gb=1.0, now=now, min_free_gb=500e-9, free_bytes=0,
                    recording_days=7)
        assert keep.exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/tcuser/src/curling_score/.claude/worktrees/live-week && /home/tcuser/src/curling_score/.venv/bin/pytest tests/test_ingest_service.py`

Expected: FAIL with `TypeError: prune() got an unexpected keyword argument 'recording_days'`.

- [ ] **Step 3: Implement**

Replace the top of `src/curling_score/ingest/prune.py` from the module docstring through `media_files`, then rewrite `prune` as below. Keep the `prune` body's existing LRU loop.

```python
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
```

Check the import direction before relying on it: `cache` must not import `prune`. It doesn't today; `grep -n "import" src/curling_score/ingest/cache.py` confirms.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /home/tcuser/src/curling_score/.claude/worktrees/live-week && /home/tcuser/src/curling_score/.venv/bin/pytest tests/test_ingest_service.py tests/test_cache.py`

Expected: PASS, the existing `TestPrune` cases included.

- [ ] **Step 5: Commit**

```bash
cd /home/tcuser/src/curling_score/.claude/worktrees/live-week
git add src/curling_score/ingest/prune.py tests/test_ingest_service.py
git commit -m "prune: live recordings go after a rolling week; downloads never age out

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Keep partial recordings when a stream is let go

**Files:**
- Modify: `src/curling_score/live/manager.py` (`LiveManager.finish`)
- Test: `tests/test_live_lane.py` (class `TestKeepingTheRecording`)

**Interfaces:**
- Consumes: `cache.keep_recording(path, vid, root)`, `cache.keep_partial(path, vid, root)` (Task 2).
- Produces: `LiveManager.finish(stream, keep=True)` behaves as follows.
  - A whole recording goes to `keep_recording`.
  - A non-whole recording goes to `keep_partial`.
  - `keep=False`, or a recorder with no file, keeps nothing.
  - In every case the `live/<vid>` directory is removed, and the cache is pruned after any keep attempt.

- [ ] **Step 1: Change and add the tests**

In `tests/test_live_lane.py`, `TestKeepingTheRecording._finish` monkeypatches `keep_recording` only. Make it record partial keeps too, and let a test take the recorder's path away.

```python
    def _finish(self, tmp_path, monkeypatch, whole=True, keep=True, fails=False,
                no_file=False):
        kept, partial, pruned = [], [], []

        def keep_recording(path, vid, root):
            if fails:
                raise RuntimeError("ffmpeg fell over")
            kept.append((path, vid, root))
            return root / "videos" / f"{vid}.mp4"

        def keep_partial(path, vid, root):
            partial.append((path, vid, root))
            return root / "kept" / f"{vid}.ts"

        monkeypatch.setattr(manager_mod.cache, "keep_recording", keep_recording)
        monkeypatch.setattr(manager_mod.cache, "keep_partial", keep_partial)
        m = manager_mod.LiveManager(
            Api([live_job(1)]), "home", model_id="m-abc", gpu=None, root=tmp_path,
            make_recorder=lambda job: Recorder(job), prune=lambda: pruned.append(1),
            background=lambda fn: fn())
        m.poll_once()
        (s,) = m.streams()
        directory = tmp_path / "live" / "liveVid0001"
        directory.mkdir(parents=True)
        (directory / "rec.0.ts").write_bytes(b"ts")
        s.recorder.whole = lambda: whole
        # This file's Recorder double has no `path` until a test gives it one,
        # so leaving it unset stands for a recorder that never wrote a file.
        # (The real recorder raises IndexError there; finish catches both.)
        if not no_file:
            s.recorder.path = directory / "rec.0.ts"
        m.finish(s, keep=keep)
        assert s.recorder.stopped and m.streams() == []
        return s, kept, partial, pruned, directory
```

Update the four existing tests to unpack five values, and change the "not whole" expectation.

```python
    def test_a_whole_recording_is_kept_then_the_cache_pruned(self, tmp_path, monkeypatch):
        _, kept, partial, pruned, directory = self._finish(tmp_path, monkeypatch)
        assert kept == [(directory / "rec.0.ts", "liveVid0001", tmp_path)]
        assert partial == [] and pruned == [1] and not directory.exists()

    def test_a_recording_that_is_not_whole_is_kept_as_a_partial(self, tmp_path, monkeypatch):
        _, kept, partial, pruned, directory = self._finish(tmp_path, monkeypatch, whole=False)
        assert kept == [] and partial == [(directory / "rec.0.ts", "liveVid0001", tmp_path)]
        assert pruned == [1] and not directory.exists()

    def test_a_recording_turned_down_is_deleted(self, tmp_path, monkeypatch):
        _, kept, partial, _, directory = self._finish(tmp_path, monkeypatch, keep=False)
        assert kept == [] and partial == [] and not directory.exists()

    def test_a_recording_that_cannot_be_kept_is_still_deleted(self, tmp_path, monkeypatch):
        _, _, _, pruned, directory = self._finish(tmp_path, monkeypatch, fails=True)
        assert not directory.exists() and pruned == [1]

    def test_a_recorder_that_never_wrote_a_file_keeps_nothing(self, tmp_path, monkeypatch):
        _, kept, partial, pruned, directory = self._finish(tmp_path, monkeypatch,
                                                           no_file=True)
        assert kept == [] and partial == [] and pruned == [] and not directory.exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/tcuser/src/curling_score/.claude/worktrees/live-week && /home/tcuser/src/curling_score/.venv/bin/pytest tests/test_live_lane.py`

Expected: FAIL. `test_a_recording_that_is_not_whole_is_kept_as_a_partial` fails with `partial == []`, and `test_a_recorder_that_never_wrote_a_file_keeps_nothing` raises `AttributeError: 'Recorder' object has no attribute 'path'`.

- [ ] **Step 3: Implement**

In `src/curling_score/live/manager.py`, replace the part of `finish` from `vid = stream.job["video_id"]` to the end of the method:

```python
        vid = stream.job["video_id"]
        directory = self.root / "live" / vid
        whole = getattr(stream.recorder, "whole", lambda: False)()
        try:
            path = stream.recorder.path
        except (AttributeError, IndexError):     # never started: nothing written
            path = None
        if not keep or path is None:
            shutil.rmtree(directory, ignore_errors=True)
            return
        # The whole stream becomes the video's cached copy; anything less is
        # kept beside it for a week, for looking into what went wrong, and is
        # never taken for the game itself.
        file_it = cache.keep_recording if whole else cache.keep_partial

        def keep_it():
            try:
                kept = file_it(path, vid, self.root)
                if kept is not None:
                    log.info("kept the %s recording of %s as %s",
                             "whole" if whole else "partial", vid, kept)
            except Exception:  # noqa: BLE001 - only a replay is lost
                log.exception("could not keep the recording of %s", vid)
            finally:
                shutil.rmtree(directory, ignore_errors=True)
            if self.prune is not None:
                try:
                    self.prune()
                except Exception as exc:  # noqa: BLE001
                    log.warning("prune failed: %s", exc)

        self.background(keep_it)
```

Also update the method's docstring, whose last sentences describe the old rule:

```python
    def finish(self, stream, keep: bool = True):
        """Let a stream go: stop recording it, and delete the recording --
        after filing it, unless ``keep`` is turned down: a whole recording as
        the video's cached copy, so reprocessing the game needs no download; a
        partial one under ``kept/``, for investigation. The pruner lets either
        go after a week (WORKER_RECORDING_DAYS), or sooner if the disk needs it."""
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /home/tcuser/src/curling_score/.claude/worktrees/live-week && /home/tcuser/src/curling_score/.venv/bin/pytest tests/test_live_lane.py tests/test_live_recorder.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /home/tcuser/src/curling_score/.claude/worktrees/live-week
git add src/curling_score/live/manager.py tests/test_live_lane.py
git commit -m "live: keep a recording cut short too, for investigation, never as the video

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Wire `WORKER_RECORDING_DAYS` through the worker

**Files:**
- Modify: `src/curling_score/service/worker.py` (`run_forever`, `build_live`, `main`)
- Modify: `deploy/docker-compose.worker.yml`, `deploy/README.md`
- Test: `tests/test_worker.py`

**Interfaces:**
- Consumes: `prune.prune(..., recording_days=)` (Task 3).
- Produces:
  - `worker.run_forever(..., min_free_gb=0.0, recording_days: float | None = None, ...)`
  - `worker.build_live(..., cache_gb=None, min_free_gb=0.0, recording_days: float | None = None)`
  - env `WORKER_RECORDING_DAYS`, default `"7"`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_worker.py` in `TestLoop`:

```python
    def test_the_prune_after_a_job_ages_out_recordings(self, tmp_path, monkeypatch):
        monkeypatch.setattr(worker, "process_job",
                            lambda job, api, wid, **kw: api.complete(job["id"], wid, {}))
        calls = []
        monkeypatch.setattr(worker.prune, "prune",
                            lambda root, gb, **kw: calls.append(kw) or [])
        worker.run_forever(FakeApi([JOB]), "home", root=tmp_path, weights=None,
                           out_dir=tmp_path, cache_gb=1.0, min_free_gb=40.0,
                           recording_days=7.0, sleep=lambda s: None, once=True)
        assert calls == [{"min_free_gb": 40.0, "recording_days": 7.0}]
```

Add to `TestBuildingTheLiveLane`:

```python
    def test_the_live_lane_prunes_with_the_recording_window(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WORKER_LIVE", "1")
        calls = []
        monkeypatch.setattr(worker.prune, "prune",
                            lambda root, gb, **kw: calls.append((gb, kw)) or [])
        got = worker.build_live("http://api", "t", "home", root=tmp_path, weights=None,
                                start=False, cache_gb=300.0, min_free_gb=40.0,
                                recording_days=7.0)
        got.manager.prune()
        assert calls == [(300.0, {"min_free_gb": 40.0, "recording_days": 7.0})]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/tcuser/src/curling_score/.claude/worktrees/live-week && /home/tcuser/src/curling_score/.venv/bin/pytest tests/test_worker.py`

Expected: FAIL with `TypeError: run_forever() got an unexpected keyword argument 'recording_days'`.

- [ ] **Step 3: Implement**

In `src/curling_score/service/worker.py`:

1. `run_forever` signature: add `recording_days: float | None = None` after `min_free_gb: float = 0.0`. In its prune call:

```python
            removed = prune.prune(root, cache_gb, min_free_gb=min_free_gb,
                                  recording_days=recording_days)
```

2. `build_live` signature: add `recording_days: float | None = None` after `min_free_gb: float = 0.0`. In `prune_cache`:

```python
    def prune_cache():
        removed = prune.prune(root, cache_gb, min_free_gb=min_free_gb,
                              recording_days=recording_days)
```

   Extend its docstring sentence "A whole recording is kept in the video cache, which is then pruned to ``cache_gb`` as after any job." to: "A recording is kept once its stream ends -- whole as the cached video, partial under ``kept/`` -- and the cache is then pruned as after any job, recordings older than ``recording_days`` first."

3. `main`: read the env once and pass it to both:

```python
    recording_days = float(os.environ.get("WORKER_RECORDING_DAYS", "7"))
```

   Add `recording_days=recording_days` to the `run_forever(...)` call and to the `build_live(...)` call.

In `deploy/docker-compose.worker.yml`, under `worker.environment` after `WORKER_MIN_FREE_GB`:

```yaml
      # Live recordings are kept this many days -- a rolling week of league
      # nights, for replaying what the nightly review flags -- then go.
      WORKER_RECORDING_DAYS: "7"
```

In `deploy/README.md`, extend the paragraph about `WORKER_CACHE_GB` / `WORKER_MIN_FREE_GB` (added in Task 1) with:

```markdown
Live recordings are kept for `WORKER_RECORDING_DAYS` (default 7) once their stream
ends: a whole one as `videos/<id>.mp4`, one cut short (cap, stall, failed exit) as
`kept/<id>.ts`, never as the video. Each has a marker, `kept/<id>.json`. A whole
recording replays like a download; point `scripts/replay_end.py` or `run_analyze`
at a partial one by path. `worker2`'s 40 GB budget holds only a few nights; raise
it on a box with the room.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /home/tcuser/src/curling_score/.claude/worktrees/live-week && /home/tcuser/src/curling_score/.venv/bin/pytest tests/test_worker.py tests/test_live_lane.py tests/test_ingest_service.py tests/test_cache.py tests/test_live_recorder.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /home/tcuser/src/curling_score/.claude/worktrees/live-week
git add src/curling_score/service/worker.py deploy/docker-compose.worker.yml deploy/README.md tests/test_worker.py
git commit -m "worker: keep live recordings for WORKER_RECORDING_DAYS (7)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Merge and deploy the worker (needs the user's go-ahead)

Operational. **Do not start it without the user's explicit OK to merge and deploy.** No new code.

- [ ] **Step 1: Merge**

From the main checkout, after `git status` shows no stray edits from this branch (see the memory note on subagents writing into the main checkout):

```bash
cd /home/tcuser/src/curling_score
git merge --ff-only live-week || git merge live-week -m "Merge live-week: keep a rolling week of live recordings

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 2: Deploy the worker from a clean worktree**

Follow memory `deploying-curling-chart`:
1. `git worktree add --detach <your scratchpad directory>/deploy-live-week HEAD`.
2. rsync to `administrator@10.0.0.182:/data/wdd/curling_score` (or `100.80.66.81` off the home LAN) with `rsync -a --exclude 'deploy/worker.env' --exclude .git --exclude .venv --exclude node_modules --exclude 'out*'`, never `--delete`.
3. Build first: `docker compose -f docker-compose.worker.yml build`.
4. Swap with `up -d` only when no live league is on and no job is claimed: check both workers' logs for an unfinished `job j_...` and `/api/admin/runs` for `processing`/`live`. `up -d` recreates both services and drops their live jobs.

- [ ] **Step 3: Confirm in the running containers**

```bash
ssh administrator@10.0.0.182 'cd /data/wdd/curling_score/deploy && docker compose -f docker-compose.worker.yml exec -T worker python -c "import inspect; from curling_score.ingest import prune; print(\"recording_days\" in inspect.signature(prune.prune).parameters)" && docker compose -f docker-compose.worker.yml exec -T worker printenv WORKER_RECORDING_DAYS WORKER_MIN_FREE_GB'
```

Expected: `True`, `7`, `40`. Do the same for `worker2`.

- [ ] **Step 4: After the first live night, check what was kept**

```bash
ssh administrator@10.0.0.182 'cd /data/wdd/curling_score/deploy && docker compose -f docker-compose.worker.yml logs worker worker2 2>&1 | grep -E "ended with the stream|kept the (whole|partial) recording|could not keep"; ls -la /data/wdd/curling-cache/kept /data/wdd/curling-cache-2/kept'
```

Expected: one "ended with the stream (exit N)" line per sheet and one "kept the … recording" line per played sheet, with markers in `kept/`. If every exit is nonzero, recordings are being kept as partials only. Report that to the user: `recorder._check`'s `code == 0` test would then need relaxing so whole recordings become the cached video. Update memory `live-recording-cache` with the result.
