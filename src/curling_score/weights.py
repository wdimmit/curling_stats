"""Which detector runs when nobody says.

Standardised on **ds11a** as of 2026-09-11. It is trained on 1,304 frames whose
labels a person checked, and against a second league it holds mAP50 0.992 where
ds10_stratified drops to 0.952 -- on moving stones, 0.935 recall against 0.810.
It is a fifth the training data of ds10 and better on every measure of both
benchmarks. See `validation/EXPERIMENTS.md`.

Changing this changes ``version.processing_version``, so cached timelines are
invalidated rather than quietly mixed -- which is the point of hashing the
weights into that string.

Resolution order:

1. ``CURLING_SCORE_WEIGHTS``, so a deployment can pin its own model. The literal
   ``none`` selects the classical colour detector.
2. ``weights/<DEFAULT_NAME>`` beside the installed package or the repository.

A path that is set but missing raises. Dropping to the colour detector without
saying so would leave every downstream number quietly incomparable.
"""

import os
from pathlib import Path

DEFAULT_NAME = "ds11a.pt"
ENV_VAR = "CURLING_SCORE_WEIGHTS"


def _candidates():
    here = Path(__file__).resolve()
    # src/curling_score/weights.py -> repo root, and the installed layout above.
    for base in (here.parents[2], here.parents[1], Path.cwd()):
        yield base / "weights" / DEFAULT_NAME


def default_path():
    """The weights to detect with, or None for the classical detector."""
    chosen = os.environ.get(ENV_VAR)
    if chosen:
        if chosen.strip().lower() in ("none", "classical", ""):
            return None
        path = Path(chosen)
        if not path.is_file():
            raise FileNotFoundError(
                f"{ENV_VAR}={chosen} does not exist. Set it to a weights file, "
                f"or to 'none' for the classical colour detector.")
        return path

    for path in _candidates():
        if path.is_file():
            return path
    raise FileNotFoundError(
        f"no {DEFAULT_NAME} found in a weights/ directory near "
        f"{Path(__file__).resolve().parents[2]}. Set {ENV_VAR} to a weights "
        f"file, or to 'none' for the classical colour detector.")
