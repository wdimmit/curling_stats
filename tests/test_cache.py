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
