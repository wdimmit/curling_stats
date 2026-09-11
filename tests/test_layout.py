import numpy as np
import pytest

from curling_score.geometry import layout

BAR = 208  # the grey letterbox bars around the overhead strip


def synth_frames(n=24, ice_band=None, seed=0):
    """Build frames shaped like the club composite.

    Everything except the separator bars changes frame to frame, which is the
    property the detector must key on. ``ice_band`` optionally paints a
    spatially uniform, bright horizontal band *inside* the top panel — clean ice
    looks exactly like this in a single frame and previously fooled a
    brightness-based detector into treating it as a panel separator.
    """
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        f = rng.integers(0, 255, (1080, 1920, 3), dtype=np.uint8)
        f[:, 800:810] = BAR          # left edge of the strip
        f[:, 1107:1119] = BAR        # right edge of the strip
        f[0:10, 810:1107] = BAR      # above the top panel
        f[524:554, 810:1107] = BAR   # between the panels
        f[1069:1080, 810:1107] = BAR # below the bottom panel
        if ice_band is not None:
            y0, y1 = ice_band
            # Uniform across the row, bright, but flickering over time.
            f[y0:y1, 810:1107] = rng.integers(200, 250)
        out.append(f)
    return out


class TestDetectPanels:
    def test_finds_both_overhead_panels(self):
        got = layout.detect_panels(synth_frames())
        assert got.top == (810, 10, 297, 514)
        assert got.bottom == (810, 554, 297, 515)

    def test_ignores_a_uniform_bright_ice_band_inside_a_panel(self):
        # Regression: Sheet 4 at t=1500 had clean ice at y=358..371 that a
        # brightness heuristic read as a separator, putting the "bottom panel"
        # crop inside the top panel so no house was found at all.
        got = layout.detect_panels(synth_frames(ice_band=(358, 372)))
        assert got.top == (810, 10, 297, 514)
        assert got.bottom == (810, 554, 297, 515)

    def test_rejects_footage_with_no_overhead_strip(self):
        rng = np.random.default_rng(1)
        noise = [rng.integers(0, 255, (1080, 1920, 3), dtype=np.uint8) for _ in range(8)]
        with pytest.raises(layout.LayoutError):
            layout.detect_panels(noise)

    def test_needs_more_than_one_frame_to_judge_what_is_static(self):
        with pytest.raises(layout.LayoutError):
            layout.detect_panels(synth_frames(n=1))


class TestOnRealFootage:
    @pytest.mark.slow
    def test_recovers_the_known_sheet_2_layout(self, primary_video):
        from curling_score.ingest import frames as F

        sample = []
        for i, (_, img) in enumerate(F.keyframe_sweep(primary_video)):
            if i % 90 == 0:  # spread across the whole 4 hours
                sample.append(img)
            if len(sample) == 24:
                break

        got = layout.detect_panels(sample)

        # Strip bounds are unambiguous.
        assert got.top[0] == got.bottom[0] == 810
        assert got.top[2] == got.bottom[2] == 297
        # Panel edges carry one anti-aliased blend row (y=1069 measures 200.9,
        # between panel content at ~139 and bar at ~207), so the height is
        # inherently +/-1 depending on which side that row is assigned to.
        assert got.top[1] == 10
        assert abs(got.top[3] - 514) <= 2
        assert got.bottom[1] == 554
        assert abs(got.bottom[3] - 515) <= 2


class TestAcrossEverySheet:
    """The five sheets have five different layouts; none may be hardcoded."""

    # Measured during planning: sheet -> (strip_x0, strip_w, top_y1, bottom_y0).
    EXPECTED = {
        1: (813, 297, 521, 554),
        2: (810, 297, 524, 554),
        3: (809, 302, 532, 540),
        4: (813, 294, 537, 554),
        5: (807, 300, 544, 554),
    }

    @pytest.mark.parametrize("sheet", sorted(EXPECTED))
    def test_recovers_that_sheets_layout(self, sheet, harvested_frames):
        from tests.conftest import VALIDATION_VIDS

        imgs = harvested_frames(VALIDATION_VIDS[sheet])
        got = layout.detect_panels(imgs)
        x0, width, top_y1, bot_y0 = self.EXPECTED[sheet]

        # +/-1 px throughout: panel edges carry an anti-aliased blend row.
        assert abs(got.top[0] - x0) <= 1, f"strip x0 {got.top[0]} != {x0}"
        assert abs(got.top[2] - width) <= 1, f"strip width {got.top[2]} != {width}"
        assert abs((got.top[1] + got.top[3]) - top_y1) <= 1
        assert abs(got.bottom[1] - bot_y0) <= 1
        assert got.top[0] == got.bottom[0]
        assert got.top[2] == got.bottom[2]
