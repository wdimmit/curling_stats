"""Choosing the hard batch's frames for player labels from the miner's candidates."""

import importlib.util
from collections import Counter
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pm = _load("player_mine")

KINDS = ("lost_no_pose", "crouched", "edge", "ordinary")


def _cands(videos=40, per=8, kinds=KINDS, sheets=4):
    """``per`` candidates of each kind in each video, a minute apart."""
    out = []
    for v in range(videos):
        for k, kind in enumerate(kinds):
            for i in range(per):
                out.append({"video": f"v{v:02d}", "t": 1000.0 + 60.0 * (i * len(kinds) + k),
                            "kind": kind, "sheet": v % sheets + 1})
    return out


def test_caps_each_kind_per_video():
    # 10 videos cannot fill 300: each gives 3 of each kind and no more
    got = pm.pick(_cands(videos=10), set(), per_kind_per_video=3, total=300)
    per = Counter((c["video"], c["kind"]) for c in got)
    assert max(per.values()) == 3
    assert sum(c["kind"] != "ordinary" for c in got) == 90


def test_reaches_the_total_with_about_a_fifth_ordinary():
    got = pm.pick(_cands(), set(), total=300)
    assert len(got) == 300
    kinds = Counter(c["kind"] for c in got)
    assert kinds["ordinary"] == 60
    # the hard kinds share the rest evenly when all have plenty
    assert {kinds[k] for k in ("lost_no_pose", "crouched", "edge")} == {80}


def test_short_of_hard_cases_keeps_ordinary_at_a_fifth():
    cands = [c for c in _cands() if c["kind"] in ("ordinary", "edge")]
    cands = [c for c in cands if c["kind"] == "ordinary" or c["video"] < "v10"]
    got = pm.pick(cands, set(), total=300)
    kinds = Counter(c["kind"] for c in got)
    assert kinds["edge"] == 30            # 10 videos x 3
    assert kinds["ordinary"] == 8         # a fifth of 38, not the 60 a full batch takes


def test_never_picks_an_excluded_video():
    excluded = {f"v{v:02d}" for v in range(0, 40, 3)}
    got = pm.pick(_cands(), excluded, total=300)
    assert got
    assert not {c["video"] for c in got} & excluded


def test_spreads_over_videos_before_doubling_up():
    # 30 frames from 40 videos: no video gives two
    got = pm.pick(_cands(), set(), total=30)
    assert len(got) == 30
    assert max(Counter(c["video"] for c in got).values()) == 1


def test_spreads_over_sheets():
    # 4 sheets of 10 videos; 8 frames take two from each sheet
    got = pm.pick(_cands(), set(), total=8)
    assert set(Counter(c["sheet"] for c in got).values()) == {2}


def test_frames_close_in_time_count_once():
    cands = [{"video": "a", "t": 100.0 + i, "kind": "edge"} for i in range(5)]
    cands += [{"video": "a", "t": 100.5 + i, "kind": "crouched"} for i in range(5)]
    got = pm.pick(cands, set(), total=10)
    ts = sorted(c["t"] for c in got)
    assert len(got) == 1
    assert all(b - a >= pm.MIN_GAP_S for a, b in zip(ts, ts[1:]))


def test_is_stable_for_a_seed():
    cands = _cands()
    a = pm.pick(cands, set(), total=120, seed=3)
    b = pm.pick(list(reversed(cands)), set(), total=120, seed=3)
    c = pm.pick(cands, set(), total=120, seed=4)
    assert a == b
    assert a != c


def test_unknown_kind_is_refused():
    with pytest.raises(ValueError):
        pm.pick([{"video": "a", "t": 1.0, "kind": "standing"}], set())


def _box(x0, y0, x1, y1):
    return {"x0": x0, "y0": y0, "x1": x1, "y1": y1, "conf": 0.5}


def test_classify_a_frame_by_its_pose_boxes():
    w, h, ppm = 300, 1000, 60.0
    assert pm.classify([], w, h, ppm) is None
    assert pm.classify([_box(100, 400, 140, 520)], w, h, ppm) == "ordinary"     # tall
    assert pm.classify([_box(100, 400, 150, 450)], w, h, ppm) == "crouched"     # small, square
    assert pm.classify([_box(100, 400, 200, 500)], w, h, ppm) == "ordinary"     # square, too big
    assert pm.classify([_box(0, 400, 40, 520)], w, h, ppm) == "edge"
    assert pm.classify([_box(0, 400, 40, 440), _box(100, 400, 140, 520)], w, h, ppm) == "edge"
    assert pm.classify([_box(0, 400, 40, 520), _box(100, 400, 150, 450)], w, h, ppm) == "crouched"


def test_lost_stones_are_the_ones_a_later_read_finds_again():
    red = {"color": "red", "x": 0.5, "y": 1.0}
    gone = {"color": "yellow", "x": -1.0, "y": 0.0}
    end = {"shots": [
        {"t_rest_s": 100.0, "t_enter_s": 95.0, "stones": [red, gone]},
        {"t_rest_s": 130.0, "t_enter_s": 106.0, "stones": [],
         "house_delta": {"removed": [red, gone]}},
        {"t_rest_s": 160.0, "t_enter_s": 150.0, "stones": [dict(red, x=0.6)]},
    ]}
    got = pm.lost_stones(end)
    assert got == [((130.0, 142.0), red)]
    # the window stops when the next rock arrives
    end["shots"][2]["t_enter_s"] = 135.0
    assert pm.lost_stones(end)[0][0] == (130.0, 135.0)


def test_stem_carries_video_panel_time_and_kind():
    assert pm.stem_for("2Cp3bNjAEWg", "top", 14450.0, "edge") == "2Cp3bNjAEWg_t_014450_00_edge"


def test_exclude_file_reads_ids_and_skips_comments(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("# harness\nabc -f3R1D3bQ0E  # one\n\n_OGoqF5W9-I\n")
    assert pm.read_exclude([f]) == {"abc", "-f3R1D3bQ0E", "_OGoqF5W9-I"}
