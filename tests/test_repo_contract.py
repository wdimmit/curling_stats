"""One set of expectations, run against both repositories.

The in-memory repository is what almost every test uses, so anywhere it
disagrees with Firestore the suite is measuring a fiction. It has happened
already: `Transaction.get` yields snapshots rather than returning one, so the
versioned save and the rate limit both raised `AttributeError` in Firestore
while passing in memory.

So the contract is written once and run twice. The Firestore half needs the
emulator (`gcloud emulators firestore start`, with `FIRESTORE_EMULATOR_HOST`
set) and skips without it; the in-memory half always runs.
"""

import os
from datetime import datetime, timedelta, timezone

import pytest

from curling_score.service.records import (
    Chart, Job, Run, Source, WatchedPlaylist, Worker,
)
from curling_score.service.repo import MemoryRepo

T0 = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
EMULATOR = os.environ.get("FIRESTORE_EMULATOR_HOST")


def at(seconds):
    return T0 + timedelta(seconds=seconds)


def _firestore_repo():
    from google.cloud import firestore

    from curling_score.service.firestore_repo import FirestoreRepo

    client = firestore.Client(project=os.environ.get("GCP_PROJECT", "curling-test"))
    repo = FirestoreRepo(client=client)
    for col in ("vod_runs", "jobs", "sources", "charts", "share_slugs",
                "workers", "watched_playlists", "rate_limits"):
        for doc in client.collection(col).stream():
            doc.reference.delete()
    return repo


@pytest.fixture(params=["memory", "firestore"])
def repo(request):
    if request.param == "memory":
        return MemoryRepo()
    if not EMULATOR:
        pytest.skip("needs the Firestore emulator (FIRESTORE_EMULATOR_HOST)")
    return _firestore_repo()


def run(**kw):
    base = dict(id="r_1", video_id="vidA", processing_version="p+m",
                status="queued", created_at=T0,
                games=[{"index": 0, "start_s": 0.0, "end_s": 6000.0, "ends": 7}])
    base.update(kw)
    return Run(**base)


def job(**kw):
    base = dict(id="j_1", run_id="r_1", state="queued", created_at=T0, run_after=T0)
    base.update(kw)
    return Job(**base)


def chart(**kw):
    base = dict(id="c_1", share_slug="s_1", video_id="vidA", run_id="r_1",
                created_at=T0, updated_at=T0)
    base.update(kw)
    return Chart(**base)


class TestRuns:
    def test_put_get_update(self, repo):
        repo.put_run(run())
        assert repo.get_run("r_1").video_id == "vidA"
        assert repo.get_run("missing") is None
        got = repo.update_run("r_1", status="ready", title="Sheet 2")
        assert got.status == "ready" and got.title == "Sheet 2"
        assert repo.get_run("r_1").status == "ready"

    def test_games_survive_the_round_trip(self, repo):
        repo.put_run(run())
        assert repo.get_run("r_1").games == [
            {"index": 0, "start_s": 0.0, "end_s": 6000.0, "ends": 7}]

    def test_runs_for_video_is_newest_first(self, repo):
        repo.put_run(run(id="r_old", created_at=at(0)))
        repo.put_run(run(id="r_new", created_at=at(100)))
        repo.put_run(run(id="r_other", video_id="vidB", created_at=at(50)))
        assert [r.id for r in repo.runs_for_video("vidA")] == ["r_new", "r_old"]

    def test_list_runs_filters_by_status(self, repo):
        repo.put_run(run(id="r_q", status="queued"))
        repo.put_run(run(id="r_r", status="ready"))
        assert {r.id for r in repo.list_runs()} == {"r_q", "r_r"}
        assert [r.id for r in repo.list_runs(status="ready")] == ["r_r"]


class TestJobsAndClaiming:
    def test_claim_gives_a_job_to_exactly_one_worker(self, repo):
        repo.put_run(run())
        repo.put_job(job())
        first = repo.claim_job("w1", at(1))
        second = repo.claim_job("w2", at(1))
        assert first is not None and first.id == "j_1"
        assert first.worker_id == "w1" and first.state == "running"
        assert first.attempts == 1
        assert second is None
        assert repo.get_run("r_1").status == "processing"

    def test_claim_takes_the_oldest_first(self, repo):
        repo.put_run(run())
        repo.put_job(job(id="j_late", created_at=at(50), run_after=at(50)))
        repo.put_job(job(id="j_early", created_at=at(0), run_after=at(0)))
        assert repo.claim_job("w", at(100)).id == "j_early"

    def test_a_job_scheduled_for_later_is_not_claimed(self, repo):
        repo.put_run(run())
        repo.put_job(job(run_after=at(600)))
        assert repo.claim_job("w", at(10)) is None
        assert repo.claim_job("w", at(601)) is not None

    def test_nothing_queued_is_none(self, repo):
        assert repo.claim_job("w", T0) is None

    def test_an_expired_lease_returns_to_the_queue(self, repo):
        repo.put_run(run())
        repo.put_job(job())
        repo.claim_job("w", at(0), lease_s=600)
        assert repo.requeue_expired(at(599)) == 0
        assert repo.requeue_expired(at(601)) == 1
        back = repo.get_job("j_1")
        assert back.state == "queued" and back.worker_id is None
        assert repo.get_run("r_1").status == "queued"
        assert repo.claim_job("w2", at(602)).attempts == 2

    def test_update_job_and_job_for_run(self, repo):
        repo.put_job(job())
        repo.update_job("j_1", phase="detect", fraction=0.5)
        assert repo.get_job("j_1").phase == "detect"
        assert repo.job_for_run("r_1").id == "j_1"
        assert repo.job_for_run("nope") is None

    def test_counting_and_position(self, repo):
        repo.put_run(run())
        for i in range(3):
            repo.put_job(job(id=f"j_{i}", created_at=at(i), run_after=at(i)))
        assert repo.count_queued() == 3
        assert repo.queue_position("j_0") == 0
        assert repo.queue_position("j_2") == 2
        assert repo.queue_position("nope") is None


class TestCharts:
    def test_put_get_and_share_lookup(self, repo):
        repo.put_chart(chart())
        assert repo.get_chart("c_1").video_id == "vidA"
        assert repo.chart_by_share("s_1").id == "c_1"
        assert repo.chart_by_share("missing") is None
        assert repo.get_chart("missing") is None

    def test_charts_for_run(self, repo):
        repo.put_chart(chart(id="c_1", share_slug="s_1"))
        repo.put_chart(chart(id="c_2", share_slug="s_2"))
        repo.put_chart(chart(id="c_3", share_slug="s_3", run_id="r_other"))
        assert {c.id for c in repo.charts_for_run("r_1")} == {"c_1", "c_2"}

    def test_the_versioned_save(self, repo):
        repo.put_chart(chart())
        ok, version, _ = repo.save_overrides("c_1", {"0.1.1": {"user_score": 3}}, 0, at(1))
        assert ok and version == 1
        assert repo.get_chart("c_1").overrides == {"0.1.1": {"user_score": 3}}
        assert repo.get_chart("c_1").overrides_version == 1

    def test_a_stale_save_is_refused_with_the_current_state(self, repo):
        repo.put_chart(chart())
        repo.save_overrides("c_1", {"a": {"x": 1}}, 0, at(1))
        ok, version, current = repo.save_overrides("c_1", {"b": {"y": 2}}, 0, at(2))
        assert not ok and version == 1 and current == {"a": {"x": 1}}
        assert repo.get_chart("c_1").overrides == {"a": {"x": 1}}

    def test_an_unconditional_save_always_lands(self, repo):
        repo.put_chart(chart())
        repo.save_overrides("c_1", {"a": {}}, 0, at(1))
        ok, version, _ = repo.save_overrides("c_1", {"b": {}}, None, at(2))
        assert ok and version == 2

    def test_saving_to_a_missing_chart_raises(self, repo):
        with pytest.raises(KeyError):
            repo.save_overrides("nope", {}, None, T0)


class TestSources:
    def test_put_get_update_and_by_video(self, repo):
        s = Source(id="s_1", video_id="vidA", game_start_s=0.0, game_end_s=6000.0,
                   current_run_id="r_1", game_index=0, created_at=T0, league="Tue")
        repo.put_source(s)
        assert repo.get_source("s_1").league == "Tue"
        repo.update_source("s_1", current_run_id="r_2", game_index=1)
        assert repo.get_source("s_1").current_run_id == "r_2"
        assert [x.id for x in repo.sources_for_video("vidA")] == ["s_1"]
        assert repo.sources_for_video("vidB") == []
        assert [x.id for x in repo.list_sources(league="Tue")] == ["s_1"]
        assert repo.list_sources(league="Other") == []


class TestWorkersAndPlaylists:
    def test_heartbeat_upserts(self, repo):
        repo.heartbeat(Worker(id="home", last_seen_at=T0, gpu="3070"))
        repo.heartbeat(Worker(id="home", last_seen_at=at(60), gpu="3070"))
        workers = repo.workers()
        assert len(workers) == 1 and workers[0].last_seen_at.timestamp() == at(60).timestamp()

    def test_playlists_crud(self, repo):
        repo.put_playlist(WatchedPlaylist(id="p_1", playlist_id="PL", label="Tue",
                                          created_at=T0))
        assert repo.get_playlist("p_1").label == "Tue"
        assert [p.id for p in repo.list_playlists()] == ["p_1"]
        repo.update_playlist("p_1", enabled=False, last_seen_video_ids=["a", "b"])
        assert repo.get_playlist("p_1").enabled is False
        assert repo.get_playlist("p_1").last_seen_video_ids == ["a", "b"]
        repo.delete_playlist("p_1")
        assert repo.get_playlist("p_1") is None


class TestRateLimit:
    def test_hour_and_day_limits(self, repo):
        assert [repo.bump_rate_limit("ip", at(i), 3, 5) for i in range(4)] == [
            True, True, True, False]

    def test_a_new_hour_resets_the_hour_but_not_the_day(self, repo):
        for i in range(3):
            repo.bump_rate_limit("ip", at(i), 3, 5)
        later = at(3700)
        assert repo.bump_rate_limit("ip", later, 3, 5)
        assert repo.bump_rate_limit("ip", later, 3, 5)
        assert not repo.bump_rate_limit("ip", later, 3, 5)

    def test_addresses_are_counted_separately(self, repo):
        assert repo.bump_rate_limit("a", T0, 1, 1)
        assert repo.bump_rate_limit("b", T0, 1, 1)
        assert not repo.bump_rate_limit("a", T0, 1, 1)


class TestBackup:
    def test_export_then_import_into_a_fresh_store(self, repo):
        repo.put_run(run())
        repo.put_job(job())
        repo.put_chart(chart(overrides={"0.1.1": {"user_score": 4}}, overrides_version=1))
        repo.put_playlist(WatchedPlaylist(id="p_1", playlist_id="PL", label="Tue",
                                          created_at=T0))
        data = repo.export_all()
        assert {r["id"] for r in data["runs"]} == {"r_1"}
        assert {c["id"] for c in data["charts"]} == {"c_1"}

        fresh = MemoryRepo()
        fresh.import_all(data)
        assert fresh.get_chart("c_1").overrides == {"0.1.1": {"user_score": 4}}
        assert fresh.chart_by_share("s_1").id == "c_1"
        assert fresh.get_run("r_1").games == run().games
        assert fresh.get_playlist("p_1").label == "Tue"
