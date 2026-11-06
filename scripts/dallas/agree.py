"""dfw1a on true colours vs overhead-3 on the green-to-red copy, frame by frame."""
import json, sys
import cv2
from collections import Counter
from ultralytics import YOLO

kinds = []
for i in range(4):
    kinds += json.load(open(f"norm/c{i}.json"))["kinds"]
R = {"top": (764, 0, 379, 526), "bottom": (764, 526, 379, 554)}
a, b = YOLO(sys.argv[1]), YOLO("weights/overhead-3.pt")
ca, cb = cv2.VideoCapture("norm/full.mp4"), cv2.VideoCapture("norm/full-g2r.mp4")
stats = Counter(); diffs = []
for t in range(151, 6120, 10):
    i = t * 30
    if len(set(kinds[i - 30:i + 31])) != 1:
        continue
    k = kinds[i]
    for cap in (ca, cb):
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
    fa, fb = ca.read()[1], cb.read()[1]
    for panel, (x, y, w, h) in R.items():
        if (k == "S0" and panel == "bottom") or (k == "S1" and panel == "top") or k == "?":
            continue
        ra = a.predict(fa[y:y + h, x:x + w], imgsz=448, conf=0.5, verbose=False)[0]
        rb = b.predict(fb[y:y + h, x:x + w], imgsz=448, conf=0.5, verbose=False)[0]
        na = Counter(int(c) for c in ra.boxes.cls.tolist()); nb = Counter(int(c) for c in rb.boxes.cls.tolist())
        stats["panels"] += 1
        stats["same"] += (na == nb)
        stats["green_a"] += na[0]; stats["green_b"] += nb[0]; stats["yel_a"] += na[1]; stats["yel_b"] += nb[1]
        if na != nb:
            diffs.append((t, panel, k, dict(na), dict(nb)))
print(dict(stats))
for d in diffs[:25]:
    print(d)
