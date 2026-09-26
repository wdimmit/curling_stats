"""The side views decode the same frames from a live MPEG-TS recording as from
the finished MP4.

An MPEG-TS file has no seek index. ffmpeg's input seek can land past the
keyframe before the window, and the decoder then throws away everything up to
the next keyframe: on a live recording a 4 s window came back starting 2 s
late with 62 of its 122 frames, and the 1 s broom window often came back
empty -- no brooms, so no lines.
"""

import shutil
import subprocess

import numpy as np
import pytest

from curling_score.detect import longview

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")

RECT = (0, 0, 320, 240)


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    """Forty seconds of a changing test pattern, keyframes 5 s apart, as an MP4
    and as the MPEG-TS a live recorder writes."""
    d = tmp_path_factory.mktemp("clips")
    mp4, ts = d / "clip.mp4", d / "clip.ts"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", "testsrc2=size=320x240:rate=30", "-t", "40", "-g", "150",
         "-keyint_min", "150", "-sc_threshold", "0", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", str(mp4)], check=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(mp4),
                    "-c", "copy", "-f", "mpegts", str(ts)], check=True)
    return mp4, ts


@pytest.mark.parametrize("t0", [11.0, 17.3, 23.9])
@pytest.mark.parametrize("fps", [30.0, 10.0, 5.0])
def test_a_window_from_the_recording_is_the_window_from_the_video(clips, t0, fps):
    mp4, ts = clips
    want, want_t = longview.decode(mp4, RECT, t0, t0 + 3.0, fps=fps)
    got, got_t = longview.decode(ts, RECT, t0, t0 + 3.0, fps=fps)
    assert len(got) == len(want)
    assert got_t == want_t
    assert all(np.array_equal(a, b) for a, b in zip(got, want))
