"""Which ends of a stream still being recorded can no longer change.

A live stream is segmented again every time more of it arrives. An end may
only be processed once no later footage could move its boundaries, or the
live timeline and the offline one would disagree about where ends begin and
end -- and a posted end would have to be taken back.
"""

import random

import pytest

from curling_score.game import segment

S = segment.Sample


def profile(spec, step=5.0, t0=0.0, jitter=None):
    """Samples from (n_repeats, top_count, bottom_count) triples.

    ``jitter`` draws each step from [1, step] instead, the way the keyframes
    of a live HLS recording arrive.
    """
    rng = random.Random(7)
    out, t = [], t0
    for n, top, bot in spec:
        for _ in range(n):
            out.append(S(t=t, top_stones=top, bottom_stones=bot))
            t += rng.uniform(1.0, step) if jitter else step
    return out


def ends_of(games):
    return [[(e.number, e.house, e.start_s, e.end_s) for e in g.ends] for g in games]


class TestWhenAnEndSettles:
    def test_nothing_settles_while_the_first_end_is_being_played(self):
        assert segment.settled_ends(profile([(180, 0, 6)])) == []

    def test_an_end_settles_once_the_next_end_has_run_long_enough(self):
        samples = profile([(180, 0, 6), (70, 5, 0)])
        settled = segment.settled_ends(samples)
        full = segment.segment_games(samples)
        assert ends_of(settled) == [ends_of(full)[0][:1]]
        assert settled[0].closed is False

    def test_the_next_end_barely_started_does_not_settle_it(self):
        # 295 s of the next end: past MIN_END_S but not past the margin.
        assert segment.settled_ends(profile([(180, 0, 6), (60, 5, 0)])) == []

    def test_a_short_look_at_the_other_house_settles_nothing(self):
        assert segment.settled_ends(profile([(180, 0, 6), (40, 5, 0)])) == []

    def test_an_empty_sheet_for_a_game_gap_closes_the_game(self):
        samples = profile([(180, 0, 6), (180, 5, 0), (70, 0, 0)])
        (game,) = segment.settled_ends(samples)
        assert [e.house for e in game.ends] == ["bottom", "top"]
        assert game.closed is True
        assert game.end_s == game.ends[-1].end_s

    def test_a_later_game_closes_the_earlier_one(self):
        samples = profile([(180, 0, 6), (180, 5, 0), (200, 0, 0), (100, 0, 4)])
        games = segment.settled_ends(samples)
        assert [(g.index, len(g.ends), g.closed) for g in games] == [(0, 2, True)]

    def test_everything_settles_when_the_stream_has_ended(self):
        samples = profile([(180, 0, 6), (100, 5, 0)])
        (game,) = segment.settled_ends(samples, ended=True)
        assert len(game.ends) == 2 and game.closed is True

    def test_an_open_game_ends_where_its_last_settled_end_does(self):
        samples = profile([(180, 0, 6), (180, 5, 0), (100, 0, 6)])
        (game,) = segment.settled_ends(samples)
        assert len(game.ends) == 2
        assert game.end_s == game.ends[-1].end_s

    def test_the_format_sets_how_long_an_end_must_run(self):
        # Doubles: ten deliveries, so 150 s makes an end.
        samples = profile([(120, 0, 6), (50, 5, 0)])
        assert segment.settled_ends(samples) == []
        settled = segment.settled_ends(samples, min_end_s=150.0)
        assert [len(g.ends) for g in settled] == [1]


# A night on one sheet: practice, two games, a dropout, a brief look at the
# other house that is too short to be an end, lights-out gaps.
NIGHT = [(30, 0, 0), (180, 0, 6), (3, 0, 0), (97, 0, 6), (40, 5, 0), (150, 0, 6),
         (180, 5, 0), (60, 0, 0), (180, 0, 5), (140, 4, 0), (200, 0, 0),
         (180, 4, 0), (4, 0, 0), (176, 0, 3), (170, 5, 0), (90, 0, 0)]


@pytest.mark.parametrize("jitter", [False, True])
@pytest.mark.parametrize("min_end_s", [segment.MIN_END_S, 150.0])
def test_every_settled_end_is_the_one_the_whole_video_would_give(jitter, min_end_s):
    samples = profile(NIGHT, jitter=jitter)
    full = segment.segment_games(samples, min_end_s=min_end_s)
    open_checked = 0
    for k in range(1, len(samples) + 1, 3):
        for game in segment.settled_ends(samples[:k], min_end_s=min_end_s):
            final = full[game.index]
            assert ends_of([game])[0] == ends_of([final])[0][:len(game.ends)], k
            assert game.start_s == final.start_s
            if game.closed:
                assert len(game.ends) == len(final.ends), k
            else:
                open_checked += len(game.ends)
    assert open_checked > 50   # the check saw ends settle inside open games
    # And once it has all arrived, the stream's end settles the rest.
    assert ends_of(segment.settled_ends(samples, min_end_s=min_end_s, ended=True)) \
        == ends_of(full)
