"""Median plates for the three-panel view and for each house's score view."""
import json, sys
import cv2, numpy as np

path, lay = sys.argv[1], json.load(open(sys.argv[2]))
cap = cv2.VideoCapture(path); fps = cap.get(cv2.CAP_PROP_FPS)
def kind(x):
    if x["bar"] < 70 and x["ss"] > 15: return "S"
    if x["sa"] > 15 and x["sb"] > 15: return "T"
    return "?"
ks = [kind(x) for x in lay]
# keep samples whose neighbours (5 s either side) are the same layout: no dissolves
keep = [(x["t"], k) for i, (x, k) in enumerate(zip(lay, ks))
        if k != "?" and 0 < i < len(ks) - 1 and ks[i - 1] == k == ks[i + 1]]
T = [t for t, k in keep if k == "T"][::4]; S = [t for t, k in keep if k == "S"][::2]
def grab(t):
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps)); ok, f = cap.read(); return f
tf = [grab(t) for t in T]; sf = [grab(t) for t in S]
cv2.imwrite("plates/T.png", np.median(np.stack(tf), axis=0).astype(np.uint8))
# split the score views by their long camera: two clusters
feat = np.stack([cv2.resize(cv2.cvtColor(f[0:950, 0:1272], cv2.COLOR_BGR2GRAY), (64, 48)).astype(np.float32).ravel() for f in sf])
_, lab, _ = cv2.kmeans(feat, 2, None, (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 50, 0.1), 5, cv2.KMEANS_PP_CENTERS)
lab = lab.ravel()
for c in (0, 1):
    fs = [f for f, l in zip(sf, lab) if l == c]
    cv2.imwrite(f"plates/S{c}.png", np.median(np.stack(fs), axis=0).astype(np.uint8))
    print("cluster", c, len(fs), "frames")
json.dump({"T": T, "S": [[t, int(l)] for t, l in zip(S, lab)]}, open("plates/samples.json", "w"))
print(len(T), "three-panel frames")
