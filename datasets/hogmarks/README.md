# Hog-line crossings, marked by hand

Ground truth for where the painted hog line falls in an overhead panel's own
coordinates, and for anything that times a crossing.

Each mark is one video frame: the moment a stone's **leading edge** touched the
**near edge** of the hog line's paint, judged by a person watching the
composite's side view, which sees that line square on from about 22 m. The
tool is `scripts/mark_hog.py`; it deliberately does not draw the line on the
frames, so a mark reads the paint rather than a machine's guess.

Why these exist: the overhead panels' along-sheet metre scale collapses away
from the house, so the hog line reports as +4.44 rather than 6.401 and nothing
measured up there in metres can be believed. A crossing does not need metres --
it needs the paint's position in the panel's own numbers, and only a person
looking at the paint can supply that without circularity.

`VXU9xwmugRg.json` -- sheet 2, ends 5 and 6, 27 marks across both panels. Each
carries the release track the overhead panel reported, so the constant can be
re-derived from the file alone without touching the video.

Adding a sheet: run `scripts/mark_hog.py` over one end thrown from each panel
and save the result here under the video id. `HOG_APPARENT_Y_M` in
`game/split.py` was measured on sheet 2 only.
