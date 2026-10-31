"""The practice page's words for a throw (frontend/core/practice.mjs), in node."""

import json
import re
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


def test_the_tables_line_leaves_off_the_broom():
    # In inch marks, as the card's figures are (PR #2).
    assert js('P.lineText({miss_m: -0.12, side: "narrow", aim_x: null}, {short: true})') == \
        '5" narrow'
    assert js('P.lineText({miss_m: 0.33, side: "wide", aim_x: null}, {short: true})') == \
        '13" wide'
    assert js('P.lineText({miss_m: 0.01, side: "wide", aim_x: null}, {short: true})') == \
        "to the broom"
    assert js('P.lineText({miss_m: null, side: null, aim_x: -0.2032}, {short: true})') == \
        '8" left of centre'


def aim(miss, side, shown=True):
    return {"at_broom": {"x": 0.0, "miss_m": miss}, "side": side, "shown": shown,
            "fit": {"n": 40, "n_eff": 12.0, "rms_m": 0.004, "se_m": 0.03}}


def throw(**line):
    base = {"miss_m": -0.127, "side": "wide", "aim_x": None, "release": [1.0, 29.0, -0.2],
            "slide": aim(0.0762, "narrow"), "release_line": aim(-0.1016, "wide"),
            "set": {"m": 0.1778, "se_m": 0.03, "shown": True}}
    base.update(line)
    return {"curl": "right", "line": base}


def figs(t):
    """With the slide shown; the default holds it back."""
    return js(f"P.aimFigures({json.dumps(t)}, {{slide: true}})")


def test_slide_and_set_shown_by_default():
    assert js(f"P.aimFigures({json.dumps(throw())})") == figs(throw())
    held = js(f"P.aimFigures({json.dumps(throw())}, {{slide: false}})")
    assert "slide" not in held and held["set"] is None
    assert held["release"] == {"value": '4"', "side": "wide"}


def test_aim_figures_in_inch_marks():
    assert figs(throw()) == {"slide": {"value": '3"', "side": "narrow"},
                             "release": {"value": '4"', "side": "wide"},
                             "final": {"value": '5"', "side": "wide"},
                             "set": {"value": '+7"'}}
    assert figs(throw(set={"m": -0.1778, "se_m": 0.03, "shown": True}))["set"] == {"value": "−7\""}


def test_within_an_inch_and_a_half_is_to_the_broom():
    # The game page's threshold too, on the measured miss, not its rounding.
    assert figs(throw(slide=aim(0.035, "narrow")))["slide"] == {"value": "to the broom", "side": None}
    assert figs(throw(slide=aim(0.040, "narrow")))["slide"] == {"value": '2"', "side": "narrow"}
    assert js('P.lineText({miss_m: 0.035, side: "wide", aim_x: null})') == "On the broom"


def test_set_without_curl_says_which_way():
    t = dict(throw(), curl=None)
    assert figs(t)["set"] == {"value": '7" right'}


def test_not_shown_or_missing_is_null():
    got = figs(throw(slide=None, release_line=aim(-0.1, "wide", shown=False),
                     set={"m": 0.1, "se_m": 0.2, "shown": False}, final_refused=True, miss_m=None))
    assert got == {"slide": None, "release": None, "final": None, "set": None}


def test_broomless_reads_against_centre():
    tee = {"at_broom": None, "at_tee": {"x": -0.2032}, "side": None, "shown": True,
           "fit": {"n": 40, "n_eff": 12.0, "rms_m": 0.004, "se_m": 0.03}}
    t = throw(slide=tee, release_line=tee, set=None, miss_m=None, side=None, aim_x=0.1016)
    got = figs(t)
    assert js(f"P.figureWords({json.dumps(got['slide'])})") == '8" left of centre'
    assert js(f"P.figureWords({json.dumps(got['final'])})") == '4" right of centre'


def test_old_record_has_no_aim_figures():
    assert js('P.aimFigures({curl: "right", line: {miss_m: -0.1, side: "wide", aim_x: null}})') is None
    assert js('P.aimFigures({line: null})') is None


def test_table_line_matches_the_card():
    t = throw()
    words = js(f"P.figureWords(P.aimFigures({json.dumps(t)}).final)")
    assert js(f"P.lineText({json.dumps(t['line'])}, {{short: true}})") == words == '5" wide'


def test_weight_and_curl():
    assert js("P.weightText(13.82)") == "13.8 s"
    assert js("P.weightText(null)") is None
    assert js('[P.curlText("left"), P.curlText("right"), P.curlText(null)]') == [
        "Curled left", "Curled right", None]


def test_in_or_out_turn_from_the_curl_and_the_hand():
    # A right-hander's in-turn spins clockwise and curls right; a left-hander's
    # in-turn spins the other way and curls left.
    assert js('[P.turnText("right", "right"), P.turnText("left", "right")]') == ["In", "Out"]
    assert js('[P.turnText("left", "left"), P.turnText("right", "left")]') == ["In", "Out"]
    assert js('[P.turnText(null, "right"), P.turnText("right", undefined)]') == [None, "In"]


def test_the_turn_column_reads_which_turn_and_how_fast():
    assert js('P.rotationText({curl: "right", spin: {rpm: 9.4, rpm_range: null}}, "right")') == "In · 9 rpm"
    assert js('P.rotationText({curl: "left", spin: {rpm: 7, rpm_range: [5.5, 8.4]}}, "right")') \
        == "Out · 6–8 rpm"
    # Either alone when the other was not read; nothing when neither was.
    assert js('[P.rotationText({curl: null, spin: {rpm: 10.2}}, "right"),'
              ' P.rotationText({curl: "left", spin: null}, "left"),'
              ' P.rotationText({curl: null}, "right")]') == ["10 rpm", "In", None]


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


def test_the_overlay_for_the_piece_playing():
    ov = '[{t0: 0, t1: 13.2, thrown: [[0.5, 0.2], [0.5, 0.9]]}, {t0: 13.2, t1: 26.7, broom: [0.5, 0.3]}]'
    assert js(f'P.overlayAt({ov}, 0)')["t1"] == 13.2
    assert js(f'P.overlayAt({ov}, 13.19)')["t1"] == 13.2
    assert js(f'P.overlayAt({ov}, 13.2)')["t0"] == 13.2
    assert js(f'P.overlayAt({ov}, 26.7)')["t0"] == 13.2       # the last frame
    assert js(f'P.overlayAt({ov}, 40)') is None
    assert js('P.overlayAt(null, 1)') is None and js('P.overlayAt(undefined, 1)') is None


def test_the_slide_dots_the_clip_has_reached():
    piece = '{t0: 0, t1: 13.2, dots: [[2.0, 0.5, 0.3, "before"], [2.5, 0.5, 0.32, "after"], [3.4, 0.49, 0.36, "after"]]}'
    assert js(f'P.dotsShown({piece}, 1.9)') == 0
    assert js(f'P.dotsShown({piece}, 2.0)') == 1
    assert js(f'P.dotsShown({piece}, 3.0)') == 2
    assert js(f'P.dotsShown({piece}, 13.0)') == 3
    assert js('P.dotsShown({t0: 0, t1: 1}, 5)') == 0 and js('P.dotsShown(null, 5)') == 0


PAGE = MODULE.parents[1] / "site/Practice.jsx"


def test_the_page_speaks_of_the_cameras_never_a_stream():
    """Practice reads the club's cameras. A session that ended in the YouTube
    days keeps its words: its stored end reason (stream_ended) still has some."""
    text = PAGE.read_text()
    literals = [a or b for a, b in re.findall(r'"([^"]*)"|`([^`]*)`', text)]
    assert [w for w in literals if "stream" in w.lower()] == []
    assert "stream_ended:" in text and "failed:" in text


def test_a_sensors_times_in_words():
    assert js("P.sensorText({short_s: 3.214, long_s: 13.9})") == "short 3.21 s · long 13.90 s"
    assert js("P.sensorText({short_s: null, long_s: 13.9})") == "long 13.90 s"
    assert js("P.sensorText({short_s: null, long_s: null})") is None
    assert js("P.sensorText(undefined)") is None


def test_unpaired_timings_go_among_the_throws_by_time():
    rows = js("""P.mergeTimings(
        [{id: "a", released_at: "2026-10-25T10:00:00Z"},
         {id: "b", released_at: "2026-10-25T10:01:00Z"}],
        [{id: "s1", hog_at: "2026-10-25T10:00:30Z"},
         {id: "s2", hog_at: "2026-10-25T10:01:00Z"}])
        .map(r => r.throw ? `${r.throw.id}${r.n}` : r.timing.id)""")
    assert rows == ["a1", "s1", "b2", "s2"]
    assert js("P.mergeTimings([], undefined)") == []
