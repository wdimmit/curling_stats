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
    flags     (status ASC, created_at DESC)

Single-field ranges only, which Firestore indexes by itself:
``vod_runs.ready_at`` and ``reviews.run_ready_at``.

Field-index exemptions the same file carries: charts.overrides and
charts.overrides_meta are maps whose every subfield Firestore would otherwise
index, thousands of entries per chart, for something no query ever touches.
"""

import json
from datetime import timedelta

from curling_score.service.records import (
    Chart, Flag, Invite, Job, Play, PlaylistIndexEntry, Review, Run, Source, Team, User,
    WatchedPlaylist, Worker, play_id,
)
from curling_score.service.repo import (
    LEASE_S, MAX_STORED_OVERRIDES_BYTES, _day_bucket, _hour_bucket,
)

RUNS, JOBS, SOURCES, CHARTS = "vod_runs", "jobs", "sources", "charts"
SHARES, WORKERS, PLAYLISTS, RATES = "share_slugs", "workers", "watched_playlists", "rate_limits"
USERS, TEAMS, INVITES, CLAIMS = "users", "teams", "invites", "chart_claims"
FLAGS, PLAYLIST_INDEX, PLAYS = "flags", "yt_playlists", "plays"
REVIEWS = "reviews"


def _claim_id(owner_key: str, source_id: str) -> str:
    """The document whose existence *is* the claim.

    Derivable on purpose -- two teammates asking at the same moment must
    compute the same id -- which is safe because it is not a capability: it
    holds only a pointer, and the deny-all rules mean no browser ever reads
    this collection. The chart id it points at stays unguessable.
    """
    import hashlib

    return hashlib.sha256(f"{owner_key}|{source_id}".encode()).hexdigest()[:32]


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

    def runs_ready_since(self, t):
        # A range on one field: Firestore's automatic index, no composite.
        q = self._where(RUNS, "ready_at", ">=", t).order_by("ready_at")
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

    def _ready_query(self, now, kind=None):
        # An inequality filter must lead the ordering, so eligibility time
        # orders the queue; for fresh jobs that is creation order anyway.
        q = self._where(JOBS, "state", "==", "queued")
        if kind is not None:
            q = q.where(filter=self._fs.FieldFilter("kind", "==", kind))
        return (q.where(filter=self._fs.FieldFilter("run_after", "<=", now))
                .order_by("run_after").order_by("created_at"))

    def claim_job(self, worker_id, now, lease_s=LEASE_S, kinds=("vod",)):
        transaction = self.db.transaction()
        fs = self._fs

        @fs.transactional
        def _claim(tx):
            # Live jobs first. The recordings' pass is the query from before
            # kinds existed, unfiltered, because a job written then has no
            # `kind` field for an equality filter to match; the few live jobs
            # it can also return are skipped.
            snap = None
            if "live" in kinds:
                docs = list(tx.get(self._ready_query(now, kind="live").limit(1)))
                snap = docs[0] if docs else None
            if snap is None and "vod" in kinds:
                snap = next((d for d in tx.get(self._ready_query(now).limit(20))
                             if (d.to_dict().get("kind") or "vod") != "live"), None)
            if snap is None:
                return None
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

    def set_entered_score(self, source_id, end, entry):
        """One end's entered score, set or cleared on its own.

        A field path rather than a read-modify-write of the whole map, so two
        people filling different ends at the same moment both land. The key
        is a bare digit, which Firestore would read as a walk into a nested
        map; FieldPath quotes it, as it does the override keys above.
        """
        from google.cloud.firestore_v1.field_path import FieldPath

        ref = self._col(SOURCES).document(source_id)
        if not ref.get().exists:
            return None
        path = FieldPath("entered_scores", str(end)).to_api_repr()
        ref.update({path: self._fs.DELETE_FIELD if entry is None else dict(entry)})
        snap = ref.get()
        return Source.from_dict(snap.to_dict()) if snap.exists else None

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
            # Transaction.get yields snapshots even for a single reference.
            snap = next(iter(tx.get(ref)))
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

    def merge_overrides(self, slug, patch, remove, meta, now):
        """Per-key edits, so disjoint saves cannot overwrite one another.

        The field paths must be built with FieldPath, not string-formatted. An
        override key is "<game>.<end>.<shot>" -- dots, and a leading digit --
        and Firestore reads a raw "overrides.0.1.1" as a walk into nested maps.
        That writes {"0": {"1": {"1": ...}}}, which every dotted lookup after it
        misses, so the chart quietly reads back as though nobody ever graded it.
        FieldPath quotes the segment: overrides.`0.1.1`.
        """
        from google.cloud.firestore_v1.field_path import FieldPath

        ref = self._col(CHARTS).document(slug)
        transaction = self.db.transaction()
        fs = self._fs

        @fs.transactional
        def _merge(tx):
            snap = next(iter(tx.get(ref)))
            if not snap.exists:
                raise KeyError(slug)
            data = snap.to_dict()
            current = int(data.get("overrides_version", 0))
            merged = dict(data.get("overrides", {}))
            merged.update({k: dict(v) for k, v in patch.items()})
            for k in remove:
                merged.pop(k, None)
            blob = json.dumps(merged)
            if len(blob) > MAX_STORED_OVERRIDES_BYTES:
                return current, dict(data.get("overrides", {})), False
            updates = {"overrides_version": current + 1, "updated_at": now}
            for k, v in patch.items():
                updates[FieldPath("overrides", k).to_api_repr()] = dict(v)
                if k in meta:
                    updates[FieldPath("overrides_meta", k).to_api_repr()] = dict(meta[k])
            for k in remove:
                updates[FieldPath("overrides", k).to_api_repr()] = fs.DELETE_FIELD
                updates[FieldPath("overrides_meta", k).to_api_repr()] = fs.DELETE_FIELD
            tx.update(ref, updates)
            return current + 1, json.loads(blob), True

        return _merge(transaction)

    def charts_for_owner(self, user_id, limit=200):
        q = self._where(CHARTS, "owner_user_id", "==", user_id)
        docs = [Chart.from_dict(d.to_dict()) for d in q.limit(limit).stream()]
        return sorted(docs, key=lambda c: c.updated_at, reverse=True)

    def charts_for_team(self, team_id, limit=200):
        q = self._where(CHARTS, "team_id", "==", team_id)
        docs = [Chart.from_dict(d.to_dict()) for d in q.limit(limit).stream()]
        return sorted(docs, key=lambda c: c.updated_at, reverse=True)

    def claim_chart(self, owner_key, source_id, chart_id):
        """First writer wins, and everyone else is told who did.

        `create` fails if the document is already there, which is the whole
        uniqueness mechanism -- no transaction, no index, no read-then-write
        for two callers to interleave.
        """
        from google.api_core.exceptions import AlreadyExists

        ref = self._col(CLAIMS).document(_claim_id(owner_key, source_id))
        try:
            ref.create({"chart_id": chart_id, "owner_key": owner_key,
                        "source_id": source_id})
            return chart_id
        except AlreadyExists:
            snap = ref.get()
            return snap.to_dict()["chart_id"] if snap.exists else chart_id

    def chart_claim(self, owner_key, source_id):
        snap = self._col(CLAIMS).document(_claim_id(owner_key, source_id)).get()
        return snap.to_dict()["chart_id"] if snap.exists else None

    # ---- users --------------------------------------------------------
    def put_user(self, user):
        self._col(USERS).document(user.id).set(user.to_dict())

    def get_user(self, user_id):
        return self._get(USERS, user_id, User)

    def update_user(self, user_id, **fields):
        return self._update(USERS, user_id, User, fields)

    # ---- teams --------------------------------------------------------
    def put_team(self, team):
        self._col(TEAMS).document(team.id).set(team.to_dict())

    def get_team(self, team_id):
        return self._get(TEAMS, team_id, Team)

    def update_team(self, team_id, **fields):
        return self._update(TEAMS, team_id, Team, fields)

    def teams_for_user(self, user_id):
        q = self._where(TEAMS, "member_ids", "array_contains", user_id)
        docs = [Team.from_dict(d.to_dict()) for d in q.stream()]
        return sorted(docs, key=lambda t: t.created_at)

    def team_members(self, team_id):
        got = self.get_team(team_id)
        return list(got.member_ids) if got else []

    def add_member(self, team_id, user_id):
        # ArrayUnion, not read-modify-write: two people accepting one invite at
        # the same moment must not lose each other.
        return self._update(TEAMS, team_id, Team,
                            {"member_ids": self._fs.ArrayUnion([user_id])})

    def remove_member(self, team_id, user_id):
        return self._update(TEAMS, team_id, Team,
                            {"member_ids": self._fs.ArrayRemove([user_id])})

    # ---- invites ------------------------------------------------------
    def put_invite(self, invite):
        self._col(INVITES).document(invite.id).set(invite.to_dict())

    def get_invite(self, token):
        return self._get(INVITES, token, Invite)

    def update_invite(self, token, **fields):
        return self._update(INVITES, token, Invite, fields)

    def invites_for_team(self, team_id):
        q = self._where(INVITES, "team_id", "==", team_id)
        return [Invite.from_dict(d.to_dict()) for d in q.stream()]

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

    def list_playlist_index(self):
        docs = [PlaylistIndexEntry.from_dict(d.to_dict())
                for d in self._col(PLAYLIST_INDEX).stream()]
        return sorted(docs, key=lambda e: e.id)

    def put_playlist_index(self, entry):
        self._col(PLAYLIST_INDEX).document(entry.id).set(entry.to_dict())

    # ---- flags --------------------------------------------------------
    def put_flag(self, flag):
        self._col(FLAGS).document(flag.id).set(flag.to_dict())

    def list_flags(self, status=None, limit=200):
        # Ordered and limited in Firestore, not here: resolved flags pile up
        # for good, and streaming them all to return the newest 200 is paid
        # for per document. The status filter needs the composite index.
        q = self._col(FLAGS) if status is None else self._where(FLAGS, "status", "==", status)
        q = q.order_by("created_at", direction=self._fs.Query.DESCENDING).limit(limit)
        return [Flag.from_dict(d.to_dict()) for d in q.stream()]

    def resolve_flag(self, flag_id, now):
        ref = self._col(FLAGS).document(flag_id)
        snap = ref.get()
        if not snap.exists:
            return None
        flag = Flag.from_dict(snap.to_dict())
        if flag.status != "resolved":
            ref.update({"status": "resolved", "resolved_at": now})
            flag.status, flag.resolved_at = "resolved", now
        return flag

    def put_flag_if_absent(self, flag):
        # create() fails when the document exists: the check and the write are
        # one operation, so two calls racing cannot both win. Only that
        # failure means "it is there"; Aborted is a Conflict too, and taking it
        # for one would lose the flag.
        from google.api_core.exceptions import AlreadyExists

        try:
            self._col(FLAGS).document(flag.id).create(flag.to_dict())
        except AlreadyExists:
            return False
        return True

    # ---- reviews ------------------------------------------------------
    def put_review(self, review):
        self._col(REVIEWS).document(review.id).set(review.to_dict())

    def get_review(self, review_id):
        return self._get(REVIEWS, review_id, Review)

    def reviews_ready_between(self, t0, t1):
        q = (self._where(REVIEWS, "run_ready_at", ">=", t0)
             .where(filter=self._fs.FieldFilter("run_ready_at", "<", t1)))
        return [Review.from_dict(d.to_dict()) for d in q.stream()]

    def reviews_for_source(self, source_id):
        # One equality filter, sorted here: no composite index to keep.
        q = self._where(REVIEWS, "source_id", "==", source_id)
        docs = [Review.from_dict(d.to_dict()) for d in q.stream()]
        return sorted(docs, key=lambda r: r.reviewed_at, reverse=True)

    # ---- plays --------------------------------------------------------
    def put_play(self, play):
        self._col(PLAYS).document(play.id).set(play.to_dict())

    def get_play(self, user_id, source_id):
        return self._get(PLAYS, play_id(user_id, source_id), Play)

    def delete_play(self, user_id, source_id):
        ref = self._col(PLAYS).document(play_id(user_id, source_id))
        if not ref.get().exists:
            return False
        ref.delete()
        return True

    def plays_for_user(self, user_id, limit=500):
        # One equality filter, sorted here: no composite index to keep. All
        # of them, then the newest `limit` -- limiting in the query would keep
        # whichever came first in document-id order instead.
        q = self._where(PLAYS, "user_id", "==", user_id)
        docs = [Play.from_dict(d.to_dict()) for d in q.stream()]
        return sorted(docs, key=lambda p: p.updated_at, reverse=True)[:limit]

    # ---- rate limit ---------------------------------------------------
    def bump_rate_limit(self, ip_hash, now, hour_limit, day_limit):
        ref = self._col(RATES).document(ip_hash)
        transaction = self.db.transaction()
        fs = self._fs
        hb, db_ = _hour_bucket(now), _day_bucket(now)

        @fs.transactional
        def _bump(tx):
            snap = next(iter(tx.get(ref)))
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
        # chart_claims and share_slugs are derivable and deliberately absent;
        # import_all rebuilds both.
        return {"runs": dump(RUNS), "jobs": dump(JOBS), "sources": dump(SOURCES),
                "charts": dump(CHARTS), "workers": dump(WORKERS),
                "watched_playlists": dump(PLAYLISTS), "users": dump(USERS),
                "teams": dump(TEAMS), "invites": dump(INVITES), "flags": dump(FLAGS),
                "plays": dump(PLAYS), "reviews": dump(REVIEWS)}

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
        for d in data.get("reviews", []):
            self.put_review(Review.from_dict(d))
        # Claims are derivable, so they are not exported -- rebuilt here
        # instead, because a restore that lost them would start handing a team
        # a second chart for a game it already has.
        for d in data.get("charts", []):
            c = Chart.from_dict(d)
            key = c.team_id or c.owner_user_id
            if key and c.source_id and not c.superseded_by:
                self.claim_chart(key, c.source_id, c.id)
