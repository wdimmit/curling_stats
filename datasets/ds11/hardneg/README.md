# ds11 hard negatives — thrower false positives

Seven overhead-panel frames from `AEqLTgM25Tc`, bottom panel, end 2 shot 16,
covering one delivery from the hack to the top of the panel.

They are here because the shipped ds11 detector puts stone boxes on the
delivering player. Two distinct failures in one sequence:

- **t=1426.40** — two red boxes. The conf-0.42 box at y=+0.67 is the stone (it
  interpolates correctly between y=+0.22 at 1426.2 and y=+1.09 at 1426.6). The
  conf-0.32 box at y=+0.14, half a metre behind it, is the thrower's trailing
  arm.
- **t=1427.60, 1428.00, 1428.40** — conf 0.31/0.32/0.31, all on the thrower's
  arm and shoulder after the stone has left the panel. The track builder joined
  these to the real delivery.

`images/` holds the clean crops, exactly the frames and the crop the detector
ran on. `preview/` is the same frames with the detector's own output drawn, for
review only — never train on those. `manifest.json` records every box and which
ones are contested.

Both failures are low confidence (0.31–0.42) against 0.84–0.89 for the same
stone a fifth of a second earlier, so confidence alone separates them here —
but it did not separate them at t=1426.40, where the true box scored 0.42 and
the arm scored 0.32 with only 0.10 between them.
