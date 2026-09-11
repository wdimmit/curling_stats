import pytest

from curling_score.detect.delivery import Delivery
from curling_score.detect.rocks import Detection
from curling_score.game import secondpass


def det(color, x, y, conf=0.8):
    return Detection(color=color, x_m=x, y_m=y, x_px=0.0, y_px=0.0,
                     area_px=140.0, confidence=conf)


def dv(color, t, rest=(0.0, 0.0), travel=3.0):
    return Delivery(color=color, t_enter=t, t_rest=t + 8.0, entry_y_m=4.0,
                    rest_x_m=rest[0], rest_y_m=rest[1], travel_m=travel)


def flight(color, t0, y0=4.0, y1=1.0, step=0.1, speed=0.35, x=0.2):
    out, t, y = [], t0, y0
    while y > y1:
        out.append((t, [det(color, x, y)]))
        y -= speed * step / 0.1 * 0.1
        t += step
    return out


class TestExpectedColour:
    def test_alternation_names_the_missing_colour(self):
        found = [dv("yellow", 100), dv("yellow", 200)]
        gaps = secondpass.gaps_to_search(found, 0.0, 900.0)
        assert len(gaps) == 1
        assert gaps[0].expected_color == "red"
        assert gaps[0].start_s == pytest.approx(108.0)
        assert gaps[0].end_s == pytest.approx(200.0)

    def test_a_complete_alternating_end_leaves_nothing_to_search(self):
        found = [dv("yellow" if i % 2 == 0 else "red", 50 * i) for i in range(16)]
        assert secondpass.gaps_to_search(found, 0.0, 900.0) == []

    def test_a_team_already_at_eight_is_never_the_expected_colour(self):
        # Yellow has thrown its eight; a missing delivery must be red.
        found = [dv("yellow", 50 * i) for i in range(8)]
        gaps = secondpass.gaps_to_search(found, 0.0, 900.0)
        assert all(g.expected_color == "red" for g in gaps)


class TestSearch:
    def test_finds_a_delivery_the_first_pass_rejected(self):
        # A short guard that never persisted: rejected globally, but here we
        # know a red is missing in this window, so a weaker signal is enough.
        frames = flight("red", 100.0, y0=3.6, y1=2.9, speed=0.2)
        gap = secondpass.Gap(start_s=99.0, end_s=115.0, expected_color="red")
        got = secondpass.search(frames, [gap])
        assert len(got) == 1
        assert got[0].color == "red"

    def test_will_not_invent_a_delivery_of_the_wrong_colour(self):
        frames = flight("yellow", 100.0, y0=3.6, y1=2.9, speed=0.2)
        gap = secondpass.Gap(start_s=99.0, end_s=115.0, expected_color="red")
        assert secondpass.search(frames, [gap]) == []

    def test_finds_nothing_in_an_empty_window(self):
        gap = secondpass.Gap(start_s=99.0, end_s=115.0, expected_color="red")
        assert secondpass.search([], [gap]) == []

    def test_takes_at_most_one_delivery_per_gap(self):
        frames = flight("red", 100.0, y0=3.8, y1=2.6, speed=0.2)
        frames += flight("red", 106.0, y0=3.8, y1=2.6, speed=0.2)
        gap = secondpass.Gap(start_s=99.0, end_s=120.0, expected_color="red")
        # Exactly one delivery is missing there, so only the best is taken.
        assert len(secondpass.search(frames, [gap])) == 1

    def test_a_gap_with_no_expected_colour_accepts_either(self):
        frames = flight("yellow", 100.0, y0=3.6, y1=2.7, speed=0.2)
        gap = secondpass.Gap(start_s=99.0, end_s=115.0, expected_color=None)
        assert len(secondpass.search(frames, [gap])) == 1


class TestDoesNotRefindWhatIsAlreadyThere:
    """A gap is padded so a delivery just outside its edge is still reachable,
    and that padding can reach over one the first pass already has.

    In end 1 the same red at (-0.60, -0.53) came back twice, 0.2 s apart,
    which reads downstream as two stones thrown from one delivery.
    """

    def test_a_candidate_at_a_known_delivery_is_skipped(self):
        frames = flight("red", 100.0)
        gaps = [secondpass.Gap(start_s=94.0, end_s=99.0, expected_color="red")]
        assert secondpass.search(frames, gaps) != []
        known = [dv("red", 100.5)]
        assert secondpass.search(frames, gaps, known) == []

    def test_a_candidate_well_clear_of_one_is_still_found(self):
        frames = flight("red", 100.0)
        gaps = [secondpass.Gap(start_s=94.0, end_s=99.0, expected_color="red")]
        assert secondpass.search(frames, gaps, [dv("red", 400.0)]) != []
