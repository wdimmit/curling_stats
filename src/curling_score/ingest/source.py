"""Resolve a user-supplied YouTube link to a video id and metadata.

Club stream links are usually pasted straight out of a playlist, e.g.
``...watch?v=VXU9xwmugRg&list=PL...&index=7``. yt-dlp treats a URL carrying
``list=`` as a playlist, so we always canonicalise to the bare watch URL rather
than relying on ``--no-playlist`` alone.
"""

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_SHEET_RE = re.compile(r"\bSheet\s+(\d+)\b", re.IGNORECASE)


def video_id(url: str) -> str:
    """Extract the 11-character YouTube video id from any accepted form."""
    candidate = url.strip()
    if _ID_RE.match(candidate):
        return candidate

    parsed = urlparse(candidate)
    if parsed.netloc.endswith("youtu.be"):
        found = parsed.path.lstrip("/").split("/")[0]
    else:
        found = (parse_qs(parsed.query).get("v") or [""])[0]

    if not _ID_RE.match(found):
        raise ValueError(f"no YouTube video id in {url!r}")
    return found


def canonical_url(url: str) -> str:
    """The bare watch URL, with playlist and index context stripped."""
    return f"https://www.youtube.com/watch?v={video_id(url)}"


def watch_url_at(vid: str, t_seconds: float) -> str:
    """A deep link to a moment in the video.

    Truncates rather than rounds, so the link never lands after the event.
    """
    return f"https://youtu.be/{vid}?t={int(t_seconds)}"


def sheet_from_title(title: str) -> int | None:
    """Club titles look like "4/30 - Sheet 2 - Spring Skip's Choice League 2026"."""
    match = _SHEET_RE.search(title or "")
    return int(match.group(1)) if match else None


@dataclass(frozen=True)
class VideoInfo:
    video_id: str
    title: str
    duration_s: float
    was_live: bool
    is_live: bool
    upload_date: str | None
    sheet: int | None


def fetch_info(url: str) -> VideoInfo:
    """Query YouTube for metadata. Requires network; does not download media."""
    import yt_dlp

    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(canonical_url(url), download=False)
    if info.get("_type") == "playlist":
        info = info["entries"][0]

    title = info.get("title") or ""
    return VideoInfo(
        video_id=info["id"],
        title=title,
        duration_s=float(info.get("duration") or 0.0),
        was_live=bool(info.get("was_live")),
        is_live=bool(info.get("is_live")),
        upload_date=info.get("upload_date"),
        sheet=sheet_from_title(title),
    )
