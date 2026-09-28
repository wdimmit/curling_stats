# ds16 — the 2026-27 season's houses, and the thrower in the yellow toque

124 overhead-panel frames from ten 2026-27 videos the worker has cached, sheets 1-4
(no sheet 5 video of this season is cached).

- **Sheet 4's portrait button.** Both of sheet 4's buttons were repainted with a
  portrait over the summer, and ds15a reads it as a yellow stone at 0.92-0.94 in 97%
  of frames. Every sheet 4 chart of 2026-09-27 (bLkgfZaDSKw, 8un0-JBKkLA, both
  Y_XxbP1-G_o games) then holds a phantom yellow on the button in almost every house,
  and the detected score gives yellow nearly every end. Last season's sheet 4 games and
  every other sheet show none.
- **The toque.** On bLkgfZaDSKw red's third wears a yellow toque, which from above is a
  round yellow blob sliding up the centre line behind the red stone; ds15a reads 10 of
  their 12 releases yellow.

| kind | frames | what it is |
|---|---|---|
| settled | 50 | a settled house 2 s after a rock stops, spread through each game |
| settled-near-button | 15 | sheet 4 only: a real stone resting 0.06-0.35 m from the tee, over or beside the portrait |
| throwing-house | 35 | the throwing house 8 s after a release, bare but for players |
| toque | 24 | red's third's twelve slides on bLkgfZaDSKw, 0.3 and 1.1 s past the long camera's reference crossing |

Sheet 4 holds 74 of the frames (24 of them the toque). 8un0-JBKkLA's 17 are the
held-out game.

| file | what it is |
|---|---|
| `manifest.json` | every frame, its video, time, panel, kind and pre-label count (built by `scripts/ds16/build_season.py`) |
| `edits/*.json` | the hand review |

Pre-labelled by ds15a at conf 0.05, imgsz 448, exactly as ds15 was. The images rebuild
from the cached videos with the builder (on the worker: `/data/wdd/curling/ds16`).
