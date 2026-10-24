"""Each sheet's saved calibration, and the check that it still fits.

A practice watch on a stream calibrates from up to twenty minutes of footage
from before Start: a mostly empty sheet. A direct feed has none, and at Start
there are players on the ice. So each sheet keeps a calibration made while it
was empty (``curling-score cameras-calibrate``), and a watch checks it at Start
against the first frames: it is used when the house rings are where it says,
and the watch calibrates from its own frames when a ring is found elsewhere
(a camera moved, say). The spec is section 4 of
docs/superpowers/specs/2026-10-24-practice-direct-feeds-design.md.

The check is new. ``Calibration.agrees_with`` compares only the side views' tee
and hog rows, and only between complete calibrations; what moves a panel's
geometry is the overhead camera, so this fits the rings again on the panels and
compares them with the ones the calibration holds. A house it cannot read --
players standing in it, say -- is not a disagreement: it says nothing about
where the camera points (``HouseCheck``).

Only ``cameras-calibrate``, on an empty sheet, replaces a saved calibration. A
watch keeps one only for a sheet that has none: one made from a busy sheet must
not stand in for one made from an empty one.

The store is a pickle per sheet: ``PanelSetup`` and the side views are plain
dataclasses, and the file is one this worker wrote for itself on the machine
that sees the cameras. A file that will not load is the same as none -- the
watch then calibrates as it would have without -- and is replaced by the next
good calibration.
"""

from __future__ import annotations

import enum
import logging
import math
import os
import pickle
from pathlib import Path

from curling_score.game import profile
from curling_score.geometry import calibrate
from curling_score.geometry import constants as C
from curling_score.live.session import Calibration

log = logging.getLogger(__name__)

# The 12-ft ring's centre may be this far from where the calibration puts the
# tee: 5 cm at the panels' ~75 px/m.
RING_AGREE_PX = 4.0
# Its radius may differ from the calibrated one by this fraction: 4 px of the
# 12-ft ring's ~137 px radius, which a re-aimed or re-zoomed camera exceeds.
RING_AGREE_RADIUS = 0.03


class CalStore:
    """``root/cameras/cal/sheet<N>.pkl``, one calibration per sheet."""

    def __init__(self, root: Path):
        self.dir = Path(root) / "cameras" / "cal"

    def path(self, sheet: int) -> Path:
        return self.dir / f"sheet{sheet}.pkl"

    def load(self, sheet: int) -> Calibration | None:
        """The sheet's saved calibration, or None if there is none or it cannot
        be read."""
        path = self.path(sheet)
        try:
            with open(path, "rb") as fh:
                # Our own file, written by save() on this machine, as the
                # module docstring says: not input from anywhere else.
                cal = pickle.load(fh)
        except FileNotFoundError:
            return None
        except Exception as exc:  # noqa: BLE001 - a bad file is no calibration
            log.warning("ignoring %s, which cannot be read: %r", path, exc)
            return None
        if not isinstance(cal, Calibration):
            log.warning("ignoring %s, which holds a %s, not a calibration",
                        path, type(cal).__name__)
            return None
        return cal

    def save(self, sheet: int, cal: Calibration) -> None:
        """Keep ``cal`` as the sheet's calibration, whole or not at all: a
        watch that reads it never meets a half-written file."""
        path = self.path(sheet)
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        try:
            with open(part, "wb") as fh:
                pickle.dump(cal, fh, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(part, path)
        finally:
            part.unlink(missing_ok=True)


class HouseCheck(enum.Enum):
    """What the house rings in a few frames say of a saved calibration."""

    AGREES = "agrees"            # both rings are where it has them
    DISAGREES = "disagrees"      # a ring found off, or the wrong size
    UNREADABLE = "unreadable"    # no frames, no well-lit frame, or no ring found


def check_house(cal: Calibration, frames: list) -> HouseCheck:
    """Whether the house rings in ``frames`` are where ``cal`` has them, if
    they can be read at all.

    On each panel's median over the frames, as the calibration itself reads
    them (`profile.panel_median`, which drops frames too dark to fit), the 12-ft
    ring is fitted again. A panel agrees when the ring's centre is within
    RING_AGREE_PX of the calibration's and its radius within RING_AGREE_RADIUS
    of the calibrated one. A ring found anywhere else on either panel
    DISAGREES, whatever the other shows. Otherwise a panel that cannot be read
    -- no frames, no well-lit frame, no ring found -- leaves it UNREADABLE,
    never an error, and both panels agreeing AGREES.
    """
    if not frames:
        log.info("saved calibration: no frames to check it against")
        return HouseCheck.UNREADABLE
    unreadable = False
    for name, setup in cal.setups.items():
        try:
            rings = calibrate.find_rings(profile.panel_median(frames, setup.rect, name))
        except calibrate.CalibrationError as exc:
            log.info("saved calibration: the %s house cannot be read: %s", name, exc)
            unreadable = True
            continue
        calib = setup.calib
        want_r = calib.px_per_m * C.R_12FT_M - calib.edge_erosion_px
        ring = rings.twelve_ft
        off_px = math.dist(ring.center, calib.center_px)
        if off_px > RING_AGREE_PX or abs(ring.radius_px - want_r) > RING_AGREE_RADIUS * want_r:
            log.info("saved calibration: the %s house is %.1f px off, radius %.1f px "
                     "against %.1f", name, off_px, ring.radius_px, want_r)
            return HouseCheck.DISAGREES
    return HouseCheck.UNREADABLE if unreadable else HouseCheck.AGREES
