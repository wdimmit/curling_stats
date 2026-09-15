import argparse
import json

import pytest

from curling_score import cli
from curling_score.harvest import sidestages


def parse(argv):
    """Parse without running: every stage touches the network, disk or GPU."""
    parser = argparse.ArgumentParser(prog="curling-score")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    cli._add_sideframes(sub)
    return parser.parse_args(argv)


def write_pool(tmp_path, videos):
    """A hand-written pool: {video_id: [candidate rows]}, no media anywhere."""
    pool = tmp_path / "pool"
    pool.mkdir()
    rows = {}
    for vid, n in videos.items():
        rows[vid] = [{
            "video_id": vid, "view": "left" if i % 2 else "right",
            "t_abs": 100.0 + i, "clip_start_s": 100.0 + i,
            "position": ("approach", "crossing", "past", "occluded",
                         "clear")[i % 5],
            "outcome": ("ok", "no_candidate", "two_candidates",
                        "never_reached", "unsteady", "wrong_speed")[i % 6],
            "color": "red", "crowding": 0, "edge_row": 500.0, "labels": [],
        } for i in range(n)]
    (pool / "candidates.json").write_text(json.dumps(rows))
    return pool


def write_videos(tmp_path, splits):
    path = tmp_path / "videos.json"
    path.write_text(json.dumps({"videos": [
        {"video_id": v, "split": s, "date": "2025-10-07", "sheet": 1}
        for v, s in splits.items()]}))
    return path


class TestManifest:
    def test_the_split_comes_from_ds11_and_holds_out_whole_videos(self, tmp_path):
        """Every frame of a val video is val; no video appears in both."""
        splits = {"a": "train", "b": "train", "c": "val"}
        args = argparse.Namespace(
            pool=write_pool(tmp_path, {v: 30 for v in splits}),
            videos=write_videos(tmp_path, splits),
            manifest=tmp_path / "manifest.json")
        assert sidestages.stage_select(args) == 0
        doc = json.loads((tmp_path / "manifest.json").read_text())
        assert doc["videos"]["c"]["split"] == "val"
        assert {e["split"] for e in doc["videos"].values()} == {"train", "val"}
        assert doc["summary"]["val_frames"] > 0

    def test_shortfall_travels_with_the_quota(self, tmp_path):
        """A bin the corpus could not fill is a number in the manifest."""
        splits = {"a": "train"}
        args = argparse.Namespace(
            pool=write_pool(tmp_path, {"a": 5}),      # nowhere near 600
            videos=write_videos(tmp_path, splits),
            manifest=tmp_path / "manifest.json")
        assert sidestages.stage_select(args) == 0
        doc = json.loads((tmp_path / "manifest.json").read_text())
        assert doc["summary"]["shortfall"], "600 frames came from 5 candidates"
        assert doc["quota"]

    def test_a_video_that_would_not_calibrate_is_named(self, tmp_path):
        """sideviews.json lists it with its reason; it is not merely absent."""
        from curling_score.harvest import sideviews
        import numpy as np

        blank = [np.full((1080, 1920, 3), 120, np.uint8) for _ in range(4)]
        doc = sideviews.to_json(sideviews.derive("bad", blank))
        assert doc["video_id"] == "bad"
        assert doc["error"]


class TestCli:
    @pytest.mark.parametrize("stage", ["views", "propose", "select", "build"])
    def test_every_stage_is_reachable(self, stage):
        assert parse(["sideframes", stage, "--root", "/tmp/c",
                      "--out", "/tmp/o"]).stage == stage

    def test_the_split_defaults_to_ds11s_own(self):
        args = parse(["sideframes", "select", "--pool", "/tmp/p"])
        assert args.videos == "datasets/ds11/videos.json"

    def test_the_manifest_defaults_into_ds13(self):
        args = parse(["sideframes", "select", "--pool", "/tmp/p"])
        assert args.manifest == "datasets/ds13/manifest.json"
