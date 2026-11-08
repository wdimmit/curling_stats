"""What the long camera behind the thrower fills in where the overheads see nothing (DFW):
the far hog crossing, so the hog-to-hog split, and guards resting beyond the overhead's view."""

import math
from types import SimpleNamespace

import pytest

from curling_score.game import clips as game_clips
from curling_score.game import fartime, longguards
from curling_score.game import split as split_mod
from curling_score.geometry import constants as C

# DFW's 5U qualifier, as its run calibrated it (46wEMds_tYQ / tWNVIspzMUs).
CAL = {"left": {"rect": [1143, 390, 777, 690], "tee_row": 71.47, "hog_row": 152.63, "d_m": 37.5264,
                "centre_col": 358.59, "lat_px_per_m_at_tee": 149.891,
                "centre_line": [358.629, -0.000589]},
       "right": {"rect": [0, 390, 764, 690], "tee_row": 68.73, "hog_row": 158.05, "d_m": 41.2064,
                 "centre_col": 374.4, "lat_px_per_m_at_tee": 163.99,
                 "centre_line": [373.408, 0.01446]}}
VIEW = game_clips.sideviews_from_calibration(CAL)["left"]
LINE_M = C.TEE_TO_HOGLINE_M + C.HOGLINE_WIDTH_M


def moving_track(t0, y0_m, v, n=40, fps=10.0, x_m=0.0):
    """A stone's near edge (toward the camera) going away from it at ``v`` m/s."""
    out = []
    for i in range(n):
        t = t0 + i / fps
        y_edge = y0_m - v * (t - t0)          # metres from the far tee, near edge
        if y_edge < 0.5:
            break
        row = VIEW.row_for(y_edge)
        out.append((t, row, 20.0, VIEW.centre_col_at(row) + x_m * VIEW.lateral_px_per_m(row)))
    return out


# --- far hog -------------------------------------------------------------------------------

def test_the_crossing_is_the_leading_edge_reaching_the_line():
    v, y0, t0 = 2.4, 12.0, 100.0
    track = moving_track(t0, y0, v)
    t_true = t0 + (y0 - 2 * C.STONE_RADIUS_M - LINE_M) / v
    got = fartime.long_camera_crossing({1: track}, VIEW, t_from=99.0)
    assert got is not None
    assert got[0] == pytest.approx(t_true, abs=0.05)
    assert got[1] == pytest.approx(v, rel=0.05)


def test_parked_stones_are_not_a_crossing():
    still = [(100 + i / 10, VIEW.row_for(5.0), 20.0, 360.0) for i in range(40)]
    assert fartime.long_camera_crossing({1: still}, VIEW, t_from=99.0) is None


def test_a_stone_going_the_other_way_is_not_a_crossing():
    away = [(t, row, w, cx) for (t, row, w, cx) in moving_track(100.0, 12.0, 2.4)]
    toward = [(200 - t, row, w, cx) for (t, row, w, cx) in away]
    assert fartime.long_camera_crossing({1: sorted(toward)}, VIEW, t_from=0.0) is None


def test_a_crossing_before_the_window_is_ignored():
    assert fartime.long_camera_crossing({1: moving_track(100.0, 12.0, 2.4)}, VIEW, t_from=110.0) is None


def test_the_pass_fills_only_shots_without_a_far_crossing():
    track = moving_track(100.0, 12.0, 2.4)
    timed = SimpleNamespace(missing=False, color="red", t_hog_s=88.0, release=None,
                            far_crossing=split_mod.FarCrossing(t=101.0))
    untimed = SimpleNamespace(missing=False, color="red", t_hog_s=88.0, release=None,
                              far_crossing=None)
    n = fartime.time_far_crossings_from_long_camera(
        [timed, untimed], "video", VIEW, model=object(),
        decode=lambda *a, **k: (["f"] * 10, [100.0 + i / 10 for i in range(10)]),
        propose=lambda *a, **k: {1: track})
    assert n == 1
    assert timed.far_crossing.t == 101.0
    assert untimed.far_crossing.t == pytest.approx(100.0 + (12.0 - 2 * C.STONE_RADIUS_M - LINE_M) / 2.4,
                                                   abs=0.05)


# --- guards ---------------------------------------------------------------------------------

def edge_of(x_m, y_m):
    """The (edge_row, cx) the camera shows for a stone centred at house (x, y)."""
    from_tee = y_m + C.STONE_RADIUS_M        # the footprint's near edge, toward the camera
    row = VIEW.row_for(from_tee)
    return row, VIEW.centre_col_at(row) + x_m * VIEW.lateral_px_per_m(row)


def test_a_guard_beyond_the_overhead_is_placed():
    row, cx = edge_of(0.3, 5.0)
    got = longguards.place([(row, cx, "red")], VIEW, known=[], from_m=3.3)
    assert len(got) == 1
    assert got[0]["x"] == pytest.approx(0.3, abs=0.05) and got[0]["y"] == pytest.approx(5.0, abs=0.05)
    assert got[0]["color"] == "red" and got[0]["source"] == "long" and got[0]["in_house"] is False


def test_stones_the_overhead_sees_or_past_the_hog_line_are_left_alone():
    near = edge_of(0.0, 1.5)                  # in the overhead's view
    past = edge_of(0.0, 8.0)                  # beyond the hog line: not in play
    dup = edge_of(0.5, 3.6)                   # the overhead has it already
    got = longguards.place([(*near, "red"), (*past, "red"), (*dup, "yellow")], VIEW,
                           known=[{"x": 0.55, "y": 3.55, "color": "yellow"}], from_m=3.3)
    assert got == []


def test_add_puts_guards_on_every_rock_after_and_marks_the_thrown_one():
    end = {"house": "top", "shots": [
        {"number": 1, "color": "red", "t_rest_s": 10.0, "stones": [], "delivered_stone_index": None},
        {"number": 2, "color": "yellow", "t_rest_s": 40.0,
         "stones": [{"x": 0.0, "y": 0.5, "color": "yellow", "distance_to_tee": 0.5, "in_house": True}],
         "delivered_stone_index": 0}],
        "final_stones": [{"x": 0.0, "y": 0.5, "color": "yellow", "distance_to_tee": 0.5, "in_house": True}]}
    guard = edge_of(-0.4, 5.2)
    n = longguards.add(end, VIEW, lambda t: [(*guard, "red")], from_m=3.3)
    assert n == 2
    s1, s2 = end["shots"]
    assert s1["stones"][0]["source"] == "long" and s1["delivered_stone_index"] == 0
    assert any(s.get("source") == "long" for s in s2["stones"]) and s2["delivered_stone_index"] == 0
    assert any(s.get("source") == "long" for s in end["final_stones"])
    assert math.isclose(s1["stones"][0]["distance_to_tee"],
                        math.hypot(s1["stones"][0]["x"], s1["stones"][0]["y"]), abs_tol=1e-6)


def test_dallas_turns_both_on_and_granite_neither():
    from curling_score import clubs
    assert clubs.DALLAS.far_hog_from_long and clubs.DALLAS.guards_from_long_m == pytest.approx(3.3)
    assert not clubs.GRANITE.far_hog_from_long and clubs.GRANITE.guards_from_long_m is None


# --- rocks seen at neither end ----------------------------------------------------------------

def test_the_long_camera_follows_lost_rocks_only_where_the_overhead_misses_guards():
    from curling_score.analyze import follows_lost
    long_view = SimpleNamespace(end_camera=False)
    end_view = SimpleNamespace(end_camera=True)
    assert follows_lost(long_view, 3.3)            # DFW
    assert not follows_lost(long_view, None)       # Granite: unchanged
    assert follows_lost(end_view, None)            # Cincinnati's end camera
    assert not follows_lost(None, 3.3)


def test_a_rock_with_no_arrival_is_read_once_it_has_stopped_and_named_a_guard():
    """A release the far house never saw arrive "rests" 6 s after it -- still
    sliding mid-sheet -- so its guard was never found at that read. Timed
    across the far hog line, it is read after that instead (before the next
    rock is thrown) and the guard of its colour that appeared is the rock."""
    end = {"house": "top", "shots": [
        {"number": 1, "color": "yellow", "t_release_s": 100.0, "t_rest_s": 106.0,
         "t_far_s": 119.0, "shot_type": "unknown", "stones": [], "delivered_stone_index": None},
        {"number": 2, "color": "red", "t_release_s": 140.0, "t_rest_s": 160.0,
         "stones": [], "delivered_stone_index": None, "shot_type": "draw"}],
        "final_stones": []}
    guard = edge_of(0.03, 4.5)
    reads = []

    def read(t):
        reads.append(t)
        return [(*guard, "yellow")] if t >= 122.0 else []

    longguards.add(end, VIEW, read, from_m=3.3)
    s1 = end["shots"][0]
    assert 122.0 <= reads[0] < 140.0
    assert s1["delivered_stone_index"] == 0 and s1["shot_type"] == "guard"
    assert s1["stones"][0]["y"] == pytest.approx(4.5, abs=0.05)


def test_a_rock_read_after_its_far_crossing_is_read_before_the_next_throw():
    end = {"house": "top", "shots": [
        {"number": 1, "color": "yellow", "t_release_s": 100.0, "t_rest_s": 106.0,
         "t_far_s": 119.0, "shot_type": "unknown", "stones": [], "delivered_stone_index": None},
        {"number": 2, "color": "red", "t_release_s": 124.0, "t_rest_s": 150.0,
         "stones": [], "delivered_stone_index": None, "shot_type": "draw"}],
        "final_stones": []}
    reads = []
    longguards.add(end, VIEW, lambda t: reads.append(t) or [], from_m=3.3)
    assert reads[0] <= 123.0
