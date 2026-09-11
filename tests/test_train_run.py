import pytest

from curling_score.train.run import SETTINGS, Run, check_base


def run(tmp_path, base="yolo11n.pt", name="r"):
    return Run(data=tmp_path / "curling.yaml", project=tmp_path / "runs",
               name=name, base=base)


class TestBaseMarker:
    def test_a_fresh_run_is_fine(self, tmp_path):
        check_base(run(tmp_path))

    def test_resuming_the_same_base_is_fine(self, tmp_path):
        r = run(tmp_path)
        r.last.parent.mkdir(parents=True)
        r.last.write_bytes(b"ckpt")
        r.marker.write_text("yolo11n.pt\n")
        check_base(r)

    def test_refuses_to_resume_a_different_architecture(self, tmp_path):
        # Resuming reads the architecture from the checkpoint and ignores the
        # base, so a request for yolo11s once silently continued a yolo11n run
        # and the only clue was one line of log.
        r = run(tmp_path, base="yolo11s.pt")
        r.last.parent.mkdir(parents=True)
        r.last.write_bytes(b"ckpt")
        r.marker.write_text("yolo11n.pt\n")
        with pytest.raises(SystemExit) as exc:
            check_base(r)
        assert "yolo11n.pt" in str(exc.value) and "yolo11s.pt" in str(exc.value)

    def test_says_how_to_recover(self, tmp_path):
        r = run(tmp_path, base="yolo11s.pt")
        r.last.parent.mkdir(parents=True)
        r.last.write_bytes(b"ckpt")
        r.marker.write_text("yolo11n.pt\n")
        with pytest.raises(SystemExit) as exc:
            check_base(r)
        assert "Delete" in str(exc.value)


class TestSettings:
    def test_hue_jitter_stays_tight(self):
        # Colour is the class signal: red against yellow handles.
        assert SETTINGS["hsv_h"] <= 0.01

    def test_the_sheet_is_flipped_sideways_but_never_end_over_end(self):
        assert SETTINGS["fliplr"] > 0 and SETTINGS["flipud"] == 0

    def test_the_recipe_matches_every_run_since_ds2(self):
        # Batch stays at 16 though the 3070 could take more: these runs exist
        # to compare datasets, and changing the recipe would confound that.
        assert SETTINGS["batch"] == 16
        assert SETTINGS["imgsz"] == 640
        assert SETTINGS["seed"] == 0
