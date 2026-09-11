"""FirestoreRepo against the emulator: the transactions, for real.

Runs only when ``FIRESTORE_EMULATOR_HOST`` is set (``gcloud emulators
firestore start`` or the compose file's ``firestore`` service). Everything else
about the repo is covered by the in-memory twin; what only the emulator can show
is that the claim and the versioned save really are atomic in Firestore terms.
"""

import os
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("FIRESTORE_EMULATOR_HOST"),
    reason="needs the Firestore emulator (FIRESTORE_EMULATOR_HOST)")

T0 = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def repo():
    from google.cloud import firestore

    from curling_score.service.firestore_repo import FirestoreRepo

    client = firestore.Client(project=os.environ.get("GCP_PROJECT", "test-project"))
    r = FirestoreRepo(client=client)
    yield r
    for col in ("vod_runs", "jobs", "sources", "charts", "share_slugs",
                "workers", "watched_playlists", "rate_limits"):
        for doc in client.collection(col).stream():
            doc.reference.delete()


def test_claim_hands_out_each_job_once(repo):
    from curling_score.service.records import Job, Run

    repo.put_run(Run(id="r1", video_id="v", processing_version="p", status="queued", created_at=T0))
    repo.put_job(Job(id="j1", run_id="r1", state="queued", created_at=T0, run_after=T0))
    a = repo.claim_job("w1", T0 + timedelta(seconds=1))
    b = repo.claim_job("w2", T0 + timedelta(seconds=1))
    assert a is not None and a.id == "j1" and a.worker_id == "w1"
    assert b is None
    assert repo.get_run("r1").status == "processing"
    assert repo.requeue_expired(T0 + timedelta(seconds=700)) == 1
    assert repo.get_job("j1").state == "queued"


def test_versioned_save(repo):
    from curling_score.service.records import Chart

    repo.put_chart(Chart(id="c1", share_slug="s1", video_id="v", run_id="r1",
                         created_at=T0, updated_at=T0))
    ok, v, _ = repo.save_overrides("c1", {"a": {"x": 1}}, 0, T0)
    assert ok and v == 1
    ok, v, cur = repo.save_overrides("c1", {"b": {}}, 0, T0)
    assert not ok and v == 1 and cur == {"a": {"x": 1}}
    assert repo.chart_by_share("s1").id == "c1"


def test_rate_limit_counts(repo):
    assert repo.bump_rate_limit("ip", T0, 2, 10)
    assert repo.bump_rate_limit("ip", T0, 2, 10)
    assert not repo.bump_rate_limit("ip", T0, 2, 10)
