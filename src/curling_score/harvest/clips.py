"""Pull short clips out of a VOD without downloading the VOD.

A season is 130 videos of four and a quarter hours. Fetching them whole is
364 GB to keep a few thousand frames, so we take ten half-minute clips from each
and keep those instead -- about 6 GB for the season, and they are the archival
source, so the dataset can be rebuilt without touching YouTube again.

Two things went wrong on the way here, and the tests pin both.

**yt-dlp's own ``--download-sections`` cannot be used.** It drives ffmpeg
without ``-copyts``, so every section reports ``start_time=0.000000`` and
carries an unrecorded keyframe lead of up to 5 s. The clip forgets where in the
VOD it came from, which breaks the review deep-links and makes frame names
unreproducible. So we resolve the media URL ourselves and do the cut.

**``-t`` does not mean what it looks like here.** With ``-copyts`` the output
timestamps stay at absolute source time, so ``-t 30`` is already past its limit
before the first frame and ffmpeg writes an empty file. The stop time has to be
``-to``, as an input option alongside ``-ss``.

And one failure that has no clean fix, only guards: on one clip in sixty the
``-ss`` seek was silently ignored, so ffmpeg began at t=0 and worked its way
towards 2.3 GB. ``-fs`` bounds it, a timeout bounds it, and
:func:`verify_clip` catches it -- by checking the property we actually depend
on, which is that the clip knows its own absolute start.
"""

import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

# Resolution first, frame rate only as a tiebreaker, and the order matters.
# One VOD in a six-video sample offers no 1080p30 at all -- only 1080p60 (itag
# 299) -- and asking for "avc1, 30 fps or less" got 360p (itag 134), where a
# stone is about 7 px across instead of 20. Height is what decides whether a
# stone is detectable; fps only decides how many bytes we pay for it.
FORMAT = (
    "bv*[height=1080][vcodec^=avc1][fps<=30]"  # ideal: 1080p30 H.264
    "/bv*[height=1080][vcodec^=avc1]"          # 1080p H.264 at any frame rate
    "/bv*[height<=1080][vcodec^=avc1]"         # best H.264 we can get
    "/bv*[height<=1080]"                       # any codec, slower to decode
    "/bv*"
)

# Below this a stone is too small to label. The club streams 1080p; anything
# less means the format list was unusual and the video needs looking at, not
# silently harvesting at a resolution the detector cannot work with.
MIN_HEIGHT = 1080

LEAD_S = 6.0  # longer than the 5 s keyframe interval, so the window is covered
CLIP_SECONDS = 24.0
MAX_CLIP_BYTES = 64 * 1024 * 1024  # ~8x a good clip; only a runaway trips it
CUT_TIMEOUT_S = 300
SPAN = (0.04, 0.96)


class ClipError(RuntimeError):
    """A clip could not be cut, or came back not describing what was asked."""


@dataclass(frozen=True)
class MediaSource:
    video_id: str
    url: str
    headers: dict
    format_id: str
    fps: float
    height: int
    duration_s: float
    resolved_at: float


@dataclass(frozen=True)
class Clip:
    video_id: str
    requested_start_s: float
    seconds: float
    pts_start_s: float  # absolute source time of the clip's first frame
    path: Path
    size_bytes: int


def section_times(duration_s: float, n: int = 10, span=SPAN) -> list[float]:
    """``n`` evenly spaced moments across the middle of a video.

    The ends are skipped: the first and last minutes are warm-up and pack-up,
    with no play, and on some nights the lights are already out.
    """
    if n < 1:
        raise ValueError("need at least one section")
    lo, hi = span[0] * duration_s, span[1] * duration_s
    if n == 1:
        return [(lo + hi) / 2.0]
    return [lo + (hi - lo) * i / (n - 1) for i in range(n)]


def clip_path(root, video_id: str, start_s: float) -> Path:
    """Where a clip lives, named so the directory sorts chronologically."""
    return Path(root) / video_id / f"{int(round(start_s * 1000)):09d}.mkv"


def cut_command(url, headers, start_s: float, seconds: float, dest,
                lead_s: float = LEAD_S, max_bytes: int = MAX_CLIP_BYTES) -> list[str]:
    """The ffmpeg call that cuts one clip, keeping absolute timestamps.

    Matroska rather than mp4: mp4's ``avoid_negative_ts`` handling is what
    erases the offset, while mkv stores absolute segment timecodes that PyAV
    reads straight back.
    """
    hdr = "".join(f"{k}: {v}\r\n" for k, v in (headers or {}).items())
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-headers", hdr,
        "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
        # Both before -i: an input-side trim, in the input's own timeline.
        "-ss", f"{start_s - lead_s:.3f}",
        "-to", f"{start_s + seconds:.3f}",
        "-i", str(url),
        "-map", "0:v:0", "-c", "copy", "-copyts",
        "-avoid_negative_ts", "disabled",
        "-fs", str(int(max_bytes)),
        "-f", "matroska", str(dest),
    ]


def resolve(url: str, *, min_height: int = MIN_HEIGHT) -> MediaSource:
    """The direct media URL for a video, so we can range-request it ourselves.

    Resolved URLs are signed and expire in a few hours, which is ample: one
    video's ten clips take under a minute.

    Refuses anything below ``min_height`` rather than harvesting it: a video
    served at 360p would quietly contribute frames no one can label.
    """
    import yt_dlp

    from curling_score.ingest import source

    opts = {"quiet": True, "no_warnings": True, "noplaylist": True,
            "format": FORMAT, "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.sanitize_info(
            ydl.extract_info(source.canonical_url(url), download=False))
    fmt = (info.get("requested_formats") or [info])[0]
    height = int(fmt.get("height") or 0)
    if height < min_height:
        raise ClipError(
            f"{info['id']}: best format is {fmt.get('format_id')} at {height}p, "
            f"below the {min_height}p floor -- a stone would be unlabellable")
    return MediaSource(
        video_id=info["id"],
        url=fmt["url"],
        headers=fmt.get("http_headers") or {},
        format_id=str(fmt.get("format_id") or ""),
        fps=float(fmt.get("fps") or 0.0),
        height=height,
        duration_s=float(info.get("duration") or 0.0),
        resolved_at=time.time(),
    )


def verify_clip(path, start_s: float, seconds: float, lead_s: float = LEAD_S) -> float:
    """The clip's absolute start, or raise if it does not describe the request.

    This is the check that matters. A clip whose ``-ss`` was ignored starts at
    zero and holds the wrong hour of the game; one whose timestamps were reset
    cannot be turned back into a moment in the VOD. Both are caught here rather
    than discovered later in a dataset nobody can reproduce.
    """
    from curling_score.ingest import frames as F

    pts_start = F.stream_start_s(path)
    earliest = start_s - lead_s - 5.0  # the keyframe before the lead
    if not earliest <= pts_start <= start_s:
        raise ClipError(
            f"{Path(path).name}: starts at {pts_start:.3f}s, expected "
            f"{earliest:.3f}..{start_s:.3f}s -- the seek did not take")
    return pts_start


def download_clip(src: MediaSource, start_s: float, seconds: float = CLIP_SECONDS,
                  root=".", *, lead_s: float = LEAD_S, force: bool = False,
                  max_bytes: int = MAX_CLIP_BYTES,
                  timeout_s: float = CUT_TIMEOUT_S) -> Clip:
    """Cut one clip, reusing what is already on disk."""
    dest = clip_path(root, src.video_id, start_s)
    dest.parent.mkdir(parents=True, exist_ok=True)

    if not force and dest.exists() and dest.stat().st_size > 0:
        try:
            pts = verify_clip(dest, start_s, seconds, lead_s)
            return Clip(src.video_id, start_s, seconds, pts, dest,
                        dest.stat().st_size)
        except Exception:
            dest.unlink(missing_ok=True)  # cached but wrong; cut it again

    partial = dest.with_suffix(".partial.mkv")
    cmd = cut_command(src.url, src.headers, start_s, seconds, partial,
                      lead_s=lead_s, max_bytes=max_bytes)
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        partial.unlink(missing_ok=True)
        raise ClipError(f"{src.video_id}@{start_s:.0f}: timed out") from exc
    if done.returncode != 0:
        partial.unlink(missing_ok=True)
        raise ClipError(
            f"{src.video_id}@{start_s:.0f}: ffmpeg failed -- "
            f"{done.stderr.strip().splitlines()[-1] if done.stderr.strip() else 'no output'}")

    try:
        pts = verify_clip(partial, start_s, seconds, lead_s)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    partial.replace(dest)
    return Clip(src.video_id, start_s, seconds, pts, dest, dest.stat().st_size)


def download_clips(src: MediaSource, starts, seconds: float = CLIP_SECONDS,
                   root=".", *, jobs: int = 10, **kw):
    """Every clip for one video, in parallel.

    Parallel because YouTube throttles per connection, not per client: measured
    0.26 MB/s on one stream against 1.53 MB/s on ten, which is the difference
    between a seven-hour harvest and a one-hour one.

    Returns ``(clips, failures)`` -- a video that loses a clip is still worth
    having, so nothing raises here.
    """
    results, failures = [], []

    def one(start_s):
        try:
            return start_s, download_clip(src, start_s, seconds, root, **kw), None
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            return start_s, None, str(exc)

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for start_s, clip, err in pool.map(one, list(starts)):
            (results if clip else failures).append(clip if clip else f"@{start_s:.0f}: {err}")
    results.sort(key=lambda c: c.requested_start_s)
    return results, failures
