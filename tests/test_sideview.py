"""The composite's two wide side views: where they are, and where the far
end's paint sits in them.

Each side camera watches the *other* end's house and hog line -- its own hog
line is about half a metre beneath it, out of frame. So the throwing end's
crossing is seen by the camera at the target end.
"""

import pytest

from curling_score.geometry import layout, sideview


def a_layout(x=810, w=297):
    """A panel layout the shape the club's composite actually produces."""
    return layout.PanelLayout(top=(x, 10, w, 514), bottom=(x, 554, w, 516))


class TestLocate:
    def test_the_views_are_what_the_overhead_strip_leaves_behind(self):
        got = sideview.locate(a_layout(), width=1920, height=1080)
        assert got["left"] == (0, 0, 810, 1080)
        assert got["right"] == (1107, 0, 813, 1080)

    def test_it_follows_the_strip_rather_than_assuming_where_it_sits(self):
        got = sideview.locate(a_layout(x=700, w=300), width=1920, height=1080)
        assert got["left"] == (0, 0, 700, 1080)
        assert got["right"] == (1000, 0, 920, 1080)

    def test_a_strip_touching_an_edge_leaves_no_view_that_side(self):
        with pytest.raises(sideview.SideViewError):
            sideview.locate(a_layout(x=0, w=297), width=1920, height=1080)
