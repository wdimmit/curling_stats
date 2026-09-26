import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "placement_acceptance",
    Path(__file__).resolve().parents[1] / "scripts/doubles/placement_acceptance.py")
PA = importlib.util.module_from_spec(spec)
spec.loader.exec_module(PA)


MARKS = {"games": {"vid1": {"ends": [
    {"end": 1, "house_stone": "yellow", "guard_stone": "red", "first_thrower": "red", "power_play": None},
    {"end": 2, "house_stone": "red", "guard_stone": "yellow", "first_thrower": "yellow",
     "power_play": {"color": "red", "side": "left"}},
]}}}


def end(n, hammer, pp, first, shots=10):
    return {"number": n, "placement": {"hammer": hammer, "power_play": pp},
            "shots": [{"color": first, "missing": False, "color_inferred": False}]
                     + [{"color": "x", "missing": False, "color_inferred": False}] * (shots - 1)}


def doc(ends):
    return {"source": {"video_id": "vid1"}, "games": [{"index": 0, "ends": ends}]}


def test_a_perfect_read_scores_full_marks():
    got = PA.compare(MARKS, [doc([end(1, "yellow", None, "red"), end(2, "red", "left", "yellow")])])
    assert (got["ends"], got["hammer_ok"], got["power_play_ok"], got["first_ok"], got["ten_shots"]) \
        == (2, 2, 2, 2, 2)
    assert got["mismatches"] == []


def test_a_wrong_hammer_and_a_missed_power_play_are_reported():
    got = PA.compare(MARKS, [doc([end(1, "red", None, "red"), end(2, "red", None, "yellow", shots=11)])])
    assert (got["hammer_ok"], got["power_play_ok"], got["ten_shots"]) == (1, 1, 1)
    assert {m["end"] for m in got["mismatches"]} == {1, 2}


def test_an_end_with_no_placement_counts_as_a_miss():
    e = end(1, "yellow", None, "red")
    e["placement"] = None
    got = PA.compare(MARKS, [doc([e])])
    assert got["hammer_ok"] == 0 and got["mismatches"][0]["end"] == 1


def test_unmarked_videos_and_ends_are_ignored():
    got = PA.compare(MARKS, [{"source": {"video_id": "other"}, "games": [{"index": 0, "ends": [end(1, "red", None, "red")]}]}])
    assert got["ends"] == 0
