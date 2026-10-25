"""A practice session is not a game, and is left out of the catalogue.

Four were published as games in the week to 2026-10-06, each confirmed on
video or by its sheet's schedule: two people throwing both colours on 10/04
Sunday Open Doubles sheet 1, one person alone on 10/02 Friday sheet 5 after
the league game, and both 10/04 Sunday Afternoon sheet 5 "games". Each read
no board card all session and kept well under an end's rocks; no real game of
127 does both.
"""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from curling_score import analyze, timeline
from curling_score.service import practice
from curling_score.service.records import Run, User
from curling_score.service.repo import MemoryRepo
from tests.test_unplayed_ends import built, context, segment


def end(kept, expected=16):
    return {"shots_expected": expected,
            "shots": [{"number": i + 1, "missing": i >= kept} for i in range(expected)]}


def game(kept_per_end, expected=16, cards=False, in_progress=False):
    return {"ends": [end(k, expected) for k in kept_per_end],
            "scoreboard": ({"per_end": {"1": {"red": 1, "yellow": 0}}} if cards else None),
            "in_progress": in_progress}


class TestIsPractice:
    def test_the_four_practice_sessions_of_the_week(self):
        # 10/04 doubles sheet 1: 10, 3 of 4 listed, 7, 5 of 10 kept
        assert timeline.is_practice(game([10, 3, 7, 5], expected=10))
        # 10/02 Friday sheet 5, game 2: 81% kept
        assert timeline.is_practice(game([13, 13]))
        # 10/04 Sunday Afternoon sheet 5: 58% and 38%
        assert timeline.is_practice(game([9, 8, 10, 10]))
        assert timeline.is_practice(game([5, 7]))

    def test_a_real_game_whose_board_was_never_read_is_a_game(self):
        """17 of 122 real games have no board card -- a board the reader
        could not read -- and every one kept 85% or more of its rocks."""
        assert not timeline.is_practice(game([16, 15, 16, 14, 16, 16]))

    def test_a_game_the_board_scored_is_a_game_however_few_rocks_it_kept(self):
        """sGYkR-Zw4MU kept 83%, the most short of any real game, and its
        board posted every end."""
        assert not timeline.is_practice(game([13, 14, 13, 13, 14, 13], cards=True))

    def test_a_board_read_with_no_cards_on_it_is_no_card(self):
        g = game([4, 5])
        g["scoreboard"] = {"per_end": {}}
        assert timeline.is_practice(g)

    def test_a_game_with_no_ends_says_nothing(self):
        assert not timeline.is_practice({"ends": [], "scoreboard": None})


class TestTheGameSummary:
    """What the worker and the live lane tell the service about each game."""

    def test_a_practice_session_says_so(self):
        g = {"index": 1, "start_s": 7630.0, "end_s": 9605.0, "ends": [{}, {}], "practice": True}
        assert timeline.game_summary(g) == {"index": 1, "start_s": 7630.0, "end_s": 9605.0,
                                            "ends": 2, "practice": True}

    def test_a_game_is_summarised_as_it_always_was(self):
        g = {"index": 0, "start_s": 10.0, "end_s": 6000.0, "ends": [{}, {}], "practice": False}
        assert timeline.game_summary(g) == {"index": 0, "start_s": 10.0, "end_s": 6000.0, "ends": 2}


class TestARecordingsGames:
    def test_a_practice_session_is_marked_and_a_game_is_not(self, monkeypatch):
        def build_one_end(ctx, game, end, prev_end_s, board_score):
            kept = 6 if game.index == 1 else 16
            return built(end.number, kept, kept, start_s=end.start_s, kept=kept), end.end_s

        monkeypatch.setattr(analyze, "build_one_end", build_one_end)
        real, practice = segment(4), replace(segment(2, start_s=9000.0), index=1)
        got = analyze.build_games(context(), [real, practice])
        assert [g["practice"] for g in got] == [False, True]


class TestCameraWatches:
    """Practice on the club's cameras: a sheet's watch, and the league that
    closes practice (service/practice.py)."""

    NOW = datetime(2026, 10, 24, 9, 5, 7, tzinfo=timezone.utc)
    USER = User(id="uid-sarah", created_at=NOW)

    def live_run(self, repo, status="live", age_s=60.0, kind="live", run_id="r_1"):
        repo.put_run(Run(id=run_id, video_id="v", processing_version="p", status=status,
                         kind=kind, created_at=self.NOW - timedelta(seconds=age_s)))

    def test_a_camera_watch_is_named_for_its_sheet_and_start(self):
        assert practice.camera_video_id(3, self.NOW) == "cams3-20261024-090507"

    def test_a_start_makes_a_camera_run(self):
        repo = MemoryRepo()
        s = practice.start(repo, self.USER, 2, "right", self.NOW, "v1+m")
        watch = repo.get_watch(s.watch_id)
        run, job = repo.get_run(watch.run_id), repo.get_job(watch.job_id)
        assert (run.video_id, run.source, run.kind, run.status, run.sheet) == (
            "cams2-20261024-090507", "cameras", "practice", "queued", 2)
        assert watch.video_id == run.video_id and run.processing_version == "v1+m"
        assert (job.kind, job.state) == ("practice", "queued")

    def test_a_sheet_the_club_has_no_cameras_on(self):
        repo = MemoryRepo()
        with pytest.raises(practice.NoCameras):
            practice.start(repo, self.USER, 6, "right", self.NOW, "v")
        assert not repo.jobs

    def test_a_start_joins_the_sheets_running_watch(self):
        repo = MemoryRepo()
        a, b = (practice.start(repo, self.USER, 2, hand, self.NOW, "v")
                for hand in ("right", "left"))
        assert a.watch_id == b.watch_id and len(repo.jobs) == 1

    def test_a_league_on_refuses_a_start(self):
        repo = MemoryRepo()
        self.live_run(repo)
        with pytest.raises(practice.LeagueOn):
            practice.start(repo, self.USER, 2, "right", self.NOW, "v")
        assert not repo.jobs

    @pytest.mark.parametrize("status", ["queued", "processing", "live"])
    def test_a_league_run_not_yet_over_is_a_league_on(self, status):
        repo = MemoryRepo()
        self.live_run(repo, status=status)
        assert practice.league_on(repo, self.NOW)

    @pytest.mark.parametrize("status", ["ready", "failed", "pending_approval"])
    def test_a_league_run_that_is_over_is_not(self, status):
        repo = MemoryRepo()
        self.live_run(repo, status=status)
        assert not practice.league_on(repo, self.NOW)

    def test_a_recording_or_a_practice_watch_is_not_a_league(self):
        repo = MemoryRepo()
        self.live_run(repo, status="queued", kind="vod")
        self.live_run(repo, status="live", kind="practice", run_id="r_2")
        assert not practice.league_on(repo, self.NOW)

    def test_a_league_run_left_behind_for_six_hours_is_forgotten(self):
        fresh, stale = MemoryRepo(), MemoryRepo()
        self.live_run(fresh, age_s=practice.LEAGUE_FRESH_S - 1)
        self.live_run(stale, age_s=practice.LEAGUE_FRESH_S + 1)
        assert practice.league_on(fresh, self.NOW) and not practice.league_on(stale, self.NOW)


class TestEndingSessions:
    """A session ends 15 minutes after its last throw, or when a league feed
    goes live anywhere (leagues use all five sheets)."""

    NOW = datetime(2026, 10, 24, 9, 5, 7, tzinfo=timezone.utc)
    USER = User(id="uid-sarah", created_at=NOW)

    def session_on(self, repo, sheet):
        return practice.start(repo, self.USER, sheet, "right", self.NOW, "v1+m")

    def doc_with_a_throw(self, at):
        return {"wall_t0": at.timestamp(), "t0_s": 0.0,
                "throws": [{"id": "t_1", "t_release_s": 0.0}]}

    def test_idle_after_fifteen_minutes(self):
        repo = MemoryRepo()
        s = self.session_on(repo, 2)
        watch = repo.get_watch(s.watch_id)
        thrown = self.NOW + timedelta(seconds=60)
        doc = self.doc_with_a_throw(thrown)
        assert practice.IDLE_S == 900.0
        assert practice.sweep(repo, watch, doc, thrown + timedelta(seconds=899)) == 1
        assert repo.get_session(s.id).active
        assert practice.sweep(repo, watch, doc, thrown + timedelta(seconds=900)) == 0
        got = repo.get_session(s.id)
        assert not got.active and got.end_reason == "idle"

    def test_idle_counts_from_start_when_nothing_thrown(self):
        repo = MemoryRepo()
        s = self.session_on(repo, 2)
        watch = repo.get_watch(s.watch_id)
        assert practice.sweep(repo, watch, None, self.NOW + timedelta(seconds=899)) == 1
        assert practice.sweep(repo, watch, None, self.NOW + timedelta(seconds=900)) == 0
        assert repo.get_session(s.id).end_reason == "idle"

    def test_a_throw_before_start_does_not_postpone_the_idle_end(self):
        repo = MemoryRepo()
        s = self.session_on(repo, 2)
        watch = repo.get_watch(s.watch_id)
        doc = self.doc_with_a_throw(self.NOW - timedelta(seconds=300))
        assert practice.sweep(repo, watch, doc, self.NOW + timedelta(seconds=900)) == 0

    def test_a_league_ends_every_session_on_every_sheet(self):
        repo = MemoryRepo()
        a, b, c = (self.session_on(repo, sheet) for sheet in (1, 3, 5))
        joined = self.session_on(repo, 3)               # joins sheet 3's watch
        later = self.NOW + timedelta(minutes=5)
        assert practice.end_for_league(repo, later) == 4
        for s in (a, b, c, joined):
            got = repo.get_session(s.id)
            assert not got.active and got.end_reason == "league" and got.ended_at == later
        # Nothing is left to end, and each watch now reads as unwatched.
        assert practice.end_for_league(repo, later) == 0
        for s in (a, b, c):
            assert practice.sweep(repo, repo.get_watch(s.watch_id), None, later) == 0

    def test_a_league_leaves_a_session_already_over_with_its_own_reason(self):
        repo = MemoryRepo()
        done, live = self.session_on(repo, 2), self.session_on(repo, 2)
        practice.stop(repo, repo.get_session(done.id), self.NOW, "stop")
        assert practice.end_for_league(repo, self.NOW + timedelta(minutes=1)) == 1
        assert repo.get_session(done.id).end_reason == "stop"
        assert repo.get_session(live.id).end_reason == "league"

    def test_a_league_leaves_a_dead_watch_to_say_it_failed(self):
        repo = MemoryRepo()
        s = self.session_on(repo, 4)
        repo.update_job(repo.get_watch(s.watch_id).job_id, state="failed")
        assert practice.end_for_league(repo, self.NOW) == 0
        assert repo.get_session(s.id).active           # settle() ends it, as "failed"
        practice.settle(repo, repo.get_session(s.id), self.NOW)
        assert repo.get_session(s.id).end_reason == "failed"

    def test_with_no_session_a_league_ends_nothing(self):
        assert practice.end_for_league(MemoryRepo(), self.NOW) == 0
