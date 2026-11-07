"""The broadcast converter: a club's switching broadcast into the canonical three-panel layout."""

import json
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest

from curling_score.broadcast import layout as L
from curling_score.broadcast import convert as C


@pytest.fixture(scope="module")
def dfw():
    return L.Layout.load("dfw-5u")


def view_frame(lay, view="T", colours=None):
    """A source frame of ``view``: each region a solid colour, hard edges at its seams."""
    w, h = lay.size
    f = np.full((h, w, 3), 120, np.uint8)
    regions = lay.views[view]["regions"]
    for i, (name, r) in enumerate(sorted(regions.items())):
        x0, y0, x1, y1 = r["src"]
        f[y0:y1, x0:x1] = (colours or {}).get(name, (40 * (i + 1), 30, 200 - 40 * i))
    for x in lay.views[view].get("seams", ()):
        f[:, max(0, x - 1):x + 1] = 0
        f[:, x + 1:x + 3] = 255
    return f


def test_layout_loads_by_name(dfw):
    assert dfw.name == "dfw-5u"
    assert dfw.size == (1280, 720)
    assert dfw.T.shape == (L.H, L.W, 3)


def test_classify_reads_the_seams(dfw):
    assert dfw.classify(view_frame(dfw)) == "T"
    grey = np.full((720, 1280, 3), 120, np.uint8)
    assert dfw.classify(grey) == "?"


def test_convert_fills_regions_and_keeps_the_plate_elsewhere(dfw):
    colours = {"long_left": (10, 200, 10), "long_right": (200, 10, 10),
               "overhead_top": (10, 10, 200), "overhead_bottom": (200, 200, 10)}
    out = dfw.convert(view_frame(dfw, colours=colours), "T")
    assert out.shape == (L.H, L.W, 3)
    for name, colour in colours.items():
        x0, y0, x1, y1 = L.RECTS[name]
        centre = out[(y0 + y1) // 2, (x0 + x1) // 2]
        assert np.abs(centre.astype(int) - colour).max() <= 2, name
    # Where a region's warped source does not reach, the plate shows.
    uncovered = ~dfw.covered[("T", "long_left")].astype(bool)
    x0, y0, x1, y1 = L.RECTS["long_left"]
    if uncovered.any():
        assert np.array_equal(out[y0:y1, x0:x1][uncovered], dfw.T[y0:y1, x0:x1][uncovered])


def test_converter_repeats_the_last_good_frame_through_a_dissolve(dfw):
    good = view_frame(dfw)
    grey = np.full_like(good, 120)
    conv = C.Converter(dfw)
    out = []
    for f in [good] * 10 + [grey] * 3 + [good] * 10:
        out += conv.push(f)
    out += conv.flush()
    assert len(out) == 23
    first = dfw.convert(good, "T")
    assert all(np.array_equal(o, first) for o in out)
    assert conv.counts == {"T": 20, "?": 3}


def test_converter_stats(dfw):
    good = view_frame(dfw)
    grey = np.full_like(good, 120)
    conv = C.Converter(dfw)
    for f in [grey] * 5:
        conv.push(f)
    assert conv.stats(10) == (False, 0.0)
    for f in [good] * 5:
        conv.push(f)
    matched, frac = conv.stats(10)
    assert matched and frac == pytest.approx(0.5)


def test_convert_file_round_trip(dfw, tmp_path):
    src, dst = tmp_path / "src.mp4", tmp_path / "dst.mp4"
    frame = view_frame(dfw)
    w = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
                          "-s", "1280x720", "-r", "30", "-i", "-", "-c:v", "libx264",
                          "-pix_fmt", "yuv420p", str(src)], stdin=subprocess.PIPE)
    for _ in range(90):
        w.stdin.write(frame.tobytes())
    w.stdin.close()
    assert w.wait() == 0
    heads = []
    counts = C.convert_file(src, dst, dfw, encoder="libx264",
                            on_head=lambda s, conv: heads.append(s))
    cap = cv2.VideoCapture(str(dst))
    assert int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) == L.W
    assert int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) == L.H
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    assert abs(n - 90) <= 1
    assert counts.get("T", 0) >= 85
    assert heads and heads[-1] == pytest.approx(n / 30, abs=0.1)


def test_layout_dir_env_overrides_the_root(tmp_path, monkeypatch, dfw):
    d = tmp_path / "mine"
    d.mkdir()
    cv2.imwrite(str(d / "T.png"), dfw.T)
    (d / "layout.json").write_text(json.dumps({"size": [1280, 720], "views": dfw.views}))
    monkeypatch.setenv("BROADCAST_LAYOUT_DIR", str(tmp_path))
    assert L.Layout.load("mine").size == (1280, 720)


def test_dallas_names_its_layout():
    from curling_score import clubs
    assert clubs.DALLAS.broadcast_layout == "dfw-5u"
    assert clubs.GRANITE.broadcast_layout is None


# --- a VOD, converted once -------------------------------------------------------------

from curling_score.broadcast import vod as V  # noqa: E402


def write_clip(path, frame, n=30):
    w = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
                          "-s", f"{frame.shape[1]}x{frame.shape[0]}", "-r", "30", "-i", "-",
                          "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], stdin=subprocess.PIPE)
    for _ in range(n):
        w.stdin.write(frame.tobytes())
    w.stdin.close()
    assert w.wait() == 0


def test_vod_converted_once(dfw, tmp_path, monkeypatch):
    path = tmp_path / "videos" / "abc.mp4"
    path.parent.mkdir()
    write_clip(path, view_frame(dfw))
    assert V.ensure_converted(path, "dfw-5u") == path
    cap = cv2.VideoCapture(str(path))
    assert int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) == L.W
    assert (tmp_path / "videos" / "abc.layout").read_text().strip() == "dfw-5u"

    def boom(*a, **k):
        raise AssertionError("converted twice")
    monkeypatch.setattr(V, "convert_file", boom)
    assert V.ensure_converted(path, "dfw-5u") == path


def test_vod_no_layout_is_untouched(tmp_path):
    path = tmp_path / "abc.mp4"
    path.write_bytes(b"not a video")
    assert V.ensure_converted(path, None) == path
    assert path.read_bytes() == b"not a video"


def test_vod_wrong_layout_fails(tmp_path):
    path = tmp_path / "abc.mp4"
    write_clip(path, np.full((720, 1280, 3), 120, np.uint8))
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="broadcast layout mismatch"):
        V.ensure_converted(path, "dfw-5u")
    assert path.read_bytes() == before
    assert not list(tmp_path.glob("*.converting-*"))


def test_vod_keeps_pinned_mtime(dfw, tmp_path):
    import os
    from curling_score.ingest import cache
    path = tmp_path / "abc.mp4"
    write_clip(path, view_frame(dfw))
    os.utime(path, ns=(path.stat().st_atime_ns, cache.PINNED_MTIME_NS))
    V.ensure_converted(path, "dfw-5u")
    assert path.stat().st_mtime_ns == cache.PINNED_MTIME_NS


def test_converter_counts_frames_and_the_first_match(dfw):
    good, grey = view_frame(dfw), np.full((720, 1280, 3), 120, np.uint8)
    conv = C.Converter(dfw)
    for f in [grey] * 3:
        conv.push(f)
    assert (conv.frames, conv.first_match) == (3, None)
    for f in [good, grey]:
        conv.push(f)
    assert (conv.frames, conv.first_match) == (5, 3)


def test_vod_conversion_reports_progress_and_can_be_stopped(dfw, tmp_path, monkeypatch):
    """A 2.5 h game converts for most of an hour: the job's lease is renewed
    through its progress, and a job given away stops the conversion."""
    path = tmp_path / "abc.mp4"
    write_clip(path, view_frame(dfw))

    def fake_convert(src, dst, layout, *, on_head=None, **kw):
        Path(dst).write_bytes(b"partial")
        for s in (60.0, 120.0, 180.0):
            on_head(s, None)
        return {"T": 100}
    monkeypatch.setattr(V, "convert_file", fake_convert)
    seen = []
    V.ensure_converted(path, "dfw-5u", on_progress=seen.append)
    assert seen == [60.0, 120.0, 180.0]

    class Lost(Exception):
        pass
    path2 = tmp_path / "def.mp4"
    write_clip(path2, view_frame(dfw))
    before = path2.read_bytes()

    def lost(s):
        raise Lost()
    with pytest.raises(Lost):
        V.ensure_converted(path2, "dfw-5u", on_progress=lost)
    assert path2.read_bytes() == before and not list(tmp_path.glob("def.*conv*"))


def test_vod_temp_file_is_a_part_file(dfw, tmp_path, monkeypatch):
    """The pruner leaves *.part* files alone; the conversion's must be one."""
    path = tmp_path / "abc.mp4"
    write_clip(path, view_frame(dfw))
    names = []

    def fake_convert(src, dst, layout, **kw):
        names.append(Path(dst).name)
        Path(dst).write_bytes(Path(src).read_bytes())
        return {"T": 30}
    monkeypatch.setattr(V, "convert_file", fake_convert)
    V.ensure_converted(path, "dfw-5u")
    assert ".part" in names[0] and names[0].endswith(".mp4")


def test_the_pruner_keeps_a_videos_layout_marker_with_it(tmp_path):
    """The marker is no media of its own: it goes with its video, and only then."""
    import os
    from curling_score.ingest import prune
    videos = tmp_path / "videos"
    videos.mkdir()
    (videos / "abc.mp4").write_bytes(b"x" * 2000)
    V.mark(videos / "abc.mp4", "dfw-5u")
    old = 1_000_000.0
    os.utime(videos / "abc.mp4", (old, old))
    os.utime(videos / "abc.layout", (old - 100, old - 100))      # older than its video
    assert videos / "abc.layout" not in prune.media_files(tmp_path)
    removed = prune.prune(tmp_path, keep_gb=0.0, now=old + 10 * 86400.0, free_bytes=10**12)
    assert videos / "abc.mp4" in removed
    assert not (videos / "abc.layout").exists()
