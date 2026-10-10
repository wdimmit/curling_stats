"""A practice throw's clip: which camera when, and the cut itself."""

from types import SimpleNamespace

import pytest

from curling_score.practice import clips as C


def view(exit_m=20.0):
    """A long camera whose bottom row is ``exit_m`` from the far tee."""
    return SimpleNamespace(rect=(0, 0, 800, 1080), metres_at=lambda row: exit_m)


def both(exit_m=20.0):
    return {"left": view(exit_m), "right": view(exit_m)}


def throw(house="top", rel=100.0, t_hog=104.0, v_hog=None, t_far=114.0, rest=126.0,
          arrived=True):
    return {"id": f"t_{rel:.1f}", "house": house, "t_release_s": rel, "t_hog_s": t_hog,
            "v_hog_m_s": v_hog, "t_far_s": t_far, "t_rest_s": rest if arrived else None,
            "arrived": arrived, "clip": False}


class TestCameras:
    def test_to_the_top_house_the_right_camera_faces_the_thrower(self):
        p = C.plan(throw(house="top"), both())
        assert [x.view for x in p.pieces] == ["right", "left"]

    def test_to_the_bottom_house_the_left_camera_faces_the_thrower(self):
        p = C.plan(throw(house="bottom"), both())
        assert [x.view for x in p.pieces] == ["left", "right"]

    def test_it_starts_before_the_push_and_ends_after_rest(self):
        p = C.plan(throw(), both())
        assert p.pieces[0].t0 == 95.0
        assert p.pieces[1].t0 == 114.0 and p.pieces[1].t1 == 127.5
        assert p.end == 127.5


class TestLeavingTheFrame:
    def test_at_a_steady_speed_by_distance(self):
        # Hog to hog is 21.945 m in 10 s; the bottom row is 13.599 m past the
        # near hog line.
        t = C.exit_time(throw(), view(20.0))
        assert t == pytest.approx(110.197, abs=0.01)

    def test_slowing_down_from_its_speed_at_the_hog_line(self):
        t = C.exit_time(throw(v_hog=2.6), view(20.0))
        assert t == pytest.approx(109.745, abs=0.01)

    def test_a_frame_that_reaches_past_the_far_hog_line_ends_there(self):
        assert C.exit_time(throw(), view(30.0)) == 114.0

    def test_without_both_hog_times_it_is_not_known(self):
        assert C.exit_time(throw(t_hog=None), view()) is None
        assert C.exit_time(throw(t_far=None), view()) is None

    def test_the_first_piece_ends_as_the_rock_leaves(self):
        p = C.plan(throw(), both(20.0))
        assert p.pieces[0].t1 == pytest.approx(110.197, abs=0.01)


class TestWhatIsMissing:
    def test_no_hog_time_cuts_over_at_the_far_hog_line(self):
        p = C.plan(throw(t_hog=None), both())
        assert p.pieces[0].t1 == 114.0 and p.pieces[1].t0 == 114.0

    def test_no_hog_times_at_all_gives_the_thrower_ten_seconds(self):
        p = C.plan(throw(t_hog=None, t_far=None), both())
        assert p.pieces[0].t1 == 110.0
        assert p.pieces[1].t0 == 110.0 and p.pieces[1].t1 == 127.5

    def test_a_throw_that_never_arrived_is_one_piece(self):
        p = C.plan(throw(arrived=False, t_far=None), both())
        assert [(x.view, x.t0, x.t1) for x in p.pieces] == [("right", 95.0, 110.0)]

    def test_only_the_camera_behind_is_one_piece_from_it(self):
        p = C.plan(throw(house="top"), {"left": view()})
        assert [(x.view, x.t0, x.t1) for x in p.pieces] == [("left", 95.0, 127.5)]

    def test_only_the_camera_facing_is_one_piece_from_it(self):
        p = C.plan(throw(house="top"), {"right": view()})
        assert [(x.view, x.t0, x.t1) for x in p.pieces] == [("right", 95.0, 127.5)]

    def test_no_release_or_no_side_views_is_no_clip(self):
        assert C.plan({**throw(), "t_release_s": None}, both()) is None
        assert C.plan(throw(), None) is None and C.plan(throw(), {}) is None


class TestUntil:
    def test_a_plan_cut_short_where_the_recording_stops(self):
        p = C.plan(throw(), both()).until(120.0)
        assert p.end == 120.0 and len(p.pieces) == 2
        assert C.plan(throw(), both()).until(112.0).pieces[-1].view == "right"
        assert C.plan(throw(), both()).until(90.0) is None
