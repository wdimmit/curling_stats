import numpy as np
import pytest

from curling_score.harvest import sideviews
from tests import synth
from tests.conftest import VALIDATION_VIDS


def _composite(tee_row=430.0, hog_row=520.0, width=1920, height=1080):
    """A whole 1920x1080 composite: two side views round an overhead strip."""
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    left = synth.side_view(tee_row=tee_row, hog_row=hog_row)
    # The right view fills columns 1108:1920, 812 px wide -- 2 px wider than
    # ``side_view``'s 810 px default, so it is asked for explicitly here.
    right = synth.side_view(tee_row=tee_row, hog_row=hog_row, w=812)
    frame[:, 0:810] = left[:, 0:810]
    frame[:, 1108:1920] = right[:, 0:812]
    frame[:, 810:1108] = synth.composite_strip(298, height)
    return frame


class TestDerive:
    def test_both_views_solve(self):
        got = sideviews.derive("vid", [_composite() for _ in range(4)])
        assert got.error is None
        assert sorted(got.views) == ["left", "right"]
        for name in ("left", "right"):
            assert got.views[name].view is not None
            assert got.views[name].view.tee_row == pytest.approx(430.0, abs=2.0)

    def test_a_video_with_no_overhead_strip_is_recorded_not_raised(self):
        blank = [np.full((1080, 1920, 3), 120, np.uint8) for _ in range(4)]
        got = sideviews.derive("vid", blank)
        assert got.error is not None and "layout" in got.error
        assert not sideviews.is_usable(got)

    def test_a_view_that_will_not_solve_keeps_the_other(self):
        frames = [_composite() for _ in range(4)]
        for f in frames:                      # flatten the right view's paint
            f[:, 1108:1920] = 150
        got = sideviews.derive("vid", frames)
        assert got.views["right"].view is None
        assert got.views["right"].error
        assert got.views["left"].view is not None
        assert sideviews.is_usable(got)

    def test_json_round_trips(self):
        got = sideviews.derive("vid", [_composite() for _ in range(4)])
        back = sideviews.from_json(sideviews.to_json(got))
        assert back == got


class TestPlate:
    def test_the_median_removes_a_transient(self):
        frames = [_composite() for _ in range(5)]
        frames[2][500:540, 100:200] = 0        # one frame has a person in it
        p = sideviews.plate(frames)
        assert p[500:540, 100:200].mean() > 100


@pytest.mark.parametrize("sheet,vid", sorted(VALIDATION_VIDS.items()))
def test_real_videos_calibrate_both_views(harvested_frames, sheet, vid):
    got = sideviews.derive(vid, harvested_frames(vid))
    assert got.error is None, got.error
    assert len(sideviews.usable_views(got)) == 2, {
        n: got.views[n].error for n in got.views}


class TestViewJsonLateral:
    def test_the_across_calibration_round_trips(self):
        from curling_score.geometry.sideview import SideView
        from curling_score.harvest import sideviews as SV
        v = SideView(rect=(0, 0, 810, 1080), tee_row=429.95, hog_row=520.0,
                     centre_col=390.1, lat_px_per_m_at_tee=148.5,
                     centre_line=(380.0, 0.01))
        assert SV._view_from_json(SV._view_to_json(v), v.rect) == v

    def test_json_from_before_it_existed_still_loads(self):
        from curling_score.harvest import sideviews as SV
        got = SV._view_from_json({"tee_row": 430.0, "hog_row": 520.0,
                                  "d_m": 40.2335}, (0, 0, 810, 1080))
        assert got.centre_col is None and not got.has_lateral
