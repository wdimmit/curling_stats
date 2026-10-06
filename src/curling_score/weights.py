"""Which detector runs when nobody says.

Standardised on **ds16a** as of 2026-09-28: ds15a's recipe and training set
plus ds16, 124 reviewed frames of the 2026-27 season (`datasets/ds16/`). Sheet
4's buttons were repainted with a portrait, which ds15a read as a yellow stone
in ~85% of frames; on the sheet 4 game ds16 held out, ds16a sees a stone on
the tee in 0% of them and its precision on that game's frames goes from 0.876
to 1.000. It also stops reading a red thrower's yellow toque as a yellow
stone. Benchmarks hold (ds11 val mAP50 0.993, ds12 0.987 against ds15a's
0.994 and 0.988).

ds15a (2026-09-23 to 2026-09-28) was ds11a's recipe and ds11's 1,304 reviewed
frames plus ds15's 234 frames of stones found late at the panel's far edge
(`datasets/ds15/`); ds11a (2026-09-11 to 2026-09-23) is the baseline both
were measured against. Both stay in the repository.

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

DEFAULT_NAME = "ds16a.pt"
ENV_VAR = "CURLING_SCORE_WEIGHTS"

# The SIDE-view detector, which times the throwing end's hog crossing. A
# different camera and a different job from DEFAULT_NAME: that one reads the
# overhead panels looking straight down, this one reads a disc edge-on at
# thirty metres.
#
# Standardised on ds13b as of 2026-09-16. Against the colour scan it replaced,
# on one whole game (208 shots) through the same gates: raw crossings 47.6% ->
# 93.3%, published splits 21.2% -> 77.9%, and on the 27 crossings marked by
# hand it times 25 against 9 with a median error of 0.018 s against 0.079 s.
#
# ds13c replaced it on 2026-09-29: ds13b's set plus 96 frames between the hack
# and the tee, which no frame had ever covered (the band starts at the tee).
# Held out, it finds 29 of 29 stones there against 26; production's resting
# start went from 86 to 94 of 94 rocks on a 2026-27 game; and over 418 rocks of
# four games it loses no line, times 414 crossings against 412, and moves the
# line 0.1-0.2 cm at the hog line (median). See datasets/ds13/manifest-hack1.json.
#
# `none` selects the colour scan, and so does a missing file -- the side view
# is an improvement on a pipeline that worked without it, not a dependency of
# it. Unlike DEFAULT_NAME, absence here is not an error.
SIDE_NAME = "ds13c.pt"
SIDE_ENV_VAR = "CURLING_SCORE_SIDE_WEIGHTS"

# The broom-head detector, which finds the skip's target broom in the camera
# looking at the destination house. Like SIDE_NAME it is optional: a missing
# file means no brooms, not a broken run. See `detect/broommodel.py`.
#
# broom4 as of 2026-10-06: broom3 plus wave 5b (10/01 shots broom3 missed or
# doubted) and wave 6, 75 frames of red shoes, feet and stone handles that
# broom3 held as pads well off the thrown line (flag f_1Np1: a doubles player
# standing behind the house read as the target four times). A check model that
# never saw that game dropped all its shoe targets and kept its 18 real pads;
# over 19 hosted games shoe-like held pads fell 8 -> 2 at 97.2% coverage
# against 97.4%. See datasets/broom/README.md.
BROOM_NAME = "broom4.pt"
BROOM_ENV_VAR = "CURLING_SCORE_BROOM_WEIGHTS"


def _candidates(name=DEFAULT_NAME):
    here = Path(__file__).resolve()
    # src/curling_score/weights.py -> repo root, and the installed layout above.
    for base in (here.parents[2], here.parents[1], Path.cwd()):
        yield base / "weights" / name


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


def side_path():
    """The side-view detector, or None to time crossings by colour scan.

    Resolved like :func:`default_path`, with one deliberate difference: a
    missing file is not an error. The overhead pipeline predates the side view
    and still runs without it; falling back is a worse answer, not a broken
    one, and `version.processing_version` records which was used either way.

    A path that is SET but missing still raises. That is a deployment saying it
    wants a particular model and not getting it, which is worth stopping for.
    """
    chosen = os.environ.get(SIDE_ENV_VAR)
    if chosen:
        if chosen.strip().lower() in ("none", "classical", ""):
            return None
        path = Path(chosen)
        if not path.is_file():
            raise FileNotFoundError(
                f"{SIDE_ENV_VAR}={chosen} does not exist. Set it to a weights "
                f"file, or to 'none' to time crossings by colour scan.")
        return path

    for path in _candidates(SIDE_NAME):
        if path.is_file():
            return path
    return None


def broom_path():
    """The broom-head detector, or None: no model means no brooms, not an error.

    Resolved like :func:`side_path`: ``none`` or unset-and-absent gives None,
    and a path that is set but missing raises -- a deployment asking for a
    model and not getting it is worth stopping for.
    """
    chosen = os.environ.get(BROOM_ENV_VAR)
    if chosen:
        if chosen.strip().lower() in ("none", ""):
            return None
        path = Path(chosen)
        if not path.is_file():
            raise FileNotFoundError(
                f"{BROOM_ENV_VAR}={chosen} does not exist. Set it to a weights "
                f"file, or to 'none' for no brooms.")
        return path
    for path in _candidates(BROOM_NAME):
        if path.is_file():
            return path
    return None
