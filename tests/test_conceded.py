"""A game conceded part-way through its last end: the rocks after the last one
thrown were never thrown, and say so.

10/15 Thursday Mens sheet 2, 7 pm: red went into the last end 5-7 down, and
after rock 13 -- red's skip's first, with one red to come -- the players
cleared the house and left the ice. The filler had read the skip's long
pause before that rock as a gap holding two unseen rocks, so the chart showed
rocks 13 and 14 missing, the red as 15 and 16 missing (`shots.Unseen`). A team
concedes when it does not have enough rocks left to tie or win.
"""

import pytest

from curling_score import analyze, timeline
from curling_score.game import format as format_mod, scoreboard as sb
from curling_score.game.segment import EndSegment, GameSegment


def shot(n, colour, *, missing=False, unseen=None):
    return {"number": n, "color": colour, "missing": missing, "unseen": unseen,
            "color_inferred": missing, "shot_type": "unknown" if missing else "draw",
            "shot_type_source": "auto", "label": f"8th end, rock {n}"}


def sheet_2(**over):
    """12 rocks seen, a gap pair, the red seen, a tail blank."""
    shots = [shot(n, "red" if n % 2 else "yellow") for n in range(1, 13)]
    shots += [shot(13, "red", missing=True, unseen="gap"),
              shot(14, "yellow", missing=True, unseen="gap"),
              shot(15, "red"),
              shot(16, "yellow", missing=True, unseen="tail")]
    end = {"number": 8, "shots": shots, "problems": ["saw 13 deliveries, an end has 16"],
           "complete": False}
    end.update(over)
    return end


class TestConcede:
    def test_the_rocks_after_the_last_thrown_were_not_thrown(self):
        end = sheet_2()
        assert timeline.concede(end, {"red": 5, "yellow": 7})
        got = [(s["number"], s["color"], s["missing"], s["shot_type"]) for s in end["shots"]]
        assert got[12:] == [(13, "red", False, "draw"),
                            (14, "yellow", True, "not_thrown"),
                            (15, "red", True, "not_thrown"),
                            (16, "yellow", True, "not_thrown")]
        # The red seen was numbered 15 when charted; overrides keep that name.
        assert end["shots"][12]["id"] == 15
        assert end["shots"][12]["label"] == "8th end, skip's first rock"
        assert end["conceded_after"] == 13
        assert end["problems"] == ["conceded after rock 13"]
        assert all(s["shot_type_source"] == "conceded" for s in end["shots"][13:])

    def test_a_team_with_rocks_enough_to_tie_had_not_conceded(self):
        """Red one down with a rock to come could still tie: those rocks
        were thrown and not seen."""
        end = sheet_2()
        assert not timeline.concede(end, {"red": 6, "yellow": 7})
        assert [s["number"] for s in end["shots"] if s["missing"]] == [13, 14, 16]

    def test_a_tied_game_is_not_judged(self):
        assert not timeline.concede(sheet_2(), {"red": 7, "yellow": 7})

    def test_without_a_score_nothing_is_judged(self):
        assert not timeline.concede(sheet_2(), None)

    def test_a_rock_the_house_proved_was_thrown_stays_missing(self):
        """Stones on the sheet that need a rock nobody saw are evidence it was
        thrown; only a pause's guess gives way."""
        end = sheet_2()
        end["shots"][12]["unseen"] = end["shots"][13]["unseen"] = "house"
        assert timeline.concede(end, {"red": 5, "yellow": 7})
        got = [(s["number"], s["missing"], s["shot_type"]) for s in end["shots"]][12:]
        assert got == [(13, True, "unknown"), (14, True, "unknown"),
                       (15, False, "draw"), (16, True, "not_thrown")]
        assert end["conceded_after"] == 15

    def test_an_end_seen_for_less_than_half_is_not_judged(self):
        shots = [shot(n, "red" if n % 2 else "yellow") for n in range(1, 5)]
        shots += [shot(n, "red" if n % 2 else "yellow", missing=True, unseen="tail")
                  for n in range(5, 17)]
        assert not timeline.concede({"number": 8, "shots": shots}, {"red": 0, "yellow": 9})

    def test_an_end_with_every_rock_accounted_for_is_not_conceded(self):
        end = sheet_2()
        end["shots"][15] = shot(16, "yellow")
        end["shots"][12]["unseen"] = end["shots"][13]["unseen"] = "house"
        assert not timeline.concede(end, {"red": 5, "yellow": 7})


def built(number, shots, start_s):
    out = timeline.build_end(number, ("top", "bottom")[number % 2], start_s, start_s + 850.0, [])
    out["shots"], out["deliveries_seen"], out["releases_seen"] = shots, 16, 16
    return out


def game_of(n_ends=8, end_s=None):
    ends = [EndSegment(number=i, house=("top", "bottom")[i % 2], start_s=i * 900.0,
                       end_s=i * 900.0 + 850.0) for i in range(1, n_ends + 1)]
    return GameSegment(index=0, start_s=ends[0].start_s, end_s=end_s or ends[-1].end_s,
                       ends=ends, closed=True)


def full(number):
    return [shot(n, "red" if n % 2 else "yellow") for n in range(1, 17)]


def board_5_7():
    """Red 5, yellow 7 over ends 1-7."""
    per_end = {1: {"red": 0, "yellow": 2}, 2: {"red": 1, "yellow": 0}, 3: {"red": 0, "yellow": 2},
               4: {"red": 0, "yellow": 1}, 5: {"red": 3, "yellow": 0}, 6: {"red": 0, "yellow": 2},
               7: {"red": 1, "yellow": 0}}
    scores = sb.BoardScores(per_end=per_end, unread_ends=(8,), final=None)
    return analyze.BoardRead(got=sb.GameBoard(scores=scores, read_at_s=0.0, reads=1,
                                              board=sb.CardBoard(red=(), yellow=())),
                             scores=scores, block={"per_end": {str(k): v for k, v in per_end.items()}})


class TestTheGameIsFinished:
    def ends(self):
        out = [built(n, full(n), n * 900.0) for n in range(1, 8)]
        last = sheet_2()
        return out + [built(8, last["shots"], 8 * 900.0)]

    def test_its_last_end_is_conceded_by_the_board_s_score(self):
        got = analyze.finish_game(game_of(), self.ends(), board_5_7(), format_mod.FOURS,
                                  recorded_s=8 * 900.0 + 850.0 + 3600.0)
        assert got["ends"][-1]["conceded_after"] == 13

    def test_a_recording_that_stopped_with_the_end_is_not_a_concession(self):
        """Kozai Draw 5: the club's feed died mid-end at 16:01."""
        got = analyze.finish_game(game_of(), self.ends(), board_5_7(), format_mod.FOURS,
                                  recorded_s=8 * 900.0 + 850.0 + 30.0)
        assert "conceded_after" not in got["ends"][-1]

    def test_a_game_still_being_played_is_not_judged(self):
        game = game_of()
        game.closed = False
        got = analyze.finish_game(game, self.ends(), board_5_7(), format_mod.FOURS,
                                  recorded_s=8 * 900.0 + 850.0 + 3600.0)
        assert "conceded_after" not in got["ends"][-1]

    def test_without_a_board_the_detected_score_judges(self):
        ends = self.ends()
        for e, s in zip(ends[:7], ({"red": 0, "yellow": 2},) * 4 + ({"red": 1, "yellow": 0},) * 3):
            e["detected_score"] = s
        got = analyze.finish_game(game_of(), ends, None, format_mod.FOURS,
                                  recorded_s=8 * 900.0 + 850.0 + 3600.0)
        assert got["ends"][-1]["conceded_after"] == 13


class TestALiveStream:
    def test_its_finished_game_s_last_end_is_conceded(self):
        """The live session publishes the same judgement once the game is
        over: the sheet sat empty after it while the stream went on."""
        from tests import test_live_session as T

        class Pipe(T.Pipeline):
            def build_end(self, ctx, game, end, prev_end_s):
                built, closed = super().build_end(ctx, game, end, prev_end_s)
                built["shots"] = sheet_2()["shots"] if end.start_s >= 2390.0 else full(end.number)
                built["deliveries_seen"] = built["releases_seen"] = 16
                return built, closed

        pipe, rec, pub = Pipe(), T.Recording(), []
        s = T.session(pipe, rec, pub)
        pipe.board = T.board(("yellow", 2, 1), ("yellow", 4, 2), ("yellow", 5, 3), n_ends=4)
        T.run_until(s, rec, 4200.0)
        last = pub[-1]["games"][0]["ends"][-1]
        assert last["conceded_after"] == 13
        assert [x["shot_type"] for x in last["shots"][13:]] == ["not_thrown"] * 3


def test_the_review_does_not_call_a_conceded_end_s_rocks_missing():
    from curling_score import autoreview

    end = sheet_2()
    timeline.concede(end, {"red": 5, "yellow": 7})
    end["shots_expected"] = 16
    assert autoreview._missing(end, first=False, last=False) == []
