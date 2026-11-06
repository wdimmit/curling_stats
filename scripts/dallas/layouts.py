"""Classify each sampled frame of the Dallas stream by its broadcast layout."""
import json, sys
import cv2, numpy as np

path, out = sys.argv[1], sys.argv[2]
step = float(sys.argv[3]) if len(sys.argv) > 3 else 5.0
cap = cv2.VideoCapture(path)
fps = cap.get(cv2.CAP_PROP_FPS); n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
rows = []
t = 0.0
while t * fps < n:
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
    ok, f = cap.read()
    if not ok:
        break
    g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float32)
    bar = float(g[965:1070, 760:1260].mean())          # score bar in the score view
    # vertical seams of the three-panel view: column-difference energy
    dx = np.abs(np.diff(g[150:1000], axis=1)).mean(axis=0)
    seam_a = float(dx[760:770].max()); seam_b = float(dx[1138:1148].max())
    seam_s = float(dx[1266:1278].max())                # score view's split
    rows.append(dict(t=round(t, 1), bar=round(bar, 1), sa=round(seam_a, 1),
                     sb=round(seam_b, 1), ss=round(seam_s, 1), mean=round(float(g.mean()), 1)))
    t += step
json.dump(rows, open(out, "w"))
print(len(rows), "frames")
