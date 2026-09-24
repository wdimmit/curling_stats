"""scripts/broom/harvest.py: a frame's manifest row must be able to map a box
back to the house, which is what the broom set is scored through."""
import importlib.util
from pathlib import Path

import pytest

from curling_score.geometry.sideview import SideView
from tests import synth

_spec = importlib.util.spec_from_file_location(
    "broom_harvest", Path(__file__).parents[1] / "scripts" / "broom" / "harvest.py")
harvest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harvest)

ENTRY = {"rect": [1107, 0, 810, 1080], "tee_row": 430.0, "hog_row": 520.0}


class TestViewFor:
    def test_a_timeline_that_carries_the_lateral_scale_is_taken_at_its_word(self):
        v = harvest.view_for({**ENTRY, "centre_col": 391.0,
                              "lat_px_per_m_at_tee": 149.0})
        assert (v.centre_col, v.lat_px_per_m_at_tee) == (391.0, 149.0)

    def test_an_older_timeline_is_fitted_from_the_view_s_own_frames(self):
        frames = [synth.side_view_house(centre_col=390.0, lat_px_per_m=148.0)] * 3
        v = harvest.view_for(ENTRY, frames)
        assert v.rect == (1107, 0, 810, 1080)       # still the composite's rect
        assert v.centre_col == pytest.approx(390.0, abs=1.0)
        assert v.lat_px_per_m_at_tee == pytest.approx(148.0, rel=0.015)


class TestManifestRow:
    def test_a_row_rebuilds_a_view_that_maps_to_the_house(self):
        view = SideView(rect=(1107, 0, 810, 1080), tee_row=430.0, hog_row=520.0,
                        centre_col=390.0, lat_px_per_m_at_tee=148.0)
        row = harvest.manifest_row(view, stem="s", crop_top=300)
        again = SideView(rect=tuple(row["rect"]), tee_row=row["tee_row"],
                         hog_row=row["hog_row"], centre_col=row["centre_col"],
                         lat_px_per_m_at_tee=row["lat_px_per_m_at_tee"])
        assert again.to_house(390.0, 430.0) == pytest.approx((0.0, 0.0))
        assert (row["stem"], row["crop_top"]) == ("s", 300)

    def test_a_row_can_name_which_camera_it_came_from(self):
        """`view` is a manifest field ("left"/"right") as well as a SideView."""
        view = SideView(rect=(0, 0, 810, 1080), tee_row=430.0, hog_row=520.0,
                        centre_col=390.0, lat_px_per_m_at_tee=148.0)
        assert harvest.manifest_row(view, view="left")["view"] == "left"
