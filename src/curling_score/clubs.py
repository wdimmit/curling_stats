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
    # Where the overheads show only the houses (DFW): the long camera behind the
    # thrower times the far hog line (`fartime.time_far_crossings_from_long_camera`),
    # and places the guards resting beyond this far in front of the tee, which no
    # panel shows (`game/longguards.py`). None: no long-camera guards.
    far_hog_from_long: bool = False
    guards_from_long_m: float | None = None
    panel_layout: PanelLayout | None = None
    # The rows of the side views that are ice and not broadcast graphics.
    side_rows: tuple[int, int] | None = None
    # Rows of each long camera that hold a logo painted on the ice that both
    # see, to tie the two cameras' fits together by (`logotie`): side -> rows.
    logo_rows: dict | None = None
    # The YouTube channels that carry this club's stream, so a worker given
    # only a video can pick the profile.
    channel_ids: tuple[str, ...] = ()
    # Detectors trained for this club's stream, as file names in weights/;
    # None runs Granite's (`weights.DEFAULT_NAME`, `weights.SIDE_NAME`). The
    # environment variables there still win. A worker claims jobs under one
    # overhead model, so a club with its own must be chosen by the worker's
    # CLUB, not by a channel id.
    overhead_model: str | None = None
    side_model: str | None = None


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
},
    # ds17b read the "Cincy Light" sponsor ring as red stones, so the red
    # house never emptied (cin1); ds13e lost the stone under the thrower's
    # hand and under the sweepers on the end cameras (cin2, cin3).
    overhead_model="cin1a-11n.pt", side_model="cin3a.pt")

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

# Ardsley's stream (1920x1080): Granite's arrangement -- two overheads
# stacked in a centre strip, top house entered from below, and long cameras
# either side, the left one looking down at the top house -- but framed in
# thin navy and gold lines rather than letterbox bars, so the panels are
# fixed. The houses paint 12-to-8 blue around a red 4-foot. The wall behind
# each house is the same blue, so the long cameras' views start below it;
# the sponsor bar starts at row 1018. Hog lines are dark. The overheads'
# ice is tinted blue (H 107-114, S 40-73) and passes BLUE_HSV; the ring is
# H ~120, S 180+.
_ARDSLEY_BLUE = ((115, 120, 30), (135, 255, 255))
ARDSLEY = Club("ardsley", (
    ("top", Panel("bottom", HousePaint(twelve_ft=(_ARDSLEY_BLUE,), four_ft=RED_HSV))),
    ("bottom", Panel("top", HousePaint(twelve_ft=(_ARDSLEY_BLUE,), four_ft=RED_HSV))),
), long_cameras={
    house: (side, EndPaint(ring=(BLUE_HSV,), inner_radius_m=C.R_8FT_M, hog="dark"))
    for house, side in (("top", "left"), ("bottom", "right"))
}, panel_layout=PanelLayout(top=(821, 7, 278, 498), bottom=(821, 515, 278, 488)),
    side_rows=(335, 1015), channel_ids=("UCyU3Hul8S0_AqhYnqNMuGRw",))

# Martensville's broadcast (SSN for CurlingZone, 1920x1080): Swift Current's
# arrangement -- two overheads in a centre strip framed in blue, both with the
# front of the house at the top, long cameras either side with the left one
# looking down at the top house. The house is a brewery's logo: a yellow band
# whose outer edge is the 12-foot circle (stones agree, 0.29 m at the scale it
# gives) and whose inner edge is 1.30 m out, not the 8-foot; the thin circle
# inside it is decoration, not the 4-foot. The overhead tiles are stretched,
# 0.94-0.95 as tall as wide. Hog lines are blue; the centre line is painted,
# faintly, and sponsors' logos break it up on the right camera. The sheet's
# name bar starts at row 978. CurlingZone carries many venues, so no channel
# picks this profile.
_MARTENSVILLE_YELLOW = ((18, 60, 110), (40, 255, 255))
# The long cameras see the band paler and browner: H 19-27, S 57-93.
_MARTENSVILLE_FAR_YELLOW = ((12, 35, 80), (40, 255, 255))
_MARTENSVILLE_HOUSE = HousePaint(twelve_ft=(_MARTENSVILLE_YELLOW,), inner_radius_m=1.30,
                                 fit_aspect=True)
MARTENSVILLE = Club("martensville", (
    ("top", Panel("top", _MARTENSVILLE_HOUSE)),
    ("bottom", Panel("top", _MARTENSVILLE_HOUSE)),
), long_cameras={
    house: (side, EndPaint(ring=(_MARTENSVILLE_FAR_YELLOW,), inner_radius_m=1.30, hog="blue",
                          centre_min_rows=40))
    for house, side in (("top", "left"), ("bottom", "right"))
}, panel_layout=PanelLayout(top=(728, 7, 468, 462), bottom=(728, 608, 468, 459)),
    side_rows=(330, 975),
    # overhead-3 read the logo's hop cones and lettering as yellow stones all
    # game, so the house never emptied (mv1).
    overhead_model="mv1a.pt")

# DFW Curling Club's broadcast (Dallas, 1920x1080) as `scripts/dallas/normalize.py`
# rewrites it: the stream cuts every half minute between a three-panel view and a
# score view of the house in play, and the normaliser maps the score view back into
# the three-panel one. That is Granite's arrangement -- two overheads in a centre
# strip, long cameras either side -- with no bars between the panels, both houses
# front at the top, and the left long camera looking down at the BOTTOM house. The
# houses paint 12-to-8 blue around a red 4-foot, with the club's logo on the
# button. Hog lines are dark grey; the centre line is painted. The stones are green
# and yellow; green is the red_stone class. The overheads are exposed darker
# than Granite's (value p95 ~145 against 185-204, lights fully on). No channel
# id until the worker normalises the stream itself: the raw stream under this
# profile reads nothing.
_DFW_HOUSE = HousePaint(twelve_ft=(BLUE_HSV,), four_ft=RED_HSV, lit_min_p95=120.0)
DALLAS = Club("dallas", (
    ("top", Panel("top", _DFW_HOUSE)),
    ("bottom", Panel("top", _DFW_HOUSE)),
), long_cameras={
    house: (side, EndPaint(ring=(BLUE_HSV,), inner_radius_m=C.R_8FT_M, hog="dark"))
    for house, side in (("top", "right"), ("bottom", "left"))
}, panel_layout=PanelLayout(top=(764, 0, 379, 526), bottom=(764, 526, 379, 554)),
    far_hog_from_long=True, guards_from_long_m=3.3,
    # The long cameras' ice starts below the far wall: the clubhouse window
    # above it holds a blue neon cowboy hat, which in the 5U Qualifier's
    # framing (46wEMds_tYQ) outweighs the ring and was fitted as the house.
    side_rows=(390, 1080),
    # overhead-3 and long-1 have never seen a green stone: they missed DFW's
    # or read them as yellow (dfw1, dfw2).
    overhead_model="dfw1a.pt", side_model="dfw2b.pt")

CLUBS = {c.name: c for c in (GRANITE, CINCINNATI, SWIFT_CURRENT, ARDSLEY, MARTENSVILLE, DALLAS)}


def for_channel(channel_id) -> Club:
    """The club whose stream a YouTube channel carries; Granite otherwise."""
    for club in CLUBS.values():
        if channel_id and channel_id in club.channel_ids:
            return club
    return GRANITE
