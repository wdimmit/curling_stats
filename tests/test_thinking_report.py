"""The thinking report: which teams in a league took longest per end --
worked out from each game's stored summary alone."""

from datetime import datetime, timedelta, timezone

import pytest

from curling_score.service import thinking_report as tr
from curling_score.service.records import Source

T0 = datetime(2026, 3, 3, 3, 0, tzinfo=timezone.utc)


def src(sid, red, yellow, ends=8, *, video=None, index=0, days=0, run="r_1",
        playlist="2025-2026 Tuesday Super League", measured=None, fmt=None,
        title="3/3 - Sheet 4 - Tuesday Super League 2025-2026", **kw):
    s = Source(id=sid, video_id=video or f"v_{sid}", game_start_s=0.0, game_end_s=7000.0,
               current_run_id=run, game_index=index, created_at=T0,
               played_at=T0 + timedelta(days=days), title=title, sheet=4,
               playlist_title=playlist, format=fmt, **kw)
    s.thinking = {"run_id": run, "play_start_s": s.play_start_s, "ends": ends,
                  "red": float(red), "yellow": float(yellow),
                  "measured_shots": 15 * ends if measured is None else measured,
                  "unmeasured_shots": ends, "estimated_shots": 0}
    return s


def only(report):
    (league,) = report["leagues"]
    return league


def ranked(league):
    return [(r["source_id"], r["colour"]) for r in league["teams"]]


class TestSummarize:
    def test_it_reads_the_trimmed_game_and_keys_it(self):
        game = {"ends": [{}, {}, {}],
                "thinking_time": {"red": 400.5, "yellow": 300.0, "measured_shots": 45,
                                  "unmeasured_shots": 3, "estimated_shots": 1, "anomalies": 0}}
        assert tr.summarize(game, "r_9", 1515.0) == {
            "run_id": "r_9", "play_start_s": 1515.0, "ends": 3, "red": 400.5,
            "yellow": 300.0, "measured_shots": 45, "unmeasured_shots": 3,
            "estimated_shots": 1}

    def test_a_timeline_from_before_thinking_time_reads_as_nothing_measured(self):
        t = tr.summarize({"ends": [{}]}, "r_1", None)
        assert t["measured_shots"] == 0 and t["red"] == t["yellow"] == 0.0


class TestCurrent:
    def test_a_reprocess_makes_it_stale(self):
        s = src("a", 100, 100)
        assert tr.current(s) is s.thinking
        s.current_run_id = "r_2"
        assert tr.current(s) is None

    def test_a_new_play_start_makes_it_stale(self):
        s = src("a", 100, 100)
        s.play_start_s = 1515.0
        assert tr.current(s) is None

    def test_no_summary_is_not_current(self):
        s = src("a", 100, 100)
        s.thinking = None
        assert tr.current(s) is None


class TestLeagueOf:
    def test_the_playlist_wins(self):
        s = src("a", 1, 1, league="Super")
        assert tr.league_of(s) == ("2025-2026 Tuesday Super League", "playlist")

    def test_then_the_title_then_the_label(self):
        s = src("a", 1, 1, playlist=None, league="Super")
        assert tr.league_of(s) == ("Tuesday Super League 2025-2026", "title")
        s.title = "Somebody's upload"
        assert tr.league_of(s) == ("Super", "label")
        s.league = None
        assert tr.league_of(s) == ("Other", "none")


class TestBuild:
    def test_each_team_in_a_game_is_its_own_row(self):
        league = only(tr.build([src("a", 900, 600, ends=6, team_red="Casey", team_yellow="Good")]))
        red, yellow = league["teams"]
        assert (red["colour"], red["team"], red["opponent"]) == ("red", "Casey", "Good")
        assert (yellow["colour"], yellow["team"], yellow["opponent"]) == ("yellow", "Good", "Casey")
        assert red["thinking_s"] == 900 and red["per_end_s"] == pytest.approx(150)
        assert yellow["per_end_s"] == pytest.approx(100)

    def test_most_thinking_is_per_end_not_per_game(self):
        long_game = src("nine", 2000, 100, ends=9)       # 222 s per end
        short_game = src("six", 1375, 100, ends=6)       # 229 s per end
        assert ranked(only(tr.build([long_game, short_game])))[:2] == [("six", "red"),
                                                                        ("nine", "red")]

    def test_both_colours_rank_in_one_list(self):
        league = only(tr.build([src("a", 600, 300, ends=2), src("b", 300, 500, ends=2)]))
        assert ranked(league) == [("a", "red"), ("b", "yellow"), ("a", "yellow"), ("b", "red")]

    def test_a_tie_goes_to_the_later_game(self):
        league = only(tr.build([src("old", 300, 0.0, ends=2, days=0),
                                src("new", 300, 0.0, ends=2, days=7)]))
        assert ranked(league)[:2] == [("new", "red"), ("old", "red")]

    def test_a_game_with_nothing_timed_is_left_out(self):
        stray = src("stray", 0, 0, ends=1, measured=0)
        league = only(tr.build([src("a", 100, 100), stray]))
        assert league["games"] == 1 and tr.build([stray])["leagues"] == []

    def test_a_stale_summary_is_pending_not_ranked(self):
        stale = src("b", 100, 100)
        stale.current_run_id = "r_2"
        report = tr.build([src("a", 100, 100), stale])
        assert report["pending"] == 1 and only(report)["games"] == 1

    def test_leagues_are_newest_first_and_split_by_format(self):
        report = tr.build([
            src("old", 100, 100, days=0, playlist="Monday Open"),
            src("new", 100, 100, days=30, playlist="Tuesday Super"),
            src("dbl", 100, 100, days=10, playlist="Tuesday Super", fmt="doubles"),
        ])
        assert [(lg["league"], lg["format"]) for lg in report["leagues"]] == [
            ("Tuesday Super", "fours"), ("Tuesday Super", "doubles"), ("Monday Open", "fours")]
        assert {r["source_id"] for r in report["leagues"][1]["teams"]} == {"dbl"}

    def test_a_league_says_where_its_name_came_from(self):
        report = tr.build([src("a", 1, 1, playlist=None), src("b", 1, 1, video="v_b")])
        assert {(lg["league"], lg["from"]) for lg in report["leagues"]} == {
            ("Tuesday Super League 2025-2026", "title"),
            ("2025-2026 Tuesday Super League", "playlist")}

    def test_the_league_average_is_per_team_per_end_weighted_by_ends(self):
        league = only(tr.build([src("a", 300, 300, ends=6), src("b", 100, 100, ends=2)]))
        assert league["avg_per_end_s"] == pytest.approx(800 / 16)

    def test_top_cuts_the_rows_but_counts_every_game(self):
        league = only(tr.build([src(f"g{i}", 100 + i, 100) for i in range(12)], top=20))
        assert league["games"] == 12 and len(league["teams"]) == 20
        assert tr.TOP == 20

    def test_a_row_knows_how_many_games_its_video_holds(self):
        a = src("a", 1, 2, video="v", index=0)
        b = src("b", 2, 1, video="v", index=1)
        assert {r["games_in_video"] for r in only(tr.build([a, b]))["teams"]} == {2}
