"""The listing of what the workers have saved under harvest/fp."""

import importlib.util
import json
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "fp_harvest_script", Path(__file__).resolve().parents[1] / "scripts" / "fp_harvest.py")
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)

TITLE = "10/20 - Sheet 5 - Tuesday Super League 2026-2027"
MODELS = {"rocks": "ds13e", "heads": "ds17b"}


def row(stem, *, video="vid1", models=MODELS, saved_at="2026-10-20T20:00:00+00:00",
        check="long.width", camera="long", title=TITLE):
    return {"stem": stem, "check": check, "camera": camera, "video_id": video,
            "title": title, "models": models, "saved_at": saved_at}


def write(cache, rows):
    by_video = {}
    for r in rows:
        by_video.setdefault(r["video_id"], []).append(r)
    for video, rs in by_video.items():
        d = cache / "harvest" / "fp" / video
        d.mkdir(parents=True)
        (d / "frames.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rs))
        for r in rs:
            (d / f"{r['stem']}.jpg").write_bytes(b"x" * 10)


def test_load_tags_each_row_with_its_cache(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    write(a, [row("s1")])
    write(b, [row("s2", video="vid2")])
    got = script.load([a, b, tmp_path / "missing"])
    assert sorted((r["stem"], r["cache"]) for r in got) == [("s1", str(a)), ("s2", str(b))]


def test_the_earlier_pile_wins_across_workers():
    rows = [
        dict(row("late1", saved_at="2026-10-21T08:00:00+00:00"), cache="/c2"),
        dict(row("early1", saved_at="2026-10-20T20:00:00+00:00"), cache="/c1"),
        dict(row("early2", saved_at="2026-10-20T20:00:01+00:00"), cache="/c1"),
        dict(row("late2", saved_at="2026-10-21T08:00:01+00:00"), cache="/c2"),
    ]
    kept, dupes = script.dedupe(rows)
    assert sorted(r["stem"] for r in kept) == ["early1", "early2"]
    assert sorted(r["stem"] for r in dupes) == ["late1", "late2"]


def test_a_new_model_on_the_same_video_is_not_a_duplicate():
    newer = {"rocks": "ds13f", "heads": "ds17b"}
    rows = [
        dict(row("old", saved_at="2026-10-20T20:00:00+00:00"), cache="/c1"),
        dict(row("new", models=newer, saved_at="2026-10-21T08:00:00+00:00"), cache="/c2"),
    ]
    kept, dupes = script.dedupe(rows)
    assert sorted(r["stem"] for r in kept) == ["new", "old"]
    assert dupes == []


def test_models_compare_regardless_of_key_order():
    rows = [
        dict(row("a", models={"x": 1, "y": 2}, saved_at="2026-10-20T20:00:00+00:00"), cache="/c1"),
        dict(row("b", models={"y": 2, "x": 1}, saved_at="2026-10-21T20:00:00+00:00"), cache="/c2"),
    ]
    kept, dupes = script.dedupe(rows)
    assert [r["stem"] for r in kept] == ["a"] and [r["stem"] for r in dupes] == ["b"]


def test_summary_counts_per_check_week_camera_and_league():
    rows = [
        row("a", check="long.width", camera="long"),
        row("b", check="overhead.wander", camera="overhead",
            saved_at="2026-10-27T20:00:00+00:00"),
        row("c", check="long.width", camera="long", title="No league here"),
    ]
    s = script.summary(rows)
    assert s["check"] == {"long.width": 2, "overhead.wander": 1}
    assert s["week"] == {"2026-W43": 2, "2026-W44": 1}
    assert s["camera"] == {"long": 2, "overhead": 1}
    assert s["league"] == {"Tuesday Super League 2026-2027": 2, "?": 1}


def test_league_is_the_part_after_the_last_dash():
    assert script.league(TITLE) == "Tuesday Super League 2026-2027"
    assert script.league("") == "?"
    assert script.league(None) == "?"


def test_list_prints_summary_disk_and_duplicates(tmp_path, capsys):
    a, b = tmp_path / "a", tmp_path / "b"
    write(a, [row("s1", saved_at="2026-10-20T20:00:00+00:00")])
    write(b, [row("s1", saved_at="2026-10-21T20:00:00+00:00")])
    assert script.main(["list", "--caches", str(a), str(b)]) == 0
    out = capsys.readouterr().out
    assert "long.width" in out and "Tuesday Super League 2026-2027" in out
    assert "2026-W43" in out
    assert str(a) in out and str(b) in out
    assert "1 duplicate" in out


def test_since_drops_older_rows(tmp_path, capsys):
    a = tmp_path / "a"
    write(a, [row("old", saved_at="2026-10-01T00:00:00+00:00"),
              row("new", video="vid2", saved_at="2026-10-20T00:00:00+00:00")])
    script.main(["list", "--since", "2026-10-10", "--caches", str(a)])
    out = capsys.readouterr().out
    assert "2026-W43" in out and "2026-W40" not in out
