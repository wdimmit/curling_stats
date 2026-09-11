"""The review round-trip, at the level a person actually runs it."""
import json

import numpy as np
import pytest

from curling_score import cli


@pytest.fixture
def dataset(tmp_path):
    import cv2

    root = tmp_path / "ds"
    (root / "images" / "train").mkdir(parents=True)
    (root / "labels" / "train").mkdir(parents=True)
    for i in range(3):
        stem = f"v_t_{i:06d}_00"
        cv2.imwrite(str(root / "images" / "train" / f"{stem}.jpg"),
                    np.zeros((40, 40, 3), np.uint8))
        (root / "labels" / "train" / f"{stem}.txt").write_text(
            "0 0.500000 0.500000 0.070000 0.040000\n")
    return root


def session(path, **kw):
    path.write_text(json.dumps({"scope": "ds:train", "reject": [], "add": [],
                                "reviewed": [], **kw}))
    return str(path)


class TestApply:
    def test_several_sessions_merge(self, tmp_path, dataset):
        # Review happens over sittings, each exporting its own file, so the
        # documented form is a shell glob over a directory of them.
        a = session(tmp_path / "a.json", reject=["v_t_000000_00|0.500,0.500"],
                    reviewed=["v_t_000000_00"])
        b = session(tmp_path / "b.json", add=["v_t_000001_00|0.300,0.400|1"],
                    reviewed=["v_t_000001_00"])
        assert cli.main(["labels", str(dataset), "--split", "train",
                         "--apply", a, b, "--keep-empty"]) == 0
        assert (dataset / "labels" / "train" / "v_t_000001_00.txt").read_text() \
            .endswith("1 0.300000 0.400000 0.070000 0.040000\n")

    def test_a_frame_stripped_to_nothing_survives_as_a_negative(self, tmp_path, dataset):
        a = session(tmp_path / "a.json", reject=["v_t_000000_00|0.500,0.500"],
                    reviewed=["v_t_000000_00"])
        cli.main(["labels", str(dataset), "--split", "train", "--apply", a,
                  "--keep-empty"])
        assert (dataset / "labels" / "train" / "v_t_000000_00.txt").read_text() == ""
        assert (dataset / "images" / "train" / "v_t_000000_00.jpg").exists()

    def test_unreviewed_frames_are_dropped(self, tmp_path, dataset):
        # An unreviewed frame still carries the detector's opinion. Letting it
        # through silently is what would make the exercise circular again.
        a = session(tmp_path / "a.json", reviewed=["v_t_000000_00"])
        cli.main(["labels", str(dataset), "--split", "train", "--apply", a,
                  "--keep-empty", "--drop-unreviewed"])
        left = sorted(p.stem for p in (dataset / "images" / "train").glob("*.jpg"))
        assert left == ["v_t_000000_00"]

    def test_refuses_edits_exported_for_another_split(self, tmp_path, dataset):
        # A session on the validation split once leaked 110 edits into an
        # export meant for training.
        a = session(tmp_path / "a.json", reject=["v_t_000000_00|0.500,0.500"])
        (tmp_path / "a.json").write_text(
            json.dumps({"scope": "ds:val", "reject": ["v_t_000000_00|0.500,0.500"]}))
        assert cli.main(["labels", str(dataset), "--split", "train", "--apply", a,
                         "--scope", "ds:train"]) == 2
        assert (dataset / "labels" / "train" / "v_t_000000_00.txt").read_text() != ""
