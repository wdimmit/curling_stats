"""Where the service's records live, and the few operations that must be atomic.

Everything the API does is a handful of reads and writes on small documents.
Three of them have to be indivisible -- handing a job to exactly one worker,
saving overrides only if nobody else saved first, and counting a submission
against a rate limit -- and those are the whole reason this is an interface
rather than a dict: the in-memory version makes them atomic with a lock, the
Firestore version with a transaction, and the API cannot tell which it has.
"""

import threading
from datetime import datetime, timedelta, timezone
from typing import Protocol

from curling_score.service.records import (
    Chart, Job, Run, Source, WatchedPlaylist, Worker,
)

LEASE_S = 600.0
HEARTBEAT_STALE_S = 90.0


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Repo(Protocol):
    # runs
    def put_run(self, run: Run) -> None: ...
    def get_run(self, run_id: str) -> Run | None: ...
    def update_run(self, run_id: str, **fields) -> Run | None: ...
    def runs_for_video(self, video_id: str) -> list[Run]: ...
    def list_runs(self, status: str | None = None, limit: int = 100) -> list[Run]: ...
    # jobs
    def put_job(self, job: Job) -> None: ...
    def get_job(self, job_id: str) -> Job | None: ...
    def update_job(self, job_id: str, **fields) -> Job | None: ...
    def job_for_run(self, run_id: str) -> Job | None: ...
    def claim_job(self, worker_id: str, now: datetime, lease_s: float = LEASE_S) -> Job | None: ...
    def requeue_expired(self, now: datetime) -> int: ...
    def queue_position(self, job_id: str) -> int | None: ...
    def count_queued(self) -> int: ...
    # sources
    def put_source(self, source: Source) -> None: ...
    def get_source(self, source_id: str) -> Source | None: ...
    def update_source(self, source_id: str, **fields) -> Source | None: ...
    def sources_for_video(self, video_id: str) -> list[Source]: ...
    def list_sources(self, league: str | None = None, limit: int = 500) -> list[Source]: ...
    # charts
    def put_chart(self, chart: Chart) -> None: ...
    def get_chart(self, slug: str) -> Chart | None: ...
    def chart_by_share(self, share_slug: str) -> Chart | None: ...
    def update_chart(self, slug: str, **fields) -> Chart | None: ...
    def charts_for_run(self, run_id: str) -> list[Chart]: ...
    def save_overrides(self, slug: str, overrides: dict, expected_version: int | None,
                       now: datetime) -> tuple[bool, int, dict]: ...
    # workers
    def heartbeat(self, worker: Worker) -> None: ...
    def workers(self) -> list[Worker]: ...
    # playlists
    def put_playlist(self, playlist: WatchedPlaylist) -> None: ...
    def get_playlist(self, playlist_id: str) -> WatchedPlaylist | None: ...
    def list_playlists(self) -> list[WatchedPlaylist]: ...
    def update_playlist(self, playlist_id: str, **fields) -> WatchedPlaylist | None: ...
    def delete_playlist(self, playlist_id: str) -> None: ...
    # rate limit
    def bump_rate_limit(self, ip_hash: str, now: datetime,
                        hour_limit: int, day_limit: int) -> bool: ...
    # backup
    def export_all(self) -> dict: ...
    def import_all(self, data: dict) -> None: ...


def worker_online(workers: list[Worker], now: datetime,
                  stale_s: float = HEARTBEAT_STALE_S) -> bool:
    return any((now - w.last_seen_at).total_seconds() < stale_s for w in workers)


def _hour_bucket(now: datetime) -> str:
    return now.strftime("%Y-%m-%dT%H")


def _day_bucket(now: datetime) -> str:
    return now.strftime("%Y-%m-%d")


class MemoryRepo:
    """The whole store in dictionaries, under one lock. For tests and local runs."""

    def __init__(self):
        self._lock = threading.RLock()
        self.runs: dict[str, Run] = {}
        self.jobs: dict[str, Job] = {}
        self.sources: dict[str, Source] = {}
        self.charts: dict[str, Chart] = {}
        self.share_index: dict[str, str] = {}
        self.workers_: dict[str, Worker] = {}
        self.playlists: dict[str, WatchedPlaylist] = {}
        self.rate: dict[str, dict] = {}

    # ---- runs ---------------------------------------------------------
    def put_run(self, run):
        with self._lock:
            self.runs[run.id] = run

    def get_run(self, run_id):
        return self.runs.get(run_id)

    def update_run(self, run_id, **fields):
        with self._lock:
            run = self.runs.get(run_id)
            if run is None:
                return None
            for k, v in fields.items():
                setattr(run, k, v)
            return run

    def runs_for_video(self, video_id):
        return sorted((r for r in self.runs.values() if r.video_id == video_id),
                      key=lambda r: r.created_at, reverse=True)

    def list_runs(self, status=None, limit=100):
        runs = [r for r in self.runs.values() if status is None or r.status == status]
        return sorted(runs, key=lambda r: r.created_at, reverse=True)[:limit]

    # ---- jobs ---------------------------------------------------------
    def put_job(self, job):
        with self._lock:
            self.jobs[job.id] = job

    def get_job(self, job_id):
        return self.jobs.get(job_id)

    def update_job(self, job_id, **fields):
        with self._lock:
            job = self.jobs.get(job_id)
            if job is None:
                return None
            for k, v in fields.items():
                setattr(job, k, v)
            return job

    def job_for_run(self, run_id):
        jobs = [j for j in self.jobs.values() if j.run_id == run_id]
        return max(jobs, key=lambda j: j.created_at) if jobs else None

    def _queued(self, now=None):
        return sorted(
            (j for j in self.jobs.values()
             if j.state == "queued" and (now is None or j.run_after <= now)),
            key=lambda j: j.created_at,
        )

    def claim_job(self, worker_id, now, lease_s=LEASE_S):
        with self._lock:
            ready = self._queued(now)
            if not ready:
                return None
            job = ready[0]
            job.state = "running"
            job.worker_id = worker_id
            job.attempts += 1
            job.started_at = now
            job.lease_expires_at = now + timedelta(seconds=lease_s)
            job.progress_at = now
            run = self.runs.get(job.run_id)
            if run is not None:
                run.status = "processing"
            return job

    def requeue_expired(self, now):
        with self._lock:
            n = 0
            for job in self.jobs.values():
                if (job.state == "running" and job.lease_expires_at is not None
                        and job.lease_expires_at < now):
                    job.state = "queued"
                    job.worker_id = None
                    job.lease_expires_at = None
                    job.run_after = now
                    job.error_kind = "lease_expired"
                    run = self.runs.get(job.run_id)
                    if run is not None:
                        run.status = "queued"
                    n += 1
            return n

    def queue_position(self, job_id):
        ids = [j.id for j in self._queued()]
        return ids.index(job_id) if job_id in ids else None

    def count_queued(self):
        return len(self._queued())

    # ---- sources ------------------------------------------------------
    def put_source(self, source):
        with self._lock:
            self.sources[source.id] = source

    def get_source(self, source_id):
        return self.sources.get(source_id)

    def update_source(self, source_id, **fields):
        with self._lock:
            src = self.sources.get(source_id)
            if src is None:
                return None
            for k, v in fields.items():
                setattr(src, k, v)
            return src

    def sources_for_video(self, video_id):
        return [s for s in self.sources.values() if s.video_id == video_id]

    def list_sources(self, league=None, limit=500):
        out = [s for s in self.sources.values() if league is None or s.league == league]
        out.sort(key=lambda s: (s.played_at or s.created_at), reverse=True)
        return out[:limit]

    # ---- charts -------------------------------------------------------
    def put_chart(self, chart):
        with self._lock:
            self.charts[chart.id] = chart
            self.share_index[chart.share_slug] = chart.id

    def get_chart(self, slug):
        return self.charts.get(slug)

    def chart_by_share(self, share_slug):
        cid = self.share_index.get(share_slug)
        return self.charts.get(cid) if cid else None

    def update_chart(self, slug, **fields):
        with self._lock:
            chart = self.charts.get(slug)
            if chart is None:
                return None
            for k, v in fields.items():
                setattr(chart, k, v)
            return chart

    def charts_for_run(self, run_id):
        return [c for c in self.charts.values() if c.run_id == run_id]

    def save_overrides(self, slug, overrides, expected_version, now):
        with self._lock:
            chart = self.charts.get(slug)
            if chart is None:
                raise KeyError(slug)
            if expected_version is not None and expected_version != chart.overrides_version:
                return False, chart.overrides_version, chart.overrides
            chart.overrides = dict(overrides)
            chart.overrides_version += 1
            chart.updated_at = now
            return True, chart.overrides_version, chart.overrides

    # ---- workers ------------------------------------------------------
    def heartbeat(self, worker):
        with self._lock:
            self.workers_[worker.id] = worker

    def workers(self):
        return list(self.workers_.values())

    # ---- playlists ----------------------------------------------------
    def put_playlist(self, playlist):
        with self._lock:
            self.playlists[playlist.id] = playlist

    def get_playlist(self, playlist_id):
        return self.playlists.get(playlist_id)

    def list_playlists(self):
        return sorted(self.playlists.values(), key=lambda p: p.created_at)

    def update_playlist(self, playlist_id, **fields):
        with self._lock:
            p = self.playlists.get(playlist_id)
            if p is None:
                return None
            for k, v in fields.items():
                setattr(p, k, v)
            return p

    def delete_playlist(self, playlist_id):
        with self._lock:
            self.playlists.pop(playlist_id, None)

    # ---- rate limit ---------------------------------------------------
    def bump_rate_limit(self, ip_hash, now, hour_limit, day_limit):
        with self._lock:
            row = self.rate.setdefault(ip_hash, {})
            hb, db = _hour_bucket(now), _day_bucket(now)
            if row.get("hour_bucket") != hb:
                row["hour_bucket"], row["hour_count"] = hb, 0
            if row.get("day_bucket") != db:
                row["day_bucket"], row["day_count"] = db, 0
            if row["hour_count"] >= hour_limit or row["day_count"] >= day_limit:
                return False
            row["hour_count"] += 1
            row["day_count"] += 1
            return True

    # ---- backup -------------------------------------------------------
    def export_all(self):
        return {
            "runs": [r.to_dict() for r in self.runs.values()],
            "jobs": [j.to_dict() for j in self.jobs.values()],
            "sources": [s.to_dict() for s in self.sources.values()],
            "charts": [c.to_dict() for c in self.charts.values()],
            "workers": [w.to_dict() for w in self.workers_.values()],
            "watched_playlists": [p.to_dict() for p in self.playlists.values()],
        }

    def import_all(self, data):
        with self._lock:
            for d in data.get("runs", []):
                self.put_run(Run.from_dict(d))
            for d in data.get("jobs", []):
                self.put_job(Job.from_dict(d))
            for d in data.get("sources", []):
                self.put_source(Source.from_dict(d))
            for d in data.get("charts", []):
                self.put_chart(Chart.from_dict(d))
            for d in data.get("workers", []):
                self.heartbeat(Worker.from_dict(d))
            for d in data.get("watched_playlists", []):
                self.put_playlist(WatchedPlaylist.from_dict(d))
