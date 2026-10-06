"""The nightly review's reading of one game: what counts as wrong, and how
sure it has to be before a game is flagged."""

from datetime import datetime, timezone

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
