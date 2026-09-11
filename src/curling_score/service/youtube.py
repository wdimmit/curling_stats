"""What we may ask YouTube directly: metadata, through the Data API.

Submission validation needs a video's channel, length and whether it is still
live. Asking yt-dlp for that from a datacenter address is exactly the request
YouTube blocks; the Data API answers it legitimately, from anywhere, for one
quota unit of a free ten thousand a day. So the API host never runs yt-dlp --
only the worker, on its residential connection, ever touches video bytes.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timezone

API = "https://www.googleapis.com/youtube/v3"
_DURATION_RE = re.compile(
    r"^P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")


@dataclass(frozen=True)
class VideoMeta:
    video_id: str
    title: str
    channel_id: str
    duration_s: float
    live_status: str          # "none" (an archived or plain video) | "live" | "upcoming"
    published_at: datetime | None = None


class SubmissionError(Exception):
    """A submission we will not accept, with an HTTP status and a plain reason."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def parse_duration(text: str) -> float:
    """ISO 8601 as the Data API writes it: ``PT3H59M52S``."""
    m = _DURATION_RE.match(text or "")
    if not m:
        raise ValueError(f"not an ISO 8601 duration: {text!r}")
    d, h, mi, s = (int(g) if g else 0 for g in m.groups())
    return float(d * 86400 + h * 3600 + mi * 60 + s)


def _parse_ts(text):
    if not text:
        return None
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


class YouTubeClient:
    """The two Data API calls the service makes, over httpx."""

    def __init__(self, api_key: str, http=None, timeout_s: float = 20.0):
        import httpx

        self._key = api_key
        self._http = http or httpx.Client(timeout=timeout_s)

    def _get(self, path, **params):
        params["key"] = self._key
        r = self._http.get(f"{API}/{path}", params=params)
        r.raise_for_status()
        return r.json()

    def videos(self, video_ids: list[str]) -> list[VideoMeta]:
        out = []
        for i in range(0, len(video_ids), 50):
            chunk = video_ids[i:i + 50]
            data = self._get("videos", part="snippet,contentDetails",
                             id=",".join(chunk), maxResults=50)
            for item in data.get("items", []):
                sn, cd = item["snippet"], item["contentDetails"]
                out.append(VideoMeta(
                    video_id=item["id"], title=sn.get("title", ""),
                    channel_id=sn.get("channelId", ""),
                    duration_s=parse_duration(cd.get("duration", "PT0S")),
                    live_status=sn.get("liveBroadcastContent", "none"),
                    published_at=_parse_ts(sn.get("publishedAt")),
                ))
        return out

    def video(self, video_id: str) -> VideoMeta | None:
        found = self.videos([video_id])
        return found[0] if found else None

    def playlist_video_ids(self, playlist_id: str, max_items: int = 5000) -> list[str]:
        ids, token = [], None
        while len(ids) < max_items:
            params = {"part": "contentDetails", "playlistId": playlist_id,
                      "maxResults": 50}
            if token:
                params["pageToken"] = token
            data = self._get("playlistItems", **params)
            ids.extend(it["contentDetails"]["videoId"] for it in data.get("items", []))
            token = data.get("nextPageToken")
            if not token:
                break
        return ids


class FakeYouTube:
    """Hand-fed metadata, for tests and for running without an API key."""

    def __init__(self, videos: dict[str, VideoMeta] | None = None,
                 playlists: dict[str, list[str]] | None = None):
        self._videos = dict(videos or {})
        self._playlists = dict(playlists or {})
        self.calls = 0

    def add(self, meta: VideoMeta):
        self._videos[meta.video_id] = meta

    def videos(self, video_ids):
        self.calls += 1
        return [self._videos[v] for v in video_ids if v in self._videos]

    def video(self, video_id):
        self.calls += 1
        return self._videos.get(video_id)

    def playlist_video_ids(self, playlist_id, max_items=5000):
        self.calls += 1
        if playlist_id not in self._playlists:
            raise KeyError(playlist_id)
        return list(self._playlists[playlist_id])[:max_items]


def validate_submission(meta: VideoMeta | None, allowed_channels: set[str] | None,
                        max_hours: float) -> VideoMeta:
    """Refuse what we cannot or should not process, with a reason a person can read."""
    if meta is None:
        raise SubmissionError(404, "not_found",
                              "That video could not be found on YouTube.")
    if allowed_channels and meta.channel_id not in allowed_channels:
        raise SubmissionError(403, "channel_not_allowed",
                              "Only videos from the club's own channel can be "
                              "processed here.")
    if meta.live_status != "none":
        raise SubmissionError(422, "still_live",
                              "That stream is still live or has not finished "
                              "processing on YouTube. Try again once the recording "
                              "is available.")
    if meta.duration_s <= 0:
        raise SubmissionError(422, "no_duration",
                              "YouTube reports no length for that video yet.")
    if meta.duration_s > max_hours * 3600:
        raise SubmissionError(422, "too_long",
                              f"That video is longer than {max_hours:g} hours.")
    return meta


class YtDlpYouTube:
    """Metadata through yt-dlp, for a host on a residential connection.

    The Data API is the right answer on Cloud Run; a laptop at home running the
    whole service for development has no key and no blocking problem, so it
    can ask YouTube the way the worker does. Never use this on a datacenter IP.
    """

    def video(self, video_id: str) -> VideoMeta | None:
        from curling_score.ingest import source

        try:
            info = source.fetch_info(video_id)
        except Exception:  # noqa: BLE001 - unavailable, private, blocked...
            return None
        return VideoMeta(
            video_id=info.video_id, title=info.title, channel_id=info.channel_id or "",
            duration_s=info.duration_s,
            live_status="live" if info.is_live else "none",
            published_at=(datetime.strptime(info.upload_date, "%Y%m%d").replace(tzinfo=timezone.utc)
                          if info.upload_date else None),
        )

    def videos(self, video_ids):
        return [m for m in (self.video(v) for v in video_ids) if m is not None]

    def playlist_video_ids(self, playlist_id: str, max_items: int = 5000) -> list[str]:
        import yt_dlp

        opts = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist",
                "skip_download": True, "playlistend": max_items}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/playlist?list={playlist_id}",
                                    download=False)
        return [e["id"] for e in info.get("entries", []) if e and e.get("id")]
