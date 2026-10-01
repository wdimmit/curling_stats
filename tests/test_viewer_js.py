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
        see the two tests below, which pin that boundary. A band tall enough to
        reach past the back of the ice is not centred; see the last test here.
        """
        x, y, w, h = (float(v) for v in
                      run_js('out(houseViewBox("crop", 390/250));').split())
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

    def test_a_box_taller_than_it_is_wide_keeps_the_back_of_the_ice_at_the_top(self):
        # The ice starts at the back line, 1.829 m behind the tee. The House
        # tab's box on a 390x844 phone is 366x457, and centring that crop on
        # the tee put 1.4 m of nothing between the pager and the ice. It now
        # starts behind the back line by the sides' own 0.225 m margin, and the
        # extra height goes in front of the house instead.
        x, y, w, h = (float(v) for v in
                      run_js('out(houseViewBox("crop", 366/457));').split())
        assert y == pytest.approx(-1.829 - 0.225, abs=1e-3)
        assert w / h == pytest.approx(366 / 457, abs=1e-3)


class TestTheInPlayView:
    """The phone's House tab on a view-only or review link shows everything in
    play: the back line to the hog line at the sheet's full width, with the
    same 0.225 m margin the crop leaves behind the back line at each end.

    The crop stopped about 4.4 m in front of the tee, halving a guard and
    hiding the hog line. Charting keeps its crop: its band is too short for
    the whole sheet to stay legible."""

    def box(self, aspect="0.8"):
        return [float(v) for v in run_js(f'out(houseViewBox("inplay", {aspect}));').split()]

    def test_it_spans_the_back_line_to_the_hog_line_at_full_width(self):
        x, y, w, h = self.box()
        assert (x, w) == (-2.6, 5.2)
        assert y == pytest.approx(-1.829 - 0.225, abs=1e-3)
        assert y + h == pytest.approx(6.401 + 0.225, abs=1e-3)

    def test_it_does_not_depend_on_the_box_it_is_drawn_in(self):
        """Letterboxed, never cropped: every stone in play stays in view."""
        assert self.box("2.0") == self.box("0.3") == self.box("0")

    def test_only_the_phone_house_on_a_watch_link_uses_it(self):
        src = (Path(__file__).resolve().parents[1] / "frontend/viewer/House.jsx").read_text()
        assert 'houseViewBox(watching && phone() ? "inplay" : box.crop ? "crop" : "full"' in src
        assert "watching: readOnly," in src


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


class TestChartedNotice:
    """What a poll that brought a teammate's work says. A shot key has three
    parts (game.end.rock); a two-part end key is a doubles role swap, which
    is not a charted shot."""

    def test_shots_are_counted(self):
        assert run_js('out(chartedNotice(["0.3.1"]));') == "Someone else charted 1 shot"
        assert run_js('out(chartedNotice(["0.3.1", "0.4.2"]));') == "Someone else charted 2 shots"

    def test_a_swap_alone_says_who_threw_changed(self):
        assert run_js('out(chartedNotice(["0.3"]));') == "Someone else changed who threw in an end"
        assert run_js('out(chartedNotice(["0.3", "0.4"]));') == (
            "Someone else changed who threw in 2 ends")

    def test_only_shot_keys_are_counted_as_shots(self):
        assert run_js('out(chartedNotice(["0.3", "0.3.1", "0.4"]));') == (
            "Someone else charted 1 shot")

    def test_the_store_hands_the_view_the_keys(self):
        root = Path(__file__).resolve().parents[1] / "frontend"
        store = (root / "runtime/overridesStore.mjs").read_text()
        assert store.count("onNotice(r.touched);") == 2 and "onNotice(r.touched.length)" not in store
        assert "store.setNotice(keys => notify(chartedNotice(keys)));" in (
            root / "viewer/App.jsx").read_text()


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


def turning(y_break, speed, standing=(0.10, -0.22)):
    """A rock running straight down the sheet at `speed` that turns off
    sideways at depth `y_break`, with a stone standing `standing` from where
    it turned; the house diff saw a stone go that the rock never went near."""
    track, x, y = [], 0.0, y_break + 8 * 0.1 * speed
    for i in range(18):
        track.append([round(i * 0.1, 2), round(x, 4), round(y, 4)])
        if i < 8:
            y -= 0.1 * speed
        else:
            x -= 0.06 * speed; y -= 0.02 * speed
    return shot(10, "yellow", "third", target_broom={"x": -0.3, "y": 1.0},
                delivered_stone_index=0, stones=[{"color": "yellow", "x": round(x, 4), "y": round(y, 4)}],
                stones_before=[{"color": "red", "x": standing[0], "y": round(y_break + standing[1], 4)}],
                house_delta={"added": [], "removed": [{"color": "red", "x": 1.8, "y": -1.5}], "moved": []},
                track=track,
                line={"start": {"x": 0.0, "y": 38.0}, "at_hog": {"x": 0.0, "offset_m": 0.0},
                      "at_broom": {"x": 0.0, "miss_m": 0.3}, "curl": "left", "confirmed": None,
                      "hog_path": [], "path": []})


class TestContactFromTheTrack:
    """A hit the house diff cannot place -- the stone it struck moved less
    than a move counts -- is found where the overhead track breaks, next to a
    stone that stood there before the throw."""

    def figs(self, s):
        return {f["key"]: f for f in run_js(f"out(lineFigures({json.dumps(s)}, {{schema_version: 6}}));")["figures"]}

    def s_0N8Q_e5_s10(self, **kw):
        """s_0N8Q2sB4vY8Hv4Ooq, end 5, rock 10: it curled right onto the front
        red of a stack at (-0.17, 1.86), which squirted 0.29 m -- under the
        0.30 m the house diff calls a move -- and drove the red behind it out.
        The rock rolled 0.95 m left. Checked against the video, 2026-09-28."""
        base = dict(
            target_broom={"x": -0.6683, "y": 1.7488}, delivered_stone_index=3,
            stones=[{"color": "red", "x": 0.1229, "y": 2.9456}, {"color": "yellow", "x": 0.6306, "y": 3.6739},
                    {"color": "red", "x": 0.1037, "y": 1.7636}, {"color": "yellow", "x": -1.195, "y": 1.3042}],
            stones_before=[{"color": "yellow", "x": 0.6306, "y": 3.6759}, {"color": "red", "x": -0.1687, "y": 1.5397},
                           {"color": "red", "x": -0.1706, "y": 1.8597}, {"color": "red", "x": 0.1223, "y": 2.9427}],
            house_delta={"added": [{"color": "yellow", "x": -1.195, "y": 1.3042}],
                         "removed": [{"color": "red", "x": -0.1687, "y": 1.5397}], "moved": []},
            track=[[4866.83, -0.164, 4.524], [4867.03, -0.217, 4.433], [4867.13, -0.2, 4.374],
                   [4867.33, -0.227, 4.286], [4867.43, -0.204, 4.233], [4867.53, -0.24, 4.186],
                   [4867.63, -0.235, 4.134], [4867.73, -0.212, 4.076], [4867.83, -0.238, 4.01],
                   [4867.93, -0.238, 3.947], [4868.03, -0.242, 3.881], [4868.13, -0.258, 3.806],
                   [4868.23, -0.248, 3.735], [4868.33, -0.26, 3.664], [4868.43, -0.258, 3.591],
                   [4868.53, -0.242, 3.505], [4868.63, -0.277, 3.426], [4868.73, -0.277, 3.347],
                   [4868.83, -0.269, 3.261], [4868.93, -0.281, 3.161], [4869.03, -0.277, 3.065],
                   [4869.13, -0.281, 2.969], [4869.23, -0.284, 2.867], [4869.33, -0.281, 2.756],
                   [4869.43, -0.281, 2.652], [4869.53, -0.284, 2.537], [4869.63, -0.286, 2.422],
                   [4869.73, -0.284, 2.299], [4869.83, -0.284, 2.178], [4869.93, -0.288, 2.069],
                   [4870.03, -0.323, 2.042], [4870.13, -0.365, 2.015], [4870.23, -0.404, 1.99],
                   [4870.33, -0.446, 1.973], [4870.43, -0.484, 1.948], [4870.53, -0.523, 1.923],
                   [4870.63, -0.561, 1.903], [4870.73, -0.599, 1.877], [4870.83, -0.634, 1.857],
                   [4870.93, -0.672, 1.834], [4871.03, -0.707, 1.811], [4871.13, -0.736, 1.794],
                   [4871.23, -0.768, 1.773], [4871.33, -0.799, 1.752], [4871.43, -0.828, 1.731],
                   [4871.53, -0.858, 1.71], [4871.63, -0.891, 1.688], [4871.73, -0.92, 1.665],
                   [4871.83, -0.947, 1.646], [4871.93, -0.972, 1.623], [4872.03, -0.995, 1.604],
                   [4872.13, -1.022, 1.589], [4872.23, -1.043, 1.566], [4872.33, -1.068, 1.55],
                   [4872.43, -1.091, 1.533], [4872.53, -1.104, 1.52], [4872.63, -1.125, 1.502],
                   [4872.73, -1.141, 1.487], [4872.83, -1.156, 1.47], [4872.93, -1.171, 1.452],
                   [4873.03, -1.189, 1.433], [4873.13, -1.198, 1.42], [4873.23, -1.212, 1.408],
                   [4873.33, -1.221, 1.401]],
            line={"start": {"x": -0.1367, "y": 37.114}, "at_hog": {"x": -0.3624, "offset_m": -0.0939},
                  "at_broom": {"x": -0.9531, "miss_m": -0.2847}, "side": "narrow", "curl": "left",
                  "confirmed": True, "hog_path": [],
                  "path": [[23.8, -0.43], [14.86, -0.516], [5.03, -0.366], [2.48, -0.272], [1.95, -0.242],
                           [1.45, -0.705], [1.02, -1.189]]})
        base.update(kw)
        return shot(10, "yellow", "third", **base)

    def test_a_struck_stone_the_house_diff_missed_is_found_where_the_track_breaks(self):
        # The break peaks at 4869.93 s, 0.24 m from the front red; curl is read
        # at the sample before, 4869.83 s. Curling right, a miss to the left
        # of the broom is on the side away from the curl.
        f = self.figs(self.s_0N8Q_e5_s10())
        assert (f["curl"]["value"], f["curl"]["note"]) == ("2 ft 2 in", "from its line to where it hit a stone")
        assert f["broom"]["value"] == "11 in wide"

    def test_without_the_house_before_it_the_rock_is_measured_to_rest(self):
        s = self.s_0N8Q_e5_s10()
        del s["stones_before"]
        f = self.figs(s)
        assert (f["curl"]["value"], f["curl"]["note"]) == ("9 in", "from its line to where it stopped")
        assert f["broom"]["value"] == "11 in narrow"

    def test_a_break_with_no_stone_standing_there_is_not_a_hit(self):
        f = self.figs(self.s_0N8Q_e5_s10(stones_before=[{"color": "red", "x": 0.1223, "y": 2.9427}]))
        assert f["curl"]["note"] == "from its line to where it stopped"

    def test_a_throw_that_disturbed_nothing_is_not_searched(self):
        # A draw that grazed a stone keeps its draw's curl, to rest.
        quiet = {"added": [{"color": "yellow", "x": -1.195, "y": 1.3042}], "removed": [], "moved": []}
        f = self.figs(self.s_0N8Q_e5_s10(house_delta=quiet))
        assert f["curl"]["note"] == "from its line to where it stopped"

    def test_a_break_at_the_panel_s_far_edge_is_not_trusted(self):
        assert self.figs(turning(3.0, 1.0))["curl"]["note"] == "from its line to where it hit a stone"
        assert self.figs(turning(4.2, 1.0))["curl"]["note"] == "from its line to where it stopped"

    def test_a_slow_rock_s_wobble_is_not_a_break(self):
        assert self.figs(turning(3.0, 0.2))["curl"]["note"] == "from its line to where it stopped"

    def test_a_track_that_speeds_up_is_a_stone_being_struck_not_striking(self):
        """A rock loses speed to the stone it strikes. A track that sits still
        and then sets off (s_0qR8SYtWpF8o2hCtq, end 10, rock 10) is following
        a stone that was struck."""
        s = turning(1.0, 1.0)
        s["track"] = [[round(i * 0.1, 2), -0.395, 0.354] for i in range(8)] + \
                     [[round(0.8 + i * 0.1, 2), round(-0.395 + 0.07 * i, 3), round(0.354 - 0.09 * i, 3)]
                      for i in range(1, 10)]
        s["stones_before"] = [{"color": "red", "x": -0.40, "y": 0.10}]
        assert self.figs(s)["curl"]["note"] == "from its line to where it stopped"

    def test_the_game_view_gives_each_rock_the_house_it_was_thrown_into(self):
        """The last house read before it: a rock never seen reads no house, so
        the one after it was thrown into the house before that."""
        one = [{"color": "red", "x": 0.0, "y": 1.0}]
        two = one + [{"color": "yellow", "x": 0.5, "y": 0.5}]
        shots = [shot(1, "red", "lead", stones=one), shot(2, "yellow", "lead", stones=two),
                 shot(3, "red", "second", missing=True, state_known=False),
                 shot(4, "yellow", "second", stones=two)]
        got = run_js(setup(doc(shots)) + "out(gameView().ends[0].shots.map(s => s.stones_before));")
        assert got == [[], one, two, two]

    def test_doubles_rock_one_is_thrown_into_the_placed_stones(self):
        d = doc([shot(1, "red", "lead", stones=[])])
        d["games"][0]["ends"][0]["placement"] = {
            "house": {"color": "yellow", "x": 0.0, "y": -0.9}, "guard": {"color": "red", "x": 0.0, "y": 4.1}}
        got = run_js(setup(d) + "out(gameView().ends[0].shots[0].stones_before);")
        assert got == [{"color": "yellow", "x": 0.0, "y": -0.9}, {"color": "red", "x": 0.0, "y": 4.1}]


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

    def test_a_link_can_open_the_delivery_tab(self):
        assert run_js('out(parseHash("#tab=delivery&e=2&s=1"));') == {"tab": "delivery", "e": 2, "s": 1}

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


DOUBLES_FORMAT = {"name": "doubles", "stones_per_team": 6, "placed_per_team": 1,
                  "delivered_per_team": 5, "delivered_per_end": 10,
                  "positions": ["A", "B"], "throw_table": [1, 2, 2, 2, 1],
                  "blank_passes_hammer": True, "swappable": True}
DOUBLES7 = {"schema_version": 7, "format": DOUBLES_FORMAT}


class TestBroomlessLine:
    """A doubles rock nobody held a broom for: its line, pinned at the tee."""

    def broomless(self, **line):
        base = {"start": {"x": -0.23, "y": 38.07},
                "at_hog": {"x": -0.757, "offset_m": None},
                "at_broom": None, "at_tee": {"x": -1.5},
                "side": None, "curl": "right", "confirmed": True,
                "hog_path": [[28.35, -0.757], [25.0, -0.85]],
                "path": [[20.0, -1.0], [1.35, -1.15]], "fit": {"n": 41, "rms_m": 0.003}}
        base.update(line)
        return shot(4, "red", "B", target_broom=None, long_split_s=13.79,
                    delivered_stone_index=0,
                    stones=[{"color": "red", "x": -1.1529, "y": 1.3486}], line=base)

    def figs(self, s, doc=DOUBLES7):
        got = run_js(f"out(lineFigures({json.dumps(s)}, {json.dumps(doc)}));")
        return got, {f["key"]: f for f in got["figures"]}

    def test_the_line_runs_through_the_hog_line_and_the_tee(self):
        # From (-0.757, 28.346) to (-1.5, 0): at the rest's depth 1.3486 the
        # line is at -1.4647, and the rock stopped 0.3118 m to its right.
        assert run_js(f"out(lineX({json.dumps(self.broomless())}, 0));") == pytest.approx(-1.5)
        got, f = self.figs(self.broomless())
        assert got["reason"] is None
        # base's confirmed defaults to True.
        assert (f["curl"]["value"], f["curl"]["note"]) == (
            "1 ft", "from its line to where it stopped · confirmed from behind the thrower")

    def test_the_curl_figure_carries_the_camera_behind_the_thrower_s_check(self):
        # M1: no other figure carries `line.confirmed` for a broomless rock,
        # so the curl figure does -- the broom figure stays a dash, untouched.
        _, f = self.figs(self.broomless(confirmed=True))
        assert (f["curl"]["tick"], f["curl"]["dim"]) == ("confirmed", False)
        assert f["curl"]["note"].endswith("· confirmed from behind the thrower")
        assert (f["broom"]["tick"], f["broom"]["value"]) == (None, "–")

        _, f = self.figs(self.broomless(confirmed=False))
        assert (f["curl"]["tick"], f["curl"]["dim"]) == ("disagrees", True)
        assert f["curl"]["note"].endswith("· the camera behind the thrower disagrees")

        _, f = self.figs(self.broomless(confirmed=None))
        assert (f["curl"]["tick"], f["curl"]["dim"]) == ("unseen", False)
        assert f["curl"]["note"].endswith("· not confirmed: hidden from behind the thrower")

    def test_broom_figures_are_dashes_and_the_rest_are_measured(self):
        _, f = self.figs(self.broomless())
        assert (f["broom"]["value"], f["broom"]["note"], f["broom"]["tick"]) == (
            "–", "no broom held in the house", None)
        assert (f["hog"]["value"], f["hog"]["note"]) == ("–", "no broom to aim at")
        assert (f["hack"]["value"], f["hack"]["note"]) == ("Left", "stone set 9 in left of centre")
        assert f["weight"]["value"] == "13.8 s"
        assert f["rest"]["value"] == "12-foot"

    def test_a_doubles_rock_with_no_line_blames_the_camera_not_the_broom(self):
        s = self.broomless()
        s["line"] = None
        got, _ = self.figs(s)
        assert got["reason"] == "The hog-line camera lost this rock"

    def test_a_fours_line_without_a_broom_is_still_refused(self):
        got, f = self.figs(self.broomless(), {"schema_version": 7})
        assert got["reason"] == "No broom was held still before the release"
        assert f["curl"]["value"] == "–"
        g = run_js(f"out(stripGeometry({json.dumps(dict(self.broomless(), line=dict(self.broomless()['line'], at_tee=None)))}));")
        assert g["thrown"] is None

    def test_a_broomless_line_with_nowhere_to_measure_to(self):
        s = self.broomless(path=[])
        s["delivered_stone_index"] = None
        _, f = self.figs(s)
        assert (f["curl"]["value"], f["curl"]["note"]) == (
            "–", "no rest position · confirmed from behind the thrower")

    def test_a_broomless_line_without_at_tee_draws_no_line(self):
        s = self.broomless(at_tee=None)
        _, f = self.figs(s)
        assert f["curl"]["value"] == "–"
        g = run_js(f"out(stripGeometry({json.dumps(s)}));")
        assert (g["thrown"], g["ext"], g["aim"], g["miss"]) == (None, None, None, None)

    def test_the_strip_draws_the_line_to_the_tee_and_nothing_about_a_broom(self):
        g = run_js(f"out(stripGeometry({json.dumps(self.broomless())}));")
        assert g["broom"] is None and g["aim"] is None and g["miss"] is None
        assert g["thrown"] and g["path"] and g["start"] is not None
        last_y = float(g["ext"].split()[-1].split(",")[1])
        assert last_y == pytest.approx((0 + 2.3) * 420 / 41.2, abs=0.1)

    def test_a_player_s_hack_comes_from_broomless_starts(self):
        a = self.broomless(start={"x": -0.2, "y": 38.07})
        b = self.broomless(start={"x": -0.25, "y": 38.07})
        for s in (a, b):
            s["thrower_slot"] = 2
        got = run_js(f"out(playerHacks({json.dumps([a, b])}));")
        assert got["red|2"]["side"] == "left"


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

    def test_four_tabs_and_the_panes_they_show(self):
        src = (self.JSX / "Watch.jsx").read_text()
        for needle in ('className="wtabs"', "actions.setTab(", "<Detail ", "<Delivery ", "<Timing ",
                       "<Pager ", "WATCH_TABS.map("):
            assert needle in src, needle
        assert "openWatch" not in src and "ui.watch" not in src
        assert run_js("out(WATCH_TABS.map(([k]) => k));") == ["house", "detail", "delivery", "timing"]

    def test_the_delivery_pane_swipes_like_the_detail_pane(self):
        src = (self.JSX / "Watch.jsx").read_text()
        assert ('<div className="wpane" {...swipe}><Delivery shot={shot} doc={view.doc} /></div>') in src

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
        assert ("Sheet from above, thrower at the bottom · across ×3 · figures ±4 in") in self.SRC.read_text()

    def test_the_desktop_turns_the_strip_and_shares_the_figures(self):
        src = self.SRC.read_text()
        assert "sideways(stripShapes(stripGeometry(shot, DESKBOX)))" in src
        assert "stripShapes(stripGeometry(shot))" in src
        assert src.count("<Figures figures={f.figures} />") == 2
        assert ("Sheet from above, thrower at the left · across ×1.5 · figures ±4 in") in src

    def test_a_broomless_rock_drops_the_intended_line_and_the_wide_gloss(self):
        # M2: a doubles rock nobody held a broom for has no intended line to
        # show and no curl direction to call "wide" against.
        src = self.SRC.read_text()
        assert '"the thrown line and where the rock went"' in src
        assert '"the intended line, the thrown line and where the rock went"' in src
        assert '" · wide = the side away from the curl"' in src
        assert "isBroomless" in src and "!shot?.target_broom" in src

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

    def test_the_delivery_chart_takes_the_pane_s_width(self):
        css = (VIEWER / "style.css").read_text()
        rule = css[css.index(".wpane .dlv {"):css.index("}", css.index(".wpane .dlv {"))]
        for decl in ("width: 100%", "max-width: 358px", "height: auto", "background: var(--ice)"):
            assert decl in rule, decl

    def test_the_detail_pane_hands_horizontal_drags_to_the_swipe(self):
        css = (VIEWER / "style.css").read_text()
        rule = css[css.index(".wpane {"):css.index("}", css.index(".wpane {"))]
        assert "touch-action: pan-y" in rule

    def test_the_old_sheets_are_gone(self):
        css = (VIEWER / "style.css").read_text()
        for gone in (".wsheethead {", ".wbar {", ".wendbar {"):
            assert gone not in css, gone


class TestFlagPlace:
    """What a flag records about the rock it was sent from."""

    def place(self, d, ei=0, si=0, gi=0):
        return run_js(f"out(flagPlace(buildGameView({json.dumps(d)}, {gi}, {{}}), {ei}, {si}));")

    def test_a_rock(self):
        """A hosted page holds one game, so the dialog names no game: its
        number there would be 1 whichever game of the video it was, and the
        owner's list numbers games within the video."""
        d = doc([shot(1, "red", "lead", t_enter_s=822.5), shot(2, "yellow", "lead")],
                end_number=4)
        got = self.place(d)
        assert got["place"] == {"game_index": 0, "end": 4, "end_id": "4", "rock": 1,
                                "rock_id": "1", "key": "0.4.1", "t_video_s": 812.5,
                                "label": "red, lead"}
        assert got["text"] == "End 4 · Rock 1 (red, lead)"

    def test_a_page_with_two_games_names_the_game(self):
        d = doc([shot(1, "red", "lead")])
        d["games"].append({**d["games"][0], "index": 1})
        got = self.place(d, gi=1)
        assert got["place"]["game_index"] == 1
        assert got["text"] == "Game 2 · End 1 · Rock 1 (red, lead)"

    def test_an_end_with_no_rocks(self):
        got = self.place(doc([], end_number=3))
        assert got["place"]["rock"] is None and got["place"]["key"] is None
        assert got["place"]["t_video_s"] is None and got["place"]["label"] is None
        assert got["text"] == "End 3"

    def test_a_rock_without_a_video_time(self):
        got = self.place(doc([shot(1, "red", "lead")]))
        assert got["place"]["t_video_s"] is None

    def test_a_rock_never_delivered_still_has_a_video_time(self):
        """The rocks worth flagging are often the ones detection missed. They
        get the same time the viewer seeks to: rest less 8 s, then the guess,
        each with the pipeline's 10 s lead-in."""
        got = self.place(doc([shot(1, "red", "lead", t_rest_s=900.0)]))
        assert got["place"]["t_video_s"] == 882.0
        got = self.place(doc([shot(1, "red", "lead", t_enter_s=700.0)]))
        assert got["place"]["t_video_s"] == 690.0

    def test_a_trimmed_end_keeps_its_identity(self):
        got = self.place(doc([shot(1, "red", "lead")], end_number=2, end_id=5))
        assert (got["place"]["end"], got["place"]["end_id"], got["place"]["key"]) == (2, "5", "0.5.1")

    def test_a_renumbered_rock_keeps_its_identity(self):
        got = self.place(doc([shot(3, "red", "lead", id=7)]))
        assert (got["place"]["rock"], got["place"]["rock_id"], got["place"]["key"]) == (3, "7", "0.1.7")

    def test_the_second_game_of_a_video_keeps_its_index(self):
        got = self.place(doc([shot(1, "red", "lead")], game_index=1))
        assert got["place"]["game_index"] == 1 and got["place"]["key"] == "1.1.1"

    def test_a_game_with_no_ends_can_still_be_flagged(self):
        """Nothing detected at all is the failure most worth reporting."""
        d = doc([])
        d["games"][0]["ends"] = []
        got = self.place(d)
        assert got["place"] == {"game_index": 0, "end": None, "end_id": None, "rock": None,
                                "rock_id": None, "key": None, "t_video_s": None,
                                "label": None}
        assert got["text"] == "No ends in this game"

    def test_a_doubles_rock_names_the_player_and_a_fours_rock_its_position(self):
        """A bare "B" reads as nothing; "player B" says who threw. The format
        comes from the caller, and four-player text is what it always was."""
        d = doubles_doc(doubles_shots())
        got = run_js(f"const v = buildGameView({json.dumps(d)}, 0, {{}});"
                     "out([flagPlace(v, 0, 2, v.format), flagPlace(v, 0, 1, v.format)]);")
        assert got[0]["place"]["label"] == "red, player B"
        assert got[0]["text"] == "End 3 · Rock 3 (red, player B)"
        assert got[1]["place"]["label"] == "yellow, player A"
        fours = doc([shot(1, "red", "lead")])
        got = run_js(f"const v = buildGameView({json.dumps(fours)}, 0, {{}});"
                     "out([flagPlace(v, 0, 0, v.format), flagPlace(v, 0, 0)]);")
        assert got[0] == got[1]
        assert got[0]["place"]["label"] == "red, lead"
        assert got[0]["text"] == "End 1 · Rock 1 (red, lead)"

    def test_it_reads_the_cursor_it_does_not_redo_it(self):
        src = (Path(__file__).resolve().parents[1] / "frontend/core/flag.mjs").read_text()
        assert "cursor(view, ei, si)" in src and "at.raws[si]" not in src
        assert "shotVideoTime(shot, VIDEO_LEAD_IN_S)" in src and "shot?.t_video_s" not in src


class TestFlagConstants:
    """Numbers the viewer and the server must agree on, where the frontend keeps
    them (core/constants.mjs), checked against the Python they mirror."""

    def test_they_match_the_server(self):
        from curling_score import timeline
        from curling_score.service import api
        assert run_js("out([NOTE_MAX, VIDEO_LEAD_IN_S]);") == [
            api.MAX_FLAG_NOTE, timeline.VIDEO_LEAD_IN_S]

    def test_they_live_in_constants(self):
        root = Path(__file__).resolve().parents[1] / "frontend/core"
        consts = (root / "constants.mjs").read_text()
        assert "export const NOTE_MAX" in consts and "export const VIDEO_LEAD_IN_S" in consts
        flag = (root / "flag.mjs").read_text()
        assert "const NOTE_MAX" not in flag and "const VIDEO_LEAD_IN_S" not in flag


class TestNoteProblem:
    @pytest.mark.parametrize("note,ok", [
        ("", False), ("   \n\t", False), ("x" * 2000, True), ("x" * 2001, False),
        ("  wrong thrower  ", True),
    ])
    def test_notes(self, note, ok):
        got = run_js(f"out(noteProblem({json.dumps(note)}));")
        assert (got is None) == ok

    def test_null_is_blank(self):
        assert run_js("out(noteProblem(null));") == "Say what is wrong."


class TestSettledUser:
    """Who is signed in, once Firebase has said -- not when it started asking.

    site/auth.js's whenReady() settles as soon as its state listener is
    registered, before the session is restored from IndexedDB, so reading
    currentUser() then made a signed-in person's first flag anonymous."""

    def test_waits_for_the_restored_session(self):
        body = """
        const listeners = new Set(); let user = null, ready = false;
        const onUser = fn => { listeners.add(fn); fn(user, ready); return () => listeners.delete(fn); };
        setTimeout(() => { user = { email: "s@x.org" }; ready = true;
                           for (const f of listeners) f(user, ready); }, 20);
        settledUser(onUser).then(u => out([u && u.email, listeners.size]));
        """
        assert run_js(body) == ["s@x.org", 0]

    def test_accounts_off_is_nobody_at_once(self):
        assert run_js("settledUser(fn => { fn(null, true); return () => {}; }).then(out);") is None

    def test_the_dialog_asks_the_same_way(self):
        src = (Path(__file__).resolve().parents[1] / "frontend/runtime/flag.mjs").read_text()
        assert "settledUser(auth.onUser)" in src and "whenReady" not in src


class TestSettleWithin:
    """The sign-in check must never hold a flag hostage."""

    def test_a_check_that_never_settles_falls_back(self):
        assert run_js("settleWithin(new Promise(() => {}), 30, 'anon').then(out);") == "anon"

    def test_a_check_that_fails_falls_back(self):
        assert run_js("settleWithin(Promise.reject(new Error('x')), 500, 'anon').then(out);") == "anon"

    def test_a_check_that_answers_in_time_wins(self):
        assert run_js("settleWithin(Promise.resolve('me'), 50, 'anon').then(out);") == "me"


class TestTheFlagButton:
    ROOT = Path(__file__).resolve().parents[1]

    def src(self, rel):
        return (self.ROOT / rel).read_text()

    def test_it_sits_in_the_menu_after_report_and_only_when_hosted(self):
        app = self.src("frontend/viewer/App.jsx")
        menu = app[app.index('<div id="menu"'):app.index("</header>")]
        assert menu.index('id="reportBtn"') < menu.index('id="flagBtn"')
        assert "hidden={!config.hosted}" in menu[menu.index('id="flagBtn"'):]

    def test_review_mode_does_not_hide_it_and_the_desktop_orders_it(self):
        css = self.src("src/curling_score/viewer/style.css")
        review = "\n".join(l for l in css.splitlines() if 'data-mode="review"' in l)
        assert "#flagBtn" not in review
        assert "header #flagBtn" in css

    def test_the_place_is_a_snapshot_taken_on_open(self):
        app = self.src("frontend/viewer/App.jsx")
        assert "const at = flagPlace(view, ui.ei, ui.si, view.format);" in app
        assert "flagging: { ...at, opened: Date.now() }" in app
        flag = self.src("frontend/viewer/Flag.jsx")
        assert "flagging.place" in flag and "ui." not in flag

    def test_each_opening_starts_a_fresh_form(self):
        """A late reply from the last opening must not land on this one, and
        a reopened dialog must focus its empty textarea, not a stale thanks."""
        flag = self.src("frontend/viewer/Flag.jsx")
        assert "function FlagForm(" in flag
        assert "<FlagForm key={flagging.opened}" in flag

    def test_keys_do_nothing_underneath_the_open_dialog(self):
        app = self.src("frontend/viewer/App.jsx")
        on_key = app[app.index("const onKey = ev =>"):]
        assert on_key.index('getElementById("flagDialog")?.open') < on_key.index("switch (ev.key)")

    def test_send_asks_who_again_with_a_fresh_token(self):
        """A slow first answer must cost the display, never the attribution,
        and a dialog left open past the token's hour must not send a dead one."""
        flag = self.src("frontend/viewer/Flag.jsx")
        assert "const w = await whoIsFlagging();" in flag
        assert "who === undefined ? await" not in flag
        assert "checked again when you send" in flag
        run = self.src("frontend/runtime/flag.mjs")
        assert "settled ??= settledUser(auth.onUser)" in run
        assert "u.getIdToken()" in run[run.index("export async function whoIsFlagging"):]

    def test_a_send_in_flight_cannot_be_cancelled_into_a_lie(self):
        """Cancel mid-send used to close the dialog while the flag was stored."""
        flag = self.src("frontend/viewer/Flag.jsx")
        assert 'disabled={sending} onClick={onClose}' in flag
        assert 'addEventListener("cancel", stop)' in flag

    def test_unsaved_edits_are_saved_before_the_flag(self):
        """The flag's overrides version must include what the charter sees."""
        flag = self.src("frontend/viewer/Flag.jsx")
        assert "if (store.isDirty()) await store.save();" in flag
        assert flag.index("await store.save()") < flag.index("await sendFlag(")

    def test_send_cannot_fire_twice(self):
        flag = self.src("frontend/viewer/Flag.jsx")
        assert "disabled={sending || !!noteProblem(note)}" in flag
        assert "if (sending || noteProblem(note)) return;" in flag

    def test_the_dialog_is_never_inside_main(self):
        app = self.src("frontend/viewer/App.jsx")
        assert "<FlagDialog" in app
        assert "<FlagDialog" not in app[app.index("<main>"):app.index("</main>")]

    def test_firebase_is_only_ever_imported_on_demand(self):
        for rel in ("frontend/viewer", "frontend/runtime", "frontend/core"):
            for p in (self.ROOT / rel).rglob("*"):
                if p.suffix in (".js", ".mjs", ".jsx"):
                    assert "site/auth.js\";" not in p.read_text(), p
        assert 'import("../site/auth.js")' in self.src("frontend/runtime/flag.mjs")


def doubles_doc(shots, end_number=3):
    from curling_score.game import format as F
    d = doc(shots, end_number=end_number)
    d["schema_version"] = 7
    d["format"] = F.DOUBLES.to_json()
    return d


def doubles_shots(n=10, end_number=3):
    from curling_score.game import format as F
    out = []
    for num in range(1, n + 1):
        t = F.DOUBLES.throw_info(num)
        out.append({"number": num, "color": "red" if num % 2 else "yellow",
                    "color_inferred": False, "missing": False, "state_known": True,
                    "position": F.DOUBLES.positions[t.position_slot - 1],
                    "thrower_slot": t.position_slot, "rock_of_player": t.rock_of_player,
                    "has_hammer": t.has_hammer,
                    "label": F.DOUBLES.shot_label(end_number, num),
                    "shot_type": "draw", "stones": []})
    return out


FIELDS = ("id", "number", "color", "label", "position", "rock_of_player",
          "has_hammer", "thrower_slot")


def both(document, overrides):
    import copy
    from curling_score import timeline
    py = timeline.apply_overrides(copy.deepcopy(document), overrides)
    py = [[s.get(f) for f in FIELDS] for s in py["games"][0]["ends"][0]["shots"]]
    js = run_js(setup(document, overrides) +
                f"out(mergedShots(e).map(s => {list(FIELDS)}.map(f => s[f] ?? null)));")
    return py, js


class TestDoublesFormat:
    def test_the_throw_table_comes_from_the_document(self):
        got = run_js(setup(doubles_doc(doubles_shots())) +
                     "const f = formatOf(state.doc);"
                     "out([1,3,9].map(n => throwInfo(n, f)).concat([shotLabel(3, 7, f), roleText(f, 1), roleText(f, 2)]));")
        assert got[:3] == [{"has_hammer": False, "thrower_slot": 1, "rock_of_player": 1},
                           {"has_hammer": False, "thrower_slot": 2, "rock_of_player": 1},
                           {"has_hammer": False, "thrower_slot": 1, "rock_of_player": 2}]
        assert got[3:] == ["3rd end, B's third rock", "1st & 5th", "2nd–4th"]

    def test_fours_is_what_it_always_was(self):
        got = run_js(setup(doc([])) +
                     "const f = formatOf(state.doc);"
                     "out([f.name, throwInfo(13), throwInfo(13, f), shotLabel(2, 16, f)]);")
        assert got[0] == "fours"
        assert got[1] == got[2] == {"has_hammer": False, "thrower_slot": 4, "rock_of_player": 1}
        assert got[3] == "2nd end, skip's second rock"

    def test_a_malformed_format_block_reads_as_fours(self):
        d = doubles_doc(doubles_shots())
        del d["format"]["throw_table"]
        assert run_js(setup(d) + "out(formatOf(state.doc).name);") == "fours"

    def test_the_thrower_row_says_player_and_rock_of_n(self):
        got = run_js(setup(doubles_doc(doubles_shots())) +
                     "const f = formatOf(state.doc);"
                     "out([throwerText(mergedShots(e)[4], f), throwerText(mergedShots(e)[8], f)]);")
        assert got == ["Player B (rock 2 of 3)", "Player A (rock 2 of 2)"]

    def test_a_position_in_a_line_of_text(self):
        """The pager ("red · player B · Draw") and the flag label read it."""
        got = run_js(setup(doubles_doc(doubles_shots())) +
                     "const f = formatOf(state.doc);"
                     "out([positionText('B', f), positionText('A', f), positionText(null, f),"
                     " positionText('lead'), positionText('skip', formatOf({}))]);")
        assert got == ["player B", "player A", None, "lead", "skip"]

    def test_fours_thrower_text_is_unchanged(self):
        got = run_js(setup(doc([{"number": 3, "color": "red", "position": "second",
                                 "rock_of_player": 1, "stones": []}])) +
                     "out(throwerText(mergedShots(e)[0], formatOf(state.doc)));")
        assert got == "second (rock 1)"


class TestRoleSwapParity:
    def test_js_and_python_agree_on_a_swap(self):
        py, js = both(doubles_doc(doubles_shots()), {"0.3": {"roles_swapped": {"red": True}}})
        assert js == py
        assert js[0][4] == "B"

    def test_js_and_python_agree_when_a_swap_is_turned_off(self):
        py, js = both(doubles_doc(doubles_shots()), {"0.3": {"roles_swapped": {"red": False}}})
        assert js == py
        assert js[0][4] == "A"

    def test_js_and_python_agree_on_a_swap_with_a_move(self):
        shots = doubles_shots(n=6, end_number=4)
        for s in shots[4:]:
            s["color_inferred"] = s["missing"] = True
            s["state_known"] = False
        py, js = both(doubles_doc(shots, end_number=4),
                      {"0.4.5": {"before": 1}, "0.4.6": {"before": 1},
                       "0.4": {"roles_swapped": {"red": True}}})
        assert js == py
        assert [row[4] for row in js] == ["B", "A", "A", "B", "A", "B"]

    @pytest.mark.parametrize("patch", [{"roles_swapped": {"red": 1, "yellow": "true"}},
                                       {"roles_swapped": "red"}])
    def test_js_and_python_agree_a_malformed_swap_swaps_nothing(self, patch):
        """Only a real true swaps a team: 1, "true" or a bare colour name is
        not a swap, on either side of the wire."""
        py, js = both(doubles_doc(doubles_shots()), {"0.3": patch})
        assert js == py
        plain_py, plain_js = both(doubles_doc(doubles_shots()), {})
        assert js == plain_js and py == plain_py
        assert [row[4] for row in js][:5] == ["A", "A", "B", "B", "B"]
        got = run_js(setup(doubles_doc(doubles_shots()), {"0.3": patch}) +
                     "out(buildGameView(state.doc, 0, state.overrides).ends[0].swapped);")
        assert got == {}
        from curling_score import timeline
        baked = timeline.apply_overrides(doubles_doc(doubles_shots()), {"0.3": patch})
        assert "roles_swapped" not in baked["games"][0]["ends"][0]

    def test_js_ignores_an_end_key_on_a_fours_chart(self):
        shots = [{"number": n, "color": "red" if n % 2 else "yellow", "position": "lead",
                  "rock_of_player": 1, "stones": []} for n in (1, 2)]
        got = run_js(setup(doc(shots, end_number=3), {"0.3": {"roles_swapped": {"red": True}}}) +
                     "out(mergedShots(e).map(s => s.position));")
        assert got == ["lead", "lead"]

    def test_the_view_carries_the_format_and_the_swap(self):
        got = run_js(setup(doubles_doc(doubles_shots()), {"0.3": {"roles_swapped": {"yellow": True}}}) +
                     "const v = buildGameView(state.doc, 0, state.overrides);"
                     "out([v.format.name, v.ends[0].swapped, endKey(v.game, v.ends[0].end)]);")
        assert got == ["doubles", {"yellow": True}, "0.3"]

    def test_the_hack_call_follows_the_person_across_a_swap(self):
        shots = doubles_shots()
        for s in shots:
            s["line"] = {"start": {"x": 0.15 if s["thrower_slot"] == 1 else -0.15}, "curl": "left"}
        got = run_js(setup(doubles_doc(shots), {"0.3": {"roles_swapped": {"red": True}}}) +
                     "const v = buildGameView(state.doc, 0, state.overrides);"
                     "out(v.ends[0].shots.filter(s => s.color === 'red').map(s => [s.position, s.hack && s.hack.side]));")
        # The set positions were written by the detected role: rocks 1 and 9 at
        # +0.15 (right of centre), rocks 3, 5 and 7 at -0.15 (left). After the
        # swap, 1 and 9 are B's and 3, 5, 7 are A's, so each person's hack is
        # read from their own rocks: B right, A left.
        assert got == [["B", "right"], ["A", "left"], ["A", "left"], ["A", "left"], ["B", "right"]]


class TestDoublesStatsAndSummary:
    def test_doubles_stats_have_a_bucket_per_player(self):
        got = run_js(setup(doubles_doc(doubles_shots())) +
                     "const v = buildGameView(state.doc, 0, state.overrides);"
                     "const s = gatherStats(v);"
                     "out([Object.keys(s.red), s.red.A.thrown, s.red.B.thrown]);")
        assert got == [["A", "B"], 2, 3]

    def test_a_swap_moves_rocks_between_players(self):
        got = run_js(setup(doubles_doc(doubles_shots()), {"0.3": {"roles_swapped": {"red": True}}}) +
                     "const s = gatherStats(buildGameView(state.doc, 0, state.overrides));"
                     "out([s.red.A.thrown, s.red.B.thrown, s.yellow.A.thrown]);")
        assert got == [3, 2, 2]

    def test_an_unknown_position_still_counts(self):
        shots = [{"number": 1, "color": "red", "position": "alternate",
                  "rock_of_player": 1, "stones": []}]
        got = run_js(setup(doc(shots)) +
                     "const s = gatherStats(buildGameView(state.doc, 0, state.overrides));"
                     "out([Object.keys(s.red), s.red.alternate.thrown]);")
        assert got == [["lead", "second", "third", "skip", "alternate"], 1]

    def test_fours_stats_keys_are_unchanged(self):
        got = run_js(setup(doc([])) +
                     "out(Object.keys(gatherStats(buildGameView(state.doc, 0, {})).yellow));")
        assert got == ["lead", "second", "third", "skip"]

    def test_the_end_summary_names_the_power_play(self):
        d = doubles_doc(doubles_shots())
        e = d["games"][0]["ends"][0]
        e["hammer"] = "yellow"
        e["hammer_source"] = "placement"
        e["placement"] = {"hammer": "yellow", "power_play": "left", "complete": True}
        got = run_js(setup(d, {"0.3": {"roles_swapped": {"red": True}}}) +
                     "const v = buildGameView(state.doc, 0, state.overrides);"
                     "const s = endSummary(v, 0);"
                     "out([s.hammer, s.powerPlay, s.hammerSource, s.swapped]);")
        assert got == ["yellow", {"color": "yellow", "side": "left"}, "placement", {"red": True}]

    def test_a_fours_end_summary_has_no_power_play(self):
        got = run_js(setup(doc([])) +
                     "const s = endSummary(buildGameView(state.doc, 0, {}), 0);"
                     "out([s.powerPlay, s.hammerSource]);")
        assert got == [None, None]

    def test_the_format_warning(self):
        fours = doc([])
        fours["format_warning"] = "analysed as fours, but the ends look like doubles"
        d = doubles_doc(doubles_shots())
        d["format"]["check"] = {"looks_like": "fours", "ends": 6, "median_offered": 15}
        quiet = doubles_doc(doubles_shots())
        quiet["format"]["check"] = {"looks_like": "doubles", "ends": 6, "median_offered": 10}
        got = run_js(f"out([formatWarning({json.dumps(fours)}), formatWarning({json.dumps(d)}),"
                     f" formatWarning({json.dumps(quiet)}), formatWarning({json.dumps(doc([]))})]);")
        assert got[0] == "analysed as fours, but the ends look like doubles"
        assert "doubles" in got[1] and "four-player" in got[1]
        assert got[2] is None and got[3] is None

    def test_the_format_warning_is_the_first_flag_not_a_child_of_main(self):
        """On a desktop <main> is a grid whose areas are all spoken for, so a
        child of its own drops to a row below the fold; on a phone it pushed
        the video down under the fixed house card."""
        app = (Path(__file__).resolve().parents[1] / "frontend/viewer/App.jsx").read_text()
        flags = app[app.index("function Flags("):app.index("function Header(")]
        main = app[app.index("<main>"):app.index("</main>")]
        assert 'id="formatWarning"' in flags and 'id="formatWarning"' not in main
        assert flags.index('id="formatWarning"') < flags.index("flags.map(")
        assert "<Flags doc={doc} " in main

    def test_a_phone_peek_hides_the_end_box(self):
        """The peek's bottom 84px belong to the fixed transport bar. The End
        box rendered into them, and a tap beside a transport button landed on
        a swap label and saved a swap."""
        css = (VIEWER / "style.css").read_text()
        start = css.index("@media (max-width: 640px) and (min-height: 521px)")
        phone = css[start:css.index("@media (max-width: 640px) and (max-height: 520px)")]
        at = phone.index('body[data-sheet="peek"] #chart #endBox')
        assert "display: none" in phone[at:phone.index("}", at)]

    def test_the_phone_transport_has_an_opaque_band(self):
        """The bar floats over a sheet that scrolls, and whatever was scrolled
        under its gaps took the tap -- in the open doubles sheet, a swap
        checkbox. Its band is painted in the sheet's colour, under the buttons
        (z-index -1 inside .transport) and over the sheet."""
        css = (VIEWER / "style.css").read_text()
        start = css.index("@media (max-width: 640px) and (min-height: 521px)")
        short = css.index("@media (max-width: 640px) and (max-height: 520px)")
        phone = css[start:short]
        rule = lambda block, sel: block[block.index(sel):block.index("}", block.index(sel))]
        band = rule(phone, ".transport::before {")
        for decl in ('content: ""', "position: absolute", "z-index: -1", "top: -8px",
                     "left: -12px", "right: -12px", "background: var(--panel)"):
            assert decl in band, decl
        assert "background: var(--panel)" in rule(phone, "\n  #chart {")
        # The short screen scrolls under its bar too: the same band, in the
        # page's colour, with the card moved to ::after so it paints over it.
        tail = css[short:]
        assert "z-index: -1" in rule(tail, ".transport::before {")
        assert "background: var(--bg)" in rule(tail, ".transport::before {")
        card = rule(tail, ".transport::after {")
        assert "background: var(--panel)" in card and "border-radius: 10px" in card
        assert "background:" not in rule(tail, ".transport {")


class TestALiveGame:
    """A game whose stream is still being played grows an end at a time: the
    page says it is live, keeps looking for the next end, and does not call
    an end the board has not caught up with 'never posted'."""

    def live_doc(self, run_live=True, game_live=True):
        d = end_four(hammer="yellow", score=None, running=None)
        d["live"] = {"in_progress": run_live, "recorded_s": 5400.0}
        d["games"][0]["in_progress"] = game_live
        return d

    def test_a_game_still_being_played_is_live(self):
        got = run_js(setup(self.live_doc()) +
                     "out([liveGame(state.doc, g), endSummary().gameLive]);")
        assert got == [True, True]

    def test_a_game_that_has_finished_while_its_stream_plays_on_is_not(self):
        got = run_js(setup(self.live_doc(game_live=False)) +
                     "out([liveGame(state.doc, g), endSummary().gameLive]);")
        assert got == [False, False]

    def test_a_finished_live_run_is_not_live(self):
        got = run_js(setup(self.live_doc(run_live=False)) + "out(liveGame(state.doc, g));")
        assert got is False

    def test_an_ordinary_recording_is_not_live(self):
        got = run_js(setup(end_four(hammer="yellow")) +
                     "out([liveGame(state.doc, g), endSummary().gameLive]);")
        assert got == [False, False]

    def test_unposted_ends_are_not_posted_yet_while_it_plays(self):
        got = run_js("out([unreadNote([5, 6], true), unreadNote([5, 6], false),"
                     " unreadNote([], true)]);")
        assert got == ["ends 5, 6 not posted yet", "ends 5, 6 were never posted", None]

    def test_a_live_page_looks_for_the_next_end_every_half_minute(self):
        assert run_js("out(LIVE_POLL_MS);") == 30000


class TestWhichLiveDocumentToShow:
    """A live page swaps in each new document -- except one it should not: a
    live run that restarted rebuilds from its first end, and jumping back to
    fewer ends would take viewers backwards through the game."""

    @staticmethod
    def d(ends, updated, live=True):
        return {"games": [{"ends": [{}] * ends}],
                "live": {"in_progress": live, "updated_at": updated}}

    def test_a_newer_document_with_more_ends_is_shown(self):
        got = run_js(f"out(acceptLiveDoc({json.dumps(self.d(3, 'a'))}, {json.dumps(self.d(4, 'b'))}).live.updated_at);")
        assert got == "b"

    def test_the_same_document_again_is_not_swapped_in(self):
        prev, same = self.d(3, "a"), self.d(3, "a")
        got = run_js(f"const p = {json.dumps(prev)}; out(acceptLiveDoc(p, {json.dumps(same)}) === p);")
        assert got is True

    def test_a_rebuild_with_fewer_ends_waits_until_it_catches_up(self):
        got = run_js(f"out(acceptLiveDoc({json.dumps(self.d(5, 'a'))}, {json.dumps(self.d(1, 'b'))}).live.updated_at);")
        assert got == "a"

    def test_the_final_document_is_always_shown(self):
        got = run_js(f"out(acceptLiveDoc({json.dumps(self.d(5, 'a'))}, {json.dumps(self.d(4, 'b', live=False))}).live.updated_at);")
        assert got == "b"


TEE_Y = 34.747


def delivery_points(gap=None, corner=0.0, t_end=4.7):
    """A rock at rest at (-0.15, 3.2 m behind the tee) until 0.5 s before the
    release, then 2 m/s up the sheet drifting right: [t, y, x] every 0.1 s."""
    out = []
    for i in range(int(round((t_end + 3.0) * 10)) + 1):
        t = round(-3.0 + i / 10, 2)
        if gap and gap[0] < t < gap[1]:
            continue
        moving = max(0.0, t + 0.5)
        yp = -3.2 + 2.0 * moving
        x = -0.15 + 0.04 * moving + corner * moving
        out.append([t, round(TEE_Y - yp, 2), round(x, 3)])
    return out


def delivery_shot(points=None, **kw):
    s = {"number": 3, "color": "red", "hack": {"side": "left"},
         "target_broom": {"x": 0.3, "y": 0.0},
         "line": {"start": {"x": -0.15, "y": TEE_Y + 3.2},
                  "delivery": delivery_points() if points is None else points}}
    s.update(kw)
    return s


D8 = {"schema_version": 8}


class TestDeliveryReason:
    def reason(self, shot, doc=D8):
        return run_js(f"out(deliveryReason({json.dumps(shot)}, {json.dumps(doc)}));")

    def test_a_followed_rock_has_no_reason(self):
        assert self.reason(delivery_shot()) is None

    def test_a_chart_from_before_schema_8_predates_it(self):
        assert self.reason(delivery_shot(), {"schema_version": 7}) == "This chart predates the delivery chart"
        assert self.reason(delivery_shot(), {}) == "This chart predates the delivery chart"

    def test_a_rock_with_no_line_says_why_as_the_strip_does(self):
        assert self.reason(delivery_shot(line=None)) == "The hog-line camera lost this rock"

    def test_a_line_without_a_delivery_is_a_rock_not_followed(self):
        want = "The hog-line camera did not follow this rock from the hack"
        assert self.reason(delivery_shot(points=[])) == want
        assert self.reason(delivery_shot(points=[[0.0, 30.0, 0.1]])) == want
        no_key = delivery_shot(); del no_key["line"]["delivery"]
        assert self.reason(no_key) == want


class TestDeliveryGeometry:
    def geom(self, shot, box="DELIVERYBOX"):
        return run_js(f"out(deliveryGeometry({json.dumps(shot)}, {box}));")

    def test_upright_the_thrower_is_at_the_bottom(self):
        g = self.geom(delivery_shot())
        y = {l["kind"]: l["y1"] for l in g["lines"] if l["kind"] != "centre"}
        assert y["hack"] > y["tee"] > y["hog"]
        assert all(l["y1"] == l["y2"] for l in g["lines"] if l["kind"] != "centre")
        # 10 px of padding, then 8.05 m down to the tee at 359 px for 12.05 m.
        assert y["tee"] == pytest.approx(10 + 8.05 * 359 / 12.05, abs=0.06)
        assert g["stretch"] == 7

    def test_on_its_side_the_hack_is_at_the_left(self):
        g = self.geom(delivery_shot(), "DESKDELIVERYBOX")
        x = {l["kind"]: l["x1"] for l in g["lines"] if l["kind"] != "centre"}
        assert x["hack"] < x["tee"] < x["hog"]
        assert all(l["x1"] == l["x2"] for l in g["lines"] if l["kind"] != "centre")
        assert g["stretch"] == 4

    def test_the_thrower_s_foothold_is_filled_and_the_aim_leaves_it(self):
        g = self.geom(delivery_shot())
        left, right = g["holds"]
        assert (left["side"], left["used"], right["used"]) == ("left", True, False)
        assert left["x"] < right["x"]
        first = [float(v) for v in g["aim"].split(" ")[0].split(",")]
        assert first[0] == pytest.approx(left["x"] + left["w"] / 2, abs=0.2)
        # Upright the foothold sits behind the hack line: the aim leaves its front edge.
        hack = next(l for l in g["lines"] if l["kind"] == "hack")
        assert first[1] == pytest.approx(left["y"], abs=0.2) == pytest.approx(hack["y1"], abs=0.2)

    def test_the_broom_label_stays_on_the_ice(self):
        for bx in (0.3, -0.3):
            g = self.geom(delivery_shot(target_broom={"x": bx, "y": 0.0}))
            end = [float(v) for v in g["aim"].split(" ")[-1].split(",")]
            right = end[0] > g["plot"]["x"] + g["plot"]["w"] / 2
            assert g["aimLabel"]["anchor"] == ("end" if right else "start")
            assert (g["aimLabel"]["x"] < end[0]) == right

    def test_no_broom_is_no_aim(self):
        g = self.geom(delivery_shot(target_broom=None))
        assert g["aim"] is None and g["aimLabel"] is None

    def test_the_dots_run_from_the_ramp_s_first_stop_to_its_last(self):
        g = self.geom(delivery_shot())
        ramp = run_js("out(DELIVERY_RAMP);")
        assert g["dots"][0]["fill"] == ramp[0] and g["dots"][-1]["fill"] == ramp[-1]
        assert g["points"] == len(g["dots"])

    def test_the_ramp_ends_in_the_strip_s_gold(self):
        detail = (Path(__file__).resolve().parents[1] / "frontend/viewer/Detail.jsx").read_text()
        assert run_js("out(DELIVERY_RAMP.at(-1));") == "#a07a00"
        assert 'const GOLD = "#a07a00";' in detail

    def test_a_long_gap_breaks_the_line_through_the_dots(self):
        assert len(self.geom(delivery_shot())["runs"]) == 1
        assert len(self.geom(delivery_shot(delivery_points(gap=(0.5, 1.6))))["runs"]) == 2

    def test_samples_past_the_window_are_not_drawn(self):
        g = self.geom(delivery_shot(delivery_points(t_end=6.0)))
        assert all(d["y"] >= g["plot"]["y"] - 0.05 for d in g["dots"])
        assert g["points"] < len(delivery_points(t_end=6.0))

    def test_a_rock_that_drifts_wide_moves_the_window_not_the_scale(self):
        g = self.geom(delivery_shot(delivery_points(corner=0.16)))
        assert g["stretch"] == 7
        lo, hi = g["plot"]["x"], g["plot"]["x"] + g["plot"]["w"]
        assert all(lo <= d["x"] <= hi for d in g["dots"])

    def test_only_a_rock_that_cannot_fit_widens_it(self):
        g = self.geom(delivery_shot(delivery_points(corner=0.30)))
        assert g["stretch"] < 7

    def test_no_shot_is_nothing_to_draw(self):
        assert run_js("out(deliveryGeometry(null, DELIVERYBOX));") is None


class TestTheDeliveryChart:
    SRC = Path(__file__).resolve().parents[1] / "frontend/viewer/Delivery.jsx"
    DETAIL = Path(__file__).resolve().parents[1] / "frontend/viewer/Detail.jsx"

    def test_the_tab_draws_the_phone_box_at_the_pane_s_width_or_says_why_not(self):
        src = self.SRC.read_text()
        body = src[src.index("export function Delivery("):]
        for needle in ("deliveryReason(shot, doc)", '<p className="dnone">{reason}</p>',
                       "deliveryGeometry(shot, DELIVERYBOX)", "fluid />",
                       'if (!shot) return <p className="dnone">No rocks were detected in this end</p>;'):
            assert needle in body, needle

    def test_the_caption_says_which_way_up_and_how_stretched(self):
        assert ("From above, thrower at the bottom · across ×{g.stretch} · a dot every 0.1 s, "
                "dark at rest to gold past the hog line") in self.SRC.read_text()

    def test_it_is_an_image_with_a_label_and_no_ids(self):
        src = self.SRC.read_text()
        assert 'role="img" aria-label={label}' in src
        # The desktop card stays mounted on a phone: two charts, one page.
        code = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
        assert "<defs" not in code and " id=" not in code

    def test_the_desktop_card_puts_it_between_the_strip_and_the_figures(self):
        src = self.DETAIL.read_text()
        body = src[src.index("export function DeskDetail("):]
        i_strip = body.index("<Strip ")
        i_chart = body.index("<DeliveryChart g={g} label={deliveryLabel(shot, true)} fluid />")
        i_why = body.index('<p className="dlvnone">{why}</p>')
        i_figs = body.index("<Figures ")
        assert i_strip < i_chart < i_why < i_figs
        assert "deliveryGeometry(shot, DESKDELIVERYBOX)" in body

    def test_an_old_chart_keeps_the_desktop_card_it_had(self):
        body = self.DETAIL.read_text()
        body = body[body.index("export function DeskDetail("):]
        assert "const why = measured ? deliveryReason(shot, doc) : null;" in body

    def test_the_desktop_chart_scales_to_the_card(self):
        css = (VIEWER / "style.css").read_text()
        rule = css[css.index("#detailCard .dlv {"):css.index("}", css.index("#detailCard .dlv {"))]
        assert "width: 100%" in rule and "height: auto" in rule


REPORT_FIXTURE = Path(__file__).resolve().parent / "fixtures/report/dimmit_grant.json"


def report_js(body: str, document=None, overrides=None):
    """Run ``body`` with ``view``, ``stats``, ``think`` and ``series`` built from
    Dimmit v Grant (2026-09-29, sheet 3) as its charter graded it -- or from
    ``document`` when one is given."""
    if document is None:
        fx = json.loads(REPORT_FIXTURE.read_text())
        document, overrides = fx["doc"], fx["overrides"]
    return run_js(
        f"const view = buildGameView({json.dumps(document)}, 0, {json.dumps(overrides or {})});\n"
        "const stats = gatherStats(view);\n"
        "const think = gatherThinkingOf(view);\n"
        "const series = cumulativeThinkingOf(view);\n" + body)


class TestTheReportByEnd:
    """The table at the top: the score, the hammer, and each team's
    shooting and clock, end by end."""

    def test_the_ends_and_their_hammer(self):
        got = report_js("out(byEnd(view).ends.map(e => [e.number, e.hammer]));")
        assert got == [[1, "yellow"], [2, "yellow"], [3, "red"], [4, "yellow"]]

    def test_an_end_the_board_never_read_has_no_score(self):
        got = report_js("out(byEnd(view).ends.map(e => e.score));")
        assert got == [{"red": 1, "yellow": 0}, {"red": 0, "yellow": 2},
                       {"red": 3, "yellow": 0}, None]

    def test_an_ungraded_end_has_no_shooting(self):
        got = report_js("out(byEnd(view).ends.map(e => e.shooting));")
        assert got == [{"red": None, "yellow": None}, {"red": 63, "yellow": 75},
                       {"red": 78, "yellow": 41}, {"red": 44, "yellow": 94}]

    def test_the_totals(self):
        got = report_js("out(byEnd(view).total);")
        assert got["score"] == {"red": 4, "yellow": 2}
        assert got["complete"] is False
        assert got["shooting"] == {"red": 61, "yellow": 70}
        assert round(got["thinking"]["red"]) == 653 and round(got["thinking"]["yellow"]) == 789

    def test_the_status_is_ok_and_the_board_was_read(self):
        got = report_js("const t = byEnd(view); out([t.status, t.boardRead]);")
        assert got == ["ok", True]

    def test_an_old_chart_says_it_predates_board_reading(self):
        d = doc([shot(1, "red", "lead")])
        d["schema_version"] = 3
        got = report_js("const t = byEnd(view); out([t.status, t.ends[0].score]);", d)
        assert got == ["predates", None]

    def test_a_withheld_board_says_so(self):
        d = doc([shot(1, "red", "lead")])
        d["games"][0]["scoreboard"] = {"scores_withheld": "could not place"}
        assert report_js("out(byEnd(view).status);", d) == "withheld"

    def test_a_ten_end_game_totals_every_end(self):
        d = doc([shot(1, "red", "lead", user_score=4)])
        first = d["games"][0]["ends"][0]
        d["games"][0]["ends"] = [{**first, "number": n, "score": {"red": n % 2, "yellow": 0},
                                  "shots": [dict(s) for s in first["shots"]]}
                                 for n in range(1, 11)]
        got = report_js("const t = byEnd(view); out([t.ends.length, t.total]);", d)
        assert got[0] == 10
        assert got[1]["score"] == {"red": 5, "yellow": 0} and got[1]["complete"] is True


class TestTheReportHeadToHead:
    def test_by_position(self):
        got = report_js("out(headToHead(stats, view.format).positions"
                        "  .map(p => [p.label, p.red, p.yellow]));")
        assert got == [["Lead", 63, 83], ["Second", 79, 63], ["Third", 54, 71], ["Skip", 50, 63]]

    def test_the_team(self):
        assert report_js("out(headToHead(stats, view.format).team);") == {"red": 61, "yellow": 70}

    def test_by_shot_type_with_the_rocks_behind_each(self):
        got = report_js("out(headToHead(stats, view.format).types"
                        "  .map(t => [t.label, t.red, t.yellow, t.redN, t.yellowN]));")
        assert got == [["Draw", 52, 68, 11, 15], ["Guard", 63, 100, 2, 2], ["Hit", 70, 58, 11, 6]]

    def test_every_player_graded_alike_is_said_once(self):
        got = report_js("out(headToHead(stats, view.format).perPlayer);")
        assert got == {"graded": 6, "thrown": 8}

    def test_the_other_rocks_are_listed(self):
        got = report_js("out(headToHead(stats, view.format).other);")
        assert got == [{"color": "red", "type": "Unknown", "thrown": 2},
                       {"color": "yellow", "type": "Unknown", "thrown": 2},
                       {"color": "yellow", "type": "Not thrown", "thrown": 1}]

    def test_doubles_reads_player_a_and_b(self):
        got = report_js("out(headToHead(stats, view.format).positions.map(p => p.label));",
                        doubles_doc(doubles_shots()))
        assert got == ["Player A", "Player B"]

    def test_nothing_graded_reads_as_no_percentage(self):
        got = report_js("out(headToHead(stats, view.format).positions[0]);",
                        doc([shot(1, "red", "lead")]))
        assert got == {"id": "lead", "label": "Lead", "red": None, "yellow": None}


class TestTheReportDetail:
    def cells(self, color):
        return report_js(
            f"out(detailRows(stats, {color!r}, view.format).rows.map(r => [r.kind, r.label,"
            "  r.cells.map(c => c && `${c.pct}:${c.graded}/${c.thrown}`),"
            "  r.all && `${r.all.pct}:${r.all.graded}/${r.all.thrown}`]));")

    def test_a_group_with_one_type_has_no_row_under_it(self):
        labels = [r[1] for r in self.cells("red")]
        assert labels == ["Draw", "Guard", "Guard", "Centre guard", "Hit", "Hit",
                          "Hit & stick", "Hit & roll", "Peel", "Run back", "Flashed", "Other"]

    def test_the_third_draws(self):
        draw = self.cells("red")[0]
        assert draw == ["group", "Draw", ["67:3/3", "50:2/2", "33:3/5", "58:3/3"], "52:11/13"]

    def test_nothing_of_a_kind_is_an_empty_cell(self):
        guard = self.cells("red")[1]
        assert guard[2] == ["63:2/2", None, None, None]

    def test_nothing_graded_has_no_percentage_but_keeps_its_count(self):
        other = self.cells("red")[-1]
        assert other[2][0] == "null:0/2"

    def test_the_all_row(self):
        got = report_js("out(detailRows(stats, 'red', view.format).all.all);")
        assert got == {"pct": 61, "graded": 24, "thrown": 32}

    def test_yellow_lists_its_own_types(self):
        labels = [r[1] for r in self.cells("yellow")]
        assert labels == ["Draw", "Draw", "Freeze", "Tap up", "Guard", "Centre guard",
                          "Corner guard", "Hit", "Hit", "Hit & stick", "Hit & roll", "Peel",
                          "Flashed", "Other", "Not thrown", "Unknown"]


class TestTheReportClockAndCoverage:
    def test_the_longest_thinks(self):
        got = report_js("out(longestThinks(view, series).map(l =>"
                        "  [clockText(l.secs), l.color, l.position, l.end, l.number, l.type]));")
        assert got[:2] == [["1:28", "yellow", "skip", 1, 14, "Hit"],
                           ["1:18", "yellow", "skip", 3, 13, "Draw"]]
        assert len(got) == 5

    def test_the_median(self):
        assert report_js("out(clockText(series.median));") == "0:21"

    def test_coverage(self):
        got = report_js("out(coverage(view));")
        assert got == {"graded": 48, "thrown": 64, "ungradedEnds": [1],
                       "first": {"ei": 0, "si": 0}}

    def test_the_pill_says_what_is_left(self):
        got = report_js("out(coverageText(coverage(view)));")
        assert got == "48 of 64 rocks graded · end 1 still to grade"

    def test_the_pill_goes_once_every_rock_is_graded(self):
        got = report_js("out(coverageText(coverage(view)));",
                        doc([shot(1, "red", "lead", user_score=3)]))
        assert got is None

    def test_nothing_graded_says_so(self):
        got = report_js("out(coverageText(coverage(view)));", doc([shot(1, "red", "lead")]))
        assert got == "No rocks graded yet"

    def test_the_notes(self):
        got = report_js("out(reportNotes(coverage(view), think, view.ends.length));")
        assert got == [
            "Percentages come from graded rocks only: 48 of 64. End 1 hasn’t been graded. "
            "A rock nobody graded counts as thrown, never as a miss.",
            "Thinking time is read for 56 of 64 rocks. An end’s first rock has nothing to "
            "time from, and the camera missed 4 more. 1 is estimated (outlined). Treat the "
            "totals as lower bounds."]

    def test_no_clock_means_no_clock_note(self):
        got = report_js("out(reportNotes(coverage(view), think, view.ends.length).length);",
                        doc([shot(1, "red", "lead")]))
        assert got == 1


class TestTheReportWords:
    def test_end_lists(self):
        assert run_js("out([endList([1]), endList([1, 3]), endList([1, 2, 4])]);") == [
            "end 1", "ends 1 and 3", "ends 1, 2 and 4"]

    def test_the_meta_line(self):
        got = run_js("out(reportMeta({chart: {league: 'Tuesday Super League 2026-2027',"
                     " played_at: '2026-09-30T02:00:00+00:00'}, source: {sheet: 3},"
                     " games: [{}]}, 0, d => d.toISOString().slice(0, 10)));")
        assert got == "Tuesday Super League 2026-2027 · Sheet 3 · 2026-09-30 · Game report"

    def test_a_recording_of_two_games_says_which(self):
        got = run_js("out(reportMeta({chart: {}, source: {}, games: [{}, {}]}, 1));")
        assert got == "Game 2 of 2 · Game report"

    def test_team_names_fall_back_to_the_colours(self):
        got = run_js("out(teamNames({teams: {red: {name: 'Dimmit'}, yellow: {name: null}}}));")
        assert got == {"red": "Dimmit", "yellow": "Yellow"}


class TestTheReportChartLabels:
    BOX = "{ w: 868, h: 200, padL: 52, padR: 128, padT: 22, padB: 30 }"

    def test_the_three_longest_bars_skip_a_neighbour(self):
        """End 3's rocks 13 and 15 are two apart: one label, not two piled up."""
        got = report_js(f"out(barLabels(barsGeometry(series, null, {self.BOX}))"
                        "  .map(l => [l.shot, l.text]));")
        assert got == [[14, "1:28"], [45, "1:18"], [63, "1:17"]]

    def test_each_line_is_labelled_with_its_total(self):
        got = report_js(f"out(lineEnds(chartGeometry(series, null, {{...{self.BOX}, h: 240,"
                        " padT: 12}), series).map(e => [e.color, clockText(e.total)]));")
        assert got == [["yellow", "13:09"], ["red", "10:53"]]

    def test_two_totals_that_would_overlap_are_pushed_apart(self):
        got = run_js("const g = {lines: [{color: 'yellow', points: [[0, 0], [100, 50]]},"
                     " {color: 'red', points: [[0, 0], [100, 55]]}]};"
                     "out(lineEnds(g, {red: 600, yellow: 590}).map(e => [e.color, e.y]));")
        assert got == [["yellow", 61.5], ["red", 43.5]]

    def test_a_bar_knows_its_seconds(self):
        got = report_js(f"out(barsGeometry(series, null, {self.BOX}).bars[0].secs);")
        assert isinstance(got, (int, float))
