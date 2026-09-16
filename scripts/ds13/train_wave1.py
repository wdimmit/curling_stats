"""Train the first side-view stone detector on the hand-labelled wave.

Deliberately modest: 190 training frames is a small set, so this starts from
COCO-pretrained weights rather than from the project's own ``ds11a.pt``. That
model was trained on the overhead panels, which look at a stone from directly
above; the side view sees a disc edge-on against ice at 30 metres. Its learned
features are less transferable here than generic object features are.

The split is by VIDEO, not by frame. Two frames from one delivery are near
duplicates, so a random split would put a stone in train and the same stone
0.2 s later in val and call the agreement generalisation. Even so, the dates
behind these nine videos are unknown -- see datasets/ds13/videos_cached.json --
so this is indicative, not a clean validation, and no headline number comes
from it.
"""
from ultralytics import YOLO

model = YOLO("yolo11s.pt")
model.train(
    data="/home/tcuser/curling-work/ds13/tree/data.yaml",
    project="/home/tcuser/curling-work/ds13/runs",
    name="wave1",
    exist_ok=True,
    # The frames are ~810x195. 800 keeps a stone near its native ~52 px rather
    # than shrinking it to 41 at the usual 640.
    imgsz=800,
    epochs=300,
    patience=60,
    batch=8,
    seed=0,
    # Small set: lean on augmentation, and keep mosaic on for most of it.
    mosaic=1.0,
    close_mosaic=30,
    scale=0.4,
    degrees=0.0,        # the camera never rolls; rotating teaches a lie
    fliplr=0.5,         # left and right views are mirror-ish, so this is real
    flipud=0.0,         # ice is always below and the house always above
    hsv_h=0.015,
    hsv_s=0.6,
    hsv_v=0.4,
    verbose=True,
)
