"""What the API image is built from: every static file the API serves must
survive .gcloudignore (what Cloud Build uploads) and .dockerignore (what the
build sees). Both drop every *.jpg, which once shipped the /label how-to
figures as 500s."""

from fnmatch import fnmatch
from pathlib import Path

import pytest

from curling_score.service.api import STATIC_ASSETS

ROOT = Path(__file__).resolve().parents[1]


def ignored(path: str, patterns: list[str]) -> bool:
    """Whether gitignore-style ``patterns`` drop ``path`` (the last match wins).

    Enough of the syntax for these two files: comments, ``!``, a leading or
    trailing ``/``, and globs matched against the whole path or any component.
    """
    parts = path.split("/")
    out = False
    for line in patterns:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        neg = line.startswith("!")
        pat = line[1:] if neg else line
        pat = pat.strip("/")
        if "/" in pat:
            hit = fnmatch(path, pat) or path.startswith(pat + "/")
        else:
            hit = any(fnmatch(p, pat) for p in parts)
        if hit:
            out = not neg
    return out


def test_the_matcher_follows_the_last_match():
    assert ignored("a/b.jpg", ["*.jpg"])
    assert not ignored("a/b.jpg", ["*.jpg", "!a/*.jpg"])
    assert ignored("datasets/x/y.txt", ["datasets/"])
    assert not ignored("src/x.py", ["*.jpg"])


@pytest.mark.parametrize("ignore_file", [".gcloudignore", ".dockerignore"])
def test_every_static_asset_reaches_the_api_image(ignore_file):
    patterns = (ROOT / ignore_file).read_text().splitlines()
    dropped = [name for name in STATIC_ASSETS
               if ignored(f"src/curling_score/service/static/{name}", patterns)]
    assert dropped == [], f"{ignore_file} drops {dropped}"
