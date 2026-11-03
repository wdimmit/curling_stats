"""The house-read harness: which stones a new read recovers, and whether the
prototype's recoveries still hold. Rows are what ``replay`` writes; the
replay itself is run with its heavy collaborators faked."""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name):
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rep = _load("house_read_report")


def stone(color, x, y):
    return {"color": color, "x": x, "y": y}


def row(shot, stones, held=None, color="red", end=1, missing=False, last=False):
    r = {"video": "v", "game": 0, "end": end, "shot": shot, "color": color,
         "missing": missing, "last": last, "t_rest": None if missing else 10.0 * shot,
         "stones": stones, "house_delta": None, "delivered_stone_index": None}
    if held is not None:
        r["held"] = held
    return r


def test_recovered_tags_put_back_and_seen():
    # The baseline had a yellow and a red in play at shot 1 and lost each at
    # shot 2; the new run has them back, the yellow from `held`.
    both = [stone("red", 0, 0), stone("yellow", 0.5, 0.5)]
    old = [row(1, both), row(2, [])]
    new = [row(1, both), row(2, both, held=[stone("yellow", 0.5, 0.5)])]
    got = rep.recovered(old, new)
    assert [(r["shot"], r["color"], r["x"], r["y"], r["how"]) for r in got] == [
        (2, "red", 0, 0, "seen"), (2, "yellow", 0.5, 0.5, "put back")]
    assert {"video", "game", "end"} <= set(got[0])


def test_a_stone_new_in_the_house_is_not_recovered():
    # The baseline never had the yellow at (1, 1) in play: a read that adds a
    # stone is not a read that gets one back.
    old = [row(1, [stone("red", 0, 0)]), row(2, [stone("red", 0, 0)])]
    new = [row(1, [stone("red", 0, 0)]),
           row(2, [stone("red", 0, 0), stone("yellow", 1, 1)])]
    assert rep.recovered(old, new) == []
    # Nor is anything at shot 1, with no row before it.
    assert rep.recovered([row(1, [])], [row(1, [stone("red", 0, 0)])]) == []


def test_a_stone_lost_at_one_shot_and_kept_counts_once():
    gone = [stone("red", 3, 3)]
    old = [row(1, gone), row(2, []), row(3, []), row(4, [])]
    new = [row(1, gone), row(2, gone), row(3, gone), row(4, gone)]
    got = rep.recovered(old, new)
    assert [(r["shot"], r["color"]) for r in got] == [(2, "red")]


def test_a_missing_row_before_recovers_nothing():
    old = [row(1, [], missing=True), row(2, [])]
    new = [row(1, [], missing=True), row(2, [stone("red", 0, 0)])]
    assert rep.recovered(old, new) == []
    assert rep.recovered([row(2, [])], [row(2, [stone("red", 0, 0)])]) == []


def test_ends_do_not_borrow_each_others_rows():
    old = [row(1, [stone("red", 0, 0)], end=1), row(1, [], end=2)]
    new = [row(1, [stone("red", 0, 0)], end=1), row(1, [stone("red", 0, 0)], end=2)]
    assert rep.recovered(old, new) == []


def test_moved_less_than_seen_m_is_not_recovered():
    first = row(1, [stone("red", 0, 0)])
    old = [first, row(2, [])]

    def after(stones):
        return [first, row(2, stones)]

    assert [r["how"] for r in rep.recovered(old, after([stone("red", 0.1, 0)]))] == ["seen"]
    # Further than SEEN_M from where it sat: a different stone, not that one.
    assert rep.recovered(old, after([stone("red", 0.2, 0)])) == []
    # Colour counts: a yellow where a red sat is a new stone.
    assert rep.recovered(old, after([stone("yellow", 0, 0)])) == []
    # A settled stone the read moves by under SEEN_M was never lost.
    assert rep.recovered([first, row(2, [stone("red", 0.1, 0)])],
                         after([stone("red", 0, 0)])) == []


def test_recovered_matches_shots_not_rest_times():
    old = [row(1, [stone("red", 0, 0), stone("yellow", 1, 1)]), row(2, [stone("red", 0, 0)])]
    new = [row(1, [stone("red", 0, 0), stone("yellow", 1, 1)]),
           row(2, [stone("red", 0, 0), stone("yellow", 1, 1)])]
    new[1]["t_rest"] += 0.4  # another model's run settles the shot a little later
    assert [r["color"] for r in rep.recovered(old, new)] == ["yellow"]
    assert rep.unmatched(old, new) == 0
    assert rep.unmatched(old, new + [row(3, [stone("red", 3, 3)])]) == 1


def test_recovered_skips_missing_and_unmatched_rows():
    old = [row(1, [], missing=True), row(2, [stone("red", 0, 0)])]
    new = [row(1, [stone("red", 5, 5)], missing=True),
           row(2, [stone("red", 0, 0)]), row(3, [stone("red", 9, 9)])]
    assert rep.recovered(old, new) == []


def _write(tmp_path, name, rows):
    p = tmp_path / name
    p.write_text(json.dumps(rows))
    return str(p)


def _pair(tmp_path, tag):
    """The baseline had the red and a yellow at shot 1 and lost the yellow at
    shot 2; the new run has it."""
    both = [stone("red", 0, 0), stone("yellow", 1, 1)]
    old = _write(tmp_path, f"{tag}-old.json", [row(1, both), row(2, [stone("red", 0, 0)])])
    new = _write(tmp_path, f"{tag}-new.json", [row(1, both), row(2, both)])
    return old, new


def _nothing(tmp_path):
    rows = [row(1, [stone("red", 0, 0)]), row(2, [stone("red", 0, 0)])]
    return _write(tmp_path, "o.json", rows), _write(tmp_path, "n.json", rows)


def test_expect_reports_still_lost(tmp_path, capsys):
    p_old, p_new = _pair(tmp_path, "proto")
    # This comparison recovers nothing.
    old, new = _nothing(tmp_path)
    code = rep.compare([old], [new], expect=([p_old], [p_new]))
    out = capsys.readouterr().out
    assert code == 1
    assert "STILL LOST" in out and "yellow" in out and "not in the new run's row" in out


def test_expect_passes_when_recovered(tmp_path, capsys):
    p_old, p_new = _pair(tmp_path, "proto")
    old, new = _pair(tmp_path, "cur")
    # The new run put it back 0.1 m from where the prototype had it.
    new_rows = json.loads(Path(new).read_text())
    new_rows[1]["stones"][1] = stone("yellow", 1.1, 1)
    new_rows[1]["held"] = [stone("yellow", 1.1, 1)]
    Path(new).write_text(json.dumps(new_rows))
    assert rep.compare([old], [new], expect=([p_old], [p_new])) == 0
    out = capsys.readouterr().out
    assert "STILL LOST" not in out
    # Both runs' counts on one line, by the same rule.
    assert ("recovered, this run: 1 stones (1 put back, 0 seen)" in out
            and "prototype pair: 1 stones (0 put back, 1 seen)" in out)
    assert [l for l in out.splitlines() if l.startswith("recovered")][0].count("|") == 1


def test_expect_judges_the_new_rows_not_the_recovered_list(tmp_path, capsys):
    # The baseline already sees the yellow, so nothing is recovered here, but
    # nothing is lost either.
    p_old, p_new = _pair(tmp_path, "proto")
    both = [row(1, [stone("red", 0, 0), stone("yellow", 1.05, 1)]),
            row(2, [stone("red", 0, 0), stone("yellow", 1.05, 1)])]
    old, new = _write(tmp_path, "o.json", both), _write(tmp_path, "n.json", both)
    assert rep.compare([old], [new], expect=([p_old], [p_new])) == 0
    out = capsys.readouterr().out
    assert "recovered, this run: 0 stones" in out and "STILL LOST" not in out


def test_expect_row_missing_from_the_new_run_is_still_lost(tmp_path, capsys):
    p_old, p_new = _pair(tmp_path, "proto")
    old = _write(tmp_path, "o.json", [row(3, [])])
    new = _write(tmp_path, "n.json", [row(3, [])])
    assert rep.compare([old], [new], expect=([p_old], [p_new])) == 1
    assert "no row in the new run" in capsys.readouterr().out
    new = _write(tmp_path, "n2.json", [row(1, []), row(2, [], missing=True)])
    assert rep.compare([old], [new], expect=([p_old], [p_new])) == 1
    assert "row is missing" in capsys.readouterr().out


def test_expect_wrong_colour_or_shot_is_still_lost(tmp_path, capsys):
    p_old, p_new = _pair(tmp_path, "proto")
    old, new = _nothing(tmp_path)
    new_rows = json.loads(Path(new).read_text())
    new_rows[1]["stones"].append(stone("red", 1, 1))  # right place, wrong colour
    Path(new).write_text(json.dumps(new_rows))
    assert rep.compare([old], [new], expect=([p_old], [p_new])) == 1
    assert "STILL LOST" in capsys.readouterr().out


def test_compare_without_expect_returns_zero(tmp_path, capsys):
    old, new = _pair(tmp_path, "cur")
    assert rep.compare([old], [new]) == 0
    out = capsys.readouterr().out
    assert "recovered, this run: 1" in out and "prototype" not in out


def test_old_rows_without_held_load(tmp_path):
    old, new = _pair(tmp_path, "cur")
    assert "held" not in json.loads(Path(old).read_text())[0]
    assert rep.compare([old], [new]) == 0


# --- replay, with its collaborators faked ------------------------------------


class _Frames(list):
    def __init__(self, stones=(), players=()):
        super().__init__(stones)
        self.players = tuple(players)


def _fake_replay(monkeypatch, tmp_path):
    """Fake what ``replay`` calls, for one game of one end; returns what the
    fakes saw. The test sets ``from_deliveries`` itself."""
    seen = SimpleNamespace(detectors=[], from_args=None)

    class FakeDetector:
        def __init__(self, weights, **kw):
            self.weights = weights
            self.model = SimpleNamespace(overrides={})
            seen.detectors.append(self)

    frames = [(0.0, _Frames()), (1.0, _Frames(players=[(0, 0, 1, 1)])),
              (2.0, _Frames(players=[(0, 0, 1, 1)]))]
    setup = SimpleNamespace(view_x_limit_m=1.0, view_y_min_m=0.0)

    def detect_end(path, s, end, fps, detector, from_s=None):
        seen.end_detector = detector
        return list(frames)

    def detect_span(path, s, a, b, fps, detector):
        seen.span_detector = detector
        return []

    monkeypatch.setattr(rep, "setups_for",
                        lambda video, vid, root: ({}, SimpleNamespace(top=0, bottom=1)))
    monkeypatch.setattr(rep.proxy, "strip_rect", lambda a, b: None)
    monkeypatch.setattr(rep.proxy, "proxy_path", lambda vid, strip, root: "p.mp4")
    monkeypatch.setattr(rep.analyze_mod, "_proxy_setups",
                        lambda setups, strip: {"top": setup, "bottom": setup})
    monkeypatch.setattr(rep.analyze_mod, "run_up_from", lambda prev, start: 0.0)
    monkeypatch.setattr(rep.yolo, "YoloDetector", FakeDetector)
    monkeypatch.setattr(rep.sequence, "detect_end", detect_end)
    monkeypatch.setattr(rep.sequence, "detect_span", detect_span)
    monkeypatch.setattr(rep.D, "find_deliveries", lambda *a, **k: [])
    monkeypatch.setattr(rep.secondpass, "gaps_to_search", lambda *a: [])
    monkeypatch.setattr(rep.secondpass, "search", lambda *a: [])
    monkeypatch.setattr(rep.release, "find_and_pair",
                        lambda *a, **k: (None, {}, []))
    monkeypatch.setattr(rep.fit, "drop_clearing", lambda *a: [])
    monkeypatch.setattr(rep.fit, "released_ids", lambda *a: set())
    monkeypatch.setattr(rep.fit, "paired_ids", lambda *a: set())
    monkeypatch.setattr(rep.fit, "fit_end", lambda ds, paired: ds)

    tl = tmp_path / "t.json"
    tl.write_text(json.dumps({
        "source": {"video_id": "vid"},
        "games": [{"index": 0, "ends": [
            {"number": 1, "house": "top", "start_s": 0.0, "end_s": 5.0, "shots": []}]}]}))
    seen.timeline = str(tl)
    seen.frames = frames
    return seen


def _shot(number):
    return SimpleNamespace(number=number, missing=False, color="red", t_rest_s=3.0,
                           stones=[SimpleNamespace(color="red", x_m=1.0, y_m=2.0)],
                           house_delta=None, delivered_stone_index=None)


def test_replay_keeps_players_and_records_held(monkeypatch, tmp_path, capsys):
    def from_deliveries(frames, kw):
        # Put back one stone for shot 2 only.
        kw["held"].append({"shot": 2, "color": "red", "x": 1.0004, "y": 2.0})
        return [_shot(1), _shot(2)]

    seen = _fake_replay(monkeypatch, tmp_path)
    # from_deliveries accepts held, so the script must pass it.
    monkeypatch.setattr(rep.shots_mod, "from_deliveries", _accepting(seen, from_deliveries))
    rows = rep.replay(seen.timeline, tmp_path, "far.pt")
    frames, kw = seen.from_args
    assert all(a is b for (_, a), (_, b) in zip(frames, seen.frames))  # players survive
    assert kw["house_frames"] is frames
    assert [r["held"] for r in rows] == [[], [stone("red", 1.0, 2.0)]]
    assert "frames with players: 2 of 3" in capsys.readouterr().out


def _accepting(seen, from_deliveries):
    def fake(deliveries, frames, thrown_by=None, house_frames=None, held=None):
        seen.from_args = (frames, {"house_frames": house_frames, "held": held})
        return from_deliveries(frames, {"held": held})
    return fake


def test_replay_without_held_in_from_deliveries_gives_empty_held(monkeypatch, tmp_path):
    seen = _fake_replay(monkeypatch, tmp_path)

    def no_held(deliveries, frames, thrown_by=None, house_frames=None):
        seen.from_args = (frames, {"house_frames": house_frames})
        return [_shot(1)]

    monkeypatch.setattr(rep.shots_mod, "from_deliveries", no_held)
    rows = rep.replay(seen.timeline, tmp_path, "far.pt")
    assert rows[0]["held"] == []


def test_house_detector_reads_the_receiving_panel_only(monkeypatch, tmp_path):
    seen = _fake_replay(monkeypatch, tmp_path)
    monkeypatch.setattr(rep.shots_mod, "from_deliveries", _accepting(seen, lambda f, kw: []))
    rep.replay(seen.timeline, tmp_path, "far.pt", house_weights="house.pt")
    assert seen.end_detector.weights == "house.pt"
    assert seen.span_detector.weights == "far.pt"
    assert len(seen.detectors) == 2


def test_equal_house_weights_reuse_one_detector(monkeypatch, tmp_path):
    seen = _fake_replay(monkeypatch, tmp_path)
    monkeypatch.setattr(rep.shots_mod, "from_deliveries", _accepting(seen, lambda f, kw: []))
    rep.replay(seen.timeline, tmp_path, "far.pt", house_weights="far.pt")
    assert len(seen.detectors) == 1 and seen.end_detector is seen.span_detector
