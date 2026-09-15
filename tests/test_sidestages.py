import argparse
import json

import pytest

from curling_score import cli
from curling_score.geometry.sideview import SideView
from curling_score.harvest import sidepool, sidestages
from curling_score.harvest import sideviews as SV
from tests import synth


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


def _dummy_view():
    """A calibrated-enough ``SideView`` to round-trip through JSON.

    The tests using this only care whether a video counts as "usable", not
    where its paint actually sits, so the numbers are arbitrary.
    """
    from curling_score.geometry.sideview import SideView

    return SideView(rect=(0, 0, 10, 10), tee_row=5.0, hog_row=8.0, d_m=1.0)


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

    def test_the_quota_is_expanded_per_view_and_reconciles_to_the_target(self, tmp_path):
        """``SCENE_QUOTA`` is per view; the manifest must say the real total,
        not the un-expanded 450 that summing its raw keys would give.
        """
        from curling_score.harvest import sideframes

        splits = {"a": "train"}
        args = argparse.Namespace(
            pool=write_pool(tmp_path, {"a": 5}),
            videos=write_videos(tmp_path, splits),
            manifest=tmp_path / "manifest.json")
        assert sidestages.stage_select(args) == 0
        doc = json.loads((tmp_path / "manifest.json").read_text())

        expected = sum(sideframes.SCENE_QUOTA.values()) * 2 \
            + sum(sideframes.OUTCOME_QUOTA.values())
        assert expected == 600
        assert sum(doc["quota"].values()) == expected
        assert doc["summary"]["target"] == expected
        assert "scene:left:approach" in doc["quota"]
        assert "scene:right:approach" in doc["quota"]
        assert "outcome:ok" in doc["quota"]

    def test_supply_counts_the_pool_before_caps_so_a_shortfall_can_be_read(
            self, tmp_path):
        """A shortfall alone cannot say whether the archive had nothing for a
        bin or the run's caps had already spent it; ``supply`` can, because
        it is counted straight off the pool before ``select`` ever runs.
        """
        splits = {"a": "train"}
        args = argparse.Namespace(
            pool=write_pool(tmp_path, {"a": 5}),
            videos=write_videos(tmp_path, splits),
            manifest=tmp_path / "manifest.json")
        assert sidestages.stage_select(args) == 0
        doc = json.loads((tmp_path / "manifest.json").read_text())
        supply = doc["summary"]["supply"]

        # write_pool's 5 rows are one each of every scene position, spread
        # across both views, and four of the six outcome keys ("two_candidates"
        # at i=2 matches no real OUTCOME_QUOTA key and so touches no bin).
        # Reconstruct exactly which bins that puts one candidate in, and
        # confirm supply agrees candidate-for-candidate -- including the
        # bins nothing in this pool could ever match, which must still
        # appear, at zero, since supply is a full accounting, not sparse.
        views = ["right" if not i % 2 else "left" for i in range(5)]
        positions = ["approach", "crossing", "past", "occluded", "clear"]
        outcomes = ["ok", "no_candidate", None, "never_reached", "unsteady"]
        expected = {key: 0 for key in supply}
        for view, position in zip(views, positions):
            expected[f"scene:{view}:{position}"] += 1
        for outcome in outcomes:
            if outcome is not None:
                expected[f"outcome:{outcome}"] += 1

        assert supply == expected
        assert supply["scene:left:approach"] == 0
        assert supply["outcome:bad_speed"] == 0

    def test_a_video_that_would_not_calibrate_is_named(self, tmp_path):
        """sideviews.json lists it with its reason; it is not merely absent."""
        from curling_score.harvest import sideviews
        import numpy as np

        blank = [np.full((1080, 1920, 3), 120, np.uint8) for _ in range(4)]
        doc = sideviews.to_json(sideviews.derive("bad", blank))
        assert doc["video_id"] == "bad"
        assert doc["error"]


class TestStageViewsResilience:
    """1197 clips on a box across the network: one bad one cannot take down
    the other 119, and a restart cannot be made to redo work already done.
    """

    def test_an_unreadable_clip_is_named_and_the_others_still_process(
            self, tmp_path, monkeypatch):
        """A zero-byte .mkv is a real, genuinely-unreadable container -- no
        mock stands in for the failure this guards against. The other video
        is faked past ``sideviews.derive`` because only the guard around a
        raising video is under test here, not calibration itself.
        """
        root = tmp_path / "clips"
        (root / "bad").mkdir(parents=True)
        (root / "bad" / "000.mkv").write_bytes(b"")
        (root / "good").mkdir(parents=True)
        (root / "good" / "000.mkv").write_bytes(b"not a real container either")

        real_one_frame_per_clip = sidestages._one_frame_per_clip

        def fake_one_frame_per_clip(path):
            if path.name == "good":
                return [(0.0, object()), (1.0, object())]
            return real_one_frame_per_clip(path)  # "bad": hits the real file

        monkeypatch.setattr(sidestages, "_one_frame_per_clip", fake_one_frame_per_clip)
        monkeypatch.setattr(
            sidestages.sideviews, "derive",
            lambda vid, frames: sidestages.sideviews.VideoViews(
                vid, (10, 10),
                {"left": sidestages.sideviews.ViewInfo(
                    "left", (0, 0, 10, 10), view=_dummy_view())}))

        views_path = tmp_path / "sideviews.json"
        args = argparse.Namespace(
            root=str(root),
            videos=str(write_videos(tmp_path, {"bad": "train", "good": "train"})),
            views=str(views_path), limit=None, force=False, stage="views")

        assert sidestages.stage_views(args) == 0

        doc = json.loads(views_path.read_text())
        assert set(doc) == {"bad", "good"}
        assert doc["bad"]["error"], "the unreadable video is named, not merely absent"
        assert "InvalidDataError" in doc["bad"]["error"]
        assert doc["good"]["error"] is None

    def test_a_video_already_banked_is_skipped_on_restart(self, tmp_path, monkeypatch):
        root = tmp_path / "clips"
        (root / "a").mkdir(parents=True)
        (root / "a" / "000.mkv").write_bytes(b"placeholder")

        calls = []

        def fake_one_frame_per_clip(path):
            calls.append(path.name)
            return [(0.0, object()), (1.0, object())]

        monkeypatch.setattr(sidestages, "_one_frame_per_clip", fake_one_frame_per_clip)
        monkeypatch.setattr(
            sidestages.sideviews, "derive",
            lambda vid, frames: sidestages.sideviews.VideoViews(
                vid, (10, 10),
                {"left": sidestages.sideviews.ViewInfo(
                    "left", (0, 0, 10, 10), view=_dummy_view())}))

        views_path = tmp_path / "sideviews.json"
        args = argparse.Namespace(
            root=str(root), videos=str(write_videos(tmp_path, {"a": "train"})),
            views=str(views_path), limit=None, force=False, stage="views")

        assert sidestages.stage_views(args) == 0
        assert calls == ["a"]

        assert sidestages.stage_views(args) == 0
        assert calls == ["a"], "a video already banked must not be redone"

        args.force = True
        assert sidestages.stage_views(args) == 0
        assert calls == ["a", "a"], "--force must redo it anyway"


class TestStageProposeResilience:
    """Same contract as ``stage_views``: a video the detector chokes on is
    named, the rest still get proposed, and a restart does not redo them.
    """

    def _views_doc(self, vids):
        from curling_score.harvest import sideviews as SV

        usable = SV.VideoViews(
            "x", (10, 10),
            {"left": SV.ViewInfo("left", (0, 0, 10, 10), view=_dummy_view())})
        return {vid: SV.to_json(usable) for vid in vids}

    def _write(self, tmp_path, root, vids):
        for vid in vids:
            (root / vid).mkdir(parents=True, exist_ok=True)
            (root / vid / "000.mkv").write_bytes(b"placeholder")
        views_path = tmp_path / "sideviews.json"
        views_path.write_text(json.dumps(self._views_doc(vids)))
        return views_path

    def test_a_failing_video_is_named_and_the_others_still_process(
            self, tmp_path, monkeypatch):
        root = tmp_path / "clips"
        out = tmp_path / "pool"
        views_path = self._write(tmp_path, root, ["bad", "good"])

        def fake_build_video_pool(vid, paths, vv, out_dir, fps):
            if vid == "bad":
                raise RuntimeError("simulated decode failure")
            return [], {"refusals": {}, "written": 0}

        monkeypatch.setattr(sidestages.sidepool, "build_video_pool",
                            fake_build_video_pool)

        args = argparse.Namespace(
            root=str(root), out=str(out),
            videos=str(write_videos(tmp_path, {"bad": "train", "good": "train"})),
            views=str(views_path), fps=5.0, limit=None, force=False,
            stage="propose")

        assert sidestages.stage_propose(args) == 0

        errors = json.loads((out / "propose_errors.json").read_text())
        assert "bad" in errors
        assert "simulated decode failure" in errors["bad"]

        pool = json.loads((out / "candidates.json").read_text())
        assert pool["bad"] == []
        assert pool["good"] == []
        assert "good" not in errors

    def test_a_video_already_banked_is_skipped_on_restart(self, tmp_path, monkeypatch):
        root = tmp_path / "clips"
        out = tmp_path / "pool"
        views_path = self._write(tmp_path, root, ["a"])

        calls = []

        def fake_build_video_pool(vid, paths, vv, out_dir, fps):
            calls.append(vid)
            return [], {"refusals": {}, "written": 0}

        monkeypatch.setattr(sidestages.sidepool, "build_video_pool",
                            fake_build_video_pool)

        args = argparse.Namespace(
            root=str(root), out=str(out),
            videos=str(write_videos(tmp_path, {"a": "train"})),
            views=str(views_path), fps=5.0, limit=None, force=False,
            stage="propose")

        assert sidestages.stage_propose(args) == 0
        assert calls == ["a"]

        assert sidestages.stage_propose(args) == 0
        assert calls == ["a"], "a video already banked must not be redone"

        args.force = True
        assert sidestages.stage_propose(args) == 0
        assert calls == ["a", "a"], "--force must redo it anyway"


class TestRequiredArgs:
    def test_views_without_root_fails_cleanly(self):
        args = argparse.Namespace(root=None, stage="views")
        assert sidestages.stage_views(args) != 0

    def test_propose_without_root_or_out_fails_cleanly(self):
        args = argparse.Namespace(root=None, out=None, stage="propose")
        assert sidestages.stage_propose(args) != 0

    def test_select_without_pool_fails_cleanly(self):
        args = argparse.Namespace(pool=None, stage="select")
        assert sidestages.stage_select(args) != 0

    def test_build_without_pool_or_out_fails_cleanly(self):
        args = argparse.Namespace(pool=None, out=None, stage="build")
        assert sidestages.stage_build(args) != 0


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


class TestProposeSelectBuildAgreeOnWhatWasWritten:
    """The C1 regression: `build_video_pool` used to return *every* candidate
    it scanned, written or not, so `stage_select` could choose a frame
    `pick_writes` had discarded and `stage_build` could only report it
    missing -- on the real numbers, about 14 chosen frames in 15. This drives
    `propose`, `select` and `build` for real, faking only the ffmpeg decode
    (`sidepool.scan_clip`), so it exercises the actual `pick_writes` /
    `cv2.imwrite` / pool-accounting path that broke, with real tiny JPEGs on
    disk -- no video, no ffmpeg, no GPU.
    """

    def test_stage_build_reports_zero_missing(self, tmp_path, monkeypatch):
        view = SideView(rect=(0, 0, 810, 1080), tee_row=430.0, hog_row=520.0)
        vv = SV.VideoViews("v1", (810, 1080), {
            "left": SV.ViewInfo("left", (0, 0, 810, 1080), view=view)})

        # Far more moments than MAX_PER_CLIP_VIEW (8) -- the same fixture
        # `TestWriteCap` uses to prove `pick_writes` actually discards most
        # of what a clip offers.
        rows = [440.0 + 2 * i for i in range(120)]

        def fake_scan_clip(video_id, clip_path, views, *, fps=sidepool.DETECT_FPS):
            t0 = 100.0
            moments = []
            for i, row in enumerate(rows):
                plate = synth.side_view()
                img = synth.side_view_stone(plate, row, width_px=52)
                moments.append((t0 + i / fps, {"left": img[..., ::-1]}))  # bgr
            rgb_moments = [(t, {n: c[..., ::-1] for n, c in crops.items()})
                          for t, crops in moments]
            candidates = sidepool.scan_moments(video_id, rgb_moments, views,
                                               clip_start_s=t0)
            return candidates, moments

        monkeypatch.setattr(sidepool, "scan_clip", fake_scan_clip)

        root = tmp_path / "clips"
        (root / "v1").mkdir(parents=True)
        (root / "v1" / "000.mkv").write_bytes(b"placeholder")
        out = tmp_path / "pool"
        views_path = tmp_path / "sideviews.json"
        views_path.write_text(json.dumps({"v1": SV.to_json(vv)}))
        videos_path = write_videos(tmp_path, {"v1": "train"})

        propose_args = argparse.Namespace(
            root=str(root), out=str(out), videos=str(videos_path),
            views=str(views_path), fps=5.0, limit=None, force=False,
            stage="propose")
        assert sidestages.stage_propose(propose_args) == 0

        pool_doc = json.loads((out / "candidates.json").read_text())
        written = list((out / "v1").glob("*.jpg"))
        # The bug: `build_video_pool` banked every moment scanned (120 of
        # them, one view), not just the ones a JPEG exists for.
        assert len(pool_doc["v1"]) == len(written), \
            "the banked pool must be exactly what got written, not everything scanned"
        assert 0 < len(written) <= sidepool.MAX_PER_CLIP_VIEW

        manifest_path = tmp_path / "manifest.json"
        select_args = argparse.Namespace(
            pool=str(out), videos=str(videos_path), manifest=str(manifest_path))
        assert sidestages.stage_select(select_args) == 0

        build_args = argparse.Namespace(
            pool=str(out), out=str(tmp_path / "yolo"), manifest=str(manifest_path))
        assert sidestages.stage_build(build_args) == 0

        manifest = json.loads(manifest_path.read_text())
        assert manifest["summary"]["frames"] > 0, \
            "the pool must have offered select() something to choose"
