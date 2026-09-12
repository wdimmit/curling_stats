"""Where the service's records live, and the few operations that must be atomic.

Everything the API does is a handful of reads and writes on small documents.
Four of them have to be indivisible -- handing a job to exactly one worker,
saving overrides only if nobody else saved first, counting a submission
against a rate limit, and settling which chart a team's game gets when two
teammates ask at once -- and those are the whole reason this is an interface
rather than a dict: the in-memory version makes them atomic with a lock, the
Firestore version with a transaction, and the API cannot tell which it has.
"""

import copy
import json
import threading
from datetime import datetime, timedelta, timezone
from typing import Protocol

from curling_score.service.records import (
    Chart, Invite, Job, Run, Source, Team, User, WatchedPlaylist, Worker,
)

LEASE_S = 600.0
HEARTBEAT_STALE_S = 90.0
# What one chart's overrides may grow to. Firestore's hard limit is 1 MiB per
# document and a fully hand-placed game measures ~300 KB, so this leaves room
# to work in and still refuses long before the commit would fail. Checked on
# the merged result, not the request: many small merges add up too.
MAX_STORED_OVERRIDES_BYTES = 700_000


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
    def merge_overrides(self, slug: str, patch: dict, remove: list[str], meta: dict,
                        now: datetime) -> tuple[int, dict, bool]: ...
    def charts_for_owner(self, user_id: str, limit: int = 200) -> list[Chart]: ...
    def charts_for_team(self, team_id: str, limit: int = 200) -> list[Chart]: ...
    # One chart per game per owner. Returns the winning chart id -- ours if we
    # got there first, theirs if not. Atomic: this is the fourth one.
    def claim_chart(self, owner_key: str, source_id: str, chart_id: str) -> str: ...
    def chart_claim(self, owner_key: str, source_id: str) -> str | None: ...
    # users
    def put_user(self, user: User) -> None: ...
    def get_user(self, user_id: str) -> User | None: ...
    def update_user(self, user_id: str, **fields) -> User | None: ...
    # teams -- member storage is deliberately behind these four
    def put_team(self, team: Team) -> None: ...
    def get_team(self, team_id: str) -> Team | None: ...
    def update_team(self, team_id: str, **fields) -> Team | None: ...
    def teams_for_user(self, user_id: str) -> list[Team]: ...
    def team_members(self, team_id: str) -> list[str]: ...
    def add_member(self, team_id: str, user_id: str) -> Team | None: ...
    def remove_member(self, team_id: str, user_id: str) -> Team | None: ...
    # invites
    def put_invite(self, invite: Invite) -> None: ...
    def get_invite(self, token: str) -> Invite | None: ...
    def update_invite(self, token: str, **fields) -> Invite | None: ...
    def invites_for_team(self, team_id: str) -> list[Invite]: ...
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
        self.users: dict[str, User] = {}
        self.teams: dict[str, Team] = {}
        self.invites: dict[str, Invite] = {}
        self.claims: dict[tuple[str, str], str] = {}

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

    def merge_overrides(self, slug, patch, remove, meta, now):
        """Apply per-key edits and return (version, merged, ok).

        Unlike save_overrides this never refuses on a version: two people
        editing different shots have no conflict to report, and one editing the
        same shot is simply the later of the two. `ok` is False only when the
        result would be too big to store.
        """
        with self._lock:
            chart = self.charts.get(slug)
            if chart is None:
                raise KeyError(slug)
            merged = dict(chart.overrides)
            merged.update({k: dict(v) for k, v in patch.items()})
            for k in remove:
                merged.pop(k, None)
            blob = json.dumps(merged)
            if len(blob) > MAX_STORED_OVERRIDES_BYTES:
                return chart.overrides_version, copy.deepcopy(chart.overrides), False
            meta_now = dict(chart.overrides_meta)
            meta_now.update({k: dict(v) for k, v in meta.items()})
            for k in remove:
                meta_now.pop(k, None)
            chart.overrides = merged
            chart.overrides_meta = meta_now
            chart.overrides_version += 1
            chart.updated_at = now
            # Round-tripped rather than handed out live: Firestore returns a
            # fresh map every time and the two must not differ.
            return chart.overrides_version, json.loads(blob), True

    def charts_for_owner(self, user_id, limit=200):
        out = [c for c in self.charts.values() if c.owner_user_id == user_id]
        out.sort(key=lambda c: c.updated_at, reverse=True)
        return out[:limit]

    def charts_for_team(self, team_id, limit=200):
        out = [c for c in self.charts.values() if c.team_id == team_id]
        out.sort(key=lambda c: c.updated_at, reverse=True)
        return out[:limit]

    def claim_chart(self, owner_key, source_id, chart_id):
        with self._lock:
            return self.claims.setdefault((owner_key, source_id), chart_id)

    def chart_claim(self, owner_key, source_id):
        return self.claims.get((owner_key, source_id))

    # ---- users --------------------------------------------------------
    def put_user(self, user):
        with self._lock:
            self.users[user.id] = user

    def get_user(self, user_id):
        return self.users.get(user_id)

    def update_user(self, user_id, **fields):
        with self._lock:
            got = self.users.get(user_id)
            if got is None:
                return None
            for k, v in fields.items():
                setattr(got, k, v)
            return got

    # ---- teams --------------------------------------------------------
    def put_team(self, team):
        with self._lock:
            self.teams[team.id] = team

    def get_team(self, team_id):
        return self.teams.get(team_id)

    def update_team(self, team_id, **fields):
        with self._lock:
            got = self.teams.get(team_id)
            if got is None:
                return None
            for k, v in fields.items():
                setattr(got, k, v)
            return got

    def teams_for_user(self, user_id):
        out = [t for t in self.teams.values() if user_id in t.member_ids]
        out.sort(key=lambda t: t.created_at)
        return out

    def team_members(self, team_id):
        got = self.teams.get(team_id)
        return list(got.member_ids) if got else []

    def add_member(self, team_id, user_id):
        with self._lock:
            got = self.teams.get(team_id)
            if got is None:
                return None
            if user_id not in got.member_ids:
                got.member_ids = [*got.member_ids, user_id]
            return got

    def remove_member(self, team_id, user_id):
        with self._lock:
            got = self.teams.get(team_id)
            if got is None:
                return None
            got.member_ids = [m for m in got.member_ids if m != user_id]
            return got

    # ---- invites ------------------------------------------------------
    def put_invite(self, invite):
        with self._lock:
            self.invites[invite.id] = invite

    def get_invite(self, token):
        return self.invites.get(token)

    def update_invite(self, token, **fields):
        with self._lock:
            got = self.invites.get(token)
            if got is None:
                return None
            for k, v in fields.items():
                setattr(got, k, v)
            return got

    def invites_for_team(self, team_id):
        return [i for i in self.invites.values() if i.team_id == team_id]

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
            "users": [u.to_dict() for u in self.users.values()],
            "teams": [t.to_dict() for t in self.teams.values()],
            "invites": [i.to_dict() for i in self.invites.values()],
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
            for d in data.get("users", []):
                self.put_user(User.from_dict(d))
            for d in data.get("teams", []):
                self.put_team(Team.from_dict(d))
            for d in data.get("invites", []):
                self.put_invite(Invite.from_dict(d))
            # Claims are derivable, so they are not exported -- rebuilt here
            # instead, because a restore that lost them would start handing a
            # team a second chart for a game it already has.
            for c in self.charts.values():
                key = c.team_id or c.owner_user_id
                if key and c.source_id and not c.superseded_by:
                    self.claim_chart(key, c.source_id, c.id)
