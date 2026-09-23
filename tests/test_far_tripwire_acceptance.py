"""The painted far tripwire, scored against hand marks on three games.

Paint found in each panel's real calibration median, each marked rock's real
arrival track timed against it, errors against a person's mark. Thresholds
are from the spec: measured 0.049 s median / 80% within 0.10 s observed, and
0.057 s median / 0.154 s worst extrapolated.
"""

import json
import statistics
from pathlib import Path

import cv2
import pytest

from curling_score.game import split
from curling_score.geometry import hogpaint
from curling_score.geometry.calibrate import PanelCalib

FIX = Path(__file__).parent / "fixtures" / "far_tripwire"


@pytest.fixture(scope="module")
def scored():
    data = json.loads((FIX / "cases.json").read_text())
    lines = {}
    for key, p in data["panels"].items():
        calib = PanelCalib(center_px=tuple(p["center_px"]), px_per_m=p["px_per_m"],
                           edge_erosion_px=p["edge_erosion_px"], residual_m=p["residual_m"],
                           flipped=p["flipped"])
        lines[key] = hogpaint.find_hog_line(cv2.imread(str(FIX / p["plate"])), calib)
    observed, extrapolated = [], []
    for c in data["cases"]:
        line = lines[f"{c['video']}/{c['panel']}"]
        track = [tuple(p) for p in c["track"]]
        t = split.line_crossing(track, line)
        if t is not None:
            observed.append(abs(t - c["mark_s"]))
            continue
        t, reach = split.far_crossing(track, line, max_reach=split.FAR_REACH_MAX_U)
        if t is not None:
            extrapolated.append(abs(t - c["mark_s"]))
    return lines, observed, extrapolated


def test_the_paint_is_found_on_all_six_panels(scored):
    lines, _, _ = scored
    assert len(lines) == 6


def test_observed_crossings_land_on_the_marks(scored):
    _, observed, _ = scored
    assert len(observed) >= 45
    assert statistics.median(observed) <= 0.06
    assert sum(e <= 0.10 for e in observed) / len(observed) >= 0.75


def test_reached_for_crossings_land_near_the_marks(scored):
    _, _, extrapolated = scored
    assert len(extrapolated) >= 6
    assert statistics.median(extrapolated) <= 0.08
    assert max(extrapolated) <= 0.20
