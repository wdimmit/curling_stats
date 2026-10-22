#!/usr/bin/env python
"""One person's release marks from the site's labelling queue, as
datasets/releasemarks files, one per game.

    ADMIN_TOKEN=... python scripts/release/pull_site_marks.py --uid UID \\
        --out datasets/releasemarks/site-2026-10-22 [--state open]

The site hands each release item to two members and calls it `done` only
then, so `scripts/labels_export.py` (done and retired items, agreement between
two) does not see one trusted person's marks on items still `open`. This reads
the items in --state (GET /api/admin/label-items, as labels_export does) and
keeps that person's answer as the mark: `off` at the answered frame, `held`,
or null for can't tell. Rows are labels_export's release rows, `chosen`
"member", so `scripts/release/cut.py` cuts them like any other marks file.

A rock from a game already in datasets/releasemarks takes that file's tag, so a
fold that holds the game out holds this rock out with it; any other game is
tagged `site-<first six characters of its source id>`.
"""

import argparse
import json
import os
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))
import labels_export  # noqa: E402

MARKS = _ROOT / "datasets" / "releasemarks"


def known_tags() -> dict:
    """source_id -> tag of every marks file already in the dataset."""
    out = {}
    for p in MARKS.glob("s_*.json"):
        doc = json.loads(p.read_text())
        sid = doc["source_id"]
        out[sid] = doc.get("tag") or ("T1020" if sid == "s_1TH0dJNrbHeKLARuj" else sid[:6])
    return out


def row(item: dict, answer: dict) -> dict:
    """labels_export's release row for one person's answer."""
    mark = answer["answer"].get("mark")
    frame = answer["answer"].get("frame")
    verdict = {"mark": "off" if mark == "off" else "held" if mark == "held" else None,
               "frame": frame if mark == "off" else None}
    return labels_export._rock_row(item, verdict, [answer])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--uid", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--state", default="open")
    ap.add_argument("--base", default=os.environ.get("BASE_URL", "https://curling.dimmit.net"))
    a = ap.parse_args(argv)
    token = os.environ["ADMIN_TOKEN"]
    items = labels_export.fetch_items(a.base, token, "release", a.state, urllib.request.urlopen)
    tags = known_tags()
    games = defaultdict(list)
    meta = {}
    for it in items:
        mine = [x for x in it.get("answers") or [] if x["uid"] == a.uid]
        if not mine:
            continue
        r = row(it, mine[0])
        sid = r["source_id"]
        games[sid].append(r)
        meta[sid] = r
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    n = 0
    for sid, rows in sorted(games.items()):
        m = meta[sid]
        doc = {"source_id": sid, "video_id": m.get("video_id"), "title": m.get("title"),
               "tag": tags.get(sid, f"site-{sid[:6]}"), "marked": "site", "fps": 30,
               "definition": "the first frame the throwing hand is clearly off the handle "
                             "(last let-go after a re-grip)",
               "rocks": sorted(rows, key=lambda r: (r["end"], r["rock"]))}
        (out / f"{sid}.json").write_text(json.dumps(doc, indent=1))
        n += len(rows)
    print(f"{n} rocks of {len(games)} games -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
