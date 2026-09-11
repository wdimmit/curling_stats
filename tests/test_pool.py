import pytest

from curling_score.harvest import pool


class TestGridMoments:
    def test_takes_the_requested_number(self):
        times = [100.0 + i * 0.2 for i in range(150)]
        assert len(pool.grid_moments(times, 2)) == 2

    def test_spreads_them_through_the_clip(self):
        # Two moments a fifth of a second apart are the same frame; the point
        # of a half-minute clip is that its ends differ.
        times = [100.0 + i * 0.2 for i in range(150)]
        span = times[-1] - times[0]
        a, b = pool.grid_moments(times, 2)
        assert b - a > span / 4

    def test_avoids_the_very_edges(self):
        # The first moments of a clip are its keyframe lead, before the window
        # anyone asked for.
        times = [100.0 + i * 0.2 for i in range(150)]
        picked = pool.grid_moments(times, 2)
        assert picked[0] > times[0]
        assert picked[-1] < times[-1]

    def test_a_short_clip_gives_what_it_has(self):
        assert pool.grid_moments([1.0], 3) == [1.0]

    def test_no_moments_gives_nothing(self):
        assert pool.grid_moments([], 3) == []

    def test_never_repeats_a_moment(self):
        times = [100.0 + i * 0.2 for i in range(6)]
        assert len(set(pool.grid_moments(times, 5))) == len(pool.grid_moments(times, 5))


class TestPickMotionMoments:
    def test_snaps_a_flight_to_moments_that_exist(self):
        # find_flights reports the track's own times, which are a subset of the
        # decoded moments; a frame can only be saved if it was decoded.
        times = [100.0 + i * 0.2 for i in range(50)]
        got = pool.nearest_moments([100.05, 102.31, 104.99], times)
        assert all(t in times for t in got)

    def test_keeps_them_distinct(self):
        times = [100.0, 100.2, 100.4]
        assert len(set(pool.nearest_moments([100.01, 100.02, 100.03], times))) == 1

    def test_returns_them_in_order(self):
        times = [100.0 + i * 0.2 for i in range(50)]
        got = pool.nearest_moments([104.9, 100.1, 102.3], times)
        assert got == sorted(got)


class TestNeighbours:
    def test_finds_the_moments_either_side(self):
        times = [100.0 + i * 0.2 for i in range(10)]
        assert pool.neighbours_of(100.6, times) == (100.4, pytest.approx(100.8))

    def test_at_the_start_there_is_only_one_side(self):
        times = [100.0, 100.2, 100.4]
        assert pool.neighbours_of(100.0, times) == (None, 100.2)

    def test_at_the_end_there_is_only_one_side(self):
        times = [100.0, 100.2, 100.4]
        assert pool.neighbours_of(100.4, times) == (100.2, None)
