"""A cached video played back as if it were live, and keyframes spread over
only the part of a recording that exists so far."""

import shutil
import subprocess
import time

import pytest

from curling_score.ingest import frames as F
from curling_score.live import replay

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    """Twenty seconds of test pattern, a keyframe every second."""
    path = tmp_path_factory.mktemp("clip") / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", "testsrc=size=320x240:rate=30", "-t", "20", "-g", "30",
         "-keyint_min", "30", "-sc_threshold", "0", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


class TestSpreadKeyframes:
    def test_it_spreads_over_the_span_asked_for_not_the_whole_file(self, clip):
        got = F.spread_keyframes(clip, until_s=10.0, count=4, with_times=True)
        times = [t for t, _ in got]
        assert len(times) == 4
        assert times[0] == pytest.approx(0.0, abs=0.05)
        assert max(times) <= 10.0
        assert times[-1] >= 6.0          # spread, not the first four

    def test_it_gives_what_there_is_when_there_are_fewer(self, clip):
        got = F.spread_keyframes(clip, until_s=2.5, count=24)
        assert len(got) == 3             # keyframes at 0, 1 and 2 s


class TestReplayRecording:
    def test_it_grows_readable_in_real_time_scaled_and_then_ends(self, tmp_path, clip):
        rec = replay.ReplayRecording(clip, tmp_path / "rec.ts", speed=8.0)
        rec.start()
        try:
            deadline = time.monotonic() + 30
            seen_partial = False
            while not rec.ended() and time.monotonic() < deadline:
                head = rec.head_s()
                if 2.0 < head < 18.0:
                    # Whatever head_s claims is already on disk and readable
                    # (keyframes are a second apart; head may lag the file).
                    keys = [t for t, _ in F.keyframe_sweep(rec.path, decode=False)]
                    assert keys and keys[-1] >= head - 1.0
                    seen_partial = True
                time.sleep(0.2)
        finally:
            rec.stop()
        assert rec.ended()
        assert seen_partial
        keys = [t for t, _ in F.keyframe_sweep(rec.path, decode=False)]
        # Same clock as the source: the first keyframe is t=0 and the last ~19 s.
        assert keys[0] == pytest.approx(0.0, abs=0.05)
        assert keys[-1] == pytest.approx(19.0, abs=0.1)
        assert rec.head_s() == pytest.approx(20.0, abs=0.5)

    def test_stopping_it_early_leaves_it_not_ended(self, tmp_path, clip):
        rec = replay.ReplayRecording(clip, tmp_path / "rec.ts", speed=1.0)
        rec.start()
        time.sleep(1.0)
        rec.stop()
        assert not rec.ended()

    def test_a_window_begins_at_the_keyframe_before_its_start(self, tmp_path, clip):
        rec = replay.ReplayRecording(clip, tmp_path / "rec.ts", speed=8.0,
                                     start_s=5.5, end_s=12.0)
        rec.start()
        try:
            deadline = time.monotonic() + 30
            while not rec.ended() and time.monotonic() < deadline:
                time.sleep(0.1)
        finally:
            rec.stop()
        assert rec.t0_s == pytest.approx(5.0, abs=0.02)   # keyframes every second
        # The recording's clock is the source's, less t0_s, to the frame.
        got = next(iter(F.window(rec.path, 2.0, 2.01, 30.0)))[1]
        want = next(iter(F.window(clip, rec.t0_s + 2.0, rec.t0_s + 2.01, 30.0)))[1]
        assert (got == want).all()
        assert rec.head_s() >= 6.0

    def test_a_lookback_burst_arrives_at_once_then_real_time(self, tmp_path, clip):
        rec = replay.ReplayRecording(clip, tmp_path / "rec.ts", speed=1.0, burst_s=10.0)
        rec.start()
        try:
            began = time.monotonic()
            while rec.head_s() < 9.0 and time.monotonic() - began < 5.0:
                time.sleep(0.05)
            assert rec.head_s() >= 9.0 and time.monotonic() - began < 4.0
            assert rec.head_s() < 16.0                     # not the whole clip at once
        finally:
            rec.stop()

    def test_a_replay_from_the_beginning_starts_at_zero(self, tmp_path, clip):
        rec = replay.ReplayRecording(clip, tmp_path / "rec.ts", speed=8.0).start()
        rec.stop()
        assert rec.t0_s == 0.0


class TestTheVideoPipeline:
    def test_a_picture_with_no_panels_is_a_calibration_failure_to_wait_out(self, clip):
        from curling_score.geometry.calibrate import CalibrationError
        from curling_score.live import session as live

        pipe = live.VideoPipeline(progress=lambda m: None)
        with pytest.raises(CalibrationError):
            pipe.calibrate(clip, until_s=10.0)

    def test_too_little_footage_is_a_calibration_failure_too(self, clip):
        from curling_score.geometry.calibrate import CalibrationError
        from curling_score.live import session as live

        with pytest.raises(CalibrationError):
            live.VideoPipeline(progress=lambda m: None).calibrate(clip, until_s=0.5)
