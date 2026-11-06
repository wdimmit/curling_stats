"""Panels with dfw1a's boxes (true colours, left) and overhead-3's (green-to-red copy, right)."""
import sys
import cv2, numpy as np
from ultralytics import YOLO
a, b = YOLO("weights/dfw1a.pt"), YOLO("weights/overhead-3.pt")
ca, cb = cv2.VideoCapture("norm/full.mp4"), cv2.VideoCapture("norm/full-g2r.mp4")
R = {"top": (764, 0, 379, 526), "bottom": (764, 526, 379, 554)}
rows = []
for tok in sys.argv[1:]:
    panel, t = tok.split(":"); x, y, w, h = R[panel]
    pair = []
    for m, cap, name in ((a, ca, "dfw1a"), (b, cb, "o3 g2r")):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(float(t) * 30)); f = cap.read()[1][y:y + h, x:x + w].copy()
        r = m.predict(f, imgsz=448, conf=0.5, verbose=False)[0]
        for c, p, bb in zip(r.boxes.cls.tolist(), r.boxes.conf.tolist(), r.boxes.xyxy.tolist()):
            col = (0, 0, 255) if int(c) == 0 else (0, 255, 255)
            cv2.rectangle(f, (int(bb[0]), int(bb[1])), (int(bb[2]), int(bb[3])), col, 2)
            cv2.putText(f, f"{p:.2f}", (int(bb[0]), int(bb[1]) - 2), 0, 0.4, col, 1)
        cv2.putText(f, f"{name} {t}", (5, 20), 0, 0.6, (255, 0, 255), 2)
        pair.append(cv2.resize(f, (379, 540)))
    rows.append(np.hstack(pair))
cv2.imwrite("stills/vs.jpg", np.hstack(rows) if len(rows) <= 2 else np.vstack([np.hstack(rows[i:i + 2]) for i in range(0, len(rows), 2)]))
