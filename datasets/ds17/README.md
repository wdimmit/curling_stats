# ds17 — the sliding thrower's head is not a stone

180 throwing-panel frames from 32 cached videos (both seasons, fours and doubles), each a
moment in a delivery where ds16a drew a box on the thrower sliding behind or over the stone.

- **What ds16a does.** A round head, hat, toque, hood or hair seen from straight above,
  right behind the stone and moving with it, reads as a stone at up to 0.98. On
  _OGoqF5W9-I yellow's third wears a red toque: it read red for over a second per slide
  and, at 10 fps, outvoted three of their real yellow releases.
- **How common.** `scripts/ds17/mine_hats.py` read the throwing panel through every timed
  throw of the 50 cached videos with hosted charts (6,593 throws): 1,089 carry such a box
  and 304 a run of them moving up the sheet. 34 throwers have three or more.
- **Selection** (`scripts/ds17/build_hats.py`): shadow throws first, then the most
  confident boxes; at most 2 frames a throw, 6 a thrower, 10 a video.

| file | what it is |
|---|---|
| `manifest.json` | every frame: video, source, panel, time, the throw, the mined boxes, pre-label count |
| `edits/*.json` | the hand review |

Pre-labelled by ds16a at conf 0.05, imgsz 448, as ds15 and ds16 were. The images rebuild
from the cached videos with the builder (on the laptop: `/data/wdd/scratch/ds17/ds17set`).

## Round 2 — the arriving stone under a person

Trained on round 1, the model (`ds17-all`) stopped boxing throwers but lost some real stones:
on the 11-video harness, 11 arrivals ds16a saw (5 then read as hogged), mostly stones with a
sweeper or skip over them in the far house. `scripts/ds17/mine_arrivals.py` ran both models
along every served arrival track (6,510 arrivals): ds16a saw the stone in 90.6% of those
frames, the round-1 model in 90.2%; 551 arrivals have the new model missing it 3+ frames
running. `scripts/ds17/build_arrivals.py` cut 131 frames from 34 videos -- the longest runs of
misses first, plus every harness arrival lost (`must-arrivals.json`; the five no served track
covers are cut directly, `extra-arrivals.json`) -- pre-labelled by ds16a.

| file | what it is |
|---|---|
| `manifest-arrivals.json` | round 2's frames |
| `edits/label-edits-ds17b-*.json` | its review (scope `ds17b:train`) |

## Training and the model shipped

`scripts/ds17/make_folds.py --ds17b` adds both rounds to ds16a's set (fold `all`, 1,973
frames; ds11 val for validation). `scripts/ds17/train.sh` trains with ds11a's recipe in the
worker image (`queue.sh` ran the three below on the Ryzen's 3080 in ~25 min each).
`scripts/ds17/fullgames.py` reads every timed throw (10 fps) and arrival (5 fps) of the 76
hosted games with every model in one decode; `fg_summary.py` tallies it.

| model | thrower runs | thrower boxes ≥0.5 | arrival frames seen | arrivals with 2+ frames lost vs ds16a | ds12 mAP50 / 50-95 | live end |
|---|---|---|---|---|---|---|
| ds16a | 304 | 2,223 | 89.94% | — | .9874 / .9386 | 138 s |
| round 1, YOLO11n | 114 | 856 | 89.59% | 618 | — | — |
| **ds17b, YOLO11n** | **76** | **653** | **91.74%** | **234** | .9878 / .9343 | 132 s |
| ds17b, YOLO26n | 92 | 686 | 90.99% | 481 | .9787 / .9315 | 142 s |
| ds17b, YOLO26s | 95 | 893 | 92.22% | 350 | .9884 / .9436 | 146 s |
| ds17b, YOLO26s, 300 epochs (stopped at 242) | 86 | 1,024 | 92.09% | 346 | .9844 / .9471 | — |

The stone on a throw is found 98.3-98.4% of the time by all of them. `weights/ds17b.pt` is
the YOLO11n run: fewest thrower boxes, fewest lost arrivals, ds16a's architecture and
cost. The 26s runs draw tighter boxes and see a few more arrival frames, but box more
throwers.
