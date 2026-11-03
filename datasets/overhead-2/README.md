# overhead-2

The overhead (panel) stone detector's training set, and the model trained on it.
Naming: see `datasets/overhead-1/README.md`.

**Why:** overhead-1 lost a little to ds17b on the harness, both ways red: it
boxed red clothing as a red stone (entTaSBFK3A e2, a jacket over the empty
button) and missed red stones on the throwing panel around the release.

**Set:** ds17b's 1,973 training frames (as overhead-1) plus 231 overhead frames
Will labelled on /label (`scripts/labels_export.py --member`,
`datasets/member-labels/2026-10-30`), 809 boxes over 58 videos:
- 139 from batch `red-2026-10-29` (`scripts/red1/`): 71 clothing (an overhead-1
  red box RF-DETR Nano did not make) and 68 release (a red stone at the release
  Nano saw and overhead-1 did not), mined from 165 hosted games of 10/15-10/29,
  harness and held-out videos excluded;
- 92 false-positive-harvest panels (overhead-1's 55 and 37 more).
Val is ds11's (364), unchanged from ds17b.

**Folds** (`scripts/member_folds.py`, laptop `/data/wdd/curling/overhead-2/folds`):
`heldout` holds out the member frames of the videos in `heldout-videos.txt`
(pe328PUupXs, XbBwOnjkhpY, 4RrNWSeNnMU as before, and every fifth red-batch
video): 55 frames, 25 of them red-batch. `all` trains on everything and is the
candidate.

**Recipe:** overhead-1's: yolo11n, 640, 100 epochs, patience 25
(`scripts/member2/run_overhead2.sh`). Scored at the pipeline's 448, with false
and missed boxes at conf 0.30 (`scripts/member2/eval_overhead2.py`).
