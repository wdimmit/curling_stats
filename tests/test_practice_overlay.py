"""Where a throw's broom and lines fall in each piece of its clip."""

import pytest

from curling_score.geometry.sideview import SideView
from curling_score.practice import clips as C
from curling_score.practice import overlay as O

# A long camera calibrated with its far tee on row 350, the far hog line on
# row 430, the centre line down column 400 and 60 px a metre across at the tee.
VIEW = SideView(rect=(0, 0, 800, 1080), tee_row=350.0, hog_row=430.0, centre_col=400.0,
                lat_px_per_m_at_tee=60.0)
TEE = (0.5, 350.0 / 1080)


def throw(house="top", broom=(0.2, 0.0), miss=-0.15, at_hog_x=0.1, start=(0.0, 38.4)):
    line = {"miss_m": miss, "at_hog_x": at_hog_x, "aim_x": None,
            "start": None if start is None else {"x": start[0], "y": start[1]}}
    return {"id": "t_100.0", "house": house, "line": line,
            "broom": None if broom is None else {"x": broom[0], "y": broom[1]}}


PLAN = C.ClipPlan((C.Piece("right", 97.0, 110.2), C.Piece("left", 114.0, 127.5)))


class TestProjection:
    def test_the_far_tee_is_where_the_camera_was_calibrated(self):
        assert O.to_view(VIEW, False, 0.0, 0.0) == pytest.approx(TEE, abs=1e-3)

    def test_the_camera_facing_the_thrower_sees_its_far_tee_as_the_throwing_tee(self):
        assert O.to_view(VIEW, True, 0.0, 34.747) == pytest.approx(TEE, abs=1e-3)

    def test_it_sees_the_sheet_mirrored(self):
        behind = O.to_view(VIEW, False, 0.5, 0.0)
        facing = O.to_view(VIEW, True, 0.5, 34.747)
        assert behind[0] == pytest.approx(430 / 800, abs=1e-3)
        assert facing[0] == pytest.approx(370 / 800, abs=1e-3)

    def test_a_point_at_the_camera_is_dropped(self):
        assert O.to_view(VIEW, False, 0.0, 40.0) is None

    def test_a_view_with_no_lateral_calibration_places_nothing(self):
        flat = SideView(rect=(0, 0, 800, 1080), tee_row=350.0, hog_row=430.0)
        assert O.to_view(flat, False, 0.0, 0.0) is None


class TestOverlay:
    def test_one_entry_per_piece_on_the_clips_own_clock(self):
        got = O.overlay(throw(), PLAN, {"left": VIEW, "right": VIEW})
        assert [(p["t0"], p["t1"]) for p in got] == [(0.0, 13.2), (13.2, 26.7)]

    def test_behind_the_thrower_the_broom_and_where_the_line_passes_it(self):
        behind = O.overlay(throw(), PLAN, {"left": VIEW, "right": VIEW})[1]
        assert behind["broom"] == pytest.approx([(400 + 0.2 * 60) / 800, TEE[1]], abs=1e-3)
        # The thrown line ends at the broom's depth, 0.15 m inside it.
        assert behind["thrown"][-1] == pytest.approx([(400 + 0.05 * 60) / 800, TEE[1]], abs=1e-3)
        # The aim line runs from the hack to the broom.
        assert behind["aim"][-1] == pytest.approx(behind["broom"], abs=1e-3)

    def test_facing_the_thrower_the_line_starts_near_the_release(self):
        facing = O.overlay(throw(), PLAN, {"left": VIEW, "right": VIEW})[0]
        # 2 m past the throwing hog line toward the hack: 6.401 - 2 m from the
        # throwing tee, mirrored, at x = 0.1 + 2 * 0.05 / 28.346.
        first = O.to_view(VIEW, True, 0.1 + 2 * 0.05 / 28.346, 28.346 + 2.0)
        assert facing["thrown"][0] == pytest.approx(first, abs=1e-3)

    def test_without_a_broom_the_line_runs_to_the_far_tee(self):
        t = throw(broom=None, miss=None)
        t["line"]["aim_x"] = -0.3
        behind = O.overlay(t, PLAN, {"left": VIEW, "right": VIEW})[1]
        assert behind["broom"] is None and behind["aim"] is None
        assert behind["thrown"][-1] == pytest.approx([(400 - 0.3 * 60) / 800, TEE[1]], abs=1e-3)

    def test_a_piece_from_an_uncalibrated_camera_has_times_only(self):
        flat = SideView(rect=(0, 0, 800, 1080), tee_row=350.0, hog_row=430.0)
        got = O.overlay(throw(), PLAN, {"left": VIEW, "right": flat})
        assert got[0] == {"t0": 0.0, "t1": 13.2}
        assert "thrown" in got[1]

    def test_no_line_and_no_broom_is_no_overlay(self):
        t = {"id": "t_1.0", "house": "top", "line": None, "broom": None}
        assert O.overlay(t, PLAN, {"left": VIEW, "right": VIEW}) is None


def slid(release=(0.0, 33.0, 0.05), t_release_s=100.0):
    """A throw released at 100.0 on the stream's clock, its delivery sampled
    1 s before the release to 12 s after it (seconds from the release, y, x)."""
    t = throw()
    t["t_release_s"] = t_release_s
    t["line"]["delivery"] = [[-1.0, 37.0, 0.0], [-0.5, 35.0, 0.02], [0.0, 33.0, 0.05],
                             [0.4, 31.0, 0.08], [12.0, 20.0, 0.1]]
    if release is not None:
        t["line"]["release"] = list(release)
    return t


class TestSlideDots:
    def test_each_dot_comes_on_the_clips_clock_as_the_rock_reaches_it(self):
        first = O.overlay(slid(), PLAN, {"left": VIEW, "right": VIEW})[0]
        # The first piece starts at 97.0 on the stream: 99.0 is 2.0 s in. The
        # last sample, 112.0, is after the piece ends at 110.2.
        assert [d[0] for d in first["dots"]] == [2.0, 2.5, 3.0, 3.4]
        assert first["dots"][0][1:3] == pytest.approx(O.to_view(VIEW, True, 0.0, 37.0), abs=1e-4)

    def test_a_dot_the_rock_passed_before_a_piece_shows_from_its_start(self):
        """Behind the thrower the slide runs out of the bottom of the picture;
        only the dot 20 m out is in it, and the rock passed it before 114.0."""
        behind = O.overlay(slid(), PLAN, {"left": VIEW, "right": VIEW})[1]
        assert [d[0] for d in behind["dots"]] == [13.2]
        assert behind["dots"][0][1:3] == pytest.approx(O.to_view(VIEW, False, 0.1, 20.0), abs=1e-4)

    def test_coloured_before_and_after_the_hand_let_go(self):
        first = O.overlay(slid(), PLAN, {"left": VIEW, "right": VIEW})[0]
        assert [d[3] for d in first["dots"]] == ["before", "before", "after", "after"]
        unread = O.overlay(slid(release=None), PLAN, {"left": VIEW, "right": VIEW})[0]
        assert {d[3] for d in unread["dots"]} == {"unknown"}

    def test_dots_closer_than_five_pixels_of_the_clip_are_one(self):
        t = slid()
        # 51 samples 1 mm apart across, at the same depth: the first and the last.
        t["line"]["delivery"] = [[0.01 * i, 33.0, 0.001 * i] for i in range(51)]
        first = O.overlay(t, PLAN, {"left": VIEW, "right": VIEW})[0]
        assert [d[0] for d in first["dots"]] == [3.0, 3.5]

    def test_no_release_time_no_dots(self):
        first = O.overlay(slid(t_release_s=None), PLAN, {"left": VIEW, "right": VIEW})[0]
        assert first["dots"] == []
