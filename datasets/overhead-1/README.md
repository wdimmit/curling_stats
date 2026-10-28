# overhead-1

The overhead (panel) stone detector's training set, and the model trained on it.

**Naming, from 2026-10-27:** datasets and models are `overhead-N` (the panels
looking straight down) and `long-N` (the long/side camera), numbered from 1.
They replace `dsN`, which named both cameras' sets in one sequence (ds17b was
overhead, ds13e long). A model takes the name of the newest set it trained on;
a retrain of the same set is `overhead-1b`. Older names stay as they were.

**Set:** ds17b's 1,973 training frames (ds11 + ds15 + ds16 + ds17 + ds17b's
arrivals; the laptop's ds17 labels, before ds18r's `stone_unknown` edits) plus
55 frames Will labelled on the members' /label page (exported 2026-10-27 with
`scripts/labels_export.py --member`, `datasets/member-labels/2026-10-27`):
false-positive-harvest panels, all boxes fixed, 343 boxes over 11 videos.
Val is ds11's (364), unchanged from ds17b.

**Folds** (`scripts/member_folds.py`, laptop `/data/wdd/curling/overhead-1/folds`):
`heldout` holds out the member frames of pe328PUupXs, XbBwOnjkhpY and
4RrNWSeNnMU (17); `all` trains on everything and is the candidate.

**Recipe:** ds17b's (ds11a): yolo11n, 640, 100 epochs, patience 25
(`scripts/member1/run_member1.sh`). Scored at the pipeline's 448
(`scripts/member1/eval_member1.py`).
