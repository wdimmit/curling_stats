#!/usr/bin/env python
"""cin1: does the Cincinnati tune cost Granite anything? Ultralytics val on Granite's held sets.

    python scripts/cin1/bench.py --models ds17b=ds17b.pt cin1a=best.pt \
        --data ds11=folds/all/curling.yaml ds12=ds12/set/curling.yaml --imgsz 448
"""

import argparse
import json


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", required=True, help="name=weights.pt")
    ap.add_argument("--data", nargs="+", required=True, help="name=curling.yaml")
    ap.add_argument("--imgsz", type=int, default=448)
    a = ap.parse_args()
    from ultralytics import YOLO

    out = {}
    for m in a.models:
        name, w = m.split("=", 1)
        for d in a.data:
            dname, yaml = d.split("=", 1)
            r = YOLO(w).val(data=yaml, imgsz=a.imgsz, batch=16, device=0, verbose=False, plots=False)
            p, rc, m50, m5095 = r.box.mp, r.box.mr, r.box.map50, r.box.map
            out[f"{name}/{dname}"] = [round(v, 4) for v in (p, rc, m50, m5095)]
            print(f"{name:8s} {dname:5s} P {p:.4f} R {rc:.4f} mAP50 {m50:.4f} mAP50-95 {m5095:.4f}", flush=True)
    print(json.dumps(out))


if __name__ == "__main__":
    main()
