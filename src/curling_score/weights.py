"""Which detector runs when nobody says.

Standardised on **ds17b** as of 2026-10-09: ds16a's recipe and training set
plus ds17, 311 reviewed throwing-panel frames (`datasets/ds17/`). ds16a read a
sliding thrower's head, hat or hood, seen from straight above right behind the
stone, as a stone at up to 0.98; at 10 fps a red toque outvoted three real
yellow releases. Over every timed throw of 76 hosted games (6,593 throws) the
runs of such boxes moving up the sheet go from 304 to 76 and the confident
boxes from 2,223 to 653, while the arriving stone is seen in 91.7% of its
frames against 89.9%. Benchmarks hold (ds11 val mAP50 0.994, ds12 0.988
against ds16a's 0.993 and 0.987). Same architecture (YOLO11n) and live cost.

ds16a (2026-09-28 to 2026-10-09) was ds15a's set plus ds16, 124 frames of the
2026-27 season (sheet 4's portrait button, a yellow toque). ds15a (2026-09-23
to 2026-09-28) was ds11a's recipe and ds11's 1,304 reviewed frames plus ds15's
234 frames of stones found late at the panel's far edge (`datasets/ds15/`);
ds11a (2026-09-11 to 2026-09-23) is the baseline all were measured against.
All stay in the repository.

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

DEFAULT_NAME = "ds17b.pt"
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
# ds13d replaced it on 2026-10-12: ds13c's set plus 80 band frames where a
# broom head, a shoe or the thrower's hand touched the rock, boxed round the
# stone alone (datasets/ds13/manifest-broom1.json). ds13c boxed the stone with
# whatever touched it for 3-5 frames at a time, which at 30 fps stepped the
# delivery chart sideways. On held-out frames a person redrew, the centre's
# error across went from 6.5 to 1.9 px (p90) and the box's width over the
# stone from +13 to +3 px; on a game neither trained on, widened frames went
# from 2.2% to 1.0%. Over 508 rocks of five games: crossings 503 against 502,
# lines 507 against 507, the line 0.13 cm apart at the hog line (median), the
# broom figure 0.57 in; band val mAP50 0.884 -> 0.908.
#
# ds13e replaced it on 2026-10-13: ds13d's set plus 120 frames where 2026-10-12's
# delivery paths kinked -- the hack with the thrower's hand on the stone, the
# slide, the band's top edge, the release -- boxed round the stone alone
# (datasets/ds13/manifest-kink1.json). Rebuilt through the production delivery
# code on 699 rocks of seven games neither trained on, the path's kinks went
# from 401 to 313 (across 123 -> 79, behind the tee 79 -> 42), fewer in every
# game; crossings 696 against 693, the line 0.13 cm apart at the hog line
# (median), the broom figure 0.47 in, the start 2.7 cm (along only, unbiased).
# It scores lower against the hack wave's own labels (val mAP50-95 0.70 ->
# 0.65), which take in a hand or shoe the kink wave's rule leaves out.
#
# long-1 replaced it on 2026-10-28, the first of the long-camera models named
# for the camera rather than in the ds sequence: ds13e's set (with the hack wave
# relabelled) plus 92 long-camera frames a member boxed on /label
# (datasets/long-1). Through the production delivery code on 821 rocks of seven
# games, the path's kinks went from 822 to 661 (across 96 -> 71, along 726 ->
# 590), fewer in every game; crossings 821 against 815, the line 0.15 cm apart
# at the hog line (median), the broom figure 0.54 in, the start 3.2 cm. On the
# held-out members' frames it finds more stones (mAP50 0.89 -> 0.94) but fits
# their boxes less tightly (mAP50-95 0.78 -> 0.75).
#
# `none` leaves the throwing hog line untimed from the long camera. Like
# DEFAULT_NAME, a missing file is an error: the colour scan it once fell back
# to is gone (2026-10-30).
SIDE_NAME = "long-1.pt"
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

# The handle-point model the spin pass reads a rock's rotation with: two
# keypoints, the handle's neck (its pivot) and its grip end, on a crop of the
# stone from the long camera facing the thrower. Like SIDE_NAME it is optional:
# a missing file means no spin, not a broken run. See `game/spintime.py`.
#
# spin1 as of 2026-10-10: yolo11n-pose at 224 px on 258 hand-labelled frames of
# five 2026-27 games, one per sheet (datasets/spin, the spike's p5). On rocks it
# never saw, its early rates sit within ~10% of frame-by-frame eye readings, and
# it reads 122 of 128 rocks of four games' unlabelled ends.
SPIN_NAME = "spin1.pt"
SPIN_ENV_VAR = "CURLING_SCORE_SPIN_WEIGHTS"

# The release model: a ResNet-18 that reads a stack of three grey crops of the
# delivery lane from the long camera facing the thrower as the hand on, or off,
# the handle. Like SPIN_NAME it is optional: a missing file means no release
# reads, not a broken run. A float16 `state_dict` of `releasemodel.build()`'s
# model. See `detect/releasemodel.py`.
RELEASE_NAME = "release1.pt"
RELEASE_ENV_VAR = "CURLING_SCORE_RELEASE_WEIGHTS"


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
    """The side-view detector, or None when it is switched off (``none``):
    then the long camera times no hog crossings, and the lines, brooms,
    spins and releases it feeds go without. Resolved like
    :func:`default_path`; a path set or defaulted but missing raises."""
    chosen = os.environ.get(SIDE_ENV_VAR)
    if chosen:
        if chosen.strip().lower() in ("none", "classical", ""):
            return None
        path = Path(chosen)
        if not path.is_file():
            raise FileNotFoundError(
                f"{SIDE_ENV_VAR}={chosen} does not exist. Set it to a weights "
                f"file, or to 'none' for no long-camera hog crossings.")
        return path

    for path in _candidates(SIDE_NAME):
        if path.is_file():
            return path
    raise FileNotFoundError(
        f"no {SIDE_NAME} found in a weights/ directory near "
        f"{Path(__file__).resolve().parents[2]}. Set {SIDE_ENV_VAR} to a weights "
        f"file, or to 'none' for no long-camera hog crossings.")


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


def spin_path():
    """The handle-point model, or None: no model means no spin, not an error.

    Resolved like :func:`broom_path`: ``none`` or unset-and-absent gives None,
    and a path that is set but missing raises.
    """
    chosen = os.environ.get(SPIN_ENV_VAR)
    if chosen:
        if chosen.strip().lower() in ("none", ""):
            return None
        path = Path(chosen)
        if not path.is_file():
            raise FileNotFoundError(
                f"{SPIN_ENV_VAR}={chosen} does not exist. Set it to a weights "
                f"file, or to 'none' for no spin.")
        return path
    for path in _candidates(SPIN_NAME):
        if path.is_file():
            return path
    return None


def release_path():
    """The release model, or None: no model means no release reads, not an error.

    Resolved like :func:`spin_path`: ``none`` or unset-and-absent gives None,
    and a path that is set but missing raises.
    """
    chosen = os.environ.get(RELEASE_ENV_VAR)
    if chosen:
        if chosen.strip().lower() in ("none", ""):
            return None
        path = Path(chosen)
        if not path.is_file():
            raise FileNotFoundError(
                f"{RELEASE_ENV_VAR}={chosen} does not exist. Set it to a weights "
                f"file, or to 'none' for no release reads.")
        return path
    for path in _candidates(RELEASE_NAME):
        if path.is_file():
            return path
    return None
