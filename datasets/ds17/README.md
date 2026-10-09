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
