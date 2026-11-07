"""The broadcast converter: a club's switching broadcast into the canonical three-panel layout."""

import json
import subprocess

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
