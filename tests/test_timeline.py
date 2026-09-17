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


class TestTimingFields:
    """The split and the clock reach the document, and say when they cannot."""

    def _shot(self, n, color, t_rest, t_rel=None, y_enter=4.5):
        from curling_score.detect.delivery import Delivery
        from curling_score.detect.release import Release
        tr, t, y = [], t_rest - 6.0, y_enter
        while y >= 0.2:
            tr.append((round(t, 3), 0.0, round(y, 4)))
            y -= 0.08
            t += 0.1
        dv = Delivery(color=color, t_enter=tr[0][0], t_rest=t_rest,
                      entry_y_m=y_enter, rest_x_m=0.0, rest_y_m=tr[-1][2],
                      travel_m=y_enter - tr[-1][2], track=tuple(tr))
        rel = None
        if t_rel is not None:
            rt, tt, yy = [], t_rel, -2.0
            # Past the hog line's paint at ``split.HOG_APPARENT_Y_M``: a throw
            # the camera loses short of it has no split, by design.
            while yy <= 4.8:
                rt.append((round(tt, 3), 0.05, round(yy, 4)))
                yy += 0.4
                tt += 0.2
            rel = Release(color=color, t=rt[0][0], y_exit_m=rt[-1][2],
                          speed_m_s=2.0, track=tuple(rt))
        return S.Shot(number=n, color=color, stones=[det(color, 0.1, 0.2)],
                      t_rest_s=t_rest, delivery=dv, release=rel)

    def test_a_measured_shot_carries_its_split_and_clock(self):
        end = timeline.build_end(
            number=1, house="top", start_s=0.0, end_s=900.0,
            shots=[self._shot(1, "red", 100.0, t_rel=70.0),
                   self._shot(2, "yellow", 160.0, t_rel=130.0)],
        )
        second = end["shots"][1]
        assert second["t_release_s"] == pytest.approx(130.0)
        assert second["t_tee_s"] == pytest.approx(131.0, abs=0.05)
        assert second["long_split_s"] is not None
        assert second["thinking_time_s"] == pytest.approx(26.0, abs=0.1)
        assert end["splits_measured"] == 2

    def test_a_shot_with_no_release_reports_none_rather_than_zero(self):
        end = timeline.build_end(
            number=1, house="top", start_s=0.0, end_s=900.0,
            shots=[self._shot(1, "red", 100.0), self._shot(2, "yellow", 160.0)],
        )
        for s in end["shots"]:
            assert s["t_release_s"] is None
            assert s["long_split_s"] is None
            assert s["thinking_time_s"] is None
        assert end["splits_measured"] == 0
        assert end["thinking_time"]["measured_shots"] == 0
        assert end["thinking_time"]["unmeasured_shots"] == 2

    def test_the_end_totals_the_clock_by_colour(self):
        end = timeline.build_end(
            number=1, house="top", start_s=0.0, end_s=900.0,
            shots=[self._shot(1, "red", 100.0, t_rel=70.0),
                   self._shot(2, "yellow", 160.0, t_rel=130.0)],
        )
        assert end["thinking_time"]["yellow"] == pytest.approx(26.0, abs=0.1)
        assert end["thinking_time"]["red"] == 0.0
        assert end["thinking_time"]["anomalies"] == 0

    def test_a_game_adds_its_ends_up(self):
        one = timeline.build_end(
            number=1, house="top", start_s=0.0, end_s=900.0,
            shots=[self._shot(1, "red", 100.0, t_rel=70.0),
                   self._shot(2, "yellow", 160.0, t_rel=130.0)],
        )
        two = dict(one, number=2)
        game = timeline.build_game(0, 0.0, 1800.0, [one, two])
        assert game["thinking_time"]["yellow"] == pytest.approx(
            2 * one["thinking_time"]["yellow"], abs=0.01)
        assert game["splits_measured"] == 2 * one["splits_measured"]

    def test_the_schema_version_says_the_shape_changed(self):
        assert timeline.SCHEMA_VERSION == 3

    def test_a_placeholder_shot_has_no_timings(self):
        end = timeline.build_end(
            number=1, house="top", start_s=0.0, end_s=900.0,
            shots=[self._shot(1, "red", 100.0, t_rel=70.0),
                   shot(2, "yellow", [], missing=True)],
        )
        assert end["shots"][1]["long_split_s"] is None
        assert end["shots"][1]["thinking_time_s"] is None


class TestTrimmingThePracticeOff:
    """A start time given at submission is a floor on the game, not a hint.

    Club streams open with practice: players slide rocks for twenty minutes
    before the first end. The sheet never sits empty long enough for
    :func:`segment.segment_games` to call that a separate game, and each block
    of it clears ``MIN_END_S``, so the practice arrives as leading ends with a
    handful of shots each -- numbered, scored, and ahead of the real first end.
    The submitter said where the game starts; this is where that gets used.
    """

    def _end(self, number, start_s, n_shots, score=None):
        shots = [shot(i, "red" if i % 2 else "yellow", []) for i in range(1, n_shots + 1)]
        out = timeline.build_end(number=number, house="top", start_s=start_s,
                                 end_s=start_s + 300.0, shots=shots)
        if score is not None:
            out["score"] = dict(score)
        return out

    def _doc(self, ends):
        return {"games": [timeline.build_game(0, ends[0]["start_s"],
                                              ends[-1]["end_s"], ends)]}

    def _practice_doc(self):
        """The shape the Year End Classic chart arrived in: three, then seven."""
        return self._doc([
            self._end(1, 0.0, 6, {"red": 0, "yellow": 1}),
            self._end(2, 530.0, 5),
            self._end(3, 1135.0, 11),
            self._end(4, 1515.0, 16, {"red": 0, "yellow": 1}),
            self._end(5, 2385.0, 16, {"red": 1, "yellow": 0}),
            self._end(6, 3280.0, 16),
            self._end(7, 4210.0, 16),
        ])

    def _ends(self, doc):
        return doc["games"][0]["ends"]

    def test_leading_practice_ends_are_dropped(self):
        got = timeline.trim_to_start(self._practice_doc(), 1440.0)
        assert [e["start_s"] for e in self._ends(got)] == [1515.0, 2385.0, 3280.0, 4210.0]

    def test_the_survivors_are_numbered_from_one(self):
        got = timeline.trim_to_start(self._practice_doc(), 1440.0)
        assert [e["number"] for e in self._ends(got)] == [1, 2, 3, 4]

    def test_the_shots_are_relabelled_to_the_new_end(self):
        got = timeline.trim_to_start(self._practice_doc(), 1440.0)
        assert self._ends(got)[0]["shots"][0]["label"] == "1st end, lead's first rock"

    def test_corrections_stay_filed_under_the_number_they_were_saved_with(self):
        # What the charter graded as "0.4.3" was the fourth end when they
        # graded it. Renumbering it to the first must not lose that work.
        doc = timeline.trim_to_start(self._practice_doc(), 1440.0)
        got = timeline.apply_overrides(doc, {"0.4.3": {"user_score": 3}})
        first = self._ends(got)[0]
        assert first["id"] == 4 and first["number"] == 1
        assert first["shots"][2]["user_score"] == 3

    def test_the_running_score_forgets_the_practice(self):
        got = timeline.trim_to_start(self._practice_doc(), 1440.0)
        assert got["games"][0]["final"] == {"red": 1, "yellow": 1}

    def test_the_game_starts_where_its_first_real_end_does(self):
        got = timeline.trim_to_start(self._practice_doc(), 1440.0)
        assert got["games"][0]["start_s"] == 1515.0

    def test_a_full_end_before_the_start_time_stops_the_trim(self):
        # The guard. Sixteen rocks were thrown, so this was a real end
        # whatever time was typed into the box -- a fat chart is recoverable,
        # a silently amputated one is not.
        doc = self._doc([self._end(1, 0.0, 16), self._end(2, 900.0, 16),
                         self._end(3, 1800.0, 16)])
        got = timeline.trim_to_start(doc, 1500.0)
        assert [e["number"] for e in self._ends(got)] == [1, 2, 3]
        assert "id" not in self._ends(got)[0]

    def test_a_short_end_after_the_start_time_is_kept(self):
        # Conceded, or the detector lost rocks. Either way it is the game.
        doc = self._doc([self._end(1, 0.0, 5), self._end(2, 1600.0, 7),
                         self._end(3, 2500.0, 16)])
        got = timeline.trim_to_start(doc, 1440.0)
        assert [e["start_s"] for e in self._ends(got)] == [1600.0, 2500.0]

    def test_no_start_time_changes_nothing(self):
        doc = self._practice_doc()
        assert timeline.trim_to_start(doc, None) == doc

    def test_a_start_time_before_the_first_end_changes_nothing(self):
        doc = self._practice_doc()
        assert timeline.trim_to_start(doc, 0.0) == doc

    def test_it_never_empties_a_game(self):
        # Every end short and the start time past all of them: the time points
        # at nothing. One end left standing beats a chart with no game in it.
        doc = self._doc([self._end(1, 0.0, 5), self._end(2, 600.0, 6),
                         self._end(3, 1200.0, 4)])
        got = timeline.trim_to_start(doc, 99999.0)
        assert [e["number"] for e in self._ends(got)] == [1]
        assert self._ends(got)[0]["id"] == 3

    def test_trimming_twice_is_the_same_as_once(self):
        once = timeline.trim_to_start(self._practice_doc(), 1440.0)
        twice = timeline.trim_to_start(json.loads(json.dumps(once)), 1440.0)
        assert once == twice

    def test_it_leaves_the_pristine_document_alone(self):
        doc = self._practice_doc()
        before = json.dumps(doc)
        timeline.trim_to_start(doc, 1440.0)
        assert json.dumps(doc) == before

    def test_the_scoreboard_verdict_is_recomputed_against_the_real_game(self):
        # The board on the wall shows the game, never the practice, so a
        # disagreement caused by practice scores has to clear when they go.
        doc = self._practice_doc()
        doc["games"][0]["scoreboard"] = {
            "final": {"red": 1, "yellow": 1}, "per_end": None,
            "agrees_with_detection": False,
        }
        got = timeline.trim_to_start(doc, 1440.0)
        assert got["games"][0]["scoreboard"]["agrees_with_detection"] is True
        assert all(e["scoreboard_agrees"] is True for e in self._ends(got))
