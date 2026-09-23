"""A setup pickled before panels carried a hog line must not be trusted."""

import pickle
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import replay_end  # noqa: E402
from curling_score.game import profile  # noqa: E402
from tests.synth_hogline import TOP, line_at  # noqa: E402


def _pickle(root, setups):
    (root / "setups-v.hogline.pkl").write_bytes(pickle.dumps((setups, "panels")))


def test_a_pickle_without_hog_lines_is_recomputed(tmp_path, monkeypatch):
    _pickle(tmp_path, {"top": profile.PanelSetup(rect=(0, 0, 1, 1), calib=TOP)})
    fresh = {"top": profile.PanelSetup(rect=(0, 0, 1, 1), calib=TOP, hog_line=line_at(4.44))}
    monkeypatch.setattr(replay_end, "_compute_setups", lambda video: (fresh, "panels"))
    setups, _ = replay_end.setups_for("v.mp4", "v", tmp_path)
    assert setups["top"].hog_line is not None


def test_main_s_setups_pickle_is_never_touched(tmp_path, monkeypatch):
    """main's code cannot unpickle a HogLine, so its setups-<vid>.pkl must
    survive a recompute byte for byte, and must not be read in its place."""
    legacy = tmp_path / "setups-v.pkl"
    legacy.write_bytes(b"main's setups, not a pickle this code should open")
    before = legacy.read_bytes()
    fresh = {"top": profile.PanelSetup(rect=(0, 0, 1, 1), calib=TOP, hog_line=line_at(4.44))}
    monkeypatch.setattr(replay_end, "_compute_setups", lambda video: (fresh, "panels"))
    setups, _ = replay_end.setups_for("v.mp4", "v", tmp_path)
    assert setups["top"].hog_line is not None
    assert legacy.read_bytes() == before
    assert (tmp_path / "setups-v.hogline.pkl").is_file()


def test_a_pickle_with_a_line_or_a_reason_is_kept(tmp_path, monkeypatch):
    kept = {"top": profile.PanelSetup(rect=(0, 0, 1, 1), calib=TOP, hog_line=line_at(4.44)),
            "bottom": profile.PanelSetup(rect=(0, 0, 1, 1), calib=TOP,
                                         hog_line_error="bottom panel: no paint")}
    _pickle(tmp_path, kept)
    monkeypatch.setattr(replay_end, "_compute_setups",
                        lambda video: (_ for _ in ()).throw(AssertionError("recomputed")))
    setups, _ = replay_end.setups_for("v.mp4", "v", tmp_path)
    assert setups["bottom"].hog_line_error == "bottom panel: no paint"
