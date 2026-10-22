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
