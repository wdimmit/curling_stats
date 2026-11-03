"""What differs from one club's stream to another's.

Granite, where all of this was built, is the default everywhere; another club
is a profile here rather than a branch through the code.
"""

from dataclasses import dataclass

from curling_score.geometry import constants as C
from curling_score.geometry.calibrate import (
    BLUE_HSV, DARK_HSV, GRANITE_HOUSE, RED_HSV, HousePaint,
)
from curling_score.geometry.endcam import EndPaint
from curling_score.geometry.layout import PanelLayout


@dataclass(frozen=True)
class Panel:
    """One overhead house panel: which edge stones enter by, and its paint."""

    delivery_side: str  # "top" or "bottom"
    paint: HousePaint


@dataclass(frozen=True)
class Club:
    name: str
    panels: tuple[tuple[str, Panel], ...]  # ("top", ...), ("bottom", ...)
    # Side cameras that each look back at their own house: house -> (which
    # side of the composite that camera is on, its paint as it sees it). None
    # for long cameras looking down the sheet, as Granite's do.
    end_cameras: dict | None = None
    # Long cameras looking down the sheet, as Granite's do, but fitted as the
    # end cameras are, from the whole ring and the paint: house -> (side,
    # paint). None fits them Granite's way.
    long_cameras: dict | None = None
    # A composite whose panels have no letterbox bars to find them by: fixed.
    panel_layout: PanelLayout | None = None
    # The rows of the side views that are ice and not broadcast graphics.
    side_rows: tuple[int, int] | None = None
    # Rows of each long camera that hold a logo painted on the ice that both
    # see, to tie the two cameras' fits together by (`logotie`): side -> rows.
    logo_rows: dict | None = None


# The two houses are played toward from opposite directions, and Granite's
# composite shows them so: stones enter the top panel from below.
GRANITE = Club("granite", (
    ("top", Panel("bottom", GRANITE_HOUSE)),
    ("bottom", Panel("top", GRANITE_HOUSE)),
))

# On Cincinnati's end cameras the red reads pink (S 50-76, V 210+) and the
# black grey (V 117-143 against ice at 225).
_END_PINK = (((0, 35, 120), (10, 255, 255)), ((165, 35, 120), (179, 255, 255)))
_END_GREY = ((0, 0, 0), (179, 45, 160))

# A centre column of red house, club logo, blue house. Both houses are shown
# with their front at the top, read from the lettering against the end
# cameras. The red house paints 12-to-8 red and 8-to-4 black around a white
# 4-foot; the blue house paints 12-to-8 blue and no 4-foot at all, only the
# club's logo.
CINCINNATI = Club("cincinnati", (
    ("top", Panel("top", HousePaint(twelve_ft=RED_HSV, four_ft=(DARK_HSV,),
                                    four_ft_edge="inner"))),
    ("bottom", Panel("top", HousePaint(twelve_ft=(BLUE_HSV,)))),
), end_cameras={
    # The red house's camera is on the right of the composite, the blue's on
    # the left; each sits above its own hog line.
    "top": ("right", EndPaint(ring=_END_PINK, inner_radius_m=C.R_4FT_M, also=(_END_GREY,))),
    "bottom": ("left", EndPaint(ring=(BLUE_HSV,), inner_radius_m=C.R_8FT_M)),
})

# Swift Current's broadcast (SSN, 1920x1080): two house-only overheads framed
# in white with a rotating sponsor tile between them, and long cameras either
# side, as at Granite -- the left one looks down at the top house. The houses
# paint 12-to-8 red and a dark 4-foot around a red button; both are shown with
# their front at the top. The hog lines are dark and there is no painted
# centre line, so the sidelines give it. The score bar starts at row 990.
SWIFT_CURRENT = Club("swift-current", (
    ("top", Panel("top", HousePaint(twelve_ft=RED_HSV, four_ft=(DARK_HSV,),
                                    four_ft_edge="outer"))),
    ("bottom", Panel("top", HousePaint(twelve_ft=RED_HSV, four_ft=(DARK_HSV,),
                                       four_ft_edge="outer"))),
), long_cameras={
    house: (side, EndPaint(ring=RED_HSV, inner_radius_m=C.R_8FT_M, hog="dark", centre="sidelines"))
    for house, side in (("top", "left"), ("bottom", "right"))
}, panel_layout=PanelLayout(top=(730, 8, 460, 454), bottom=(730, 613, 460, 454)),
    side_rows=(0, 990),
    # The Pepsi logo at centre ice, near the bottom of both long cameras.
    logo_rows={"left": (770, 990), "right": (800, 990)})

CLUBS = {c.name: c for c in (GRANITE, CINCINNATI, SWIFT_CURRENT)}
