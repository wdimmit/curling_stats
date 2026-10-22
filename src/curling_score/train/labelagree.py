"""Whether two members' answers to one item are the same answer.

Two members label each item; this runs at export time, on the laptop, and
decides which items become training labels. It works on the normalised answers
the service keeps (``labelling.check_answer``) and nothing else, so it carries
no service imports and can be run over an export with no server around.

Boxes agree when every box has a partner at IoU 0.7 or better and the
partners share a colour; the label is then the mean of each pair. Two
independent hand-drawn boxes on a 20-40 px stone rarely beat 0.7 by luck, so
the bar is a real statement that both looked at the same stone. A pair that
overlaps but disagrees on colour is its own failure, because that is the
question the colour labels exist to settle.

A **rubber stamp** is the failure the bar cannot see: both members accepted
the detector's boxes without touching one. Their boxes match perfectly, and
they say nothing the detector didn't, so that agreement is disputed instead of
trusted.

Release marks agree when they name the same frame to within a few: the hand
leaves the handle over several frames, so a mark a frame or two either side is
the same mark. The later frame is kept, since the first frame the stone is
clearly free is the one we want.
"""

from __future__ import annotations

# Two hand-drawn boxes on the same stone should clear this; a box half a
# stone off does not.
MIN_IOU = 0.7
# Frames apart at which two release marks are still the same mark.
FRAME_TOL = 3

_UNTOUCHED = ("untouched", "kept")


def iou(a: dict, b: dict) -> float:
    """Intersection over union of two boxes with x0, y0, x1, y1."""
    w = min(a["x1"], b["x1"]) - max(a["x0"], b["x0"])
    h = min(a["y1"], b["y1"]) - max(a["y0"], b["y0"])
    if w <= 0 or h <= 0:
        return 0.0
    inter = w * h
    area = lambda r: (r["x1"] - r["x0"]) * (r["y1"] - r["y0"])
    union = area(a) + area(b) - inter
    return inter / union if union > 0 else 0.0


def match_boxes(a: list[dict], b: list[dict], min_iou: float = MIN_IOU
                ) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    """Pair boxes one-to-one, best overlap first. Returns the pairs as
    (index in a, index in b) in a's order, then the indices of a and b left
    without a partner."""
    scored = sorted(((iou(x, y), i, j) for i, x in enumerate(a)
                     for j, y in enumerate(b)), key=lambda t: (-t[0], t[1], t[2]))
    pairs, used_a, used_b = [], set(), set()
    for score, i, j in scored:
        if score < min_iou:
            break
        if i in used_a or j in used_b:
            continue
        pairs.append((i, j))
        used_a.add(i)
        used_b.add(j)
    pairs.sort()
    return (pairs, [i for i in range(len(a)) if i not in used_a],
            [j for j in range(len(b)) if j not in used_b])


def _rubber_stamp(answer: dict) -> bool:
    """Every detected start accepted as it was, and nothing drawn new."""
    return (all(s in _UNTOUCHED for s in answer["starts"])
            and all(b.get("start") is not None for b in answer["boxes"]))


def _no(why: str, **rest) -> dict:
    return {"agree": False, "why": why, **rest}


def box_verdict(a: dict, b: dict) -> dict:
    """{"agree", "why", "boxes"}: why is None, "cant_tell", "unmatched",
    "colour" or "rubber_stamp". Agreed boxes are each pair's mean, with the
    shared colour and no start, since the start is the detector's bookkeeping
    and not part of the label."""
    if a["cant_tell"] or b["cant_tell"]:
        return _no("cant_tell", boxes=None)
    if _rubber_stamp(a) and _rubber_stamp(b):
        return _no("rubber_stamp", boxes=None)
    pairs, lone_a, lone_b = match_boxes(a["boxes"], b["boxes"])
    if lone_a or lone_b:
        return _no("unmatched", boxes=None)
    if any(a["boxes"][i]["color"] != b["boxes"][j]["color"] for i, j in pairs):
        return _no("colour", boxes=None)
    mean = lambda x, y, k: (x[k] + y[k]) / 2
    boxes = []
    for i, j in pairs:
        x, y = a["boxes"][i], b["boxes"][j]
        boxes.append({**{k: mean(x, y, k) for k in ("x0", "y0", "x1", "y1")},
                      "color": x["color"]})
    return {"agree": True, "why": None, "boxes": boxes}


def release_verdict(a: dict, b: dict, tol: int = FRAME_TOL) -> dict:
    """{"agree", "why", "mark", "frame"}: why is None, "cant_tell", "mixed"
    (one said off, the other held) or "apart" (both off, too many frames
    between)."""
    gone = {"mark": None, "frame": None}
    if "cant_tell" in (a["mark"], b["mark"]):
        return _no("cant_tell", **gone)
    if a["mark"] != b["mark"]:
        return _no("mixed", **gone)
    if a["mark"] == "held":
        return {"agree": True, "why": None, "mark": "held", "frame": None}
    if abs(a["frame"] - b["frame"]) > tol:
        return _no("apart", **gone)
    return {"agree": True, "why": None, "mark": "off",
            "frame": max(a["frame"], b["frame"])}


def agrees_with_reference(kind: str, answer: dict, reference: dict) -> bool:
    """Whether a member's answer matches a known-good one, by the same rules.
    A rubber stamp is not checked: against a known answer, accepting the
    detector's boxes unchanged is simply right when they are."""
    if kind == "box":
        if answer["cant_tell"]:
            return False
        ref = reference["boxes"]
        pairs, lone_a, lone_b = match_boxes(answer["boxes"], ref)
        return (not lone_a and not lone_b and all(
            answer["boxes"][i]["color"] == ref[j]["color"] for i, j in pairs))
    if kind == "release":
        return release_verdict(answer, reference)["agree"]
    raise ValueError(f"unknown kind {kind!r}")
