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


def pushed(stone, start, end, n):
    """The stone slid in a straight line from `start` to `end` over n frames."""
    return [det(stone.color, start[0] + (end[0] - start[0]) * k / (n - 1),
                start[1] + (end[1] - start[1]) * k / (n - 1)) for k in range(n)]


class TestUntilDisturbed:
    """The house after a shot is read only until a settled stone is moved.

    The last rock of an end is read for twelve seconds, and the players start
    clearing the house about four seconds after it stops (s_0kdoX2XVKN5e2lBUF
    end 4 rock 16: at rest 3376.5, the first stone moved at 3380.2). A settled
    stone that leaves its place is how the clearing shows in the detections.
    """

    STEP = 0.1  # the rate houses are read at, analyze.SHOT_FPS

    def frames(self, per_frame):
        return [(k * self.STEP, list(d)) for k, d in enumerate(per_frame)]

    def test_a_quiet_house_is_read_in_full(self):
        f = self.frames([AB] * 120)
        assert rest.until_disturbed(f) == f

    def test_it_stops_where_a_settled_stone_starts_to_move(self):
        red = AB[1]
        slide = pushed(red, (-0.4, 0.5), (1.0, -1.5), 30)       # 0.08 m a frame
        f = self.frames([AB] * 40 + [[AB[0], s] for s in slide] + [[AB[0]]] * 50)
        got = rest.until_disturbed(f)
        assert 40 <= len(got) <= 42     # its first frame beyond jitter
        assert len(rest.stones_in_window(got)) == 2

    def test_jitter_is_not_a_push(self):
        wobble = [[AB[0], det("red", -0.4 + 0.04 * (k % 3 - 1), 0.5)] for k in range(120)]
        f = self.frames(wobble)
        assert len(rest.until_disturbed(f)) == 120

    def test_a_stone_still_rolling_when_the_shooter_stopped_is_part_of_the_shot(self):
        # A struck stone rolls on for three seconds after the shooter rests. It
        # never settled before it moved, so it is the shot, not the clearing.
        roll = pushed(det("red", 0, 0), (0.0, 1.0), (0.0, -0.8), 30)
        f = self.frames([[AB[0], s] for s in roll] + [[AB[0], roll[-1]]] * 90)
        assert len(rest.until_disturbed(f)) == 120

    def hit(self, n_before, n_after):
        """A settled yellow, struck by a red that arrives at 0.15 m a frame --
        too fast to track, so it is a string of single sightings -- and knocked
        0.44 m back, where it settles again (end 4 rock 11 of the same game)."""
        y0 = det("yellow", 0.23, -1.13)
        incoming = [[y0, det("red", 0.45, -0.01 - 0.15 * k)] for k in range(7)]
        knocked = pushed(y0, (0.23, -1.13), (-0.14, -1.38), 8)
        return ([[y0]] * n_before + incoming + [[s, det("red", 0.5, -1.0)] for s in knocked]
                + [[knocked[-1], det("red", 0.5, -1.0)]] * n_after)

    def test_a_settled_stone_struck_by_one_still_moving_is_part_of_the_shot(self):
        # The shooter stopped; the stone it hit rolled on and struck a third
        # 1.5 s later. The house is the one after that, not before it.
        f = self.frames(self.hit(8, 90))
        assert len(rest.until_disturbed(f)) == len(f)

    def test_a_stone_that_settles_after_a_hit_can_still_be_cleared(self):
        f = self.hit(8, 40)
        y = f[-1][0]
        f += [[s] for s in pushed(y, (y.x_m, y.y_m), (1.5, -1.9), 20)]
        got = rest.until_disturbed(self.frames(f))
        assert 8 + 7 + 8 + 38 <= len(got) <= 8 + 7 + 8 + 42

    def test_a_track_that_wanders_off_and_back_is_not_a_push(self):
        # The stone stayed put; its track followed a stray detection away while
        # a player stood over it, and came back. Nine of thirteen mid-end cuts
        # across sixteen games were this.
        red = AB[1]
        away = pushed(red, (-0.4, 0.5), (-0.4, 1.1), 8)
        f = self.frames([AB] * 30 + [[AB[0], s] for s in away + away[::-1]] + [AB] * 60)
        assert len(rest.until_disturbed(f)) == len(f)

    def test_a_stone_hidden_by_a_player_comes_back_to_the_same_place(self):
        f = self.frames([AB] * 30 + [A] * 50 + [AB] * 40)
        assert len(rest.until_disturbed(f)) == 120

    def test_an_empty_window_stays_empty(self):
        assert rest.until_disturbed([]) == []
