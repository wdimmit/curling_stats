"""Panel tiles near the end of each end, with overhead-3's boxes from the g2r video."""
import sys
import cv2, numpy as np
from ultralytics import YOLO
m = YOLO("weights/overhead-3.pt")
cap = cv2.VideoCapture("norm/full-g2r.mp4")
R = {"top": (764, 0, 379, 526), "bottom": (764, 526, 379, 554)}
tiles = []
for tok in sys.argv[1:]:
    n, house, t = tok.split(":")
    cap.set(cv2.CAP_PROP_POS_MSEC, float(t) * 1000); ok, f = cap.read()
    x, y, w, h = R[house]
    tile = f[y:y + h, x:x + w].copy()
    r = m.predict(tile, imgsz=448, conf=0.3, verbose=False)[0]
    for c, p, b in zip(r.boxes.cls.tolist(), r.boxes.conf.tolist(), r.boxes.xyxy.tolist()):
        col = (0, 0, 255) if int(c) == 0 else (0, 255, 255)
        cv2.rectangle(tile, (int(b[0]), int(b[1])), (int(b[2]), int(b[3])), col, 2)
        cv2.putText(tile, f"{p:.2f}", (int(b[0]), int(b[1]) - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.4, col, 1)
    tile = cv2.resize(tile, (379, 540))
    cv2.putText(tile, f"e{n} t={t}", (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 0, 255), 2)
    tiles.append(tile)
rows = [np.hstack(tiles[i:i + 4]) for i in range(0, len(tiles), 4)]
cv2.imwrite("stills/finals.jpg", np.vstack(rows))
