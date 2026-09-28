"""The composite's two wide side views, and the far end's paint within them.

`geometry/layout.py` finds the overhead strip and ignores everything else;
everything else is two wide cameras, one at each end, each looking down the
sheet at the *other* end's house. Neither sees its own hog line -- that sits
about half a metre beneath the camera, out of frame -- so the throwing end's
hog line is watched by the camera at the target end, and which physical camera
that is alternates with the end, exactly as ``OTHER_HOUSE`` does.

They are worth the trouble because the overhead panel loses the throw before
the hog line on about 40% of deliveries: it is looking straight down at a
stone with the thrower and sweepers standing over it. From the end of the
sheet the sweepers are beside the stone, not on top of it.
"""

import dataclasses
import math
from dataclasses import dataclass

import numpy as np

from curling_score.geometry import constants as C

Rect = tuple[int, int, int, int]

# The minimum width worth calling a view; below this the strip is against the
# frame edge and there is no camera that side.
_MIN_VIEW_PX = 200


class SideViewError(RuntimeError):
    """A side view could not be located or read."""


def locate(layout, width: int, height: int) -> dict[str, Rect]:
    """The two wide views either side of the overhead strip."""
    x0 = min(layout.top[0], layout.bottom[0])
    x1 = max(layout.top[0] + layout.top[2], layout.bottom[0] + layout.bottom[2])
    views = {"left": (0, 0, x0, height), "right": (x1, 0, width - x1, height)}
    for name, (_x, _y, w, _h) in views.items():
        if w < _MIN_VIEW_PX:
            raise SideViewError(
                f"{name} view is only {w} px wide; the overhead strip runs to the edge"
            )
    return views


# Backboard to backboard is 45.720 m against 34.747 m tee to tee, so a tee sits
# 5.487 m from its own backboard and a camera on that wall is this far from the
# far tee. It sets only the curvature between the two fitted rows; the tripwire
# does not use it, and the depth scale at the hog line moves by under 5% across
# D = 35..45 m.
CAMERA_TO_FAR_TEE_M = C.TEE_TO_TEE_M + (C.BACKBOARD_TO_BACKBOARD_M - C.TEE_TO_TEE_M) / 2

# Measured on the clean plates of all ten of the club's side views (5 sheets,
# left and right), the annulus's greenness peak runs 6.44 to 13.27. 2.5 sits
# 2.6x below the weakest of those while staying clear of ice noise, and the
# hog line cannot be mistaken for paint at any threshold here: its greenness
# is negative, not merely small.
_GREEN_THRESHOLD = 2.5
# Fitted over both side views of all five club sheets, the tee lands at rows
# 410.2-489.7 of a 1080-row frame, i.e. 0.380-0.453. (0.33, 0.50) brackets that
# with about 0.05 spare either side.
_HOUSE_SEARCH = (0.33, 0.50)
# That was the 2025-26 season's framing. Sheets 2-4 were re-aimed over the
# 2026 summer, and their new positions are the framing from the 2026-27
# season on: on 2026-09-27 their tees sat at rows 305.8-368.6 (0.283-0.341),
# the 12-ft ring's far edge as high as row 290 (0.269). Sheet 2's ring fell
# wholly above row 356; sheets 3 and 4 were cut through and mispaired. Sheets
# 1 and 5 kept theirs (tees 463-494). A view the band above cannot fit is
# searched again from 0.20 -- 70 rows above the highest ring seen, and clear
# of the far wall's green stripe, which sits near row 150 where the house sits
# near 460. Only then: the rows above the house carry faint green on some
# right views (up to 2.3 on 24ysVAIxKg8 and AEqLTgM25Tc, against a threshold
# of 2.5), so widening every view's search would stake calibrations that are
# right, and already published, on that margin. Checked over 19 archived
# videos and 2026-09-27's five streams: every older view fits exactly as
# before (one lateral scale moves 0.13%).
_HOUSE_SEARCH_WIDE = (0.20, 0.50)
# Over the same ten views, the hog line sits 57-66 px below the annulus's last
# green edge. 140 is a little over twice the largest gap seen.
_HOG_SEARCH_PX = 140

# Measured across the club's five sheets: the far tee sits 78-90 rows above
# its hog line. A fit outside this found the wrong row, and a wrong row is far
# worse than no calibration -- every crossing after it is confidently
# mistimed.
PLAUSIBLE_ROWS = (60.0, 110.0)


@dataclass(frozen=True)
class SideView:
    """One wide side view, and where the far end's paint sits in it."""

    rect: Rect
    tee_row: float
    hog_row: float
    d_m: float = CAMERA_TO_FAR_TEE_M
    # Where the far house's centre sits on the tee row, in the view's own
    # columns, and how many pixels one metre across the sheet spans there. Both
    # come from `solve_lateral`. A view without them can still time a hog
    # crossing -- that is depth alone -- but cannot place anything across the
    # sheet.
    centre_col: float | None = None
    lat_px_per_m_at_tee: float | None = None
    # The painted centre line, col = a + b*row, traced below the house by
    # `solve_centre_line`. The ring fit's centre is a column only on the tee
    # row, and on VXU9's left camera it sits 4.5 px (3 cm) off the paint; the
    # line itself leans up to 13 px over the rows below the house. None falls
    # back to `centre_col` everywhere.
    centre_line: tuple[float, float] | None = None

    @property
    def has_lateral(self) -> bool:
        return self.centre_col is not None and self.lat_px_per_m_at_tee is not None

    def lateral_px_per_m(self, row: float) -> float:
        """Pixels per metre across the sheet on ``row``.

        The same 1/(d - x) law as ``stone_width_at``, so it too is a line
        through ``yh``.
        """
        self._need_lateral()
        _c, yh = self._map()
        return self.lat_px_per_m_at_tee * (row - yh) / (self.tee_row - yh)

    def centre_col_at(self, row: float) -> float:
        """The centre line's column on ``row``: the paint where it was traced,
        else the ring's centre."""
        if self.centre_line is not None:
            a, b = self.centre_line
            return a + b * row
        self._need_lateral()
        return self.centre_col

    def lateral_x(self, col: float, row: float) -> float:
        """Metres across the sheet of a point on the ice at (col, row)."""
        self._need_lateral()
        return (col - self.centre_col_at(row)) / self.lateral_px_per_m(row)

    def to_house(self, col: float, row: float) -> tuple[float, float]:
        """A point ON THE ICE, from view pixels to house metres.

        Timeline axes: +y up-sheet toward the thrower, which is toward this
        camera; +x the thrower's right, which is image right -- the view is not
        mirrored. Anything standing up must be read at its foot: a stone's body
        rises about 17 px above its footprint at the tee.
        """
        self._need_lateral()
        return self.lateral_x(col, row), self.metres_at(row)

    def to_image(self, x_m: float, y_m: float) -> tuple[float, float]:
        """The view pixel ``(col, row)`` of a point on the ice."""
        self._need_lateral()
        row = self.row_for(y_m)
        return self.centre_col_at(row) + x_m * self.lateral_px_per_m(row), row

    def _need_lateral(self):
        if not self.has_lateral:
            raise SideViewError("this view has no lateral calibration; "
                                "run solve_lateral first")

    @property
    def rows_tee_to_hog(self) -> float:
        return self.hog_row - self.tee_row

    def _map(self) -> tuple[float, float]:
        u = self.rows_tee_to_hog * (self.d_m - C.TEE_TO_HOGLINE_M) / C.TEE_TO_HOGLINE_M
        return self.d_m * u, self.tee_row - u

    def row_for(self, x_m: float) -> float:
        """The image row of a point ``x_m`` from the far tee, toward the camera."""
        c, yh = self._map()
        return yh + c / (self.d_m - x_m)

    def metres_at(self, row: float) -> float:
        """How far from the far tee a given image row is."""
        c, yh = self._map()
        return self.d_m - c / (row - yh)

    def stone_width_at(self, row: float, width_at_hog: float) -> float:
        """How many pixels wide a stone is when its edge sits on ``row``.

        The one lateral fact this class can honestly supply. It carries no
        lateral scale of its own -- ``row_for``/``metres_at`` are depth only --
        but a stone's apparent width scales with the same 1/(d - x) the row
        spacing does, so one measured width pins the rest.

        Substituting ``metres_at`` into that ratio collapses to a line through
        ``yh``::

            w(row) = width_at_hog * (d - TEE_TO_HOGLINE) * (row - yh) / c

        which is worth noticing: sizing a box by hand needs one multiply, not
        a segmentation model. A reviewer who says where a stone is has, by
        saying it, also said how big it is.
        """
        c, yh = self._map()
        return width_at_hog * (self.d_m - C.TEE_TO_HOGLINE_M) * (row - yh) / c


def _green_profile(plate, rect):
    x, y, w, h = rect
    view = np.asarray(plate, dtype=np.float32)[y:y + h, x:x + w]
    mid = view[:, int(w * 0.30):int(w * 0.70)]
    green = (mid[:, :, 1] - (mid[:, :, 0] + mid[:, :, 2]) / 2).mean(axis=1)
    return green, view.mean(axis=(1, 2))


def _crossings(sig, lo, hi, thresh):
    out = []
    for i in range(lo, min(hi, len(sig)) - 1):
        a, b = sig[i], sig[i + 1]
        if (a - thresh) * (b - thresh) < 0 and a != b:
            out.append(i + (thresh - a) / (b - a))
    return out


# TESTED AND REJECTED. Kept so the next person does not re-invent it.
#
# `_hog_row` returns the row whose dip against the ice above it is steepest.
# That is the paint's near edge only when the edge is sharp, and on
# VXU9xwmugRg's left view -- where the paint fades in over three rows -- it
# lands 2.77 px low, which at ~33 image rows a second is 0.084 s late on every
# crossing timed through that camera.
#
# `_hog_row_edge` below walks up from the dip to where luminance has recovered
# a fraction of the way to the ice, sub-pixel. On that one view it is a clear
# win: +0.45 px against +2.77.
#
# It does not survive a second video. Measured against 46 hand marks over four
# views (datasets/hogmarks, two videos), every fraction from 0.3 to 0.9:
#
#     view            argmin   edge@0.5   edge@0.7
#     VXU9 left        +2.77     +0.45      +1.04
#     VXU9 right       -0.14     -1.61      -1.28
#     AEqL left        -1.02     -1.84      -1.51
#     AEqL right       -0.86     -1.73      -1.38
#     worst             2.77      1.84       1.51
#     mean |err|        1.20      1.41       1.30
#
# So it trades a 2.77 px worst case for a 1.51 px one and makes the typical
# view slightly worse, on a sample of four. Three of the four views are already
# within ~1 px of truth with `_hog_row` -- about 0.03 s, well inside the 0.15 s
# a crossing is judged by -- and VXU9's left view is the outlier, not the rule.
# Changing the fitter would move all 225 calibrated views and the constants
# derived from them to buy an improvement this thin.
#
# What would settle it: marks on enough views to say whether VXU9 left is rare
# or whether a third of cameras fade their paint in. Until then `solve` keeps
# calling `_hog_row`.
HOG_EDGE_FRACTION = 0.5


def _hog_row_edge(lum, start, name, frac: float = HOG_EDGE_FRACTION):
    """The near edge of the hog line's paint, to sub-pixel precision.

    Not used by :func:`solve` -- see the note above for why. Kept because it is
    the obvious next idea and the evidence against it is worth more than the
    idea.
    """
    import numpy as np

    stop = min(start + _HOG_SEARCH_PX, len(lum) - 6)
    if stop <= start:
        raise SideViewError(f"{name}: no ice below the house to look for a hog line")
    seg = np.asarray(lum[start:stop], dtype=float)
    # Anchor on the same RELATIVE dip `_hog_row` uses -- each row against the
    # ice a few rows above it -- not on the raw minimum. The raw minimum is not
    # the paint: on AEqLTgM25Tc's left view something 46 px above the hog line
    # is darker than it, and anchoring there put the fit at row 541 against a
    # true 588. A relative dip asks "how much darker than the ice just above",
    # which is what a painted line on ice actually is.
    dips = [seg[i] - seg[max(0, i - 9):max(1, i - 4)].mean()
            for i in range(len(seg))]
    i_min = int(np.argmin(dips))
    above = seg[max(0, i_min - 25):max(1, i_min - 8)]
    if not len(above):
        raise SideViewError(f"{name}: no ice above the paint to measure against")
    ice = float(np.median(above))
    dark = float(seg[i_min])
    if ice - dark < 1.0:
        raise SideViewError(f"{name}: no luminance dip that could be a hog line")
    thr = ice - frac * (ice - dark)
    for i in range(i_min, 0, -1):
        if seg[i] <= thr < seg[i - 1]:
            span = seg[i - 1] - seg[i]
            step = (seg[i - 1] - thr) / span if span else 0.0
            return start + (i - 1) + step
    raise SideViewError(f"{name}: the paint's near edge was never crossed")


def _hog_row(lum, start, name):
    stop = min(start + _HOG_SEARCH_PX, len(lum) - 6)
    if stop <= start:
        raise SideViewError(f"{name}: no ice below the house to look for a hog line")
    dips = [lum[i] - lum[max(0, i - 9):i - 4].mean() for i in range(start, stop)]
    return start + int(np.argmin(dips))


def solve(plate, rect: Rect, name: str = "side") -> SideView:
    """Fit the far house's tee and hog rows from paint alone.

    The tee is fitted, not taken as the green annulus's centroid: perspective
    magnifies its near half and drags a centroid about 2 px toward the camera.
    The hog line is the darkest full-width row below the house.
    """
    green, lum = _green_profile(plate, rect)
    try:
        return _solve_within(green, lum, rect, name, _HOUSE_SEARCH)
    except SideViewError:
        return _solve_within(green, lum, rect, name, _HOUSE_SEARCH_WIDE)


# The most rows between two green runs of one ring: the 8-ft's interior, 20
# rows at the nearest framing measured (sheet 1, tee 494). Twice that keeps a
# ring whole, and leaves out green that is not the ring -- on sheet 3's right
# view, 2026-09-27 at 19:31, a faint patch 120 rows above it.
_RING_GAP_PX = 40


def _ring_rows(green, lo, last):
    """The rows the ring's green spans, as ``(first, last)``, or None.

    The strongest run above the threshold, with every run within
    ``_RING_GAP_PX`` of it, and of those, taken in turn.
    """
    runs, start = [], None
    for i in range(lo, last + 1):
        on = green[i] > _GREEN_THRESHOLD
        if on and start is None:
            start = i
        elif not on and start is not None:
            runs.append((start, i - 1))
            start = None
    if start is not None:
        runs.append((start, last))
    if not runs:
        return None
    k = max(range(len(runs)), key=lambda j: float(green[runs[j][0]:runs[j][1] + 1].max()))
    a, b = k, k
    while a > 0 and runs[a][0] - runs[a - 1][1] <= _RING_GAP_PX:
        a -= 1
    while b < len(runs) - 1 and runs[b + 1][0] - runs[b][1] <= _RING_GAP_PX:
        b += 1
    return runs[a][0], runs[b][1]


def _solve_within(green, lum, rect, name, search):
    h = rect[3]
    lo, hi = int(h * search[0]), int(h * search[1])
    last = min(hi, len(green)) - 1
    ring = _ring_rows(green, lo, last)
    if ring is None:
        raise SideViewError(f"{name}: found 0 green edges, need at least 2")
    if ring[0] <= lo or ring[1] >= last:
        # The rows searched start or stop inside the ring, so the outermost
        # crossings are not the 12-ft ring's and a fit from them is wrong by
        # a band's width -- 8 px of tee on sheet 3, 2026-09-27.
        raise SideViewError(
            f"{name}: the house runs past rows {lo}-{last} searched")
    edges = _crossings(green, ring[0] - 1, ring[1] + 2, _GREEN_THRESHOLD)
    if len(edges) < 2:
        raise SideViewError(f"{name}: found {len(edges)} green edges, need at least 2")

    # Fit from the outermost pair alone. A noisy plate can throw extra
    # crossings inside the annulus -- one of the club's ten views does,
    # yielding six -- and picking the 8-ft pair by position (edges[1],
    # edges[-2]) then mispairs silently: the fit still lands in the plausible
    # band, which is worse than failing. The inner edges were also the less
    # trustworthy pair regardless: paint bleed widens the band inward by
    # about 7 px against 4 px at the outer edge, so preferring them over the
    # outer pair is not a trade worth making even when they are genuine.
    seen = [edges[0], edges[-1]]
    want = np.array([-C.R_12FT_M, C.R_12FT_M])

    hog = _hog_row(lum, int(edges[-1]) + 12, name)

    def error(tee):
        v = SideView(rect=rect, tee_row=tee, hog_row=hog)
        return float(((np.array([v.row_for(x) for x in want]) - seen) ** 2).sum())

    tee = min(np.arange(hog - _HOG_SEARCH_PX, hog - 30, 0.05), key=error)
    view = SideView(rect=rect, tee_row=float(tee), hog_row=float(hog))
    if not PLAUSIBLE_ROWS[0] <= view.rows_tee_to_hog <= PLAUSIBLE_ROWS[1]:
        raise SideViewError(
            f"{name}: tee to hog measured {view.rows_tee_to_hog:.1f} px, "
            f"outside the {PLAUSIBLE_ROWS[0]:.0f}-{PLAUSIBLE_ROWS[1]:.0f} px "
            f"every sheet falls in -- the fit found the wrong row")
    return view



# The lateral fit, measured on VXU9's two views on 2026-09-23. Along the tee row
# bare ice reads -6..+5 in greenness while the near band peaks ~22 and the far
# band only ~10, so a fixed threshold finds noise: each band is found above a
# low floor, then its edges are placed at half its own peak.
_BAND_FLOOR = 5.0
_BAND_MIN_PX = 10
_BAND_SMOOTH_PX = 7
# Rows either side of the tee that are read. The tee line's own rows (within
# 1.5 of it) are skipped; each row is corrected for the chord it crosses the
# ring on, which is shorter than the diameter away from the tee.
_LATERAL_ROWS = 4
_LATERAL_MIN_ROWS = 3
# The 8-ft span over the 12-ft span, against the chords' own ratio. Paint bleed
# moves it by a few hundredths; a mispaired band moves it by far more.
_RATIO_TOL = 0.08
# Pixels per metre across the sheet at the far tee. The club's views measured
# 128-149 (Phase 0) and the ring spans ~480-555 px of an ~810 px view; this is
# wide enough for any sheet's framing and narrow enough to refuse a fit that
# took one band for both.
PLAUSIBLE_LAT_PX_PER_M = (90.0, 220.0)


def _bands(prof):
    """Every green band along one row, left to right, as (start, stop)."""
    runs, start = [], None
    for i, on in enumerate(np.append(prof > _BAND_FLOOR, False)):
        if on and start is None:
            start = i
        elif not on and start is not None:
            runs.append((start, i))
            start = None
    return [r for r in runs if r[1] - r[0] >= _BAND_MIN_PX]


def _ring_chords(prof, runs, a8_over_a12):
    """The 12-ft and 8-ft chords this row cuts, as ``(l12, l8, r8, r12)``, or
    None when no two bands make them.

    The ring's two bands are next to each other -- nothing green lies inside
    the 8-ft -- so only neighbouring runs are paired, and the pair whose inner
    span over outer span is nearest the chords' own ratio wins. Not the two
    longest: the neighbouring sheets' rings show at the view's edges, and in
    sheet 2's wider 2026-27 framing they are longer than this ring's bands.
    """
    best = None
    for a, b in zip(runs, runs[1:]):
        left, right = _band_edges(prof, a), _band_edges(prof, b)
        if left is None or right is None:
            continue
        (l12, l8), (r8, r12) = left, right
        outer, inner = r12 - l12, r8 - l8
        if outer <= 0 or inner <= 0:
            continue
        miss = abs(inner / outer - a8_over_a12)
        if miss <= _RATIO_TOL and (best is None or miss < best[0]):
            best = (miss, (l12, l8, r8, r12))
    return None if best is None else best[1]


def _band_edges(prof, run):
    a, b = run
    half = float(prof[a:b].max()) / 2
    xs = _crossings(prof, max(0, a - 30), min(len(prof), b + 30), half)
    return (xs[0], xs[-1]) if len(xs) >= 2 else None


def solve_lateral(plate, view: SideView, name: str = "side") -> SideView:
    """Fit the far house's centre column and lateral scale from paint alone.

    A row is a line of constant depth, so where it cuts the green annulus its
    two bands' outer edges are the 12-ft ring's chord at that depth and their
    inner edges the 8-ft's. Summing the two spans cancels paint erosion, which
    narrows the outer span by as much as it widens the inner. Each row is
    divided by its own chords and by its own lateral factor before the median,
    so rows off the tee do not bias the scale low.
    """
    x0, y0, w, h = view.rect
    img = np.asarray(plate, dtype=np.float32)[y0:y0 + h, x0:x0 + w]
    green = img[:, :, 1] - (img[:, :, 0] + img[:, :, 2]) / 2
    kernel = np.ones(_BAND_SMOOTH_PX) / _BAND_SMOOTH_PX
    _c, yh = view._map()
    lats, centres = [], []
    t = int(round(view.tee_row))
    for r in range(t - _LATERAL_ROWS, t + _LATERAL_ROWS + 1):
        if abs(r - view.tee_row) < 1.5 or not 0 <= r < h:
            continue
        prof = np.convolve(green[r], kernel, mode="same")
        y = view.metres_at(r)
        a12 = math.sqrt(C.R_12FT_M ** 2 - y ** 2)
        a8 = math.sqrt(C.R_8FT_M ** 2 - y ** 2)
        chords = _ring_chords(prof, _bands(prof), a8 / a12)
        if chords is None:
            continue
        l12, l8, r8, r12 = chords
        outer, inner = r12 - l12, r8 - l8
        factor = (r - yh) / (view.tee_row - yh)
        lats.append((outer + inner) / (2 * (a12 + a8)) / factor)
        centres.append((l12 + l8 + r8 + r12) / 4)
    if len(lats) < _LATERAL_MIN_ROWS:
        raise SideViewError(
            f"{name}: the ring's two sides were read on only {len(lats)} rows "
            f"near the tee, need {_LATERAL_MIN_ROWS}")
    lat, centre = float(np.median(lats)), float(np.median(centres))
    if not PLAUSIBLE_LAT_PX_PER_M[0] <= lat <= PLAUSIBLE_LAT_PX_PER_M[1]:
        raise SideViewError(
            f"{name}: {lat:.1f} px/m across the sheet is outside the "
            f"{PLAUSIBLE_LAT_PX_PER_M[0]:.0f}-{PLAUSIBLE_LAT_PX_PER_M[1]:.0f} "
            f"any framing gives -- the fit paired the wrong bands")
    if not 0.2 * w <= centre <= 0.8 * w:
        raise SideViewError(f"{name}: house centre at column {centre:.0f} of {w}")
    return dataclasses.replace(view, centre_col=centre, lat_px_per_m_at_tee=lat)


# The painted centre line below the house. Measured 2026-09-24 on the club's
# sheets 1, 2, 3 and 5: a straight image line to under 1 px (the side cameras
# have no lens distortion to bend it), leaning up to 13 px over the rows below
# the house, and interrupted on sheet 3 by a centre-ice logo. Traced twice:
# widely around the ring's centre, then narrowly around that first fit.
_CL_BELOW_TEE_ROWS = 40
_CL_STEP_ROWS = 2
_CL_WIDE_PX = 16
_CL_NARROW_PX = 4
_CL_MARGIN_PX = 12
_CL_MIN_DIP = 3.0
_CL_OUTLIER_PX = 1.5
_CL_MIN_ROWS = 100
_CL_MAX_RMS_PX = 1.5
_CL_MAX_SLOPE = 0.03


def _trace_dips(lum, rows, guide, half):
    """On each row, the darkest sub-pixel column within ``half`` of ``guide(row)``."""
    rs, cs = [], []
    w = lum.shape[1]
    kernel = np.ones(21) / 21
    for r in rows:
        g = int(round(guide(r)))
        lo, hi = g - half - _CL_MARGIN_PX, g + half + _CL_MARGIN_PX + 1
        if lo < 0 or hi > w:
            continue
        band = lum[r - 1:r + 2, lo:hi].mean(axis=0)
        seg = (band - np.convolve(band, kernel, mode="same"))[_CL_MARGIN_PX:-_CL_MARGIN_PX]
        i = int(np.argmin(seg))
        if seg[i] > -_CL_MIN_DIP or i in (0, len(seg) - 1):
            continue
        a, b, c = seg[i - 1], seg[i], seg[i + 1]
        den = a - 2 * b + c
        rs.append(r)
        cs.append(lo + _CL_MARGIN_PX + i + (0.5 * (a - c) / den if den else 0.0))
    return np.array(rs, float), np.array(cs, float)


def _robust_line(rs, cs):
    keep = np.ones(len(rs), bool)
    q = None
    for _ in range(5):
        if keep.sum() < 2:
            return None, keep
        q = np.polyfit(rs[keep], cs[keep], 1)
        keep = np.abs(cs - np.polyval(q, rs)) < _CL_OUTLIER_PX
    return q, keep


def solve_centre_line(plate, view: SideView, name: str = "side") -> SideView:
    """Trace the painted centre line below the house and fit it as a line."""
    view._need_lateral()
    x0, y0, w, h = view.rect
    lum = np.asarray(plate, dtype=np.float32)[y0:y0 + h, x0:x0 + w].mean(axis=2)
    rows = np.arange(int(view.tee_row) + _CL_BELOW_TEE_ROWS, h - 2, _CL_STEP_ROWS)
    rs, cs = _trace_dips(lum, rows, lambda r: view.centre_col, _CL_WIDE_PX)
    q, _ = _robust_line(rs, cs)
    if q is None:
        raise SideViewError(f"{name}: no painted centre line near column {view.centre_col:.0f}")
    rs, cs = _trace_dips(lum, rows, lambda r: np.polyval(q, r), _CL_NARROW_PX)
    q, keep = _robust_line(rs, cs)
    if q is None or keep.sum() < _CL_MIN_ROWS:
        raise SideViewError(f"{name}: the centre line was traced on only "
                            f"{int(keep.sum())} rows, need {_CL_MIN_ROWS}")
    rms = float(np.std(cs[keep] - np.polyval(q, rs[keep])))
    if rms > _CL_MAX_RMS_PX or abs(q[0]) > _CL_MAX_SLOPE:
        raise SideViewError(f"{name}: centre line fit {rms:.2f} px RMS, slope {q[0]:.4f}")
    return dataclasses.replace(view, centre_line=(float(q[1]), float(q[0])))
