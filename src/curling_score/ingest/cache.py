"""Local media cache. Download a VOD once, reuse it for every analysis run.

We fetch a video-only H.264 stream: audio is useless for this pipeline, and
avc1 decodes several times faster than VP9/AV1. Downloading rather than
streaming also sidesteps googlevideo URL expiry part-way through a long run and
makes reruns byte-identical, which matters for reproducible CV.

Every cache -- videos, proxies, detections -- lives under one root, chosen by
``$CURLING_SCORE_CACHE`` so a worker can put it on whatever disk it likes.
"""

import os
import secrets
import time
from pathlib import Path

from curling_score.ingest.source import canonical_url, video_id

# 1080p matters: the overhead strip is only ~297 px wide at 1080p, so a stone is
# ~20 px across. At 720p that drops to ~14 px and detection accuracy suffers.
FORMAT = "bv*[height<=1080][vcodec^=avc1]/bv*[height<=1080]/bv*"

# YouTube's refusals, as they surface in yt-dlp's messages. A burst of requests
# from one address once got the whole public IP blocked for a quarter of an
# hour; these are what that looked like.
BLOCK_HINTS = ("sign in to confirm", "not a bot", "429", "too many requests")
# Wait it out rather than spend the next hour rediscovering it.
BACKOFF_S = (300, 600, 1200)

# Cached media carries a fixed modification time. The detection cache keys on a
# file's name, size and mtime; the mtime is there to notice a file that changed
# in place, which a download never does -- the same bytes re-fetched to a fresh
# file must look like the same file, or every rebuild throws the whole
# detection cache away.
PINNED_MTIME_NS = 1_000_000_000 * 10**9  # 2001-09-09, arbitrary and obviously so


class BlockedError(RuntimeError):
    """YouTube declined to serve the video to this client.

    Distinct from every other failure because the remedy is different: not a
    retry now, but a wait, another address, or a signed-in session.
    """


def default_root() -> Path:
    env = os.environ.get("CURLING_SCORE_CACHE")
    return Path(env) if env else Path.home() / ".cache" / "curling_score"


def video_path(vid: str, root: Path | None = None) -> Path:
    root = Path(root) if root is not None else default_root()
    return root / "videos" / f"{vid}.mp4"


def is_cached(vid: str, root: Path | None = None) -> bool:
    p = video_path(vid, root)
    return p.is_file() and p.stat().st_size > 0


def is_blocked_message(message: str) -> bool:
    low = str(message).lower()
    return any(h in low for h in BLOCK_HINTS)


def pin_mtime(path) -> None:
    """Give a cached file the fixed timestamp every rebuild of it shares."""
    os.utime(path, ns=(PINNED_MTIME_NS, PINNED_MTIME_NS))


def _ytdlp_download(url: str, opts: dict) -> None:
    import yt_dlp

    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])


def _hook(progress_hook):
    """Adapt yt-dlp's progress dictionaries to ``progress_hook(fraction, msg)``."""
    if progress_hook is None:
        return []

    def on_progress(d):
        status = d.get("status")
        if status == "finished":
            progress_hook(1.0, "download finished")
            return
        if status != "downloading":
            return
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        done = d.get("downloaded_bytes") or 0
        frac = (done / total) if total else 0.0
        progress_hook(max(0.0, min(1.0, frac)),
                      f"downloading {done / 1e6:.0f} MB")

    return [on_progress]


def ensure_cached(url: str, root: Path | None = None, progress: bool = True, *,
                  progress_hook=None, cookies=None, pot_provider=None,
                  attempts: int = len(BACKOFF_S) + 1, sleep=time.sleep,
                  downloader=_ytdlp_download) -> Path:
    """Return a local path for the video, downloading it only if absent.

    ``attempts`` counts tries against a *blocked* response; other errors are
    raised at once. The CLI waits out blocks in-process; a queue-driven worker
    passes ``attempts=1`` and lets the queue reschedule instead, so the same
    wait is never served twice.
    """
    vid = video_id(url)
    dest = video_path(vid, root)
    if is_cached(vid, root):
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)
    # A name no other process will pick, renamed into place only when whole,
    # so a crash or a second worker cannot leave a half-file that later loads.
    tmp = dest.with_name(f"{vid}.part-{os.getpid()}-{secrets.token_hex(4)}.mp4")
    opts = {
        "noplaylist": True,
        "format": FORMAT,
        "outtmpl": str(tmp),
        "concurrent_fragment_downloads": 8,
        "quiet": not progress,
        "no_warnings": True,
        "socket_timeout": 30,
        "retries": 10,
        "fragment_retries": 10,
        "progress_hooks": _hook(progress_hook),
    }
    cookies = cookies or os.environ.get("YTDLP_COOKIES")
    if cookies:
        opts["cookiefile"] = str(cookies)
    pot_provider = pot_provider or os.environ.get("YTDLP_POT_PROVIDER")
    if pot_provider:
        opts["extractor_args"] = {
            "youtubepot-bgutilhttp": {"base_url": [pot_provider]},
        }

    try:
        for attempt in range(max(1, attempts)):
            try:
                downloader(canonical_url(url), opts)
                break
            except Exception as exc:  # noqa: BLE001 - yt-dlp raises many kinds
                message = f"{type(exc).__name__}: {exc}"
                if not is_blocked_message(message):
                    raise
                if attempt + 1 >= attempts:
                    raise BlockedError(message) from exc
                sleep(BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)])
        if not (tmp.is_file() and tmp.stat().st_size > 0):
            raise RuntimeError(f"download did not produce a usable file at {tmp}")
        tmp.replace(dest)
        pin_mtime(dest)
    finally:
        tmp.unlink(missing_ok=True)
    return dest
