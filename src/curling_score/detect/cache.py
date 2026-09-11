"""Cache a panel's detections so delivery logic can be iterated on cheaply.

Detecting stones over one end costs about a minute of GPU time, and a whole
game about ten. Almost every change worth measuring -- how tracks are linked,
what confirms a shot, how the second pass ranks candidates -- happens *after*
detection and cannot alter a single box. Re-inferring for those is pure waste.

The danger with any cache is that it outlives the thing it was computed from
and quietly answers with stale numbers, which in a measurement harness looks
like a result rather than a bug. So the key covers everything that can change
a detection: the frames (video identity, panel rectangle, time range, sample
step), the mapping to metres (the calibration's own numbers), the model
(weights file identity and inference settings) -- and the *source* of the
modules that do the detecting. Editing an HSV threshold in ``rocks.py`` is
otherwise invisible to a key built from arguments alone.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, is_dataclass
from pathlib import Path

import numpy as np

from curling_score.detect.rocks import Detection

# Changing how a detection is stored or replayed must not read old files back.
FORMAT_VERSION = 1
# Any change to these files can change a detection, so they are part of the key.
_SOURCE_MODULES = (
    "curling_score.detect.rocks",
    "curling_score.detect.yolo",
    "curling_score.geometry.calibrate",
)
_COLORS = ("red", "yellow")
_FIELDS = ("x_m", "y_m", "x_px", "y_px", "area_px", "confidence")


def cache_dir() -> Path:
    # One root for every cache, so pointing a worker at a disk moves all of it.
    from curling_score.ingest.cache import default_root

    return default_root() / "detections"


def _file_identity(path) -> list:
    """Enough to notice a file changed without reading all of it."""
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return [str(p), None]
    return [p.name, st.st_size, st.st_mtime_ns]


def _source_digest() -> str:
    h = hashlib.sha256()
    for name in _SOURCE_MODULES:
        module = __import__(name, fromlist=["__file__"])
        h.update(Path(module.__file__).read_bytes())
    return h.hexdigest()[:16]


def _detector_identity(detector) -> list:
    if detector is None:
        return ["classical"]
    weights = getattr(getattr(detector, "model", None), "ckpt_path", None)
    return [
        type(detector).__name__,
        _file_identity(weights) if weights else None,
        getattr(detector, "conf", None),
        getattr(detector, "imgsz", None),
        bool(getattr(detector, "model", None)
             and detector.model.overrides.get("half")),
    ]


def key(video_path, rect, calib, detector, start_s, end_s, step_s,
        extra=None) -> str:
    """A digest of everything that can change the detections."""
    parts = [
        FORMAT_VERSION,
        _source_digest(),
        _file_identity(video_path),
        list(rect),
        asdict(calib) if is_dataclass(calib) else repr(calib),
        _detector_identity(detector),
        round(float(start_s), 3),
        round(float(end_s), 3),
        round(float(step_s), 6),
        extra,
    ]
    blob = json.dumps(parts, sort_keys=True, default=repr).encode()
    return hashlib.sha256(blob).hexdigest()[:32]


def load(digest: str):
    """The cached sequence for this key, or None."""
    path = cache_dir() / f"{digest}.npz"
    if not path.exists():
        return None
    with np.load(path) as z:
        ts, counts = z["ts"], z["counts"]
        cols = z["color"]
        vals = {f: z[f] for f in _FIELDS}
    out, i = [], 0
    for t, n in zip(ts.tolist(), counts.tolist()):
        dets = [
            Detection(color=_COLORS[cols[i + k]],
                      **{f: float(vals[f][i + k]) for f in _FIELDS})
            for k in range(n)
        ]
        out.append((t, dets))
        i += n
    return out


def save(digest: str, seq) -> None:
    d = cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    flat = [det for _t, dets in seq for det in dets]
    arrays = {
        "ts": np.array([t for t, _ in seq], dtype=np.float64),
        "counts": np.array([len(dets) for _, dets in seq], dtype=np.int32),
        "color": np.array([_COLORS.index(d.color) for d in flat], dtype=np.uint8),
    }
    for f in _FIELDS:
        # float64, not float32: the point of the cache is that a replayed run
        # and a fresh one are the same run. Rounding to single precision moves
        # a stone by half a micron, which is nothing physically, but it makes
        # cached and uncached results merely close rather than identical -- and
        # then a measurement taken from the cache can always be doubted.
        arrays[f] = np.array([getattr(d, f) for d in flat], dtype=np.float64)
    # Write beside the target and rename, so an interrupted run cannot leave a
    # half-written file that later loads as a short sequence.
    # The suffix stays .npz: savez appends it to any name that lacks one, and
    # would then leave the rename pointing at a file it never wrote.
    tmp = d / f"{digest}.tmp{os.getpid()}.npz"
    with tmp.open("wb") as fh:
        np.savez(fh, **arrays)
    tmp.replace(d / f"{digest}.npz")


def detections(digest: str, produce):
    """The cached sequence, computing and storing it on a miss."""
    hit = load(digest)
    if hit is not None:
        return hit
    seq = list(produce())
    save(digest, seq)
    return seq
