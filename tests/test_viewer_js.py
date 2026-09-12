"""The viewer's own arithmetic, exercised in node.

Two things in ``app.js`` are worth testing rather than eyeballing: the override
merge, which has to agree with :func:`timeline.apply_overrides` or charting
work silently disagrees with what the next analysis bakes in, and the report
percentages, which are the numbers a player will read off and believe.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "src/curling_score/viewer/app.js"
node = shutil.which("node")
pytestmark = pytest.mark.skipif(node is None, reason="node is not installed")


def run_js(body: str):
    """Run ``body`` with the viewer's exports in scope; return its JSON output."""
    script = (
        f"const A = require({str(APP)!r});\n"
        "const {state, merge, keyFor, mergedShots, layout, identity, gatherStats,\n"
        "       pct, avg, isBlank, isGraded, typeOf, shotVideoTime, stoneAt,\n"
        "       shotKey, rawShot, houseViewBox, peekMode, renumberNotice} = A;\n"
        "function out(v){ console.log(JSON.stringify(v)); }\n" + body
    )
    proc = subprocess.run([node, "-e", script], capture_output=True, text=True,
                          timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def shot(number, color, position, **kw):
    base = {"number": number, "color": color, "position": position,
            "stones": [], "missing": False, "state_known": True,
            "shot_type": "draw", "label": f"shot {number}"}
    base.update(kw)
    return base


def doc(shots, game_index=0, end_number=1):
    return {"source": {"video_id": "v"},
            "games": [{"index": game_index, "teams": {"red": {"name": None},
                                                      "yellow": {"name": None}},
                       "final": {"red": 0, "yellow": 0},
                       "ends": [{"number": end_number, "score": {"red": 0, "yellow": 0},
                                 "shots": shots}]}]}


SETUP = """
state.doc = %s; state.overrides = %s; state.gi = 0; state.ei = 0; state.si = 0;
const g = state.doc.games[0], e = g.ends[0];
"""


def setup(document, overrides=None):
    return SETUP % (json.dumps(document), json.dumps(overrides or {}))


class TestOverrideMerge:
    """Must match timeline.apply_overrides, key for key."""

    def test_the_key_is_game_index_end_number_shot_number(self):
        got = run_js(setup(doc([shot(1, "red", "lead")], game_index=0,
                               end_number=7)) +
                     "out(keyFor(g, e, e.shots[0]));")
        assert got == "0.7.1"

    def test_a_patch_replaces_fields_and_marks_the_shot_corrected(self):
        got = run_js(setup(doc([shot(1, "red", "lead")]),
                           {"0.1.1": {"shot_type": "peel", "user_score": 3}}) +
                     "out(merge(g, e, e.shots[0]));")
        assert got["shot_type"] == "peel"
        assert got["user_score"] == 3
        assert got["corrected"] is True
        assert got["color"] == "red"  # untouched fields survive

    def test_an_unpatched_shot_is_returned_unchanged(self):
        got = run_js(setup(doc([shot(1, "red", "lead")])) +
                     "out(merge(g, e, e.shots[0]).corrected ?? null);")
        assert got is None

    def test_a_patch_for_another_shot_does_not_leak(self):
        got = run_js(setup(doc([shot(1, "red", "lead"), shot(2, "yellow", "lead")]),
                           {"0.1.2": {"user_score": 4}}) +
                     "out(mergedShots(e).map(s => s.user_score ?? null));")
        assert got == [None, 4]


class TestBlanks:
    def test_a_missing_shot_is_blank(self):
        got = run_js(setup(doc([shot(1, "red", "lead", missing=True)])) +
                     "out(isBlank(merge(g, e, e.shots[0])));")
        assert got is True

    def test_an_unreadable_house_is_blank(self):
        got = run_js(setup(doc([shot(1, "red", "lead", state_known=False)])) +
                     "out(isBlank(merge(g, e, e.shots[0])));")
        assert got is True

    def test_charting_it_clears_the_blank(self):
        got = run_js(setup(doc([shot(1, "red", "lead", state_known=False)]),
                           {"0.1.1": {"state_known": True}}) +
                     "out(isBlank(merge(g, e, e.shots[0])));")
        assert got is False

    def test_an_empty_house_we_could_read_is_not_blank(self):
        got = run_js(setup(doc([shot(1, "red", "lead", state_known=True)])) +
                     "out(isBlank(merge(g, e, e.shots[0])));")
        assert got is False


class TestReportArithmetic:
    """Shooting percentage is Curl Coach's: the points scored out of 4 a shot."""

    def _stats(self, shots, overrides=None):
        return run_js(setup(doc(shots), overrides) + "out(gatherStats());")

    def test_a_perfect_lead_shoots_a_hundred(self):
        got = self._stats([shot(1, "red", "lead", user_score=4),
                           shot(3, "red", "lead", user_score=4)])
        lead = got["red"]["lead"]
        assert lead == {"thrown": 2, "graded": 2, "sum": 8,
                        "types": {"draw": {"thrown": 2, "graded": 2, "sum": 8}}}

    def test_the_percentage_is_points_over_four_per_graded_shot(self):
        pct = run_js(setup(doc([shot(1, "red", "lead", user_score=3),
                                shot(3, "red", "lead", user_score=2)])) +
                     "out(pct(gatherStats().red.lead));")
        assert pct == "63%"          # (3+2) / (4*2) = 62.5%, rounded

    def test_ungraded_shots_count_as_thrown_but_not_against_the_percentage(self):
        got = self._stats([shot(1, "red", "lead", user_score=4),
                           shot(3, "red", "lead")])
        lead = got["red"]["lead"]
        assert lead["thrown"] == 2 and lead["graded"] == 1
        pct = run_js(setup(doc([shot(1, "red", "lead", user_score=4),
                                shot(3, "red", "lead")])) +
                     "out(pct(gatherStats().red.lead));")
        assert pct == "100%"

    def test_nothing_graded_yet_shows_no_percentage_rather_than_zero(self):
        pct = run_js(setup(doc([shot(1, "red", "lead")])) +
                     "out(pct(gatherStats().red.lead));")
        assert pct == "—"

    def test_throw_aways_are_thrown_but_never_scored(self):
        # Curl Coach's "non scored shots": counting a throw-away as 0 would
        # punish a deliberate one.
        got = self._stats([shot(1, "red", "skip", shot_type="through",
                                user_score=0)])
        skip = got["red"]["skip"]
        assert skip["thrown"] == 1
        assert skip["graded"] == 0 and skip["sum"] == 0

    def test_shots_are_grouped_by_type_within_a_player(self):
        got = self._stats([shot(1, "red", "lead", shot_type="guard", user_score=4),
                           shot(3, "red", "lead", shot_type="draw", user_score=2)])
        types = got["red"]["lead"]["types"]
        assert set(types) == {"guard", "draw"}
        assert types["guard"]["sum"] == 4 and types["draw"]["sum"] == 2

    def test_a_manually_retyped_shot_is_grouped_where_the_charter_put_it(self):
        got = self._stats(
            [shot(1, "red", "lead", shot_type="draw", user_score=4)],
            {"0.1.1": {"shot_type": "peel"}},
        )
        assert set(got["red"]["lead"]["types"]) == {"peel"}

    def test_teams_and_positions_are_kept_apart(self):
        got = self._stats([shot(1, "red", "lead", user_score=4),
                           shot(2, "yellow", "lead", user_score=0),
                           shot(5, "red", "second", user_score=2)])
        assert got["red"]["lead"]["sum"] == 4
        assert got["yellow"]["lead"]["graded"] == 1
        assert got["yellow"]["lead"]["sum"] == 0
        assert got["red"]["second"]["sum"] == 2
        assert got["red"]["third"]["thrown"] == 0

    def test_a_zero_is_graded_not_ignored(self):
        # A missed shot has to drag the percentage down; treating 0 as
        # "ungraded" would make a bad game look perfect.
        pct = run_js(setup(doc([shot(1, "red", "lead", user_score=0),
                                shot(3, "red", "lead", user_score=4)])) +
                     "out(pct(gatherStats().red.lead));")
        assert pct == "50%"


class TestVideoTiming:
    def test_it_starts_before_the_stone_comes_into_view(self):
        got = run_js(setup(doc([shot(1, "red", "lead", t_enter_s=100.0)])) +
                     "state.leadIn = 10; out(shotVideoTime(merge(g, e, e.shots[0])));")
        assert got == 90.0

    def test_it_never_seeks_before_the_start_of_the_video(self):
        got = run_js(setup(doc([shot(1, "red", "lead", t_enter_s=4.0)])) +
                     "state.leadIn = 10; out(shotVideoTime(merge(g, e, e.shots[0])));")
        assert got == 0

    def test_a_shot_with_no_entry_time_falls_back_further_behind_its_rest(self):
        # Without an entry time the throw is somewhere before the stone
        # stopped, so the lead-in has to cover the flight as well.
        got = run_js(setup(doc([shot(1, "red", "lead", t_rest_s=100.0)])) +
                     "state.leadIn = 10; out(shotVideoTime(merge(g, e, e.shots[0])));")
        assert got == 82.0

    def test_a_blank_with_no_times_at_all_has_nowhere_to_seek(self):
        got = run_js(setup(doc([shot(1, "red", "lead", missing=True)])) +
                     "out(shotVideoTime(merge(g, e, e.shots[0])));")
        assert got is None


class TestPlacingStones:
    def test_a_stone_on_the_button_counts(self):
        got = run_js("out(stoneAt(0, 0, 'red'));")
        assert got["in_house"] is True and got["distance_to_tee"] == 0

    def test_a_stone_outside_the_rings_does_not(self):
        got = run_js("out(stoneAt(0, 3.0, 'yellow'));")
        assert got["in_house"] is False

    def test_a_placed_stone_is_marked_as_manual(self):
        got = run_js("out(stoneAt(0.5, 0.5, 'red'));")
        assert got["source"] == "manual"
        assert got["confidence"] == 1.0


class TestAnEndWithNothingDetected:
    """It still has to render rather than throw."""

    def test_there_is_no_current_shot_and_no_key_for_one(self):
        got = run_js(setup(doc([])) + "out([rawShot(), shotKey()]);")
        assert got == [None, None]

    def test_a_key_exists_as_soon_as_there_is_a_shot(self):
        got = run_js(setup(doc([shot(1, "red", "lead")], end_number=4)) +
                     "out(shotKey());")
        assert got == "0.4.1"

    def test_merging_a_missing_shot_yields_nothing(self):
        got = run_js(setup(doc([])) + "out(merge(g, e, e.shots[0]));")
        assert got is None

    def test_blank_and_type_helpers_tolerate_no_shot(self):
        got = run_js(setup(doc([])) +
                     "out([isBlank(null) ?? null, typeOf(null), "
                     "isGraded(null) ?? null, shotVideoTime(null)]);")
        assert got == [None, "unknown", None, None]


class TestHostedMode:
    def test_served_locally_the_page_is_editable(self):
        # No window.CHART in node, as when curling-score serve hosts the page.
        got = run_js("out([A.READ_ONLY, state.version]);")
        assert got == [False, None]


class TestMovingAShot:
    """Must match timeline.apply_overrides shot for shot -- the same end, the
    same moves, the same numbers, colours and labels on both sides."""

    def _shots(self):
        def s(n, color, inferred=False):
            return {"number": n, "color": color, "color_inferred": inferred,
                    "missing": inferred, "state_known": not inferred,
                    "label": f"4th end, shot {n}", "position": "lead",
                    "shot_type": "draw", "stones": []}
        return [s(1, "red"), s(2, "yellow"), s(3, "red"), s(4, "yellow"),
                s(5, "red", True), s(6, "yellow", True)]

    def _both(self, overrides):
        from curling_score import timeline
        fields = ("id", "number", "color", "label", "position", "rock_of_player",
                  "has_hammer", "thrower_slot")
        py = timeline.apply_overrides(doc(self._shots(), end_number=4), overrides)
        py = [[s.get(f) for f in fields] for s in py["games"][0]["ends"][0]["shots"]]
        js = run_js(setup(doc(self._shots(), end_number=4), overrides) +
                    f"out(mergedShots(e).map(s => {list(fields)}.map(f => s[f] ?? null)));")
        return py, js

    def test_js_and_python_agree_on_a_move_to_the_front(self):
        py, js = self._both({"0.4.5": {"before": 1}, "0.4.6": {"before": 1}})
        assert js == py
        assert [row[0] for row in js] == [5, 6, 1, 2, 3, 4]

    def test_js_and_python_agree_on_a_move_into_the_middle(self):
        py, js = self._both({"0.4.5": {"before": 2}, "0.4.6": {"before": 2},
                             "0.4.3": {"color": "yellow"}})
        assert js == py

    def test_js_and_python_agree_when_nothing_moved(self):
        py, js = self._both({"0.4.3": {"user_score": 2}})
        assert js == py
        assert js[0][0] is None   # no id until an end is reordered

    def test_the_raw_shot_follows_the_display_order(self):
        got = run_js(setup(doc(self._shots(), end_number=4),
                           {"0.4.5": {"before": 1}}) +
                     "state.si = 0; out([rawShot().number, shotKey()]);")
        assert got == [5, "0.4.5"]

    def test_a_moved_blank_borrows_a_time_from_the_nearest_seen_rock(self):
        shots = self._shots()
        shots[0]["t_enter_s"] = 2899.0
        got = run_js(setup(doc(shots, end_number=4),
                           {"0.4.5": {"before": 1}, "0.4.6": {"before": 1}}) +
                     "state.leadIn = 10; out(mergedShots(e).slice(0, 3).map(shotVideoTime));")
        assert got == [2899.0 - 90 - 10, 2899.0 - 45 - 10, 2899.0 - 10]

    def test_a_trailing_blank_is_guessed_after_the_last_seen_rock(self):
        shots = self._shots()
        shots[3]["t_enter_s"] = 3000.0
        got = run_js(setup(doc(shots, end_number=4)) +
                     "state.leadIn = 0; out(shotVideoTime(mergedShots(e)[5]));")
        assert got == 3090.0


class TestHouseViewBox:
    """The desktop crop is load-bearing: it must not drift by a character."""

    def test_the_full_view_is_the_string_the_desktop_has_always_used(self):
        got = run_js('out(houseViewBox("full", 0.605));')
        assert got == "-2.6 -2.6 5.2 8.6"

    def test_an_unusable_aspect_falls_back_to_the_full_view(self):
        for bad in ("0", "-1", "NaN", "undefined"):
            got = run_js(f'out(houseViewBox("crop", {bad}));')
            assert got == "-2.6 -2.6 5.2 8.6", bad

    def test_the_crop_is_always_as_wide_as_the_sheet(self):
        x, y, w, h = (float(v) for v in
                      run_js('out(houseViewBox("crop", 390/321));').split())
        assert (x, w) == (-2.6, 5.2)

    def test_the_crop_fills_the_box_it_is_given(self):
        x, y, w, h = (float(v) for v in
                      run_js('out(houseViewBox("crop", 390/321));').split())
        assert w / h == pytest.approx(390 / 321, abs=1e-3)

    def test_the_crop_is_centred_on_the_tee(self):
        """Centring on the tee is the whole of what the crop guarantees.

        It is *not* a guarantee that the whole house is shown. The band is
        exactly the gap between the video and the sheet, so on a short phone
        the crop is tighter than the rings and the front of the twelve-foot
        falls off the bottom. Centred means what is lost is lost evenly, and
        that nothing is drawn where the sheet would cover it. The ring itself
        survives only while the band's aspect stays at or below RING_ASPECT --
        see the two tests below, which pin that boundary.
        """
        x, y, w, h = (float(v) for v in
                      run_js('out(houseViewBox("crop", 390/321));').split())
        assert y == pytest.approx(-h / 2, abs=1e-3)

    # The crop always spans the sheet's 5.2 m of width, and the twelve-foot
    # reaches 1.829 m from the tee in each direction, so the ring fits exactly
    # when the box is 5.2 / (2 * 1.829) times wider than it is tall. On a
    # 390 px-wide phone that wants a 274 px band, which needs about 797 px of
    # dynamic viewport -- more than a 390x844 phone has once the video, the
    # header and the sheet have taken theirs.
    RING_ASPECT = 5.2 / (2 * 1.829)          # 1.4215...

    def test_the_whole_twelve_foot_is_visible_right_up_to_that_aspect(self):
        x, y, w, h = (float(v) for v in
                      run_js(f'out(houseViewBox("crop", {self.RING_ASPECT!r}));').split())
        assert y <= -1.829 and y + h >= 1.829

    def test_a_band_any_shorter_than_that_crops_into_the_twelve_foot(self):
        x, y, w, h = (float(v) for v in
                      run_js(f'out(houseViewBox("crop", {self.RING_ASPECT * 1.01!r}));').split())
        assert y > -1.829 and y + h < 1.829

    def test_the_band_a_real_phone_gets_shows_the_rings_but_not_all_of_them(self):
        # 390x844 with a 48 px header, a 219.375 px video and a 256 px sheet
        # leaves a 320.625 px band -- the whole ring. 390x700 leaves 176.625,
        # which does not, and that is the honest outcome, not a bug.
        tall = [float(v) for v in
                run_js('out(houseViewBox("crop", 390/320.625));').split()]
        short = [float(v) for v in
                 run_js('out(houseViewBox("crop", 390/176.625));').split()]
        assert tall[1] <= -1.829 and tall[1] + tall[3] >= 1.829 - 1e-3
        assert short[1] > -1.829
        assert short[1] == pytest.approx(-short[3] / 2, abs=1e-3)

    def test_a_box_taller_than_the_sheet_does_not_zoom_past_the_full_view(self):
        x, y, w, h = (float(v) for v in
                      run_js('out(houseViewBox("crop", 0.2));').split())
        assert h == 8.6


class TestPeekMode:
    """A blank's fast path is saying where the rock went, not grading it."""

    def test_a_shot_we_watched_offers_grading(self):
        got = run_js(setup(doc([shot(1, "red", "lead")])) +
                     'out(peekMode(merge(g, e, e.shots[0])));')
        assert got == "grade"

    def test_a_rock_never_seen_offers_the_order_picker(self):
        got = run_js(setup(doc([shot(1, "red", "lead", missing=True)])) +
                     'out(peekMode(merge(g, e, e.shots[0])));')
        assert got == "order"

    def test_an_unreadable_house_offers_the_order_picker(self):
        got = run_js(setup(doc([shot(1, "red", "lead", state_known=False)])) +
                     'out(peekMode(merge(g, e, e.shots[0])));')
        assert got == "order"

    def test_grading_a_blank_without_placing_stones_leaves_it_a_blank(self):
        got = run_js(setup(doc([shot(1, "red", "lead", state_known=False)]),
                           {"0.1.1": {"user_score": 3}}) +
                     'out(peekMode(merge(g, e, e.shots[0])));')
        assert got == "order"

    def test_there_being_no_shot_is_not_a_blank(self):
        got = run_js('out(peekMode(null));')
        assert got == "grade"


class TestRenumberNotice:
    """The phone has no chip strip, so the renumber has to say so itself."""

    def test_a_move_says_the_new_number(self):
        got = run_js('out(renumberNotice({number:9}, {number:3}));')
        assert "rock 3" in got

    def test_a_move_that_settled_the_colour_says_where_the_colour_came_from(self):
        got = run_js('out(renumberNotice({number:9},'
                     ' {number:3, color:"red", color_inferred:true}));')
        assert "rock 3" in got and "red" in got and "alternation" in got

    def test_a_rock_whose_colour_was_seen_does_not_claim_alternation(self):
        got = run_js('out(renumberNotice({number:9},'
                     ' {number:3, color:"red", color_inferred:false}));')
        assert "alternation" not in got

    def test_landing_back_on_the_same_number_announces_nothing(self):
        got = run_js('out(renumberNotice({number:4}, {number:4}));')
        assert got is None

    def test_a_missing_shot_announces_nothing(self):
        assert run_js('out(renumberNotice(null, {number:3}));') is None
        assert run_js('out(renumberNotice({number:3}, null));') is None
