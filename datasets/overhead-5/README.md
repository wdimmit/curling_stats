# overhead-5

The receiving panel's detector (`weights.RECEIVING_NAME`): overhead-3's stones
plus a player class (class 2) for the cover check, `detect/cover.py`.
overhead-3 keeps the throwing panel. The second player-class model:
overhead-4 (`datasets/overhead-4`) was never shipped. Spec:
`docs/superpowers/specs/2026-11-01-overhead-player-class-design.md`. Naming:
see `datasets/overhead-1/README.md`.

**Why:** on the house-read harness overhead-4 made 13 impossible additions
against a bar of 10. Three were red shoes it read as red stones, and two were
rest times moved by a player over a stone.

**Set** (laptop `/data/wdd/curling/overhead-4r2/B`, built 2026-11-04 by
`scripts/overhead4_merge.py --base overhead-3/folds/all --players
member-labels/2026-11-04/players --sides overhead-4/sides-B.json --auto
overhead-4/auto-pilot-1102c`; trained as `overhead-4r2-B`):
- Train, 2,791 frames:
  - overhead-4's 1,893 (`datasets/overhead-4`).
  - 244 hard-2 frames. A Ryzen miner ran overhead-3 and overhead-4 over 66
    videos and kept 2,304 frames where they disagreed. 251 were picked from
    65 videos (`hard-2-picks.jsonl`): 100 overhead-4 stones inside its own
    player box, 38 other overhead-4 extras, 75 overhead-3 stones overhead-4
    missed at least 3.5 m up the panel, 38 other misses. Will boxed them on
    /label with stone fixes on (83 fixed). Left out: 5 marked can't tell, 2
    skipped.
  - 654 of overhead-3's throwing-panel frames, `keep` in `sides.json`
    (overhead-4's `sides.json` with `by` "+variantB"), players from
    pilot-1102c at conf 0.45 like overhead-4's other auto frames. Variant A
    without them lost a stone on the harness (-f3R1D3bQ0E game 0 end 1 rock 16).
- Val, the same 281 frames as overhead-4 (559 players).

**Recipe:** overhead-4's (`scripts/logo1/run_overhead4.sh DATA_YAML NAME`):
yolo11n, 640, 100 epochs, patience 25. 29 min on the laptop's 3070; best
epoch 96.

**On val** (mAP50-95 at 448; overhead-3 on stones only):

| | overhead-3 | overhead-4 | overhead-5 |
|---|---|---|---|
| red | 0.895 | 0.893 | 0.897 |
| yellow | 0.913 | 0.898 | 0.902 |
| player | | 0.765 | 0.775 |

At 640 overhead-5's yellow is 0.944, overhead-3's.

**House-read harness** (16 games, receiving panel only, onset rule;
`houseread-1027` on the laptop):

| | overhead-3 | overhead-4 | overhead-5 |
|---|---|---|---|
| board scores right (54 ends) | 42 | 43 | 43 |
| impossible additions (bar 10) | 15 | 13 | 9 |
| stones lost | 0 | 0 | 0 |
| put back or seen (16 expected) | | 37 | 24 |
| false holds | | 0 | 0 |

The seven holds new with overhead-5 were each checked on video: a real stone
under a player. One impossible addition is still a red shoe (VXU9 game 1 end
4 rock 5); one is new, hOKZ game 0 end 7 rock 9, a rest time moved 40 s by a
player crouched over the stone.

**One whole game on the Ryzen** (1MK77SP66GE, 10/25 sheet 1, 112 rocks): no
release, hog time, colour or score changes. Splits 106 against overhead-3's
107. Of the two overhead-4 lost (e5 rock 1, e6 rock 5), overhead-5 times both.
It loses end 5 rock 11, a stone crossing the far hog line at the top edge of
the panel under a sweeper: its first sightings jitter and the reach-back is
refused. overhead-3's split there came from a stray first point 0.4 m to the
side, not a sighting.
