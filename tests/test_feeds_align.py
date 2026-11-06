"""Matching a piece of the YouTube picture to the long_a camera's file: the
offset that puts a camera game on the video's clock."""

from fractions import Fraction

import av
import numpy as np
import pytest

from curling_score.cameras import layout
from curling_score.feeds import align

FPS = 60


def _write(path, frames, size=(1920, 1080)):
    with av.open(str(path), "w", format="mpegts") as out:
        stream = out.add_stream("libx264", rate=FPS)
        stream.width, stream.height = size
        stream.pix_fmt = "yuv420p"
        stream.options = {"preset": "ultrafast", "x264-params": "keyint=60:bframes=0"}
        stream.codec_context.time_base = Fraction(1, FPS)
        for i, img in enumerate(frames):
            frame = av.VideoFrame.from_ndarray(img, format="rgb24")
            frame.pts = i
            for packet in stream.encode(frame):
                out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)


def synthetic_pair(tmp_path, *, piece_from_s, sheet, seconds=12.0, piece_s=3.0, still=False):
    """A long_a camera file of moving blocks, and a composite piece: ``piece_s``
    of it from ``piece_from_s``, its long_a crop placed where the layout puts
    it in a 1920x1080 picture, re-encoded."""
    p = next(q for q in layout.load(sheet).placements if q.role == "long_a")
    cx, cy, cw, ch = p.crop
    ox, oy, ow, oh = p.out
    rng = np.random.default_rng(7)
    n = int(seconds * FPS)
    blocks = rng.integers(0, [cw - 120, ch - 120], size=(n, 6, 2))
    camera = []
    for i in range(n):
        img = np.full((1080, 1920, 3), 90, np.uint8)
        for bx, by in blocks[0 if still else i]:
            img[cy + by:cy + by + 120, cx + bx:cx + bx + 120] = 230
        camera.append(img)
    cam = tmp_path / "long_a.ts"
    _write(cam, camera)
    first = int(round(piece_from_s * FPS))
    piece_frames = []
    for img in camera[first:first + int(piece_s * FPS)]:
        comp = np.full((1080, 1920, 3), 40, np.uint8)
        comp[oy:oy + oh, ox:ox + ow] = img[cy:cy + ch, cx:cx + cw]
        piece_frames.append(comp)
    piece = tmp_path / "piece.ts"
    _write(piece, piece_frames)
    return cam, piece


def test_match_finds_the_offset_to_a_frame(tmp_path):
    cam, piece = synthetic_pair(tmp_path, piece_from_s=4.25, sheet=3)
    # The piece's first frame is camera t 4.25 and video t 1004.25: offset 1000.
    m = align.match(piece, piece_t_s=1004.25, camera=cam, guess_s=1000.8, sheet=3, search_s=3)
    assert m is not None
    assert abs(m.offset_s - 1000.0) < 1 / FPS
    assert m.margin >= align.MIN_MARGIN


def test_a_still_picture_matches_nothing(tmp_path):
    cam, piece = synthetic_pair(tmp_path, piece_from_s=4.25, sheet=3, still=True)
    assert align.match(piece, 1004.25, cam, guess_s=1000.8, sheet=3, search_s=3) is None


def test_outside_the_camera_file_is_none(tmp_path):
    cam, piece = synthetic_pair(tmp_path, piece_from_s=4.25, sheet=3)
    assert align.match(piece, 1004.25, cam, guess_s=900.0, sheet=3, search_s=3) is None


def test_a_best_match_at_the_edge_of_the_search_is_none(tmp_path):
    """The true offset lies outside the window searched: the best of what was
    searched sits at its edge and says nothing."""
    cam, piece = synthetic_pair(tmp_path, piece_from_s=4.25, sheet=3)
    assert align.match(piece, 1004.25, cam, guess_s=1002.5, sheet=3, search_s=1.5) is None


def test_agree():
    assert align.agree(align.Match(600.0, 2.0), align.Match(600.03, 1.5))
    assert not align.agree(align.Match(600.0, 2.0), align.Match(600.05, 1.5))


# --- following the clock through a game ---------------------------------------

OLD, NEW = -908.94, -914.72


def m(offset):
    return align.Match(offset, 2.0)


def test_the_first_offset_takes_two_agreeing_checks():
    track = align.ClockTrack()
    track.add(300.0, m(OLD))
    assert track.steps() == [] and track.clock() is None
    track.add(900.0, m(OLD + 0.01))
    assert track.steps() == [[0.0, OLD]]
    assert track.clock() == {"kind": "cameras", "aligned": "segment", "offset_s": OLD,
                             "steps": [[0.0, OLD]]}


def test_a_lone_disagreeing_check_changes_nothing():
    track = align.ClockTrack()
    for t, o in ((300.0, OLD), (900.0, OLD), (1500.0, OLD - 3.0), (2100.0, OLD), (2700.0, None)):
        track.add(t, None if o is None else m(o))
    assert track.steps() == [[0.0, OLD]] and track.probe() is None


def test_a_step_is_placed_by_probes():
    """11/05 sheet 4: checks every 600 s; the video lost 5.78 s at ~3000."""
    def truth(video_t):
        return OLD if video_t < 3000.0 else NEW

    track = align.ClockTrack()
    for t in (300.0, 900.0, 1500.0, 2100.0, 2700.0, 3300.0, 3900.0):
        track.add(t, m(truth(t)))
    probes = 0
    while (t := track.probe()) is not None:
        track.add_probe(t, m(truth(t)))
        probes += 1
    assert probes <= align.MAX_PROBES
    (_, first), (camera_from, offset) = track.steps()
    assert first == OLD and offset == NEW
    assert abs((camera_from + NEW) - 3000.0) <= align.PLACE_S


def test_probing_gives_up_and_takes_the_middle():
    track = align.ClockTrack()
    for t, o in ((300.0, OLD), (900.0, OLD), (1500.0, NEW), (2100.0, NEW)):
        track.add(t, m(o))
    for _ in range(align.MAX_PROBES):
        t = track.probe()
        assert t is not None
        track.add_probe(t, None)          # nothing matched
    assert track.probe() is None
    (_, _), (camera_from, offset) = track.steps()
    assert offset == NEW and camera_from + NEW == pytest.approx((900.0 + 1500.0) / 2)


def test_track_recording_follows_a_kept_video(tmp_path):
    """The 11/05 sheet 4 game: checks every 600 s of the camera files, probes
    placing the step, every piece cut from the kept video at its video time."""
    def truth(video_t):
        return OLD if video_t < 3000.0 else NEW

    cut, looked = [], []

    def fake_cut(video, t, dur_s=5.0):
        cut.append(t)
        return tmp_path / f"piece-{t:.2f}.ts"

    def fake_match(piece, piece_t_s, camera, guess_s, sheet, *, search_s=30.0):
        looked.append((piece_t_s, guess_s))
        return align.Match(truth(piece_t_s), 2.0)

    track = align.track_recording(tmp_path / "v.mp4", tmp_path / "long_a.ts", 4, guess_s=-905.0,
                                  length_s=11698.0, video_length_s=7195.0,
                                  cut=fake_cut, match=fake_match)
    (_, first), (camera_from, offset) = track.steps()
    assert (first, offset) == (OLD, NEW)
    assert abs(camera_from + NEW - 3000.0) <= align.PLACE_S
    assert cut == [t for t, _ in looked]
    # Every check's piece is within the camera's span and the video's.
    assert all(0 <= t - g <= 11698.0 for t, g in looked)
    assert all(0 <= t and t + align.PIECE_S <= 7195.0 for t, _ in looked)


def test_an_unreadable_piece_is_no_match(tmp_path):
    """A piece cut past the video's end is an empty file."""
    empty = tmp_path / "piece.ts"
    empty.write_bytes(b"")
    assert align.match(empty, 7300.0, tmp_path / "long_a.ts", -908.0, sheet=4) is None
