"""Green->red hue remap, then the current detectors: a pre-labeller for green stones."""
import sys
import cv2, numpy as np
from ultralytics import YOLO

def green_to_red(img, lo=55, hi=95, smin=60, gain=1.6):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)
    m = (h >= lo) & (h <= hi) & (s >= smin)
    h2 = h.astype(np.int16); h2[m] = (h2[m] - 74) % 180
    v = v.copy(); v[m] = np.clip(v[m].astype(np.float32) * gain, 0, 255).astype(np.uint8)
    return cv2.cvtColor(cv2.merge([h2.astype(np.uint8), s, v]), cv2.COLOR_HSV2BGR), m

weights_dir = sys.argv[1]
oh = YOLO(f"{weights_dir}/overhead-3.pt"); lg = YOLO(f"{weights_dir}/long-1.pt")
crops = {
  "t4500": {"oh_top": (765, 0, 1143, 525), "long_L": (0, 0, 765, 1080), "long_R": (1143, 0, 1920, 1080)},
  "t6000": {"oh_big": (1274, 0, 1920, 1080), "long_big": (0, 0, 1272, 950)},
  "t1500": {"oh_big": (1274, 0, 1920, 1080), "long_big": (0, 0, 1272, 950)},
  "t3000": {"oh_big": (1274, 0, 1920, 1080), "long_big": (0, 0, 1272, 950)},
  "t600":  {"oh_big": (1274, 0, 1920, 1080), "long_big": (0, 0, 1272, 950)},
}
for still, regions in crops.items():
    img = cv2.imread(f"stills/{still}.jpg")
    sw, mask = green_to_red(img)
    cv2.imwrite(f"stills/{still}_swap.jpg", sw)
    for name, (x0, y0, x1, y1) in regions.items():
        m = lg if name.startswith("long") else oh
        r = m.predict(sw[y0:y1, x0:x1], imgsz=640, conf=0.25, verbose=False)[0]
        dets = [(m.names[int(c)][0], round(float(p), 2), int((b[0]+b[2])/2)+x0, int((b[1]+b[3])/2)+y0)
                for c, p, b in zip(r.boxes.cls, r.boxes.conf, r.boxes.xyxy.tolist())]
        n_r = sum(d[0] == "r" for d in dets); n_y = sum(d[0] == "y" for d in dets)
        print(still, name, f"red(green)={n_r} yellow={n_y}", sorted(dets, key=lambda d: d[3]))
