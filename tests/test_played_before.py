"""A recording that opens on an end already played starts its numbering at
end 2.

10/15 Thursday Mens: the 7 pm draw began before its streams came up at
19:10. Sheets 1-4 each opened on end 1's rocks at rest in the bottom house,
being brought back, and end 2 began in the top house half a minute after they
cleared. Numbered from 1, every board score sat one end early: the boards
agreed with detection on 3/5, 1/7, 1/5 and 2/7 ends, and on 4/4, 4/6, 4/4 and
5/6 numbered from 2.
"""

import json
from pathlib import Path

import pytest

from curling_score import analyze, timeline
from curling_score.game import boardsplit, scoreboard as sb, segment
from curling_score.game.segment import EndSegment, GameSegment

S = segment.Sample


def profile(spec, step=5.0, t0=0.0):
    """Samples from (n_repeats, top_count, bottom_count) triples."""
    out, t = [], t0
    for n, top, bot in spec:
        for _ in range(n):
            out.append(S(t=t, top_stones=top, bottom_stones=bot))
            t += step
    return out


def numbers(samples):
    return [[e.number for e in g.ends] for g in segment.segment_games(samples)]


class TestOnTheirOwnProfiles:
    """The first 900 s of twelve recordings (tests/fixtures/profiles/heads.json)."""

    DOC = json.loads((Path(__file__).parent / "fixtures" / "profiles"
                      / "heads.json").read_text())

    @pytest.mark.parametrize("vid", sorted(DOC["videos"]))
    def test_an_end_played_before_the_recording_numbers_the_first_from_2(self, vid):
        v = self.DOC["videos"][vid]
        samples = [S(t=t, top_stones=a, bottom_stones=b, top_playable=bool(c),
                     bottom_playable=bool(d)) for t, a, b, c, d in v["samples"]]
        min_end = (10 if v["format"] == "doubles" else 16) * segment.MIN_DELIVERY_GAP_S
        first = segment.segment_games(samples, min_end_s=min_end)[0].ends[0]
        assert first.number == (2 if v["played_before"] else 1), v["title"]


class TestTheRule:
    def test_rocks_at_rest_in_the_other_house_at_the_start(self):
        # 100 s of a finished end's stones, cleared, end 2 30 s later.
        samples = profile([(21, 0, 6), (6, 0, 0), (60, 5, 0), (60, 0, 5)])
        assert numbers(samples) == [[2, 3]]

    def test_a_blip_at_the_start_is_not_an_end(self):
        """A person at the panel's edge for a keyframe or two (10/13 Super
        sheet 1 opened on one "stone" for 15 s)."""
        samples = profile([(2, 0, 1), (6, 0, 0), (60, 5, 0), (60, 0, 5)])
        assert numbers(samples) == [[1, 2]]

    def test_stones_cleared_a_changeover_before_the_game_are_not_an_end(self):
        """_OGoqF5W9-I: practice stones sat 125 s in the top house, and the
        game began 425 s after they cleared."""
        samples = profile([(25, 3, 0), (40, 0, 0), (60, 0, 5), (60, 5, 0)])
        assert numbers(samples) == [[1, 2]]

    def test_stones_in_the_house_of_the_first_end_are_that_end(self):
        """10/11 Skips sheet 2 opened on a stone in the bottom house, where
        end 1 began a minute later."""
        samples = profile([(4, 0, 2), (8, 0, 0), (60, 0, 5), (60, 5, 0)])
        assert numbers(samples) == [[1, 2]]

    def test_a_recording_that_opens_inside_its_first_end_numbers_it_1(self):
        """Joined late (`analyze.joined_late`): its missing rocks are numbered
        first, and it is end 1."""
        samples = profile([(60, 0, 5), (60, 5, 0)])
        assert numbers(samples) == [[1, 2]]

    def test_a_later_game_numbers_from_1(self):
        samples = profile([(21, 0, 6), (6, 0, 0), (60, 5, 0), (60, 0, 5),
                           (60, 0, 0), (60, 0, 5), (60, 5, 0)])
        assert numbers(samples) == [[2, 3], [1, 2]]

    def test_the_live_stream_settles_the_same_numbers(self):
        samples = profile([(21, 0, 6), (6, 0, 0), (60, 5, 0), (60, 0, 5), (80, 5, 0)])
        settled = segment.settled_ends(samples)
        assert [e.number for e in settled[0].ends] == [2, 3]


def from_2(n, index=0, start_s=0.0):
    ends = [EndSegment(number=i + 1, house=("top", "bottom")[i % 2],
                       start_s=start_s + i * 900.0, end_s=start_s + i * 900.0 + 850.0)
            for i in range(1, n + 1)]
    return GameSegment(index=index, start_s=ends[0].start_s, end_s=ends[-1].end_s, ends=ends)


class TestTheNumbersHold:
    def test_the_board_split_keeps_the_first_game_s_numbers(self):
        # Ends 2-6 of one game, a 400 s changeover the board was cleared
        # across, and two of the next game's.
        game = from_2(5)
        later = [EndSegment(number=6 + i, house=("top", "bottom")[i % 2],
                            start_s=game.end_s + 400.0 + i * 900.0,
                            end_s=game.end_s + 1250.0 + i * 900.0) for i in range(2)]
        game = GameSegment(index=0, start_s=game.start_s, end_s=later[-1].end_s,
                           ends=game.ends + later)
        cleared = from_2(5).end_s + 60.0
        board = lambda t0, t1: [(t, "cards" if t < cleared else "blank")
                                for t in range(int(t0), int(t1), 30)]
        a, b = boardsplit.split_games([game], board)
        assert [e.number for e in a.ends] == [2, 3, 4, 5, 6]
        assert [e.number for e in b.ends] == [1, 2]

    def test_the_board_join_numbers_on(self):
        first, second = from_2(4), GameSegment(index=1, start_s=4 * 900.0 + 1300.0, end_s=0.0,
                                                ends=[EndSegment(1, "top", 4 * 900.0 + 1300.0,
                                                                 4 * 900.0 + 2100.0)])
        kept = lambda t0, t1: [(t, "cards") for t in range(int(t0), int(t1), 30)]
        (joined,) = boardsplit.join_games([first, second], kept)
        assert [e.number for e in joined.ends] == [2, 3, 4, 5, 6]

    def test_an_empty_first_end_left_off_leaves_the_next_as_end_2(self):
        assert [e.number for e in analyze.without_first_end(from_2(3)).ends] == [2, 3]

    def test_the_board_s_end_1_starts_the_running_score(self):
        ends = [timeline.build_end(2, "top", 0.0, 10.0, [], board_score={"red": 0, "yellow": 2}),
                timeline.build_end(3, "bottom", 10.0, 20.0, [], board_score={"red": 1, "yellow": 0})]
        board = sb.BoardScores(per_end={1: {"red": 1, "yellow": 0}, 2: ends[0]["score"],
                                        3: ends[1]["score"]},
                               unread_ends=(), final={"red": 2, "yellow": 2})
        game = timeline.build_game(0, 0.0, 20.0, ends, board=board)
        assert [e["running"] for e in game["ends"]] == [{"red": 1, "yellow": 2},
                                                       {"red": 2, "yellow": 2}]

    def test_an_end_1_the_board_never_posted_leaves_the_running_score_unknown(self):
        ends = [timeline.build_end(2, "top", 0.0, 10.0, [], board_score={"red": 0, "yellow": 2})]
        board = sb.BoardScores(per_end={2: ends[0]["score"]}, unread_ends=(1,), final=None)
        game = timeline.build_game(0, 0.0, 10.0, ends, board=board)
        assert game["ends"][0]["running"] is None

    def test_the_board_must_name_the_last_end_to_account_for_every_end(self):
        ends = [timeline.build_end(n, "top", 0.0, 10.0, []) for n in (2, 3, 4)]
        ends[0]["shots"] = [{"number": i + 1, "missing": False} for i in range(16)]
        block = {}
        assert timeline.settle_board_scores(ends, block, highest_end=3)
        assert block["accounts_for_every_end"] is False


class TestALiveStream:
    """The live session numbers from the same settled ends, so a stream that
    came up on end 1's rocks publishes end 2 first, with end 2's card on it."""

    def test_it_publishes_from_end_2_with_the_board_s_end_2(self):
        from tests import test_live_session as T

        def opens_on_end_1(t):
            if t < 100:
                return 0, 6                    # end 1's rocks, being brought back
            if t < 130 or t >= 2830:
                return 0, 0
            k = int((t - 130) // 900)
            return (5, 0) if k % 2 == 0 else (0, 5)

        class Pipe(T.Pipeline):
            def samples(self, path, setups, from_s, until_s, detector=None):
                return [S(t=x.t, top_stones=opens_on_end_1(x.t)[0],
                          bottom_stones=opens_on_end_1(x.t)[1])
                        for x in super().samples(path, setups, from_s, until_s)]

        pipe, rec, pub = Pipe(), T.Recording(), []
        s = T.session(pipe, rec, pub)
        pipe.board = T.board(("red", 1, 1), ("yellow", 2, 2), ("red", 2, 3), ("yellow", 3, 4),
                             n_ends=4)
        T.run_until(s, rec, 3600.0)
        rec.finished = True
        for _ in range(10):
            s.step()
        ends = pub[-1]["games"][0]["ends"]
        assert [e["number"] for e in ends] == [2, 3, 4]
        assert [b["end"] for b in pipe.built] == [2, 3, 4]
        assert ends[0]["score"] == {"red": 0, "yellow": 2}
        assert ends[0]["running"] == {"red": 1, "yellow": 2}
        assert ends[1]["score"] == {"red": 1, "yellow": 0}
        assert ends[2]["running"] == {"red": 2, "yellow": 3}
