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
