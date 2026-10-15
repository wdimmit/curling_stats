# Chart Write Protection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** With accounts on, making a chart needs sign-in, and only a chart's owner (personal chart) or its team's current members (team chart) can save its grading. Everyone else holding the `/c/` link watches.

**Architecture:**
- One server rule, `may_edit_chart(chart, uid)`, guards the only chart write: `POST /c/{slug}/overrides.json`.
- `GET /c/{slug}/access.json` tells the page whether to unlock editing. `window.CHART.guarded` tells the page to ask.
- The viewer decides edit or view once, before its first render.
- It sends the Firebase ID token with every save, and the close-of-tab `sendBeacon` becomes a keepalive `fetch` that carries the last token.
- With accounts off, nothing changes.

**Tech Stack:**
- Server: FastAPI (`src/curling_score/service/api.py`). Tests use an in-memory repo and `FakeVerifier` in place of Firebase.
- Frontend: React 19 bundled by esbuild (`frontend/`). The core and runtime modules are plain ES modules, tested from pytest through `node`.

**Spec:** `docs/superpowers/specs/2026-10-15-chart-write-protection-design.md`

## Global Constraints

- **Where to work:** in the worktree `/home/tcuser/src/curling_score/.claude/worktrees/chart-guard`, branch `chart-guard`. Never write under `/home/tcuser/src/curling_score` outside this worktree: another session shares that checkout.
- **Running tests:** `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q -p no:warnings -p no:cacheprovider -rf <files>; echo "exit $?"`. The repo's pytest config hides the summary line, so judge by the exit code and the `FAILED`/`ERROR` lines.
- **Never run the whole suite, or any vision suite** (`test_yolo`, `test_rocks`, `test_review`, `test_scoreboard`, ...). This machine runs out of memory (exit 137). Run only the files a step names.
- **Known failure:** `tests/test_service_api.py::TestCatalogueAndAdmin::test_the_pages_load` already fails on main ("Most lopsided" is in no page). It is not this work's; ignore that one failure.
- **Frontend builds:** before the first build, run `ln -s /home/tcuser/src/curling_score/frontend/node_modules frontend/node_modules` once. Build with `(cd frontend && npm run build)` (eslint, then the bundle).
  - Commit the built `src/curling_score/viewer/app.js`, `src/curling_score/service/static/site.js` and `frontend/.buildstamp.json` with the sources that changed them. `tests/test_frontend_build.py` fails if they drift.
  - Never `git add` the `frontend/node_modules` symlink.
- **Commits:** stage files by name, never `git add -A`. Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Accounts off stays as before:** with `auth.enabled` false (NoAuth, `FIREBASE_PROJECT` unset), anyone charts, a link is all it takes to save, and no page is `guarded`. `tests/test_service_api.py` runs in that world and must stay green, apart from the known failure.
- **Copy, verbatim:**
  - 401 on making a chart: `sign in to chart`
  - 401 on a save: `sign in to change this chart`
  - 403 on a save: `only the person or team who made this chart can change it`
  - `access.json` reasons: `owner`, `team`, `open`, `signin`, `not-yours`, `view-only-link`. The client adds `unchecked`.
  - View-only lines:
    - `View only — sign in to edit if this chart is yours or your team's`
    - `View only — only the person or team who made this chart can change it`
    - `View only — only team {name} can change this chart`
    - `View only — couldn't check sign-in. Reload to try again.`
    - On a phone: `View only`
  - Refused save status: `not saved — sign in again`
  - Token refresh tick: every 5 minutes (`300000` ms)

## Review Focus

1. **A charting tab open longer than an hour** must keep saving: each save has to ask for the token again rather than reuse the first one. Test in Task 5 (`test_every_save_asks_for_the_token_again`).
2. **A save through a team's superseded "loser" link** reaches the team's chart for a member, and a stranger is refused. Test in Task 2 (`test_a_stranger_saving_through_the_loser_link_is_refused`).
3. **Sign-in slow or unreachable** (Firebase SDK blocked or slow) on a guarded page: the page opens view-only with "couldn't check sign-in". It is never editable and never blank. Tests in Task 4 (`test_no_answer_watches_unchecked`) and Task 6 (`test_slow_sign_in_is_no_answer_not_signed_out`).
4. **A whole-document save** (no `merge`; older pages and `curling-score serve` send these) is held to the same rule. Test in Task 2 (`test_a_whole_document_save_is_held_to_the_same_rule`).
5. **A failing close-of-tab save** (offline, or a body over keepalive's 64 KB) throws nothing into the page and leaves no unhandled rejection. Test in Task 5 (`test_a_close_of_tab_save_that_fails_is_swallowed`).

---

### Task 1: Sign in to chart

**Files:**
- Modify: `src/curling_score/service/api.py`: `submit` (≈ line 1308) and `api_start_charting` (≈ line 1400)
- Modify tests: `tests/test_service_auth.py`, `tests/test_plays.py`, `tests/test_entered_scores.py`

**Interfaces:**
- Produces, in `tests/test_service_auth.py`:
  - `MAKER = {"Authorization": "Bearer tok-maker"}` (uid `uid-maker`), known to the `w` fixture's `FakeVerifier`;
  - `an_unowned_chart(w) -> str`, the slug of a processed chart with no owner and no team;
  - `a_ready_game(w)` now submits as `MAKER`.

  Later tasks use all three.

- [ ] **Step 1: Give the test world a maker, and write the failing tests**

In `tests/test_service_auth.py`:

Below `ALEX = ...` add:

```python
# Somebody who makes charts in the background of a test -- the game a test
# needs, an unowned chart -- so that none of the test's own people own them.
MAKER = {"Authorization": "Bearer tok-maker"}
```

In the `w` fixture, after the `tok-alex` line add:

```python
    auth.add("tok-maker", "uid-maker", "maker@example.org", "Maker")
```

Replace `a_ready_game` with:

```python
def a_ready_game(w):
    """Submit (as Maker) and process a video; return the source id the
    catalogue lists. With accounts on, charting needs somebody signed in."""
    post(w, "/api/submissions", {"url": f"https://youtu.be/{VID}"}, MAKER)
    work_through(w)
    games = w["client"].get("/api/games").json()["games"]
    return next(g["source_id"] for g in games if g["source_id"])


def an_unowned_chart(w):
    """A processed chart nobody owns, as every chart made before charting
    needed an account is: made by Maker, then its owner taken off. After
    a_ready_game the video is already processed and the chart already has its
    game, and work_through would find no job to claim -- so only a new video
    is worked through."""
    slug = post(w, "/api/submissions", {"url": f"https://youtu.be/{VID}"}, MAKER).json()["slug"]
    if w["repo"].get_chart(slug).source_id is None:
        work_through(w)
    w["repo"].update_chart(slug, owner_user_id=None)
    return slug
```

Delete `TestSigningIn.test_a_forged_token_is_simply_anonymous_everywhere_else`. It is replaced below; flags still take a bad token as anonymous (`TestAFlagCarriesTheAccount.test_a_bad_token_is_anonymous_not_an_error`).

Replace `TestMyGames.test_a_link_made_anonymously_can_be_claimed_later` with:

```python
    def test_a_link_made_anonymously_can_be_claimed_later(self, w):
        """The recovery path for every chart made before charting needed an account."""
        slug = an_unowned_chart(w)
        assert w["client"].get("/api/me/charts", headers=SARAH).json()["charts"] == []
        assert post(w, f"/api/me/charts/{slug}/claim", {}, SARAH).status_code == 200
        mine = w["client"].get("/api/me/charts", headers=SARAH).json()["charts"]
        assert [c["slug"] for c in mine] == [slug]
```

In `TestOneChartPerGame`:
- replace `test_anonymous_visitors_each_get_their_own` and `test_making_new_charts_is_still_bounded` with:

```python
    def test_anonymous_visitors_each_get_their_own(self, no_accounts):
        """With accounts off, unchanged from before accounts: no owner, no dedupe."""
        sid = a_ready_game(no_accounts)
        a = post(no_accounts, "/api/charts", {"source_id": sid}).json()
        b = post(no_accounts, "/api/charts", {"source_id": sid}).json()
        assert a["slug"] != b["slug"]

    def test_making_new_charts_is_still_bounded(self, no_accounts):
        """Free of the submission budget is not the same as free of any budget:
        an anonymous caller never dedupes, so every press makes a document."""
        sid = a_ready_game(no_accounts)
        codes = {post(no_accounts, "/api/charts", {"source_id": sid}).status_code
                 for _ in range(40)}
        assert codes == {201, 429}
```

- in `test_a_game_still_processing_cannot_be_charted_this_way`, change its first line to `post(w, "/api/submissions", {"url": f"https://youtu.be/{VID}"}, MAKER)`.

Replace `TestMovingAChartToATeam.test_claiming_without_a_team_is_unchanged` with:

```python
    def test_claiming_without_a_team_is_unchanged(self, w):
        slug = an_unowned_chart(w)
        assert post(w, f"/api/me/charts/{slug}/claim", {}, SARAH).status_code == 200
        chart = w["repo"].get_chart(slug)
        assert chart.owner_user_id == "uid-sarah" and chart.team_id is None
```

Add this class after `TestMyGames`:

```python
class TestSignInToChart:
    """With accounts on, a chart always has somebody to protect it for."""

    def test_a_submission_signed_out_is_401_and_queues_nothing(self, w):
        r = post(w, "/api/submissions", {"url": f"https://youtu.be/{VID}"})
        assert r.status_code == 401 and r.json()["detail"] == "sign in to chart"
        assert w["repo"].runs_for_video(VID) == []

    def test_charting_a_known_game_signed_out_is_401(self, w):
        sid = a_ready_game(w)
        r = post(w, "/api/charts", {"source_id": sid})
        assert r.status_code == 401 and r.json()["detail"] == "sign in to chart"

    def test_a_forged_token_is_signed_out(self, w):
        r = post(w, "/api/submissions", {"url": f"https://youtu.be/{VID}"},
                 {"Authorization": "Bearer forged"})
        assert r.status_code == 401

    def test_signed_in_the_chart_is_theirs(self, w):
        slug = post(w, "/api/submissions", {"url": f"https://youtu.be/{VID}"},
                    SARAH).json()["slug"]
        assert w["repo"].get_chart(slug).owner_user_id == "uid-sarah"

    def test_with_accounts_off_charting_stays_anonymous(self, no_accounts):
        sid = a_ready_game(no_accounts)
        r = post(no_accounts, "/api/charts", {"source_id": sid})
        assert r.status_code == 201
        assert no_accounts["repo"].get_chart(r.json()["slug"]).owner_user_id is None
```

In `tests/test_plays.py`, under the existing `from tests.test_service_auth import give_role` line, add `from tests.test_service_auth import MAKER`. In `a_played_game`, change `slug = post(w, "/api/submissions", body).json()["slug"]` to `slug = post(w, "/api/submissions", body, MAKER).json()["slug"]`.

In `tests/test_entered_scores.py`, change the import line to `from tests.test_service_auth import ALEX, MAKER, SARAH, post, w  # noqa: F401  (w is a fixture)`. In `a_scored_game`, change the post to `post(w, "/api/submissions", {"url": f"https://youtu.be/{VID}"}, MAKER)`.

- [ ] **Step 2: Run the new tests to see them fail**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q -p no:warnings -p no:cacheprovider -rf tests/test_service_auth.py -k "SignInToChart"; echo "exit $?"`
Expected: FAILED for the three 401 tests (they get 201). `test_signed_in_the_chart_is_theirs` and `test_with_accounts_off_charting_stays_anonymous` pass already.

- [ ] **Step 3: Refuse charting signed out, with accounts on**

In `api.py`, `async def submit(...)`: directly after `me = current_user_or_none(authorization)` add:

```python
        # With accounts on, a chart always has somebody to protect it for
        # (may_edit_chart): no account, no chart. Accounts off keeps charting
        # anonymous, exactly as before accounts existed.
        if auth.enabled and me is None:
            raise HTTPException(401, "sign in to chart")
```

In `def api_start_charting(...)`: directly after `me = current_user_or_none(authorization)` add the same four lines (comment included).

- [ ] **Step 4: Run the affected files**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q -p no:warnings -p no:cacheprovider -rf tests/test_service_auth.py tests/test_plays.py tests/test_entered_scores.py tests/test_shared_shots.py tests/test_game_clips_api.py; echo "exit $?"`
Expected: `exit 0`.

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q -p no:warnings -p no:cacheprovider -rf tests/test_service_api.py; echo "exit $?"`
Expected: the only FAILED line is `TestCatalogueAndAdmin::test_the_pages_load`.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/service/api.py tests/test_service_auth.py tests/test_plays.py tests/test_entered_scores.py
git commit -m "Charting needs sign-in when accounts are on

So every new chart has an owner or a team to protect it for. Accounts off
keeps charting anonymous. Tests chart as a background Maker, and
an_unowned_chart stands in for charts made before this.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Only the owner or the team saves a chart

**Files:**
- Modify: `src/curling_score/service/api.py`: `current_user_or_none` (≈ line 385), a new `claims_of`/`uid_of` beside it, a new `may_edit_chart` after `may_use_team` (≈ line 426), and `overrides_post` (≈ line 1738)
- Test: `tests/test_service_auth.py`

**Interfaces:**
- Consumes: `MAKER`, `an_unowned_chart`, `a_ready_game` (Task 1); `_joined_team(w)` (existing; Sarah's team "Thistles", which Alex has joined).
- Produces, in `api.py` inside `create_app`:
  - `uid_of(authorization: str | None) -> str | None`
  - `may_edit_chart(chart: Chart, uid: str | None) -> bool`
  - In tests: `save_grading(w, key, headers=None, *, kind="c", merge=True)`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_service_auth.py`, add after `a_team`:

```python
def save_grading(w, key, headers=None, *, kind="c", merge=True):
    """One shot graded, sent as the page sends it: a merge, or the whole
    document that `curling-score serve` and older pages send."""
    url = f"/{kind}/{key}/overrides.json" + ("?merge=1" if merge else "")
    return w["client"].post(url, json={"0.1.1": {"user_score": 3}},
                            headers={"X-Forwarded-For": "1.2.3.4", **(headers or {})})
```

Replace the whole `TestCapabilityUrlsAreUntouched` class with:

```python
class TestOnlyItsMakerChangesAChart:
    """With accounts on, a chart's grading is its owner's, or its team's, to
    change; anyone else holding the link watches."""

    def a_personal_chart(self, w):
        sid = a_ready_game(w)
        return post(w, "/api/charts", {"source_id": sid}, SARAH).json()["slug"]

    def a_team_chart(self, w):
        team = _joined_team(w)                       # Sarah's; Alex joined
        sid = a_ready_game(w)
        got = post(w, "/api/charts", {"source_id": sid, "team_id": team}, SARAH).json()
        return team, got["slug"]

    def test_the_owner_saves(self, w):
        slug = self.a_personal_chart(w)
        assert save_grading(w, slug, SARAH).status_code == 200
        assert w["repo"].get_chart(slug).overrides == {"0.1.1": {"user_score": 3}}

    def test_a_stranger_with_the_link_is_refused_and_nothing_changes(self, w):
        slug = self.a_personal_chart(w)
        r = save_grading(w, slug, ALEX)
        assert r.status_code == 403
        assert r.json()["detail"] == "only the person or team who made this chart can change it"
        assert w["repo"].get_chart(slug).overrides == {}

    def test_signed_out_is_told_to_sign_in(self, w):
        slug = self.a_personal_chart(w)
        r = save_grading(w, slug)
        assert r.status_code == 401 and r.json()["detail"] == "sign in to change this chart"

    def test_a_forged_token_is_signed_out(self, w):
        slug = self.a_personal_chart(w)
        assert save_grading(w, slug, {"Authorization": "Bearer forged"}).status_code == 401

    def test_a_whole_document_save_is_held_to_the_same_rule(self, w):
        slug = self.a_personal_chart(w)
        assert save_grading(w, slug, ALEX, merge=False).status_code == 403
        assert save_grading(w, slug, SARAH, merge=False).status_code == 200

    def test_every_member_of_the_team_saves(self, w):
        _, slug = self.a_team_chart(w)
        assert save_grading(w, slug, SARAH).status_code == 200
        assert save_grading(w, slug, ALEX).status_code == 200

    def test_a_stranger_cannot_save_a_team_chart(self, w):
        _, slug = self.a_team_chart(w)
        assert save_grading(w, slug, MAKER).status_code == 403

    def test_leaving_the_team_ends_it_even_for_the_charts_maker(self, w):
        """A team chart is the team's: whoever made it has no rights of their own."""
        team, slug = self.a_team_chart(w)
        w["repo"].update_chart(slug, owner_user_id="uid-alex")          # Alex made it ...
        w["client"].delete(f"/api/teams/{team}/members/uid-alex", headers=ALEX)  # ... and left
        assert save_grading(w, slug, ALEX).status_code == 403
        assert save_grading(w, slug, SARAH).status_code == 200

    def test_an_unowned_chart_stays_open_to_its_link(self, w):
        assert save_grading(w, an_unowned_chart(w)).status_code == 200

    def test_until_someone_claims_it(self, w):
        slug = an_unowned_chart(w)
        post(w, f"/api/me/charts/{slug}/claim", {}, SARAH)
        assert save_grading(w, slug).status_code == 401
        assert save_grading(w, slug, ALEX).status_code == 403
        assert save_grading(w, slug, SARAH).status_code == 200

    def test_a_share_link_never_saves_even_for_the_owner(self, w):
        sid = a_ready_game(w)
        got = post(w, "/api/charts", {"source_id": sid}, SARAH).json()
        share = got["share_url"].rstrip("/").split("/")[-1]
        assert save_grading(w, share, SARAH, kind="s").status_code == 403

    def test_with_accounts_off_the_link_is_still_the_permission(self, no_accounts):
        sid = a_ready_game(no_accounts)
        slug = post(no_accounts, "/api/charts", {"source_id": sid}).json()["slug"]
        assert save_grading(no_accounts, slug).status_code == 200

    def test_a_shared_chart_tells_the_page_to_poll(self, w):
        team = _joined_team(w)
        sid = a_ready_game(w)
        slug = post(w, "/api/charts", {"source_id": sid, "team_id": team}, SARAH).json()["slug"]
        assert '"shared": true' in w["client"].get(f"/c/{slug}/").text
        solo = post(w, "/api/charts", {"source_id": sid}, ALEX).json()["slug"]
        assert '"shared": false' in w["client"].get(f"/c/{solo}/").text
```

(`test_a_shared_chart_tells_the_page_to_poll` moves over unchanged from the deleted class. `test_a_share_link_is_still_read_only_for_a_member` is covered by `test_a_share_link_never_saves_even_for_the_owner`.)

Existing tests that save as nobody now need the saver's headers:
- In `TestOneChartPerGame.test_grading_survives_coming_back`, add `headers=SARAH` to the `w["client"].post(f"/c/{slug}/overrides.json?merge=1&v=0", ...)` call.
- In `TestRacingOnANewVideo.test_the_loser_link_keeps_working_and_shows_the_teams_chart`, add `headers=SARAH` to the save through `first` and `headers=ALEX` to the save through `second`.

Then add to `TestRacingOnANewVideo`:

```python
    def test_a_stranger_saving_through_the_loser_link_is_refused(self, w):
        team = _joined_team(w)
        first, second = self._both_submit(w, team)
        work_through(w)
        r = w["client"].post(f"/c/{second}/overrides.json?merge=1",
                             json={"0.1.1": {"user_score": 3}}, headers=MAKER)
        assert r.status_code == 403
        assert w["repo"].get_chart(first).overrides == {}
```

- [ ] **Step 2: Run them to see them fail**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q -p no:warnings -p no:cacheprovider -rf tests/test_service_auth.py -k "OnlyItsMaker or loser_link"; echo "exit $?"`
Expected: FAILED for every refusal test (they get 200). The owner, member, unowned and accounts-off tests pass already.

- [ ] **Step 3: Implement the rule**

In `api.py`, replace `current_user_or_none` (its docstring stays word for word) with:

```python
    def claims_of(authorization) -> dict | None:
        """The verified claims behind a bearer token, or None. Never raises."""
        if not auth.enabled or not authorization or not authorization.startswith("Bearer "):
            return None
        claims = auth.verify(authorization[len("Bearer "):].strip())
        return claims if claims and claims.get("sub") else None

    def uid_of(authorization) -> str | None:
        """Who is asking, by id alone. Reads no record: a save asks this every
        800 ms while somebody charts, and may_edit_chart needs only the id."""
        claims = claims_of(authorization)
        return claims["sub"] if claims else None

    def current_user_or_none(authorization) -> User | None:
        """Who this request is, if it says so.

        Never raises. A missing, expired, malformed or foreign token all mean
        the same thing -- we do not know who this is -- and that is a state
        every route here already handles, because it is the only state that
        existed before accounts did.
        """
        claims = claims_of(authorization)
        return user_for_claims(claims) if claims else None
```

After `may_use_team` add:

```python
    def may_edit_chart(chart: Chart, uid: str | None) -> bool:
        """Whether `uid` may change this chart's grading.

        A team's chart is its current members' -- whoever made it included,
        and only while they stay on the team. A personal chart is its owner's.
        A chart nobody owns (made before charting needed an account) is still
        its link's, until somebody claims it. With accounts off every link
        edits, as before accounts existed.
        """
        if not auth.enabled:
            return True
        if chart.team_id:
            return uid is not None and uid in repo.team_members(chart.team_id)
        if chart.owner_user_id:
            return uid == chart.owner_user_id
        return True
```

In `overrides_post`, add one paragraph at the end of its docstring:

```python
            Only whoever may_edit_chart allows may save: with accounts on, a
            chart's owner or its team's members, or anyone for a chart nobody
            owns. Refused before the body is read, so a refusal writes nothing.
```

Directly after `if read_only: raise HTTPException(403, "this is a view-only link")` add:

```python
            uid = uid_of(request.headers.get("authorization"))
            if not may_edit_chart(chart, uid):
                if uid is None:
                    raise HTTPException(401, "sign in to change this chart")
                raise HTTPException(403, "only the person or team who made this chart can change it")
```

- [ ] **Step 4: Run the affected files**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q -p no:warnings -p no:cacheprovider -rf tests/test_service_auth.py tests/test_plays.py tests/test_entered_scores.py tests/test_shared_shots.py tests/test_game_clips_api.py; echo "exit $?"`
Expected: `exit 0`.

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q -p no:warnings -p no:cacheprovider -rf tests/test_service_api.py tests/test_viewer_server.py; echo "exit $?"`
Expected: the only FAILED line is `TestCatalogueAndAdmin::test_the_pages_load`.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/service/api.py tests/test_service_auth.py
git commit -m "Only a chart's owner or its team's members save its grading

may_edit_chart guards POST overrides.json, merge and whole document alike:
401 signed out, 403 for anyone else. A chart nobody owns stays open to its
link until claimed; accounts off is unchanged.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `access.json` and the guarded page flag

**Files:**
- Modify: `src/curling_score/service/api.py`:
  - a new `chart_access` after `may_edit_chart`;
  - inside `register_chart_routes`, `chart_page` (≈ line 1682) and a new `access_json` route registered **before** `chart_asset`. `chart_asset`'s `/{asset}` route catches every other name, so a route registered after it is never reached.
- Test: `tests/test_service_auth.py`

**Interfaces:**
- Consumes: `uid_of`, `may_edit_chart` (Task 2); `MAKER`, `an_unowned_chart`, `a_ready_game` (Task 1).
- Produces:
  - `GET /{c|s}/{key}/access.json` → `{"edit": bool, "reason": "owner"|"team"|"open"|"signin"|"not-yours"|"view-only-link", "team": {"name": str} | null}`, with `Cache-Control: private, no-store`.
  - `window.CHART.guarded: bool`.
  - Tasks 4 and 6 read both.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_service_auth.py`, after `TestOnlyItsMakerChangesAChart`:

```python
def access(w, key, headers=None, kind="c"):
    r = w["client"].get(f"/{kind}/{key}/access.json", headers=headers or {})
    assert r.status_code == 200
    assert r.headers["cache-control"] == "private, no-store"
    return r.json()


class TestWhoTheChartPageUnlocksFor:
    """access.json: what a guarded /c/ page asks before it renders."""

    def test_the_owner_and_everyone_else(self, w):
        sid = a_ready_game(w)
        slug = post(w, "/api/charts", {"source_id": sid}, SARAH).json()["slug"]
        assert access(w, slug, SARAH) == {"edit": True, "reason": "owner", "team": None}
        assert access(w, slug, ALEX) == {"edit": False, "reason": "not-yours", "team": None}
        assert access(w, slug) == {"edit": False, "reason": "signin", "team": None}

    def test_a_team_chart_names_its_team_and_nobody_else(self, w):
        team = _joined_team(w)
        sid = a_ready_game(w)
        slug = post(w, "/api/charts", {"source_id": sid, "team_id": team}, SARAH).json()["slug"]
        named = {"name": "Thistles"}
        assert access(w, slug, ALEX) == {"edit": True, "reason": "team", "team": named}
        assert access(w, slug, MAKER) == {"edit": False, "reason": "not-yours", "team": named}
        assert access(w, slug) == {"edit": False, "reason": "signin", "team": named}

    def test_an_unowned_chart_is_open(self, w):
        assert access(w, an_unowned_chart(w)) == {"edit": True, "reason": "open", "team": None}

    def test_a_share_link_is_view_only_even_for_its_owner(self, w):
        sid = a_ready_game(w)
        got = post(w, "/api/charts", {"source_id": sid}, SARAH).json()
        share = got["share_url"].rstrip("/").split("/")[-1]
        assert access(w, share, SARAH, kind="s") == {
            "edit": False, "reason": "view-only-link", "team": None}

    def test_with_accounts_off_everything_is_open(self, no_accounts):
        sid = a_ready_game(no_accounts)
        slug = post(no_accounts, "/api/charts", {"source_id": sid}).json()["slug"]
        assert access(no_accounts, slug) == {"edit": True, "reason": "open", "team": None}

    def test_only_an_owned_chart_page_asks(self, w):
        sid = a_ready_game(w)
        mine = post(w, "/api/charts", {"source_id": sid}, SARAH).json()
        assert '"guarded": true' in w["client"].get(f"/c/{mine['slug']}/").text
        share = mine["share_url"].rstrip("/").split("/")[-1]
        assert '"guarded": false' in w["client"].get(f"/s/{share}/").text
        assert '"guarded": false' in w["client"].get(f"/c/{an_unowned_chart(w)}/").text

    def test_with_accounts_off_no_page_asks(self, no_accounts):
        sid = a_ready_game(no_accounts)
        slug = post(no_accounts, "/api/charts", {"source_id": sid}).json()["slug"]
        assert '"guarded": false' in no_accounts["client"].get(f"/c/{slug}/").text
```

- [ ] **Step 2: Run them to see them fail**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q -p no:warnings -p no:cacheprovider -rf tests/test_service_auth.py -k "UnlocksFor"; echo "exit $?"`
Expected: every test FAILED. `access.json` falls through to `/{asset}` and gets a 404, and no page carries `guarded`.

- [ ] **Step 3: Implement**

In `api.py`, after `may_edit_chart` add:

```python
    def chart_access(chart: Chart, uid: str | None, read_only: bool) -> dict:
        """What access.json answers: whether this person may edit the chart,
        and why -- the reason picks the page's view-only line. A team's chart
        carries the team's name; a personal chart never names its owner."""
        team = repo.get_team(chart.team_id) if chart.team_id else None
        named = {"name": team.name} if team else None
        if read_only:
            reason = "view-only-link"
        elif not auth.enabled or not (chart.team_id or chart.owner_user_id):
            reason = "open"
        elif may_edit_chart(chart, uid):
            reason = "team" if chart.team_id else "owner"
        else:
            reason = "signin" if uid is None else "not-yours"
        return {"edit": reason in ("owner", "team", "open"), "reason": reason, "team": named}
```

In `chart_page`, add this comment above the `return HTMLResponse(viewer.boot_page({` call:

```python
            # `guarded`: this chart is somebody's or a team's, so the page asks
            # access.json before it unlocks editing (may_edit_chart). Never on
            # a view link, an unowned chart, or with accounts off -- those boot
            # exactly as they always have.
```

Then add this key to the dict, after `"shared": chart.team_id is not None,`:

```python
                "guarded": (not read_only and auth.enabled
                            and bool(chart.owner_user_id or chart.team_id)),
```

Directly after the `overrides_meta_get` route (and so before `export_json` and `chart_asset`) add:

```python
        @app.get(prefix + "/access.json")
        def access_json(key: str, authorization: str | None = Header(default=None)):
            """Whether the page should unlock editing for whoever is asking
            (chart_access). A guarded page asks once, before it renders."""
            chart, _run, read_only = lookup(kind, key)
            return JSONResponse(chart_access(chart, uid_of(authorization), read_only),
                                headers={"Cache-Control": "private, no-store"})
```

- [ ] **Step 4: Run the affected files**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q -p no:warnings -p no:cacheprovider -rf tests/test_service_auth.py tests/test_viewer_server.py; echo "exit $?"`
Expected: `exit 0`.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/service/api.py tests/test_service_auth.py
git commit -m "access.json, and guarded on the pages of owned charts

What a /c/ page asks before it unlocks editing: edit or not, and why.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The page's edit-or-watch decision (`core/wire.mjs`)

**Files:**
- Modify: `frontend/core/wire.mjs`: `modeOf` (line 15), plus the new exports `accessMode` and `viewOnlyText`
- Test: `tests/test_viewer_js.py`

**Interfaces:**
- Consumes: `access.json`'s shape (Task 3).
- Produces (`frontend/core/index.mjs` re-exports `wire.mjs`):
  - `modeOf(chart)` gains `guarded: boolean` and `viewOnly: null`.
  - `accessMode(config, access) -> config`: on yes, `viewOnly: null`; on no, `readOnly: true` and `viewOnly: {reason: string, team: string | null}`.
  - `viewOnlyText(viewOnly | null) -> string`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_viewer_js.py`, after `class TestModes`:

```python
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
```

- [ ] **Step 2: Run them to see them fail**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q -p no:warnings -p no:cacheprovider -rf tests/test_viewer_js.py -k "AsksWhoMayEdit"; echo "exit $?"`
Expected: FAILED. `guarded` is undefined, so JSON prints nothing, and `W.accessMode is not a function`.

- [ ] **Step 3: Implement**

In `frontend/core/wire.mjs`, inside `modeOf`'s returned object, after `merge: !!c.merge,` add:

```js
    // Whether this /c/ page's chart is somebody's or a team's, so the page
    // asks access.json before it unlocks editing (accessMode).
    guarded: !!c.guarded,
    // Why the page is read-only when access.json said so; null otherwise.
    viewOnly: null,
```

After `modeOf` add:

```js
/* The config a guarded /c/ page renders with, from its access.json answer.
 * Asked once, before the first render, so editing never appears and then
 * goes. Anything but a clear yes is a page to watch: no answer means sign-in
 * was slow or the network was down, and an editable page whose saves would
 * all be refused is worse than one that says so. */
export function accessMode(config, access) {
  if (!access || typeof access.edit !== "boolean")
    return { ...config, readOnly: true, viewOnly: { reason: "unchecked", team: null } };
  if (access.edit) return { ...config, viewOnly: null };
  return { ...config, readOnly: true,
           viewOnly: { reason: access.reason, team: access.team?.name ?? null } };
}

/* The header's line on a page that may not edit (accessMode's viewOnly). */
export function viewOnlyText(why) {
  if (!why) return "";
  if (why.reason === "signin")
    return "View only — sign in to edit if this chart is yours or your team's";
  if (why.reason === "not-yours")
    return why.team ? `View only — only team ${why.team} can change this chart`
                    : "View only — only the person or team who made this chart can change it";
  return "View only — couldn't check sign-in. Reload to try again.";
}
```

- [ ] **Step 4: Build, then run the viewer and build tests**

Run: `(cd frontend && npm run build)`
Expected: eslint passes and both bundles are rebuilt.

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q -p no:warnings -p no:cacheprovider -rf tests/test_viewer_js.py tests/test_frontend_build.py; echo "exit $?"`
Expected: `exit 0`.

- [ ] **Step 5: Commit**

```bash
git add frontend/core/wire.mjs tests/test_viewer_js.py src/curling_score/viewer/app.js src/curling_score/service/static/site.js frontend/.buildstamp.json
git commit -m "core: accessMode and viewOnlyText -- a guarded page edits or watches

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Saves carry the token (`runtime/`)

**Files:**
- Modify: `frontend/runtime/auth.mjs`: add `idToken`
- Create: `frontend/runtime/chartAuth.mjs`
- Modify: `frontend/runtime/overridesStore.mjs`: the header comment, `start`, `save`, and the `pagehide` listener
- Test: `tests/test_viewer_js.py`

**Interfaces:**
- Produces:
  - `idToken({force = false, ms} = {}) -> Promise<string | null | undefined>`. `null` means nobody is signed in; `undefined` means no answer.
  - `chartAuth(get = idToken) -> {fresh(force = false): Promise<string|null>, last(): string|null, keepFresh(): () => void}`.
  - `TOKEN_TICK_MS = 300000`.
  - `overridesStore.start(cfg, {overrides, version, auth = null})`, where `auth` is a `chartAuth()` or null.
  - Task 6 builds and passes the `auth`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_viewer_js.py`, after `class TestUnloadBeacon`:

```python
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
        got = self.run("let unhandled = 0; process.on('unhandledRejection', () => unhandled++);\n"
                       "globalThis.fetch = () => Promise.reject(new TypeError('too big'));\n"
                       "S.start(EDIT, {overrides: {}, version: 0, auth});\n"
                       "S.apply({a: {x: 1}}, 'a'); on.pagehide();\n"
                       "await new Promise(r => setImmediate(r));\n"
                       "out({unhandled});")
        assert got == {"unhandled": 0}
```

- [ ] **Step 2: Run them to see them fail**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q -p no:warnings -p no:cacheprovider -rf tests/test_viewer_js.py -k "ChartToken or CarryTheToken"; echo "exit $?"`
Expected: FAILED. `chartAuth.mjs` does not exist, saves carry no `Authorization`, and `pagehide` calls `navigator.sendBeacon`, which node does not have.

- [ ] **Step 3: Implement**

In `frontend/runtime/auth.mjs`, after `whoIsSignedIn` add:

```js
/* The signed-in person's ID token: a string, null for nobody, undefined when
 * auth has not answered within `ms`. Firebase hands back its cached token and
 * renews it itself in the token's last five minutes; `force` renews it now,
 * for a save the server has just refused. */
export async function idToken({ force = false, ms = SIGN_IN_WAIT_MS } = {}) {
  const u = await settleWithin(signedIn(), ms, undefined);
  if (!u) return u;
  return settleWithin(u.getIdToken(force), ms, undefined);
}
```

Create `frontend/runtime/chartAuth.mjs`:

```js
/* The token a guarded chart's saves carry (api.py may_edit_chart).
 *
 * `fresh()` is asked before every save. `last()` is what the close-of-tab
 * save sends: pagehide cannot wait for a promise, so the token has to be in
 * hand already, and keepFresh() keeps it so. Firebase renews a token only in
 * the last five minutes of its hour, and a five-minute tick is sure to land
 * in that window. */
import { idToken } from "./auth.mjs";

export const TOKEN_TICK_MS = 5 * 60 * 1000;

export function chartAuth(get = idToken) {
  let last = null;

  async function fresh(force = false) {
    const t = await get({ force });
    if (t === undefined) return last;   // no answer: keep what we had
    last = t;                           // a token, or null once signed out
    return last;
  }

  /* Started once by the page; the returned function stops it. */
  function keepFresh() {
    const id = setInterval(() => { fresh(); }, TOKEN_TICK_MS);
    const onShow = () => { if (document.visibilityState === "visible") fresh(); };
    addEventListener("visibilitychange", onShow);
    return () => { clearInterval(id); removeEventListener("visibilitychange", onShow); };
  }

  return { fresh, last: () => last, keepFresh };
}
```

In `frontend/runtime/overridesStore.mjs`:

1. In the header comment, change `the beacon sends only dirty keys so a closing tab cannot erase a teammate's work.` to `the close-of-tab save sends only dirty keys so a closing tab cannot erase a teammate's work, and a guarded chart's saves carry its person's token (chartAuth).`

2. Below `let onNotice = () => {};` add:

```js
let auth = null;              // chartAuth() on a guarded chart this person edits
```

3. Replace `start` with:

```js
export function start(cfg, { overrides: initial, version: v, auth: a = null }) {
  config = cfg;
  overrides = initial || {};
  version = v ?? null;
  auth = a;
  setStatus("saved", Object.keys(overrides).length ? "loaded" : "no edits yet");
  if (config.merge && !config.readOnly && config.shared) poll();
}
```

4. Above `export async function save()` add:

```js
/* A save's headers. A guarded chart's carry the person's token (api.py
 * may_edit_chart); every other page's carry none, as they always have. */
function headersWith(token) {
  return token ? { "Content-Type": "application/json", Authorization: `Bearer ${token}` }
               : { "Content-Type": "application/json" };
}

const refused = res => res.status === 401 || res.status === 403;
```

5. In `save()`, replace

```js
    const res = await fetch(saveUrl(config, version), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
```

with

```js
    const url = saveUrl(config, version);
    const body = JSON.stringify(payload);
    let res = await fetch(url, { method: "POST", headers: headersWith(await auth?.fresh()), body });
    // Refused: perhaps only a token that ran out while the tab slept. Renew
    // it once and try again; a second refusal is not something retrying can
    // fix, so the keys wait for the person to sign in, and ⬇ is still there.
    if (refused(res) && auth)
      res = await fetch(url, { method: "POST", headers: headersWith(await auth.fresh(true)), body });
    if (refused(res)) {
      for (const k of sent) dirty.add(k);
      setStatus("failed", "not saved — sign in again");
      return;
    }
```

The existing `if (res.status === 409) { ... }` block follows unchanged.

6. Replace the `pagehide` listener with:

```js
/* Registered once at module scope, as it was before: a listener that came and
 * went with a component could fire after unmount or not at all.
 *
 * A keepalive fetch, not sendBeacon: a beacon cannot carry the token a
 * guarded chart's save needs. The token is the one in hand (chartAuth's
 * last()), since nothing can be awaited here. Whatever goes wrong -- no
 * network, a body over keepalive's 64 KB -- is swallowed: the page is going. */
if (typeof addEventListener === "function")
  addEventListener("pagehide", () => {
    const beacon = unloadBeacon(config, overrides, dirty);
    if (!beacon) return;
    try {
      fetch(beacon.url, { method: "POST", keepalive: true,
                          headers: headersWith(auth?.last()),
                          body: JSON.stringify(beacon.body) }).catch(() => {});
    } catch { /* fetch can throw at once on a keepalive body it will not take */ }
  });
```

- [ ] **Step 4: Build, then run the viewer and build tests**

Run: `(cd frontend && npm run build)`
Expected: eslint passes and both bundles are rebuilt.

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q -p no:warnings -p no:cacheprovider -rf tests/test_viewer_js.py tests/test_frontend_build.py; echo "exit $?"`
Expected: `exit 0`.

- [ ] **Step 5: Commit**

```bash
git add frontend/runtime/auth.mjs frontend/runtime/chartAuth.mjs frontend/runtime/overridesStore.mjs tests/test_viewer_js.py src/curling_score/viewer/app.js src/curling_score/service/static/site.js frontend/.buildstamp.json
git commit -m "viewer: saves carry the token; the close-of-tab save is a keepalive fetch

A refusal renews the token once, then says 'not saved — sign in again' and
stops retrying. chartAuth keeps the last token fresh every 5 minutes for
the save that cannot wait.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: The guarded page boots, and says when it is view-only

**Files:**
- Modify: `frontend/viewer/main.jsx`: the boot
- Create: `frontend/viewer/ViewOnly.jsx`
- Modify: `frontend/viewer/App.jsx`: one line after the `#save` span (≈ line 584)
- Modify: `src/curling_score/viewer/style.css`: the header order block (≈ line 571) and the phone block (≈ line 812)
- Modify: `frontend/site/auth.js`: the header comment only
- Test: `tests/test_viewer_js.py`

**Interfaces:**
- Consumes: `accessMode`, `viewOnlyText` (Task 4); `chartAuth` (Task 5); `whoIsSignedIn`, `signInNow` (existing in `runtime/auth.mjs`); `access.json` (Task 3).
- Produces: `#viewOnly` (a `.pill` holding `.long` and `.short` spans) and `#viewOnlySignIn` in the viewer header. Task 9 checks both in a browser.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_viewer_js.py`, after `class TestSavesCarryTheToken`:

```python
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
        assert "if (!base.guarded) return null;" in main

    def test_slow_sign_in_is_no_answer_not_signed_out(self):
        """A slow SDK must not tell the owner their own chart is someone else's."""
        main = self.src("frontend/viewer/main.jsx")
        loader = main[main.index("async function loadAccess()"):]
        assert "if (who === undefined) return null;" in loader[:400]

    def test_only_an_editable_guarded_page_keeps_a_token(self):
        main = self.src("frontend/viewer/main.jsx")
        assert "const auth = base.guarded && !config.readOnly ? chartAuth() : null;" in main
        assert "store.start(config, { overrides: ov.map, version: ov.version, auth });" in main

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
```

- [ ] **Step 2: Run them to see them fail**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q -p no:warnings -p no:cacheprovider -rf tests/test_viewer_js.py -k "GuardedPage"; echo "exit $?"`
Expected: FAILED (the boot is unchanged; `ViewOnly.jsx` is missing).

- [ ] **Step 3: Implement**

In `frontend/viewer/main.jsx`:

1. Change the core import to `import { LIVE_POLL_MS, acceptLiveDoc, accessMode, modeOf } from "../core/index.mjs";`. Below `import * as store from "../runtime/overridesStore.mjs";` add:

```js
import { whoIsSignedIn } from "../runtime/auth.mjs";
import { chartAuth } from "../runtime/chartAuth.mjs";
```

2. Replace `const config = modeOf(window.CHART);` with `const base = modeOf(window.CHART);`. In `loadOverrides`, change `config.review` to `base.review`.

3. After `loadOverrides` add:

```js
/* On a guarded page -- a chart somebody or a team owns -- whether this person
 * may edit it (api.py access.json). Asked alongside the timeline, so it costs
 * little time, and before the first render, so editing never appears and then
 * goes. Sign-in that does not answer in time is no answer (accessMode reads
 * it as "unchecked"), not "signed out": a slow SDK must not tell the owner
 * their own chart is someone else's. */
async function loadAccess() {
  if (!base.guarded) return null;
  const who = await whoIsSignedIn();
  if (who === undefined) return null;
  try {
    const r = await fetch("access.json",
      who ? { headers: { Authorization: `Bearer ${who.token}` } } : {});
    return r.ok ? await r.json() : null;
  } catch {
    return null;
  }
}
```

4. Change `function Live({ first, cursor }) {` to `function Live({ first, cursor, config }) {`.

5. Replace the final `Promise.all(...)` chain's head, up to and including the `.render(...)` call, with:

```js
Promise.all([fetch("timeline.json").then(r => r.json()), loadOverrides(), loadAccess()])
  .then(([doc, ov, access]) => {
    const config = base.guarded ? accessMode(base, access) : base;
    // A guarded chart this person may edit saves with their token, kept
    // fresh for the close-of-tab save (chartAuth); any other page sends none.
    const auth = base.guarded && !config.readOnly ? chartAuth() : null;
    if (auth) { auth.keepFresh(); auth.fresh(); }
    store.start(config, { overrides: ov.map, version: ov.version, auth });
    createRoot(document.getElementById("root"))
      .render(<Live first={doc} cursor={loadCursor(config.slug)} config={config} />);
  })
```

The `.catch(err => ...)` that follows is unchanged.

Create `frontend/viewer/ViewOnly.jsx`:

```jsx
/* The header's line on a chart page this person may not edit (core
 * accessMode / viewOnlyText). A phone's header is one 48px row, so there it
 * says just "View only", and the whole line is the title. "Sign in" shows
 * only when signing in could change the answer; the page then loads again,
 * so the boot asks access.json once more, with the new token. */
import { viewOnlyText } from "../core/index.mjs";
import { signInNow } from "../runtime/auth.mjs";

export function ViewOnly({ why }) {
  const text = viewOnlyText(why);
  const signIn = async () => {
    try { await signInNow(); location.reload(); } catch { /* the popup was shut */ }
  };
  return (
    <>
      <span id="viewOnly" className="pill" title={text}>
        <span className="long">{text}</span><span className="short">View only</span>
      </span>
      {why.reason === "signin"
        ? <button id="viewOnlySignIn" onClick={signIn}>Sign in</button> : null}
    </>
  );
}
```

In `frontend/viewer/App.jsx`, add `import { ViewOnly } from "./ViewOnly.jsx";` beside the other local component imports. Directly after the `<span id="save" ...>{status.text}</span>` element add:

```jsx
      {config.viewOnly ? <ViewOnly why={config.viewOnly} /> : null}
```

In `src/curling_score/viewer/style.css`:

1. In the `@media (min-width: 641px)` header-order block, after `header > #save     { order: 9; }`, add:

```css
  header > #viewOnly       { order: 9; }
  header > #viewOnlySignIn { order: 9; }
```

2. Directly above `/* Watch mode is a phone layout only.`, add:

```css
/* A guarded chart this person may not edit says so where the save status
   would be (ViewOnly.jsx): the whole line on a desktop, "View only" on a
   phone, whose header is one 48px row. */
#viewOnly { white-space: normal; }
#viewOnly .short { display: none; }
```

3. In the `@media (max-width: 640px) and (min-height: 461px)` block, after `#save { flex: 0 0 auto; }`, add:

```css
  #viewOnly { flex: 0 0 auto; white-space: nowrap; }
  #viewOnly .long { display: none; }
  #viewOnly .short { display: inline; }
  #viewOnlySignIn { min-height: 44px; flex: 0 0 auto; }
```

In `frontend/site/auth.js`, replace the header paragraph that begins `Deliberately not loaded by the charting viewer.` and ends `keeps working offline under \`curling-score serve\`.` with:

```js
 * The charting viewer loads this only where it has to: on a guarded /c/
 * page (a chart somebody or a team owns), whose saves carry the person's
 * token (api.py may_edit_chart), and when somebody opens Flag, I played… or
 * Name teams. A view link, a review page, a chart nobody owns and the local
 * `curling-score serve` never load it, so they keep working offline.
```

- [ ] **Step 4: Build, then run the viewer and build tests**

Run: `(cd frontend && npm run build)`
Expected: eslint passes and both bundles are rebuilt.

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q -p no:warnings -p no:cacheprovider -rf tests/test_viewer_js.py tests/test_frontend_build.py tests/test_viewer_server.py; echo "exit $?"`
Expected: `exit 0`.

- [ ] **Step 5: Commit**

```bash
git add frontend/viewer/main.jsx frontend/viewer/ViewOnly.jsx frontend/viewer/App.jsx frontend/site/auth.js src/curling_score/viewer/style.css tests/test_viewer_js.py src/curling_score/viewer/app.js src/curling_score/service/static/site.js frontend/.buildstamp.json
git commit -m "viewer: a guarded chart page asks who may edit before it renders

Editable for its owner or team, with the token kept fresh; a view-only line
with Sign in for anyone else ('View only' on a phone).

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Chart and Submit sign you in first

**Files:**
- Modify: `frontend/site/auth.js`: add `ensureSignedIn`
- Modify: `frontend/site/Games.jsx`: `Actions` (≈ line 236) and the `chartnote` paragraph (≈ line 398)
- Modify: `frontend/site/Submit.jsx`
- Test: `tests/test_viewer_js.py`

**Interfaces:**
- Consumes: the 401 `sign in to chart` (Task 1).
- Produces: `ensureSignedIn() -> Promise<User>` in `frontend/site/auth.js`. It throws if the popup is shut.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_viewer_js.py`, after `class TestTheGuardedPage`:

```python
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
```

- [ ] **Step 2: Run them to see them fail**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q -p no:warnings -p no:cacheprovider -rf tests/test_viewer_js.py -k "SignsYouInFirst"; echo "exit $?"`
Expected: FAILED.

- [ ] **Step 3: Implement**

In `frontend/site/auth.js`, after `export async function signIn() { ... }` add:

```js
/** Sign in if nobody is, and resolve once this page knows who.
 *
 * signInWithPopup can settle a moment before onAuthStateChanged has told this
 * module, and authedFetch reads the module's `user`, so the caller waits for
 * that rather than for the popup. Throws if the popup was shut. */
export async function ensureSignedIn() {
  if (user) return user;
  await signIn();
  if (user) return user;
  return new Promise(resolve => {
    let off = null;
    off = onUser(u => { if (u) { off?.(); resolve(u); } });
  });
}
```

In `frontend/site/Games.jsx`:

1. Change `import { authedFetch } from "./auth.js";` to `import { authedFetch, ensureSignedIn } from "./auth.js";`.

2. In `function Actions({ game, mine, team }) {`, below `const [busy, setBusy] = useState(false);`, add `const { user, accounts } = useAuthUser();`.

3. In `chart()`, directly after `setBusy(true);`, add:

```js
    // With accounts on, a chart needs somebody to belong to (api.py
    // may_edit_chart): sign in first, then carry on. A shut popup is a change
    // of mind, not an error.
    if (accounts && !user) {
      try { await ensureSignedIn(); } catch { setBusy(false); return; }
    }
```

4. Replace the `chartnote` span with:

```jsx
        <span className="chartnote"><strong>Chart</strong> starts your own chart of the
        game. Only you, or your team, can change it; send the link and others can
        watch.</span></p>
```

In `frontend/site/Submit.jsx`:

1. Change the auth import to `import { authedFetch, ensureSignedIn } from "./auth.js";`. Add `import { useAuthUser } from "./useAuth.js";`.

2. Below `const [msg, setMsg] = useState(null);` add:

```js
  const { user, accounts } = useAuthUser();
  // With accounts on, the link is to a chart, and a chart needs an owner.
  const needsSignIn = accounts && !user;
```

3. In `submit`, directly before `setBusy(true);` (the line before `setMsg(...Checking the video…)`), add:

```js
    if (needsSignIn) {
      try { await ensureSignedIn(); } catch { return; }
    }
```

4. Change the button text `Get my link` to `{needsSignIn ? "Sign in to get your link" : "Get my link"}`.

- [ ] **Step 4: Build, then run the tests**

Run: `(cd frontend && npm run build)`
Expected: eslint passes and both bundles are rebuilt.

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q -p no:warnings -p no:cacheprovider -rf tests/test_viewer_js.py tests/test_frontend_build.py; echo "exit $?"`
Expected: `exit 0`.

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q -p no:warnings -p no:cacheprovider -rf tests/test_service_api.py; echo "exit $?"`
Expected: the only FAILED line is `TestCatalogueAndAdmin::test_the_pages_load`. The bundle-words check still finds "Get my link".

- [ ] **Step 5: Commit**

```bash
git add frontend/site/auth.js frontend/site/Games.jsx frontend/site/Submit.jsx tests/test_viewer_js.py src/curling_score/viewer/app.js src/curling_score/service/static/site.js frontend/.buildstamp.json
git commit -m "site: Chart and Submit sign you in first

ensureSignedIn waits until the page knows who, so the request carries the
token. The game list says only you, or your team, can change your chart.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Terms, privacy, README and the API's own words

**Files:**
- Modify: `src/curling_score/service/static/terms.html` (line 30, the "Accounts" paragraph)
- Modify: `src/curling_score/service/static/privacy.html` (lines 32 and 49)
- Modify: `README.md` (the `**Accounts**` paragraph, and the `FIREBASE_PROJECT` sentence after it)
- Modify: `src/curling_score/service/api.py`: the module docstring (lines 9–11), the claim route's docstring (≈ line 1544), and the account-routes comment (≈ line 1437)
- Test: `tests/test_service_api.py`

- [ ] **Step 1: Write the failing test**

Add to `TestCatalogueAndAdmin` in `tests/test_service_api.py`, after `test_the_terms_and_privacy_pages_load_without_a_script`:

```python
    def test_the_terms_and_privacy_say_who_may_change_a_chart(self, world):
        c = world["client"]
        terms = " ".join(c.get("/terms").text.split())
        privacy = " ".join(c.get("/privacy").text.split())
        assert "edit a chart you have a link to" not in terms
        assert "only the person or team who made a chart can change it" in terms
        assert ("A chart's link shows that chart to whoever has it. Only the person or"
                " team who made it can change it.") in privacy
        assert "Charts you make, edits made through a chart's link" not in privacy
```

- [ ] **Step 2: Run it to see it fail**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q -p no:warnings -p no:cacheprovider -rf tests/test_service_api.py -k "who_may_change_a_chart"; echo "exit $?"`
Expected: FAILED.

- [ ] **Step 3: Change the words**

`terms.html`, replace the paragraph that begins `<p>You don't need an account to watch games or to edit a chart you have a link to.` with:

```html
<p>You don't need an account to watch games or to look at a chart someone sends you. You need to sign in with a Google account to chart a game, and only the person or team who made a chart can change it. Signing in is also needed for naming teams, entering scores, saying which rocks you threw, clips, and teams on the site. Naming teams also needs your account to be validated by the site's operator or an admin they appoint.</p>
```

`privacy.html`:
- Replace `<p>A chart's link opens that chart for whoever has it.</p>` with `<p>A chart's link shows that chart to whoever has it. Only the person or team who made it can change it.</p>`.
- Replace the paragraph that begins `<p>Charts you make, edits made through a chart's link, and flags are kept without a name.` with:

```html
<p>Flags, and edits made through the link of an older chart nobody owns, are kept without a name. To limit abuse, the site keeps a scrambled, one-way code made from your IP address (a salted hash) with charts created and flags sent. The same code is kept in rate-limit counters for two days. The address itself can't be read back from the code.</p>
```

In both pages, `Last updated` already reads `October 15, 2026`. Change it only if this lands on a later day.

`README.md`: replace the paragraph that begins `**Accounts** are optional and purely additive.` with:

```markdown
**Accounts.** Watching needs no account, and neither does looking at a chart
someone sends you. Charting does: sign in with Google and the chart is yours,
listed at `/mine`, and only you can change it. A **team** shares one chart per
game, so everyone fills in the same sheet rather than four of them; only the
team's current members can change it, and members see the same list. Anyone
else holding a chart's link sees it view-only. Charts made before charting
needed an account belong to nobody and stay editable by their link until
someone claims one at `/mine`. You can also name a game's league, and its
teams once an admin has validated your account: team names are public and
usually a skip's surname. Admins validate accounts at `/users`;
`scripts/users.py` lists the accounts and is the only way to make or unmake an
admin.
```

In the next paragraph, change `and the service behaves exactly as it did before any of it was added.` to `and the service behaves exactly as it did before any of it was added: anyone charts, and a chart's link is all it takes to change it.`

`api.py`:
- Module docstring: replace

```
The three human surfaces differ in what holding the URL means. ``/g/`` is
public and grants nothing; the other two are unguessable, and holding one *is*
the permission it carries. The kind of URL decides the mode, always.
```

with

```
The three human surfaces differ in what holding the URL means. ``/g/`` is
public and grants nothing; ``/s/`` is unguessable and shows a chart read-only.
``/c/`` is unguessable too, but with accounts on, holding it lets you change
only a chart that is yours or your team's (``may_edit_chart``); anyone else
watches. The kind of URL still decides the most a link can do.
```

- `api_claim_chart` docstring: replace `Holding the chart id is the whole permission -- it is the same thing that lets you edit -- so nothing else is asked for.` with `Holding the chart id is what lets you claim a chart nobody owns -- for one made before charting needed an account, the link was its whole permission -- and claiming it makes it yours, so it then locks to you (may_edit_chart).`
- Account-routes comment: replace its two lines after the dashed header with:

```python
    # Accounts exist so a person can find their charts again, so a team can
    # share one, and -- with accounts on -- so a chart belongs to someone: only
    # its owner, or its team's members, may change it (may_edit_chart). A
    # chart URL still shows the chart to whoever holds it.
```

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q -p no:warnings -p no:cacheprovider -rf tests/test_service_api.py tests/test_service_auth.py; echo "exit $?"`
Expected: the only FAILED line is `TestCatalogueAndAdmin::test_the_pages_load`.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/service/static/terms.html src/curling_score/service/static/privacy.html README.md src/curling_score/service/api.py tests/test_service_api.py
git commit -m "Terms, privacy, README: only a chart's maker changes it

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Check it in a real browser

Nothing in this task is committed unless a check fails and needs a fix. A fix goes in its own commit, with a test that would have caught it.

**Files:**
- Create (scratchpad, not the repo): `$SCRATCH/chart-guard-check/serve.py` and `$SCRATCH/chart-guard-check/check.mjs`. `$SCRATCH` is the session's scratchpad directory.

- [ ] **Step 1: Run the affected test files once more**

Run, one line at a time:

```bash
PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q -p no:warnings -p no:cacheprovider -rf tests/test_service_auth.py tests/test_plays.py tests/test_entered_scores.py tests/test_shared_shots.py tests/test_game_clips_api.py; echo "exit $?"
PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q -p no:warnings -p no:cacheprovider -rf tests/test_viewer_js.py tests/test_frontend_build.py tests/test_viewer_server.py; echo "exit $?"
PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q -p no:warnings -p no:cacheprovider -rf tests/test_service_api.py tests/test_practice_api.py tests/test_thinking_api.py; echo "exit $?"
```

Expected: `exit 0` for the first two. The third shows only the known `test_the_pages_load` failure.

- [ ] **Step 2: Write the local server**

`$SCRATCH/chart-guard-check/serve.py`:

```python
"""Throwaway: the real app in memory -- Sarah's chart, Thistles' chart
(Sarah + Alex), and one nobody owns -- served on :8766."""
import json
import sys
WT = "/home/tcuser/src/curling_score/.claude/worktrees/chart-guard"
sys.path[:0] = [WT, WT + "/src"]

import uvicorn
from curling_score.service.auth import FakeVerifier
from tests.test_service_auth import (MAKER, SARAH, _joined_team, _world, a_ready_game,
                                     an_unowned_chart, post)

auth = FakeVerifier()
auth.add("tok-sarah", "uid-sarah", "sarah@example.org", "Sarah")
auth.add("tok-alex", "uid-alex", "alex@example.org", "Alex")
auth.add("tok-maker", "uid-maker", "maker@example.org", "Maker")
auth.add("tok-stranger", "uid-stranger", "stranger@example.org", "Stranger")
w = _world(auth)
sid = a_ready_game(w)
team = _joined_team(w)
personal = post(w, "/api/charts", {"source_id": sid}, SARAH).json()["slug"]
teamed = post(w, "/api/charts", {"source_id": sid, "team_id": team}, SARAH).json()["slug"]
unowned = an_unowned_chart(w)
print(json.dumps({"sid": sid, "personal": personal, "team": teamed, "unowned": unowned}),
      flush=True)
uvicorn.run(w["client"].app, host="127.0.0.1", port=8766, log_level="warning")
```

Start it in the background: `/home/tcuser/src/curling_score/.venv/bin/python $SCRATCH/chart-guard-check/serve.py > $SCRATCH/chart-guard-check/serve.log 2>&1`. Wait until `curl -s -o /dev/null http://127.0.0.1:8766/api/healthz` succeeds, then read the slugs from the first line of `serve.log`.

Start Chrome in the background: `google-chrome --headless=new --disable-gpu --no-sandbox --remote-debugging-port=9334 --user-data-dir=$SCRATCH/chart-guard-check/chrome about:blank`.

- [ ] **Step 3: Write and run the checks**

`$SCRATCH/chart-guard-check/check.mjs`:

```js
// Throwaway: drive the local server as each person, with a fake Firebase SDK.
import fs from "node:fs";
import { attach } from "/home/tcuser/src/curling_score/.claude/worktrees/chart-guard/scripts/cdp.mjs";

const OUT = new URL(".", import.meta.url).pathname;
const BASE = "http://127.0.0.1:8766";
const S = JSON.parse(process.argv[2]);           // {sid, personal, team, unowned}

const FAKE_APP = "export const initializeApp = () => ({});";
const FAKE_AUTH = `
let told = null;
const who = () => {
  const tok = localStorage.getItem("fakeTok");
  return tok ? { uid: tok, displayName: tok, email: tok + "@x",
                 getIdToken: async () => localStorage.getItem("fakeTok") } : null;
};
export const getAuth = () => ({});
export const connectAuthEmulator = () => {};
export class GoogleAuthProvider {}
export async function signInWithPopup() {
  localStorage.setItem("fakeTok", localStorage.getItem("popupTok") || "tok-sarah");
  setTimeout(() => told && told(who()), 50);
}
export async function signOut() { localStorage.removeItem("fakeTok"); told && told(null); }
export function onAuthStateChanged(auth, cb) { told = cb; setTimeout(() => cb(who()), 0); return () => {}; }`;

const cdp = await attach("about:blank");
cdp.ws.addEventListener("message", async ev => {
  const m = JSON.parse(ev.data);
  if (m.method !== "Fetch.requestPaused") return;
  const body = m.params.request.url.endsWith("firebase-app.js") ? FAKE_APP : FAKE_AUTH;
  await cdp.send("Fetch.fulfillRequest", {
    requestId: m.params.requestId, responseCode: 200,
    responseHeaders: [{ name: "Content-Type", value: "text/javascript" },
                      { name: "Access-Control-Allow-Origin", value: "*" }],
    body: Buffer.from(body).toString("base64"),
  });
});
await cdp.send("Fetch.enable", { patterns: [{ urlPattern: "https://www.gstatic.com/*" }] });

const sleep = ms => new Promise(r => setTimeout(r, ms));
const view = (w, h, mobile = false) => cdp.send("Emulation.setDeviceMetricsOverride",
  { width: w, height: h, deviceScaleFactor: 1, mobile });
async function go(url, wait = 4000) { await cdp.send("Page.navigate", { url }); await sleep(wait); }
async function as(tok) {
  await go(BASE + "/terms", 800);
  await cdp.eval(tok ? `localStorage.setItem("fakeTok", ${JSON.stringify(tok)}); 1`
                     : `localStorage.removeItem("fakeTok"); 1`);
}
async function shot(name) {
  const { data } = await cdp.send("Page.captureScreenshot", { format: "png" });
  fs.writeFileSync(OUT + name, Buffer.from(data, "base64"));
}
const state = () => cdp.eval(`({mode: document.body.dataset.mode || "",
  viewOnly: document.querySelector("#viewOnly .long")?.innerText ?? null,
  signIn: !!document.getElementById("viewOnlySignIn"),
  save: document.getElementById("save")?.innerText ?? null})`);
const grading = slug => cdp.eval(`fetch("${BASE}/c/${slug}/overrides.json").then(r => r.json())`);
const report = {};

await view(1200, 800);

// Sarah, on her own chart: editable, and a save lands with her token.
await as("tok-sarah");
await go(`${BASE}/c/${S.personal}/`);
report.ownerPage = await state();
await cdp.eval(`document.getElementById("markCharted").click(); 1`);
await sleep(2000);
report.ownerSaved = Object.keys(await grading(S.personal)).length;
// The close-of-tab save: an edit to another shot, then away before the
// 800 ms debounce runs out.
await cdp.eval(`document.getElementById("next").click();
                document.getElementById("markCharted").click(); 1`);
await cdp.send("Page.navigate", { url: BASE + "/terms" });
await sleep(1500);
report.ownerAfterClose = Object.keys(await grading(S.personal)).length;

// Alex, on Sarah's chart: view-only, and why.
await as("tok-alex");
await go(`${BASE}/c/${S.personal}/`);
report.strangerPage = await state();
await shot("stranger-desktop.png");

// Alex, on the team's chart: editable.
await go(`${BASE}/c/${S.team}/`);
report.teammatePage = await state();

// The stranger, on the team's chart: names the team.
await as("tok-stranger");
await go(`${BASE}/c/${S.team}/`);
report.strangerOnTeam = await state();

// Signed out, on Sarah's chart: view-only with Sign in; on a phone too.
await as(null);
await go(`${BASE}/c/${S.personal}/`);
report.signedOut = await state();
await view(390, 844, true);
await go(`${BASE}/c/${S.personal}/`);
report.phone = await cdp.eval(`({width: document.documentElement.scrollWidth,
  short: getComputedStyle(document.querySelector("#viewOnly .short")).display,
  long: getComputedStyle(document.querySelector("#viewOnly .long")).display})`);
await shot("signed-out-phone.png");
await view(1200, 800);

// Signed out, on the unowned chart: editable, as before.
await go(`${BASE}/c/${S.unowned}/`);
report.unownedPage = await state();

// Signed out, the game list's Chart: signs in (as Alex, by the fake popup), then charts.
await cdp.eval(`localStorage.setItem("popupTok", "tok-alex"); 1`);
await go(BASE + "/");
await cdp.eval(`document.querySelector("button.chart").click(); 1`);
await sleep(4000);
report.chartLandedOn = await cdp.eval("location.pathname");

console.log(JSON.stringify(report, null, 1));
process.exit(0);
```

Run: `CDP_PORT=9334 timeout 180 node $SCRATCH/chart-guard-check/check.mjs '<the JSON line from serve.log>'`

Expected:
- `ownerPage`: `{mode: "", viewOnly: null, signIn: false, save: ...}`.
- `ownerSaved` ≥ 1, and `ownerAfterClose` > `ownerSaved`, so the close-of-tab save landed.
- `strangerPage`: `mode "view"`, viewOnly `View only — only the person or team who made this chart can change it`, `signIn false`.
- `teammatePage.mode` = `""`.
- `strangerOnTeam.viewOnly` = `View only — only team Thistles can change this chart`.
- `signedOut`: `mode "view"`, viewOnly `View only — sign in to edit if this chart is yours or your team's`, `signIn true`.
- `phone`: `{width: 390, short: "inline", long: "none"}`.
- `unownedPage.mode` = `""`.
- `chartLandedOn` starts with `/c/`.

Then open `stranger-desktop.png` and `signed-out-phone.png` (Read tool). The view-only line and Sign in must sit in the header, with nothing clipped or overlapping.

- [ ] **Step 4: Stop the server and Chrome**

Stop both background tasks (TaskStop). Check that `ss -ltn | grep -E ":8766 |:9334 "` prints nothing.

- [ ] **Step 5: Report**

Report each expectation that held. For any that failed: fix it with a test that pins the failure, commit, and re-run the check.
