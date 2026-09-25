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


def doc(shots, game_index=0, end_number=1, end_id=None):
    end = {"number": end_number, "score": {"red": 0, "yellow": 0}, "shots": shots}
    if end_id is not None:
        end["id"] = end_id
    # Current schema by default: this fixture stands in for an ordinary,
    # already-analysed chart in every test that is not itself about the
    # schema gate, and the gate now fails closed on anything else -- an
    # undated document reads as one that predates board reading, not as one
    # that happens to be current. Tests that want the old-schema case set
    # `schema_version` themselves (see TestAnOldChartShowsNoBoardScore).
    return {"schema_version": 4, "source": {"video_id": "v"},
            "games": [{"index": game_index, "teams": {"red": {"name": None},
                                                      "yellow": {"name": None}},
                       "final": {"red": 0, "yellow": 0},
                       "ends": [end]}]}


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

    def test_a_trimmed_end_keys_on_the_number_it_was_detected_with(self):
        # Trimming the practice off the front renumbers the ends that survive,
        # so the real first end shows as 1 while its corrections stay filed
        # under 4. timeline.end_identity is the other half of this.
        got = run_js(setup(doc([shot(1, "red", "lead")], game_index=0,
                               end_number=1, end_id=4)) +
                     "out(keyFor(g, e, e.shots[0]));")
        assert got == "0.4.1"

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


class TestTheScoreboardShowsOnlyWhatWasRead:
    """Blank and unread are different facts: one says nobody scored, the
    other says nobody posted. One mark for both would be the same dishonesty
    in a new place."""

    def test_a_blank_end_and_an_unread_end_read_differently(self):
        got = run_js(
            "out([scoreCell({red: 0, yellow: 0}, 'red'), scoreCell(null, 'red')]);"
        )
        assert got[0] != got[1]

    def test_a_scored_end_shows_its_number(self):
        got = run_js("out(scoreCell({red: 2, yellow: 0}, 'red'));")
        assert got == "2"

    def test_a_blank_end_is_not_shown_as_a_score(self):
        got = run_js("out(scoreCell({red: 0, yellow: 0}, 'red'));")
        assert got != "0"


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

    def test_every_type_counts_once_a_charter_has_scored_it(self):
        # Curl Coach exempts its "non scored shots" from the percentage. We do
        # not: every rock is scored, a throw-away included. Nothing starts
        # counting that nobody graded -- a type carries no exemption of its
        # own, so the charter pressing a number is the whole of the decision.
        got = self._stats([shot(1, "red", "skip", shot_type="through",
                                user_score=0)])
        skip = got["red"]["skip"]
        assert skip["thrown"] == 1
        assert skip["graded"] == 1 and skip["sum"] == 0

    def test_a_type_nobody_graded_is_still_only_thrown(self):
        got = self._stats([shot(1, "red", "skip", shot_type="through")])
        skip = got["red"]["skip"]
        assert skip["thrown"] == 1 and skip["graded"] == 0

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

    def test_a_reached_for_far_crossing_says_so(self):
        """`long_split_far_reach_u` is the ARRIVING end in the panel's own y
        units, set when that crossing was extrapolated rather than seen. A
        different quantity from `long_split_extrapolated_m`, which is the
        THROWING end in metres and belongs to timelines made before both lines
        were timed at the paint -- so both must render, and neither may be
        mistaken for the other."""
        got = run_js(
            "out([splitText({long_split_s:13.2, long_split_far_reach_u:0.031}),"
            "     splitText({long_split_s:13.2, long_split_far_reach_u:0})])")
        assert got[0] == "13.2 s (est.)"
        assert got[1] == "13.2 s"

    def test_the_two_estimate_markers_do_not_collide(self):
        got = run_js(
            "out(splitText({long_split_s:17.4, long_split_extrapolated_m:2.3,"
            "               long_split_far_reach_u:0.03}))")
        assert got == "17.4 s (2.3 m est.)", got

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
        assert run_js('out(openGroupFor("centre_guard", null));') == "Guard"

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


class TestASubtypeNeverRepeatsItsCategory:
    """Being made to pick "Draw" inside Draw is the same question twice. The
    category is a whole answer on its own -- the detector never offers more
    than one anyway -- and what sits under it are refinements."""

    def test_no_group_offers_its_own_name_as_a_subtype(self):
        got = run_js("""out(GROUPS.map(g =>
            [g, subtypesOf(g).map(t => t.name)]));""")
        for group, names in got:
            assert group not in names, f"{group} offers itself as a subtype"

    def test_a_guard_is_centre_or_corner_and_nothing_else(self):
        got = run_js('out(subtypesOf("Guard").map(t => t.id));')
        assert got == ["centre_guard", "corner_guard"]

    def test_the_three_real_categories_are_answers_in_themselves(self):
        """`shot_type: "draw"` is what the detector emits and what a charter
        leaves alone, so each category's own id has to be a type."""
        got = run_js("""out(Object.entries(GROUP_TYPE)
            .map(([g, id]) => [g, id, TYPE[id]?.group ?? null, !!TYPE[id]?.base]));""")
        assert got == [["Draw", "draw", "Draw", True],
                       ["Guard", "guard", "Guard", True],
                       ["Hit", "hit", "Hit", True]]

    def test_other_is_a_container_not_a_category(self):
        """A rock is never "an Other" -- its entries are whole answers."""
        assert run_js('out(GROUP_TYPE["Other"] ?? null);') is None
        assert run_js('out(subtypesOf("Other").map(t => t.id));') == [
            "through", "hogged", "not_thrown", "unknown"]

    def test_the_two_ways_out_of_play_sit_under_the_shot_they_were(self):
        """A rock through the house was a draw and a flash was a takeout, so
        each refines its own group rather than landing in Other."""
        assert run_js('out([TYPE["draw_through"]?.group ?? null, '
                      'TYPE["flashed"]?.group ?? null]);') == ["Draw", "Hit"]

    def test_no_type_is_exempt_from_the_percentage(self):
        """Every rock is scored; the charter decides by grading it or not."""
        assert run_js('out(TYPES.filter(t => t.unscored).map(t => t.id));') == []

    def test_a_type_this_table_no_longer_knows_degrades_rather_than_breaks(self):
        """Refinements get retired -- four were, none of which any of the 29
        charts on the service had ever used. A chart that did carry one must
        still open, so an unrecognised id has to fall through everywhere
        rather than be special-cased in a list that only grows."""
        got = run_js("""out([TYPE["free_guard"] ?? null,
                            openGroupFor("free_guard", "Guard"),
                            subtypesOf("Guard").some(t => t.id === "free_guard")]);""")
        # No entry, so the picker opens nothing and waits, which is the honest
        # thing to do about a rock whose type means nothing here.
        assert got == [None, None, False]

    def test_an_unknown_type_is_still_counted_as_thrown(self):
        """And it must not quietly vanish from the report: it is a rock that
        was thrown, whatever the id says."""
        got = run_js(setup(doc([shot(1, "red", "lead", shot_type="retired_thing",
                                    user_score=3)])) +
                     "const b = gatherStats().red.lead;"
                     "out([b.thrown, b.graded, Object.keys(b.types)]);")
        assert got == [1, 1, ["retired_thing"]]


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


# The real end 4 of 4RrNWSeNnMU: the end whose opening centre guard the
# detector had been missing. Rock 1 has no interval (the first rock of an end
# is never timed), rock 9 hogged and was charged nothing, and rock 13 is the
# 81-second outlier the bars are scaled against.
END_FOUR = [
    (1, "red", "guard", None), (2, "yellow", "through", 24.27),
    (3, "red", "draw", 18.28), (4, "yellow", "hit", 23.28),
    (5, "red", "guard", 16.23), (6, "yellow", "through", 19.4),
    (7, "red", "draw", 15.41), (8, "yellow", "through", 36.98),
    (9, "red", "hogged", 0.0), (10, "yellow", "hit", 53.69),
    (11, "red", "draw", 21.52), (12, "yellow", "hit", 32.03),
    (13, "red", "hit", 81.26), (14, "yellow", "through", 54.2),
    (15, "red", "guard", 41.77), (16, "yellow", "hit", 67.87),
]


def end_four(**end_kw):
    shots = []
    for n, colour, kind, secs in END_FOUR:
        shots.append(shot(n, colour, ["lead", "second", "third", "skip"][(n - 1) // 4],
                          shot_type=kind, thinking_time_s=secs,
                          t_rest_s=3300.0 + n * 60, t_enter_s=3290.0 + n * 60))
    d = doc(shots, end_number=4)
    d["games"][0]["ends"][0].update(end_kw)
    return d


class TestTheEndAsAList:
    """What a phone shows someone watching rather than charting."""

    def test_a_row_per_rock_carrying_its_share_of_the_longest_interval(self):
        got = run_js(setup(end_four()) +
                     "const r = rockRows();"
                     "out([r.length, r[12].number, r[12].name, r[12].frac,"
                     " r[1].name, Number(r[1].frac.toFixed(3))]);")
        # Rock 13 is the longest in the end, so it is the full-width bar.
        assert got == [16, 13, "Hit", 1, "Throw away", round(24.27 / 81.26, 3)]

    def test_the_first_rock_of_an_end_is_unmeasured_not_instant(self):
        """There is no previous rest to measure from, so there is no number --
        and a zero-width bar would read as a rock thrown instantly."""
        got = run_js(setup(end_four()) +
                     "const r = rockRows()[0];"
                     "out([r.secs, r.text, r.unmeasured, r.frac]);")
        assert got == [None, "—", True, 0]

    def test_a_rock_charged_nothing_is_not_a_rock_that_took_no_time(self):
        """The hogged rock's interval came out at zero, which the clock counts
        as an anomaly rather than a measurement."""
        got = run_js(setup(end_four()) +
                     "const r = rockRows()[8];"
                     "out([r.name, r.secs, r.unmeasured, r.frac]);")
        assert got == ["Hogged", None, True, 0]

    def test_bars_are_scaled_inside_the_end_never_across_the_game(self):
        """An end played under time pressure must not look quick beside a
        leisurely one, so the longest rock of *this* end is full width and the
        shape of the end is what the bars carry."""
        body = ("const r = rockRows();"
                "out([r[12].frac, Number(r[1].frac.toFixed(4))]);")
        full = run_js(setup(end_four()) + body)
        # Every interval halved: the same rock is still the longest, and every
        # bar keeps its share, so the end reads identically.
        halved = doc([shot(n, c, ["lead", "second", "third", "skip"][(n - 1) // 4],
                           shot_type=k, thinking_time_s=None if s is None else s / 2,
                           t_rest_s=3300.0 + n * 60, t_enter_s=3290.0 + n * 60)
                      for n, c, k, s in END_FOUR], end_number=4)
        assert run_js(setup(halved) + body) == full == [1, round(24.27 / 81.26, 4)]

    def test_a_type_the_table_no_longer_knows_still_names_itself(self):
        got = run_js(setup(doc([shot(1, "red", "lead", shot_type="retired_thing",
                                     thinking_time_s=12.0, t_rest_s=100.0)],
                               end_number=4)) +
                     "out(rockRows()[0].name);")
        assert got == "retired_thing"

    def test_the_row_knows_where_to_start_the_video(self):
        """Ten seconds before the rock entered, so the throw is seen and not
        just its aftermath -- the same rule the transport already uses."""
        got = run_js(setup(end_four()) + "out(rockRows()[2].tVideo);")
        assert got == 3290.0 + 3 * 60 - 10


class TestTheListFollowsTheVideo:
    """Which rock the playhead is in."""

    def test_a_rock_is_current_from_the_previous_rest_until_its_own(self):
        """That interval is what a viewer is watching: the throw, not what
        happens after it stops."""
        got = run_js(setup(end_four()) +
                     "const r = rockRows();"
                     "out([rockAt(r, r[0].tRest - 5), rockAt(r, r[0].tRest + 5),"
                     " rockAt(r, r[4].tRest + 1)]);")
        assert got == [0, 1, 5]

    def test_before_the_first_rest_it_is_the_first_rock(self):
        got = run_js(setup(end_four()) + "out(rockAt(rockRows(), 0));")
        assert got == 0

    def test_past_the_last_rest_it_stays_on_the_last_rock(self):
        got = run_js(setup(end_four()) +
                     "const r = rockRows(); out(rockAt(r, r[15].tRest + 999));")
        assert got == 15

    def test_a_rock_with_no_rest_time_cannot_bound_anything(self):
        """A missing rest would otherwise read as zero and drag the answer
        back to the top of the end."""
        got = run_js(setup(end_four()) +
                     "const r = rockRows(); r[3].tRest = null;"
                     "out(rockAt(r, r[4].tRest + 1));")
        assert got == 5

    def test_the_playhead_is_never_in_a_rock_that_was_never_seen(self):
        """A missing first rock has no rest: before the second rock's rest the
        playhead is on the second rock, not the missing one."""
        got = run_js(setup(end_four()) +
                     "const r = rockRows(); r[0].tRest = null;"
                     "out([rockAt(r, 0), rockAt(r, r[1].tRest - 5)]);")
        assert got == [1, 1]

    def test_after_a_missing_rock_mid_end_it_is_the_next_rock_seen(self):
        got = run_js(setup(end_four()) +
                     "const r = rockRows(); r[3].tRest = null;"
                     "out(rockAt(r, r[2].tRest + 1));")
        assert got == 4

    def test_a_missing_rock_shares_its_interval_with_the_next_rock_seen(self):
        got = run_js(setup(end_four()) +
                     "const r = rockRows(); r[0].tRest = null; r[3].tRest = null;"
                     "out([rockSpan(r, 0), rockSpan(r, r[2].tRest + 1), rockSpan(r, r[5].tRest + 1)]);")
        assert got == [[0, 1], [3, 4], [6, 6]]

    def test_following_leaves_the_rock_alone_while_the_playhead_is_in_its_span(self):
        src = (Path(__file__).resolve().parents[1] / "frontend/viewer/Watch.jsx").read_text()
        assert "rockSpan(rows, player.currentTime())" in src and "si < span[0] || si > span[1]" in src


class TestARowCarriesItsSplit:
    """The long split beside the shot type, now that most rocks have one."""

    def test_a_timed_rock_carries_its_split(self):
        got = run_js(setup(doc([shot(1, "red", "lead", long_split_s=14.24)],
                               end_number=4)) +
                     "out(rockRows()[0].splitText);")
        assert got == "14.2 s"

    def test_a_reached_for_crossing_says_so_in_the_panel_s_words(self):
        """The phone and the desktop detail list are one formatter, so an
        estimate reads the same wherever it is read."""
        got = run_js(setup(doc([shot(1, "red", "lead", long_split_s=13.2,
                                     long_split_far_reach_u=0.03)],
                               end_number=4)) +
                     "out([rockRows()[0].splitText,"
                     "     splitText(state.doc.games[0].ends[0].shots[0])]);")
        assert got == ["13.2 s (est.)", "13.2 s (est.)"]

    def test_a_rock_with_no_split_carries_nothing_rather_than_a_dash(self):
        """A hogged rock never crosses the far line. A dash beside every one
        would be noise, and a zero would read as a split that was timed."""
        rows = run_js(setup(end_four()) + "out(rockRows().map(r => r.splitText));")
        assert rows == [None] * 16
        got = run_js(setup(doc([shot(1, "red", "lead", long_split_s=None)],
                               end_number=4)) +
                     "out(rockRows()[0].splitText);")
        assert got is None

    def test_a_long_type_name_gives_way_before_the_split_does(self):
        """The name ellipsises and the number never does. A flex child keeps
        its content width unless told otherwise, so the name needs
        min-width: 0 to shrink and the split needs flex: none to hold."""
        block = TestAWatchLinkOnAPhoneShowsSomething().phone_block()
        rules = block.split("}")
        split = next(r for r in rules if ".wsplit {" in r)
        name = next(r for r in rules if ".wname {" in r)
        assert "flex: none" in split
        assert "min-width: 0" in name


class TestTheEndSwitcher:
    def test_it_reads_the_board_s_running_score(self):
        """Someone reading down the game wants to know who is winning -- and
        wants it to be the score the club posted, not one we worked out."""
        got = run_js(setup(end_four(hammer="yellow",
                                    score={"red": 3, "yellow": 0},
                                    running={"red": 7, "yellow": 1},
                                    thinking_time={"red": 194.47, "yellow": 311.71})) +
                     "const s = endSummary();"
                     "out([s.number, s.of, s.hammer, s.running, s.red, s.yellow]);")
        assert got == [4, 1, "yellow", {"red": 7, "yellow": 1}, "3:14", "5:12"]

    def test_an_end_the_board_never_reached_has_no_running_score(self):
        got = run_js(setup(end_four(hammer="yellow", score=None, running=None)) +
                     "out(endSummary().running);")
        assert got is None


class TestAWithheldGameSaysSoOnTheEndBar:
    """settle_board_scores (timeline.py) withholds a whole game's scores when
    the board cannot be placed against the detected ends -- a leading end
    short of a full sixteen rocks, with the board short of the ends too. That
    is recorded on ``game.scoreboard.scores_withheld``, and the end bar has
    to tell it apart from an end that was simply never posted: the board
    *was* read here, so "not posted" would be the wrong story."""

    def test_a_withheld_game_flags_the_end_summary(self):
        d = end_four(hammer="yellow", score=None, running=None)
        d["games"][0]["scoreboard"] = {"scores_withheld": "no start time"}
        got = run_js(setup(d) + "out(endSummary().scoresWithheld);")
        assert got is True

    def test_an_ordinary_unposted_end_is_not_withheld(self):
        d = end_four(hammer="yellow", score=None, running=None)
        d["games"][0]["scoreboard"] = {"scores_withheld": None}
        got = run_js(setup(d) + "out(endSummary().scoresWithheld);")
        assert got is False

    def test_a_game_with_no_scoreboard_block_at_all_is_not_withheld(self):
        d = end_four(hammer="yellow", score=None, running=None)
        got = run_js(setup(d) + "out(endSummary().scoresWithheld);")
        assert got is False

    def test_the_readable_gate_overrides_withheld_too(self):
        """An old chart never even asks: schema_version < 4 means end.score
        and game.final are the detector's old guess, and the same gate that
        keeps that guess off the screen has to keep this flag off it too."""
        d = end_four(hammer="yellow", score=None, running=None)
        d["games"][0]["scoreboard"] = {"scores_withheld": "no start time"}
        d["schema_version"] = 3
        got = run_js(setup(d) + "out(endSummary().scoresWithheld);")
        assert got is False

    def test_the_chart_panel_explains_a_withheld_score_in_place_of_the_row(self):
        """Same shape as "the wall board could not be read for this game": a
        withheld game gets a sentence instead of a table full of unread
        marks, and the sentence names the fix (a start time) rather than
        just the problem."""
        jsx = (Path(__file__).resolve().parents[1]
               / "frontend/viewer/ChartPanel.jsx").read_text()
        assert "board?.scores_withheld" in jsx
        assert "start time" in jsx

    def test_the_end_header_names_the_fix_too(self):
        jsx = (Path(__file__).resolve().parents[1]
               / "frontend/viewer/Timing.jsx").read_text()
        assert "scoresWithheld" in jsx
        assert "start time" in jsx
        # Distinct from the plain "not posted" case, and from the old-chart
        # case -- three different reasons need three different words.
        assert '"not posted"' in jsx
        assert "chart predates board reading" in jsx

    def test_the_visible_label_itself_names_the_remedy(self):
        """Minor 4: the tooltip is unreachable on a touch screen, and this bar
        is a 44px strip with room for a phrase, not a sentence. So whatever
        names the fix has to be in the label text a phone actually shows, not
        only in the ``title`` a phone can never hover -- and it has to be a
        remedy, not a restatement of "board read, ends unmatched" in the
        detector's own vocabulary."""
        jsx = (Path(__file__).resolve().parents[1]
               / "frontend/viewer/Timing.jsx").read_text()
        # The label's own scoresWithheld branch, right after the
        # "chart predates board reading" branch -- not the title= tooltip,
        # which has its own separate "scoresWithheld ?" a few lines above.
        m = re.search(
            r'chart predates board reading"\s*\n\s*:\s*scoresWithheld\s*\?\s*"([^"]+)"',
            jsx)
        assert m, "expected a plain-string scoresWithheld branch on the label"
        label = m.group(1)
        assert "start time" in label.lower()
        assert label != "board read, ends unmatched"


class TestAnOldChartShowsNoBoardScore:
    """schema_version < 4 is a chart from before the board was read at all --
    end.score and game.final held the detector's own guess in those same
    fields back then. Gating on the version, not on whether a score happens
    to be present, is what keeps that old guess from ever being redrawn as
    though the board had said it."""

    def test_a_current_document_is_readable(self):
        got = run_js("out([boardReadable({schema_version: 4}), "
                     "boardReadable({schema_version: 5})]);")
        assert got == [True, True]

    def test_an_old_document_is_not(self):
        got = run_js("out([boardReadable({schema_version: 1}), "
                     "boardReadable({schema_version: 3})]);")
        assert got == [False, False]

    def test_a_document_with_no_version_at_all_is_refused_not_assumed_current(self):
        """The gate must fail closed: a document we cannot date is far more
        likely to predate board reading than not, since the field is new. An
        undated document's `score`/`final` may still be the detector's old
        inferred numbers, so treating "unknown" as "current" would let those
        numbers straight through -- the exact leak this gate exists to stop.
        (`doc.schema_version < 4` alone gets this backwards: `undefined < 4`
        is `false`, so its negation reads an absent version as readable.)"""
        assert run_js("out(boardReadable({}));") is False

    def test_null_and_non_numeric_versions_are_also_refused(self):
        """Whatever shape arrives over the wire in this field, only an actual
        number of 4 or more passes -- not just "not less than 4"."""
        got = run_js("out([boardReadable({schema_version: null}), "
                     "boardReadable({schema_version: 'bogus'})]);")
        assert got == [False, False]

    def test_the_gate_overrides_a_populated_running_score(self):
        """Not "no running score was posted" -- the field is populated, and
        still must not reach the screen, because on this document it is the
        detector's guess wearing the board's name."""
        d = end_four(hammer="yellow", score={"red": 3, "yellow": 0},
                    running={"red": 7, "yellow": 1})
        d["schema_version"] = 3
        got = run_js(setup(d) + "const s = endSummary();"
                     "out([s.score, s.running, s.boardReadable]);")
        assert got == [None, None, False]


class TestAWatchLinkOnAPhoneShowsSomething:
    """The bug this layout was written for.

    The phone block hides ``#chart``'s headings, ``<dl>`` and ``<details>``
    unconditionally, to buy room for the grade row. That took #detail,
    #clockBox and #scoreBox off the phone in *every* mode, and review also
    hides #grading -- everything the sheet holds -- so a /g/ link rendered a
    256px empty panel. 1457 tests passed throughout, because nothing asserts
    on the rendered phone DOM.

    These read the stylesheet, which is the only guard available without a
    browser: `pytest` has to stay green inside Dockerfile.api, where there is
    no node, let alone Chrome.
    """

    CSS = VIEWER / "style.css"

    def phone_block(self):
        css = self.CSS.read_text()
        want = run_js("out(PHONE_QUERY);")
        at = css.index(f"@media {want}")
        depth, i = 0, css.index("{", at)
        for j in range(i, len(css)):
            depth += (css[j] == "{") - (css[j] == "}")
            if depth == 0:
                return css[i:j]
        raise AssertionError("the phone media query is never closed")

    def test_the_read_only_surfaces_get_a_layout_of_their_own(self):
        block = self.phone_block()
        for mode in ("view", "review"):
            assert f'body[data-mode="{mode}"] #watch' in block, (
                f"{mode} on a phone has no watch layout, so it is showing the "
                f"charting shell with the grading cut out")

    def test_the_rock_list_is_not_swept_up_by_the_chart_panel_s_blanket_hide(self):
        """#watch is a sibling of #chart, not a child, so the rule that hides
        the panel's own furniture cannot reach it. Stated as a test because
        the two live a dozen lines apart."""
        block = self.phone_block()
        blanket = next(r for r in block.split("}") if "#chart dl" in r)
        for part in blanket.split(","):
            assert "#watch" not in part and ".wlist" not in part

    def test_the_list_row_height_matches_what_the_component_scrolls_by(self):
        """Timing.jsx keeps the current row in view with ROW_H, because
        scrollIntoView would scroll the fixed shell around it. If the two
        disagree, following drifts a row further out every rock."""
        block = self.phone_block()
        rule = next(r for r in block.split("}") if ".wrow {" in r)
        assert "height: 56px" in rule
        js = (Path(__file__).resolve().parents[1]
              / "frontend/viewer/Timing.jsx").read_text()
        assert "const ROW_H = 56;" in js

    def test_the_player_is_the_one_thing_the_layout_leaves_alone(self):
        """Watch mode hides main's children, and #playCard must be the
        exception: reparenting or unmounting it costs the iframe."""
        block = self.phone_block()
        rule = next(r for r in block.split("}")
                    if 'body[data-mode="review"] main > *' in r)
        assert ":not(#playCard)" in rule


class TestTheSkipsBroom:
    """Drawn only from numbers the timeline actually has: an old chart, or a
    shot with no held pad, draws nothing rather than a pad at the tee."""

    def mark(self, s):
        return run_js(f"out(broomMark({json.dumps(s)}));")

    def test_a_broom_links_to_the_stone_this_shot_left(self):
        s = shot(3, "red", "lead", target_broom={"x": 0.6, "y": -0.2},
                 stones=[{"color": "yellow", "x": 1.0, "y": 1.0},
                         {"color": "red", "x": 0.3, "y": 0.4}],
                 delivered_stone_index=1)
        assert self.mark(s) == {"x": 0.6, "y": -0.2, "to": {"x": 0.3, "y": 0.4}}

    def test_no_delivered_stone_is_a_broom_with_no_link(self):
        s = shot(3, "red", "lead", target_broom={"x": 0.6, "y": -0.2})
        assert self.mark(s) == {"x": 0.6, "y": -0.2, "to": None}

    def test_an_old_chart_or_a_null_broom_draws_nothing(self):
        assert self.mark(shot(3, "red", "lead")) is None
        assert self.mark(shot(3, "red", "lead", target_broom=None)) is None

    def test_a_malformed_broom_draws_nothing(self):
        assert self.mark(shot(3, "red", "lead", target_broom={"x": "0.6", "y": 1})) is None

    def test_a_guard_call_past_the_house_view_is_not_clamped(self):
        """houseViewBox stops at y = 6.0; a pad near the hog line is drawn
        where it was, outside the box, never moved onto the ice."""
        s = shot(3, "red", "lead", target_broom={"x": 0.1, "y": 6.2})
        assert self.mark(s) == {"x": 0.1, "y": 6.2, "to": None}

    def test_the_pad_is_drawn_upright(self):
        """The user's call: the pad stands vertical on the house diagram --
        narrow across the sheet, long along it -- centred on the broom point."""
        src = (Path(__file__).resolve().parents[1] / "frontend/viewer/House.jsx").read_text()
        broom = src[src.index("function Broom("):src.index("function Stones(")]
        assert "x={m.x - 0.04} y={m.y - 0.11} width={0.08} height={0.22}" in broom

    def test_the_house_draws_it_with_the_track_and_under_the_stones(self):
        src = (Path(__file__).resolve().parents[1] / "frontend/viewer/House.jsx").read_text()
        i_broom, i_stones = src.index("<Broom shot={shot}"), src.index("<Stones shot={shot}")
        assert "showTrack && <Broom" in src and i_broom < i_stones


class TestGhostStones:
    """The house draws where the stones a rock disturbed sat before it, from
    `house_delta` -- but only what the house as it stands still agrees with."""

    def ghosts(self, s):
        return run_js(f"out(ghostStones({json.dumps(s)}));")

    def hit(self, **kw):
        """s_0NOnuMHZoSp23r6n4 end 1 rock 14: took out two reds, rolled on."""
        base = dict(
            delivered_stone_index=0,
            stones=[{"color": "yellow", "x": 1.2298, "y": 2.1359}],
            house_delta={"added": [{"color": "yellow", "x": 1.2298, "y": 2.1359}],
                         "removed": [{"color": "red", "x": -0.2534, "y": 1.5078},
                                     {"color": "red", "x": 0.1764, "y": 2.3061}], "moved": []})
        base.update(kw)
        return shot(14, "yellow", "skip", **base)

    def test_a_stone_knocked_out_is_a_ghost_with_nowhere_to_go(self):
        assert self.ghosts(self.hit()) == [
            {"color": "red", "x": -0.2534, "y": 1.5078, "to": None},
            {"color": "red", "x": 0.1764, "y": 2.3061, "to": None}]

    def test_a_moved_stone_is_a_ghost_where_it_sat_linked_to_where_it_went(self):
        s = self.hit(stones=[{"color": "red", "x": 0.0, "y": 0.9}],
                     house_delta={"added": [], "removed": [], "moved": [
                         {"color": "red", "x": 0.0, "y": 0.9, "from_x": 0.18, "from_y": 2.31,
                          "distance_m": 1.42}]})
        assert self.ghosts(s) == [{"color": "red", "x": 0.18, "y": 2.31, "to": {"x": 0.0, "y": 0.9}}]

    def test_nothing_without_a_diff_or_on_a_blank(self):
        assert self.ghosts(self.hit(house_delta=None)) == []
        assert self.ghosts(self.hit(state_known=False)) == []
        assert run_js("out(ghostStones(null));") == []

    def test_a_move_the_edited_house_no_longer_shows_is_dropped(self):
        s = self.hit(stones=[{"color": "red", "x": 1.5, "y": 0.2}],
                     house_delta={"added": [], "removed": [], "moved": [
                         {"color": "red", "x": 0.0, "y": 0.9, "from_x": 0.18, "from_y": 2.31,
                          "distance_m": 1.42}]})
        assert self.ghosts(s) == []

    def test_a_removal_whose_stone_is_back_by_hand_is_dropped(self):
        s = self.hit(stones=[{"color": "yellow", "x": 1.2298, "y": 2.1359},
                             {"color": "red", "x": 0.2, "y": 2.3, "source": "manual"}])
        assert self.ghosts(s) == [{"color": "red", "x": -0.2534, "y": 1.5078, "to": None}]

    def test_the_other_colour_on_the_spot_does_not_count(self):
        s = self.hit(stones=[{"color": "yellow", "x": 0.18, "y": 2.3}])
        assert len(self.ghosts(s)) == 2

    def test_entries_without_numbers_are_skipped(self):
        s = self.hit(stones=[{"color": "red", "x": 1.2, "y": 2.1}], house_delta={"added": [], "moved": [
            {"color": "red", "x": 1.2, "y": 2.1, "from_x": None, "from_y": 2.0}],
            "removed": [{"color": "red", "x": "0.2", "y": 1.0}]})
        assert self.ghosts(s) == []

    def test_the_house_draws_them_with_the_track_and_under_the_stones(self):
        src = (Path(__file__).resolve().parents[1] / "frontend/viewer/House.jsx").read_text()
        i_ghosts, i_stones = src.index("<Ghosts shot={shot}"), src.index("<Stones shot={shot}")
        assert "showTrack && <Ghosts" in src and i_ghosts < i_stones
        ghosts = src[src.index("function Ghosts("):src.index("function Stones(")]
        assert 'pointerEvents="none"' in ghosts


class TestLineFigures:
    """The Detail pane's six figures, from schema 6's `line`."""

    DOC6 = {"schema_version": 6}

    def figs(self, s, doc=None):
        return run_js(f"out(lineFigures({json.dumps(s)}, {json.dumps(doc if doc is not None else self.DOC6)}));")

    def measured(self, **line):
        base = {"start": {"x": -0.23, "y": 38.07},
                "at_hog": {"x": -0.757, "offset_m": -0.163},
                "at_broom": {"x": -2.359, "miss_m": -0.712},
                "side": "wide", "curl": "right", "confirmed": True,
                "hog_path": [[28.35, -0.757], [25.0, -0.95]],
                "path": [[20.0, -1.18], [1.35, -1.15]], "fit": {"n": 53, "rms_m": 0.004}}
        base.update(line)
        return shot(11, "yellow", "third", target_broom={"x": -1.647, "y": 0.17},
                    long_split_s=13.79, delivered_stone_index=0,
                    stones=[{"color": "yellow", "x": -1.1529, "y": 1.3486}], line=base)

    def by_key(self, got):
        return {f["key"]: f for f in got["figures"]}

    def test_a_wide_throw_confirmed_from_behind_the_thrower(self):
        f = self.by_key(self.figs(self.measured()))
        assert (f["broom"]["value"], f["broom"]["note"], f["broom"]["tick"]) == (
            "2 ft 4 in wide", "confirmed from behind the thrower", "confirmed")
        assert (f["hack"]["value"], f["hack"]["note"]) == ("Left", "stone set 9 in left of centre")
        # From the left hack's foothold, (-0.152, 38.405), to the broom: that
        # line is at x -0.545 at the hog line, and the rock at -0.757.
        assert (f["hog"]["value"], f["hog"]["note"]) == ("8 in wide", "of the hack-to-broom line")
        assert (f["weight"]["value"], f["curl"]["value"]) == ("13.8 s", "3 ft 9 in")
        assert (f["rest"]["value"], f["rest"]["note"]) == ("12-foot", "1.8 m from the button")

    def test_an_estimated_split_says_so_in_the_weight_note(self):
        # long_split_far_reach_u > 0: the arriving end was reached for, not seen.
        reach = dict(self.measured(), long_split_far_reach_u=0.4)
        f = self.by_key(self.figs(reach))
        assert (f["weight"]["value"], f["weight"]["note"]) == (
            "13.8 s", "hog line to hog line, estimated")

        # long_split_extrapolated_m > 0.05: the throwing end was extrapolated.
        extrap = dict(self.measured(), long_split_extrapolated_m=0.8)
        f = self.by_key(self.figs(extrap))
        assert (f["weight"]["value"], f["weight"]["note"]) == (
            "13.8 s", "hog line to hog line, estimated")

        # Neither field set: the split reads as measured, same as before.
        f = self.by_key(self.figs(self.measured()))
        assert (f["weight"]["value"], f["weight"]["note"]) == ("13.8 s", "hog line to hog line")

    def test_an_offset_is_in_feet_and_inches_to_the_nearest_inch(self):
        got = run_js('out([0.0254, -0.3048, 0.712, 0.3302, 2.4892].map(feetInches));')
        assert got == ["1 in", "1 ft", "2 ft 4 in", "1 ft 1 in", "8 ft 2 in"]

    def test_within_ten_centimetres_is_on_the_broom(self):
        f = self.by_key(self.figs(self.measured(at_broom={"x": -1.6, "miss_m": 0.06})))
        assert f["broom"]["value"] == "On the broom"

    def test_hidden_from_behind_the_thrower_keeps_the_number_and_says_so(self):
        f = self.by_key(self.figs(self.measured(confirmed=None)))
        assert (f["broom"]["value"], f["broom"]["note"], f["broom"]["tick"], f["broom"]["dim"]) == (
            "2 ft 4 in wide", "not confirmed: hidden from behind the thrower", "unseen", False)

    def test_a_check_that_disagrees_greys_the_number(self):
        f = self.by_key(self.figs(self.measured(confirmed=False)))
        assert (f["broom"]["note"], f["broom"]["dim"]) == ("the camera behind the thrower disagrees", True)

    def test_no_curl_direction_says_left_or_right(self):
        f = self.by_key(self.figs(self.measured(side=None, curl=None,
                                                at_broom={"x": -1.35, "miss_m": 0.30})))
        assert f["broom"]["value"] == "1 ft right"

    def test_an_older_chart_predates_the_measurement(self):
        got = self.figs(self.measured(), {"schema_version": 5})
        assert (got["predates"], got["reason"]) == (True, "This chart predates line measurement")

    def test_a_chart_with_no_schema_version_predates_it_too(self):
        got = self.figs(self.measured(), {})
        assert (got["predates"], got["reason"]) == (True, "This chart predates line measurement")

    def test_each_reason_for_no_line(self):
        cases = [(shot(1, "red", "lead", missing=True, target_broom={"x": 0, "y": 0}), "This rock was never seen"),
                 (shot(1, "red", "lead"), "No broom was held still before the release"),
                 (shot(1, "red", "lead", target_broom={"x": 0.5, "y": 0.0}, line=None),
                  "The hog-line camera lost this rock")]
        for s, why in cases:
            got = self.figs(s)
            f = self.by_key(got)
            assert (got["predates"], got["reason"], f["broom"]["value"], f["broom"]["note"]) == (False, why, "–", why)
            assert (f["hog"]["value"], f["hog"]["note"]) == ("–", why)

    def test_the_house_caption_says_where_it_stopped(self):
        assert run_js(f"out(houseCaption({json.dumps(self.measured())}));") == (
            "Stopped 1.8 m from the button, in the 12-foot")

    def test_there_is_no_centre_hack(self):
        f = self.by_key(self.figs(self.measured(start={"x": -0.08, "y": 38.07})))
        assert (f["hack"]["value"], f["hack"]["note"]) == ("Left", "stone set 3 in left of centre")

    def test_a_stone_on_the_centre_line_still_names_the_player_s_hack(self):
        s = dict(self.measured(start={"x": 0.01, "y": 38.07}), hack={"side": "left", "x": -0.003})
        f = self.by_key(self.figs(s))
        assert (f["hack"]["value"], f["hack"]["note"]) == ("Left", "stone set on the centre line")

    def test_the_right_hack_measures_the_hog_line_from_the_right_foothold(self):
        # (0.152, 38.405) to the broom (0.5, 0.2) is at x 0.244 at the hog
        # line; the rock at 0.40 is 6 in off it, away from a curl to the left.
        s = dict(self.measured(start={"x": 0.15, "y": 38.07}, at_hog={"x": 0.40, "offset_m": 0.0},
                               at_broom={"x": 0.9, "miss_m": 0.4}, curl="left", side="wide"),
                 target_broom={"x": 0.5, "y": 0.2}, hack={"side": "right", "x": 0.15})
        f = self.by_key(self.figs(s))
        assert (f["hack"]["value"], f["hack"]["note"]) == ("Right", "stone set 6 in right of centre")
        assert f["hog"]["value"] == "6 in wide"

    def test_a_rock_not_seen_before_the_push_takes_its_player_s_hack(self):
        s = dict(self.measured(start=None), hack={"side": "left", "x": -0.2})
        f = self.by_key(self.figs(s))
        assert (f["hack"]["value"], f["hack"]["note"]) == ("Left", "as on this player's other rocks")
        assert f["hog"]["value"] == "8 in wide"

    def test_without_a_hack_neither_figure_is_guessed(self):
        f = self.by_key(self.figs(self.measured(start=None)))
        assert (f["hack"]["value"], f["hack"]["note"]) == ("–", "not seen before the push")
        assert (f["hog"]["value"], f["hog"]["note"]) == ("–", "needs the hack")

    def test_a_rock_with_no_line_still_shows_its_player_s_hack(self):
        s = shot(3, "red", "lead", target_broom={"x": 0.5, "y": 0.0}, line=None,
                 hack={"side": "right", "x": 0.12})
        f = self.by_key(self.figs(s))
        assert (f["hack"]["value"], f["hack"]["note"]) == ("Right", "as on this player's other rocks")
        assert f["hog"]["value"] == "–"


class TestCurlStopsAtAHit:
    """Curl is measured to where the rock stopped, or to where it hit a stone
    its throw disturbed -- whichever came first."""

    def figs(self, s):
        return {f["key"]: f for f in run_js(f"out(lineFigures({json.dumps(s)}, {{schema_version: 6}}));")["figures"]}

    def s_0NOn_e1_s14(self, **kw):
        """A hit on the broom (s_0NOnuMHZoSp23r6n4, end 1, rock 14): on its line
        until it took out the red at (0.18, 2.31), then rolled 0.9 m right."""
        base = dict(
            target_broom={"x": 0.3442, "y": 2.5778}, delivered_stone_index=0,
            stones=[{"color": "yellow", "x": 1.2298, "y": 2.1359}],
            house_delta={"added": [{"color": "yellow", "x": 1.2298, "y": 2.1359}],
                         "removed": [{"color": "red", "x": -0.2534, "y": 1.5078},
                                     {"color": "red", "x": 0.1764, "y": 2.3061}], "moved": []},
            track=[[833.8, 0.208, 3.002], [833.9, 0.214, 2.839], [834.0, 0.221, 2.663],
                   [834.1, 0.236, 2.513], [834.2, 0.278, 2.494], [834.3, 0.323, 2.481]],
            line={"start": {"x": -0.0619, "y": 38.035}, "at_hog": {"x": 0.0536, "offset_m": 0.0045},
                  "at_broom": {"x": 0.3391, "miss_m": -0.0051}, "side": "wide", "curl": "right",
                  "confirmed": None, "hog_path": [], "path": [[7.63, 0.243], [2.23, 0.308]]})
        base.update(kw)
        return shot(14, "yellow", "skip", **base)

    def test_a_hit_is_measured_to_just_before_it_touched(self):
        # 833.9 s is the last sample more than two radii and 10 cm from the red.
        f = self.figs(self.s_0NOn_e1_s14())
        assert (f["curl"]["value"], f["curl"]["note"]) == ("5 in", "from its line to where it hit a stone")

    def test_without_the_hit_it_would_be_measured_to_rest(self):
        f = self.figs(self.s_0NOn_e1_s14(house_delta=None))
        assert (f["curl"]["value"], f["curl"]["note"]) == ("2 ft 11 in", "from its line to where it stopped")

    def test_a_stone_it_never_reached_is_not_a_hit(self):
        far = {"added": [], "removed": [{"color": "red", "x": -1.4, "y": 0.5}], "moved": []}
        f = self.figs(self.s_0NOn_e1_s14(house_delta=far))
        assert f["curl"]["note"] == "from its line to where it stopped"

    def test_a_moved_stone_counts_from_where_it_sat(self):
        moved = {"added": [], "removed": [], "moved": [
            {"color": "red", "x": 0.0, "y": 0.9, "from_x": 0.1764, "from_y": 2.3061, "distance_m": 1.4}]}
        f = self.figs(self.s_0NOn_e1_s14(house_delta=moved))
        assert (f["curl"]["value"], f["curl"]["note"]) == ("5 in", "from its line to where it hit a stone")

    def test_without_a_track_the_path_from_behind_the_thrower_finds_it(self):
        # The path's point at 2.23 m is already on the red; the one before is
        # 7.63 m out, 4 cm left of the line.
        f = self.figs(self.s_0NOn_e1_s14(track=None))
        assert (f["curl"]["value"], f["curl"]["note"]) == ("2 in", "from its line to where it hit a stone")

    def test_a_hit_before_the_rock_was_seen_has_no_curl(self):
        f = self.figs(self.s_0NOn_e1_s14(track=[[834.1, 0.236, 2.513], [834.2, 0.278, 2.494]],
                                         line=dict(self.s_0NOn_e1_s14()["line"], path=[])))
        assert (f["curl"]["value"], f["curl"]["note"]) == ("–", "hit a stone before it was seen")

    def test_the_turn_comes_from_before_the_hit_too(self):
        """Its line misses the broom 30 cm left. The pipeline read the turn off
        the roll after the hit (right, so wide); before the hit it had curled
        4 in left, so the miss is on the side it curls toward."""
        line = {"start": {"x": 0.0, "y": 38.0}, "at_hog": {"x": 0.0536, "offset_m": 0.0},
                "at_broom": {"x": 0.0442, "miss_m": -0.30}, "curl": "right", "confirmed": None,
                "hog_path": [], "path": []}
        s = shot(9, "red", "third", target_broom={"x": 0.3442, "y": 2.5778}, line=line,
                 delivered_stone_index=0, stones=[{"color": "red", "x": 1.0, "y": 2.1}],
                 house_delta={"added": [], "removed": [{"color": "yellow", "x": -0.10, "y": 2.10}], "moved": []},
                 track=[[0.0, -0.02, 4.0], [0.1, -0.04, 3.4], [0.2, -0.056, 2.7],
                        [0.3, -0.06, 2.4], [0.4, 0.3, 2.3], [0.5, 0.8, 2.2]])
        f = self.figs(s)
        assert (f["curl"]["value"], f["broom"]["value"]) == ("4 in", "1 ft narrow")
        assert self.figs(dict(s, house_delta=None))["broom"]["value"] == "1 ft wide"


def lined(number, color, slot, x, curl=None):
    """A rock whose stone sat at `x` before the push."""
    return shot(number, color, "lead", thrower_slot=slot, target_broom={"x": 0.0, "y": 0.0},
                line={"start": {"x": x, "y": 38.0}, "curl": curl,
                      "at_hog": {"x": 0.0, "offset_m": 0.0}, "at_broom": {"x": 0.0, "miss_m": 0.0}})


class TestPlayerHacks:
    """One hack per player per game, from where their stones sat."""

    def hacks(self, shots):
        return run_js(f"out(playerHacks({json.dumps(shots)}));")

    def test_each_player_is_called_on_their_own_stones(self):
        got = self.hacks([lined(1, "red", 1, -0.10), lined(3, "red", 1, -0.12),
                          lined(2, "yellow", 1, 0.15), lined(5, "red", 2, 0.10)])
        assert {k: v["side"] for k, v in got.items()} == {"red|1": "left", "yellow|1": "right", "red|2": "right"}

    def test_the_turns_are_weighed_equally(self):
        """Stones that curl right sit further to the thrower's left, whichever
        hack: five curl-left rocks must not outvote the one curl-right one."""
        shots = [lined(n, "red", 3, 0.02, "left") for n in (9, 11, 25, 27, 41)] + [lined(43, "red", 3, -0.03, "right")]
        got = self.hacks(shots)["red|3"]
        assert got["side"] == "left" and got["x"] == pytest.approx(-0.005)

    def test_a_hit_counts_with_the_turn_it_had_before_the_hit(self):
        """The pipeline read this hit as curling right off its roll after the
        hit; before the hit it curled left. Counted right, the player would
        be called right."""
        hit = shot(9, "red", "third", thrower_slot=3, target_broom={"x": 0.3442, "y": 2.5778},
                   line={"start": {"x": -0.08, "y": 38.0}, "curl": "right",
                         "at_hog": {"x": 0.0536, "offset_m": 0.0}, "at_broom": {"x": 0.0442, "miss_m": -0.30},
                         "path": []},
                   house_delta={"added": [], "removed": [{"color": "yellow", "x": -0.10, "y": 2.10}], "moved": []},
                   track=[[0.0, -0.02, 4.0], [0.1, -0.04, 3.4], [0.2, -0.056, 2.7], [0.3, -0.06, 2.4]])
        shots = [hit, lined(11, "red", 3, 0.01, "left"), lined(25, "red", 3, 0.02, "right"),
                 lined(27, "red", 3, 0.03, "right")]
        got = self.hacks(shots)["red|3"]
        assert got["side"] == "left" and got["x"] == pytest.approx(-0.005)

    def test_one_turn_only_is_its_plain_median(self):
        got = self.hacks([lined(1, "red", 1, x, "right") for x in (-0.02, 0.01, 0.03)])["red|1"]
        assert got["side"] == "right" and got["x"] == pytest.approx(0.01)

    def test_rocks_without_a_start_are_not_counted(self):
        shots = [lined(1, "red", 1, 0.2), dict(lined(3, "red", 1, 0.0), line=None),
                 dict(lined(5, "red", 1, 0.0), missing=True), shot(7, "red", "lead", thrower_slot=1)]
        assert self.hacks(shots) == {"red|1": {"side": "right", "x": 0.2}}

    def test_dead_centre_is_the_left_hack(self):
        assert self.hacks([lined(1, "red", 1, 0.0)])["red|1"]["side"] == "left"

    def test_the_game_view_gives_every_rock_its_player_s_hack(self):
        shots = [lined(1, "red", 1, -0.1), lined(2, "yellow", 1, 0.2),
                 dict(lined(3, "red", 1, 0.0), line=None), shot(4, "yellow", "lead", thrower_slot=1)]
        got = run_js(setup(doc(shots)) + "out(gameView().ends[0].shots.map(s => s.hack?.side ?? null));")
        assert got == ["left", "right", "left", "right"]

    def test_a_player_is_called_across_the_whole_game(self):
        d = doc([lined(1, "red", 1, -0.1)])
        first = d["games"][0]["ends"][0]
        d["games"][0]["ends"].append(dict(first, number=2, shots=[shot(1, "red", "lead", thrower_slot=1)]))
        got = run_js(setup(d) + "out(gameView().ends[1].shots[0].hack);")
        assert got == {"side": "left", "x": -0.1}


def two_ends():
    d = doc([shot(i, "red" if i % 2 else "yellow", "lead") for i in range(1, 4)])
    first = d["games"][0]["ends"][0]
    d["games"][0]["ends"].append(dict(first, number=2, shots=[shot(i, "red", "lead") for i in (1, 2)]))
    return d


class TestSteppingAndTheHash:
    def test_a_step_off_the_end_s_last_rock_is_the_next_end_s_first(self):
        got = run_js(setup(two_ends()) + "state.si = 2; out(stepRock(1));")
        assert got == {"ei": 1, "si": 0}

    def test_a_step_back_from_an_end_s_first_is_the_last_before_it(self):
        assert run_js(setup(two_ends()) + "state.ei = 1; state.si = 0; out(stepRock(-1));") == {"ei": 0, "si": 2}

    def test_past_the_game_s_last_rock_there_is_nowhere_to_go(self):
        assert run_js(setup(two_ends()) + "state.ei = 1; state.si = 1; out(stepRock(1));") is None

    def test_an_empty_end_is_stepped_over(self):
        view = {"ends": [{"shots": [1, 2, 3]}, {"shots": []}, {"shots": [1, 2]}]}
        got = run_js(f"out([stepRockIn({json.dumps(view)}, 0, 2, 1), stepRockIn({json.dumps(view)}, 2, 0, -1)]);")
        assert got == [{"ei": 2, "si": 0}, {"ei": 0, "si": 2}]

    def test_the_tab_and_rock_round_trip_through_the_hash(self):
        got = run_js(setup(two_ends()) + (
            'const h = formatHash({tab: "detail", g: 1, e: 2, s: 2});'
            'out([h, cursorFromHash(parseHash(h))]);'))
        assert got == ["#tab=detail&e=2&s=2", {"gi": 0, "ei": 1, "si": 1}]

    def test_a_hash_naming_no_such_rock_or_tab_is_ignored(self):
        got = run_js(setup(two_ends()) + (
            'out([cursorFromHash(parseHash("#tab=timing&e=9&s=1")), parseHash("#tab=bogus&e=x")]);'))
        assert got == [None, {}]

    def test_a_link_made_off_the_phone_names_no_tab(self):
        got = run_js(setup(two_ends()) + (
            'const h = formatHash({g: 1, e: 2, s: 1});'
            'out([h, parseHash(h), cursorFromHash(parseHash(h))]);'))
        assert got == ["#e=2&s=1", {"e": 2, "s": 1}, {"gi": 0, "ei": 1, "si": 0}]

    def test_a_link_made_after_a_move_opens_the_moved_rock(self):
        shots = [shot(i, "red" if i % 2 else "yellow", "lead") for i in range(1, 7)]
        got = run_js(setup(doc(shots, end_number=4), {"0.4.5": {"before": 1}}) + (
            'const c = cursorFromHash(parseHash(formatHash({e: 4, s: 1})));'
            'state.si = c.si; out([c, rawShot().number]);'))
        assert got == [{"gi": 0, "ei": 0, "si": 0}, 5]

    def test_the_view_only_link_carries_the_rock(self):
        got = run_js(
            'out([withHash("https://x/s/ab/", "#e=3&s=5"),'
            ' withHash("https://x/s/ab/#e=1&s=1", "#tab=house&e=3&s=5"),'
            ' withHash("https://x/s/ab/#e=1&s=1", "")]);')
        assert got == ["https://x/s/ab/#e=3&s=5", "https://x/s/ab/#tab=house&e=3&s=5",
                       "https://x/s/ab/"]


class TestSwipe:
    def step(self, dx, dy, x0=200):
        return run_js(f"out(swipeStep({dx}, {dy}, {x0}));")

    def test_left_is_the_next_rock_and_right_the_one_before(self):
        assert (self.step(-80, 5), self.step(80, 5)) == (1, -1)

    def test_a_vertical_scroll_is_not_a_swipe(self):
        assert (self.step(60, 70), self.step(30, 0)) == (0, 0)

    def test_the_browser_s_back_gesture_edge_is_left_alone(self):
        assert self.step(80, 0, x0=12) == 0


class TestStripAndTrack:
    SHOT = TestLineFigures().measured()

    def test_the_broom_and_the_line_s_end_land_where_the_metres_say(self):
        g = run_js(f"out(stripGeometry({json.dumps(self.SHOT)}));")
        assert g["broom"]["x"] == pytest.approx(75 + (-1.647) * 150 / 4.75, abs=0.05)
        assert g["broom"]["y"] == pytest.approx((0.17 + 2.3) * 420 / 41.2, abs=0.05)
        last_y = float(g["ext"].split()[-1].split(",")[1])
        assert last_y == pytest.approx(g["broom"]["y"], abs=0.1)
        assert g["miss"]["label"] == "2 ft 4 in"

    def test_the_intended_line_starts_at_the_hack_not_the_stone(self):
        g = run_js(f"out(stripGeometry({json.dumps(self.SHOT)}));")
        first = [float(v) for v in g["aim"].split()[0].split(",")]
        assert first == pytest.approx([75 - 0.152 * 150 / 4.75, (38.405 + 2.3) * 420 / 41.2], abs=0.05)
        assert g["start"]["x"] == pytest.approx(75 - 0.23 * 150 / 4.75, abs=0.05)

    def test_no_line_still_draws_the_sheet_broom_and_rest(self):
        s = shot(1, "red", "lead", target_broom={"x": 0.5, "y": 0.2}, delivered_stone_index=0,
                 stones=[{"color": "red", "x": 0.3, "y": 1.0}], line=None)
        g = run_js(f"out(stripGeometry({json.dumps(s)}));")
        assert g["thrown"] is None and g["ext"] is None
        assert g["broom"] is not None and g["rest"] is not None
        assert len(g["rings"]) == 8

    def test_no_shot_draws_nothing(self):
        assert run_js("out(stripGeometry(null));") is None

    def test_the_house_draws_the_path_from_behind_the_thrower_when_there_is_one(self):
        assert run_js(f"out(trackPoints({json.dumps(self.SHOT)}));") == [[-1.18, 20.0], [-1.15, 1.35]]

    def test_otherwise_the_panel_s_track_as_before(self):
        s = shot(1, "red", "lead", track=[[1.0, 0.1, 4.0], [2.0, 0.2, 1.0]])
        assert run_js(f"out(trackPoints({json.dumps(s)}));") == [[0.1, 4.0], [0.2, 1.0]]

    def test_the_current_end_s_span_on_the_clock_chart(self):
        geom = {"ticks": [{"x": 100, "y1": 8, "y2": 188}, {"x": 200, "y1": 8, "y2": 188}]}
        got = run_js(f"out([endSpan({json.dumps(geom)}, 0, {{padL: 46}}), "
                     f"endSpan({json.dumps(geom)}, 1, {{padL: 46}}), endSpan({json.dumps(geom)}, 5, {{padL: 46}})]);")
        assert got == [{"x0": 46, "x1": 100, "y1": 8, "y2": 188},
                       {"x0": 100, "x1": 200, "y1": 8, "y2": 188}, None]


class TestTheStripOnItsSide:
    """The desktop draws the phone's strip turned a quarter: hack at the left."""

    SHOT = TestLineFigures().measured()

    def turned(self, s=None):
        return run_js(f"out(sideways(stripShapes(stripGeometry({json.dumps(s or self.SHOT)}, DESKBOX))));")

    def test_upright_shapes_are_the_phone_strip_s_own_numbers(self):
        got = run_js(f"const g = stripGeometry({json.dumps(self.SHOT)}); const s = stripShapes(g);"
                     "out([s.lines[0], s.rings[0], s.broom, s.aim === g.aim, s.lines.length, s.miss.anchor, s.miss.size]);")
        assert got == [{"x1": 0, "y1": 88.7, "x2": 150, "y2": 88.7, "kind": "hog"},
                       {"cx": 75, "cy": 23.4, "rx": 57.8, "ry": 18.6, "kind": "twelve"},
                       {"x": 21, "y": 20.2, "w": 4, "h": 10}, True, 8, "middle", 9]

    def test_the_frame_turns_and_the_hack_is_at_the_left(self):
        s = self.turned()
        assert (s["w"], s["h"]) == (660, 114)
        assert s["start"] == {"x": 13.3, "y": 51.5}
        # The intended line starts at the left hack's foothold, (-0.152, 38.405).
        assert s["aim"].split()[0] == "7.9,53.4"

    def test_the_thrower_s_left_is_the_top_edge(self):
        # The broom is at x = -1.647, the thrower's left: it lands above the centre line.
        s = self.turned()
        centre = s["lines"][-1]
        assert centre == {"x1": 660, "y1": 57, "x2": 0, "y2": 57, "kind": "centre"}
        assert s["broom"]["y"] + s["broom"]["h"] / 2 < 57

    def test_a_ring_s_radii_swap_and_the_broom_lies_along_the_sheet(self):
        s = self.turned()
        assert s["rings"][0] == {"cx": 623.2, "cy": 57, "rx": 29.3, "ry": 43.9, "kind": "twelve"}
        assert s["broom"] == {"x": 615.4, "y": 15.5, "w": 10, "h": 4}

    def test_the_hog_lines_run_across_the_sheet(self):
        assert self.turned()["lines"][0] == {"x1": 520.6, "y1": 0, "x2": 520.6, "y2": 114, "kind": "hog"}

    def test_the_miss_bracket_stands_7_px_past_the_broom_labelled_before_it(self):
        s = self.turned()
        m, b = s["miss"], s["broom"]
        assert m["x1"] == m["x2"] == pytest.approx(b["x"] + b["w"] / 2 + 7, abs=0.15)
        assert (m["label"], m["anchor"], m["size"], m["halo"]) == ("2 ft 4 in", "end", 11, True)
        assert m["tx"] == pytest.approx(m["x1"] - 16, abs=0.05)

    def test_the_label_stays_inside_the_frame_on_a_huge_miss(self):
        big = dict(self.SHOT, target_broom={"x": 1.9, "y": 0.0},
                   line=dict(self.SHOT["line"], at_broom={"x": -1.5, "miss_m": 3.4}))
        m = self.turned(big)["miss"]
        assert m["label"] == "11 ft 2 in"
        assert 11 <= m["ty"] <= 111 and m["tx"] < m["x1"]

    def test_no_line_still_turns_the_sheet(self):
        s = shot(1, "red", "lead", target_broom={"x": 0.5, "y": 0.2}, delivered_stone_index=0,
                 stones=[{"color": "red", "x": 0.3, "y": 1.0}], line=None)
        t = self.turned(s)
        assert t["miss"] is None and t["aim"] is None
        assert t["broom"] is not None and t["rest"] is not None and len(t["rings"]) == 8

    def test_an_empty_hog_path_does_not_turn_into_nan(self):
        # stripGeometry gives thrown: "" when hog_path is empty; sideways's
        # pts() used to check only == null, so "" fell through to "NaN,0".
        s = dict(self.SHOT, line=dict(self.SHOT["line"], hog_path=[]))
        t = self.turned(s)
        assert t["thrown"] is None

    def test_the_clamp_at_the_thrower_s_left_edge(self):
        # target_broom.x = -2.2 (near the left edge), at_broom.x = -2.35: a
        # small (0.15 m) miss. Unclamped this lands at ty ~= 6.4, below the
        # low clamp, so it clamps UP to 11.
        s = dict(self.SHOT, target_broom={"x": -2.2, "y": 0.17},
                 line=dict(self.SHOT["line"], at_broom={"x": -2.35, "miss_m": -0.15}))
        m = self.turned(s)["miss"]
        assert m["ty"] == 11

    def test_the_clamp_at_the_thrower_s_right_edge(self):
        # The mirror: target_broom.x = 2.2, at_broom.x = 2.35. Unclamped this
        # lands at ty ~= 115.6, above the high clamp, so it clamps DOWN to 111.
        s = dict(self.SHOT, target_broom={"x": 2.2, "y": 0.17},
                 line=dict(self.SHOT["line"], at_broom={"x": 2.35, "miss_m": 0.15}))
        m = self.turned(s)["miss"]
        assert m["ty"] == 111

    def test_no_shot_draws_nothing(self):
        assert run_js("out(sideways(stripShapes(stripGeometry(null, DESKBOX))));") is None


class TestTheChartPanelList:
    SRC = Path(__file__).resolve().parents[1] / "frontend/viewer/ChartPanel.jsx"

    def test_the_house_counts_stones_not_inches(self):
        got = run_js('out([houseDeltaText({added: [1]}), houseDeltaText({removed: [1, 2], moved: [3]}),'
                     ' houseDeltaText({added: []}), houseDeltaText(null)]);')
        assert got == ["1 stone in", "2 stones out, 1 stone moved", "no change", "—"]

    def test_the_list_names_the_entry_speed_and_drops_the_long_split(self):
        src = self.SRC.read_text()
        assert '["Entry speed",' in src and '"Long split"' not in src and '["Weight",' not in src
        assert '["House", houseDeltaText(shot?.house_delta)]' in src


class TestTheAppKnowsItsTab:
    APP = Path(__file__).resolve().parents[1] / "frontend/viewer/App.jsx"

    def test_a_seek_queued_before_the_player_is_ready_keeps_its_own_autoplay(self):
        """A restored rock seeks on load with autoplay off, before the iframe
        is ready; the queue must not swap that for the "play on jump" pref."""
        src = (Path(__file__).resolve().parents[1] / "frontend/runtime/player.mjs").read_text()
        assert "pending = { t, autoplay }" in src and "play ?? autoplay()" in src

    def test_the_tab_and_rock_are_written_to_the_hash(self):
        src = self.APP.read_text()
        assert "history.replaceState(" in src and "formatHash(" in src

    def test_a_link_s_rock_is_restored_before_the_session_s(self):
        src = self.APP.read_text()
        assert "cursorFromHash(" in src and "export function App({ doc, config, cursor })" in src

    def test_the_house_re_measures_its_crop_when_the_tab_changes(self):
        assert "cropDeps={[ui.sheet, ui.houseMode, ui.ei, ui.si, ui.gi, ui.tab]}" in self.APP.read_text()

    def test_the_house_card_swipes_on_the_read_only_surfaces(self):
        assert "{...(config.readOnly ? houseSwipe : {})}" in self.APP.read_text()

    def test_the_last_tab_is_remembered(self):
        prefs = (Path(__file__).resolve().parents[1] / "frontend/runtime/prefs.mjs").read_text()
        assert 'tab: "detail"' in prefs

    def test_a_saved_rock_that_is_gone_is_ignored(self):
        assert ".shots[cursor.si]" in self.APP.read_text()

    def test_the_swipe_is_phone_only_and_the_hash_is_everywhere(self):
        """A link to a rock is shared from the desktop and the edit page too,
        so every surface reads the hash and keeps it current; only a tab is
        the phone's alone."""
        src = self.APP.read_text()
        assert "if (phone()) actions.step(d)" in src
        assert "!config.readOnly || !phone()" not in src
        assert "config.readOnly ? parseHash" not in src
        assert "const tab = config.readOnly && phone() ? ui.tab : undefined;" in src

    def test_a_link_is_read_against_the_game_as_charted(self):
        src = self.APP.read_text()
        assert "buildGameView(doc, gi, ov)" in src and "const ov = store.getOverrides();" in src
        assert "buildGameView(doc, gi, {})" not in src

    def test_a_link_opened_in_a_tab_already_on_the_chart_goes_to_its_rock(self):
        src = self.APP.read_text()
        assert 'addEventListener("hashchange", onHash)' in src
        assert "if (typeof t === \"number\") player.seek(t, false);" in src
        # A link naming no rock puts the URL back on the one being shown.
        assert 'history.replaceState(null, "", written.current ||' in src

    def test_the_view_only_link_names_the_rock(self):
        assert "withHash(doc.chart.share_url, location.hash)" in self.APP.read_text()

    def test_a_restored_cursor_seeks_the_player_once_on_mount(self):
        src = self.APP.read_text()
        assert "player.seek(shotVideoTime(" in src and ", false)" in src

    def test_a_step_follows_the_video_again(self):
        src = self.APP.read_text()
        assert "setFollowing(true); goTo(n.ei, n.si);" in src
        assert 'setPref({ tab });' in src


class TestTheTabShell:
    JSX = Path(__file__).resolve().parents[1] / "frontend/viewer"

    def test_three_tabs_and_the_panes_they_show(self):
        src = (self.JSX / "Watch.jsx").read_text()
        for needle in ('className="wtabs"', "actions.setTab(", "<Detail ", "<Timing ", "<Pager "):
            assert needle in src, needle
        assert "openWatch" not in src and "ui.watch" not in src

    def test_tapping_the_pager_s_end_and_rock_picks_an_end(self):
        pager = (self.JSX / "Pager.jsx").read_text()
        assert 'className="wpend"' in pager and "onEnd(+e.target.value)" in pager
        # An end with nothing in it cannot be picked: no pager there, no way back.
        assert "disabled={!x.rocks}" in pager
        watch = (self.JSX / "Watch.jsx").read_text()
        assert "onEnd={actions.goToEnd}" in watch and "ends={ends} ei={ui.ei}" in watch
        app = (self.JSX / "App.jsx").read_text()
        assert "goToEnd: ei => { if (view.ends[ei]?.shots.length) { setFollowing(true); goTo(ei, 0); } }" in app

    def test_the_house_draws_its_path_through_track_points(self):
        src = (self.JSX / "House.jsx").read_text()
        body = src[src.index("function Track("):src.index("function Broom(")]
        assert "trackPoints(shot)" in body and "shot?.track" not in body


class TestTheDesktopDetailCard:
    APP = Path(__file__).resolve().parents[1] / "frontend/viewer/App.jsx"

    def css(self):
        return (VIEWER / "style.css").read_text()

    def test_the_card_follows_the_play_card(self):
        src = self.APP.read_text()
        i_play, i_card, i_house = (src.index('id="playCard"'), src.index('id="detailCard"'),
                                   src.index('id="houseCard"'))
        assert i_play < i_card < i_house
        assert "<DeskDetail shot={shot} doc={doc} />" in src

    def test_the_grid_is_laid_out_by_area_at_every_width(self):
        css = self.css()
        for needle in ('grid-template-areas: "play house chart" "detail house chart";',
                       'grid-template-areas: "play house" "detail house" "chart house";',
                       'grid-template-areas: "play" "detail" "house" "chart";',
                       "#detailCard { grid-area: detail; }", "#chart { grid-area: chart; }"):
            assert needle in css, needle

    def test_the_phones_get_their_own_grid_back_and_no_card(self):
        assert ("@media (max-width:640px){\n"
                "  main { grid-template-areas: none; }\n"
                "  #playCard, #houseCard, #chart { grid-area: auto; }\n"
                "  #detailCard { display: none; }\n"
                "}") in self.css()

    def test_the_strip_scales_to_the_card_and_the_figures_go_three_across(self):
        css = self.css()
        strip = css[css.index("#detailCard .dstrip {"):css.index("}", css.index("#detailCard .dstrip {"))]
        assert "width: 100%" in strip and "height: auto" in strip
        figs = css[css.index("#detailCard .dfigs {"):css.index("}", css.index("#detailCard .dfigs {"))]
        assert "grid-template-columns: repeat(3, minmax(0, 1fr))" in figs


class TestTheDetailPane:
    SRC = Path(__file__).resolve().parents[1] / "frontend/viewer/Detail.jsx"

    def test_it_draws_the_strip_and_the_six_figures_from_the_core(self):
        src = self.SRC.read_text()
        for needle in ("lineFigures(shot, doc)", "stripGeometry(shot)", 'className="dstrip"',
                       'className="dcap"', "f.predates"):
            assert needle in src, needle

    def test_the_broom_marker_is_drawn_from_its_shape(self):
        # Upright 4 x 10 on the phone, 10 x 4 on its side: TestTheStripOnItsSide pins both.
        assert "width={s.broom.w} height={s.broom.h}" in self.SRC.read_text()

    def test_the_caption_is_the_spec_s(self):
        assert ("Sheet from above, thrower at the bottom · across ×3 · figures ±4 in · "
                "wide = the side away from the curl") in self.SRC.read_text()

    def test_the_desktop_turns_the_strip_and_shares_the_figures(self):
        src = self.SRC.read_text()
        assert "sideways(stripShapes(stripGeometry(shot, DESKBOX)))" in src
        assert "stripShapes(stripGeometry(shot))" in src
        assert src.count("<Figures figures={f.figures} />") == 2
        assert ("Sheet from above, thrower at the left · across ×1.5 · figures ±4 in · "
                "wide = the side away from the curl") in src

    def test_an_empty_end_says_so_rather_than_blaming_the_broom(self):
        assert 'if (!shot) return <p className="dnone">No rocks were detected in this end</p>;' in self.SRC.read_text()

    def test_the_label_halo_is_ice_under_the_text(self):
        assert '{ stroke: PAINT.ice, strokeWidth: 3, paintOrder: "stroke" }' in self.SRC.read_text()


class TestTheTimingTab:
    SRC = Path(__file__).resolve().parents[1] / "frontend/viewer/Timing.jsx"

    def test_every_end_of_the_game_with_its_header(self):
        src = self.SRC.read_text()
        for needle in ("view.ends.map(", "endSummary(view, k)", "rockRows(view, k, ui.leadIn)",
                       'className="tend"', "shadeEnd={ui.ei}", "TIMINGBOX"):
            assert needle in src, needle

    def test_a_tap_stays_on_timing_and_follows_the_video_again(self):
        src = self.SRC.read_text()
        assert "actions.setFollowing(true); actions.goTo(k, i);" in src
        assert "setTab" not in src

    def test_the_row_is_measured_against_the_list_not_the_fixed_shell(self):
        src = self.SRC.read_text()
        assert "getBoundingClientRect().top - el.getBoundingClientRect().top + el.scrollTop" in src
        assert "STICKY_H" in src


class TestThePhoneTabsCss:
    def test_the_tabs_the_pager_and_the_panes_have_rules(self):
        css = (VIEWER / "style.css").read_text()
        for sel in (".wtabs {", ".wpager {", ".wpane {", ".dstrip {", ".tend {", ".tlist {",
                    'body[data-watch="house"] main > #houseCard'):
            assert sel in css, sel

    def test_the_end_picker_covers_the_pager_s_middle_invisibly(self):
        css = (VIEWER / "style.css").read_text()
        rule = css[css.index(".wpend {"):css.index("}", css.index(".wpend {"))]
        for decl in ("position: absolute", "inset: 0", "opacity: 0", "font-size: 16px"):
            assert decl in rule, decl
        mid = css[css.index(".wpmid {"):css.index("}", css.index(".wpmid {"))]
        assert "position: relative" in mid

    def test_the_detail_pane_hands_horizontal_drags_to_the_swipe(self):
        css = (VIEWER / "style.css").read_text()
        rule = css[css.index(".wpane {"):css.index("}", css.index(".wpane {"))]
        assert "touch-action: pan-y" in rule

    def test_the_old_sheets_are_gone(self):
        css = (VIEWER / "style.css").read_text()
        for gone in (".wsheethead {", ".wbar {", ".wendbar {"):
            assert gone not in css, gone
