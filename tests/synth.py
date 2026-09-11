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
