"""Local media cache. Download a VOD once, reuse it for every analysis run.

We fetch a video-only H.264 stream: audio is useless for this pipeline, and
avc1 decodes several times faster than VP9/AV1. Downloading rather than
streaming also sidesteps googlevideo URL expiry part-way through a long run and
makes reruns byte-identical, which matters for reproducible CV.
"""

from pathlib import Path

from curling_score.ingest.source import canonical_url

# 1080p matters: the overhead strip is only ~297 px wide at 1080p, so a stone is
# ~20 px across. At 720p that drops to ~14 px and detection accuracy suffers.
FORMAT = "bv*[height<=1080][vcodec^=avc1]/bv*[height<=1080]/bv*"


def default_root() -> Path:
    return Path.home() / ".cache" / "curling_score"


def video_path(vid: str, root: Path | None = None) -> Path:
    root = Path(root) if root is not None else default_root()
    return root / "videos" / f"{vid}.mp4"


def is_cached(vid: str, root: Path | None = None) -> bool:
    p = video_path(vid, root)
    return p.is_file() and p.stat().st_size > 0


def ensure_cached(url: str, root: Path | None = None, progress: bool = True) -> Path:
    """Return a local path for the video, downloading it only if absent."""
    import yt_dlp

    from curling_score.ingest.source import video_id

    vid = video_id(url)
    dest = video_path(vid, root)
    if is_cached(vid, root):
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)
    opts = {
        "noplaylist": True,
        "format": FORMAT,
        "outtmpl": str(dest),
        "concurrent_fragment_downloads": 8,
        "quiet": not progress,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([canonical_url(url)])

    if not is_cached(vid, root):
        raise RuntimeError(f"download did not produce a usable file at {dest}")
    return dest
