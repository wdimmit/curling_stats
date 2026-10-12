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


# The pipeline's own frame reader has the same trouble: PyAV's seek into an
# MPEG-TS lands on the keyframe *after* the target. A window asked for from
# 2000 s of a live recording began at 2005 s -- the start of every end's
# run-up, on both panels, lost.
@pytest.mark.parametrize("start", [10.0, 12.0, 17.3, 24.9])
def test_a_window_of_the_recording_starts_where_the_videos_does(clips, start):
    from curling_score.ingest import frames as F

    mp4, ts = clips
    want = [t for t, _ in F.window(mp4, start, start + 3.0, 10.0, crop=RECT)]
    got = [t for t, _ in F.window(ts, start, start + 3.0, 10.0, crop=RECT)]
    assert got == pytest.approx(want, abs=1e-3)


@pytest.mark.parametrize("start", [10.0, 12.0, 17.3])
def test_a_keyframe_sweep_of_the_recording_sees_the_videos_keyframes(clips, start):
    from curling_score.ingest import frames as F

    mp4, ts = clips
    want = [t for t, _ in F.keyframe_sweep(mp4, decode=False, start_s=start, end_s=start + 12)]
    got = [t for t, _ in F.keyframe_sweep(ts, decode=False, start_s=start, end_s=start + 12)]
    assert got == pytest.approx(want, abs=1e-3)


class TestEnteringJustBeforeTheKeyframe:
    """A recording is entered whole seconds before the keyframe a window
    needs, not a flat 10 s, and still gives the video's frames."""

    def test_the_keyframes_are_listed_from_the_packets(self, clips):
        from curling_score.ingest import frames as F

        _mp4, ts = clips
        times, read_to = F.keyframe_times(ts, 12.0, 30.0)
        assert [round(t, 2) for t in times if 9.0 <= t <= 31.0] == [10.0, 15.0, 20.0, 25.0, 30.0]
        assert read_to >= 30.0

    @pytest.mark.parametrize("t0, lead", [(17.3, 4.0), (15.5, 2.0), (19.99, 6.0)])
    def test_the_lead_is_whole_seconds_short_of_ten(self, clips, t0, lead):
        _mp4, ts = clips
        longview._keyframes.clear()
        assert longview._lead(ts, t0) == lead

    def test_an_indexed_video_needs_none(self, clips):
        mp4, _ts = clips
        assert longview._lead(mp4, 17.3) == 0.0

    def test_a_listing_that_is_wrong_costs_time_not_frames(self, clips, monkeypatch):
        mp4, ts = clips
        # A keyframe claimed at 17.0 that is not one: entered at 15.3, past
        # the real one at 15, the decoder drops all it has until 20.
        monkeypatch.setattr(longview, "_keyframe_before", lambda video, t0: 17.0)
        want, _ = longview.decode(mp4, RECT, 17.3, 20.3, fps=10.0)
        got, _ = longview.decode(ts, RECT, 17.3, 20.3, fps=10.0)
        assert len(got) == len(want)
        assert all(np.array_equal(a, b) for a, b in zip(got, want))


@pytest.mark.parametrize("t0", [11.0, 14.99, 15.01, 15.5, 17.3, 19.97, 23.9, 31.234])
@pytest.mark.parametrize("fps", [30.0, 10.0, 5.0, 4.0])
def test_the_short_lead_reads_what_the_flat_lead_read(clips, t0, fps):
    """Whatever the recording gave before, frame for frame -- including where
    that is not quite the video's (a 30 fps window's phase can differ)."""
    from curling_score.ingest.frames import TS_SEEK_LEAD_S

    _mp4, ts = clips
    longview._keyframes.clear()
    assert longview._lead(ts, t0) < TS_SEEK_LEAD_S
    want, want_t = longview._run(
        longview._command(ts, RECT, t0, t0 + 3.0, fps, TS_SEEK_LEAD_S), RECT, t0, fps)
    got, got_t = longview.decode(ts, RECT, t0, t0 + 3.0, fps=fps)
    assert got_t == want_t
    assert got.tobytes() == want.tobytes()


class TestReadingAhead:
    def test_a_window_read_ahead_is_the_window_read_then(self, clips):
        _mp4, ts = clips
        want, want_t = longview.decode(ts, RECT, 17.3, 20.3, fps=10.0)
        with longview.reading_ahead() as ahead:
            longview.soon(ts, RECT, 17.3, 20.3, fps=10.0)
            assert len(ahead.waiting) == 1
            got, got_t = longview.decode(ts, RECT, 17.3, 20.3, fps=10.0)
            assert not ahead.waiting
        assert got_t == want_t
        assert all(np.array_equal(a, b) for a, b in zip(got, want))

    def test_a_window_not_announced_is_decoded_then(self, clips):
        _mp4, ts = clips
        want, _ = longview.decode(ts, RECT, 11.0, 12.0, fps=5.0)
        with longview.reading_ahead() as ahead:
            longview.soon(ts, RECT, 11.0, 12.5, fps=5.0)      # not what is asked for
            got, _ = longview.decode(ts, RECT, 11.0, 12.0, fps=5.0)
            assert len(ahead.waiting) == 1
        assert all(np.array_equal(a, b) for a, b in zip(got, want))

    def test_only_the_newest_announcements_are_held(self, clips):
        _mp4, ts = clips
        with longview.reading_ahead() as ahead:
            for t0 in (11.0, 12.0, 13.0, 14.0):
                longview.soon(ts, RECT, t0, t0 + 0.5, fps=5.0)
            assert [k[2] for k in ahead.waiting] == [13.0, 14.0]

    def test_soon_does_nothing_with_no_read_ahead_open(self, clips):
        _mp4, ts = clips
        longview.soon(ts, RECT, 11.0, 12.0)
        assert longview._reading is None

    def test_the_recording_is_only_listed_on_the_callers_thread(self, clips, monkeypatch):
        import threading

        from curling_score.ingest import frames as F

        _mp4, ts = clips
        longview._keyframes.clear()
        listed_on = []
        real = F.keyframe_times

        def listing(*a, **k):
            listed_on.append(threading.current_thread() is threading.main_thread())
            return real(*a, **k)

        monkeypatch.setattr(F, "keyframe_times", listing)
        with longview.reading_ahead():
            for t0 in (11.0, 31.0):
                longview.soon(ts, RECT, t0, t0 + 1.0, fps=10.0)
            for t0 in (11.0, 31.0):
                longview.decode(ts, RECT, t0, t0 + 1.0, fps=10.0)
        assert listed_on and all(listed_on)


class TestAPlan:
    WINDOWS = [(RECT, 11.0, 12.0, 10.0), (RECT, 13.0, 14.0, 10.0), (RECT, 15.5, 16.0, 5.0)]

    def test_each_window_starts_as_the_one_before_is_taken(self, clips):
        _mp4, ts = clips
        want = [longview.decode(ts, *w) for w in self.WINDOWS]
        with longview.reading_ahead() as ahead:
            plan = longview.Plan(ts, self.WINDOWS)
            got = []
            for i, w in enumerate(self.WINDOWS):
                got.append(plan.decode(ts, *w))
                waiting = [k[2] for k in ahead.waiting]
                assert waiting == ([self.WINDOWS[i + 1][1]] if i + 1 < len(self.WINDOWS) else [])
        for (a, at), (b, bt) in zip(got, want):
            assert at == bt and a.tobytes() == b.tobytes()

    def test_a_window_out_of_the_plan_is_decoded_then(self, clips):
        _mp4, ts = clips
        with longview.reading_ahead() as ahead:
            plan = longview.Plan(ts, self.WINDOWS)
            plan.decode(ts, *self.WINDOWS[0])
            frames, _ = plan.decode(ts, RECT, 30.0, 30.5, 10.0)
            assert len(frames) == 6
            assert [k[2] for k in ahead.waiting] == [13.0]     # still next

    def test_skipping_ahead_starts_the_window_after_it(self, clips):
        _mp4, ts = clips
        with longview.reading_ahead() as ahead:
            plan = longview.Plan(ts, self.WINDOWS)
            plan.decode(ts, *self.WINDOWS[1])
            assert [k[2] for k in ahead.waiting] == [15.5]

    def test_a_window_taken_is_never_started_again(self, clips):
        _mp4, ts = clips
        with longview.reading_ahead() as ahead:
            plan = longview.Plan(ts, self.WINDOWS)
            plan.decode(ts, *self.WINDOWS[0])
            plan.decode(ts, *self.WINDOWS[1])
            plan.decode(ts, RECT, 30.0, 30.5, 10.0)            # not in the plan
            plan.decode(ts, RECT, 31.0, 31.5, 10.0)
            assert [k[2] for k in ahead.waiting] == [15.5]

    def test_with_no_read_ahead_open_it_just_decodes(self, clips):
        _mp4, ts = clips
        plan = longview.Plan(ts, self.WINDOWS)
        frames, _ = plan.decode(ts, *self.WINDOWS[0])
        assert frames.tobytes() == longview.decode(ts, *self.WINDOWS[0])[0].tobytes()


class TestToBgr:
    def test_the_bytes_numpy_gave(self):
        rng = np.random.default_rng(3)
        frame = rng.integers(0, 256, (60, 81, 3), dtype=np.uint8)
        for crop in (frame, frame[7:41], frame[:, 5:70], frame[3:3]):
            want = np.ascontiguousarray(crop[:, :, ::-1])
            got = longview.to_bgr(crop)
            assert got.shape == want.shape and got.tobytes() == want.tobytes()
