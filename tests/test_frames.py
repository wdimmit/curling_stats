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
