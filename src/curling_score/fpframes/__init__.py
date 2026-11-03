"""Whole video frames where the pipeline's own checks caught a model out.

The hosted workers already throw detections away when they fail a sanity
check: a long-camera box far too wide or narrow for a stone at its row, a
lone side box no overhead track backs up, an overhead track that never moves
or wanders across the sheet. Each of those is a probable false positive, and
a few such frames per game are exactly what a labelling round wants. This
package records them (``record``), picks a few per game (``choose``) and
saves the frames (``store``).

Operating it: frames live in ``<cache root>/harvest/fp/<video_id>/``
(``/data/wdd/curling-cache`` and ``/data/wdd/curling-cache-2`` on the host),
and the pruner never deletes them. Each cache is capped by
``WORKER_FP_HARVEST_GB`` (20 each, so about 40 GB across both workers); when
over, the worker stops saving and logs it once. ``scripts/fp_harvest.py list``
shows the pile across both caches. The files are root-owned but
world-readable. The switch is ``WORKER_FP_HARVEST`` in the compose file's
``environment:``, which overrides worker.env.

Off unless ``WORKER_FP_HARVEST=1``. Importing the package must stay cheap and
must never touch the pipeline, so heavy imports happen inside functions.
"""

import os
from dataclasses import dataclass

# The order matters: when a game has more firings than its cap, the ones that
# sort first in this list are kept first.
CHECKS = (
    "side.width_far",
    "side.width_off",
    "side.lone",
    "ovh.quiet",
    "ovh.lateral",
    "ovh.wander",
)

# A long-camera width ratio at or outside these is not a stone seen a bit
# large or small but something else entirely (a far-end rock the model drew
# at the wrong scale, a sweeper, a banner).
WIDTH_FAR = (0.4, 2.0)

DEFAULT_CAP_GB = 20.0


@dataclass(frozen=True)
class Settings:
    enabled: bool = False
    cap_gb: float = DEFAULT_CAP_GB


def settings(env=os.environ) -> Settings:
    """The harvest switch and disk cap from the worker's environment.

    A cap that is not a number falls back to the default: a typo in a
    deployment file must not stop a worker from running jobs.
    """
    try:
        cap = float(env.get("WORKER_FP_HARVEST_GB", DEFAULT_CAP_GB))
    except (TypeError, ValueError):
        cap = DEFAULT_CAP_GB
    return Settings(enabled=env.get("WORKER_FP_HARVEST") == "1", cap_gb=cap)


def model_ids(weights) -> dict:
    """The overhead and side detectors in use, as ``version.model_id`` names.

    ``side`` is None when the side-view model is switched off (``none``).
    """
    from .. import version
    from .. import weights as weights_mod

    side = weights_mod.side_path()
    return {
        "overhead": version.model_id(weights),
        "side": version.model_id(side) if side else None,
    }
