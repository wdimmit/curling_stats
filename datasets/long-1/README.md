# long-1

The long (side) camera stone detector's training set, and the model trained on
it. Naming: see `datasets/overhead-1/README.md`.

**Set:** ds13e's trees with hack1r in place of hack1 (the 10/13 verdict: hack1's
labels break the stone-alone rule) -- wave2 430, hack1r 76, broom1 80, kink1
102 -- plus 92 frames Will labelled on the members' /label page
(`datasets/member-labels/2026-10-27`): 720x360 whole-frame windows on a doubted
box, all boxes fixed, 481 boxes over 11 videos. Val is the four trees' (109).

**Folds** (`scripts/member_folds.py`, laptop `/data/wdd/curling/long-1/folds`):
`heldout` holds out the member frames of pe328PUupXs, XbBwOnjkhpY and
4RrNWSeNnMU (27); `all` is the candidate.

**Recipe:** ds13e's: yolo11s, 800, 300 epochs, patience 60, batch 8
(`scripts/member1/run_member1.sh`).
