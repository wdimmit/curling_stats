"""The viewer's own arithmetic, exercised in node.

Two things in ``app.js`` are worth testing rather than eyeballing: the override
merge, which has to agree with :func:`timeline.apply_overrides` or charting
work silently disagrees with what the next analysis bakes in, and the report
percentages, which are the numbers a player will read off and believe.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

# The framework-free core, reached through the adapter that keeps the mutable
# `state` these tests were written against. See tests/js/singleton.mjs.
CORE = Path(__file__).resolve().parent / "js/singleton.mjs"
# The stylesheet is hand-authored and deliberately outside the build, so
# the rules these tests read are still the rules that ship.
VIEWER = Path(__file__).resolve().parents[1] / "src/curling_score/viewer"
node = shutil.which("node")
pytestmark = pytest.mark.skipif(node is None, reason="node is not installed")


def run_js(body: str, *, chart=None):
    """Run ``body`` with the core's exports in scope; return its JSON output.

    ESM rather than ``require``: the core is plain ES modules, and dynamic
    ``import()`` wants a file URL -- a bare path happens to work on Linux but
    is not specified to. Everything the module exports is put on ``globalThis``
    rather than destructured by name, so a new export is testable without
    editing this harness.

    With ``chart`` given, ``window.CHART`` is set *before* the import, which is
    how the browser sees it. ``document`` stays unset: nothing reachable from
    the core may touch it, and these tests are what holds that line.
    """
    window = f"globalThis.window = {{ CHART: {json.dumps(chart)} }};\n" if chart is not None else ""
    script = (
        window
        + f"const A = await import({CORE.as_uri()!r});\n"
        "Object.assign(globalThis, A);\n"
        "function out(v){ console.log(JSON.stringify(v)); }\n" + body
    )
    proc = subprocess.run([node, "--input-type=module", "-e", script],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def run_js_with_chart(chart, body: str):
    """``run_js`` with a mode chosen. Kept as its own name so the tests that
    read better that way do not have to change to say the same thing."""
    return run_js(body, chart=chart)


class TestModes:
    """Which surface the page thinks it is on, and what that permits."""

    def test_review_is_read_only(self):
        """The invariant: /g/ can never edit, whatever else changes."""
        assert run_js_with_chart({"mode": "review", "source": "s_x"},
                                 "out([REVIEW, READ_ONLY]);") == [True, True]

    def test_a_share_link_is_read_only_but_not_review(self):
        assert run_js_with_chart({"mode": "view", "slug": "abc"},
                                 "out([REVIEW, READ_ONLY]);") == [False, True]

    def test_a_chart_link_edits(self):
        assert run_js_with_chart({"mode": "edit", "slug": "abc"},
                                 "out([REVIEW, READ_ONLY]);") == [False, False]

    def test_served_locally_everything_edits(self):
        """`curling-score serve` sets no window.CHART and must stay editable."""
        assert run_js_with_chart(None, "out([REVIEW, READ_ONLY]);") == [False, False]


class TestDirtyPayload:
    """What a merge actually puts on the wire."""

    def test_it_sends_only_the_dirty_shots(self):
        body = run_js_with_chart(
            {"mode": "edit", "merge": True},
            "state.overrides = {a:{x:1}, b:{y:2}, c:{z:3}};\n"
            "out(dirtyPayload(state.overrides, new Set(['a','c'])));")
        assert body == {"a": {"x": 1}, "c": {"z": 3}}

    def test_a_shot_edited_away_becomes_a_tombstone(self):
        """The key is gone locally, so the server has to be told to drop it."""
        body = run_js_with_chart(
            {"mode": "edit", "merge": True},
            "state.overrides = {a:{x:1}};\n"
            "out(dirtyPayload(state.overrides, new Set(['a','gone'])));")
        assert body == {"a": {"x": 1}, "gone": None}


class TestSaveUrl:
    def test_a_hosted_chart_merges(self):
        got = run_js_with_chart({"mode": "edit", "merge": True},
                                "state.version = 3; out(saveUrl());")
        assert got == "overrides.json?merge=1&v=3"

    def test_the_local_server_still_gets_whole_documents(self):
        """`curling-score serve` sets no window.CHART and cannot merge."""
        got = run_js_with_chart(None, "state.version = null; out(saveUrl());")
        assert got == "overrides.json"

    def test_a_first_save_carries_no_version(self):
        got = run_js_with_chart({"mode": "edit", "merge": True},
                                "state.version = null; out(saveUrl());")
        assert got == "overrides.json?merge=1"


class TestUnloadBeacon:
    """The tab-close save. This is the one that used to eat a teammate's work."""

    def test_a_tab_with_nothing_unsaved_sends_nothing(self):
        """It used to send the whole document, every close, forever after the
        first edit -- with no version, so it overwrote whatever it landed on."""
        got = run_js_with_chart(
            {"mode": "edit", "merge": True},
            "state.overrides = {a:{x:1}, b:{y:2}}; state.dirty = new Set();\n"
            "out(unloadBeacon());")
        assert got is None

    def test_it_carries_only_the_unsaved_shots(self):
        got = run_js_with_chart(
            {"mode": "edit", "merge": True},
            "state.overrides = {a:{x:1}, b:{y:2}}; state.dirty = new Set(['b']);\n"
            "out(unloadBeacon());")
        assert got == {"url": "overrides.json?merge=1", "body": {"b": {"y": 2}}}

    def test_a_view_link_never_beacons(self):
        got = run_js_with_chart(
            {"mode": "view", "merge": True},
            "state.overrides = {a:{x:1}}; state.dirty = new Set(['a']);\n"
            "out(unloadBeacon());")
        assert got is None

    def test_the_local_server_still_gets_the_whole_document(self):
        got = run_js_with_chart(
            None,
            "state.overrides = {a:{x:1}, b:{y:2}}; state.dirty = new Set(['b']);\n"
            "out(unloadBeacon());")
        assert got == {"url": "overrides.json", "body": {"a": {"x": 1}, "b": {"y": 2}}}


RECONCILE = """
state.doc = %s; state.gi = 0; state.ei = 0; state.si = 0;
state.overrides = %s; state.dirty = new Set(%s); state.dragging = %s;
"""


def reconcile_js(overrides, dirty, server, dragging="false"):
    document = doc([shot(1, "red", "lead")])
    return (RECONCILE % (json.dumps(document), json.dumps(overrides),
                         json.dumps(list(dirty)), dragging)
            + f"const touched = reconcile({json.dumps(server)});\n"
              "out({overrides: state.overrides, touched: touched.sort()});")


class TestReconcile:
    """Folding in a teammate's edits without stepping on your own."""

    def test_a_clean_key_takes_the_servers_value(self):
        got = run_js_with_chart({"mode": "edit", "merge": True},
                                reconcile_js({"a": {"x": 1}}, [], {"a": {"x": 9}}))
        assert got["overrides"] == {"a": {"x": 9}} and got["touched"] == ["a"]

    def test_an_unsaved_key_is_never_overwritten(self):
        """Ours is newer -- it has not reached the server yet."""
        got = run_js_with_chart({"mode": "edit", "merge": True},
                                reconcile_js({"a": {"x": 1}}, ["a"], {"a": {"x": 9}}))
        assert got["overrides"] == {"a": {"x": 1}} and got["touched"] == []

    def test_a_key_the_server_dropped_goes_too(self):
        got = run_js_with_chart({"mode": "edit", "merge": True},
                                reconcile_js({"a": {"x": 1}, "b": {"y": 2}}, [], {"a": {"x": 1}}))
        assert got["overrides"] == {"a": {"x": 1}} and got["touched"] == ["b"]

    def test_but_not_one_we_have_just_cleared_ourselves(self):
        got = run_js_with_chart({"mode": "edit", "merge": True},
                                reconcile_js({"b": {"y": 2}}, ["b"], {}))
        assert got["overrides"] == {"b": {"y": 2}} and got["touched"] == []

    def test_an_unchanged_key_is_not_reported_as_touched(self):
        """Otherwise every poll would claim a teammate had been charting."""
        got = run_js_with_chart({"mode": "edit", "merge": True},
                                reconcile_js({"a": {"x": 1}}, [], {"a": {"x": 1}}))
        assert got["touched"] == []

    def test_the_shot_being_dragged_is_left_alone(self):
        """A redraw mid-drag would yank the stone out from under the pointer."""
        got = run_js_with_chart(
            {"mode": "edit", "merge": True},
            reconcile_js({"0.1.1": {"x": 1}}, [], {"0.1.1": {"x": 9}}, dragging="true"))
        assert got["overrides"] == {"0.1.1": {"x": 1}} and got["touched"] == []


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


class TestShouldCrop:
    """Whether the band shows the crop. Two bugs have lived in this branch."""

    def call(self, **kw):
        args = {"phone": True, "editing": False, "width": 390, "height": 320}
        args.update(kw)
        return run_js(f"out(shouldCrop({json.dumps(args)}));")

    def test_a_short_wide_band_on_the_phone_is_cropped(self):
        assert self.call() is True

    def test_a_desktop_is_never_cropped_however_its_box_measures(self):
        assert self.call(phone=False) is False
        assert self.call(phone=False, height=150) is False

    def test_the_full_screen_editor_always_gets_the_whole_sheet(self):
        assert self.call(editing=True) is False

    def test_an_unmeasurable_box_is_not_cropped(self):
        # The first paint can measure a zero-height SVG; cropping to that
        # ratio is a fixed point that re-derives itself forever.
        assert self.call(height=0) is False
        assert self.call(width=0) is False

    def test_a_band_tall_enough_for_the_sheet_is_left_alone(self):
        assert self.call(height=390 * 1.3) is False
        assert self.call(height=390 * 1.3 - 1) is True


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


class TestTimingDisplay:
    """A split and a clock a player reads off and believes."""

    def test_a_clock_reads_as_minutes_and_seconds(self):
        assert run_js("out([clockText(36), clockText(185), clockText(0)])") == [
            "0:36", "3:05", "0:00"]

    def test_an_unread_clock_is_a_dash_not_a_zero(self):
        assert run_js("out(clockText(null))") == "—"

    def test_a_split_says_how_much_was_estimated(self):
        got = run_js(
            "out([splitText({long_split_s:17.4, long_split_extrapolated_m:2.3}),"
            "     splitText({long_split_s:17.4, long_split_extrapolated_m:0})])")
        assert got[0] == "17.4 s (2.3 m est.)"
        assert got[1] == "17.4 s"

    def test_an_unmeasured_split_is_a_dash(self):
        assert run_js("out(splitText({long_split_s:null}))") == "—"
        assert run_js("out(splitText(null))") == "—"

    def test_thinking_time_sums_the_ends(self):
        got = run_js("""
          state.doc = {games:[{ends:[
            {thinking_time:{red:60, yellow:30, measured_shots:4, unmeasured_shots:2}},
            {thinking_time:{red:40, yellow:20, measured_shots:5, unmeasured_shots:1}}
          ]}]};
          state.gi = 0;
          out(gatherThinking());
        """)
        assert got == {"red": 100, "yellow": 50, "measured": 9,
                       "unmeasured": 3, "estimated": 0}

    def test_an_end_with_no_clock_is_skipped_not_counted_as_zero(self):
        got = run_js("""
          state.doc = {games:[{ends:[{}, {thinking_time:{red:10, yellow:5,
            measured_shots:1, unmeasured_shots:0}}]}]};
          state.gi = 0;
          out(gatherThinking());
        """)
        assert got == {"red": 10, "yellow": 5, "measured": 1,
                       "unmeasured": 0, "estimated": 0}


class TestAnEstimatedClockSaysSo:
    """A guessed interval is worth showing and worth marking: a shot with no
    number silently shortens its team's total, and an unmarked guess is worse
    than either."""

    def test_a_seen_crossing_reads_as_a_plain_clock(self):
        assert run_js(
            "out(thinkText({thinking_time_s:74, t_tee_estimated:false}))") == "1:14"

    def test_an_assumed_crossing_is_marked(self):
        assert run_js(
            "out(thinkText({thinking_time_s:74, t_tee_estimated:true}))") == "1:14 (est.)"

    def test_no_clock_at_all_is_a_dash(self):
        assert run_js("out(thinkText({thinking_time_s:null}))") == "—"
        assert run_js("out(thinkText(null))") == "—"

    def test_the_report_counts_the_estimates_apart(self):
        got = run_js("""
          state.doc = {games:[{ends:[
            {thinking_time:{red:60, yellow:30, measured_shots:4,
                            unmeasured_shots:2, estimated_shots:1}},
            {thinking_time:{red:40, yellow:20, measured_shots:5,
                            unmeasured_shots:1, estimated_shots:2}}
          ]}]};
          state.gi = 0;
          out(gatherThinking());
        """)
        assert got["estimated"] == 3 and got["measured"] == 9


CLOCK_DOC = {
    "source": {"video_id": "v"},
    "games": [{"index": 0, "teams": {"red": {"name": None}, "yellow": {"name": None}},
               "final": {"red": 0, "yellow": 0}, "ends": [
        {"number": 1, "score": {"red": 0, "yellow": 0},
         "thinking_time": {"red": 20.0, "yellow": 40.0, "measured_shots": 3,
                           "unmeasured_shots": 1, "estimated_shots": 1},
         "shots": [
            shot(1, "red", "lead", thinking_time_s=None),
            shot(2, "yellow", "lead", thinking_time_s=30.0),
            shot(3, "red", "lead", thinking_time_s=20.0),
            shot(4, "yellow", "lead", thinking_time_s=10.0,
                 t_tee_estimated=True)]},
        {"number": 2, "score": {"red": 0, "yellow": 0},
         "thinking_time": {"red": 40.0, "yellow": 0.0, "measured_shots": 1,
                           "unmeasured_shots": 1, "estimated_shots": 0},
         "shots": [
            shot(1, "yellow", "lead", thinking_time_s=None),
            shot(2, "red", "lead", thinking_time_s=40.0)]},
    ]}],
}


def clock_setup():
    return SETUP % (json.dumps(CLOCK_DOC), "{}")


class TestTheClockChart:
    """Cumulative thinking time, which is a shape rather than a number: the
    gap between the lines at any rock is what the two teams have spent."""

    def test_a_team_s_line_steps_only_on_its_own_rocks(self):
        got = run_js(clock_setup() + "out(cumulativeThinking().points);")
        assert [p["red"] for p in got] == [0, 0, 0, 20, 20, 20, 60]
        assert [p["yellow"] for p in got] == [0, 0, 30, 30, 40, 40, 40]

    def test_it_starts_at_the_origin(self):
        got = run_js(clock_setup() + "out(cumulativeThinking().points[0]);")
        assert got["i"] == 0 and got["red"] == 0 and got["yellow"] == 0

    def test_an_interval_nobody_could_read_does_not_step(self):
        """Which is exactly why both lines are lower bounds."""
        got = run_js(clock_setup() + "out(cumulativeThinking().points);")
        assert got[1]["red"] == 0 and got[1]["yellow"] == 0

    def test_the_totals_are_the_ones_the_report_prints(self):
        got = run_js(clock_setup() +
                     "const c = cumulativeThinking();"
                     "out([c.red, c.yellow, gatherThinking().red, gatherThinking().yellow]);")
        assert got[0] == got[2] and got[1] == got[3]

    def test_the_ends_are_marked_where_they_ended(self):
        got = run_js(clock_setup() + "out(cumulativeThinking().bounds);")
        assert got == [{"i": 4, "number": 1}, {"i": 6, "number": 2}]

    def test_an_estimated_step_is_carried_through(self):
        got = run_js(clock_setup() +
                     "out(cumulativeThinking().points.filter(p => p.estimated));")
        assert len(got) == 1 and got[0]["i"] == 4

    def test_the_chart_draws_one_line_per_team(self):
        got = run_js(clock_setup() +
                     "out(chartGeometry(cumulativeThinking()).lines"
                     "      .map(l => [l.color, l.points.length]));")
        # Yellow first, so red draws over it where the two coincide.
        assert got == [["yellow", 7], ["red", 7]]

    def test_the_chart_marks_every_estimated_step(self):
        """And marks it on the line of the team that threw it, not the other."""
        got = run_js(clock_setup() +
                     "const c = cumulativeThinking();"
                     "out([chartGeometry(c).marks.map(m => m.color),"
                     "     c.points.filter(p => p.estimated).map(p => p.color)]);")
        assert len(got[0]) == 1
        assert got[0] == got[1]

    def test_a_game_with_no_clock_at_all_draws_nothing(self):
        """Better an absent chart than two flat lines implying nobody thought."""
        got = run_js(clock_setup() +
                     "out(chartGeometry({points:[{i:0,red:0,yellow:0}],"
                     "                   bounds:[], red:0, yellow:0}));")
        assert got is None

    def test_the_axis_is_labelled_in_minutes(self):
        got = run_js(clock_setup() +
                     "out(chartGeometry(cumulativeThinking()).grid"
                     "      .map(g => g.label));")
        assert got and got[0] == "0:00"
        assert all(":" in label for label in got)


class TestWhereYouAreOnTheClock:
    """Drawn beside the play, the useful part is where the viewer is: the
    chart takes an optional position so stepping through moves a marker."""

    def test_no_position_draws_no_marker(self):
        got = run_js(clock_setup() +
                     "out(chartGeometry(cumulativeThinking()).you);")
        assert got is None

    def test_a_position_draws_one(self):
        got = run_js(clock_setup() +
                     "out(chartGeometry(cumulativeThinking(), 3).you);")
        assert got is not None and got["x"] > 0

    def test_a_position_off_the_end_is_ignored_rather_than_drawn_outside(self):
        for at in ("-1", "999"):
            got = run_js(clock_setup() +
                         f"out(chartGeometry(cumulativeThinking(), {at}).you);")
            assert got is None

    def test_the_first_and_last_rock_are_both_on_the_chart(self):
        for at in ("0", "6"):
            got = run_js(clock_setup() +
                         f"out(chartGeometry(cumulativeThinking(), {at}).you);")
            assert got is not None


class TestTheBarsPerRock:
    """A cumulative curve cannot be read for "which rock took so long" -- a
    long shot is a slightly steeper step among a hundred. A bar is tall."""

    def test_one_bar_per_rock_that_was_timed(self):
        """Four of the six shots in the fixture have an interval."""
        got = run_js(clock_setup() +
                     "out(barsGeometry(cumulativeThinking()).bars.length);")
        assert got == 4

    def test_a_bar_is_its_team_s_colour(self):
        got = run_js(clock_setup() +
                     "out(barsGeometry(cumulativeThinking()).bars"
                     "      .map(b => b.color));")
        assert got.count("red") == 2 and got.count("yellow") == 2

    def test_an_assumed_interval_is_drawn_hollow(self):
        got = run_js(clock_setup() +
                     "out(barsGeometry(cumulativeThinking()).bars"
                     "      .filter(b => b.est).length);")
        assert got == 1

    def test_the_median_is_drawn_so_long_means_long_for_this_game(self):
        got = run_js(clock_setup() +
                     "const c = cumulativeThinking();"
                     "out([barsGeometry(c).median !== null, c.median, c.longest]);")
        assert got[0] is True
        assert got[1] == 30 and got[2] == 40

    def test_every_bar_says_which_rock_it_is(self):
        got = run_js(clock_setup() +
                     "out(barsGeometry(cumulativeThinking()).bars"
                     "      .map(b => b.title));")
        assert "End 1, shot 2 — 0:30" in got
        assert any("(estimated)" in t for t in got)

    def test_a_bar_carries_the_rock_it_came_from(self):
        """So that clicking one can go there; the index keys back into points."""
        got = run_js(clock_setup() +
                     "const c = cumulativeThinking();"
                     "out(barsGeometry(c).bars"
                     "      .map(b => [c.points[b.shot].end, c.points[b.shot].si]));")
        assert got == [[1, 1], [1, 2], [1, 3], [2, 1]]

    def test_a_bar_also_carries_where_to_go(self):
        """The end and shot indices ride on the bar itself, so a click needs no
        lookup and cannot key into a different game view than the one drawn."""
        got = run_js(clock_setup() +
                     "const c = cumulativeThinking();"
                     "out(barsGeometry(c).bars.map(b => [b.ei, b.si]));")
        assert got == [[0, 1], [0, 2], [0, 3], [1, 1]]

    def test_it_shares_the_ends_with_the_cumulative_chart(self):
        got = run_js(clock_setup() +
                     "const c = cumulativeThinking();"
                     "out([chartGeometry(c), barsGeometry(c)]"
                     "      .map(g => g.ticks.map(t => t.x)));")
        assert got[0] == got[1]
        assert len(got[0]) == 2

    def test_a_game_nobody_timed_draws_nothing(self):
        got = run_js(clock_setup() +
                     "out(barsGeometry({points:[{i:0}], bounds:[], red:0, yellow:0,"
                     "                  median:0, longest:0}));")
        assert got is None

    def test_it_takes_a_position_like_the_lines_do(self):
        got = run_js(clock_setup() +
                     "out(barsGeometry(cumulativeThinking(), 3).you);")
        assert got is not None and got["x"] > 0


class TestWhichTypesAreOffered:
    """The shot-type row is two levels: four groups, and the types inside the
    open one. What it does on arriving at a rock is the whole question."""

    def test_a_rock_nothing_has_typed_opens_nothing(self):
        """The detector offers four coarse guesses and is often wrong -- the
        fine type is the charter's to give -- so an untyped rock must not be
        shown a pre-opened row suggesting an answer nobody has."""
        assert run_js('out(openGroupFor("unknown", "Hit"));') is None
        assert run_js('out(openGroupFor(null, "Hit"));') is None

    def test_a_typed_rock_opens_the_group_holding_its_type(self):
        assert run_js('out(openGroupFor("peel", null));') == "Hit"
        assert run_js('out(openGroupFor("free_guard", null));') == "Guard"

    def test_it_stays_put_when_this_rock_is_in_the_group_already_open(self):
        """A run of hits is one click a rock, and the target does not move."""
        assert run_js('out(openGroupFor("hit_roll", "Hit"));') == "Hit"

    def test_it_moves_when_this_rock_is_somewhere_else(self):
        """Otherwise a typed rock shows nothing highlighted, which reads as
        ungraded when it is not."""
        assert run_js('out(openGroupFor("draw", "Hit"));') == "Draw"

    def test_every_type_can_be_reached(self):
        got = run_js("out(TYPES.map(t => [t.id, openGroupFor(t.id, null)]));")
        unreachable = [t for t, g in got if g is None and t != "unknown"]
        assert not unreachable, f"no group offers {unreachable}"


class TestTheJsGateIsTheStylesheetGate:
    """The phone shell is a stylesheet block, and the JS has to agree about
    exactly when it is in force -- the crop, the bottom sheet and the
    full-screen house editor all assume the layout the block creates. The two
    strings being equal is the whole contract, and nothing checked it until
    now: `app.js` carried a comment asking the next person to keep them in
    step, which is not the same as a test."""

    def test_phone_query_is_the_media_query_byte_for_byte(self):
        css = (VIEWER / "style.css").read_text()
        queries = re.findall(r"@media ([^{]+)\{", css)
        want = run_js("out(PHONE_QUERY);")
        assert want in [q.strip() for q in queries], (
            f"PHONE_QUERY is {want!r}, which is not one of the stylesheet's "
            f"media queries -- the phone gate has drifted")


class TestABarIsSomethingYouCanHit:
    """Found the rock that took four minutes, now go and watch it."""

    CSS = VIEWER / "style.css"

    def test_a_hollow_bar_still_takes_the_click(self):
        """An estimated interval is drawn with no fill, and an unfilled rect
        is clickable only on its 1.5 px outline -- which made every estimated
        rock a target nobody could hit."""
        css = self.CSS.read_text()
        rule = next(r for r in css.split("}") if ".bar.est" in r and "fill:none" in r)
        assert "pointer-events:all" in rule

    def test_a_filled_bar_says_it_is_clickable(self):
        css = self.CSS.read_text()
        rule = next(r for r in css.split("}") if ".clockchart .bar {" in r)
        assert "cursor:pointer" in rule
