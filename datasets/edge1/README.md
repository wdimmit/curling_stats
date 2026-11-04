# edge1

Empty-sheet negatives for the next overhead stone model: 95 panel crops with
empty labels, each a keyframe where overhead-3 boxed a "stone" that is not one.
Naming: see `datasets/overhead-1/README.md`; the set goes into the next
overhead set as `logo1` went into overhead-3.

**Why:** 11/03 Tuesday Super League sheet 4 (T2jGXKs2s9Q) was charted as one
thirteen-end game where the 7 pm and 9 pm games were played. Four minutes into
the changeover, with both houses empty, a tan broom head held into the top
panel's corner behind the back line read as a yellow stone at 7920 and 7925 s
(conf 0.61, 0.44), and the activity profile ran the 7 pm game's last end on
to it. `geometry/backpaint.py` and `segment._drop_strays` stop it holding a
game open; this set is for the model not to see it at all.

**Set** (`sheet4-1103-frames.txt` + `mined-frames.txt`, stems; images and
empty labels at Ryzen `/data/wdd/datasets/edge1` and laptop
`/data/wdd/curling/edge1`, `SHA256SUMS` beside them):
- 4 top-panel crops of the 11/03 changeover itself (7740, 7760, 7920, 7925 s),
  both houses checked empty by eye in the frames either side.
- 91 more from `scripts/edge1/edge_mine.py`, 2026-11-04: every keyframe away
  from play -- more than 30 s from any end's first entry or last rest, by the
  rocks -- of the 90 kept recordings of 10/14-11/03 that have charts, both
  panels, counted as the live profile counts them (overhead-3, imgsz 448, conf
  0.30, production calibration and sideline filter). 33,390 hits in 8,465
  keyframes; the surest 6 a video cut as crops (385), those centred past the
  back line first.
  - 173 set aside: the keyframe also had two or more stones at conf >= 0.8
    (stones being gathered after a game, practice throws).
  - Of the other 212, the 196 whose panel had no other detection were checked
    by eye at 3x around the hit (`scripts/edge1/zoom.py`): 93 are not stones,
    91 of them kept here (2 are the 11/03 crops above). The rest are real
    stones -- many parked out of play behind the back line -- or too clipped
    by the frame edge to call, and are left out: a missed real stone would be
    labelled empty.

What overhead-3 took for stones, all on an empty sheet: tan broom heads and
stone pushers (about half; 9 at the back edge of a panel like 11/03's), red
shoes and red broom pads, heads, gloves and jackets. People are in many crops;
for a model with a player class (overhead-4's) they need player boxes first,
as `scripts/overhead4_merge.py --auto` gives.
