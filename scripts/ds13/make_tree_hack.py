"""The hack wave as a YOLO tree: train, val and a held-out split.

    ./.venv/bin/python scripts/ds13/make_tree_hack.py ~/curling-work/ds13hack/wave1 \\
        datasets/ds13/manifest-hack1.json \\
        datasets/ds13/edits/ds13-hack1-20260928-211534.json \\
        datasets/ds13/edits/ds13-hack1-colourfix-20260928.json \\
        --exclude 8un0-JBKkLA_l_000904_00 --out ~/curling-work/ds13hack/tree-hack1

Edits are merged in the order given (``labels.merge_edits``: a later file
restates a frame an earlier one boxed), so a correction goes last. The wave was
pre-labelled, so only frames marked reviewed count. A box whose centre lies in
the grey mask is dropped: a frame reviewed before the mask arrived may carry one.

Train games go 85/15 to train/val by a stable hash of the delivery; held-out
games go to ``heldout`` whole and are never trained or validated on.
"""
import argparse
import hashlib
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mask_wave import inside, view_of, window  # noqa: E402


def split_for(m: dict, val_fraction: float) -> str:
    if m["role"] == "heldout":
        return "heldout"
    key = f"{m['video_id']}:{m['end']}:{m['shot']}".encode()
    u = int(hashlib.sha256(key).hexdigest()[:8], 16) / 0xFFFFFFFF
    return "val" if u < val_fraction else "train"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("wave")
    ap.add_argument("manifest")
    ap.add_argument("edits", nargs="+", help="session files, merged in this order")
    ap.add_argument("--out", required=True)
    ap.add_argument("--exclude", nargs="*", default=[], help="stems left out entirely")
    ap.add_argument("--val-fraction", type=float, default=0.15)
    args = ap.parse_args()

    from curling_score.train import labels

    wave = Path(args.wave).expanduser(); out = Path(args.out).expanduser()
    man = {m["stem"]: m for m in json.loads(Path(args.manifest).read_text())}
    items = {i["stem"]: i for i in json.loads((wave / "items.json").read_text())}
    e = labels.merge_edits(*[json.loads(Path(f).read_text()) for f in args.edits])
    if out.exists():
        shutil.rmtree(out)
    counts, boxes_n, masked, record = Counter(), Counter(), 0, []
    for stem in sorted(e.reviewed):
        if stem in args.exclude or stem not in items:
            continue
        it, m = items[stem], man[stem]
        W, H = it["width"], it["height"]
        lo, hi = window(view_of(m), m["crop_top"], H, W, m.get("mask_half_m") or 99.0)
        rows = e.boxes.get(stem, [])
        kept = [b for b in rows if inside(b, lo, hi, W, H)]
        masked += len(rows) - len(kept)
        split = split_for(m, args.val_fraction)
        (out / "images" / split).mkdir(parents=True, exist_ok=True)
        (out / "labels" / split).mkdir(parents=True, exist_ok=True)
        shutil.copy2(wave / it["image"], out / "images" / split / f"{stem}.jpg")
        (out / "labels" / split / f"{stem}.txt").write_text(
            "".join(f"{int(c)} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}\n" for c, cx, cy, w, h in kept))
        counts[split] += 1; boxes_n[split] += len(kept)
        record.append({"stem": stem, "split": split, "boxes": len(kept), "role": m["role"], "pick": m["pick"]})
    (out / "tree.json").write_text(json.dumps({
        "edits": [str(f) for f in args.edits], "excluded": args.exclude,
        "val_fraction": args.val_fraction, "masked_boxes_dropped": masked,
        "frames": dict(counts), "boxes": dict(boxes_n), "rows": record}, indent=1) + "\n")
    print(f"frames {dict(counts)}; boxes {dict(boxes_n)}; boxes in the mask dropped {masked}; "
          f"excluded {len(args.exclude)}; tree {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
