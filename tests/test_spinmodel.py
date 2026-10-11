"""The handle-point model's wrapper: what it hands back per crop."""
import numpy as np

from curling_score.detect import spinmodel


class _Arr:
    def __init__(self, a):
        self.a = np.asarray(a, float)

    def cpu(self):
        return self

    def numpy(self):
        return self.a


class _Res:
    def __init__(self, found):
        if found is None:
            self.boxes, self.keypoints = [], None
            return
        boxes_conf, xy, kconf = found
        self.boxes = type("B", (), {"conf": _Arr(boxes_conf), "__len__": lambda s: len(boxes_conf)})()
        self.keypoints = type("K", (), {"xy": _Arr(xy), "conf": _Arr(kconf)})()


class FakePose:
    def __init__(self, results):
        self.results, self.seen = results, []

    def predict(self, images, imgsz, conf, verbose):
        self.seen += [im.copy() for im in images]
        out, self.results = self.results[:len(images)], self.results[len(images):]
        return [_Res(r) for r in out]


def test_points_come_back_per_crop_with_the_weaker_confidence():
    pose = FakePose([([0.9], [[[10.0, 20.0], [30.0, 15.0]]], [[0.95, 0.6]]), None])
    got = spinmodel.handle_points(pose, [np.zeros((40, 60, 3), np.uint8)] * 2)
    assert got[0] == ((10.0, 20.0), (30.0, 15.0), 0.6)
    assert got[1] is None


def test_the_most_confident_stone_is_the_one_read():
    pose = FakePose([([0.4, 0.9], [[[1, 1], [2, 2]], [[10, 20], [30, 15]]], [[0.9, 0.9], [0.8, 0.7]])])
    assert spinmodel.handle_points(pose, [np.zeros((40, 60, 3), np.uint8)])[0][0] == (10.0, 20.0)


def test_crops_go_to_the_model_as_bgr_like_the_training_pngs():
    pose = FakePose([None])
    rgb = np.zeros((4, 4, 3), np.uint8); rgb[..., 0] = 200      # red in RGB
    spinmodel.handle_points(pose, [rgb])
    assert pose.seen[0][0, 0].tolist() == [0, 0, 200]
