import json
import math

from pathlib import Path

import pytest

from curling_score.geometry.sideview import SideView
from curling_score.train import boxedit, segserve

VIEW = SideView(rect=(0, 0, 810, 1080), tee_row=435.0, hog_row=520.0)
GEOM = boxedit.frame_geometry(VIEW, 52.0)


def box_of(width, aspect, bottom_row):
    """A box whose inclusive extent is exactly `width` px wide.

    `score` measures `x1 - x0 + 1`, the pixel-inclusive convention a mask's
    bbox has, so a helper that spans `width` between its edges would be one
    pixel wider than it claims and no assertion about exact ratios would hold.
    """
    h = width * aspect
    return (400.0, bottom_row - h + 1, 400.0 + width - 1, bottom_row)


class TestScore:
    """Geometry's job here is to CHOOSE among SAM's readings of one click, not
    to filter colour blobs. SAM returns the stone, the stone plus its shadow,
    the stone plus the hand on it; `stone_width_at` knows which is stone-sized.
    """

    def test_a_stone_sized_mask_scores_best(self):
        at_hog = VIEW.stone_width_at(520.0, 52.0)
        exact = segserve.score(box_of(at_hog, 0.42, 520.0), GEOM)[0]
        wide = segserve.score(box_of(at_hog * 1.8, 0.42, 520.0), GEOM)[0]
        assert exact < wide

    def test_the_whole_sheet_is_rejected(self):
        assert segserve.score((0, 300, 809, 1079), GEOM)[0] == math.inf

    def test_a_tall_thin_mask_is_rejected(self):
        at_hog = VIEW.stone_width_at(520.0, 52.0)
        assert segserve.score(box_of(at_hog, 3.0, 520.0), GEOM)[0] == math.inf

    def test_half_size_and_double_size_are_equally_wrong(self):
        """Scored on the log of the ratio. On a plain ratio everything too
        small crowds into (0, 1) while everything too large has the rest of
        the line, which quietly makes the chooser prefer masks too small."""
        at_hog = VIEW.stone_width_at(520.0, 52.0)
        half = segserve.score(box_of(at_hog * 0.5, 0.42, 520.0), GEOM)[0]
        double = segserve.score(box_of(at_hog * 2.0, 0.42, 520.0), GEOM)[0]
        assert half == pytest.approx(double, rel=1e-6)

    def test_the_same_stone_scores_well_at_any_row(self):
        """A stone further down the sheet is wider in pixels and must not be
        penalised for it -- that is the whole point of using the solve."""
        for row in (450.0, 500.0, 560.0, 620.0):
            w = VIEW.stone_width_at(row, 52.0)
            s, ratio, _a = segserve.score(box_of(w, 0.42, row), GEOM)
            assert ratio == pytest.approx(1.0, abs=0.05), row
            assert s < 0.06, row

    def test_a_row_off_the_top_of_the_map_is_refused_not_divided_by_zero(self):
        assert segserve.score((0, 0, 40, 2), GEOM)[0] == math.inf


class TestHandler:
    def test_a_bad_request_does_not_kill_the_server(self):
        """A click that raises must come back as JSON, not a dead socket --
        the person is mid-session with unsaved work in the browser."""
        class Boom:
            def box_at(self, *a, **k):
                raise RuntimeError("gpu fell over")

        handler = segserve.make_handler(".", Boom(), {})
        assert handler is not None       # constructed without touching CUDA


class TestStalePageGuard:
    """A page rendered before the segmenter wiring falls back to a
    geometry-sized box on every click, which is indistinguishable from
    segmentation working badly. It cost a review session; refuse to serve it.
    """

    def test_a_wired_page_is_accepted(self, tmp_path):
        (tmp_path / "index.html").write_text(
            "<script>fetch('/segment', {method:'POST'})</script>")
        segserve.check_page(tmp_path)          # does not raise

    def test_a_page_without_the_wiring_is_refused(self, tmp_path):
        (tmp_path / "index.html").write_text("<script>placeGeometric()</script>")
        with pytest.raises(segserve.StalePage, match="re-render|Re-render"):
            segserve.check_page(tmp_path)

    def test_a_missing_page_is_refused(self, tmp_path):
        with pytest.raises(segserve.StalePage, match="no index.html"):
            segserve.check_page(tmp_path)

    def test_the_real_template_passes_its_own_guard(self, tmp_path):
        """Pins the two together: if the wiring is ever renamed, this fails
        rather than the guard quietly rejecting every good page."""
        from curling_score.train import boxedit
        boxedit.render([], tmp_path, scope="s")
        segserve.check_page(tmp_path)


class TestSaveEdits:
    """A browser download lands on the browser's machine, which need not be the
    one holding the images: the first real session was labelled from a laptop
    and the file never reached the box with the dataset."""

    def test_it_writes_beside_the_frames(self, tmp_path):
        out = segserve.save_edits(
            tmp_path, {"scope": "ds13:wave1", "reviewed": ["a"],
                       "boxes": {"a": [[0, 0.5, 0.5, 0.06, 0.03]]}})
        assert out["ok"] and out["frames"] == 1 and out["boxes"] == 1
        assert Path(out["path"]).is_file()
        assert Path(out["path"]).parent == tmp_path / "edits"

    def test_each_sitting_gets_its_own_file(self, tmp_path):
        """`labels.merge_edits` exists so a bad sitting can be dropped without
        losing the rest; overwriting one file would throw that away."""
        import time
        a = segserve.save_edits(tmp_path, {"scope": "s", "boxes": {}})
        time.sleep(1.05)
        b = segserve.save_edits(tmp_path, {"scope": "s", "boxes": {}})
        assert a["path"] != b["path"]

    def test_a_hostile_scope_cannot_escape_the_directory(self, tmp_path):
        out = segserve.save_edits(tmp_path, {"scope": "../../etc/passwd",
                                             "boxes": {}})
        assert Path(out["path"]).parent == tmp_path / "edits"

    def test_the_payload_round_trips_through_the_edit_parser(self, tmp_path):
        from curling_score.train import labels
        payload = {"scope": "ds13:wave1", "reviewed": ["a"],
                   "boxes": {"a": [[1, 0.4, 0.5, 0.06, 0.03]]}}
        out = segserve.save_edits(tmp_path, payload)
        e = labels.parse_edits_full(json.loads(Path(out["path"]).read_text()))
        assert e.boxes["a"][0][0] == 1 and e.reviewed == ("a",)


class TestBroomShape:
    ROW = 440.0

    def _expect(self):
        return GEOM["k"] * (self.ROW - GEOM["yh"])

    def test_a_pad_held_along_the_line_is_a_broom_not_a_stone(self):
        """Narrow and nearly square: a stone's width rule rejects it, a broom's
        does not. ~30 px against a stone's ~44 at the tee (Phase 0)."""
        box = box_of(0.27 * self._expect(), 1.1, self.ROW)
        assert segserve.score(box, GEOM)[0] == math.inf
        assert segserve.score(box, GEOM, shape="broom")[0] < math.inf

    def test_a_pad_across_the_line_scores_best_at_its_own_size(self):
        exact = box_of(0.70 * self._expect(), 0.33, self.ROW)
        wide = box_of(1.8 * 0.70 * self._expect(), 0.2, self.ROW)
        assert (segserve.score(exact, GEOM, shape="broom")[0]
                < segserve.score(wide, GEOM, shape="broom")[0])
        assert segserve.score(exact, GEOM, shape="broom")[0] == pytest.approx(0.0, abs=0.02)


    def test_a_pad_seen_end_on_is_still_a_broom(self):
        """Measured on the first real click (VXU9 r 459.97): SAM's tight box on
        a yellow pad pointing at the camera was 14 x 30 px, 0.46 of a broom's
        expected width, standing more than twice as tall as it is wide."""
        box = box_of(0.46 * 0.70 * self._expect(), 2.14, self.ROW)
        assert segserve.score(box, GEOM, shape="broom")[0] < math.inf

class TestSegmentRequest:
    class Seen:
        def box_at(self, stem, x, y, geom, shape="stone"):
            self.call = (stem, x, y, geom, shape)
            return {"ok": True}

    def test_the_armed_class_picks_the_shape(self):
        seg = self.Seen()
        segserve.segment_request(seg, {"stem": "s", "x": 1, "y": 2, "cls": 0},
                                 {"s": {"k": 1, "yh": 0}}, ("broom",))
        assert seg.call == ("s", 1.0, 2.0, {"k": 1, "yh": 0}, "broom")

    def test_a_stone_page_is_unchanged(self):
        seg = self.Seen()
        segserve.segment_request(seg, {"stem": "s", "x": 1, "y": 2, "cls": 1},
                                 {}, ("stone", "stone"))
        assert seg.call[-1] == "stone"

    def test_a_class_the_page_does_not_have_falls_back_to_stone(self):
        seg = self.Seen()
        segserve.segment_request(seg, {"stem": "s", "x": 1, "y": 2, "cls": 7},
                                 {}, ("broom",))
        assert seg.call[-1] == "stone"
