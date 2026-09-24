"""Synthetic overhead house images, for deterministic geometry tests."""

import cv2
import numpy as np

from curling_score.geometry import constants as C

ICE = (235, 235, 235)
GREEN = (60, 150, 60)
BLUE = (140, 60, 60)
WHITE = (250, 250, 250)
LINE = (90, 90, 90)


def house_panel(w=297, h=514, cx=148.0, cy=150.0, px_per_m=75.0, noise=0.0, seed=0):
    """Render a top-down house the way the club's overhead camera sees it.

    ``px_per_m`` 75 puts the 12-foot ring at ~274 px across, matching the real
    panels (measured 242-290 px).
    """
    img = np.full((h, w, 3), ICE, dtype=np.uint8)
    r = lambda m: int(round(m * px_per_m))
    c = (int(round(cx)), int(round(cy)))

    cv2.circle(img, c, r(C.R_12FT_M), GREEN, -1, cv2.LINE_AA)
    cv2.circle(img, c, r(C.R_8FT_M), WHITE, -1, cv2.LINE_AA)
    cv2.circle(img, c, r(C.R_4FT_M), BLUE, -1, cv2.LINE_AA)
    cv2.circle(img, c, r(C.R_BUTTON_M), WHITE, -1, cv2.LINE_AA)

    # Tee line (horizontal, through the tee) and centre line (vertical).
    cv2.line(img, (0, c[1]), (w, c[1]), LINE, 1, cv2.LINE_AA)
    cv2.line(img, (c[0], 0), (c[0], h), LINE, 1, cv2.LINE_AA)

    if noise:
        rng = np.random.default_rng(seed)
        img = np.clip(
            img.astype(np.float32) + rng.normal(0, noise, img.shape), 0, 255
        ).astype(np.uint8)
    return img


def with_stone(img, cx, cy, color="red", radius=10):
    """Paint a stone handle on a panel. ``color`` is 'red' or 'yellow'."""
    bgr = (40, 40, 210) if color == "red" else (40, 210, 230)
    out = img.copy()
    cv2.circle(out, (int(cx), int(cy)), radius, (120, 120, 120), -1, cv2.LINE_AA)
    cv2.circle(out, (int(cx), int(cy)), radius - 3, bgr, -1, cv2.LINE_AA)
    return out


def break_ring(img, angle_deg=45.0, width_deg=25.0, cx=148.0, cy=150.0):
    """Paint over a wedge of the house, as a player or sweeper does.

    A gap turns the green annulus from a closed ring into a C-shape, which
    changes its contour topology - the case that broke the first ring fitter.
    """
    out = img.copy()
    a0 = angle_deg - width_deg / 2.0
    a1 = angle_deg + width_deg / 2.0
    cv2.ellipse(out, (int(cx), int(cy)), (400, 400), 0.0, a0, a1, ICE, -1)
    return out


SIDE_ICE = (238, 238, 236)
# The ice line is neutral-to-bluish, not warm: measured -5.5 to -1.9 greenness
# (G - (R+B)/2) on the club's own plates where the line is correctly located.
# This gives -6, matching that measurement -- a warm grey here previously read
# as faintly green-positive and was mistaken for paint by the fitter.
#
# Its luminance dip is real footage's too: about 25 levels below the ice
# (238 -> ~212), not the ~93-level dip an earlier, darker version of this
# constant gave. longview's granite-body threshold (_BODY_DARKER_THAN_ICE =
# 45) is set deliberately above the real 25-level dip so the line is never
# mistaken for a stone's own edge; a fixture line darker than that threshold
# would trigger exactly the confusion the threshold exists to avoid, on a
# defect that does not exist on real footage.
SIDE_LINE = (208, 208, 220)
GREEN_PAINT = (70, 150, 70)


def side_view(tee_row=430.0, hog_row=520.0, w=810, h=1080, d_m=40.233,
              noise=0.0, seed=0):
    """An oblique view down the sheet at the far end's house.

    Returns **RGB**, unlike ``house_panel`` above, which is BGR for cv2. These
    feed ``detect/longview.py``, which reads frames ffmpeg decoded as rgb24,
    and its colour mask takes channel 0 as red. ``sideview.solve`` is unaffected
    either way -- greenness and luminance are channel-order agnostic.

    Renders only what ``sideview.solve`` reads: the green 12-ft annulus as two
    bands either side of the tee, and the hog line as a darker row. Positions
    come from the same perspective map the fit inverts, so a correct fit
    recovers ``tee_row`` and ``hog_row`` exactly.
    """
    img = np.full((h, w, 3), SIDE_ICE, dtype=np.uint8)
    rows = hog_row - tee_row
    u = rows * (d_m - C.TEE_TO_HOGLINE_M) / C.TEE_TO_HOGLINE_M
    c, yh = d_m * u, tee_row - u
    row_for = lambda x: yh + c / (d_m - x)

    # the annulus: 1.219..1.829 m either side of the tee
    for lo, hi in ((-C.R_12FT_M, -C.R_8FT_M), (C.R_8FT_M, C.R_12FT_M)):
        a, b = sorted((int(round(row_for(lo))), int(round(row_for(hi)))))
        img[a:b + 1, int(w * 0.12):int(w * 0.88)] = GREEN_PAINT

    r = int(round(hog_row))
    img[r - 1:r + 2] = SIDE_LINE

    if noise:
        rng = np.random.default_rng(seed)
        img = np.clip(img.astype(np.float32) + rng.normal(0, noise, img.shape),
                      0, 255).astype(np.uint8)
    return img



def side_view_house(tee_row=430.0, hog_row=520.0, centre_col=390.0,
                    lat_px_per_m=148.0, w=810, h=1080, d_m=40.233,
                    far_green=GREEN_PAINT, noise=0.0, seed=0):
    """The far house as the side camera sees it, for the LATERAL fit.

    ``side_view`` paints the annulus as two flat bands, which is all the depth
    fit reads. Here it is the ellipse perspective makes of the ring, so a row
    through the tee has a left band and a right band with real edges. Positions
    come from the same map ``SideView.to_image`` inverts, so a correct fit
    recovers ``centre_col`` and ``lat_px_per_m`` exactly. ``far_green`` paints
    the right-hand band, which on real plates is often half as green as the
    left (VXU9's left view: peaks ~22 and ~10). RGB.
    """
    img = np.full((h, w, 3), SIDE_ICE, dtype=np.uint8)
    u = (hog_row - tee_row) * (d_m - C.TEE_TO_HOGLINE_M) / C.TEE_TO_HOGLINE_M
    c, yh = d_m * u, tee_row - u
    cols = np.arange(w)
    top = int(yh + c / (d_m + C.R_12FT_M)) - 1
    bot = int(yh + c / (d_m - C.R_12FT_M)) + 2
    for r in range(top, bot):
        y = d_m - c / (r - yh)
        x = (cols - centre_col) / (lat_px_per_m * (r - yh) / (tee_row - yh))
        rho = np.hypot(x, y)
        ring = (rho >= C.R_8FT_M) & (rho <= C.R_12FT_M)
        img[r, ring & (cols < centre_col)] = GREEN_PAINT
        img[r, ring & (cols >= centre_col)] = far_green
    rr = int(round(hog_row))
    img[rr - 1:rr + 2] = SIDE_LINE
    if noise:
        rng = np.random.default_rng(seed)
        img = np.clip(img.astype(np.float32) + rng.normal(0, noise, img.shape),
                      0, 255).astype(np.uint8)
    return img

def composite_strip(width, height, bar_px=4, bar_color=(90, 90, 90)):
    """The grey overhead strip framing two house panels, with its 3 bars.

    ``layout.detect_panels`` finds the strip by what never moves: a fixed
    ``bar_px``-wide flat bar runs along each edge of the strip and again
    between the two panels, identical on every frame. It also insists panel
    content is *not* flat, so each panel here is a vertical stripe pattern
    with plenty of column-to-column contrast -- comfortably past
    ``_MAX_SPATIAL_STD`` -- rather than a plain fill that would read as one
    more bar and collapse the two panels into none.

    Channel order does not matter here, as with ``side_view`` above:
    ``detect_panels`` reads mean-of-channels luminance plus per-column/row
    variance, so this can sit RGB-side-by-side with an RGB ``side_view`` in
    one composite frame without either caring.
    """
    img = np.full((height, width, 3), bar_color, dtype=np.uint8)
    mid0 = height // 2 - bar_px // 2
    mid1 = mid0 + bar_px
    stripe = (150 + 40 * (np.arange(width - 2 * bar_px) // 6 % 2)).astype(np.uint8)
    for lo, hi in ((bar_px, mid0), (mid1, height - bar_px)):
        img[lo:hi, bar_px:width - bar_px] = stripe[None, :, None]
    return img


def side_view_stone(img, row, width_px=52, color="red", x=None):
    """Paint a stone on a side view: a grey body with a coloured handle. RGB."""
    out = img.copy()
    h, w = out.shape[:2]
    cx = w // 2 if x is None else int(x)
    body_h = max(4, int(width_px * 0.42))
    r = int(round(row))
    x0, x1 = cx - width_px // 2, cx + width_px // 2
    out[max(0, r - body_h):r, max(0, x0):x1] = (150, 150, 150)
    hw = max(3, width_px // 4)
    rgb = (210, 40, 40) if color == "red" else (230, 210, 40)
    out[max(0, r - body_h - hw // 2):max(0, r - body_h) + 1,
        cx - hw:cx + hw] = rgb
    return out
