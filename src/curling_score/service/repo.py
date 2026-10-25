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
import hmac
import json
import threading
from datetime import datetime, timedelta, timezone
from typing import Protocol

from curling_score.service.records import (
    Chart, ClipJob, Flag, Invite, Job, LabelAnswer, LabelItem, Play, PlaylistIndexEntry,
    PracticeSession, PracticeWatch, Review, Run, SigninRelay, Source, Team, TeamNameEdit, User,
    WatchedPlaylist, Worker, play_id,
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
    def runs_ready_since(self, t: datetime) -> list[Run]: ...
    # jobs
    def put_job(self, job: Job) -> None: ...
    def get_job(self, job_id: str) -> Job | None: ...
    def update_job(self, job_id: str, **fields) -> Job | None: ...
    def job_for_run(self, run_id: str) -> Job | None: ...
    def claim_job(self, worker_id: str, now: datetime, lease_s: float = LEASE_S,
                  kinds: tuple = ("vod",)) -> Job | None: ...
    def requeue_expired(self, now: datetime) -> int: ...
    # A worker starting again: its running jobs back in the queue at once, as
    # requeue_expired puts a job whose lease has run out.
    def release_jobs(self, worker_id: str, now: datetime) -> int: ...
    def queue_position(self, job_id: str) -> int | None: ...
    def count_queued(self) -> int: ...
    # sources
    def put_source(self, source: Source) -> None: ...
    def get_source(self, source_id: str) -> Source | None: ...
    def update_source(self, source_id: str, **fields) -> Source | None: ...
    def set_entered_score(self, source_id: str, end: int,
                          entry: dict | None) -> Source | None: ...
    # One colour's switched ends (Source.switched_ends), set or cleared on
    # its own; None back for a game we do not have.
    def set_switched_ends(self, source_id: str, color: str,
                          entry: dict | None) -> Source | None: ...
    def sources_for_video(self, video_id: str) -> list[Source]: ...
    def list_sources(self, league: str | None = None, limit: int = 500) -> list[Source]: ...
    def sources_played(self, since: datetime | None = None, before: datetime | None = None,
                       limit: int | None = None) -> list[Source]: ...
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
    def list_users(self, limit: int = 500) -> list[User]: ...       # newest first
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

    # sign-in relays: an emailed link held behind a code, transient
    def put_signin_relay(self, relay: SigninRelay) -> None: ...
    def get_signin_relay(self, relay_id: str) -> SigninRelay | None: ...
    def claim_signin_relay(self, relay_id: str, code: str, now: datetime,
                           max_misses: int) -> tuple[str, str | None]: ...
    # workers
    def heartbeat(self, worker: Worker) -> None: ...
    def workers(self) -> list[Worker]: ...
    # playlists
    def put_playlist(self, playlist: WatchedPlaylist) -> None: ...
    def get_playlist(self, playlist_id: str) -> WatchedPlaylist | None: ...
    def list_playlists(self) -> list[WatchedPlaylist]: ...
    def update_playlist(self, playlist_id: str, **fields) -> WatchedPlaylist | None: ...
    def delete_playlist(self, playlist_id: str) -> None: ...
    # the channel's playlists, indexed -- a cache of YouTube, not a record
    def list_playlist_index(self) -> list[PlaylistIndexEntry]: ...
    def put_playlist_index(self, entry: PlaylistIndexEntry) -> None: ...
    # flags
    def put_flag(self, flag: Flag) -> None: ...
    def list_flags(self, status: str | None = None, limit: int = 200) -> list[Flag]: ...
    def resolve_flag(self, flag_id: str, now: datetime) -> Flag | None: ...
    def put_flag_if_absent(self, flag: Flag) -> bool: ...
    # team-name edits -- who named a game's teams, and what they were before
    def put_team_name_edit(self, edit: TeamNameEdit) -> None: ...
    def team_name_edits(self, source_id: str | None = None,
                        limit: int = 200) -> list[TeamNameEdit]: ...
    # reviews -- the nightly review's reading of each game, per run
    def put_review(self, review: Review) -> None: ...
    def get_review(self, review_id: str) -> Review | None: ...
    def reviews_ready_between(self, t0: datetime, t1: datetime) -> list[Review]: ...
    def reviews_for_source(self, source_id: str) -> list[Review]: ...
    # plays -- which rocks of a game a person threw, one per person per game
    def put_play(self, play: Play) -> None: ...
    def get_play(self, user_id: str, source_id: str) -> Play | None: ...
    def delete_play(self, user_id: str, source_id: str) -> bool: ...
    def plays_for_user(self, user_id: str, limit: int = 500) -> list[Play]: ...
    def all_plays(self) -> list[Play]: ...
    # clip jobs -- clips of a play's rocks, a queue of their own (service/gameclips.py)
    def put_clip_job(self, job: ClipJob) -> None: ...
    def get_clip_job(self, job_id: str) -> ClipJob | None: ...
    def update_clip_job(self, job_id: str, **fields) -> ClipJob | None: ...
    def clip_jobs_for_play(self, play_id: str) -> list[ClipJob]: ...
    def clip_jobs_in_state(self, state: str) -> list[ClipJob]: ...
    def take_clip_job(self, job_id: str, worker_id: str, now: datetime,
                      lease_s: float) -> ClipJob | None: ...
    # practice
    def put_watch(self, watch: PracticeWatch) -> None: ...
    def get_watch(self, watch_id: str) -> PracticeWatch | None: ...
    def update_watch(self, watch_id: str, **fields) -> PracticeWatch | None: ...
    def watches_for_sheet(self, sheet: int) -> list[PracticeWatch]: ...
    def watch_for_job(self, job_id: str) -> PracticeWatch | None: ...
    def put_session(self, session: PracticeSession) -> None: ...
    def get_session(self, session_id: str) -> PracticeSession | None: ...
    def update_session(self, session_id: str, **fields) -> PracticeSession | None: ...
    def sessions_for_watch(self, watch_id: str) -> list[PracticeSession]: ...
    def sessions_for_user(self, user_id: str, limit: int = 50) -> list[PracticeSession]: ...
    # member labelling -- items put to members, and their answers (service/labelling.py)
    def put_label_item(self, item: LabelItem) -> None: ...
    def get_label_item(self, item_id: str) -> LabelItem | None: ...
    def update_label_item(self, item_id: str, **fields) -> LabelItem | None: ...
    def open_label_items(self, kind: str, n_labels: int, limit: int,
                         after: datetime | None = None) -> list[LabelItem]: ...
    def list_label_items(self, kind: str, state: str, limit: int,
                         after: datetime | None = None) -> list[LabelItem]: ...
    def hold_label_item(self, item_id: str, uid: str, now: datetime,
                        hold_s: float) -> LabelItem | None: ...
    def submit_label(self, answer: LabelAnswer, now: datetime,
                     max_labels: int) -> tuple[str, LabelItem | None]: ...
    def skip_label_item(self, item_id: str, uid: str, now: datetime,
                        retire_after: int) -> LabelItem | None: ...
    def label_answers(self, item_id: str) -> list[LabelAnswer]: ...
    def label_count(self, uid: str, kind: str) -> int: ...
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
        self.relays: dict[str, SigninRelay] = {}
        self.claims: dict[tuple[str, str], str] = {}
        self.flags: dict[str, Flag] = {}
        self.plays: dict[str, Play] = {}
        self.clip_jobs: dict[str, ClipJob] = {}
        self.reviews: dict[str, Review] = {}
        self.playlist_index: dict[str, PlaylistIndexEntry] = {}
        self.watches: dict[str, PracticeWatch] = {}
        self.sessions: dict[str, PracticeSession] = {}
        self.team_name_edits_: dict[str, TeamNameEdit] = {}
        self.label_items: dict[str, LabelItem] = {}
        self.label_answers_: dict[str, LabelAnswer] = {}

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

    def runs_ready_since(self, t):
        got = [r for r in self.runs.values() if r.ready_at is not None and r.ready_at >= t]
        return sorted(got, key=lambda r: r.ready_at)

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

    def _queued(self, now=None, kinds=None):
        # Live jobs first: a stream being played cannot wait for a recording.
        return sorted(
            (j for j in self.jobs.values()
             if j.state == "queued" and (now is None or j.run_after <= now)
             and (kinds is None or (j.kind or "vod") in kinds)),
            key=lambda j: (j.kind != "live", j.created_at),
        )

    def claim_job(self, worker_id, now, lease_s=LEASE_S, kinds=("vod",)):
        with self._lock:
            ready = self._queued(now, kinds)
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

    def _requeue(self, job, now, why):
        job.state = "queued"
        job.worker_id = None
        job.lease_expires_at = None
        job.run_after = now
        job.error_kind = why
        run = self.runs.get(job.run_id)
        if run is not None:
            run.status = "queued"

    def requeue_expired(self, now):
        with self._lock:
            n = 0
            for job in self.jobs.values():
                if (job.state == "running" and job.lease_expires_at is not None
                        and job.lease_expires_at < now):
                    self._requeue(job, now, "lease_expired")
                    n += 1
            return n

    def release_jobs(self, worker_id, now):
        with self._lock:
            n = 0
            for job in self.jobs.values():
                if job.state == "running" and job.worker_id == worker_id:
                    self._requeue(job, now, "worker_restarted")
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

    def set_entered_score(self, source_id, end, entry):
        with self._lock:
            src = self.sources.get(source_id)
            if src is None:
                return None
            scores = dict(src.entered_scores or {})
            if entry is None:
                scores.pop(str(end), None)
            else:
                scores[str(end)] = dict(entry)
            src.entered_scores = scores
            return src

    def set_switched_ends(self, source_id, color, entry):
        with self._lock:
            src = self.sources.get(source_id)
            if src is None:
                return None
            marks = dict(src.switched_ends or {})
            if entry is None:
                marks.pop(color, None)
            else:
                marks[color] = dict(entry)
            src.switched_ends = marks
            return src

    def sources_for_video(self, video_id):
        return [s for s in self.sources.values() if s.video_id == video_id]

    def list_sources(self, league=None, limit=500):
        out = [s for s in self.sources.values() if league is None or s.league == league]
        out.sort(key=lambda s: (s.played_at or s.created_at), reverse=True)
        return out[:limit]

    def sources_played(self, since=None, before=None, limit=None):
        """Played in [since, before), newest first: by played_at, or by when it
        arrived for a game with no date read off its video."""
        when = lambda s: s.played_at or s.created_at
        out = [s for s in self.sources.values()
               if (since is None or when(s) >= since) and (before is None or when(s) < before)]
        out.sort(key=when, reverse=True)
        return out if limit is None else out[:limit]

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

    def list_users(self, limit=500):
        return sorted(self.users.values(), key=lambda u: u.created_at, reverse=True)[:limit]

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

    # ---- sign-in relays -----------------------------------------------
    def put_signin_relay(self, relay):
        with self._lock:
            self.relays[relay.id] = relay

    def get_signin_relay(self, relay_id):
        return self.relays.get(relay_id)

    def claim_signin_relay(self, relay_id, code, now, max_misses):
        with self._lock:
            got = self.relays.get(relay_id)
            if got is None:
                return "gone", None
            if not got.live(now):
                del self.relays[relay_id]
                return "gone", None
            if hmac.compare_digest(got.code.encode(), code.encode()):
                del self.relays[relay_id]
                return "ok", got.link
            got.misses += 1
            if got.misses >= max_misses:
                del self.relays[relay_id]
                return "gone", None
            return "wrong", None

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

    def list_playlist_index(self):
        return sorted(self.playlist_index.values(), key=lambda e: e.id)

    def put_playlist_index(self, entry):
        with self._lock:
            self.playlist_index[entry.id] = entry

    # ---- flags --------------------------------------------------------
    def put_flag(self, flag):
        with self._lock:
            self.flags[flag.id] = flag

    def list_flags(self, status=None, limit=200):
        out = [f for f in self.flags.values() if status is None or f.status == status]
        out.sort(key=lambda f: f.created_at, reverse=True)
        return out[:limit]

    def resolve_flag(self, flag_id, now):
        with self._lock:
            got = self.flags.get(flag_id)
            if got is None:
                return None
            if got.status != "resolved":
                got.status, got.resolved_at = "resolved", now
            return got

    def put_flag_if_absent(self, flag):
        with self._lock:
            if flag.id in self.flags:
                return False
            self.flags[flag.id] = flag
            return True

    # ---- team-name edits ----------------------------------------------
    def put_team_name_edit(self, edit):
        with self._lock:
            self.team_name_edits_[edit.id] = edit

    def team_name_edits(self, source_id=None, limit=200):
        out = [e for e in self.team_name_edits_.values()
               if source_id is None or e.source_id == source_id]
        out.sort(key=lambda e: e.at, reverse=True)
        return out[:limit]

    # ---- reviews ------------------------------------------------------
    def put_review(self, review):
        with self._lock:
            self.reviews[review.id] = review

    def get_review(self, review_id):
        return self.reviews.get(review_id)

    def reviews_ready_between(self, t0, t1):
        return [r for r in self.reviews.values()
                if r.run_ready_at is not None and t0 <= r.run_ready_at < t1]

    def reviews_for_source(self, source_id):
        got = [r for r in self.reviews.values() if r.source_id == source_id]
        return sorted(got, key=lambda r: r.reviewed_at, reverse=True)

    # ---- plays --------------------------------------------------------
    def put_play(self, play):
        with self._lock:
            self.plays[play.id] = play

    def get_play(self, user_id, source_id):
        return self.plays.get(play_id(user_id, source_id))

    def delete_play(self, user_id, source_id):
        with self._lock:
            return self.plays.pop(play_id(user_id, source_id), None) is not None

    def plays_for_user(self, user_id, limit=500):
        out = [p for p in self.plays.values() if p.user_id == user_id]
        out.sort(key=lambda p: p.updated_at, reverse=True)
        return out[:limit]

    def all_plays(self):
        return sorted(self.plays.values(), key=lambda p: p.updated_at, reverse=True)

    # ---- clip jobs ----------------------------------------------------
    def put_clip_job(self, job):
        with self._lock:
            self.clip_jobs[job.id] = job

    def get_clip_job(self, job_id):
        return self.clip_jobs.get(job_id)

    def update_clip_job(self, job_id, **fields):
        with self._lock:
            job = self.clip_jobs.get(job_id)
            if job is None:
                return None
            for k, v in fields.items():
                setattr(job, k, v)
            return job

    def clip_jobs_for_play(self, play_id):
        return sorted((j for j in self.clip_jobs.values() if j.play_id == play_id),
                      key=lambda j: j.created_at)

    def clip_jobs_in_state(self, state):
        return sorted((j for j in self.clip_jobs.values() if j.state == state),
                      key=lambda j: j.created_at)

    def take_clip_job(self, job_id, worker_id, now, lease_s):
        with self._lock:
            job = self.clip_jobs.get(job_id)
            if job is None or job.state != "queued":
                return None
            job.state, job.worker_id = "running", worker_id
            job.attempts += 1
            job.lease_expires_at = now + timedelta(seconds=lease_s)
            return job

    # ---- practice -----------------------------------------------------
    def put_watch(self, watch):
        with self._lock:
            self.watches[watch.id] = watch

    def get_watch(self, watch_id):
        return self.watches.get(watch_id)

    def update_watch(self, watch_id, **fields):
        with self._lock:
            w = self.watches.get(watch_id)
            if w is None:
                return None
            for k, v in fields.items():
                setattr(w, k, v)
            return w

    def watches_for_sheet(self, sheet):
        out = [w for w in self.watches.values() if w.sheet == sheet]
        return sorted(out, key=lambda w: w.created_at, reverse=True)

    def watch_for_job(self, job_id):
        return next((w for w in self.watches.values() if w.job_id == job_id), None)

    def put_session(self, session):
        with self._lock:
            self.sessions[session.id] = session

    def get_session(self, session_id):
        return self.sessions.get(session_id)

    def update_session(self, session_id, **fields):
        with self._lock:
            s = self.sessions.get(session_id)
            if s is None:
                return None
            for k, v in fields.items():
                setattr(s, k, v)
            return s

    def sessions_for_watch(self, watch_id):
        out = [s for s in self.sessions.values() if s.watch_id == watch_id]
        return sorted(out, key=lambda s: s.started_at)

    def sessions_for_user(self, user_id, limit=50):
        out = [s for s in self.sessions.values() if s.user_id == user_id]
        return sorted(out, key=lambda s: s.started_at, reverse=True)[:limit]

    # ---- member labelling ---------------------------------------------
    def put_label_item(self, item):
        with self._lock:
            self.label_items[item.id] = item

    def get_label_item(self, item_id):
        return self.label_items.get(item_id)

    def update_label_item(self, item_id, **fields):
        with self._lock:
            item = self.label_items.get(item_id)
            if item is None:
                return None
            for k, v in fields.items():
                setattr(item, k, v)
            return item

    def _label_items_where(self, limit, after, keep):
        with self._lock:
            out = [i for i in self.label_items.values()
                   if keep(i) and (after is None or i.created_at > after)]
        return sorted(out, key=lambda i: i.created_at)[:limit]

    def open_label_items(self, kind, n_labels, limit, after=None):
        return self._label_items_where(
            limit, after, lambda i: i.kind == kind and i.state == "open"
            and i.n_labels == n_labels)

    def list_label_items(self, kind, state, limit, after=None):
        return self._label_items_where(
            limit, after, lambda i: i.kind == kind and i.state == state)

    def hold_label_item(self, item_id, uid, now, hold_s):
        with self._lock:
            item = self.label_items.get(item_id)
            if item is None or item.state != "open":
                return None
            if uid in item.labelled_by or uid in item.skipped_by:
                return None
            if item.hold_uid not in (None, uid) and item.hold_until > now:
                return None
            item.hold_uid, item.hold_until = uid, now + timedelta(seconds=hold_s)
            return item

    def submit_label(self, answer, now, max_labels):
        with self._lock:
            item = self.label_items.get(answer.item_id)
            if item is None:
                return "missing", None
            if item.state in ("pending", "retired"):
                return "closed", item
            if answer.uid in item.labelled_by:
                self.label_answers_[answer.id] = answer
                return "replaced", item
            if item.n_labels >= max_labels:
                return "full", item
            if item.hold_uid not in (None, answer.uid) and item.hold_until > now:
                return "held", item
            self.label_answers_[answer.id] = answer
            item.n_labels += 1
            item.labelled_by.append(answer.uid)
            if item.hold_uid == answer.uid:
                item.hold_uid = item.hold_until = None
            if item.n_labels >= max_labels:
                item.state, item.done_at = "done", now
            return "added", item

    def skip_label_item(self, item_id, uid, now, retire_after):
        with self._lock:
            item = self.label_items.get(item_id)
            if item is None:
                return None
            if uid in item.skipped_by or item.state != "open":
                return item
            item.skipped_by.append(uid)
            item.skips += 1
            if item.hold_uid == uid:
                item.hold_uid = item.hold_until = None
            if item.skips >= retire_after:
                item.state = "retired"
            return item

    def label_answers(self, item_id):
        out = [a for a in self.label_answers_.values() if a.item_id == item_id]
        return sorted(out, key=lambda a: a.at)

    def label_count(self, uid, kind):
        return sum(1 for a in self.label_answers_.values() if a.uid == uid and a.kind == kind)

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
            "flags": [f.to_dict() for f in self.flags.values()],
            "plays": [p.to_dict() for p in self.plays.values()],
            "clip_jobs": [j.to_dict() for j in self.clip_jobs.values()],
            "reviews": [r.to_dict() for r in self.reviews.values()],
            "practice_watches": [w.to_dict() for w in self.watches.values()],
            "practice_sessions": [s.to_dict() for s in self.sessions.values()],
            "team_name_edits": [e.to_dict() for e in self.team_name_edits_.values()],
            "label_items": [i.to_dict() for i in self.label_items.values()],
            "label_answers": [a.to_dict() for a in self.label_answers_.values()],
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
            for d in data.get("flags", []):
                self.put_flag(Flag.from_dict(d))
            for d in data.get("plays", []):
                self.put_play(Play.from_dict(d))
            for d in data.get("clip_jobs", []):
                self.put_clip_job(ClipJob.from_dict(d))
            for d in data.get("reviews", []):
                self.put_review(Review.from_dict(d))
            for d in data.get("practice_watches", []):
                self.put_watch(PracticeWatch.from_dict(d))
            for d in data.get("practice_sessions", []):
                self.put_session(PracticeSession.from_dict(d))
            for d in data.get("team_name_edits", []):
                self.put_team_name_edit(TeamNameEdit.from_dict(d))
            for d in data.get("label_items", []):
                self.put_label_item(LabelItem.from_dict(d))
            for d in data.get("label_answers", []):
                a = LabelAnswer.from_dict(d)
                self.label_answers_[a.id] = a
            # Claims are derivable, so they are not exported -- rebuilt here
            # instead, because a restore that lost them would start handing a
            # team a second chart for a game it already has.
            for c in self.charts.values():
                key = c.team_id or c.owner_user_id
                if key and c.source_id and not c.superseded_by:
                    self.claim_chart(key, c.source_id, c.id)
