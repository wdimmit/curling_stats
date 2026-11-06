"""A live stream's HLS segments, and where one sits on the video's clock."""

import io
from fractions import Fraction

import av
import numpy as np
import pytest

from curling_score.live import hls

PLAYLIST = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:5
#EXT-X-MEDIA-SEQUENCE:{seq}
#EXT-X-PROGRAM-DATE-TIME:2026-11-05T18:00:09.120+00:00
#EXTINF:5.005,
sq/0/seg.ts
#EXTINF:5.005,
sq/1/seg.ts
#EXTINF:4.990,
https://other.example/sq/2/seg.ts
"""
BASE = "https://manifest.example/api/manifest/hls_playlist/index.m3u8"


def ts_bytes(first_pts, frames=3):
    """A few frames of H.264 in MPEG-TS whose first packet's pts is ``first_pts``
    (90 kHz), as a YouTube segment carries the stream's own clock."""
    buf = io.BytesIO()
    with av.open(buf, "w", format="mpegts", options={"mpegts_copyts": "1"}) as out:
        stream = out.add_stream("libx264", rate=60)
        stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
        stream.options = {"preset": "ultrafast", "x264-params": "bframes=0"}
        stream.codec_context.time_base = Fraction(1, 90_000)
        for i in range(frames):
            frame = av.VideoFrame.from_ndarray(np.zeros((48, 64, 3), np.uint8), format="rgb24")
            frame.pts = first_pts + i * 1500
            frame.time_base = Fraction(1, 90_000)
            for packet in stream.encode(frame):
                out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)
    return buf.getvalue()


def test_segments_are_numbered_from_the_sequence_with_absolute_uris():
    segs = hls.segments(PLAYLIST.format(seq=0), BASE)
    assert [s.seq for s in segs] == [0, 1, 2]
    assert segs[0].uri == "https://manifest.example/api/manifest/hls_playlist/sq/0/seg.ts"
    assert segs[2].uri == "https://other.example/sq/2/seg.ts"
    assert [s.extinf_s for s in segs] == [5.005, 5.005, 4.99]


def test_a_playlist_not_from_the_start_has_no_video_time():
    with pytest.raises(hls.NoStart):
        hls.segments(PLAYLIST.format(seq=7), BASE, from_start=True)
    assert hls.segments(PLAYLIST.format(seq=7), BASE)[0].seq == 7


def test_video_time_is_pts_against_segment_0():
    seg0, segk = ts_bytes(126_000), ts_bytes(126_000 + round(90_000 * 15.015))
    assert hls.first_pts_s(seg0) == pytest.approx(1.4, abs=1e-6)
    assert hls.video_time_s(seg0, segk) == pytest.approx(15.015, abs=1e-4)


def test_video_time_across_a_pts_wrap():
    wrap = 2 ** 33
    seg0, segk = ts_bytes(wrap - 90_000), ts_bytes(9_000)
    assert hls.video_time_s(seg0, segk) == pytest.approx(1.1, abs=1e-4)


def test_program_date_time_of_the_first_segment():
    when = hls.program_date_time(PLAYLIST.format(seq=0))
    assert when.timestamp() == pytest.approx(1793901609.12)
    assert hls.program_date_time("#EXTM3U\n") is None


def test_the_recorder_still_reads_where_a_playlist_begins():
    from curling_score.live import recorder

    assert recorder.first_media_sequence(PLAYLIST.format(seq=900)) == 900
    assert hls.first_media_sequence(PLAYLIST.format(seq=0)) == 0
