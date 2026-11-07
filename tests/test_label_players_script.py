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
    dets = [(10, 10, 60, 100, 0.09, 0), (110, 10, 160, 100, 0.1, 0), (210, 10, 260, 100, 0.9, 0)]
    got = lp.pose_starts(frame(), StubPose(dets))
    assert [s["conf"] for s in got] == [0.1, 0.9]


def test_only_people_are_boxed_and_the_model_is_asked_the_agreed_way():
    model = StubPose([(10, 10, 60, 100, 0.9, 0), (110, 10, 160, 100, 0.9, 2)])
    got = lp.pose_starts(frame(), model)
    assert len(got) == 1
    kw = model.calls[0]
    assert kw["imgsz"] == 640 and kw["device"] == "cpu" and kw["conf"] == 0.1


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


def test_a_malformed_line_is_ignored_and_the_rest_of_the_label_is_kept():
    txt = "0 abc 0.5 0.1 0.1\n1 0.5 0.5 nan 0.1\n1 0.5 0.5 0.2 0.2\n"
    assert [s["color"] for s in lp.stones_from_label(txt, 100, 100)] == ["yellow"]


def test_stones_are_clipped_to_the_image_and_one_wholly_outside_is_dropped():
    txt = "0 0.0 0.5 0.2 0.2\n1 1.05 0.5 0.2 0.2\n0 1.5 0.5 0.1 0.1\n"
    got = lp.stones_from_label(txt, 100, 200)
    assert got == [{"x0": 0.0, "y0": 80.0, "x1": 10.0, "y1": 120.0, "color": "red"},
                   {"x0": 95.0, "y0": 80.0, "x1": 100.0, "y1": 120.0, "color": "yellow"}]


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


def test_only_the_contact_sheets_frames_are_kept_decoded(tmp_path, monkeypatch):
    images, labels = _dataset(tmp_path, names=("a", "b", "c", "d"))
    monkeypatch.setattr(lp, "load_model", lambda path: StubPose([]))
    monkeypatch.setattr(lp, "CONTACT_FRAMES", 2)
    kept = []
    real = lp.contact_sheet
    monkeypatch.setattr(lp, "contact_sheet", lambda frames: kept.append(len(frames)) or real(frames))
    out = tmp_path / "out"
    rc = lp.main(["publish", "--images", str(images), "--labels", str(labels), "--dataset", "d",
                  "--fold", "f", "--batch", "pl", "--weights", "w.pt", "--dry-run",
                  "--out", str(out)])
    assert rc == 0 and kept == [2]
    assert len(list(out.glob("p_*.json"))) == 4


def test_publishing_goes_a_page_at_a_time_with_only_that_pages_images(tmp_path, monkeypatch, capsys):
    images, labels = _dataset(tmp_path, names=("a", "b", "c"))
    monkeypatch.setenv("ADMIN_TOKEN", "tok")
    monkeypatch.setattr(lp.label_publish, "preflight", lambda base, token: None)
    monkeypatch.setattr(lp.label_publish, "PAGE", 2)
    monkeypatch.setattr(lp, "load_model", lambda path: StubPose([]))
    calls = []

    def fake_publish(base, token, items, imgs):
        calls.append((len(items), set(imgs) == {i["id"] for i in items}))
        return {"opened": [i["id"] for i in items], "skipped": [], "missing_image": []}

    monkeypatch.setattr(lp.label_publish, "publish", fake_publish)
    rc = lp.main(["publish", "--images", str(images), "--labels", str(labels), "--dataset", "d",
                  "--fold", "f", "--batch", "pl", "--weights", "w.pt"])
    assert rc == 0
    assert calls == [(2, True), (1, True)]
    assert "opened 3," in capsys.readouterr().out


# --- val frames, threads, picks ----------------------------------------------

def _dry(images, labels, out, *extra, fold="f"):
    return lp.main(["publish", "--images", str(images), "--labels", str(labels), "--dataset", "d",
                    "--fold", fold, "--batch", "pl", "--weights", "w.pt", "--dry-run",
                    "--out", str(out), *extra])


def _train_and_val(tmp_path):
    import cv2
    images, labels = tmp_path / "images", tmp_path / "labels"
    for sub, n in (("train", "a"), ("val", "b")):
        (images / sub).mkdir(parents=True)
        (labels / sub).mkdir(parents=True)
        cv2.imwrite(str(images / sub / f"{n}.jpg"), np.full((200, 100, 3), 200, np.uint8))
    return images, labels


def test_a_val_frame_is_refused_unless_the_fold_is_val(tmp_path, monkeypatch, capsys):
    images, labels = _train_and_val(tmp_path)
    monkeypatch.setattr(lp, "load_model", lambda path: pytest.fail("loaded with val frames in an all fold"))
    rc = _dry(images, labels, tmp_path / "out", fold="all")
    err = capsys.readouterr().err
    assert rc == 2 and "val" in err and "b.jpg" in err and "--fold" in err
    assert not (tmp_path / "out").exists()


def test_the_train_folder_alone_is_fine_for_fold_all(tmp_path, monkeypatch):
    images, labels = _train_and_val(tmp_path)
    monkeypatch.setattr(lp, "load_model", lambda path: StubPose([]))
    out = tmp_path / "out"
    assert _dry(images / "train", labels / "train", out, fold="all") == 0
    assert [json.loads(f.read_text())["payload"]["origin"]["stem"]
            for f in out.glob("p_*.json")] == ["a"]


def test_fold_val_takes_val_frames_and_a_val_images_folder_is_not_refused(tmp_path, monkeypatch):
    images, labels = _train_and_val(tmp_path)
    monkeypatch.setattr(lp, "load_model", lambda path: StubPose([]))
    assert _dry(images / "val", labels / "val", tmp_path / "o1", fold="val") == 0
    assert _dry(images, labels, tmp_path / "o2", fold="val") == 0
    assert len(list((tmp_path / "o2").glob("p_*.json"))) == 2
    # The images folder itself being `val` is as much a val frame under fold all.
    assert _dry(images / "val", labels / "val", tmp_path / "o3", fold="all") == 2


def _stub_torch(monkeypatch):
    import sys
    import types
    got = []
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(set_num_threads=got.append))
    return got


def test_pose_runs_on_two_threads_by_default(tmp_path, monkeypatch):
    images, labels = _dataset(tmp_path)
    got = _stub_torch(monkeypatch)
    monkeypatch.setattr(lp, "load_model", lambda path: StubPose([]))
    assert _dry(images, labels, tmp_path / "out") == 0
    assert got == [2]


def test_threads_can_be_set_and_are_applied_before_the_model_loads(tmp_path, monkeypatch):
    images, labels = _dataset(tmp_path)
    got = _stub_torch(monkeypatch)
    seen = []
    monkeypatch.setattr(lp, "load_model", lambda path: seen.append(list(got)) or StubPose([]))
    assert _dry(images, labels, tmp_path / "out", "--threads", "1") == 0
    assert got == [1] and seen == [[1]]


def test_a_picks_row_gives_the_items_origin_its_provenance(tmp_path, monkeypatch):
    images, labels = _dataset(tmp_path)
    rows = [{"stem": "a", "video": "VID", "t": 12.5, "rect": [1, 2, 3, 4], "kind": "lost_no_pose",
             "game": 1, "end": 3, "sheet": 2, "club": "evergreen", "panel": "t", "info": {"x": 1}},
            {"stem": "zzz", "video": "other"}]
    (tmp_path / "picks.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows) + "not json\n")
    monkeypatch.setattr(lp, "load_model", lambda path: StubPose([]))
    out = tmp_path / "out"
    assert _dry(images, labels, out) == 0
    by_stem = {i["payload"]["origin"]["stem"]: i["payload"]["origin"]
               for i in (json.loads(f.read_text()) for f in out.glob("p_*.json"))}
    assert by_stem["a"] == {"dataset": "d", "fold": "f", "stem": "a", "video": "VID", "t": 12.5,
                            "rect": [1, 2, 3, 4], "kind": "lost_no_pose", "game": 1, "end": 3,
                            "sheet": 2, "club": "evergreen"}
    assert by_stem["b"] == {"dataset": "d", "fold": "f", "stem": "b"}      # no pick: as before


def test_a_picks_file_inside_the_images_folder_counts_too_and_null_fields_are_left_out(tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    (images / "picks.jsonl").write_text(json.dumps({"stem": "a", "video": "V", "game": None}) + "\n")
    assert lp.picks(images) == {"a": {"video": "V"}}
    assert lp.picks(tmp_path / "nowhere") == {}


# --- receiving or throwing ------------------------------------------------------

SPANS = [(100.0, 700.0, "bottom"), (760.0, 1400.0, "top")]


def test_inside_an_end_its_house_is_receiving_and_the_other_throwing():
    assert lp.side_of(SPANS, "bottom", 300.0) == "receiving"
    assert lp.side_of(SPANS, "top", 300.0) == "throwing"
    assert lp.side_of(SPANS, "top", 1400.0) == "receiving"


def test_between_ends_a_frame_belongs_to_the_next_ends_run_up():
    assert lp.side_of(SPANS, "top", 730.0) == "receiving"
    assert lp.side_of(SPANS, "bottom", 730.0) == "throwing"


def test_far_from_any_end_or_with_runs_disagreeing_the_ends_say_nothing():
    assert lp.side_of(SPANS, "top", 5000.0) is None
    assert lp.side_of(SPANS, "top", 100.0 - lp.RUN_UP_S - 1) is None
    assert lp.side_of(SPANS + [(200.0, 300.0, "top")], "top", 250.0) is None
    assert lp.side_of([], "top", 250.0) is None


def test_a_stem_gives_video_panel_and_time_and_an_item_id_does_not():
    assert lp.parse_stem("-0pMQupm60I_b_000615_40") == ("-0pMQupm60I", "bottom", 615.4)
    assert lp.parse_stem("Y_XxbP1-G_o_t_003416_20") == ("Y_XxbP1-G_o", "top", 3416.2)
    assert lp.parse_stem("b_81b4b696fd7bf9e1") is None
    assert lp.parse_stem("qMDNIgIGHZs_t_003725_90_logo1") is None


def test_the_timeline_decides_where_it_can_and_the_stones_where_it_cannot():
    ends = {"vid": SPANS}
    assert lp.sort_frame(0, ("vid", "bottom", 300.0), ends) == {
        "keep": True, "side": "receiving", "by": "timeline"}
    assert lp.sort_frame(9, ("vid", "top", 300.0), ends) == {
        "keep": False, "side": "throwing", "by": "timeline"}
    assert lp.sort_frame(lp.MIN_STONES, ("other", "top", 300.0), ends)["keep"] is True
    assert lp.sort_frame(lp.MIN_STONES - 1, ("other", "top", 300.0), ends) == {
        "keep": False, "side": "throwing", "by": "stones"}
    assert lp.sort_frame(0, None, ends) == {"keep": True, "side": None, "by": "no provenance"}


def _timeline(video, ends):
    return {"source": {"video_id": video},
            "games": [{"ends": [{"house": h, "start_s": a, "end_s": b} for a, b, h in ends]}]}


def test_ends_joins_every_run_of_a_video_and_skips_what_is_not_a_timeline(tmp_path):
    tl = tmp_path / "tl"
    tl.mkdir()
    (tl / "r1.json").write_text(json.dumps(_timeline("vid", SPANS[:1])))
    (tl / "r2.json").write_text(json.dumps(_timeline("vid", SPANS)))
    (tl / "junk.json").write_text("{}")
    out = tmp_path / "ends.json"
    assert lp.main(["ends", "--timelines", str(tl), "--out", str(out)]) == 0
    assert json.loads(out.read_text()) == {"vid": [list(s) for s in SPANS]}


def test_sort_places_named_and_member_frames_and_keeps_ones_with_no_provenance(tmp_path):
    import cv2
    images, labels = tmp_path / "images", tmp_path / "labels"
    images.mkdir()
    labels.mkdir()
    stems = ["vid_padding_b_000300_00", "vidxxxxxxxx_t_000300_00", "b_0123456789abcdef",
             "qMDNIgIGHZs_t_003725_90_logo1"]
    for s in stems:
        cv2.imwrite(str(images / f"{s}.jpg"), np.zeros((8, 8, 3), np.uint8))
    (labels / "vidxxxxxxxx_t_000300_00.txt").write_text("0 .5 .5 .1 .1\n1 .2 .2 .1 .1\n\n")
    ends = tmp_path / "ends.json"
    ends.write_text(json.dumps({"vidxxxxxxxx": [[100.0, 700.0, "bottom"]],
                                "vidyyyyyyyy": [[100.0, 700.0, "top"]]}))
    member = tmp_path / "member.jsonl"
    member.write_text(json.dumps({"id": "b_0123456789abcdef", "origin": {
        "video_id": "vidyyyyyyyy", "where": "top", "t": 250.5}}) + "\n")
    out = tmp_path / "sides.json"
    rc = lp.main(["sort", "--images", str(images), "--labels", str(labels), "--ends", str(ends),
                  "--provenance", str(member), "--out", str(out)])
    assert rc == 0
    got = json.loads(out.read_text())
    assert got["vidxxxxxxxx_t_000300_00"] == {"keep": False, "side": "throwing", "by": "timeline"}
    assert got["b_0123456789abcdef"] == {"keep": True, "side": "receiving", "by": "timeline"}
    assert got["qMDNIgIGHZs_t_003725_90_logo1"]["by"] == "no provenance"
    # A video no timeline covers goes by its stones: none here.
    assert got["vid_padding_b_000300_00"] == {"keep": False, "side": "throwing", "by": "stones"}


def test_publish_with_sides_takes_only_kept_frames_and_records_their_side(tmp_path, monkeypatch):
    images, labels = _dataset(tmp_path, names=("a", "b", "c"))
    sides = tmp_path / "sides.json"
    sides.write_text(json.dumps({"a": {"keep": True, "side": "receiving", "by": "timeline"},
                                 "b": {"keep": False, "side": "throwing", "by": "stones"},
                                 "c": {"keep": True, "side": None, "by": "no provenance"}}))
    monkeypatch.setattr(lp, "load_model", lambda path: StubPose([]))
    out = tmp_path / "out"
    rc = lp.main(["publish", "--images", str(images), "--labels", str(labels), "--dataset", "d",
                  "--fold", "f", "--batch", "pl", "--weights", "w.pt", "--dry-run",
                  "--out", str(out), "--sides", str(sides)])
    assert rc == 0
    origins = {i["stem"]: i for i in (json.loads(f.read_text())["payload"]["origin"]
                                      for f in out.glob("p_*.json"))}
    assert sorted(origins) == ["a", "c"]
    assert origins["a"]["side"] == "receiving"
    assert "side" not in origins["c"]


def test_publish_refuses_frames_the_sides_file_does_not_cover(tmp_path, monkeypatch, capsys):
    images, labels = _dataset(tmp_path)
    sides = tmp_path / "sides.json"
    sides.write_text(json.dumps({"a": {"keep": True, "side": "receiving", "by": "timeline"}}))
    monkeypatch.setattr(lp, "load_model", lambda path: pytest.fail("loaded the model"))
    rc = lp.main(["publish", "--images", str(images), "--labels", str(labels), "--dataset", "d",
                  "--fold", "f", "--batch", "pl", "--weights", "w.pt", "--dry-run",
                  "--out", str(tmp_path / "out"), "--sides", str(sides)])
    assert rc == 2
    assert "not in" in capsys.readouterr().err


def test_lock_stones_marks_every_item_and_is_off_by_default(tmp_path, monkeypatch):
    images, labels = _dataset(tmp_path)
    monkeypatch.setattr(lp, "load_model", lambda path: StubPose([]))
    args = ["publish", "--images", str(images), "--labels", str(labels), "--dataset", "d",
            "--fold", "f", "--batch", "pl", "--weights", "w.pt", "--dry-run"]
    assert lp.main(args + ["--out", str(tmp_path / "open")]) == 0
    assert lp.main(args + ["--out", str(tmp_path / "locked"), "--lock-stones"]) == 0
    payloads = lambda d: [json.loads(f.read_text())["payload"] for f in (tmp_path / d).glob("p_*.json")]
    assert all("stones_locked" not in p for p in payloads("open"))
    assert [p["stones_locked"] for p in payloads("locked")] == [True, True]


def test_a_label_files_player_lines_become_starts_with_their_confidence():
    # 0/1 are stones, not starts; a 6th column is the confidence; under 3 px once
    # clipped is dropped, as the pose starts are.
    txt = ("0 0.5 0.5 0.2 0.1\n2 0.5 0.2 0.2 0.2 0.83\n2 0.95 0.5 0.2 0.2\n"
           "2 0.5 0.5 0.001 0.5 0.9\n")
    assert lp.starts_from_label(txt, 300, 500) == [
        {"x0": 120.0, "y0": 50.0, "x1": 180.0, "y1": 150.0, "conf": 0.83},
        {"x0": 255.0, "y0": 200.0, "x1": 300.0, "y1": 300.0}]


def test_starts_from_a_folder_load_no_model_and_need_no_weights(tmp_path, monkeypatch):
    images, labels = _dataset(tmp_path)
    starts = tmp_path / "starts"
    starts.mkdir()
    (starts / "a.txt").write_text("2 0.3 0.5 0.4 0.5 0.7\n")
    monkeypatch.setattr(lp, "load_model", lambda path: pytest.fail("no model with --starts"))
    out = tmp_path / "out"
    rc = lp.main(["publish", "--images", str(images), "--labels", str(labels), "--dataset", "d",
                  "--fold", "f", "--batch", "pl", "--starts", str(starts), "--dry-run",
                  "--out", str(out)])
    assert rc == 0
    by_stem = {p["origin"]["stem"]: p for p in
               (json.loads(f.read_text())["payload"] for f in out.glob("p_*.json"))}
    assert by_stem["a"]["starts"] == [{"x0": 10.0, "y0": 50.0, "x1": 50.0, "y1": 150.0, "conf": 0.7}]
    assert by_stem["b"]["starts"] == []                 # no file: no starts
