"""Bringing the labels home: a fake API, no network. Image bytes are tiny JPEGs
and sprites are made by label_releases' own make_sprite, so the cut-back-out
layout is the one the site is given."""

import importlib.util
import io
import json
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


exp = _load("labels_export")
rel = _load("label_releases")

T0 = datetime(2026, 11, 1, 12, 0, 0, 123456, tzinfo=timezone.utc)


def jpeg(color=(200, 30, 30), size=(100, 60)) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


def sprite(n=12, fw=20, fh=10, cols=10) -> tuple[bytes, dict]:
    """Frame i is solid (10 * i, 0, 0)."""
    import tempfile
    from PIL import Image
    d = Path(tempfile.mkdtemp())
    paths = []
    for i in range(n):
        p = d / f"f{i}.jpg"
        Image.new("RGB", (fw, fh), (0, 0, 10 * i)).save(p, "JPEG", quality=95)   # cv2 reads BGR
        paths.append(p)
    return rel.make_sprite(paths, cols)


class FakeApi:
    """The opener: the admin listing (paged by `after`), the accounts, and
    image bytes at https://bucket.test/<name>."""

    def __init__(self, items, users=(), page=2):
        self.items = list(items)
        self.users = list(users)
        self.page = page
        self.images = {}
        self.calls = []

    def put_image(self, name, data):
        self.images[name] = data
        return f"https://bucket.test/{name}?sig=1"

    def __call__(self, req, timeout=None):
        url = req.full_url
        self.calls.append((url, req.get_header("Authorization")))
        parsed = urllib.parse.urlparse(url)
        q = urllib.parse.parse_qs(parsed.query)
        if parsed.netloc == "bucket.test":
            body = self.images[parsed.path.lstrip("/")]
        elif parsed.path == "/api/admin/users":
            body = json.dumps({"users": self.users}).encode()
        elif parsed.path == "/api/admin/label-items":
            kind, state = q["kind"][0], q["state"][0]
            since = datetime.fromisoformat(q["after"][0]) if "after" in q else None
            rows = sorted((i for i in self.items if i["kind"] == kind and i["state"] == state
                           and (since is None or datetime.fromisoformat(i["created_at"]) > since)),
                          key=lambda i: i["created_at"])[:self.page]
            body = json.dumps({"items": rows,
                               "after": rows[-1]["created_at"] if rows else None}).encode()
        else:
            raise AssertionError(url)
        return _Reply(body)


class _Reply:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.body


_n = [0]


def _stamp():
    _n[0] += 1
    return (T0 + timedelta(seconds=_n[0])).isoformat()


def answer(item_id, uid, kind, ans, seconds=5.0, at=None):
    return {"id": f"{item_id}-{uid}", "item_id": item_id, "kind": kind, "uid": uid,
            "at": at or _stamp(), "seconds": seconds, "answer": ans}


def box(x0, y0, x1, y1, color="red"):
    return {"x0": x0, "y0": y0, "x1": x1, "y1": y1, "color": color, "start": None}


def box_ans(*boxes, cant_tell=False):
    return {"cant_tell": cant_tell, "boxes": list(boxes), "starts": []}


def box_item(api, item_id, answers, state="done", reference=None, crop=(10, 20, 100, 60)):
    url = api.put_image(f"{item_id}.jpg", jpeg(size=(crop[2], crop[3])))
    item = {"id": item_id, "kind": "box", "created_at": _stamp(), "batch": "fp-1", "state": state,
            "payload": {"image": f"labelling/box/{item_id}.jpg", "w": crop[2], "h": crop[3],
                        "starts": [], "origin": {"video_id": "vid", "stem": item_id,
                                                 "crop": list(crop)}},
            "reference": reference, "image_url": url,
            "answers": [answer(item_id, u, "box", a) for u, a in answers]}
    api.items.append(item)
    return item


def rel_ans(mark, frame=None):
    return {"mark": mark, "frame": frame}


def release_item(api, item_id, answers, state="done", reference=None, n=12, batch="releases-1"):
    data, meta = sprite(n)
    url = api.put_image(f"{item_id}.jpg", data)
    frames = [{"o": round(0.0333 * i, 4), "yp": round(1.0 + 0.1 * i, 3)} for i in range(n)]
    item = {"id": item_id, "kind": "release", "created_at": _stamp(), "batch": batch,
            "state": state,
            "payload": {"image": f"labelling/release/{item_id}.jpg", **meta, "fps": 30.0,
                        "frames": frames,
                        "origin": {"source_id": "s_a", "video_id": "vid", "title": "T", "end": 3,
                                   "rock": 7, "color": "red", "position": "lead",
                                   "label": "lbl", "t_release_s": 12.5}},
            "reference": reference, "image_url": url,
            "answers": [answer(item_id, u, "release", a) for u, a in answers]}
    api.items.append(item)
    return item


def run(api, tmp_path, monkeypatch, *argv):
    monkeypatch.setenv("ADMIN_TOKEN", "tok")
    monkeypatch.setenv("BASE_URL", "https://site.test")
    out = tmp_path / "out"
    code = exp.main(["--out", str(out), *argv], opener=api)
    return code, out


def jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_agreeing_box_items_become_a_yolo_tree(tmp_path, monkeypatch):
    api = FakeApi([])
    box_item(api, "b_one", [("u1", box_ans(box(10, 10, 30, 40), box(50, 10, 70, 30, "yellow"))),
                            ("u2", box_ans(box(12, 10, 30, 40), box(50, 10, 70, 30, "yellow")))])
    code, out = run(api, tmp_path, monkeypatch)
    assert code == 0
    agreed = out / "boxes" / "agreed"
    assert (agreed / "images" / "b_one.jpg").read_bytes() == api.images["b_one.jpg"]
    lines = (agreed / "labels" / "b_one.txt").read_text().splitlines()
    assert lines[0].split()[0] == "0" and lines[1].split()[0] == "1"
    cls, cx, cy, w, h = lines[0].split()
    assert (float(cx), float(cy), float(w), float(h)) == pytest.approx(
        (20.5 / 100, 25 / 60, 19 / 100, 30 / 60), abs=1e-6)
    (row,) = jsonl(agreed / "agreed.jsonl")
    assert row["id"] == "b_one" and row["origin"]["video_id"] == "vid"
    assert row["boxes"][0] == {"x0": 11.0, "y0": 10.0, "x1": 30.0, "y1": 40.0, "color": "red"}
    # whole-frame pixels: the crop's corner (10, 20) added
    assert row["boxes_frame"][0] == {"x0": 21.0, "y0": 30.0, "x1": 40.0, "y1": 60.0,
                                     "color": "red"}
    assert not list((out / "boxes" / "disputed" / "images").iterdir())


def test_disputed_boxes_render_with_ghosts(tmp_path, monkeypatch):
    api = FakeApi([])
    box_item(api, "b_two", [("u1", box_ans(box(10, 10, 30, 40, "red"))),
                            ("u2", box_ans(box(10, 10, 30, 40, "yellow")))])
    code, out = run(api, tmp_path, monkeypatch)
    dis = out / "boxes" / "disputed"
    assert not list((out / "boxes" / "agreed" / "images").iterdir())
    assert (dis / "images" / "b_two.jpg").exists()
    (row,) = jsonl(dis / "disputed.jsonl")
    assert row["id"] == "b_two" and row["why"] == "colour"
    assert [a["uid"] for a in row["answers"]] == ["u1", "u2"]
    assert row["answers"][1]["answer"]["boxes"][0]["color"] == "yellow"
    page = (dis / "index.html").read_text()
    blob = page.split("const ITEMS = ", 1)[1].split(";\n", 1)[0]
    (item,) = json.loads(blob)
    assert item["stem"] == "b_two" and item["image"] == "images/b_two.jpg"
    assert item["geom"] is None and (item["width"], item["height"]) == (100, 60)
    # the first member's box is the editable one (proposals on), the second's the ghost
    assert item["boxes"] == [[0, pytest.approx(0.2), pytest.approx(25 / 60),
                              pytest.approx(0.2), pytest.approx(0.5)]]
    assert item["ghosts"][0][0] == 1


def test_retired_items_export_as_disputed(tmp_path, monkeypatch):
    api = FakeApi([])
    box_item(api, "b_skip", [], state="retired")
    release_item(api, "r_skip", [], state="retired")
    code, out = run(api, tmp_path, monkeypatch)
    (row,) = jsonl(out / "boxes" / "disputed" / "disputed.jsonl")
    assert row["id"] == "b_skip" and row["why"] == "skipped" and row["answers"] == []
    blob = (out / "boxes" / "disputed" / "index.html").read_text() \
        .split("const ITEMS = ", 1)[1].split(";\n", 1)[0]
    assert json.loads(blob)[0]["boxes"] == []
    (d,) = json.loads((out / "releases" / "disputed" / "disputed.json").read_text())
    assert d["id"] == "r_skip" and d["why"] == "skipped" and d["members"] == []
    assert (out / "releases" / "disputed" / "r_skip_000.jpg").exists()


def test_agreeing_releases_land_in_releasemarks_shape(tmp_path, monkeypatch):
    api = FakeApi([])
    release_item(api, "r_a", [("u1", rel_ans("off", 5)), ("u2", rel_ans("off", 6))])
    release_item(api, "r_held", [("u1", rel_ans("held")), ("u2", rel_ans("held"))])
    code, out = run(api, tmp_path, monkeypatch)
    got = json.loads((out / "releases" / "agreed.json").read_text())
    assert got["batch"] == "releases-1" and got["fps"] == 30.0
    assert "first frame the throwing hand is clearly off the handle" in got["definition"]
    a, held = got["rocks"]
    assert a["id"] == "r_a" and a["chosen"] == "member"
    assert (a["mark"], a["frame"]) == ("off", 6)           # the later frame
    assert a["o"] == pytest.approx(0.0333 * 6) and a["yp"] == pytest.approx(1.6)
    assert a["yp_prev"] == pytest.approx(1.5)
    assert (a["video_id"], a["end"], a["rock"], a["color"], a["t_release_s"]) == \
        ("vid", 3, 7, "red", 12.5)
    assert a["members"] == [{"uid": "u1", "mark": "off", "frame": 5},
                            {"uid": "u2", "mark": "off", "frame": 6}]
    assert (held["mark"], held["frame"], held["o"], held["yp"], held["yp_prev"]) == \
        ("held", None, None, None, None)
    assert not json.loads((out / "releases" / "disputed" / "index.json").read_text())


def test_disputed_releases_become_a_prepared_mark_session(tmp_path, monkeypatch):
    api = FakeApi([])
    release_item(api, "r_far", [("u1", rel_ans("off", 2)), ("u2", rel_ans("off", 9))])
    release_item(api, "r_mix", [("u1", rel_ans("held")), ("u2", rel_ans("off", 4))])
    code, out = run(api, tmp_path, monkeypatch)
    sess = out / "releases" / "disputed"
    assert (sess / "marks.json").read_text() == "{}"
    index = json.loads((sess / "index.json").read_text())
    assert [e["id"] for e in index] == ["r_far", "r_mix"]
    e = index[0]
    assert len(e["frames"]) == 12                      # n entries, one JPEG each
    assert len(list(sess.glob("r_far_*.jpg"))) == 12
    assert e["frames"][3] == {"file": "r_far_003.jpg", "o": pytest.approx(0.0999), "yp": 1.3}
    assert (e["video_id"], e["end"], e["rock"], e["t_release_s"], e["tag"]) == \
        ("vid", 3, 7, 12.5, "")
    # cut back out in make_sprite's row-major layout: frame 11 is row 1, column 1
    from PIL import Image
    im = Image.open(sess / "r_far_011.jpg")
    assert im.size == (20, 10)
    assert im.getpixel((10, 5))[0] == pytest.approx(0, abs=12)
    assert im.getpixel((10, 5))[2] == pytest.approx(110, abs=12)
    d = {x["id"]: x for x in json.loads((sess / "disputed.json").read_text())}
    assert d["r_far"]["why"] == "apart" and d["r_mix"]["why"] == "mixed"
    assert d["r_far"]["members"] == [{"uid": "u1", "mark": "off", "frame": 2},
                                     {"uid": "u2", "mark": "off", "frame": 9}]


def test_member_check_scores(tmp_path, monkeypatch):
    api = FakeApi([], users=[{"id": "u1", "email": "a@x.org"}, {"id": "u2", "email": "b@x.org"}])
    ref_box = {"boxes": [box(10, 10, 30, 40)]}
    box_item(api, "bc_1", [("u1", box_ans(box(11, 10, 30, 40))),
                           ("u2", box_ans(box(40, 10, 60, 40)))],
             state="done", reference=ref_box)
    release_item(api, "rc_1", [("u1", rel_ans("off", 6)), ("u2", rel_ans("held"))],
                 reference={"mark": "off", "frame": 5})
    release_item(api, "r_plain", [("u1", rel_ans("off", 5)), ("u2", rel_ans("off", 5))])
    code, out = run(api, tmp_path, monkeypatch)
    m = json.loads((out / "members.json").read_text())
    assert m["u1"]["email"] == "a@x.org" and m["u2"]["email"] == "b@x.org"
    assert m["u1"]["checks"] == {"n": 2, "agree": 2}
    assert m["u2"]["checks"] == {"n": 2, "agree": 0}
    assert (m["u1"]["box"], m["u1"]["release"]) == (1, 2)
    # check items score the member and are not exported as labels
    assert not list((out / "boxes" / "agreed" / "images").iterdir())
    assert not list((out / "boxes" / "disputed" / "images").iterdir())
    assert [r["id"] for r in json.loads((out / "releases" / "agreed.json").read_text())["rocks"]] \
        == ["r_plain"]


def test_median_seconds_is_per_answer(tmp_path, monkeypatch):
    api = FakeApi([])
    for i, secs in enumerate((2.0, 4.0, 30.0)):
        box_item(api, f"b_{i}", [("u1", {**box_ans(box(1, 1, 20, 20))}),
                                 ("u2", box_ans(box(1, 1, 20, 20)))])
        api.items[-1]["answers"][0]["seconds"] = secs
    code, out = run(api, tmp_path, monkeypatch)
    assert json.loads((out / "members.json").read_text())["u1"]["median_seconds"] == 4.0


def test_it_pages_through_the_listing(tmp_path, monkeypatch):
    api = FakeApi([], page=2)
    for i in range(5):
        box_item(api, f"b_{i}", [("u1", box_ans(box(10, 10, 30, 40))),
                                 ("u2", box_ans(box(10, 10, 30, 40)))])
    code, out = run(api, tmp_path, monkeypatch, "--kind", "box")
    assert len(jsonl(out / "boxes" / "agreed" / "agreed.jsonl")) == 5
    listing = [u for u, _ in api.calls if "/api/admin/label-items" in u
               and "state=done" in u]
    assert len(listing) == 4                         # 2 + 2 + 1, then an empty page
    # `after` carries the "+00:00": it has to be percent-encoded, not sent raw
    assert all("after=" not in u or "%2B00%3A00" in u for u in listing)
    assert not any("kind=release" in u for u, _ in api.calls)


def test_images_are_fetched_without_the_admin_token(tmp_path, monkeypatch):
    api = FakeApi([])
    box_item(api, "b_one", [("u1", box_ans(box(10, 10, 30, 40))),
                            ("u2", box_ans(box(10, 10, 30, 40)))])
    run(api, tmp_path, monkeypatch)
    for url, auth in api.calls:
        assert (auth is None) == url.startswith("https://bucket.test/")


def test_it_needs_the_admin_token(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    assert exp.main(["--out", str(tmp_path / "o")], opener=FakeApi([])) == 2
    assert "ADMIN_TOKEN" in capsys.readouterr().err


def test_an_unreadable_image_skips_that_item_and_says_so(tmp_path, monkeypatch, capsys):
    api = FakeApi([])
    box_item(api, "b_bad", [("u1", box_ans(box(10, 10, 30, 40))),
                            ("u2", box_ans(box(10, 10, 30, 40)))])
    box_item(api, "b_ok", [("u1", box_ans(box(10, 10, 30, 40))),
                           ("u2", box_ans(box(10, 10, 30, 40)))])
    real = api.__call__

    def flaky(req, timeout=None):
        if "b_bad" in req.full_url and req.full_url.startswith("https://bucket.test"):
            raise OSError("boom")
        return real(req, timeout)
    monkeypatch.setenv("ADMIN_TOKEN", "t")
    code = exp.main(["--out", str(tmp_path / "o"), "--kind", "box"], opener=flaky)
    assert code == 1
    assert "b_bad" in capsys.readouterr().err
    assert [r["id"] for r in jsonl(tmp_path / "o" / "boxes" / "agreed" / "agreed.jsonl")] == ["b_ok"]


def test_a_second_export_keeps_the_marks_and_edits_saved_in_the_dispute_folders(
        tmp_path, monkeypatch):
    """mark_release.py and the box editor save their work beside what the export
    wrote; running the export again must not empty marks.json or touch edits/."""
    api = FakeApi([])
    release_item(api, "r_far", [("u1", rel_ans("off", 2)), ("u2", rel_ans("off", 9))])
    box_item(api, "b_two", [("u1", box_ans(box(10, 10, 30, 40, "red"))),
                            ("u2", box_ans(box(10, 10, 30, 40, "yellow")))])
    code, out = run(api, tmp_path, monkeypatch)
    marks = out / "releases" / "disputed" / "marks.json"
    marks.write_text(json.dumps({"r_far": {"mark": "off", "frame": 5}}))
    edits = out / "boxes" / "disputed" / "edits"
    edits.mkdir()
    (edits / "member-labels-20261105-101500.json").write_text('{"boxes": {}}')
    code, out2 = run(api, tmp_path, monkeypatch)
    assert out2 == out and code == 0
    assert json.loads(marks.read_text()) == {"r_far": {"mark": "off", "frame": 5}}
    assert (edits / "member-labels-20261105-101500.json").read_text() == '{"boxes": {}}'
