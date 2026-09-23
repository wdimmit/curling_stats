"""The hog line, found where its paint is in each overhead panel."""

import numpy as np
import pytest

from curling_score.geometry import hogpaint
from curling_score.geometry.calibrate import PanelCalib
from tests.synth_hogline import BLUE_BGR, BOTTOM, TOP, plate_with_line


def expected_outer(c, outer_row, bow=0.0004, w=300):
    return outer_row + bow * (c - w / 2) ** 2


class TestFindingThePaint:
    def test_finds_a_bowed_line_on_a_top_panel(self):
        line = hogpaint.find_hog_line(plate_with_line(), TOP)
        for c in (20, 150, 280):
            assert np.polyval(line.coef, c) == pytest.approx(expected_outer(c, 516.0), abs=0.5)

    def test_finds_it_on_a_flipped_bottom_panel(self):
        plate = plate_with_line(h=516, flipped=True, outer_row=15.0)
        line = hogpaint.find_hog_line(plate, BOTTOM)
        for c in (20, 150, 280):
            assert np.polyval(line.coef, c) == pytest.approx(expected_outer(c, 15.0), abs=0.5)

    def test_a_red_blob_between_the_paint_and_the_frame_edge_does_not_pull_the_fit(self):
        plate = plate_with_line()
        yy, xx = np.mgrid[0:534, 0:300]
        plate[(yy - 526) ** 2 + (xx - 100) ** 2 <= 7 ** 2] = (40, 40, 210)
        line = hogpaint.find_hog_line(plate, TOP)
        assert np.polyval(line.coef, 100) == pytest.approx(expected_outer(100, 516.0), abs=0.5)

    def test_it_records_how_much_paint_it_saw(self):
        line = hogpaint.find_hog_line(plate_with_line(), TOP)
        assert line.columns >= 290
        assert line.scatter_px < 0.6


class TestRefusingPaintItCannotTrust:
    def test_too_few_columns(self):
        with pytest.raises(hogpaint.HogPaintError, match="columns"):
            hogpaint.find_hog_line(plate_with_line(cols=range(0, 50)), TOP)

    def test_a_line_outside_the_far_band_is_not_found(self):
        with pytest.raises(hogpaint.HogPaintError, match="columns"):
            hogpaint.find_hog_line(plate_with_line(outer_row=300.0), TOP)

    def test_a_ragged_edge_is_refused(self):
        with pytest.raises(hogpaint.HogPaintError, match="scatter"):
            hogpaint.find_hog_line(plate_with_line(jitter=lambda c: 2 if c % 2 else -2), TOP)

    def test_paint_at_an_implausible_position_is_refused(self):
        coarse = PanelCalib(center_px=(150.0, 161.0), px_per_m=100.0,
                            edge_erosion_px=0.0, residual_m=0.0, flipped=False)
        with pytest.raises(hogpaint.HogPaintError, match="outside"):
            hogpaint.find_hog_line(plate_with_line(), coarse)


class TestItReadsBgr:
    """Calibration frames arrive BGR. Read as RGB, a first probe of this found
    the line in 8 of 298 columns -- this pins the channel order."""

    def test_red_paint_in_a_bgr_frame_is_found(self):
        assert hogpaint.find_hog_line(plate_with_line(), TOP).columns >= 290

    def test_blue_paint_is_not_a_hog_line(self):
        with pytest.raises(hogpaint.HogPaintError, match="columns"):
            hogpaint.find_hog_line(plate_with_line(colour=BLUE_BGR), TOP)


class TestTheTripwire:
    def test_y_at_adds_the_leading_edge_offset(self):
        line = hogpaint.find_hog_line(plate_with_line(), TOP)
        assert line.y_at(0.0) == pytest.approx(line.outer_edge_y(0.0) + hogpaint.LEADING_EDGE_OFFSET_U)
        assert hogpaint.LEADING_EDGE_OFFSET_U == 0.080

    def test_it_follows_the_bow_off_the_centre_line(self):
        line = hogpaint.find_hog_line(plate_with_line(), TOP)
        assert line.outer_edge_y(1.0) > line.outer_edge_y(0.0) + 0.02

    @pytest.mark.parametrize("calib,plate", [
        (TOP, plate_with_line()),
        (BOTTOM, plate_with_line(h=516, flipped=True, outer_row=15.0)),
    ], ids=["top", "flipped-bottom"])
    def test_an_off_centre_reading_is_the_fitted_row_at_that_column(self, calib, plate):
        """A flipped panel negates x in to_pixels; reading the bow on the wrong
        side would mistime every curled stone on that panel."""
        line = hogpaint.find_hog_line(plate, calib)
        for x_m in (-1.2, 0.7):
            col, _ = calib.to_pixels(x_m, 0.0)
            want = calib.to_sheet(col, float(np.polyval(line.coef, col)))[1]
            assert line.outer_edge_y(x_m) == pytest.approx(want, abs=1e-9)

    def test_it_publishes_what_it_found(self):
        got = hogpaint.find_hog_line(plate_with_line(), TOP).to_json()
        assert set(got) == {"outer_edge_row_coef", "columns", "scatter_px",
                            "width_px", "offset_u"}
        assert got["offset_u"] == hogpaint.LEADING_EDGE_OFFSET_U

    @pytest.mark.parametrize("calib,plate", [
        (TOP, plate_with_line()),
        (BOTTOM, plate_with_line(h=516, flipped=True, outer_row=15.0)),
    ], ids=["top", "flipped-bottom"])
    def test_the_painted_band_s_width_is_measured(self, calib, plate):
        """The synthetic plates paint a 4-row-thick line (``plate_with_line``'s
        default ``thick=4``), on both a top panel and a flipped one."""
        line = hogpaint.find_hog_line(plate, calib)
        assert line.width_px == pytest.approx(4.0, abs=0.5)

    def test_departure_y_at_reads_the_inside_edge(self):
        line = hogpaint.find_hog_line(plate_with_line(), TOP)
        assert line.departure_y_at(0.0) == pytest.approx(
            line.outer_edge_y(0.0) - line.width_px / TOP.px_per_m
            - hogpaint.LEADING_EDGE_OFFSET_U)
        # A departing stone's tripwire sits well below an arriving one's: the
        # width of the paint plus the leading-edge offset on both edges.
        assert line.departure_y_at(0.0) < line.y_at(0.0) - 2 * hogpaint.LEADING_EDGE_OFFSET_U
