"""The destination hog line from an end camera, for a house-only overhead (game.fartime)."""
from types import SimpleNamespace

import pytest

from curling_score.game import fartime, split
from curling_score.geometry import constants as C
from tests.test_endcam import TRUE

VIEW = TRUE.__class__(**{**TRUE.__dict__, "end_camera": True})
LINE_M = C.TEE_TO_HOGLINE_M + 2 * C.STONE_RADIUS_M      # the near edge as the leading edge crosses


def coming_in(t0, speed, *, col=300.0, until=2.0, y_from=6.9):
    """Box samples (t, bottom_row, width, cx) of a stone coming in from the view's edge."""
    out = []
    for i in range(int(until * 30)):
        t = t0 + i / 30
        y = y_from - speed * (t - t0)
        row = VIEW.row_for(y)
        if row < VIEW.rect[3]:
            out.append((t, min(row, VIEW.rect[3] - 1.0), 50.0, col))
    return out


def parked(y, t0, *, col=300.0, until=6.0):
    return [(t0 + i / 30, VIEW.row_for(y), 50.0, col) for i in range(int(until * 30))]


class TestEndCameraCrossing:
    def test_times_the_leading_edge_from_the_first_frames_in_view(self):
        got = fartime.end_camera_crossing({2: coming_in(110.0, 2.0)}, VIEW, 105.0)
        assert got[0] == pytest.approx(110.0 + (6.9 - LINE_M) / 2.0, abs=0.02)
        assert got[1] == pytest.approx(2.0, abs=0.05)

    def test_a_stone_parked_in_the_same_column_does_not_take_over(self):
        tracks = {2: sorted(parked(5.9, 105.0) + coming_in(110.0, 1.8))}
        got = fartime.end_camera_crossing(tracks, VIEW, 105.0)
        assert got[0] == pytest.approx(110.0 + (6.9 - LINE_M) / 1.8, abs=0.03)

    def test_a_parked_stone_alone_is_no_crossing(self):
        assert fartime.end_camera_crossing({2: parked(6.0, 105.0)}, VIEW, 105.0) is None


class TestTimeFromEndCamera:
    def test_fills_only_the_shots_the_panels_did_not_time(self):
        timed = SimpleNamespace(color="red", t_hog_s=100.0, far_crossing=split.FarCrossing(t=111.0))
        untimed = SimpleNamespace(color="red", t_hog_s=100.0, far_crossing=split.FarCrossing(t=None, v_near=2.0))
        windows = []

        def decode(video, rect, t0, t1, fps):
            windows.append((t0, t1))
            return [0], [t0]

        n = fartime.time_far_crossings_from_end_camera(
            [timed, untimed], "v.mp4", VIEW, model=object(), decode=decode,
            propose=lambda *a, **k: {2: coming_in(110.0, 2.0)})
        assert n == 1 and windows == [(105.0, 120.0)]
        assert timed.far_crossing.t == 111.0
        assert untimed.far_crossing.t == pytest.approx(110.0 + (6.9 - LINE_M) / 2.0, abs=0.02)
        assert untimed.far_crossing.v_near == 2.0

    def test_leaves_a_long_camera_alone(self):
        shot = SimpleNamespace(color="red", t_hog_s=100.0)
        assert fartime.time_far_crossings_from_end_camera([shot], "v.mp4", TRUE, model=object()) == 0
