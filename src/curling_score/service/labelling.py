"""Members' labelling: which item to put in front of whom, and whether an
answer is one we can keep.

An item is handed out under a hold (a lease on it for HOLD_S), so two members
don't spend their time on the same item while others wait. Items already
labelled once come first, because a second label finishes them; after those,
the oldest untouched ones. What a member is shown never includes the known
answer, the item's origin, or who else has seen it.

Nothing here knows about HTTP: the API turns a ValueError from check_answer
into a 422 carrying its message.
"""

from datetime import datetime

from curling_score.service.records import LABEL_KINDS, LabelItem

# How long an item is reserved for the member it was handed to.
HOLD_S = 600
# Labels an item gets before it is done.
MAX_LABELS = 2
# Skips by different members before an item is retired as not worth asking.
RETIRE_AFTER_SKIPS = 5
# Items read from the repo per query.
PAGE = 50
# More boxes than this on one image is a mistake, not a careful answer.
MAX_BOXES = 60

BOX_COLORS = ("red", "yellow")
START_STATUSES = ("untouched", "kept", "changed", "deleted")
RELEASE_MARKS = ("off", "held", "cant_tell")
# A box edge may overshoot the image by this much (a drag past the border).
EDGE_SLOP_PX = 1.0
MIN_BOX_PX = 3.0
# No image is anywhere near this wide; past it a coordinate is nonsense.
MAX_COORD = 1e9


def image_key(item_id: str, kind: str) -> str:
    """Where an item's image lives in the bucket."""
    if kind not in LABEL_KINDS:
        raise ValueError(f"unknown kind {kind!r}")
    return f"labelling/{kind}/{item_id}.jpg"


def _is_free(item: LabelItem, uid: str, now: datetime) -> bool:
    if uid in item.labelled_by or uid in item.skipped_by:
        return False
    return item.hold_uid in (None, uid) or item.hold_until is None or item.hold_until <= now


def next_items(repo, kind: str, uid: str, now: datetime, n: int = 1) -> list[LabelItem]:
    """Up to ``n`` items for this member, each now held for them.

    Order: the member's own live hold (a reload resumes), then items with one
    label already, then untouched ones, oldest first. The first page of each
    is read up front, which is where a member's own hold is found without a
    query of its own; untouched items are paged on only if that isn't enough.
    A hold that comes back None was lost to someone else, so we move on.
    """
    half = repo.open_label_items(kind, 1, PAGE)
    fresh = repo.open_label_items(kind, 0, PAGE)
    own = [i for i in half + fresh if i.hold_uid == uid and _is_free(i, uid, now)]
    own_ids = {i.id for i in own}
    got: list[LabelItem] = []

    def take(items):
        for item in items:
            if len(got) >= n:
                return
            if not _is_free(item, uid, now):
                continue
            held = repo.hold_label_item(item.id, uid, now, HOLD_S)
            if held is not None:
                got.append(held)

    take(own)
    take([i for i in half if i.id not in own_ids])
    take([i for i in fresh if i.id not in own_ids])
    page = fresh
    while len(got) < n and len(page) >= PAGE:
        page = repo.open_label_items(kind, 0, PAGE, after=page[-1].created_at)
        take([i for i in page if i.id not in own_ids])
    return got


def _num(v, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
        raise ValueError(f"{what} is not a number")
    # JSON allows integers too big for a float, and float() of one overflows.
    if abs(v) > MAX_COORD:
        raise ValueError(f"{what} is out of range")
    return float(v)


def _check_box_answer(item: LabelItem, answer: dict) -> dict:
    if answer.get("cant_tell") is True:
        return {"cant_tell": True, "boxes": [], "starts": []}
    payload = item.payload
    w, h = payload["w"], payload["h"]
    starts = answer.get("starts")
    boxes = answer.get("boxes")
    if not isinstance(starts, list) or not isinstance(boxes, list):
        raise ValueError("starts and boxes are needed")
    if len(starts) != len(payload["starts"]):
        raise ValueError("one status per detected start is needed")
    if any(s not in START_STATUSES for s in starts):
        raise ValueError("unknown start status")
    for s, given in zip(starts, payload["starts"]):
        if given.get("flagged") and s == "untouched":
            raise ValueError("the flagged start must be kept, changed or deleted")
    if len(boxes) > MAX_BOXES:
        raise ValueError(f"at most {MAX_BOXES} boxes")
    out, seen = [], set()
    for b in boxes:
        if not isinstance(b, dict):
            raise ValueError("a box is not an object")
        x0, y0, x1, y1 = (_num(b.get(k), k) for k in ("x0", "y0", "x1", "y1"))
        if (x0 < -EDGE_SLOP_PX or y0 < -EDGE_SLOP_PX
                or x1 > w + EDGE_SLOP_PX or y1 > h + EDGE_SLOP_PX):
            raise ValueError("a box is outside the image")
        x0, y0, x1, y1 = max(x0, 0.0), max(y0, 0.0), min(x1, float(w)), min(y1, float(h))
        if x1 - x0 < MIN_BOX_PX or y1 - y0 < MIN_BOX_PX:
            raise ValueError("a box is too small")
        if b.get("color") not in BOX_COLORS:
            raise ValueError("a box needs a colour, red or yellow")
        start = b.get("start")
        if start is not None:
            if isinstance(start, bool) or not isinstance(start, int) \
                    or not 0 <= start < len(starts):
                raise ValueError("a box names a start that isn't there")
            if start in seen:
                raise ValueError("a start is boxed twice")
            seen.add(start)
        out.append({"x0": x0, "y0": y0, "x1": x1, "y1": y1,
                    "color": b["color"], "start": start})
    for i, s in enumerate(starts):
        if s == "deleted" and i in seen:
            raise ValueError("a deleted start still has a box")
        if s != "deleted" and i not in seen:
            raise ValueError("a start that isn't deleted needs its box")
    return {"cant_tell": False, "boxes": out, "starts": list(starts)}


def _check_release_answer(item: LabelItem, answer: dict) -> dict:
    mark, frame = answer.get("mark"), answer.get("frame")
    if mark not in RELEASE_MARKS:
        raise ValueError("unknown mark")
    if mark == "off":
        n = item.payload["n"]
        if isinstance(frame, bool) or not isinstance(frame, int) or not 0 <= frame < n:
            raise ValueError("a release mark needs a frame in the clip")
        return {"mark": "off", "frame": frame}
    if frame is not None:
        raise ValueError("only a release mark takes a frame")
    return {"mark": mark, "frame": None}


def check_answer(item: LabelItem, answer: dict) -> dict:
    """The answer in the form we keep, or ValueError saying what is wrong."""
    if not isinstance(answer, dict):
        raise ValueError("the answer is not an object")
    if item.kind == "box":
        return _check_box_answer(item, answer)
    if item.kind == "release":
        return _check_release_answer(item, answer)
    raise ValueError(f"unknown kind {item.kind!r}")


def for_member(item: LabelItem, image_url: str) -> dict:
    """What a member is shown: the payload and the image, and nothing else.
    Not the reference answer, the item's origin, who has labelled it or how
    many have."""
    shown = {k: v for k, v in item.payload.items() if k not in ("image", "origin")}
    return {"id": item.id, "kind": item.kind, "image_url": image_url, **shown}
