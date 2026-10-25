import numpy as np
import pytest

from curling_score.ingest import frames


class TestProbe:
    def test_reads_dimensions_and_duration(self, primary_video):
        info = frames.probe(primary_video)
        assert (info.width, info.height) == (1920, 1080)
        assert 14000 < info.duration_s < 15000


class TestKeyframeSweep:
    def test_yields_bgr_frames_with_monotonic_timestamps(self, primary_video):
        got = []
        for t, img in frames.keyframe_sweep(primary_video):
            got.append((t, img))
            if len(got) == 20:
                break
        times = [t for t, _ in got]
        assert times == sorted(times)
        assert len(set(times)) == len(times)
        img = got[0][1]
        assert img.shape == (1080, 1920, 3)
        assert img.dtype == np.uint8

    @pytest.mark.slow
    def test_covers_the_whole_video_at_roughly_one_frame_per_five_seconds(
        self, primary_video
    ):
        times = [t for t, _ in frames.keyframe_sweep(primary_video, decode=False)]
        assert len(times) > 2000
        assert times[-1] > 14000
        gaps = np.diff(times)
        assert 1.0 < float(np.median(gaps)) < 10.0


class TestWindow:
    def test_samples_a_time_range_at_the_requested_rate(self, primary_video):
        got = list(frames.window(primary_video, start_s=900.0, end_s=904.0, fps=2.0))
        assert 6 <= len(got) <= 10
        times = [t for t, _ in got]
        assert times == sorted(times)
        assert 899.0 <= times[0] <= 901.0
        assert times[-1] <= 905.0
        assert got[0][1].shape == (1080, 1920, 3)

    def test_can_crop_to_a_region_to_save_work(self, primary_video):
        got = list(
            frames.window(
                primary_video, start_s=900.0, end_s=901.0, fps=1.0,
                crop=(810, 10, 297, 514),
            )
        )
        assert got[0][1].shape == (514, 297, 3)


class TestSampleKeyframes:
    """Calibration needs a handful of frames spread across the video.

    Materialising the whole keyframe sweep costs about 18 GB of decoded frames
    for a four-hour stream, which is what killed the analyser. Sampling has to
    stop as soon as it has what it needs and never hold the rest.
    """

    def test_returns_the_requested_number_of_frames(self, primary_video):
        got = frames.sample_keyframes(primary_video, count=8, stride=90)
        assert len(got) == 8
        assert all(img.shape == (1080, 1920, 3) for img in got)

    def test_spreads_the_sample_across_the_video(self, primary_video):
        got = frames.sample_keyframes(primary_video, count=6, stride=90,
                                      with_times=True)
        times = [t for t, _ in got]
        assert times == sorted(times)
        # 90 keyframes apart at ~5 s each is roughly 7 minutes between samples.
        assert times[-1] - times[0] > 1800

    def test_stops_early_rather_than_decoding_the_whole_video(self, primary_video):
        import time

        t0 = time.time()
        frames.sample_keyframes(primary_video, count=4, stride=10)
        # A full sweep takes ~40 s; four frames ten keyframes apart is a fraction.
        assert time.time() - t0 < 20.0


class TestChunked:
    """Frames must never be materialised wholesale.

    A 16-minute end at 10 fps is 9550 panels, about 4.4 GB if held at once. Two
    analyses doing that at the same time drove the machine into swap and stalled
    with the GPU idle.
    """

    def test_yields_batches_of_the_requested_size(self, primary_video):
        sizes = [
            len(batch)
            for batch in frames.chunked(
                primary_video, 3000, 3020, fps=5.0, size=16,
                crop=(810, 10, 297, 514),
            )
        ]
        assert sizes, "expected at least one batch"
        assert all(s <= 16 for s in sizes)
        assert sum(sizes) > 50

    def test_each_batch_carries_times_and_images(self, primary_video):
        batch = next(
            frames.chunked(primary_video, 3000, 3010, fps=5.0, size=8,
                           crop=(810, 10, 297, 514))
        )
        t, img = batch[0]
        assert 2999.0 <= t <= 3011.0
        assert img.shape == (514, 297, 3)

    def test_times_increase_across_batches(self, primary_video):
        seen = []
        for batch in frames.chunked(primary_video, 3000, 3020, fps=5.0, size=16,
                                    crop=(810, 10, 297, 514)):
            seen.extend(t for t, _ in batch)
        assert seen == sorted(seen)


class TestKeyframeSweepSeeking:
    """Reading a late part of the video must not scan everything before it.

    Board reads happen at a dozen points across a four-hour stream. Starting
    each sweep from the beginning costs ~10 s of scanning to reach the one-hour
    mark and worsens from there.
    """

    def test_a_bounded_sweep_starts_near_where_it_was_asked_to(self, primary_video):
        got = list(frames.keyframe_sweep(primary_video, decode=False,
                                         start_s=3600.0, end_s=3700.0))
        assert got, "expected keyframes in the range"
        assert got[0][0] >= 3590.0
        assert got[-1][0] <= 3705.0

    def test_a_late_bounded_sweep_is_quick(self, primary_video):
        import time

        t0 = time.time()
        got = list(frames.keyframe_sweep(primary_video, decode=False,
                                         start_s=13000.0, end_s=13100.0))
        elapsed = time.time() - t0
        assert got
        assert elapsed < 5.0, f"took {elapsed:.1f}s to reach t=13000"

    def test_an_unbounded_sweep_still_covers_the_whole_video(self, primary_video):
        times = [t for t, _ in frames.keyframe_sweep(primary_video, decode=False)]
        assert times[0] < 10.0
        assert times[-1] > 14000.0


class TestFfmpegLogsBypassPython:
    """PyAV's log callback deadlocks with frame threading on container close.

    The decoder thread wants the GIL to log; the main thread holds it while
    freeing the codec context and waiting for that thread. Three hangs on
    2026-09-11/12 had exactly those stacks. FFmpeg's own callback needs no
    GIL -- but torchvision installs PyAV's on import, after this module has
    loaded, so the default has to be put back at every open.
    """

    def _python_sees_ffmpeg_logs(self, caplog):
        import logging

        import av

        with caplog.at_level(logging.ERROR, logger="libav.probe"):
            av.logging.log(av.logging.ERROR, "probe", "are you there")
        return any("are you there" in r.message for r in caplog.records)

    def test_the_python_callback_is_what_torchvision_leaves_behind(self, caplog):
        """The hazard, still real inside PyAV.

        `frames._seal_logging_callback` rebinds `av.logging.set_level`, so the
        name torchvision calls no longer reaches this -- which is the whole
        point of the seal, and also why this test has to go through the handle
        the seal keeps rather than through the module attribute.
        """
        import av

        from curling_score.ingest import frames

        frames._seal_logging_callback()
        frames._real_set_level(av.logging.ERROR)   # what torchvision.io does on import
        try:
            assert self._python_sees_ffmpeg_logs(caplog)
        finally:
            av.logging.restore_default_callback()

    def test_opening_a_video_puts_ffmpeg_s_own_callback_back(self, caplog, tmp_path):
        import av

        from curling_score.ingest import frames

        av.logging.set_level(av.logging.ERROR)
        frames._quiet_ffmpeg()
        caplog.clear()
        assert not self._python_sees_ffmpeg_logs(caplog)

    def test_every_decode_path_reasserts_it(self):
        import inspect

        from curling_score.ingest import frames

        src = inspect.getsource(frames)
        opens = src.count("with av.open(")
        assert opens >= 2
        assert src.count("_quiet_ffmpeg()\n    with av.open(") == opens


class TestLoggingCallbackSeal:
    """The PyAV deadlock that hung a whole-video run on 2026-09-15.

    Re-asserting the safe callback at every open -- the previous fix -- is one
    step too early. `torchvision.io` is what calls `av.logging.set_level`, and
    it is imported not when the detector is built but on its **first predict**.
    Measured on the worker: absent from `sys.modules` before the constructor,
    absent after it returns, present after one `predict`. Every detection loop
    here consumes a lazy frame generator, so that predict lands *inside* the
    `av.open` block: safe callback at open, dangerous one installed mid-decode,
    deadlock at close. Restoring it again before the close would not help --
    a thread already inside the old callback stays blocked -- so the callback
    must never be installed at all.
    """

    def _python_sees_ffmpeg_logs(self, caplog):
        import logging

        import av

        with caplog.at_level(logging.ERROR, logger="libav.probe"):
            av.logging.log(av.logging.ERROR, "probe", "are you there")
        return any("are you there" in r.message for r in caplog.records)

    def test_a_torchvision_style_call_cannot_reinstall_the_callback(self, caplog):
        import av

        from curling_score.ingest import frames

        frames._quiet_ffmpeg()
        caplog.clear()
        # Exactly the line torchvision/io/video.py runs on import.
        av.logging.set_level(av.logging.ERROR)
        assert not self._python_sees_ffmpeg_logs(caplog), (
            "torchvision's call still installed the Python callback, so a "
            "decoder thread can still deadlock the close")

    def test_the_seal_still_honours_the_level_it_is_given(self, monkeypatch):
        """Silencing the callback must not silence libav's own level."""
        import av

        from curling_score.ingest import frames

        frames._seal_logging_callback()
        seen = []
        monkeypatch.setattr(av.logging, "set_libav_level", seen.append)
        av.logging.set_level(av.logging.ERROR)
        assert seen == [av.logging.ERROR]


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    """Six seconds of a changing test pattern, a keyframe every second."""
    import shutil
    import subprocess

    if shutil.which("ffmpeg") is None:
        pytest.skip("needs ffmpeg")
    path = tmp_path_factory.mktemp("feeds") / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", "testsrc2=size=320x240:rate=30", "-t", "6", "-g", "30",
         "-keyint_min", "30", "-sc_threshold", "0", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


class TestFeeds:
    """The readers take a ``Feed`` where they took a path, and a ``Cut`` where
    they took a rect. A crop-only ``Cut`` reads exactly what its rect does."""

    RECT = (11, 7, 101, 93)
    OTHER = (150, 20, 120, 80)

    def test_window_cut_equals_rect(self, clip):
        from curling_score.feeds import Cut, as_feed

        want = list(frames.window(clip, 1.0, 3.0, 5.0, crop=self.RECT))
        got = list(frames.window(as_feed(clip), 1.0, 3.0, 5.0,
                                 crop=Cut("left", self.RECT)))
        assert want
        assert [t for t, _ in got] == [t for t, _ in want]
        assert all(np.array_equal(a, b) for (_, a), (_, b) in zip(got, want))
        assert all(img.flags.c_contiguous for _, img in got)

    def test_window_takes_a_feed_with_no_crop(self, clip):
        from curling_score.feeds import as_feed

        want = list(frames.window(clip, 1.0, 2.0, 5.0))
        got = list(frames.window(as_feed(clip), 1.0, 2.0, 5.0))
        assert [t for t, _ in got] == [t for t, _ in want]
        assert all(np.array_equal(a, b) for (_, a), (_, b) in zip(got, want))

    def test_windows_cuts_one_decode(self, clip):
        from curling_score.feeds import Cut, as_feed

        cuts = [Cut("left", self.RECT), Cut("right", self.OTHER)]
        specs = [(1.0, 5.0, cuts[0]), (1.5, 10.0, cuts[1])]
        got = {0: [], 1: []}
        for i, t, img in frames.windows(as_feed(clip), specs, 3.0):
            got[i].append((t, img))
        for i, (start, fps, cut) in enumerate(specs):
            want = list(frames.window(clip, start, 3.0, fps, crop=cut.crop))
            assert want
            assert [t for t, _ in got[i]] == [t for t, _ in want]
            assert all(np.array_equal(a, b) for (_, a), (_, b) in zip(got[i], want))

    def test_windows_rects_and_none_still_work(self, clip):
        specs = [(1.0, 5.0, self.RECT), (1.0, 5.0, None)]
        out = {0: [], 1: []}
        for i, t, img in frames.windows(clip, specs, 2.0):
            out[i].append(img)
        assert out[0][0].shape == (93, 101, 3)
        assert out[1][0].shape == (240, 320, 3)

    def test_windows_two_files_raises(self, clip, tmp_path):
        from curling_score.feeds import Cut, as_feed

        other = tmp_path / "other.mp4"
        other.write_bytes(b"")
        feed = as_feed(clip).on(["right"], other)
        specs = [(1.0, 5.0, Cut("left", self.RECT)),
                 (1.0, 5.0, Cut("right", self.OTHER))]
        with pytest.raises(NotImplementedError, match="phase 2"):
            list(frames.windows(feed, specs, 2.0))

    def test_a_turned_cut_is_phase_2(self, clip):
        from curling_score.feeds import Cut, as_feed

        with pytest.raises(NotImplementedError, match="phase 2"):
            list(frames.window(as_feed(clip), 1.0, 2.0, 5.0,
                               crop=Cut("left", self.RECT, rotate=90)))

    def test_chunked_takes_a_cut(self, clip):
        from curling_score.feeds import Cut, as_feed

        want = [item for b in frames.chunked(clip, 1.0, 3.0, 5.0, size=4, crop=self.RECT)
                for item in b]
        got = [item for b in frames.chunked(as_feed(clip), 1.0, 3.0, 5.0, size=4,
                                            crop=Cut("left", self.RECT))
               for item in b]
        assert want
        assert [t for t, _ in got] == [t for t, _ in want]
        assert all(np.array_equal(a, b) for (_, a), (_, b) in zip(got, want))

    def test_keyframe_sweep_takes_feed(self, clip):
        from curling_score.feeds import as_feed

        want = list(frames.keyframe_sweep(clip))
        got = list(frames.keyframe_sweep(as_feed(clip)))
        assert len(want) >= 5
        assert [t for t, _ in got] == [t for t, _ in want]
        assert all(np.array_equal(a, b) for (_, a), (_, b) in zip(got, want))

    def test_the_other_readers_take_a_feed(self, clip):
        from curling_score.feeds import as_feed

        feed = as_feed(clip)
        assert frames.probe(feed) == frames.probe(clip)
        assert frames.keyframe_times(feed, 1.0, 4.0) == frames.keyframe_times(clip, 1.0, 4.0)
        assert frames.stream_start_s(feed) == frames.stream_start_s(clip)
        assert len(frames.sample_keyframes(feed, count=3, stride=1)) == 3
        assert len(frames.spread_keyframes(feed, 5.0, count=3)) == 3

    def test_seek_lead_of_ts_feed(self):
        from pathlib import Path

        from curling_score.feeds import as_feed

        assert frames.seek_lead(as_feed(Path("a.ts"))) == frames.TS_SEEK_LEAD_S
        assert frames.seek_lead(as_feed(Path("a.mp4"))) == 0.0
