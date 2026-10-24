"""A sheet's composite layout: the FFmpeg graph that builds it, its file, and
fitting it to YouTube's picture."""

import importlib.util
import math
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

from curling_score.cameras import layout

_spec = importlib.util.spec_from_file_location(
    "fit_layout", Path(__file__).resolve().parents[1] / "scripts" / "cameras" / "fit_layout.py")
fit_layout = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = fit_layout  # its dataclasses look themselves up there
_spec.loader.exec_module(fit_layout)

W, H = 1920, 1080
GREY = (0x6E, 0x6E, 0x6E)

FIXTURE = layout.SheetLayout(
    sheet=2,
    placements=(
        layout.Placement("long_a", (512, 0, 800, 1080), 0, (0, 0, 800, 1080)),
        layout.Placement("house_h", (301, 137, 1153, 673), 90, (812, 10, 298, 510)),
        layout.Placement("house_a", (240, 160, 1293, 745), -90, (812, 552, 298, 517)),
        layout.Placement("long_h", (603, 0, 800, 1080), 0, (1120, 0, 800, 1080)),
    ),
    background="0x6e6e6e",
)


def test_filtergraph_sheet_fixture():
    got = layout.filtergraph(
        FIXTURE, inputs={"house_a": 0, "house_h": 1, "long_a": 2, "long_h": 3}, t0=1761312345.25)
    assert got == (
        "[2:v]setpts=PTS-1761312345.250000/TB,crop=800:1080:512:0:exact=1,scale=800:1080[long_a];"
        "[1:v]setpts=PTS-1761312345.250000/TB,crop=1153:673:301:137:exact=1,transpose=1,scale=298:510[house_h];"
        "[0:v]setpts=PTS-1761312345.250000/TB,crop=1293:745:240:160:exact=1,transpose=2,scale=298:517[house_a];"
        "[3:v]setpts=PTS-1761312345.250000/TB,crop=800:1080:603:0:exact=1,scale=800:1080[long_h];"
        "color=c=0x6e6e6e:s=1920x1080:r=60[base];"
        "[base][long_a]overlay=x=0:y=0:eof_action=repeat:repeatlast=1[v1];"
        "[v1][house_h]overlay=x=812:y=10:eof_action=repeat:repeatlast=1[v2];"
        "[v2][house_a]overlay=x=812:y=552:eof_action=repeat:repeatlast=1[v3];"
        "[v3][long_h]overlay=x=1120:y=0:eof_action=repeat:repeatlast=1[out]"
    )


def test_layout_json_roundtrip(tmp_path):
    path = tmp_path / "sheet2.json"
    layout.save(FIXTURE, path)
    assert layout.load(2, root=tmp_path) == FIXTURE


def test_load_names_the_missing_sheet(tmp_path):
    with pytest.raises(LookupError, match="sheet 4"):
        layout.load(4, root=tmp_path)


def _committed():
    return [pytest.param(layout.load(int(p.stem.removeprefix("sheet"))), id=p.stem)
            for p in sorted(layout.LAYOUTS.glob("sheet*.json"))]


@pytest.mark.parametrize("sheet_layout", [pytest.param(FIXTURE, id="fixture"), *_committed()])
def test_placements_tile_inside_frame(sheet_layout):
    outs = [p.out for p in sheet_layout.placements]
    assert sorted(p.role for p in sheet_layout.placements) == [
        "house_a", "house_h", "long_a", "long_h"]
    for x, y, w, h in outs:
        assert 0 <= x and 0 <= y and w > 0 and h > 0 and x + w <= W and y + h <= H
        # overlay puts a yuv420p picture at even x and y whatever it is told.
        assert x % 2 == 0 and y % 2 == 0, (x, y)
    for i, (ax, ay, aw, ah) in enumerate(outs):
        for bx, by, bw, bh in outs[i + 1:]:
            assert ax + aw <= bx or bx + bw <= ax or ay + ah <= by or by + bh <= ay


# --- fitting, on synthetic cameras -------------------------------------------------------


def _texture(rng):
    """A camera's worth of structure ORB can find again at 0.4x: noise at three
    scales and some hard-edged shapes."""
    img = np.zeros((H, W), np.float32)
    for cell, weight in ((48, 1.0), (16, 0.6), (5, 0.3)):
        small = rng.random((H // cell + 2, W // cell + 2)).astype(np.float32)
        img += weight * cv2.resize(small, (W + 2 * cell, H + 2 * cell),
                                   interpolation=cv2.INTER_CUBIC)[cell:cell + H, cell:cell + W]
    img = (img - img.min()) / (img.max() - img.min()) * 200 + 20
    for _ in range(250):
        x, y = int(rng.integers(0, W)), int(rng.integers(0, H))
        level = float(rng.integers(0, 256))
        if rng.random() < 0.5:
            cv2.circle(img, (x, y), int(rng.integers(8, 40)), level, -1)
        else:
            cv2.rectangle(img, (x, y), (x + int(rng.integers(10, 80)), y + int(rng.integers(10, 80))),
                          level, -1)
    img = img.astype(np.uint8)
    return cv2.merge([img, cv2.add(img, 10), img])


@pytest.fixture(scope="module")
def cameras():
    rng = np.random.default_rng(7)
    roles = ("house_a", "house_h", "long_a", "long_h")
    return [{role: _texture(rng) for role in roles} for _ in range(3)]


def _similarity(crop, rotate, out, *, stretch=1.0, extra_deg=0.0):
    """The 2x3 map from raw to composite pixels that puts ``crop``, turned by
    ``rotate``, onto ``out``, in continuous coordinates (pixel centres at +0.5).
    ``stretch`` scales the out rect's height alone; ``extra_deg`` turns the
    picture about the out rect's centre."""
    cx, cy, cw, ch = crop
    ox, oy, ow, oh = out
    if rotate == 0:
        a, c = ow / cw, oh / ch
        m = np.array([[a, 0, ox - a * cx], [0, c, oy - c * cy]])
    elif rotate == 90:  # clockwise: raw left -> top, raw top -> right
        a, c = oh / cw, ow / ch
        m = np.array([[0, -c, ox + c * (cy + ch)], [a, 0, oy - a * cx]])
    else:
        a, c = oh / cw, ow / ch
        m = np.array([[0, c, ox - c * cy], [-a, 0, oy + a * (cx + cw)]])
    centre = np.array([ox + ow / 2, oy + oh / 2])
    t = math.radians(extra_deg)
    turn = np.array([[math.cos(t), -math.sin(t)], [math.sin(t), math.cos(t)]])
    turn = np.diag([1.0, stretch]) @ turn
    full = np.vstack([m, [0, 0, 1]])
    move = np.eye(3)
    move[:2, :2] = turn
    move[:2, 2] = centre - turn @ centre
    return (move @ full)[:2]


def _compose(raw, truth, **distort):
    """A YouTube-like composite: grey bars, and each role's camera mapped onto
    its out rect. ``distort`` maps a role to _similarity's extra arguments."""
    frame = np.empty((H, W, 3), np.uint8)
    frame[:] = GREY
    for p in truth:
        m = _similarity(p.crop, p.rotate, p.out, **distort.get(p.role, {}))
        scale = math.sqrt(abs(np.linalg.det(m[:, :2])))
        src = raw[p.role]
        if scale < 0.9:
            src = cv2.GaussianBlur(src, (0, 0), 0.45 / scale)
        # cv2 maps integer pixel centres; shift the continuous map onto them.
        m_int = m.copy()
        m_int[:, 2] += m[:, :2] @ [0.5, 0.5] - [0.5, 0.5]
        warped = cv2.warpAffine(src, m_int, (W, H), flags=cv2.INTER_LINEAR,
                                borderMode=cv2.BORDER_CONSTANT, borderValue=GREY)
        x, y, w, h = p.out
        frame[y:y + h, x:x + w] = warped[y:y + h, x:x + w]
    return frame


def _crop_under(truth, out):
    """The raw rectangle that the truth's map sends onto ``out``."""
    inv = cv2.invertAffineTransform(_similarity(truth.crop, truth.rotate, truth.out))
    x, y, w, h = out
    corners = np.array([[x, y], [x + w, y + h]], float) @ inv[:, :2].T + inv[:, 2]
    (x0, y0), (x1, y1) = corners.min(axis=0), corners.max(axis=0)
    return x0, y0, x1 - x0, y1 - y0


# Panels at YouTube's rows on sheet 1, 11 and 553, which overlay cannot place.
YOUTUBE_LIKE = tuple(
    layout.Placement(p.role, p.crop, p.rotate, (p.out[0], p.out[1] + 1, *p.out[2:])) if p.rotate
    else p for p in FIXTURE.placements)
SWAPPED = (
    layout.Placement("long_h", (450, 0, 800, 1080), 0, (0, 0, 800, 1080)),
    layout.Placement("house_a", (380, 120, 1180, 690), -90, (812, 11, 298, 510)),
    layout.Placement("house_h", (300, 200, 1150, 662), 90, (812, 553, 298, 517)),
    layout.Placement("long_a", (600, 49, 727, 982), 0, (1120, 0, 800, 1080)),
)


def _fit(cameras, truth, *, sheet=2, **kwargs):
    distort = kwargs.pop("distort", {})
    frames = [_compose(cams, truth, **distort) for cams in cameras]
    return fit_layout.fit(frames[0], cameras[0], sheet, frames=frames, **kwargs)


@pytest.mark.parametrize("truth", [YOUTUBE_LIKE, SWAPPED], ids=["youtube-like", "swapped"])
def test_fit_recovers_synthetic_layout(cameras, truth):
    got = _fit(cameras, truth)
    assert got.sheet == 2
    assert got.background == "0x6e6e6e"
    by_role = {p.role: p for p in got.placements}
    assert sorted(by_role) == ["house_a", "house_h", "long_a", "long_h"]
    for want in truth:
        p = by_role[want.role]
        assert p.rotate == want.rotate, want.role
        # The region YouTube shows, moved to even x and y; the crop moves with
        # it, so the picture stays where YouTube put it.
        x, y, w, h = want.out
        assert p.out == (x - x % 2, y - y % 2, w, h), want.role
        crop = _crop_under(want, p.out)
        assert np.abs(np.subtract(p.crop, crop)).max() <= 2, (want.role, p.crop, crop)


def test_fit_takes_a_long_view_shown_one_to_one_at_its_own_size(cameras):
    # YouTube shows the long views 1:1; a fit 0.2% off that is noise, and
    # cropping 1078 rows to fill 1080 would resample the whole view.
    got = _fit(cameras, YOUTUBE_LIKE, distort={"long_h": {"stretch": 1.002}})
    p = {p.role: p for p in got.placements}["long_h"]
    assert p.crop[2:] == p.out[2:] == (800, 1080)
    assert abs(p.crop[0] - 603) <= 2


def test_fit_refuses_a_panel_turned_off_the_right_angle(cameras):
    with pytest.raises(fit_layout.LayoutFitError, match="house_h.*4.0"):
        _fit(cameras, YOUTUBE_LIKE, distort={"house_h": {"extra_deg": 4.0}})


def test_fit_refuses_a_stretched_panel_unless_allowed(cameras):
    distort = {"house_a": {"stretch": 1.06}}
    with pytest.raises(fit_layout.LayoutFitError, match="house_a.*x and y"):
        _fit(cameras, YOUTUBE_LIKE, distort=distort)
    got = _fit(cameras, YOUTUBE_LIKE, distort=distort, max_anisotropy=0.1)
    p = {p.role: p for p in got.placements}["house_a"]
    # The stretch is along the panel's height, which is the raw frame's width.
    cx, cy, cw, ch = p.crop
    assert abs(cw - 1293 / 1.06) <= 3 and abs(ch - 745) <= 2


def test_sheet_1_top_panel_alone_keeps_youtubes_stretch(cameras):
    # YouTube has shown sheet 1's top panel 5.5% stretched since 10/15 at least.
    stretched_top = {"house_h": {"stretch": 1.055}}
    got = _fit(cameras, YOUTUBE_LIKE, sheet=1, distort=stretched_top)
    p = {p.role: p for p in got.placements}["house_h"]
    cx, cy, cw, ch = p.crop
    assert abs(cw - 1153 / 1.055) <= 3 and abs(ch - 673) <= 2
    # Only there: the same stretch elsewhere on sheet 1, or on another sheet, is refused.
    with pytest.raises(fit_layout.LayoutFitError, match="house_a.*x and y"):
        _fit(cameras, YOUTUBE_LIKE, sheet=1, distort={"house_a": {"stretch": 1.055}})
    with pytest.raises(fit_layout.LayoutFitError, match="house_h.*x and y"):
        _fit(cameras, YOUTUBE_LIKE, sheet=2, distort=stretched_top)


def test_fit_reports_a_crop_outside_the_raw_frame(cameras):
    rezoomed = tuple(
        layout.Placement("long_h", (1180, 0, 800, 1080), 0, p.out) if p.role == "long_h" else p
        for p in YOUTUBE_LIKE)
    with pytest.raises(fit_layout.CropOutsideFrame, match="long_h"):
        _fit(cameras, rezoomed)
