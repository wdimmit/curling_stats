"""The one-off release set: which cached rocks are picked, the sprite, the item."""

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


rel = _load("label_releases")


def shot(number, delivery=True, t_release=10.0, color="red"):
    line = {"delivery": [[0.0, 34.7, 0.0], [1.0, 30.0, 0.1]] if delivery else None}
    return {"number": number, "color": color, "position": "lead", "label": f"rock {number}",
            "t_release_s": t_release, "line": line}


def timeline(ends):
    return {"games": [{"ends": [{"number": n, "shots": s} for n, s in ends]}]}


def test_candidates_skip_shots_without_a_delivery_line():
    tl = timeline([(1, [shot(1), shot(2, delivery=False), shot(3, t_release=None),
                        {"number": 4, "color": "red", "t_release_s": 5.0, "line": None},
                        {"number": 5, "color": "red", "t_release_s": 5.0}]),
                   (2, [shot(1, color="yellow")])])
    got = rel.candidates(tl, "s_a", "vid")
    assert [(c["end"], c["rock"]) for c in got] == [(1, 1), (2, 1)]
    assert got[0] == {"source_id": "s_a", "video_id": "vid", "end": 1, "rock": 1,
                      "color": "red", "position": "lead", "label": "rock 1"}
    assert got[1]["color"] == "yellow"


def _cands(source, n):
    return [{"source_id": source, "video_id": "v" + source, "end": 1 + i // 8, "rock": 1 + i % 8,
             "color": "red", "position": "lead", "label": "x"} for i in range(n)]


def test_pick_spreads_across_sources_and_is_repeatable():
    cands = _cands("s_a", 10) + _cands("s_b", 15) + _cands("s_c", 5)
    got = rel.pick(cands, 12, 7, set())
    assert len(got) == 12
    for s in ("s_a", "s_b", "s_c"):
        assert sum(1 for c in got if c["source_id"] == s) == 4
    assert rel.pick(cands, 12, 7, set()) == got
    assert rel.pick(cands, 12, 8, set()) != got
    # A short source runs out; the rest carry on.
    assert len(rel.pick(cands, 40, 7, set())) == 30


def test_pick_excludes_marked_rocks():
    cands = _cands("s_a", 4)
    got = rel.pick(cands, 10, 1, {("vs_a", 1, 1), ("vs_a", 1, 3)})
    assert sorted(c["rock"] for c in got) == [2, 4]


def test_marked_reads_every_rock_of_the_mark_files(tmp_path):
    (tmp_path / "s_a.json").write_text(json.dumps(
        {"video_id": "v1", "rocks": [{"end": 1, "rock": 6}, {"end": 2, "rock": 3}]}))
    (tmp_path / "remarks-2026-10-21.json").write_text("[]")
    assert rel.marked(tmp_path) == {("v1", 1, 6), ("v1", 2, 3)}


def test_sprite_layout():
    from PIL import Image
    paths = []
    import tempfile
    d = Path(tempfile.mkdtemp())
    for i in range(12):
        p = d / f"f{i}.jpg"
        Image.new("RGB", (20, 10), (i * 20, 0, 0)).save(p, "JPEG")
        paths.append(p)
    data, meta = rel.make_sprite(paths)
    assert meta == {"cols": 10, "fw": 20, "fh": 10, "n": 12}
    img = Image.open(io.BytesIO(data))
    assert img.size == (200, 20) and img.format == "JPEG"


def extracted(os_):
    return {"id": "x", "frames": [{"file": f"f{i}.jpg", "o": o, "yp": 1.0 + i}
                                  for i, o in enumerate(os_)],
            "video_id": "vid", "end": 3, "rock": 7, "color": "red", "position": "lead",
            "label": "lbl", "t_release_s": 12.5}


PICK = {"source_id": "s_a", "video_id": "vid", "title": "T", "end": 3, "rock": 7,
        "color": "red", "position": "lead", "label": "lbl"}
META = {"cols": 10, "fw": 560, "fh": 350, "n": 3}


def test_item_is_a_release_payload_with_its_origin():
    item = rel.item_for(PICK, extracted([0.0, 0.0333, 0.0667]), META, "releases-1")
    assert item["id"] == "r_vid_e03r07" and item["kind"] == "release"
    assert item["batch"] == "releases-1" and "reference" not in item
    p = item["payload"]
    assert (p["cols"], p["fw"], p["fh"], p["n"], p["fps"]) == (10, 560, 350, 3, 30.0)
    assert p["frames"] == [{"o": 0.0, "yp": 1.0}, {"o": 0.0333, "yp": 2.0}, {"o": 0.0667, "yp": 3.0}]
    assert p["origin"] == {"source_id": "s_a", "video_id": "vid", "title": "T", "end": 3,
                           "rock": 7, "color": "red", "position": "lead", "label": "lbl",
                           "t_release_s": 12.5}
    assert rel.ID_OK.match(item["id"])


def test_two_sources_of_one_video_get_different_ids():
    a = dict(PICK, source_id="s_a")
    b = dict(PICK, source_id="s_b")
    rel.assign_ids([a, b, dict(PICK, rock=8, source_id="s_a")])
    assert a["id"] == "r_vid_e03r07-gs_a" and b["id"] == "r_vid_e03r07-gs_b"
    item = rel.item_for(a, extracted([0.0]), dict(META, n=1), "b")
    assert item["id"] == "r_vid_e03r07-gs_a"


def test_check_reference_frame_is_the_nearest_o():
    ex = extracted([0.0, 0.1, 0.2, 0.3])
    assert rel.reference_for({"mark": "off", "o": 0.17}, ex) == {"mark": "off", "frame": 2}
    assert rel.reference_for({"mark": "off", "o": 5.0}, ex) == {"mark": "off", "frame": 3}
    assert rel.reference_for({"mark": "held", "o": None}, ex) == {"mark": "held", "frame": None}
    item = rel.item_for(PICK, ex, dict(META, n=4), "b", reference={"mark": "held", "frame": None})
    assert item["reference"] == {"mark": "held", "frame": None}


def test_checks_are_random_marked_rocks_with_a_cached_video(tmp_path):
    d = tmp_path / "marks"
    d.mkdir()
    rocks = [{"id": "a-e01r01", "end": 1, "rock": 1, "chosen": "random", "mark": "off", "o": 1.0},
             {"id": "a-e01r02", "end": 1, "rock": 2, "chosen": "hard", "mark": "off", "o": 1.0},
             {"id": "a-e01r03", "end": 1, "rock": 3, "chosen": "random", "mark": None, "o": None},
             {"id": "a-e01r04", "end": 1, "rock": 4, "chosen": "random", "mark": "held", "o": None}]
    (d / "s_a.json").write_text(json.dumps(
        {"source_id": "s_a", "video_id": "vid", "title": "T", "rocks": rocks}))
    (d / "s_b.json").write_text(json.dumps(
        {"source_id": "s_b", "video_id": "gone", "title": "T", "rocks": rocks}))
    got = rel.check_rocks(d, {"vid"}, limit=20)
    assert [(c["video_id"], c["rock"]) for c in got] == [("vid", 1), ("vid", 4)]
    assert got[0]["mark"] == {"mark": "off", "o": 1.0}
    assert len(rel.check_rocks(d, {"vid"}, limit=1)) == 1


def test_a_rock_that_fails_is_skipped_with_its_id_and_the_rest_go(tmp_path, monkeypatch, capsys):
    picks = [dict(PICK, rock=r) for r in (1, 2, 3)]
    (tmp_path / "picks.json").write_text(json.dumps({"picks": picks}))
    cache = tmp_path / "c"
    (cache / "videos").mkdir(parents=True)
    (cache / "videos" / "vid.mp4").write_bytes(b"")

    class Fake:
        OUT_W = 560

        def extract(self, video, tl, end, rock, dest, tag):
            if rock == 2:
                raise RuntimeError("no frames")
            from PIL import Image
            names = []
            for i in range(3):
                Image.new("RGB", (8, 4)).save(dest / f"{i}.jpg", "JPEG")
                names.append({"file": f"{i}.jpg", "o": i / 30, "yp": float(i)})
            return {"id": "x", "frames": names, "video_id": "vid", "end": end, "rock": rock,
                    "color": "red", "position": "lead", "label": "l", "t_release_s": 1.0}

    monkeypatch.setattr(rel, "_mark_release", lambda: Fake())
    monkeypatch.setattr(rel, "fetch_timeline", lambda *a, **k: {"games": []})
    monkeypatch.setattr(rel.label_publish, "publish",
                        lambda *a, **k: pytest.fail("published in a dry run"))
    out = tmp_path / "out"
    assert rel.main(["publish", "--picks", str(tmp_path / "picks.json"), "--caches", str(cache),
                     "--dry-run", "--out", str(out), "--scratch", str(tmp_path / "s"),
                     "--checks", str(tmp_path / "none")]) == 0
    names = sorted(p.name for p in out.iterdir() if p.is_file())
    assert names == ["r_vid_e03r01.jpg", "r_vid_e03r01.json", "r_vid_e03r03.jpg",
                     "r_vid_e03r03.json"]
    assert "r_vid_e03r02" in capsys.readouterr().err
    item = json.loads((out / "r_vid_e03r01.json").read_text())
    assert item["payload"]["n"] == 3 and item["payload"]["fw"] == 8


def test_a_real_run_sends_one_rock_at_a_time_and_survives_a_refused_one(tmp_path, monkeypatch,
                                                                         capsys):
    picks = [dict(PICK, rock=r) for r in (1, 2)]
    (tmp_path / "picks.json").write_text(json.dumps({"picks": picks}))
    cache = tmp_path / "c"
    (cache / "videos").mkdir(parents=True)
    (cache / "videos" / "vid.mp4").write_bytes(b"")

    class Fake:
        def extract(self, video, tl, end, rock, dest, tag):
            from PIL import Image
            Image.new("RGB", (8, 4)).save(dest / "0.jpg", "JPEG")
            return {"frames": [{"file": "0.jpg", "o": 0.0, "yp": 0.0}], "t_release_s": 1.0}

    sent = []

    def publish(base, token, items, images, **kw):
        (item,) = items
        if item["id"].endswith("r01"):
            raise OSError("refused")
        sent.append((token, item["id"], images[item["id"]][:2]))
        return {"opened": [item["id"]], "skipped": [], "missing_image": []}

    monkeypatch.setenv("ADMIN_TOKEN", "tok")
    monkeypatch.setattr(rel, "_mark_release", lambda: Fake())
    monkeypatch.setattr(rel, "fetch_timeline", lambda *a, **k: {})
    monkeypatch.setattr(rel.label_publish, "publish", publish)
    assert rel.main(["publish", "--picks", str(tmp_path / "picks.json"), "--caches", str(cache),
                     "--scratch", str(tmp_path / "s")]) == 0
    assert sent == [("tok", "r_vid_e03r02", b"\xff\xd8")]
    assert "r_vid_e03r01: skipped" in capsys.readouterr().err
    assert not list((tmp_path / "s" / "frames").iterdir())       # scratch is cleaned per rock
