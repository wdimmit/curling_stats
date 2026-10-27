"""What differs from one club's stream to another's.

Granite, where all of this was built, is the default everywhere; another club
is a profile here rather than a branch through the code.
"""

from dataclasses import dataclass

from curling_score.geometry.calibrate import (
    BLUE_HSV, DARK_HSV, GRANITE_HOUSE, RED_HSV, HousePaint,
)


@dataclass(frozen=True)
class Panel:
    """One overhead house panel: which edge stones enter by, and its paint."""

    delivery_side: str  # "top" or "bottom"
    paint: HousePaint


@dataclass(frozen=True)
class Club:
    name: str
    panels: tuple[tuple[str, Panel], ...]  # ("top", ...), ("bottom", ...)


# The two houses are played toward from opposite directions, and Granite's
# composite shows them so: stones enter the top panel from below.
GRANITE = Club("granite", (
    ("top", Panel("bottom", GRANITE_HOUSE)),
    ("bottom", Panel("top", GRANITE_HOUSE)),
))

# A centre column of red house, club logo, blue house. Both houses are shown
# with their front at the top, read from the lettering against the end
# cameras. The red house paints 12-to-8 red and 8-to-4 black around a white
# 4-foot; the blue house paints 12-to-8 blue and no 4-foot at all, only the
# club's logo.
CINCINNATI = Club("cincinnati", (
    ("top", Panel("top", HousePaint(twelve_ft=RED_HSV, four_ft=(DARK_HSV,),
                                    four_ft_edge="inner"))),
    ("bottom", Panel("top", HousePaint(twelve_ft=(BLUE_HSV,)))),
))

CLUBS = {c.name: c for c in (GRANITE, CINCINNATI)}
