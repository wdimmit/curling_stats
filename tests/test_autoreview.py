"""The nightly review's reading of one game: what counts as wrong, and how
sure it has to be before a game is flagged."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from curling_score import autoreview
from curling_score.autoreview import Baseline, EndMetrics, Finding, GameReview

FLOOR = Baseline({}, {})        # no history yet: the fixed floor stands in


def rock(n, colour=None, **kw):
    s = {"number": n, "color": colour or ("red" if n % 2 else "yellow"), "missing": False,
         "t_video_s": 100.0 + n, "target_broom": {"x": 0.1, "y": 0.0, "confidence": 0.9},
         "long_split_s": 14.0, "line": {"at_broom": {"miss_m": 0.2}},
         "t_release_s": 90.0 + n, "color_inferred": False}
    s.update(kw)
    return s


def end(number, house, rocks=16, expected=16, shots=None, **kw):
    e = {"number": number, "house": house, "start_s": 1000.0 * number,
         "shots_expected": expected, "unplaced_shots": 0,
         "score": {"red": 1, "yellow": 0}, "detected_score": {"red": 1, "yellow": 0},
         "shots": shots if shots is not None else [rock(n) for n in range(1, rocks + 1)]}
    e.update(kw)
    return e


def house(i):
    return "top" if i % 2 else "bottom"


def game(*ends, n=4, hammer=True):
    ends = list(ends) or [end(i, house(i)) for i in range(1, n + 1)]
    return {"index": 0, "hammer_consistent": hammer, "ends": ends}


def doubles_game(n=6):
    return game(*[end(i, house(i), rocks=10, expected=10) for i in range(1, n + 1)])


def checks(g, baseline=FLOOR):
    return sorted(f.check for f in autoreview.review_game(g, baseline).findings)


class TestAGame:
    def test_a_clean_fours_game_has_nothing_to_say(self):
        got = autoreview.review_game(game(), FLOOR)
        assert got.findings == [] and got.notes == [] and not got.raise_flag
        assert got.format == "fours" and [m.number for m in got.ends] == [1, 2, 3, 4]

    def test_ten_rock_ends_are_doubles(self):
        assert autoreview.game_format(doubles_game()) == "doubles"
        assert autoreview.game_format(game()) == "fours"

    def test_end_metrics_count_placed_rocks_and_what_each_has(self):
        shots = [rock(n) for n in range(1, 17)]
        shots[2]["missing"] = True
        shots[3]["target_broom"] = None
        shots[4]["long_split_s"] = None
        shots[5]["line"] = None
        shots[6]["t_release_s"] = None
        assert autoreview.end_metrics(end(1, "top", shots=shots)) == EndMetrics(
            number=1, rocks=15, broom=14, split=14, line=14, release=14)

    def test_a_timeline_from_before_the_newer_fields_is_read_without_error(self):
        bare = {"index": 0, "ends": [{"number": 1, "house": "top", "start_s": 0.0,
                                      "shots": [{"number": 1, "color": "red",
                                                 "missing": False}]}]}
        got = autoreview.review_game(bare, FLOOR)
        assert got.ends == [EndMetrics(1, 1, 0, 0, 0, 0)]


class TestTheBaseline:
    def rows(self, fmt, broom_counts):
        return [(fmt, EndMetrics(i, 16, b, 16, 16, 16)) for i, b in enumerate(broom_counts)]

    def test_normal_is_the_2nd_percentile_of_recent_ends(self):
        counts = [16] * 95 + [8] * 3 + [0] * 2          # 100 ends
        base = Baseline.from_ends(self.rows("fours", counts))
        # sorted coverage: 0, 0, .5, .5, .5, 1 ...; index int(0.02 * 99) = 1 -> 0.0
        assert base.threshold("fours", "broom") == 0.0
        assert base.ends == {"fours": 100}

    def test_too_few_ends_fall_back_to_the_floor(self):
        base = Baseline.from_ends(self.rows("fours", [16] * 99))
        assert base.threshold("fours", "broom") == autoreview.COVERAGE_FLOOR

    def test_doubles_brooms_have_no_floor(self):
        assert FLOOR.threshold("doubles", "broom") == 0.0
        assert FLOOR.threshold("doubles", "split") == autoreview.COVERAGE_FLOOR

    def test_ends_too_short_to_judge_are_left_out(self):
        rows = [("fours", EndMetrics(1, 5, 0, 0, 0, 0))] * 200
        assert Baseline.from_ends(rows).ends == {}

    def test_stored_records_are_plain_dicts(self):
        rows = [("fours", EndMetrics(i, 16, 12, 16, 16, 16).to_dict()) for i in range(100)]
        assert Baseline.from_ends(rows).threshold("fours", "broom") == 0.75


class TestWhenAGameIsFlagged:
    def got(self, *strengths):
        return GameReview("fours", [], [Finding("x", s) for s in strengths if s != "note"],
                          [Finding("n", "note") for s in strengths if s == "note"])

    @pytest.mark.parametrize("strengths,flagged", [
        (("strong",), True), (("weak",), False), (("weak", "weak"), True),
        (("note", "note", "note"), False), ((), False)])
    def test_one_strong_or_two_weak(self, strengths, flagged):
        assert self.got(*strengths).raise_flag is flagged


class TestTheFlagItRaises:
    def test_its_id_is_derived_from_the_game_the_run_and_when_the_run_finished(self):
        t1 = datetime(2026, 10, 5, 3, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc)
        a = autoreview.flag_id("s_1", "r_1", t1)
        assert a == autoreview.flag_id("s_1", "r_1", t1) and a.startswith("fa_")
        assert len(a) == 3 + 16
        assert a != autoreview.flag_id("s_1", "r_1", t2) != autoreview.flag_id("s_2", "r_1", t2)

    def test_the_summary_names_each_finding(self):
        got = GameReview("fours", [], [Finding("same_house", "strong", 5, None, "e4 and e5 both top"),
                                       Finding("odd_split", "weak", 6, 9, "split 23.4 s")],
                         [Finding("hammer", "note", detail="hammer sequence broken")])
        text = autoreview.summary(got)
        assert text.startswith("Auto-review: 2 findings")
        assert "e5 e4 and e5 both top" in text and "e6 r9 split 23.4 s" in text
        assert "hammer sequence broken" in text

    def test_a_long_summary_is_cut_but_keeps_the_earlier_flag(self):
        many = [Finding("odd_split", "weak", 1, n, "split 30.0 s") for n in range(400)]
        text = autoreview.summary(GameReview("fours", [], many, []),
                                  extra="earlier auto-flag fa_0123 (run r_9)", limit=2000)
        assert len(text) <= 2000 and text.endswith("…")
        assert "earlier auto-flag fa_0123 (run r_9)" in text


def missing(e, *numbers):
    for s in e["shots"]:
        if s["number"] in numbers:
            s["missing"] = True
    return e


class TestStructure:
    def test_two_ends_in_a_row_to_the_same_house(self):
        g = game(end(1, "top"), end(2, "bottom"), end(3, "bottom"), end(4, "top"))
        got = autoreview.review_game(g, FLOOR).findings
        assert [(f.check, f.end, f.strength) for f in got] == [("same_house", 3, "strong")]

    def test_an_end_number_skipped(self):
        g = game(end(1, "top"), end(2, "bottom"), end(4, "top"), end(5, "bottom"))
        assert checks(g) == ["end_gap"]

    def test_rocks_missing_mid_game(self):
        g = game()
        missing(g["ends"][1], 5, 9)
        got = [f for f in autoreview.review_game(g, FLOOR).findings]
        assert [(f.check, f.end, f.rock) for f in got] == [("missing_rocks", 2, 5)]
        assert "5, 9" in got[0].detail

    def test_joining_late_is_not_missing_rocks(self):
        g = game()
        missing(g["ends"][0], 1, 2, 3)
        assert checks(g) == []

    def test_a_gap_in_the_first_end_after_rock_1_is(self):
        g = game()
        missing(g["ends"][0], 1, 2, 5)
        assert checks(g) == ["missing_rocks"]

    def test_a_conceded_last_end_is_not_missing_rocks(self):
        g = game()
        missing(g["ends"][-1], 14, 15, 16)
        assert checks(g) == []

    def test_a_middle_end_listing_too_few_rocks(self):
        g = game(end(1, "top"), end(2, "bottom", rocks=14), end(3, "top"), end(4, "bottom"))
        assert checks(g) == ["missing_rocks"]

    def test_short_lists_at_either_end_of_the_game_are_normal(self):
        g = game(end(1, "top", rocks=13), end(2, "bottom"), end(3, "top"), end(4, "bottom", rocks=9))
        assert checks(g) == []

    def test_one_colour_twice_in_a_row(self):
        g = game()
        g["ends"][2]["shots"][5]["color"] = g["ends"][2]["shots"][4]["color"]
        assert "not_alternating" in checks(g)

    def test_a_missing_rock_between_two_of_a_colour_is_not_a_repeat(self):
        g = game()
        missing(g["ends"][1], 6)          # rocks 5 and 7 are both red, and should be
        assert "not_alternating" not in checks(g)

    def test_doubles_are_not_held_to_alternating(self):
        g = doubles_game()
        g["ends"][0]["shots"][1]["color"] = "red"
        assert "not_alternating" not in checks(g)

    def test_one_colour_with_more_than_half_the_rocks(self):
        g = game()
        for s in g["ends"][1]["shots"][:10]:
            s["color"] = "red"
        assert "team_over" in checks(g)

    def test_a_game_of_two_ends(self):
        got = autoreview.review_game(game(n=2), FLOOR).findings
        assert [(f.check, f.end) for f in got] == [("short_game", None)]

    def test_a_tiny_last_end_after_a_real_game(self):
        g = game(*[end(i, house(i)) for i in range(1, 7)], end(7, "top", rocks=3))
        assert checks(g) == ["tiny_last_end"]

    def test_a_short_game_is_not_also_a_tiny_end(self):
        g = game(end(1, "top"), end(2, "bottom", rocks=2))
        assert checks(g) == ["short_game"]


def without(e, metric_key, count, value=None):
    """Take a measurement off the first ``count`` rocks of an end."""
    for s in e["shots"][:count]:
        s[metric_key] = value
    return e


class TestCoverage:
    def test_an_end_well_below_normal_is_flagged(self):
        g = game()
        without(g["ends"][1], "target_broom", 10)       # 6/16 = 38% < the 60% floor
        got = autoreview.review_game(g, FLOOR).findings
        assert [(f.check, f.end) for f in got] == [("coverage_broom", 2)]
        assert "6/16" in got[0].detail and "60%" in got[0].detail

    def test_below_normal_by_fewer_than_four_rocks_is_not(self):
        g = game()
        without(g["ends"][1], "target_broom", 3)        # 13/16, below a 0.9 normal
        base = Baseline({("fours", "broom"): 0.9}, {"fours": 500})
        assert checks(g, base) == []

    def test_normal_comes_from_the_baseline(self):
        g = game()
        without(g["ends"][1], "target_broom", 10)       # 38%, above a 25% normal
        base = Baseline({("fours", "broom"): 0.25}, {"fours": 500})
        assert checks(g, base) == []

    def test_doubles_without_brooms_are_normal(self):
        g = doubles_game()
        for e in g["ends"]:
            without(e, "target_broom", 10)
        assert checks(g) == []

    @pytest.mark.parametrize("key,metric", [("long_split_s", "split"), ("line", "line"),
                                            ("t_release_s", "release")])
    def test_each_measurement_is_judged(self, key, metric):
        g = game()
        without(g["ends"][2], key, 12)
        assert checks(g) == [f"coverage_{metric}"]

    def test_ends_too_short_to_judge_are_skipped(self):
        g = game(end(1, "top", rocks=5), end(2, "bottom"), end(3, "top"), end(4, "bottom"))
        without(g["ends"][0], "target_broom", 5)
        assert checks(g) == []


class TestOddRocks:
    @pytest.mark.parametrize("broom", [{"x": 2.5, "y": 0.0}, {"x": 0.0, "y": -2.2},
                                       {"x": 0.0, "y": 7.5}])
    def test_a_broom_off_the_sheet(self, broom):
        g = game()
        g["ends"][0]["shots"][3]["target_broom"] = {**broom, "confidence": 0.8}
        got = autoreview.review_game(g, FLOOR)
        assert [(f.check, f.strength, f.end, f.rock) for f in got.findings] == [
            ("broom_off_sheet", "strong", 1, 4)]

    def test_one_odd_split_is_weak_and_alone_flags_nothing(self):
        g = game()
        g["ends"][1]["shots"][8]["long_split_s"] = 23.4
        got = autoreview.review_game(g, FLOOR)
        assert [(f.check, f.strength, f.detail) for f in got.findings] == [
            ("odd_split", "weak", "split 23.4 s")]
        assert not got.raise_flag

    def test_two_weak_findings_flag_the_game(self):
        g = game()
        g["ends"][1]["shots"][8]["long_split_s"] = 6.5
        g["ends"][2]["shots"][2]["color_inferred"] = True
        got = autoreview.review_game(g, FLOOR)
        assert sorted(f.check for f in got.findings) == ["colour_inferred", "odd_split"]
        assert got.raise_flag

    def test_rocks_seen_but_not_placed(self):
        g = game()
        g["ends"][3]["unplaced_shots"] = 2
        assert checks(g) == ["unplaced"]


class TestNotes:
    def test_notes_are_listed_but_never_flag(self):
        g = game(hammer=False)
        g["ends"][1]["score"] = {"red": 2, "yellow": 0}
        g["ends"][2]["shots"][0]["line"] = {"at_broom": {"miss_m": 1.4}}
        g["ends"][2]["shots"][1]["line"] = {"at_broom": {"miss_m": -1.2}}
        got = autoreview.review_game(g, FLOOR)
        assert got.findings == [] and not got.raise_flag
        assert [(n.check, n.end) for n in got.notes] == [
            ("board_disagrees", 2), ("hammer", None), ("broom_miss", None)]
        assert got.notes[2].detail == "2 lines miss their broom by more than 1.0 m"

    def test_an_end_the_board_never_read_says_nothing(self):
        g = game()
        g["ends"][1]["score"] = None
        assert autoreview.review_game(g, FLOOR).notes == []


FIXTURES = Path(__file__).parent / "fixtures" / "autoreview"
# The fours thresholds the 2026-09-27..10-05 backtest gave (p2 of 506 ends) and
# the doubles ones (118 ends), as the endpoint would have them.
BACKTEST = Baseline({("fours", "broom"): 0.5, ("fours", "split"): 0.75,
                     ("fours", "line"): 0.5, ("fours", "release"): 0.88,
                     ("doubles", "broom"): 0.0, ("doubles", "split"): 0.8,
                     ("doubles", "line"): 0.8, ("doubles", "release"): 0.8},
                    {"fours": 506, "doubles": 118})


class TestRealGames:
    """Four hosted games, as served on 2026-10-05, read the way the backtest read them."""

    @pytest.mark.parametrize("sid,expected,flagged", [
        # Super League 09/30 S5 (EUpp): the brooms and lines of two ends lost,
        # and one end missing all 8 of one colour.
        ("s_0qsNY1Vdc3vynNPx5", ["coverage_broom", "coverage_line", "missing_rocks"], True),
        # Supper 09/29 S3 (LFvF): e8 back to e7's house with 4 rocks -- the
        # next draw's first -- plus an unplaced rock and an odd split.
        ("s_11DBbXARm30gMQEsy", ["odd_split", "same_house", "tiny_last_end", "unplaced"], True),
        ("s_00hRGMbcChFTNviqg", [], False),          # a clean fours game, 7 ends
        ("s_0PXVGfZmiusxIlyX5", [], False),          # a clean doubles game, no brooms
    ])
    def test_the_backtest_reading(self, sid, expected, flagged):
        doc = json.loads((FIXTURES / f"{sid}.json").read_text())
        got = autoreview.review_game(doc["games"][0], BACKTEST)
        assert sorted({f.check for f in got.findings}) == expected
        assert got.raise_flag is flagged


def test_one_wide_line_reads_in_the_singular():
    g = game()
    g["ends"][0]["shots"][0]["line"] = {"at_broom": {"miss_m": 1.4}}
    (note,) = autoreview.review_game(g, FLOOR).notes
    assert note.detail == "1 line misses its broom by more than 1.0 m"
