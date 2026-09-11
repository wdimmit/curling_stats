# ds12 — a held-out test set from a second league

207 frames from **45 games of the Spring Monday Open League 2026** (9 dates x 5
sheets, 13 April to 15 June 2026), labelled by the *union* of ds10 and ds11a and
then reviewed by hand.

It exists to answer something ds11's own val split cannot. That split is 25
unseen *games*, but from the same league, the same weeknight and the same
months as the training data. ds12 is a different competition, a different night,
shorter games, and entirely later in time than every training frame.

    overlap with ds11's 120 videos : 0
    overlap with ds10's 5 videos   : 0
    overlap with the primary VOD   : 0

## Two choices that make the comparison fair

**Labelled by the union of both models.** Pre-labelling with one model hides
that model's own misses, because an unlabelled stone is easy for a reviewer to
overlook where a wrong box is obvious — so the model that wrote the labels gets
flattered. `harvest pool --weights-extra` unions the two through
`dataset.merge_detections`, confidence-sorted so the surer reading wins a
collision. Where the models disagree the reviewer sees a box and decides.

**No backfill.** Built with `--wave 1`, one frame per bin per video and no
substitutes: on a test set a bin that cannot be filled should read as a
shortfall, not be quietly topped up from an easier one.

## What the league looks like

45 of 45 videos gave a usable two-panel layout — no single-camera nights here,
unlike ds11's 2025-12-09. But the mix of play differs, and one difference
matters:

| per video | ds12 (Open) | ds11 (Super) |
|---|---|---|
| motion | 19.2 | 17.2 |
| empty | 16.4 | 24.5 |
| sparse | 6.5 | 5.2 |
| medium | 6.1 | 5.2 |
| **busy** | **2.7** | 4.1 |

Crowded houses are markedly rarer, and only 31 of 45 games offer even one — so
the set is 31 busy frames against 45 of everything else. That is a real property
of the league rather than a sampling failure: fewer stones survive in play. It
also means the bin ds10 handles best is the bin this league supplies least,
which is worth remembering when reading an overall number.

## Reading a score on this set

The bins are balanced by construction, so **an overall figure here is not an
estimate of real-world accuracy** — it is a comparison between models over a
deliberately even spread of difficulty. Report per-bin alongside it.
