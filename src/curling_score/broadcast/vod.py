"""A downloaded video converted in place through its club's broadcast layout, once.

The converted file replaces the download under the same name, so the proxy,
the detection caches and a clip all read the converted picture. Beside it,
``<id>.layout`` names the layout it went through: a video already converted
through that layout (a retry, a reprocess, a kept live recording) is left alone.
"""

import os
from pathlib import Path

from curling_score.broadcast.convert import convert_file
from curling_score.broadcast.layout import Layout

# Under this share of frames matching a view, the video is not this layout's.
MIN_MATCHED = 0.40


def marker(path) -> Path:
    path = Path(path)
    return path.with_name(f"{path.stem}.layout")


def mark(path, layout_name: str) -> None:
    marker(path).write_text(f"{layout_name}\n")


def is_converted(path, layout_name: str) -> bool:
    try:
        return marker(path).read_text().strip() == layout_name
    except OSError:
        return False


def ensure_converted(path, layout_name: str | None) -> Path:
    """``path`` converted through ``layout_name`` (None: untouched)."""
    path = Path(path)
    if layout_name is None or is_converted(path, layout_name):
        return path
    tmp = path.with_name(f"{path.stem}.converting-{os.getpid()}.mp4")
    st = path.stat()
    try:
        counts = convert_file(path, tmp, Layout.load(layout_name))
        total = sum(counts.values())
        matched = total - counts.get("?", 0)
        if not total or matched / total < MIN_MATCHED:
            raise RuntimeError(f"broadcast layout mismatch: {matched} of {total} frames of "
                               f"{path.name} match a view of {layout_name}")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    # A kept recording's pinned mtime (ingest/cache.py) is what keeps it from
    # the pruner: the converted file keeps it, with a fresh atime.
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
    mark(path, layout_name)
    return path
