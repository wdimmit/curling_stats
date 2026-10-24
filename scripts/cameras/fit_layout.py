"""Fit a sheet's camera layout to YouTube's picture of that sheet.

    python scripts/cameras/fit_layout.py --sheet 2 \\
        --youtube 'layout-ref/yt/sheet2/f*.png' --cameras layout-ref/cam \\
        --note "awslsC-NuII (10/22 sheet 2); cameras 2026-10-24 08:09 PT, sheet empty"

``--youtube`` is at least eight frames from across one of this season's
recordings of the sheet; ``--cameras`` holds one frame from each of the sheet's
four cameras, named ``<camera>.png`` (``2A-HOUSE.png``...). It writes
``src/curling_score/cameras/layouts/sheet{N}.json`` and a check image under
``out/cameras/layout-check/``: YouTube, the picture rebuilt from the cameras,
their blend and their difference.

YouTube's regions are found the way the tracker finds them: the house panels by
the bars' stillness (``geometry.layout.detect_panels``) and the long views
either side of the strip (``geometry.sideview.locate``, less the bar). Each
region is matched to the camera it shows by ORB features and a RANSAC
similarity, against both cameras that could fill it, so which camera goes where
is measured rather than assumed. A turn more than ``MAX_RESIDUAL_DEG`` off a
right angle, x and y scales more than ``MAX_ANISOTROPY`` apart (sheet 1's
stretched top panel is the one exception, ``STRETCH_ALLOWED``), or a crop that
runs off the camera's frame (a PTZ moved since the broadcast) refuses the fit.
"""

from __future__ import annotations

import argparse
import glob
import logging
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np

from curling_score.cameras import inventory, layout
from curling_score.geometry import layout as panels
from curling_score.geometry import sideview

log = logging.getLogger("fit_layout")

Rect = tuple[int, int, int, int]

# ORB: plenty of features, and enough pyramid levels (1.2**11 = 7.4x) to span
# the house panels' ~0.4x of their camera.
ORB_FEATURES = 8000
ORB_SCALE = 1.2
ORB_LEVELS = 12
ORB_FAST_THRESHOLD = 10
RATIO_TEST = 0.8
RANSAC_PX = 3.0
REFITS = 3
# Below this width a region is a house panel, ~300 px of a camera shrunk to
# 0.4x; doubling it first gave 1.5-2x the inliers on sheet 1's panels.
SMALL_REGION_PX = 400
# On this season's five sheets the right camera kept 331-1246 inliers and the
# other candidate at most 108: the two house cameras both see rings, and the
# two long cameras the far wall.
MIN_INLIERS = 40
MIN_MARGIN = 2.0
MAX_RESIDUAL_DEG = 2.0
# The true fits measured 0.0-0.4% apart; 1% is 5 px across a 510 px panel.
MAX_ANISOTROPY = 0.01
# The one exception, by (sheet, region). YouTube shows sheet 1's top panel
# (1H-HOUSE) 5.5% taller than its width would make it, on every recording
# checked since 10/15. The tracker has been tuned on that picture, so the
# rebuilt one keeps it; crop and scale reproduce a stretch exactly.
STRETCH_ALLOWED = {(1, "top"): 0.07}
# YouTube shows the long views 1:1, and their fits came out 0.9988-1.0013.
NATIVE_SNAP = 0.005
# A crop may graze its frame's edge by fit noise (sheet 2's H long view sits
# 1.2 px low on YouTube); anything more is a camera that has moved.
CROP_SLACK_PX = 2
# A long view's edge column belongs to the view only if it moves like the view.
# Compression blurs the bar's edge column to 6-10 px of std over a recording;
# bar interiors sit at <=2, the views at >=38.
VIEW_EDGE_STD = 20.0
MIN_YOUTUBE_FRAMES = 8
LONG_REGIONS = ("left", "right")
PANEL_REGIONS = ("top", "bottom")
ROLES = ("house_a", "house_h", "long_a", "long_h")


class LayoutFitError(RuntimeError):
    """The sheet's layout could not be fitted; nothing should be written."""


class CropOutsideFrame(LayoutFitError):
    """YouTube shows more than the camera now sees: it has moved or zoomed."""


@dataclass(frozen=True)
class Match:
    """How one YouTube region was fitted to its camera."""

    region: str
    role: str
    camera: str
    inliers: int
    others: dict[str, int]  # the other candidate's inliers, by role
    angle_deg: float  # the RANSAC similarity's turn, before snapping
    rotate: int
    scale: tuple[float, float]  # along the raw camera's x and y
    rms_px: float
    crop: Rect
    out: Rect
    seen: Rect  # the region on YouTube; out is it moved to even x and y
    nudged_px: int
    max_anisotropy: float  # the limit this region was held to


def regions(frames) -> dict[str, Rect]:
    """YouTube's four regions in composite pixels: the house panels as the
    tracker finds them, and the long views less the bars beside the strip."""
    found = panels.detect_panels(frames)
    h, w = np.asarray(frames[0]).shape[:2]
    views = sideview.locate(found, w, h)
    stack = np.stack([np.asarray(f, dtype=np.float32).mean(axis=2) for f in frames])
    moves = np.percentile(stack.std(axis=0), 99, axis=0)
    lx, ly, lw, lh = views["left"]
    while lw > 0 and moves[lx + lw - 1] < VIEW_EDGE_STD:
        lw -= 1
    rx, ry, rw, rh = views["right"]
    while rw > 0 and moves[rx] < VIEW_EDGE_STD:
        rx, rw = rx + 1, rw - 1
    return {"left": (lx, ly, lw, lh), "top": found.top, "bottom": found.bottom,
            "right": (rx, ry, rw, rh)}


def background(frames, rects) -> str:
    """The bars' grey as ``0xRRGGBB``: whatever no region covers."""
    mean = np.mean(np.stack([np.asarray(f, dtype=np.float32) for f in frames]), axis=0)
    bars = np.ones(mean.shape[:2], bool)
    for x, y, w, h in rects:
        bars[y:y + h, x:x + w] = False
    b, g, r = (int(round(v)) for v in np.median(mean[bars], axis=0))
    return f"0x{r:02x}{g:02x}{b:02x}"


def _orb():
    return cv2.ORB_create(nfeatures=ORB_FEATURES, scaleFactor=ORB_SCALE, nlevels=ORB_LEVELS,
                          fastThreshold=ORB_FAST_THRESHOLD)


def _centre(keypoints, indices):
    """Keypoints in continuous pixel coordinates (centres at +0.5). ORB scales
    a level's point up by the level's factor alone, which leaves it
    0.5 * (factor - 1) px short of where it is; uncorrected, a house panel's
    crop came out about a pixel low."""
    return np.float32([[c + 0.5 * ORB_SCALE ** kp.octave for c in kp.pt]
                       for kp in (keypoints[i] for i in indices)]).reshape(-1, 2)


def _features(orb, img):
    gray = cv2.cvtColor(np.ascontiguousarray(img), cv2.COLOR_BGR2GRAY)
    return orb.detectAndCompute(gray, None)


def _pairs(raw_features, region_features, origin, up):
    """Ratio-tested matches as (raw points, composite points), both in
    continuous pixel coordinates (centres at +0.5), as crop and scale see them."""
    kr, dr = raw_features
    kq, dq = region_features
    if dr is None or dq is None or len(kr) < 2 or len(kq) < 2:
        return np.empty((0, 2), np.float32), np.empty((0, 2), np.float32)
    good = [m for m, n in (p for p in cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(dr, dq, k=2)
                           if len(p) == 2) if m.distance < RATIO_TEST * n.distance]
    src = _centre(kr, [m.queryIdx for m in good])
    dst = _centre(kq, [m.trainIdx for m in good]) / up
    return src, dst + np.float32(origin)


def _similarity(src, dst):
    """RANSAC similarity from raw to composite: (2x3 matrix, inlier mask), or
    (None, empty mask) when there is nothing to fit."""
    if len(src) < 3:
        return None, np.zeros(len(src), bool)
    m, inliers = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC,
                                             ransacReprojThreshold=RANSAC_PX, maxIters=10000,
                                             confidence=0.999)
    if m is None:
        return None, np.zeros(len(src), bool)
    return m, inliers.ravel().astype(bool)


# Which raw axis feeds each composite axis, and its sign, for each turn.
# Clockwise sends the raw frame's left edge to the top and its top to the right.
_AXES = {0: ((0, 1), (1, 1)), 90: ((1, -1), (0, 1)), -90: ((1, 1), (0, -1))}


def _axis_fit(src, dst, rotate):
    """Least squares with the turn fixed: each composite axis from its raw axis,
    with its own scale. (slope, offset) per composite axis."""
    return [tuple(np.polyfit(src[:, raw_axis], dst[:, out_axis], 1))
            for out_axis, (raw_axis, _sign) in enumerate(_AXES[rotate])]


def _axis_err(src, dst, fits, rotate):
    pred = np.stack([slope * src[:, raw_axis] + offset
                     for (raw_axis, _sign), (slope, offset) in zip(_AXES[rotate], fits)], axis=1)
    return np.hypot(*(pred - dst).T)


def _fit_axes(src, dst, inliers, rotate):
    """The per-axis fit, choosing its inliers afresh a few times, so a stretch
    the similarity could not follow does not keep the panel's far ends out.
    Returns the fit, its inliers and their rms error in composite pixels."""
    for _ in range(REFITS):
        fits = _axis_fit(src[inliers], dst[inliers], rotate)
        inliers = _axis_err(src, dst, fits, rotate) < RANSAC_PX
    fits = _axis_fit(src[inliers], dst[inliers], rotate)
    err = _axis_err(src[inliers], dst[inliers], fits, rotate)
    return fits, inliers, float(np.sqrt(np.mean(err ** 2)))


def _inside(lo, hi, size):
    """[lo, hi) slid back inside [0, size), keeping its length where it fits."""
    shift = max(0, -lo) - max(0, hi - size)
    return max(0, lo + shift), min(size, hi + shift)


def _crop(fits, rotate, out):
    """The raw rectangle that the per-axis fit maps onto ``out``, as floats."""
    ox, oy, ow, oh = out
    spans = [None, None]
    for (lo, size), (raw_axis, _sign), (slope, offset) in zip(
            ((ox, ow), (oy, oh)), _AXES[rotate], fits):
        a, b = sorted(((lo - offset) / slope, (lo + size - offset) / slope))
        spans[raw_axis] = (a, b)
    (x0, x1), (y0, y1) = spans
    return x0, y0, x1, y1


def _snap(angle):
    """The nearest turn FFmpeg's transpose can make, and how far off it is."""
    best = min((0, 90, -90, 180), key=lambda r: abs((angle - r + 180) % 360 - 180))
    return best, (angle - best + 180) % 360 - 180


def _place(region, role, camera, rect, fit, others, raw_shape, max_anisotropy) -> Match:
    m, src, dst, inliers = fit
    angle = math.degrees(math.atan2(m[1, 0], m[0, 0]))
    rotate, residual = _snap(angle)
    where = f"the {region} region: {role} ({camera})"
    if rotate == 180:
        raise LayoutFitError(f"{where} is upside down ({angle:.1f}°); transpose cannot turn 180°")
    if abs(residual) > MAX_RESIDUAL_DEG:
        raise LayoutFitError(f"{where} is turned {angle:.1f}°, {abs(residual):.1f}° off a right "
                             f"angle (limit {MAX_RESIDUAL_DEG}°)")
    fits, inliers, rms = _fit_axes(src, dst, inliers, rotate)
    scale = [0.0, 0.0]
    for (raw_axis, sign), (slope, _offset) in zip(_AXES[rotate], fits):
        scale[raw_axis] = sign * slope
    if min(scale) <= 0:
        raise LayoutFitError(f"{where} is mirrored (scales {scale[0]:.4f}, {scale[1]:.4f})")
    apart = scale[0] / scale[1] - 1
    if abs(apart) > max_anisotropy:
        raise LayoutFitError(
            f"{where} is scaled {scale[0]:.4f} and {scale[1]:.4f} along the camera's x and y, "
            f"{abs(apart):.1%} apart (limit {max_anisotropy:.1%}); YouTube stretched it")
    # overlay puts a yuv420p picture at even x and y. Move the rect there and
    # crop for the moved rect, so the picture itself stays where YouTube had it.
    ox, oy, ow, oh = rect
    out = (ox - ox % 2, oy - oy % 2, ow, oh)
    x0, y0, x1, y1 = _crop(fits, rotate, out)
    if all(abs(s - 1) <= NATIVE_SNAP for s in scale):
        # Shown 1:1: crop the out's own size about the fitted centre, so
        # nothing resamples the view.
        w, h = (ow, oh) if rotate == 0 else (oh, ow)
        x0, y0 = int(round((x0 + x1 - w) / 2)), int(round((y0 + y1 - h) / 2))
        x1, y1 = x0 + w, y0 + h
    x0, y0, x1, y1 = (int(round(v)) for v in (x0, y0, x1, y1))
    rh, rw = raw_shape[:2]
    over = max(0, -x0, -y0, x1 - rw, y1 - rh)
    if over > CROP_SLACK_PX:
        raise CropOutsideFrame(
            f"{where} needs raw crop x {x0}..{x1}, y {y0}..{y1}, {over} px outside its "
            f"{rw}x{rh} frame: the camera has moved or zoomed since the broadcast")
    if over:
        (x0, x1), (y0, y1) = _inside(x0, x1, rw), _inside(y0, y1, rh)
        log.warning("%s: crop grazed the frame's edge by %d px; moved it inside", where, over)
    return Match(region=region, role=role, camera=camera, inliers=int(inliers.sum()),
                 others=others, angle_deg=round(angle, 3), rotate=rotate,
                 scale=(round(float(scale[0]), 5), round(float(scale[1]), 5)), rms_px=round(rms, 3),
                 crop=(x0, y0, x1 - x0, y1 - y0), out=out, seen=tuple(rect), nudged_px=over,
                 max_anisotropy=max_anisotropy)


def fit_matches(composite, raw, sheet, *, frames, max_anisotropy=None):
    """``fit``, with the record of each region's match."""
    if sorted(raw) != sorted(ROLES):
        raise ValueError(f"need a frame from each of {ROLES}, got {sorted(raw)}")
    cameras = inventory.sheet_cameras(sheet)
    rects = regions(frames)
    orb = _orb()
    raw_features = {role: _features(orb, img) for role, img in raw.items()}
    fits = {}  # (region, role) -> (matrix, src, dst, inlier mask)
    for region, (x, y, w, h) in rects.items():
        up = 2 if w < SMALL_REGION_PX else 1
        crop = np.asarray(composite)[y:y + h, x:x + w]
        if up > 1:
            crop = cv2.resize(crop, (w * up, h * up), interpolation=cv2.INTER_CUBIC)
        region_features = _features(orb, crop)
        kind = "long" if region in LONG_REGIONS else "house"
        for role in (r for r in ROLES if r.startswith(kind)):
            src, dst = _pairs(raw_features[role], region_features, (x, y), up)
            m, inliers = _similarity(src, dst)
            fits[region, role] = (m, src, dst, inliers)

    def count(region, role):
        return int(fits[region, role][3].sum())

    chosen = {}
    for pair, kind in ((LONG_REGIONS, "long"), (PANEL_REGIONS, "house")):
        a, b = f"{kind}_a", f"{kind}_h"
        ways = [{pair[0]: a, pair[1]: b}, {pair[0]: b, pair[1]: a}]
        totals = [sum(count(reg, role) for reg, role in way.items()) for way in ways]
        best = int(np.argmax(totals))
        table = ", ".join(f"{reg}~{role} {count(reg, role)}" for reg in pair for role in (a, b))
        if totals[best] < MIN_MARGIN * totals[1 - best]:
            raise LayoutFitError(f"sheet {sheet}: cannot tell which {kind} camera is where "
                                 f"(inliers {table})")
        for reg, role in ways[best].items():
            if count(reg, role) < MIN_INLIERS:
                raise LayoutFitError(f"sheet {sheet}: the {reg} region matches {role} "
                                     f"({cameras[role]}) with only {count(reg, role)} inliers "
                                     f"(need {MIN_INLIERS}; {table})")
        chosen.update(ways[best])

    matches = []
    for region in ("left", "top", "bottom", "right"):
        role = chosen[region]
        others = {r: count(region, r) for r in ROLES if r != role and (region, r) in fits}
        limit = (max_anisotropy if max_anisotropy is not None
                 else STRETCH_ALLOWED.get((sheet, region), MAX_ANISOTROPY))
        match = _place(region, role, cameras[role], rects[region], fits[region, role], others,
                       np.asarray(raw[role]).shape, limit)
        log.info("sheet %d %s: %s (%s), %d inliers against %s; turned %.2f° -> %d, "
                 "scale %.4f x %.4f, rms %.2f px, crop %s -> out %s", sheet, region, role,
                 match.camera, match.inliers, others, match.angle_deg, match.rotate,
                 *match.scale, match.rms_px, match.crop, match.out)
        matches.append(match)
    sheet_layout = layout.SheetLayout(
        sheet=sheet,
        placements=tuple(layout.Placement(m.role, m.crop, m.rotate, m.out) for m in matches),
        background=background(frames, rects.values()))
    return sheet_layout, matches


def fit(composite, raw, sheet, *, frames, max_anisotropy=None) -> layout.SheetLayout:
    """The layout that rebuilds ``composite`` (a YouTube picture of ``sheet``)
    from ``raw`` (role -> that camera's frame).

    ``frames`` are YouTube frames from across the same recording: the house
    panels are found by what never moves, so it takes several (``composite``
    may be one of them, or their median). ``max_anisotropy``, if given,
    replaces every region's limit (``MAX_ANISOTROPY``, or ``STRETCH_ALLOWED``).
    """
    return fit_matches(composite, raw, sheet, frames=frames, max_anisotropy=max_anisotropy)[0]


def render(sheet_layout: layout.SheetLayout, raw) -> np.ndarray:
    """The composite the layout's filtergraph would build from ``raw``."""
    bg = int(sheet_layout.background, 16)
    frame = np.empty((layout.HEIGHT, layout.WIDTH, 3), np.uint8)
    frame[:] = (bg & 0xFF, (bg >> 8) & 0xFF, bg >> 16)
    turn = {90: cv2.ROTATE_90_CLOCKWISE, -90: cv2.ROTATE_90_COUNTERCLOCKWISE}
    for p in sheet_layout.placements:
        cx, cy, cw, ch = p.crop
        part = np.asarray(raw[p.role])[cy:cy + ch, cx:cx + cw]
        if p.rotate:
            part = cv2.rotate(part, turn[p.rotate])
        ox, oy, ow, oh = p.out
        frame[oy:oy + oh, ox:ox + ow] = cv2.resize(part, (ow, oh), interpolation=cv2.INTER_AREA)
    return frame


def check_image(youtube, rebuilt) -> np.ndarray:
    """YouTube | rebuilt over blend | difference, for judging the fit by eye."""
    blend = cv2.addWeighted(youtube, 0.5, rebuilt, 0.5, 0)
    diff = cv2.convertScaleAbs(cv2.absdiff(youtube, rebuilt), alpha=2.0)
    return np.vstack([np.hstack([youtube, rebuilt]), np.hstack([blend, diff])])


def _read(path):
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise SystemExit(f"cannot read {path}")
    return img


def main(argv=None) -> int:
    root = Path(__file__).resolve().parents[2]
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sheet", type=int, required=True)
    ap.add_argument("--youtube", nargs="+", required=True,
                    help="YouTube frames from across one recording of the sheet (globs expand)")
    ap.add_argument("--cameras", type=Path, required=True,
                    help="directory of <camera>.png frames, one per camera of the sheet")
    ap.add_argument("--note", default="", help="where the frames came from, kept in the file")
    ap.add_argument("--out", type=Path, help="default: the package's layouts/sheet{N}.json")
    ap.add_argument("--check", type=Path, help="default: out/cameras/layout-check/sheet{N}.jpg")
    ap.add_argument("--max-anisotropy", type=float,
                    help=f"one limit for every region, in place of {MAX_ANISOTROPY} "
                         f"(and STRETCH_ALLOWED's exceptions)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    paths = sorted({p for g in args.youtube for p in (glob.glob(g) or [g])})
    if len(paths) < MIN_YOUTUBE_FRAMES:
        raise SystemExit(f"need at least {MIN_YOUTUBE_FRAMES} YouTube frames, got {len(paths)}")
    frames = [_read(p) for p in paths]
    cameras = inventory.sheet_cameras(args.sheet)
    raw = {role: _read(args.cameras / f"{name}.png") for role, name in cameras.items()}
    # Players move and the paint does not: the median of frames from across a
    # game is close to an empty sheet, and matched ~50% more inliers than any one.
    composite = np.median(np.stack(frames), axis=0).astype(np.uint8)
    try:
        sheet_layout, matches = fit_matches(composite, raw, args.sheet, frames=frames,
                                            max_anisotropy=args.max_anisotropy)
    except LayoutFitError as e:
        print(f"sheet {args.sheet}: not fitted: {e}", file=sys.stderr)
        return 1

    out = args.out or layout.LAYOUTS / f"sheet{args.sheet}.json"
    record = {
        "note": args.note,
        "youtube_frames": [Path(p).name for p in paths],
        "cameras": cameras,
        "regions": {m.region: {k: v for k, v in asdict(m).items() if k != "region"}
                    for m in matches},
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    layout.save(sheet_layout, out, fit=record)
    check = args.check or root / "out" / "cameras" / "layout-check" / f"sheet{args.sheet}.jpg"
    check.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(check), check_image(composite, render(sheet_layout, raw)),
                [cv2.IMWRITE_JPEG_QUALITY, 90])
    print(f"sheet {args.sheet}: wrote {out} and {check}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
