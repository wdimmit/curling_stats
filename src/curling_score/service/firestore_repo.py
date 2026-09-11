"""The Repo on Firestore: one document per record, transactions where it matters.

Firestore has no row locks. The three operations that must not race -- handing
a job to one worker, saving overrides only against the version the page last
saw, and counting a submission -- each read and write inside a transaction,
which Firestore retries for us if another writer got there first.

Composite indexes this needs (``deploy/firestore.indexes.json``):
    jobs      (state ASC, run_after ASC, created_at ASC)
    jobs      (state ASC, lease_expires_at ASC)
    vod_runs  (video_id ASC, created_at DESC)
    sources   (league ASC, played_at DESC)
    charts    (run_id ASC)
"""

from datetime import timedelta

from curling_score.service.records import (
    Chart, Job, Run, Source, WatchedPlaylist, Worker,
)
from curling_score.service.repo import LEASE_S, _day_bucket, _hour_bucket

RUNS, JOBS, SOURCES, CHARTS = "vod_runs", "jobs", "sources", "charts"
SHARES, WORKERS, PLAYLISTS, RATES = "share_slugs", "workers", "watched_playlists", "rate_limits"


class FirestoreRepo:
    def __init__(self, client=None, project: str | None = None, database: str | None = None):
        from google.cloud import firestore

        self._fs = firestore
        kwargs = {}
        if project:
            kwargs["project"] = project
        if database:
            kwargs["database"] = database
        self.db = client or firestore.Client(**kwargs)

    # ---- helpers ------------------------------------------------------
    def _col(self, name):
        return self.db.collection(name)

    def _get(self, col, doc_id, cls):
        snap = self._col(col).document(doc_id).get()
        return cls.from_dict(snap.to_dict()) if snap.exists else None

    def _update(self, col, doc_id, cls, fields):
        ref = self._col(col).document(doc_id)
        ref.update(fields)
        snap = ref.get()
        return cls.from_dict(snap.to_dict()) if snap.exists else None

    def _where(self, col, field, op, value):
        from google.cloud.firestore_v1.base_query import FieldFilter

        return self._col(col).where(filter=FieldFilter(field, op, value))

    # ---- runs ---------------------------------------------------------
    def put_run(self, run):
        self._col(RUNS).document(run.id).set(run.to_dict())

    def get_run(self, run_id):
        return self._get(RUNS, run_id, Run)

    def update_run(self, run_id, **fields):
        return self._update(RUNS, run_id, Run, fields)

    def runs_for_video(self, video_id):
        q = (self._where(RUNS, "video_id", "==", video_id)
             .order_by("created_at", direction=self._fs.Query.DESCENDING))
        return [Run.from_dict(d.to_dict()) for d in q.stream()]

    def list_runs(self, status=None, limit=100):
        q = self._col(RUNS)
        if status is not None:
            q = self._where(RUNS, "status", "==", status)
        q = q.order_by("created_at", direction=self._fs.Query.DESCENDING).limit(limit)
        return [Run.from_dict(d.to_dict()) for d in q.stream()]

    # ---- jobs ---------------------------------------------------------
    def put_job(self, job):
        self._col(JOBS).document(job.id).set(job.to_dict())

    def get_job(self, job_id):
        return self._get(JOBS, job_id, Job)

    def update_job(self, job_id, **fields):
        return self._update(JOBS, job_id, Job, fields)

    def job_for_run(self, run_id):
        q = (self._where(JOBS, "run_id", "==", run_id)
             .order_by("created_at", direction=self._fs.Query.DESCENDING).limit(1))
        docs = list(q.stream())
        return Job.from_dict(docs[0].to_dict()) if docs else None

    def _ready_query(self, now):
        # An inequality filter must lead the ordering, so eligibility time
        # orders the queue; for fresh jobs that is creation order anyway.
        return (self._where(JOBS, "state", "==", "queued")
                .where(filter=self._fs.FieldFilter("run_after", "<=", now))
                .order_by("run_after").order_by("created_at"))

    def claim_job(self, worker_id, now, lease_s=LEASE_S):
        transaction = self.db.transaction()
        fs = self._fs

        @fs.transactional
        def _claim(tx):
            docs = list(tx.get(self._ready_query(now).limit(1)))
            if not docs:
                return None
            snap = docs[0]
            data = snap.to_dict()
            if data.get("state") != "queued":
                return None
            update = {
                "state": "running", "worker_id": worker_id,
                "attempts": int(data.get("attempts", 0)) + 1,
                "started_at": now, "progress_at": now,
                "lease_expires_at": now + timedelta(seconds=lease_s),
            }
            tx.update(snap.reference, update)
            tx.update(self._col(RUNS).document(data["run_id"]), {"status": "processing"})
            return Job.from_dict({**data, **update})

        return _claim(transaction)

    def requeue_expired(self, now):
        q = (self._where(JOBS, "state", "==", "running")
             .where(filter=self._fs.FieldFilter("lease_expires_at", "<", now)))
        n = 0
        batch = self.db.batch()
        for snap in q.stream():
            data = snap.to_dict()
            batch.update(snap.reference, {
                "state": "queued", "worker_id": None, "lease_expires_at": None,
                "run_after": now, "error_kind": "lease_expired",
            })
            batch.update(self._col(RUNS).document(data["run_id"]), {"status": "queued"})
            n += 1
        if n:
            batch.commit()
        return n

    def queue_position(self, job_id):
        from datetime import datetime, timezone

        ids = [d.id for d in self._ready_query(datetime.now(timezone.utc)).stream()]
        return ids.index(job_id) if job_id in ids else None

    def count_queued(self):
        from datetime import datetime, timezone

        result = self._ready_query(datetime.now(timezone.utc)).count().get()
        return int(result[0][0].value)

    # ---- sources ------------------------------------------------------
    def put_source(self, source):
        self._col(SOURCES).document(source.id).set(source.to_dict())

    def get_source(self, source_id):
        return self._get(SOURCES, source_id, Source)

    def update_source(self, source_id, **fields):
        return self._update(SOURCES, source_id, Source, fields)

    def sources_for_video(self, video_id):
        q = self._where(SOURCES, "video_id", "==", video_id)
        return [Source.from_dict(d.to_dict()) for d in q.stream()]

    def list_sources(self, league=None, limit=500):
        q = self._col(SOURCES)
        if league is not None:
            q = self._where(SOURCES, "league", "==", league)
        docs = [Source.from_dict(d.to_dict()) for d in q.limit(limit * 2).stream()]
        docs.sort(key=lambda s: (s.played_at or s.created_at), reverse=True)
        return docs[:limit]

    # ---- charts -------------------------------------------------------
    def put_chart(self, chart):
        batch = self.db.batch()
        batch.set(self._col(CHARTS).document(chart.id), chart.to_dict())
        batch.set(self._col(SHARES).document(chart.share_slug), {"chart_id": chart.id})
        batch.commit()

    def get_chart(self, slug):
        return self._get(CHARTS, slug, Chart)

    def chart_by_share(self, share_slug):
        snap = self._col(SHARES).document(share_slug).get()
        if not snap.exists:
            return None
        return self.get_chart(snap.to_dict()["chart_id"])

    def update_chart(self, slug, **fields):
        return self._update(CHARTS, slug, Chart, fields)

    def charts_for_run(self, run_id):
        q = self._where(CHARTS, "run_id", "==", run_id)
        return [Chart.from_dict(d.to_dict()) for d in q.stream()]

    def save_overrides(self, slug, overrides, expected_version, now):
        ref = self._col(CHARTS).document(slug)
        transaction = self.db.transaction()
        fs = self._fs

        @fs.transactional
        def _save(tx):
            snap = tx.get(ref)
            if not snap.exists:
                raise KeyError(slug)
            data = snap.to_dict()
            current = int(data.get("overrides_version", 0))
            if expected_version is not None and expected_version != current:
                return False, current, data.get("overrides", {})
            tx.update(ref, {"overrides": dict(overrides), "overrides_version": current + 1,
                            "updated_at": now})
            return True, current + 1, dict(overrides)

        return _save(transaction)

    # ---- workers ------------------------------------------------------
    def heartbeat(self, worker):
        self._col(WORKERS).document(worker.id).set(worker.to_dict())

    def workers(self):
        return [Worker.from_dict(d.to_dict()) for d in self._col(WORKERS).stream()]

    # ---- playlists ----------------------------------------------------
    def put_playlist(self, playlist):
        self._col(PLAYLISTS).document(playlist.id).set(playlist.to_dict())

    def get_playlist(self, playlist_id):
        return self._get(PLAYLISTS, playlist_id, WatchedPlaylist)

    def list_playlists(self):
        docs = [WatchedPlaylist.from_dict(d.to_dict()) for d in self._col(PLAYLISTS).stream()]
        return sorted(docs, key=lambda p: p.created_at)

    def update_playlist(self, playlist_id, **fields):
        return self._update(PLAYLISTS, playlist_id, WatchedPlaylist, fields)

    def delete_playlist(self, playlist_id):
        self._col(PLAYLISTS).document(playlist_id).delete()

    # ---- rate limit ---------------------------------------------------
    def bump_rate_limit(self, ip_hash, now, hour_limit, day_limit):
        ref = self._col(RATES).document(ip_hash)
        transaction = self.db.transaction()
        fs = self._fs
        hb, db_ = _hour_bucket(now), _day_bucket(now)

        @fs.transactional
        def _bump(tx):
            snap = tx.get(ref)
            row = snap.to_dict() if snap.exists else {}
            hour = row.get("hour_count", 0) if row.get("hour_bucket") == hb else 0
            day = row.get("day_count", 0) if row.get("day_bucket") == db_ else 0
            if hour >= hour_limit or day >= day_limit:
                return False
            tx.set(ref, {"hour_bucket": hb, "hour_count": hour + 1,
                         "day_bucket": db_, "day_count": day + 1,
                         # For a Firestore TTL policy on this field.
                         "expires_at": now + timedelta(days=2)})
            return True

        return _bump(transaction)

    # ---- backup -------------------------------------------------------
    def export_all(self):
        def dump(col):
            return [d.to_dict() for d in self._col(col).stream()]
        return {"runs": dump(RUNS), "jobs": dump(JOBS), "sources": dump(SOURCES),
                "charts": dump(CHARTS), "workers": dump(WORKERS),
                "watched_playlists": dump(PLAYLISTS)}

    def import_all(self, data):
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
