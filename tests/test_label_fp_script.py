"""Publishing harvested FP frames as box items: the crop, the pre-boxes, the client."""

import importlib.util
import io
import json
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fp = _load("label_fp")
pub = _load("label_publish")


def side_row(box, rect=(0, 0, 1280, 720), **kw):
    return dict({"stem": "vid1_long_000010_00_x", "camera": "side", "color": None,
                 "box": list(box), "rect": list(rect), "conf": 0.6}, **kw)


def det(x0, y0, x1, y1, color="red", conf=0.9):
    return {"x0": x0, "y0": y0, "x1": x1, "y1": y1, "color": color, "conf": conf}


def test_side_crop_centres_on_the_flag_and_stays_in_the_panel():
    rect = (100, 200, 1000, 500)
    row = side_row((1050, 400, 1090, 420), rect=rect)
    x, y, w, h = fp.crop_for(row)
    assert (w, h) == (fp.CROP_W, fp.CROP_H) == (720, 360)
    assert x + w == rect[0] + rect[2]            # pinned to the panel's right edge
    assert rect[1] <= y and y + h <= rect[1] + rect[3]
    # Away from the edges the flag's centre is the crop's centre.
    x, y, w, h = fp.crop_for(side_row((500, 400, 540, 420), rect=rect))
    assert (x + w / 2, y + h / 2) == (520, 410)


def test_side_crop_shrinks_to_a_smaller_panel():
    assert fp.crop_for(side_row((100, 100, 140, 120), rect=(0, 0, 600, 300))) == (0, 0, 600, 300)


def test_overhead_crop_is_the_panel():
    row = {"camera": "overhead", "box": [300, 300, 340, 340], "rect": [250, 120, 516, 298]}
    assert fp.crop_for(row) == (250, 120, 516, 298)


def test_a_detection_on_the_flag_is_replaced_by_the_flag():
    flagged = {"x0": 500, "y0": 400, "x1": 540, "y1": 420, "color": None, "conf": 0.55}
    dets = [det(502, 401, 541, 421, "yellow", 0.9), det(600, 400, 640, 420, "red", 0.8)]
    starts = fp.starts_for(dets, flagged, (400, 300, 720, 360))
    assert len(starts) == 2
    f = [s for s in starts if s["flagged"]]
    assert len(f) == 1
    # The row has no colour, so the flag takes the detection it replaced.
    assert f[0]["color"] == "yellow" and f[0]["conf"] == 0.55
    assert (f[0]["x0"], f[0]["y0"], f[0]["x1"], f[0]["y1"]) == (100, 100, 140, 120)
    other = [s for s in starts if not s["flagged"]][0]
    assert (other["x0"], other["color"]) == (200, "red")


def test_the_row_colour_wins_and_red_is_the_last_resort():
    crop = (0, 0, 720, 360)
    flagged = {"x0": 10, "y0": 10, "x1": 50, "y1": 30, "color": "yellow", "conf": 0.5}
    assert fp.starts_for([det(10, 10, 50, 30, "red")], flagged, crop)[0]["color"] == "yellow"
    flagged["color"] = None
    assert fp.starts_for([], flagged, crop)[0]["color"] == "red"


def test_detections_mostly_outside_are_dropped_and_others_clipped():
    crop = (100, 100, 400, 300)
    flagged = {"x0": 200, "y0": 200, "x1": 240, "y1": 220, "color": "red", "conf": 0.5}
    dets = [
        det(60, 150, 110, 190),     # 20% inside: dropped
        det(80, 150, 130, 190),     # 60% inside: kept, clipped at x=0
        det(490, 150, 540, 190),    # 20% inside the right edge: dropped
        det(480, 150, 530, 190),    # 40% inside is kept (>= KEEP_INSIDE), clipped to w
    ]
    got = fp.starts_for(dets, flagged, crop)
    kept = sorted((s["x0"], s["x1"]) for s in got if not s["flagged"])
    assert kept == [(0.0, 30.0), (380.0, 400.0)]


def test_exactly_one_start_is_flagged():
    crop = (0, 0, 720, 360)
    flagged = {"x0": 100, "y0": 100, "x1": 140, "y1": 120, "color": "red", "conf": 0.5}
    dets = [det(100, 100, 140, 120), det(101, 99, 141, 121), det(300, 100, 340, 120)]
    got = fp.starts_for(dets, flagged, crop)
    assert sum(s["flagged"] for s in got) == 1
    assert len(got) == 2        # both detections on the flag are the flag


def test_item_for_carries_origin_and_the_crop():
    row = side_row((500, 400, 540, 420), video_id="vid1", title="T", t=10.0, where="long",
                   check="long.width", models={"rocks": "ds13e"}, pipeline="2026.10.22.1")
    crop = fp.crop_for(row)
    item = fp.item_for(row, crop, [], "fp-2026-10-22")
    assert item["id"] == "b_vid1_long_000010_00_x" and item["kind"] == "box"
    assert item["batch"] == "fp-2026-10-22"
    p = item["payload"]
    assert (p["w"], p["h"]) == (720, 360) and p["starts"] == []
    assert p["origin"]["crop"] == list(crop)
    assert p["origin"]["video_id"] == "vid1" and p["origin"]["models"] == {"rocks": "ds13e"}
    assert "reference" not in item


def test_an_id_the_server_would_refuse_is_mapped_the_same_way_each_time():
    import re
    ok = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")
    assert fp.item_id("abc_1-2.3") == "b_abc_1-2.3"
    odd = fp.item_id("a b/é" + "x" * 200)
    assert ok.match(odd) and odd == fp.item_id("a b/é" + "x" * 200)
    assert odd != fp.item_id("a b/é" + "x" * 201)


# --- the client -------------------------------------------------------------

class Server:
    """A fake of the three admin calls; ``answers`` says what create returns."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, req, timeout=None):
        body = req.data
        self.calls.append((req.get_method(), req.full_url, dict(req.header_items()), body))
        url = req.full_url
        if url.endswith("/api/admin/label-items"):
            ids = [i["id"] for i in json.loads(body)["items"]]
            out = {"items": [{"id": i, "state": "pending" if self.answers[i] else "open",
                              "upload": self.answers[i]} for i in ids]}
        elif url.endswith("/api/admin/label-items/ready"):
            out = {"opened": json.loads(body)["ids"], "missing_image": []}
        else:
            out = {}
        return io.BytesIO(json.dumps(out).encode())


def test_a_rerun_reuploads_pending_and_skips_open():
    server = Server({"b_a": {"url": "https://bucket/a", "headers": {"x-goog-meta": "1"}},
                     "b_b": None})
    items = [{"id": "b_a", "kind": "box", "batch": "x", "payload": {}},
             {"id": "b_b", "kind": "box", "batch": "x", "payload": {}}]
    got = pub.publish("https://example.test", "tok", items,
                      {"b_a": b"AAA", "b_b": b"BBB"}, opener=server)
    puts = [c for c in server.calls if c[0] == "PUT"]
    assert [(c[1], c[3]) for c in puts] == [("https://bucket/a", b"AAA")]
    assert puts[0][2].get("X-goog-meta") == "1"
    # Only our own API gets the admin token, never the signed URL.
    assert all("Authorization" not in c[2] for c in puts)
    posts = [c for c in server.calls if c[0] == "POST"]
    assert all(c[2]["Authorization"] == "Bearer tok" for c in posts)
    ready = json.loads(posts[-1][3])
    assert ready == {"ids": ["b_a", "b_b"]}
    assert got["skipped"] == ["b_b"] and got["opened"] == ["b_a", "b_b"]
    assert got["missing_image"] == []


def test_dry_run_sends_nothing():
    def boom(*a, **k):
        raise AssertionError("sent a request")
    got = pub.publish("https://example.test", "tok",
                      [{"id": "b_a", "kind": "box", "batch": "x", "payload": {}}],
                      {"b_a": b"A"}, dry_run=True, opener=boom)
    assert got == {"opened": [], "skipped": [], "missing_image": []}


def test_create_goes_out_in_pages_the_server_accepts():
    server = Server({f"b_{i}": None for i in range(pub.PAGE + 1)})
    items = [{"id": f"b_{i}", "kind": "box", "batch": "x", "payload": {}}
             for i in range(pub.PAGE + 1)]
    pub.publish("https://example.test", "tok", items, {}, opener=server)
    creates = [c for c in server.calls if c[1].endswith("/api/admin/label-items")]
    assert [len(json.loads(c[3])["items"]) for c in creates] == [pub.PAGE, 1]


def test_an_item_without_an_image_is_not_uploaded_or_readied():
    server = Server({"b_a": {"url": "https://bucket/a", "headers": {}}})
    got = pub.publish("https://example.test", "tok",
                      [{"id": "b_a", "kind": "box", "batch": "x", "payload": {}}], {},
                      opener=server)
    assert not [c for c in server.calls if c[0] == "PUT"]
    assert got["missing_image"] == ["b_a"]


# --- main, dry run ----------------------------------------------------------

def _write_pile(cache, rows):
    import cv2
    import numpy as np
    d = cache / "harvest" / "fp" / "vid1"
    d.mkdir(parents=True)
    (d / "frames.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    for r in rows:
        cv2.imwrite(str(d / f"{r['stem']}.jpg"), np.zeros((1080, 1920, 3), np.uint8))


def test_dry_run_writes_a_crop_and_an_item_per_row_and_sends_nothing(tmp_path, monkeypatch):
    rows = [dict(side_row((900, 500, 940, 520), rect=(0, 0, 1920, 1080)),
                 stem=f"s{i}", video_id="vid1", title="T", t=1.0 + i, where="long",
                 check="long.width", models={"rocks": "ds13e"}, pipeline="p",
                 saved_at="2026-10-22T00:00:00+00:00") for i in range(2)]
    cache = tmp_path / "cache"
    _write_pile(cache, rows)
    monkeypatch.setattr(fp, "load_detectors", lambda: (lambda img, camera: [], {}))
    monkeypatch.setattr(fp.label_publish, "publish",
                        lambda *a, **k: pytest.fail("published in a dry run"))
    out = tmp_path / "out"
    assert fp.main(["publish", "--caches", str(cache), "--dry-run", "--out", str(out)]) == 0
    assert sorted(p.name for p in out.iterdir()) == [
        "b_s0.jpg", "b_s0.json", "b_s1.jpg", "b_s1.json"]
    item = json.loads((out / "b_s0.json").read_text())
    assert item["payload"]["w"] == 720 and item["payload"]["starts"][0]["flagged"] is True
    assert item["payload"]["origin"]["crop"] == [560, 330, 720, 360]


def test_check_frames_carry_the_known_boxes_and_flag_the_most_confident_prebox(tmp_path):
    import cv2
    import numpy as np
    cv2.imwrite(str(tmp_path / "k1.jpg"), np.zeros((200, 400, 3), np.uint8))
    cv2.imwrite(str(tmp_path / "k2.jpg"), np.zeros((200, 400, 3), np.uint8))
    known = [{"x0": 1, "y0": 2, "x1": 31, "y1": 22, "color": "red"}]
    (tmp_path / "boxes.json").write_text(json.dumps({"k1": known, "k2": known}))

    def detect(img, camera):
        return [det(10, 10, 50, 30, "red", 0.4), det(100, 50, 140, 70, "yellow", 0.8)] \
            if camera == "side" and detect.first else []
    detect.first = True
    (item, jpg), = [x for x in fp.check_items(tmp_path, "side", detect, "chk") if
                    x[0]["id"] == "bc_k1"]
    assert item["reference"] == {"boxes": known}
    flagged = [s for s in item["payload"]["starts"] if s["flagged"]]
    assert len(item["payload"]["starts"]) == 2 and flagged[0]["color"] == "yellow"
    assert jpg[:2] == b"\xff\xd8"
    # With no pre-box a centred 40x17 box is the flag.
    detect.first = False
    item = next(i for i, _ in fp.check_items(tmp_path, "side", detect, "chk"))
    (s,) = item["payload"]["starts"]
    assert s["flagged"] and (s["x1"] - s["x0"], s["y1"] - s["y0"]) == (40, 17)


def test_a_row_whose_detector_raises_is_skipped_and_the_rest_are_written(tmp_path, monkeypatch,
                                                                         capsys):
    rows = [dict(side_row((900, 500, 940, 520), rect=(0, 0, 1920, 1080)),
                 stem=f"s{i}", video_id="vid1", title="T", t=1.0 + i, where="long",
                 check="long.width", models={}, pipeline="p",
                 saved_at="2026-10-22T00:00:00+00:00") for i in range(3)]
    cache = tmp_path / "cache"
    _write_pile(cache, rows)
    calls = []

    def detect(img, camera):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("cuda said no")
        return []
    monkeypatch.setattr(fp, "load_detectors", lambda: (detect, {}))
    out = tmp_path / "out"
    assert fp.main(["publish", "--caches", str(cache), "--dry-run", "--out", str(out)]) == 0
    assert len(list(out.glob("*.json"))) == 2
    err = capsys.readouterr().err
    assert "cuda said no" in err and "skipped" in err


def test_a_row_whose_crop_is_outside_the_image_is_skipped(tmp_path, monkeypatch):
    good = dict(side_row((900, 500, 940, 520), rect=(0, 0, 1920, 1080)), stem="g",
                video_id="vid1", saved_at="2026-10-22T00:00:00+00:00")
    off = dict(side_row((900, 500, 940, 520), rect=(5000, 5000, 800, 600)), stem="o",
               video_id="vid1", saved_at="2026-10-22T00:00:00+00:00")
    cache = tmp_path / "cache"
    _write_pile(cache, [good, off])
    monkeypatch.setattr(fp, "load_detectors", lambda: (lambda img, camera: [], {}))
    out = tmp_path / "out"
    assert fp.main(["publish", "--caches", str(cache), "--dry-run", "--out", str(out)]) == 0
    assert [p.name for p in out.glob("*.json")] == ["b_g.json"]


def test_a_check_box_without_a_colour_is_skipped_with_a_message(tmp_path, capsys):
    import cv2
    import numpy as np
    for stem in ("ok", "nocolour"):
        cv2.imwrite(str(tmp_path / f"{stem}.jpg"), np.zeros((200, 400, 3), np.uint8))
    good = [{"x0": 1, "y0": 2, "x1": 31, "y1": 22, "color": "red"}]
    (tmp_path / "boxes.json").write_text(json.dumps(
        {"ok": good, "nocolour": [{"x0": 1, "y0": 2, "x1": 31, "y1": 22}], "noimage": good}))
    skips = []
    got = list(fp.check_items(tmp_path, "side", lambda img, camera: [], "chk", None, skips))
    assert [i["id"] for i, _ in got] == ["bc_ok"]
    assert sorted(skips) == ["nocolour", "noimage"]
    err = capsys.readouterr().err
    assert "check nocolour: skipped" in err and "colour" in err


def test_unreadable_boxes_json_stops_the_run_with_a_message(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(fp, "load_detectors", lambda: (lambda img, camera: [], {}))
    (tmp_path / "boxes.json").write_text("{not json")
    assert fp.main(["publish", "--checks", str(tmp_path), "--dry-run",
                    "--out", str(tmp_path / "o")]) == 1
    assert "cannot read the check frames" in capsys.readouterr().err
