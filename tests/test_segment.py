import pytest

from curling_score.game import segment

S = segment.Sample


def profile(spec, step=5.0, t0=0.0):
    """Build samples from (n_repeats, top_count, bottom_count) triples."""
    out, t = [], t0
    for n, top, bot in spec:
        for _ in range(n):
            out.append(S(t=t, top_stones=top, bottom_stones=bot,
                         top_playable=True, bottom_playable=True))
            t += step
    return out


class TestEndsAlternate:
    def test_splits_ends_where_the_active_house_switches(self):
        # Three ends: bottom, top, bottom. 180 samples * 5 s = 15 min each.
        samples = profile([(180, 0, 6), (180, 5, 0), (180, 0, 7)])
        games = segment.segment_games(samples)
        assert len(games) == 1
        assert [e.house for e in games[0].ends] == ["bottom", "top", "bottom"]

    def test_numbers_ends_from_one(self):
        samples = profile([(180, 0, 6), (180, 5, 0)])
        ends = segment.segment_games(samples)[0].ends
        assert [e.number for e in ends] == [1, 2]

    def test_end_times_cover_the_play(self):
        samples = profile([(180, 0, 6), (180, 5, 0)])
        ends = segment.segment_games(samples)[0].ends
        assert ends[0].start_s == pytest.approx(0.0, abs=10)
        assert ends[1].start_s == pytest.approx(900.0, abs=20)

    def test_brief_dropouts_do_not_split_an_end(self):
        # A player standing over the stones can hide them for a few samples.
        samples = profile([(80, 0, 6), (3, 0, 0), (97, 0, 6), (180, 5, 0)])
        ends = segment.segment_games(samples)[0].ends
        assert [e.house for e in ends] == ["bottom", "top"]


class TestGames:
    def test_a_long_idle_gap_separates_two_games(self):
        samples = profile(
            [(180, 0, 6), (180, 5, 0),      # game 1
             (200, 0, 0),                    # ~17 min changeover
             (180, 0, 4), (180, 3, 0)]       # game 2
        )
        games = segment.segment_games(samples)
        assert len(games) == 2
        assert [len(g.ends) for g in games] == [2, 2]

    def test_games_are_numbered_and_ordered(self):
        samples = profile([(180, 0, 6), (200, 0, 0), (180, 0, 4)])
        games = segment.segment_games(samples)
        assert [g.index for g in games] == [0, 1]
        assert games[0].start_s < games[1].start_s

    def test_a_short_gap_between_ends_is_not_a_new_game(self):
        samples = profile([(180, 0, 6), (12, 0, 0), (180, 5, 0)])
        assert len(segment.segment_games(samples)) == 1

    def test_ignores_stretches_where_the_lights_are_out(self):
        samples = profile([(180, 0, 6)])
        samples += [
            S(t=900 + 5 * i, top_stones=0, bottom_stones=0,
              top_playable=False, bottom_playable=False)
            for i in range(200)
        ]
        samples += profile([(180, 4, 0)], t0=1900.0)
        games = segment.segment_games(samples)
        assert len(games) == 2

    def test_no_activity_yields_no_games(self):
        assert segment.segment_games(profile([(100, 0, 0)])) == []


class TestOnTheReferenceVod:
    @pytest.mark.slow
    def test_recovers_two_games_of_alternating_ends(self, primary_video):
        import itertools

        from curling_score.game import profile
        from curling_score.geometry import layout
        from curling_score.ingest import frames as F

        sweep = list(F.keyframe_sweep(primary_video))
        assert len(sweep) > 2000

        calib_frames = [img for _, img in itertools.islice(sweep, 0, None, 90)][:24]
        panels = layout.detect_panels(calib_frames)
        setups = profile.calibrate_panels(calib_frames, panels)

        samples = profile.build_profile(primary_video, setups, sweep=sweep)
        games = segment.segment_games(samples)

        # Measured during planning: an ~17 min changeover at 1.80-2.09 h splits
        # the stream into two games.
        assert len(games) == 2
        for g in games:
            assert 5 <= len(g.ends) <= 10
            houses = [e.house for e in g.ends]
            assert all(a != b for a, b in zip(houses, houses[1:])), houses
        assert games[0].end_s < games[1].start_s


class TestTooShortToBeAnEnd:
    """Sixteen deliveries cannot fit into a couple of minutes.

    Game 1's last segment was 120 s of the players pushing the rocks back after
    the game had finished. It was analysed as an end and given a score, putting
    a point on the board that was never played for.
    """

    def test_the_floor_follows_from_the_rules(self):
        from curling_score.geometry import constants as C

        assert segment.MIN_END_S == (
            C.STONES_PER_END * segment.MIN_DELIVERY_GAP_S
        )

    def test_a_two_minute_run_is_not_an_end(self):
        # Two full-length ends, then a short burst of clearing activity.
        samples = profile([(180, 3, 0), (2, 0, 0), (180, 0, 3), (2, 0, 0),
                           (24, 3, 0)])
        games = segment.segment_games(samples)
        spans = [e.end_s - e.start_s for g in games for e in g.ends]
        assert spans, "the two real ends must survive"
        assert all(sp >= segment.MIN_END_S for sp in spans), spans

    def test_full_length_ends_still_segment(self):
        samples = profile([(180, 3, 0), (2, 0, 0), (180, 0, 3)])
        games = segment.segment_games(samples)
        assert sum(len(g.ends) for g in games) == 2
