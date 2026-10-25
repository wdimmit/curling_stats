"""The ice maker's report's arithmetic (frontend/core/ice.mjs), in node: which
rocks it reads, which side and turns band each goes in, the last-10 m bend,
and the difference between two draws."""

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

ICE = Path(__file__).resolve().parents[1] / "frontend/core/ice.mjs"
node = shutil.which("node")
pytestmark = pytest.mark.skipif(node is None, reason="node is not installed")


def run_js(body: str):
    script = (f"const A = await import({ICE.as_uri()!r});\nObject.assign(globalThis, A);\n"
              "function out(v){ console.log(JSON.stringify(v)); }\n" + body)
    proc = subprocess.run([node, "--input-type=module", "-e", script],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def curved(rest_x, rest_y, bend, side=1.0):
    """A path [[y, x]] from 20 m out to rest, curling `bend` m by rest the way
    `side` says (+1 right), quadratic in the distance travelled."""
    ys = np.append(np.arange(20.0, rest_y, -0.25), rest_y)
    u = (ys - rest_y) / (20.0 - rest_y)   # 1 where it was first seen, 0 at rest
    xs = rest_x - side * bend * (1 - (1 - u) ** 2)
    return [[round(float(y), 3), round(float(x), 4)] for y, x in zip(ys, xs)]


def draw(number=1, *, rest=(0.4, 0.3), curl="right", hit=None, spin=None, path=None, sheet_x0=None):
    """A rock that stopped at `rest`, thrown at a broom at x = 0 on a line from
    the hog line at `sheet_x0` (default: 0.5 m off the way it curls back)."""
    side = 1.0 if curl == "right" else -1.0
    x0 = -side * 0.5 if sheet_x0 is None else sheet_x0
    return {
        "number": number, "color": "red", "position": "lead", "missing": False,
        "state_known": True, "shot_type": "draw", "label": "x",
        "target_broom": {"x": x0, "y": 0.0}, "delivered_stone_index": 0,
        "stones": [{"color": "red", "x": rest[0], "y": rest[1]}],
        "house_delta": hit,
        "line": {"start": {"x": 0.0, "y": 38.0}, "at_hog": {"x": x0, "offset_m": 0.0},
                 "at_broom": {"x": x0, "miss_m": 0.0}, "curl": curl, "confirmed": None,
                 "hog_path": [], "path": path if path is not None else curved(rest[0], rest[1], 0.6, side)},
        "spin": spin,
    }


def doc(shots, sheet=2):
    return {"schema_version": 9, "source": {"video_id": "v", "sheet": sheet},
            "games": [{"index": 0, "teams": {"red": {"name": None}, "yellow": {"name": None}},
                       "final": {"red": 0, "yellow": 0},
                       "ends": [{"number": 1, "score": {"red": 0, "yellow": 0}, "shots": shots}]}]}


def rocks(shots):
    return run_js(f"out(iceRocks({json.dumps(doc(shots))}, 's_g'));")


class TestWhichRocks:
    def test_a_draw_that_stopped_in_the_house_untouched_is_read(self):
        (r,) = rocks([draw()])
        assert r["game"] == "s_g" and r["sheet"] == 2 and r["turn"] == "right"
        # 0.4 at rest against a line at -0.5: 0.9 m the way it was turned.
        assert r["curl_m"] == pytest.approx(0.9)

    def test_a_rock_that_curled_left_reads_positive_too(self):
        (r,) = rocks([draw(rest=(-0.4, 0.3), curl="left")])
        assert r["curl_m"] == pytest.approx(0.9)

    def test_a_guard_is_not(self):
        assert rocks([draw(rest=(0.4, 4.0))]) == []

    def test_a_rock_through_the_house_is_not(self):
        assert rocks([draw(rest=(0.0, -2.0))]) == []

    def test_a_hit_is_not(self):
        struck = {"added": [], "removed": [{"color": "yellow", "x": 0.4, "y": 0.5}], "moved": []}
        assert rocks([draw(hit=struck)]) == []

    def test_a_rock_with_no_turn_read_is_not(self):
        assert rocks([draw(curl=None)]) == []

    def test_a_missing_rock_is_not(self):
        assert rocks([dict(draw(), missing=True)]) == []


class TestSide:
    def test_curling_out_toward_the_sideline_is_away(self):
        # Right of the centre line and curling right.
        assert run_js("out(sideOf([[5, 0.8], [1, 0.9]], {x: 1.0, y: 0.5}, 'right'));") == "away"

    def test_curling_back_toward_the_centre_line_is_toward(self):
        assert run_js("out(sideOf([[5, 0.8], [1, 0.9]], {x: 1.0, y: 0.5}, 'left'));") == "toward"

    def test_the_left_of_the_sheet_mirrors_it(self):
        assert run_js("out([sideOf([[5, -0.8]], {x: -1, y: 0.5}, 'left'), "
                      "sideOf([[5, -0.8]], {x: -1, y: 0.5}, 'right')]);") == ["away", "toward"]

    def test_it_is_the_side_it_travelled_on_not_where_it_stopped(self):
        # Crossed the centre line in its last metre: most of its last 10 m was on the left.
        assert run_js("out(sideOf([[9, -0.6], [6, -0.5], [3, -0.3], [1.2, 0.2]], "
                      "{x: 0.3, y: 1.0}, 'right'));") == "toward"

    def test_close_to_the_centre_line_is_down_the_middle(self):
        assert run_js("out(sideOf([[5, 0.1], [2, -0.05]], {x: 0.05, y: 1}, 'right'));") == "middle"

    def test_without_a_path_its_rest_says(self):
        assert run_js("out(sideOf(null, {x: 1.2, y: 0.5}, 'right'));") == "away"


class TestTurns:
    def test_turns_are_the_rate_over_two_and_a_half(self):
        (r,) = rocks([draw(spin={"rpm": 10, "rpm_range": None})])
        assert r["rpm"] == 10 and r["turns"] == 4 and r["turnBin"] == "t1"

    def test_a_range_counts_at_its_middle(self):
        (r,) = rocks([draw(spin={"rpm": None, "rpm_range": [6, 8]})])
        assert r["rpm"] == 7 and r["turns"] == pytest.approx(2.8) and r["turnBin"] == "t0"

    def test_no_rate_is_not_read(self):
        (r,) = rocks([draw()])
        assert r["turns"] is None and r["turnBin"] == "unread"

    def test_the_bands(self):
        assert run_js("out([3.9, 4, 5.5, 6, 9].map(turnBin));") == ["t0", "t1", "t2", "t3", "t3"]
        assert [b["name"] for b in run_js("out(TURN_BINS);")] == [
            "Under 4 turns", "4–5 turns", "5–6 turns", "6+ turns", "Rotation not read"]


class TestLastTenMetres:
    def test_it_agrees_with_numpy(self):
        """The quadratic tangent the week-over-week study fitted with polyfit
        (~/curling-work/ice-week/ice.py)."""
        path = curved(0.4, 0.3, 0.6)
        rest = {"x": 0.4, "y": 0.3}
        pts = np.array([(y, x) for y, x in path if abs(y - 10.3) <= 2.0])
        c = np.polyfit(pts[:, 0] - 10.3, pts[:, 1], 2)
        want = rest["x"] - (c[2] - c[1] * 10)
        got = run_js(f"out(tailBend({json.dumps(path)}, {json.dumps(rest)}));")
        assert got == pytest.approx(want, abs=1e-9) and got > 0

    def test_a_path_that_stops_short_has_none(self):
        assert run_js("out(tailBend([[6, 0.1], [5, 0.2], [4, 0.3]], {x: 0.4, y: 0.3}));") is None

    def test_a_rock_carries_it_signed_by_its_turn(self):
        (r,) = rocks([draw(rest=(-0.4, 0.3), curl="left")])
        assert r["tail_m"] > 0


ROWS = """
const mk = (prefix, games, sheets, value) => {
  const rows = [];
  for (let g = 0; g < games; g++)
    for (let i = 0; i < 4; i++)
      rows.push({game: `${prefix}${g}`, sheet: sheets[g], curl_m: value + 0.01 * i + 0.002 * g,
                 tail_m: null, side: i % 2 ? "away" : "toward", turnBin: "t1"});
  return rows;
};
"""


class TestDifference:
    def test_a_clear_drop_has_a_range_below_zero(self):
        got = run_js(ROWS + "out(difference(mk('a', 5, [1,2,3,4,5], 1.6), mk('b', 5, [1,2,3,4,5], 1.4), 'curl_m'));")
        assert got["diff"] == pytest.approx(-0.2)
        assert got["lo"] < got["diff"] < got["hi"] < 0
        assert got["sheets"] == {"less": 5, "more": 0, "of": 5}

    def test_it_is_the_same_every_time(self):
        body = ROWS + "out(difference(mk('a', 4, [1,2,3,4], 1.6), mk('b', 4, [1,2,3,4], 1.5), 'curl_m'));"
        assert run_js(body) == run_js(body)

    def test_too_few_games_says_nothing(self):
        assert run_js(ROWS + "out(difference(mk('a', 2, [1,2], 1), mk('b', 5, [1,2,3,4,5], 1), 'curl_m'));") is None

    def test_a_reading_nobody_has_says_nothing(self):
        assert run_js(ROWS + "out(difference(mk('a', 5, [1,2,3,4,5], 1), mk('b', 5, [1,2,3,4,5], 1), 'tail_m'));") is None

    def test_sheets_only_one_draw_played_on_are_not_counted(self):
        got = run_js(ROWS + "out(difference(mk('a', 4, [1,2,3,4], 1.6), mk('b', 4, [3,4,5,6], 1.4), 'curl_m'));")
        assert got["sheets"] == {"less": 2, "more": 0, "of": 2}


class TestTables:
    def test_both_tables_and_their_rows(self):
        got = run_js(ROWS + "out(iceTables(mk('a', 5, [1,2,3,4,5], 1.6), mk('b', 5, [1,2,3,4,5], 1.4)));")
        assert [r["name"] for r in got["sides"]] == [
            "Away from centre", "Toward centre", "Down the middle", "All"]
        all_ = got["sides"][-1]
        assert all_["a"]["rocks"] == 20 and all_["a"]["games"] == 5 and all_["curl"]["diff"] < 0
        assert got["sides"][2]["curl"] is None
        assert [r["a"]["rocks"] for r in got["turns"]] == [0, 20, 0, 0, 0]

    def test_one_draw_on_its_own(self):
        got = run_js(ROWS + "out(iceTables(mk('a', 3, [1,2,3], 1.6), null));")
        assert got["sides"][-1]["b"] is None and got["sides"][-1]["curl"] is None
