"""scripts/compare_timelines.py: how far a live timeline is from the VOD's."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import compare_timelines as ct  # noqa: E402


def shot(n, color, x, y, missing=False):
    return {"number": n, "color": color, "missing": missing,
            "stones": [] if missing else [{"color": color, "x": x, "y": y}],
            "delivered_stone_index": None if missing else 0}


def doc(*games):
    return {"games": [{"index": i, "start_s": g[0], "end_s": g[1], "final": g[3],
                       "ends": g[2]} for i, g in enumerate(games)]}


def end(n, house, shots, score=None):
    return {"number": n, "house": house, "shots": shots, "score": score}


def test_identical_timelines_match_everywhere():
    a = doc((0, 900, [end(1, "top", [shot(1, "red", 0, 0.5), shot(2, "yellow", 0.2, 1.0)],
                          {"red": 1, "yellow": 0})], {"red": 1, "yellow": 0}))
    r = ct.compare(a, a)
    assert (r["shots_matched"], r["shots_total"]) == (2, 2)
    assert r["ends_matched"] == r["ends_total"] == 1
    assert r["scores_equal"] == 1 and r["finals_equal"] == 1


def test_a_stone_resting_more_than_ten_cm_away_is_a_mismatch():
    a = doc((0, 900, [end(1, "top", [shot(1, "red", 0, 0.5)])], None))
    b = doc((0, 900, [end(1, "top", [shot(1, "red", 0, 0.62)])], None))
    r = ct.compare(a, b)
    assert r["shots_matched"] == 0 and r["shots_total"] == 1
    assert r["mismatches"][0]["why"] == "rest 12.0 cm apart"


def test_a_missing_shot_and_a_different_colour_are_mismatches():
    a = doc((0, 900, [end(1, "top", [shot(1, "red", 0, 0), shot(2, "yellow", 0, 1)])], None))
    b = doc((0, 900, [end(1, "top", [shot(1, "yellow", 0, 0)])], None))
    r = ct.compare(a, b)
    assert r["shots_matched"] == 0 and r["shots_total"] == 2
    assert [m["why"] for m in r["mismatches"]] == ["colour red vs yellow", "only in A"]


def test_games_pair_by_overlap_and_an_extra_end_is_counted():
    a = doc((1000, 3000, [end(1, "top", []), end(2, "bottom", [])], None))
    b = doc((1010, 2990, [end(1, "top", [])], None))
    r = ct.compare(a, b)
    assert r["games_paired"] == 1
    assert (r["ends_matched"], r["ends_total"]) == (1, 2)
