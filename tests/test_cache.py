import json
from pathlib import Path

from curling_score.ingest import cache


class TestCachePath:
    def test_names_the_file_after_the_video_id(self, tmp_path):
        p = cache.video_path("VXU9xwmugRg", root=tmp_path)
        assert p == tmp_path / "videos" / "VXU9xwmugRg.mp4"

    def test_is_cached_is_false_when_absent(self, tmp_path):
        assert cache.is_cached("VXU9xwmugRg", root=tmp_path) is False

    def test_is_cached_is_true_once_a_nonempty_file_exists(self, tmp_path):
        p = cache.video_path("VXU9xwmugRg", root=tmp_path)
        p.parent.mkdir(parents=True)
        p.write_bytes(b"not really an mp4, but non-empty")
        assert cache.is_cached("VXU9xwmugRg", root=tmp_path) is True

    def test_a_zero_byte_file_counts_as_not_cached(self, tmp_path):
        p = cache.video_path("VXU9xwmugRg", root=tmp_path)
        p.parent.mkdir(parents=True)
        p.touch()
        assert cache.is_cached("VXU9xwmugRg", root=tmp_path) is False

    def test_default_root_is_under_the_user_cache_dir(self):
        assert cache.default_root() == Path.home() / ".cache" / "curling_score"


class TestAFailedDownloadLeavesNothingBehind:
    """yt-dlp writes ``<name>.part`` and renames on success, so a refused
    download leaves the ``.part``, not the name we chose. Two 403s on one
    video left two of them in the cache, and the pruner never touches
    anything called ``.part``."""

    def test_ytdlp_s_partial_file_is_removed_when_the_download_fails(self, tmp_path):
        import pytest

        from curling_score.ingest import cache

        def refused(url, opts):
            partial = tmp_path / "videos" / (opts["outtmpl"].rsplit("/", 1)[1] + ".part")
            partial.write_bytes(b"ten megabytes of nothing")
            raise RuntimeError("HTTP Error 403: Forbidden")

        with pytest.raises(RuntimeError):
            cache.ensure_cached("https://www.youtube.com/watch?v=abcdefghijk",
                                root=tmp_path, downloader=refused)
        assert list((tmp_path / "videos").iterdir()) == []

    def test_a_successful_download_is_kept(self, tmp_path):
        from curling_score.ingest import cache

        def ok(url, opts):
            from pathlib import Path as P
            P(opts["outtmpl"]).write_bytes(b"video")

        got = cache.ensure_cached("https://www.youtube.com/watch?v=abcdefghijk",
                                  root=tmp_path, downloader=ok)
        assert got.read_bytes() == b"video"
        assert [p.name for p in (tmp_path / "videos").iterdir()] == ["abcdefghijk.mp4"]


class TestKeepingALiveRecording:
    """A whole live recording is filed as the video's cached copy, so
    reprocessing the game needs no download."""

    @staticmethod
    def _ts(d):
        import subprocess

        mp4, ts = d / "src.mp4", d / "rec.0.ts"
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
             "-i", "testsrc2=size=160x120:rate=30", "-t", "12", "-g", "150",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", str(mp4)], check=True)
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(mp4),
                        "-c", "copy", "-f", "mpegts", str(ts)], check=True)
        return ts

    def test_the_recording_becomes_an_mp4_on_the_same_clock(self, tmp_path):
        import shutil
        import time

        import pytest

        from curling_score.ingest import frames

        if shutil.which("ffmpeg") is None:
            pytest.skip("needs ffmpeg")
        live = tmp_path / "live" / "liveVid0001"
        live.mkdir(parents=True)
        ts = self._ts(live)
        kept = cache.keep_recording(ts, "liveVid0001", tmp_path)
        assert kept == cache.video_path("liveVid0001", tmp_path)
        assert cache.is_cached("liveVid0001", tmp_path)
        assert not (live / "liveVid0001.mp4").exists()
        assert frames.seek_lead(kept) == 0.0             # indexed, unlike the .ts
        st = kept.stat()
        assert st.st_mtime_ns == cache.PINNED_MTIME_NS
        # Freshly read as far as the pruner can tell, not the oldest file there.
        assert time.time() - st.st_atime < 60
        want = [t for t, _ in frames.keyframe_sweep(ts, decode=False)]
        got = [t for t, _ in frames.keyframe_sweep(kept, decode=False)]
        assert got == pytest.approx(want, abs=1e-3)

    def test_a_video_already_cached_is_left_as_it_is(self, tmp_path):
        dest = cache.video_path("liveVid0001", tmp_path)
        dest.parent.mkdir(parents=True)
        dest.write_bytes(b"the download")
        calls = []
        assert cache.keep_recording(tmp_path / "rec.0.ts", "liveVid0001", tmp_path,
                                    run=lambda *a, **k: calls.append(a)) is None
        assert calls == [] and dest.read_bytes() == b"the download"

    def test_a_remux_that_fails_files_nothing(self, tmp_path):
        import subprocess

        import pytest

        def run(cmd, check):
            raise subprocess.CalledProcessError(1, cmd)

        with pytest.raises(subprocess.CalledProcessError):
            cache.keep_recording(tmp_path / "rec.0.ts", "liveVid0001", tmp_path, run=run)
        assert not cache.is_cached("liveVid0001", tmp_path)


class TestMarkingKeptRecordings:
    """A kept live recording carries a marker, so the pruner can age it out
    after a week; a downloaded video carries none and never ages out."""

    def _remux(self, cmd, check):
        Path(cmd[-1]).write_bytes(b"mp4")

    def test_a_whole_recording_is_marked_as_one(self, tmp_path):
        rec = tmp_path / "live" / "vid1" / "rec.0.ts"
        rec.parent.mkdir(parents=True)
        rec.write_bytes(b"ts")
        cache.keep_recording(rec, "vid1", tmp_path, run=self._remux)
        marker = json.loads(cache.kept_marker("vid1", tmp_path).read_text())
        assert marker["whole"] is True and marker["kept_at"] > 0

    def test_a_recording_of_a_cached_video_writes_no_marker(self, tmp_path):
        cache.video_path("vid1", tmp_path).parent.mkdir(parents=True)
        cache.video_path("vid1", tmp_path).write_bytes(b"download")
        rec = tmp_path / "rec.0.ts"
        rec.write_bytes(b"ts")
        assert cache.keep_recording(rec, "vid1", tmp_path, run=self._remux) is None
        assert not cache.kept_marker("vid1", tmp_path).exists()


class TestKeepingAPartialRecording:
    """A recording cut short is kept for investigation, never as the video."""

    def _rec(self, tmp_path, vid="vid1"):
        rec = tmp_path / "live" / vid / "rec.0.ts"
        rec.parent.mkdir(parents=True)
        rec.write_bytes(b"partial ts")
        return rec

    def test_it_moves_to_kept_and_is_marked_partial(self, tmp_path):
        rec = self._rec(tmp_path)
        got = cache.keep_partial(rec, "vid1", tmp_path, now=1234.0)
        assert got == cache.partial_path("vid1", tmp_path) == tmp_path / "kept" / "vid1.ts"
        assert got.read_bytes() == b"partial ts" and not rec.exists()
        assert json.loads(cache.kept_marker("vid1", tmp_path).read_text()) == {
            "kept_at": 1234.0, "whole": False}

    def test_it_never_becomes_the_cached_video(self, tmp_path):
        cache.keep_partial(self._rec(tmp_path), "vid1", tmp_path)
        assert not cache.is_cached("vid1", tmp_path)

    def test_a_video_already_cached_keeps_nothing(self, tmp_path):
        cache.video_path("vid1", tmp_path).parent.mkdir(parents=True)
        cache.video_path("vid1", tmp_path).write_bytes(b"download")
        rec = self._rec(tmp_path)
        assert cache.keep_partial(rec, "vid1", tmp_path) is None
        assert not cache.partial_path("vid1", tmp_path).exists()
        assert not cache.kept_marker("vid1", tmp_path).exists()

    def test_an_empty_recording_keeps_nothing(self, tmp_path):
        rec = self._rec(tmp_path)
        rec.write_bytes(b"")
        assert cache.keep_partial(rec, "vid1", tmp_path) is None
        assert not cache.kept_marker("vid1", tmp_path).exists()

    def test_an_id_that_starts_with_a_dash(self, tmp_path):
        got = cache.keep_partial(self._rec(tmp_path, "-f3RabcdEF"), "-f3RabcdEF", tmp_path)
        assert got == tmp_path / "kept" / "-f3RabcdEF.ts" and got.is_file()
        assert cache.kept_marker("-f3RabcdEF", tmp_path).is_file()
