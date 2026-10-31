"""Where the slide was aimed and where the release sent the rock, at the broom
(frontend/core/slide.mjs), in node."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / "frontend/core/slide.mjs"
node = shutil.which("node")
pytestmark = pytest.mark.skipif(node is None, reason="node is not installed")

IN = 0.0254
START_Y, RELEASE_Y = 38.4, 30.0
BROOM = {"x": 0.10, "y": 0.0}


def js(expr: str, **consts):
    defs = "".join(f"const {k} = {json.dumps(v)};\n" for k, v in consts.items())
    script = (f"const S = await import({MODULE.as_uri()!r});\n{defs}"
              f"console.log(JSON.stringify({expr}));")
    proc = subprocess.run([node, "--input-type=module", "-e", script],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def throw(s_in, r_in, curl="left", slope=0.001, release=True, broom=True, step=0.1,
          noise=None):
    """A throw whose slide, extended to the broom, lands ``s_in`` inches to the
    broom's right (x), and whose release line lands ``r_in`` to its right.
    Points near the hack and in the last 2.33 m before the release are thrown
    off the line, to prove the fit leaves them out."""
    at_broom = BROOM["x"] + s_in * IN
    pts, t, y = [], 0.0, START_Y
    while y > RELEASE_Y - 1.5:
        x = at_broom + slope * y
        if y > START_Y - 0.3 or RELEASE_Y < y < RELEASE_Y + 2.33:
            x += 0.05                                   # the push-off and the arm extension
        if y <= RELEASE_Y:
            x = BROOM["x"] + r_in * IN + slope * y      # after the release: its own line
        if noise:
            x += noise[len(pts) % len(noise)]
        pts.append([round(t, 2), round(y, 4), round(x, 6)])
        t += 0.033
        y -= step
    line = {"delivery": pts, "start": {"x": pts[0][2], "y": START_Y},
            "miss_m": (r_in * IN) if broom else None}
    if release:
        line["release"] = [1.0, RELEASE_Y, BROOM["x"]]
    return {"line": line, "broom": BROOM if broom else None, "curl": curl}


def summary(t):
    return js("S.slideSummary(T)", T=t)


def test_the_slide_aimed_off_and_the_release_moving_it():
    # Curling left, right (+x) is wide.
    assert summary(throw(-9, 2)) == {"slide": "Slide: 9 in narrow",
                                     "release": "Release: 2 in wide (set +11 in)"}


def test_curling_right_turns_wide_and_narrow_around():
    assert summary(throw(-9, 2, curl="right")) == {
        "slide": "Slide: 9 in wide", "release": "Release: 2 in narrow (set −11 in)"}


def test_without_a_curl_the_sides_are_left_and_right():
    assert summary(throw(-9, 2, curl=None)) == {
        "slide": "Slide: 9 in left", "release": "Release: 2 in right (set 11 in right)"}


def test_a_set_within_its_uncertainty_is_left_out():
    # Set -1 in, under twice the 1.5 in floor on R's error.
    assert summary(throw(1.6, 0.5)) == {"slide": "Slide: 2 in wide",
                                        "release": "Release: to the broom"}


def test_within_an_inch_either_side_is_to_the_broom():
    got = summary(throw(0.1, 8))
    assert got["slide"] == "Slide: to the broom"
    assert got["release"] == "Release: 8 in wide (set +8 in)"
    assert summary(throw(-8, 0.1))["release"] == "Release: to the broom (set +8 in)"
    # Judged on the figure as shown: 1 in is to the broom, 2 in is not.
    assert summary(throw(-1.4, 8))["slide"] == "Slide: to the broom"
    assert summary(throw(-1.6, 8))["slide"] == "Slide: 2 in narrow"


def test_rounded_to_the_nearest_inch():
    # Finer would claim more than the cameras measure: their error is inches.
    assert summary(throw(-8.8, 3.1))["slide"] == "Slide: 9 in narrow"
    assert summary(throw(-8.8, 3.1))["release"] == "Release: 3 in wide (set +12 in)"


def test_the_fit_leaves_out_the_push_off_and_the_last_six_feet_and_a_half_metre():
    fit = js("S.slideFit(T.line)", T=throw(-9, 2))
    assert fit["n"] == 57                     # 38.1 down to 32.33 m in 0.1 m steps
    assert fit["b"] == pytest.approx(0.001, abs=1e-9)


def test_no_release_point_means_no_slide_and_no_set():
    assert summary(throw(-9, 2, release=False)) == {"slide": None,
                                                    "release": "Release: 2 in wide"}


def test_no_broom_means_no_summary():
    assert summary(throw(-9, 2, broom=False)) is None


def test_too_short_a_slide_is_not_fitted():
    # 0.5 m steps leave 12 points over 5.5 m: fitted. 1.0 m steps leave 6: refused.
    assert summary(throw(-9, 2, step=0.5))["slide"] == "Slide: 9 in narrow"
    assert summary(throw(-9, 2, step=1.0)) == {"slide": None, "release": "Release: 2 in wide"}


def test_a_noisy_slide_needs_a_bigger_set_to_show_it():
    noisy = throw(-1.5, 2, noise=[0.0, 0.02, -0.02, 0.01, -0.01])
    got = js("S.slideReading(T)", T=noisy)
    assert got["se_s_in"] > 0.3
    assert got["set_in"] == pytest.approx(3.5, abs=0.3)
    assert got["set_shown"] is (abs(got["set_in"]) > 2 * (got["se_s_in"] ** 2 + 1.5 ** 2) ** 0.5)


def test_the_figures_for_the_hero_come_in_parts():
    assert js("S.slideFigures(T)", T=throw(-9, 2)) == {
        "slide": {"value": "9 in", "side": "narrow"},
        "release": {"value": "2 in", "side": "wide"},
        "set": {"value": "+11 in"}}
    assert js("S.slideFigures(T)", T=throw(-9, 2, curl="right"))["set"] == {"value": "−11 in"}
    got = js("S.slideFigures(T)", T=throw(0.1, 0.5))
    assert got["slide"] == {"value": "to the broom", "side": None}
    assert got["set"] is None
    assert js("S.slideFigures(T)", T=throw(-9, 2, release=False))["slide"] is None
    assert js("S.slideFigures(T)", T=throw(-9, 2, curl=None))["set"] == {
        "value": "11 in right"}
    assert js("S.slideFigures(T)", T=throw(-9, 2, broom=False)) is None


def test_the_figures_in_short_words_for_the_throws_table():
    f = js("S.slideFigures(T)", T=throw(-9, 2))
    assert js("S.figureWords(F.slide)", F=f) == "9 in narrow"
    assert js("S.setWords(F.set)", F=f) == "+11 in"
    assert js("S.setWords(S.slideFigures(T).set)", T=throw(-9, 2, curl="right")) == "−11 in"
    assert js("S.figureWords({value: 'to the broom', side: null})") == "to the broom"
    assert js("S.setWords(S.slideFigures(T).set)", T=throw(-9, 2, curl=None)) == "11 in right"


def test_nothing_to_say_without_a_line():
    assert js("S.slideSummary({line: null, broom: null, curl: null})") is None
    assert js("S.slideSummary({})") is None
