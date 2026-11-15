"""Which file each view lives in.

YouTube is one composite, so all five views are that one file. The cameras
are a file each (``feeds/cameras.py``), and a camera feed knows its sheet, whose
layout says how each view is cut. A feed with a single file stands in for that
path (``os.fspath``), so code that still takes a video path keeps working; a
feed with several raises there rather than quietly reading one of its files.
``str`` never raises, so a feed can always be named in an error.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

VIEWS = ("top", "bottom", "left", "right", "board")


@dataclass(frozen=True)
class Feed:
    kind: str  # "youtube" | "cameras"
    paths: Mapping[str, Path]  # view name -> file; every name in VIEWS
    sheet: int | None = None  # a camera feed's sheet, whose layout cuts its views

    def __post_init__(self):
        missing = [v for v in VIEWS if v not in self.paths]
        if missing:
            raise ValueError(f"feed has no file for {', '.join(missing)}")
        object.__setattr__(self, "paths",
                           MappingProxyType({v: Path(self.paths[v]) for v in VIEWS}))

    def __eq__(self, other):
        if not isinstance(other, Feed):
            return NotImplemented
        return (self.kind, self.sheet, dict(self.paths)) == (other.kind, other.sheet,
                                                             dict(other.paths))

    def __hash__(self):
        return hash((self.kind, self.sheet, tuple(self.paths.values())))

    def single(self) -> Path | None:
        """The one file all the views share, or None."""
        files = set(self.paths.values())
        return next(iter(files)) if len(files) == 1 else None

    def path_for(self, name: str) -> Path:
        """The file a view lives in. ``""`` (a cut with no name) is the single
        file, and is an error when there are several."""
        if name:
            if name not in self.paths:
                raise ValueError(f"no such view: {name}")
            return self.paths[name]
        one = self.single()
        if one is None:
            raise ValueError(f"a view with no name needs one file, this feed has {self._files()}")
        return one

    def _files(self) -> str:
        return ", ".join(sorted({str(p) for p in self.paths.values()}))

    def __fspath__(self) -> str:
        one = self.single()
        if one is None:
            raise TypeError(f"this feed is more than one file: {self._files()}")
        return str(one)

    def __str__(self) -> str:
        one = self.single()
        return str(one) if one is not None else f"{self.kind} feed: {self._files()}"

    def on(self, names: Iterable[str], path) -> Feed:
        """The same feed with those views taken from another file."""
        names = tuple(names)
        if unknown := [n for n in names if n not in VIEWS]:
            raise ValueError(f"no such view: {', '.join(unknown)}")
        return Feed(self.kind, {**self.paths, **{n: Path(path) for n in names}}, self.sheet)


def as_feed(video) -> Feed:
    """A ``Feed`` passes through; a path is a YouTube composite."""
    if isinstance(video, Feed):
        return video
    return Feed("youtube", {v: Path(os.fspath(video)) for v in VIEWS})
