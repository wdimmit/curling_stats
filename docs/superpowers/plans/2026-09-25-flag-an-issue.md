# Flag an Issue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** a **⚑ Flag** button in the viewer that sends a note about the rock on
screen to the cloud, where the owner can list and resolve it.

**Architecture:**
- **Record:** a new `Flag` record behind the existing `Repo` interface, in
  memory and in Firestore.
- **Routes:** `POST /api/flags` resolves the page's own path (`/c/`, `/s/` or
  `/g/`) to a chart, source and run on the server. Two admin routes list and
  resolve flags.
- **Viewer:** a native `<dialog>` snapshots the rock when it opens, loads
  sign-in lazily, and posts.
- **Script:** `scripts/flags.py` reads flags back through the admin routes.

**Tech Stack:** Python 3.12 + FastAPI (`src/curling_score/service/`), Firestore;
React 19 JSX built by esbuild (`frontend/`, `npm run build`); pytest; plain-ESM
core tested under node from pytest.

**Spec:** `docs/superpowers/specs/2026-09-25-flag-an-issue-design.md`

## Global Constraints

- **Naming.** The feature is "flag". Never use "report": that name belongs to
  the printable stats page (`#reportBtn`, `Report.jsx`, `ui.reporting`).
- **Taken names.** Never use the id `#flags` or the component name `Flags`.
  Both already exist in `App.jsx`, for the warnings about a rock. Use
  `#flagBtn`, `#flagDialog`, `#flagNote` and `FlagDialog`.
- **No contact field.** A flag carries `user = {uid, email}` only when the
  request has a valid sign-in, otherwise `null`.
- **The note.** It is trimmed and must be 1–2,000 characters
  (`MAX_FLAG_NOTE = 2_000` in Python, `NOTE_MAX = 2000` in JS).
- **Request cap.** `MAX_FLAG_BYTES = 8_000`.
- **Rate limit.** 20 flags per IP per hour and 100 per day, under the key
  `"flag:" + ip_hash`.
- **`where` is the server's.** It comes from `lookup()` or `lookup_source()` and
  is never taken from the request body.
- **No writes under `/g/`.** `POST /g/...` must stay 405.
- **Sign-in wait.** Send never waits more than 3 s for sign-in to settle.
- **After sending.** Success shows "Thanks, flagged." and closes after 1.5 s.
  Failure shows the server's `detail`, or "Could not send. Try again.", and
  keeps the text.
- **The button.** It is hidden when `config.hosted` is false. It is **not**
  hidden in review mode.
- **Build.** After any `frontend/` edit, run `npm run build` in `frontend/`,
  never esbuild directly. Commit `src/curling_score/viewer/app.js` and
  `frontend/.buildstamp.json` with the change.
- **Commands.**
  - Python tests: `/home/tcuser/src/curling_score/.venv/bin/pytest` from the
    worktree root (pytest's `pythonpath = ["src"]` puts the worktree's code
    first).
  - `frontend/node_modules` is a symlink to the main checkout's.
  - Never run the whole suite: it runs out of memory on this machine. Run the
    files named in each step.

## Review Focus

These are inputs the spec implies but no happy-path test exercises, most
likely to bite first. Each one has a pinning test in the task named.

1. **Keys pressed while the dialog is open,** with focus on Send or Cancel
   rather than the textarea. They must not step, grade or seek the rock
   underneath. Pinned in Task 4:
   `test_keys_do_nothing_underneath_the_open_dialog`.
2. **Flagging from a phone `/s/` or `/g/` link.** The watch layout hides every
   child of `<main>` except `#playCard`, so a dialog rendered inside `<main>`
   would never show. Pinned in Task 4: `test_the_dialog_is_never_inside_main`.
3. **Double-clicking Send, or pressing Enter twice.** One flag must be stored.
   Pinned in Task 4: `test_send_cannot_fire_twice`.
4. **A sign-in check that never settles** (CDN blocked, token refresh hanging).
   The flag must still go, anonymously, within 3 s. Pinned in Task 3:
   `TestSettleWithin`.
5. **A blank rock with no video time, or an end with no rocks.** `rock`, `key`
   and `t_video_s` are null, and the server must accept them. Pinned in Task 2
   (`test_a_place_with_nothing_known_is_accepted`) and Task 3
   (`test_an_end_with_no_rocks`).

---

### Task 1: The `Flag` record and its storage

**Files:**
- Modify: `src/curling_score/service/records.py` (add the `Flag` dataclass after `WatchedPlaylist`)
- Modify: `src/curling_score/service/slug.py` (add `new_flag_id`)
- Modify: `src/curling_score/service/repo.py` (Protocol, `MemoryRepo`, `export_all`/`import_all`)
- Modify: `src/curling_score/service/firestore_repo.py` (collection, methods, export/import)
- Modify: `src/curling_score/service/restore.py:13-16` (`resolved_at` in `_TIME_FIELDS`)
- Test: `tests/test_repo_contract.py`, `tests/test_service_core.py`

**Interfaces:**
- Produces:
  - `records.Flag(id, created_at, note, where={}, place={}, status="open", resolved_at=None, overrides_version=None, user=None, ip_hash=None)`
  - `slug.new_flag_id() -> str`, prefixed `"f_"`
  - `Repo.put_flag(flag) -> None`
  - `Repo.list_flags(status: str | None = None, limit: int = 200) -> list[Flag]`, newest first; `status=None` means all
  - `Repo.resolve_flag(flag_id, now) -> Flag | None`, idempotent: the first `resolved_at` stands
  - `export_all()["flags"]`

- [ ] **Step 1: Write the failing contract tests**

In `tests/test_repo_contract.py`:
- Add `Flag` to the records import.
- Add `"flags"` to the cleanup tuple in `_firestore_repo()`.
- Add this class before `class TestBackup`:

```python
class TestFlags:
    def flag(self, **kw):
        base = dict(id="f_1", created_at=T0, note="the house is wrong",
                    where={"link": "g", "source_id": "s_1", "run_id": "r_1"},
                    place={"end": 4, "rock": 16, "key": "0.4.16"})
        base.update(kw)
        return Flag(**base)

    def test_newest_first_and_filtered_by_status(self, repo):
        repo.put_flag(self.flag(id="f_1", created_at=T0))
        repo.put_flag(self.flag(id="f_2", created_at=at(60)))
        repo.put_flag(self.flag(id="f_3", created_at=at(30), status="resolved",
                                resolved_at=at(40)))
        assert [f.id for f in repo.list_flags("open")] == ["f_2", "f_1"]
        assert [f.id for f in repo.list_flags("resolved")] == ["f_3"]
        assert [f.id for f in repo.list_flags()] == ["f_2", "f_3", "f_1"]
        assert [f.id for f in repo.list_flags(limit=1)] == ["f_2"]

    def test_resolving_once_and_only_once(self, repo):
        repo.put_flag(self.flag())
        got = repo.resolve_flag("f_1", at(90))
        assert got.status == "resolved"
        assert got.resolved_at.timestamp() == at(90).timestamp()
        assert repo.list_flags("open") == []
        again = repo.resolve_flag("f_1", at(200))
        assert again.resolved_at.timestamp() == at(90).timestamp()
        assert repo.resolve_flag("f_nope", at(90)) is None

    def test_the_maps_come_back_as_they_went_in(self, repo):
        repo.put_flag(self.flag(user={"uid": "u1", "email": "a@b.c"}, overrides_version=3))
        f = repo.list_flags()[0]
        assert f.where["source_id"] == "s_1" and f.place["rock"] == 16
        assert f.user == {"uid": "u1", "email": "a@b.c"} and f.overrides_version == 3
```

In `TestBackup.test_export_then_import_into_a_fresh_store`:
- after the `put_playlist` line, add
  `repo.put_flag(Flag(id="f_1", created_at=T0, note="n", place={"end": 1}))`;
- at the end, add
  `assert [f.id for f in fresh.list_flags()] == ["f_1"]`.

In `tests/test_service_core.py`, append:

```python
def test_restore_revives_a_flags_times():
    from curling_score.service.repo import MemoryRepo
    from curling_score.service.restore import restore

    repo = MemoryRepo()
    restore(repo, {"flags": [{"id": "f_1", "note": "n", "status": "resolved",
                              "created_at": "2026-09-25T12:00:00+00:00",
                              "resolved_at": "2026-09-25T13:00:00+00:00"}]})
    f = repo.list_flags("resolved")[0]
    assert f.created_at.hour == 12 and f.resolved_at.hour == 13
```

- [ ] **Step 2: Run them and watch them fail**

Run: `/home/tcuser/src/curling_score/.venv/bin/pytest tests/test_repo_contract.py tests/test_service_core.py -q -p no:cacheprovider`
Expected: FAIL with `ImportError: cannot import name 'Flag'`.

- [ ] **Step 3: Implement the record, the id, both repositories and restore**

`records.py`, after `WatchedPlaylist`:

```python
@dataclass
class Flag:
    """Somebody pointed at a rock and said what is wrong with it.

    `where` is worked out by the server from the link the flag was sent from
    -- chart, source, run, video, pipeline version -- and is never taken from
    the browser; `place` is what the browser showed. Kept out of the chart
    entirely: overrides flow into export.json and on into training labels,
    and a complaint about a chart is not a label.
    """

    id: str
    created_at: datetime
    note: str
    where: dict = field(default_factory=dict)
    place: dict = field(default_factory=dict)
    status: str = "open"                  # "open" | "resolved"
    resolved_at: datetime | None = None
    overrides_version: int | None = None
    user: dict | None = None              # {"uid", "email"} when signed in
    ip_hash: str | None = None

    to_dict = asdict
    from_dict = classmethod(_from_dict)
```

`slug.py`, after `new_team_id`:

```python
def new_flag_id() -> str:
    return new_slug(SHORT_BYTES, "f_")
```

`repo.py`:
- Add `Flag` to the records import.
- In `class Repo(Protocol)`, before `# rate limit`, add:

```python
    # flags
    def put_flag(self, flag: Flag) -> None: ...
    def list_flags(self, status: str | None = None, limit: int = 200) -> list[Flag]: ...
    def resolve_flag(self, flag_id: str, now: datetime) -> Flag | None: ...
```

In `MemoryRepo.__init__`, add `self.flags: dict[str, Flag] = {}`. Before
`# ---- rate limit`, add:

```python
    # ---- flags --------------------------------------------------------
    def put_flag(self, flag):
        with self._lock:
            self.flags[flag.id] = flag

    def list_flags(self, status=None, limit=200):
        out = [f for f in self.flags.values() if status is None or f.status == status]
        out.sort(key=lambda f: f.created_at, reverse=True)
        return out[:limit]

    def resolve_flag(self, flag_id, now):
        with self._lock:
            got = self.flags.get(flag_id)
            if got is None:
                return None
            if got.status != "resolved":
                got.status, got.resolved_at = "resolved", now
            return got
```

In `MemoryRepo.export_all`, add `"flags": [f.to_dict() for f in self.flags.values()],`.
In `import_all`, before the claims comment, add:

```python
            for d in data.get("flags", []):
                self.put_flag(Flag.from_dict(d))
```

`firestore_repo.py`:
- Add `Flag` to the records import.
- After the `USERS, TEAMS, ...` line, add `FLAGS = "flags"`.
- Before the rate-limit section, add:

```python
    # ---- flags --------------------------------------------------------
    def put_flag(self, flag):
        self._col(FLAGS).document(flag.id).set(flag.to_dict())

    def list_flags(self, status=None, limit=200):
        # Equality only, sorted here: few flags, and no composite index to add.
        q = self._col(FLAGS) if status is None else self._where(FLAGS, "status", "==", status)
        docs = [Flag.from_dict(d.to_dict()) for d in q.stream()]
        return sorted(docs, key=lambda f: f.created_at, reverse=True)[:limit]

    def resolve_flag(self, flag_id, now):
        ref = self._col(FLAGS).document(flag_id)
        snap = ref.get()
        if not snap.exists:
            return None
        flag = Flag.from_dict(snap.to_dict())
        if flag.status != "resolved":
            ref.update({"status": "resolved", "resolved_at": now})
            flag.status, flag.resolved_at = "resolved", now
        return flag
```

In `FirestoreRepo.export_all`, add `"flags": dump(FLAGS)` to the returned dict.
In `import_all`, before the claims comment, add:

```python
        for d in data.get("flags", []):
            self.put_flag(Flag.from_dict(d))
```

`restore.py`: add `"resolved_at"` to `_TIME_FIELDS`.

- [ ] **Step 4: Run the tests and watch them pass**

Run: `/home/tcuser/src/curling_score/.venv/bin/pytest tests/test_repo_contract.py tests/test_service_core.py -q -p no:cacheprovider`
Expected: PASS. The Firestore half skips without `FIRESTORE_EMULATOR_HOST`.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/service/records.py src/curling_score/service/slug.py \
  src/curling_score/service/repo.py src/curling_score/service/firestore_repo.py \
  src/curling_score/service/restore.py tests/test_repo_contract.py tests/test_service_core.py
git commit -m "service: a Flag record in both repositories, backed up and restored"
```

---

### Task 2: The API: create, list, resolve

**Files:**
- Modify: `src/curling_score/service/api.py`:
  - imports: add `re` and add `Flag` to the records import;
  - constants beside `MAX_MERGE_BYTES` (~line 91);
  - `clean_place` and `_flag_json` at module level, next to `_iso`;
  - the create route after the `/g/` routes (after `review_asset`, ~line 1260);
  - the admin routes after `admin_export` (~line 1516).
- Test: `tests/test_service_api.py`, `tests/test_service_auth.py`, `tests/test_review_mode.py`

**Interfaces:**
- Consumes:
  - Task 1's `Flag`, `slug.new_flag_id`, `repo.put_flag`, `list_flags` and `resolve_flag`;
  - the existing `lookup(kind, key) -> (chart, run, read_only)`, `lookup_source(sid) -> (src, run)`, `ip_hash(request)`, `current_user_or_none(authorization)`, `require_admin(authorization)` and `repo.bump_rate_limit`.
- Produces:
  - `POST /api/flags` with body `{path, place, note}` → 201 `{"id"}`;
  - `GET /api/admin/flags?status=open|resolved|all&limit=` → `{"flags": [flag json]}`;
  - `POST /api/admin/flags/{id}/resolve` → flag json;
  - flag json is `Flag.to_dict()` with `created_at`/`resolved_at` as ISO strings.

- [ ] **Step 1: Write the failing API tests**

Append to `tests/test_service_api.py` (it already has `submit`, `work_through`,
`ADMIN`, `VID` and `world`):

```python
class TestFlags:
    """Anyone looking at a game can say what is wrong with a rock."""

    PLACE = {"game_index": 0, "end": 1, "rock": 2, "end_id": "1", "rock_id": "2",
             "key": "0.1.2", "t_video_s": 812.5, "label": "yellow, lead"}

    def flag(self, w, path, note="the house is wrong", place=None, ip="9.9.9.9", headers=None):
        return w["client"].post(
            "/api/flags", json={"path": path, "note": note,
                                "place": self.PLACE if place is None else place},
            headers={"X-Forwarded-For": ip, **(headers or {})})

    def chart(self, w):
        slug = submit(w).json()["slug"]
        work_through(w)
        return w["repo"].get_chart(slug)

    def source(self, w):
        submit(w)
        work_through(w)
        games = w["client"].get("/api/games").json()["games"]
        return w["repo"].get_source(next(g["source_id"] for g in games if g["source_id"]))

    def test_a_flag_from_an_edit_link(self, world):
        ch = self.chart(world)
        r = self.flag(world, f"/c/{ch.id}/")
        assert r.status_code == 201 and r.json()["id"].startswith("f_")
        f = world["repo"].list_flags()[0]
        assert f.note == "the house is wrong" and f.status == "open"
        assert f.where == {"link": "c", "chart_id": ch.id, "share_slug": ch.share_slug,
                           "source_id": ch.source_id, "run_id": ch.run_id, "video_id": VID,
                           "processing_version": world["repo"].get_run(ch.run_id).processing_version,
                           "title": world["repo"].get_run(ch.run_id).title}
        assert f.place == self.PLACE and f.overrides_version == 0 and f.user is None
        assert f.ip_hash

    def test_a_flag_from_a_view_only_link_names_the_same_chart(self, world):
        ch = self.chart(world)
        assert self.flag(world, f"/s/{ch.share_slug}/").status_code == 201
        f = world["repo"].list_flags()[0]
        assert f.where["link"] == "s" and f.where["chart_id"] == ch.id

    def test_a_flag_from_a_review_link(self, world):
        src = self.source(world)
        assert self.flag(world, f"/g/{src.id}").status_code == 201
        f = world["repo"].list_flags()[0]
        assert f.where["link"] == "g" and f.where["chart_id"] is None
        assert f.where["source_id"] == src.id and f.where["run_id"] == src.current_run_id
        assert f.overrides_version is None

    def test_a_superseded_chart_is_followed(self, world):
        import dataclasses
        old = self.chart(world)
        world["repo"].put_chart(dataclasses.replace(old, id="c_newer", share_slug="s_newer"))
        world["repo"].update_chart(old.id, superseded_by="c_newer")
        self.flag(world, f"/c/{old.id}/")
        assert world["repo"].list_flags()[0].where["chart_id"] == "c_newer"

    def test_a_flag_never_touches_the_charts_overrides(self, world):
        ch = self.chart(world)
        self.flag(world, f"/c/{ch.id}/")
        assert world["client"].get(f"/c/{ch.id}/overrides.json").json() == {}

    @pytest.mark.parametrize("path", ["/c/nosuchchartatall/", "/s/nosuchshare/", "/g/s_nosuchgame/"])
    def test_an_unknown_link_is_404(self, world, path):
        assert self.flag(world, path).status_code == 404
        assert world["repo"].list_flags() == []

    @pytest.mark.parametrize("body,code", [
        (b"{not json", 400), (b"[1, 2]", 400),
        (b'{"path": "/x/abc/", "note": "n", "place": {}}', 400),
        (b'{"note": "n", "place": {}}', 400),
        (b'{"path": "/c/../../etc/", "note": "n", "place": {}}', 400),
    ])
    def test_a_bad_body_is_400(self, world, body, code):
        r = world["client"].post("/api/flags", content=body,
                                 headers={"Content-Type": "application/json"})
        assert r.status_code == code

    @pytest.mark.parametrize("note,place", [
        ("   \n ", None), ("x" * 2001, None), (None, None),
        ("n", "not an object"), ("n", {"rock": "16"}), ("n", {"rock": True}),
        ("n", {"label": "x" * 81}), ("n", {"key": "k" * 41}), ("n", {"t_video_s": "12"}),
    ])
    def test_a_bad_note_or_place_is_422(self, world, note, place):
        ch = self.chart(world)
        r = world["client"].post("/api/flags", json={"path": f"/c/{ch.id}/", "note": note,
                                                     "place": self.PLACE if place is None else place})
        assert r.status_code == 422
        assert world["repo"].list_flags() == []

    def test_a_note_of_exactly_the_limit_is_accepted_trimmed(self, world):
        ch = self.chart(world)
        assert self.flag(world, f"/c/{ch.id}/", note="  " + "x" * 2000 + " \n").status_code == 201
        assert world["repo"].list_flags()[0].note == "x" * 2000

    def test_a_place_with_nothing_known_is_accepted(self, world):
        """A blank rock has no video time; an end with no rocks has no rock."""
        ch = self.chart(world)
        place = {"game_index": 0, "end": 3, "end_id": "3", "rock": None, "rock_id": None,
                 "key": None, "t_video_s": None, "label": None}
        assert self.flag(world, f"/c/{ch.id}/", place=place).status_code == 201
        assert world["repo"].list_flags()[0].place == place

    def test_numbers_in_identities_are_kept_as_text(self, world):
        ch = self.chart(world)
        self.flag(world, f"/c/{ch.id}/", place={**self.PLACE, "end_id": 7, "rock_id": 12})
        f = world["repo"].list_flags()[0]
        assert f.place["end_id"] == "7" and f.place["rock_id"] == "12"

    def test_an_oversized_body_is_413(self, world):
        ch = self.chart(world)
        assert self.flag(world, f"/c/{ch.id}/", note="x" * 9000).status_code == 413

    def test_twenty_an_hour_per_address(self, world):
        ch = self.chart(world)
        codes = [self.flag(world, f"/c/{ch.id}/").status_code for _ in range(21)]
        assert codes[:20] == [201] * 20 and codes[20] == 429
        assert self.flag(world, f"/c/{ch.id}/", ip="8.8.8.8").status_code == 201
        world["clock"].advance(3600)
        assert self.flag(world, f"/c/{ch.id}/").status_code == 201

    def test_the_admin_lists_newest_first_and_resolves(self, world):
        ch = self.chart(world)
        c = world["client"]
        first = self.flag(world, f"/c/{ch.id}/", note="one").json()["id"]
        world["clock"].advance(60)
        second = self.flag(world, f"/c/{ch.id}/", note="two").json()["id"]
        assert c.get("/api/admin/flags").status_code == 401
        listed = c.get("/api/admin/flags", headers=ADMIN).json()["flags"]
        assert [f["id"] for f in listed] == [second, first]
        assert listed[0]["created_at"].startswith("2026-") and listed[0]["resolved_at"] is None
        r = c.post(f"/api/admin/flags/{first}/resolve", headers=ADMIN)
        assert r.status_code == 200 and r.json()["status"] == "resolved"
        assert [f["id"] for f in c.get("/api/admin/flags", headers=ADMIN).json()["flags"]] == [second]
        assert [f["id"] for f in c.get("/api/admin/flags?status=resolved",
                                       headers=ADMIN).json()["flags"]] == [first]
        assert len(c.get("/api/admin/flags?status=all", headers=ADMIN).json()["flags"]) == 2
        assert c.get("/api/admin/flags?status=bogus", headers=ADMIN).status_code == 422
        assert c.post("/api/admin/flags/f_nope/resolve", headers=ADMIN).status_code == 404
        assert c.post(f"/api/admin/flags/{first}/resolve").status_code == 401

    def test_flags_are_in_the_backup(self, world):
        ch = self.chart(world)
        self.flag(world, f"/c/{ch.id}/")
        data = world["client"].get("/api/admin/export", headers=ADMIN).json()
        assert [f["note"] for f in data["flags"]] == ["the house is wrong"]
```

Append to `tests/test_service_auth.py` (it has `w`, `no_accounts`, `post`,
`a_ready_game` and `SARAH`):

```python
class TestAFlagCarriesTheAccount:
    def body(self, sid):
        return {"path": f"/g/{sid}/", "note": "wrong thrower", "place": {}}

    def test_a_signed_in_flag_names_its_author(self, w):
        sid = a_ready_game(w)
        assert post(w, "/api/flags", self.body(sid), SARAH).status_code == 201
        assert w["repo"].list_flags()[0].user == {"uid": "uid-sarah",
                                                  "email": "sarah@example.org"}

    def test_a_bad_token_is_anonymous_not_an_error(self, w):
        sid = a_ready_game(w)
        r = post(w, "/api/flags", self.body(sid), {"Authorization": "Bearer nonsense"})
        assert r.status_code == 201 and w["repo"].list_flags()[0].user is None

    def test_with_accounts_off_a_token_is_ignored(self, no_accounts):
        sid = a_ready_game(no_accounts)
        assert post(no_accounts, "/api/flags", self.body(sid), SARAH).status_code == 201
        assert no_accounts["repo"].list_flags()[0].user is None
```

Append to `tests/test_review_mode.py`, at module level (it already imports the
`world` fixture and defines `a_source`):

```python
def test_a_flag_is_not_a_route_under_the_review_link(world):
    """Flags go to /api/flags; the review link keeps carrying no POST route."""
    sid = a_source(world)["source_id"]
    r = world["client"].post(f"/g/{sid}/flag", json={"note": "n"})
    assert r.status_code == 405
```

- [ ] **Step 2: Run them and watch them fail**

Run: `/home/tcuser/src/curling_score/.venv/bin/pytest tests/test_service_api.py::TestFlags tests/test_service_auth.py::TestAFlagCarriesTheAccount tests/test_review_mode.py -q -p no:cacheprovider`
Expected: the `TestFlags` and `TestAFlagCarriesTheAccount` tests FAIL with 404
or 405 on `/api/flags`. The review-mode test passes already, as it pins an
existing property.

- [ ] **Step 3: Implement**

`api.py` imports: add `import re` with the other stdlib imports, and add `Flag`
to `from curling_score.service.records import (...)`.

Constants, after `MAX_MERGE_BYTES = 100_000`:

```python
# A flag is a sentence or three about one rock. These bound what a stranger
# can make us store, and the budget is a flag's own, like the chart's, so
# flagging a game never costs anyone a submission.
MAX_FLAG_BYTES = 8_000
MAX_FLAG_NOTE = 2_000
FLAG_HOUR_LIMIT = 20
FLAG_DAY_LIMIT = 100
# The page a flag was sent from: an edit, view-only or review link.
FLAG_PATH = re.compile(r"^/(c|s|g)/([A-Za-z0-9_-]+)/?$")
_PLACE_INTS = ("game_index", "end", "rock")
_PLACE_TEXT = {"end_id": 40, "rock_id": 40, "key": 40, "label": 80}
```

Module level, next to `_iso`:

```python
def clean_place(place) -> dict:
    """The browser's account of which rock, checked field by field.

    Every field may be null: a blank rock has no video time and an end with
    no rocks has no rock. Raises ValueError naming the first wrong field.
    """
    if not isinstance(place, dict):
        raise ValueError("place must be an object")
    out = {}
    for k in _PLACE_INTS:
        v = place.get(k)
        if v is not None and (isinstance(v, bool) or not isinstance(v, int)):
            raise ValueError(f"place.{k} must be a whole number")
        out[k] = v
    for k, most in _PLACE_TEXT.items():
        v = place.get(k)
        if isinstance(v, int) and not isinstance(v, bool):
            v = str(v)   # an identity is the detector's number unless renumbered
        if v is not None and (not isinstance(v, str) or len(v) > most):
            raise ValueError(f"place.{k} must be text of at most {most} characters")
        out[k] = v
    t = place.get("t_video_s")
    if t is not None and (isinstance(t, bool) or not isinstance(t, (int, float))):
        raise ValueError("place.t_video_s must be a number")
    out["t_video_s"] = None if t is None else float(t)
    return out


def _flag_json(flag) -> dict:
    return {**flag.to_dict(), "created_at": _iso(flag.created_at),
            "resolved_at": _iso(flag.resolved_at)}
```

The create route, right after `review_asset`:

```python
    # ------------------------------------------------------------- flags
    # Anyone looking at a chart may say what is wrong with it. One route for
    # all three kinds of link rather than one under each: /g/ carries no POST
    # route at all (see lookup_source), and one route is one budget to count.
    # The link arrives as the page's own path and is resolved here, so where a
    # flag says it was is the server's word, never the browser's.
    @app.post("/api/flags", status_code=201)
    async def create_flag(request: Request, authorization: str | None = Header(default=None)):
        raw = await request.body()
        if len(raw) > MAX_FLAG_BYTES:
            raise HTTPException(413, "that flag is too long")
        try:
            body = json.loads(raw)
        except ValueError:
            raise HTTPException(400, "send JSON") from None
        if not isinstance(body, dict):
            raise HTTPException(400, "send a JSON object")
        m = FLAG_PATH.match(str(body.get("path") or ""))
        if not m:
            raise HTTPException(400, "path must be a chart or game link")
        kind, key = m.groups()
        note = body.get("note")
        note = note.strip() if isinstance(note, str) else ""
        if not note:
            raise HTTPException(422, "say what is wrong")
        if len(note) > MAX_FLAG_NOTE:
            raise HTTPException(422, f"keep it under {MAX_FLAG_NOTE} characters")
        try:
            place = clean_place(body.get("place"))
        except ValueError as err:
            raise HTTPException(422, str(err)) from None
        if kind == "g":
            src, run = lookup_source(key)
            chart = None
        else:
            chart, run, _ = lookup(kind, key)
            src = repo.get_source(chart.source_id) if chart.source_id else None
        iph = ip_hash(request)
        if not repo.bump_rate_limit("flag:" + iph, now(), FLAG_HOUR_LIMIT, FLAG_DAY_LIMIT):
            raise HTTPException(429, "that is a lot of flags; try again later")
        user = current_user_or_none(authorization)
        flag = Flag(
            id=slug.new_flag_id(), created_at=now(), note=note, place=place,
            where={"link": kind,
                   "chart_id": chart.id if chart else None,
                   "share_slug": chart.share_slug if chart else None,
                   "source_id": src.id if src else None,
                   "run_id": run.id, "video_id": run.video_id,
                   "processing_version": run.processing_version,
                   "title": run.title},
            overrides_version=chart.overrides_version if chart else None,
            user={"uid": user.id, "email": user.email} if user else None,
            ip_hash=iph)
        repo.put_flag(flag)
        return {"id": flag.id}
```

The admin routes, right after `admin_export`:

```python
    @app.get("/api/admin/flags")
    def admin_flags(status: str = "open", limit: int = Query(200, ge=1, le=1000),
                    authorization: str | None = Header(default=None)):
        require_admin(authorization)
        if status not in ("open", "resolved", "all"):
            raise HTTPException(422, "status is open, resolved or all")
        flags = repo.list_flags(None if status == "all" else status, limit)
        return {"flags": [_flag_json(f) for f in flags]}

    @app.post("/api/admin/flags/{flag_id}/resolve")
    def admin_resolve_flag(flag_id: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        got = repo.resolve_flag(flag_id, now())
        if got is None:
            raise HTTPException(404, "no such flag")
        return _flag_json(got)
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `/home/tcuser/src/curling_score/.venv/bin/pytest tests/test_service_api.py tests/test_service_auth.py tests/test_review_mode.py tests/test_repo_contract.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/service/api.py tests/test_service_api.py \
  tests/test_service_auth.py tests/test_review_mode.py
git commit -m "api: POST /api/flags resolves the page's link; admin lists and resolves flags"
```

---

### Task 3: Core helpers: the place, the note, the sign-in timeout

**Files:**
- Create: `frontend/core/flag.mjs`
- Modify: `frontend/core/index.mjs` (add `export * from "./flag.mjs";`)
- Modify: `tests/js/singleton.mjs` (re-export the new names)
- Test: `tests/test_viewer_js.py`

**Interfaces:**
- Consumes: `endIdentity`, `identity` and `keyFor` from `frontend/core/timeline.mjs`; the view shape from `buildGameView(doc, gi, overrides)`, which is `{doc, gi, game, ends: [{end, shots, raws}]}`.
- Produces:
  - `NOTE_MAX = 2000`;
  - `flagPlace(view, ei, si) -> {place, text} | null`, where `place` is `{game_index, end, end_id, rock, rock_id, key, t_video_s, label}` and `text` is `"Game N · End E · Rock R (label)"`, or `"Game N · End E"` for an end with no rocks;
  - `noteProblem(note) -> string | null`;
  - `settleWithin(promise, ms, fallback = null) -> Promise`, which resolves to the promise's value, or to `fallback` on rejection or after `ms`.

- [ ] **Step 1: Write the failing node tests**

In `tests/js/singleton.mjs`, beside the other one-line re-exports:

```js
export const flagPlace = core.flagPlace;
export const noteProblem = core.noteProblem;
export const settleWithin = core.settleWithin;
export const NOTE_MAX = core.NOTE_MAX;
export const buildGameView = core.buildGameView;
```

Append to `tests/test_viewer_js.py` (it has `run_js`, `doc` and `shot`):

```python
class TestFlagPlace:
    """What a flag records about the rock it was sent from."""

    def place(self, d, ei=0, si=0):
        return run_js(f"out(flagPlace(buildGameView({json.dumps(d)}, 0, {{}}), {ei}, {si}));")

    def test_a_rock(self):
        d = doc([shot(1, "red", "lead", t_video_s=812.5), shot(2, "yellow", "lead")],
                end_number=4)
        got = self.place(d)
        assert got["place"] == {"game_index": 0, "end": 4, "end_id": "4", "rock": 1,
                                "rock_id": "1", "key": "0.4.1", "t_video_s": 812.5,
                                "label": "red, lead"}
        assert got["text"] == "Game 1 · End 4 · Rock 1 (red, lead)"

    def test_an_end_with_no_rocks(self):
        got = self.place(doc([], end_number=3))
        assert got["place"]["rock"] is None and got["place"]["key"] is None
        assert got["place"]["t_video_s"] is None and got["place"]["label"] is None
        assert got["text"] == "Game 1 · End 3"

    def test_a_rock_without_a_video_time(self):
        got = self.place(doc([shot(1, "red", "lead")]))
        assert got["place"]["t_video_s"] is None

    def test_a_trimmed_end_keeps_its_identity(self):
        got = self.place(doc([shot(1, "red", "lead")], end_number=2, end_id=5))
        assert (got["place"]["end"], got["place"]["end_id"], got["place"]["key"]) == (2, "5", "0.5.1")

    def test_a_renumbered_rock_keeps_its_identity(self):
        got = self.place(doc([shot(3, "red", "lead", id=7)]))
        assert (got["place"]["rock"], got["place"]["rock_id"], got["place"]["key"]) == (3, "7", "0.1.7")

    def test_the_second_game_of_a_video_keeps_its_index(self):
        got = self.place(doc([shot(1, "red", "lead")], game_index=1))
        assert got["place"]["game_index"] == 1 and got["place"]["key"] == "1.1.1"

    def test_past_the_last_end_there_is_nothing_to_flag(self):
        assert self.place(doc([shot(1, "red", "lead")]), ei=5) is None


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


class TestSettleWithin:
    """The sign-in check must never hold a flag hostage."""

    def test_a_check_that_never_settles_falls_back(self):
        assert run_js("settleWithin(new Promise(() => {}), 30, 'anon').then(out);") == "anon"

    def test_a_check_that_fails_falls_back(self):
        assert run_js("settleWithin(Promise.reject(new Error('x')), 500, 'anon').then(out);") == "anon"

    def test_a_check_that_answers_in_time_wins(self):
        assert run_js("settleWithin(Promise.resolve('me'), 50, 'anon').then(out);") == "me"
```

- [ ] **Step 2: Run them and watch them fail**

Run: `/home/tcuser/src/curling_score/.venv/bin/pytest tests/test_viewer_js.py -q -p no:cacheprovider -k "FlagPlace or NoteProblem or SettleWithin"`
Expected: FAIL. The node process errors because `core.flagPlace` and the rest
are undefined, so `flagPlace is not a function`.

- [ ] **Step 3: Implement**

Create `frontend/core/flag.mjs`:

```js
/* What a flag says about where it was, and whether its note can be sent.
 *
 * Plain ESM with no React and no DOM, so the Python suite can run it under
 * bare node -- see tests/test_viewer_js.py. */
import { endIdentity, identity, keyFor } from "./timeline.mjs";

export const NOTE_MAX = 2000;   // api.MAX_FLAG_NOTE

/* The rock the cursor is on, as a flag records it, and the line the dialog
 * shows. An end with no rocks is still somewhere to flag: its rock is null.
 * Identities are sent as text -- the detector's number unless a charter
 * renumbered -- so a flag still finds its rock after the display moves. */
export function flagPlace(view, ei, si) {
  const at = view.ends[ei];
  if (!at) return null;
  const e = at.end;
  const raw = at.raws[si] ?? null;
  const shot = at.shots[si] ?? null;
  const label = shot ? [shot.color, shot.position].filter(Boolean).join(", ") || null : null;
  const place = {
    game_index: view.game.index ?? null,
    end: e.number ?? null,
    end_id: String(endIdentity(e)),
    rock: shot ? shot.number : null,
    rock_id: raw ? String(identity(raw)) : null,
    key: raw ? keyFor(view.game, e, raw) : null,
    t_video_s: typeof shot?.t_video_s === "number" ? shot.t_video_s : null,
    label,
  };
  const text = `Game ${(view.gi ?? 0) + 1} · End ${place.end}`
    + (shot ? ` · Rock ${place.rock}${label ? ` (${label})` : ""}` : "");
  return { place, text };
}

/* Why this note cannot be sent, or null when it can. */
export function noteProblem(note) {
  const t = (note ?? "").trim();
  if (!t) return "Say what is wrong.";
  if (t.length > NOTE_MAX) return `Keep it under ${NOTE_MAX} characters.`;
  return null;
}

/* `p`'s value, or `fallback` if it rejects or has not settled within `ms`.
 * An unreachable sign-in is just another way to be anonymous. */
export function settleWithin(p, ms, fallback = null) {
  let timer;
  return Promise.race([
    Promise.resolve(p).catch(() => fallback),
    new Promise(res => { timer = setTimeout(() => res(fallback), ms); }),
  ]).finally(() => clearTimeout(timer));
}
```

In `frontend/core/index.mjs`, add `export * from "./flag.mjs";` after the
`line.mjs` export.

- [ ] **Step 4: Run the tests and watch them pass**

Run: `/home/tcuser/src/curling_score/.venv/bin/pytest tests/test_viewer_js.py -q -p no:cacheprovider -k "FlagPlace or NoteProblem or SettleWithin"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/core/flag.mjs frontend/core/index.mjs tests/js/singleton.mjs tests/test_viewer_js.py
git commit -m "viewer core: the place a flag records, its note check, and a sign-in timeout"
```

(The bundle is rebuilt in Task 4. `test_frontend_build.py` will be stale until
then, so do not run it in this task.)

---

### Task 4: The button, the dialog, lazy sign-in

**Files:**
- Create: `frontend/runtime/flag.mjs`
- Create: `frontend/viewer/Flag.jsx`
- Modify: `frontend/viewer/App.jsx`:
  - imports;
  - `flagging: null` in the initial `ui` (~line 68);
  - the keyboard guard at the top of `onKey` (~line 318);
  - `closeFlag`;
  - `<FlagDialog>` as a direct child of the root fragment, after `<section id="report">`;
  - `#flagBtn` in `Header`'s `#menu`, after `#reportBtn` (~line 561).
- Modify: `src/curling_score/viewer/style.css` (desktop header order; dialog styles; phone rules inside the existing `@media (max-width: 640px) and (min-height: 521px)` block)
- Build: `frontend/` → `npm run build`, which regenerates `src/curling_score/viewer/app.js` and `frontend/.buildstamp.json`
- Test: `tests/test_viewer_js.py`, `tests/test_review_mode.py`

**Interfaces:**
- Consumes:
  - Task 3's `flagPlace`, `noteProblem`, `NOTE_MAX` and `settleWithin` (via `../core/index.mjs`);
  - Task 2's `POST /api/flags`;
  - `frontend/site/auth.js`'s `whenReady()` and `currentUser()`. The latter returns a Firebase `User` with `.email` and `.getIdToken()`.
- Produces:
  - `whoIsFlagging() -> Promise<{email, token} | null>`, which always settles within 3 s;
  - `sendFlag(body, token) -> Promise<{ok: true, id} | {ok: false, error}>`;
  - `<FlagDialog flagging={ {place, text} | null } onClose={fn} />`;
  - `#flagBtn`, `#flagDialog` and `#flagNote` in the DOM.

- [ ] **Step 1: Write the failing source and bundle tests**

Append to `tests/test_viewer_js.py`:

```python
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
        assert "flagging: flagPlace(view, ui.ei, ui.si)" in app
        flag = self.src("frontend/viewer/Flag.jsx")
        assert "flagging.place" in flag and "ui." not in flag

    def test_keys_do_nothing_underneath_the_open_dialog(self):
        app = self.src("frontend/viewer/App.jsx")
        on_key = app[app.index("const onKey = ev =>"):]
        assert on_key.index('getElementById("flagDialog")?.open') < on_key.index("switch (ev.key)")

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
```

Append to the `TestReviewPage` class in `tests/test_review_mode.py`:

```python
    def test_the_flag_button_is_in_the_bundle(self, world):
        sid = a_source(world)["source_id"]
        app = world["client"].get(f"/g/{sid}/app.js").text
        assert "flagBtn" in app and "flagDialog" in app and "/api/flags" in app
```

- [ ] **Step 2: Run them and watch them fail**

Run: `/home/tcuser/src/curling_score/.venv/bin/pytest tests/test_viewer_js.py::TestTheFlagButton tests/test_review_mode.py -q -p no:cacheprovider`
Expected: FAIL. `ValueError: substring not found`, or the assertions fail on
missing `flagBtn`.

- [ ] **Step 3: Write the runtime module**

Create `frontend/runtime/flag.mjs`:

```js
/* Sending a flag, and finding out who is sending it.
 *
 * The viewer does not load Firebase (see site/auth.js, lines 1-12). Asking
 * who is signed in imports that module on demand -- esbuild inlines it but
 * does not evaluate it until this runs -- so a charting session that never
 * opens the dialog never fetches the SDK. The session is per origin, so
 * someone signed in on the site is signed in here. All of it fails soft:
 * no answer within SIGN_IN_WAIT_MS is an anonymous flag, not a stuck one. */
import { settleWithin } from "../core/index.mjs";

export const SIGN_IN_WAIT_MS = 3000;

export function whoIsFlagging() {
  return settleWithin((async () => {
    const auth = await import("../site/auth.js");
    await auth.whenReady();
    const u = auth.currentUser();
    if (!u) return null;
    return { email: u.email ?? null, token: await u.getIdToken() };
  })(), SIGN_IN_WAIT_MS, null);
}

const FAILED = "Could not send. Try again.";

export async function sendFlag(body, token) {
  try {
    const r = await fetch("/api/flags", {
      method: "POST",
      headers: { "Content-Type": "application/json",
                 ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: JSON.stringify(body),
    });
    const data = await r.json().catch(() => ({}));
    if (r.ok) return { ok: true, id: data.id };
    return { ok: false, error: typeof data.detail === "string" ? data.detail : FAILED };
  } catch {
    return { ok: false, error: FAILED };
  }
}
```

- [ ] **Step 4: Write the dialog**

Create `frontend/viewer/Flag.jsx`:

```jsx
/* The flag dialog: which rock, what is wrong, and who is saying so.
 *
 * A native <dialog> opened with showModal(), which brings focus handling,
 * Escape and a backdrop, and puts it in the top layer above the phone shell.
 * `flagging` is the snapshot App took when the button was pressed. The
 * cursor may move underneath while this is open -- a review link follows the
 * video -- and the flag must not move with it, so nothing here reads the
 * cursor. */
import { useEffect, useRef, useState } from "react";
import { NOTE_MAX, noteProblem } from "../core/index.mjs";
import { sendFlag, whoIsFlagging } from "../runtime/flag.mjs";

export function FlagDialog({ flagging, onClose }) {
  const ref = useRef(null);
  const [note, setNote] = useState("");
  const [who, setWho] = useState(undefined);      // undefined: still asking
  const [sending, setSending] = useState(false);
  const [error, setError] = useState(null);
  const [sent, setSent] = useState(false);

  useEffect(() => {
    const d = ref.current;
    if (!d) return undefined;
    if (!flagging) { if (d.open) d.close(); return undefined; }
    setNote(""); setError(null); setSent(false); setSending(false); setWho(undefined);
    if (!d.open) d.showModal();
    let live = true;
    whoIsFlagging().then(w => { if (live) setWho(w); });
    return () => { live = false; };
  }, [flagging]);

  useEffect(() => {
    if (!sent) return undefined;
    const t = setTimeout(onClose, 1500);
    return () => clearTimeout(t);
  }, [sent, onClose]);

  const send = async ev => {
    ev.preventDefault();
    if (sending || noteProblem(note)) return;
    setSending(true);
    setError(null);
    const w = who === undefined ? await whoIsFlagging() : who;
    const res = await sendFlag(
      { path: location.pathname, place: flagging.place, note: note.trim() }, w?.token);
    setSending(false);
    if (res.ok) setSent(true);
    else setError(res.error);
  };

  const from = who === undefined ? "Checking sign-in…"
    : who ? `From ${who.email ?? "your account"}`
    : "Sent without your name. Sign in on the site to attach your account.";

  return (
    <dialog id="flagDialog" ref={ref} onClose={onClose} aria-labelledby="flagTitle">
      {flagging && (sent ? <p className="flagThanks">Thanks, flagged.</p> : (
        <form onSubmit={send}>
          <h2 id="flagTitle">Flag an issue</h2>
          <p className="flagPlace">{flagging.text}</p>
          <textarea id="flagNote" rows={5} maxLength={NOTE_MAX} value={note}
                    onChange={e => setNote(e.target.value)}
                    placeholder="What is wrong here?" aria-label="What is wrong" />
          <p className="flagWho">{from}</p>
          {error && <p className="flagError" role="alert">{error}</p>}
          <div className="flagButtons">
            <button type="button" onClick={onClose}>Cancel</button>
            <button type="submit" className="on" disabled={sending || !!noteProblem(note)}>
              {sending ? "Sending…" : "Send"}
            </button>
          </div>
        </form>
      ))}
    </dialog>
  );
}
```

- [ ] **Step 5: Wire it into App**

In `frontend/viewer/App.jsx`:
- Add `flagPlace,` to the `../core/index.mjs` import list.
- Add `import { FlagDialog } from "./Flag.jsx";` after the `Report` import.
- In the reducer's initial state object, add `flagging: null,` after `reporting: false,`.
- After the `notify` callback, add:

```jsx
  const closeFlag = useCallback(() => dispatch({ type: "set", patch: { flagging: null } }), []);
```

At the top of `onKey`, before the modifier-key check:

```jsx
      // Behind the open flag dialog nothing steps, grades or seeks: the keys
      // belong to its buttons, and its textarea is covered by typing() anyway.
      if (document.getElementById("flagDialog")?.open) return;
```

As the last child of the root fragment, directly after
`</section>` of `#report`:

```jsx
      {/* Outside <main>: the phone's watch layout hides everything in it
          but #playCard, and a dialog inside a hidden parent never shows. */}
      <FlagDialog flagging={ui.flagging} onClose={closeFlag} />
```

In `Header`, inside `#menu`, directly after the `#reportBtn` button:

```jsx
        <button id="flagBtn" title="Flag an issue with this rock" hidden={!config.hosted}
                onClick={() => dispatch({ type: "set",
                  patch: { flagging: flagPlace(view, ui.ei, ui.si) } })}>
          ⚑ Flag
        </button>
```

- [ ] **Step 6: Style it**

In `src/curling_score/viewer/style.css`, add inside the existing
`@media (min-width: 641px)` desktop-order block, after
`header #reportBtn  { order: 11; }`:

```css
  header #flagBtn    { order: 12; }
```

After that block, add:

```css
/* --- flag dialog ---------------------------------------------------------- */
/* A modal <dialog> is in the top layer, above the phone shell and #menu, so
   it needs no z-index -- only never to be inside a hidden parent (App.jsx). */
#flagDialog { width: min(440px, calc(100vw - 32px)); padding: 16px;
  border: 1px solid var(--line); border-radius: 10px;
  background: var(--panel); color: var(--ink); }
#flagDialog::backdrop { background: rgb(0 0 0 / .35); }
#flagDialog h2 { margin: 0 0 4px; font: 600 18px var(--display); }
#flagDialog .flagPlace { margin: 0 0 10px; color: var(--muted); }
#flagDialog textarea { display: block; width: 100%; box-sizing: border-box;
  min-height: 120px; padding: 8px; font: inherit; color: var(--ink);
  background: var(--bg); border: 1px solid var(--line); border-radius: 6px; }
#flagDialog .flagWho { margin: 8px 0 0; font-size: 13px; color: var(--muted); }
#flagDialog .flagError { margin: 8px 0 0; color: var(--warn); }
#flagDialog .flagButtons { display: flex; justify-content: flex-end; gap: 8px;
  margin-top: 12px; }
#flagDialog .flagThanks { margin: 8px 0; font-weight: 600; }
```

Inside the existing `@media (max-width: 640px) and (min-height: 521px)` block,
at its end, add:

```css
  /* A bottom sheet on the phone, with thumb-sized buttons. */
  #flagDialog { width: 100vw; max-width: 100vw; margin: auto 0 0;
    border-radius: 16px 16px 0 0; }
  #flagDialog .flagButtons button { flex: 1; min-height: 44px; }
```

- [ ] **Step 7: Build, check the lazy import, and run the tests**

Run: `cd frontend && npm run build && cd ..`
Expected: eslint passes and `built: src/curling_score/viewer/app.js ...`.

Check that the auth module is lazily evaluated in the bundle:
`grep -n "api/auth/config" src/curling_score/viewer/app.js`
Expected: one hit, inside an `__esm({ ... })` wrapper (esbuild's
lazy-evaluation shape), not at the bundle's top level. Confirm by reading about
15 lines above the hit for `__esm(`.

If it is at top level, stop. Replace the `import("../site/auth.js")` in
`runtime/flag.mjs` with a local copy of `auth.js`'s `started` block (config
fetch, then the two `import(`${SDK}...`)` calls, then `onAuthStateChanged`
resolving once), wrapped in a function called only from `whoIsFlagging`. Then
rebuild.

Run: `/home/tcuser/src/curling_score/.venv/bin/pytest tests/test_viewer_js.py tests/test_review_mode.py tests/test_frontend_build.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add frontend/runtime/flag.mjs frontend/viewer/Flag.jsx frontend/viewer/App.jsx \
  src/curling_score/viewer/style.css src/curling_score/viewer/app.js \
  src/curling_score/service/static/site.js frontend/.buildstamp.json \
  tests/test_viewer_js.py tests/test_review_mode.py
git commit -m "viewer: a Flag button and dialog, snapshotting the rock, signing in only when opened"
```

(`site.js` is listed because `npm run build` rewrites both bundles. `git add`
of an unchanged file is a no-op.)

---

### Task 5: `scripts/flags.py`

**Files:**
- Create: `scripts/flags.py`
- Test: `tests/test_flags_script.py`

**Interfaces:**
- Consumes: Task 2's `GET /api/admin/flags` and `POST /api/admin/flags/{id}/resolve`, and its flag JSON.
- Produces:
  - `rock_link(flag, base) -> str`;
  - `youtube_link(flag) -> str | None`;
  - `describe(flag, base) -> str`;
  - a CLI: `list [--status] [--json]` and `resolve ID...`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_flags_script.py`:

```python
"""The terminal side of flags: what the owner reads back."""

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "flags_script", Path(__file__).resolve().parents[1] / "scripts" / "flags.py")
flags = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(flags)

BASE = "https://curling.dimmit.net"


def flag(**kw):
    base = {"id": "f_abc", "status": "open", "created_at": "2026-09-25T21:03:00+00:00",
            "note": "the ghosts are on the wrong stones",
            "user": {"uid": "u1", "email": "sarah@example.org"},
            "where": {"link": "g", "chart_id": None, "share_slug": None, "source_id": "s_x",
                      "run_id": "r_1", "video_id": "VID", "processing_version": "2026.09.25+m",
                      "title": "4/30 - Sheet 2"},
            "place": {"game_index": 0, "end": 4, "end_id": "4", "rock": 16, "rock_id": "16",
                      "key": "0.4.16", "t_video_s": 3354.6, "label": "red, skip"},
            "overrides_version": None}
    base.update(kw)
    return base


def test_a_review_flag_links_to_its_rock():
    assert flags.rock_link(flag(), BASE) == f"{BASE}/g/s_x/#e=4&s=16"


def test_chart_flags_link_to_the_page_they_came_from():
    edit = flag(where={**flag()["where"], "link": "c", "chart_id": "C1", "share_slug": "S1"})
    view = flag(where={**flag()["where"], "link": "s", "chart_id": "C1", "share_slug": "S1"})
    assert flags.rock_link(edit, BASE) == f"{BASE}/c/C1/#e=4&s=16"
    assert flags.rock_link(view, BASE) == f"{BASE}/s/S1/#e=4&s=16"


def test_an_end_with_no_rocks_links_to_the_page():
    f = flag(place={**flag()["place"], "rock": None, "key": None})
    assert flags.rock_link(f, BASE) == f"{BASE}/g/s_x/"


def test_youtube_at_the_rock():
    assert flags.youtube_link(flag()) == "https://youtu.be/VID?t=3354"
    assert flags.youtube_link(flag(place={**flag()["place"], "t_video_s": None})) is None


def test_describe_says_who_where_and_what():
    text = flags.describe(flag(), BASE)
    for part in ("f_abc", "open", "sarah@example.org", "4/30 - Sheet 2",
                 "Game 1 · End 4 · Rock 16 (red, skip)", "the ghosts are on the wrong stones",
                 f"{BASE}/g/s_x/#e=4&s=16", "https://youtu.be/VID?t=3354",
                 "r_1", "0.4.16", "2026.09.25+m"):
        assert part in text, part


def test_describe_says_anonymous():
    assert "anonymous" in flags.describe(flag(user=None), BASE)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `/home/tcuser/src/curling_score/.venv/bin/pytest tests/test_flags_script.py -q -p no:cacheprovider`
Expected: FAIL with `FileNotFoundError` for `scripts/flags.py`.

- [ ] **Step 3: Implement**

Create `scripts/flags.py`:

```python
#!/usr/bin/env python
"""List and resolve the flags people have sent from the viewer.

    ADMIN_TOKEN=... python scripts/flags.py list [--status open|resolved|all] [--json]
    ADMIN_TOKEN=... python scripts/flags.py resolve f_abc f_def

BASE_URL defaults to https://curling.dimmit.net. The admin token is the
`curling-admin-token` secret:
    ADMIN_TOKEN=$(~/google-cloud-sdk/bin/gcloud secrets versions access latest \\
        --secret=curling-admin-token --project=curling-stats-508323)

Each flag prints with a link to the page it was sent from, opened at its
rock, and YouTube at that moment. The run id and override key say exactly
what was on screen: a chart stays on its run, but after a reprocess a /g/
link shows the new run, where end and rock numbers may differ.
"""

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime

PAGES = {"c": ("/c/", "chart_id"), "s": ("/s/", "share_slug"), "g": ("/g/", "source_id")}


def rock_link(flag: dict, base: str) -> str:
    where, place = flag["where"], flag["place"]
    prefix, field = PAGES[where["link"]]
    page = f"{base}{prefix}{where[field]}/"
    if place.get("rock") is None or place.get("end") is None:
        return page
    return f"{page}#e={place['end']}&s={place['rock']}"


def youtube_link(flag: dict) -> str | None:
    t = flag["place"].get("t_video_s")
    vid = flag["where"].get("video_id")
    return f"https://youtu.be/{vid}?t={int(t)}" if t is not None and vid else None


def describe(flag: dict, base: str) -> str:
    where, place = flag["where"], flag["place"]
    when = datetime.fromisoformat(flag["created_at"]).astimezone().strftime("%Y-%m-%d %H:%M")
    who = (flag.get("user") or {}).get("email") or "anonymous"
    game = place.get("game_index")
    at = f"Game {game + 1 if game is not None else '?'} · End {place.get('end')}"
    if place.get("rock") is not None:
        at += f" · Rock {place['rock']}" + (f" ({place['label']})" if place.get("label") else "")
    lines = [f"{flag['id']}  {flag['status']}  {when}  {who}",
             f"  {where.get('title') or where.get('video_id')}",
             f"  {at}"]
    lines += [f"  > {line}" for line in flag["note"].splitlines() or [""]]
    lines.append(f"  {rock_link(flag, base)}")
    yt = youtube_link(flag)
    if yt:
        lines.append(f"  {yt}")
    lines.append(f"  run {where.get('run_id')} · key {place.get('key')}"
                 f" · pipeline {where.get('processing_version')}")
    return "\n".join(lines)


def _call(method: str, url: str, token: str):
    req = urllib.request.Request(url, method=method,
                                 headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list")
    ls.add_argument("--status", default="open", choices=("open", "resolved", "all"))
    ls.add_argument("--json", action="store_true")
    rs = sub.add_parser("resolve")
    rs.add_argument("ids", nargs="+")
    args = ap.parse_args(argv)
    token = os.environ.get("ADMIN_TOKEN", "").strip()
    if not token:
        print("set ADMIN_TOKEN (see this script's docstring)", file=sys.stderr)
        return 2
    base = os.environ.get("BASE_URL", "https://curling.dimmit.net").rstrip("/")
    if args.cmd == "list":
        got = _call("GET", f"{base}/api/admin/flags?status={args.status}", token)["flags"]
        if args.json:
            print(json.dumps(got, indent=1))
        else:
            print("\n\n".join(describe(f, base) for f in got) or "no flags")
        return 0
    for fid in args.ids:
        f = _call("POST", f"{base}/api/admin/flags/{fid}/resolve", token)
        print(f"{f['id']} resolved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `/home/tcuser/src/curling_score/.venv/bin/pytest tests/test_flags_script.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/flags.py tests/test_flags_script.py
git commit -m "scripts: flags.py lists and resolves flags, linking each to its rock"
```

---

### Task 6: End-to-end check in a browser, and the spec's status

**Files:**
- Modify: `docs/superpowers/specs/2026-09-25-flag-an-issue-design.md` (Status line)
- Throwaway, not committed: a CDP driver script in the session scratchpad

- [ ] **Step 1: Serve a real timeline and start headless Chrome**

```bash
SP=/tmp/claude-1000/-home-tcuser-src-curling-score/737151dd-7574-4bd4-9ffe-a88e854f91df/scratchpad
PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python scripts/devserve.py \
  /home/tcuser/curling-work/broom/wave3-src/s_0NOnuMHZoSp23r6n4.json > $SP/devserve.log 2>&1 &
google-chrome --headless=new --remote-debugging-port=9222 --user-data-dir=$SP/chrome about:blank &
```

Read the `edit`, `view-only` and `review` URLs from `$SP/devserve.log`.
devserve's admin token is `admin-secret`.

- [ ] **Step 2: Drive each surface**

Write `$SP/flagcheck.mjs`, using `scripts/cdp.mjs`'s `attach()`, `send()` and
`eval()`. For each of `{edit, view, review}` × `{1440×900 desktop, 390×844
phone with mobile: true and touch emulation}`:
1. Navigate to `<url>#e=1&s=3` and reload.
2. On phone, click `#menuBtn` first. Then click `#flagBtn`.
3. Assert `document.getElementById("flagDialog").open === true`.
4. Assert `.flagPlace` reads `Game 1 · End 1 · Rock 3 (...)`.
5. On review, wait 3 s with the video playing and assert `.flagPlace` has not
   changed.
6. Set the textarea's value through the React-friendly setter
   (`Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,"value").set.call(el, "e2e <surface> <layout>")`
   and dispatch an `input` event), then click the submit button.
7. Wait for `.flagThanks`, then for the dialog to close (about 1.5 s).
8. Assert that
   `performance.getEntriesByType("resource").some(e => e.name.includes("/api/auth/config"))`
   is false **before** the first click, which shows sign-in loads lazily.

Take one screenshot of the open dialog per layout into `$SP/`, and look at
them.

Finally,
`curl -s -H "Authorization: Bearer admin-secret" "http://127.0.0.1:<port>/api/admin/flags?status=all"`
should list six flags, with notes `e2e <surface> <layout>`, `where.link` of
`c`, `s` and `g`, and `place.rock == 3`.

- [ ] **Step 3: Stop the server and Chrome by PID**

Take the PIDs from `$!` or `pgrep -f` on the executable path. A pattern that
also appears in your own shell's command line kills your shell.

- [ ] **Step 4: Run every touched test file once more**

Run: `/home/tcuser/src/curling_score/.venv/bin/pytest tests/test_service_api.py tests/test_service_auth.py tests/test_review_mode.py tests/test_repo_contract.py tests/test_service_core.py tests/test_viewer_js.py tests/test_frontend_build.py tests/test_flags_script.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Mark the spec built and commit**

Change the spec's **Status** to: `built on branch flag-an-issue (2026-09-25);
checked in headless Chrome on /c/, /s/ and /g/ at desktop and phone widths.`

```bash
git add docs/superpowers/specs/2026-09-25-flag-an-issue-design.md
git commit -m "spec: flag an issue is built"
```

Deploying the API is a separate, explicit step for the owner. See the
deploying-curling-chart notes: `deploy-api.sh` from a clean worktree. No worker
change and no new Firestore index are needed.
