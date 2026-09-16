"""Finding where a composite's cameras fell out of sync.

The four sources in the composite frame are not guaranteed to show the same
instant, and `split_coverage --dump-refused` writes every crossing both a side
camera and a panel timed. This turns those pairings into a verdict per
recording.
"""

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "sync_report",
    Path(__file__).resolve().parents[1] / "scripts" / "ds13" / "sync_report.py")
sync_report = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sync_report)


def series(*groups):
    """`groups` are (count, delta) runs, laid out one second apart."""
    out, t = [], 0.0
    for n, d in groups:
        for _ in range(n):
            out.append({"t_hog": t, "delta": d})
            t += 1.0
    return out


class TestFindStep:
    def test_a_steady_offset_is_not_a_step(self):
        assert sync_report.find_step(series((20, 0.205))) is None

    def test_a_video_in_sync_is_not_a_step(self):
        assert sync_report.find_step(series((20, 0.01))) is None

    def test_a_real_step_is_found_where_it_happens(self):
        rows = series((11, -0.90), (13, -0.07))
        got = sync_report.find_step(rows)
        assert got is not None
        t, before, after = got
        assert t == pytest.approx(11.0)
        assert before == pytest.approx(-0.90, abs=0.02)
        assert after == pytest.approx(-0.07, abs=0.02)

    def test_it_is_a_changepoint_not_a_median_difference(self):
        """Maximising |median(before) - median(after)| looks equivalent and is
        not: on a bimodal series the mixed group's median sits at the edge of
        one cluster, so the score keeps climbing past the real change. That put
        AEqLTgM25Tc's step at t=12617 when the series steps at 10196."""
        rows = series((11, -0.90), (13, -0.07))
        t, _b, _a = sync_report.find_step(rows)
        worst = None
        import statistics
        for i in range(4, len(rows) - 3):
            a = statistics.median(r["delta"] for r in rows[:i])
            b = statistics.median(r["delta"] for r in rows[i:])
            if worst is None or abs(b - a) > worst[1]:
                worst = (rows[i]["t_hog"], abs(b - a))
        assert t == pytest.approx(11.0)
        assert worst[0] != pytest.approx(11.0), (
            "the naive rule happened to agree; pick a harder fixture")

    def test_a_small_wobble_is_not_a_step(self):
        assert sync_report.find_step(series((10, 0.02), (10, 0.12))) is None

    def test_too_few_crossings_to_judge(self):
        assert sync_report.find_step(series((3, -0.9), (3, 0.0))) is None
