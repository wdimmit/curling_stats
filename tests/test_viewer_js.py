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



class TestTheChartPageAsksWhoMayEdit:
    """modeOf / accessMode / viewOnlyText: the edit-or-watch decision a
    guarded /c/ page makes once, before it renders."""
    WIRE = Path(__file__).resolve().parents[1] / "frontend/core/wire.mjs"
    EDIT = "W.modeOf({mode: 'edit', slug: 'a', merge: true, guarded: true})"

    def js(self, expr):
        script = (f"const W = await import({self.WIRE.as_uri()!r});\n"
                  f"console.log(JSON.stringify({expr}));")
        proc = subprocess.run([node, "--input-type=module", "-e", script],
                              capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_only_a_page_the_server_marks_asks(self):
        assert self.js("W.modeOf({mode: 'edit', slug: 'a', guarded: true}).guarded") is True
        assert self.js("W.modeOf({mode: 'edit', slug: 'a'}).guarded") is False
        assert self.js("W.modeOf(undefined).guarded") is False
        assert self.js("W.modeOf({mode: 'edit', slug: 'a'}).viewOnly") is None

    def test_a_yes_edits(self):
        got = self.js(f"W.accessMode({self.EDIT}, {{edit: true, reason: 'owner', team: null}})")
        assert got["readOnly"] is False and got["viewOnly"] is None and got["merge"] is True

    def test_a_no_watches_and_says_why(self):
        got = self.js(f"W.accessMode({self.EDIT},"
                      " {edit: false, reason: 'not-yours', team: {name: 'Thistles'}})")
        assert got["readOnly"] is True
        assert got["viewOnly"] == {"reason": "not-yours", "team": "Thistles"}

    def test_no_answer_watches_unchecked(self):
        """Sign-in slow, offline, or a garbled reply: never editable, never blank."""
        for answer in ("null", "undefined", "{}", "{edit: 'yes'}"):
            got = self.js(f"W.accessMode({self.EDIT}, {answer})")
            assert got["readOnly"] is True, answer
            assert got["viewOnly"] == {"reason": "unchecked", "team": None}, answer

    def test_the_lines_the_page_shows(self):
        line = lambda why: self.js(f"W.viewOnlyText({why})")   # noqa: E731
        assert line("{reason: 'signin', team: null}") == \
            "View only — sign in to edit if this chart is yours or your team's"
        assert line("{reason: 'not-yours', team: null}") == \
            "View only — only the person or team who made this chart can change it"
        assert line("{reason: 'not-yours', team: 'Thistles'}") == \
            "View only — only team Thistles can change this chart"
        assert line("{reason: 'unchecked', team: null}") == \
            "View only — couldn't check sign-in. Reload to try again."
        assert line("null") == ""

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



class TestTheChartToken:
    """runtime/chartAuth.mjs, with Firebase stood in for by `get`."""
    MODULE = Path(__file__).resolve().parents[1] / "frontend/runtime/chartAuth.mjs"

    def run(self, body):
        script = ("const asked = [], ticks = [], on = {};\n"
                  f"const M = await import({self.MODULE.as_uri()!r});\n"
                  "function out(v) { console.log(JSON.stringify(v)); process.exit(0); }\n"
                  + body)
        proc = subprocess.run([node, "--input-type=module", "-e", script],
                              capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_it_keeps_the_last_token_for_the_close_of_tab_save(self):
        got = self.run(
            "const answers = ['t1', undefined, 't2', null];\n"
            "const A = M.chartAuth(async ({force}) => { asked.push(force); return answers.shift(); });\n"
            "const seen = [];\n"
            "seen.push(await A.fresh(), A.last());       // a token\n"
            "seen.push(await A.fresh(), A.last());       // no answer: keep it\n"
            "seen.push(await A.fresh(true), A.last());   // forced: the new one\n"
            "seen.push(await A.fresh(), A.last());       // signed out: none\n"
            "out({seen, asked});")
        assert got["seen"] == ["t1", "t1", "t1", "t1", "t2", "t2", None, None]
        assert got["asked"] == [False, False, True, False]

    def test_it_is_renewed_every_five_minutes_and_on_coming_back(self):
        got = self.run(
            "let tick = null;\n"
            "globalThis.setInterval = (fn, ms) => { ticks.push(ms); tick = fn; return 1; };\n"
            "globalThis.addEventListener = (t, fn) => { on[t] = fn; };\n"
            "globalThis.document = {visibilityState: 'visible'};\n"
            "const A = M.chartAuth(async ({force}) => { asked.push(force); return 't'; });\n"
            "A.keepFresh(); tick(); on.visibilitychange();\n"
            "await new Promise(r => setImmediate(r));\n"
            "out({ticks, asked, tickMs: M.TOKEN_TICK_MS});")
        assert got == {"ticks": [300000], "asked": [False, False], "tickMs": 300000}

    # A Firebase ID token, as far as chartAuth reads one: its payload's exp.
    JWT = ("const jwt = (exp, tag) => 'h.' + Buffer.from(JSON.stringify({exp, tag}))"
           ".toString('base64url') + '.s';\n"
           "const inMin = m => Math.floor(Date.now() / 1000) + m * 60;\n")

    def ticked(self, minutes_left, how="tick()"):
        return self.run(
            self.JWT +
            "let tick = null;\n"
            "globalThis.setInterval = (fn, ms) => { tick = fn; return 1; };\n"
            "globalThis.addEventListener = (t, fn) => { on[t] = fn; };\n"
            "globalThis.document = {visibilityState: 'visible'};\n"
            f"const A = M.chartAuth(async ({{force}}) => {{ asked.push(force);"
            f" return force ? jwt(inMin(60), 'new') : jwt(inMin({minutes_left}), 'old'); }});\n"
            f"A.keepFresh(); {how};\n"
            "await new Promise(r => setTimeout(r, 20));\n"
            "out({asked, last: JSON.parse(Buffer.from(A.last().split('.')[1], 'base64url')).tag});")

    def test_a_token_near_its_end_is_renewed_by_the_tick(self):
        """Firebase renews a token only within 30 s of its end when asked, and
        its own early refresh runs only for other Firebase products -- so the
        tick has to look at the token's expiry and renew it itself."""
        assert self.ticked(3) == {"asked": [False, True], "last": "new"}

    def test_and_on_coming_back_to_the_tab(self):
        assert self.ticked(3, "on.visibilitychange()") == {"asked": [False, True], "last": "new"}

    def test_a_token_with_time_left_is_left_alone(self):
        assert self.ticked(30) == {"asked": [False], "last": "old"}


class TestSavesCarryTheToken:
    """overridesStore with fetch, timers and the page's events stood in for."""
    MODULE = Path(__file__).resolve().parents[1] / "frontend/runtime/overridesStore.mjs"

    def run(self, body, replies=()):
        script = (
            "const calls = [], timers = [], on = {}, asked = [];\n"
            f"const replies = {json.dumps(list(replies))};\n"
            "globalThis.addEventListener = (t, fn) => { on[t] = fn; };\n"
            "globalThis.setTimeout = (fn, ms) => { timers.push(ms); return timers.length; };\n"
            "globalThis.clearTimeout = () => {};\n"
            "globalThis.fetch = async (url, init) => {\n"
            "  calls.push({url, ...init, body: JSON.parse(init.body)});\n"
            "  const status = replies.length ? replies.shift() : 200;\n"
            "  return {ok: status < 300, status, headers: {get: () => null},\n"
            "          json: async () => ({ok: status < 300, version: 1})};\n"
            "};\n"
            "const auth = {fresh: async (force = false) => { asked.push(force);"
            " return force ? 't2' : 't1'; }, last: () => 't1'};\n"
            "const EDIT = {mode: 'edit', readOnly: false, merge: true, shared: false};\n"
            f"const S = await import({self.MODULE.as_uri()!r});\n"
            "function out(v) { console.log(JSON.stringify(v)); process.exit(0); }\n"
            + body)
        proc = subprocess.run([node, "--input-type=module", "-e", script],
                              capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_a_guarded_save_carries_the_token(self):
        got = self.run("S.start(EDIT, {overrides: {}, version: 0, auth});\n"
                       "S.apply({a: {x: 1}}, 'a'); await S.save();\n"
                       "out({calls, asked, status: S.getStatus().text});")
        (call,) = got["calls"]
        assert call["headers"]["Authorization"] == "Bearer t1"
        assert call["url"] == "overrides.json?merge=1&v=0" and call["body"] == {"a": {"x": 1}}
        assert got["asked"] == [False] and got["status"].startswith("saved")

    def test_every_save_asks_for_the_token_again(self):
        """A tab open past the hour keeps saving: Firebase renews the token when
        asked, so each save asks rather than reusing the first one."""
        got = self.run("S.start(EDIT, {overrides: {}, version: 0, auth});\n"
                       "S.apply({a: {x: 1}}, 'a'); await S.save();\n"
                       "S.apply({a: {x: 2}}, 'a'); await S.save();\n"
                       "out({asked});")
        assert got["asked"] == [False, False]

    def test_an_unguarded_save_carries_none(self):
        got = self.run("S.start(EDIT, {overrides: {}, version: 0});\n"
                       "S.apply({a: {x: 1}}, 'a'); await S.save();\n"
                       "out(calls);")
        assert "Authorization" not in got[0]["headers"]

    def test_a_refusal_renews_the_token_once_then_stops(self):
        got = self.run("S.start(EDIT, {overrides: {}, version: 0, auth});\n"
                       "S.apply({a: {x: 1}}, 'a'); timers.length = 0; await S.save();\n"
                       "out({calls, asked, timers, status: S.getStatus().text,"
                       " dirty: S.isDirty()});",
                       replies=[401, 403])
        assert [c["headers"]["Authorization"] for c in got["calls"]] == ["Bearer t1", "Bearer t2"]
        assert got["asked"] == [False, True]
        assert got["status"] == "not saved — sign in again" and got["dirty"] is True
        assert 5000 not in got["timers"]        # retrying cannot fix a refusal

    def test_a_renewed_token_that_works_saves(self):
        got = self.run("S.start(EDIT, {overrides: {}, version: 0, auth});\n"
                       "S.apply({a: {x: 1}}, 'a'); await S.save();\n"
                       "out({n: calls.length, status: S.getStatus().text, dirty: S.isDirty()});",
                       replies=[401, 200])
        assert got["n"] == 2 and got["status"].startswith("saved") and got["dirty"] is False

    def test_a_chart_claimed_while_open_takes_a_token_at_the_first_refusal(self):
        """An unowned chart open in one tab, claimed on /mine in another: the
        tab had no token to send. It takes one at the first refusal, rather
        than lose every edit made since."""
        got = self.run("let made = 0, kept = 0;\n"
                       "const later = () => { made++;"
                       " return {...auth, keepFresh: () => { kept++; }}; };\n"
                       "S.start(EDIT, {overrides: {}, version: 0, authLater: later});\n"
                       "S.apply({a: {x: 1}}, 'a'); await S.save();\n"
                       "S.apply({a: {x: 2}}, 'a'); await S.save();\n"
                       "out({auths: calls.map(c => c.headers.Authorization ?? null), made, kept,"
                       " status: S.getStatus().text});",
                       replies=[401, 200, 200])
        assert got["auths"] == [None, "Bearer t2", "Bearer t1"]
        assert got["made"] == 1 and got["kept"] == 1 and got["status"].startswith("saved")

    def test_an_open_chart_nobody_claims_never_asks_for_sign_in(self):
        got = self.run("let made = 0;\n"
                       "S.start(EDIT, {overrides: {}, version: 0,"
                       " authLater: () => { made++; return auth; }});\n"
                       "S.apply({a: {x: 1}}, 'a'); await S.save();\n"
                       "out({made, auth: calls[0].headers.Authorization ?? null});")
        assert got == {"made": 0, "auth": None}

    def test_the_close_of_tab_save_is_a_keepalive_fetch_with_the_token(self):
        got = self.run("S.start(EDIT, {overrides: {a: {x: 1}}, version: 3, auth});\n"
                       "S.apply({a: {x: 1}, b: {y: 2}}, 'b');\n"
                       "on.pagehide(); await null;\n"
                       "out(calls);")
        (call,) = got
        assert call["keepalive"] is True and call["method"] == "POST"
        assert call["headers"]["Authorization"] == "Bearer t1"
        assert call["url"] == "overrides.json?merge=1" and call["body"] == {"b": {"y": 2}}

    def test_an_unguarded_close_of_tab_save_carries_no_token(self):
        got = self.run("S.start(EDIT, {overrides: {}, version: 0});\n"
                       "S.apply({a: {x: 1}}, 'a'); on.pagehide(); await null;\n"
                       "out(calls);")
        assert got[0]["keepalive"] is True and "Authorization" not in got[0]["headers"]

    def test_a_close_of_tab_save_that_fails_is_swallowed(self):
        """Offline, or a body over keepalive's 64 KB: the page is going, and
        nothing may be thrown into it on the way out."""
        got = self.run("let unhandled = 0, tried = 0;\n"
                       "process.on('unhandledRejection', () => unhandled++);\n"
                       "globalThis.fetch = () => { tried++;"
                       " return Promise.reject(new TypeError('too big')); };\n"
                       "S.start(EDIT, {overrides: {}, version: 0, auth});\n"
                       "S.apply({a: {x: 1}}, 'a'); on.pagehide();\n"
                       "await new Promise(r => setImmediate(r));\n"
                       "out({tried, unhandled});")
        assert got == {"tried": 1, "unhandled": 0}



class TestCheckingAccess:
    """runtime/access.mjs checkAccess: what a guarded page asks before it
    renders, with sign-in and fetch stood in for."""
    MODULE = Path(__file__).resolve().parents[1] / "frontend/runtime/access.mjs"

    def run(self, who, accounts=True, status=200, reply=None, throws=False):
        fetch = ("async () => { throw new Error('offline'); }" if throws else
                 "async (url, init) => { calls.push({url, init: init || null});"
                 f" return {{ok: {str(200 <= status < 300).lower()}, status: {status},"
                 f" json: async () => ({json.dumps(reply)})}}; }}")
        script = ("const calls = [];\n"
                  f"globalThis.fetch = {fetch};\n"
                  f"const M = await import({self.MODULE.as_uri()!r});\n"
                  f"const got = await M.checkAccess({{who: async () => {who},"
                  f" accounts: async () => {str(accounts).lower()}}});\n"
                  "console.log(JSON.stringify({got: got === undefined ? 'undefined' : got,"
                  " calls}));")
        proc = subprocess.run([node, "--input-type=module", "-e", script],
                              capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout.strip().splitlines()[-1])

    YES = {"edit": True, "reason": "owner", "team": None}

    def test_a_signed_in_person_asks_with_their_token(self):
        got = self.run("({email: 'a@x', token: 'tok'})", reply=self.YES)
        assert got["got"] == self.YES
        (call,) = got["calls"]
        assert call["url"] == "access.json"
        assert call["init"]["headers"]["Authorization"] == "Bearer tok"

    def test_nobody_signed_in_asks_without_one(self):
        reply = {"edit": False, "reason": "signin", "team": None}
        got = self.run("null", reply=reply)
        assert got["got"] == reply and got["calls"] == [{"url": "access.json", "init": None}]

    def test_sign_in_that_does_not_answer_is_no_answer(self):
        got = self.run("undefined")
        assert got == {"got": None, "calls": []}

    def test_a_sign_in_sdk_that_never_loaded_is_no_answer_not_signed_out(self):
        """Blocked or offline: accounts read as off on a page the server only
        marks guarded when they are on. "Sign in" there is a button that can
        never work; only "couldn't check" is true."""
        got = self.run("null", accounts=False)
        assert got == {"got": None, "calls": []}

    def test_a_refused_or_failed_ask_is_no_answer(self):
        assert self.run("null", status=500, reply={"detail": "boom"})["got"] is None
        assert self.run("null", throws=True)["got"] is None

class TestTheGuardedPage:
    """The boot of a /c/ page whose chart is somebody's, and its view-only line."""
    ROOT = Path(__file__).resolve().parents[1]

    def src(self, rel):
        return (self.ROOT / rel).read_text()

    def test_it_asks_before_the_first_render(self):
        main = self.src("frontend/viewer/main.jsx")
        assert ('Promise.all([fetch("timeline.json").then(r => r.json()), loadOverrides(),'
                ' loadAccess()])') in main
        assert "const config = base.guarded ? accessMode(base, access) : base;" in main

    def test_the_boot_asks_checkaccess_only_on_a_guarded_page(self):
        """What checkAccess answers is tested in TestCheckingAccess."""
        main = self.src("frontend/viewer/main.jsx")
        loader = main[main.index("async function loadAccess()"):]
        assert "return base.guarded ? checkAccess() : null;" in loader[:200]

    def test_only_an_editable_guarded_page_keeps_a_token(self):
        main = self.src("frontend/viewer/main.jsx")
        assert "const auth = base.guarded && !config.readOnly ? chartAuth() : null;" in main
        assert "store.start(config, { overrides: ov.map, version: ov.version, auth, authLater });" in main
        assert ("const authLater = config.hosted && !config.readOnly && !auth"
                " ? () => chartAuth() : null;") in main

    def test_the_header_says_view_only_where_the_save_status_is(self):
        app = self.src("frontend/viewer/App.jsx")
        assert "{config.viewOnly ? <ViewOnly why={config.viewOnly} /> : null}" in app
        view = self.src("frontend/viewer/ViewOnly.jsx")
        assert "viewOnlyText(why)" in view
        assert 'why.reason === "signin"' in view and "location.reload()" in view
        assert '<span className="short">View only</span>' in view

    def test_a_phone_says_just_view_only(self):
        css = self.src("src/curling_score/viewer/style.css")
        assert "header > #viewOnly       { order: 9; }" in css
        assert "#viewOnly .short { display: none; }" in css
        phone = css[css.index("@media (max-width: 640px) and (min-height: 461px)"):]
        assert "#viewOnly .long { display: none; }" in phone
        assert "#viewOnly .short { display: inline; }" in phone

    def test_on_a_phone_the_way_to_sign_in_stays_on_screen(self):
        """Watch mode shows the site's name and #src in the 48px header; with
        the view-only line and Sign in as well, the row ran to 523px at 390
        and pushed Sign in and the menu off the screen."""
        css = self.src("src/curling_score/viewer/style.css")
        phone = css[css.index("@media (max-width: 640px) and (min-height: 461px)"):]
        assert "body:has(#viewOnly) #src { display: none; }" in phone
        assert ("body:has(#viewOnly) header > h1 { min-width: 0; overflow: hidden;"
                " text-overflow: ellipsis; white-space: nowrap; }") in phone



class TestThinkingTimeNeedsSignIn:
    """/thinking: with accounts on, the ranking is for people signed in."""
    ROOT = Path(__file__).resolve().parents[1]

    def src(self):
        return (self.ROOT / "frontend/site/Thinking.jsx").read_text()

    def test_it_asks_only_once_auth_has_decided_and_with_the_token(self):
        page = " ".join(self.src().split())
        assert ('useResource("/api/reports/thinking", { skip: !ready || (accounts && !user),'
                ' deps: [user?.uid] })') in page
        assert "auth: false" not in page

    def test_signed_out_it_offers_sign_in_instead(self):
        page = " ".join(self.src().split())
        assert "Thinking time is for signed-in members.</p>" in page
        assert "each league's slowest teams" not in page
        assert "signIn().catch(" in page

    def test_its_link_shows_only_to_someone_signed_in(self):
        """Every page's header: hidden while auth is deciding, and for nobody;
        shown when signed in, or when there are no accounts at all."""
        ui = (self.ROOT / "frontend/site/ui.jsx").read_text()
        header = ui[ui.index("export function Header("):]
        assert 'const SIGNED_IN_ONLY = new Set(["/thinking"]);' in ui
        assert ("links.filter(([href]) => !SIGNED_IN_ONLY.has(href)"
                " || (ready && (!accounts || !!user)))") in " ".join(header.split())

class TestChartingSignsYouInFirst:
    """With accounts on, Chart and Submit need somebody to own the chart."""
    ROOT = Path(__file__).resolve().parents[1]

    def src(self, rel):
        return (self.ROOT / rel).read_text()

    def test_chart_signs_in_then_carries_on_and_a_shut_popup_sends_nothing(self):
        games = self.src("frontend/site/Games.jsx")
        actions = games[games.index("function Actions("):games.index("/* One draw:")]
        assert "if (accounts && !user) {" in actions
        assert "try { await ensureSignedIn(); } catch { setBusy(false); return; }" in actions
        assert actions.index("ensureSignedIn") < actions.index("authedFetch(url")

    def test_the_game_list_says_only_you_can_change_your_chart(self):
        games = " ".join(self.src("frontend/site/Games.jsx").split())
        assert ("<strong>Chart</strong> starts your own chart of the game. Only you, or your"
                " team, can change it; send the link and others can watch.") in games
        assert "private to whoever has it" not in games

    def test_submit_says_sign_in_and_signs_in_before_it_sends(self):
        submit = self.src("frontend/site/Submit.jsx")
        assert '{needsSignIn ? "Sign in to get your link" : "Get my link"}' in submit
        assert "try { await ensureSignedIn(); } catch { return; }" in submit
        assert submit.index("ensureSignedIn();") < submit.index('authedFetch("/api/submissions"')

    def test_signing_in_waits_for_the_page_to_know_who(self):
        auth = self.src("frontend/site/auth.js")
        fn = auth[auth.index("export async function ensureSignedIn()"):]
        assert "if (user) return user;" in fn and "await signIn();" in fn
        assert "onUser(u =>" in fn

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
    def test_it_starts_the_lead_in_before_the_release(self):
        got = run_js(setup(doc([shot(1, "red", "lead", t_release_s=83.0, t_enter_s=100.0)])) +
                     "state.leadIn = 10; out(shotVideoTime(merge(g, e, e.shots[0])));")
        assert got == 73.0

    def test_with_no_release_seen_it_starts_well_before_the_arrival(self):
        got = run_js(setup(doc([shot(1, "red", "lead", t_enter_s=100.0)])) +
                     "state.leadIn = 10; out(shotVideoTime(merge(g, e, e.shots[0])));")
        assert got == 100.0 - 15 - 10

    def test_an_older_charts_rock_timed_by_its_release_is_not_moved_back_again(self):
        # Before 2026.10.01.1 a rock settled from its release carried no
        # release, but its t_enter is the release itself.
        got = run_js(setup(doc([shot(1, "red", "lead", t_enter_s=100.0, reason="hogged")])) +
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
        assert got == 100.0 - 8 - 15 - 10

    def test_a_blank_with_no_times_at_all_has_nowhere_to_seek(self):
        got = run_js(setup(doc([shot(1, "red", "lead", missing=True)])) +
                     "out(shotVideoTime(merge(g, e, e.shots[0])));")
        assert got is None


class TestSavedPrefs:
    """What a returning browser gets back from localStorage."""

    PREFS = Path(__file__).resolve().parents[1] / "frontend/runtime/prefs.mjs"

    def load(self, stored):
        """The prefs a page loads from ``stored``, and what it loads again
        once it has saved them."""
        return run_js(
            "const box = new Map();\n"
            "globalThis.localStorage = { getItem: k => box.get(k) ?? null,"
            " setItem: (k, v) => box.set(k, v) };\n"
            f"box.set('curlchart', {json.dumps(json.dumps(stored))});\n"
            f"const P = await import({self.PREFS.as_uri()!r});\n"
            "const first = P.loadPrefs(); P.savePrefs(first);\n"
            "out([first, P.loadPrefs()]);")

    def test_a_lead_in_saved_when_it_counted_from_the_arrival_is_dropped(self):
        from curling_score import timeline
        first, again = self.load({"autoplay": False, "leadIn": 10})
        assert first["leadIn"] == timeline.VIDEO_LEAD_IN_S
        assert first["autoplay"] is False
        assert again == first

    def test_a_lead_in_saved_since_is_kept(self):
        first, again = self.load({"leadIn": 8, "leadInFrom": "release"})
        assert first["leadIn"] == 8 and again == first


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
        assert got == [2899.0 - 90 - 25, 2899.0 - 45 - 25, 2899.0 - 25]

    def test_a_trailing_blank_is_guessed_after_the_last_seen_rock(self):
        shots = self._shots()
        shots[3]["t_enter_s"] = 3000.0
        got = run_js(setup(doc(shots, end_number=4)) +
                     "state.leadIn = 0; out(shotVideoTime(mergedShots(e)[5]));")
        assert got == 3090.0 - 15


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
        """Ten seconds before the rock was thrown -- with no release seen, a
        typical flight before it entered -- so the throw is seen and not just
        its aftermath: the same rule the transport already uses."""
        got = run_js(setup(end_four()) + "out(rockRows()[2].tVideo);")
        assert got == 3290.0 + 3 * 60 - 15 - 5


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



class TestContactFromThePath:
    """A hit the overhead never saw -- a sweeper over the rock -- is placed
    from the camera behind the thrower, whose path loses the rock as it closes
    on the stone: the two read as one box from that far back."""

    def figs(self, s):
        return {f["key"]: f for f in run_js(f"out(lineFigures({json.dumps(s)}, {{schema_version: 6}}));")["figures"]}

    def s_0MZM_e4_s9(self, **kw):
        """s_0MZM5biOgc46X2jEa, end 4, rock 9: a sweeper hid it from above
        2.8 m out; it took the yellow at (1.26, 0.14) off the side of the
        four-foot and rolled a metre left to rest beside the button. Checked
        against the video, 2026-10-10. The path is the one read past its lost
        track (`linetime.path_until`): last seen at 0.57 m, 0.47 m from the
        yellow."""
        base = dict(
            target_broom={"x": 1.5346, "y": 0.3737}, delivered_stone_index=0,
            stones=[{"color": "red", "x": 0.2664, "y": -0.3264}],
            house_delta={"added": [{"color": "red", "x": 0.2664, "y": -0.3264}],
                         "removed": [{"color": "yellow", "x": 1.2594, "y": 0.1442}], "moved": []},
            track=[[3423.7, 0.984, 3.323], [3423.9, 1.006, 3.14], [3424.0, 1.026, 3.045],
                   [3424.1, 1.039, 2.934], [3424.2, 1.052, 2.835]],
            line={"start": {"x": -0.052, "y": 37.774}, "at_hog": {"x": 0.4661, "offset_m": 0.1182},
                  "at_broom": {"x": 2.028, "miss_m": 0.4934}, "side": "wide", "curl": "left",
                  "confirmed": True, "hog_path": [],
                  "path": [[20.14, 0.895], [15.25, 1.075], [10.07, 1.175], [5.09, 1.2], [3.34, 1.176],
                           [2.19, 1.156], [1.52, 1.134], [0.88, 1.118], [0.57, 1.058]]})
        base.update(kw)
        return shot(9, "red", "third", **base)

    def test_the_path_s_closest_approach_to_the_stone_it_struck_is_where_it_hit(self):
        # Read at (1.058, 0.57), 0.96 m left of the line; to rest it was 1.80.
        f = self.figs(self.s_0MZM_e4_s9())
        assert (f["curl"]["value"], f["curl"]["note"]) == ("3 ft 2 in", "from its line to where it hit a stone")

    def test_a_path_cut_short_well_before_the_stone_is_measured_to_rest(self):
        # As first published: the path read only to a second past the lost
        # track, 1.85 m out and 1.7 m from the yellow. The roll counts.
        s = self.s_0MZM_e4_s9()
        s["line"]["path"] = [[20.14, 0.895], [10.07, 1.175], [3.36, 1.167], [2.18, 1.15], [1.85, 1.136]]
        f = self.figs(s)
        assert (f["curl"]["value"], f["curl"]["note"]) == ("5 ft 11 in", "from its line to where it stopped")

    def test_a_path_that_passed_more_than_a_hand_from_reach_did_not_hit(self):
        # Its nearest, 0.57 m from the yellow's centre.
        s = self.s_0MZM_e4_s9()
        s["line"]["path"] = s["line"]["path"][:-1] + [[0.62, 0.95]]
        assert self.figs(s)["curl"]["note"] == "from its line to where it stopped"

    def test_only_past_where_the_overhead_lost_it(self):
        """s_1WB80m666M2idwp59 e3 r5: a draw to the button that passed a guard
        the house read in the other colour after, so "removed" it. The path
        came 0.44 m from it; the overhead followed the rock past it, 0.39 m
        off, and on to rest without a break. Its curl stays a draw's, to rest."""
        s = shot(5, "red", "second", target_broom={"x": -1.6404, "y": 0.0441}, delivered_stone_index=0,
                 stones=[{"color": "red", "x": -0.0707, "y": -0.1114}],
                 house_delta={"added": [{"color": "red", "x": -0.1859, "y": 2.751},
                                        {"color": "red", "x": -0.0707, "y": -0.1114}],
                              "removed": [{"color": "yellow", "x": -0.1585, "y": 2.8992}], "moved": []},
                 track=[[9508.87, -0.545, 2.952], [9508.97, -0.545, 2.893], [9509.07, -0.545, 2.849],
                        [9509.17, -0.545, 2.786], [9509.27, -0.536, 2.745], [9509.67, -0.53, 2.528],
                        [9511.27, -0.444, 1.598], [9513.27, -0.269, 0.564], [9514.87, -0.111, 0.023],
                        [9515.27, -0.093, -0.035]],
                 line={"start": None, "at_hog": {"x": -0.7563, "offset_m": -0.1872},
                       "at_broom": {"x": -1.9693, "miss_m": -0.3289}, "curl": "right", "confirmed": None,
                       "hog_path": [], "path": [[3.97, -0.716], [3.4, -0.639], [2.81, -0.585], [2.21, -0.52],
                                                [1.09, -0.309], [-0.11, 0.017]]})
        f = self.figs(s)
        assert (f["curl"]["value"], f["curl"]["note"]) == ("6 ft 3 in", "from its line to where it stopped")
        # Lost before the guard, the same path would place a hit there.
        s["track"] = s["track"][:1]
        assert self.figs(s)["curl"]["note"] == "from its line to where it hit a stone"

    def test_the_overhead_s_own_contact_comes_first(self):
        # A track that reached the yellow places the hit, not the path: read
        # at (0.98, 0.70), the sample before (1.08, 0.40) came within reach.
        s = self.s_0MZM_e4_s9()
        s["track"] = s["track"] + [[3425.9, 0.98, 0.70], [3426.0, 1.08, 0.40]]
        f = self.figs(s)
        assert (f["curl"]["value"], f["curl"]["note"]) == ("3 ft 5 in", "from its line to where it hit a stone")


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
        assert "cursorFromHash(" in src and "export function App({ doc, config, cursor, reload })" in src

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
        for needle in ("lineFigures(shot, doc)", "stripGeometry(shot)", 'className = "dstrip"',
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


class TestShotClip:
    """A clip of the rock on screen, from the menu (core/shotclip.mjs)."""

    def name(self, d, ei=0, si=0):
        return run_js(f"out(shotClipName(buildGameView({json.dumps(d)}, 0, {{}}), {ei}, {si}));")

    def test_named_by_the_numbers_the_run_detected(self):
        assert self.name(doc([shot(1, "red", "lead")], end_number=4)) == "0.4.1"
        assert self.name(doc([shot(1, "red", "lead")], end_number=2, end_id=5)) == "0.5.1"
        assert self.name(doc([shot(3, "red", "lead", id=7)])) == "0.1.7"
        assert self.name(doc([shot(1, "red", "lead")], game_index=1)) == "1.1.1"

    def test_none_without_a_rock_or_for_one_never_seen(self):
        assert self.name(doc([])) is None
        assert self.name(doc([shot(1, "red", "lead", missing=True)])) is None

    def offered(self, d, days):
        return run_js(f"const d = {json.dumps(d)};"
                      f"out(shotClipsOffered(d, Date.parse(d.source.analysed_at)"
                      f" + {days} * 86400000));")

    def test_offered_for_three_weeks_after_the_game_was_analysed(self):
        d = doc([shot(1, "red", "lead")])
        d["source"]["analysed_at"] = "2026-10-12T21:07:24+00:00"
        assert self.offered(d, 0) is True and self.offered(d, 20.9) is True
        assert self.offered(d, 21.01) is False

    def test_while_the_game_is_live_too_but_not_without_a_date(self):
        """Every rock a live page shows is in an end already published, and
        the clips worker cuts it from the recording being made."""
        d = doc([shot(1, "red", "lead")])
        d["source"]["analysed_at"] = "2026-10-12T21:07:24+00:00"
        d["live"] = {"in_progress": True}
        d["games"][0]["in_progress"] = True
        assert self.offered(d, 0) is True
        d = doc([shot(1, "red", "lead")])
        d["source"].pop("analysed_at", None)
        assert run_js(f"const d = {json.dumps(d)}; out(shotClipsOffered(d, 0));") is False

    def test_what_the_dialog_says(self):
        got = run_js("out([{state: 'queued', ahead: 0}, {state: 'queued', ahead: 2},"
                     " {state: 'cutting'}, {state: 'ready'}, {state: 'gone'}, {state: 'expired'},"
                     " {state: 'failed', reason: 'no_release'}, {state: 'failed'}]"
                     ".map(shotClipStatus));")
        assert [g["busy"] for g in got] == [True, True, True] + [False] * 5
        assert got[1]["text"] == "Waiting for the clips worker: 2 ahead of this one…"
        assert got[2]["text"] == "Cutting the clip…"
        assert got[3]["text"] is None
        assert "no longer kept" in got[4]["text"] and got[4] == got[5]
        assert "never seen released" in got[6]["text"]

    def test_the_menu_offers_it_and_the_page_names_the_dialog(self):
        app = (Path(__file__).resolve().parents[1] / "frontend/viewer/App.jsx").read_text()
        assert 'id="clipBtn"' in app and "shotClipsOffered(doc, openedAt)" in app
        assert "<ShotClipDialog clipping={ui.clipping}" in app
        css = (VIEWER / "style.css").read_text()
        assert "header #clipBtn    { order: 15; }" in css, "the desktop header puts it first"


class TestHouseImage:
    """The house saved as a picture from the menu (core/houseimage.mjs)."""

    def got(self, d, ei=0, si=0, gi=0):
        return run_js(f"const v = buildGameView({json.dumps(d)}, {gi}, {{}});"
                      f"out([houseImageCaption(v, {ei}, {si}), houseImageName(v, {ei}, {si})]);")

    def test_captioned_with_the_rock_as_the_flag_dialog_names_it(self):
        caption, name = self.got(doc([shot(1, "red", "lead"), shot(2, "yellow", "lead")],
                                     end_number=4), si=1)
        assert caption == {"title": None, "place": "End 4 · Rock 2 (yellow, lead)"}
        assert name == "house-end4-rock2.png"

    def test_named_teams_head_the_caption(self):
        d = doc([shot(1, "red", "lead")])
        d["games"][0]["teams"] = {"red": {"name": "Dimmit"}, "yellow": {"name": "Grant"}}
        assert self.got(d)[0]["title"] == "Dimmit v Grant"
        d["games"][0]["teams"]["yellow"]["name"] = None
        assert self.got(d)[0]["title"] == "Dimmit v Yellow"

    def test_a_page_with_two_games_names_the_game(self):
        d = doc([shot(1, "red", "lead")])
        d["games"].append({**d["games"][0], "index": 1})
        caption, name = self.got(d, gi=1)
        assert caption["place"] == "Game 2 · End 1 · Rock 1 (red, lead)"
        assert name == "house-game2-end1-rock1.png"

    def test_an_end_with_no_rocks(self):
        assert self.got(doc([], end_number=3))[1] == "house-end3.png"

    def test_offered_behind_the_menu_only(self):
        app = (Path(__file__).resolve().parents[1] / "frontend/viewer/App.jsx").read_text()
        assert 'id="houseImgBtn"' in app
        css = (VIEWER / "style.css").read_text()
        assert "\n#houseImgBtn { display: none; }" in css, "not in the desktop header"
        phone = css[css.index("@media (max-width: 640px) and (min-height: 461px) {"):]
        assert "  #houseImgBtn { display: block; }" in phone[:phone.index("\n}\n")]
        assert "  header #houseImgBtn { display: block; }" in css

    def test_the_picture_leaves_out_the_selection_ring(self):
        house = (Path(__file__).resolve().parents[1] / "frontend/viewer/House.jsx").read_text()
        assert '<circle className="selring"' in house
        rt = (Path(__file__).resolve().parents[1] / "frontend/runtime/houseImage.mjs").read_text()
        assert 'querySelectorAll("title, .selring")' in rt


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
                                "rock_id": "1", "key": "0.4.1", "t_video_s": 802.5,
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
        get the same time the viewer seeks to: from the rest or the arrival
        back to an unseen release, then the pipeline's 5 s lead-in."""
        got = self.place(doc([shot(1, "red", "lead", t_rest_s=900.0)]))
        assert got["place"]["t_video_s"] == 900.0 - 8 - 15 - 5
        got = self.place(doc([shot(1, "red", "lead", t_enter_s=700.0)]))
        assert got["place"]["t_video_s"] == 700.0 - 15 - 5

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
        assert run_js("out([NOTE_MAX, VIDEO_LEAD_IN_S, RELEASE_TO_ARRIVAL_S]);") == [
            api.MAX_FLAG_NOTE, timeline.VIDEO_LEAD_IN_S, timeline.RELEASE_TO_ARRIVAL_S]

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
        """Who is signed in is decided by settledUser, never whenReady, which
        settles before the session is restored. accountsOn may use whenReady:
        it only asks whether accounts exist at all."""
        src = (Path(__file__).resolve().parents[1] / "frontend/runtime/auth.mjs").read_text()
        assert "settledUser(auth.onUser)" in src
        who = (src[src.index("async function signedIn"):src.index("export async function accountsOn")]
               + src[src.index("export async function whoIsSignedIn"):])
        assert "whenReady" not in who


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
        # Report is built once, above the header's markup: the portrait iPad
        # puts it before ⋯, and everywhere else the menu renders it here.
        assert 'const reportBtn = (\n    <button id="reportBtn"' in app
        assert menu.index("{tablet ? null : <>{thinkBtn}{reportBtn}</>}") < menu.index('id="flagBtn"')
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
        """Any dialog: the flag's, I played…, a score. Enter on a team button
        in I played… once also marked the rock behind it charted."""
        app = self.src("frontend/viewer/App.jsx")
        on_key = app[app.index("const onKey = ev =>"):]
        assert on_key.index('document.querySelector("dialog[open]")') < on_key.index("switch (ev.key)")

    def test_send_asks_who_again_with_a_fresh_token(self):
        """A slow first answer must cost the display, never the attribution,
        and a dialog left open past the token's hour must not send a dead one."""
        flag = self.src("frontend/viewer/Flag.jsx")
        assert "const w = await whoIsFlagging();" in flag
        assert "who === undefined ? await" not in flag
        assert "checked again when you send" in flag
        run = self.src("frontend/runtime/auth.mjs")
        assert "settled ??= settledUser(auth.onUser)" in run
        assert "u.getIdToken()" in run[run.index("export async function whoIsSignedIn"):]

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
        assert 'import("../site/auth.js")' in self.src("frontend/runtime/auth.mjs")


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


def switched_doc():
    """A doubles end red switched, as api.game_doc serves it from the game's
    switched ends (timeline.apply_switched_ends)."""
    from curling_score import timeline
    return timeline.apply_switched_ends(doubles_doc(doubles_shots()), {"red": {"ends": [3]}})


class TestSwitchedEndsParity:
    """A switch held on the document reads the same in the viewer as in
    apply_overrides, and a chart's own swap only adds to it."""

    def test_as_served(self):
        py, js = both(switched_doc(), {})
        assert js == py and js[0][4] == "B"

    def test_with_a_move(self):
        from curling_score import timeline
        shots = doubles_shots(n=6, end_number=4)
        for s in shots[4:]:
            s["color_inferred"] = s["missing"] = True
            s["state_known"] = False
        d = timeline.apply_switched_ends(doubles_doc(shots, end_number=4), {"red": {"ends": [4]}})
        py, js = both(d, {"0.4.5": {"before": 1}, "0.4.6": {"before": 1}})
        assert js == py and [r[4] for r in js] == ["B", "A", "A", "B", "A", "B"]

    def test_a_malformed_document_switch_swaps_nothing(self):
        d = doubles_doc(doubles_shots())
        d["games"][0]["ends"][0]["roles_swapped"] = {"red": 1}
        got = run_js(setup(d) + "out(buildGameView(state.doc, 0, state.overrides).ends[0].swapped);")
        assert got == {}

    def test_an_override_adds_to_a_document_switch(self):
        got = run_js(setup(switched_doc(), {"0.3": {"roles_swapped": {"yellow": True}}}) +
                     "out(buildGameView(state.doc, 0, state.overrides).ends[0].swapped);")
        assert got == {"red": True, "yellow": True}

    def test_the_players_slice_matches_the_view(self):
        TestPlayerSliceParity()._check(switched_doc(), {}, "red", 2)


def short_doc(colours=("red",), end_number=2, credited=True):
    """A fours end with the format block api.game_doc writes for a team of
    three -- its rocks credited 3/3/2 as game_doc credits them, or, with
    `credited` off, still as the pipeline wrote them."""
    from curling_score import timeline
    from curling_score.game import format as F
    d = doc(_sliced_shots(16, "fours", end_number), end_number=end_number)
    d["format"] = {**F.FOURS.to_json(), "short_handed": list(colours)}
    return timeline.assign_short_handed(d) if credited else d


THREE_ROWS = [("lead", 1), ("lead", 2), ("lead", 3), ("second", 1),
              ("second", 2), ("second", 3), ("skip", 1), ("skip", 2)]


class TestShortHandedFormat:
    def test_short_of_reads_the_block(self):
        from curling_score.game import format as F
        dbl = {**F.DOUBLES.to_json(), "short_handed": ["red"]}
        got = run_js(setup(short_doc()) +
                     "const f = formatOf(state.doc);"
                     "out([[...shortOf(f)], [...shortOf(formatOf({}))],"
                     " [...shortOf({...f, short_handed: ['red', 'blue', 3]})],"
                     " [...shortOf({...f, short_throw_table: 'x'})],"
                     f" [...shortOf({json.dumps(dbl)})]]);")
        assert got == [["red"], [], ["red"], [], []]

    def test_the_throw_table_for_a_team_of_three(self):
        got = run_js("const F4 = formatOf({});"
                     "out([5, 7, 13].map(n => throwInfo(n, F4, false, true))"
                     ".concat([shotLabel(2, 5, F4, false, true), roleText(F4, 1, true),"
                     " roleText(F4, 2, true), roleText(F4, 3, true), roleText(F4, 4, true),"
                     " roleText(F4, 1)]));")
        assert got[:3] == [{"has_hammer": False, "thrower_slot": 1, "rock_of_player": 3},
                           {"has_hammer": False, "thrower_slot": 2, "rock_of_player": 1},
                           {"has_hammer": False, "thrower_slot": 4, "rock_of_player": 1}]
        assert got[3:] == ["2nd end, lead's third rock", "1st–3rd", "4th–6th", "",
                           "7th & 8th", "1st & 2nd"]

    def test_the_report_heads_say_three_players(self):
        got = run_js(setup(short_doc()) +
                     "out(teamHeads({red: 'Dimmit', yellow: 'Grant'}, formatOf(state.doc)));")
        assert got == {"red": "Dimmit · 3 players", "yellow": "Grant"}


class TestShortHandedParity:
    """The viewer credits a team of three exactly as apply_overrides does."""

    def test_as_served(self):
        py, js = both(short_doc(), {})
        assert js == py
        assert [(r[4], r[5]) for r in js if r[2] == "red"] == THREE_ROWS

    def test_a_block_whose_rocks_are_not_yet_credited(self):
        py, js = both(short_doc(credited=False), {})
        assert js == py
        assert [(r[4], r[5]) for r in js if r[2] == "red"] == THREE_ROWS

    def test_a_moved_rock(self):
        py, js = both(short_doc(), {"0.2.6": {"before": 5}})
        assert js == py
        assert (js[5][0], js[5][1], js[5][4], js[5][5]) == (5, 6, "lead", 3)

    def test_a_rock_recoloured_onto_the_short_team(self):
        py, js = both(short_doc(), {"0.2.6": {"color": "red"}})
        assert js == py
        assert (js[5][2], js[5][4], js[5][5]) == ("red", "lead", 3)

    def test_the_players_slice_matches_the_view(self):
        TestPlayerSliceParity()._check(short_doc(), {}, "red", 1)


def _sliced_shots(n, fmt_name, end_number):
    """An end whose every rock has a house and a set position, so the slice
    has something to lose: `stones` for stones_before, `line.start` for the
    hack call."""
    shots = doubles_shots(n, end_number) if fmt_name == "doubles" else [
        shot(i, "red" if i % 2 else "yellow", ["lead", "second", "third", "skip"][(i - 1) // 4],
             thrower_slot=(i - 1) // 4 + 1, rock_of_player=1 + ((i - 1) // 2) % 2,
             color_inferred=False)
        for i in range(1, n + 1)]
    for s in shots:
        s["stones"] = [{"color": s["color"], "x": round(0.05 * s["number"], 2), "y": 0.3}]
        s["line"] = {"start": {"x": -0.14 if s["number"] % 3 else 0.12, "y": 38.0}, "curl": "left"}
    return shots


class TestPlayerSliceParity:
    """The report lays out a game the server already graded and cut down to
    one player, with no overrides; the viewer lays out the raw game with
    them. Every rock must come out numbered and attributed the same, and the
    player's own rocks with the same house and hack."""

    def _view(self, document, overrides):
        return run_js(
            f"const v = buildGameView({json.dumps(document)}, 0, {json.dumps(overrides)});"
            "out(v.ends[0].shots.map(s => ({id: s.id ?? null, number: s.number, color: s.color,"
            " slot: s.thrower_slot ?? null, position: s.position ?? null,"
            " house: s.stones_before ?? null, hack: s.hack ?? null})));")

    def _check(self, document, overrides, color, slot):
        import copy
        from curling_score import timeline
        raw = self._view(document, overrides)
        baked = timeline.for_player(
            timeline.apply_overrides(copy.deepcopy(document), overrides), color, slot)
        cut = self._view(baked, {})
        who = [{k: r[k] for k in ("id", "number", "color", "slot", "position")} for r in raw]
        assert [{k: r[k] for k in who[0]} for r in cut] == who
        mine = [i for i, r in enumerate(raw) if r["color"] == color and r["slot"] == slot]
        assert mine, "the case must give the player some rocks"
        assert [cut[i]["house"] for i in mine] == [raw[i]["house"] for i in mine]
        assert [cut[i]["hack"] for i in mine] == [raw[i]["hack"] for i in mine]
        return raw

    def test_a_rock_moved_in_fours(self):
        d = doc(_sliced_shots(16, "fours", 2), end_number=2)
        raw = self._check(d, {"0.2.16": {"before": 14}}, "yellow", 4)
        assert [r["id"] for r in raw][-3:] == [16, 14, 15]

    def test_a_blank_coloured_by_hand_and_moved(self):
        shots = _sliced_shots(6, "fours", 4)
        for s in shots[4:]:
            s.update(color_inferred=True, missing=True, state_known=False)
        self._check(doc(shots, end_number=4),
                    {"0.4.5": {"before": 2}, "0.4.6": {"before": 2},
                     "0.4.3": {"color": "yellow"}}, "yellow", 1)

    def test_a_doubles_swap(self):
        raw = self._check(doubles_doc(_sliced_shots(10, "doubles", 3)),
                          {"0.3": {"roles_swapped": {"red": True}}}, "red", 2)
        assert [r["number"] for r in raw if r["color"] == "red" and r["slot"] == 2] == [1, 9]

    def test_a_doubles_swap_with_a_move(self):
        shots = _sliced_shots(6, "doubles", 4)
        for s in shots[4:]:
            s.update(color_inferred=True, missing=True, state_known=False)
        self._check(doubles_doc(shots, end_number=4),
                    {"0.4.5": {"before": 1}, "0.4.6": {"before": 1},
                     "0.4": {"roles_swapped": {"red": True}}}, "red", 2)


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
        # One element, placed by each layout: under the transport below
        # 1500 px, at the top of the figures card from there (spec 2026-10-15).
        assert "const flags = <Flags doc={doc} " in app
        assert main.count("{flags}") == 2

    def test_a_phone_peek_hides_the_end_box(self):
        """The peek's bottom 84px belong to the fixed transport bar. The End
        box rendered into them, and a tap beside a transport button landed on
        a swap label and saved a swap."""
        css = (VIEWER / "style.css").read_text()
        start = css.index("@media (max-width: 640px) and (min-height: 461px)")
        phone = css[start:css.index("@media (max-width: 640px) and (max-height: 460px)")]
        at = phone.index('body[data-sheet="peek"] #chart #endBox')
        assert "display: none" in phone[at:phone.index("}", at)]

    def test_the_phone_transport_has_an_opaque_band(self):
        """The bar floats over a sheet that scrolls, and whatever was scrolled
        under its gaps took the tap -- in the open doubles sheet, a swap
        checkbox. Its band is painted in the sheet's colour, under the buttons
        (z-index -1 inside .transport) and over the sheet."""
        css = (VIEWER / "style.css").read_text()
        start = css.index("@media (max-width: 640px) and (min-height: 461px)")
        short = css.index("@media (max-width: 640px) and (max-height: 460px)")
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


def delivery_points(gap=None, corner=0.0, t_end=4.7, fps=10):
    """A rock at rest at (-0.15, 3.2 m behind the tee) until 0.5 s before the
    release, then 2 m/s up the sheet drifting right: [t, y, x] every 1/fps s,
    t to the hundredth as the timeline writes it."""
    out = []
    for i in range(int(round((t_end + 3.0) * fps)) + 1):
        t = round(-3.0 + i / fps, 2)
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

# Four real rocks, 30 fps paths: a hit, a hogged rock, a full-weight hit, and a
# guard whose stone was nudged forward before the delivery (tl-s_0cYt.json).
ROCKS = json.loads((Path(__file__).resolve().parent / "fixtures/delivery/s_0cYt_rocks.json")
                   .read_text())["rocks"]


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

    def test_across_is_ticked_every_six_inches(self):
        g = self.geom(delivery_shot())
        assert [t["label"] for t in g["ticks"]] == ["30 in", "24 in", "18 in", "12 in", "6 in", "0",
                                                    "6 in", "12 in", "18 in", "24 in", "30 in"]
        xs = [t["x1"] for t in g["ticks"]]
        # 316 px for 1.6 m: 6 in is 30.1 px.
        assert all(b - a == pytest.approx(0.1524 * 316 / 1.6, abs=0.15) for a, b in zip(xs, xs[1:]))
        side = self.geom(delivery_shot(), "DESKDELIVERYBOX")
        assert "0" in [t["label"] for t in side["ticks"]] and all(t["label"] for t in side["ticks"])

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

    def test_every_sample_is_a_dot(self):
        g = self.geom(delivery_shot())
        assert g["points"] == len(g["dots"]) == len(delivery_points())

    def test_after_the_release_is_the_strip_s_gold(self):
        detail = (Path(__file__).resolve().parents[1] / "frontend/viewer/Detail.jsx").read_text()
        assert run_js("out(DELIVERY_COLORS);") == {
            "before": "#2d1e6b", "after": "#a07a00", "unknown": "#1f7a8c"}
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

    def test_a_tenth_of_a_second_path_keeps_its_dots_and_its_words(self):
        g = self.geom(delivery_shot(delivery_points(fps=10)))
        assert g["every"] == {"short": "0.1 s", "words": "tenth of a second"}
        assert g["dotR"] == 2.3

    def test_dropped_samples_do_not_change_the_rate(self):
        """Samples go missing where the side model loses the rock; the rate is
        still the one the path was read at."""
        for fps, every, r in ((10, "0.1 s", 2.3), (30, "frame (30 a second)", 1.3)):
            pts = [p for i, p in enumerate(delivery_points(fps=fps)) if i % 6 != 3]
            g = self.geom(delivery_shot(pts))
            assert (g["every"]["short"], g["dotR"]) == (every, r), fps

    def test_a_brief_loss_at_30_a_second_is_still_one_line(self):
        """A fifth of a second, 0.4 m: bridged at 10 a second, so at 30 too."""
        assert len(self.geom(delivery_shot(delivery_points(fps=30, gap=(1.0, 1.2))))["runs"]) == 1
        assert len(self.geom(delivery_shot(delivery_points(fps=10, gap=(1.0, 1.2))))["runs"]) == 1
        assert len(self.geom(delivery_shot(delivery_points(fps=30, gap=(0.5, 1.6))))["runs"]) == 2

    def test_every_frame_is_said_so_and_drawn_smaller(self):
        # Times to the hundredth: steps of 0.03 and 0.04 by turns.
        g = self.geom(delivery_shot(delivery_points(fps=30)))
        assert g["every"] == {"short": "frame (30 a second)", "words": "frame, thirty a second"}
        assert g["dotR"] == 1.3
        assert g["points"] > 2.5 * self.geom(delivery_shot())["points"]


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
        assert ("From above, thrower at the bottom · across ×{g.stretch} · a dot every {g.every.short}, "
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
        i_chart = body.index("<DeliveryChart g={g} label={deliveryLabel(shot, true, g)} fluid />")
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

    def test_a_speed_chart_and_a_nested_chart_carry_no_ids(self):
        src = self.SRC.read_text()
        assert "export function SpeedChart(" in src
        code = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
        assert "<defs" not in code and " id=" not in code
        # Nesting is spread last and only when asked, so every existing chart's
        # markup is unchanged.
        assert "{...(at ? { x: at.x, y: at.y } : {})}" in src

    def test_the_paint_is_under_everything_and_clipped_to_the_plot(self):
        src = self.SRC.read_text()
        body = src[src.index("export function DeliveryChart("):src.index("export const deliveryLabel")]
        i_ice = body.index("<rect x={0} y={0} width={g.w} height={g.h} fill={PAINT.ice} />")
        i_paint = body.index("{g.paint ? (")
        i_lines = body.index("{g.lines.map(")
        assert i_ice < i_paint < i_lines
        assert "viewBox={`${g.plot.x} ${g.plot.y} ${g.plot.w} ${g.plot.h}`}" in body


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


class TestDeliverySpeed:
    """Speed along the delivery: a straight-line fit over ±0.5 s at each
    sample, because along the ice the camera is ~10x noisier than across."""

    def speed(self, shot):
        return run_js(f"out(deliverySpeed({json.dumps(shot)}));")

    @pytest.mark.parametrize("key,peak,peak_yp,hog", [
        ("e2r4", 2.378, -1.39, 1.854),
        ("e2r2", 2.562, -2.71, 1.723),
        ("e1r4", 2.958, 0.47, 2.673),
        ("e2r9", 2.243, -1.65, 1.844),
    ])
    def test_real_rocks(self, key, peak, peak_yp, hog):
        s = self.speed(ROCKS[key])
        assert s["peak"]["v"] == pytest.approx(peak, abs=0.005)
        assert s["peak"]["yp"] == pytest.approx(peak_yp, abs=0.01)
        assert s["hog"]["v"] == pytest.approx(hog, abs=0.005)

    def test_a_stone_nudged_before_the_delivery_is_not_the_start(self):
        s = self.speed(ROCKS["e2r9"])
        assert s["series"][0]["t"] == pytest.approx(-0.5, abs=0.001)
        assert all(q["t"] >= -0.5 for q in s["series"])

    def test_a_steady_rock_reads_its_speed(self):
        for fps in (10, 30):
            s = self.speed(delivery_shot(delivery_points(fps=fps)))
            assert s["peak"]["v"] == pytest.approx(2.0, abs=0.01), fps
            assert s["hog"]["v"] == pytest.approx(2.0, abs=0.01), fps

    def test_a_path_that_stops_short_of_the_hog_has_no_hog_speed(self):
        s = self.speed(delivery_shot(delivery_points(t_end=2.0)))
        assert s["hog"] is None
        assert s["peak"]["v"] == pytest.approx(2.0, abs=0.01)

    def test_too_little_to_read_is_nothing(self):
        assert self.speed(delivery_shot(points=[[0.0, 30.0, 0.1], [0.1, 29.8, 0.1]])) is None
        assert self.speed(delivery_shot(points=[])) is None
        assert run_js("out(deliverySpeed(null));") is None


class TestSpeedNumbers:
    def test_the_three_numbers(self):
        n = run_js(f"out(speedNumbers(deliverySpeed({json.dumps(ROCKS['e2r4'])})));")
        assert n["peak"] == "2.4 m/s"
        assert n["peakWhere"] == "at its fastest, 2.3 m out of the hack"
        assert n["hog"] == "1.9 m/s"
        assert n["change"] == {"value": "0.5 m/s", "words": "lost between them"}

    def test_the_loss_is_the_two_figures_shown_subtracted(self):
        # Unrounded, 2.56 - 1.72 read 0.8 under 2.6 and 1.7.
        n = run_js(f"out(speedNumbers(deliverySpeed({json.dumps(ROCKS['e2r2'])})));")
        assert (n["peak"], n["hog"]) == ("2.6 m/s", "1.7 m/s")
        assert n["change"] == {"value": "0.9 m/s", "words": "lost between them"}

    @pytest.mark.parametrize("hog,change", [
        (1.96, {"value": None, "words": "held its speed to the hog line"}),   # 2.0 and 2.0
        (1.95, {"value": "0.1 m/s", "words": "lost between them"}),           # 2.0 and 1.9
    ])
    def test_the_boundary_is_the_rounded_figures(self, hog, change):
        n = run_js("out(speedNumbers({series: [], peak: {t: 0, yp: 1, v: 2.0}, "
                   f"hog: {{v: {hog}}}}}));")
        assert n["change"] == change

    def test_a_rock_that_kept_its_speed_never_shows_a_negative_loss(self):
        for hog in (2.0, 2.03, 2.4):
            n = run_js("out(speedNumbers({series: [], peak: {t: 0, yp: 1, v: 2.0}, "
                       f"hog: {{v: {hog}}}}}));")
            assert n["change"] == {"value": None, "words": "held its speed to the hog line"}, hog

    def test_no_hog_speed_is_no_hog_number(self):
        n = run_js("out(speedNumbers({series: [], peak: {t: 0, yp: -1.4, v: 2.38}, hog: null}));")
        assert n["hog"] is None and n["change"] is None
        assert n["peakWhere"] == "at its fastest, 2.3 m out of the hack"

    def test_nothing_is_nothing(self):
        assert run_js("out(speedNumbers(null));") is None


class TestTheBigDeliveryAndItsSpeed:
    def geom(self, shot, box):
        return run_js(f"out(deliveryGeometry({json.dumps(shot)}, {box}));")

    def speed_geom(self, shot):
        s = json.dumps(shot)
        return run_js(f"const s = {s}; out(speedGeometry(deliverySpeed(s), SPEEDBOX, s));")

    def test_the_window_is_returned_in_metres(self):
        g = self.geom(delivery_shot(), "DELIVERYBOX")
        assert g["win"]["span"] == pytest.approx(1.6)
        zero = next(t for t in g["ticks"] if t["label"] == "0")
        k = g["plot"]["w"] / g["win"]["span"]
        assert zero["x1"] == pytest.approx(g["plot"]["x"] + (0 - g["win"]["x0"]) * k, abs=0.1)

    def test_minor_ticks_only_where_a_box_asks(self):
        small = self.geom(delivery_shot(), "DELIVERYBOX")
        big = self.geom(delivery_shot(), "BIGDELIVERYBOX")
        assert small["minor"] == [] and len(big["minor"]) > 0
        figured = {t["y1"] for t in big["ticks"]}
        assert not figured & {m["y1"] for m in big["minor"]}
        ys = sorted({m["y1"] for m in big["minor"]} | figured)
        k = big["plot"]["h"] / big["win"]["span"]
        assert all(b - a == pytest.approx(2 * 0.0254 * k, abs=0.15) for a, b in zip(ys, ys[1:]))

    def test_the_big_box_is_the_spec_s(self):
        assert run_js("out(BIGDELIVERYBOX);") == {
            "w": 1000, "h": 250, "side": True, "from": -4.0, "to": 8.05, "across": 0.9,
            "minorInches": 2, "paint": True, "pad": {"l": 10, "r": 52, "t": 18, "b": 8}}
        assert run_js("out(SPEEDBOX);") == {
            "w": 1000, "h": 170, "from": -4.0, "to": 8.05, "pad": {"l": 10, "r": 52, "t": 16, "b": 22}}

    def test_the_speed_and_the_delivery_share_their_x(self):
        g = self.geom(ROCKS["e2r4"], "BIGDELIVERYBOX")
        sg = self.speed_geom(ROCKS["e2r4"])
        gx = {l["kind"]: l["x1"] for l in g["lines"] if l["kind"] != "centre"}
        sx = {l["kind"]: l["x"] for l in sg["lines"]}
        for kind in ("hack", "tee", "hog"):
            assert sx[kind] == pytest.approx(gx[kind], abs=0.1), kind

    def test_the_speed_chart_marks_the_peak_above_the_hog(self):
        sg = self.speed_geom(ROCKS["e2r4"])
        assert sg["peak"]["y"] < sg["hog"]["y"]            # faster is higher
        assert sg["top"] >= 3
        assert [q["label"] for q in sg["grid"][:3]] == ["0", "", "1 m/s"]
        assert sg["hog"]["x"] == pytest.approx(next(l["x"] for l in sg["lines"] if l["kind"] == "hog"))

    def test_a_gap_in_the_path_breaks_the_speed_line(self):
        whole = self.speed_geom(delivery_shot(delivery_points(fps=30)))
        gapped = self.speed_geom(delivery_shot(delivery_points(fps=30, gap=(0.5, 1.6))))
        widest = lambda sg: max(s["x2"] - s["x1"] for s in sg["segs"])   # noqa: E731
        assert widest(whole) < 10
        assert widest(gapped) < 10       # 2.2 m of ice is ~170 px: no segment bridges it
        assert len(gapped["segs"]) < len(whole["segs"])

    def test_nothing_to_draw(self):
        assert run_js("out(speedGeometry(null, SPEEDBOX, null));") is None


NAVY, GOLD_DOT, TEAL = "#2d1e6b", "#a07a00", "#1f7a8c"


def on_path(t):
    """Where delivery_points' rock is `t` seconds from the release: (yp, x)."""
    moving = max(0.0, t + 0.5)
    return -3.2 + 2.0 * moving, -0.15 + 0.04 * moving


def released(t, shot=None, *, yp=None, x=None):
    """`shot` (delivery_shot() by default) with line.release [t, y, x], rounded
    as the timeline writes it: by default the rock's own point on
    delivery_points' path at `t`."""
    s = json.loads(json.dumps(shot or delivery_shot()))
    p_yp, p_x = on_path(t)
    s["line"]["release"] = [t, round(TEE_Y - (p_yp if yp is None else yp), 2),
                            round(p_x if x is None else x, 3)]
    return s


class TestTheRelease:
    """Where the hand let go (line.release): a marker on the path, the dots one
    colour before it and another after, and the speed line split with them."""

    def geom(self, shot, box="DELIVERYBOX"):
        return run_js(f"out(deliveryGeometry({json.dumps(shot)}, {box}));")

    def speed_geom(self, shot, box="SPEEDBOX"):
        s = json.dumps(shot)
        return run_js(f"const s = {s}; out(speedGeometry(deliverySpeed(s), {box}, s));")

    @staticmethod
    def inside(g, x, y):
        p = g["plot"]
        return p["x"] <= x <= p["x"] + p["w"] and p["y"] <= y <= p["y"] + p["h"]

    def test_dots_split_at_the_release(self):
        ts = [p[0] for p in delivery_points()]
        for t_rel in (0.05, 0.5):           # between two samples, and on one
            g = self.geom(released(t_rel))
            assert [d["fill"] for d in g["dots"]] == [NAVY if t < t_rel else GOLD_DOT for t in ts], t_rel
        assert g["legend"] == [{"color": NAVY, "text": "before release"},
                               {"color": GOLD_DOT, "text": "after release"}]

    def test_the_colour_rule_is_one_function(self):
        got = run_js("const r = {t: 0.2, yp: 0, x: 0};"
                     "out([dotColor(0.1, r), dotColor(0.2, r), dotColor(0.3, r), dotColor(0.1, null)]);")
        assert got == [NAVY, GOLD_DOT, GOLD_DOT, TEAL]

    def test_a_timeline_without_a_release_draws_every_dot_teal_with_no_marker_or_legend(self):
        for box in ("DELIVERYBOX", "BIGDELIVERYBOX"):
            g = self.geom(delivery_shot(), box)
            assert g["release"] is None and g["legend"] == []
            assert {d["fill"] for d in g["dots"]} == {TEAL}
        sg = self.speed_geom(ROCKS["e2r4"])
        assert sg["release"] is None and {s["color"] for s in sg["segs"]} == {TEAL}

    def test_the_marker_sits_on_the_path_at_the_release_in_both_orientations(self):
        i = [p[0] for p in delivery_points()].index(0.5)
        for box, side in (("DELIVERYBOX", False), ("DESKDELIVERYBOX", True),
                          ("BIGDELIVERYBOX", True), ("TABLETDELIVERYBOX", True)):
            g = self.geom(released(0.5), box)
            r, dot = g["release"], g["dots"][i]
            assert (r["x"], r["y"]) == (dot["x"], dot["y"]), box
            tk = r["tick"]
            if side:      # the path runs left to right: the tick is upright
                assert tk["x1"] == tk["x2"] == r["x"] and tk["y1"] < r["y"] < tk["y2"], box
                half = (tk["y2"] - tk["y1"]) / 2
                assert r["y"] - tk["y1"] == pytest.approx(tk["y2"] - r["y"], abs=0.11)
            else:         # the path runs up the page: the tick lies flat
                assert tk["y1"] == tk["y2"] == r["y"] and tk["x1"] < r["x"] < tk["x2"], box
                half = (tk["x2"] - tk["x1"]) / 2
                assert r["x"] - tk["x1"] == pytest.approx(tk["x2"] - r["x"], abs=0.11)
            assert 3 <= half <= 8, box
            assert r["label"]["text"] == "Release"
            assert self.inside(g, r["label"]["x"], r["label"]["y"]), box
        # Between two samples it is between their dots.
        g = self.geom(released(0.05))
        a, b = g["dots"][i - 5], g["dots"][i - 4]           # t 0.0 and 0.1
        assert b["y"] < g["release"]["y"] < a["y"]
        assert min(a["x"], b["x"]) - 0.1 <= g["release"]["x"] <= max(a["x"], b["x"]) + 0.1

    W_WORD = 32          # "Release" at the chart's 8.5-9 px type, about

    def test_upright_the_label_is_beside_the_tick_and_never_off_the_plot(self):
        # The aim line runs right of x -0.5: the word goes left, away from it,
        # unless the plot's edge is too near; at x 0.75 the right edge is.
        for x, anchor in ((-0.5, "end"), (-0.75, "start"), (0.75, "end")):
            g = self.geom(released(0.05, x=x))
            r, lab = g["release"], g["release"]["label"]
            p = g["plot"]
            assert lab["anchor"] == anchor, x
            assert abs(lab["y"] - r["y"]) <= 4, x
            if anchor == "end":
                assert lab["x"] < r["tick"]["x1"] and lab["x"] - self.W_WORD >= p["x"], x
            else:
                assert lab["x"] > r["tick"]["x2"] and lab["x"] + self.W_WORD <= p["x"] + p["w"], x

    def test_on_its_side_the_label_is_above_or_below_the_tick_and_never_off_the_plot(self):
        # The thrower's right is down the page. At x 0.4 the aim line is above,
        # but the bottom edge is too near to go below; at -0.4 the top is.
        for x, above in ((0.4, True), (-0.4, False), (0.2, False)):
            g = self.geom(released(0.05, x=x), "DESKDELIVERYBOX")
            r, lab = g["release"], g["release"]["label"]
            p = g["plot"]
            assert lab["anchor"] == "middle", x
            assert abs(lab["x"] - r["x"]) < 0.1, x
            if above:
                assert lab["y"] < r["tick"]["y1"] and lab["y"] - 8 >= p["y"], x
            else:
                assert lab["y"] > r["tick"]["y2"] and lab["y"] <= p["y"] + p["h"], x

    def test_a_release_outside_the_box_draws_no_marker(self):
        for shot in (released(0.05, yp=8.5), released(0.05, x=1.2), released(0.05, x=-0.9)):
            g = self.geom(shot)
            assert g["release"] is None
            assert g["legend"] and {d["fill"] for d in g["dots"]} <= {NAVY, GOLD_DOT}
        assert self.speed_geom(released(0.05, yp=8.5))["release"] is None

    def test_speed_segments_and_dots_change_colour_at_the_same_moment(self):
        for fps in (10, 30):
            shot = released(0.05, delivery_shot(delivery_points(fps=fps)))
            sg = self.speed_geom(shot)
            g = self.geom(shot, "BIGDELIVERYBOX")
            at = sg["release"]["x"]
            assert at == pytest.approx(g["release"]["x"], abs=0.1)
            navy = [s for s in sg["segs"] if s["color"] == NAVY]
            gold = [s for s in sg["segs"] if s["color"] == GOLD_DOT]
            assert navy and gold and len(navy) + len(gold) == len(sg["segs"])
            # The line turns at the release, as the dots do.
            assert max(s["x2"] for s in navy) == pytest.approx(at, abs=0.2) == min(s["x1"] for s in gold)
            assert all(d["x"] < at for d in g["dots"] if d["fill"] == NAVY)
            assert all(d["x"] > at for d in g["dots"] if d["fill"] == GOLD_DOT)

    def test_the_speed_chart_has_a_release_line_at_the_dots_distance(self):
        shot = released(0.5)
        for dbox, sbox in (("BIGDELIVERYBOX", "SPEEDBOX"), ("TABLETDELIVERYBOX", "TABLETSPEEDBOX")):
            g, sg = self.geom(shot, dbox), self.speed_geom(shot, sbox)
            rl = sg["release"]
            assert rl["x"] == pytest.approx(g["release"]["x"], abs=0.1), dbox
            assert (rl["y1"], rl["y2"]) == (sg["plot"]["y"], sg["plot"]["y"] + sg["plot"]["h"])

    def test_aim_frame_and_overlay_carry_the_release(self):
        d8 = {"schema_version": 8}
        rocks = []
        for off in (0.04, -0.02, 0.01):
            s = aimed(13, "red", 4, off=off)
            t, y, x = s["line"]["delivery"][3]                   # yp 0, at the throwing tee
            s["line"]["release"] = [t, y, x]
            rocks.append(s)
        rocks.append(aimed(13, "red", 4, off=0.0))             # no release read
        frames = run_js(f"out({json.dumps(rocks)}.map(s => aimFrame(s, {json.dumps(d8)}).release));")
        assert [f and (round(f["yp"], 3), round(f["dx"], 3)) for f in frames] == [
            (0.0, 0.04), (0.0, -0.02), (0.0, 0.01), None]
        g = run_js(f"const d = {json.dumps(d8)};"
                   f"const paths = {json.dumps(rocks)}.map((s, i) => ({{key: 'k' + i, ...aimFrame(s, d)}}));"
                   "out(deliveryOverlay(paths, 'right'));")
        for rock in g["rocks"][:3]:
            fourth = [float(v) for v in rock["d"].split(" ")[3].split(",")]
            assert [rock["release"]["x"], rock["release"]["y"]] == fourth
        assert g["rocks"][3]["release"] is None
        # A release past the window is not drawn.
        far = json.loads(json.dumps(rocks[0]))
        far["line"]["release"][1] = round(TEE_Y - 9.0, 4)
        g = run_js(f"const d = {json.dumps(d8)};"
                   f"out(deliveryOverlay([{{key: 'k', ...aimFrame({json.dumps(far)}, d)}}], 'right'));")
        assert g["rocks"][0]["release"] is None

    @pytest.mark.parametrize("bad", ["[1, null, 2]", '"x"', "[]", "[0.1, 36.8]", "[NaN, 36.8, 0]",
                                     "[0.1, Infinity, 0]", '["0.1", 36.8, 0]', "{t: 0.1}", "null"])
    def test_a_malformed_release_is_ignored(self, bad):
        got = run_js(f"const s = {json.dumps(delivery_shot())}; s.line.release = {bad};"
                     "const g = deliveryGeometry(s, DELIVERYBOX);"
                     "const sg = speedGeometry(deliverySpeed(s), SPEEDBOX, s);"
                     "out([deliveryRelease(s), g.release, g.legend, [...new Set(g.dots.map(d => d.fill))],"
                     " sg.release, [...new Set(sg.segs.map(q => q.color))],"
                     " aimFrame(s, {schema_version: 8}).release]);")
        assert got == [None, None, [], [TEAL], None, [TEAL], None]

    def test_the_release_is_read_in_the_delivery_s_frame(self):
        s = released(0.05)
        t, y, x = s["line"]["release"]
        got = run_js(f"out(deliveryRelease({json.dumps(s)}));")
        assert got["t"] == t and got["x"] == x and got["yp"] == pytest.approx(TEE_Y - y)
        assert run_js("out([deliveryRelease(null), deliveryRelease({}), deliveryRelease({line: {}})]);") == [
            None, None, None]


class TestThePaintUnderTheDelivery:
    """The near house and the club's 4 ft stripe, faint under the close up, so
    a slide can be read against the sheet. The stripe is painted in front of
    the house only -- from where it meets the 12 ft out toward the hog line."""
    PAINTED = "{...DELIVERYBOX, paint: true}"

    def geom(self, box):
        return run_js(f"out(deliveryGeometry({json.dumps(delivery_shot())}, {box}));")

    def test_only_where_a_box_asks(self):
        assert self.geom("DELIVERYBOX")["paint"] is None
        assert self.geom("DESKDELIVERYBOX")["paint"] is None
        assert self.geom(self.PAINTED)["paint"] is not None

    def test_the_house_sits_on_the_throwing_tee_at_the_chart_s_stretch(self):
        g = self.geom(self.PAINTED)
        tee = next(l for l in g["lines"] if l["kind"] == "tee")
        house = {r["kind"]: r for r in g["paint"]["house"]}
        assert list(house) == ["twelve", "eight", "four", "button"]
        kx = g["plot"]["w"] / g["win"]["span"]
        ky = g["plot"]["h"] / 12.05
        zero = next(t for t in g["ticks"] if t["label"] == "0")
        for r in house.values():
            assert (r["cx"], r["cy"]) == (pytest.approx(zero["x1"], abs=0.1), pytest.approx(tee["y1"], abs=0.1))
        assert house["twelve"]["rx"] == pytest.approx(1.829 * kx, abs=0.1)
        assert house["twelve"]["ry"] == pytest.approx(1.829 * ky, abs=0.1)

    def test_the_stripe_is_in_front_of_the_house_only(self):
        g = self.geom(self.PAINTED)
        s = g["paint"]["stripe"]
        kx = g["plot"]["w"] / g["win"]["span"]
        ky = g["plot"]["h"] / 12.05
        meet = (1.829 ** 2 - 0.6096 ** 2) ** 0.5     # metres past the tee where an edge meets the 12 ft
        zero = next(t for t in g["ticks"] if t["label"] == "0")
        tee = next(l for l in g["lines"] if l["kind"] == "tee")
        for e, side in zip(s["edges"], (-1, 1)):
            assert e["x1"] == e["x2"] == pytest.approx(zero["x1"] + side * 0.6096 * kx, abs=0.1)
            assert e["y1"] == pytest.approx(tee["y1"] - meet * ky, abs=0.1)   # upright: in front is up
            assert e["y2"] == pytest.approx(g["plot"]["y"], abs=0.1)          # out to the chart's far edge
        assert " A" in s["d"] and s["d"].endswith("Z")

    def test_on_its_side_in_front_is_to_the_right(self):
        g = run_js(f"out(deliveryGeometry({json.dumps(delivery_shot())}, BIGDELIVERYBOX));")
        tee = next(l for l in g["lines"] if l["kind"] == "tee")
        for e in g["paint"]["stripe"]["edges"]:
            assert e["x1"] > tee["x1"] and e["x2"] == pytest.approx(g["plot"]["x"] + g["plot"]["w"], abs=0.1)

    def test_the_stripe_is_the_one_the_calibration_reads(self):
        side = (Path(__file__).resolve().parents[1] / "src/curling_score/geometry/sideview.py").read_text()
        assert "STRIP_HALF_M = 1.2192 / 2" in side
        assert run_js("out(STRIPE_HALF_M);") == pytest.approx(1.2192 / 2)


class TestTheLineChart:
    """One drawing for the wide layout's side column: the far end of the sheet
    above, the delivery close up below, and a funnel between them."""
    D9 = {"schema_version": 9}

    def lc(self, shot, doc=None, box="LINEBOX"):
        return run_js(f"out(lineChartGeometry({json.dumps(shot)}, {json.dumps(doc or self.D9)}, {box}));")

    @staticmethod
    def crowd(g, x, y, w=104, h=26):
        return sum(1 for d in g["dots"] if x - 4 <= d["x"] <= x + w and y - h + 8 <= d["y"] <= y + 8)

    def test_its_pads_are_the_delivery_s(self):
        assert run_js("out(LINEBOX.pad);") == run_js("out(DELIVERYBOX.pad);")

    def test_the_two_parts_meet_where_the_delivery_ends(self):
        lc = self.lc(ROCKS["e2r4"])
        ht = lc["top"]["h"]
        y0, y1 = -2.3, TEE_Y - 8.05
        hog = next(l for l in lc["top"]["shapes"]["lines"] if l["kind"] == "hog" and 0 <= l["y1"] <= ht)
        assert hog["y1"] == pytest.approx((6.401 - y0) * ht / (y1 - y0), abs=0.1)
        assert lc["bottom"]["y"] == ht + 13
        assert ht + 13 + lc["bottom"]["h"] == lc["h"]
        assert lc["bottom"]["h"] == lc["h"] // 2          # the delivery takes a full half

    def test_the_funnel_spans_the_delivery_window_on_the_sheet(self):
        lc = self.lc(ROCKS["e2r4"])
        g, f, top = lc["bottom"]["g"], lc["funnel"], lc["top"]
        kx = top["w"] / 4.75
        assert f["xr"] - f["xl"] == pytest.approx(g["win"]["span"] * kx, abs=0.2)
        assert f["xl"] == pytest.approx(top["x"] + top["w"] / 2 + g["win"]["x0"] * kx, abs=0.2)
        assert f["bl"] == g["plot"]["x"]
        assert f["br"] == pytest.approx(g["plot"]["x"] + g["plot"]["w"], abs=0.1)
        assert (f["yt"], f["yb"]) == (top["h"], lc["bottom"]["y"] + g["plot"]["y"])

    def test_the_legend_goes_where_the_dots_are_not(self):
        """Its place is `legendAt`: `legend` is the delivery's own entries."""
        for key, rock in ROCKS.items():
            g = self.lc(rock)["bottom"]["g"]
            assert self.crowd(g, g["legendAt"]["x"], g["legendAt"]["y"]) == 0, key
            assert g["legend"] == [], key           # these rocks have no release read

    def test_the_aim_label_is_left_to_the_sheet_above(self):
        assert self.lc(ROCKS["e2r4"])["bottom"]["g"]["aimLabel"] is None

    def test_the_close_up_has_the_paint_under_it(self):
        paint = self.lc(ROCKS["e2r4"])["bottom"]["g"]["paint"]
        assert len(paint["house"]) == 4 and paint["stripe"] is not None

    def test_it_says_how_stretched_each_part_is(self):
        lc = self.lc(ROCKS["e2r4"])
        assert lc["acrossTop"] == 5 and lc["acrossBottom"] == lc["bottom"]["g"]["stretch"]

    def test_a_rock_not_followed_is_the_strip_at_full_height(self):
        rock = {**ROCKS["e2r4"], "line": {**ROCKS["e2r4"]["line"], "delivery": []}}
        lc = self.lc(rock)
        assert lc["bottom"] is None and lc["funnel"] is None
        assert lc["why"] == "The hog-line camera did not follow this rock from the hack"
        assert lc["top"]["h"] == lc["h"]

    def test_an_old_chart_is_the_strip_with_the_reason(self):
        lc = self.lc(ROCKS["e2r4"], {"schema_version": 7})
        assert lc["why"] == "This chart predates the delivery chart" and lc["bottom"] is None

    def test_a_broomless_rock_still_draws(self):
        # Only a doubles rock draws without a broom (lineReason fails closed for
        # four-player charts), so the doc carries the doubles format.
        rock = {**ROCKS["e2r4"], "target_broom": None}
        lc = self.lc(rock, {**DOUBLES7, "schema_version": 9})
        assert lc["bottom"]["g"]["aim"] is None and lc["funnel"] is not None
        assert self.lc(rock)["why"] == "No broom was held still before the release"

    def test_it_takes_the_box_it_is_given(self):
        lc = self.lc(ROCKS["e2r4"], box="{...LINEBOX, w: 300, h: 600}")
        assert (lc["w"], lc["h"]) == (300, 600)
        assert (lc["bottom"]["h"], lc["top"]["h"]) == (300, 600 - 13 - 300)

    def test_no_shot_is_nothing(self):
        assert run_js("out(lineChartGeometry(null, {}, LINEBOX));") is None


class TestTheLineChartMarkup:
    SRC = Path(__file__).resolve().parents[1] / "frontend/viewer/LineChart.jsx"
    DETAIL = Path(__file__).resolve().parents[1] / "frontend/viewer/Detail.jsx"

    def test_it_is_an_image_with_no_ids(self):
        src = self.SRC.read_text()
        code = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
        assert 'role="img"' in code
        assert "<defs" not in code and " id=" not in code and "clipPath" not in code

    def test_the_strip_is_shared_not_copied(self):
        assert "export function Strip(" in self.DETAIL.read_text()
        assert 'import { Strip } from "./Detail.jsx";' in self.SRC.read_text()

    def test_the_phone_strip_is_called_as_before(self):
        src = self.DETAIL.read_text()
        assert ("<Strip shot={shot} shapes={stripShapes(stripGeometry(shot))}\n"
                "               label={`The sheet from above, thrower at the bottom: ${seen}`} />") in src


class TestTheWideLayoutGate:
    def test_wide_query_is_a_media_query_byte_for_byte(self):
        css = (VIEWER / "style.css").read_text()
        queries = [q.strip() for q in re.findall(r"@media ([^{]+)\{", css)]
        want = run_js("out(WIDE_QUERY);")
        assert want == "(min-width: 1500px)"
        assert want in queries, "WIDE_QUERY is not one of the stylesheet's media queries"


class TestTheTabletLayoutGate:
    def test_tablet_query_is_a_media_query_byte_for_byte(self):
        css = (VIEWER / "style.css").read_text()
        queries = [q.strip() for q in re.findall(r"@media ([^{]+)\{", css)]
        want = run_js("out(TABLET_QUERY);")
        assert want == ("(any-pointer: coarse) and (orientation: portrait) "
                        "and (min-width: 700px) and (max-width: 1199px)")
        assert want in queries, "TABLET_QUERY is not one of the stylesheet's media queries"


class TestSheetButtonText:
    def test_nothing_set_asks(self):
        assert run_js('out([sheetButtonText("Miss", null), sheetButtonText("Miss", "")]);') == ["Miss…", "Miss…"]

    def test_a_value_is_shown(self):
        assert run_js('out(sheetButtonText("Miss", "wide"));') == "Miss: wide"

    def test_a_long_value_is_cut(self):
        got = run_js('out(sheetButtonText("Note", "came up two feet short of the tee"));')
        assert got == "Note: came up two feet…"

    def test_the_set_label_can_differ(self):
        assert run_js('out([sheetButtonText("Swapped roles", null, "Swapped"), '
                      'sheetButtonText("Swapped roles", "Red", "Swapped")]);') == ["Swapped roles…", "Swapped: Red"]


class TestNextBlankOther:
    ITEMS = [{"ei": 0, "si": 2}, {"ei": 1, "si": 0}]

    def go(self, items, ei, si):
        return run_js(f"out(nextBlankOther({json.dumps(items)}, {ei}, {si}));")

    def test_the_next_one(self):
        assert self.go(self.ITEMS, 0, 2) == {"ei": 1, "si": 0}

    def test_it_wraps(self):
        assert self.go(self.ITEMS, 1, 0) == {"ei": 0, "si": 2}

    def test_never_the_rock_it_is_on(self):
        assert self.go([{"ei": 0, "si": 2}], 0, 2) is None

    def test_none_left(self):
        assert self.go([], 0, 0) is None

    # Placing a stone marks the house known, so by the time ✔ Charted is
    # tapped the rock has left the list: the search goes on from the cursor's
    # place in the game, not from the list's start.
    SPREAD = [{"ei": 0, "si": 3}, {"ei": 2, "si": 5}, {"ei": 4, "si": 1}]

    def test_a_cursor_not_in_the_list_goes_to_the_next_after_it(self):
        assert self.go(self.SPREAD, 3, 0) == {"ei": 4, "si": 1}
        assert self.go(self.SPREAD, 2, 4) == {"ei": 2, "si": 5}

    def test_a_cursor_after_them_all_wraps_to_the_first(self):
        assert self.go(self.SPREAD, 5, 0) == {"ei": 0, "si": 3}
        assert self.go(self.SPREAD, 4, 2) == {"ei": 0, "si": 3}

    def test_a_list_of_only_the_cursor_is_none(self):
        assert self.go([{"ei": 4, "si": 1}], 4, 1) is None


class TestChartedNext:
    """✔ Charted on the iPad, its button's and the Enter key's (ruling R14):
    the queue is blankQueue's, filtered to the houses still unknown."""
    VIEW = {"ends": [{"shots": [{"state_known": False}, {"state_known": True, "missing": True},
                                {"state_known": False}, {"state_known": True}]},
                     {"shots": [{"state_known": False}]}]}
    QUEUE = [{"ei": 0, "si": 0}, {"ei": 0, "si": 1}, {"ei": 0, "si": 2}, {"ei": 1, "si": 0}]

    def go(self, queue, ei, si):
        return run_js(f"out(chartedNext({json.dumps(queue)}, {json.dumps(self.VIEW)}, {ei}, {si}));")

    def test_a_charted_never_seen_rock_is_skipped(self):
        assert self.go(self.QUEUE, 0, 0) == {"ei": 0, "si": 2}

    def test_from_a_rock_just_charted_it_goes_on_from_there(self):
        assert self.go(self.QUEUE, 0, 3) == {"ei": 1, "si": 0}
        assert self.go(self.QUEUE, 0, 1) == {"ei": 0, "si": 2}

    def test_it_wraps_and_ends_on_none(self):
        assert self.go(self.QUEUE, 1, 0) == {"ei": 0, "si": 0}
        assert self.go([{"ei": 0, "si": 1}, {"ei": 1, "si": 0}], 1, 0) is None


class TestEndLineText:
    NAME = "c => ({ red: 'Red', yellow: 'Yellow' })[c]"

    def line(self, summary, n=3):
        return run_js(f"out(endLineText({json.dumps(summary)}, {n}, {self.NAME}));")

    def test_hammer_and_power_play(self):
        s = {"hammer": "red", "hammerSource": "throws", "powerPlay": {"color": "red", "side": "left"}}
        assert self.line(s) == "End 3 · Red has hammer · power play, red, left"

    def test_hammer_from_the_placement(self):
        assert self.line({"hammer": "yellow", "hammerSource": "placement", "powerPlay": None}) \
            == "End 3 · Yellow has hammer (from the placement)"

    def test_no_summary(self):
        assert self.line(None, 1) == "End 1"

    def test_the_swapped_names(self):
        assert run_js(f"out([swapText({{red: true, yellow: false}}, {self.NAME}), "
                      f"swapText({{yellow: true, red: true}}, {self.NAME}), swapText({{}}, {self.NAME})]);") \
            == ["Red", "Red, Yellow", None]


class TestTheTabletBoxes:
    def test_they_are_the_big_boxes_narrowed(self):
        big, d = run_js("out([BIGDELIVERYBOX, TABLETDELIVERYBOX]);")
        assert d == {**big, "w": 480, "h": 160}
        sp, s = run_js("out([SPEEDBOX, TABLETSPEEDBOX]);")
        assert s == {**sp, "w": 480, "h": 115}

    def test_the_speed_and_the_delivery_share_their_x(self):
        r = json.dumps(ROCKS["e2r4"])
        g, sg = run_js(f"const s = {r}; out([deliveryGeometry(s, TABLETDELIVERYBOX), "
                       "speedGeometry(deliverySpeed(s), TABLETSPEEDBOX, s)]);")
        gx = {l["kind"]: l["x1"] for l in g["lines"] if l["kind"] != "centre"}
        sx = {l["kind"]: l["x"] for l in sg["lines"]}
        for kind in ("hack", "tee", "hog"):
            assert sx[kind] == pytest.approx(gx[kind], abs=0.1), kind


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


    def test_an_end_played_before_the_stream_counts_from_the_board(self):
        """10/15 Thursday Mens: the streams came up after end 1, so the first
        end shown is end 2 and end 1's score is the board's alone."""
        d = doc([shot(1, "red", "lead")])
        first = d["games"][0]["ends"][0]
        d["games"][0]["ends"] = [{**first, "number": n, "score": {"red": 0, "yellow": 1},
                                  "shots": [dict(s) for s in first["shots"]]}
                                 for n in (2, 3)]
        d["games"][0]["scoreboard"] = {"per_end": {"1": {"red": 2, "yellow": 0},
                                                   "2": {"red": 0, "yellow": 1},
                                                   "3": {"red": 0, "yellow": 1}}}
        got = report_js("const t = byEnd(view); out([t.earlier, t.total.score, t.total.complete]);", d)
        assert got == [[{"number": 1, "score": {"red": 2, "yellow": 0}}],
                       {"red": 2, "yellow": 2}, True]

    def test_an_earlier_end_the_board_never_posted_leaves_the_total_partial(self):
        d = doc([shot(1, "red", "lead")])
        first = d["games"][0]["ends"][0]
        d["games"][0]["ends"] = [{**first, "number": 2, "score": {"red": 0, "yellow": 1}}]
        d["games"][0]["scoreboard"] = {"per_end": {"2": {"red": 0, "yellow": 1}}}
        got = report_js("const t = byEnd(view); out([t.earlier, t.total.complete]);", d)
        assert got == [[{"number": 1, "score": None}], False]


class TestARockNeverThrown:
    """A game conceded part-way through its last end (`timeline.concede`):
    the rocks after the last one thrown are ``not_thrown``. The report keeps
    counting the type as a charter's ``not_thrown`` always has (every rock is
    scored); the line panel says why there is no line."""

    def conceded(self):
        return doc([shot(1, "red", "lead"), shot(2, "yellow", "lead"),
                    shot(3, "red", "second", missing=True, state_known=False,
                         shot_type="not_thrown", shot_type_source="conceded"),
                    shot(4, "yellow", "second", missing=True, state_known=False,
                         shot_type="not_thrown", shot_type_source="conceded")])

    def test_the_line_panel_says_it_was_never_thrown(self):
        d = self.conceded()
        d["schema_version"] = 9
        got = run_js(f"const d = {json.dumps(d)}; "
                     "out(lineReason(d.games[0].ends[0].shots[2], d));")
        assert got == "This rock was never thrown"


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


class TestTheReportPage:
    ROOT = Path(__file__).resolve().parents[1]

    def src(self, rel):
        return (self.ROOT / rel).read_text()

    def test_the_report_holds_no_arithmetic(self):
        """Every number comes from core/report.mjs, where it is tested."""
        report = self.src("frontend/viewer/Report.jsx")
        for name in ("byEnd(", "headToHead(", "detailRows(", "coverage(", "reportNotes("):
            assert name in report
        assert "/ (4 *" not in report and "toFixed" not in report

    def test_the_button_says_close_while_open(self):
        app = self.src("frontend/viewer/App.jsx")
        assert '{ui.reporting ? "Close report" : "Report"}' in app

    def test_the_viewer_carries_its_own_source_sans(self):
        """Not from Google: the viewer fetches nothing but YouTube, so it
        keeps working offline (TestItStillWorksWithNoNetwork)."""
        css = self.src("src/curling_score/viewer/style.css")
        face = css[css.index("@font-face"):][:300]
        assert 'font-family:"Source Sans 3"' in face
        assert 'url("source-sans-3.woff2") format("woff2")' in face
        assert "fonts.googleapis.com" not in self.src("src/curling_score/viewer/index.html")
        viewer_dir = self.ROOT / "src/curling_score/viewer"
        assert (viewer_dir / "source-sans-3.woff2").read_bytes()[:4] == b"wOF2"
        assert (viewer_dir / "source-sans-3.OFL.txt").is_file()

    def test_the_phone_hides_only_the_headers_title(self):
        css = self.src("src/curling_score/viewer/style.css")
        assert "header > h1, #src { display: none; }" in css
        assert "\n  h1, #src { display: none; }" not in css

    def test_the_watching_layout_steps_aside_for_the_report(self):
        """On a phone's view link #watch is fixed over the page; left up, it
        covered the report (as on the live site before this)."""
        css = self.src("src/curling_score/viewer/style.css")
        assert 'body.reporting[data-mode="view"] #watch,' in css
        assert 'body.reporting[data-mode="review"] #watch { display: none; }' in css

    def test_watch_mode_shows_only_the_headers_title(self):
        """A bare h1 rule there outranked .rpt-title and broke its layout."""
        css = self.src("src/curling_score/viewer/style.css")
        assert 'body[data-mode="view"] header > h1, body[data-mode="review"] header > h1,' in css

    def test_a_long_game_scrolls_inside_its_card(self):
        css = self.src("src/curling_score/viewer/style.css")
        assert ".rpt-scroll { overflow-x:auto; }" in css
        assert "position:sticky; left:0;" in css[css.index(".rpt-byend th[scope=row]"):]
        # "End 1" on one line, even in a 48 px phone column.
        assert "white-space:nowrap" in css[css.index(".rpt-byend thead th"):][:160]

    def test_team_colours_are_drawn_not_painted_behind(self):
        """CSS backgrounds vanish when printed; SVG fills do not."""
        report = self.src("frontend/viewer/Report.jsx")
        assert "className=\"swatch\"" not in report
        assert "<Dot " in report


class TestTheReportPrints:
    ROOT = Path(__file__).resolve().parents[1]

    def print_css(self):
        css = (self.ROOT / "src/curling_score/viewer/style.css").read_text()
        start = css.index("@media print {")
        return css[start:css.index("\n}\n", start)]

    def test_print_forces_the_light_tokens(self):
        """From a dark-mode device the ink would print cream on white paper."""
        block = self.print_css()
        assert ':root, :root:not([data-theme="light"])' in block
        assert "--ink:#16130c" in block and "--panel:#fff" in block

    def test_print_drops_the_screen_only_parts(self):
        block = self.print_css()
        assert ".rpt .screenonly" in block and ".rpt-matrix tr.type" in block

    def test_print_keeps_one_sheet(self):
        """Paper is inside the narrow-window rule, which would stack the two
        detail tables; and by shot type repeats the detail's group rows."""
        block = self.print_css()
        assert ".rpt-detail-body { grid-template-columns:repeat(2, minmax(0,1fr));" in block
        assert ".rpt-h2h-body > div + div { display:none; }" in block

    def test_print_asks_for_letter(self):
        assert "@page { size:letter;" in self.print_css()


class TestEnteringScores:
    ROOT = Path(__file__).resolve().parents[1]

    def src(self, rel):
        return (self.ROOT / rel).read_text()

    def test_fours_offers_a_blank_and_one_to_eight_each(self):
        got = run_js("out(scoreChoices(FOURS_FORMAT).map(c => [c.red, c.yellow]));")
        assert got[0] == [0, 0] and len(got) == 17
        assert got[1:9] == [[n, 0] for n in range(1, 9)]
        assert got[9:] == [[0, n] for n in range(1, 9)]

    def test_doubles_offers_one_to_six(self):
        from curling_score.game import format as F
        got = run_js(f"out(scoreChoices({json.dumps(F.DOUBLES.to_json())}).length);")
        assert got == 13

    def test_an_entered_end_is_marked(self):
        d = doc([shot(1, "red", "lead")])
        d["games"][0]["ends"][0].update(score={"red": 0, "yellow": 3}, score_source="entered")
        assert report_js("out(byEnd(view).ends[0].entered);", d) is True

    def test_the_errors_people_see(self):
        assert run_js("out([scoreError(409), scoreError(401), scoreError(0), scoreError(500)]);") == [
            "The board has a score for this end now", "Sign in again to save",
            "Couldn't save. Try again.", "Couldn't save. Try again."]

    def test_sign_in_is_asked_on_demand_from_one_place(self):
        auth = self.src("frontend/runtime/auth.mjs")
        assert 'import("../site/auth.js")' in auth
        assert "settled ??= settledUser(auth.onUser)" in auth
        assert "u.getIdToken()" in auth[auth.index("export async function whoIsSignedIn"):]
        flag = self.src("frontend/runtime/flag.mjs")
        assert 'from "./auth.mjs"' in flag

    def test_the_picker_needs_a_game_and_a_person(self):
        report = self.src("frontend/viewer/Report.jsx")
        assert 'who === "in" && !!view.doc.chart?.source_id' in report
        assert '<dialog ref={ref} id="scoreDialog"' in report

    def test_a_save_refetches_the_chart(self):
        main = self.src("frontend/viewer/main.jsx")
        assert "reload={reload}" in main
        report = self.src("frontend/viewer/Report.jsx")
        assert "await actions.reloadDoc()" in report

    def test_the_picker_buttons_are_big_enough_on_a_phone(self):
        css = self.src("src/curling_score/viewer/style.css")
        assert "#scoreDialog .sc-nums button { min-width:44px; min-height:44px;" in css


class TestTheReviewFixes:
    """Findings from the branch's final review, each pinned."""
    ROOT = Path(__file__).resolve().parents[1]

    def src(self, rel):
        return (self.ROOT / rel).read_text()

    def test_a_score_button_keeps_the_marks_its_span_would_have(self):
        """`.sc-q { all:unset }` wiped the dotted "entered by hand" line and
        the winner's bold from exactly the people who can enter scores."""
        css = self.src("src/curling_score/viewer/style.css")
        unset = css.index(".rpt-byend .sc-q { all:unset;")
        for rule in (".rpt-byend .sc-q.entered {", ".rpt-byend .sc-q.won {",
                     ".rpt-byend .sc-q.zero, .rpt-byend .sc-q.none {"):
            assert rule in css and css.index(rule) > unset, rule

    def test_sign_in_is_asked_only_once_the_report_opens(self):
        """Report is always mounted, so asking on mount loaded Firebase on
        every chart page -- which site/auth.js says the viewer never does."""
        report = self.src("frontend/viewer/Report.jsx")
        assert "const [who, setWho] = useWho(config, open);" in report
        assert "if (!config?.hosted || !open || who !== null) return undefined;" in report
        assert "open={ui.reporting}" in self.src("frontend/viewer/App.jsx")

    def test_a_long_game_takes_the_full_width(self):
        report = self.src("frontend/viewer/Report.jsx")
        assert 'className={`rpt-top${table.ends.length > 4 ? " long" : ""}`}' in report
        css = self.src("src/curling_score/viewer/style.css")
        assert ".rpt-top.long { grid-template-columns:minmax(0,1fr); }" in css


class TestMoveToTeamButton:
    """My games: a chart charted as yourself can be moved onto a team."""
    MINE = Path(__file__).resolve().parents[1] / "frontend/site/Mine.jsx"

    def test_it_sends_the_team_to_the_claim_route(self):
        src = self.MINE.read_text()
        move = src[src.index("function MoveToTeam("):]
        assert "/claim`" in move and "JSON.stringify({ team_id: team })" in move

    def test_a_team_that_already_has_the_game_is_named_with_a_link(self):
        src = self.MINE.read_text()
        assert "res.status === 409 && data.detail?.chart" in src
        assert "already has a chart of this game" in src

    def test_only_personal_charts_offer_it(self):
        src = self.MINE.read_text()
        assert "{!c.team_id && teams.length ? (" in src


class TestLineNumbers:
    """The numbers under the Detail pane's figures, for the report to add up."""

    DOC6 = {"schema_version": 6}

    def nums(self, s, doc=None):
        return run_js(f"const s = {json.dumps(s)}, d = {json.dumps(doc or self.DOC6)};"
                      "out({n: lineNumbers(s, d), f: lineFigures(s, d).figures});")

    def test_they_are_what_the_figures_say(self):
        got = self.nums(TestLineFigures().measured())
        n, f = got["n"], {x["key"]: x["value"] for x in got["f"]}
        assert n["miss_m"] == -0.712 and n["on_broom"] is False
        assert f["broom"] == "2 ft 4 in wide"
        # The hack-to-broom line is at x -0.545 at the hog line; the rock at -0.757.
        assert n["hog_off_m"] == pytest.approx(-0.757 + 0.545, abs=0.002)
        assert (n["split_s"], n["split_estimated"], n["turn"], n["hack"], n["tick"]) == (
            13.79, False, "right", "left", "confirmed")
        assert n["curl"]["m"] == pytest.approx(1.143, abs=0.01) and n["curl"]["hit"] is False
        assert n["rest"] == {"x": -1.1529, "y": 1.3486}
        assert n["reason"] is None and n["broomless"] is False

    def test_narrow_is_the_side_the_rock_curls_to(self):
        got = run_js("out([narrowOf(0.2, 'right'), narrowOf(0.2, 'left'), narrowOf(-0.2, 'right'),"
                     " narrowOf(0.2, null), narrowOf(null, 'right')]);")
        assert got == [0.2, -0.2, -0.2, None, None]
        # The figure agrees: curling right, thrown 1 ft right of the broom is narrow.
        rock = TestLineFigures().measured(at_broom={"x": -1.34, "miss_m": 0.3048})
        f = {x["key"]: x["value"] for x in self.nums(rock)["f"]}
        assert f["broom"] == "1 ft narrow"

    def test_no_line_leaves_only_what_needs_none(self):
        rock = TestLineFigures().measured()
        del rock["line"]
        n = self.nums(rock)["n"]
        assert n["reason"] == "The hog-line camera lost this rock"
        assert (n["miss_m"], n["hog_off_m"], n["curl"], n["turn"]) == (None, None, None, None)
        assert n["split_s"] == 13.79 and n["rest"] is not None

    def test_an_old_chart_predates_it(self):
        n = self.nums(TestLineFigures().measured(), {"schema_version": 5})["n"]
        assert n["predates"] is True and n["miss_m"] is None

    def test_a_doubles_rock_with_no_broom_has_a_curl_but_no_miss(self):
        rock = TestLineFigures().measured(at_broom=None, at_tee={"x": -1.9})
        del rock["target_broom"]
        n = self.nums(rock, {"schema_version": 7, "format": {"name": "doubles",
                     "positions": ["A", "B"], "throw_table": [1, 2, 2, 2, 1]}})["n"]
        assert n["broomless"] is True and n["miss_m"] is None and n["hog_off_m"] is None
        assert n["curl"]["m"] is not None and n["turn"] == "right"

    def test_the_sides_of_the_line_by_turn(self):
        got = run_js("out([sideNames('right'), sideNames('left'), sideNames(null)]);")
        assert got == [["wide", "narrow"], ["narrow", "wide"], ["left", "right"]]

    def test_the_type_groups(self):
        got = run_js("out(['hit', 'draw', 'guard', 'draw_through', 'flashed', 'hogged', 'unknown',"
                     " 'takeout', 'nonsense'].map(groupOf));")
        assert got[:7] == ["Hit", "Draw", "Guard", "Draw", "Hit", "Other", "Other"]
        assert got[8] == "Other"


TEE_Y = 34.747
HACK_Y = 34.747 + 3.658
FOURS_JSON = {"name": "fours", "positions": ["lead", "second", "third", "skip"],
              "throw_table": [1, 1, 2, 2, 3, 3, 4, 4], "swappable": False}


def aimed(number, color, slot, *, broom_x=0.5, off=0.0, miss=0.0, split=13.9, curl="right",
          hack_x=-0.1, delivery=True):
    """A schema-8 rock thrown from the left hack at a broom on the tee line at
    `broom_x`, its delivery `off` metres right of its hack-to-broom line."""
    def aim(y):
        hx = -0.152
        return hx + (broom_x - hx) * (y - HACK_Y) / (0.0 - HACK_Y)
    pts = []
    for i, yp in enumerate([-3.45, -3.45, -2.0, 0.0, 2.0, 4.0, 6.0, 7.5]):
        y = TEE_Y - yp
        pts.append([round(i * 0.4 - 3.0, 2), round(y, 4), round(aim(y) + off, 4)])
    line = {"start": {"x": hack_x, "y": TEE_Y + 3.45}, "curl": curl, "confirmed": True,
            "at_hog": {"x": round(aim(28.346) + off, 4), "offset_m": 0.0},
            "at_broom": {"x": broom_x + miss, "miss_m": miss}}
    if delivery:
        line["delivery"] = pts
    return shot(number, color, ["lead", "second", "third", "skip"][slot - 1], thrower_slot=slot,
                target_broom={"x": broom_x, "y": 0.0}, long_split_s=split, line=line)


def play(**kw):
    base = {"source_id": "src1", "color": "red", "slot": 4, "format": FOURS_JSON,
            "played_at": "2026-09-29T23:30:00+00:00", "view_path": "/s/share1/",
            "teams": {"red": "Dimmit", "yellow": "Grant"}, "sheet": 3}
    base.update(kw)
    return base


DAY = "d => d.toISOString().slice(5, 10)"


class TestSwitchedEndsDialog:
    """What "I played…" needs to mark the ends a doubles pair switched."""

    def test_ends_text(self):
        got = run_js("out([[4,5,6,7],[2,4],[2,3],[2,4,5,6],[2,3,5],[3]].map(endsText));")
        assert got == ["4–7", "2 & 4", "2 & 3", "2 & 4–6", "2, 3 & 5", "3"]

    def test_switch_note(self):
        got = run_js("out([switchNote([]), switchNote([3]), switchNote([4,5,6,7])]);")
        tail = ("Your partner gets the rocks you did not, in every end: "
                "saving marks the game for both of you.")
        assert got == [tail, "You threw the other rocks in end 3. " + tail,
                       "You threw the other rocks in ends 4–7. " + tail]

    def test_play_body_sends_switched_ends_only_when_touched(self):
        got = run_js("const b = {path: '/g/x/', color: 'red', slot: 1};"
                     "out([playBody({...b, touched: false, on: true, flips: new Set([3])}),"
                     " playBody({...b, touched: true, on: false, flips: new Set([3])}),"
                     " playBody({...b, touched: true, on: true, flips: new Set([5, 3])})]);")
        assert got == [{"path": "/g/x/", "color": "red", "slot": 1},
                       {"path": "/g/x/", "color": "red", "slot": 1, "switched_ends": []},
                       {"path": "/g/x/", "color": "red", "slot": 1, "switched_ends": [3, 5]}]

    def test_the_view_says_which_ends_each_team_switched(self):
        from curling_score import timeline
        d = timeline.apply_switched_ends(doubles_doc(doubles_shots()), {"yellow": {"ends": [3]}})
        got = run_js(setup(d) + "const v = buildGameView(state.doc, 0, state.overrides);"
                     "out([switchedEnds(v, 'red'), switchedEnds(v, 'yellow'), endChoices(v)]);")
        assert got == [[], [3], [{"id": 3, "number": 3}]]

    def test_unticking_clears_a_charts_own_swap_of_that_colour(self):
        """A swap the chart holds itself (an end-level override, from before
        switched ends lived on the game) would keep the box ticked once the
        game's mark is gone: unticking turns it off too, and only its colour."""
        got = run_js(setup(doubles_doc(doubles_shots()),
                           {"0.3": {"roles_swapped": {"red": True, "yellow": True}}}) +
                     "const v = buildGameView(state.doc, 0, state.overrides);"
                     "out([chartSwapOff(state.overrides, v.game, v.ends[0].end, 'red'),"
                     " chartSwapOff({}, v.game, v.ends[0].end, 'red'),"
                     " chartSwapOff({'0.3': {roles_swapped: {red: false}}}, v.game, v.ends[0].end, 'red')]);")
        assert got == [{"key": "0.3", "fields": {"roles_swapped": {"red": False, "yellow": True}}},
                       None, None]

    def test_a_charts_own_swap_is_not_the_games(self):
        """The dialog speaks for the game, so a swap only the chart holds (an
        end-level override) is not one of its switched ends."""
        got = run_js(setup(doubles_doc(doubles_shots()), {"0.3": {"roles_swapped": {"red": True}}}) +
                     "const v = buildGameView(state.doc, 0, state.overrides);"
                     "out(switchedEnds(v, 'red'));")
        assert got == []


class TestPositionChoices:
    def test_fours_by_throwing_order(self):
        got = run_js("out(positionChoices(formatOf({})));")
        assert [(c["slot"], c["label"], c["role"]) for c in got] == [
            (1, "Lead", "1st & 2nd"), (2, "Second", "3rd & 4th"),
            (3, "Third", "5th & 6th"), (4, "Skip", "7th & 8th")]

    def test_doubles_by_player(self):
        got = run_js(setup(doubles_doc(doubles_shots())) + "out(positionChoices(formatOf(state.doc)));")
        assert [(c["label"], c["role"]) for c in got] == [("Player A", "1st & 5th"),
                                                          ("Player B", "2nd–4th")]

    def test_a_play_and_its_game_in_words(self):
        got = run_js(f"const p = {json.dumps(play())};"
                     f"out([playText(p), gameText(p, {DAY}), gameText({{...p, teams: {{}}}}, {DAY}),"
                     f" gameText({{...p, teams: {{}}, sheet: null, played_at: null}}), playText({{...p, teams: {{}}}})]);")
        assert got == ["Dimmit · Skip (7th & 8th)", "09-29 v Grant", "09-29 · sheet 3",
                       "Undated", "Red · Skip (7th & 8th)"]


SHORT_JSON = {**FOURS_JSON, "short_throw_table": [1, 1, 1, 2, 2, 2, 4, 4]}


class TestShortHandedPlays:
    def test_choices_for_a_team_of_three(self):
        got = run_js(f"out(positionChoices({json.dumps(SHORT_JSON)}, true));")
        assert [(c["slot"], c["label"], c["role"]) for c in got] == [
            (1, "Lead", "1st–3rd"), (2, "Second", "4th–6th"), (4, "Skip", "7th & 8th")]

    def test_a_play_on_a_team_of_three(self):
        plays = [play(slot=1, short_handed=["red"], format=SHORT_JSON),
                 play(slot=3, short_handed=["red"], format=SHORT_JSON),
                 play(slot=3, short_handed=["yellow"], format=SHORT_JSON),
                 play(slot=4, format=SHORT_JSON)]
        got = run_js(f"out({json.dumps(plays)}.map(playText));")
        assert got == ["Dimmit · Lead (1st–3rd)",
                       "Dimmit · This team played with three. Pick your rocks again",
                       "Dimmit · Third (5th & 6th)",
                       "Dimmit · Skip (7th & 8th)"]


class TestPlayerRocks:
    """A player's rocks from the game the server baked and cut down for them."""

    def _baked(self, document, overrides, color, slot):
        import copy
        from curling_score import timeline
        return timeline.for_player(timeline.apply_overrides(copy.deepcopy(document), overrides),
                                   color, slot)

    def test_the_same_rocks_and_types_the_viewer_shows(self):
        fx = json.loads(REPORT_FIXTURE.read_text())
        baked = self._baked(fx["doc"], fx["overrides"], "red", 4)
        got = run_js(
            f"const raw = buildGameView({json.dumps(fx['doc'])}, 0, {json.dumps(fx['overrides'])});"
            "const want = raw.ends.flatMap(x => x.shots.filter(s => s.color === 'red' && s.thrower_slot === 4"
            "  && !s.missing).map(s => [x.end.number, s.number, typeOf(s)]));"
            f"const {{rows, missing}} = playerRocks({json.dumps(play())}, {json.dumps(baked)}, {DAY});"
            "out({want, got: rows.map(r => [r.end, r.number, r.type]), missing,"
            " groups: rows.map(r => r.group), href: rows[0].href, game: rows[0].game});")
        assert got["got"] == got["want"] and len(got["got"]) >= 6
        assert set(got["groups"]) <= {"Hit", "Draw", "Guard", "Other"}
        assert got["href"] == f"/s/share1/#e={got['got'][0][0]}&s={got['got'][0][1]}"
        assert got["game"] == "09-29 v Grant"

    def test_a_measured_rock_carries_its_numbers_figures_and_path(self):
        d = doc([aimed(13, "red", 4, off=0.05, miss=-0.2), aimed(15, "red", 4, delivery=False),
                 aimed(14, "yellow", 4)], end_number=3)
        d["schema_version"] = 8
        got = run_js(f"const {{rows}} = playerRocks({json.dumps(play())}, {json.dumps(d)}, {DAY});"
                     "out(rows.map(r => ({n: r.number, turn: r.turn, name: r.turnName, miss: r.nums.miss_m,"
                     " broom: r.figures[0].value, path: r.path && r.path.pts.map(p => +p.dx.toFixed(3)),"
                     " start: r.path && r.path.start, key: r.key})));")
        assert [r["n"] for r in got] == [13, 15]
        first = got[0]
        assert first["turn"] == "right" and first["miss"] == -0.2
        assert first["name"] == "in-turn"                # curling right off the left hack
        assert first["broom"] == "8 in wide"
        assert first["path"] == [0.05] * 8          # every sample 5 cm right of its aim line
        assert first["start"]["yp"] == pytest.approx(-3.45)
        assert first["key"] == "src1.3.13"
        assert got[1]["path"] is None

    def test_a_rock_never_seen_is_counted_not_listed(self):
        d = doc([aimed(13, "red", 4), shot(15, "red", "skip", thrower_slot=4, missing=True)])
        d["schema_version"] = 8
        got = run_js(f"const r = playerRocks({json.dumps(play())}, {json.dumps(d)});"
                     "out([r.rows.length, r.missing]);")
        assert got == [1, 1]

    def test_a_rock_a_conceded_end_never_threw_is_neither(self):
        """Not lost and not thrown: the player never threw it (`timeline.concede`)."""
        d = doc([aimed(13, "red", 4), shot(15, "red", "skip", thrower_slot=4, missing=True,
                                           shot_type="not_thrown", shot_type_source="conceded")])
        d["schema_version"] = 8
        got = run_js(f"const r = playerRocks({json.dumps(play())}, {json.dumps(d)});"
                     "out([r.rows.length, r.missing]);")
        assert got == [1, 0]

    def test_doubles_by_the_person_across_a_swap(self):
        shots = doubles_shots()
        baked = self._baked(doubles_doc(shots), {"0.3": {"roles_swapped": {"red": True}}}, "red", 1)
        got = run_js(f"const p = {json.dumps(play(slot=1, format=baked['format']))};"
                     f"out(playerRocks(p, {json.dumps(baked)}).rows.map(r => r.number));")
        assert got == [3, 5, 7]


def _row(group, turn, *, hack="left", miss=None, split=None, on=False, path=True, played="2026-09-29",
         end=1, number=13, est=False, tick="confirmed"):
    name = None if turn is None or hack is None else "out-turn" if turn == hack else "in-turn"
    return {"key": f"{played}.{end}.{number}", "group": group, "turn": turn, "turnName": name,
            "playedAt": played, "end": end, "number": number, "href": f"/g/x/#e={end}&s={number}",
            "path": {"pts": []} if path else None,
            "nums": {"miss_m": miss, "split_s": split, "on_broom": on, "split_estimated": est,
                     "tick": tick, "hack": hack, "turn": turn}}


class TestShotGroups:
    def test_fixed_order_and_no_empty_groups(self):
        rows = [_row("Draw", "left"), _row("Hit", None), _row("Draw", "right"), _row("Hit", "right"),
                _row("Other", "left"), _row("Hit", "left", hack=None)]
        got = run_js(f"out(shotGroups({json.dumps(rows)}).map(g => [g.id, g.label, g.rows.length, g.turn]));")
        assert got == [["Hit-in-turn", "Hits · In-turn", 1, "right"],
                       ["Hit-unknown", "Hits · Turn not known", 2, None],
                       ["Draw-in-turn", "Draws & guards · In-turn", 1, "right"],
                       ["Draw-out-turn", "Draws & guards · Out-turn", 1, "left"],
                       ["Other-out-turn", "Other rocks · Out-turn", 1, "left"]]

    def test_draws_and_guards_are_one_group(self):
        rows = [_row("Guard", "right", number=3), _row("Draw", "right", number=13),
                _row("Guard", "left", number=5)]
        got = run_js(f"out(shotGroups({json.dumps(rows)}).map(g => [g.id, g.rows.map(r => r.number), g.turn]));")
        assert got == [["Draw-in-turn", [3, 13], "right"], ["Draw-out-turn", [5], "left"]]

    def test_an_in_turn_from_the_other_hack_is_drawn_mirrored(self):
        # The left hack's in-turn curls right, the right hack's curls left: both
        # 30 cm left of the broom, wide for the first and narrow for the second.
        rows = [_row("Draw", "right", miss=-0.3, split=14.0, played="2026-09-29"),
                _row("Draw", "right", miss=-0.2, split=14.1, played="2026-09-29", number=15),
                _row("Draw", "left", hack="right", miss=-0.3, split=14.0, played="2026-09-22")]
        rows[2]["path"] = {"pts": [{"t": 0, "yp": 1.0, "dx": 0.04}], "start": {"yp": -3.45, "dx": -0.01}}
        got = run_js(f"const [g] = shotGroups({json.dumps(rows)}); const t = summaryText(g.summary, g.turn);"
                     "out([g.id, g.turn, t.broom, t.notes, g.drawn[2].nums.miss_m, g.drawn[2].path,"
                     " g.rows[2].nums.miss_m, g.rows[2].turn]);")
        assert got[:4] == ["Draw-in-turn", "right", "0 on · 2 wide · 1 narrow",
                           ["1 from the right hack, drawn mirrored"]]
        assert got[4] == 0.3
        assert got[5] == {"pts": [{"t": 0, "yp": 1.0, "dx": -0.04}], "start": {"yp": -3.45, "dx": 0.01}}
        assert got[6:] == [-0.3, "left"]                 # the table's rows as thrown

    def test_a_tie_is_drawn_from_the_left_hack(self):
        rows = [_row("Hit", "right", miss=0.1, split=9.0), _row("Hit", "left", hack="right", miss=0.1, split=9.0),
                _row("Hit", "left", miss=0.1, split=9.0, number=15),
                _row("Hit", "right", hack="right", miss=0.1, split=9.0, number=15)]
        got = run_js(f"out(shotGroups({json.dumps(rows)}).map(g => [g.id, g.turn, g.summary.mirrored]));")
        assert got == [["Hit-in-turn", "right", 1], ["Hit-out-turn", "left", 1]]

    def test_two_games_on_one_day_are_not_interleaved(self):
        rows = [dict(_row("Draw", "right", played="", end=e, number=n), sourceId=g, key=f"{g}.{e}.{n}")
                for g, e, n in (("A", 1, 7), ("B", 1, 7), ("B", 1, 9), ("A", 2, 7))]
        got = run_js(f"out(shotGroups({json.dumps(rows)})[0].rows.map(r => r.key));")
        assert got == ["B.1.7", "B.1.9", "A.1.7", "A.2.7"]

    def test_newest_game_first_then_end_and_rock(self):
        rows = [_row("Draw", "right", played="2026-09-22", end=1, number=13),
                _row("Draw", "right", played="2026-09-29", end=4, number=15),
                _row("Draw", "right", played="2026-09-29", end=2, number=13)]
        got = run_js(f"out(shotGroups({json.dumps(rows)})[0].rows.map(r => r.key));")
        assert got == ["2026-09-29.2.13", "2026-09-29.4.15", "2026-09-22.1.13"]

    def test_the_summary_counts_by_side_of_the_turn(self):
        rows = [_row("Draw", "right", miss=0.05, split=13.8, on=True),
                _row("Draw", "right", miss=-0.4, split=14.2),      # left of the broom: wide
                _row("Draw", "right", miss=-0.3, split=13.6),
                _row("Draw", "right", miss=0.25, split=None),      # right: narrow
                _row("Draw", "right", miss=None, split=14.0, path=False)]
        got = run_js(f"const s = summarize({json.dumps(rows)}, 'right'); out([s, summaryText(s, 'right')]);")
        s, t = got
        assert (s["n"], s["broom"]["on"], s["broom"]["minus"], s["broom"]["plus"]) == (5, 1, 2, 1)
        assert s["weight"] == {"n": 4, "median": pytest.approx(13.9), "lo": 13.6, "hi": 14.2}
        assert t["count"] == "5 rocks"
        assert t["weight"] == "13.9 s (13.6–14.2)"
        assert t["broom"] == "1 on · 2 wide · 1 narrow"
        # Curling right, + is narrow: [-0.4, -0.3, 0.05, 0.25] has median -0.125, 5 in wide.
        assert t["typical"] == "5 in wide"
        assert t["notes"] == ["1 not measured at the broom", "1 not timed", "1 with no delivery path"]

    def test_counter_clockwise_flips_the_sides_and_no_turn_says_left_and_right(self):
        rows = [_row("Hit", "left", miss=-0.3, split=9.0)]
        assert run_js(f"const s = summarize({json.dumps(rows)}, 'left'); out(summaryText(s, 'left').broom);") == \
            "0 on · 0 wide · 1 narrow"
        rows = [_row("Hit", None, miss=-0.3, split=9.0)]
        assert run_js(f"const s = summarize({json.dumps(rows)}, null); out(summaryText(s, null));")["broom"] == \
            "0 on · 1 left · 0 right"

    def test_nothing_measured_reads_as_dashes(self):
        got = run_js(f"out(summaryText(summarize({json.dumps([_row('Guard', None)])}, null), null));")
        assert (got["count"], got["weight"], got["broom"], got["typical"]) == ("1 rock", "–", "–", "–")


class TestMissScatter:
    def test_the_frame_and_the_points(self):
        rows = [_row("Draw", "right", miss=0.3048, split=13.5), _row("Draw", "right", miss=-0.6, split=14.4, est=True),
                _row("Draw", "right", miss=0.0, split=14.0, tick="disagrees"), _row("Draw", "right")]
        g = run_js(f"out(missScatter({json.dumps(rows)}, 'right'));")
        assert [s["text"] for s in g["sides"]] == ["wide", "narrow"]
        assert [t["label"] for t in g["xTicks"]] == ["2 ft", "1 ft", "0", "1 ft", "2 ft"]
        assert [t["label"] for t in g["yTicks"]] == ["13.5", "14.0", "14.5"]
        p = g["plot"]
        for q in g["points"]:
            assert p["x"] <= q["cx"] <= p["x"] + p["w"] and p["y"] <= q["cy"] <= p["y"] + p["h"]
        heavy, light, on = g["points"]
        assert heavy["cy"] < on["cy"] < light["cy"]           # heavier (shorter split) at the top
        assert light["cx"] < on["cx"] < heavy["cx"]           # left of the broom to the left
        assert light["hollow"] and on["dim"] and not heavy["hollow"]
        assert g["skipped"] == 1

    def test_counter_clockwise_and_a_miss_off_the_chart(self):
        rows = [_row("Hit", "left", miss=3.0, split=9.0)]
        g = run_js(f"out(missScatter({json.dumps(rows)}, 'left'));")
        assert [s["text"] for s in g["sides"]] == ["narrow", "wide"]
        assert len(g["xTicks"]) == 13 and g["points"][0]["clipped"] is True and g["clipped"] == 1
        assert [t["label"] for t in g["xTicks"]][4:9] == ["2 ft", "", "0", "", "2 ft"]
        assert g["points"][0]["cx"] == g["plot"]["x"] + g["plot"]["w"]


class TestMissScatterWeightAxis:
    def test_a_wide_range_of_weights_is_ticked_less_often(self):
        def labels(splits):
            rows = [_row("Hit", "right", miss=0.1 * i, split=sp) for i, sp in enumerate(splits)]
            return [t["label"] for t in run_js(f"out(missScatter({json.dumps(rows)}, 'right'));")["yTicks"]]
        assert labels([13.6, 14.4]) == ["13.5", "14.0", "14.5"]
        assert labels([8.4, 12.9]) == ["8", "9", "10", "11", "12", "13"]
        assert labels([8.4, 16.4]) == ["8", "10", "12", "14", "16", "18"]

    def test_a_narrow_range_widens_on_the_half_seconds(self):
        rows = [_row("Draw", "right", miss=0.0, split=sp) for sp in (14.1, 14.4)]
        g = run_js(f"out(missScatter({json.dumps(rows)}, 'right'));")
        assert [t["label"] for t in g["yTicks"]] == ["13.5", "14.0", "14.5"]


class TestDeliveryOverlay:
    def test_each_rock_is_measured_off_its_own_aim_line(self):
        rocks = [aimed(13, "red", 4, broom_x=bx, off=off) for bx, off in ((0.8, 0.03), (-1.2, -0.02))]
        got = run_js(f"const d = {{schema_version: 8}};"
                     f"out({json.dumps(rocks)}.map(s => aimFrame(s, d).pts.map(p => +p.dx.toFixed(3))));")
        assert got == [[0.03] * 8, [-0.02] * 8]

    def test_no_broom_or_no_delivery_is_no_frame(self):
        no_path = aimed(13, "red", 4, delivery=False)
        old = aimed(13, "red", 4)
        got = run_js(f"out([aimFrame({json.dumps(no_path)}, {{schema_version: 8}}),"
                     f" aimFrame({json.dumps(old)}, {{schema_version: 7}})]);")
        assert got == [None, None]

    def test_the_overlay_centres_the_aim_line_and_draws_a_median(self):
        rocks = [aimed(13, "red", 4, off=off) for off in (0.04, -0.02, 0.01)]
        g = run_js(f"const d = {{schema_version: 8}};"
                   f"const paths = {json.dumps(rocks)}.map((s, i) => ({{key: 'k' + i, ...aimFrame(s, d)}}));"
                   "out(deliveryOverlay(paths, 'right'));")
        assert g["n"] == 3 and len(g["rocks"]) == 3 and g["median"]
        assert g["aim"]["x1"] == pytest.approx(g["plot"]["x"] + g["plot"]["w"] / 2, abs=1)
        assert [s["text"] for s in g["sides"]] == ["wide", "narrow"]
        assert {q["kind"] for q in g["lines"]} == {"hack", "tee", "hog"}
        assert all(r["start"] for r in g["rocks"])
        # 0.6 m across: a tick every 3 in, figured every 6.
        assert [t["label"] for t in g["ticks"]] == ["", "6 in", "", "0", "", "6 in", ""]

    def test_fewer_than_three_rocks_have_no_median_and_a_wild_one_caps_the_window(self):
        rocks = [aimed(13, "red", 4, off=off) for off in (0.02, 3.0)]
        g = run_js(f"const d = {{schema_version: 8}};"
                   f"const paths = {json.dumps(rocks)}.map((s, i) => ({{key: 'k' + i, ...aimFrame(s, d)}}));"
                   "out(deliveryOverlay(paths, null));")
        assert g["median"] is None and g["n"] == 2
        # 1.6 m at most, -30 in to +30 every 3 in, figured every foot past a metre.
        assert len(g["ticks"]) == 21
        assert [t["label"] for t in g["ticks"] if t["label"]] == ["24 in", "12 in", "0", "12 in", "24 in"]


class TestThePlayedDialog:
    """The viewer's "I played…": which rocks of a game were yours."""
    ROOT = Path(__file__).resolve().parents[1]

    def src(self, rel):
        return (self.ROOT / rel).read_text()

    def test_the_button_is_only_where_there_is_a_game_and_a_server(self):
        app = self.src("frontend/viewer/App.jsx")
        button = app[app.index('<button id="playedBtn"'):]
        assert "hidden={!config.hosted || !doc.chart?.source_id}" in button[:400]
        assert "<PlayedDialog playing={ui.playing} onClose={closePlayed} onSaved={reload} />" in app

    def test_sign_in_is_asked_only_once_the_dialog_is_open(self):
        """The dialog is always mounted; its form is not. Asking from the form
        keeps Firebase off every page nobody opens it on."""
        played = self.src("frontend/viewer/Played.jsx")
        dialog = played[played.index("export function PlayedDialog("):played.index("function PlayedForm(")]
        assert "whoIsSignedIn" not in dialog and "accountsOn" not in dialog
        assert "{playing && <PlayedForm key={playing.opened}" in dialog
        form = played[played.index("function PlayedForm("):]
        assert "await accountsOn()" in form and "await whoIsSignedIn()" in form

    def test_it_sends_the_pages_own_path_and_a_fresh_token(self):
        played = self.src("frontend/viewer/Played.jsx")
        save = played[played.index("const save = async"):played.index("const clear = async")]
        assert "await whoIsSignedIn()" in save
        assert "playBody({ path: location.pathname, color, slot: picked," in save

    def test_the_calls_go_to_the_play_routes(self):
        plays = self.src("frontend/runtime/plays.mjs")
        assert "`/api/me/plays/${encodeURIComponent(sourceId)}`" in plays
        assert 'method: "PUT"' in plays and 'method: "DELETE"' in plays

    def test_on_the_desktop_it_sits_after_the_flag(self):
        css = self.src("src/curling_score/viewer/style.css")
        assert "header #playedBtn  { order: 13; }" in css


class TestNamingTheTeams:
    """The viewer's "Name teams…": who played, typed in from the game's own
    page, kept on the game the way the game list keeps it."""
    ROOT = Path(__file__).resolve().parents[1]

    def src(self, rel):
        return (self.ROOT / rel).read_text()

    def send(self, status, reply, *, throws=False, body=None):
        """sendTeams against a stubbed fetch: what it asked for, and what it said."""
        body = body or {"red": "Dimmit", "yellow": ""}
        module = (self.ROOT / "frontend/runtime/teams.mjs").as_uri()
        fetch = ("async () => { throw new Error('offline'); }" if throws else
                 f"async (url, init) => {{ asked.push({{url, init}});"
                 f" return {{ ok: {str(200 <= status < 300).lower()}, status: {status},"
                 f" json: async () => ({json.dumps(reply)}) }}; }}")
        script = (f"const asked = [];\nglobalThis.fetch = {fetch};\n"
                  f"const M = await import({module!r});\n"
                  f"const res = await M.sendTeams('s 1', {json.dumps(body)}, 'tok');\n"
                  "console.log(JSON.stringify({asked, res}));")
        proc = subprocess.run([node, "--input-type=module", "-e", script],
                              capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_the_form_starts_from_the_names_typed_not_the_colours(self):
        """teamNames falls back on "Red": saved back, that would name a team Red."""
        got = run_js("out([typedTeamNames({teams: {red: {name: 'Dimmit'}}}),"
                     " typedTeamNames({}), typedTeamNames(null)]);")
        assert got == [{"red": "Dimmit", "yellow": ""}, {"red": "", "yellow": ""},
                       {"red": "", "yellow": ""}]

    def test_it_posts_both_names_with_the_token(self):
        got = self.send(200, {"ok": True, "red": "Dimmit", "yellow": None})
        (call,) = got["asked"]
        assert call["url"] == "/api/games/s%201/teams"
        assert call["init"]["method"] == "POST"
        assert call["init"]["headers"]["Authorization"] == "Bearer tok"
        assert json.loads(call["init"]["body"]) == {"red": "Dimmit", "yellow": ""}
        assert got["res"] == {"ok": True, "status": 200, "red": "Dimmit", "yellow": None}

    def test_it_sends_a_team_of_three_with_the_names(self):
        body = {"red": "Dimmit", "yellow": "", "short_handed": ["red"]}
        got = self.send(200, {"ok": True, "short_handed": ["red"]}, body=body)
        assert json.loads(got["asked"][0]["init"]["body"]) == body

    def test_each_box_says_which_team_it_marks(self):
        """Both boxes read "Played with three"; a screen reader has to hear
        which team a box marks, or the wrong team's rocks get re-credited."""
        teams = self.src("frontend/viewer/Teams.jsx")
        assert 'aria-label="Red team played with three"' in teams
        assert 'aria-label="Yellow team played with three"' in teams

    def test_the_menu_says_teams_and_the_dialog_offers_three(self):
        assert ">\n      Teams…\n" in self.src("frontend/viewer/App.jsx")
        teams = self.src("frontend/viewer/Teams.jsx")
        assert "Played with three" in teams and "short_handed" in teams

    def test_a_refusal_carries_the_servers_words(self):
        got = self.send(422, {"detail": "that name is too long for a team"})
        assert got["res"] == {"ok": False, "status": 422,
                              "error": "that name is too long for a team"}

    def test_no_network_is_a_failure_not_a_throw(self):
        assert self.send(0, {}, throws=True)["res"] == {"ok": False, "status": 0, "error": None}

    def known(self, status, reply, *, throws=False):
        """fetchTeamNames against a stubbed fetch: what it asked for, and the names."""
        module = (self.ROOT / "frontend/runtime/teams.mjs").as_uri()
        fetch = ("async () => { throw new Error('offline'); }" if throws else
                 f"async (url, init) => {{ asked.push({{url, init: init || null}});"
                 f" return {{ ok: {str(200 <= status < 300).lower()}, status: {status},"
                 f" json: async () => ({json.dumps(reply)}) }}; }}")
        script = (f"const asked = [];\nglobalThis.fetch = {fetch};\n"
                  f"const M = await import({module!r});\n"
                  "const names = await M.fetchTeamNames('s 1');\n"
                  "console.log(JSON.stringify({asked, names}));")
        proc = subprocess.run([node, "--input-type=module", "-e", script],
                              capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_the_leagues_names_are_asked_for_without_a_token(self):
        got = self.known(200, {"league": "Monday", "names": ["Dimmit", "Grant"]})
        assert got["asked"] == [{"url": "/api/games/s%201/team-names", "init": None}]
        assert got["names"] == ["Dimmit", "Grant"]

    def test_no_names_is_all_that_can_go_wrong(self):
        assert self.known(404, {"detail": "no such game"})["names"] == []
        assert self.known(200, {"names": "Dimmit"})["names"] == []
        assert self.known(200, {"names": ["Dimmit", None, 3]})["names"] == ["Dimmit"]
        assert self.known(0, {}, throws=True)["names"] == []

    def matching(self, names, typed, *limit):
        """namesMatching, run in node: the names shown under a box."""
        module = (self.ROOT / "frontend/runtime/teams.mjs").as_uri()
        args = ", ".join(json.dumps(a) for a in (names, typed, *limit))
        script = (f"const M = await import({module!r});\n"
                  f"console.log(JSON.stringify(M.namesMatching({args})));")
        proc = subprocess.run([node, "--input-type=module", "-e", script],
                              capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout.strip().splitlines()[-1])

    LEAGUE = ["Casey", "Dimmit", "McAll In The Fam", "Hartung", "Reich", "Singh", "Harrison"]

    def test_an_empty_box_offers_the_most_used_names(self):
        assert self.matching(self.LEAGUE, "") == self.LEAGUE
        assert self.matching(self.LEAGUE, "  ", 3) == ["Casey", "Dimmit", "McAll In The Fam"]

    def test_names_that_start_with_the_typing_come_before_ones_that_hold_it(self):
        assert self.matching(self.LEAGUE, "ha") == ["Hartung", "Harrison"]
        assert self.matching(self.LEAGUE, "  FAM ") == ["McAll In The Fam"]
        assert self.matching(["Hart", "Grant", "Arnold"], "ar") == ["Arnold", "Hart"]
        assert self.matching(self.LEAGUE, "zz") == []

    def test_a_name_already_in_the_box_is_not_offered_again(self):
        """Typing toward a longer name goes on offering it: "Hart" on the way
        to "Hartung" still shows "Hartung"."""
        assert self.matching(["Hart", "Hartung"], "Har") == ["Hart", "Hartung"]
        assert self.matching(["Hart", "Hartung"], "hart ") == ["Hartung"]
        assert self.matching(["Hart", "Hartung"], "Hartung") == []

    def test_a_pick_closes_the_list_until_more_is_typed(self):
        """What a tap leaves behind: the list goes, rather than offering the
        names the one just picked is the start of."""
        box = self.src("frontend/viewer/NameBox.jsx")
        assert "const pick = name => { onValue(name); setHi(-1); setOpen(false); };" in box
        assert "onChange={e => { onValue(e.target.value); setHi(-1); setOpen(true); }}" in box

    def test_both_boxes_offer_the_leagues_names(self):
        teams = self.src("frontend/viewer/Teams.jsx")
        form = teams[teams.index("function TeamsForm("):]
        assert "fetchTeamNames(naming.sourceId)" in form
        assert form.count("<NameBox ") == 2
        assert form.count("names={known}") == 2

    def test_so_do_the_game_lists(self):
        games = self.src("frontend/site/Games.jsx")
        cell = games[games.index("function TeamsCell("):games.index("const STATUS =")]
        assert "fetchTeamNames(game.source_id)" in cell
        assert cell.count("<NameBox ") == 2
        assert cell.count("names={names}") == 2

    def test_the_names_are_drawn_by_the_page_not_a_datalist(self):
        """Firefox for Android draws nothing for a datalist on a text box, so
        a phone there was offered no names."""
        box = self.src("frontend/viewer/NameBox.jsx")
        teams = self.src("frontend/viewer/Teams.jsx")
        games = self.src("frontend/site/Games.jsx")
        cell = games[games.index("function TeamsCell("):games.index("const STATUS =")]
        assert "<datalist" not in teams and "<datalist" not in cell
        assert 'role="listbox"' in box and 'role="option"' in box
        # A press on the list keeps focus in the box: the game list saves as
        # soon as focus leaves the pair.
        assert "onMouseDown={e => e.preventDefault()}" in box

    def test_the_names_are_not_inside_the_boxs_label(self):
        """Inside a <label>, the listed names would be read out as the box's name."""
        teams = self.src("frontend/viewer/Teams.jsx")
        form = teams[teams.index("function TeamsForm("):]
        assert '<label className="tm-team">' not in form
        assert form.count("<label htmlFor=") == 2

    def test_the_button_is_only_where_there_is_a_game_and_a_server(self):
        app = self.src("frontend/viewer/App.jsx")
        button = app[app.index('<button id="teamsBtn"'):]
        assert "hidden={!config.hosted || !doc.chart?.source_id}" in button[:400]
        assert "names: typedTeamNames(view.game)" in button[:400]
        assert "<TeamsDialog naming={ui.naming} onClose={closeNaming} onSaved={reload} />" in app

    def test_sign_in_is_asked_only_once_the_dialog_is_open(self):
        teams = self.src("frontend/viewer/Teams.jsx")
        dialog = teams[teams.index("export function TeamsDialog("):teams.index("function TeamsForm(")]
        assert "whoIsSignedIn" not in dialog and "accountsOn" not in dialog
        assert "{naming && <TeamsForm key={naming.opened}" in dialog
        form = teams[teams.index("function TeamsForm("):]
        assert "await accountsOn()" in form and "await whoIsSignedIn()" in form

    def test_a_save_asks_for_a_fresh_token_then_shows_the_game_again(self):
        teams = self.src("frontend/viewer/Teams.jsx")
        save = teams[teams.index("const save = async"):]
        save = save[:save.index("\n  };")]
        assert "await whoIsSignedIn()" in save
        assert "{ red: red.trim(), yellow: yellow.trim()," in save
        assert "short_handed:" in save and "sendTeams(naming.sourceId, body, who.token)" in save
        assert save.index("await onSaved()") < save.index("onClose()")

    def test_a_name_is_as_long_as_the_server_takes(self):
        teams = self.src("frontend/viewer/Teams.jsx")
        assert teams.count("maxLength={60}") == 2

    def role(self, status, reply, *, throws=False):
        """fetchRole against a stubbed fetch: what it asked for, and the role
        ("not known" standing in for undefined, which JSON drops)."""
        module = (self.ROOT / "frontend/runtime/teams.mjs").as_uri()
        fetch = ("async () => { throw new Error('offline'); }" if throws else
                 f"async (url, init) => {{ asked.push({{url, init}});"
                 f" return {{ ok: {str(200 <= status < 300).lower()}, status: {status},"
                 f" json: async () => ({json.dumps(reply)}) }}; }}")
        script = (f"const asked = [];\nglobalThis.fetch = {fetch};\n"
                  f"const M = await import({module!r});\n"
                  "const role = await M.fetchRole('tok');\n"
                  "console.log(JSON.stringify({asked,"
                  " role: role === undefined ? 'not known' : role}));")
        proc = subprocess.run([node, "--input-type=module", "-e", script],
                              capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_the_role_is_asked_of_me_with_the_token(self):
        got = self.role(200, {"user": {"id": "u", "role": "validated"}, "teams": []})
        (call,) = got["asked"]
        assert call["url"] == "/api/me"
        assert call["init"]["headers"]["Authorization"] == "Bearer tok"
        assert got["role"] == "validated"

    def test_no_role_is_null_and_no_answer_is_not_known(self):
        assert self.role(200, {"user": {"id": "u", "role": None}})["role"] is None
        assert self.role(200, {"user": {"id": "u"}})["role"] is None
        assert self.role(500, {"detail": "boom"})["role"] == "not known"
        assert self.role(0, {}, throws=True)["role"] == "not known"

    def test_validated_accounts_and_admins_may_name_teams(self):
        module = (self.ROOT / "frontend/runtime/teams.mjs").as_uri()
        script = (f"const M = await import({module!r});\n"
                  "console.log(JSON.stringify(['validated', 'admin', null, undefined, 'owner']"
                  ".map(M.canNameTeams)));")
        proc = subprocess.run([node, "--input-type=module", "-e", script],
                              capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr
        assert json.loads(proc.stdout.strip()) == [True, True, False, False, False]

    def test_an_account_not_yet_validated_is_told_so_not_shown_the_form(self):
        teams = self.src("frontend/viewer/Teams.jsx")
        form = teams[teams.index("function TeamsForm("):]
        assert "await fetchRole(who.token)" in form
        assert "canNameTeams(role)" in form
        assert "Naming teams is open to accounts the site has validated." in form

    def test_the_dialog_says_who_may_change_them(self):
        teams = " ".join(self.src("frontend/viewer/Teams.jsx").split())
        assert "Validated accounts can change them" in teams
        assert "Anyone signed in can change them" not in teams

    def test_the_game_list_offers_naming_only_to_those_who_may(self):
        games = self.src("frontend/site/Games.jsx")
        assert "canNameTeams(me.data?.user?.role)" in games
        draw = games[games.index("function Draw("):games.index("/* The catalogue opens")]
        assert "<TeamsCell game={g} onSaved={onSaved} editable={own && canName} />" in draw

    def test_on_the_desktop_it_sits_after_i_played(self):
        css = self.src("src/curling_score/viewer/style.css")
        assert "header #teamsBtn   { order: 14; }" in css


class TestTheUsersPage:
    """/users: an admin's list of accounts, with a box to validate each."""
    ROOT = Path(__file__).resolve().parents[1]

    def src(self, rel):
        return (self.ROOT / rel).read_text()

    def test_it_is_a_page_of_the_site(self):
        main = self.src("frontend/site/main.jsx")
        assert 'import { Users } from "./Users.jsx";' in main
        assert "users: Users" in main

    def test_only_an_admin_is_shown_the_way_there(self):
        mine = self.src("frontend/site/Mine.jsx")
        assert ('me.data?.user?.role === "admin"\n'
                '                         ? [["/users", "Users"], ["/playlists", "Playlists"]] : []'
                ) in mine

    def test_a_box_validates_or_unvalidates_and_an_admin_has_none(self):
        page = self.src("frontend/site/Users.jsx")
        assert "`/api/admin/users/${encodeURIComponent(u.id)}/role`" in page
        assert 'method: "PUT"' in page
        assert 'JSON.stringify({ role: on ? "validated" : null })' in page
        assert 'u.role === "admin" ?' in page

    def test_anyone_else_is_told_it_is_for_admins(self):
        page = self.src("frontend/site/Users.jsx")
        assert "users.error?.status === 403" in page


class TestThePlaylistsPage:
    """/playlists: the playlists the site watches, for an admin to keep. Its
    words and its form are core/playlists.mjs (tests/test_playlists_js.py)."""
    ROOT = Path(__file__).resolve().parents[1]

    def src(self, rel):
        return (self.ROOT / rel).read_text()

    def test_it_is_a_page_of_the_site(self):
        main = self.src("frontend/site/main.jsx")
        assert 'import { Playlists } from "./Playlists.jsx";' in main
        assert "playlists: Playlists" in main

    def test_the_users_page_and_it_lead_to_each_other(self):
        assert '["/playlists", "Playlists"]' in self.src("frontend/site/Users.jsx")
        assert '["/users", "Users"]' in self.src("frontend/site/Playlists.jsx")

    def test_it_makes_the_admin_tokens_calls_and_never_the_poll(self):
        page = self.src("frontend/site/Playlists.jsx")
        assert 'const API = "/api/admin/playlists";' in page
        assert "const one = id => `${API}/${encodeURIComponent(id)}`;" in page
        assert 'send(adding ? "POST" : "PATCH", adding ? API : one(initial.id), body)' in page
        assert 'call("PATCH", { enabled: !pl.enabled }' in page
        assert 'call("DELETE", null' in page
        assert "poll-playlists" not in page

    def test_removing_asks_first_and_says_what_it_forgets(self):
        page = self.src("frontend/site/Playlists.jsx")
        start = page.index("{removing ? (")
        asking = page[start:page.index(") : (", start)]
        assert "Removing it forgets which videos it has seen." in asking
        assert 'call("DELETE"' in asking and page.count('call("DELETE"') == 1
        assert "onClick={() => setRemoving(true)}>Remove</button>" in page

    def test_anyone_else_is_told_it_is_for_admins(self):
        assert "got.error?.status === 403" in self.src("frontend/site/Playlists.jsx")


class TestTheShotsPage:
    """My shots lays out core's words and numbers; it computes none."""
    ROOT = Path(__file__).resolve().parents[1]

    def src(self, rel):
        return (self.ROOT / rel).read_text()

    def test_the_page_does_no_arithmetic_of_its_own(self):
        for rel in ("frontend/site/Shots.jsx", "frontend/site/ShotCharts.jsx"):
            page = self.src(rel)
            assert "toFixed" not in page and "Math." not in page, rel

    def test_games_load_a_few_at_a_time_and_only_once_each(self):
        page = self.src("frontend/site/Shots.jsx")
        assert "const AT_ONCE = 3;" in page
        assert "for (let i = 0; i < AT_ONCE; i++) worker();" in page
        assert "!asked.current.has(playKey(p))" in page
        assert "`${reportUrl(p.source_id, player)}/doc`" in page

    def test_changing_a_play_from_here_sends_no_path(self):
        page = self.src("frontend/site/Shots.jsx")
        assert 'send("PUT", { color, slot: Number(slot) })' in page
        assert 'send("DELETE")' in page

    def test_every_signed_in_page_links_to_it(self):
        assert '<a href="/shots">My shots</a>' in self.src("frontend/site/ui.jsx")


class TestTheGameFilter:
    """The words at the top of My shots when only some games are in it."""

    def plays(self, n):
        days = ["2026-09-29", "2026-09-22", "2026-09-15", "2026-09-08", "2026-09-01", "2026-08-25"]
        them = ["Grant", "Thistles", "Brooms", "Hackers", "Hogs", "Rocks"]
        return [play(source_id=f"s{i}", played_at=f"{days[i]}T23:30:00+00:00",
                     teams={"red": "Dimmit", "yellow": them[i]}) for i in range(n)]

    def text(self, plays, off):
        return run_js(f"out(pickedText({json.dumps(plays)}, new Set({json.dumps(off)}), {DAY}));")

    def test_every_game(self):
        t = self.text(self.plays(6), [])
        assert (t["label"], t["names"], t["all"]) == ("All 6 games", "", True)
        assert t["intro"] == ("Every rock you threw in all 6 games, by shot and by turn."
                              " The turn is read from the way the rock curled and the hack you threw from.")

    def test_some_games_are_named_up_to_three(self):
        t = self.text(self.plays(6), ["s3", "s4", "s5"])
        assert t["label"] == "3 of 6 games" and t["on"] == 3
        assert t["names"] == "09-29 v Grant, 09-22 v Thistles, 09-15 v Brooms"
        assert t["intro"].startswith("Every rock you threw in 3 of 6 games,")
        assert self.text(self.plays(6), ["s5"])["names"] == ""

    def test_no_games(self):
        t = self.text(self.plays(6), [f"s{i}" for i in range(6)])
        assert (t["label"], t["names"], t["on"]) == ("No games", "", 0)
        assert t["intro"] == "Pick at least one game to see your rocks."

    def test_one_game_reads_as_one(self):
        t = self.text(self.plays(1), [])
        assert t["label"] == "1 game" and "in 1 game," in t["intro"]

    def test_a_game_left_out_that_is_no_longer_listed_counts_for_nothing(self):
        assert self.text(self.plays(2), ["gone"])["label"] == "All 2 games"

    def test_a_row_of_the_list(self):
        p = play(slot=3, played_at="2026-09-15T23:30:00+00:00")
        got = run_js(f"const p = {json.dumps(p)};"
                     f"out([pickerRow(p, undefined, {DAY}), pickerRow(p, {{rows: [1, 2]}}, {DAY}),"
                     f" pickerRow(p, {{rows: [1]}}, {DAY}).rocks, pickerRow(p, {{failed: true}}, {DAY}).rocks,"
                     f" pickerRow({{...p, teams: {{}}}}, undefined, {DAY}).against]);")
        assert got[0] == {"when": "09-15", "against": "v Grant", "position": "Third", "rocks": "…"}
        assert got[1]["rocks"] == "2 rocks" and got[2] == "1 rock" and got[3] == "–"
        assert got[4] == "sheet 3"

    def test_game_text_reads_as_it_did(self):
        got = run_js(f"const p = {json.dumps(play())};"
                     f"out([gameText(p, {DAY}), gameText({{...p, teams: {{}}}}, {DAY}),"
                     f" gameText({{...p, teams: {{}}, sheet: null}}, {DAY})]);")
        assert got == ["09-29 v Grant", "09-29 · sheet 3", "09-29"]


class TestTheGameFilterOnThePage:
    """The filter leaves games out of the report, and remembers which."""
    ROOT = Path(__file__).resolve().parents[1]

    def page(self):
        return (self.ROOT / "frontend/site/Shots.jsx").read_text()

    def test_the_groups_draw_only_on_the_games_left_in(self):
        page = self.page()
        assert "live.filter(p => !off.has(p.source_id))" in page
        assert "[plays, rocks, off]" in page

    def test_what_is_kept_is_the_games_left_out_and_storage_may_fail(self):
        page = self.page()
        load = page[page.index("function loadOff("):page.index("function saveOff(")]
        save = page[page.index("function saveOff("):page.index("function GamePicker(")]
        assert "try {" in load and "catch" in load and "Array.isArray(ids)" in load
        assert "try {" in save and "catch" in save
        assert 'const OFF = "curlchart:shots-off";' in page

    def test_it_shuts_like_a_menu(self):
        page = self.page()
        picker = page[page.index("function GamePicker("):]
        assert 'e.key === "Escape"' in picker and '"pointerdown"' in picker
        assert "aria-expanded={open}" in picker

    def test_a_game_left_out_says_so_in_the_list(self):
        page = self.page()
        assert "left={off.has(p.source_id)}" in page
        assert "Not in the report" in page


class TestATeammatesShots:
    """A teammate sharing theirs is read at /shots?player=<id>: the same
    report from their plays, in their name, with nothing to change."""
    ROOT = Path(__file__).resolve().parents[1]

    def page(self):
        return (self.ROOT / "frontend/site/Shots.jsx").read_text()

    def test_the_words_name_them(self):
        p = [play(source_id="s0")]
        got = run_js(f"const p = {json.dumps(p)};"
                     f"out([pickedText(p, new Set(), {DAY}, 'Sam').intro,"
                     f" pickedText(p, new Set(['s0']), {DAY}, 'Sam').intro]);")
        assert got == ["Every rock Sam threw in 1 game, by shot and by turn."
                       " The turn is read from the way the rock curled and the hack they threw from.",
                       "Pick at least one game to see Sam's rocks."]

    def test_where_each_report_reads_from(self):
        got = run_js(
            f"const P = await import({(self.ROOT / 'frontend/runtime/plays.mjs').as_uri()!r});"
            "out([P.playsUrl(null), P.playsUrl('u/1'), P.reportUrl('g 1', null), P.reportUrl('g', 'u1')]);")
        assert got == ["/api/me/plays", "/api/players/u%2F1/plays", "/api/me/plays/g%201",
                       "/api/players/u1/plays/g"]

    def test_the_switch_and_the_footer_say_who_can_see_it(self):
        got = run_js("out([sharingText(null), sharingText({share: false, teams: ['Thistles']}),"
                     " sharingText({share: true, teams: ['Thistles', 'Brooms', 'Hogs']})]);")
        assert got[0]["footer"] == "Only you can see which games you played and this page."
        assert got[1]["label"] == "Let my teammates see this page"
        assert got[1]["note"] == ("Members of Thistles would see your games and rocks here,"
                                  " but not change them.")
        assert got[1]["footer"] == got[0]["footer"]
        assert got[2]["note"] == "Members of Thistles, Brooms and Hogs can see your games and rocks here."
        assert got[2]["footer"] == ("You and members of Thistles, Brooms and Hogs can see this page."
                                    " Only you can change it.")

    def test_a_teammates_report_has_nothing_to_change(self):
        page = self.page()
        row = page[page.index("function PlayRow("):page.index("function Whose(")]
        assert "{!readOnly && (" in row
        assert row.index("{!readOnly && (") < row.index('send("DELETE")')
        assert "readOnly={Boolean(theirs)}" in page

    def test_its_own_filter_and_only_your_own_switch(self):
        page = self.page()
        assert "const offKey = player => (player ? `${OFF}:${player}` : OFF);" in page
        assert "{!theirs && sharing.data?.teams.length > 0 && (" in page

    def test_your_own_id_in_the_link_is_your_own_report(self):
        assert "asked && user && asked !== user.uid ? asked : null" in self.page()


class TestClipsOnTheShotsPage:
    """My shots plays a clip of each rock that has one; core says where the
    clips stand, the page only lays it out."""
    ROOT = Path(__file__).resolve().parents[1]

    def test_each_row_knows_its_rock(self):
        mine = aimed(13, "red", 4)
        mine["rock"] = "0.1.13"
        d = doc([mine, aimed(14, "yellow", 4), aimed(15, "red", 4)])
        d["schema_version"] = 8
        got = run_js(f"out(playerRocks({json.dumps(play())}, {json.dumps(d)}).rows"
                     ".map(r => r.rock));")
        assert got == ["0.1.13", None]

    def test_only_a_made_clip_plays(self):
        clips = {"rocks": {"0.1.13": {"clip": True, "overlay": [{"t0": 0, "t1": 1}]},
                           "0.1.15": {"clip": None, "reason": "no_release"},
                           "0.1.11": {"clip": False}}, "pending": True}
        got = run_js(f"const c = {json.dumps(clips)};"
                     "out(['0.1.13', '0.1.15', '0.1.11', null].map(k => rockClip({rock: k}, c))"
                     ".concat([rockClip({rock: '0.1.13'}, undefined)]));")
        assert got == [{"clip": True, "overlay": [{"t0": 0, "t1": 1}]}, None, None, None, None]

    @pytest.mark.parametrize("states,want", [
        ([{"clip": False}, {"clip": True}], "Clips on their way…"),
        ([None, {"clip": True}], "Clips on their way…"),
        ([{"clip": True}, {"clip": True}], "2 clips"),
        ([{"clip": True}, {"clip": None, "reason": "no_release"}], "1 of 2 clips"),
        ([{"clip": None, "reason": "video_gone"}] * 2, "No clips: the video is no longer kept"),
        ([{"clip": None, "reason": "no_release"}, {"clip": None, "reason": "video_gone"}],
         "No clips"),
    ])
    def test_the_games_table_says_where_they_stand(self, states, want):
        rows = [{"rock": f"0.1.{n}"} for n in range(len(states))]
        clips = {"rocks": {f"0.1.{n}": s for n, s in enumerate(states) if s is not None}}
        assert run_js(f"out(clipText({json.dumps(rows)}, {json.dumps(clips)}));") == want

    def test_with_clips_off_it_says_nothing(self):
        assert run_js("out([clipText([{rock: '0.1.1'}], undefined), clipText([], {rocks: {}})]);") \
            == [None, None]

    def test_the_page_polls_only_while_clips_are_on_their_way(self):
        page = (self.ROOT / "frontend/site/Shots.jsx").read_text()
        assert "const CLIP_POLL_MS = 15000;" in page
        assert "`${reportUrl(p.source_id, player)}/clips`" in page
        assert "?.clips?.pending" in page

    def test_practice_and_my_shots_share_one_player(self):
        for rel in ("frontend/site/Practice.jsx", "frontend/site/Shots.jsx"):
            assert 'import { ClipPlayer } from "./clip.jsx";' in (self.ROOT / rel).read_text()
        assert "function ClipLines" not in (self.ROOT / "frontend/site/Practice.jsx").read_text()

    def test_clip_is_the_filled_button_beside_an_outlined_watch(self):
        page = (self.ROOT / "frontend/site/Shots.jsx").read_text()
        table = page[page.index("function RockTable"):page.index("function Group")]
        assert 'className="primary"' in table and "▶ Clip" in table
        assert '<a className="btn watch" href={r.href}>Watch ↗</a>' in table
        css = (self.ROOT / "src/curling_score/service/static/site.css").read_text()
        assert "#shots table.rocks td.actions a.btn.watch { border:2px solid var(--ink);" in css
        # On a phone every rocks cell is made a block by `#shots table.rocks
        # td` (1,1,2), which stacked Watch under Clip; the actions cell needs
        # a rule that outranks it, after it, to sit them side by side.
        phone = css[css.index("@media (max-width: 640px) {\n  #shots table"):]
        block = phone.index("#shots td, #shots table.rocks td { display:block;")
        assert phone.index("#shots table.rocks td.actions { display:flex;") > block

    def test_one_clip_is_open_at_a_time_across_groups(self):
        page = (self.ROOT / "frontend/site/Shots.jsx").read_text()
        table = page[page.index("function RockTable"):page.index("function Group")]
        assert "useState" not in table
        assert "const [openClip, setOpenClip] = useState(null);" in page
        assert "setOpenClip={setOpenClip}" in page


class TestRotationFigure:
    """The rock's spin, rpm at release, in-turn or out-turn from the curl and
    the hack: in-turn when it curls away from its hack's side."""

    DOC9 = {"schema_version": 9}

    def rock(self, curl="right", start_x=-0.23, rpm=10.6, **kw):
        line = {"start": {"x": start_x, "y": 38.07},
                "at_hog": {"x": -0.757, "offset_m": -0.163},
                "at_broom": {"x": -2.359, "miss_m": -0.712},
                "side": "wide", "curl": curl, "confirmed": True,
                "hog_path": [[28.35, -0.757], [25.0, -0.95]],
                "path": [[20.0, -1.18], [1.35, -1.15]], "fit": {"n": 53, "rms_m": 0.004}}
        base = dict(target_broom={"x": -1.647, "y": 0.17}, long_split_s=13.79,
                    delivered_stone_index=0, stones=[{"color": "yellow", "x": -1.1529, "y": 1.3486}],
                    line=line, spin=None if rpm is None else {"rpm": rpm, "rpm_avg": rpm - 0.4,
                                                               "turns_in_view": 1.8, "samples": 74})
        base.update(kw)
        return shot(11, "yellow", "third", **base)

    def fig(self, s, doc=None):
        got = run_js(f"out(lineFigures({json.dumps(s)}, {json.dumps(doc or self.DOC9)}));")
        return {f["key"]: f for f in got["figures"]}, [f["key"] for f in got["figures"]]

    def test_left_hack_curling_right_is_an_in_turn(self):
        f, keys = self.fig(self.rock(curl="right", start_x=-0.23))
        assert (f["rotation"]["label"], f["rotation"]["value"], f["rotation"]["note"]) == (
            "Rotation", "11 rpm", "in-turn")
        assert keys.index("rotation") == keys.index("weight") + 1

    def test_left_hack_curling_left_is_an_out_turn(self):
        f, _ = self.fig(self.rock(curl="left", start_x=-0.23))
        assert f["rotation"]["note"] == "out-turn"

    def test_right_hack_mirrors_it(self):
        assert self.fig(self.rock(curl="left", start_x=0.20))[0]["rotation"]["note"] == "in-turn"
        assert self.fig(self.rock(curl="right", start_x=0.20))[0]["rotation"]["note"] == "out-turn"

    def test_no_curl_says_the_turn_is_not_known_but_keeps_the_rpm(self):
        f, _ = self.fig(self.rock(curl=None))
        assert (f["rotation"]["value"], f["rotation"]["note"]) == ("11 rpm", "turn not known")

    def test_a_rock_without_a_line_still_shows_its_rpm(self):
        f, _ = self.fig(self.rock(line=None))
        assert (f["rotation"]["value"], f["rotation"]["note"]) == ("11 rpm", "turn not known")

    def test_a_rock_not_read_says_so(self):
        f, _ = self.fig(self.rock(rpm=None))
        assert (f["rotation"]["value"], f["rotation"]["note"]) == ("–", "not read")

    def test_a_chart_from_before_spin_has_no_rotation_figure(self):
        _, keys = self.fig(self.rock(), {"schema_version": 8})
        assert "rotation" not in keys

    def ranged(self, lo, hi):
        s = self.rock()
        s["spin"] = {"rpm": (lo + hi) / 2, "rpm_avg": (lo + hi) / 2, "turns_in_view": 0.43,
                     "samples": 76, "rpm_range": [lo, hi]}
        return s

    def test_a_rock_timed_only_as_a_range_shows_the_range(self):
        f, _ = self.fig(self.ranged(3.1, 4.6))
        assert (f["rotation"]["value"], f["rotation"]["note"]) == ("3–5 rpm", "in-turn")

    def test_a_range_that_rounds_to_one_number_shows_one(self):
        f, _ = self.fig(self.ranged(3.6, 4.4))
        assert f["rotation"]["value"] == "4 rpm"

    def test_turn_name_on_its_own(self):
        got = run_js("out([turnName({hack: {side: 'left'}}, 'right'), turnName({hack: {side: 'left'}}, 'left'),"
                     " turnName({hack: {side: 'right'}}, 'left'), turnName({}, 'right'),"
                     " turnName({hack: {side: 'left'}}, null)]);")
        assert got == ["in-turn", "out-turn", "in-turn", None, None]


class TestTheRocksTableColumns:
    """My shots' table spans games from before spin was read (no Rotation
    figure) and after: its columns are every figure any row has, so an older
    row's Curl does not slide under Rotation."""
    ROOT = Path(__file__).resolve().parents[1]
    ROCK = TestRotationFigure().rock()

    def columns(self, *versions):
        rows = ", ".join(f"{{figures: lineFigures(s, {{schema_version: {v}}}).figures}}" for v in versions)
        return run_js(f"const s = {json.dumps(self.ROCK)}; out(tableColumns([{rows}]).map(c => c.label));")

    def test_newest_first_or_oldest_first_the_columns_are_the_same(self):
        want = ["At the broom", "Hack", "At the hog line", "Weight", "Rotation", "Curl", "Came to rest"]
        assert self.columns(9, 8) == want
        assert self.columns(8, 9) == want

    def test_only_older_games_have_no_rotation_column(self):
        assert "Rotation" not in self.columns(8, 8)

    def test_the_table_lays_each_row_out_by_column(self):
        page = (self.ROOT / "frontend/site/Shots.jsx").read_text()
        table = page[page.index("function RockTable"):page.index("function Group")]
        assert "const cols = tableColumns(rows);" in table
        assert "rows[0].figures" not in table
        assert "colSpan={cols.length + 5}" in table
        # The gap an older row leaves takes no room on a phone, which labels
        # every cell itself.
        css = (self.ROOT / "src/curling_score/service/static/site.css").read_text()
        phone = css[css.index("@media (max-width: 640px) {\n  #shots table"):]
        block = phone.index("#shots td, #shots table.rocks td { display:block;")
        assert phone.index("#shots table.rocks td.nofig { display:none; }") > block


class TestTheDeliveryDialog:
    SRC = Path(__file__).resolve().parents[1] / "frontend/viewer/DeliveryDialog.jsx"

    def test_its_words_are_the_spec_s(self):
        src = self.SRC.read_text()
        for needle in (
            'hint = "Esc or click outside to close"', "box = BIGDELIVERYBOX", "speedBox = SPEEDBOX",
            "From above, thrower at the left · across ×{g.stretch} · a dot every {g.every.short}, "
            "dark at the push-off to gold past the hog line · ticks every 2 in",
            "Speed along the same axis, smoothed over a second. Ripples smaller than about 0.2 m/s "
            "come from the camera, not the throw.",
            "The speed could not be read for this rock.",
            "deliveryGeometry(shot, box)", "speedGeometry(speed, speedBox, shot)",
        ):
            assert needle in src, needle
        app = (Path(__file__).resolve().parents[1] / "frontend/viewer/App.jsx").read_text()
        assert 'hint={tablet ? "Tap outside to close" : undefined}' in app

    def test_it_is_a_modal_dialog_that_keeps_its_rock(self):
        src = self.SRC.read_text()
        assert 'id="deliveryDialog"' in src and "showModal()" in src
        assert "key={delivering.opened}" in src
        # A click on the backdrop lands on the dialog itself.
        assert "e.target === e.currentTarget" in src
        # The padding is the wrapper's, so the backdrop is the only thing that reaches the dialog.
        assert '<div className="dd-body">' in src
        css = (Path(__file__).resolve().parents[1] / "src/curling_score/viewer/style.css").read_text()
        rule = css[css.index("#deliveryDialog, #thinkingDialog, #endDialog {"):].split("}")[0]
        assert "padding: 0" in rule
        assert ".dd-body { padding: 16px 24px 18px" in css


class TestTheThinkingDialog:
    SRC = Path(__file__).resolve().parents[1] / "frontend/viewer/ThinkingDialog.jsx"
    CSS = VIEWER / "style.css"

    def test_it_is_the_panel_s_chart_in_a_dialog(self):
        src = self.SRC.read_text()
        for needle in ('id="thinkingDialog"', "showModal()", "<ThinkingBars", "<ThinkingChart", "<ClockKey",
                       "box={TALLBOX}", "No thinking time was measured in this game."):
            assert needle in src, needle

    def test_its_content_sits_in_the_padded_wrapper(self):
        # The padding is the wrapper's, so a click inside the card never reaches the <dialog>
        # element and only the backdrop closes it.
        src = self.SRC.read_text()
        assert '<div className="dd-body">' in src
        assert "e.target === e.currentTarget" in src
        assert src.index('<div className="dd-body">') < src.index('className="dd-head"')

    def test_the_clock_s_styles_apply_in_it(self):
        css = self.CSS.read_text()
        for rule in ("#clock .key {", "#clock .key i.sw {", "#clock .key button.linky {"):
            sel = rule[:-2]
            assert f"{sel}, #thinkingDialog {sel[len('#clock '):]}" in css, sel

    def test_the_header_button_sits_before_report(self):
        css = self.CSS.read_text()
        wide = css[css.index("@media (min-width: 1500px)"):]
        assert "header #thinkBtn  { order: 11; }" in wide
        assert "header #reportBtn { order: 12; }" in wide
        app = (Path(__file__).resolve().parents[1] / "frontend/viewer/App.jsx").read_text()
        assert 'id="thinkBtn"' in app and "<ThinkingDialog" in app


class TestTheEndDialog:
    """Every rock's house in the end the cursor is in, from the desktop header
    (2026-10-21)."""
    ROOT = Path(__file__).resolve().parents[1]
    CSS = VIEWER / "style.css"

    def src(self, rel):
        return (self.ROOT / rel).read_text()

    def test_it_is_a_modal_dialog_of_the_end_it_was_opened_on(self):
        src = self.src("frontend/viewer/EndDialog.jsx")
        for needle in ('id="endDialog"', "showModal()", "e.target === e.currentTarget",
                       '<div className="dd-body">', "view.ends[ending.ei]", "<HouseThumb shot={s}",
                       "No rocks were found in this end."):
            assert needle in src, needle
        assert src.index('<div className="dd-body">') < src.index('className="dd-head"')

    def test_a_house_goes_to_its_rock_and_closes_it(self):
        src = self.src("frontend/viewer/EndDialog.jsx")
        assert "actions.goToBarFromReport({ ei: ending.ei, si }); ref.current?.close();" in src
        app = self.src("frontend/viewer/App.jsx")
        assert "at={ui.ending?.ei === ui.ei ? ui.si : null}" in app

    def test_the_small_houses_name_ids_of_their_own(self):
        # Sixteen drawings on one page: a shared clipPath or hatch id would be
        # sixteen duplicates, and #house is the editor's (and the saved image's).
        house = self.src("frontend/viewer/House.jsx")
        thumb = house[house.index("export function HouseThumb("):house.index("function Unknown(")]
        assert "useId()" in thumb and 'id="house"' not in thumb and "<Stones " not in thumb
        assert "<StoneDiscs " in thumb and 'viewBox={houseViewBox("full")}' in thumb

    def test_it_is_the_desktop_s(self):
        app = self.src("frontend/viewer/App.jsx")
        assert "const endBtn = tablet ? null : (" in app
        assert "{tablet ? null : (\n        <EndDialog" in app
        css = self.CSS.read_text()
        assert "@media (max-width: 640px) { #endBtn { display: none; } }" in css
        assert "header #endBtn     { order: 11; }" in css
        # Its 11 is shared with Thinking in the wide layout: the source puts it first.
        assert app.index("{endBtn}") < app.index("{tablet ? null : <>{thinkBtn}{reportBtn}</>}")

    def test_two_rows_fit_the_screen(self):
        css = self.CSS.read_text()
        assert "grid-template-columns: repeat(var(--cols), minmax(0, 1fr));" in css
        assert "max-height: calc((100vh - 240px) / 2);" in css
        src = self.src("frontend/viewer/EndDialog.jsx")
        assert '"--cols": Math.ceil(e.shots.length / 2)' in src


class TestTheWideMoreMenu:
    """In the wide layout ⬇, I played…, Teams… and Clip… are behind ⋯, which
    leaves the header room for Whole end at 1500 px (2026-10-21). Everywhere
    else they stay in #menu, where they always were."""
    ROOT = Path(__file__).resolve().parents[1]

    def test_the_four_leave_the_menu_only_when_wide(self):
        app = (self.ROOT / "frontend/viewer/App.jsx").read_text()
        menu = app[app.index('<div id="menu"'):app.index("{/* The house as a picture to keep")]
        assert "{wide ? null : downloadBtn}" in menu
        assert "{wide ? null : <>{playedBtn}{teamsBtn}{clipBtn}</>}" in menu
        assert '{wide && ui.menu === "open" ? (' in app
        assert "{downloadBtn}{playedBtn}{teamsBtn}{clipBtn}" in app
        # Nothing to offer, no ⋯.
        assert "{wide && !more ? null : (" in app

    def test_it_closes_like_the_phone_s(self):
        app = (self.ROOT / "frontend/viewer/App.jsx").read_text()
        assert '|| ev.target.closest("#more") || ev.target.closest("#playback")) return;' in app
        assert '...(wide ? { menu: "" } : {})' in app

    def test_wide_shows_the_button_and_drops_the_list_under_it(self):
        css = (VIEWER / "style.css").read_text()
        wide = css[css.index("@media (min-width: 1500px)"):]
        wide = wide[:wide.index("/* --- portrait iPad")]
        assert "header > #menuBtn { display: inline-grid; place-items: center; order: 20; }" in wide
        assert "header > #more { position: absolute;" in wide


class TestTheWideLayoutKeepsToItsBranch:
    """Outside the desk layouts the DOM is the base's, byte for byte: what the
    wide and tablet layouts add renders only in App's `wide`, `desk` or
    `tablet` branches, and the panel only in the other. Source pins, so
    neither can drift across quietly."""
    ROOT = Path(__file__).resolve().parents[1]

    @staticmethod
    def side(src, needle):
        """The flag and the side of the innermost line-start `{flag ? … : …}`
        the needle sits in, flag being wide, desk or tablet: "desk", "!desk",
        "tablet"…; None outside every one. A ternary whose first line ends in
        "(" runs to its `) : null}` or `) : (` … `)}` at the same indent; any
        other is one line."""
        i = src.index(needle)
        found = [m for m in re.finditer(r"^( *)\{(!?)(wide|desk|tablet) \? ", src, re.M) if m.start() < i]
        for m in reversed(found):                     # the nearest first: the innermost holding it
            indent, eol = m.group(1), src.index("\n", m.start())
            if src[eol - 1] != "(":                   # a one-line ternary
                if i > eol:
                    continue
                first = i < src.index(" : ", m.start())
            else:
                els = src.index(f"\n{indent}) : ", m.start())
                end = els if src.startswith(") : null}", els + 1 + len(indent)) else src.index(f"\n{indent})}}", els)
                if i > end:
                    continue
                first = i < els
            flag = m.group(3)
            return flag if first != (m.group(2) == "!") else f"!{flag}"
        return None

    def test_each_piece_is_in_its_layout_s_branch(self):
        app = (self.ROOT / "frontend/viewer/App.jsx").read_text()
        for needle, want in (("<GradingBar ", "desk"), ("<FiguresCard ", "desk"), ("<LineCard ", "wide"),
                             ("<DeliveryDialog ", "desk"), ("<ThinkingDialog ", "desk"),
                             ("<ChartPanel ", "!desk"), ('id="detailCard"', "!desk"),
                             ('id="houseOpen"', "tablet")):
            assert app.count(needle) == 1, needle
            assert self.side(app, needle) == want, needle
        assert self.side(app, "<FlagDialog ") is None
        # Next rock is a const passed to the bar as a prop, so it is pinned by
        # the prop rather than by a block (ruling R2).
        assert "next={tablet ? nextRockBtn : null}" in app

    def test_a_typed_field_lets_go_and_takes_the_caret_back(self):
        src = (self.ROOT / "frontend/viewer/ChartPanel.jsx").read_text()
        hook = src[src.index("function useTypedField("):src.index("function useOpenGroup(")]
        cleanup = hook[hook.index("useEffect(() => () => {"):]
        assert "busy(false);" in cleanup                     # R9: the rock is not left busy
        assert "refocus = { id: node.id," in cleanup
        mount = hook[:hook.index("useEffect(() => () => {")]
        assert "r.id !== node.id" in mount and "node.focus();" in mount
        assert "ref: el," in hook


class TestTheTabletBranch:
    ROOT = Path(__file__).resolve().parents[1]

    def test_the_resize_handler_knows_the_tablet_has_an_editor(self):
        app = (self.ROOT / "frontend/viewer/App.jsx").read_text()
        assert "if (!phone() && !tabletNow() && (ui.houseMode || ui.sheet !== \"peek\"))" in app

    def test_the_tablet_s_charted_and_enter_are_one(self):
        """Ruling R14: on the iPad, ✔ Charted and Enter run one helper; the
        desktop's Mark charted and Enter keep nextBlankAfter."""
        app = (self.ROOT / "frontend/viewer/App.jsx").read_text()
        assert "const it = chartedNext(queue, view, ui.ei, ui.si);" in app
        assert 'case "Enter": { if (tablet) { chartedTablet(); break; }' in app
        assert "onClick={() => { if (tablet) { chartedTablet(); return; }" in app
        assert app.count("nextBlankAfter(queue, ui.ei, ui.si)") == 4   # n, Enter, Mark charted, the pill

    def test_a_turn_opens_a_sheet_only_for_this_rock_s_field(self):
        """R8: the bar opens the sheet holding the caret's field only when the
        field it left was this rock's."""
        src = (self.ROOT / "frontend/viewer/ChartPanel.jsx").read_text()
        bar = src[src.index("export function GradingBar("):src.index("function Detail(")]
        assert "const r = refocus?.owner === shotKey ? refocus : null;" in bar

    def test_no_control_double_tap_zooms(self):
        css = (VIEWER / "style.css").read_text()
        block = css[css.index("@media (any-pointer: coarse) and (orientation: portrait) and (min-width: 700px) "
                              "and (max-width: 1199px) {"):]
        assert "button, select, a, .shot, label, input, textarea { touch-action: manipulation; }" in block

    def test_a_tap_opens_the_editor_on_the_tablet(self):
        src = (self.ROOT / "frontend/viewer/House.jsx").read_text()
        assert "if ((phone() || tabletNow()) && houseMode !== \"edit\")" in src

    def test_the_bar_s_sheets_keep_the_typed_fields(self):
        src = (self.ROOT / "frontend/viewer/ChartPanel.jsx").read_text()
        bar = src[src.index("export function GradingBar("):src.index("function Detail(")]
        for needle in ('id="missSheet"', 'id="noteSheet"', 'id="orderSheet"', "{...miss}", "{...note}",
                       "miss.onChange({ target: { value: r } })"):
            assert needle in bar, needle
