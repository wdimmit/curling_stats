"""The practice page's words for a throw (frontend/core/practice.mjs), in node."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / "frontend/core/practice.mjs"
node = shutil.which("node")
pytestmark = pytest.mark.skipif(node is None, reason="node is not installed")


def js(expr: str):
    script = (f"const P = await import({MODULE.as_uri()!r});\n"
              f"console.log(JSON.stringify({expr}));")
    proc = subprocess.run([node, "--input-type=module", "-e", script],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_where_it_stopped_in_a_curlers_words():
    assert js('["button","4","8","12","short","long","out"].map(r => P.ringWord({ring: r}))') == [
        "Button", "4-foot", "8-foot", "12-foot", "In front", "Behind", "Out of play"]
    assert js('P.restText({x: 0.1, y: -0.4, to_tee_m: 0.41, ring: "4"})') == \
        "4-foot · 1 ft 4 in from the tee"
    assert js('P.restText({x: 1.7, y: 0.8, to_tee_m: null, ring: "out"})') == "Out of play"
    assert js("P.restText(null)") is None


def test_the_line_against_the_broom_or_where_it_was_aimed():
    assert js('P.lineText({miss_m: -0.12, side: "narrow", aim_x: null})') == \
        "5 in narrow of the broom"
    assert js('P.lineText({miss_m: 0.01, side: "wide", aim_x: null})') == "On the broom"
    assert js('P.lineText({miss_m: null, side: null, aim_x: -0.21})') == \
        "aimed 8 in left of centre"
    assert js("P.lineText(null)") is None


def test_weight_and_curl():
    assert js("P.weightText(13.82)") == "13.8 s"
    assert js("P.weightText(null)") is None
    assert js('[P.curlText("left"), P.curlText("right"), P.curlText(null)]') == [
        "Curled left", "Curled right", None]


def test_a_stone_nothing_released_is_not_shown_as_a_throw():
    # Real practice (Qzh8): every arrival with no release from either camera
    # was a stone placed by hand for a drill.
    got = js('P.shown(['
             '{id: "a", arrived: true, release_source: "overhead"},'
             '{id: "b", arrived: true, release_source: null},'
             '{id: "c", arrived: true, release_source: "side"},'
             '{id: "d", arrived: false, release_source: "overhead"}]).map(t => t.id)')
    assert got == ["a", "c", "d"]


def test_the_summary_strip():
    got = js('P.summary(['
             '{split_s: 13.6, line: {miss_m: 0.05}, hidden: false},'
             '{split_s: 14.6, line: {miss_m: -0.22}, hidden: false},'
             '{split_s: 12.0, line: null, hidden: true},'
             '{split_s: null, line: {miss_m: 0.1}, hidden: false}])')
    assert got == {"count": 3, "weightMin": 13.6, "weightMax": 14.6, "onBroom": 2,
                   "withBroom": 3}


def test_how_long_a_session_ran():
    got = js('[0, 40, 600, 3540, 3600, 5520, 10800].map(P.lengthText)')
    assert got == ["under a minute", "under a minute", "10 min", "59 min", "1 h",
                   "1 h 32 min", "3 h"]


def test_where_a_clip_goes():
    assert js('P.clipState({clip: true, t_release_s: 1}, {live: false, newest: false})') == "play"
    assert js('P.clipState({clip: false, t_release_s: 1}, {live: true, newest: true})') == "wait"
    assert js('P.clipState({clip: false, t_release_s: 1}, {live: true, newest: false})') is None
    assert js('P.clipState({clip: false, t_release_s: 1}, {live: false, newest: true})') is None
    assert js('P.clipState({clip: false, t_release_s: null}, {live: true, newest: true})') is None
    # null: none is coming; absent: a document from before clips.
    assert js('P.clipState({clip: null, t_release_s: 1}, {live: true, newest: true})') is None
    assert js('P.clipState({t_release_s: 1}, {live: true, newest: true})') is None


def test_a_link_is_fetched_again_before_it_expires():
    assert js("P.linkFresh({url: 'u', at: 0}, 49 * 60 * 1000)") is True
    assert js("P.linkFresh({url: 'u', at: 0}, 51 * 60 * 1000)") is False
    assert js("P.linkFresh(undefined, 0)") is False
