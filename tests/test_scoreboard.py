import cv2
import numpy as np
import pytest

from curling_score.game import scoreboard as SB


class TestCumulative:
    def test_the_rightmost_card_gives_the_running_total(self):
        board = SB.CardBoard(
            yellow=(SB.Card(1, 1, 0.5), SB.Card(3, 3, 0.5)),
            red=(SB.Card(2, 2, 0.5),),
        )
        assert SB.cumulative(board) == {"yellow": 3, "red": 2}

    def test_an_empty_board_is_nil_all(self):
        assert SB.cumulative(SB.CardBoard((), ())) == {"yellow": 0, "red": 0}


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


def _wall_with_boards(home_x=1381, neighbour=True, neighbour_r=12):
    """A wall with sheet 3's board as tonight's re-aimed camera frames it:
    yellow over red markers 52 px apart, three printed rules, and -- at the
    right edge of the frame -- the next sheet's markers, a shade larger, with
    the rest of that board out of shot (VTld/4l60 sheet 3, 2026-09-29)."""
    img = np.full((1080, 1920, 3), 200, np.uint8)
    ay, dy = 117, 52
    ry = ay + dy
    x0, x1 = int(home_x - 0.6 * dy), int(home_x + 5.6 * dy)
    cv2.rectangle(img, (x0, int(ay - 0.7 * dy)), (x1, int(ry + 0.7 * dy)), (150, 150, 150), -1)
    for y in (ay - 8, ay + 33, ry + 12):            # under the banner, under 1-14, the bottom
        cv2.rectangle(img, (x0, y - 1), (x1, y + 1), (20, 20, 20), -1)
    cv2.circle(img, (home_x, ay), 11, (40, 200, 230), -1)    # yellow
    cv2.circle(img, (home_x, ry), 11, (40, 40, 200), -1)     # red
    if neighbour:
        nx = 1920 - 25
        cv2.circle(img, (nx, ay + 3), neighbour_r, (40, 200, 230), -1)
        cv2.circle(img, (nx, ry + 3), neighbour_r, (40, 40, 200), -1)
    return img


class TestANeighbouringBoardAtTheEdgeOfTheFrame:
    """Since the 2026 re-aim, sheets 3 and 4's camera also shows the next
    sheet's board markers at the right edge. When those read a few pixels
    larger than the home board's, they were picked, the card slots landed off
    the frame, and the board read as nothing -- on every blank board of the
    changeover that decides a board split."""

    def test_the_board_that_fits_in_the_frame_is_the_one_found(self):
        geom = SB.find_board(_wall_with_boards())
        assert geom is not None
        assert geom.anchor_x == pytest.approx(1381, abs=2)
        assert geom.slot_x[-1] < 1920

    def test_markers_alone_at_the_edge_are_still_no_board(self):
        img = _wall_with_boards(home_x=1381)
        img[:, :1700] = 200                             # only the neighbour's markers left
        assert SB.find_board(img) is None


class TestTemplates:
    """The printed 1-14 row is the exemplar set for the card digits: same
    font, same scale, same lighting, same frame. Nothing is shipped or
    trained, so these have to come out of the image itself."""

    def test_it_yields_one_normalised_glyph_per_slot(self, known_frame):
        img = known_frame("board_sheet2_t11000.png")
        geom = SB.find_board(img)
        t = SB.templates(img, geom)
        assert sorted(t) == list(range(1, 15))
        for g in t.values():
            assert g.shape == SB.GLYPH_SHAPE
            assert abs(float(g.mean())) < 1e-5
            assert abs(float(g.std()) - 1.0) < 1e-5

    def test_the_printed_row_sits_below_the_cards(self, known_frame):
        """Cards hang above the printed numbers in the same band. Reading the
        card band as the template source would match cards against cards."""
        img = known_frame("board_sheet2_t11000.png")
        geom = SB.find_board(img)
        assert geom.printed_row[0] >= geom.yellow_row[1]
        assert geom.printed_row[1] <= geom.mid_line_y


class TestReadDigit:
    """Read a card glyph with the trained classifier (`digits.ConvModel`,
    shipped in Task 6F). The second return value is now the winning class
    probability, not a correlation margin -- on the one known occluded card
    it still lands right at the reject edge, which is what makes it usable
    as a reject gate at `MIN_CONFIDENCE`."""

    def _cards(self, img):
        geom = SB.find_board(img)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        return geom, gray, SB.read_slots(img, geom)

    def test_it_reads_the_end_number_off_each_yellow_card(self, known_frame):
        img = known_frame("board_sheet2_t11000.png")
        geom, gray, reading = self._cards(img)
        got = {}
        for slot in sorted(reading.yellow):
            glyph = SB._card_glyph(gray, geom, "yellow", slot)
            got[slot] = SB.read_digit(glyph)[0]
        # Yellow reached 1 after end 1 and 3 after end 3.
        assert got == {1: 1, 3: 3}

    def test_a_card_under_a_hand_is_never_read_as_the_wrong_end(self, known_frame):
        """Slot 4 of this frame is behind a spectator's hand. A crude fixed-crop
        matcher with no median stacking or ink centring called it a 9. This
        implementation segments the tile and centres on the ink, and may
        legitimately read the card correctly as 4 -- or it may refuse it as
        None if the occlusion still confuses the model. Either is safe;
        calling it anything else (e.g. the old 9) is the one outcome that
        cannot be allowed."""
        img = known_frame("board_sheet2_t11700_person.png")
        geom, gray, _reading = self._cards(img)
        glyph = SB._card_glyph(gray, geom, "yellow", 4)
        digit, conf = SB.read_digit(glyph)
        assert digit in (None, 4), f"occluded card misread as {digit} (confidence {conf:.5f})"

    def test_an_out_of_frame_slot_has_no_glyph(self, known_frame):
        img = known_frame("board_sheet2_t11000.png")
        geom = SB.find_board(img)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        far = SB.BoardGeometry(
            anchor_x=geom.anchor_x, yellow_y=geom.yellow_y, red_y=geom.red_y,
            dy=geom.dy, top_line_y=geom.top_line_y, mid_line_y=geom.mid_line_y,
            bottom_line_y=geom.bottom_line_y,
            slot_x=[x + 10_000 for x in geom.slot_x],
        )
        assert SB._card_glyph(gray, far, "yellow", 1) is None


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

    # Committed sample frames, so this runs from a fresh clone. The harvest's
    # full-resolution PNGs are deliberately not in the repo -- an earlier
    # version of this test read one of those and passed only on the machine
    # that had harvested it.
    CROWD = "datasets/board-cards-train/samples/s_iPqkT02q8_board_t06300.jpg"
    CLEAR = "datasets/board-cards-train/samples/s_iPqkT02q8_board_t01200.jpg"

    def test_a_crowd_in_front_of_the_lower_board_is_not_nine_red_cards(self):
        """The printed 1-14 row sits ABOVE the red card band, so someone
        standing in front of only the board's lower half leaves that row
        completely clear -- the printed-row check alone has nothing to
        object to.

        On this frame the brightness thresholds `read_slots` used until
        2026-09-30 reported red cards at slots [2, 4, 5, 6, 7, 9, 10, 11, 12]
        -- a cumulative score of 12 -- where three or four people's clothing
        crosses the red row. The slot model reads the board as it is: red "3"
        and "5" at slots 4 and 5, yellow "1", "2" and "4" at 3, 5 and 8, and
        nothing where the crowd is. `is_readable` still refuses the frame, a
        second guard against a row with people across it.
        """
        img = cv2.imread(self.CROWD)
        assert img is not None, "fixture frame missing"
        geom = SB.find_board(img)
        assert geom is not None

        got = SB.read_slots(img, geom)
        assert sorted(got.red) == [4, 5]
        assert sorted(got.yellow) == [3, 5, 8]

        assert SB.is_readable(img, geom) is False

    def test_the_same_board_unobstructed_is_still_readable(self):
        """The guard has to reject a crowd, not a sheet. This is the same
        board and camera earlier in the same video, with one genuine card
        on it -- if this were refused too, the check would be rejecting
        sheet 4 rather than the people standing in front of it.
        """
        img = cv2.imread(self.CLEAR)
        assert img is not None, "fixture frame missing"
        geom = SB.find_board(img)
        assert geom is not None
        assert SB.is_readable(img, geom) is True
        assert sorted(SB.read_slots(img, geom).yellow) == [3]


class TestReadCards:
    def test_it_reads_every_card_with_its_end_and_confidence(self, known_frame):
        img = known_frame("board_sheet2_t11000.png")
        board = SB.read_cards(img, SB.find_board(img))
        assert [(c.slot, c.end) for c in board.yellow] == [(1, 1), (3, 3)]
        assert [(c.slot, c.end) for c in board.red] == [(2, 2)]
        assert all(c.confidence > 0 for c in board.yellow)

    def test_cumulative_is_the_highest_slot_per_team(self, known_frame):
        img = known_frame("board_sheet2_t11000.png")
        board = SB.read_cards(img, SB.find_board(img))
        assert SB.cumulative(board) == {"yellow": 3, "red": 2}

    def test_an_empty_board_is_blank(self, known_frame):
        img = known_frame("board_sheet2_t7200.png")   # between games
        board = SB.read_cards(img, SB.find_board(img))
        assert board.is_blank()
        assert SB.cumulative(board) == {"yellow": 0, "red": 0}

    def test_highest_end_ignores_cards_whose_digit_was_refused(self, known_frame):
        """An unread digit cannot extend how far the board is known to go."""
        board = SB.CardBoard(
            yellow=(SB.Card(1, 1, 0.3), SB.Card(4, None, 0.01)), red=(),
        )
        assert board.highest_end() == 1


class TestPerEndFromCards:
    """A card's slot is the cumulative total and its digit is the end that
    produced it, so one board state is a whole game -- except for ends after
    the last card, which may be blank or may simply not be posted yet."""

    def board(self, yellow=(), red=()):
        return SB.CardBoard(
            yellow=tuple(SB.Card(s, e, 0.5) for s, e in yellow),
            red=tuple(SB.Card(s, e, 0.5) for s, e in red),
        )

    def test_one_board_state_gives_every_end(self):
        # The reference frame: Y+1 in end 1, R+2 in end 2, Y+2 in end 3.
        got = SB.per_end_from_cards(
            self.board(yellow=[(1, 1), (3, 3)], red=[(2, 2)]), n_ends=3)
        assert got.per_end == {
            1: {"red": 0, "yellow": 1},
            2: {"red": 2, "yellow": 0},
            3: {"red": 0, "yellow": 2},
        }
        assert got.unread_ends == ()
        assert got.final == {"red": 2, "yellow": 3}

    def test_an_interior_end_with_no_card_was_blank(self):
        """A later end is posted, so end 2 was genuinely passed over. This is
        read, not inferred, and it is the common case."""
        got = SB.per_end_from_cards(
            self.board(yellow=[(1, 1), (2, 3)]), n_ends=3)
        assert got.per_end[2] == {"red": 0, "yellow": 0}
        assert got.unread_ends == ()

    def test_ends_after_the_last_card_are_unread_not_blank(self):
        """Nothing distinguishes 'blank' from 'not posted yet' up here, so it
        is reported as unknown rather than guessed at zero."""
        got = SB.per_end_from_cards(self.board(yellow=[(1, 1)]), n_ends=4)
        assert got.unread_ends == (2, 3, 4)
        assert 2 not in got.per_end

    def test_the_final_is_unknown_while_any_end_is_unread(self):
        got = SB.per_end_from_cards(self.board(yellow=[(1, 1)]), n_ends=4)
        assert got.final is None

    def test_a_board_beyond_the_detected_end_count_is_still_honoured(self):
        """n_ends is a stopping hint. The board is the score, so a card past
        the detected end count is read, not discarded."""
        got = SB.per_end_from_cards(
            self.board(yellow=[(1, 1)], red=[(2, 5)]), n_ends=2)
        assert got.per_end[5] == {"red": 2, "yellow": 0}
        assert got.unread_ends == ()

    def test_rejects_an_unread_digit(self):
        with pytest.raises(SB.ScoreboardError, match="could not be read"):
            SB.per_end_from_cards(self.board(yellow=[(1, None)]), n_ends=1)

    def test_rejects_both_teams_scoring_in_one_end(self):
        with pytest.raises(SB.ScoreboardError, match="both teams"):
            SB.per_end_from_cards(
                self.board(yellow=[(1, 2)], red=[(1, 2)]), n_ends=2)

    def test_rejects_a_team_whose_total_goes_backwards(self):
        with pytest.raises(SB.ScoreboardError, match="backwards"):
            SB.per_end_from_cards(
                self.board(yellow=[(3, 1), (2, 2)]), n_ends=2)

    def test_rejects_a_step_no_team_could_have_scored(self):
        """Eight rocks a side, so nine in one end is physically impossible.

        The cheapest defence there is against a phantom card at a high slot,
        which the slot-must-increase check lets straight through.
        """
        with pytest.raises(SB.ScoreboardError, match="cannot score"):
            SB.per_end_from_cards(self.board(yellow=[(9, 1)]), n_ends=1)

    def test_rejects_a_step_no_team_could_have_scored_mid_board(self):
        with pytest.raises(SB.ScoreboardError, match="cannot score"):
            SB.per_end_from_cards(
                self.board(yellow=[(2, 1), (11, 2)]), n_ends=2)

    def test_an_eight_ender_is_still_read(self):
        got = SB.per_end_from_cards(self.board(yellow=[(8, 1)]), n_ends=1)
        assert got.per_end[1] == {"red": 0, "yellow": 8}

    def test_a_blank_board_reads_every_end_as_unread(self):
        got = SB.per_end_from_cards(self.board(), n_ends=2)
        assert got.unread_ends == (1, 2)
        assert got.per_end == {}
        assert got.final is None


class TestReadGameBoard:
    """Cards accumulate, so the latest usable board state is also the most
    complete one. The sampler walks back from the end of the game and stops at
    the first state it can use -- one read, in the ordinary case."""

    def board(self, yellow=(), red=()):
        return SB.CardBoard(
            yellow=tuple(SB.Card(s, e, 0.5) for s, e in yellow),
            red=tuple(SB.Card(s, e, 0.5) for s, e in red),
        )

    def test_a_complete_board_costs_one_read(self):
        seen = []

        def read_at(t):
            seen.append(t)
            return self.board(yellow=[(1, 1), (3, 3)], red=[(2, 2)])

        got = SB.read_game_board("v", 0.0, 3000.0, n_ends=3, read_at=read_at)
        assert got.reads == 1
        assert len(seen) == 1
        assert got.scores.final == {"red": 2, "yellow": 3}

    def test_it_steps_back_past_a_cleared_board(self):
        """Read too late and the board has been wiped for the next game."""
        def read_at(t):
            return self.board() if t > 2000.0 else self.board(yellow=[(1, 1)])

        got = SB.read_game_board("v", 0.0, 3000.0, n_ends=1, read_at=read_at)
        assert got.reads > 1
        assert got.read_at_s <= 2000.0

    def test_it_steps_back_past_an_inconsistent_read(self):
        """A misread digit usually shows up as two teams scoring one end."""
        def read_at(t):
            if t > 2000.0:
                return self.board(yellow=[(1, 2)], red=[(1, 2)])
            return self.board(yellow=[(1, 1)])

        got = SB.read_game_board("v", 0.0, 3000.0, n_ends=1, read_at=read_at)
        assert got.scores.per_end[1] == {"red": 0, "yellow": 1}

    def test_it_never_looks_past_the_end_of_the_game(self):
        """Past end_s the board belongs to the next game: a repopulated board
        would be silently attributed to this one."""
        seen = []

        def read_at(t):
            seen.append(t)
            return self.board(yellow=[(1, 1)])

        SB.read_game_board("v", 0.0, 3000.0, n_ends=1, read_at=read_at)
        assert max(seen) <= 3000.0

    def test_it_gives_up_after_the_read_budget(self):
        got = SB.read_game_board("v", 0.0, 9000.0, n_ends=3,
                                 read_at=lambda t: None)
        assert got is None

    def test_an_incomplete_board_is_still_returned(self):
        """Trailing ends unposted is a partial answer, not a failure."""
        got = SB.read_game_board("v", 0.0, 3000.0, n_ends=4,
                                 read_at=lambda t: self.board(yellow=[(1, 1)]))
        assert got.scores.unread_ends == (2, 3, 4)
        assert got.scores.final is None


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
