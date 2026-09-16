import json

import pytest

from curling_score.geometry.sideview import SideView
from curling_score.train import boxedit, labels

VIEW = SideView(rect=(0, 0, 810, 1080), tee_row=435.0, hog_row=520.0)


class TestFrameGeometry:
    """The whole perspective model reaches the browser as a slope and an
    intercept, because `stone_width_at` is linear in the row."""

    def test_the_line_reproduces_stone_width_at(self):
        g = boxedit.frame_geometry(VIEW, 52.0)
        for row in (440.0, 500.0, 520.0, 600.0):
            assert g["k"] * (row - g["yh"]) == pytest.approx(
                VIEW.stone_width_at(row, 52.0), rel=1e-9)

    def test_at_the_hog_line_it_gives_the_measured_width(self):
        g = boxedit.frame_geometry(VIEW, 52.0)
        assert g["k"] * (VIEW.hog_row - g["yh"]) == pytest.approx(52.0, abs=0.01)


class TestRender:
    def _items(self):
        return [{"stem": "v1_l_000100_00", "image": "images/v1_l_000100_00.jpg",
                 "width": 810, "height": 1080,
                 "boxes": [[0, 0.5, 0.48, 0.064, 0.02]],
                 "geom": boxedit.frame_geometry(VIEW, 52.0)}]

    def test_it_writes_a_page_carrying_the_frames(self, tmp_path):
        page = boxedit.render(self._items(), tmp_path, scope="ds13:train")
        html = page.read_text()
        assert "v1_l_000100_00" in html
        assert "ds13:train" in html

    def test_the_data_survives_as_valid_json(self, tmp_path):
        """The page embeds the items by substitution, so a quote or a
        backslash in a stem would otherwise break the script silently."""
        page = boxedit.render(self._items(), tmp_path, scope="ds13:train")
        html = page.read_text()
        blob = html.split("const ITEMS = ", 1)[1].split(";\n", 1)[0]
        assert json.loads(blob)[0]["stem"] == "v1_l_000100_00"

    def test_no_placeholder_survives_into_the_page(self, tmp_path):
        page = boxedit.render(self._items(), tmp_path, scope="ds13:train")
        html = page.read_text()
        for token in ("__DATA__", "__SCOPE__", "__TITLE__"):
            assert token not in html


class TestReplacementEdits:
    def _tree(self, tmp_path):
        for sub in ("labels/train", "images/train"):
            (tmp_path / sub).mkdir(parents=True)
        (tmp_path / "labels/train/f1.txt").write_text(
            "0 0.500000 0.480000 0.064000 0.020000\n")
        (tmp_path / "images/train/f1.jpg").write_bytes(b"jpg")
        return tmp_path

    def test_a_restatement_replaces_the_frames_boxes(self, tmp_path):
        root = self._tree(tmp_path)
        counts = labels.apply_edits(
            root, "train", rejects=(),
            boxes={"f1": [[1, 0.400, 0.520, 0.080, 0.034]]})
        assert counts["replaced"] == 1
        got = (root / "labels/train/f1.txt").read_text().split()
        assert got[0] == "1"
        assert float(got[1]) == pytest.approx(0.400)
        assert float(got[3]) == pytest.approx(0.080)

    def test_a_restatement_wins_over_a_rejection_for_the_same_frame(self, tmp_path):
        """The two compose badly: a rejection keyed on the ORIGINAL centre
        would delete a box the restatement had already moved, leaving the
        frame empty when the reviewer had meant to keep one box."""
        root = self._tree(tmp_path)
        counts = labels.apply_edits(
            root, "train", rejects=("f1|0.500,0.480",),
            boxes={"f1": [[0, 0.410, 0.530, 0.080, 0.034]]})
        assert counts["replaced"] == 1
        assert (root / "labels/train/f1.txt").read_text().strip()

    def test_a_restatement_to_nothing_empties_the_frame(self, tmp_path):
        root = self._tree(tmp_path)
        counts = labels.apply_edits(root, "train", rejects=(), boxes={"f1": []})
        assert counts["emptied"] == 1
        assert (root / "labels/train/f1.txt").read_text().strip() == ""

    def test_the_export_round_trips_through_parse(self):
        payload = {"scope": "ds13:train", "reviewed": ["f1"],
                   "boxes": {"f1": [[0, 0.4, 0.5, 0.08, 0.03]]}}
        e = labels.parse_edits_full(payload)
        assert e.boxes == {"f1": [[0, 0.4, 0.5, 0.08, 0.03]]}
        assert e.reviewed == ("f1",)

    def test_a_later_sitting_wins_for_a_frame_both_touched(self):
        """Boxes state a whole frame, so they cannot be unioned like keys --
        the later sitting is the one that saw the earlier one's work."""
        a = {"scope": "s", "boxes": {"f1": [[0, 0.1, 0.1, 0.05, 0.02]]}}
        b = {"scope": "s", "boxes": {"f1": [[1, 0.9, 0.9, 0.06, 0.03]]}}
        assert labels.merge_edits(a, b).boxes["f1"][0][0] == 1

    def test_an_old_export_without_boxes_still_loads(self):
        e = labels.parse_edits_full({"reject": ["f1|0.5,0.5"]})
        assert e.boxes == {}
        assert e.reject == ("f1|0.5,0.5",)


class TestUncertaintyFlagSurvives:
    """SAM's "geometry doubts this" flag rides as a sixth array element.

    As a property on the array (`b.plausible = ...`) it survived in memory and
    vanished the moment `save()` ran `JSON.stringify`, so the dashed border
    silently became solid on reload -- the reviewer would stop being told
    which boxes to look at twice.
    """

    def test_the_flag_is_an_array_element_not_a_property(self):
        src = boxedit._PAGE
        assert "b.plausible =" not in src
        assert "j.plausible ? 0 : 1" in src
        assert 'b[5] ? " iffy" : ""' in src

    def test_the_export_keeps_only_the_five_label_fields(self):
        """A sixth element must not reach the label files: `apply_edits`
        builds a Box from exactly five."""
        src = boxedit._PAGE
        assert "b.map" not in src
        assert "[b[0], +b[1].toFixed(6), +b[2].toFixed(6)," in src
        assert "+b[3].toFixed(6), +b[4].toFixed(6)]" in src
