"""Which site this deploy is: its name, nav, time zone and rules.

One codebase serves curling.dimmit.net (Granite, the default: ``SITE`` unset)
and other clubs' public sites from their own projects. ``SITE=dfw`` is DFW
Curling Club's event site, dfwcurling.dimmit.net: public with no accounts,
read-only to everyone without the admin token, every rock's clip cut without
asking, and only the streams of its events' dates charted (``DISCOVER_RANGES``).
"""

import os
from dataclasses import dataclass, replace
from datetime import date

# Writes a public page needs to load or play on a read-only site. None today:
# the /g/ viewer is read-only (review mode), and every POST the pages make is
# a signed-in or editing action (frontend/: teams, scores, flags, plays,
# overrides, submissions, clip asks -- which an auto-clips site never needs).
PUBLIC_POSTS: frozenset = frozenset()
# And under these, on a site whose labelling is open (`open_labelling`).
LABEL_POSTS = "/api/label/"


@dataclass(frozen=True)
class SiteProfile:
    key: str
    name: str
    club_name: str
    tz: str
    nav_hidden: frozenset = frozenset()
    read_only: bool = False
    auto_clips: bool = False
    pages_dir: str | None = None
    discover_ranges: tuple | None = None
    # /label without accounts: whoever has its (unlinked) address labels, as
    # one labeller, and one label finishes an item. For a temporary site
    # whose operator is its only labeller.
    open_labelling: bool = False


GRANITE = SiteProfile(key="", name="curling.dimmit.net", club_name="Granite Curling Club",
                      tz="America/Los_Angeles")
DFW = SiteProfile(
    key="dfw", name="dfwcurling.dimmit.net", club_name="DFW Curling Club", tz="America/Chicago",
    nav_hidden=frozenset({"/thinking", "/ice", "/practice", "/label", "/submit", "/playlists",
                          "/mine", "/shots"}),
    read_only=True, auto_clips=True, pages_dir="dfw", discover_ranges=(), open_labelling=True)
SITES = {p.key: p for p in (GRANITE, DFW)}


def parse_ranges(text: str) -> tuple:
    """``"2026-11-06/2026-11-09,2026-11-13/2026-11-16"`` -> ((date, date), ...), inclusive."""
    out = []
    for part in (text or "").split(","):
        part = part.strip()
        if not part:
            continue
        a, _, b = part.partition("/")
        lo, hi = date.fromisoformat(a.strip()), date.fromisoformat((b or a).strip())
        if hi < lo:
            raise ValueError(f"DISCOVER_RANGES: {part} ends before it starts")
        out.append((lo, hi))
    return tuple(out)


def from_env(env=None) -> SiteProfile:
    env = os.environ if env is None else env
    key = (env.get("SITE") or "").strip()
    if key not in SITES:
        raise ValueError(f"SITE={key!r}: no such site (known: {sorted(k for k in SITES if k)})")
    profile = SITES[key]
    if profile.discover_ranges is not None:
        profile = replace(profile, discover_ranges=parse_ranges(env.get("DISCOVER_RANGES", "")))
    return profile
