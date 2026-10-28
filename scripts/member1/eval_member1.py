"""Live model vs new, at the pipeline's imgsz: overhead 448, long 800."""
import json
from ultralytics import YOLO
R = "/data/wdd/curling/runs"
O, L = "/data/wdd/curling/overhead-1/folds/heldout", "/data/wdd/curling/long-1/folds/heldout"
T = "/data/wdd/curling/long-1/trees"
jobs = [
    ("overhead", 448, {"ds17b": "/data/wdd/curling/ds17b/weights/ds17b-11n.pt",
                       "overhead-1-heldout": f"{R}/overhead-1-heldout/weights/best.pt",
                       "overhead-1": f"{R}/overhead-1/weights/best.pt"},
     {"member-heldout": f"{O}/heldout.yaml", "ds11-val": f"{O}/data.yaml"}),
    ("long", 800, {"ds13e": "/data/wdd/curling/long-1/ds13e.pt",
                   "long-1-heldout": f"{R}/long-1-heldout/weights/best.pt",
                   "long-1": f"{R}/long-1/weights/best.pt"},
     {"member-heldout": f"{L}/heldout.yaml", "trees-val": f"{L}/data.yaml"}),
]
out = {}
for cam, imgsz, models, sets in jobs:
    for mname, w in models.items():
        try:
            m = YOLO(w)
        except Exception as e:
            print(cam, mname, "missing", e); continue
        for sname, y in sets.items():
            r = m.val(data=y, imgsz=imgsz, batch=8, device=0, verbose=False, plots=False,
                      project="/tmp/member1val", name=f"{mname}-{sname}", exist_ok=True)
            row = {"P": r.box.mp, "R": r.box.mr, "mAP50": r.box.map50, "mAP50-95": r.box.map}
            out[f"{cam}|{mname}|{sname}"] = row
            print(cam, mname, sname, {k: round(v, 3) for k, v in row.items()}, flush=True)
json.dump(out, open("/data/wdd/curling/member1_eval.json", "w"), indent=1)
