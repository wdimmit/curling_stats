"""The lane keeper: a grey strip of the delivery lane, kept from the long
camera's own decodes so the release pass never decodes it again."""
import numpy as np
import pytest

from curling_score.detect import lanekeep, longview
from curling_score.geometry.sideview import SideView

# 10/20 Wed sheet 2's left view.
VIEW = SideView(rect=(0, 0, 810, 1080), tee_row=360.8, hog_row=433.0,
                centre_col=399.21, lat_px_per_m_at_tee=117.425,
                centre_line=(394.038, 0.007471))
FPS = lanekeep.SOURCE_FPS


def synthetic(view, t0, n, fps=FPS):
    """``n`` frames of the view's size labelled the way `longview.decode`
    labels them, each filled with its own source frame index (mod 256)."""
    _x, _y, w, h = view.rect
    first = int(np.ceil(t0 * FPS - 1e-6))
    frames = np.empty((n, h, w, 3), dtype=np.uint8)
    for i in range(n):
        frames[i] = (first + i) % 256
    return frames, [t0 + i / fps for i in range(n)]


def test_keeps_every_source_frame_inside_a_window_once():
    k = lanekeep.LaneKeeper(VIEW, windows=[(10.0, 11.0)])
    frames, times = synthetic(VIEW, t0=9.5, n=60)        # 30 fps, 9.5 .. 11.47
    k.offer(VIEW.rect, frames, times, FPS)
    k.offer(VIEW.rect, frames, times, FPS)               # a second decode of the same frames
    strips, ts = k.frames_between(0, 99)
    assert ts.min() >= 10.0 and ts.max() <= 11.0 and len(ts) == len(set(ts)) == 30
    assert ts.max() < 11.0                               # the window is half-open
    assert strips.shape[0] == 30 and np.all(np.diff(ts) > 0)


def test_two_decodes_of_one_frame_land_on_one_key():
    # A decode labels frame i t0 + i/fps, but ffmpeg returns the first source
    # frame at or after t0. Two decodes whose t0 differ by a fraction of a
    # frame read the same source frames under different labels.
    k = lanekeep.LaneKeeper(VIEW, windows=[(10.0, 11.0)])
    a = synthetic(VIEW, t0=9.5, n=60)
    b = synthetic(VIEW, t0=9.5 + 0.4 / FPS, n=60)
    k.offer(VIEW.rect, *a, FPS)
    k.offer(VIEW.rect, *b, FPS)
    strips, ts = k.frames_between(0, 99)
    assert len(ts) == 30
    # Each stored time is the frame's own, so its pixels are its index.
    assert np.allclose(ts * FPS, np.round(ts * FPS))
    assert np.array_equal(strips[:, 0, 0], np.round(ts * FPS).astype(int) % 256)


def test_another_cameras_frames_are_not_kept():
    k = lanekeep.LaneKeeper(VIEW, windows=[(0, 99)])
    k.offer((1107, 0, 813, 1080), *synthetic(VIEW, t0=1.0, n=10), FPS)
    assert k.frames_between(0, 99)[0].shape[0] == 0


def test_a_ten_fps_decode_is_not_kept():
    # Other passes read this camera at 10 fps; their labels lag the true frame.
    k = lanekeep.LaneKeeper(VIEW, windows=[(0, 99)])
    k.offer(VIEW.rect, *synthetic(VIEW, t0=1.0, n=10, fps=10.0), 10.0)
    assert k.frames_between(0, 99)[0].shape[0] == 0
    assert k.nbytes == 0


def test_frames_outside_every_window_are_not_kept():
    k = lanekeep.LaneKeeper(VIEW, windows=[(1.0, 1.1), (2.0, 2.1)])
    k.offer(VIEW.rect, *synthetic(VIEW, t0=0.0, n=100), FPS)
    _strips, ts = k.frames_between(0, 99)
    assert len(ts) == 6
    assert np.allclose(sorted(ts), [1.0, 1 + 1 / 30, 1 + 2 / 30, 2.0, 2 + 1 / 30, 2 + 2 / 30])


def test_every_keeps_each_nth_source_frame():
    k = lanekeep.LaneKeeper(VIEW, windows=[(0, 99)], every=2)
    k.offer(VIEW.rect, *synthetic(VIEW, t0=1.0, n=10), FPS)
    _strips, ts = k.frames_between(0, 99)
    assert [int(round(t * FPS)) % 2 for t in ts] == [0] * 5


def test_frames_between_is_time_sorted_and_half_open():
    k = lanekeep.LaneKeeper(VIEW, windows=[(0, 99)])
    k.offer(VIEW.rect, *synthetic(VIEW, t0=5.0, n=30), FPS)
    k.offer(VIEW.rect, *synthetic(VIEW, t0=4.0, n=30), FPS)    # earlier, offered later
    _strips, ts = k.frames_between(4.5, 5.5)
    assert len(ts) == 30 and np.all(np.diff(ts) > 0)
    assert ts[0] == pytest.approx(4.5) and ts[-1] < 5.5


def test_strip_is_grey_of_the_lane_box():
    r0, r1, c0, c1 = lanekeep.strip_box(VIEW)
    k = lanekeep.LaneKeeper(VIEW, windows=[(0, 99)])
    assert k.origin == (c0, r0)
    frames, times = synthetic(VIEW, t0=1.0, n=2)
    frames[:] = 0
    frames[0, r0, c0] = (255, 0, 0)            # pure red
    k.offer(VIEW.rect, frames, times, FPS)
    strips, _ts = k.frames_between(0, 99)
    assert strips.shape == (2, r1 - r0, c1 - c0) and strips.dtype == np.uint8
    assert strips[0, 0, 0] == 76               # RGB2GRAY: 0.299 * 255
    assert strips[0, 0, 1] == 0
    # A copy: the strip does not pin the decode's frames.
    frames[:] = 200
    assert k.frames_between(0, 99)[0][1, 0, 0] == 0


def test_strip_box_covers_the_lane_inside_the_view():
    r0, r1, c0, c1 = lanekeep.strip_box(VIEW)
    top = VIEW.row_for(lanekeep.FROM_M)
    bottom = VIEW.row_for(lanekeep.TO_M)
    assert 0 <= r0 < top and bottom < r1 <= VIEW.rect[3]
    assert 0 <= c0 < c1 <= VIEW.rect[2]
    # 1.5 m either side of the centre line on the nearer (lower) row.
    centre = VIEW.centre_col_at(bottom)
    reach = lanekeep.ACROSS_M * VIEW.lateral_px_per_m(bottom)
    assert c0 <= centre - reach + 1 and c1 >= centre + reach - 1


def test_strip_box_is_clipped_to_the_view():
    narrow = SideView(rect=(0, 0, 300, 500), tee_row=360.8, hog_row=433.0,
                      centre_col=399.21, lat_px_per_m_at_tee=117.425,
                      centre_line=(394.038, 0.007471))
    r0, r1, c0, c1 = lanekeep.strip_box(narrow)
    assert 0 <= r0 < r1 <= 500 and 0 <= c0 < c1 <= 300


def test_decode_offers_frames_only_while_keeping(monkeypatch):
    seen = []
    k = lanekeep.LaneKeeper(VIEW, windows=[(0, 99)])
    monkeypatch.setattr(k, "offer", lambda *a: seen.append(a))
    got = synthetic(VIEW, t0=1.0, n=3)
    monkeypatch.setattr(longview, "_decode_now", lambda *a: got)
    monkeypatch.setattr(longview, "_lead", lambda video, t0: 0.0)

    assert longview.decode("v.mp4", VIEW.rect, 1.0, 1.1, 10.0) is got
    assert seen == []                                  # not keeping

    with longview.keeping(k):
        assert longview.decode("v.mp4", VIEW.rect, 1.0, 1.1, 10.0) is got
        assert longview.decode("v.mp4", VIEW.rect, 1.0, 1.1) is got
    assert [(a[0], a[3]) for a in seen] == [(VIEW.rect, 10.0), (VIEW.rect, 30.0)]
    assert seen[0][1] is got[0] and seen[0][2] is got[1]

    longview.decode("v.mp4", VIEW.rect, 1.0, 1.1)      # the context closed
    assert len(seen) == 2


def test_decode_still_returns_its_frames_when_the_keeper_raises(monkeypatch, caplog):
    class Broken:
        def offer(self, *a):
            raise RuntimeError("keeper broke")

    got = synthetic(VIEW, t0=1.0, n=3)
    monkeypatch.setattr(longview, "_decode_now", lambda *a: got)
    monkeypatch.setattr(longview, "_lead", lambda video, t0: 0.0)
    with longview.keeping(Broken()):
        assert longview.decode("v.mp4", VIEW.rect, 1.0, 1.1) is got
    assert "keeper broke" in caplog.text


def test_decode_feeds_the_keeper_from_the_read_ahead_too(monkeypatch):
    seen = []
    k = lanekeep.LaneKeeper(VIEW, windows=[(0, 99)])
    monkeypatch.setattr(k, "offer", lambda *a: seen.append(a))
    got = synthetic(VIEW, t0=1.0, n=3)

    class Ahead:
        def take(self, key):
            return got

    monkeypatch.setattr(longview, "_reading", Ahead())
    with longview.keeping(k):
        assert longview.decode("v.mp4", VIEW.rect, 1.0, 1.1) is got
    assert len(seen) == 1 and seen[0][1] is got[0]


def test_decode_offers_nothing_for_an_empty_window(monkeypatch):
    k = lanekeep.LaneKeeper(VIEW, windows=[(0, 99)])
    seen = []
    monkeypatch.setattr(k, "offer", lambda *a: seen.append(a))
    monkeypatch.setattr(longview, "_decode_now",
                        lambda *a: (np.empty((0, 4, 4, 3), np.uint8), []))
    monkeypatch.setattr(longview, "_lead", lambda video, t0: 0.0)
    with longview.keeping(k):
        longview.decode("v.mp4", VIEW.rect, 2.0, 1.0)
    assert seen == []


def test_nbytes_and_clear():
    k = lanekeep.LaneKeeper(VIEW, windows=[(0, 99)])
    r0, r1, c0, c1 = lanekeep.strip_box(VIEW)
    k.offer(VIEW.rect, *synthetic(VIEW, t0=1.0, n=4), FPS)
    assert k.nbytes == 4 * (r1 - r0) * (c1 - c0)
    k.clear()
    assert k.nbytes == 0 and k.frames_between(0, 99)[0].shape[0] == 0


def test_a_sixteen_rock_end_stays_under_budget():
    r0, r1, c0, c1 = lanekeep.strip_box(VIEW)
    per_end = 16 * 8.0 * (lanekeep.SOURCE_FPS / lanekeep.KEEP_EVERY) * (r1 - r0) * (c1 - c0)
    assert per_end < 400e6


def test_rewindow_keeps_for_the_new_windows_and_lets_go_of_the_rest():
    # A rock the long camera gave a release after the keeper was made: its
    # seconds move, and what was kept for the old ones goes.
    k = lanekeep.LaneKeeper(VIEW, windows=[(10.0, 11.0), (20.0, 21.0)])
    k.offer(VIEW.rect, *synthetic(VIEW, t0=10.0, n=30), FPS)
    k.offer(VIEW.rect, *synthetic(VIEW, t0=20.0, n=30), FPS)
    k.rewindow([(10.5, 12.0), (20.0, 21.0)])
    assert k.windows == [(10.5, 12.0), (20.0, 21.0)]
    _strips, ts = k.frames_between(0, 99)
    assert len(ts) == 15 + 30 and ts.min() >= 10.5 - 1e-9
    # The new window's seconds are kept from the next decode on.
    k.offer(VIEW.rect, *synthetic(VIEW, t0=11.0, n=30), FPS)
    _strips, ts = k.frames_between(11.0, 12.0)
    assert len(ts) == 30
