# ds13 — side-view frames, for a detector that can time a hog-line crossing

Work in progress. This directory currently holds the **calibration census**
only; the frames themselves are not extracted yet.

## What the calibration found

`sideviews.json` is the output of `cs sideframes views` over ds11's whole
archive — 120 videos, 240 wide side views, one frame per clip medianed into a
clean plate per video.

**225 of 240 views calibrated.**

| | videos | views |
|---|---|---|
| both side views fitted | 111 | 222 |
| one view fitted | 3 | 3 |
| no view fitted | 6 | 0 |

**The six total failures are not side-view failures.** They are
`Bs_z89y-hi8`, `G_3D95BIbK8`, `563I0SvTZsM`, `dCviTBb5X-E`, `0M6_9wD3ofE` and
`H00-hfFV7oI` — the same six `datasets/ds11/README.md` names, the nights when
only one overhead camera was feeding. They fail at
`layout: expected 3 horizontal bars, found 2`, before any side-view code runs:
`sideview.locate` needs the overhead strip's position to know where the side
views begin. Against the 228 views that had a layout to work from, **225
calibrated — 98.7%.**

## The three that were refused, and why they stay refused

All three are **right** views, on three different nights:

| video | date | sheet | tee-to-hog |
|---|---|---|---|
| `CUWZhtdO8Sk` | 2025-11-18 | 3 | 130.4 px |
| `y-V6L_jZq2A` | 2025-11-25 | 3 | 129.1 px |
| `ervZxZmJz3A` | 2026-01-06 | 1 | 113.4 px |

Each was refused by `geometry/sideview.PLAUSIBLE_ROWS`, which admits 60–110 px.

That band was set from ten views measuring 78–90 px, where 110 looked
generous. **It is no longer generous.** Across the 225 views that did
calibrate the real spread is **77.0 to 107.0 px**, median 84.9, and sheet 3
reaches 107.0 — three pixels under the ceiling:

| sheet | views | tee-to-hog |
|---|---|---|
| 1 | 45 | 80.2–96.8 |
| 2 | 46 | 77.0–91.9 |
| 3 | 44 | 79.6–107.0 |
| 4 | 44 | 82.7–92.0 |
| 5 | 46 | 82.8–100.0 |

So 113.4 px sits only six pixels beyond the widest view we accept, which is
genuinely ambiguous: it could be a view the band wrongly excludes, or a fit
that found the wrong row. The constant's own comment is the reason not to
guess — *"a wrong row is far worse than no calibration"*, because every
crossing measured against it would be confidently mistimed rather than
missing.

**Decision: leave them refused.** Three views out of 228 is not worth widening
a guard for on a hunch. Settling it properly means looking at those three
plates and finding out *why* they read high — that is a measurement, not a
threshold change, and it can happen whenever someone wants those three nights
back.
