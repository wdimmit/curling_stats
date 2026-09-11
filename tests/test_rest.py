import pytest

from curling_score.detect import rest
from curling_score.detect.rocks import Detection


def det(color, x, y, conf=0.9):
    return Detection(color=color, x_m=x, y_m=y, x_px=0.0, y_px=0.0,
                     area_px=150.0, confidence=conf)


def series(*blocks, step=0.5, t0=0.0):
    """Build (t, detections) frames from (n_frames, stones) blocks."""
    out, t = [], t0
    for n, stones in blocks:
        for _ in range(n):
            out.append((t, list(stones)))
            t += step
    return out


A = [det("yellow", 0.2, 0.1)]
AB = [det("yellow", 0.2, 0.1), det("red", -0.4, 0.5)]
ABC = AB + [det("yellow", 1.0, -0.3)]


class TestFindRestStates:
    def test_a_single_steady_configuration_is_one_rest_state(self):
        states = rest.find_rest_states(series((20, A)))
        assert len(states) == 1
        assert [s.color for s in states[0].stones] == ["yellow"]

    def test_each_new_configuration_becomes_its_own_rest_state(self):
        states = rest.find_rest_states(series((20, A), (20, AB), (20, ABC)))
        assert [len(s.stones) for s in states] == [1, 2, 3]

    def test_ignores_a_configuration_that_never_settles(self):
        # Stones in motion, or a player walking through, must not register.
        states = rest.find_rest_states(series((20, A), (2, AB), (20, ABC)))
        assert [len(s.stones) for s in states] == [1, 3]

    def test_a_brief_occlusion_does_not_split_a_rest_state(self):
        # A skip crossing the house hides a stone for a moment.
        states = rest.find_rest_states(series((14, AB), (2, A), (14, AB)))
        assert len(states) == 1
        assert len(states[0].stones) == 2

    def test_records_when_each_state_held(self):
        states = rest.find_rest_states(series((20, A), (20, AB)))
        assert states[0].t_start == pytest.approx(0.0, abs=0.6)
        assert states[1].t_start == pytest.approx(10.0, abs=1.0)

    def test_a_stone_that_moves_slightly_is_the_same_state(self):
        nudged = [det("yellow", 0.205, 0.098)]
        states = rest.find_rest_states(series((15, A), (15, nudged)))
        assert len(states) == 1

    def test_a_stone_that_is_hit_away_is_a_new_state(self):
        moved = [det("yellow", 1.4, -0.9)]
        states = rest.find_rest_states(series((15, A), (15, moved)))
        assert len(states) == 2

    def test_no_frames_yields_no_states(self):
        assert rest.find_rest_states([]) == []

    def test_an_empty_house_can_be_a_rest_state(self):
        states = rest.find_rest_states(series((20, []), (20, A)))
        assert [len(s.stones) for s in states] == [0, 1]


class TestOcclusionFlicker:
    """Real footage flickers: players hide stones for seconds at a time."""

    def test_a_stone_hidden_for_several_seconds_does_not_split_the_state(self):
        # Observed on the reference VOD: a settled house read 7 stones, then 6,
        # then 7 again, producing three "rest states" for one configuration.
        blocks = []
        for _ in range(6):
            blocks.append((16, AB))  # 8 s with both visible
            blocks.append((8, A))  # 4 s with one hidden behind a player
        states = rest.find_rest_states(series(*blocks))
        assert len(states) == 1
        assert len(states[0].stones) == 2

    def test_repeated_flicker_still_yields_one_state_per_configuration(self):
        blocks = []
        for _ in range(4):
            blocks += [(14, ABC), (6, AB)]
        blocks.append((30, ABC))
        for _ in range(4):
            blocks += [(14, AB), (6, A)]
        states = rest.find_rest_states(series(*blocks))
        assert [len(s.stones) for s in states] == [3, 2]

    def test_a_genuine_takeout_is_still_seen_through_the_flicker(self):
        # The stone goes and stays gone, unlike an occlusion.
        blocks = [(14, AB), (6, A), (14, AB), (60, A)]
        states = rest.find_rest_states(series(*blocks))
        assert [len(s.stones) for s in states] == [2, 1]

    def test_an_arriving_stone_is_believed_promptly(self):
        blocks = [(30, A), (30, AB)]
        states = rest.find_rest_states(series(*blocks))
        assert [len(s.stones) for s in states] == [1, 2]


class TestLongOcclusion:
    """Measured on the reference VOD: players hide stones for 5-9 seconds."""

    def test_a_stone_hidden_for_nine_seconds_does_not_split_the_state(self):
        # t=10875-10936 read 6 stones, then 5 for 8.5 s, then 6 again.
        blocks = [(120, AB), (18, A), (120, AB)]
        states = rest.find_rest_states(series(*blocks))
        assert len(states) == 1
        assert len(states[0].stones) == 2

    def test_a_stone_that_never_comes_back_is_a_takeout(self):
        blocks = [(120, AB), (120, A)]
        states = rest.find_rest_states(series(*blocks))
        assert [len(s.stones) for s in states] == [2, 1]

    def test_a_stone_knocked_to_a_new_place_ends_one_state_and_starts_another(self):
        moved = [det("yellow", 0.2, 0.1), det("red", 1.6, -0.8)]
        blocks = [(120, AB), (120, moved)]
        states = rest.find_rest_states(series(*blocks))
        assert len(states) == 2
        assert {(round(s.x_m, 1)) for s in states[1].stones} == {0.2, 1.6}

    def test_a_one_frame_false_positive_is_ignored(self):
        ghost = AB + [det("red", 1.9, 1.9)]
        blocks = [(60, AB), (1, ghost), (60, AB)]
        states = rest.find_rest_states(series(*blocks))
        assert len(states) == 1
        assert len(states[0].stones) == 2


class TestStonesInWindow:
    """The settled house over a span of frames, immune to flicker."""

    def test_returns_the_stones_present_throughout(self):
        got = rest.stones_in_window(series((20, AB)))
        assert len(got) == 2
        assert {s.color for s in got} == {"yellow", "red"}

    def test_averages_away_positional_jitter(self):
        a = [det("yellow", 0.20, 0.10)]
        b = [det("yellow", 0.24, 0.14)]
        got = rest.stones_in_window(series((10, a), (10, b)))
        assert len(got) == 1
        assert got[0].x_m == pytest.approx(0.22, abs=0.02)

    def test_drops_a_stone_seen_in_only_a_few_frames(self):
        got = rest.stones_in_window(series((18, AB), (2, ABC)))
        assert len(got) == 2

    def test_keeps_a_stone_hidden_for_a_moment(self):
        got = rest.stones_in_window(series((10, AB), (2, A), (10, AB)))
        assert len(got) == 2

    def test_an_empty_window_gives_nothing(self):
        assert rest.stones_in_window([]) == []
