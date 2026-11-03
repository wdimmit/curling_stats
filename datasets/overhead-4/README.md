# overhead-4

The overhead (panel) detector for each end's receiving panel: overhead-3's
stones plus a player class (class 2), for the cover check. overhead-3 keeps the
throwing panel. Spec: `docs/superpowers/specs/2026-11-01-overhead-player-class-design.md`.
Naming: see `datasets/overhead-1/README.md`.

**Set** (laptop `/data/wdd/curling/overhead-4/folds/all`, built 2026-11-02 by
`scripts/overhead4_merge.py --base overhead-3/folds/all --players
member-labels/2026-11-02d/players --sides overhead-4/sides.json --auto
overhead-4/auto-pilot-1102c`):
- Train, 1,893 frames, 4,350 player boxes:
  - 1,595 of overhead-3's 2,249 training frames (the receiving panel by
    `sides.json`; 45 of them logo crops). Will reviewed 370 on /label; the
    other 1,225 take pilot-1102c's player boxes at conf 0.45 (`--auto`, the
    user's call: on the pilot's 61 held-out frames it found 118 of 122
    players with 10 extra boxes, mostly slivers at the frame edge).
  - 298 of the 300 hard frames (`scripts/player_mine.py`, batch hard-1), all
    reviewed; their stones are Will's fix (40) or as shown (258). Left out,
    skipped on /label: WhuUBNHOD2A_t_005686_10_lost_no_pose,
    lJREEPDFNbE_b_005988_23_edge.
- Val, 281 frames, 559 players: ds11 val's receiving-panel frames, all
  reviewed.
- 131 frames take a member's stone fix in place of their stone label.

The auto boxes come from `runs/pilot-1102c` (trained on 653 reviewed frames;
`/data/wdd/curling/prebox-1102/infer_auto.py`). The same boxes were the
pre-boxes of the items still open on /label (`prebox-1102/backup.jsonl` keeps
the pose ones they replaced); a member's later label wins over them.

No held-out fold: overhead-2's held-out videos leave 11 receiving-panel
frames, too few to measure. Val is the comparison.

**Recipe:** overhead-3's, three classes (`scripts/logo1/run_overhead4.sh
DATA_YAML overhead-4`): yolo11n, 640, 100 epochs, patience 25. 26 min on the
laptop's 3070; best epoch 78.

**On val** (same 281 frames, stones against overhead-3, mAP50-95):

| imgsz | | overhead-3 | overhead-4 |
|---|---|---|---|
| 448 | red | 0.895 | 0.893 |
| 448 | yellow | 0.913 | 0.898 |
| 640 | red | 0.933 | 0.929 |
| 640 | yellow | 0.944 | 0.937 |

mAP50 0.993-0.995 for both, recall equal or better. Player, conf 0.25:
precision 0.863, recall 0.987 (yolo11x-pose at 0.1: 0.915 / 0.735).
