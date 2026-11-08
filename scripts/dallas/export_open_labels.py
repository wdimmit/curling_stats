#!/usr/bin/env python
"""Bring an open labelling site's labels home (service/site.py ``open_labelling``: DFW's).

    ADMIN_TOKEN=... BASE_URL=https://dfwcurling.dimmit.net \\
        python export_open_labels.py --out DIR [--batch NAME ...]

Every answered rock-box item (kind "box"), as `labels_export.py --member` writes
them -- DIR/boxes/member/<camera>/{images,labels,member.jsonl} -- for the one
labeller everyone on such a site is (uid ``open-labeller``, who has no account
to look up by email). And every answered broom item (kind "broom"), as
DIR/brooms/{images,labels,brooms.jsonl}: YOLO class 0, broom_head, as the broom
trees label it. ``--batch`` keeps only items of those batches.
"""

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import labels_export as LE  # noqa: E402

UID = "open-labeller"


def fetch_answered(base, token, kind, batches):
    items = []
    for state in ("done", "open", "retired"):
        items += LE.fetch_items(base, token, kind, state, urllib.request.urlopen)
    items = [i for i in items if any(a["uid"] == UID for a in i.get("answers") or [])]
    return [i for i in items if not batches or i.get("batch") in batches]


def export_brooms(items, out: Path) -> int:
    d = out / "brooms"
    for sub in ("images", "labels"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    rows = []
    for item in items:
        a = [x for x in LE._sorted_answers(item) if x["uid"] == UID][-1]["answer"]
        if a.get("cant_tell"):
            continue
        w, h = item["payload"]["w"], item["payload"]["h"]
        boxes = [{k: b[k] for k in ("x0", "y0", "x1", "y1")} for b in a["boxes"]]
        (d / "images" / f"{item['id']}.jpg").write_bytes(LE.fetch_image(item["image_url"],
                                                                         urllib.request.urlopen))
        (d / "labels" / f"{item['id']}.txt").write_text(
            "".join(f"0 {(b['x0'] + b['x1']) / 2 / w:.6f} {(b['y0'] + b['y1']) / 2 / h:.6f} "
                    f"{(b['x1'] - b['x0']) / w:.6f} {(b['y1'] - b['y0']) / h:.6f}\n" for b in boxes))
        rows.append({"id": item["id"], "batch": item.get("batch"),
                     "origin": item["payload"].get("origin") or {}, "boxes": boxes})
    (d / "brooms.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--batch", action="append", default=[])
    a = ap.parse_args()
    base, token = os.environ.get("BASE_URL", "").rstrip("/"), os.environ["ADMIN_TOKEN"]
    boxes = fetch_answered(base, token, "box", set(a.batch))
    counts, skipped = LE.export_member_boxes(boxes, UID, a.out, urllib.request.urlopen)
    brooms = export_brooms(fetch_answered(base, token, "broom", set(a.batch)), a.out)
    print(f"boxes by camera {counts}, skipped {len(skipped)}; brooms {brooms}")


if __name__ == "__main__":
    main()
