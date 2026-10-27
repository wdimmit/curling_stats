# cin3 — Cincinnati's end cameras, throws arriving

100 frames from `rKiMmtVod0I`. Each comes from the end camera above the destination's hog line, looking back at its
house, which watches the stone come in and stop. cin2a was trained on cin2, which covered only the throwing end, and
it loses the stone here:
- under the sweepers' bodies and brooms right below the camera;
- in a house with the skips standing in it.

In the run with the far read (`out5`), 41 of 119 placed throws got no far hog crossing, and the last rocks of
ends 1 and 6 were never followed to rest.

`scripts/cin3/build.py` anchors each throw's arrival on its far crossing. Where there is none, it uses 3 s before
the overhead saw the stone enter the house, or else 12.5 s after its near crossing. It takes one frame per throw:
- `coming`: 1.0 s past the anchor;
- `house`: 4.5 s past the anchor.

| | coming | house |
|---|---|---|
| throws with no far crossing (`-missed`) | 20 | 19 |
| throws with one | 37 | 24 |

Frames run from 80 rows above the tee to the bottom of the view, the rows `farfollow` reads. Pre-labels use cin2's
rule: cin2a at 0.1, stone-width gate. That gives 439 boxes. Each frame carries `add_box` for row-sized additions.

The set is on the GPU laptop at `/data/wdd/scratch/cincinnati/cin3/set1`. The review is served at
`http://100.80.66.81:8791/` (scope `cin3:train`).
