# Member Labelling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `/label` page on the hosted site where signed-in members fix rock boxes on harvested FP frames and mark release frames. Every item gets two independent labels, and an export settles agreement on the laptop.

**Architecture:**
- **Items and answers** live in Firestore behind the existing `Repo` protocol (`LabelItem`, `LabelAnswer`). Holds and submits are transactional.
- **Logic and routes.** Pure logic is in `service/labelling.py`; the routes are added inside `create_app` in `service/api.py`, as every other feature does.
- **Images** go into the bucket from the worker box by signed PUT, and are served to members by signed GET. A release rock is one sprite image, because every signed URL is an IAM `signBlob` call on Cloud Run.
- **Agreement** is computed only at export, in `train/labelagree.py`.
- **Scripts.** Two publish scripts run on the worker box: FP frames → box items, and a one-off release set. One export script runs on the laptop.

**Tech Stack:** FastAPI, google-cloud-firestore, GCS signed URLs, React (esbuild, `frontend/site`), plain ES modules tested in node (`frontend/core`), ultralytics YOLO (CPU) for pre-boxing, OpenCV.

**Spec:** `docs/superpowers/specs/2026-10-22-member-labelling-design.md`

## Global Constraints

- Every `/api/label/...` route calls `require_user`. Every `/api/admin/label-items...` route calls `require_admin`, which takes the ADMIN_TOKEN bearer.
- Two labels per item from two different uids (`MAX_LABELS = 2`). The user's own site labels count as one of the two.
- A member is never sent another member's answer, `labelled_by`, `skipped_by` or `reference`.
- Hold: `HOLD_S = 600`. Retire after `RETIRE_AFTER_SKIPS = 5` skips by different members.
- Keys in both flows: `S` = can't tell (a label), `N` = skip (no label).
  - Boxes: `R` / `Y` set the colour, `Delete` removes, `K` keeps the flagged box, `Enter` = done. Enter is refused until the flagged box was kept, changed or deleted.
  - Release: arrows step a frame (shift = 5), `Enter` = hand off here, `H` = still holding at the end.
- Agreement:
  - Release: both `off` within 3 frames, keeping the later frame; or both `held`.
  - Boxes: one-to-one match at IoU ≥ 0.7 with the same colour and nothing left over. The agreed box is the mean of each matched pair.
  - Disputed instead of agreed: any can't-tell, a mismatch, or both answers leaving every starting box untouched or kept with nothing added (rubber stamp).
- Desktop only: below 1000 px of window width the page shows a note instead of the tools.
- No SAM on the site. A new box is drawn by dragging on empty ice.
- Nothing here changes the worker image or `PIPELINE_VERSION`. Pre-boxing runs on the CPU. The API deploy is the user's call.
- Firestore stores no array directly inside an array. Every box is a dict `{"x0","y0","x1","y1",...}`, never a list.
- Run only the affected tests (the full suite OOMs on this box). The Firestore half of `tests/test_repo_contract.py` needs `FIRESTORE_EMULATOR_HOST`.
- `privacy.html` changes in the same commit as the routes.

## Review Focus

1. **The same member in two tabs** submits the same item twice. The second submit replaces the first answer, and `n_labels` stays at 1 (Task 1 test `test_a_second_submit_by_the_same_member_replaces_it`).
2. **A hold expires mid-item** and two others finish it. The late submit gets 409 `full`, and the page says someone else finished it and loads the next item (Task 3 test `test_a_late_submit_on_a_finished_item_is_409`, Task 5 handles 409).
3. **A half-published item:** created, but its upload failed. It stays `pending`, is never handed out, and a publish rerun re-uploads it and opens it (Task 1 `test_pending_items_are_never_handed_out`, Task 3 `test_ready_opens_only_items_whose_image_exists`, Task 7 `test_a_rerun_reuploads_pending_and_skips_open`).
4. **A signed-out or expired token** gets 401 on every member route, and the page shows the sign-in prompt rather than a blank editor (Task 3 `test_member_routes_need_sign_in`).
5. **An item nobody can label** is skipped 5 times. It leaves the queue and is exported as disputed with `why: "skipped"` (Task 1 `test_five_skips_retire_an_item`, Task 9 `test_retired_items_export_as_disputed`).

---

### Task 1: Records and repo for label items and answers

**Files:**
- Modify: `src/curling_score/service/records.py` (append after `PracticeSession`)
- Modify: `src/curling_score/service/repo.py` (Protocol + `MemoryRepo`, `export_all`/`import_all`)
- Modify: `src/curling_score/service/firestore_repo.py` (collections `label_items`, `label_answers`; docstring index list; export/import)
- Modify: `deploy/firestore.indexes.json`
- Modify: `tests/test_repo_contract.py` (add both collections to `_firestore_repo`'s wipe list; new `TestLabelling`)

**Interfaces:**
- Produces, in `records.py`:
```python
LABEL_KINDS = ("box", "release")
LABEL_STATES = ("pending", "open", "done", "retired")

def label_answer_id(item_id: str, uid: str) -> str:   # f"{item_id}__{uid}"

@dataclass
class LabelItem:
    id: str
    kind: str                 # "box" | "release"
    created_at: datetime
    batch: str                # e.g. "fp-2026-10-22", "releases-1"
    payload: dict             # what the page shows (Task 2 says its shape)
    state: str = "pending"
    n_labels: int = 0
    labelled_by: list = field(default_factory=list)   # uids
    skipped_by: list = field(default_factory=list)    # uids
    skips: int = 0
    hold_uid: str | None = None
    hold_until: datetime | None = None
    reference: dict | None = None   # check items only; never sent to a member
    done_at: datetime | None = None
    to_dict = asdict
    from_dict = classmethod(_from_dict)

@dataclass
class LabelAnswer:
    id: str                   # label_answer_id(item_id, uid)
    item_id: str
    kind: str
    uid: str
    at: datetime
    seconds: float
    answer: dict
    to_dict = asdict
    from_dict = classmethod(_from_dict)
```
- Produces, on `Repo` / `MemoryRepo` / `FirestoreRepo`:
```python
def put_label_item(self, item: LabelItem) -> None
def get_label_item(self, item_id: str) -> LabelItem | None
def update_label_item(self, item_id: str, **fields) -> LabelItem | None
def open_label_items(self, kind: str, n_labels: int, limit: int,
                     after: datetime | None = None) -> list[LabelItem]
    # state == "open" and n_labels == n_labels, created_at ascending, strictly after `after`
def list_label_items(self, kind: str, state: str, limit: int,
                     after: datetime | None = None) -> list[LabelItem]
    # state == state, created_at ascending, strictly after `after`
def hold_label_item(self, item_id: str, uid: str, now: datetime, hold_s: float) -> LabelItem | None
def submit_label(self, answer: LabelAnswer, now: datetime, max_labels: int) -> tuple[str, LabelItem | None]
def skip_label_item(self, item_id: str, uid: str, now: datetime, retire_after: int) -> LabelItem | None
def label_answers(self, item_id: str) -> list[LabelAnswer]   # oldest first
def label_count(self, uid: str, kind: str) -> int
```

Semantics (both repos; Firestore inside one transaction for hold/submit/skip):
- **`hold_label_item`** returns the item with `hold_uid=uid` and `hold_until=now+hold_s` only when all of these hold:
  - state is `open`;
  - uid is not in `labelled_by` or `skipped_by`;
  - the hold is free, expired (`hold_until <= now`) or already uid's.

  Otherwise it returns None.
- **`submit_label`** returns `(result, item)`, checking in this order:
  - missing → `("missing", None)`;
  - state `pending` or `retired` → `("closed", item)`;
  - uid already in `labelled_by` → overwrite that answer → `("replaced", item)`. This works on `done` items too.
  - `n_labels >= max_labels` → `("full", item)`;
  - another uid's live hold → `("held", item)`;
  - otherwise write the answer, increment `n_labels`, append the uid, and clear the hold if it was uid's. When it reaches `max_labels`, set `state="done"` and `done_at=now`. Return `("added", item)`.
- **`skip_label_item`**:
  - Missing → None.
  - A uid already in `skipped_by`, or a state that isn't `open`, changes nothing and returns the item.
  - Otherwise append the uid, `skips += 1`, and clear the hold if it was uid's. When `skips >= retire_after`, set `state="retired"`.

- [ ] **Step 1: Write the failing contract tests** in `tests/test_repo_contract.py`, class `TestLabelling`, parametrised over both repos by the existing `repo` fixture:

```python
def item(i, **kw):
    return LabelItem(id=f"b_{i}", kind="box", created_at=at(i), batch="t",
                     payload={"w": 10}, state=kw.pop("state", "open"), **kw)

def answer(item_id, uid, a=None):
    return LabelAnswer(id=label_answer_id(item_id, uid), item_id=item_id, kind="box",
                       uid=uid, at=at(100), seconds=4.0, answer=a or {"cant_tell": True})

class TestLabelling:
    def test_open_items_by_label_count_oldest_first(self, repo):
        for i in (3, 1, 2): repo.put_label_item(item(i))
        repo.put_label_item(item(4, n_labels=1))
        repo.put_label_item(item(5, state="pending"))
        assert [x.id for x in repo.open_label_items("box", 0, 10)] == ["b_1", "b_2", "b_3"]
        assert [x.id for x in repo.open_label_items("box", 0, 10, after=at(1))] == ["b_2", "b_3"]
        assert [x.id for x in repo.open_label_items("box", 1, 10)] == ["b_4"]

    def test_pending_items_are_never_handed_out(self, repo):
        repo.put_label_item(item(1, state="pending"))
        assert repo.hold_label_item("b_1", "u1", at(10), 600) is None
        assert repo.submit_label(answer("b_1", "u1"), at(10), 2)[0] == "closed"

    def test_a_hold_keeps_others_out_until_it_expires(self, repo):
        repo.put_label_item(item(1))
        assert repo.hold_label_item("b_1", "u1", at(10), 600).hold_uid == "u1"
        assert repo.hold_label_item("b_1", "u2", at(20), 600) is None
        assert repo.submit_label(answer("b_1", "u2"), at(20), 2)[0] == "held"
        assert repo.hold_label_item("b_1", "u2", at(611), 600).hold_uid == "u2"

    def test_two_members_finish_an_item(self, repo):
        repo.put_label_item(item(1))
        assert repo.submit_label(answer("b_1", "u1"), at(10), 2)[0] == "added"
        res, got = repo.submit_label(answer("b_1", "u2"), at(11), 2)
        assert (res, got.state, got.n_labels, got.done_at) == ("added", "done", 2, at(11))
        assert repo.submit_label(answer("b_1", "u3"), at(12), 2)[0] == "full"
        assert [a.uid for a in repo.label_answers("b_1")] == ["u1", "u2"]
        assert repo.label_count("u1", "box") == 1 and repo.label_count("u3", "box") == 0

    def test_a_second_submit_by_the_same_member_replaces_it(self, repo):
        repo.put_label_item(item(1))
        repo.submit_label(answer("b_1", "u1"), at(10), 2)
        res, got = repo.submit_label(answer("b_1", "u1", {"cant_tell": False, "boxes": []}), at(11), 2)
        assert (res, got.n_labels, got.labelled_by) == ("replaced", 1, ["u1"])
        assert repo.label_answers("b_1")[0].answer["cant_tell"] is False
        assert repo.hold_label_item("b_1", "u1", at(12), 600) is None   # never their own again

    def test_five_skips_retire_an_item(self, repo):
        repo.put_label_item(item(1))
        for n in range(5):
            got = repo.skip_label_item("b_1", f"u{n}", at(10 + n), 5)
        repo.skip_label_item("b_1", "u0", at(20), 5)          # a repeat counts once
        assert (got.state, got.skips) == ("retired", 5)
        assert repo.hold_label_item("b_1", "u9", at(30), 600) is None
        assert repo.get_label_item("b_1").skips == 5

    def test_a_skipper_is_not_offered_it_again(self, repo):
        repo.put_label_item(item(1))
        repo.skip_label_item("b_1", "u1", at(10), 5)
        assert repo.hold_label_item("b_1", "u1", at(11), 600) is None
        assert repo.hold_label_item("b_1", "u2", at(11), 600) is not None

    def test_listing_by_state_and_round_trip(self, repo):
        repo.put_label_item(item(1, reference={"mark": "off", "frame": 3},
                                 payload={"starts": [{"x0": 1, "y0": 2, "x1": 3, "y1": 4,
                                                      "color": "red", "flagged": True}]}))
        repo.put_label_item(item(2, state="done"))
        assert [x.id for x in repo.list_label_items("box", "done", 10)] == ["b_2"]
        got = repo.get_label_item("b_1")
        assert got.reference == {"mark": "off", "frame": 3}
        assert got.payload["starts"][0]["flagged"] is True
```

- [ ] **Step 2: Run them to see them fail**

Run: `pytest tests/test_repo_contract.py -k Labelling -q`
Expected: FAIL, ImportError on `LabelItem`. The firestore params skip without the emulator.

- [ ] **Step 3: Add the records and the `MemoryRepo` methods.** Keep `MemoryRepo` items in a dict under the repo's existing lock.

- [ ] **Step 4: Run the memory half**

Run: `pytest tests/test_repo_contract.py -k "Labelling and memory" -q`
Expected: PASS

- [ ] **Step 5: Add the `FirestoreRepo` methods.** Collections are `LABEL_ITEMS, LABEL_ANSWERS = "label_items", "label_answers"`. The answer doc id is `label_answer_id`. `hold`, `submit` and `skip` each run as a `@self._fs.transactional` read-check-write, like `take_clip_job`. `label_count` uses a count aggregation on `uid ==` and `kind ==`. Add both to `export_all`/`import_all`. Add to `deploy/firestore.indexes.json` and the module docstring:
  - `label_items (kind ASC, state ASC, n_labels ASC, created_at ASC)`
  - `label_items (kind ASC, state ASC, created_at ASC)`

- [ ] **Step 6: Run both halves against the emulator**

Run: `FIRESTORE_EMULATOR_HOST=localhost:8080 pytest tests/test_repo_contract.py -k Labelling -q` (start the emulator as memory `firestore-emulator` says)
Expected: PASS for memory and firestore

- [ ] **Step 7: Commit**

```bash
git add src/curling_score/service/records.py src/curling_score/service/repo.py \
  src/curling_score/service/firestore_repo.py deploy/firestore.indexes.json tests/test_repo_contract.py
git commit -m "labelling: items and answers in the repo, two labels each, holds and skips"
```

---

### Task 2: Handing out items and checking answers (`service/labelling.py`)

**Files:**
- Create: `src/curling_score/service/labelling.py`
- Test: `tests/test_labelling.py`

**Interfaces:**
- Consumes: Task 1 repo methods and records.
- Produces:
```python
HOLD_S = 600
MAX_LABELS = 2
RETIRE_AFTER_SKIPS = 5
PAGE = 50
MAX_BOXES = 60

def image_key(item_id: str, kind: str) -> str
    # "labelling/box/<id>.jpg" | "labelling/release/<id>.jpg"
def next_items(repo, kind: str, uid: str, now: datetime, n: int = 1) -> list[LabelItem]
def check_answer(item: LabelItem, answer: dict) -> dict   # the normalised answer, or raises ValueError(reason)
def for_member(item: LabelItem, image_url: str) -> dict
```

Payload shapes (the publishers write these, and the page reads them):
- **box:**
```
{"image": key, "w": int, "h": int,
 "starts": [{"x0","y0","x1","y1": float, "color": "red"|"yellow", "conf": float|None, "flagged": bool}],
 "origin": {...}}
```
  Exactly one start has `flagged: true`.
- **release:**
```
{"image": key, "cols": int, "fw": int, "fh": int, "n": int, "fps": 30.0,
 "frames": [{"o": float, "yp": float}], "origin": {...}}
```

Answer shapes:
- **box:**
```
{"cant_tell": bool,
 "boxes": [{"x0","y0","x1","y1","color","start": int|None}],
 "starts": ["untouched"|"kept"|"changed"|"deleted", ...]}
```
- **release:** `{"mark": "off"|"held"|"cant_tell", "frame": int|None}`

`next_items` order:
1. The uid's own live hold first (reload resumes).
2. Then `open_label_items(kind, 1, PAGE)`, one page only.
3. Then `open_label_items(kind, 0, PAGE, after=...)`, paging until `n` are held.

It tries `repo.hold_label_item` on each candidate whose `labelled_by`/`skipped_by` lack the uid and whose hold is free. A None from the hold means the race was lost, so move on. Finding the uid's own hold needs no extra query: the n=1 and n=0 pages already include it, so put it first when it's found.

`check_answer` rules, each raising `ValueError` with a short reason:
- **Box, can't tell:** `cant_tell: true` returns `{"cant_tell": True, "boxes": [], "starts": []}`.
- **Box, otherwise:**
  - `len(starts) == len(payload.starts)`, and each status is one of the four.
  - The flagged start's status isn't `"untouched"`.
  - Each box has `0 <= x0 < x1 <= w` and `0 <= y0 < y1 <= h` (±1 px tolerance, then clamped), is at least 3 px each way, and has a colour of `red`/`yellow`.
  - `start` is a valid index or None. No start appears twice. Every start not `deleted` appears exactly once, and every `deleted` start is absent.
  - At most `MAX_BOXES`.
- **Release:** `mark` is one of the three. `frame` is an int in `[0, n)` when `off`, and None otherwise.

`for_member` returns `{"id", "kind", "image_url", ...payload without "image" and without "origin"}`. Nothing else: no reference, no uids, no counts.

- [ ] **Step 1: Write the failing tests**

```python
def box_item(**kw):  # 100x50 image, start 0 flagged, start 1 not
    payload = {"image": "k", "w": 100, "h": 50, "starts": [
        {"x0": 10, "y0": 10, "x1": 20, "y1": 18, "color": "red", "conf": 0.4, "flagged": True},
        {"x0": 50, "y0": 10, "x1": 60, "y1": 18, "color": "yellow", "conf": 0.9, "flagged": False}],
        "origin": {"video_id": "v"}}
    return LabelItem(id="b_1", kind="box", created_at=T0, batch="t", payload=payload,
                     state="open", reference={"boxes": []}, **kw)

def test_the_flagged_box_must_be_acted_on():
    with pytest.raises(ValueError, match="flagged"):
        check_answer(box_item(), {"cant_tell": False, "boxes": [
            {"x0": 10, "y0": 10, "x1": 20, "y1": 18, "color": "red", "start": 0},
            {"x0": 50, "y0": 10, "x1": 60, "y1": 18, "color": "yellow", "start": 1}],
            "starts": ["untouched", "untouched"]})

def test_a_deleted_start_must_be_gone_and_a_kept_one_present():
    ok = {"cant_tell": False, "starts": ["deleted", "untouched"], "boxes": [
        {"x0": 50, "y0": 10, "x1": 60, "y1": 18, "color": "yellow", "start": 1}]}
    assert check_answer(box_item(), ok)["starts"] == ["deleted", "untouched"]
    bad = {**ok, "boxes": ok["boxes"] + [{"x0": 10, "y0": 10, "x1": 20, "y1": 18,
                                          "color": "red", "start": 0}]}
    with pytest.raises(ValueError):
        check_answer(box_item(), bad)

def test_boxes_outside_the_image_or_tiny_are_refused(): ...   # x1=140 -> ValueError; 2px wide -> ValueError
def test_cant_tell_needs_nothing_else(): ...
def test_release_answers(): ...   # off+frame 3 ok; off+frame None, off+frame n, held+frame 2 -> ValueError

def test_for_member_hides_reference_origin_and_uids():
    got = for_member(box_item(labelled_by=["u1"]), "https://img")
    assert got["image_url"] == "https://img" and "reference" not in got
    assert "origin" not in got and "labelled_by" not in got and len(got["starts"]) == 2

def test_next_items_finishes_half_done_first_and_never_repeats(repo): ...
    # MemoryRepo. Items 1, 2 open at n=0; item 3 at n=1 by u1.
    # next for u2 -> [b_3]; next for u1 n=2 -> b_1, b_2;
    # u1 asking again within HOLD_S gets back the same held b_1 first.
```

- [ ] **Step 2: Run them to see them fail**

Run: `pytest tests/test_labelling.py -q`
Expected: FAIL, no module `curling_score.service.labelling`

- [ ] **Step 3: Implement the module** to the interface above.

- [ ] **Step 4: Run them to see them pass**

Run: `pytest tests/test_labelling.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/service/labelling.py tests/test_labelling.py
git commit -m "labelling: hand out items half-done first, check answers, hide what members must not see"
```

---

### Task 3: API routes, the `/label` shell, and the privacy page

**Files:**
- Modify: `src/curling_score/service/api.py`. Put the page route beside `/thinking`, member routes after `/api/flags`, and admin routes after `/api/admin/flags`.
- Create: `src/curling_score/service/static/label.html` (a copy of `thinking.html` with `data-page="label"` and the title "Label · curling.dimmit.net")
- Modify: `src/curling_score/service/static/privacy.html`
- Test: `tests/test_labelling_api.py` (fixtures `w`, `SARAH`, `ALEX` from `tests/test_service_auth.py`; `ADMIN` from where `give_role` gets it)

**Interfaces:**
- Consumes: Task 2 `next_items`, `check_answer`, `for_member`, `image_key` and the constants; Task 1 repo methods; `store.presign_get`, `presign_put`, `exists`.
- Produces, as HTTP. `{kind}` is `box` or `release`, anything else is 404. Image URLs are `store.presign_get(key, 3600)`.

| Route | Auth | Body / query | Response |
|---|---|---|---|
| `GET /label` | none | | `page("label.html")` |
| `GET /api/label/{kind}/next` | user | `n` = 1..2 | `{"items": [for_member...], "count": label_count(uid, kind)}` |
| `POST /api/label/{kind}/{item_id}` | user | `{"answer": {...}, "seconds": float}` | `{"result": "added"\|"replaced", "count": int}`; 404 missing or wrong kind; 409 `{"detail": "full"\|"held"\|"closed"}`; 422 the `check_answer` reason |
| `POST /api/label/{kind}/{item_id}/skip` | user | | `{"ok": true}`; 404 missing |
| `GET /api/label/me` | user | | `{"box": int, "release": int}` |
| `POST /api/admin/label-items` | admin | `{"items": [{"id","kind","batch","payload","reference"?}]}` | `{"items": [{"id","state","upload": presign_put(key,"image/jpeg") or null}]}` |
| `POST /api/admin/label-items/ready` | admin | `{"ids": [...]}` | `{"opened": [...], "missing_image": [...]}` |
| `GET /api/admin/label-items` | admin | `kind`, `state` (`done`\|`retired`\|`open`\|`pending`), `after` ISO, `limit` ≤ 500 | `{"items": [{...item.to_dict(), "image_url", "answers": [answer.to_dict()]}], "after": last created_at or null}` |

- **Create.** The server sets `payload["image"] = image_key(id, kind)`. A new id is stored `pending`. An existing `pending` id is left as it is and gets a fresh upload URL. An existing id in any other state gets `upload: null` and is left untouched. Ids must match `^[A-Za-z0-9_.-]{1,120}$`.
- **Ready.** Opens `pending` items only, and only when `store.exists(payload["image"])`.
- **Answer.** `seconds` is clamped to 0..3600. The answer id is `label_answer_id(item_id, me.id)`.

Privacy, add one paragraph after the "kept longer to test and train" one:
> Members who sign in and volunteer to help can be shown still frames and short frame sequences from games, and asked to mark where the stones are or when a thrower let go. What they mark is stored with their account and used to train the software.

- [ ] **Step 1: Write the failing tests**

```python
def publish(w, n=2, kind="box"):  # creates, uploads through the memory store, readies
    items = [{"id": f"b_{i}", "kind": kind, "batch": "t", "payload": BOX_PAYLOAD} for i in range(n)]
    r = w["client"].post("/api/admin/label-items", json={"items": items}, headers=ADMIN)
    for it in r.json()["items"]:
        w["store"].put_bytes(f"labelling/{kind}/{it['id']}.jpg", b"jpg", "image/jpeg")
    w["client"].post("/api/admin/label-items/ready", json={"ids": [i["id"] for i in items]},
                     headers=ADMIN)

def test_member_routes_need_sign_in(w):
    for m, p in [("get", "/api/label/box/next"), ("post", "/api/label/box/b_0"),
                 ("post", "/api/label/box/b_0/skip"), ("get", "/api/label/me")]:
        assert getattr(w["client"], m)(p).status_code == 401

def test_admin_routes_need_the_admin_token(w):
    assert w["client"].post("/api/admin/label-items", json={"items": []},
                            headers=SARAH).status_code == 401

def test_ready_opens_only_items_whose_image_exists(w): ...
    # create 2, upload 1 -> opened == ["b_0"], missing_image == ["b_1"]; next never returns b_1

def test_two_members_label_an_item_and_counts_go_up(w): ...
    # SARAH next -> b_0 (+ count 0); answer -> added, count 1; ALEX next -> b_0 first (half-done)

def test_a_late_submit_on_a_finished_item_is_409(w): ...
    # add a third account "tok-maker" (already in the fixture): after SARAH+ALEX, MAKER submit -> 409 "full"

def test_a_bad_answer_is_422_with_the_reason(w): ...   # flagged left untouched -> 422, detail mentions "flagged"
def test_a_member_never_sees_reference_or_others(w): ...  # publish with reference; next JSON has no "reference"/"labelled_by"
def test_create_is_idempotent(w): ...                  # create again after ready -> upload None, state "open"
def test_admin_listing_returns_answers_and_pages(w): ...
def test_the_label_page_is_served(w): assert 'data-page="label"' in w["client"].get("/label").text
```

- [ ] **Step 2: Run them to see them fail**

Run: `pytest tests/test_labelling_api.py -q`
Expected: FAIL (404s)

- [ ] **Step 3: Add the routes, `label.html` and the privacy paragraph**

- [ ] **Step 4: Run them, and the neighbours that share the fixtures**

Run: `pytest tests/test_labelling_api.py tests/test_service_auth.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/service/api.py src/curling_score/service/static/label.html \
  src/curling_score/service/static/privacy.html tests/test_labelling_api.py
git commit -m "labelling: member and admin routes, the /label shell, and say so on the privacy page"
```

---

### Task 4: Agreement (`train/labelagree.py`)

**Files:**
- Create: `src/curling_score/train/labelagree.py`
- Test: `tests/test_labelagree.py`

**Interfaces:**
- Consumes: the Task 2 answer shapes.
- Produces:
```python
MIN_IOU = 0.7
FRAME_TOL = 3
def iou(a: dict, b: dict) -> float
def match_boxes(a: list[dict], b: list[dict], min_iou: float = MIN_IOU) -> tuple[list[tuple[int, int]], list[int], list[int]]
    # greedy by IoU, highest first, one-to-one; (pairs, unmatched a idx, unmatched b idx)
def box_verdict(a: dict, b: dict) -> dict
    # {"agree": bool, "why": None|"cant_tell"|"unmatched"|"colour"|"rubber_stamp", "boxes": list[dict]|None}
def release_verdict(a: dict, b: dict, tol: int = FRAME_TOL) -> dict
    # {"agree": bool, "why": None|"cant_tell"|"apart"|"mixed", "mark": "off"|"held"|None, "frame": int|None}
def agrees_with_reference(kind: str, answer: dict, reference: dict) -> bool
    # same rules, with the reference as the other side; rubber stamp is not checked against a reference
```

Rules:
- **Rubber stamp:** both answers have every start status in (`untouched`, `kept`) and no box with `start is None`. That's checked before matching and wins over agreement.
- **Box colour:** a pair at IoU ≥ 0.7 with different colours gives `why = "colour"`.
- **Agreed boxes** are the coordinate means of each pair, with the shared colour, `start` dropped.
- **Release:** a can't-tell on either side → `cant_tell`. `off`/`held` mixed → `mixed`. Both off but more than `tol` frames apart → `apart`. Both off within tol → agree, keeping the frame `max(a, b)`. Both held → agree, `held`.

- [ ] **Step 1: Write the failing tests.** These cover:
  - `test_identical_boxes_agree_and_average`: two answers with one box each, offset by 1 px, which agree with the mean box.
  - `test_a_missing_box_is_unmatched`
  - `test_same_place_other_colour_is_colour`
  - `test_iou_just_under_the_bar_disagrees`: two 10×10 boxes sharing 8 columns (IoU 0.667) give `unmatched`.
  - `test_both_rubber_stamped_is_disputed_even_when_identical`
  - `test_one_member_changed_something_is_not_a_rubber_stamp`
  - `test_releases_within_three_frames_take_the_later`: 40 and 43 agree on 43; 40 and 44 give `apart`.
  - `test_held_and_off_is_mixed`, `test_both_held_agree`, `test_cant_tell_is_never_agreement`
  - `test_reference_check`: the user's off at 40 against a member's 42 is True, against 45 is False.

- [ ] **Step 2: Run them to see them fail**

Run: `pytest tests/test_labelagree.py -q`
Expected: FAIL, no module

- [ ] **Step 3: Implement**

- [ ] **Step 4: Run them to see them pass**

Run: `pytest tests/test_labelagree.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/train/labelagree.py tests/test_labelagree.py
git commit -m "labelagree: when two members' boxes or release marks agree"
```

---

### Task 5: The page, and the rock-box flow

**Files:**
- Create: `frontend/core/labelling.mjs` (pure, no DOM)
- Create: `frontend/site/Label.jsx`
- Modify: `frontend/site/main.jsx` (register `label: Label`)
- Modify: `frontend/site/ui.jsx` (add `"/label"` to `SIGNED_IN_ONLY`)
- Modify: `src/curling_score/service/static/site.css` (a `.label-*` section)
- Test: `tests/test_labelling_js.py` (node, in the pattern of `tests/test_practice_js.py`)
- Rebuild: `service/static/site.js` with `node frontend/build.mjs`, and check it with `tests/test_frontend_build.py`.

**Interfaces:**
- Consumes: the Task 3 routes; `useAuthUser`, `Header`, the sign-in dialog from `frontend/site`.
- Produces, in `frontend/core/labelling.mjs`:
```js
export const MIN_WIDTH_PX = 1000;
export function startBoxes(item)          // -> state {boxes:[{x0,y0,x1,y1,color,start}], starts:[status...], flagged:int}
export function boxAt(state, x, y, pad)   // -> index of the topmost box containing (x,y) within pad, or -1
export function moveCorner(box, corner, x, y, w, h)   // corner "nw"|"ne"|"sw"|"se"; clamped, min 3 px
export function setColour(state, i, color) // -> new state; its start becomes "changed" if the colour differs
export function removeBox(state, i)        // -> new state; its start becomes "deleted"
export function keepFlagged(state)         // -> new state; flagged start "kept" if it was "untouched"
export function addBox(state, box)         // -> new state; box.start = null
export function canFinish(state)           // -> flagged start status !== "untouched"
export function boxAnswer(state)           // -> the Task 2 box answer (cant_tell false)
```
  A move or resize of a starting box marks its start `"changed"`. Every function returns a new state; none mutates.

Page behaviour:
- **Signed out:** the sign-in prompt.
- **Window under `MIN_WIDTH_PX`:** the note "Labelling needs a computer: a mouse and a keyboard."
- **Top bar:** flow name, "You've labelled N", and a link to the other flow. The flow is chosen by `#boxes` or `#releases` in the URL, defaulting to boxes.
- **Image:** drawn at `min(2, fit)` scale with an SVG overlay of the boxes. The flagged box is dashed until acted on. Each box has a colour tag.
- **Mouse:** drag a corner to resize, drag inside a box to move it, drag on empty ice to draw a new box (colour = last used, default red). Click selects.
- **Keys:** `R`/`Y` set the selected box's colour, `Delete`/`Backspace` removes it, `K` keeps the flagged box, `Enter` finishes, `S` sends can't tell, `N` skips.
  - Enter while `!canFinish` shows "Keep (K), fix or delete the dashed box first".
- **Submitting:** it times from first paint to submit.
  - A 409 shows "Someone else finished this one" and loads the next item.
  - A 401 re-opens sign-in.
  - It always holds one prefetched item (`n=2` on the first ask, then `n=1`).
  - An `<img>` error shows "This image didn't load" with the skip button focused.
- **Back:** "Back" re-opens the previous item with this member's answer for one step, and a resubmit replaces it.
- **First visit to the flow** (localStorage, wrapped in try/catch): the how-to card. "Box every stone you can see, even part-hidden ones. People, brooms and shoes get no box. The dashed box is what the computer wasn't sure about."

- [ ] **Step 1: Write the failing node tests** in `tests/test_labelling_js.py`:
  - `test_a_fresh_item_cannot_finish_until_the_flagged_box_is_acted_on`
  - `test_moving_a_starting_box_marks_it_changed`
  - `test_deleting_marks_deleted_and_drops_it_from_the_answer`
  - `test_keep_only_touches_the_flagged_box`
  - `test_corners_clamp_to_the_image_and_keep_three_pixels`
  - `test_box_answer_matches_the_server_shape`: it carries `cant_tell` false, `boxes` with `start` indices, and `starts` the same length as the item's.

- [ ] **Step 2: Run them to see them fail**

Run: `pytest tests/test_labelling_js.py -q`
Expected: FAIL (module missing)

- [ ] **Step 3: Write `labelling.mjs`, then `Label.jsx`, register the page, and build**

Run: `node frontend/build.mjs`

- [ ] **Step 4: Run the JS and build tests**

Run: `pytest tests/test_labelling_js.py tests/test_frontend_build.py -q`
Expected: PASS

- [ ] **Step 5: Look at it in a browser.** Start `devserve` with a MemoryRepo seeded through the Task 3 admin routes with the two frames in `.superpowers/brainstorm/*/content/`. Check these, and screenshot the box flow at 1440×900:
  - each key;
  - the refused Enter;
  - the counter going up;
  - the narrow-window note at 900 px.

  Memory `signed-in-browser-check` covers the fake Firebase.

- [ ] **Step 6: Commit**

```bash
git add frontend/core/labelling.mjs frontend/site/Label.jsx frontend/site/main.jsx frontend/site/ui.jsx \
  src/curling_score/service/static/site.css src/curling_score/service/static/site.js tests/test_labelling_js.py
git commit -m "label page: the rock-box flow"
```

---

### Task 6: The release flow on the page

**Files:**
- Modify: `frontend/core/labelling.mjs`
- Modify: `frontend/site/Label.jsx`
- Modify: `src/curling_score/service/static/site.css`
- Test: `tests/test_labelling_js.py`
- Rebuild: `site.js`

**Interfaces:**
- Consumes: the release payload from Task 2; the page frame from Task 5.
- Produces:
```js
export function spriteOffset(item, i)       // -> {x, y} pixel offset of frame i in the sprite: col = i % cols, row = floor(i / cols)
export function step(i, delta, n)           // clamped to [0, n-1]
export function releaseAnswer(mark, frame)  // -> {mark, frame}; frame null unless mark === "off"
```

Page behaviour:
- **Frame:** one frame large (`fw`×`fh`, 1× or more to fit), drawn from the sprite with CSS `background-position`. Below it is a strip of 8 neighbours. The current frame has a blue border, and a frame this member marked earlier has orange.
- **Slider and keys:** a range slider; ←/→ step 1 frame, shift+←/→ step 5; `Enter` = hand off here, `H` = still holding, `S` = can't tell, `N` = skip.
- **Starting frame:** an item opens on frame 0. The pipeline's `o` is never shown, and nothing a model read is drawn.
- **How-to on first visit:** "Find the first frame where the hand is clearly off the handle. If the thrower lets go and grabs it again, mark the last let-go." The two example rocks are left for after launch (the user marks them; see Task 8).
- **Submitting:** the same 409/401, back, prefetch and counter behaviour as Task 5.

- [ ] **Step 1: Write the failing node tests:** `test_sprite_offsets_walk_rows`, `test_step_clamps`, `test_release_answer_drops_frame_unless_off`.

- [ ] **Step 2: Run them to see them fail**

Run: `pytest tests/test_labelling_js.py -q -k "sprite or step or release"`
Expected: FAIL

- [ ] **Step 3: Implement, then build**

Run: `node frontend/build.mjs`

- [ ] **Step 4: Run the JS and build tests**

Run: `pytest tests/test_labelling_js.py tests/test_frontend_build.py -q`
Expected: PASS

- [ ] **Step 5: Look at it in a browser.** Seed one release item whose sprite is built from any ten JPEGs (Task 8's `make_sprite` will exist later, so use PIL inline). Check the keys and the strip, and screenshot.

- [ ] **Step 6: Commit**

```bash
git add frontend/core/labelling.mjs frontend/site/Label.jsx src/curling_score/service/static/site.css \
  src/curling_score/service/static/site.js tests/test_labelling_js.py
git commit -m "label page: the release flow"
```

---

### Task 7: Publish harvested FP frames as box items (`scripts/label_fp.py`, worker box)

**Files:**
- Create: `scripts/label_fp.py`
- Create: `scripts/label_publish.py` (shared client for both publishers: create, upload, ready)
- Test: `tests/test_label_fp_script.py`

**Interfaces:**
- Consumes: `curling_score.fpframes.store.read_rows`, `video_dir`; `curling_score.weights.default_path()` (overhead) and `side_path()`; Task 3 admin routes.
- Produces:
```python
# scripts/label_publish.py
def publish(base: str, token: str, items: list[dict], images: dict[str, bytes], *,
            dry_run: bool = False, opener=urllib.request.urlopen) -> dict
    # POST create -> PUT each returned upload with images[id] -> POST ready; returns {"opened": [...], "skipped": [...], "missing_image": [...]}

# scripts/label_fp.py
CROP_W, CROP_H = 720, 360          # long camera, whole-frame px
PREBOX_CONF = 0.25
KEEP_INSIDE = 0.4                  # a detection is kept if >= 40% of its area is in the crop
def crop_for(row: dict) -> tuple[int, int, int, int]
def starts_for(dets: list[dict], flagged: dict, crop: tuple) -> list[dict]
def item_for(row: dict, crop: tuple, starts: list[dict], batch: str) -> dict
def main(argv=None) -> int          # publish --caches A B [--since DATE] [--batch NAME] [--checks DIR] [--dry-run --out DIR]
                                    # --dry-run sends nothing and writes each crop + item JSON to --out
```

Decisions:
- **Crop (my call, replacing the spec's "check the cropped50 tooling").** `cropped50` turned out to be "the first 50 hand labels", not a crop. ds13's own `band_crop` needs a calibrated `SideView`, which `frames.jsonl` doesn't carry. So:
  - **Side:** a `CROP_W`×`CROP_H` window centred on the flagged box's centre, clamped inside the row's `rect`, with each side shrunk to the rect if it's smaller.
  - **Overhead:** the row's `rect`.
- **Pre-boxing.** Run the current overhead and side weights with ultralytics on the CPU (`device="cpu"`). Use `imgsz=800` for the side, and the pipeline's overhead `imgsz` (see `YoloDetector` callers in `analyze.py`). Classes are 0 red and 1 yellow, at `conf >= PREBOX_CONF`.
  - Detections are clipped to the crop. Drop any with less than `KEEP_INSIDE` of their area inside.
  - Drop detections at IoU ≥ 0.5 with the flagged box, then add the flagged box itself with `flagged: True` and `conf` from the row.
  - Its colour comes from the row, else from the overlapping detection it replaced, else `"red"`.
  - Coordinates are crop pixels.
- **Item id:** `"b_" + row["stem"]`. Batch default `fp-<today>`. `origin` keeps `video_id, title, t, camera, where, check, models, pipeline, stem` and `crop: [x, y, w, h]`, so the export can return to whole-frame pixels.
- **Reading frames.** Images are read from `<cache>/harvest/fp/<video>/<stem>.jpg`, cropped, and JPEG-encoded at quality 90.
- **Check items.** `--checks DIR` takes a folder of `<stem>.jpg` with a `boxes.json` of `{stem: [{"x0","y0","x1","y1","color"}]}`, the user's own labelled frames. Each one is published with that as `reference: {"boxes": [...]}`, model pre-boxes as starts, and the highest-confidence pre-box flagged. With no pre-box, a centred 40×17 box is flagged.
- **Idempotent:** rerunning on the same piles changes nothing open, and re-uploads pending items (the server decides this, Task 3).
- **Running it:** the docstring says how, in the worker image, like `fp_harvest.py list`. That's `docker run --rm` with the caches mounted read-only, `ADMIN_TOKEN` and `BASE_URL` in the environment, and `--entrypoint python`.

- [ ] **Step 1: Write the failing tests** (no model; `starts_for` and `crop_for` are pure, and `publish` gets a fake opener):
  - `test_side_crop_centres_on_the_flag_and_stays_in_the_panel`: a flag near the panel's right edge gives a crop whose x1 is at the rect's right edge and width 720.
  - `test_overhead_crop_is_the_panel`
  - `test_a_detection_on_the_flag_is_replaced_by_the_flag`
  - `test_detections_mostly_outside_are_dropped_and_others_clipped`
  - `test_exactly_one_start_is_flagged`
  - `test_a_rerun_reuploads_pending_and_skips_open`: the fake server answers create with `upload` for one id and None for another, and `publish` PUTs only the first, then readies both.

- [ ] **Step 2: Run them to see them fail**

Run: `pytest tests/test_label_fp_script.py -q`
Expected: FAIL

- [ ] **Step 3: Implement both scripts**

- [ ] **Step 4: Run them to see them pass**

Run: `pytest tests/test_label_fp_script.py tests/test_fp_harvest_script.py -q`
Expected: PASS

- [ ] **Step 5: Dry run on the worker box** against the real piles (81+ frames):

Run: `python scripts/label_fp.py publish --caches /c1 /c2 --dry-run --out /tmp/label-fp-dry`

Expected:
- one crop JPEG and one item JSON per row in `/tmp/label-fp-dry`;
- no request sent.

Look at five crops with their starts drawn, and check that the flagged box sits on the flagged thing.

- [ ] **Step 6: Commit**

```bash
git add scripts/label_fp.py scripts/label_publish.py tests/test_label_fp_script.py
git commit -m "label_fp: publish harvested FP frames as box items, cropped and pre-boxed"
```

---

### Task 8: The one-off release set (`scripts/label_releases.py`, worker box)

**Files:**
- Create: `scripts/label_releases.py`
- Test: `tests/test_label_releases_script.py`

**Interfaces:**
- Consumes: `scripts/mark_release.py` `extract(video, tl, end_no, rock_no, dest, tag)` and `OUT_W`, loaded with `importlib` as `tests/test_mark_release.py` does; Task 7's `label_publish.publish`; the public `GET /api/games?video_id=` and `GET /g/{sid}/timeline.json`.
- Produces:
```python
SPRITE_COLS = 10
def candidates(timeline: dict, source_id: str, video_id: str) -> list[dict]
    # every shot with line.delivery and t_release_s: {"source_id","video_id","end","rock","color","position","label"}
def pick(cands: list[dict], n: int, seed: int, exclude: set[tuple[str, int, int]]) -> list[dict]
    # shuffle each source's rocks with random.Random(seed), then round-robin across sources (sorted by id) until n
def make_sprite(frame_paths: list[Path], cols: int = SPRITE_COLS) -> tuple[bytes, dict]
    # JPEG q85 bytes, {"cols","fw","fh","n"}
def item_for(pick: dict, extracted: dict, sprite_meta: dict, batch: str, reference=None) -> dict
def main(argv=None) -> int
    # pick --caches A B --n 1000 --seed 20261023 --out picks.json
    # publish --picks picks.json --caches A B [--batch releases-1] [--checks datasets/releasemarks] [--dry-run]
```

Decisions:
- **Inputs.** Videos are `<cache>/videos/<video_id>.mp4` in either cache. There are no downloads (memory `no-more-vod-downloads`).
- **Exclusions.** `exclude` is every `(video_id, end, rock)` already in `datasets/releasemarks/s_*.json`.
- **Payload.** `frames` are `[{"o","yp"}]` from `extract`. `origin` keeps `source_id, video_id, title, end, rock, color, position, label, t_release_s`. The item id is `f"r_{video_id}_e{end:02d}r{rock:02d}"`. Use `-g{source}` if a video has two sources that share end and rock numbers.
- **Check items.** `--checks` publishes up to 20 of the `chosen == "random"` rocks with `mark` off or held from `datasets/releasemarks/`, whose video is cached. The reference is `{"mark", "frame"}`, where `frame` is the index in the freshly extracted frames whose `o` is nearest the marked `o`, or `{"mark": "held", "frame": None}`.
- **Scratch and progress.** Extraction goes into a scratch dir that is deleted per rock after upload. It logs progress every 25 rocks.

- [ ] **Step 1: Write the failing tests** (pure functions; `make_sprite` on 12 tiny generated JPEGs):
  - `test_candidates_skip_shots_without_a_delivery_line`
  - `test_pick_spreads_across_sources_and_is_repeatable`: three sources of 10, 15 and 5 rocks with `n=12` give 4 from each, and the same seed gives the same list.
  - `test_pick_excludes_marked_rocks`
  - `test_sprite_layout`: 12 frames of 20×10 at `cols=10` give a 200×20 image with `n == 12`.
  - `test_check_reference_frame_is_the_nearest_o`

- [ ] **Step 2: Run them to see them fail**

Run: `pytest tests/test_label_releases_script.py -q`
Expected: FAIL

- [ ] **Step 3: Implement**

- [ ] **Step 4: Run them, and mark_release's own**

Run: `pytest tests/test_label_releases_script.py tests/test_mark_release.py -q`
Expected: PASS

- [ ] **Step 5: On the worker box**, run `pick --n 1000` and `publish --dry-run` for the first 5 picks. Check:
  - each sprite has about 100+ frames at 560 px wide;
  - the size is under 6 MB;
  - the rock is centred.

  Don't publish the full 1,000; that's the user's go-ahead after the deploy.

- [ ] **Step 6: Commit**

```bash
git add scripts/label_releases.py tests/test_label_releases_script.py
git commit -m "label_releases: pick 1,000 cached rocks and publish them as release items"
```

---

### Task 9: Export to the laptop (`scripts/labels_export.py`)

**Files:**
- Create: `scripts/labels_export.py`
- Modify: `src/curling_score/train/boxedit.py`. `render` gains optional per-item `ghosts` (the other member's boxes, drawn dashed and not editable). An item may have `geom: None`, in which case a click-to-add box falls back to a fixed 40 px wide box at `HEIGHT_RATIO`.
- Test: `tests/test_labels_export_script.py`, `tests/test_boxedit.py` (two new tests)

**Interfaces:**
- Consumes: Task 3 `GET /api/admin/label-items` and `GET /api/admin/users`; Task 4 `box_verdict`, `release_verdict`, `agrees_with_reference`; `boxedit.render`; `mark_release.py`'s prepared-session format (`index.json` + frames + `marks.json`).
- Produces: `main(argv=None) -> int`, run as `ADMIN_TOKEN=... python scripts/labels_export.py --out datasets/member-labels/<YYYY-MM-DD> [--kind box|release]`.

Writes under `--out`:
- **`boxes/agreed/`:**
  - `images/<id>.jpg`;
  - `labels/<id>.txt` in YOLO (`cls cx cy w h`, normalised to the crop, 0 = red, 1 = yellow), as `train.dataset` trees are;
  - `agreed.jsonl`, one row per item with `id, origin, boxes` (crop px) and `boxes_frame` (whole-frame px = crop px + `origin.crop[:2]`).
- **`boxes/disputed/`:** `index.html` from `boxedit.render(..., proposals=True)`. Member A's boxes are editable and member B's are `ghosts`. Also `disputed.jsonl` with `id, why, origin` and both answers. Retired items go here with `why: "skipped"` and no answers.
- **`releases/agreed.json`:** `{"batch", "fps", "definition", "rocks": [...]}`, where each rock is
  - `origin` fields
  - `mark`, `frame`, `o` = `frames[frame].o`, `yp`, `yp_prev`
  - `members: [{"uid","mark","frame"}]`

  This is the `datasets/releasemarks` row shape, with `chosen: "member"`.
- **`releases/disputed/`:** a `mark_release.py --prepared` session. The frames are cut back out of each sprite as `<id>_<i:03d>.jpg`. `index.json` uses `extract`'s entry shape, `marks.json` is `{}`, and `disputed.json` holds `why` and both members' marks.
- **`members.json`:** per uid
  - `email` (from `/api/admin/users`)
  - `box`, `release`: counts
  - `median_seconds`
  - `checks: {"n", "agree"}`, scored only on items with a `reference`, by `agrees_with_reference`

Settled disputes are recorded on the laptop under `datasets/` by the existing tools. Nothing is written back to the site.

- [ ] **Step 1: Write the failing tests** (a fake API: the opener returns canned listing pages, image bytes are tiny JPEGs, and sprites are built with Task 8's `make_sprite`):
  - `test_agreeing_box_items_become_a_yolo_tree`
  - `test_disputed_boxes_render_with_ghosts`
  - `test_retired_items_export_as_disputed`
  - `test_agreeing_releases_land_in_releasemarks_shape`
  - `test_disputed_releases_become_a_prepared_mark_session`: there's one JPEG per frame, and `index.json` frames list has `n` entries.
  - `test_member_check_scores`
  - `test_it_pages_through_the_listing`
  - in `tests/test_boxedit.py`: `test_ghosts_are_drawn_but_not_exported` and `test_an_item_without_geometry_still_renders`

- [ ] **Step 2: Run them to see them fail**

Run: `pytest tests/test_labels_export_script.py tests/test_boxedit.py -q`
Expected: FAIL on the new tests only

- [ ] **Step 3: Implement**

- [ ] **Step 4: Run them to see them pass**

Run: `pytest tests/test_labels_export_script.py tests/test_boxedit.py tests/test_labelagree.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/labels_export.py src/curling_score/train/boxedit.py \
  tests/test_labels_export_script.py tests/test_boxedit.py
git commit -m "labels_export: agreed labels to training formats, disputes to boxedit and mark_release"
```

---

## After the tasks (the user's calls, not part of this plan's execution)

1. Run the whole-branch review, then merge.
2. Deploy the API (`deploy-api.sh`, memory `deploying-curling-chart`) and the Firestore indexes (`gcloud firestore indexes composite create` from `deploy/firestore.indexes.json`, or the console). Wait for the indexes to finish building before anyone opens `/label`.
3. Publish on the worker box:
   - `label_fp.py publish` with the piles so far;
   - `label_releases.py pick` and `publish --checks`, once the user has seen five rocks on the live page.
4. The user marks the two how-to example rocks, a lift and a hover, and they get wired into the release how-to.
5. Send the link to the volunteers.
