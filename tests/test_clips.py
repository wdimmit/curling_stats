import pytest

from curling_score.harvest import clips


class TestSectionTimes:
    def test_returns_the_requested_number_of_times(self):
        assert len(clips.section_times(15600.0, n=10)) == 10

    def test_spans_the_middle_of_the_video_not_its_ends(self):
        # The first and last minutes are warm-up and pack-up: no play, and on
        # some nights the lights are already off.
        times = clips.section_times(15600.0, n=10)
        assert times[0] == pytest.approx(15600.0 * 0.04)
        assert times[-1] == pytest.approx(15600.0 * 0.96)

    def test_spaces_them_evenly(self):
        times = clips.section_times(15600.0, n=10)
        gaps = [b - a for a, b in zip(times, times[1:])]
        assert max(gaps) - min(gaps) < 1e-6

    def test_uses_each_videos_own_duration(self):
        # Durations run 13,962-15,605 s across the season; a fixed grid would
        # walk off the end of the short ones.
        short = clips.section_times(13962.0, n=10)
        assert short[-1] < 13962.0

    def test_a_single_section_lands_in_the_middle(self):
        assert clips.section_times(1000.0, n=1) == [500.0]

    def test_rejects_a_nonsense_count(self):
        with pytest.raises(ValueError):
            clips.section_times(15600.0, n=0)


class TestClipPath:
    def test_names_the_clip_by_its_requested_start_in_milliseconds(self):
        path = clips.clip_path("/data/clips", "mBBGkVPcPBQ", 3200.5)
        assert path.name == "003200500.mkv"
        assert path.parent.name == "mBBGkVPcPBQ"

    def test_sorts_chronologically_as_text(self):
        names = [clips.clip_path("/r", "v", t).name for t in (700.0, 3200.0, 14100.0)]
        assert names == sorted(names)


class TestCutCommand:
    """Both of these encode a trap that cost real time; do not relax them."""

    def cmd(self, **kw):
        return clips.cut_command("https://example/video", {"User-Agent": "x"},
                                 start_s=3200.0, seconds=24.0,
                                 dest="/tmp/out.mkv", **kw)

    def test_seeks_before_the_input_so_the_fetch_is_a_range_request(self):
        cmd = self.cmd()
        assert cmd.index("-ss") < cmd.index("-i")
        assert cmd.index("-to") < cmd.index("-i")

    def test_starts_a_lead_before_the_wanted_moment(self):
        # Keyframes are 5 s apart, so the cut begins at the keyframe at or
        # before start-lead. A lead shorter than that can miss the window.
        cmd = self.cmd(lead_s=6.0)
        assert cmd[cmd.index("-ss") + 1] == "3194.000"
        assert cmd[cmd.index("-to") + 1] == "3224.000"

    def test_keeps_source_timestamps(self):
        # Without -copyts the clip forgets where in the VOD it came from, which
        # is the whole reason we do not use yt-dlp --download-sections.
        assert "-copyts" in self.cmd()

    def test_never_uses_dash_t(self):
        # -t is measured against OUTPUT timestamps, which -copyts leaves at
        # absolute source time. "-t 30" is therefore already past its limit and
        # ffmpeg writes a zero-frame file.
        assert "-t" not in self.cmd()

    def test_caps_the_output_size(self):
        # Measured: one clip in sixty had its -ss silently ignored, so ffmpeg
        # started at t=0 and ran towards 2.3 GB before it was killed. -fs is the
        # hard stop that bounds that failure.
        cmd = self.cmd(max_bytes=1234)
        assert cmd[cmd.index("-fs") + 1] == "1234"

    def test_copies_the_stream_rather_than_re_encoding(self):
        # A 20 px stone must reach the labeller exactly as YouTube stored it.
        cmd = self.cmd()
        assert cmd[cmd.index("-c") + 1] == "copy"


class TestFormat:
    def test_prefers_thirty_frames_a_second(self):
        # One VOD in the sample served 1080p60 (itag 299) where the rest served
        # 1080p30 (137): twice the bytes for frames we never decode.
        assert "fps<=30" in clips.FORMAT.split("/")[0]

    def test_asks_for_full_height_before_it_asks_for_thirty_fps(self):
        # The bug this pins: a VOD with no 1080p30 answered
        # "avc1 and fps<=30" with 360p, where a stone is ~7 px instead of ~20.
        # Every preference above the first plain height<=1080 fallback must
        # still be pinned to 1080.
        rungs = clips.FORMAT.split("/")
        capped = next(i for i, r in enumerate(rungs) if "height<=1080" in r)
        assert capped >= 2, "there must be 1080p-only rungs before any fallback"
        assert all("height=1080" in r for r in rungs[:capped])

    def test_still_accepts_a_video_with_no_thirty_fps_variant(self):
        assert len(clips.FORMAT.split("/")) > 1
