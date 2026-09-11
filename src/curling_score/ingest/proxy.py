"""A cropped proxy video of the overhead strip.

Analysis is decode-bound, not detection-bound: reading a 4-hour 1080p stream at
2 fps costs about 3.2 minutes of decoding against 1.2 minutes of detection, and
cropping afterwards saves nothing because the codec has already reconstructed
every pixel. Decoding *fewer pixels* is the only real lever.

The overhead strip is roughly a sixteenth of the frame, so transcoding it once
into its own small video makes every later pass 3-4x cheaper:

    full 1080p, decode all           3.2 min per 4 h pass
    full 1080p, skip non-reference   2.5 min
    strip proxy 297x1060             1.0 min
    strip proxy + skip non-reference 0.8 min

Building the proxy costs about nine minutes for a four-hour video (measured),
so a single cold run is slower overall; it wins on every run after that, which
is what iteration actually looks like. It is cached
beside the video and keyed by the crop, so a re-detected layout rebuilds it.
"""

import subprocess
from pathlib import Path

# Quality matters more than size here: the strip is small and stones are ~20 px,
# so we keep the encode visually lossless rather than saving disk.
CRF = 18
PRESET = "veryfast"
# The activity profile reads keyframes, so the proxy must carry them at the same
# spacing as the source or the profile silently loses samples. These club
# streams key every 5 s at 30 fps; left to itself x264 chose 8.33 s, which cost
# 40% of the profile and invented a spurious one-end "game" out of a changeover.
GOP_FRAMES = 150


def strip_rect(top, bottom):
    """The smallest even-sized rect covering both overhead panels."""
    x = min(top[0], bottom[0])
    y = min(top[1], bottom[1])
    x1 = max(top[0] + top[2], bottom[0] + bottom[2])
    y1 = max(top[1] + top[3], bottom[1] + bottom[3])
    w, h = x1 - x, y1 - y
    # H.264 needs even dimensions for 4:2:0 chroma.
    return (x, y, w + (w % 2), h + (h % 2))


def encode_args():
    """ffmpeg encoder arguments for a proxy that stands in for the source."""
    return [
        "-c:v", "libx264", "-preset", PRESET, "-crf", str(CRF),
        # Fixed GOP with scene-cut keyframes disabled: keyframe spacing has to
        # be predictable, not a function of the content.
        "-g", str(GOP_FRAMES), "-keyint_min", str(GOP_FRAMES), "-sc_threshold", "0",
        "-an", "-sn", "-dn",
    ]


def _key(strip) -> str:
    return "x".join(str(v) for v in strip)


def proxy_path(vid, strip, root=None) -> Path:
    from curling_score.ingest.cache import default_root

    root = Path(root) if root is not None else default_root()
    return root / "proxies" / f"{vid}.{_key(strip)}.mp4"


def is_cached(vid, strip, root=None) -> bool:
    p = proxy_path(vid, strip, root)
    return p.is_file() and p.stat().st_size > 0


def translate(rect, strip):
    """Move a full-frame rect into the proxy's coordinate system."""
    x, y, w, h = rect
    sx, sy, sw, sh = strip
    if x < sx or y < sy or x + w > sx + sw or y + h > sy + sh:
        raise ValueError(f"rect {rect} does not lie inside strip {strip}")
    return (x - sx, y - sy, w, h)


def ensure_proxy(video_path, vid, strip, root=None, progress=None) -> Path:
    """Return a proxy of the strip, building it only if it is not cached."""
    dest = proxy_path(vid, strip, root)
    if is_cached(vid, strip, root):
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    x, y, w, h = strip
    tmp = dest.with_suffix(".partial.mp4")
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-i", str(video_path),
        "-vf", f"crop={w}:{h}:{x}:{y}",
        *encode_args(),
        str(tmp),
    ]
    if progress:
        progress(f"building strip proxy {w}x{h} (one-off, ~9 min for a 4 h video)")
    subprocess.run(cmd, check=True)
    tmp.replace(dest)
    return dest
