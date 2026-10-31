"""A practice throw as the session page reads it (`practice.throws`)."""

import pytest

from curling_score.detect.delivery import Delivery
from curling_score.detect.release import Release
from curling_score.detect.rocks import Detection
from curling_score.game import split
from curling_score.game.broomtime import TargetBroom
from curling_score.game.linetime import Line
from curling_score.game.shots import Shot
from curling_score.game.spinfit import Spin
from curling_score.practice import throws


def stone(color, x, y):
    return Detection(color=color, x_m=x, y_m=y, x_px=0.0, y_px=0.0,
                     area_px=140.0, confidence=0.9)


def arrival(color="red", t_enter=40.0, x=0.1, y=-0.4, came_to_rest=True):
    # 95 samples 0.1 s apart, coming down the sheet from 3.8 m.
    track = tuple((t_enter + i * 0.1, x, 3.8 - i * 0.05) for i in range(95))
    return Delivery(color=color, t_enter=t_enter, t_rest=t_enter + 9.4, entry_y_m=3.8,
                    rest_x_m=x, rest_y_m=y, travel_m=4.2, came_to_rest=came_to_rest,
                    track=track)


def line(**kw):
    base = dict(start=(0.0, -3.6), at_hog_x=0.1, at_hog_offset=0.08, at_broom_x=-0.57,
                miss=-0.12, curl="left", side="narrow", hog_path=(),
                path=(), fit_n=12, fit_rms=0.01)
    return Line(**{**base, **kw})


class TestRingOf:
    @pytest.mark.parametrize("x, y, ring", [
        (0.0, 0.0, "button"), (0.0, 0.25, "button"), (0.0, 0.6, "4"), (0.5, 0.9, "8"),
        (0.0, 1.9, "12"), (0.0, 2.5, "short"), (1.5, -1.5, "long"),
        (0.0, -2.0, "out"), (2.3, 0.0, "out")])
    def test_the_smallest_ring_a_stone_touches(self, x, y, ring):
        assert throws.ring_of(x, y) == ring


class TestThrowRecord:
    def test_a_measured_throw_is_on_the_streams_clock(self):
        dv = arrival()
        shot = Shot(number=1, color="red",
                    stones=[stone("yellow", 0.8, 0.2), stone("red", 0.12, -0.41)],
                    t_rest_s=dv.t_rest, delivery=dv,
                    release=Release("red", 22.0, 1.0, 2.4), delivered_stone_index=1)
        shot.t_hog_s = 24.0
        shot.v_hog_m_s = 2.41
        shot.far_crossing = split.FarCrossing(t=37.8)
        shot.target_broom = TargetBroom(x_m=-0.45, y_m=0.0, seen=0.9, confidence=0.8)
        shot.line = line()

        got = throws.throw_record(shot, house="top", t0_s=1000.0)

        assert got["id"] == "t_1022.0"
        assert got["t_release_s"] == 1022.0 and got["t_rest_s"] == 1049.4
        assert (got["house"], got["color"], got["arrived"]) == ("top", "red", True)
        assert got["release_source"] == "overhead"
        assert got["release_speed"] == 2.4
        assert got["split_s"] == pytest.approx(13.8)
        assert got["curl"] == "left"
        assert got["broom"] == {"x": -0.45, "y": 0.0}
        assert got["line"] == {"miss_m": -0.12, "side": "narrow", "hog_offset_m": 0.08,
                               "at_hog_x": 0.1, "aim_x": None,
                               "start": {"x": 0.0, "y": -3.6}, "delivery": []}
        # Where it rests is the house read's stone, not the track's last point.
        assert got["rest"] == {"x": 0.12, "y": -0.41, "to_tee_m": 0.427, "ring": "4"}
        # What a clip is cut on: both hog lines, on the stream's clock.
        assert got["t_hog_s"] == 1024.0 and got["v_hog_m_s"] == 2.41
        assert got["t_far_s"] == 1037.8
        assert got["clip"] is False

    def test_the_track_is_thinned_to_five_points_a_second(self):
        dv = arrival()
        shot = Shot(number=1, color="red", stones=[], t_rest_s=dv.t_rest, delivery=dv)
        track = throws.throw_record(shot, house="top")["track"]
        assert track[0] == [40.0, 0.1, 3.8]
        assert track[-1][0] == pytest.approx(49.4)
        assert all(b[0] - a[0] >= 0.19 for a, b in zip(track, track[1:-1]))
        assert len(track) < 60

    def test_a_throw_that_never_arrived(self):
        shot = Shot(number=1, color="yellow", stones=[], t_rest_s=float("nan"),
                    release=Release("yellow", 30.0, 1.0, 2.0), state_known=False)
        got = throws.throw_record(shot, house="top")
        assert got["id"] == "t_30.0"
        assert got["arrived"] is False and got["t_rest_s"] is None
        assert got["rest"] is None and got["track"] == [] and got["split_s"] is None

    def test_no_release_no_broom_and_no_line_are_nulls(self):
        dv = arrival()
        shot = Shot(number=1, color="red", stones=[], t_rest_s=dv.t_rest, delivery=dv)
        got = throws.throw_record(shot, house="bottom")
        assert got["id"] == "t_40.0"              # dated by its arrival
        assert got["t_release_s"] is None and got["release_source"] is None
        assert got["broom"] is None and got["line"] is None and got["curl"] is None
        assert got["spin"] is None
        # No house read: the rest comes from the delivery itself.
        assert got["rest"]["x"] == 0.1 and got["rest"]["ring"] == "4"
        assert got["t_hog_s"] is None and got["v_hog_m_s"] is None
        assert got["t_far_s"] is None and got["clip"] is False

    def test_the_spin_and_its_range(self):
        dv = arrival()
        shot = Shot(number=1, color="red", stones=[], t_rest_s=dv.t_rest, delivery=dv)
        shot.spin = Spin(rpm=9.04, rpm_avg=8.6, turns_in_view=2.1, samples=40)
        assert throws.throw_record(shot, house="top")["spin"] == {"rpm": 9.0, "rpm_range": None}
        shot.spin = Spin(rpm=7.0, rpm_avg=7.0, turns_in_view=0.4, samples=20,
                         rpm_range=(5.52, 8.48))
        assert throws.throw_record(shot, house="top")["spin"] == {
            "rpm": 7.0, "rpm_range": [5.5, 8.5]}

    def test_a_stone_that_ran_out_of_play_is_out(self):
        dv = arrival(came_to_rest=False, y=-2.3)
        shot = Shot(number=1, color="red", stones=[], t_rest_s=dv.t_rest, delivery=dv)
        rest = throws.throw_record(shot, house="top")["rest"]
        assert rest["ring"] == "out" and rest["to_tee_m"] is None

    def test_the_line_carries_the_delivery_from_the_hack(self):
        # The page draws the viewer's Delivery chart from these: where the
        # rock sat before the push, and its path to past the hog line.
        dv = arrival()
        shot = Shot(number=1, color="red", stones=[], t_rest_s=dv.t_rest, delivery=dv)
        shot.line = line(delivery=((0.0, -3.62, 0.15), (0.5, -2.9, 0.141), (1.0, -1.8, 0.1305)))
        got = throws.throw_record(shot, house="top")["line"]
        assert got["start"] == {"x": 0.0, "y": -3.6}
        assert got["delivery"] == [[0.0, -3.62, 0.15], [0.5, -2.9, 0.141], [1.0, -1.8, 0.131]]

    def test_the_line_carries_the_release_when_there_is_one(self):
        dv = arrival()
        shot = Shot(number=1, color="red", stones=[], t_rest_s=dv.t_rest, delivery=dv)
        shot.line = line(release=(1.2345, 30.1234, -0.14199))
        assert throws.throw_record(shot, house="top")["line"]["release"] == [1.23, 30.12, -0.142]

    def test_a_line_without_a_release_has_no_release_key(self):
        dv = arrival()
        shot = Shot(number=1, color="red", stones=[], t_rest_s=dv.t_rest, delivery=dv)
        shot.line = line()
        assert "release" not in throws.throw_record(shot, house="top")["line"]

    def test_a_line_with_no_broom_says_where_it_was_aimed(self):
        dv = arrival()
        shot = Shot(number=1, color="red", stones=[], t_rest_s=dv.t_rest, delivery=dv)
        shot.line = line(at_broom_x=None, miss=None, side=None, at_tee_x=-0.21)
        got = throws.throw_record(shot, house="top")["line"]
        assert got == {"miss_m": None, "side": None, "hog_offset_m": 0.08, "at_hog_x": 0.1,
                       "aim_x": -0.21,
                       "start": {"x": 0.0, "y": -3.6}, "delivery": []}
