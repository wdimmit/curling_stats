import pytest

from curling_score.detect.rocks import Detection
from curling_score.game import placement as P


def det(color, x, y):
    return Detection(color=color, x_m=x, y_m=y, x_px=0.0, y_px=0.0,
                     area_px=140.0, confidence=0.9)


def frames(t0, t1, stones, fps=10.0, hide=None):
    """(t, detections) from t0 to t1.

    ``stones`` is [(color, x, y, t_from, t_to)]; ``hide(t, i)`` true drops
    stone ``i`` from the frame at ``t`` (a player standing over it).
    """
    out = []
    n = int(round((t1 - t0) * fps))
    for k in range(n + 1):
        t = round(t0 + k / fps, 3)
        out.append((t, [det(c, x, y) for i, (c, x, y, a, b) in enumerate(stones)
                        if a <= t < b and not (hide and hide(t, i))]))
    return out


class TestClassify:
    def test_a_centre_placement(self):
        p = P.classify([("yellow", 0.01, -0.50), ("red", 0.00, 3.40)])
        assert (p.hammer, p.guard[0], p.power_play, p.complete) == ("yellow", "red", None, True)

    def test_a_power_play_on_the_left(self):
        p = P.classify([("red", -1.28, 0.16), ("yellow", -0.93, 3.56)])
        assert (p.hammer, p.power_play, p.guard[0]) == ("red", "left", "yellow")

    def test_a_power_play_on_the_right(self):
        p = P.classify([("yellow", 1.29, 0.13), ("red", 0.95, 3.41)])
        assert (p.hammer, p.power_play) == ("yellow", "right")

    def test_the_house_stone_alone_is_an_incomplete_placement(self):
        p = P.classify([("red", -0.02, -0.51)])
        assert p.hammer == "red" and p.complete is False and p.guard is None

    def test_a_guard_of_the_house_stones_colour_is_not_its_guard(self):
        p = P.classify([("red", 0.0, -0.5), ("red", 0.0, 3.4)])
        assert p.guard is None

    def test_a_centre_guard_does_not_guard_a_power_play(self):
        p = P.classify([("red", -1.28, 0.16), ("yellow", 0.0, 3.4)])
        assert p.power_play == "left" and p.guard is None

    def test_two_stones_on_house_spots_are_not_a_placement(self):
        assert P.classify([("red", 0.0, -0.5), ("yellow", 1.27, 0.17)]) is None

    def test_nothing_on_a_house_spot_is_not_a_placement(self):
        assert P.classify([("red", 0.5, 1.0), ("yellow", 0.0, 3.4)]) is None

    def test_detections_work_as_well_as_tuples(self):
        p = P.classify([det("yellow", 0.0, -0.49), det("red", 0.0, 3.4)])
        assert p.hammer == "yellow" and p.complete


class TestFind:
    def test_pushed_in_from_behind(self):
        fr = frames(0, 400, [("red", 0.0, -0.5, 100, 400), ("yellow", 0.0, 3.4, 130, 400)])
        p = P.find(fr, 0, 400)
        assert p.hammer == "red" and p.complete
        assert 125 <= p.t_s <= 130

    def test_a_house_stone_slid_long_before_its_guard(self):
        # ih59 end 1: the yellow house stone arrived at 166 s, the red guard at 262 s.
        fr = frames(100, 500, [("yellow", 0.01, -0.5, 166, 500), ("red", 0.01, 3.4, 262, 500)])
        p = P.find(fr, 100, 500)
        assert p.complete and 257 <= p.t_s <= 262

    def test_a_guard_hidden_half_the_time_still_counts(self):
        hide = lambda t, i: i == 1 and int(t) % 2 == 0
        fr = frames(0, 300, [("red", 0.0, -0.5, 50, 300), ("yellow", 0.0, 3.4, 80, 300)], hide=hide)
        p = P.find(fr, 0, 300)
        assert p.complete and p.t_s <= 82

    def test_a_guard_never_seen_gives_the_house_stone_alone(self):
        fr = frames(0, 200, [("red", 0.0, -0.5, 50, 200)])
        p = P.find(fr, 0, 200)
        assert p.hammer == "red" and not p.complete

    def test_a_stone_crossing_the_house_spot_is_not_a_placement(self):
        # A guard pushed up through the house sits on the house spot for 4 s.
        fr = frames(0, 200, [("yellow", 0.0, -0.49, 50, 54)])
        assert P.find(fr, 0, 200) is None

    def test_an_empty_house_has_no_placement(self):
        assert P.find(frames(0, 100, []), 0, 100) is None


class TestReadBefore:
    def test_a_two_step_power_play_reads_as_the_power_play(self):
        stones = [("red", 0.02, -0.5, 100, 150), ("yellow", 0.0, 3.4, 110, 150),
                  ("red", -1.28, 0.16, 152, 400), ("yellow", -0.93, 3.56, 152, 400)]
        fr = frames(0, 400, stones)
        found = P.find(fr, 0, 400)
        assert found.power_play is None           # the first arrangement to hold
        read = P.read_before(fr, found, t_first=200.0)
        assert (read.power_play, read.hammer, read.t_s) == ("left", "red", found.t_s)
        assert len(read.seed) == 2

    def test_an_unreadable_window_keeps_the_found_arrangement(self):
        fr = frames(0, 400, [("red", 0.0, -0.5, 100, 400), ("yellow", 0.0, 3.4, 130, 400)])
        found = P.find(fr, 0, 400)
        read = P.read_before(fr, found, t_first=5000.0)
        assert (read.hammer, read.guard, read.seed) == (found.hammer, found.guard, ())


from curling_score.detect.delivery import Delivery
from curling_score.game import format as F


def dv(color, t_rest, x=0.3, y=1.0):
    return Delivery(color=color, t_enter=t_rest - 8.0, t_rest=t_rest, entry_y_m=4.0,
                    rest_x_m=x, rest_y_m=y, travel_m=3.0)


PLACED = P.Placement(t_s=200.0, house=("yellow", 0.0, -0.5), guard=("red", 0.0, 3.4),
                     power_play=None)


class TestExclude:
    def test_nothing_is_dropped_without_a_placement(self):
        ds = [dv("red", 100.0), dv("yellow", 300.0)]
        assert P.exclude(ds, None) == (ds, [])

    def test_a_slid_placement_stone_is_dropped(self):
        slid = dv("yellow", 166.0, x=0.01, y=-0.5)      # ih59 end 1's house stone
        guard = dv("red", 205.0, x=0.0, y=3.4)          # settles as the pattern completes
        rock1 = dv("red", 260.0, x=0.2, y=2.9)
        kept, dropped = P.exclude([slid, guard, rock1], PLACED)
        assert kept == [rock1] and dropped == [slid, guard]

    def test_a_pre_game_slide_is_dropped(self):
        stray = dv("red", 120.0, x=1.8, y=-0.76)
        assert P.exclude([stray], PLACED) == ([], [stray])

    def test_a_later_rock_on_a_placed_spot_is_kept(self):
        # A hit and stick on the house stone, well after rock 1.
        stick = dv("red", 900.0, x=0.0, y=-0.5)
        assert P.exclude([stick], PLACED) == ([stick], [])


class TestFillBase:
    def test_a_complete_placement_is_two_stones(self):
        assert P.fill_base(PLACED, F.DOUBLES) == 2

    def test_an_unseen_guard_is_not_counted(self):
        alone = P.Placement(t_s=1.0, house=("red", 0.0, -0.5), guard=None, power_play=None)
        assert P.fill_base(alone, F.DOUBLES) == 1

    def test_no_placement_in_doubles_assumes_two(self):
        assert P.fill_base(None, F.DOUBLES) == 2

    def test_fours_places_nothing(self):
        assert P.fill_base(None, F.FOURS) == 0
