import pytest

from curling_score.ingest import source


class TestVideoId:
    def test_extracts_id_from_a_plain_watch_url(self):
        assert source.video_id("https://www.youtube.com/watch?v=VXU9xwmugRg") == "VXU9xwmugRg"

    def test_ignores_playlist_and_index_parameters(self):
        url = (
            "https://www.youtube.com/watch?v=VXU9xwmugRg"
            "&list=PLrMwR_YwOp3IPy70FTKIQqy1DL_lZXP-s&index=7"
        )
        assert source.video_id(url) == "VXU9xwmugRg"

    def test_accepts_short_youtu_be_links(self):
        assert source.video_id("https://youtu.be/VXU9xwmugRg?t=61") == "VXU9xwmugRg"

    def test_accepts_a_bare_video_id(self):
        assert source.video_id("VXU9xwmugRg") == "VXU9xwmugRg"

    def test_rejects_a_url_with_no_video(self):
        with pytest.raises(ValueError):
            source.video_id("https://www.youtube.com/playlist?list=PLabc")


class TestCanonicalUrl:
    def test_drops_playlist_context_so_links_do_not_reopen_the_playlist(self):
        url = "https://www.youtube.com/watch?v=VXU9xwmugRg&list=PLabc&index=7"
        assert source.canonical_url(url) == "https://www.youtube.com/watch?v=VXU9xwmugRg"


class TestWatchUrlAt:
    def test_builds_a_deep_link_at_a_whole_second(self):
        assert source.watch_url_at("VXU9xwmugRg", 61.4) == "https://youtu.be/VXU9xwmugRg?t=61"

    def test_truncates_rather_than_rounds_so_the_link_never_overshoots(self):
        assert source.watch_url_at("VXU9xwmugRg", 61.99) == "https://youtu.be/VXU9xwmugRg?t=61"


class TestSheetFromTitle:
    def test_reads_the_sheet_number_from_the_club_title_format(self):
        assert source.sheet_from_title("4/30 - Sheet 2 - Spring Skip's Choice League 2026") == 2

    def test_returns_none_when_the_title_has_no_sheet(self):
        assert source.sheet_from_title("Some other video") is None
