# Sign in with an emailed link: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Members without a Google account can sign in with a link emailed to them, on a phone, finishing in the browser where they started. Google sign-in stays as it is.

**Architecture:** Firebase's email-link sign-in, started from one chooser dialog that `site/auth.js` opens for every existing `signIn()` caller. The link lands on a new `/signin` page. If that page is in the same browser that asked, it finishes the sign-in. If not, it hands the link to the server, which holds it under the request's id for 10 minutes behind a 6-digit code. The waiting tab trades the code for the link and finishes there. The server also learns to ignore unverified password accounts and to save a name.

**Tech Stack:** FastAPI + MemoryRepo/FirestoreRepo (Python); the Firebase web SDK 12.19.0 from gstatic (`firebase-auth.js`); React 19 site pages; plain-DOM dialog; esbuild bundles committed into `src/`.

**Spec:** `docs/superpowers/specs/2026-10-21-email-link-sign-in-design.md`

## Global Constraints

- `auth.js` stays the only file that imports the Firebase SDK. The dialog and `/signin` reach Firebase only through functions `auth.js` passes or exports.
- No static import of `site/auth.js` or `site/signInDialog.js` from `frontend/viewer`, `frontend/runtime` or `frontend/core` (`test_firebase_is_only_ever_imported_on_demand`).
- No new script host: only the same gstatic `firebase-auth.js` module (`test_frontend_build.py`).
- Every caller of `signIn()` / `ensureSignedIn()` keeps working unchanged. Closing the dialog rejects with `{code: "auth/popup-closed-by-user"}`.
- The emailed link carries only `r=<request id>`. `next` lives only in localStorage.
- Code: 6 digits, zero-padded, from `secrets.randbelow(10**6)`; lasts 10 minutes (`RELAY_TTL_S = 600`); single use; the 5th wrong try deletes it (`RELAY_MAX_MISSES = 5`).
- Request id: 22 characters of base64url (16 random bytes), `^[A-Za-z0-9_-]{22}$`.
- Name: trimmed, inner whitespace collapsed to one space, 1–60 characters, no control characters.
- A token whose `firebase.sign_in_provider` is `password` and whose `email_verified` is false is anonymous.
- The dialog fits a 390 px wide screen.
- Built bundles are committed: every task that touches `frontend/` ends with `cd frontend && npm run build` and commits `src/curling_score/service/static/site.js`, `src/curling_score/viewer/app.js` and `frontend/.buildstamp.json` with it.
- Tests run with the venv and only the named files (the full suite OOMs this box):
  `.venv/bin/python -m pytest -q -p no:warnings -p no:cacheprovider <files>`. Check the exit code; the summary is suppressed.
- The checkout is shared with other sessions: `git add` only this task's files, never `-A`. `deploy/README.md` already carries someone else's change; stage only your hunks (`git add -p`).

Exact copy (spec, "What a member sees"):

| Where | Text |
|---|---|
| Chooser | "Continue with Google" · "or" · label "Email" · "Email me a link" |
| Waiting | "Check your email" · "We sent a link to {email}." · "If the link opens in another app, it shows a code. Type it here:" · button "Sign in" · "Send again" · "Use a different address" |
| Wrong code | "That code isn't right." |
| Code gone (5 misses, expired, used) | "That code has run out — send a new link." |
| Google, address already used by link | "This address signs in with an emailed link." (email box filled in) |
| Name step | "Your name" · "what your teammates see" · "Save" |
| `/signin`, other browser | "Almost there" · "Type this code where you asked to sign in:" · the code as `482 913` · "Works for 10 minutes." · "Sign in on this screen instead" |
| `/signin`, bad link | "This link has run out or has already been used." + "Sign in" |
| `/signin`, wrong address | "That isn't the address the link was sent to." |
| Site cards | "Sign in with Google" → "Sign in"; Join: "Sign in with Google to join" → "Sign in to join" |

## Review Focus

1. **An email typed with spaces or capitals** (" Jo@Example.org "). It is trimmed before sending and before finishing, so the link sent is the link that finishes. Pinned in Task 5 (`cleanEmail`).
2. **A code pasted with a space or dash** ("482 913", "482-913"). It is accepted as 482913. Pinned in Task 5 (`parseCode`).
3. **Send again, then the first email's link is the one tapped.** Send again keeps the same request id, so either email's link reaches the waiting tab. Pinned in Task 5 (`resend`) and Task 4 (a newer link for the same id replaces the code).
4. **localStorage that throws or is empty** (private window, blocked storage). Nothing breaks, and the waiting tab keeps the request in memory, so the code path still finishes. Pinned in Task 5 (storage that throws).
5. **`next` pointing back at `/signin`, or off the site** (`//evil.example`, `/\evil`). Goes to `/`. Pinned in Task 5 (`safeNext`).

---

### Task 1: Unverified password accounts are nobody

**Files:**
- Modify: `src/curling_score/service/auth.py` (`FakeVerifier.add`)
- Modify: `src/curling_score/service/api.py` (`claims_of`, ~line 395)
- Test: `tests/test_service_auth.py` (new class `TestPasswordAccounts`, after `TestSigningIn`)

**Interfaces:**
- Produces: `FakeVerifier.add(token, sub, email=None, name=None, email_verified=True, provider="google.com") -> str`. Its claims gain `"firebase": {"sign_in_provider": provider}`.
- Produces: `claims_of(authorization) -> dict | None` returns None for an unverified password token. `uid_of` and `current_user_or_none` inherit this.

- [ ] **Step 1: Write the failing tests**

```python
class TestPasswordAccounts:
    """Email links need Firebase's Email/Password provider on, so anyone can make
    an unverified password account under someone else's address. That is nobody."""

    def test_an_unverified_password_token_is_anonymous(self, w):
        w["auth"].add("tok-squat", "uid-squat", "sarah@example.org", None,
                      email_verified=False, provider="password")
        squat = {"Authorization": "Bearer tok-squat"}
        assert w["client"].get("/api/me", headers=squat).status_code == 401
        assert w["repo"].get_user("uid-squat") is None

    def test_someone_who_came_by_an_emailed_link_is_somebody(self, w):
        w["auth"].add("tok-link", "uid-link", "jo@example.org", None, provider="password")
        me = w["client"].get("/api/me", headers={"Authorization": "Bearer tok-link"}).json()
        assert me["user"]["id"] == "uid-link" and me["user"]["name"] is None

    def test_an_unverified_password_token_cannot_save_a_chart(self, w):
        # uid_of is what a save asks; it must agree with /api/me.
        w["auth"].add("tok-squat", "uid-squat", "x@example.org", None,
                      email_verified=False, provider="password")
        r = post(w, "/api/submissions", {"url": f"https://youtu.be/{VID}"},
                 {"Authorization": "Bearer tok-squat"})
        assert r.status_code == 401
```

- [ ] **Step 2: Run them and check they fail**

Run: `.venv/bin/python -m pytest -q -p no:warnings -p no:cacheprovider tests/test_service_auth.py -k PasswordAccounts`
Expected: FAIL. `add()` has no `provider` argument (TypeError).

- [ ] **Step 3: Implement**

Add `provider` to `FakeVerifier.add`. In `claims_of`, after verifying, return None when `(claims.get("firebase") or {}).get("sign_in_provider") == "password" and not claims.get("email_verified")`. Explain why in a comment (the spec's builder check, point 1), in the voice of the neighbouring docstrings.

- [ ] **Step 4: Run `tests/test_service_auth.py` and check it passes**

Expected: exit 0. Every existing test still passes, because the default provider is `google.com`.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/service/auth.py src/curling_score/service/api.py tests/test_service_auth.py
git commit -m "accounts: an unverified password account is nobody"
```

---

### Task 2: A name for someone who came by link

**Files:**
- Create: `src/curling_score/service/signin.py` (the server's half of emailed-link sign-in; starts with `clean_name`)
- Modify: `src/curling_score/service/api.py`: `PUT /api/me/name` next to `PUT /api/me/shots-sharing` (~2432), and `user_for_claims` (~376)
- Test: `tests/test_service_auth.py` (new class `TestNames`)

**Interfaces:**
- Produces: `signin.clean_name(raw) -> str | None`. Returns the cleaned name, or None if it isn't 1–60 characters, has a control character (`unicodedata.category(c).startswith("C")`), or isn't a string. Applies NFC, strips, and collapses whitespace runs to one space.
- Produces: `PUT /api/me/name {name}` → 200 `{"name": str}`; 422 for a bad name; 401 when signed out; 429 when rate-limited (`"name:" + me.id`, `PLAY_HOUR_LIMIT`, `PLAY_DAY_LIMIT`).
- Produces: `user_for_claims` fills a missing name from the token's `name`. It is folded into the same `update_user` call as `last_seen_at` when both are due, so it's still one write at most.

- [ ] **Step 1: Write the failing tests**

```python
LINK = {"Authorization": "Bearer tok-link"}


def _came_by_link(w):
    w["auth"].add("tok-link", "uid-link", "jo@example.org", None, provider="password")


class TestNames:
    def test_someone_who_came_by_link_can_give_a_name(self, w):
        _came_by_link(w)
        r = put(w, "/api/me/name", {"name": "  Jo \t  Smith "}, LINK)
        assert r.status_code == 200 and r.json() == {"name": "Jo Smith"}
        assert w["client"].get("/api/me", headers=LINK).json()["user"]["name"] == "Jo Smith"

    def test_sixty_characters_is_fine(self, w):
        _came_by_link(w)
        assert put(w, "/api/me/name", {"name": "x" * 60}, LINK).status_code == 200

    @pytest.mark.parametrize("bad", ["", "   ", "x" * 61, "Jo\u0000", "Jo​Smith", 5, None])
    def test_a_name_is_one_to_sixty_characters_of_text(self, w, bad):
        _came_by_link(w)
        assert put(w, "/api/me/name", {"name": bad}, LINK).status_code == 422

    def test_signed_out_names_nobody(self, w):
        assert put(w, "/api/me/name", {"name": "Jo"}).status_code == 401

    def test_a_later_google_sign_in_fills_in_a_missing_name(self, w):
        _came_by_link(w)
        w["client"].get("/api/me", headers=LINK)
        w["auth"].add("tok-jo-g", "uid-link", "jo@example.org", "Joanne Smith")
        w["client"].get("/api/me", headers={"Authorization": "Bearer tok-jo-g"})
        assert w["repo"].get_user("uid-link").name == "Joanne Smith"

    def test_a_name_they_gave_beats_googles(self, w):
        _came_by_link(w)
        put(w, "/api/me/name", {"name": "Jo"}, LINK)
        w["auth"].add("tok-jo-g", "uid-link", "jo@example.org", "Joanne Smith")
        w["client"].get("/api/me", headers={"Authorization": "Bearer tok-jo-g"})
        assert w["repo"].get_user("uid-link").name == "Jo"
```

(`​` is a zero-width space, category Cf, which the control-character rule refuses.)

- [ ] **Step 2: Run them and check they fail**

Run: `… tests/test_service_auth.py -k TestNames`
Expected: FAIL with 404/405 on `/api/me/name`.

- [ ] **Step 3: Implement `clean_name`, the route, and the fill-in**

- [ ] **Step 4: Run `tests/test_service_auth.py` and check it passes**

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/service/signin.py src/curling_score/service/api.py tests/test_service_auth.py
git commit -m "accounts: PUT /api/me/name; a later Google sign-in fills a missing name"
```

---

### Task 3: The relay record and its repository

**Files:**
- Modify: `src/curling_score/service/records.py`: `SigninRelay` after `Invite`
- Modify: `src/curling_score/service/repo.py`: protocol lines after `# invites`; MemoryRepo `self.relays` and methods
- Modify: `src/curling_score/service/firestore_repo.py`: `RELAYS = "signin_relays"` and methods
- Test: `tests/test_repo_contract.py`: new class `TestSigninRelays`; add `"signin_relays"` to `_firestore_repo`'s cleanup list

**Interfaces:**
- Produces:
  ```python
  @dataclass
  class SigninRelay:
      id: str            # the request id from the link's r=
      code: str          # "012345"
      link: str
      created_at: datetime
      expires_at: datetime
      misses: int = 0
      def live(self, now: datetime) -> bool: ...   # expires_at > now
  ```
- Produces: `put_signin_relay(relay) -> None` (replaces), `get_signin_relay(relay_id) -> SigninRelay | None`, and `claim_signin_relay(relay_id, code, now, max_misses) -> tuple[str, str | None]`, which returns one of:
  - `("ok", link)`: the relay is deleted.
  - `("wrong", None)`: counts a miss.
  - `("gone", None)`: missing, expired, or the miss that reached `max_misses`, which deletes the relay.
- Compare codes with `hmac.compare_digest`. The Firestore claim runs in one transaction, like `take_clip_job` (`firestore_repo.py:648`). Not in `export_all`: relays are transient, like `rate_limits`.

- [ ] **Step 1: Write the failing contract tests**

```python
RID = "AbCdEfGhIjKlMnOpQrStUv"
RLINK = f"https://chart.example/signin?r={RID}&mode=signIn&oobCode=o1&apiKey=k"


def relay(**kw):
    base = dict(id=RID, code="012345", link=RLINK, created_at=T0, expires_at=at(600))
    base.update(kw)
    return SigninRelay(**base)


class TestSigninRelays:
    def test_put_and_get(self, repo):
        repo.put_signin_relay(relay())
        assert repo.get_signin_relay(RID).code == "012345"
        assert repo.get_signin_relay("nope") is None

    def test_put_replaces(self, repo):
        repo.put_signin_relay(relay(code="111111", misses=2))
        repo.put_signin_relay(relay(code="222222"))
        got = repo.get_signin_relay(RID)
        assert (got.code, got.misses) == ("222222", 0)

    def test_the_right_code_takes_the_link_once(self, repo):
        repo.put_signin_relay(relay())
        assert repo.claim_signin_relay(RID, "012345", at(60), 5) == ("ok", RLINK)
        assert repo.claim_signin_relay(RID, "012345", at(61), 5) == ("gone", None)

    def test_wrong_codes_count_and_the_fifth_ends_it(self, repo):
        repo.put_signin_relay(relay())
        for i in range(4):
            assert repo.claim_signin_relay(RID, "999999", at(i), 5) == ("wrong", None)
        assert repo.get_signin_relay(RID).misses == 4
        assert repo.claim_signin_relay(RID, "999999", at(5), 5) == ("gone", None)
        assert repo.get_signin_relay(RID) is None
        assert repo.claim_signin_relay(RID, "012345", at(6), 5) == ("gone", None)

    def test_an_expired_relay_is_gone_even_with_the_right_code(self, repo):
        repo.put_signin_relay(relay())
        assert repo.claim_signin_relay(RID, "012345", at(600), 5) == ("gone", None)
```

- [ ] **Step 2: Run them and check they fail**

Run: `… tests/test_repo_contract.py -k SigninRelays`
Expected: FAIL. `SigninRelay` can't be imported.

- [ ] **Step 3: Implement the record and both repos**

- [ ] **Step 4: Run the memory half, then the Firestore half against the emulator**

Start the emulator as in the `firestore-emulator` memory note:
`JAVA=/usr/lib/jvm/java-21-openjdk-amd64/bin/java ~/google-cloud-sdk/platform/cloud-firestore-emulator/cloud_firestore_emulator start --host=127.0.0.1 --port=8399`

Then run:
`FIRESTORE_EMULATOR_HOST=127.0.0.1:8399 PYTHONPATH=src .venv/bin/python -m pytest tests/test_repo_contract.py -o addopts="" -q`

Expected: exit 0 with the firestore params run, not skipped. Afterwards, stop the emulator's java PID (not the wrapper's).

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/service/records.py src/curling_score/service/repo.py \
        src/curling_score/service/firestore_repo.py tests/test_repo_contract.py
git commit -m "repo: sign-in relays, claimed once, five misses and gone"
```

---

### Task 4: The relay routes

**Files:**
- Modify: `src/curling_score/service/signin.py`: `relay_id_of`, `new_code`, constants
- Modify: `src/curling_score/service/api.py`: the two routes, beside `/api/auth/config` (~1733)
- Create: `tests/test_signin_relay.py`

**Interfaces:**
- Consumes: Task 3's repo methods.
- Produces in `signin.py`: `RELAY_TTL_S = 600`, `RELAY_MAX_MISSES = 5`, `RELAY_HOUR_LIMIT = 30`, `RELAY_DAY_LIMIT = 100`, `CLAIM_HOUR_LIMIT = 60`, `CLAIM_DAY_LIMIT = 300`, `MAX_LINK_BYTES = 2048`, `REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{22}$")`. The club shares one IP, so the per-IP limits are loose; the 5-miss cap is the real guard.
- Produces: `signin.relay_id_of(link, base_url, api_key) -> str | None`. Returns the `r` id when `link` is a string of at most `MAX_LINK_BYTES` whose scheme and host match `base_url`, whose path is `/signin`, and whose query has exactly one each of `mode=signIn`, a non-empty `oobCode`, `apiKey == api_key`, and an `r` matching `REQUEST_ID`. Otherwise None.
- Produces: `signin.new_code() -> str` (`f"{secrets.randbelow(10**6):06d}"`).
- Produces: `POST /api/signin/relay {link}` → 200 `{"code": "482913", "expires_at": iso}` with `Cache-Control: no-store`.
  - 503 when accounts are off; 422 when `relay_id_of` refuses the link; 429 when over `"relay:" + ip_hash`.
  - If a live relay for the id already holds this exact link, return its code and expiry unchanged. Otherwise put a new relay.
- Produces: `POST /api/signin/relay/{relay_id}/claim {code}` → 200 `{"link": str}` (`no-store`).
  - 404 "that code isn't right" on `wrong`; 410 "that code has run out — send a new link" on `gone`.
  - 422 when the id doesn't match `REQUEST_ID` or the code isn't 6 digits; 429 over `"claim:" + ip_hash`; 503 when accounts are off.

- [ ] **Step 1: Write the failing tests**

```python
"""The relay: an emailed link opened in the wrong browser, held for the right one.

Opening the link elsewhere gives a code. Typing that code where the sign-in
started gives the link back once. Nothing here signs anyone in: the link is
useless without the address it was sent to, and Firebase checks that.
"""
from urllib.parse import urlencode

import pytest

from curling_score.service.auth import FakeVerifier, NoAuth
from tests.test_service_auth import _world, post

RID = "AbCdEfGhIjKlMnOpQrStUv"


def link(rid=RID, base="https://chart.example/signin", **q):
    params = {"r": rid, "apiKey": "AIzaFake", "oobCode": "oob-1", "mode": "signIn",
              "lang": "en", **q}
    return f"{base}?{urlencode(params)}"


@pytest.fixture
def w():
    return _world(FakeVerifier())


def relay(w, lnk=None, ip="1.2.3.4"):
    return w["client"].post("/api/signin/relay", json={"link": lnk or link()},
                            headers={"X-Forwarded-For": ip})


def claim(w, code, rid=RID, ip="1.2.3.4"):
    return w["client"].post(f"/api/signin/relay/{rid}/claim", json={"code": code},
                            headers={"X-Forwarded-For": ip})


class TestRelay:
    def test_a_link_opened_elsewhere_gets_a_six_digit_code(self, w):
        r = relay(w)
        assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
        assert len(r.json()["code"]) == 6 and r.json()["code"].isdigit()
        assert r.json()["expires_at"] == "2026-09-11T12:10:00+00:00"

    def test_the_same_link_again_gets_the_same_code(self, w):
        assert relay(w).json() == relay(w).json()

    def test_a_newer_link_for_the_same_request_replaces_it(self, w):
        relay(w)
        second = relay(w, link(oobCode="oob-2")).json()["code"]
        assert claim(w, second).json() == {"link": link(oobCode="oob-2")}

    def test_the_code_gives_the_link_back_once(self, w):
        code = relay(w).json()["code"]
        r = claim(w, code)
        assert r.status_code == 200 and r.json() == {"link": link()}
        assert claim(w, code).status_code == 410

    def test_four_wrong_codes_are_404_and_the_fifth_is_410(self, w):
        code = relay(w).json()["code"]
        wrong = "000000" if code != "000000" else "111111"
        assert [claim(w, wrong).status_code for _ in range(5)] == [404] * 4 + [410]
        assert claim(w, code).status_code == 410

    def test_a_code_runs_out_after_ten_minutes(self, w):
        code = relay(w).json()["code"]
        w["clock"].advance(600)
        assert claim(w, code).status_code == 410

    @pytest.mark.parametrize("bad", [
        link(base="https://evil.example/signin"), link(base="http://chart.example/signin"),
        link(base="https://chart.example/mine"), link(mode="resetPassword"),
        link(oobCode=""), link(apiKey="AIzaOther"), link(rid="short"),
        link(lang="x" * 2100), "not a url", 5, None,
    ])
    def test_only_our_own_sign_in_links_are_held(self, w, bad):
        assert relay(w, bad).status_code == 422

    def test_a_link_with_two_oob_codes_is_refused(self, w):
        assert relay(w, link() + "&oobCode=oob-x").status_code == 422

    @pytest.mark.parametrize("code", ["12345", "1234567", "abcdef", "", None])
    def test_a_code_is_six_digits(self, w, code):
        assert claim(w, code).status_code == 422

    def test_making_codes_is_limited_per_address(self, w):
        codes = [relay(w, link(rid=f"{i:022d}")).status_code for i in range(31)]
        assert codes[:30] == [200] * 30 and codes[30] == 429

    def test_trying_codes_is_limited_per_address(self, w):
        statuses = [claim(w, "000000", rid=f"{i:022d}").status_code for i in range(61)]
        assert statuses[60] == 429

    def test_with_accounts_off_there_is_no_relay(self):
        off = _world(NoAuth())
        assert relay(off).status_code == 503
        assert claim(off, "123456").status_code == 503
```

`ip_hash` reads the last `X-Forwarded-For` hop (`proxy_hops` defaults to 1), so a different `ip=` is a different address.

- [ ] **Step 2: Run them and check they fail**

Run: `… tests/test_signin_relay.py`
Expected: FAIL with 404 on `/api/signin/relay`.

- [ ] **Step 3: Implement `relay_id_of`, `new_code` and the two routes**

When drawing a code, redraw up to 5 times if it equals the code a replaced relay had, so a re-post with a new link never hands back the same digits. That's cosmetic, not security.

- [ ] **Step 4: Run `tests/test_signin_relay.py tests/test_service_auth.py` and check they pass**

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/service/signin.py src/curling_score/service/api.py tests/test_signin_relay.py
git commit -m "accounts: hold an emailed link opened elsewhere behind a code"
```

---

### Task 5: The browser's pure sign-in state

**Files:**
- Create: `frontend/site/signinState.mjs` (no Firebase, no DOM; storage and clock passed in)
- Create: `tests/test_signin_js.py` (node, importing the module by file URI like `TestTheChartPageAsksWhoMayEdit.js` in `test_viewer_js.py:84`)

**Interfaces:**
- Produces, all exported:
  - `PENDING_KEY = "signin.pending"`, `FRESH_MS = 15 * 60 * 1000`
  - `newRequestId(fill = b => crypto.getRandomValues(b)) -> string`: 16 bytes as base64url, 22 characters
  - `continueUrl(origin, id) -> string`: `${origin}/signin?r=${id}`
  - `requestIdOf(href) -> string | null`: the `r` parameter if it matches `/^[A-Za-z0-9_-]{22}$/`
  - `cleanEmail(text) -> string | null`: trimmed; null unless there is exactly one `@` with something on both sides and no whitespace
  - `parseCode(text) -> string | null`: digits only after removing spaces and dashes; null unless exactly 6
  - `formatCode(code) -> string`: `"482 913"`
  - `safeNext(next) -> string`: `next` if it starts with `/`, not `//`, not `/\`, and not `/signin`; else `"/"`
  - `remember(storage, pending)`, `recall(storage) -> pending | null`, `forget(storage)`: JSON under `PENDING_KEY`. Every storage call is in try/catch; `recall` returns null on a throw or on bad JSON.
  - `isFresh(pending, now, maxAgeMs = FRESH_MS) -> boolean`
  - `resend(pending, now) -> pending`: the same `email`, `id` and `next`, with `at = now`
  - `pending` is `{email: string, id: string, next: string, at: number}`

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run them and check they fail**

Run: `… tests/test_signin_js.py`
Expected: FAIL. The module isn't found (node exits non-zero).

- [ ] **Step 3: Implement `signinState.mjs`**

- [ ] **Step 4: Run `tests/test_signin_js.py` and check it passes**

- [ ] **Step 5: Commit**

No bundle rebuild is needed yet: nothing imports the module.

```bash
git add frontend/site/signinState.mjs tests/test_signin_js.py
git commit -m "site: the sign-in dialog's state, framework-free"
```

---

### Task 6: The chooser dialog and `auth.js`

**Files:**
- Modify: `frontend/site/auth.js`
- Create: `frontend/site/signInDialog.js`
- Modify: `frontend/site/useAuth.js`: also subscribe to `profileVersion`
- Modify: `frontend/site/ui.jsx`: `IdentityChip` shows no error on a dismissal
- Modify: `frontend/runtime/auth.mjs:56`: the comment says the chooser, not "Google's sign-in popup"
- Test: `tests/test_signin_js.py` (class `TestTheDialogIsWiredRight`, source assertions in the style of `test_viewer_js.py:531-559`); `tests/test_viewer_js.py:553-559` (`test_signing_in_waits_for_the_page_to_know_who` must still pass unchanged)

**Interfaces:**
- Consumes: Task 5's `signinState.mjs`; Task 4's routes; Task 2's `PUT /api/me/name`.
- Produces in `auth.js` (all exported):
  - `signIn() -> Promise<User>`: throws "accounts are not configured" as today. Then `(await import("./signInDialog.js")).openSignIn(api())`.
  - `signInWithGoogle() -> Promise<User>`: today's popup body, moved here.
  - `sendLink(email) -> Promise<pending>`: `sendSignInLinkToEmail(auth, email, {url: continueUrl(location.origin, id), handleCodeInApp: true})`. It reuses a fresh pending request's id when that request is for the same email (`resend`); otherwise it makes a `newRequestId()`. `next` is `location.pathname + location.search + location.hash`. It remembers the request in localStorage and returns it.
  - `finishWithLink(email, link) -> Promise<User>`: `signInWithEmailLink`, then `forget(localStorage)`, then resolves once `onUser` reports the user (like `ensureSignedIn`).
  - `isLink(url) -> Promise<boolean>`: awaits `started`, then `isSignInWithEmailLink`.
  - `relayLink(link) -> Promise<{code, expires_at}>`: POST `/api/signin/relay`.
  - `claimCode(id, code) -> Promise<{link} | {error: "wrong" | "gone" | "busy"}>`: POST claim. 404 → wrong, 410 → gone, 429 → busy.
  - `needsName() -> Promise<boolean>`: `authedFetch("/api/me")` → `!user.name`.
  - `setName(name) -> Promise<string>`: PUT `/api/me/name`, then `updateProfile(user, {displayName})`, then `pv++`, then `announce()`.
  - `profileVersion() -> number`.
  - `askName() -> Promise<void>`: `(await import("./signInDialog.js")).askName(api())`, so `/signin` never imports the dialog itself.
  - The `api()` object handed to the dialog: `{signInWithGoogle, sendLink, claimCode, finishWithLink, onUser, needsName, setName, pending: () => recall(localStorage)}`.
- Produces in `signInDialog.js`:
  - `openSignIn(api, {resume} = {}) -> Promise<User>`. Resolves once signed in, and after the name step when this tab finished an email-link sign-in. Rejects with an Error whose `code` is `"auth/popup-closed-by-user"` when closed. The dialog stays open if the Google popup alone is shut, so they can switch to email.
  - `askName(api) -> Promise<void>`: the name step on its own, for `/signin`.
- Behaviour pinned by the spec, which the implementer builds:
  - One `<dialog>`, styles injected once into a `<style id="signin-style">` using `--panel --ink --muted --line --accent --onaccent` (both stylesheets define them). `max-width: min(360px, calc(100vw - 32px))`.
  - Waiting state: keeps `pending` in memory, so the code path works with storage blocked. `onUser` → resolve and close.
  - Code entry uses `parseCode`, then `claimCode`, then `finishWithLink(pending.email, link)`, then `needsName` → name step. Errors use the copy table.
  - Google error `auth/account-exists-with-different-credential`: the copy-table message, with `err.customData.email` in the email box.
  - Resume: at `auth.js` start-up, once the first `onAuthStateChanged` reports nobody, if `recall()` is fresh and `safeNext(pending.next)` without its hash equals `location.pathname + location.search`, call `openSignIn(api(), {resume: pending})`. When that resolves, `location.reload()`: the page's own action was lost with the reload.
  - `useAuth.js`: `useAuthUser` also reads `useSyncExternalStore(subscribe, profileVersion, () => 0)`, so `setName` re-renders the chip.
  - `IdentityChip`: in the catch, set no error when `e.code === "auth/popup-closed-by-user"`.

- [ ] **Step 1: Write the failing source assertions**

```python
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
```

- [ ] **Step 2: Run them and check they fail**

Run: `… tests/test_signin_js.py -k DialogIsWired`

- [ ] **Step 3: Implement `auth.js`, `signInDialog.js`, `useAuth.js`, `ui.jsx` and the `auth.mjs` comment**

- [ ] **Step 4: Build, then run the dialog tests and the existing sign-in tests**

```bash
cd frontend && npm run build && cd ..
.venv/bin/python -m pytest -q -p no:warnings -p no:cacheprovider tests/test_signin_js.py tests/test_viewer_js.py tests/test_frontend_build.py
```

Expected: exit 0.

- [ ] **Step 5: Commit**

```bash
git add frontend/site/auth.js frontend/site/signInDialog.js frontend/site/useAuth.js \
        frontend/site/ui.jsx frontend/runtime/auth.mjs tests/test_signin_js.py \
        src/curling_score/service/static/site.js src/curling_score/viewer/app.js frontend/.buildstamp.json
git commit -m "site: one chooser for Google or an emailed link"
```

Stage `frontend/.buildstamp.json` with every rebuild; `test_frontend_build.py` checks it.

---

### Task 7: The `/signin` page

**Files:**
- Create: `src/curling_score/service/static/signin.html` (copy `join.html`; title "Sign in — curling.dimmit.net"; `data-page="signin"`)
- Create: `frontend/site/SignIn.jsx`
- Modify: `frontend/site/main.jsx`: `signin: SignIn` in PAGES
- Modify: `src/curling_score/service/api.py`: `@app.get("/signin")` beside `/join/{token}` (~974), returning `page("signin.html")`
- Test: `tests/test_service_api.py:782-785` (add `("/signin", "signin")`), `:844` (add `"/signin"`), the bundle-words test (add `"Almost there"`); `tests/test_signin_js.py` (class `TestTheLandingPage`)

**Interfaces:**
- Consumes: `auth.js`'s `isLink`, `finishWithLink`, `relayLink`, `needsName`, `onUser`, `signIn`; `signinState`'s `requestIdOf`, `recall`, `safeNext`, `formatCode`, `cleanEmail`; `auth.js`'s `askName`.
- Behaviour (the spec's `/signin` page):
  - Accounts off → the page says sign-in isn't available here.
  - Not a link → a Sign in button (`signIn()`, then `location.replace("/")`).
  - A link and `recall()?.id === requestIdOf(location.href)` → `finishWithLink(pending.email, location.href)` → `history.replaceState(null, "", "/signin")` → `askName` if `needsName()` → `location.replace(safeNext(pending.next))`.
  - A link and no matching request → `relayLink(location.href)` → "Almost there" with `formatCode(code)`. Below it, "Sign in on this screen instead" opens an email box → `finishWithLink(cleanEmail(typed), location.href)` → name step → `location.replace("/")`.
  - Errors: `auth/invalid-action-code` / `auth/expired-action-code` → bad-link copy; `auth/invalid-email` → wrong-address copy. A relay 422 is treated as a bad link.
  - Already signed in on arrival with a link → skip to `safeNext(recall()?.next)` without consuming it.

- [ ] **Step 1: Write the failing tests**

In `test_service_api.py`, add the two path-list entries and `"Almost there"`. In `test_signin_js.py`:

```python
class TestTheLandingPage:
    ROOT = Path(__file__).resolve().parents[1]

    def page(self):
        return " ".join((self.ROOT / "frontend/site/SignIn.jsx").read_text().split())

    def test_this_browser_finishes_only_its_own_request(self):
        p = self.page()
        assert "requestIdOf(location.href)" in p and "recall(" in p
        assert 'history.replaceState(null, "", "/signin")' in p
        assert "safeNext(" in p

    def test_another_browser_shows_a_code_and_never_uses_the_link_on_load(self):
        p = self.page()
        assert "relayLink(location.href)" in p
        assert p.index("relayLink(location.href)") < p.index("Sign in on this screen instead")
        for words in ("Almost there", "Type this code where you asked to sign in:",
                      "Works for 10 minutes.", "Sign in on this screen instead",
                      "This link has run out or has already been used.",
                      "That isn't the address the link was sent to."):
            assert words in p, words
```

- [ ] **Step 2: Run them and check they fail**

Run: `… tests/test_signin_js.py -k LandingPage tests/test_service_api.py -k "pages or script"`

- [ ] **Step 3: Implement the shell, the route, `SignIn.jsx` and the PAGES entry**

- [ ] **Step 4: Build, then run `tests/test_signin_js.py tests/test_service_api.py tests/test_frontend_build.py`**

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/service/static/signin.html frontend/site/SignIn.jsx frontend/site/main.jsx \
        src/curling_score/service/api.py tests/test_service_api.py tests/test_signin_js.py \
        src/curling_score/service/static/site.js src/curling_score/viewer/app.js frontend/.buildstamp.json
git commit -m "site: /signin finishes a link here, or shows the code to finish it there"
```

---

### Task 8: Words: the cards, privacy, terms, READMEs

**Files:**
- Modify: `frontend/site/Mine.jsx:205-209`, `Shots.jsx:431-435`, `Join.jsx:48-53`, `Thinking.jsx:115-118`, `Practice.jsx:287-297`, `Users.jsx:73-76`, `Playlists.jsx:225-229` (button text only)
- Modify: `src/curling_score/service/static/privacy.html`, `terms.html:30`
- Modify: `README.md:490` (and `:281` if it says Google-only), `deploy/README.md` accounts block (stage only your hunks)
- Test: `tests/test_service_api.py` (new `test_the_terms_and_privacy_say_how_to_sign_in`, beside the other terms/privacy tests); `tests/test_signin_js.py` (`test_no_card_says_google_only`)

**Interfaces:** none new.

- [ ] **Step 1: Write the failing tests**

```python
    def test_the_terms_and_privacy_say_how_to_sign_in(self, world):
        c = world["client"]
        terms = " ".join(c.get("/terms").text.split())
        privacy = " ".join(c.get("/privacy").text.split())
        assert "sign in with a Google account to" not in terms
        assert "sign in, with a Google account or a link emailed to you," in terms
        assert "You sign in with Google, or with a link emailed to you" in privacy
        assert "held for up to 10 minutes" in privacy
        assert "sends the sign-in email" in privacy
```

```python
    def test_no_card_says_google_only(self):
        for name in ("Mine", "Shots", "Join", "Thinking", "Practice", "Users", "Playlists"):
            src = (self.ROOT / f"frontend/site/{name}.jsx").read_text()
            assert "Sign in with Google" not in src, name
        assert "Sign in to join" in (self.ROOT / "frontend/site/Join.jsx").read_text()
```

Match `terms.html`'s real sentence: read line 30 and adjust the first assertion's negative text to what is there now. Do the same for `privacy.html`'s account bullet. The spec lists five privacy points; each needs a sentence.

- [ ] **Step 2: Run them and check they fail**

- [ ] **Step 3: Change the copy**

In `deploy/README.md`:
- Enable Email/Password plus its Email link (passwordless) option.
- Set the public-facing name.
- Keep one account per email address.
- Add the optional custom sender domain.
- Add the `signin_relays` TTL `gcloud` line next to the `invites` one.
- Note that the daily limit is 25,000 emails with billing on.

- [ ] **Step 4: Build, then run `tests/test_service_api.py tests/test_signin_js.py tests/test_viewer_js.py tests/test_frontend_build.py`**

Fix any text assertion in `test_viewer_js.py` that quoted old card copy.

- [ ] **Step 5: Commit**

```bash
git add frontend/site/{Mine,Shots,Join,Thinking,Practice,Users,Playlists}.jsx \
        src/curling_score/service/static/{privacy,terms}.html README.md \
        tests/test_service_api.py tests/test_signin_js.py tests/test_viewer_js.py \
        src/curling_score/service/static/site.js src/curling_score/viewer/app.js frontend/.buildstamp.json
git add -p deploy/README.md
git commit -m "words: sign in with Google or an emailed link"
```

---

### Task 9: Check it in a browser, at phone size

No commit unless the check finds a bug, which then gets a test and a fix in the task that owns the code.

**Kit** (in `~/curling-work/signin-check/`, not the repo; see the `signed-in-browser-check` memory note):
- `serve.py`: `tests.test_service_auth._world(FakeVerifier())` under uvicorn on `127.0.0.1:8790`, with `public_base_url` set to `http://127.0.0.1:8790`.
  - The relay's host check compares scheme and host with `public_base_url`, so this must match.
  - `FakeVerifier` gets `fake-google` (Google, named) and `fake-link-<n>` (provider `password`, no name) tokens added on demand. A tiny `/__fake/token` route in `serve.py` adds one.
- `fake-firebase-auth.js`, served through CDP `Fetch` on `https://www.gstatic.com/*`. It exports:
  - `initializeApp`, `getAuth`, `connectAuthEmulator`, `GoogleAuthProvider`, `signInWithPopup`, `signOut`, `updateProfile`.
  - `onAuthStateChanged`: fires on `storage` events for `fakeTok`, which is how cross-tab sync is faked.
  - `sendSignInLinkToEmail`: writes `{email, url}` to `localStorage.fakeMail`.
  - `isSignInWithEmailLink`: `mode=signIn` and an `oobCode` present.
  - `signInWithEmailLink`: checks the email, mints a token through `/__fake/token`, then sets `fakeTok`. Throws `{code: "auth/invalid-action-code"}` for an `oobCode` already used.
- `check.mjs` on `scripts/cdp.mjs`: 390×844, `mobile: true`, touch emulation on, taps as `Input.dispatchTouchEvent` pairs (the `headless-phone-check` memory note).

**Walks** (each takes a screenshot into the kit folder):
- [ ] The chooser opens from the header's Sign in on `/` and fits 390 px with no horizontal scroll.
- [ ] Same browser: send to `jo@example.org` → open the mailed URL in a second tab → signed in, name asked, back at the start page. The first tab's dialog closed by itself.
- [ ] Mid-action: on a `/c/` chart, Name teams → Sign in → email path, finished in a second tab → the Name teams dialog carries on.
- [ ] Another browser: open the mailed URL in a fresh incognito context (`Target.createBrowserContext`) → "Almost there" with a code → type it in the first tab → signed in there; the incognito page never consumed the link.
- [ ] Reload the waiting tab → the code box is back.
- [ ] Five wrong codes → "That code has run out — send a new link."; a reused link → the bad-link copy.
- [ ] The Google button still signs in and still resolves `ensureSignedIn` (Chart on `/games`).

---

### Task 10: Ship it (each step needs the user's go-ahead at the time)

- [ ] Run the review with the whole-branch reviewer chosen at handoff.
- [ ] Deploy the API from a clean worktree (the `deploying-curling-chart` and `concurrent-session-commits` memory notes). `deploy-api.sh` reads `/api/auth/config` back afterwards. **Ask first.**
- [ ] Run `gcloud firestore fields ttls update expires_at --collection-group=signin_relays --enable-ttl`. **Ask first.** This goes BEFORE the provider is switched on, so no relay is ever written without its expiry.
- [ ] Switch on Email/Password and Email link (passwordless) in the Firebase console, and set the public-facing name. That's the user's click, or the Identity Toolkit admin API with their go-ahead. It goes after the deploy, so `claims_of` is already live.
- [ ] Real-project checks from the spec's Testing section, using test accounts that are deleted afterwards:
  - FIRST after switching on, the phone mail-app check, finished with the code. If Firebase's landing URL has a different shape from what `relay_id_of` expects, every relay answers 422, and this is where it shows.
  - The accepted pre-made-account risk (spec, Builder check point 5): create a password account for a fresh test address in advance, then sign in by emailed link as the owner of that address, and record whether the pre-made password still works (the unverified case; Firebase's documentation says it should not). Use the same test account to rehearse the operator remedy in deploy/README.md (remove the password with `accounts:update` and `deleteProvider: ["password"]`, then revoke sessions), and check that the pre-made password stops working. Delete these test accounts afterwards too.
  - A non-Gmail Google account: Google, then a link, then Google.
  - A Gmail address: a link, then Google.
- [ ] Save a memory note: what shipped, the commits, and anything the real-project check showed about Firebase account linking.
