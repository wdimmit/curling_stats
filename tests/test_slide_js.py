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


def figures(t):
    """The figures as the page reads them: the slide and the release in words
    (the card's figure, the throws table's Line), the set as shown under the
    release; None for what is not shown."""
    return js("(f => f && {slide: f.slide && S.figureWords(f.slide), "
              "release: S.figureWords(f.release), set: f.set && f.set.value})"
              "(S.slideFigures(T))", T=t)


def test_the_slide_aimed_off_and_the_release_moving_it():
    # Curling left, right (+x) is wide.
    assert figures(throw(-9, 2)) == {"slide": "9\" narrow", "release": "2\" wide",
                                     "set": "+11\""}


def test_curling_right_turns_wide_and_narrow_around():
    assert figures(throw(-9, 2, curl="right")) == {
        "slide": "9\" wide", "release": "2\" narrow", "set": "−11\""}


def test_without_a_curl_the_sides_are_left_and_right():
    assert figures(throw(-9, 2, curl=None)) == {
        "slide": "9\" left", "release": "2\" right", "set": "11\" right"}


def test_a_set_within_its_uncertainty_is_left_out():
    # Set -1 in, under twice the 1.5 in floor on R's error.
    assert figures(throw(1.6, 0.5)) == {"slide": "2\" wide", "release": "to the broom",
                                        "set": None}


def test_within_an_inch_either_side_is_to_the_broom():
    got = figures(throw(0.1, 8))
    assert got["slide"] == "to the broom"
    assert (got["release"], got["set"]) == ("8\" wide", "+8\"")
    got = figures(throw(-8, 0.1))
    assert (got["release"], got["set"]) == ("to the broom", "+8\"")
    # Judged on the figure as shown: 1 in is to the broom, 2 in is not.
    assert figures(throw(-1.4, 8))["slide"] == "to the broom"
    assert figures(throw(-1.6, 8))["slide"] == "2\" narrow"


def test_rounded_to_the_nearest_inch():
    # Finer would claim more than the cameras measure: their error is inches.
    assert figures(throw(-8.8, 3.1)) == {"slide": "9\" narrow", "release": "3\" wide",
                                         "set": "+12\""}


def test_the_fit_leaves_out_the_push_off_and_the_last_six_feet_and_a_half_metre():
    fit = js("S.slideFit(T.line)", T=throw(-9, 2))
    assert fit["n"] == 57                     # 38.1 down to 32.33 m in 0.1 m steps
    assert fit["b"] == pytest.approx(0.001, abs=1e-9)


def test_no_release_point_means_no_slide_and_no_set():
    assert figures(throw(-9, 2, release=False)) == {"slide": None, "release": "2\" wide",
                                                    "set": None}


def test_no_broom_means_no_figures():
    assert figures(throw(-9, 2, broom=False)) is None


def test_too_short_a_slide_is_not_fitted():
    # 0.5 m steps leave 12 points over 5.5 m: fitted. 1.0 m steps leave 6: refused.
    assert figures(throw(-9, 2, step=0.5))["slide"] == "9\" narrow"
    assert figures(throw(-9, 2, step=1.0)) == {"slide": None, "release": "2\" wide",
                                               "set": None}


def test_a_noisy_slide_needs_a_bigger_set_to_show_it():
    # The same 4 in set both times. Through a clean slide the bar is 2 x 1.5 in,
    # so it shows; scatter of up to 2 cm puts the slide's own error near 1.6 in
    # at the broom, the bar near 4.4 in, and it does not.
    assert figures(throw(-2, 2))["set"] == "+4\""
    noisy = throw(-2, 2, noise=[0.0, 0.02, -0.02, 0.01, -0.01])
    assert figures(noisy)["set"] is None
    assert js("S.slideReading(T).set_in", T=noisy) == pytest.approx(4.0, abs=0.3)


def test_the_figures_for_the_card_come_in_parts():
    assert js("S.slideFigures(T)", T=throw(-9, 2)) == {
        "slide": {"value": "9\"", "side": "narrow"},
        "release": {"value": "2\"", "side": "wide"},
        "set": {"value": "+11\""}}
    got = js("S.slideFigures(T)", T=throw(0.1, 0.5))
    assert got["slide"] == {"value": "to the broom", "side": None}
    assert got["set"] is None


def test_the_throws_table_says_what_the_card_says():
    # Inches past a foot, as the card has them, not "1 ft 2 in".
    assert js("S.releaseWords(T)", T=throw(-9, 14)) == "14\" wide"
    # The card's to-the-broom, not lineText's narrower one: 1.4 in rounds to 1.
    assert js("S.releaseWords(T)", T=throw(-9, 1.4)) == "to the broom"
    assert js("S.releaseWords(T)", T=throw(-9, 2, curl="right")) == "2\" narrow"
    # No broom: nothing here, and the table falls back on lineText.
    assert js("S.releaseWords(T)", T=throw(-9, 2, broom=False)) is None


def test_nothing_to_say_without_a_line():
    assert js("S.slideFigures({line: null, broom: null, curl: null})") is None
    assert js("S.slideFigures({})") is None
