"""overhead-2 vs ds17b and overhead-1 at the pipeline's 448, on the member frames
held out of overhead-2-heldout (split: red batch / the rest) and ds11 val.
Besides mAP: at conf 0.30 (what the pipeline keeps), false boxes (no label of
that colour at IoU >= 0.5) and missed labels, red and yellow apart."""
import json
from pathlib import Path
from ultralytics import YOLO

W = Path("/data/wdd/curling")
R = W / "runs"
F = W / "overhead-2/folds/heldout"
member = {}
for line in (W / "member-labels/2026-10-30/boxes/member/overhead/member.jsonl").read_text().splitlines():
    r = json.loads(line)
    member[r["id"]] = r["origin"].get("check") or ""


def subset(name, keep):
    d = W / "overhead-2/eval" / name
    for sub in ("images/val", "labels/val"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    for img in sorted((F / "images/heldout").glob("*.jpg")):
        if not keep(member.get(img.stem, "")):
            continue
        for src, dst in ((img, d / "images/val" / img.name),
                         (F / "labels/heldout" / f"{img.stem}.txt", d / "labels/val" / f"{img.stem}.txt")):
            if not dst.exists():
                dst.symlink_to(src.resolve())
    y = d / "data.yaml"
    y.write_text(f"path: {d}\ntrain: images/val\nval: images/val\nnames:\n  0: red_stone\n  1: yellow_stone\n")
    return y


sets = {"red-heldout": subset("red", lambda c: c.startswith("red.")),
        "clothing-heldout": subset("clothing", lambda c: c == "red.clothing"),
        "release-heldout": subset("release", lambda c: c == "red.release"),
        "other-heldout": subset("other", lambda c: not c.startswith("red.")),
        "ds11-val": F / "data.yaml"}
models = {"ds17b": W / "ds17b/weights/ds17b-11n.pt", "overhead-1": R / "overhead-1/weights/best.pt",
          "overhead-2-heldout": R / "overhead-2-heldout/weights/best.pt", "overhead-2": R / "overhead-2/weights/best.pt"}


def iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    i = ix * iy
    return i / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i + 1e-9)


def counts(m, yaml):
    d = Path(yaml).parent if "eval" in str(yaml) else None
    if d is None:
        return {}
    out = {"false_red": 0, "false_yellow": 0, "missed_red": 0, "missed_yellow": 0, "frames": 0}
    for img in sorted((d / "images/val").glob("*.jpg")):
        res = m.predict(str(img), imgsz=448, conf=0.30, device=0, verbose=False)[0]
        h, w = res.orig_shape
        gt = []
        for line in (d / "labels/val" / f"{img.stem}.txt").read_text().split("\n"):
            if line.strip():
                c, x, y, bw, bh = map(float, line.split())
                gt.append((int(c), [(x - bw / 2) * w, (y - bh / 2) * h, (x + bw / 2) * w, (y + bh / 2) * h]))
        pred = [(int(c), b.tolist()) for c, b in zip(res.boxes.cls.cpu().numpy(), res.boxes.xyxy.cpu().numpy())]
        used = set()
        for c, b in pred:
            hit = next((i for i, (gc, gb) in enumerate(gt) if i not in used and gc == c and iou(b, gb) >= 0.5), None)
            if hit is None:
                out["false_" + ("red", "yellow")[c]] += 1
            else:
                used.add(hit)
        for i, (gc, _) in enumerate(gt):
            if i not in used:
                out["missed_" + ("red", "yellow")[gc]] += 1
        out["frames"] += 1
    return out


out = {}
for mname, w in models.items():
    if not w.exists():
        print(mname, "missing", w, flush=True)
        continue
    m = YOLO(str(w))
    for sname, y in sets.items():
        r = m.val(data=str(y), imgsz=448, batch=8, device=0, verbose=False, plots=False,
                  project="/tmp/overhead2val", name=f"{mname}-{sname}", exist_ok=True)
        row = {"P": r.box.mp, "R": r.box.mr, "mAP50": r.box.map50, "mAP50-95": r.box.map}
        row.update(counts(m, y))
        out[f"{mname}|{sname}"] = row
        print(mname, sname, {k: (round(v, 3) if isinstance(v, float) else v) for k, v in row.items()}, flush=True)
json.dump(out, open(W / "overhead2_eval.json", "w"), indent=1)
