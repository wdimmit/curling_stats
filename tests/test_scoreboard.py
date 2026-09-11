import numpy as np
import pytest

from curling_score.game import scoreboard as SB


class TestCumulative:
    def test_the_rightmost_card_gives_the_running_total(self):
        r = SB.BoardReading(yellow={1, 3}, red={2})
        assert SB.cumulative(r) == {"yellow": 3, "red": 2}

    def test_an_empty_board_is_nil_all(self):
        assert SB.cumulative(SB.BoardReading(set(), set())) == {"yellow": 0, "red": 0}

    def test_only_the_highest_slot_matters(self):
        # Cards are hung cumulatively, so the lower ones are history.
        assert SB.cumulative(SB.BoardReading({1, 2, 5}, set()))["yellow"] == 5


class TestPerEndScores:
    def test_turns_a_sequence_of_boards_into_end_by_end_scores(self):
        readings = [
            SB.BoardReading({1}, set()),          # end 1: yellow 1
            SB.BoardReading({1}, {2}),            # end 2: red 2
            SB.BoardReading({1, 3}, {2}),         # end 3: yellow 2
        ]
        assert SB.per_end_scores(readings) == [
            {"red": 0, "yellow": 1},
            {"red": 2, "yellow": 0},
            {"red": 0, "yellow": 2},
        ]

    def test_ignores_repeated_readings_of_an_unchanged_board(self):
        readings = [SB.BoardReading({1}, set())] * 5
        assert SB.per_end_scores(readings) == [{"red": 0, "yellow": 1}]

    def test_a_board_that_never_changes_from_empty_yields_nothing(self):
        assert SB.per_end_scores([SB.BoardReading(set(), set())] * 4) == []

    def test_a_reset_to_empty_starts_a_new_game(self):
        readings = [
            SB.BoardReading({1}, set()),
            SB.BoardReading(set(), set()),
            SB.BoardReading({2}, set()),
        ]
        games = SB.split_games(readings)
        assert len(games) == 2
        assert SB.per_end_scores(games[1]) == [{"red": 0, "yellow": 2}]

    def test_rejects_a_board_that_goes_backwards(self):
        # Cumulative scores cannot decrease within one game.
        readings = [SB.BoardReading({3}, set()), SB.BoardReading({1}, set())]
        with pytest.raises(SB.ScoreboardError):
            SB.per_end_scores(readings)


class TestFindBoard:
    def test_locates_the_board_from_the_team_colour_markers(self, known_frame):
        geom = SB.find_board(known_frame("board_sheet2_t3600.png"))
        assert geom is not None
        # Measured on the reference VOD.
        assert geom.anchor_x == pytest.approx(1376, abs=6)
        assert geom.yellow_y == pytest.approx(175, abs=6)
        assert geom.dy == pytest.approx(57, abs=6)

    def test_lays_out_fourteen_evenly_spaced_slots(self, known_frame):
        geom = SB.find_board(known_frame("board_sheet2_t3600.png"))
        assert len(geom.slot_x) == SB.SLOTS
        gaps = np.diff(geom.slot_x)
        assert np.allclose(gaps, gaps[0], atol=0.5)
        # Slot 1 sits under the printed "1"; slot 14 under the printed "14".
        assert geom.slot_x[0] == pytest.approx(1418, abs=6)
        assert geom.slot_x[13] == pytest.approx(1685, abs=10)

    def test_returns_none_when_there_is_no_board(self):
        blank = np.full((1080, 1920, 3), 200, np.uint8)
        assert SB.find_board(blank) is None


class TestReadSlots:
    """Ground truth read by eye from the reference VOD."""

    CASES = {
        "board_sheet2_t0900.png": ({1}, set()),
        "board_sheet2_t3600.png": ({1, 2}, {1}),
        "board_sheet2_t7200.png": (set(), set()),
        "board_sheet2_t11000.png": ({1, 3}, {2}),
    }

    @pytest.mark.parametrize("name", sorted(CASES))
    def test_reads_the_hung_cards(self, name, known_frame):
        img = known_frame(name)
        geom = SB.find_board(img)
        assert geom is not None, f"{name}: board not found"
        got = SB.read_slots(img, geom)
        want_y, want_r = self.CASES[name]
        assert (got.yellow, got.red) == (want_y, want_r)

    def test_reads_the_running_score_at_each_point_in_the_game(self, known_frame):
        order = ["board_sheet2_t0900.png", "board_sheet2_t3600.png",
                 "board_sheet2_t11000.png"]
        got = []
        for name in order:
            img = known_frame(name)
            got.append(SB.cumulative(SB.read_slots(img, SB.find_board(img))))
        assert got == [
            {"yellow": 1, "red": 0},
            {"yellow": 2, "red": 1},
            {"yellow": 3, "red": 2},
        ]

    def test_sparse_sampling_is_reported_rather_than_guessed_at(self, known_frame):
        # Two ends elapsed between t=900 and t=3600, so the jump shows both
        # teams scoring. That is impossible in one end, and it means the board
        # was sampled too rarely -- worth saying so rather than inventing a split.
        readings = []
        for name in ("board_sheet2_t0900.png", "board_sheet2_t3600.png"):
            img = known_frame(name)
            readings.append(SB.read_slots(img, SB.find_board(img)))
        with pytest.raises(SB.ScoreboardError, match="both teams"):
            SB.per_end_scores(readings)


class TestObstruction:
    """People walk in front of the board constantly."""

    def test_an_intact_board_reads_as_unobstructed(self, known_frame):
        img = known_frame("board_sheet2_t3600.png")
        assert SB.is_readable(img, SB.find_board(img)) is True

    def test_a_board_with_someone_in_front_of_it_is_rejected(self, known_frame):
        img = known_frame("board_sheet2_t3600.png").copy()
        geom = SB.find_board(img)
        # Paint a dark figure across the middle of the printed 1-14 row.
        x = int(geom.slot_x[5])
        img[int(geom.top_line_y) : int(geom.bottom_line_y), x : x + 70] = 30
        assert SB.is_readable(img, geom) is False

    def test_reading_an_obstructed_board_gives_nothing_rather_than_nonsense(
        self, known_frame
    ):
        img = known_frame("board_sheet2_t3600.png").copy()
        geom = SB.find_board(img)
        x = int(geom.slot_x[5])
        img[int(geom.top_line_y) : int(geom.bottom_line_y), x : x + 70] = 30
        assert SB.read_board(img) is None


class TestConsolidate:
    """Cards accumulate through a game and are never taken down mid-game.

    A slot that shows up in one reading and is gone from the next was noise --
    typically someone standing in front of the lower half of the board.
    """

    def test_drops_a_slot_that_appears_only_once(self):
        readings = [
            SB.BoardReading({1}, {1}),
            SB.BoardReading({1}, {1, 7}),   # 7 is a false positive
            SB.BoardReading({1}, {1}),
            SB.BoardReading({1}, {1}),
        ]
        got = SB.consolidate(readings)
        assert all(r.red == {1} for r in got)

    def test_keeps_a_slot_that_persists(self):
        readings = [
            SB.BoardReading({1}, set()),
            SB.BoardReading({1}, {2}),
            SB.BoardReading({1}, {2}),
            SB.BoardReading({1}, {2}),
        ]
        got = SB.consolidate(readings)
        assert got[-1].red == {2}

    def test_a_card_once_established_stays_even_if_a_frame_misses_it(self):
        readings = [
            SB.BoardReading({1}, set()),
            SB.BoardReading({1}, set()),
            SB.BoardReading(set(), set()),  # obscured for one reading
            SB.BoardReading({1}, set()),
        ]
        got = SB.consolidate(readings)
        assert all(r.yellow == {1} for r in got)

    def test_the_result_only_ever_grows(self):
        readings = [
            SB.BoardReading({1}, set()),
            SB.BoardReading({1, 2}, {1}),
            SB.BoardReading({1, 2, 3}, {1}),
        ]
        got = SB.consolidate(readings)
        for a, b in zip(got, got[1:]):
            assert a.yellow <= b.yellow
            assert a.red <= b.red

    def test_noise_in_the_real_sweep_is_cleaned_up(self):
        # Taken from the reference VOD: yellow was clean, red flickered.
        readings = [
            SB.BoardReading({1, 2, 3}, {1, 2}),
            SB.BoardReading({1, 2, 3}, {1, 2, 7, 8}),
            SB.BoardReading({1, 2, 3}, {1, 2}),
            SB.BoardReading({1, 2, 3}, {1, 2, 3, 6}),
        ]
        got = SB.consolidate(readings)
        assert got[-1].red == {1, 2}
        assert got[-1].yellow == {1, 2, 3}


class TestPersonInFrontOfTheBoard:
    """A card is a white tile with a black digit; a person is just dark.

    At t=11700 on the reference VOD a spectator stood in front of the red row.
    The real card read 22 *brighter* than the board; the person read 59-105
    darker, but with just as much internal contrast. Contrast alone cannot tell
    them apart -- brightness can.
    """

    def test_a_spectator_is_not_read_as_a_row_of_cards(self, known_frame):
        img = known_frame("board_sheet2_t11700_person.png")
        got = SB.read_slots(img, SB.find_board(img))
        assert got.red == {2}, f"person leaked in as {sorted(got.red)}"

    def test_the_unobstructed_row_still_reads_correctly(self, known_frame):
        img = known_frame("board_sheet2_t11700_person.png")
        got = SB.read_slots(img, SB.find_board(img))
        assert got.yellow == {1, 3, 4}

    def test_the_running_score_is_right_despite_the_spectator(self, known_frame):
        img = known_frame("board_sheet2_t11700_person.png")
        r = SB.read_slots(img, SB.find_board(img))
        assert SB.cumulative(r) == {"yellow": 4, "red": 2}


class TestReadingIsCheap:
    """Board reads must not decode the whole neighbourhood at full rate.

    Each read medians a few-minute window to remove people walking past. Doing
    that with a fixed frame rate decodes every frame in the window at full
    resolution -- about 180 s of 1080p per read, and a game needs a dozen or
    more. The board changes once an end, so keyframes are ample.
    """

    @pytest.mark.slow
    def test_a_board_read_is_much_faster_than_the_window_it_spans(
        self, primary_video
    ):
        import time

        t0 = time.time()
        reading = SB.read_board_at(primary_video, 3600, window_s=75)
        elapsed = time.time() - t0
        assert reading is not None, "expected a readable board at t=3600"
        # 150 s of video: decoding it frame by frame takes far longer than this.
        assert elapsed < 12.0, f"board read took {elapsed:.1f}s"
