"""Calibrate an end camera: one mounted high at a house's own end, looking back at it.

Cincinnati's side cameras each sit above their own hog line and look back
down at their own house, hack at the top of the frame and hog line along the
bottom. That is the geometry `SideView` already describes -- metres from the
tee toward the camera, row = yh + c / (d - x) -- but `sideview.solve` fits only
two rows and fixes ``d`` at the far wall of a long camera, and its search bands
are Granite's. A steeply pitched camera needs ``d`` fitted; here it is.

The whole ring is in view and large, so the fit uses it all: every edge point
of the 12-foot band, and of the inner edge the club paints (the 8-foot, or the
4-foot where an 8-to-4 band is painted dark), at their known radii. The hog
line's paint gives ``hog_row`` directly, and the painted centre line gives the
lateral origin from the house down to the hog line, where the ring cannot.
"""

from dataclasses import dataclass

import cv2
import numpy as np

from curling_score.geometry import constants as C
from curling_score.geometry.sideview import SideView, SideViewError, _robust_line, _trace_dips

# A ring edge counts as a point on the ring within this; letters painted into
# the band make the rest.
_HUBER_M = 0.03
_ANGLE_BINS = 720
# The ring as an end camera sees it is about three times wider than tall; the
# angular sweep is done on a circle-ish rescale of it (at least this flat).
_SQUASH = 3.0
# A fit whose 12-foot edge sits further out than this, at the median, is not one.
MAX_RING_RESIDUAL_M = 0.05
# On a long camera one row is ~9 cm along the sheet at the far tee (Swift
# Current), so a pixel of paint eroded by the threshold is that much; across
# the sheet a pixel is under 1 cm, and the sidelines check that scale.
MAX_RING_RESIDUAL_LONG_M = 0.15
_CL_MIN_ROWS = 60
_CL_HALVES = (40, 12, 4)


@dataclass(frozen=True)
class EndPaint:
    """The house as an end camera sees it: the HSV bands of its paint and the
    radius of the paint's inner edge. ``ring`` is the 12-foot band's colour,
    which locates the house; ``also`` is other paint of the house that may be
    dark or grey on video, kept to the ring's box."""

    ring: tuple
    inner_radius_m: float
    also: tuple = ()
    # The hog line's paint: "red", "blue", or "dark" for a grey-black line. And where
    # the centre line comes from: its "paint", or the midpoint of the two
    # "sidelines" where it is not painted, as at Swift Current.
    hog: str = "red"
    centre: str = "paint"
    # The fewest rows a painted centre line must be traced on: fewer where
    # logos on the ice break up a faint line, as at Martensville.
    centre_min_rows: int = _CL_MIN_ROWS


@dataclass(frozen=True)
class EndFit:
    view: SideView
    ring_residual_m: float  # median, 12-foot edge
    inner_residual_m: float
    centre_rows: int


def _mask(view, bands):
    hsv = cv2.cvtColor(view, cv2.COLOR_BGR2HSV)
    m = np.zeros(hsv.shape[:2], np.uint8)
    for lo, hi in bands:
        m |= cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8))
    return m


def ring_edges(view, paint: EndPaint):
    """The outer and inner edge of the house's paint, one point per angle."""
    colour = _mask(view, paint.ring)
    box_src = cv2.morphologyEx(colour, cv2.MORPH_CLOSE,
                               cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (25, 25)))
    n, lab, st, _ = cv2.connectedComponentsWithStats(box_src, 8)
    if n < 2:
        raise SideViewError("no house paint in view")
    k = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
    x0, y0, w0, h0 = st[k, :4]
    m = colour | (_mask(view, paint.also) if paint.also else 0)
    box = np.zeros_like(m)
    box[max(0, y0 - 6):y0 + h0 + 6, max(0, x0 - 6):x0 + w0 + 6] = 255
    m = cv2.morphologyEx(m & box, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    n, lab, st, _ = cv2.connectedComponentsWithStats(m, 8)
    if n < 2:
        raise SideViewError("no house paint in view")
    ys, xs = np.nonzero(lab == 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA])))
    cx, cy = xs.mean(), ys.mean()
    # A long camera's far house is far flatter than an end camera's own:
    # about 12:1 at Swift Current. Sweep it on a circle of its own shape.
    squash = max(_SQUASH, float(np.ptp(xs)) / max(float(np.ptp(ys)), 1.0))
    ang = np.arctan2((ys - cy) * squash, xs - cx)
    rad = np.hypot(xs - cx, (ys - cy) * squash)
    bins = ((ang + np.pi) / (2 * np.pi) * _ANGLE_BINS).astype(int) % _ANGLE_BINS
    order = np.lexsort((rad, bins))
    b_sorted = bins[order]
    first = np.r_[True, b_sorted[1:] != b_sorted[:-1]]
    last = np.r_[b_sorted[1:] != b_sorted[:-1], True]
    inner = np.stack([xs[order[first]], ys[order[first]]], axis=1).astype(float)
    outer = np.stack([xs[order[last]], ys[order[last]]], axis=1).astype(float)
    return outer, inner


def hog_row(view, below_row: int, colour: str = "red") -> float:
    """The hog line's paint: the reddest (or bluest) row below the house, sub-pixel."""
    v = view.astype(np.float32)
    w = v.shape[1]
    mid = v[:, int(w * 0.3):int(w * 0.7)]
    ch = 2 if colour == "red" else 0  # BGR
    red = (mid[:, :, ch] - (mid[:, :, 1] + mid[:, :, 2 - ch]) / 2).mean(axis=1)[below_row:]
    if len(red) < 3:
        raise SideViewError("no ice below the house to look for a hog line")
    i = int(np.argmax(red))
    if 0 < i < len(red) - 1:
        a, b, c = red[i - 1], red[i], red[i + 1]
        den = a - 2 * b + c
        if den:
            i = i + 0.5 * (a - c) / den
    return below_row + float(i)


def dark_hog_row(view, below_row: int) -> float:
    """A dark hog line: the darkest row in the 200 below the house, sub-pixel."""
    v = view.astype(np.float32)
    w = v.shape[1]
    lum = v[below_row:below_row + 200, int(w * 0.3):int(w * 0.7)].mean(axis=(1, 2))
    if len(lum) < 3:
        raise SideViewError("no ice below the house to look for a hog line")
    i = int(np.argmin(lum))
    if 0 < i < len(lum) - 1:
        a, b, c = lum[i - 1], lum[i], lum[i + 1]
        den = a - 2 * b + c
        if den:
            i = i + 0.5 * (a - c) / den
    return below_row + float(i)


def _sideline_centre(lum, p, hog, rect, rows, name):
    """The centre line as the midpoint of the two sidelines, each traced from
    the guess the ring fit ``p`` makes of it. A straight line on the ice is
    straight in the image, and so is the midpoint of two."""
    v = _view(rect, hog, p[0], p[1], p[2], p[3], p[4])
    lines = []
    for sign in (-1, 1):
        def guide(r, sign=sign):
            return v.centre_col_at(r) + sign * C.SHEET_WIDTH_M / 2 * v.lateral_px_per_m(r)
        q = None
        for half in _CL_HALVES:
            rs, cs = _trace_dips(lum, rows, guide if q is None else (lambda rr, q=q: np.polyval(q, rr)), half)
            q2, keep = _robust_line(rs, cs)
            if q2 is None:
                raise SideViewError(f"{name}: no {'left' if sign < 0 else 'right'} sideline")
            q = q2
        if keep.sum() < _CL_MIN_ROWS:
            raise SideViewError(f"{name}: a sideline was traced on only {int(keep.sum())} rows")
        lines.append((q, int(keep.sum())))
    (ql, nl), (qr, nr) = lines
    q = (ql + qr) / 2
    return (float(q[1]), float(q[0])), min(nl, nr)


def _view(rect, hog, tee, d, a, b, lat, end_camera=True):
    tee, hog, d, a, b, lat = (float(v) for v in (tee, hog, d, a, b, lat))
    return SideView(rect=tuple(int(v) for v in rect), tee_row=tee, hog_row=hog, d_m=d,
                    centre_col=a + b * tee, lat_px_per_m_at_tee=lat, centre_line=(a, b),
                    end_camera=end_camera)


def _residuals(p, pts, radii, hog, rect, line=None):
    tee, d, lat = p[0], p[1], p[-1]
    a, b = line if line is not None else (p[2], p[3])
    if d <= C.TEE_TO_HOGLINE_M + 0.2 or lat <= 0:
        return np.full(len(pts), 10.0)
    v = _view(rect, hog, tee, d, a, b, lat)
    c, yh = v._map()
    cols, rows = pts[:, 0], pts[:, 1]
    if np.any(rows <= yh) or tee <= yh:
        return np.full(len(pts), 10.0)
    y = d - c / (rows - yh)
    x = (cols - (a + b * rows)) / (lat * (rows - yh) / (tee - yh))
    return np.hypot(x, y) - radii


def _lm(f, p, iters=200):
    """Levenberg-Marquardt with Huber weights, numeric Jacobian. Few parameters."""
    lam = 1e-2
    r = f(p)
    for _ in range(iters):
        w = np.where(np.abs(r) < _HUBER_M, 1.0, _HUBER_M / np.maximum(np.abs(r), 1e-12))
        J = np.empty((len(r), len(p)))
        for j in range(len(p)):
            h = 1e-4 * max(1.0, abs(p[j]))
            q = p.copy()
            q[j] += h
            J[:, j] = (f(q) - r) / h
        A = J.T @ (J * w[:, None])
        g = J.T @ (w * r)
        step = np.linalg.lstsq(A + lam * np.diag(np.diag(A)) + 1e-12 * np.eye(len(p)), -g, rcond=None)[0]
        q = p + step
        rq = f(q)
        if (w * rq ** 2).sum() < (w * r ** 2).sum():
            p, r, lam = q, rq, lam / 3
            if np.abs(step).max() < 1e-7:
                break
        else:
            lam *= 4
    return p, r


def solve(plate, rect, paint: EndPaint, name: str = "end", end_camera: bool = True) -> EndFit:
    """Fit a `SideView` to an end camera's view of its own house -- or, with
    ``end_camera`` False, to a long camera's view of the far house, which is
    the same geometry from further away."""
    x0, y0, w, h = rect
    view = np.ascontiguousarray(np.asarray(plate)[y0:y0 + h, x0:x0 + w])
    outer, inner = ring_edges(view, paint)
    below = int(outer[:, 1].max()) + 20
    hog = dark_hog_row(view, below) if paint.hog == "dark" else hog_row(view, below, paint.hog)
    pts = np.vstack([outer, inner])
    radii = np.r_[np.full(len(outer), C.R_12FT_M), np.full(len(inner), paint.inner_radius_m)]
    xl, xr = outer[:, 0].min(), outer[:, 0].max()
    tee0 = (outer[np.abs(outer[:, 0] - xl) < 3, 1].mean() + outer[np.abs(outer[:, 0] - xr) < 3, 1].mean()) / 2
    lat0 = (xr - xl) / (2 * C.R_12FT_M)

    # 1. The rings alone, from a few camera distances.
    best = None
    for d0 in (8.0, 10.0, 15.0, 25.0, 40.0):
        p, r = _lm(lambda q: _residuals(q, pts, radii, hog, rect),
                   np.array([tee0, d0, (xl + xr) / 2, 0.0, lat0]))
        cost = float(np.median(np.abs(r)))
        if best is None or cost < best[0]:
            best = (cost, p)
    p = best[1]

    # 2. The painted centre line, from the house down to the hog line, which
    # the rings cannot reach: their fit leans 0.06 px a row off it on
    # Cincinnati's blue camera, 25 px by the hog line.
    lum = view.astype(np.float32).mean(axis=2)
    if paint.centre == "sidelines":
        top = int(outer[:, 1].min()) - 30
        line, centre_rows = _sideline_centre(lum, p, hog, rect, np.arange(top, int(hog) + 60), name)
        return _finish(view, rect, pts, radii, outer, hog, p, line, centre_rows, name, end_camera)
    # A long camera sees the far house small, with few rows to its hog line,
    # but the line runs on toward the camera: trace it the rest of the view.
    last = int(hog) - 6 if end_camera else h - 2
    rows = np.arange(int(outer[:, 1].max()) + 10, last, 2)
    q = (p[3], p[2])
    keep = np.zeros(0, bool)
    for half in _CL_HALVES:
        rs, cs = _trace_dips(lum, rows, lambda rr: np.polyval(q, rr), half)
        q2, keep = _robust_line(rs, cs)
        if q2 is None:
            raise SideViewError(f"{name}: no painted centre line below the house")
        q = q2
    if keep.sum() < paint.centre_min_rows:
        raise SideViewError(f"{name}: the centre line was traced on only {int(keep.sum())} rows")
    line = (float(q[1]), float(q[0]))
    return _finish(view, rect, pts, radii, outer, hog, p, line, int(keep.sum()), name, end_camera)


def _finish(view, rect, pts, radii, outer, hog, p, line, centre_rows, name, end_camera):
    # 3. The rings again, the centre line held to the paint.
    t, r = _lm(lambda tt: _residuals(tt, pts, radii, hog, rect, line), np.array([p[0], p[1], p[4]]))
    ring = float(np.median(np.abs(r[:len(outer)])))
    if ring > (MAX_RING_RESIDUAL_M if end_camera else MAX_RING_RESIDUAL_LONG_M):
        raise SideViewError(f"{name}: the 12-foot ring fits only to {ring * 100:.1f} cm")
    v = _view(rect, hog, t[0], t[1], line[0], line[1], t[2], end_camera)
    return EndFit(view=v, ring_residual_m=ring, inner_residual_m=float(np.median(np.abs(r[len(outer):]))),
                  centre_rows=centre_rows)
