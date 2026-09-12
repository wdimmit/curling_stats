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
