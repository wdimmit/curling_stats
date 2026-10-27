# cin1 — Cincinnati's houses

100 overhead-panel frames from Cincinnati Curling Club's stream `rKiMmtVod0I` (Comp League playoffs,
2026-04-20, 720p30), the first set from a club other than Granite.

Cincinnati paints its red house's 12-to-8 band with a sponsor's white "Cincy Light" lettering, and
ds17b reads the red between the letters as red stones: 3-4 boxes at 0.3+ in every frame, up to 0.9,
that never leave. The house never empties, so its ends run together (the first `--club cincinnati`
run found 3 ends in the whole game).

- `scripts/cin1/mine.py`: ds17b at 0.05 (imgsz 448) on both houses every 2 s of the stream, 6,491
  frames a house.
- `scripts/cin1/build.py`: 100 frames, each kind spread over the stream (equal-count runs, the
  strongest frame of each):
  - `red-empty` 20: the red house with boxes only on the band, the most phantom confidence first;
  - `red-play` 50: the red house in play, busy houses and confident band reds first;
  - `blue-play` 30: the blue house in play, so the tune keeps it.
- Pre-labels: ds17b at 0.25 rather than 0.05, which would put ~14 lettering boxes on every red-house
  frame. Real stones score 0.8+ here; a missed one is for the reviewer to add.

Set on the GPU laptop at `/data/wdd/scratch/cincinnati/cin1/set1`. Review served from
`http://100.80.66.81:8789/` (scope `cin1:train`), rendered with the ds19 branch's review page.
