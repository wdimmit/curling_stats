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
    Chart, Invite, Job, Run, Source, Team, User, WatchedPlaylist, Worker,
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
                "workers", "watched_playlists", "rate_limits",
                "users", "teams", "invites", "chart_claims"):
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


class TestMergeOverrides:
    """Per-key merging, so two people charting one game cannot erase each other."""

    def test_a_dotted_key_stays_one_flat_key(self, repo):
        """Override keys are "<game>.<end>.<shot>".

        Firestore reads a dot in a field path as a step into a nested map, so
        the obvious update writes {"0": {"1": {"1": ...}}} and every dotted
        lookup downstream -- apply_overrides included -- then finds nothing and
        the chart reads as ungraded. The key must survive whole.
        """
        repo.put_chart(chart())
        repo.merge_overrides("c_1", {"0.1.1": {"user_score": 3}}, [], {}, at(1))
        assert repo.get_chart("c_1").overrides == {"0.1.1": {"user_score": 3}}

    def test_a_plain_key_works_too(self, repo):
        """The one that passes even with the nesting bug -- both, or neither proves much."""
        repo.put_chart(chart())
        repo.merge_overrides("c_1", {"plain": {"x": 1}}, [], {}, at(1))
        assert repo.get_chart("c_1").overrides == {"plain": {"x": 1}}

    def test_merging_another_key_leaves_the_first_alone(self, repo):
        """The whole point: disjoint edits do not collide."""
        repo.put_chart(chart())
        repo.merge_overrides("c_1", {"0.1.1": {"user_score": 3}}, [], {}, at(1))
        version, merged, ok = repo.merge_overrides("c_1", {"0.1.2": {"user_score": 4}},
                                                   [], {}, at(2))
        assert ok and version == 2
        assert merged == {"0.1.1": {"user_score": 3}, "0.1.2": {"user_score": 4}}
        assert repo.get_chart("c_1").overrides == merged

    def test_merging_the_same_key_replaces_the_shot(self, repo):
        """A shot moves as a unit: stones and delivered_stone_index index each other."""
        repo.put_chart(chart())
        repo.merge_overrides("c_1", {"0.1.1": {"user_score": 3, "note": "wide"}},
                             [], {}, at(1))
        _v, merged, _ok = repo.merge_overrides("c_1", {"0.1.1": {"user_score": 1}},
                                               [], {}, at(2))
        assert merged == {"0.1.1": {"user_score": 1}}

    def test_removing_a_key_removes_its_meta_too(self, repo):
        repo.put_chart(chart())
        repo.merge_overrides("c_1", {"0.1.1": {"user_score": 3}}, [],
                             {"0.1.1": {"by": "u1", "at": "2026-09-11T12:00:00+00:00"}}, at(1))
        assert repo.get_chart("c_1").overrides_meta["0.1.1"]["by"] == "u1"
        _v, merged, _ok = repo.merge_overrides("c_1", {}, ["0.1.1"], {}, at(2))
        assert merged == {}
        got = repo.get_chart("c_1")
        assert got.overrides == {} and got.overrides_meta == {}

    def test_meta_lands_under_the_same_dotted_key(self, repo):
        repo.put_chart(chart())
        repo.merge_overrides("c_1", {"0.1.1": {"user_score": 3}}, [],
                             {"0.1.1": {"by": "u1", "at": "2026-09-11T12:00:00+00:00"}}, at(1))
        assert list(repo.get_chart("c_1").overrides_meta) == ["0.1.1"]

    def test_removing_a_key_that_is_not_there_is_fine(self, repo):
        repo.put_chart(chart())
        _v, merged, ok = repo.merge_overrides("c_1", {}, ["nope"], {}, at(1))
        assert ok and merged == {}

    def test_the_version_counts_up_alongside_save_overrides(self, repo):
        repo.put_chart(chart())
        repo.save_overrides("c_1", {"a": {"x": 1}}, 0, at(1))
        version, merged, ok = repo.merge_overrides("c_1", {"b": {"y": 2}}, [], {}, at(2))
        assert ok and version == 2 and merged == {"a": {"x": 1}, "b": {"y": 2}}
        ok2, version2, _ = repo.save_overrides("c_1", {"c": {}}, 2, at(3))
        assert ok2 and version2 == 3

    def test_the_merged_map_is_a_copy(self, repo):
        """Handing out the store's own dict is a divergence Firestore cannot have."""
        repo.put_chart(chart())
        _v, merged, _ok = repo.merge_overrides("c_1", {"a": {"x": 1}}, [], {}, at(1))
        merged["a"]["x"] = 999
        assert repo.get_chart("c_1").overrides == {"a": {"x": 1}}

    def test_merging_into_a_missing_chart_raises(self, repo):
        with pytest.raises(KeyError):
            repo.merge_overrides("nope", {"a": {}}, [], {}, T0)


def user(**kw):
    base = dict(id="fbuid1", created_at=T0, email="a@example.org", email_verified=True)
    base.update(kw)
    return User(**base)


def team(**kw):
    base = dict(id="t_1", name="Thistles", owner_user_id="fbuid1", created_at=T0,
                member_ids=["fbuid1"])
    base.update(kw)
    return Team(**base)


class TestUsers:
    def test_put_get_and_update(self, repo):
        repo.put_user(user())
        assert repo.get_user("fbuid1").email == "a@example.org"
        assert repo.get_user("nope") is None
        repo.update_user("fbuid1", name="Sarah", last_seen_at=at(60))
        got = repo.get_user("fbuid1")
        assert got.name == "Sarah" and got.last_seen_at == at(60)

    def test_putting_twice_is_idempotent(self, repo):
        """Two requests can race the first-sight upsert; neither may fail."""
        repo.put_user(user())
        repo.put_user(user(name="Sarah"))
        assert repo.get_user("fbuid1").name == "Sarah"


class TestTeams:
    def test_put_get_and_update(self, repo):
        repo.put_team(team())
        assert repo.get_team("t_1").name == "Thistles"
        assert repo.get_team("nope") is None
        repo.update_team("t_1", name="Renamed")
        assert repo.get_team("t_1").name == "Renamed"

    def test_members_come_and_go(self, repo):
        repo.put_team(team())
        repo.add_member("t_1", "fbuid2")
        assert sorted(repo.team_members("t_1")) == ["fbuid1", "fbuid2"]
        repo.remove_member("t_1", "fbuid2")
        assert repo.team_members("t_1") == ["fbuid1"]

    def test_adding_the_same_member_twice_is_idempotent(self, repo):
        """Two people can accept one invite at the same moment."""
        repo.put_team(team())
        repo.add_member("t_1", "fbuid2")
        repo.add_member("t_1", "fbuid2")
        assert sorted(repo.team_members("t_1")) == ["fbuid1", "fbuid2"]

    def test_removing_someone_who_is_not_a_member_is_a_no_op(self, repo):
        repo.put_team(team())
        repo.remove_member("t_1", "stranger")
        assert repo.team_members("t_1") == ["fbuid1"]

    def test_teams_for_user_finds_every_one(self, repo):
        repo.put_team(team())
        repo.put_team(team(id="t_2", name="Rocks", member_ids=["fbuid1", "fbuid2"]))
        repo.put_team(team(id="t_3", name="Other", member_ids=["fbuid9"]))
        assert sorted(t.id for t in repo.teams_for_user("fbuid1")) == ["t_1", "t_2"]
        assert [t.id for t in repo.teams_for_user("fbuid2")] == ["t_2"]
        assert repo.teams_for_user("nobody") == []


class TestInvites:
    def _invite(self, **kw):
        base = dict(id="i_1", team_id="t_1", created_by_user_id="fbuid1", created_at=T0)
        base.update(kw)
        return Invite(**base)

    def test_put_get_and_update(self, repo):
        repo.put_invite(self._invite())
        assert repo.get_invite("i_1").team_id == "t_1"
        assert repo.get_invite("nope") is None
        repo.update_invite("i_1", uses=1)
        assert repo.get_invite("i_1").uses == 1

    def test_invites_for_team(self, repo):
        repo.put_invite(self._invite())
        repo.put_invite(self._invite(id="i_2"))
        repo.put_invite(self._invite(id="i_3", team_id="t_9"))
        assert sorted(i.id for i in repo.invites_for_team("t_1")) == ["i_1", "i_2"]


class TestChartClaims:
    """One chart per game per team, decided by whoever gets there first."""

    def test_the_first_claim_wins_and_later_ones_are_told_so(self, repo):
        assert repo.claim_chart("t_1", "s_1", "c_1") == "c_1"
        assert repo.claim_chart("t_1", "s_1", "c_2") == "c_1"

    def test_reading_a_claim_back(self, repo):
        repo.claim_chart("t_1", "s_1", "c_1")
        assert repo.chart_claim("t_1", "s_1") == "c_1"
        assert repo.chart_claim("t_1", "s_other") is None
        assert repo.chart_claim("t_other", "s_1") is None

    def test_different_owners_of_the_same_game_do_not_collide(self, repo):
        assert repo.claim_chart("t_1", "s_1", "c_1") == "c_1"
        assert repo.claim_chart("t_2", "s_1", "c_2") == "c_2"


class TestChartsByOwner:
    def test_by_team_and_by_user(self, repo):
        repo.put_chart(chart(id="c_1", share_slug="sh_1", team_id="t_1"))
        repo.put_chart(chart(id="c_2", share_slug="sh_2", team_id="t_1"))
        repo.put_chart(chart(id="c_3", share_slug="sh_3", owner_user_id="fbuid1"))
        repo.put_chart(chart(id="c_4", share_slug="sh_4"))
        assert sorted(c.id for c in repo.charts_for_team("t_1")) == ["c_1", "c_2"]
        assert [c.id for c in repo.charts_for_owner("fbuid1")] == ["c_3"]
        assert repo.charts_for_team("t_none") == []


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
