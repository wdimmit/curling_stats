# overhead-3

The overhead (panel) stone detector's training set, and the model trained on it.
Naming: see `datasets/overhead-1/README.md`.

**Why:** on the harness, overhead-2 tied ds17b but read a red logo in the ice of
sheet 1's top panel as a red stone after play stopped, making a one-rock game
after the last game on AEqLTgM25Tc (15335 s) and _OGoqF5W9-I (12015 s).

**Set:** overhead-2's fold `all` (2,204 frames) plus 45 top-panel crops with
empty labels (`logo1-frames.txt`), from `scripts/logo1/logo_mine.py` over the
red batch's 165 games (harness videos excluded), outside the games only:
- 35 `_logo1`: lights out over the sheet, overhead-2 boxing a red logo in the
  ice (two logos at one spot of the top panel, 11 videos; 2 are dark ice with
  no logo), ds17b and RF-DETR Nano boxing nothing. Checked by eye.
- 10 `_logo1lit`: the same videos lit, no one on the panel, all three models
  boxing nothing.
The other 186 first-pass hits were players (heads, shoes, broom heads,
jackets); not used, since a missed real stone would be labelled empty.
Val is ds11's (364), unchanged.

**Recipe:** overhead-2's (`scripts/logo1/run_overhead3.sh`). Only fold `all`
was trained; the harness is the test.
