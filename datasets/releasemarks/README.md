# Releases, marked by hand

Ground truth for where a thrower lets go of the handle, for scoring anything
that reads the release from video (so far: the COCO pose model on the long
camera facing the thrower, `~/curling-work/body-spike`).

Each mark is one video frame: the **first frame the throwing hand is clearly
off the handle**, the last let-go if the thrower re-grips. It was judged by a
person stepping through the camera at the far end, facing the thrower, cropped
to follow the rock at one metric scale with the handle in the middle, 30 fps,
from the tee to about a second past the end of the delivery path (hog line
+1.5 m, then carried on at the rock's last speed). The tool is
`scripts/mark_release.py`; it draws nothing a model saw, and does not show the
distance.

Per rock: `o` is the marked frame's time from the shot's `t_release_s`; `yp`
is the rock's centre at that frame, metres past the throwing tee along the
timeline's `line.delivery` (`yp_prev` is the frame before, so the mark sits in
[`yp_prev`, `yp`]). `mark` is `off`, `held` (still on at the last frame) or
null (can't tell). `chosen` says how the rock got into the set: `random` (two
per colour and position, seeded) or `hard` (picked because a pose read looked
wrong) -- score the two apart; only `random` is a fair sample.

Along the sheet one image row is 6-13 cm past the tee on these cameras, so a
`yp` is good to a few tenths of a metre, not to the centimetre.

`s_1TH0dJNrbHeKLARuj.json` -- 10/20 Tuesday Super League sheet 2, game 2 of
stream dmljQzQr1xo (Grause red, Dimmit yellow), 20 rocks, marked 2026-10-21.

Five more games, 20 rocks each (two per colour and position, then four more,
all `random`, seed 20261022), marked 2026-10-21 in one session (`tag` names
the game there), for training and scoring a hand-on/hand-off model one game
held out at a time:

| file | tag | game |
|---|---|---|
| `s_0ezqH3CRyUL9sxYwf.json` | S1w | 10/14 Wednesday Womens, sheet 1 |
| `s_0cYtR4gDsEm5hr1jh.json` | S2w | 10/14 Wednesday Womens, sheet 2 |
| `s_1Obm7nDkFUQOhsbsy.json` | S3k | Knock the Dust Off draw 12 (midday), sheet 3 |
| `s_0kjMTVPJbVUFPmwt7.json` | S4m | 10/15 Thursday Mens, sheet 4 |
| `s_04zvg6W7FkgdYOPMt.json` | S5f | 10/16 Friday Evening, sheet 5 |

On S1w the crop drifts up to 0.3 m off the rock late in the delivery; the hand
stays in frame.
