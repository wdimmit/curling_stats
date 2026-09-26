"""The thinking report: which games in a league took longest, and which were
most one-sided -- worked out from each game's stored summary alone."""

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
    def test_most_thinking_is_per_end_not_per_game(self):
        long_game = src("nine", 1800, 2000, ends=9)      # 3800 s over 9 ends = 422 s
        short_game = src("six", 1340, 1375, ends=6)      # 2715 s over 6 ends = 452 s
        league = only(tr.build([long_game, short_game]))
        assert [r["source_id"] for r in league["by_pace"]] == ["six", "nine"]
        assert league["by_pace"][0]["per_end_s"] == pytest.approx(2715 / 6)

    def test_lopsided_is_the_larger_share_whichever_colour(self):
        red_heavy = src("r", 2293.27, 1589.27)           # red 59%
        yellow_heavy = src("y", 730.28, 1455.44)         # yellow 67%
        even = src("e", 1340, 1375)
        league = only(tr.build([red_heavy, even, yellow_heavy]))
        rows = league["by_split"]
        assert [r["source_id"] for r in rows] == ["y", "r", "e"]
        assert rows[0]["longer"] == "yellow" and rows[0]["share"] == pytest.approx(0.6659, abs=1e-4)
        assert rows[1]["longer"] == "red" and rows[1]["gap_s"] == pytest.approx(704.0)
        assert rows[0]["share_red"] + rows[0]["share_yellow"] == pytest.approx(1.0)

    def test_an_equal_share_is_broken_by_the_bigger_gap(self):
        small = src("small", 300, 200, ends=2)
        big = src("big", 3000, 2000)
        assert [r["source_id"] for r in only(tr.build([small, big]))["by_split"]] == ["big", "small"]

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

    def test_a_league_says_where_its_name_came_from(self):
        report = tr.build([src("a", 1, 1, playlist=None), src("b", 1, 1, video="v_b")])
        assert {(lg["league"], lg["from"]) for lg in report["leagues"]} == {
            ("Tuesday Super League 2025-2026", "title"),
            ("2025-2026 Tuesday Super League", "playlist")}

    def test_the_league_average_weights_by_ends(self):
        league = only(tr.build([src("a", 300, 300, ends=6), src("b", 100, 100, ends=2)]))
        assert league["avg_per_end_s"] == pytest.approx(800 / 8)

    def test_top_cuts_both_lists_but_counts_every_game(self):
        league = only(tr.build([src(f"g{i}", 100 + i, 100) for i in range(12)], top=10))
        assert league["games"] == 12
        assert len(league["by_pace"]) == len(league["by_split"]) == 10

    def test_a_row_knows_how_many_games_its_video_holds(self):
        a = src("a", 1, 2, video="v", index=0)
        b = src("b", 2, 1, video="v", index=1)
        rows = only(tr.build([a, b]))["by_pace"]
        assert {r["games_in_video"] for r in rows} == {2}
