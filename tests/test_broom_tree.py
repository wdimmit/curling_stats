"""scripts/broom/make_tree.py: labelled waves become a YOLO tree."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "broom_tree", Path(__file__).parents[1] / "scripts" / "broom" / "make_tree.py")
tree = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tree)

ITEMS = [{"stem": "a", "image": "images/a.jpg"}, {"stem": "b", "image": "images/b.jpg"},
         {"stem": "c", "image": "images/c.jpg"}]


class TestLabelled:
    def test_a_frame_nobody_touched_is_left_out(self):
        got = tree.labelled(ITEMS, {"a": [[0, .5, .5, .1, .1]], "b": []})
        assert [it["stem"] for it, _ in got] == ["a", "b"]

    def test_a_frame_restated_to_nothing_is_a_negative_not_a_gap(self):
        got = dict((it["stem"], rows) for it, rows in
                   tree.labelled(ITEMS, {"b": []}))
        assert got == {"b": []}


class TestRoleFor:
    def test_both_frames_of_one_shot_go_the_same_way(self):
        a = {"video_id": "v", "end": 3, "shot": 7, "offset": -1.0}
        b = {**a, "offset": -0.3}
        for frac in (0.1, 0.5, 0.9):
            assert tree.role_for(a, "split", frac) == tree.role_for(b, "split", frac)

    def test_a_split_sends_roughly_the_fraction_asked_to_val(self):
        rows = [{"video_id": "v", "end": e, "shot": s} for e in range(1, 9)
                for s in range(1, 17)]
        val = sum(tree.role_for(r, "split", 0.15) == "val" for r in rows)
        assert 8 <= val <= 32                       # 15% of 128 is 19

    def test_an_explicit_role_wins(self):
        r = {"video_id": "v", "end": 1, "shot": 1}
        assert tree.role_for(r, "train", 0.5) == "train"
        assert tree.role_for(r, "val", 0.5) == "val"


class TestWriteTree:
    def test_it_writes_images_labels_and_yaml(self, tmp_path):
        src = tmp_path / "wave"
        (src / "images").mkdir(parents=True)
        (src / "images" / "a.jpg").write_bytes(b"jpg")
        (src / "images" / "b.jpg").write_bytes(b"jpg")
        out = tmp_path / "tree"
        tree.write_tree(out, [(src / "images/a.jpg", "a", [[0, .5, .25, .1, .05]], "train"),
                              (src / "images/b.jpg", "b", [], "val")])
        assert (out / "labels/train/a.txt").read_text().split() == \
            ["0", "0.500000", "0.250000", "0.100000", "0.050000"]
        assert (out / "labels/val/b.txt").read_text() == ""
        assert (out / "images/val/b.jpg").exists()
        y = (out / "data.yaml").read_text()
        assert f"path: {out}" in y and "0: broom_head" in y
