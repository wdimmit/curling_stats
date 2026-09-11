"""Turn a club playlist into a list of games, dated and sheeted.

The season playlist is a grid: 26 Tuesdays x 5 sheets, one four-hour VOD each.
Both axes matter. The sheet says which camera and which painted ice, and the
date says which night's lighting, ice and teams -- so the train/val split is
made by *date*, holding out whole Tuesdays, and a date is only usable if all
five of its sheets are present.

Titles look like ``"3/24 - Sheet 2 - Tuesday Super League 2025-2026"``. The year
is not in the date, only in the league name, and the season straddles New Year,
so the month is the only thing that says which half of ``2025-2026`` a date
belongs to.
"""

import datetime as _dt
import re
from dataclasses import dataclass

from curling_score.ingest import source

# August or later belongs to the first year of a "2025-2026" season. The club
# runs September to April, so the boundary sits in the empty months either way.
SEASON_SPLIT_MONTH = 8

_DATE_RE = re.compile(r"^\s*(\d{1,2})\s*/\s*(\d{1,2})\b")
_SEASON_RE = re.compile(r"\b(20\d{2})\s*[-–]\s*(20\d{2})\b")
_YEAR_RE = re.compile(r"\b(20\d{2})\b")


@dataclass(frozen=True)
class Entry:
    """One VOD: a game night on one sheet."""

    video_id: str
    title: str
    duration_s: float
    date: str  # ISO, "2025-10-14"
    sheet: int


def parse_date(title: str) -> str | None:
    """The ISO date a club title describes, or None if it does not carry one."""
    found = _DATE_RE.match(title or "")
    if not found:
        return None
    month, day = int(found.group(1)), int(found.group(2))

    span = _SEASON_RE.search(title)
    if span:
        first, second = int(span.group(1)), int(span.group(2))
        year = first if month >= SEASON_SPLIT_MONTH else second
    else:
        single = _YEAR_RE.search(title)
        if not single:
            return None
        year = int(single.group(1))

    try:
        return _dt.date(year, month, day).isoformat()
    except ValueError:
        return None


def entries_from_flat(flat_entries) -> tuple[list[Entry], list[str]]:
    """Parse yt-dlp's flat-playlist output into entries, plus complaints.

    Anything unparseable is *reported*, never dropped quietly: a title the club
    typed differently is a video missing from the dataset, and a silent gap in a
    130-video grid is not something anyone would notice.

    The result is sorted by date then sheet, so nothing downstream depends on
    YouTube's newest-first ordering.
    """
    entries: list[Entry] = []
    problems: list[str] = []
    for raw in flat_entries:
        vid = raw.get("id") or "<no id>"
        title = raw.get("title") or ""
        date = parse_date(title)
        sheet = source.sheet_from_title(title)
        if date is None or sheet is None:
            missing = " and ".join(
                part for part, ok in (("date", date), ("sheet", sheet)) if not ok)
            problems.append(f"{vid}: no {missing} in title {title!r}")
            continue
        entries.append(Entry(vid, title, float(raw.get("duration") or 0.0), date, sheet))

    entries.sort(key=lambda e: (e.date, e.sheet))
    return entries, problems


def enumerate_playlist(url: str) -> tuple[list[Entry], list[str]]:
    """Every video in a playlist, from one flat metadata request."""
    import yt_dlp

    opts = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist"}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return entries_from_flat(info.get("entries") or [])


def group_by_date(entries) -> dict[str, list[Entry]]:
    """Entries keyed by date, each list sorted by sheet."""
    out: dict[str, list[Entry]] = {}
    for entry in entries:
        out.setdefault(entry.date, []).append(entry)
    for same_date in out.values():
        same_date.sort(key=lambda e: e.sheet)
    return out


def check(entries, sheets=(1, 2, 3, 4, 5)) -> list[str]:
    """Complaints about a season that is not a full date x sheet grid."""
    problems = []
    wanted = set(sheets)
    for date, group in sorted(group_by_date(entries).items()):
        seen = [e.sheet for e in group]
        for sheet in sorted(wanted - set(seen)):
            problems.append(f"{date}: no sheet {sheet}")
        for sheet in sorted({s for s in seen if seen.count(s) > 1}):
            problems.append(f"{date}: sheet {sheet} appears {seen.count(sheet)} times")
        for sheet in sorted(set(seen) - wanted):
            problems.append(f"{date}: unexpected sheet {sheet}")
    return problems


def pick_val_dates(dates, n: int = 5, exclude=()) -> list[str]:
    """``n`` hold-out dates, spread evenly through the season.

    Evenly rather than randomly, and never the first or last night: the start
    and end of a season are its most unusual ice, and holding one out entirely
    would make val harder than train for a reason that has nothing to do with
    the model.

    ``exclude`` keeps a date out of the hold-out without dropping it from the
    season. Two October Tuesdays of this season were streamed at 720p where the
    other twenty-four are 1080p; those games are worth training on, since the
    club really does post 720p sometimes, but a val split resting on them would
    measure the encoder rather than the model.
    """
    skip = set(exclude)
    ordered = [d for d in sorted(set(dates)) if d not in skip]
    if n < 1:
        raise ValueError("need at least one hold-out date")
    if len(ordered) < 2 * n:
        raise ValueError(
            f"cannot spread {n} hold-out dates across {len(ordered)} eligible "
            f"({len(set(dates))} in the season, {len(skip & set(dates))} excluded); "
            f"need {2 * n}")
    step = len(ordered) // n
    offset = step // 2
    return [ordered[offset + i * step] for i in range(n)]


def assign_splits(entries, val_dates) -> dict[str, str]:
    """Video id -> "train" or "val", holding out whole dates.

    Whole dates rather than whole videos: two sheets of the same night share
    ice, lighting and often teams, so a val video whose sibling is in train is
    not the unseen game it looks like.
    """
    known = {e.date for e in entries}
    unknown = sorted(set(val_dates) - known)
    if unknown:
        raise ValueError(f"hold-out dates not in the season: {', '.join(unknown)}")
    held = set(val_dates)
    return {e.video_id: ("val" if e.date in held else "train") for e in entries}
