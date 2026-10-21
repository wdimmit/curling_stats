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

    # Firestore reserves ids that start and end with "__"; the server refuses
    # either end (service/signin.py REQUEST_ID), and so does this.
    @pytest.mark.parametrize("rid", ["__AAAAAAAAAAAAAAAAAA__", "__AAAAAAAAAAAAAAAAAAAA",
                                     "AAAAAAAAAAAAAAAAAAAA__"])
    def test_an_id_firestore_reserves_is_no_id(self, rid):
        assert js(f"S.requestIdOf('https://chart.example/signin?r={rid}')") is None
        assert js("S.requestIdOf('https://chart.example/signin?r=A_BBBBBBBBBBBBBBBBBB_C')") == \
            "A_BBBBBBBBBBBBBBBBBB_C"

    def test_a_draw_that_starts_with_two_underscores_is_drawn_again(self):
        # Sixteen 0xff bytes encode as "_" throughout; zeros as "A".
        assert js("S.newRequestId(b => b.fill(n++ ? 0 : 255))", "let n = 0;") == "A" * 22


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


class TestWhatToSay:
    def test_closing_the_chooser_says_nothing(self):
        assert js("S.signInTrouble({code: 'auth/popup-closed-by-user'})") == ""

    def test_anything_else_says_what_went_wrong(self):
        assert js("S.signInTrouble({code: 'auth/network-request-failed', message: 'm'})") == \
            "auth/network-request-failed"
        assert js("S.signInTrouble(new Error('accounts are not configured'))") == \
            "accounts are not configured"

    def test_a_failure_names_firebases_code_or_the_servers_status(self):
        assert js("S.failed('Couldn\\'t sign in', {code: 'auth/network-request-failed'})") == \
            "Couldn't sign in (auth/network-request-failed). Try again."
        assert js("S.failed('Couldn\\'t sign in', Object.assign(new Error('claim: 500'),"
                  " {status: 500}))") == "Couldn't sign in (500). Try again."
        assert js("S.failed('Couldn\\'t sign in', new Error('x'))") == "Couldn't sign in. Try again."

    def test_email_sign_in_switched_off_in_firebase_is_not_worth_another_try(self):
        assert js("S.emailFailed('Couldn\\'t send the link', {code: 'auth/operation-not-allowed'})") \
            == "Signing in by email isn't available right now."
        assert js("S.emailFailed('Couldn\\'t send the link', {code: 'auth/quota-exceeded'})") == \
            "Couldn't send the link (auth/quota-exceeded). Try again."


RID = "AbCdEfGhIjKlMnOpQrStUv"


class TestTheLandingDecision:
    """signinState.landing: what /signin does with a visit."""
    P = {"email": "jo@x.org", "id": RID, "next": "/mine", "at": 1000}
    LINK = f"https://chart.example/signin?r={RID}&apiKey=k&oobCode=x&mode=signIn"

    def land(self, accounts=True, signedIn=False, isLink=True, pending=P, href=LINK):
        args = {"accounts": accounts, "signedIn": signedIn, "isLink": isLink,
                "pending": pending, "href": href}
        return js(f"S.landing({json.dumps(args)})")

    def test_accounts_off(self):
        assert self.land(accounts=False) == {"is": "off"}

    def test_no_link_and_nobody_signed_in_is_a_sign_in_button(self):
        assert self.land(isLink=False, href="https://chart.example/signin") == {"is": "plain"}

    def test_no_link_and_somebody_signed_in_goes_home(self):
        assert self.land(isLink=False, signedIn=True) == {"is": "go", "next": "/"}

    def test_this_browsers_own_request_with_nobody_signed_in_is_finished(self):
        assert self.land() == {"is": "finish", "next": "/mine"}

    def test_this_browsers_own_request_already_signed_in_goes_on_without_it(self):
        assert self.land(signedIn=True) == {"is": "go", "next": "/mine"}

    def test_another_browsers_link_shows_the_code_even_signed_in(self):
        # A member signed in on the phone who asked on a computer: the
        # computer waits for this code.
        assert self.land(signedIn=True, pending=None) == {"is": "relay"}
        assert self.land(pending=None) == {"is": "relay"}

    def test_a_request_of_this_browsers_with_another_id_is_another_browsers(self):
        other = {**self.P, "id": "ZyXwVuTsRqPoNmLkJiHgFe"}
        assert self.land(pending=other) == {"is": "relay"}
        assert self.land(pending=other, signedIn=True) == {"is": "relay"}

    @pytest.mark.parametrize("href", [
        "https://chart.example/signin?apiKey=k&oobCode=x&mode=signIn",
        "https://chart.example/signin?r=short&apiKey=k&oobCode=x&mode=signIn",
        "https://chart.example/signin?r=__AAAAAAAAAAAAAAAAAAAA&apiKey=k&oobCode=x&mode=signIn",
    ])
    def test_no_usable_id_is_never_finished(self, href):
        rid = (href.split("r=")[1].split("&")[0]) if "r=" in href else RID
        assert self.land(href=href, pending={**self.P, "id": rid}) == {"is": "relay"}
        assert self.land(href=href, pending={**self.P, "id": rid}, signedIn=True) == {"is": "relay"}

    def test_where_to_go_is_a_path_on_this_site(self):
        assert self.land(pending={**self.P, "next": "//evil.example/"}) == \
            {"is": "finish", "next": "/"}


# Just enough of a DOM for signInDialog.js: elements with attributes,
# children, text, listeners and focus, and a <dialog> whose close() fires
# "close" a moment later, as a browser's does.
FAKE_DOM = r"""
class El {
  constructor(tag) { this.tag = tag; this.kids = []; this.parent = null; this.on = {};
    this.cls = new Set(); this.id = ""; this.value = ""; this.disabled = false; this.open = false; }
  setAttribute(k, v) { if (k === "id") this.id = v; if (k === "class") this.cls = new Set(v.split(" ")); }
  get classList() { return { toggle: (c, on) => (on ? this.cls.add(c) : this.cls.delete(c)) }; }
  append(...kids) {
    for (const k of kids) {
      const n = typeof k === "string" ? Object.assign(new El("#text"), { data: k }) : k;
      n.parent = this; this.kids.push(n);
    }
  }
  replaceChildren(...kids) { this.kids = []; this.append(...kids); }
  remove() { if (this.parent) this.parent.kids = this.parent.kids.filter(k => k !== this); }
  get textContent() { return this.tag === "#text" ? this.data : this.kids.map(k => k.textContent).join(""); }
  set textContent(v) { this.kids = []; if (v) this.append(String(v)); }
  *all() { for (const k of this.kids) { yield k; yield* k.all(); } }
  querySelectorAll(sel) { const tags = sel.split(",").map(s => s.trim()); return [...this.all()].filter(n => tags.includes(n.tag)); }
  addEventListener(type, fn) { (this.on[type] ??= []).push(fn); }
  fire(type) {
    const ev = { type, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
    for (const fn of this.on[type] ?? []) fn(ev);
    return ev;
  }
  focus() { document.activeElement = this; }
  select() {}
  showModal() { this.open = true; }
  close() { if (!this.open) return; this.open = false; setTimeout(() => this.fire("close"), 0); }
}
globalThis.document = {
  head: new El("head"), body: new El("body"), activeElement: null,
  createElement: tag => new El(tag),
  getElementById(id) { return [...this.head.all(), ...this.body.all()].find(n => n.id === id) ?? null; },
};
const D = await import(DIALOG);
const tick = (ms = 5) => new Promise(r => setTimeout(r, ms));
const dlg = () => document.getElementById("signInDialog");
const inDlg = () => (dlg() ? [...dlg().all()] : []);
const button = text => inDlg().find(n => n.tag === "button" && n.textContent === text);
const view = () => ({
  open: !!dlg()?.open, title: document.getElementById("signInDialogTitle")?.textContent ?? null,
  buttons: inDlg().filter(n => n.tag === "button" && n.textContent !== "×").map(n => n.textContent),
  email: document.getElementById("signInDialogEmail")?.value ?? null,
  code: !!document.getElementById("signInDialogCode"),
  say: inDlg().find(n => n.cls.has("si-say"))?.textContent ?? null,
  text: dlg()?.textContent ?? "",
});
const typeCode = async c => {
  document.getElementById("signInDialogCode").value = c;
  inDlg().find(n => n.tag === "form").fire("submit");
  await tick();
};
// Escape on an open dialog: "cancel", then a close unless it was refused.
// A second Escape, or Android's Back, may close it with no "cancel" at all.
const escape = () => { if (!dlg().fire("cancel").defaultPrevented) dlg().close(); };
const forceClose = () => dlg().close();
const JO = { uid: "uid-jo", email: "jo@x.org" };
const REQ = { email: "jo@x.org", id: "AbCdEfGhIjKlMnOpQrStUv", next: "/", at: Date.now() };
const calls = [];
// Each claim and finish waits for the test to settle it, so it can act
// while one is under way.
const later = () => { let settle; const p = new Promise((ok, no) => { settle = { ok, no }; }); return [p, settle]; };
const claims = [], finishes = [];
const api = {
  emailLink: true, currentUser: () => null, pending: () => REQ,
  signInWithGoogle: () => new Promise(() => {}),
  sendLink: async email => { calls.push(["send", email]); return { ...REQ, email }; },
  claimCode: (id, code) => { calls.push(["claim", id, code]); const [p, s] = later(); claims.push(s); return p; },
  finishWithLink: (email, link) => { calls.push(["finish", email, link]); const [p, s] = later(); finishes.push(s); return p; },
  onUser: fn => { fn(null, true); return () => {}; },
  needsName: async () => { calls.push(["needsName"]); return true; },
  setName: async n => n,
};
const outcome = p => p.then(u => ({ resolved: u }), e => ({ rejected: e?.code ?? String(e) }));
"""


def dialog(body):
    """Run `body` (top-level await allowed) with the fake DOM and the dialog
    loaded; it ends with out(value)."""
    dialog_mod = MOD.with_name("signInDialog.js")
    script = (f"const DIALOG = {dialog_mod.as_uri()!r};\n{FAKE_DOM}\n"
              "const out = v => console.log(JSON.stringify(v));\n"
              f"{body}\n")
    proc = subprocess.run([node, "--input-type=module", "-e", script],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


class TestTheChooser:
    def test_with_email_links_off_it_is_google_alone(self):
        got = dialog("api.emailLink = false; D.openSignIn(api); await tick(); out(view());")
        assert got["title"] == "Sign in" and got["buttons"] == ["Continue with Google"]
        assert got["email"] is None and got["code"] is False

    def test_with_email_links_on_it_offers_both(self):
        got = dialog("api.pending = () => null; D.openSignIn(api); await tick(); out(view());")
        assert got["buttons"] == ["Continue with Google", "Email me a link"]
        assert got["email"] == ""

    def test_a_request_still_waited_on_opens_at_its_code_box(self):
        # A viewer chart or review page, reloaded while its person was in the
        # mail app, loads auth.js only when they press Sign in again.
        got = dialog("D.openSignIn(api); await tick(); const a = view();"
                     " button('Use a different address').fire('click'); await tick();"
                     " out([a, view()]);")
        assert got[0]["title"] == "Check your email" and got[0]["code"] is True
        assert "We sent a link to jo@x.org." in got[0]["text"]
        assert got[1]["buttons"] == ["Continue with Google", "Email me a link"]
        assert got[1]["email"] == "jo@x.org"

    @pytest.mark.parametrize("why", [
        "api.emailLink = false;",
        "api.currentUser = () => JO;",
        "api.pending = () => ({ ...REQ, at: Date.now() - 15 * 60 * 1000 });",
    ])
    def test_not_at_the_code_box_when(self, why):
        got = dialog(f"{why} D.openSignIn(api); await tick(); out(view());")
        assert got["title"] == "Sign in" and got["code"] is False

    def test_email_sign_in_switched_off_in_firebase_says_so_plainly(self):
        got = dialog("api.pending = () => null;"
                     " api.sendLink = async () => { throw { code: 'auth/operation-not-allowed' }; };"
                     " D.openSignIn(api); await tick();"
                     " document.getElementById('signInDialogEmail').value = 'jo@x.org';"
                     " inDlg().find(n => n.tag === 'form').fire('submit'); await tick();"
                     " out(view());")
        assert got["say"] == "Signing in by email isn't available right now."


class TestTheCodeBox:
    def test_the_link_is_finished_with_the_address_this_browser_stored(self):
        got = dialog("const p = outcome(D.openSignIn(api, { resume: REQ })); await tick(); await typeCode('482913');"
                     " claims[0].ok({ link: 'L1' }); await tick();"
                     " out(calls);")
        assert got == [["claim", "AbCdEfGhIjKlMnOpQrStUv", "482913"],
                       ["finish", "jo@x.org", "L1"]]

    def test_a_server_failure_names_its_status(self):
        got = dialog("D.openSignIn(api, { resume: REQ }); await tick(); await typeCode('482913');"
                     " claims[0].no(Object.assign(new Error('claim: 503'), { status: 503 }));"
                     " await tick(); out(view().say);")
        assert got == "Couldn't sign in (503). Try again."

    def test_a_finish_that_fails_is_tried_again_with_the_link_already_claimed(self):
        got = dialog("const p = outcome(D.openSignIn(api, { resume: REQ })); await tick(); await typeCode('482913');"
                     " claims[0].ok({ link: 'L1' }); await tick();"
                     " finishes[0].no({ code: 'auth/network-request-failed' }); await tick();"
                     " const say = view().say;"
                     " await typeCode('482913');"
                     " finishes[1].ok(JO); await tick();"
                     " out({ say, calls, open: view().title });")
        assert got["say"] == "Couldn't sign in (auth/network-request-failed). Try again."
        # One claim: the relay gave the link up when it was claimed.
        assert got["calls"] == [["claim", "AbCdEfGhIjKlMnOpQrStUv", "482913"],
                                ["finish", "jo@x.org", "L1"], ["finish", "jo@x.org", "L1"],
                                ["needsName"]]
        assert got["open"].startswith("Your name")

    def test_a_dead_link_is_dropped_and_the_next_code_claimed_afresh(self):
        got = dialog("D.openSignIn(api, { resume: REQ }); await tick(); await typeCode('482913');"
                     " claims[0].ok({ link: 'L1' }); await tick();"
                     " finishes[0].no({ code: 'auth/invalid-action-code' }); await tick();"
                     " const say = view().say;"
                     " await typeCode('123456');"
                     " out({ say, calls });")
        assert got["say"] == "That code has run out — send a new link."
        assert got["calls"][-1] == ["claim", "AbCdEfGhIjKlMnOpQrStUv", "123456"]


class TestClosingWhileACodeIsTraded:
    """Escape is refused while a code is traded, but a second Escape or
    Android's Back can close the dialog anyway."""

    def test_the_first_escape_is_refused(self):
        got = dialog("D.openSignIn(api, { resume: REQ }); await tick(); await typeCode('482913');"
                     " escape(); await tick(); out(view().open);")
        assert got is True

    def test_closed_anyway_and_signed_in_resolves_with_the_user(self):
        got = dialog("const p = outcome(D.openSignIn(api, { resume: REQ })); await tick(); await typeCode('482913');"
                     " escape(); forceClose(); await tick();"
                     " claims[0].ok({ link: 'L1' }); await tick(); finishes[0].ok(JO);"
                     " out({ got: await p, calls, left: !!dlg() });")
        assert got["got"] == {"resolved": {"uid": "uid-jo", "email": "jo@x.org"}}
        # The dialog is gone, so the name waits for the next link sign-in.
        assert ["needsName"] not in got["calls"] and got["left"] is False

    def test_closed_anyway_and_failed_rejects_as_a_shut_popup(self):
        got = dialog("const p = outcome(D.openSignIn(api, { resume: REQ })); await tick(); await typeCode('482913');"
                     " forceClose(); await tick();"
                     " claims[0].ok({ error: 'wrong' });"
                     " out(await p);")
        assert got == {"rejected": "auth/popup-closed-by-user"}

    def test_closed_anyway_and_the_finish_failed_rejects_too(self):
        got = dialog("const p = outcome(D.openSignIn(api, { resume: REQ })); await tick(); await typeCode('482913');"
                     " forceClose(); await tick();"
                     " claims[0].ok({ link: 'L1' }); await tick();"
                     " finishes[0].no({ code: 'auth/network-request-failed' });"
                     " out(await p);")
        assert got == {"rejected": "auth/popup-closed-by-user"}

    def test_closed_with_nothing_under_way_rejects_at_once(self):
        got = dialog("const p = outcome(D.openSignIn(api, { resume: REQ })); await tick(); forceClose();"
                     " out(await p);")
        assert got == {"rejected": "auth/popup-closed-by-user"}


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

    def test_no_card_says_google_only(self):
        for name in ("Mine", "Shots", "Join", "Thinking", "Practice", "Users", "Playlists"):
            assert "Sign in with Google" not in self.src(f"frontend/site/{name}.jsx"), name
        assert "Sign in to join" in self.src("frontend/site/Join.jsx")

    def test_a_dismissal_is_not_an_error_in_the_header(self):
        ui = self.src("frontend/site/ui.jsx")
        chip = ui[ui.index("export function IdentityChip"):ui.index("const SIGNED_IN_ONLY")]
        assert '"auth/popup-closed-by-user"' in chip

    @pytest.mark.parametrize("name", ["Mine", "Shots", "Thinking", "Practice", "Join"])
    def test_a_dismissal_is_not_an_error_on_a_card(self, name):
        page = " ".join(self.src(f"frontend/site/{name}.jsx").split())
        assert "signIn().catch(e => setErr(signInTrouble(e)))" in page or (
            name == "Join" and "const say = signInTrouble(e);" in page), name
        assert "e.code || e.message" not in page and "err.code || err.message" not in page

    def test_the_dialog_finishes_only_with_the_address_this_browser_stored(self):
        # A stranger's link, claimed with a stranger's code, is no use here
        # because it is finished with this browser's own address. Any other
        # source for that address -- the link, the claim, a box -- breaks it.
        d = self.src("frontend/site/signInDialog.js")
        assert d.count("finishWithLink(") == d.count("api.finishWithLink(pending.email, held)") == 1

    def test_the_emailed_link_is_offered_only_once_it_is_switched_on(self):
        d = " ".join(self.src("frontend/site/signInDialog.js").split())
        assert "const input = api.emailLink ? h(\"input\"" in d
        assert "if (!input) { show(google, title(\"Sign in\"), google, say); return; }" in d
        auth = " ".join(self.src("frontend/site/auth.js").split())
        assert "emailLink = !!cfg.emailLink;" in auth
        assert "emailLink: emailLinkOn()," in auth
        resume = auth[auth.index("function resumeWaiting()"):]
        assert resume.index("if (!emailLinkOn()) return;") < resume.index("import(")

    def test_the_page_to_go_back_to_is_never_a_signin_address(self):
        # From /signin it would be the link itself, oobCode and all.
        auth = " ".join(self.src("frontend/site/auth.js").split())
        assert "const next = safeNext(location.pathname + location.search + location.hash);" in auth

    def test_a_new_name_redraws_the_header(self):
        assert "profileVersion" in self.src("frontend/site/useAuth.js")


class TestTheLandingPage:
    ROOT = Path(__file__).resolve().parents[1]

    def page(self):
        return " ".join((self.ROOT / "frontend/site/SignIn.jsx").read_text().split())

    def test_this_browser_finishes_only_its_own_request(self):
        # Which request a link is for is signinState landing's to say
        # (TestTheLandingDecision), from the landing address.
        p = self.page()
        assert "href: location.href" in p and "recall(" in p
        state = " ".join(MOD.read_text().split())
        assert "const id = requestIdOf(href);" in state
        assert 'history.replaceState(null, "", "/signin")' in p
        assert "next: safeNext(pending.next)" in state

    def test_another_browser_shows_a_code_and_never_uses_the_link_on_load(self):
        p = self.page()
        assert "relayLink(location.href)" in p
        assert p.index("relayLink(location.href)") < p.index("Sign in on this screen instead")
        for words in ("Almost there", "Type this code where you asked to sign in:",
                      "Works for 10 minutes.", "Sign in on this screen instead",
                      "This link has run out or has already been used.",
                      "That isn't the address the link was sent to."):
            assert words in p, words

    def test_what_to_do_is_signinStates_landing(self):
        p = self.page()
        assert ("landing({ accounts, signedIn: !!user, isLink: await isLink(location.href),"
                " pending, href: location.href })") in p

    def test_its_own_request_is_finished_with_the_address_it_stored(self):
        # Never one the link brings: the id in the link only picks the path.
        p = self.page()
        assert "const pending = recall(store());" in p
        assert ('if (to.is === "finish") { await finish(pending.email, to.next,'
                ' { is: "failed" }, setView); return; }') in p
        # That one, and "here", with the address typed on this screen, which
        # Firebase checks against the one the link was sent to.
        assert p.count("finish(") - p.count("async function finish(") == 2
        assert 'finish(typed, "/", { is: "here" }, setView)' in p

    def test_signed_in_there_is_no_signing_in_on_this_screen_instead(self):
        assert 'const instead = !user && (' in self.page()

    def test_the_code_comes_with_a_warning(self):
        p = self.page()
        warning = "Didn't ask to sign in? Close this page, and never read this code to anyone."
        assert warning in p
        assert p.index("formatCode(view.code)") < p.index(warning) < p.index("{instead}")

    def test_the_page_never_sends_its_address_as_a_referrer(self):
        # Until it is taken off, the address bar holds the link's oobCode.
        page = (self.ROOT / "src/curling_score/service/static/signin.html").read_text()
        assert '<meta name="referrer" content="strict-origin">' in page
