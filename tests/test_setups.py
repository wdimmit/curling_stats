import json

import numpy as np
import pytest

from curling_score.geometry import calibrate
from curling_score.harvest import setups


def calib(px_per_m=75.0):
    return calibrate.PanelCalib(center_px=(148.0, 150.0), px_per_m=px_per_m,
                                edge_erosion_px=1.7, residual_m=0.003, flipped=False)


def panel(name="top", rect=(810, 10, 297, 514), cal=None, error=None):
    return setups.PanelInfo(name=name, rect=rect, calib=cal,
                            lighting={"lit": 10}, error=error)


class TestJsonRoundTrip:
    def test_a_calibrated_setup_survives_json(self):
        # JSON and not pickle: ds8's setups were pickled into /tmp and died
        # with it, which is why that dataset can never be rebuilt.
        before = setups.VideoSetup(
            video_id="mBBGkVPcPBQ", frame_size=(1920, 1080),
            panels={"top": panel("top", cal=calib()),
                    "bottom": panel("bottom", (810, 554, 297, 516), calib(76.1))},
            frame_times=(700.0, 2190.0), error=None)
        after = setups.from_json(json.loads(json.dumps(setups.to_json(before))))
        assert after == before

    def test_an_uncalibrated_panel_survives_with_its_reason(self):
        before = setups.VideoSetup(
            video_id="v", frame_size=(1920, 1080),
            panels={"top": panel("top", cal=calib()),
                    "bottom": panel("bottom", error="no well-lit frames")},
            frame_times=(700.0,), error=None)
        after = setups.from_json(json.loads(json.dumps(setups.to_json(before))))
        assert after == before
        assert after.panels["bottom"].calib is None

    def test_a_video_with_no_layout_survives(self):
        before = setups.VideoSetup(video_id="v", frame_size=(1920, 1080), panels={},
                                   frame_times=(), error="need at least 2 frames")
        assert setups.from_json(json.loads(json.dumps(setups.to_json(before)))) == before


class TestUsablePanels:
    def test_lists_only_the_panels_that_calibrated(self):
        s = setups.VideoSetup("v", (1920, 1080),
                              {"top": panel("top", cal=calib()),
                               "bottom": panel("bottom", error="dark")}, (), None)
        assert [p.name for p in setups.usable_panels(s)] == ["top"]

    def test_one_good_panel_is_enough_to_keep_a_video(self):
        # The club kills the lights on a sheet as it finishes while others play
        # on, so a video with one dark house is a normal night, not a broken
        # one -- and the lit house still holds a whole game.
        s = setups.VideoSetup("v", (1920, 1080),
                              {"top": panel("top", cal=calib()),
                               "bottom": panel("bottom", error="dark")}, (), None)
        assert setups.is_usable(s)

    def test_a_video_with_no_calibrated_panel_is_not_usable(self):
        s = setups.VideoSetup("v", (1920, 1080),
                              {"top": panel("top", error="dark"),
                               "bottom": panel("bottom", error="dark")}, (), None)
        assert not setups.is_usable(s)

    def test_a_layout_failure_is_not_usable(self):
        assert not setups.is_usable(
            setups.VideoSetup("v", (1920, 1080), {}, (), "LayoutError"))


class TestDerive:
    def test_records_a_layout_failure_instead_of_raising(self):
        # 130 unseen videos will not all work. A harvest that dies on the first
        # odd one is a harvest nobody can run overnight.
        blank = [np.zeros((1080, 1920, 3), np.uint8) for _ in range(4)]
        got = setups.derive("v", list(zip([1.0, 2.0, 3.0, 4.0], blank)))
        assert got.error and not setups.is_usable(got)

    def test_refuses_too_few_frames_to_tell_a_bar_from_still_ice(self):
        # detect_panels finds bars by what does not change over time. Two
        # frames a second apart would read a settled house as a bar.
        with pytest.raises(ValueError):
            setups.derive("v", [(1.0, np.zeros((1080, 1920, 3), np.uint8))])
