"""A YouTube live stream's HLS playlist, and where a segment sits on the
video's clock.

The video's clock is the recording's from segment 0: what yt-dlp records with
``-live_start_index 0`` (``recorder.YtDlpRecorder``) and what YouTube's player
seeks in. A segment's first frame is therefore at its pts less segment 0's --
exact, where counting ``#EXTINF`` durations would gather the rounding of every
segment before it. A playlist that no longer begins at segment 0 (a stream
past YouTube's rewind window) cannot say where its segments sit: ``NoStart``.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urljoin

from curling_score.ingest import source

_SEQ_RE = re.compile(r"#EXT-X-MEDIA-SEQUENCE:(\d+)")
_PDT_RE = re.compile(r"#EXT-X-PROGRAM-DATE-TIME:(\S+)")
_INF_RE = re.compile(r"#EXTINF:([\d.]+)")
PTS_WRAP = 2 ** 33
PTS_HZ = 90_000


class NoStart(Exception):
    """The playlist does not begin at segment 0."""


@dataclass(frozen=True)
class Segment:
    seq: int
    uri: str
    extinf_s: float


def first_media_sequence(playlist: str) -> int | None:
    m = _SEQ_RE.search(playlist or "")
    return int(m.group(1)) if m else None


def program_date_time(playlist: str) -> datetime | None:
    """The wall time YouTube gives the playlist's first segment, if it says."""
    m = _PDT_RE.search(playlist or "")
    if not m:
        return None
    return datetime.fromisoformat(m.group(1).replace("Z", "+00:00"))


def segments(playlist: str, base_url: str, *, from_start: bool = False) -> list[Segment]:
    """The playlist's segments, numbered from its media sequence, with absolute
    URIs. ``from_start``: raise ``NoStart`` unless the first is segment 0."""
    seq = first_media_sequence(playlist) or 0
    if from_start and seq != 0:
        raise NoStart(f"the playlist begins at segment {seq}, not 0")
    out, extinf = [], None
    for line in (playlist or "").splitlines():
        line = line.strip()
        m = _INF_RE.match(line)
        if m:
            extinf = float(m.group(1))
        elif line and not line.startswith("#") and extinf is not None:
            out.append(Segment(seq=seq + len(out), uri=urljoin(base_url, line), extinf_s=extinf))
            extinf = None
    return out


def first_pts_s(ts: bytes) -> float:
    """The first video packet's pts in a segment, in seconds, as sent."""
    import av

    with av.open(io.BytesIO(ts), format="mpegts") as container:
        stream = container.streams.video[0]
        for packet in container.demux(stream):
            if packet.pts is not None:
                return float(packet.pts * stream.time_base)
    raise ValueError("no video packet in the segment")


def video_time_s(seg0: bytes, segk: bytes) -> float:
    """Where ``segk``'s first frame sits on the video's clock: its pts less
    segment 0's, across a 2^33 wrap of the 90 kHz clock."""
    d = first_pts_s(segk) - first_pts_s(seg0)
    if d < 0:
        d += PTS_WRAP / PTS_HZ
    return d


# --- YouTube ---------------------------------------------------------------------------

def info(video_id: str, *, pot_provider=None, cookies=None) -> dict:
    """yt-dlp's metadata of a video, nothing downloaded."""
    import yt_dlp

    opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    if pot_provider:
        opts["extractor_args"] = {"youtubepot-bgutilhttp": {"base_url": [pot_provider]}}
    if cookies:
        opts["cookiefile"] = str(cookies)
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(source.canonical_url(video_id), download=False)


def pick_playlist(meta: dict) -> str | None:
    """The media playlist yt-dlp records: the tallest HLS video up to 1080p."""
    formats = [f for f in meta.get("formats", [])
               if str(f.get("protocol", "")).startswith("m3u8")
               and (f.get("height") or 0) <= 1080 and f.get("vcodec", "none") != "none"]
    return max(formats, key=lambda f: f.get("height") or 0)["url"] if formats else None


def media_playlist_url(video_id: str, *, pot_provider=None, cookies=None) -> str | None:
    return pick_playlist(info(video_id, pot_provider=pot_provider, cookies=cookies))


def fetch_text(url: str) -> str:
    import httpx

    return httpx.get(url, timeout=30.0).text


def fetch_bytes(url: str) -> bytes:
    import httpx

    r = httpx.get(url, timeout=30.0)
    r.raise_for_status()
    return r.content
