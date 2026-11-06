"""Run overhead-3 and long-1 on Dallas stills and print what they see."""
import sys
import cv2
from ultralytics import YOLO

weights_dir = sys.argv[1]
oh = YOLO(f"{weights_dir}/overhead-3.pt"); lg = YOLO(f"{weights_dir}/long-1.pt")
print("overhead names", oh.names, "long names", lg.names)
crops = {
  # three-panel: centre strip panels and long cameras
  "t4500": {"oh_top": (765, 0, 1143, 525), "oh_bot": (765, 528, 1143, 1080),
            "long_L": (0, 0, 765, 1080), "long_R": (1143, 0, 1920, 1080)},
  "t60":   {"oh_top": (765, 0, 1143, 525), "oh_bot": (765, 528, 1143, 1080)},
  # score view: one big overhead and one long camera
  "t6000": {"oh_big": (1274, 0, 1920, 1080), "long_big": (0, 0, 1272, 950)},
  "t1500": {"oh_big": (1274, 0, 1920, 1080), "long_big": (0, 0, 1272, 950)},
  "t3000": {"oh_big": (1274, 0, 1920, 1080), "long_big": (0, 0, 1272, 950)},
}
for still, regions in crops.items():
    img = cv2.imread(f"stills/{still}.jpg")
    for name, (x0, y0, x1, y1) in regions.items():
        m = lg if name.startswith("long") else oh
        r = m.predict(img[y0:y1, x0:x1], imgsz=640, conf=0.15, verbose=False)[0]
        dets = sorted(((m.names[int(c)], round(float(p), 2)) for c, p in zip(r.boxes.cls, r.boxes.conf)), key=lambda d: -d[1])
        print(still, name, len(dets), dets[:14])
