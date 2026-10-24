"""`curling-score cameras-calibrate`: a sheet's saved calibration, made from its cameras."""

import dataclasses
from pathlib import Path

import pytest

from curling_score import cli
from curling_score.cameras import calstore
from curling_score.cameras import recorder as cam_recorder
from curling_score.geometry.calibrate import CalibrationError
from curling_score.geometry.sideview import SideView
from curling_score.live import session
from curling_score.live.session import Calibration, LiveError
from tests.test_cameras_calstore import calibration


def complete(**kw) -> Calibration:
    """A calibration with both hog lines and both side views calibrated across."""
    cal = calibration()
    setups = {n: dataclasses.replace(s, hog_line="paint") for n, s in cal.setups.items()}
    views = {n: SideView(rect=(0, 0, 100, 100), tee_row=430.0 + i, hog_row=520.0 + i,
                         centre_col=390.0, lat_px_per_m_at_tee=148.0)
             for i, n in enumerate(("left", "right"))}
    return dataclasses.replace(cal, setups=setups, sideviews=views, **kw)


class World:
    """The recorder and the calibration pipeline, faked: what they were asked,
    and what is left on disk when the command is done."""

    def __init__(self, monkeypatch, tmp_path, cal=None, fails=None):
        self.root, self.cal, self.fails = tmp_path / "data", cal, fails
        self.recorders, self.calibrated = [], []
        world = self

        class Recorder:
            def __init__(self, sheet, directory, **kw):
                self.sheet, self.dir = sheet, Path(directory)
                self.path = self.dir / "rec.0.ts"
                self.head, self.checks, self.stopped = 0.0, 0, False
                world.recorders.append(self)

            def start(self):
                if world.fails == "start":
                    raise LiveError("camera 3A-HOUSE sent no H.264 1920x1080 within 15 s")
                self.path.write_bytes(b"ts")              # what a recording leaves
                return self

            def check(self):
                self.checks += 1
                self.head += 20.0
                if world.fails == "check":
                    raise LiveError("sheet 3's composer stalled")

            def head_s(self):
                return self.head

            def ended(self):
                return False

            def stop(self):
                self.stopped = True

        class Pipeline:
            def __init__(self, **kw):
                pass

            def calibrate(self, path, until_s):
                world.calibrated.append((Path(path).name, until_s, Path(path).exists()))
                if world.fails == "calibrate":
                    raise CalibrationError("panels not found")
                return world.cal

        monkeypatch.setattr(cam_recorder, "CameraRecorder", Recorder)
        monkeypatch.setattr(session, "VideoPipeline", Pipeline)
        monkeypatch.setattr(cli, "CAL_POLL_S", 0.0)

    def run(self, *extra, sheet=3, seconds=60):
        return cli.main(["cameras-calibrate", "--sheet", str(sheet), "--seconds", str(seconds),
                         "--root", str(self.root), *extra])

    def left_on_disk(self):
        """Everything under the recording scratch directory."""
        scratch = self.root / "cameras" / "recording"
        return sorted(scratch.rglob("*")) if scratch.exists() else []


def test_a_complete_calibration_is_saved_from_a_recording_that_is_then_deleted(
        monkeypatch, tmp_path, capfd):
    cal = complete()
    world = World(monkeypatch, tmp_path, cal)
    assert world.run() == 0
    (rec,) = world.recorders
    assert rec.sheet == 3 and rec.stopped
    # It was recorded for the seconds asked, and the file was read after the stop.
    assert rec.head >= 60.0 and rec.checks == 3
    assert world.calibrated == [("rec.0.ts", rec.head, True)]
    assert calstore.CalStore(world.root).load(3) == cal
    assert world.left_on_disk() == []
    out = capfd.readouterr().out
    top = cal.setups["top"].calib
    assert f"{top.px_per_m:.1f} px/m (residual {top.residual_m * 100:.2f} cm)" in out
    assert "tee row 430.0" in out and "hog row 521.0" in out


def test_an_incomplete_calibration_is_not_saved_and_the_old_one_stays(
        monkeypatch, tmp_path, capfd):
    old = complete()
    calstore.CalStore(tmp_path / "data").save(3, old)
    world = World(monkeypatch, tmp_path, calibration())      # no hog lines, no side views
    assert world.run() == 1
    assert calstore.CalStore(world.root).load(3) == old
    assert world.left_on_disk() == []
    assert "not saved" in capfd.readouterr().out


@pytest.mark.parametrize("fails", ["start", "check", "calibrate"])
def test_a_failure_is_reported_and_leaves_no_recording_and_no_calibration(
        monkeypatch, tmp_path, capfd, fails):
    world = World(monkeypatch, tmp_path, complete(), fails=fails)
    assert world.run() == 1
    assert world.recorders[0].stopped
    assert calstore.CalStore(world.root).load(3) is None
    assert world.left_on_disk() == []
    assert "cameras-calibrate:" in capfd.readouterr().err


def test_the_recording_is_deleted_whatever_goes_wrong(monkeypatch, tmp_path):
    world = World(monkeypatch, tmp_path, complete())

    def broken(self, path, until_s):
        raise RuntimeError("something nobody planned for")

    monkeypatch.setattr(session.VideoPipeline, "calibrate", broken)
    with pytest.raises(RuntimeError, match="nobody planned"):
        world.run()
    assert world.recorders[0].stopped and world.left_on_disk() == []


def test_only_a_sheet_the_club_has_can_be_asked_for(monkeypatch, tmp_path):
    world = World(monkeypatch, tmp_path, complete())
    with pytest.raises(SystemExit):
        world.run(sheet=6)
    assert world.recorders == []
