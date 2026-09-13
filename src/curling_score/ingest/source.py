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
# YouTube's own timestamp forms: "90", "90s", "1m30s", "1h2m3s".
_TIME_RE = re.compile(r"^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s?)?$")


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


def parse_time(text) -> float | None:
    """Seconds from a YouTube-style timestamp, or None if it is not one."""
    if text is None:
        return None
    text = str(text).strip().lower()
    if not text:
        return None
    m = _TIME_RE.match(text)
    if not m or not any(m.groups()):
        return None
    h, mi, s = (int(g) if g else 0 for g in m.groups())
    return float(h * 3600 + mi * 60 + s)


@dataclass(frozen=True)
class Link:
    """A pasted link, taken apart: which video, and where in it."""

    video_id: str
    start_s: float | None


def parse_link(url: str) -> Link:
    """The video id and any start time carried by the link.

    ``canonical_url`` deliberately drops everything but the id, which is right
    for downloading and wrong for a user who pasted ``?t=4212`` to say *which
    game* in a four-hour stream they mean. This keeps that.
    """
    vid = video_id(url)
    parsed = urlparse(url.strip())
    query = parse_qs(parsed.query)
    raw = (query.get("t") or query.get("start") or [None])[0]
    if raw is None and parsed.fragment.startswith("t="):
        raw = parsed.fragment[2:]
    return Link(video_id=vid, start_s=parse_time(raw))


def sheet_from_title(title: str) -> int | None:
    """Club titles look like "4/30 - Sheet 2 - Spring Skip's Choice League 2026"."""
    match = _SHEET_RE.search(title or "")
    return int(match.group(1)) if match else None


def league_from_title(title: str) -> str | None:
    """What the club calls the competition, from the title it streamed under.

    The same titles that carry the sheet carry the league after it, and the
    club is consistent about the order even when the front of the title is
    not: "4/30 - Sheet 2 - Spring Skip's Choice League 2026" and "Rice (r) vs
    Casey (y) - Draw 1 (15:00) - Sheet 5 - 2026 5U National Championship" both
    put it last. So the rule is everything after the Sheet segment, rather
    than a position counted from the front.

    Only a guess from a naming convention, and one somebody can correct: the
    playlist watcher's label beats it, and so does anything set by hand.
    """
    parts = [p.strip() for p in (title or "").split(" - ")]
    for i, part in enumerate(parts):
        if _SHEET_RE.fullmatch(part):
            rest = " - ".join(p for p in parts[i + 1:] if p)
            return rest or None
    return None


@dataclass(frozen=True)
class VideoInfo:
    video_id: str
    title: str
    duration_s: float
    was_live: bool
    is_live: bool
    upload_date: str | None
    sheet: int | None
    channel_id: str | None = None


def fetch_info(url: str, timeout_s: float = 60.0) -> VideoInfo:
    """Query YouTube for metadata. Requires network; does not download media."""
    import yt_dlp

    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        # A hung metadata request otherwise blocks a caller forever.
        "socket_timeout": timeout_s,
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
        channel_id=info.get("channel_id"),
    )
