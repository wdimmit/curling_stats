"""What produced a timeline, so a reader can tell two analyses apart.

A processed game is only reusable if we know it was made by the same pipeline
and the same model. Two strings capture that: the pipeline version, bumped by
hand whenever a change alters the output, and a model id derived from the
weights file itself so an upgraded detector cannot masquerade as the old one.
"""

import hashlib
from pathlib import Path

# Bump when a code change alters what the timeline says -- new fields, changed
# rules, different acceptance thresholds. Not for refactors that leave the
# document byte-identical.
# 2026.09.16: the side-view detector became the default crossing proposer, a
# release is no longer required for a hog-to-hog split, and the far crossing
# may be reached for within a 0.05-unit cap -- with `long_split_far_
# reach_u` added to say when it was. Different rules and a new field, so
# timelines from before this are not comparable and must not be reused.
# 2026.09.22: the destination hog line is placed per panel from its paint
# rather than the global 4.441, the far crossing may be reached for up to 0.20
# panel units, and BASELINE_M is the 21.843 m a stone's leading edge covers.
# Also covers the stage-1 release changes (db182cf), which altered releases,
# splits and thinking times without bumping this. Split values move by up to
# ~0.57 s on some panels; timelines from before this must not be reused.
PIPELINE_VERSION = "2026.09.22"


def model_id(weights) -> str:
    """A short, stable name for a weights file: its stem and a content hash.

    Two files with the same name but different training runs must not be
    confused, and a renamed copy of the same weights must not look new.
    """
    if weights is None:
        return "classical"
    path = Path(weights)
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return f"{path.stem}-{h.hexdigest()[:8]}"


def processing_version(weights, side_weights=None) -> str:
    """The full identity of one analysis configuration.

    ``side_weights`` is folded in because the side-view detector changes the
    timeline as surely as the overhead one does -- it is what times every
    throwing-end hog crossing, and so every split. Leaving it out would let a
    timeline made with the colour scan be reused for one made with a trained
    detector, which is exactly the quiet mixing this string exists to prevent.

    Omitted entirely when there is no side model, so every timeline produced
    before there was one keeps the identity it was published with.
    """
    base = f"{PIPELINE_VERSION}+{model_id(weights)}"
    return base if side_weights is None else f"{base}+side-{model_id(side_weights)}"
