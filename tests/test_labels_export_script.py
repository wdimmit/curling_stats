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
                        "origin": {"source_id": "s_a", "video_id": "vid", "title": "T", "end": 3,
                                   "rock": 7, "color": "red", "position": "lead",
                                   "label": "lbl", "t_release_s": 12.5, "frames": frames}},
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
    assert row["members"] == [{"uid": "u1", "seconds": 5.0}, {"uid": "u2", "seconds": 5.0}]
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
    assert row["members"] == [{"uid": "u1", "seconds": 5.0}, {"uid": "u2", "seconds": 5.0}]
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
    assert row["members"] == []
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
    assert a["members"] == [{"uid": "u1", "seconds": 5.0, "mark": "off", "frame": 5},
                            {"uid": "u2", "seconds": 5.0, "mark": "off", "frame": 6}]
    assert "frames" not in a                              # the model's readings aren't a row's
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
    assert d["r_far"]["members"] == [
        {"uid": "u1", "seconds": 5.0, "mark": "off", "frame": 2},
        {"uid": "u2", "seconds": 5.0, "mark": "off", "frame": 9}]


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


def test_member_mode_takes_one_members_boxes_from_open_items_too(tmp_path, monkeypatch):
    api = FakeApi([], users=[{"id": "u1", "email": "me@x"}, {"id": "u2", "email": "o@x"}])
    one = box_item(api, "b_open", [("u1", box_ans(box(10, 10, 30, 40)))], state="open")
    one["payload"]["origin"]["camera"] = "side"
    box_item(api, "b_done", [("u2", box_ans(box(1, 1, 5, 5))),
                             ("u1", box_ans(box(50, 10, 70, 30, "yellow")))])
    box_item(api, "b_cant", [("u1", box_ans(cant_tell=True))], state="open")
    box_item(api, "b_check", [("u1", box_ans(box(10, 10, 30, 40)))], state="open",
             reference={"boxes": []})
    box_item(api, "b_theirs", [("u2", box_ans(box(10, 10, 30, 40)))], state="open")
    code, out = run(api, tmp_path, monkeypatch, "--member", "me@x")
    assert code == 0
    member = out / "boxes" / "member"
    assert sorted(p.name for p in member.iterdir()) == ["overhead", "side"]
    (side,) = jsonl(member / "side" / "member.jsonl")
    assert side["id"] == "b_open" and side["state"] == "open"
    assert side["boxes_frame"][0] == {"x0": 20, "y0": 30, "x1": 40, "y1": 60, "color": "red"}
    (ovh,) = jsonl(member / "overhead" / "member.jsonl")
    assert ovh["id"] == "b_done" and ovh["boxes"][0]["color"] == "yellow"
    assert (member / "overhead" / "labels" / "b_done.txt").read_text().split()[0] == "1"
    assert not (out / "boxes" / "agreed").exists()


# ---- player items -------------------------------------------------------------

def pbox(x0, y0, x1, y1, start=None):
    return {"x0": x0, "y0": y0, "x1": x1, "y1": y1, "start": start}


def play_ans(*boxes, starts=(), cant_tell=False):
    return {"cant_tell": cant_tell, "boxes": list(boxes), "starts": list(starts)}


def player_item(api, item_id, answers, state="done", starts=(), stones=(), w=200, h=100):
    """`answers` are (uid, answer) or (uid, answer, discarded)."""
    url = api.put_image(f"{item_id}.jpg", jpeg(size=(w, h)))
    item = {"id": item_id, "kind": "player", "created_at": _stamp(), "batch": "players-1",
            "state": state,
            "payload": {"image": f"labelling/player/{item_id}.jpg", "w": w, "h": h,
                        "starts": list(starts), "stones": list(stones),
                        "origin": {"dataset": "overhead-3", "fold": "all", "stem": item_id}},
            "reference": None, "image_url": url,
            "answers": []}
    for uid, a, *gone in answers:
        item["answers"].append({**answer(item_id, uid, "player", a), "discarded": bool(gone and gone[0])})
    api.items.append(item)
    return item


def test_a_done_player_item_gives_an_image_and_a_class_2_label(tmp_path, monkeypatch):
    api = FakeApi([], page=500)
    player_item(api, "p_one", [("u1", play_ans(pbox(20, 10, 60, 90, start=0),
                                               pbox(100, 20, 120, 60),
                                               starts=["kept"]), )],
                starts=[{"x0": 20, "y0": 10, "x1": 60, "y1": 90, "conf": 0.9}])
    code, out = run(api, tmp_path, monkeypatch, "--kind", "player")
    assert code == 0
    lab = out / "players" / "labelled"
    assert (lab / "images" / "p_one.jpg").read_bytes() == api.images["p_one.jpg"]
    lines = (lab / "labels" / "p_one.txt").read_text().splitlines()
    assert [ln.split()[0] for ln in lines] == ["2", "2"]
    assert [float(v) for v in lines[0].split()[1:]] == pytest.approx(
        [40 / 200, 50 / 100, 40 / 200, 80 / 100], abs=1e-6)
    (row,) = jsonl(lab / "players.jsonl")
    assert row["id"] == "p_one" and row["origin"]["stem"] == "p_one"
    assert row["members"] == [{"uid": "u1", "seconds": 5.0}]
    assert len(row["boxes"]) == 2


def test_an_answer_with_no_players_is_an_empty_label_file(tmp_path, monkeypatch):
    api = FakeApi([], page=500)
    player_item(api, "p_none", [("u1", play_ans())])
    player_item(api, "p_gone", [("u1", play_ans(starts=["deleted"]))],
                starts=[{"x0": 5, "y0": 5, "x1": 20, "y1": 50, "conf": 0.5}])
    code, out = run(api, tmp_path, monkeypatch, "--kind", "player")
    lab = out / "players" / "labelled" / "labels"
    assert (lab / "p_none.txt").read_text() == ""
    assert (lab / "p_gone.txt").read_text() == ""
    assert (out / "players" / "labelled" / "images" / "p_none.jpg").exists()
    assert not (out / "players" / "to_user" / "images" / "p_none.jpg").exists()


def stone_fix(*boxes):
    return [{"x0": x0, "y0": y0, "x1": x1, "y1": y1, "color": c} for x0, y0, x1, y1, c in boxes]


def test_a_stone_fix_is_written_as_the_frames_stone_label_and_drawn_for_a_look(tmp_path, monkeypatch):
    api = FakeApi([], page=500)
    ans = {**play_ans(pbox(20, 10, 60, 90)),
           "stones": stone_fix((100, 40, 120, 60, "yellow"), (150, 10, 160, 20, "red"))}
    player_item(api, "p_fix", [("u1", ans)],
                stones=stone_fix((101, 40, 121, 60, "red")))
    player_item(api, "p_plain", [("u1", play_ans(pbox(20, 10, 60, 90)))])
    code, out = run(api, tmp_path, monkeypatch, "--kind", "player")
    assert code == 0
    lab = out / "players" / "labelled"
    lines = (lab / "stones" / "p_fix.txt").read_text().splitlines()
    assert [ln.split()[0] for ln in lines] == ["1", "0"]
    assert [float(v) for v in lines[0].split()[1:]] == pytest.approx(
        [110 / 200, 50 / 100, 20 / 200, 20 / 100], abs=1e-6)
    rows = {r["id"]: r for r in jsonl(lab / "players.jsonl")}
    assert [b["color"] for b in rows["p_fix"]["stones"]] == ["yellow", "red"]
    assert "stones" not in rows["p_plain"]
    assert not (lab / "stones" / "p_plain.txt").exists()
    (fix,) = jsonl(out / "players" / "stonefix" / "stonefix.jsonl")
    assert fix["id"] == "p_fix"
    assert (out / "players" / "stonefix" / "sheet-01.jpg").stat().st_size > 0


def test_a_fix_to_no_stones_is_an_empty_stone_label(tmp_path, monkeypatch):
    api = FakeApi([], page=500)
    player_item(api, "p_clear", [("u1", {**play_ans(), "stones": []})],
                stones=stone_fix((101, 40, 121, 60, "red")))
    code, out = run(api, tmp_path, monkeypatch, "--kind", "player")
    assert (out / "players" / "labelled" / "stones" / "p_clear.txt").read_text() == ""


def test_a_re_export_leaves_no_stone_fix_from_an_earlier_run(tmp_path, monkeypatch):
    api = FakeApi([], page=500)
    item = player_item(api, "p_fix", [("u1", {**play_ans(), "stones": stone_fix((10, 10, 20, 20, "red"))})])
    code, out = run(api, tmp_path, monkeypatch, "--kind", "player")
    assert (out / "players" / "labelled" / "stones" / "p_fix.txt").exists()
    # u1's answer is discarded and u2 answers without a fix.
    item["answers"] = [{**item["answers"][0], "discarded": True},
                       {**answer("p_fix", "u2", "player", play_ans()), "discarded": False}]
    code, out = run(api, tmp_path, monkeypatch, "--kind", "player")
    assert not (out / "players" / "labelled" / "stones" / "p_fix.txt").exists()
    assert not list((out / "players" / "stonefix").glob("sheet-*.jpg"))


def test_boxes_past_the_edge_are_clamped_and_slivers_dropped(tmp_path, monkeypatch):
    api = FakeApi([], page=500)
    player_item(api, "p_edge", [("u1", play_ans(pbox(-5, 10, 40, 120), pbox(300, 0, 310, 50)))])
    code, out = run(api, tmp_path, monkeypatch, "--kind", "player")
    (line,) = (out / "players" / "labelled" / "labels" / "p_edge.txt").read_text().splitlines()
    assert [float(v) for v in line.split()[1:]] == pytest.approx(
        [20 / 200, 55 / 100, 40 / 200, 90 / 100], abs=1e-6)


def test_cant_tell_and_retired_player_items_go_to_the_user(tmp_path, monkeypatch):
    api = FakeApi([], page=500)
    stones = [{"x0": 10, "y0": 10, "x1": 30, "y1": 20, "color": "red"},
              {"x0": 50, "y0": 10, "x1": 70, "y1": 20, "color": "yellow"}]
    starts = [{"x0": 100, "y0": 20, "x1": 140, "y1": 90, "conf": 0.8}]
    player_item(api, "p_ct", [("u1", play_ans(cant_tell=True))], stones=stones, starts=starts)
    player_item(api, "p_skip", [], state="retired", stones=stones, starts=starts)
    code, out = run(api, tmp_path, monkeypatch, "--kind", "player")
    dest = out / "players" / "to_user"
    assert (dest / "images" / "p_ct.jpg").exists() and (dest / "images" / "p_skip.jpg").exists()
    assert not list((out / "players" / "labelled" / "images").iterdir())
    blob = (dest / "index.html").read_text().split("const ITEMS = ", 1)[1].split(";\n", 1)[0]
    items = {i["stem"]: i for i in json.loads(blob)}
    assert [b[0] for b in items["p_ct"]["boxes"]] == [0, 1, 2]
    assert items["p_ct"]["boxes"][0][1:] == pytest.approx([20 / 200, 15 / 100, 20 / 200, 10 / 100])
    assert [b[0] for b in items["p_skip"]["boxes"]] == [0, 1, 2]
    assert '+player (H)' in (dest / "index.html").read_text()
    why = {r["id"]: r["why"] for r in jsonl(dest / "to_user.jsonl")}
    assert why == {"p_ct": "cant_tell", "p_skip": "skipped"}


def test_a_discarded_answer_is_ignored_and_its_item_is_not_written(tmp_path, monkeypatch):
    api = FakeApi([], page=500)
    player_item(api, "p_bad", [("u9", play_ans(pbox(20, 10, 60, 90)), True)])
    player_item(api, "p_redo", [("u9", play_ans(pbox(1, 1, 5, 5)), True),
                                ("u1", play_ans(pbox(20, 10, 60, 90)))])
    code, out = run(api, tmp_path, monkeypatch, "--kind", "player")
    lab = out / "players" / "labelled"
    assert not (lab / "images" / "p_bad.jpg").exists()
    assert not (out / "players" / "to_user" / "images" / "p_bad.jpg").exists()
    assert (lab / "labels" / "p_redo.txt").read_text().count("\n") == 1
    (row,) = jsonl(lab / "players.jsonl")
    assert row["members"] == [{"uid": "u1", "seconds": 5.0}]
    # nor does the discarded member show in the report
    m = json.loads((out / "members.json").read_text())
    assert "u9" not in m or m["u9"]["player"] == 0


def test_an_answer_without_the_discarded_key_is_live(tmp_path, monkeypatch):
    api = FakeApi([], page=500)
    item = player_item(api, "p_old", [("u1", play_ans(pbox(20, 10, 60, 90)))])
    del item["answers"][0]["discarded"]
    code, out = run(api, tmp_path, monkeypatch, "--kind", "player")
    assert (out / "players" / "labelled" / "labels" / "p_old.txt").exists()


def test_the_untouched_rate(tmp_path, monkeypatch):
    api = FakeApi([], users=[{"id": "u1", "email": "a@x.org"}, {"id": "u2", "email": "b@x.org"}],
                  page=500)
    s2 = [{"x0": 1, "y0": 1, "x1": 9, "y1": 9, "conf": 0.9}] * 2
    # u1 accepts everything, but answers a frame with no starts and no boxes (not counted
    # either way) and changes one
    player_item(api, "p_a", [("u1", play_ans(pbox(1, 1, 9, 9, 0), pbox(1, 1, 9, 9, 1),
                                             starts=["untouched", "untouched"]))], starts=s2)
    player_item(api, "p_b", [("u1", play_ans())])
    player_item(api, "p_f", [("u1", play_ans(pbox(2, 2, 9, 9, 0), starts=["changed"]))],
                starts=s2[:1])
    # u3 only ever sees empty frames: no rate
    player_item(api, "p_g", [("u3", play_ans())])
    # u2 moves one, draws one, and cannot tell once
    player_item(api, "p_c", [("u2", play_ans(pbox(2, 2, 9, 9, 0), pbox(1, 1, 9, 9, 1),
                                             starts=["changed", "untouched"]))], starts=s2)
    player_item(api, "p_d", [("u2", play_ans(pbox(1, 1, 9, 9), starts=["untouched"]))],
                starts=s2[:1])
    player_item(api, "p_e", [("u2", play_ans(cant_tell=True))])
    code, out = run(api, tmp_path, monkeypatch, "--kind", "player")
    m = json.loads((out / "members.json").read_text())
    assert m["u1"]["untouched_rate"] == 0.5 and m["u1"]["player"] == 3
    assert m["u3"]["untouched_rate"] is None and m["u3"]["player"] == 1
    assert m["u2"]["untouched_rate"] == 0.0 and m["u2"]["player"] == 3
    assert m["u2"]["email"] == "b@x.org" and m["u2"]["checks"] == {"n": 0, "agree": 0}


def test_member_report_untouched_rate_is_none_without_player_answers():
    items = [{"id": "b", "kind": "box", "reference": None,
              "answers": [answer("b", "u1", "box", box_ans(box(1, 1, 5, 5)))]}]
    assert exp.member_report(items, [])["u1"]["untouched_rate"] is None


def test_the_spot_check_is_five_percent_rounded_up(tmp_path, monkeypatch):
    import math
    api = FakeApi([], users=[{"id": "u1", "email": "a@x.org"}], page=500)
    for i in range(41):
        player_item(api, f"p_{i:02d}", [("u1", play_ans(pbox(20, 10, 60, 90)))],
                    stones=[{"x0": 10, "y0": 10, "x1": 30, "y1": 20, "color": "red"}])
    code, out = run(api, tmp_path, monkeypatch, "--kind", "player")
    spot = out / "players" / "spotcheck"
    drawn = jsonl(spot / "spotcheck.jsonl")
    assert len(drawn) == math.ceil(0.05 * 41) == 3
    assert all(r["email"] == "a@x.org" for r in drawn)
    assert [p.name for p in sorted(spot.glob("sheet-*.jpg"))] == ["sheet-01.jpg"]
    from PIL import Image
    assert Image.open(spot / "sheet-01.jpg").width > 400


def test_a_spot_check_over_sixteen_makes_a_second_sheet(tmp_path, monkeypatch):
    api = FakeApi([], page=500)
    for i in range(330):                                    # 5% of 330 -> 17 tiles
        player_item(api, f"p_{i:03d}", [("u1", play_ans())], w=40, h=20)
    code, out = run(api, tmp_path, monkeypatch, "--kind", "player")
    spot = out / "players" / "spotcheck"
    assert len(jsonl(spot / "spotcheck.jsonl")) == 17
    assert [p.name for p in sorted(spot.glob("sheet-*.jpg"))] == ["sheet-01.jpg", "sheet-02.jpg"]


def test_the_spot_check_leans_on_the_members_who_change_nothing():
    heavy = exp.spot_weight(1.0)
    assert heavy == 4.0 and exp.spot_weight(0.0) == 1.0 and exp.spot_weight(None) == 1.0
    rates = {"stamp": 1.0, "careful": 0.0}
    items = [{"id": f"{u}{i}", "uid": u} for u in rates for i in range(200)]
    picks = [exp.spot_pick(items, 40, lambda r: rates[r["uid"]], seed=s) for s in range(20)]
    stamped = sum(r["uid"] == "stamp" for p in picks for r in p) / (20 * 40)
    assert 0.65 < stamped < 0.9                              # 4 : 1 is 0.8, no replacement dips it


def test_the_spot_check_is_the_same_every_time(tmp_path, monkeypatch):
    def once(sub):
        api = FakeApi([], page=500)
        for i in range(60):
            player_item(api, f"p_{i:02d}", [("u1", play_ans(pbox(20, 10, 60, 90)))])
        sub.mkdir()
        return run(api, sub, monkeypatch, "--kind", "player")[1]
    a, b = once(tmp_path / "a"), once(tmp_path / "b")
    assert [r["id"] for r in jsonl(a / "players" / "spotcheck" / "spotcheck.jsonl")] == \
        [r["id"] for r in jsonl(b / "players" / "spotcheck" / "spotcheck.jsonl")]


def test_player_is_not_read_unless_asked_for(tmp_path, monkeypatch):
    api = FakeApi([], page=500)
    player_item(api, "p_one", [("u1", play_ans())])
    code, out = run(api, tmp_path, monkeypatch)
    assert not (out / "players").exists()
    assert not any("kind=player" in u for u, _ in api.calls)


def test_player_images_are_fetched_without_the_admin_token(tmp_path, monkeypatch):
    api = FakeApi([], page=500)
    player_item(api, "p_one", [("u1", play_ans())])
    run(api, tmp_path, monkeypatch, "--kind", "player")
    assert all(auth is None for url, auth in api.calls if "bucket.test" in url)


def test_an_unreadable_player_image_is_skipped_and_said(tmp_path, monkeypatch, capsys):
    api = FakeApi([], page=500)
    player_item(api, "p_one", [("u1", play_ans())])
    player_item(api, "p_two", [("u1", play_ans())])
    real = api.__call__

    def flaky(req, timeout=None):
        if "p_one" in req.full_url and req.full_url.startswith("https://bucket.test"):
            raise OSError("boom")
        return real(req, timeout)
    monkeypatch.setenv("ADMIN_TOKEN", "t")
    out = tmp_path / "o"
    assert exp.main(["--out", str(out), "--kind", "player"], opener=flaky) == 1
    assert "p_one" in capsys.readouterr().err
    assert not (out / "players" / "labelled" / "images" / "p_one.jpg").exists()
    assert (out / "players" / "labelled" / "images" / "p_two.jpg").exists()
