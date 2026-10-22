#!/usr/bin/env python
"""Bring the members' labels home: agreed ones as training data, disputed ones
for you to settle.

    ADMIN_TOKEN=... python scripts/labels_export.py --out datasets/member-labels/2026-11-05 \\
        [--kind box|release]

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

  members.json          per account: email, how many of each kind, median seconds
                        per answer, and checks {n, agree} against the items whose
                        answer we already knew.

Check items (those with a known answer) score the member and are otherwise left
out: their answer is not a member's to give. Agreement is
src/curling_score/train/labelagree.py.
"""

import argparse
import json
import os
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
ID_OK = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")
FPS = 30.0
# datasets/releasemarks/README.md's wording of the mark.
DEFINITION = ("the first frame the throwing hand is clearly off the handle "
              "(last let-go after a re-grip)")
FRAME_QUALITY = 88


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


def _norm(boxes: list[dict], w: float, h: float) -> list[list]:
    """Boxes in pixels as boxedit's [cls, cx, cy, w, h], normalised."""
    return [[COLORS.index(b["color"]), (b["x0"] + b["x1"]) / 2 / w, (b["y0"] + b["y1"]) / 2 / h,
             (b["x1"] - b["x0"]) / w, (b["y1"] - b["y0"]) / h] for b in boxes]


def _yolo(boxes: list[dict], w: float, h: float) -> str:
    return "".join(" ".join([str(c)] + [f"{v:.6f}" for v in rest]) + "\n"
                   for c, *rest in _norm(boxes, w, h))


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


def member_report(items: list[dict], users: list[dict]) -> dict:
    """Per uid: email, answers by kind, median seconds, and the score on check
    items. A member who answered anything is here even if no account is listed."""
    emails = {u["id"]: u.get("email") for u in users}
    seconds: dict[str, list] = {}
    out: dict[str, dict] = {}
    for item in items:
        for a in item.get("answers") or []:
            m = out.setdefault(a["uid"], {"email": emails.get(a["uid"]), "box": 0, "release": 0,
                                          "median_seconds": None,
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
    ap.add_argument("--kind", choices=("box", "release"), help="only this kind (default both)")
    args = ap.parse_args(argv)
    token = os.environ.get("ADMIN_TOKEN", "").strip()
    if not token:
        print("set ADMIN_TOKEN (see scripts/users.py)", file=sys.stderr)
        return 2
    base = os.environ.get("BASE_URL", "https://curling.dimmit.net").rstrip("/")
    out = Path(args.out)
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
    report = member_report([i for items in by_kind.values() for i in items], users)
    (out / "members.json").write_text(json.dumps(report, indent=1))
    for s in skipped:
        print(f"skipped {s}", file=sys.stderr)
    print(f"wrote {out} ({len(report)} members)")
    return 1 if skipped else 0


if __name__ == "__main__":
    sys.exit(main())
