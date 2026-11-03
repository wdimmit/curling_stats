"""Scoring a player detector: greedy IoU matching, and the plots' inputs."""

import importlib.util
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pe = _load("player_eval")


def test_a_perfect_match():
    truth = [(10, 10, 50, 90), (100, 20, 140, 100)]
    pred = [(10, 10, 50, 90, 0.9), (100, 20, 140, 100, 0.8)]
    assert pe.match(pred, truth) == (2, 0, 0)


def test_a_duplicate_prediction_is_a_false_positive():
    truth = [(10, 10, 50, 90)]
    pred = [(10, 10, 50, 90, 0.9), (11, 10, 51, 90, 0.8)]
    assert pe.match(pred, truth) == (1, 1, 0)


def test_a_miss_is_a_false_negative():
    truth = [(10, 10, 50, 90), (100, 20, 140, 100)]
    pred = [(10, 10, 50, 90, 0.9)]
    assert pe.match(pred, truth) == (1, 0, 1)


def test_iou_just_under_the_threshold_does_not_match():
    # a 100x100 truth and a 100x100 prediction shifted 34 px: 66/134 = 0.4925
    truth = [(0, 0, 100, 100)]
    assert pe.match([(34, 0, 134, 100, 0.9)], truth) == (0, 1, 1)
    # shifted 33: 67/133 = 0.5038 matches
    assert pe.match([(33, 0, 133, 100, 0.9)], truth) == (1, 0, 0)


def test_the_more_confident_prediction_claims_a_truth_first():
    truth = [(0, 0, 100, 100), (50, 0, 150, 100)]
    a = (20, 0, 120, 100, 0.9)        # IoU .667 with the first truth, .538 with the second
    b = (0, 0, 100, 100, 0.3)         # the first truth exactly; the second only .333
    # a goes first whatever the list order, takes the first truth, and b has nothing left
    assert pe.match([a, b], truth) == (1, 1, 1)
    assert pe.match([b, a], truth) == (1, 1, 1)


def test_no_predictions_and_no_truth():
    assert pe.match([], []) == (0, 0, 0)
    assert pe.match([], [(0, 0, 10, 10)]) == (0, 0, 1)
    assert pe.match([(0, 0, 10, 10, 0.5)], []) == (0, 1, 0)


def test_boxes_without_a_confidence_still_match():
    assert pe.match([(0, 0, 10, 10)], [(0, 0, 10, 10)]) == (1, 0, 0)


def test_truth_players_keeps_class_2_in_pixels():
    label = "0 0.5 0.5 0.1 0.1\n2 0.5 0.5 0.5 0.5\n1 0.1 0.1 0.1 0.1\n2 0.25 0.5 0.5 1.0\n"
    assert pe.truth_players(label, 200, 100) == [(50.0, 25.0, 150.0, 75.0),
                                                 (0.0, 0.0, 100.0, 100.0)]
    assert pe.truth_players("", 200, 100) == []


def test_precision_and_recall():
    assert pe.precision_recall(8, 2, 8) == (0.8, 0.5)
    assert pe.precision_recall(0, 0, 0) == (0.0, 0.0)


class FakeModel:
    """Predicts the same boxes on every frame; the real call's shape."""

    def __init__(self, boxes, shape=(100, 200)):
        self.boxes, self.shape = boxes, shape
        self.calls, self.sizes = [], []

    def predict(self, path, conf, classes, imgsz, verbose):
        import numpy as np
        self.calls.append((conf, classes))
        self.sizes.append(imgsz)
        keep = [b for b in self.boxes if b[4] >= conf]

        class T:
            def __init__(self, a):
                self.a = np.array(a, dtype=float)

            def cpu(self):
                return self

            def numpy(self):
                return self.a

        boxes = type("B", (), {"xyxy": T(np.array([b[:4] for b in keep]).reshape(-1, 4)),
                               "conf": T([b[4] for b in keep])})()
        return [type("R", (), {"boxes": boxes, "orig_shape": self.shape})()]


def _val(tmp_path, frames: dict[str, str]):
    for name, label in frames.items():
        (tmp_path / "images/val").mkdir(parents=True, exist_ok=True)
        (tmp_path / "images/val" / f"{name}.jpg").write_bytes(b"x")
        if label is not None:
            (tmp_path / "labels/val").mkdir(parents=True, exist_ok=True)
            (tmp_path / "labels/val" / f"{name}.txt").write_text(label)
    return tmp_path


def test_evaluate_sums_over_frames_and_scores_each_confidence_from_one_pass(tmp_path):
    val = _val(tmp_path, {"a": "2 0.5 0.5 0.5 0.5\n", "b": "", "c": None})
    # truth on a is (50, 25, 150, 75); the model has it at conf 0.6 and a stray at 0.2
    model = FakeModel([(50, 25, 150, 75, 0.6), (0, 0, 20, 20, 0.2)])
    got = pe.evaluate(model, val, pe.PLAYER_CLASS, [0.25, 0.1])
    # c has no label and is skipped; at 0.25 only the real box is kept, at 0.1 the stray too
    assert got == {0.25: (1, 1, 0), 0.1: (1, 3, 0)}
    assert all(call == (0.1, [2]) for call in model.calls) and len(model.calls) == 2


def test_read_results_strips_padded_headers(tmp_path):
    csv = tmp_path / "results.csv"
    csv.write_text("                  epoch,         train/box_loss,   metrics/mAP50-95(B)\n"
                   "                      1,                  1.5,                0.2\n"
                   "                      2,                  1.25,               0.3\n")
    assert pe.read_results(csv) == {"epoch": [1.0, 2.0], "train/box_loss": [1.5, 1.25],
                                    "metrics/mAP50-95(B)": [0.2, 0.3]}


def test_main_writes_curves_png_for_each_run(tmp_path, capsys):
    for run in ("pilot", "overhead-3"):
        (tmp_path / run).mkdir()
        (tmp_path / run / "results.csv").write_text(
            "epoch,train/box_loss,train/cls_loss,train/dfl_loss,metrics/mAP50-95(B),"
            "val/box_loss,val/cls_loss,val/dfl_loss\n1,1,2,3,0.1,1,2,3\n2,.9,1.5,2.5,0.2,.9,1.5,2.5\n")
    rc = pe.main(["--curves", str(tmp_path / "pilot/results.csv"),
                  f"v3={tmp_path / 'overhead-3/results.csv'}", "--out", str(tmp_path / "o")])
    assert rc == 0
    assert (tmp_path / "o/curves.png").read_bytes()[:4] == b"\x89PNG"


def test_a_sheet_of_the_models_player_boxes(tmp_path):
    import cv2
    import numpy as np
    frames = tmp_path / "frames"
    frames.mkdir()
    for n in range(3):
        cv2.imwrite(str(frames / f"f{n}.jpg"), np.full((100, 200, 3), 128, np.uint8))
    model = FakeModel([(50, 25, 150, 75, 0.6)])
    assert pe.write_sheet(model, frames, tmp_path / "sheet.jpg", 0.25) == 3
    assert cv2.imread(str(tmp_path / "sheet.jpg")) is not None
    assert all(call == (0.25, [2]) for call in model.calls)


def test_the_model_runs_at_the_pipelines_448_by_default():
    assert pe.IMGSZ == 448
    model = FakeModel([(50, 25, 150, 75, 0.6)])
    pe.predict(model, Path("x.jpg"), pe.PLAYER_CLASS, 0.25)
    assert model.sizes == [448]
    pe.predict(model, Path("x.jpg"), pe.PLAYER_CLASS, 0.25, imgsz=640)
    assert model.sizes == [448, 640]


def test_evaluate_and_the_sheet_pass_imgsz_on(tmp_path):
    import cv2
    import numpy as np
    val = _val(tmp_path / "v", {"a": "2 0.5 0.5 0.5 0.5\n"})
    model = FakeModel([(50, 25, 150, 75, 0.6)])
    pe.evaluate(model, val, pe.PLAYER_CLASS, [0.25])
    pe.evaluate(model, val, pe.PLAYER_CLASS, [0.25], imgsz=512)
    assert model.sizes == [448, 512]
    frames = tmp_path / "frames"
    frames.mkdir()
    cv2.imwrite(str(frames / "f.jpg"), np.full((100, 200, 3), 128, np.uint8))
    sheet = FakeModel([])
    pe.write_sheet(sheet, frames, tmp_path / "s.jpg", 0.25)
    assert sheet.sizes == [448]


def test_main_defaults_to_imgsz_448_and_takes_another(tmp_path, monkeypatch):
    import sys
    import types
    val = _val(tmp_path / "v", {"a": "2 0.5 0.5 0.5 0.5\n"})
    made = []

    def YOLO(path):
        made.append(FakeModel([(50, 25, 150, 75, 0.6)]))
        return made[-1]

    monkeypatch.setitem(sys.modules, "ultralytics", types.SimpleNamespace(YOLO=YOLO))
    assert pe.main(["--val", str(val), "--model", "m.pt", "--pose", "p.pt",
                    "--out", str(tmp_path / "o")]) == 0
    assert set(made[0].sizes) == {448}
    assert set(made[1].sizes) == {640}              # the incumbent at its own size
    made.clear()
    assert pe.main(["--val", str(val), "--model", "m.pt", "--imgsz", "512",
                    "--out", str(tmp_path / "o")]) == 0
    assert set(made[0].sizes) == {512}
