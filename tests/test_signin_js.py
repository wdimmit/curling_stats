"""The sign-in dialog's arithmetic, exercised in node: no Firebase, no page."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

MOD = Path(__file__).resolve().parents[1] / "frontend/site/signinState.mjs"
node = shutil.which("node")
pytestmark = pytest.mark.skipif(node is None, reason="node is not installed")


def js(expr, prelude=""):
    script = (f"const S = await import({MOD.as_uri()!r});\n{prelude}\n"
              f"console.log(JSON.stringify({expr}));")
    proc = subprocess.run([node, "--input-type=module", "-e", script],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


MAP_STORAGE = ("const m = new Map(); const store = {getItem: k => m.has(k) ? m.get(k) : null,"
               " setItem: (k, v) => m.set(k, String(v)), removeItem: k => m.delete(k)};")
BROKEN = ("const store = {getItem() { throw new Error('denied'); },"
          " setItem() { throw new Error('denied'); }, removeItem() { throw new Error('denied'); }};")


class TestRequestIds:
    def test_an_id_is_22_url_safe_characters(self):
        rid = js("S.newRequestId()")
        assert len(rid) == 22 and all(c.isalnum() or c in "-_" for c in rid)

    def test_the_link_carries_only_the_id(self):
        assert js("S.continueUrl('https://chart.example', 'AbCdEfGhIjKlMnOpQrStUv')") == \
            "https://chart.example/signin?r=AbCdEfGhIjKlMnOpQrStUv"

    def test_the_id_is_read_back_off_the_landing_url(self):
        href = "https://chart.example/signin?r=AbCdEfGhIjKlMnOpQrStUv&oobCode=x&mode=signIn"
        assert js(f"S.requestIdOf({href!r})") == "AbCdEfGhIjKlMnOpQrStUv"
        assert js("S.requestIdOf('https://chart.example/signin?r=short')") is None


class TestWhatPeopleType:
    def test_an_email_is_trimmed(self):
        assert js("S.cleanEmail('  Jo@Example.org ')") == "Jo@Example.org"

    @pytest.mark.parametrize("bad", ["", "jo", "jo@", "@x.org", "jo smith@x.org", "a@b@c"])
    def test_not_an_email(self, bad):
        assert js(f"S.cleanEmail({bad!r})") is None

    @pytest.mark.parametrize("typed", ["482913", "482 913", " 482-913 "])
    def test_a_code_however_it_is_pasted(self, typed):
        assert js(f"S.parseCode({typed!r})") == "482913"

    @pytest.mark.parametrize("bad", ["48291", "4829130", "48291a", ""])
    def test_not_a_code(self, bad):
        assert js(f"S.parseCode({bad!r})") is None

    def test_a_code_is_shown_in_two_threes(self):
        assert js("S.formatCode('482913')") == "482 913"


class TestWhereToGoBack:
    @pytest.mark.parametrize("nxt, want", [
        ("/c/abc/#e=2", "/c/abc/#e=2"), ("/join/i_x", "/join/i_x"),
        ("//evil.example/", "/"), ("/\\evil.example", "/"), ("https://evil.example", "/"),
        ("/signin?r=x", "/"), ("", "/"), (None, "/"),
    ])
    def test_only_a_path_on_this_site(self, nxt, want):
        assert js(f"S.safeNext({json.dumps(nxt)})") == want


class TestRemembering:
    P = "{email: 'jo@x.org', id: 'AbCdEfGhIjKlMnOpQrStUv', next: '/mine', at: 1000}"

    def test_remembered_and_recalled(self):
        assert js("S.recall(store)", MAP_STORAGE + f"S.remember(store, {self.P});") == \
            {"email": "jo@x.org", "id": "AbCdEfGhIjKlMnOpQrStUv", "next": "/mine", "at": 1000}

    def test_forgotten(self):
        assert js("S.recall(store)",
                  MAP_STORAGE + f"S.remember(store, {self.P}); S.forget(store);") is None

    def test_storage_that_throws_is_nothing_remembered_and_no_error(self):
        assert js("S.recall(store)", BROKEN + f"S.remember(store, {self.P}); S.forget(store);") is None

    def test_garbage_in_storage_is_nothing(self):
        assert js("S.recall(store)", MAP_STORAGE + "store.setItem(S.PENDING_KEY, '{nope');") is None

    def test_fresh_for_fifteen_minutes(self):
        assert js(f"[S.isFresh({self.P}, 1000 + 15*60*1000 - 1), S.isFresh({self.P}, 1000 + 15*60*1000)]") == [True, False]

    def test_send_again_keeps_the_request(self):
        assert js(f"S.resend({self.P}, 5000)") == \
            {"email": "jo@x.org", "id": "AbCdEfGhIjKlMnOpQrStUv", "next": "/mine", "at": 5000}


class TestTheDialogIsWiredRight:
    ROOT = Path(__file__).resolve().parents[1]

    def src(self, rel):
        return (self.ROOT / rel).read_text()

    def test_firebase_still_lives_only_in_auth_js(self):
        assert "gstatic" not in self.src("frontend/site/signInDialog.js")
        assert "gstatic" not in self.src("frontend/site/signinState.mjs")

    def test_the_dialog_is_loaded_only_when_someone_signs_in(self):
        auth = self.src("frontend/site/auth.js")
        assert 'import("./signInDialog.js")' in auth
        assert 'from "./signInDialog.js"' not in auth
        for rel in ("frontend/viewer", "frontend/runtime", "frontend/core", "frontend/site"):
            for p in (self.ROOT / rel).rglob("*"):
                if p.suffix in (".js", ".mjs", ".jsx") and p.name != "auth.js":
                    assert 'signInDialog.js"' not in p.read_text(), p

    def test_the_link_is_sent_to_come_back_to_signin_and_nothing_else(self):
        auth = " ".join(self.src("frontend/site/auth.js").split())
        assert "handleCodeInApp: true" in auth
        assert "continueUrl(location.origin," in auth

    def test_closing_the_dialog_looks_like_a_shut_popup(self):
        assert '"auth/popup-closed-by-user"' in self.src("frontend/site/signInDialog.js")

    def test_the_chooser_says_both_ways_in(self):
        d = self.src("frontend/site/signInDialog.js")
        for words in ("Continue with Google", "Email me a link", "Check your email",
                      "If the link opens in another app, it shows a code. Type it here:",
                      "That code isn't right.", "That code has run out — send a new link.",
                      "This address signs in with an emailed link.", "Your name",
                      "what your teammates see", "Send again", "Use a different address"):
            assert words in d, words

    def test_a_dismissal_is_not_an_error_in_the_header(self):
        ui = self.src("frontend/site/ui.jsx")
        chip = ui[ui.index("export function IdentityChip"):ui.index("const SIGNED_IN_ONLY")]
        assert '"auth/popup-closed-by-user"' in chip

    def test_a_new_name_redraws_the_header(self):
        assert "profileVersion" in self.src("frontend/site/useAuth.js")
