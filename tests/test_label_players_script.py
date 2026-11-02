"""Publishing player items: the pose pre-boxes, the stones read from a YOLO label, the item."""

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


lp = _load("label_players")


class _T:
    """Enough of a torch tensor for ``.cpu().numpy()``."""

    def __init__(self, a):
        self.a = np.asarray(a, dtype=float)

    def cpu(self):
        return self

    def numpy(self):
        return self.a


class StubPose:
    """A pose model that finds ``dets`` ((x0, y0, x1, y1, conf, cls) rows) and keeps its call."""

    def __init__(self, dets):
        self.dets = dets
        self.calls = []

    def predict(self, img, **kw):
        self.calls.append(kw)
        d = np.array(self.dets, dtype=float).reshape(-1, 6)
        boxes = type("B", (), {"xyxy": _T(d[:, :4]), "conf": _T(d[:, 4]), "cls": _T(d[:, 5])})()
        return [type("R", (), {"boxes": boxes})()]


def frame(w=300, h=500):
    return np.zeros((h, w, 3), np.uint8)


# --- pose_starts ------------------------------------------------------------

def test_a_box_running_off_the_image_is_clipped_to_it():
    got = lp.pose_starts(frame(300, 500), StubPose([(-12.0, 480.0, 90.0, 530.0, 0.8, 0)]))
    assert got == [{"x0": 0.0, "y0": 480.0, "x1": 90.0, "y1": 500.0, "conf": 0.8}]
    got = lp.pose_starts(frame(300, 500), StubPose([(250.0, -5.0, 340.0, 60.0, 0.5, 0)]))
    assert (got[0]["x1"], got[0]["y0"]) == (300.0, 0.0)


def test_a_box_under_three_px_once_clipped_is_dropped():
    # 2 px wide inside the image, and one wholly outside it.
    dets = [(-40.0, 10.0, 2.0, 80.0, 0.9, 0), (298.0, 10.0, 330.0, 80.0, 0.9, 0),
            (-30.0, 10.0, -5.0, 80.0, 0.9, 0), (100.0, 100.0, 140.0, 102.5, 0.9, 0),
            (100.0, 200.0, 103.0, 260.0, 0.9, 0)]
    got = lp.pose_starts(frame(300, 500), StubPose(dets))
    assert [(s["x0"], s["x1"]) for s in got] == [(100.0, 103.0)]


def test_starts_under_the_confidence_floor_are_dropped():
    dets = [(10, 10, 60, 100, 0.24, 0), (110, 10, 160, 100, 0.25, 0), (210, 10, 260, 100, 0.9, 0)]
    got = lp.pose_starts(frame(), StubPose(dets))
    assert [s["conf"] for s in got] == [0.25, 0.9]


def test_only_people_are_boxed_and_the_model_is_asked_the_agreed_way():
    model = StubPose([(10, 10, 60, 100, 0.9, 0), (110, 10, 160, 100, 0.9, 2)])
    got = lp.pose_starts(frame(), model)
    assert len(got) == 1
    kw = model.calls[0]
    assert kw["imgsz"] == 640 and kw["device"] == "cpu" and kw["conf"] == 0.25


def test_no_detection_is_no_starts():
    assert lp.pose_starts(frame(), StubPose([])) == []


# --- stones_from_label ------------------------------------------------------

def test_a_label_line_becomes_a_pixel_box_with_its_colour():
    got = lp.stones_from_label("0 0.5 0.25 0.1 0.2\n1 0.1 0.9 0.2 0.1\n", 300, 500)
    assert got == [{"x0": 135.0, "y0": 75.0, "x1": 165.0, "y1": 175.0, "color": "red"},
                   {"x0": 0.0, "y0": 425.0, "x1": 60.0, "y1": 475.0, "color": "yellow"}]


def test_other_classes_blank_and_short_lines_are_ignored():
    txt = "2 0.5 0.5 0.1 0.1\n\n0 0.5 0.5\n  \n0 0.5 0.5 0.2 0.2 extra\n"
    assert [s["color"] for s in lp.stones_from_label(txt, 100, 100)] == ["red"]


# --- item_for ---------------------------------------------------------------

def make_item(**kw):
    args = dict(dataset="overhead-3", fold="all", stem="hOKZ_b_000343_80", w=300, h=516,
                starts=[{"x0": 1.0, "y0": 2.0, "x1": 30.0, "y1": 90.0, "conf": 0.7}],
                stones=[{"x0": 5.0, "y0": 5.0, "x1": 20.0, "y1": 20.0, "color": "red"}],
                batch="players-1", extra_origin={})
    args.update(kw)
    return lp.item_for(**args)


def test_the_item_has_the_payload_the_server_and_page_expect():
    item = make_item(extra_origin={"video": "hOKZ", "t": 343.8, "rect": [0, 0, 300, 516]})
    assert item["kind"] == "player" and item["batch"] == "players-1"
    p = item["payload"]
    assert (p["w"], p["h"]) == (300, 516)
    assert p["starts"][0]["conf"] == 0.7 and p["stones"][0]["color"] == "red"
    assert "image" not in p
    assert item["payload"]["origin"] == {"dataset": "overhead-3", "fold": "all",
                                         "stem": "hOKZ_b_000343_80", "video": "hOKZ",
                                         "t": 343.8, "rect": [0, 0, 300, 516]}


def test_the_id_is_stable_and_says_nothing_about_the_frame():
    a, b = make_item(), make_item(starts=[])
    assert a["id"] == b["id"]
    assert a["id"].startswith("p_") and "hOKZ" not in a["id"] and "000343" not in a["id"]
    assert make_item(stem="other")["id"] != a["id"]
    assert make_item(fold="f2")["id"] != a["id"]
    pub = _load("label_publish")
    assert a["id"] == pub.opaque_id("p_", "overhead-3/all/hOKZ_b_000343_80")


# --- main -------------------------------------------------------------------

def _dataset(tmp_path, names=("a", "b")):
    import cv2
    images, labels = tmp_path / "images", tmp_path / "labels"
    images.mkdir()
    labels.mkdir()
    for n in names:
        cv2.imwrite(str(images / f"{n}.jpg"), np.full((200, 100, 3), 200, np.uint8))
    (labels / "a.txt").write_text("0 0.5 0.5 0.2 0.1\n2 0.1 0.1 0.1 0.1\n")
    return images, labels


def test_dry_run_writes_items_and_a_contact_sheet_and_sends_nothing(tmp_path, monkeypatch):
    images, labels = _dataset(tmp_path)
    out = tmp_path / "out"
    model = StubPose([(10, 20, 50, 190, 0.9, 0)])
    monkeypatch.setattr(lp, "load_model", lambda path: model)
    monkeypatch.setattr(lp.label_publish, "publish",
                        lambda *a, **k: pytest.fail("published in a dry run"))
    monkeypatch.setattr(lp.label_publish, "preflight",
                        lambda *a, **k: pytest.fail("a dry run needs no token"))
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    rc = lp.main(["publish", "--images", str(images), "--labels", str(labels), "--dataset", "d",
                  "--fold", "f", "--batch", "pl", "--weights", "w.pt", "--dry-run",
                  "--out", str(out)])
    assert rc == 0
    files = sorted(out.glob("p_*.json"))
    assert len(files) == 2
    items = [json.loads(f.read_text()) for f in files]
    by_stem = {i["payload"]["origin"]["stem"]: i for i in items}
    assert by_stem["a"]["payload"]["stones"] == [
        {"x0": 40.0, "y0": 90.0, "x1": 60.0, "y1": 110.0, "color": "red"}]
    assert by_stem["b"]["payload"]["stones"] == []      # no label file: no stones
    assert by_stem["a"]["payload"]["starts"][0]["x1"] == 50.0
    assert (out / "contact.jpg").stat().st_size > 0


def test_a_frame_that_cannot_be_read_is_skipped_and_the_rest_go(tmp_path, monkeypatch, capsys):
    images, labels = _dataset(tmp_path)
    (images / "bad.jpg").write_bytes(b"not a jpeg")
    monkeypatch.setattr(lp, "load_model", lambda path: StubPose([]))
    out = tmp_path / "out"
    rc = lp.main(["publish", "--images", str(images), "--labels", str(labels), "--dataset", "d",
                  "--fold", "f", "--batch", "pl", "--weights", "w.pt", "--dry-run",
                  "--out", str(out)])
    assert rc == 0
    assert len(list(out.glob("p_*.json"))) == 2
    assert "bad" in capsys.readouterr().err


def test_a_real_run_needs_the_admin_token(tmp_path, monkeypatch, capsys):
    images, labels = _dataset(tmp_path)
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    monkeypatch.setattr(lp, "load_model", lambda path: pytest.fail("loaded before the token"))
    rc = lp.main(["publish", "--images", str(images), "--labels", str(labels), "--dataset", "d",
                  "--fold", "f", "--batch", "pl", "--weights", "w.pt"])
    assert rc == 2 and "ADMIN_TOKEN" in capsys.readouterr().err


def test_a_refused_token_ends_the_run_before_the_model_loads(tmp_path, monkeypatch, capsys):
    images, labels = _dataset(tmp_path)
    monkeypatch.setenv("ADMIN_TOKEN", "tok")
    monkeypatch.setattr(lp.label_publish, "preflight", lambda base, token: "the admin token was refused")
    monkeypatch.setattr(lp, "load_model", lambda path: pytest.fail("loaded after a refused token"))
    rc = lp.main(["publish", "--images", str(images), "--labels", str(labels), "--dataset", "d",
                  "--fold", "f", "--batch", "pl", "--weights", "w.pt"])
    assert rc == 1 and "refused" in capsys.readouterr().err


def test_a_real_run_publishes_the_items_with_their_image_bytes(tmp_path, monkeypatch, capsys):
    images, labels = _dataset(tmp_path)
    monkeypatch.setenv("ADMIN_TOKEN", "tok-secret")
    monkeypatch.setenv("BASE_URL", "https://example.test/")
    monkeypatch.setattr(lp.label_publish, "preflight", lambda base, token: None)
    monkeypatch.setattr(lp, "load_model", lambda path: StubPose([(10, 20, 50, 190, 0.9, 0)]))
    sent = {}

    def fake_publish(base, token, items, imgs):
        sent.update(base=base, token=token, items=items, imgs=imgs)
        return {"opened": [i["id"] for i in items], "skipped": [], "missing_image": []}

    monkeypatch.setattr(lp.label_publish, "publish", fake_publish)
    rc = lp.main(["publish", "--images", str(images), "--labels", str(labels), "--dataset", "d",
                  "--fold", "f", "--batch", "pl", "--weights", "w.pt"])
    assert rc == 0
    assert sent["base"] == "https://example.test" and sent["token"] == "tok-secret"
    assert len(sent["items"]) == 2 and set(sent["imgs"]) == {i["id"] for i in sent["items"]}
    assert all(i["kind"] == "player" and i["batch"] == "pl" for i in sent["items"])
    a = next(i for i in sent["items"] if i["payload"]["origin"]["stem"] == "a")
    assert sent["imgs"][a["id"]] == (images / "a.jpg").read_bytes()
    assert "tok-secret" not in capsys.readouterr().out
