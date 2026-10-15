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
    Chart, ClipJob, Flag, Invite, Job, Play, PlaylistIndexEntry, Review, Run, Source, Team,
    TeamNameEdit, User, WatchedPlaylist, Worker, play_id, review_id,
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
                "users", "teams", "invites", "chart_claims", "flags", "yt_playlists",
                "plays", "reviews", "practice_watches", "practice_sessions",
                "clip_jobs", "team_name_edits"):
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

    def test_a_live_job_is_claimed_before_older_recordings(self, repo):
        repo.put_run(run())
        repo.put_run(run(id="r_live"))
        repo.put_job(job(id="j_vod", created_at=at(0), run_after=at(0)))
        repo.put_job(job(id="j_live", run_id="r_live", kind="live",
                         created_at=at(50), run_after=at(50)))
        assert repo.claim_job("w", at(100), kinds=("live", "vod")).id == "j_live"
        assert repo.claim_job("w", at(100), kinds=("live", "vod")).id == "j_vod"

    def test_a_practice_job_goes_only_to_a_worker_that_asks_for_practice(self, repo):
        repo.put_run(run(id="r_p", kind="practice"))
        repo.put_job(job(id="j_p", run_id="r_p", kind="practice"))
        assert repo.claim_job("w", at(10), kinds=("vod",)) is None
        assert repo.claim_job("w", at(10), kinds=("live",)) is None
        assert repo.claim_job("w", at(10), kinds=("live", "practice")).id == "j_p"

    def test_a_worker_asking_only_for_live_work_gets_no_recording(self, repo):
        repo.put_run(run())
        repo.put_job(job())
        assert repo.claim_job("w", at(10), kinds=("live",)) is None
        assert repo.claim_job("w", at(10), kinds=("live", "vod")).id == "j_1"

    def test_a_worker_that_never_asks_for_live_work_never_gets_it(self, repo):
        repo.put_run(run(id="r_live"))
        repo.put_job(job(id="j_live", run_id="r_live", kind="live"))
        assert repo.claim_job("w", at(10)) is None
        assert repo.claim_job("w", at(10), kinds=("vod",)) is None
        assert repo.claim_job("w", at(10), kinds=("live",)).id == "j_live"

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

    def test_a_role_is_kept(self, repo):
        repo.put_user(user())
        assert repo.get_user("fbuid1").role is None
        repo.update_user("fbuid1", role="validated")
        assert repo.get_user("fbuid1").role == "validated"
        repo.update_user("fbuid1", role=None)
        assert repo.get_user("fbuid1").role is None

    def test_listed_newest_first(self, repo):
        repo.put_user(user(id="u1", created_at=T0))
        repo.put_user(user(id="u3", created_at=at(120)))
        repo.put_user(user(id="u2", created_at=at(60)))
        assert [u.id for u in repo.list_users()] == ["u3", "u2", "u1"]
        assert [u.id for u in repo.list_users(limit=2)] == ["u3", "u2"]


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

    def test_short_handed_round_trips(self, repo):
        repo.put_source(Source(id="s_3", video_id="vidC", game_start_s=0.0, game_end_s=6000.0,
                               current_run_id="r_1", game_index=0, created_at=T0))
        assert repo.get_source("s_3").short_handed is None
        repo.update_source("s_3", short_handed=["red", "yellow"])
        assert repo.get_source("s_3").short_handed == ["red", "yellow"]
        repo.update_source("s_3", short_handed=None)
        assert repo.get_source("s_3").short_handed is None

    def test_listed_newest_first_whatever_the_ids(self, repo):
        # Source ids are random slugs, so id order says nothing about age.
        # Here it runs the other way: the newest games sort last by id, where
        # a fetch of the first few ids and a sort afterwards never sees them.
        for i in range(8):
            repo.put_source(Source(id=f"s_{i}", video_id=f"vid{i}", game_start_s=0.0,
                                   game_end_s=6000.0, current_run_id=f"r_{i}", game_index=0,
                                   created_at=T0, played_at=at(i * 86400),
                                   league="Tue" if i % 2 else "Wed"))
        # No date read off the video: it is as old as when it arrived.
        repo.put_source(Source(id="s_8", video_id="vid8", game_start_s=0.0, game_end_s=6000.0,
                               current_run_id="r_8", game_index=0,
                               created_at=at(10 * 86400), league="Tue"))
        assert [x.id for x in repo.list_sources(limit=3)] == ["s_8", "s_7", "s_6"]
        assert [x.id for x in repo.list_sources(league="Tue", limit=2)] == ["s_8", "s_7"]
        assert [x.id for x in repo.list_sources(league="Wed", limit=2)] == ["s_6", "s_4"]
        assert len(repo.list_sources()) == 9

    def test_played_between_two_times(self, repo):
        for i in range(6):
            repo.put_source(Source(id=f"s_{i}", video_id=f"vid{i}", game_start_s=0.0,
                                   game_end_s=6000.0, current_run_id=f"r_{i}", game_index=0,
                                   created_at=T0, played_at=at(i * 86400)))
        # Undated, so placed by when it arrived: between days 2 and 3.
        repo.put_source(Source(id="s_u", video_id="vidU", game_start_s=0.0, game_end_s=6000.0,
                               current_run_id="r_u", game_index=0, created_at=at(2.5 * 86400)))
        ids = lambda xs: [x.id for x in xs]
        assert ids(repo.sources_played()) == ["s_5", "s_4", "s_3", "s_u", "s_2", "s_1", "s_0"]
        # since is inclusive, before is not -- so a cursor never repeats a game.
        assert ids(repo.sources_played(since=at(2 * 86400), before=at(4 * 86400))) == [
            "s_3", "s_u", "s_2"]
        assert ids(repo.sources_played(before=at(3 * 86400), limit=1)) == ["s_u"]
        assert ids(repo.sources_played(before=at(2 * 86400), limit=1)) == ["s_1"]
        assert ids(repo.sources_played(since=at(5 * 86400))) == ["s_5"]
        assert repo.sources_played(before=at(0)) == []

    def test_the_report_fields_round_trip(self, repo):
        repo.put_source(Source(id="s_1", video_id="vidA", game_start_s=0.0, game_end_s=6000.0,
                               current_run_id="r_1", game_index=0, created_at=T0))
        summary = {"run_id": "r_1", "play_start_s": None, "ends": 8, "red": 1252.85,
                   "yellow": 1872.16, "measured_shots": 120, "unmeasured_shots": 8,
                   "estimated_shots": 1}
        repo.update_source("s_1", thinking=summary, playlist_id="PL",
                           playlist_title="2026 Spring Skip's Choice League")
        got = repo.get_source("s_1")
        assert got.thinking == summary
        assert (got.playlist_id, got.playlist_title) == ("PL", "2026 Spring Skip's Choice League")


    def _source(self, repo):
        repo.put_source(Source(id="s_1", video_id="vidA", game_start_s=0.0, game_end_s=6000.0,
                               current_run_id="r_1", game_index=0, created_at=T0))

    def test_an_entered_score_round_trips(self, repo):
        self._source(repo)
        assert repo.get_source("s_1").entered_scores == {}
        entry = {"red": 0, "yellow": 3, "by": "uid-sarah", "at": "2026-10-01T12:00:00+00:00"}
        got = repo.set_entered_score("s_1", 4, entry)
        assert got.entered_scores == {"4": entry}
        assert repo.get_source("s_1").entered_scores == {"4": entry}

    def test_two_ends_are_kept_apart(self, repo):
        """Two people filling different ends at once must not overwrite each other."""
        self._source(repo)
        repo.set_entered_score("s_1", 3, {"red": 1, "yellow": 0})
        repo.set_entered_score("s_1", 4, {"red": 0, "yellow": 2})
        assert set(repo.get_source("s_1").entered_scores) == {"3", "4"}

    def test_clearing_one_end_leaves_the_others(self, repo):
        self._source(repo)
        repo.set_entered_score("s_1", 3, {"red": 1, "yellow": 0})
        repo.set_entered_score("s_1", 4, {"red": 0, "yellow": 2})
        repo.set_entered_score("s_1", 3, None)
        assert repo.get_source("s_1").entered_scores == {"4": {"red": 0, "yellow": 2}}

    def test_an_unknown_game_is_none(self, repo):
        assert repo.set_entered_score("s_nope", 1, {"red": 1, "yellow": 0}) is None

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

    def test_the_playlist_index_upserts(self, repo):
        assert repo.list_playlist_index() == []
        repo.put_playlist_index(PlaylistIndexEntry(id="PL_b", channel_id="UC", title="B",
                                                   item_count=1, scanned_at=T0,
                                                   video_ids=["v1"]))
        repo.put_playlist_index(PlaylistIndexEntry(id="PL_a", channel_id="UC", title="A",
                                                   item_count=0, scanned_at=T0))
        repo.put_playlist_index(PlaylistIndexEntry(id="PL_b", channel_id="UC", title="B2",
                                                   item_count=2, scanned_at=at(60),
                                                   video_ids=["v1", "v2"]))
        got = repo.list_playlist_index()
        assert [e.id for e in got] == ["PL_a", "PL_b"]
        assert got[1].title == "B2" and got[1].video_ids == ["v1", "v2"]
        assert got[1].scanned_at.timestamp() == at(60).timestamp()


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


class TestFlags:
    def flag(self, **kw):
        base = dict(id="f_1", created_at=T0, note="the house is wrong",
                    where={"link": "g", "source_id": "s_1", "run_id": "r_1"},
                    place={"end": 4, "rock": 16, "key": "0.4.16"})
        base.update(kw)
        return Flag(**base)

    def test_newest_first_and_filtered_by_status(self, repo):
        repo.put_flag(self.flag(id="f_1", created_at=T0))
        repo.put_flag(self.flag(id="f_2", created_at=at(60)))
        repo.put_flag(self.flag(id="f_3", created_at=at(30), status="resolved",
                                resolved_at=at(40)))
        assert [f.id for f in repo.list_flags("open")] == ["f_2", "f_1"]
        assert [f.id for f in repo.list_flags("resolved")] == ["f_3"]
        assert [f.id for f in repo.list_flags()] == ["f_2", "f_3", "f_1"]
        assert [f.id for f in repo.list_flags(limit=1)] == ["f_2"]

    def test_resolving_once_and_only_once(self, repo):
        repo.put_flag(self.flag())
        got = repo.resolve_flag("f_1", at(90))
        assert got.status == "resolved"
        assert got.resolved_at.timestamp() == at(90).timestamp()
        assert repo.list_flags("open") == []
        again = repo.resolve_flag("f_1", at(200))
        assert again.resolved_at.timestamp() == at(90).timestamp()
        assert repo.resolve_flag("f_nope", at(90)) is None

    def test_the_maps_come_back_as_they_went_in(self, repo):
        repo.put_flag(self.flag(user={"uid": "u1", "email": "a@b.c"}, overrides_version=3))
        f = repo.list_flags()[0]
        assert f.where["source_id"] == "s_1" and f.place["rock"] == 16
        assert f.user == {"uid": "u1", "email": "a@b.c"} and f.overrides_version == 3


class TestTeamNameEdits:
    def edit(self, **kw):
        base = dict(id="tn_1", source_id="s_1", user_id="u1", at=T0,
                    before={"red": None}, after={"red": "Rice"})
        base.update(kw)
        return TeamNameEdit(**base)

    def test_newest_first_one_game_or_all(self, repo):
        repo.put_team_name_edit(self.edit(id="tn_1", at=T0))
        repo.put_team_name_edit(self.edit(id="tn_2", at=at(60), source_id="s_2"))
        repo.put_team_name_edit(self.edit(id="tn_3", at=at(30)))
        assert [e.id for e in repo.team_name_edits()] == ["tn_2", "tn_3", "tn_1"]
        assert [e.id for e in repo.team_name_edits("s_1")] == ["tn_3", "tn_1"]
        assert [e.id for e in repo.team_name_edits("s_1", limit=1)] == ["tn_3"]
        assert [e.id for e in repo.team_name_edits(limit=1)] == ["tn_2"]
        assert repo.team_name_edits("s_nope") == []

    def test_a_cleared_name_comes_back_as_none(self, repo):
        repo.put_team_name_edit(self.edit(before={"red": "Rice", "yellow": "Casey"},
                                          after={"red": None, "yellow": "Casey 2"}))
        e = repo.team_name_edits()[0]
        assert e.before == {"red": "Rice", "yellow": "Casey"}
        assert e.after == {"red": None, "yellow": "Casey 2"}
        assert (e.user_id, e.at.timestamp()) == ("u1", T0.timestamp())


class TestReviews:
    def review(self, **kw):
        base = dict(id=review_id("s_1", "r_1"), source_id="s_1", run_id="r_1",
                    reviewed_at=T0, format="fours", run_ready_at=T0,
                    ends=[{"number": 1, "rocks": 16, "broom": 15, "split": 16, "line": 16,
                           "release": 16}],
                    findings=[{"check": "odd_split", "strength": "weak", "end": 1, "rock": 9,
                               "detail": "split 23.4 s", "t_video_s": 1234.5}])
        base.update(kw)
        return Review(**base)

    def test_put_and_get(self, repo):
        repo.put_review(self.review())
        got = repo.get_review("s_1_r_1")
        assert got.ends[0]["broom"] == 15 and got.findings[0]["rock"] == 9
        assert repo.get_review("s_9_r_9") is None

    def test_between_is_half_open_on_when_the_run_finished(self, repo):
        for i, t in enumerate((-60, 0, 60)):
            repo.put_review(self.review(id=f"s_{i}_r_1", source_id=f"s_{i}",
                                        run_ready_at=at(t)))
        got = repo.reviews_ready_between(at(-60), at(60))
        assert sorted(r.id for r in got) == ["s_0_r_1", "s_1_r_1"]

    def test_for_a_source_newest_first(self, repo):
        repo.put_review(self.review(id="s_1_r_1", reviewed_at=at(0)))
        repo.put_review(self.review(id="s_1_r_2", run_id="r_2", reviewed_at=at(60)))
        repo.put_review(self.review(id="s_2_r_1", source_id="s_2"))
        assert [r.id for r in repo.reviews_for_source("s_1")] == ["s_1_r_2", "s_1_r_1"]


class TestRunsReadySince:
    def test_only_runs_that_finished_since_oldest_first(self, repo):
        repo.put_run(run(id="r_never"))
        repo.put_run(run(id="r_old", status="ready", ready_at=at(-10)))
        repo.put_run(run(id="r_b", status="ready", ready_at=at(20)))
        repo.put_run(run(id="r_a", status="ready", ready_at=at(10)))
        assert [r.id for r in repo.runs_ready_since(at(0))] == ["r_a", "r_b"]


class TestFlagIfAbsent:
    def test_the_first_write_wins_and_a_resolved_flag_stays_resolved(self, repo):
        f = Flag(id="fa_1", created_at=T0, note="first", origin="auto",
                 findings=[{"check": "same_house"}])
        assert repo.put_flag_if_absent(f) is True
        repo.resolve_flag("fa_1", at(60))
        again = Flag(id="fa_1", created_at=at(120), note="second", origin="auto")
        assert repo.put_flag_if_absent(again) is False
        (got,) = repo.list_flags()
        assert (got.note, got.status, got.origin) == ("first", "resolved", "auto")
        assert got.findings == [{"check": "same_house"}]

    def test_a_flag_from_before_origins_reads_as_a_viewer_flag(self, repo):
        repo.put_flag(Flag(id="f_1", created_at=T0, note="n"))
        (got,) = repo.list_flags()
        assert got.origin == "viewer" and got.findings == []


class TestPlays:
    def play(self, **kw):
        base = dict(user_id="u1", source_id="src_1", color="red", slot=4,
                    created_at=T0, updated_at=T0)
        base.update(kw)
        return Play(id=play_id(base["user_id"], base["source_id"]), **base)

    def test_everyones_plays(self, repo):
        repo.put_play(self.play())
        repo.put_play(self.play(user_id="u2", updated_at=at(5)))
        repo.put_play(self.play(source_id="src_2", updated_at=at(9)))
        got = repo.all_plays()
        assert [(p.user_id, p.source_id) for p in got] == [
            ("u1", "src_2"), ("u2", "src_1"), ("u1", "src_1")]

    def test_put_and_get(self, repo):
        repo.put_play(self.play(chart_id="c_1", link="s"))
        got = repo.get_play("u1", "src_1")
        assert (got.color, got.slot, got.chart_id, got.link) == ("red", 4, "c_1", "s")
        assert got.format == "fours"
        assert repo.get_play("u1", "src_2") is None
        assert repo.get_play("u2", "src_1") is None

    def test_one_per_person_per_game_and_the_second_replaces_the_first(self, repo):
        repo.put_play(self.play(color="red", slot=4, chart_id="c_1", link="c"))
        repo.put_play(self.play(color="yellow", slot=1, updated_at=at(60)))
        got = repo.get_play("u1", "src_1")
        assert (got.color, got.slot, got.chart_id, got.link) == ("yellow", 1, None, "g")
        assert [p.source_id for p in repo.plays_for_user("u1")] == ["src_1"]

    def test_each_person_sees_only_their_own(self, repo):
        repo.put_play(self.play(source_id="src_1"))
        repo.put_play(self.play(source_id="src_2", updated_at=at(60)))
        repo.put_play(self.play(user_id="u2", source_id="src_1"))
        assert [p.source_id for p in repo.plays_for_user("u1")] == ["src_2", "src_1"]
        assert [p.user_id for p in repo.plays_for_user("u2")] == ["u2"]
        assert repo.plays_for_user("u3") == []

    def test_deleting(self, repo):
        repo.put_play(self.play())
        repo.put_play(self.play(user_id="u2"))
        assert repo.delete_play("u1", "src_1") is True
        assert repo.get_play("u1", "src_1") is None
        assert repo.delete_play("u1", "src_1") is False
        assert repo.get_play("u2", "src_1") is not None

    def test_the_id_is_the_pair(self):
        assert play_id("u1", "src_1") == play_id("u1", "src_1")
        assert play_id("u1", "src_1") != play_id("u2", "src_1")
        assert play_id("u1", "src_1") != play_id("u1", "src_2")


class TestBackup:
    def test_export_then_import_into_a_fresh_store(self, repo):
        repo.put_run(run())
        repo.put_job(job())
        repo.put_chart(chart(overrides={"0.1.1": {"user_score": 4}}, overrides_version=1))
        repo.put_playlist(WatchedPlaylist(id="p_1", playlist_id="PL", label="Tue",
                                          created_at=T0))
        repo.put_flag(Flag(id="f_1", created_at=T0, note="n", place={"end": 1}))
        repo.put_play(Play(id=play_id("u1", "src_1"), user_id="u1", source_id="src_1",
                           color="yellow", slot=2, created_at=T0, updated_at=T0,
                           chart_id="c_1", link="c"))
        repo.put_review(Review(id="s_1_r_1", source_id="s_1", run_id="r_1",
                               reviewed_at=T0, run_ready_at=T0, flag_id="fa_1"))
        repo.put_team_name_edit(TeamNameEdit(id="tn_1", source_id="s_1", user_id="u1", at=T0,
                                             before={"red": None}, after={"red": "Rice"}))
        data = repo.export_all()
        assert {r["id"] for r in data["runs"]} == {"r_1"}
        assert {c["id"] for c in data["charts"]} == {"c_1"}

        fresh = MemoryRepo()
        fresh.import_all(data)
        assert fresh.get_chart("c_1").overrides == {"0.1.1": {"user_score": 4}}
        assert fresh.chart_by_share("s_1").id == "c_1"
        assert fresh.get_run("r_1").games == run().games
        assert fresh.get_playlist("p_1").label == "Tue"
        assert [f.id for f in fresh.list_flags()] == ["f_1"]
        got = fresh.get_play("u1", "src_1")
        assert (got.color, got.slot, got.chart_id) == ("yellow", 2, "c_1")
        assert fresh.get_review("s_1_r_1").flag_id == "fa_1"
        assert [e.after for e in fresh.team_name_edits("s_1")] == [{"red": "Rice"}]


class TestPractice:
    def test_watches_by_sheet_newest_first(self, repo):
        from curling_score.service.records import PracticeWatch

        for i, sheet in enumerate((3, 3, 4)):
            repo.put_watch(PracticeWatch(id=f"pw_{i}", sheet=sheet, video_id="v", run_id="r",
                                         job_id="j", created_at=at(i)))
        assert [w.id for w in repo.watches_for_sheet(3)] == ["pw_1", "pw_0"]
        assert repo.watch_for_job("j").id in ("pw_0", "pw_1", "pw_2")
        assert repo.watch_for_job("j_none") is None
        assert repo.update_watch("pw_0", status="ended", throws=4).throws == 4
        assert repo.get_watch("pw_0").status == "ended"

    def test_sessions_by_watch_and_by_user(self, repo):
        from curling_score.service.records import PracticeSession

        for i, (user, watch) in enumerate((("u1", "pw_a"), ("u2", "pw_a"), ("u1", "pw_b"))):
            repo.put_session(PracticeSession(id=f"ps_{i}", user_id=user, sheet=3,
                                             watch_id=watch, started_at=at(i)))
        assert [s.id for s in repo.sessions_for_watch("pw_a")] == ["ps_0", "ps_1"]
        assert [s.id for s in repo.sessions_for_user("u1")] == ["ps_2", "ps_0"]
        got = repo.update_session("ps_0", ended_at=at(9), end_reason="stop", hidden=["t_1"])
        assert (got.active, got.hidden) == (False, ["t_1"])


def clip_job(id="cj_1", play="p1", state="queued", seconds=0, **kw):
    kw.setdefault("spec", {"rocks": [{"name": "0.1.1"}]})
    return ClipJob(id=id, play_id=play, user_id="u", source_id="s", run_id="r", video_id="v",
                   state=state, created_at=at(seconds), run_after=at(seconds), **kw)


class TestTheClipQueue:
    """Clip jobs (records.ClipJob): a queue of their own for the clips worker."""

    def test_a_queued_job_is_taken_once(self, repo):
        repo.put_clip_job(clip_job())
        got = repo.take_clip_job("cj_1", "clips", T0, 600.0)
        assert (got.state, got.worker_id, got.attempts) == ("running", "clips", 1)
        assert got.lease_expires_at == T0 + timedelta(seconds=600)
        assert repo.get_clip_job("cj_1").state == "running"
        assert repo.take_clip_job("cj_1", "other", T0, 600.0) is None
        assert repo.take_clip_job("nope", "clips", T0, 600.0) is None

    def test_by_play_and_by_state_oldest_first(self, repo):
        repo.put_clip_job(clip_job("cj_b", seconds=5))
        repo.put_clip_job(clip_job("cj_a"))
        repo.put_clip_job(clip_job("cj_c", play="p2", state="done"))
        assert [j.id for j in repo.clip_jobs_for_play("p1")] == ["cj_a", "cj_b"]
        assert [j.id for j in repo.clip_jobs_in_state("queued")] == ["cj_a", "cj_b"]
        assert [j.id for j in repo.clip_jobs_in_state("done")] == ["cj_c"]

    def test_an_update_changes_only_what_it_names(self, repo):
        repo.put_clip_job(clip_job())
        got = repo.update_clip_job("cj_1", state="done", rocks={"0.1.1": {"clip": True}})
        assert (got.state, got.rocks, got.play_id) == ("done", {"0.1.1": {"clip": True}}, "p1")
        assert repo.update_clip_job("nope", state="done") is None

    def test_a_backup_keeps_them(self, repo):
        repo.put_clip_job(clip_job(rocks={"0.1.1": {"clip": True}}))
        fresh = MemoryRepo()
        fresh.import_all(repo.export_all())
        got = fresh.get_clip_job("cj_1")
        assert (got.play_id, got.state, got.rocks, got.spec) == \
            ("p1", "queued", {"0.1.1": {"clip": True}}, {"rocks": [{"name": "0.1.1"}]})

    def test_a_finished_job_with_an_overlay_round_trips(self, repo):
        # An overlay is lists of [u, v] points; Firestore holds no array
        # directly inside an array, so the repo must store it another way.
        spec = {"rocks": [{"name": "0.1.1", "house": "top",
                           "line": {"start": {"x": 0.1, "y": 38.0}, "at_hog": {"x": 0.2}}}]}
        rocks = {"0.1.1": {"clip": True, "overlay": [
            {"t0": 0.0, "t1": 16.0},
            {"t0": 16.0, "t1": 24.5, "thrown": [[0.5, 0.6], [0.51, 0.7]],
             "aim": [[0.4, 0.9], [0.5, 0.6]], "broom": [0.5, 0.58]}]}}
        repo.put_clip_job(clip_job(spec=spec))
        assert repo.take_clip_job("cj_1", "clips", T0, 600.0).spec == spec
        done = repo.update_clip_job("cj_1", state="done", rocks=rocks, finished_at=T0)
        assert (done.spec, done.rocks) == (spec, rocks)
        assert repo.get_clip_job("cj_1").rocks == rocks
        assert repo.clip_jobs_for_play("p1")[0].rocks == rocks
        assert repo.clip_jobs_in_state("done")[0].rocks == rocks
        assert repo.clip_jobs_in_state("done")[0].spec == spec
        exported = repo.export_all()["clip_jobs"][0]
        assert (exported["spec"], exported["rocks"]) == (spec, rocks)
        fresh = MemoryRepo()
        fresh.import_all(repo.export_all())
        assert fresh.get_clip_job("cj_1").rocks == rocks
        assert fresh.get_clip_job("cj_1").spec == spec
