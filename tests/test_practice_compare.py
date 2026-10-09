"""scripts/practice/compare_throws.py: a practice replay against the end pipeline."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "practice"))
import compare_throws as ct  # noqa: E402


def shot(color, t_rest, t_release=None, x=0.0, y=0.0, reason="rest", split=None,
         broom=True, miss=None):
    return {"color": color, "missing": False, "reason": reason, "t_rest_s": t_rest,
            "t_release_s": t_release, "stones": [{"color": color, "x": x, "y": y}],
            "delivered_stone_index": 0, "long_split_s": split,
            "target_broom": {"x": 0.0, "y": 0.0} if broom else None,
            "line": {"at_broom": {"miss_m": miss}} if miss is not None else None}


def doc(house, *shots):
    return {"games": [{"ends": [{"house": house, "shots": list(shots)}]}]}


def throw(color, t_rest, *, house="top", x=0.0, y=0.0, arrived=True, t_release=None,
          split=None, broom=True, miss=None, latency=10.0, source="overhead"):
    return {"id": f"t_{t_rest or t_release}", "color": color, "house": house,
            "t_rest_s": t_rest, "t_release_s": t_release, "arrived": arrived,
            "release_source": source,
            "rest": {"x": x, "y": y} if arrived else None, "split_s": split,
            "broom": {"x": 0.0, "y": 0.0} if broom else None,
            "line": {"miss_m": miss} if miss is not None else None, "latency_s": latency}


def test_reference_keeps_the_window_and_skips_placeholders():
    d = doc("top", shot("red", 120.0, t_release=100.0), shot("red", 400.0, t_release=380.0),
            {"missing": True, "color": "yellow"})
    refs = ct.reference(d, 50.0, 300.0)
    assert [(r["color"], r["t_rest_s"], r["house"]) for r in refs] == [("red", 120.0, "top")]


def test_a_throw_matches_a_shot_of_its_colour_house_time_and_place():
    refs = ct.reference(doc("top", shot("red", 120.0, t_release=100.0, x=0.1, y=-0.4)),
                        0.0, 1000.0)
    pairs, false, missed = ct.match([throw("red", 121.5, x=0.15, y=-0.38)], refs)
    assert len(pairs) == 1 and false == [] and missed == []
    pairs, false, missed = ct.match([throw("red", 121.5, x=1.5, y=1.0)], refs)
    assert pairs == [] and len(false) == 1 and len(missed) == 1
    pairs, _, _ = ct.match([throw("red", 121.5, house="bottom")], refs)
    assert pairs == []


def test_a_throw_that_never_arrived_matches_a_hogged_shot_by_its_release():
    refs = ct.reference(doc("top", shot("yellow", None, t_release=100.0, reason="hogged")),
                        0.0, 1000.0)
    pairs, _, _ = ct.match([throw("yellow", None, arrived=False, t_release=101.0)], refs)
    assert len(pairs) == 1


def test_the_summary_counts_and_measures():
    refs = ct.reference(doc("top",
                            shot("red", 120.0, t_release=100.0, x=0.1, y=-0.4, split=13.8,
                                 miss=-0.10),
                            shot("yellow", 180.0, t_release=160.0, broom=False)), 0.0, 1000.0)
    ths = [throw("red", 120.5, x=0.13, y=-0.40, split=13.85, miss=-0.12, latency=12.0),
           throw("red", 300.0, x=-1.0, y=1.0, latency=20.0, source=None)]
    pairs, false, missed = ct.match(ths, refs)
    s = ct.summarize(pairs, false, missed, ths)
    assert s["reference_arrived"] == 2 and s["matched_arrived"] == 1
    assert s["recall_arrived"] == 0.5 and s["false_throws"] == 1
    assert s["rest_cm"]["p50"] == 3.0
    assert s["split_s"]["p50"] == 0.05
    assert s["miss_cm"]["p50"] == 2.0
    assert s["broom"] == {"both": 1, "neither": 0, "throw_only": 0, "reference_only": 0}
    assert s["latency_s"] == {"n": 2, "p50": 12.0, "p90": 20.0, "max": 20.0}
    assert s["unreleased"] == {"n": 1, "matched": 0}
