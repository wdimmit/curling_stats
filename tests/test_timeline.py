import json

import pytest

from curling_score import timeline
from curling_score.detect.rocks import Detection
from curling_score.game import shots as S


def det(color, x, y):
    return Detection(color=color, x_m=x, y_m=y, x_px=0.0, y_px=0.0,
                     area_px=150.0, confidence=0.9)


def shot(n, color, stones, t=100.0, missing=False):
    return S.Shot(number=n, color=color, stones=list(stones), t_rest_s=t,
                  missing=missing)


class TestBuildEnd:
    def test_scores_the_end_from_the_final_house(self):
        final = [det("yellow", 0.1, 0.0), det("red", 1.0, 0.0)]
        end = timeline.build_end(
            number=1, house="top", start_s=0.0, end_s=900.0,
            shots=[shot(1, "red", []), shot(2, "yellow", final)],
        )
        assert end["score"] == {"red": 0, "yellow": 1}

    def test_reads_the_hammer_from_who_threw_first(self):
        end = timeline.build_end(
            number=1, house="top", start_s=0.0, end_s=900.0,
            shots=[shot(1, "red", []), shot(2, "yellow", [])],
        )
        assert end["hammer"] == "yellow"

    def test_labels_every_shot_the_way_a_curler_says_it(self):
        end = timeline.build_end(
            number=3, house="top", start_s=0.0, end_s=900.0,
            shots=[shot(i, "red" if i % 2 else "yellow", []) for i in range(1, 10)],
        )
        assert end["shots"][0]["label"] == "3rd end, lead's first rock"
        assert end["shots"][4]["label"] == "3rd end, second's first rock"
        assert end["shots"][8]["label"] == "3rd end, third's first rock"

    def test_scores_from_the_last_house_actually_seen(self):
        # A trailing delivery that was never observed carries no stones. Scoring
        # from it turned real ends into blanks on the reference VOD.
        final = [det("yellow", 0.1, 0.0), det("red", 1.0, 0.0)]
        end = timeline.build_end(
            number=1, house="top", start_s=0.0, end_s=900.0,
            shots=[shot(1, "red", []), shot(2, "yellow", final),
                   shot(3, "red", [], missing=True)],
        )
        assert end["score"] == {"red": 0, "yellow": 1}

    def test_reports_which_shot_the_score_was_read_from(self):
        final = [det("yellow", 0.1, 0.0)]
        end = timeline.build_end(
            number=1, house="top", start_s=0.0, end_s=900.0,
            shots=[shot(1, "red", final), shot(2, "yellow", [], missing=True)],
        )
        assert end["scored_from_shot"] == 1

    def test_an_end_with_no_shots_is_blank(self):
        end = timeline.build_end(number=1, house="top", start_s=0.0, end_s=1.0, shots=[])
        assert end["score"] == {"red": 0, "yellow": 0}
        assert end["hammer"] is None


class TestBuildGame:
    def test_accumulates_the_running_score(self):
        ends = [
            {"number": 1, "score": {"red": 0, "yellow": 2}},
            {"number": 2, "score": {"red": 1, "yellow": 0}},
        ]
        game = timeline.build_game(0, 0.0, 1800.0, ends)
        assert game["final"] == {"red": 1, "yellow": 2}
        assert game["ends"][0]["running"] == {"red": 0, "yellow": 2}
        assert game["ends"][1]["running"] == {"red": 1, "yellow": 2}


class TestSerialisation:
    def test_the_timeline_round_trips_through_json(self):
        final = [det("yellow", 0.1, 0.0)]
        end = timeline.build_end(1, "top", 0.0, 900.0, [shot(1, "red", final)])
        doc = timeline.build_document(
            video_id="VXU9xwmugRg", url="u", sheet=2, duration_s=14392.0,
            calibration={}, games=[timeline.build_game(0, 0.0, 900.0, [end])],
        )
        assert json.loads(json.dumps(doc)) == doc

    def test_every_shot_carries_a_deep_link_to_the_video(self):
        end = timeline.build_end(1, "top", 0.0, 900.0, [shot(1, "red", [], t=61.4)])
        doc = timeline.build_document(
            video_id="VXU9xwmugRg", url="u", sheet=2, duration_s=1.0,
            calibration={}, games=[timeline.build_game(0, 0.0, 900.0, [end])],
        )
        link = doc["games"][0]["ends"][0]["shots"][0]["youtube_url"]
        assert link == "https://youtu.be/VXU9xwmugRg?t=61"


class TestHammerCrossCheck:
    """The hammer read from deliveries is checked against the rules."""

    def test_a_consistent_game_is_marked_agreeing(self):
        ends = [
            {"number": 1, "score": {"red": 0, "yellow": 1}, "hammer": "red"},
            {"number": 2, "score": {"red": 1, "yellow": 0}, "hammer": "red"},
            {"number": 3, "score": {"red": 0, "yellow": 0}, "hammer": "yellow"},
        ]
        game = timeline.build_game(0, 0.0, 900.0, ends)
        assert game["hammer_consistent"] is True
        assert [e["hammer_expected"] for e in game["ends"]] == \
               ["red", "red", "yellow"]

    def test_an_impossible_chain_is_flagged(self):
        # Red cannot hold the hammer in every end while also scoring.
        ends = [
            {"number": 1, "score": {"red": 1, "yellow": 0}, "hammer": "red"},
            {"number": 2, "score": {"red": 1, "yellow": 0}, "hammer": "red"},
        ]
        game = timeline.build_game(0, 0.0, 900.0, ends)
        assert game["hammer_consistent"] is False

    def test_ends_with_no_detected_hammer_do_not_break_it(self):
        ends = [
            {"number": 1, "score": {"red": 0, "yellow": 1}, "hammer": None},
            {"number": 2, "score": {"red": 1, "yellow": 0}, "hammer": None},
        ]
        game = timeline.build_game(0, 0.0, 900.0, ends)
        assert game["hammer_consistent"] is None


class TestMovingAShot:
    """`before` on a patch says the shot was really thrown before another.

    The filler's blanks land at the end of a short end when nothing says
    otherwise; the charter watching the video often knows better. Moving the
    blank has to renumber the end -- the thrower is read off the number -- and
    keep every correction filed where it was.
    """

    def _doc(self):
        def s(n, color, inferred=False):
            return {"number": n, "color": color, "color_inferred": inferred,
                    "missing": inferred, "state_known": not inferred,
                    "label": f"4th end, shot {n}", "position": "lead"}
        return {"games": [{"index": 0, "ends": [{"number": 4, "shots": [
            s(1, "red"), s(2, "yellow"), s(3, "red"), s(4, "yellow"),
            s(5, "red", True), s(6, "yellow", True)]}]}]}

    def _shots(self, overrides):
        return timeline.apply_overrides(self._doc(), overrides)["games"][0]["ends"][0]["shots"]

    def test_two_blanks_moved_to_the_front_lead_the_end(self):
        got = self._shots({"0.4.5": {"before": 1}, "0.4.6": {"before": 1}})
        assert [s["id"] for s in got] == [5, 6, 1, 2, 3, 4]
        assert [s["number"] for s in got] == [1, 2, 3, 4, 5, 6]

    def test_the_throwers_follow_the_new_numbers(self):
        got = self._shots({"0.4.5": {"before": 1}, "0.4.6": {"before": 1}})
        assert got[2]["label"] == "4th end, lead's second rock"
        assert got[2]["rock_of_player"] == 2 and got[2]["position"] == "lead"
        assert got[4]["label"] == "4th end, second's first rock"
        assert [s["has_hammer"] for s in got] == [False, True] * 3

    def test_blanks_take_the_colour_the_alternation_needs(self):
        # The blanks were red/yellow at the tail; in front of a red rock they
        # have to be red then yellow, so the seen rock keeps its own colour.
        got = self._shots({"0.4.5": {"before": 1}, "0.4.6": {"before": 1}})
        assert [s["color"] for s in got] == ["red", "yellow"] * 3
        assert got[2]["color_inferred"] is False

    def test_a_seen_rock_never_changes_colour(self):
        got = self._shots({"0.4.5": {"before": 2}, "0.4.6": {"before": 2}})
        assert [s["id"] for s in got] == [1, 5, 6, 2, 3, 4]
        assert [s["color"] for s in got] == ["red", "yellow", "red", "yellow", "red", "yellow"]

    def test_a_colour_set_by_hand_on_a_blank_is_kept(self):
        got = self._shots({"0.4.6": {"before": 1, "color": "red"}})
        assert got[0]["id"] == 6 and got[0]["color"] == "red"

    def test_corrections_stay_filed_under_the_original_number(self):
        got = self._shots({"0.4.5": {"before": 1}, "0.4.1": {"user_score": 3}})
        moved_first = got[1]
        assert moved_first["id"] == 1 and moved_first["number"] == 2
        assert moved_first["user_score"] == 3 and moved_first["corrected"] is True

    def test_applying_twice_is_the_same_as_once(self):
        ov = {"0.4.5": {"before": 1}, "0.4.6": {"before": 1}, "0.4.3": {"user_score": 2}}
        once = timeline.apply_overrides(self._doc(), ov)
        twice = timeline.apply_overrides(json.loads(json.dumps(once)), ov)
        assert once == twice

    def test_an_end_nobody_moved_is_untouched(self):
        got = self._shots({"0.4.3": {"user_score": 2}})
        assert "id" not in got[0]
        assert [s["number"] for s in got] == [1, 2, 3, 4, 5, 6]
        assert got[0]["label"] == "4th end, shot 1"

    def test_a_target_that_does_not_exist_moves_nothing(self):
        got = self._shots({"0.4.5": {"before": 99}})
        assert [s["number"] for s in got] == [1, 2, 3, 4, 5, 6]
        assert "id" not in got[0]
