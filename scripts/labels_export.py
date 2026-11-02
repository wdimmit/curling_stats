#!/usr/bin/env python
"""Bring the members' labels home: agreed ones as training data, disputed ones
for you to settle.

    ADMIN_TOKEN=... python scripts/labels_export.py --out datasets/member-labels/2026-11-05 \\
        [--kind box|release|player]

Reads every `done` and `retired` item from the site (GET /api/admin/label-items,
paged) with both members' answers, and the accounts (GET /api/admin/users) for
their emails. ADMIN_TOKEN and BASE_URL are as for scripts/users.py. Nothing is
written back to the site; a dispute you settle is recorded on this machine, by
the tools below, under datasets/.

Under --out:

  boxes/agreed/         images/<id>.jpg, labels/<id>.txt (YOLO, 0 red 1 yellow,
                        normalised to the crop) and agreed.jsonl: id, origin,
                        boxes in crop pixels, boxes_frame in whole-frame pixels,
                        members.
  boxes/disputed/       index.html, the box editor (train/boxedit) with the first
                        member's boxes to fix and the second's drawn dashed;
                        disputed.jsonl says why. Items nobody could answer
                        (`retired`) are here too, why "skipped", boxes empty.
  releases/agreed.json  the datasets/releasemarks row shape, chosen "member",
                        with each member's own mark under `members`.
  releases/disputed/    a `mark_release.py --prepared` session: the sprites cut
                        back into frames, index.json, marks.json "{}", and
                        disputed.json with why and both members' marks.
  Every agreed and disputed row, of both kinds, carries `members`: who
  labelled it, as [{"uid", "seconds"}] (a release row's also has the member's
  mark and frame), earliest first, so a bad habit can be traced to a person.

  boxes/member/<camera>/  with --member EMAIL instead: that member's own boxes
                        on every item they answered, open ones too, as the
                        label (images/, labels/, member.jsonl); check items and
                        can't-tells left out. For training on your own labels.

  players/labelled/     with --kind player (not in the default): images/<id>.jpg,
                        labels/<id>.txt (YOLO, class 2 = player only; empty when
                        nobody was on the ice) and players.jsonl: id, origin,
                        boxes in image pixels, members. One answer each, no pairs.
                        Answers an admin discarded are ignored entirely. A member
                        who fixed the stones ("Fix stones") also gives
                        stones/<id>.txt (classes 0/1, the frame's whole stone
                        label; empty if they removed every stone) and the row's
                        `stones`; overhead4_merge.py takes it over the base's.
  players/to_user/      the frames nobody could settle (a can't-tell, or `retired`)
                        as the box editor with its player option: the item's stones
                        (classes 0/1) and the detector's starts (class 2) to fix;
                        to_user.jsonl says why.
  players/spotcheck/    sheet-NN.jpg, 4 x 4 tiles of ceil(5%) of the labelled
                        answers, each tile its boxes, the member's email and the
                        item id; members who changed nothing are drawn more
                        often. spotcheck.jsonl says which and where.
  players/stonefix/     the same sheets of every frame whose stones a member
                        fixed: the stones before in thick grey, after in their
                        colours. stonefix.jsonl says which and where. For a look;
                        fixes go into training as they are.

  members.json          per account: email, how many of each kind, median seconds
                        per answer, checks {n, agree} against the items whose
                        answer we already knew, and for players untouched_rate:
                        the share of their answers that accepted every start and
                        drew nothing (can't-tells aside, and answers to frames
                        with no starts and no boxes, where there was nothing
                        to touch: those count in neither half).

Check items (those with a known answer) score the member and are otherwise left
out: their answer is not a member's to give. Agreement is
src/curling_score/train/labelagree.py.
"""

import argparse
import json
import math
import os
import random
import re
import statistics
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from curling_score.train import boxedit, labelagree  # noqa: E402

PAGE = 200
STATES = ("done", "retired")
COLORS = ("red", "yellow")
CLASSES = COLORS + ("player",)      # the detector's class numbers, 2 = player
ID_OK = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")
FPS = 30.0
# datasets/releasemarks/README.md's wording of the mark.
DEFINITION = ("the first frame the throwing hand is clearly off the handle "
              "(last let-go after a re-grip)")
FRAME_QUALITY = 88
SPOT_SHARE = 0.05                       # of the labelled player answers
SPOT_GRID = 4                           # a sheet is SPOT_GRID x SPOT_GRID tiles
SPOT_TILE = (480, 270)
SPOT_CAPTION = 22
SPOT_MARGIN = 6
PLAYER_BLUE = (42, 111, 208)            # boxedit's --ply
SPOT_STONES = {"red": (224, 60, 60), "yellow": (232, 192, 32)}


def _why(err: Exception) -> str:
    """An HTTP or network failure as one line: the status and the API's detail."""
    if isinstance(err, urllib.error.HTTPError):
        try:
            detail = json.loads(err.read() or b"{}").get("detail")
        except ValueError:
            detail = None
        return f"{err.code} {detail or err.reason}"
    return str(getattr(err, "reason", err))


def _read(req, opener) -> bytes:
    with opener(req, timeout=60) as r:
        return r.read()


def _api(base: str, token: str, path: str, query: dict | None, opener):
    url = base + path + ("?" + urllib.parse.urlencode(query) if query else "")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    return json.loads(_read(req, opener))


def fetch_items(base: str, token: str, kind: str, state: str, opener) -> list[dict]:
    """Every item of the kind in the state, each with its answers. The listing
    pages on `after`, a full-precision time whose "+00:00" has to be encoded."""
    out, after = [], None
    while True:
        query = {"kind": kind, "state": state, "limit": PAGE}
        if after:
            query["after"] = after
        page = _api(base, token, "/api/admin/label-items", query, opener)
        out += page["items"]
        after = page.get("after")
        if not page["items"] or not after:
            return out


def fetch_image(url: str, opener) -> bytes:
    """An item's image from its signed URL. The admin token goes to the API and
    nowhere else, so this request carries none."""
    return _read(urllib.request.Request(url), opener)


def _sorted_answers(item: dict) -> list[dict]:
    """The item's answers, earliest first: the first is the member whose boxes
    are editable on a dispute, the second's are the ghosts."""
    return sorted(item.get("answers") or [], key=lambda a: (str(a["at"]), a["uid"]))


def _norm(boxes: list[dict], w: float, h: float, classes=COLORS) -> list[list]:
    """Boxes in pixels as boxedit's [cls, cx, cy, w, h], normalised. A box's
    class is its `color`'s place in `classes`."""
    return [[classes.index(b["color"]), (b["x0"] + b["x1"]) / 2 / w, (b["y0"] + b["y1"]) / 2 / h,
             (b["x1"] - b["x0"]) / w, (b["y1"] - b["y0"]) / h] for b in boxes]


def _yolo(boxes: list[dict], w: float, h: float, classes=COLORS) -> str:
    return "".join(" ".join([str(c)] + [f"{v:.6f}" for v in rest]) + "\n"
                   for c, *rest in _norm(boxes, w, h, classes))


def _members(answers: list[dict]) -> list[dict]:
    """Who labelled an item and how long each took."""
    return [{"uid": a["uid"], "seconds": a["seconds"]} for a in answers]


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _sound(item: dict) -> bool:
    """An id safe to be a file name; the server's own pattern allows nothing worse
    than a leading dot."""
    return bool(ID_OK.match(str(item.get("id")))) and not str(item["id"]).startswith(".")


def export_boxes(items: list[dict], out: Path, opener) -> tuple[int, int, list[str]]:
    """Returns (agreed, disputed, ids skipped)."""
    agreed_dir, disputed_dir = out / "boxes" / "agreed", out / "boxes" / "disputed"
    for d in (agreed_dir / "images", agreed_dir / "labels", disputed_dir / "images"):
        d.mkdir(parents=True, exist_ok=True)
    agreed, disputed, editor, skipped = [], [], [], []
    for item in items:
        if item.get("reference") is not None:
            continue
        payload = item["payload"]
        w, h = payload["w"], payload["h"]
        origin = payload.get("origin") or {}
        answers = _sorted_answers(item)
        if item["state"] == "retired":
            why, verdict = "skipped", None
        elif len(answers) < 2:
            why, verdict = "incomplete", None
        else:
            verdict = labelagree.box_verdict(answers[0]["answer"], answers[1]["answer"])
            why = verdict["why"]
        try:
            if not _sound(item):
                raise ValueError("not a usable id")
            image = fetch_image(item["image_url"], opener)
        except (OSError, ValueError) as err:
            skipped.append(f"{item.get('id')}: {_why(err)}")
            continue
        if verdict is not None and verdict["agree"]:
            (agreed_dir / "images" / f"{item['id']}.jpg").write_bytes(image)
            (agreed_dir / "labels" / f"{item['id']}.txt").write_text(
                _yolo(verdict["boxes"], w, h))
            cx, cy = (origin.get("crop") or [0, 0])[:2]
            agreed.append({"id": item["id"], "origin": origin, "members": _members(answers),
                           "boxes": verdict["boxes"],
                           "boxes_frame": [{**b, "x0": b["x0"] + cx, "x1": b["x1"] + cx,
                                            "y0": b["y0"] + cy, "y1": b["y1"] + cy}
                                           for b in verdict["boxes"]]})
            continue
        (disputed_dir / "images" / f"{item['id']}.jpg").write_bytes(image)
        disputed.append({"id": item["id"], "why": why, "origin": origin,
                         "members": _members(answers),
                         "answers": [{"uid": a["uid"], "answer": a["answer"]} for a in answers]})
        mine, theirs = (answers + [None, None])[:2] if why != "skipped" else (None, None)
        editor.append({"stem": item["id"], "image": f"images/{item['id']}.jpg",
                       "width": w, "height": h, "geom": None,
                       "boxes": _norm(mine["answer"]["boxes"], w, h) if mine else [],
                       "ghosts": _norm(theirs["answer"]["boxes"], w, h) if theirs else []})
    _write_jsonl(agreed_dir / "agreed.jsonl", agreed)
    _write_jsonl(disputed_dir / "disputed.jsonl", disputed)
    boxedit.render(editor, disputed_dir, scope="member-labels", title="Disputed boxes",
                   proposals=True)
    return len(agreed), len(disputed), skipped


def export_member_boxes(items: list[dict], uid: str, out: Path, opener) -> tuple[dict, list[str]]:
    """One member's own boxes, taken as the label whatever the item's state:
    boxes/member/<camera>/{images,labels} and member.jsonl. Check items and the
    member's can't-tells are left out. Returns (counts by camera, ids skipped)."""
    root = out / "boxes" / "member"
    rows, counts, skipped = {}, {}, []
    for item in items:
        mine = [a for a in _sorted_answers(item) if a["uid"] == uid]
        if not mine or item.get("reference") is not None or mine[-1]["answer"].get("cant_tell"):
            continue
        a = mine[-1]
        payload = item["payload"]
        origin = payload.get("origin") or {}
        camera = origin.get("camera") or "overhead"
        d = root / camera
        try:
            if not _sound(item):
                raise ValueError("not a usable id")
            image = fetch_image(item["image_url"], opener)
        except (OSError, ValueError) as err:
            skipped.append(f"{item.get('id')}: {_why(err)}")
            continue
        for sub in ("images", "labels"):
            (d / sub).mkdir(parents=True, exist_ok=True)
        boxes = [{k: b[k] for k in ("x0", "y0", "x1", "y1", "color")} for b in a["answer"]["boxes"]]
        (d / "images" / f"{item['id']}.jpg").write_bytes(image)
        (d / "labels" / f"{item['id']}.txt").write_text(_yolo(boxes, payload["w"], payload["h"]))
        cx, cy = (origin.get("crop") or [0, 0])[:2]
        rows.setdefault(camera, []).append(
            {"id": item["id"], "state": item["state"], "origin": origin,
             "members": _members(mine), "boxes": boxes,
             "boxes_frame": [{**b, "x0": b["x0"] + cx, "x1": b["x1"] + cx,
                              "y0": b["y0"] + cy, "y1": b["y1"] + cy} for b in boxes]})
        counts[camera] = counts.get(camera, 0) + 1
    for camera, got in rows.items():
        _write_jsonl(root / camera / "member.jsonl", got)
    return counts, skipped


def _member_marks(answers: list[dict]) -> list[dict]:
    return [{"uid": a["uid"], "seconds": a["seconds"], "mark": a["answer"]["mark"],
             "frame": a["answer"]["frame"]} for a in answers]


def _rock_row(item: dict, verdict: dict, answers: list[dict]) -> dict:
    origin = item["payload"].get("origin") or {}
    frames = origin["frames"]
    f = verdict["frame"]
    return {"id": item["id"], **{k: v for k, v in origin.items() if k != "frames"},
            "chosen": "member",
            "mark": verdict["mark"], "frame": f,
            "o": frames[f]["o"] if f is not None else None,
            "yp": frames[f]["yp"] if f is not None else None,
            "yp_prev": frames[f - 1]["yp"] if f else None,
            "members": _member_marks(answers)}


def _cut_frames(sprite: bytes, payload: dict, stem: str, dest: Path) -> list[dict]:
    """The sprite's frames written as <stem>_<i:03d>.jpg, row-major as
    label_releases.make_sprite laid them out, as {"file","o","yp"}. The
    readings come from the item's origin, where the publisher keeps them."""
    import cv2
    import numpy as np
    img = cv2.imdecode(np.frombuffer(sprite, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("the sprite could not be read")
    cols, fw, fh = payload["cols"], payload["fw"], payload["fh"]
    out = []
    for i, f in enumerate(payload["origin"]["frames"][:payload["n"]]):
        r, c = divmod(i, cols)
        tile = img[r * fh:(r + 1) * fh, c * fw:(c + 1) * fw]
        if tile.shape[:2] != (fh, fw):
            raise ValueError("the sprite is smaller than its layout")
        name = f"{stem}_{i:03d}.jpg"
        cv2.imwrite(str(dest / name), tile, [cv2.IMWRITE_JPEG_QUALITY, FRAME_QUALITY])
        out.append({"file": name, "o": f["o"], "yp": f["yp"]})
    return out


def export_releases(items: list[dict], out: Path, opener) -> tuple[int, int, list[str]]:
    rel = out / "releases"
    session = rel / "disputed"
    session.mkdir(parents=True, exist_ok=True)
    rocks, index, disputed, batches, skipped = [], [], [], [], []
    for item in items:
        if item.get("reference") is not None:
            continue
        payload = item["payload"]
        origin = payload.get("origin") or {}
        answers = _sorted_answers(item)
        if item["state"] == "retired":
            why, verdict = "skipped", None
        elif len(answers) < 2:
            why, verdict = "incomplete", None
        else:
            verdict = labelagree.release_verdict(answers[0]["answer"], answers[1]["answer"])
            why = verdict["why"]
        if verdict is not None and verdict["agree"]:
            rocks.append(_rock_row(item, verdict, answers))
            batches.append(item["batch"])
            continue
        try:
            if not _sound(item):
                raise ValueError("not a usable id")
            frames = _cut_frames(fetch_image(item["image_url"], opener), payload,
                                 item["id"], session)
        except (OSError, ValueError) as err:
            skipped.append(f"{item.get('id')}: {_why(err)}")
            continue
        index.append({"id": item["id"], "tag": "", "video_id": origin.get("video_id"),
                      "end": origin.get("end"), "rock": origin.get("rock"),
                      "color": origin.get("color"), "position": origin.get("position"),
                      "label": origin.get("label"), "t_release_s": origin.get("t_release_s"),
                      "frames": frames})
        disputed.append({"id": item["id"], "why": why,
                         "members": _member_marks(answers)})
    batch = ", ".join(sorted(set(batches))) or None
    (rel / "agreed.json").write_text(json.dumps(
        {"batch": batch, "fps": FPS, "definition": DEFINITION, "rocks": rocks}, indent=1))
    (session / "index.json").write_text(json.dumps(index, indent=1))
    # The session is worked in place by mark_release.py, which saves each mark
    # here; a re-export must add to the session, never empty it.
    if not (session / "marks.json").exists():
        (session / "marks.json").write_text("{}")
    (session / "disputed.json").write_text(json.dumps(disputed, indent=1))
    return len(rocks), len(disputed), skipped


def _live(item: dict) -> list[dict]:
    """A player item's answers that count, earliest first. An answer an admin
    discarded is ignored entirely; documents from before the flag lack the key."""
    return [a for a in _sorted_answers(item) if not a.get("discarded")]


def _untouched(answer: dict) -> bool:
    """Every start accepted as it was and nothing drawn: the member changed
    nothing. From the stored answer, not the page. (An answer to a frame with no
    starts and no boxes is vacuously so; untouched_rates leaves those out.)"""
    return (not answer.get("cant_tell")
            and all(s == "untouched" for s in answer.get("starts") or [])
            and not any(b.get("start") is None for b in answer.get("boxes") or []))


def untouched_rates(items: list[dict]) -> dict[str, float]:
    """Per uid, the share of their live player answers that were untouched,
    over those that were not can't-tells and had something to touch: an answer
    to an item with no starts and no boxes is in neither the numerator nor the
    denominator. A member with only those has none."""
    got: dict[str, list[int]] = {}
    for item in items:
        if item["kind"] != "player":
            continue
        for a in _live(item):
            nothing_to_touch = (not item["payload"].get("starts")
                                and not a["answer"].get("boxes"))
            if not a["answer"].get("cant_tell") and not nothing_to_touch:
                n = got.setdefault(a["uid"], [0, 0])
                n[0] += 1
                n[1] += _untouched(a["answer"])
    return {uid: round(k / n, 3) for uid, (n, k) in got.items()}


def spot_weight(rate: float | None) -> float:
    """How likely a member's answer is to be drawn for the spot check, against
    one that changed something: a member who changes nothing is looked at more."""
    return 1.0 + 3.0 * (rate or 0.0)


def spot_pick(rows: list[dict], n: int, rate_of, seed: int = 0) -> list[dict]:
    """`n` of the rows without replacement, each drawn with probability
    proportional to spot_weight(rate_of(row)): the keys u ** (1 / w), the n
    largest. Fixed by `seed`; returned in the rows' order."""
    rng = random.Random(seed)
    keys = [rng.random() ** (1.0 / spot_weight(rate_of(r))) for r in rows]
    best = sorted(range(len(rows)), key=lambda i: -keys[i])[:n]
    return [rows[i] for i in sorted(best)]


def _clip_players(boxes: list[dict], w: float, h: float) -> list[dict]:
    """The player boxes inside the image, as the page may let one hang past its
    edge; one with nothing left (under a pixel) is dropped. All are class 2."""
    out = []
    for b in boxes:
        x0, x1 = max(0.0, min(b["x0"], b["x1"])), min(float(w), max(b["x0"], b["x1"]))
        y0, y1 = max(0.0, min(b["y0"], b["y1"])), min(float(h), max(b["y0"], b["y1"]))
        if x1 - x0 >= 1 and y1 - y0 >= 1:
            out.append({"x0": x0, "y0": y0, "x1": x1, "y1": y1, "color": "player"})
    return out


def _spot_tile(row: dict, font):
    """One answer's frame, its stones thin and its players blue, with the
    member's email and the item id under it."""
    from PIL import Image, ImageDraw

    tw, th = SPOT_TILE
    img = Image.open(row["path"]).convert("RGB")
    w, h = row["w"], row["h"]
    k = min(tw / w, th / h)
    img = img.resize((max(1, round(w * k)), max(1, round(h * k))))
    d = ImageDraw.Draw(img)
    for b in row.get("before") or ():
        d.rectangle([b["x0"] * k, b["y0"] * k, b["x1"] * k, b["y1"] * k],
                    outline=(150, 150, 150), width=3)
    for b in row["stones"]:
        d.rectangle([b["x0"] * k, b["y0"] * k, b["x1"] * k, b["y1"] * k],
                    outline=SPOT_STONES.get(b["color"], (160, 160, 160)), width=1)
    for b in row["boxes"]:
        d.rectangle([b["x0"] * k, b["y0"] * k, b["x1"] * k, b["y1"] * k],
                    outline=PLAYER_BLUE, width=2)
    tile = Image.new("RGB", (tw, th + SPOT_CAPTION), (255, 255, 255))
    tile.paste(img, (0, 0))
    ImageDraw.Draw(tile).text((2, th + 3), f"{row['email']}  {row['id']}", fill=(0, 0, 0), font=font)
    return tile


def _write_spotcheck(rows: list[dict], dest: Path, log_name: str = "spotcheck.jsonl") -> None:
    """spotcheck/sheet-NN.jpg, SPOT_GRID x SPOT_GRID tiles a sheet (the last
    sheet as many rows of tiles as it needs), and spotcheck.jsonl."""
    from PIL import Image, ImageFont

    dest.mkdir(parents=True, exist_ok=True)
    font = ImageFont.load_default(size=13)
    tw, th = SPOT_TILE[0], SPOT_TILE[1] + SPOT_CAPTION
    per = SPOT_GRID * SPOT_GRID
    log = []
    for s in range(0, len(rows), per):
        chunk = rows[s:s + per]
        nrows = -(-len(chunk) // SPOT_GRID)
        sheet = Image.new("RGB", (SPOT_GRID * (tw + SPOT_MARGIN) + SPOT_MARGIN,
                                  nrows * (th + SPOT_MARGIN) + SPOT_MARGIN), (200, 200, 200))
        name = f"sheet-{s // per + 1:02d}.jpg"
        for i, row in enumerate(chunk):
            r, c = divmod(i, SPOT_GRID)
            sheet.paste(_spot_tile(row, font), (SPOT_MARGIN + c * (tw + SPOT_MARGIN),
                                                SPOT_MARGIN + r * (th + SPOT_MARGIN)))
            log.append({"id": row["id"], "uid": row["uid"], "email": row["email"],
                        "sheet": name, "tile": i})
        sheet.save(dest / name, "JPEG", quality=FRAME_QUALITY)
    _write_jsonl(dest / log_name, log)


def export_players(items: list[dict], out: Path, opener, seed: int = 0,
                   users=()) -> tuple[int, int, list[str]]:
    """Returns (written, to the user, ids skipped). A `done` item with one live
    answer that is not a can't-tell is a label (class 2 only; empty when nobody
    was on the ice); a can't-tell or a `retired` item goes to the user's box
    editor; an item whose answers are all discarded goes nowhere. Then the spot
    check, drawn from what was written. `users` gives the emails for it."""
    root = out / "players"
    lab, mine = root / "labelled", root / "to_user"
    for d in (lab / "images", lab / "labels", mine / "images"):
        d.mkdir(parents=True, exist_ok=True)
    emails = {u["id"]: u.get("email") for u in users}
    rates = untouched_rates(items)
    written, editor, to_user, spot, fixes, skipped = [], [], [], [], [], []
    for item in items:
        if item.get("reference") is not None:
            continue
        answers = _live(item)
        if item["state"] == "retired":
            why = "skipped"
        elif answers and answers[0]["answer"].get("cant_tell"):
            why = "cant_tell"
        elif item["state"] == "done" and answers:
            why = None
        else:
            continue
        payload = item["payload"]
        w, h = payload["w"], payload["h"]
        origin = payload.get("origin") or {}
        try:
            if not _sound(item):
                raise ValueError("not a usable id")
            image = fetch_image(item["image_url"], opener)
        except (OSError, ValueError) as err:
            skipped.append(f"{item.get('id')}: {_why(err)}")
            continue
        if why is not None:
            (mine / "images" / f"{item['id']}.jpg").write_bytes(image)
            to_user.append({"id": item["id"], "why": why, "origin": origin,
                            "members": _members(answers)})
            starts = [{**b, "color": "player"} for b in payload.get("starts") or []]
            editor.append({"stem": item["id"], "image": f"images/{item['id']}.jpg",
                           "width": w, "height": h, "geom": None,
                           "boxes": _norm((payload.get("stones") or []) + starts, w, h, CLASSES)})
            continue
        boxes = _clip_players(answers[0]["answer"]["boxes"], w, h)
        (lab / "images" / f"{item['id']}.jpg").write_bytes(image)
        (lab / "labels" / f"{item['id']}.txt").write_text(_yolo(boxes, w, h, CLASSES))
        row = {"id": item["id"], "origin": origin, "members": _members(answers), "boxes": boxes}
        uid = answers[0]["uid"]
        fix = answers[0]["answer"].get("stones")
        if fix is not None:
            (lab / "stones").mkdir(exist_ok=True)
            (lab / "stones" / f"{item['id']}.txt").write_text(_yolo(fix, w, h))
            row["stones"] = fix
            fixes.append({"id": item["id"], "uid": uid, "email": emails.get(uid) or uid,
                          "path": lab / "images" / f"{item['id']}.jpg", "w": w, "h": h,
                          "before": payload.get("stones") or [], "stones": fix, "boxes": boxes})
        written.append(row)
        spot.append({"id": item["id"], "uid": uid, "email": emails.get(uid) or uid,
                     "path": lab / "images" / f"{item['id']}.jpg", "w": w, "h": h,
                     "stones": payload.get("stones") or [], "boxes": boxes})
    _write_jsonl(lab / "players.jsonl", written)
    _write_jsonl(mine / "to_user.jsonl", to_user)
    boxedit.render(editor, mine, scope="member-labels-players", title="Players nobody could settle",
                   proposals=True, kind="player")
    spot.sort(key=lambda r: r["id"])
    _write_spotcheck(spot_pick(spot, math.ceil(SPOT_SHARE * len(spot)),
                               lambda r: rates.get(r["uid"]), seed), root / "spotcheck")
    _write_spotcheck(fixes, root / "stonefix", "stonefix.jsonl")
    return len(written), len(to_user), skipped


def member_report(items: list[dict], users: list[dict]) -> dict:
    """Per uid: email, answers by kind, median seconds, the score on check
    items and, for player answers, the untouched rate (None until they have
    one). A member who answered anything is here even if no account is listed;
    a discarded player answer does not count."""
    emails = {u["id"]: u.get("email") for u in users}
    rates = untouched_rates(items)
    seconds: dict[str, list] = {}
    out: dict[str, dict] = {}
    for item in items:
        for a in (_live(item) if item["kind"] == "player" else item.get("answers") or []):
            m = out.setdefault(a["uid"], {"email": emails.get(a["uid"]), "box": 0, "release": 0,
                                          "player": 0, "median_seconds": None,
                                          "untouched_rate": rates.get(a["uid"]),
                                          "checks": {"n": 0, "agree": 0}})
            m[item["kind"]] += 1
            seconds.setdefault(a["uid"], []).append(a["seconds"])
            if item.get("reference") is not None:
                m["checks"]["n"] += 1
                m["checks"]["agree"] += labelagree.agrees_with_reference(
                    item["kind"], a["answer"], item["reference"])
    for uid, m in out.items():
        m["median_seconds"] = statistics.median(seconds[uid])
    return out


def main(argv=None, opener=urllib.request.urlopen) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True, help="e.g. datasets/member-labels/<YYYY-MM-DD>")
    ap.add_argument("--kind", choices=("box", "release", "player"),
                    help="only this kind (default box and release)")
    ap.add_argument("--member", metavar="EMAIL",
                    help="instead, that member's own boxes from every item they answered, "
                         "open ones too, taken as the label (boxes/member/<camera>/)")
    args = ap.parse_args(argv)
    token = os.environ.get("ADMIN_TOKEN", "").strip()
    if not token:
        print("set ADMIN_TOKEN (see scripts/users.py)", file=sys.stderr)
        return 2
    base = os.environ.get("BASE_URL", "https://curling.dimmit.net").rstrip("/")
    out = Path(args.out)
    if args.member:
        try:
            users = _api(base, token, "/api/admin/users", {"limit": 1000}, opener)["users"]
            uid = next((u["id"] for u in users if u.get("email") == args.member), None)
            if uid is None:
                print(f"no account {args.member}", file=sys.stderr)
                return 1
            items = [i for s in ("open",) + STATES for i in fetch_items(base, token, "box", s, opener)]
        except OSError as err:
            print(f"could not read the labels: {_why(err)}", file=sys.stderr)
            return 1
        counts, skipped = export_member_boxes(items, uid, out, opener)
        for s in skipped:
            print(f"skipped {s}", file=sys.stderr)
        print(f"wrote {out / 'boxes' / 'member'}: "
              + (", ".join(f"{n} {c}" for c, n in sorted(counts.items())) or "nothing"))
        return 1 if skipped else 0
    kinds = [args.kind] if args.kind else ["box", "release"]
    try:
        users = _api(base, token, "/api/admin/users", {"limit": 1000}, opener)["users"]
        by_kind = {k: [i for s in STATES for i in fetch_items(base, token, k, s, opener)]
                   for k in kinds}
    except OSError as err:              # URLError and HTTPError are OSErrors
        print(f"could not read the labels: {_why(err)}", file=sys.stderr)
        return 1
    out.mkdir(parents=True, exist_ok=True)
    skipped = []
    if "box" in by_kind:
        a, d, bad = export_boxes(by_kind["box"], out, opener)
        print(f"boxes: {a} agreed, {d} disputed")
        skipped += bad
    if "release" in by_kind:
        a, d, bad = export_releases(by_kind["release"], out, opener)
        print(f"releases: {a} agreed, {d} disputed")
        skipped += bad
    if "player" in by_kind:
        n, u, bad = export_players(by_kind["player"], out, opener, users=users)
        print(f"players: {n} labelled, {u} for you")
        skipped += bad
    report = member_report([i for items in by_kind.values() for i in items], users)
    (out / "members.json").write_text(json.dumps(report, indent=1))
    for s in skipped:
        print(f"skipped {s}", file=sys.stderr)
    print(f"wrote {out} ({len(report)} members)")
    return 1 if skipped else 0


if __name__ == "__main__":
    sys.exit(main())
