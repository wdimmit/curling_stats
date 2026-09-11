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
