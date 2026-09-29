"""Score side-model weights where it matters for the hack wave. Runs on the worker.

    /data/wdd/curling/.venv/bin/python eval_hack.py ds13b=/path/ds13b.pt ds13c=/path/best.pt

For each model:
- ``hack_heldout``: the 29 held-out frames (3 games never trained on), at the
  pipeline's own cut (0.35). Recall of the labelled stones at IoU >= 0.5, by
  the frame's depth (at rest / hack to -1 m / -1 m to +1.5 m); the same
  ignoring colour, which says whether a miss is a colour mistake; and boxes
  with no labelled stone.
- ``hack_val``: the same on the 20 val frames. Training picked its best epoch
  on these, so they flatter the new model.
- mAP50 on the held-out frames, and on wave2's val frames (the band) -- the
  band must not get worse.
"""
import json
import os
import sys
from pathlib import Path

from ultralytics import YOLO

TREES = Path(os.environ.get("DS13_TREES", "/data/wdd/curling/ds13_trees"))
CUT = 0.35


def boxes_of(path: Path, W: int, H: int):
    out = []
    for ln in path.read_text().split("\n"):
        if ln.strip():
            c, cx, cy, w, h = (float(v) for v in ln.split())
            out.append((int(c), (cx - w / 2) * W, (cy - h / 2) * H, (cx + w / 2) * W, (cy + h / 2) * H))
    return out


def iou(a, b):
    ix = max(0.0, min(a[3], b[3]) - max(a[1], b[1])); iy = max(0.0, min(a[4], b[4]) - max(a[2], b[2]))
    i = ix * iy
    u = (a[3] - a[1]) * (a[4] - a[2]) + (b[3] - b[1]) * (b[4] - b[2]) - i
    return i / u if u > 0 else 0.0


def recall(model, split: str, picks: dict) -> dict:
    by = {}
    for img in sorted((TREES / "hack1/images" / split).glob("*.jpg")):
        res = model.predict(str(img), imgsz=800, conf=CUT, verbose=False)[0]
        H, W = res.orig_shape
        preds = [(int(c), *[float(v) for v in b]) for b, c in
                 zip(res.boxes.xyxy.cpu().numpy(), res.boxes.cls.cpu().numpy())]
        gts = boxes_of(TREES / "hack1/labels" / split / f"{img.stem}.txt", W, H)
        key = picks.get(img.stem, "?")
        d = by.setdefault(key, {"stones": 0, "found": 0, "found_any_colour": 0, "extra_boxes": 0})
        used = set()
        for g in gts:
            d["stones"] += 1
            m = [(iou(g, p), j) for j, p in enumerate(preds) if j not in used and iou(g, p) >= 0.5]
            same = [(v, j) for v, j in m if preds[j][0] == g[0]]
            if same:
                d["found"] += 1; used.add(max(same)[1])
            if m:
                d["found_any_colour"] += 1
        d["extra_boxes"] += len(preds) - len(used)
    tot = {k: sum(v[k] for v in by.values()) for k in ("stones", "found", "found_any_colour", "extra_boxes")}
    return {"all": tot, "by_depth": by}


def main() -> int:
    tree = json.loads((TREES / "hack1/tree.json").read_text())
    picks = {r["stem"]: r["pick"] for r in tree["rows"]}
    out = {}
    for arg in sys.argv[1:]:
        name, path = arg.split("=", 1)
        model = YOLO(path)
        r = {"weights": path,
             "hack_heldout": recall(model, "heldout", picks),
             "hack_val": recall(model, "val", picks)}
        for key, yaml in (("map50_hack_heldout", "hack1_heldout.yaml"), ("map50_band_val", "wave2/data.yaml")):
            if not (TREES / yaml).exists():
                r[key] = None
                continue
            m = model.val(data=str(TREES / yaml), imgsz=800, batch=8, split="val", plots=False, verbose=False)
            r[key] = round(float(m.box.map50), 4)
        out[name] = r
        a = r["hack_heldout"]["all"]
        print(f"{name}: held-out {a['found']}/{a['stones']} at {CUT} "
              f"({a['found_any_colour']} ignoring colour, {a['extra_boxes']} extra boxes); "
              f"mAP50 held-out {r['map50_hack_heldout']}, band val {r['map50_band_val']}", flush=True)
    Path("eval.json").write_text(json.dumps(out, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
