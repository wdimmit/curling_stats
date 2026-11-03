"""Tie two long cameras together through a logo both of them see.

Each long camera is fitted on its own far house (`endcam`), and from there
out to mid-sheet its model is an extrapolation. At Swift Current the left
camera's ran 13-17% large across the sheet by mid-ice and put the centre-ice
logo 1.6 m short, while the right camera's held. A logo painted on the ice
that both cameras see is a common ruler with no clock in it: whatever its
size, the two cameras must put its outline in the same place. So both are
refitted together -- tee row, distance and lateral scale each, the hog rows
and centre lines held to their paint -- on their own rings plus the logo's
outline agreeing between them. Nothing assumes either camera is the right one.

The cameras face each other: one's +x is the other's -x, and one's distance
from its far tee is the tee-to-tee distance less the other's.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

import cv2
import numpy as np

from curling_score.geometry import constants as C
from curling_score.geometry import endcam
from curling_score.geometry.sideview import SideView, SideViewError

# Each outline is thinned to this many points: the distances are all-pairs.
_OUTLINE_N = 300
# An outline point off the fitted ellipse by more than this is not the outline.
_ELLIPSE_TOL_PX = 2.0
_MIN_OUTLINE = 200
# A tie whose logo outlines still sit this far apart, at the median, is not one.
MAX_LOGO_RESIDUAL_M = 0.12


@dataclass(frozen=True)
class Camera:
    """One long camera to tie: its own fit, its house's edge points at their
    radii, and the logo's outline in view pixels."""

    view: SideView
    ring_pts: np.ndarray
    ring_radii: np.ndarray
    outline: np.ndarray


@dataclass(frozen=True)
class Tie:
    views: tuple[SideView, SideView]
    logo_before_m: float  # median outline disagreement, own fits
    logo_after_m: float
    ring_m: tuple[float, float]


def logo_outline(view_img, rows: tuple[int, int]) -> np.ndarray:
    """The logo's outer outline in ``rows`` of a long camera's plate, in view
    pixels: the outermost non-ice pixel down each column and along each row,
    kept where it lies on one ellipse."""
    r0, r1 = rows
    crop = np.asarray(view_img)[r0:r1]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    lum = crop.astype(np.float32).mean(axis=2)
    ink = ((hsv[:, :, 1] > 60) | (lum < np.median(lum) - 30)).astype(np.uint8)
    ink = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(cv2.dilate(ink, np.ones((9, 9), np.uint8)), 8)
    if n < 2:
        raise SideViewError("no logo in the rows given")
    ink = ink & (lab == 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA])))
    pts = []
    for col in range(ink.shape[1]):
        rs = np.nonzero(ink[:, col])[0]
        if len(rs) >= 2:
            pts += [(col, rs[0]), (col, rs[-1])]
    for row in range(ink.shape[0]):
        cs = np.nonzero(ink[row])[0]
        if len(cs) >= 2:
            pts += [(cs[0], row), (cs[-1], row)]
    p = np.array(pts, float)
    for _ in range(4):
        if len(p) < _MIN_OUTLINE:
            break
        (ex, ey), (ea, eb), ang = cv2.fitEllipse(p.astype(np.float32))
        th = np.deg2rad(ang)
        dx, dy = p[:, 0] - ex, p[:, 1] - ey
        u = dx * np.cos(th) + dy * np.sin(th)
        q = -dx * np.sin(th) + dy * np.cos(th)
        res = (np.hypot(u / (ea / 2), q / (eb / 2)) - 1) * min(ea, eb) / 2
        p = p[np.abs(res) < _ELLIPSE_TOL_PX]
    # Points on the crop's own edges are where it was cut, not the outline.
    p = p[(p[:, 1] > 1) & (p[:, 1] < len(crop) - 2) & (p[:, 0] > 1) & (p[:, 0] < crop.shape[1] - 2)]
    if len(p) < _MIN_OUTLINE:
        raise SideViewError(f"the logo's outline gave only {len(p)} points")
    return p + [0, r0]


def _thin(p, n=_OUTLINE_N):
    return p if len(p) <= n else p[np.linspace(0, len(p) - 1, n).astype(int)]


def _view(v: SideView, q) -> SideView | None:
    tee, d, lat = (float(x) for x in q)
    if d <= C.TEE_TO_HOGLINE_M + 0.2 or lat <= 0:
        return None
    a, b = v.centre_line
    out = replace(v, tee_row=tee, d_m=d, lat_px_per_m_at_tee=lat, centre_col=a + b * tee)
    if tee <= out._map()[1]:
        return None
    return out


def _ice(v: SideView, px) -> np.ndarray | None:
    """View pixels to the ice, vectorised: (x, y) from the far tee."""
    c, yh = v._map()
    cols, rows = px[:, 0], px[:, 1]
    if np.any(rows <= yh):
        return None
    a, b = v.centre_line
    y = v.d_m - c / (rows - yh)
    x = (cols - (a + b * rows)) / (v.lat_px_per_m_at_tee * (rows - yh) / (v.tee_row - yh))
    return np.c_[x, y]


def _facing(p):
    """The other camera's frame."""
    return np.c_[-p[:, 0], C.TEE_TO_TEE_M - p[:, 1]]


def _gap(a, b):
    return np.sqrt(((a[:, None, :] - b[None, :, :]) ** 2).sum(-1)).min(axis=1)


def _logo_gap(va, vb, oa, ob):
    """Each outline's distance to the other's, on the ice in ``va``'s frame."""
    ia, ib = _ice(va, oa), _ice(vb, ob)
    if ia is None or ib is None:
        return None
    ib = _facing(ib)
    return np.r_[_gap(ia, ib), _gap(ib, ia)]


def _ring(v, cam):
    ice = _ice(v, cam.ring_pts)
    if ice is None:
        return None
    return np.hypot(ice[:, 0], ice[:, 1]) - cam.ring_radii


def tie(a: Camera, b: Camera) -> Tie:
    """Both cameras refitted on their rings and the logo's outline agreeing."""
    oa, ob = _thin(a.outline), _thin(b.outline)
    n = len(a.ring_pts) + len(b.ring_pts) + len(oa) + len(ob)

    def residuals(p):
        va, vb = _view(a.view, p[:3]), _view(b.view, p[3:])
        if va is None or vb is None:
            return np.full(n, 10.0)
        ra, rb, g = _ring(va, a), _ring(vb, b), _logo_gap(va, vb, oa, ob)
        if ra is None or rb is None or g is None:
            return np.full(n, 10.0)
        return np.r_[ra, rb, g]

    before = _logo_gap(a.view, b.view, oa, ob)
    best = None
    for da, db in ((0, 0), (6, 0), (0, 6), (6, 6), (-3, -3)):
        p0 = np.array([a.view.tee_row, a.view.d_m + da, a.view.lat_px_per_m_at_tee,
                       b.view.tee_row, b.view.d_m + db, b.view.lat_px_per_m_at_tee])
        p, r = endcam._lm(residuals, p0)
        cost = float(np.median(np.abs(r)))
        if best is None or cost < best[0]:
            best = (cost, p)
    p = best[1]
    va, vb = _view(a.view, p[:3]), _view(b.view, p[3:])
    after = _logo_gap(va, vb, oa, ob)
    return Tie(views=(va, vb), logo_before_m=float(np.median(before)) if before is not None else float("nan"),
               logo_after_m=float(np.median(after)),
               ring_m=(float(np.median(np.abs(_ring(va, a)))), float(np.median(np.abs(_ring(vb, b))))))
