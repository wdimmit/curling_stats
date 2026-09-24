"""The skip's target broom: the pad held still in the second before the tee."""
from types import SimpleNamespace

import numpy as np
import pytest

from curling_score.game import broomtime
from curling_score.geometry.sideview import SideView


def s(i, x, y, conf=0.8):
    return (i, x, y, conf)


class TestPickTarget:
    def test_a_pad_held_still_is_the_target(self):
        got = broomtime.pick_target([s(i, 0.60, -0.20) for i in range(10)], 10)
        assert (got.x_m, got.y_m) == pytest.approx((0.60, -0.20))
        assert got.seen == pytest.approx(1.0)

    def test_a_stray_does_not_outvote_a_held_pad(self):
        held = [s(i, 0.6, -0.2) for i in range(8)]
        stray = [s(i, -1.4, 1.0, 0.95) for i in range(3)]
        got = broomtime.pick_target(held + stray, 10)
        assert got.x_m == pytest.approx(0.6)

    def test_a_pad_down_for_under_half_the_window_is_no_call(self):
        """A clock offset can put the lift inside the window; a half-seen pad
        is not averaged into a marker somewhere it never was."""
        assert broomtime.pick_target([s(i, 0.6, -0.2) for i in range(4)], 10) is None

    def test_two_pads_held_still_go_to_the_one_nearest_the_tee(self):
        skip = [s(i, 0.5, 0.3) for i in range(10)]
        sideline = [s(i, 2.1, 0.9) for i in range(10)]     # VXU9 e5 s4
        got = broomtime.pick_target(sideline + skip, 10)
        assert got.x_m == pytest.approx(0.5)

    def test_behind_the_back_line_is_the_other_skip(self):
        assert broomtime.pick_target([s(i, 0.2, -2.1) for i in range(10)], 10) is None

    def test_past_the_hog_line_or_off_the_sheet_is_refused(self):
        assert broomtime.pick_target([s(i, 0.0, 6.8) for i in range(10)], 10) is None
        assert broomtime.pick_target([s(i, 2.4, 0.0) for i in range(10)], 10) is None

    def test_a_guard_call_in_front_of_the_house_is_kept(self):
        got = broomtime.pick_target([s(i, 0.1, 4.0) for i in range(10)], 10)
        assert got.y_m == pytest.approx(4.0)

    def test_the_median_ignores_one_wobble(self):
        pts = [s(i, 0.60, -0.20) for i in range(9)] + [s(9, 0.72, -0.10)]
        got = broomtime.pick_target(pts, 10)
        assert (got.x_m, got.y_m) == pytest.approx((0.60, -0.20))

    def test_nothing_seen_is_no_call(self):
        assert broomtime.pick_target([], 10) is None


class TestWindowFor:
    def test_the_second_before_an_observed_tee_crossing(self):
        shot = SimpleNamespace(missing=False, release=None, tee_s=100.0,
                               tee_estimated=False)
        assert broomtime.window_for(shot) == pytest.approx((99.0, 100.0))

    def test_an_estimated_crossing_is_used_too(self):
        shot = SimpleNamespace(missing=False, release=None, tee_s=50.0,
                               tee_estimated=True)
        assert broomtime.window_for(shot) == pytest.approx((49.0, 50.0))

    def test_a_placeholder_or_an_untimed_shot_has_none(self):
        assert broomtime.window_for(SimpleNamespace(missing=True, release=None,
                                                    tee_s=10.0)) is None
        assert broomtime.window_for(SimpleNamespace(missing=False, release=None,
                                                    tee_s=None)) is None


VIEW = SideView(rect=(0, 0, 810, 1080), tee_row=430.0, hog_row=520.0,
                centre_col=390.0, lat_px_per_m_at_tee=148.0)


class TestTimeTargetBrooms:
    def _shot(self, **kw):
        base = dict(missing=False, release=None, tee_s=100.0, tee_estimated=False,
                    target_broom=None)
        base.update(kw)
        return SimpleNamespace(**base)

    def _run(self, shots, monkeypatch, view=VIEW, col=390.0 + 0.5 * 148.0, row=430.0):
        from curling_score.detect import broommodel as bm
        seen = []

        def decode(video, rect, t0, t1, fps):
            seen.append((t0, t1, fps))
            return [np.zeros((1080, 810, 3), np.uint8)] * 10, [t0 + i / fps for i in range(10)]

        monkeypatch.setattr(bm, "find", lambda model, frames, view, conf=bm.CONF_MIN: [
            [bm.Pad(col, row, 0.9, col - 15, row - 10, col + 15, row)] for _ in frames])
        broomtime.time_target_brooms(shots, "v.mp4", view, model=object(), decode=decode)
        return seen

    def test_it_attaches_the_broom_in_house_metres(self, monkeypatch):
        shot = self._shot()
        seen = self._run([shot], monkeypatch)
        assert seen == [(pytest.approx(99.0), pytest.approx(100.0), broomtime.FPS)]
        assert (shot.target_broom.x_m, shot.target_broom.y_m) == pytest.approx(
            (0.5, 0.0), abs=1e-6)

    def test_a_placeholder_is_left_alone(self, monkeypatch):
        shot = self._shot(missing=True)
        assert self._run([shot], monkeypatch) == [] and shot.target_broom is None

    def test_no_lateral_calibration_is_a_no_op(self, monkeypatch):
        shot = self._shot()
        flat = SideView(rect=(0, 0, 810, 1080), tee_row=430.0, hog_row=520.0)
        assert self._run([shot], monkeypatch, view=flat) == [] and shot.target_broom is None

    def test_no_model_is_a_no_op(self):
        shot = self._shot()
        broomtime.time_target_brooms([shot], "v.mp4", VIEW, model=None,
                                     decode=lambda *a: ([], []))
        assert shot.target_broom is None

    def test_no_side_view_is_a_no_op(self):
        shot = self._shot()
        broomtime.time_target_brooms([shot], "v.mp4", None, model=object(),
                                     decode=lambda *a: ([], []))
        assert shot.target_broom is None
