"""The tracker's input: a ``Feed`` says which file each view lives in, and a
``Cut`` says how the view is taken from that file."""

from curling_score.feeds.cut import Cut
from curling_score.feeds.feed import VIEWS, Feed, as_feed

__all__ = ["Cut", "Feed", "as_feed", "VIEWS"]
