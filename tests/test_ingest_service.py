"""The ingest changes a hosted worker depends on.

Links carry a start time; the download waits out a block or reports it as
one; every cache lives under one configurable root; a rebuilt proxy is the same
file to the detection cache; and old media is pruned without touching a job in
progress. None of these need the network.
"""

import os
import time
from pathlib import Path

import pytest

from curling_score import version
from curling_score.detect import cache as detcache
from curling_score.ingest import cache, prune, source


class TestParseLink:
    def test_a_plain_watch_url_has_no_start(self):
        link = source.parse_link("https://www.youtube.com/watch?v=VXU9xwmugRg")
        assert link == source.Link("VXU9xwmugRg", None)

    def test_t_in_seconds(self):
        link = source.parse_link("https://youtu.be/VXU9xwmugRg?t=4212")
        assert link.start_s == 4212.0

    def test_t_with_units(self):
        link = source.parse_link(
            "https://www.youtube.com/watch?v=VXU9xwmugRg&t=1h10m12s")
        assert link.start_s == 4212.0

    def test_t_with_trailing_s(self):
        assert source.parse_link("https://youtu.be/VXU9xwmugRg?t=90s").start_s == 90.0

    def test_start_parameter_is_also_accepted(self):
        link = source.parse_link(
            "https://www.youtube.com/watch?v=VXU9xwmugRg&start=300")
        assert link.start_s == 300.0

    def test_fragment_form(self):
        assert source.parse_link("https://youtu.be/VXU9xwmugRg#t=2m").start_s == 120.0

    def test_playlist_context_does_not_confuse_it(self):
        link = source.parse_link(
            "https://www.youtube.com/watch?v=VXU9xwmugRg&list=PLabc&index=7&t=61")
        assert link == source.Link("VXU9xwmugRg", 61.0)

    def test_garbage_time_is_ignored_not_fatal(self):
        assert source.parse_link("https://youtu.be/VXU9xwmugRg?t=soon").start_s is None

    def test_a_bare_id_is_fine(self):
        assert source.parse_link("VXU9xwmugRg") == source.Link("VXU9xwmugRg", None)

    def test_parse_time_edge_cases(self):
        assert source.parse_time(None) is None
        assert source.parse_time("") is None
        assert source.parse_time("0") == 0.0
        assert source.parse_time("2h") == 7200.0


class TestOneCacheRoot:
    def test_the_env_var_moves_every_cache(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CURLING_SCORE_CACHE", str(tmp_path))
        assert cache.default_root() == tmp_path
        assert cache.video_path("abc") == tmp_path / "videos" / "abc.mp4"
        assert detcache.cache_dir() == tmp_path / "detections"

    def test_without_it_the_home_cache_is_used(self, monkeypatch):
        monkeypatch.delenv("CURLING_SCORE_CACHE", raising=False)
        assert cache.default_root() == Path.home() / ".cache" / "curling_score"

    def test_an_explicit_root_still_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CURLING_SCORE_CACHE", "/nowhere")
        assert cache.video_path("abc", root=tmp_path).parent == tmp_path / "videos"


class TestPinnedMtime:
    """The detection cache keys on (name, size, mtime); a rebuild must match."""

    def test_two_builds_of_the_same_file_share_an_identity(self, tmp_path):
        a = tmp_path / "x.mp4"
        a.write_bytes(b"same bytes")
        cache.pin_mtime(a)
        first = detcache._file_identity(a)
        time.sleep(0.01)
        a.write_bytes(b"same bytes")  # a fresh write, new mtime
        cache.pin_mtime(a)
        assert detcache._file_identity(a) == first

    def test_different_bytes_are_still_told_apart(self, tmp_path):
        a = tmp_path / "x.mp4"
        a.write_bytes(b"abc")
        cache.pin_mtime(a)
        first = detcache._file_identity(a)
        a.write_bytes(b"abcd")
        cache.pin_mtime(a)
        assert detcache._file_identity(a) != first


class TestDownloadBackoff:
    """Blocks are waited out and then named; other errors are raised at once."""

    URL = "https://www.youtube.com/watch?v=VXU9xwmugRg"

    def _downloader_that(self, script, written=b"video"):
        """Fail with each message in ``script``, then write the file."""
        calls = []

        def download(url, opts):
            calls.append(opts)
            if len(calls) <= len(script):
                raise RuntimeError(script[len(calls) - 1])
            Path(opts["outtmpl"]).write_bytes(written)

        download.calls = calls
        return download

    def test_a_clean_download_lands_at_the_cached_path(self, tmp_path):
        dl = self._downloader_that([])
        out = cache.ensure_cached(self.URL, root=tmp_path, downloader=dl)
        assert out == tmp_path / "videos" / "VXU9xwmugRg.mp4"
        assert out.read_bytes() == b"video"
        assert not list((tmp_path / "videos").glob("*.part-*"))

    def test_the_cached_file_carries_the_pinned_mtime(self, tmp_path):
        out = cache.ensure_cached(self.URL, root=tmp_path,
                                  downloader=self._downloader_that([]))
        assert out.stat().st_mtime_ns == cache.PINNED_MTIME_NS

    def test_a_block_is_waited_out_then_retried(self, tmp_path):
        slept = []
        dl = self._downloader_that(["Sign in to confirm you're not a bot"])
        out = cache.ensure_cached(self.URL, root=tmp_path, downloader=dl,
                                  sleep=slept.append)
        assert out.is_file()
        assert slept == [cache.BACKOFF_S[0]]
        assert len(dl.calls) == 2

    def test_exhausting_the_attempts_names_the_block(self, tmp_path):
        slept = []
        dl = self._downloader_that(["HTTP Error 429: Too Many Requests"] * 4)
        with pytest.raises(cache.BlockedError):
            cache.ensure_cached(self.URL, root=tmp_path, downloader=dl,
                                sleep=slept.append)
        assert slept == list(cache.BACKOFF_S)
        assert not cache.is_cached("VXU9xwmugRg", tmp_path)

    def test_a_worker_can_ask_for_no_in_process_waiting(self, tmp_path):
        slept = []
        dl = self._downloader_that(["not a bot"])
        with pytest.raises(cache.BlockedError):
            cache.ensure_cached(self.URL, root=tmp_path, downloader=dl,
                                sleep=slept.append, attempts=1)
        assert slept == []

    def test_other_errors_are_not_retried(self, tmp_path):
        slept = []
        dl = self._downloader_that(["Video unavailable: private"])
        with pytest.raises(RuntimeError, match="private"):
            cache.ensure_cached(self.URL, root=tmp_path, downloader=dl,
                                sleep=slept.append)
        assert slept == [] and len(dl.calls) == 1

    def test_an_empty_download_is_not_accepted(self, tmp_path):
        dl = self._downloader_that([], written=b"")
        with pytest.raises(RuntimeError, match="usable file"):
            cache.ensure_cached(self.URL, root=tmp_path, downloader=dl)
        assert not cache.is_cached("VXU9xwmugRg", tmp_path)

    def test_progress_is_reported_as_a_fraction(self, tmp_path):
        seen = []

        def download(url, opts):
            for hook in opts["progress_hooks"]:
                hook({"status": "downloading", "downloaded_bytes": 50,
                      "total_bytes": 200})
                hook({"status": "finished"})
            Path(opts["outtmpl"]).write_bytes(b"v")

        cache.ensure_cached(self.URL, root=tmp_path, downloader=download,
                            progress_hook=lambda f, m: seen.append(f))
        assert seen == [0.25, 1.0]

    def test_cookies_and_pot_provider_come_from_the_environment(
            self, tmp_path, monkeypatch):
        monkeypatch.setenv("YTDLP_COOKIES", "/secrets/cookies.txt")
        monkeypatch.setenv("YTDLP_POT_PROVIDER", "http://pot:4416")
        dl = self._downloader_that([])
        cache.ensure_cached(self.URL, root=tmp_path, downloader=dl)
        opts = dl.calls[0]
        assert opts["cookiefile"] == "/secrets/cookies.txt"
        assert opts["extractor_args"]["youtubepot-bgutilhttp"]["base_url"] == [
            "http://pot:4416"]

    def test_is_blocked_message(self):
        assert cache.is_blocked_message("ERROR: Sign in to confirm you’re not a bot")
        assert cache.is_blocked_message("HTTP Error 429")
        assert not cache.is_blocked_message("Video unavailable")


class TestPrune:
    def _file(self, root, sub, name, size, age_s, now):
        d = root / sub
        d.mkdir(parents=True, exist_ok=True)
        p = d / name
        p.write_bytes(b"x" * size)
        os.utime(p, (now - age_s, now - age_s))
        return p

    def test_nothing_goes_while_under_budget(self, tmp_path):
        now = 1_000_000.0
        self._file(tmp_path, "videos", "a.mp4", 1000, 99999, now)
        assert prune.prune(tmp_path, keep_gb=1.0, now=now) == []

    def test_oldest_read_goes_first(self, tmp_path):
        now = 1_000_000.0
        old = self._file(tmp_path, "videos", "old.mp4", 600, 90000, now)
        new = self._file(tmp_path, "proxies", "new.mp4", 600, 80000, now)
        removed = prune.prune(tmp_path, keep_gb=800e-9, now=now)
        assert removed == [old]
        assert new.exists()

    def test_a_file_in_use_is_never_removed(self, tmp_path):
        now = 1_000_000.0
        busy = self._file(tmp_path, "videos", "busy.mp4", 1000, 60, now)
        removed = prune.prune(tmp_path, keep_gb=100e-9, now=now)
        assert removed == [] and busy.exists()

    def test_partial_downloads_are_left_alone(self, tmp_path):
        now = 1_000_000.0
        part = self._file(tmp_path, "videos", "v.part-1-ab.mp4", 1000, 99999, now)
        assert prune.prune(tmp_path, keep_gb=0.0, now=now) == []
        assert part.exists()

    def test_detections_are_never_pruned(self, tmp_path):
        now = 1_000_000.0
        det = self._file(tmp_path, "detections", "k.npz", 5000, 99999, now)
        prune.prune(tmp_path, keep_gb=0.0, now=now)
        assert det.exists()

    def test_a_disk_short_of_room_takes_no_download(self, tmp_path):
        # The floor makes room from live recordings only (TestRecordingsGiveWayFirst);
        # downloads -- the harness among them -- answer to the budget alone.
        now = 1_000_000.0
        old = self._file(tmp_path, "videos", "old.mp4", 600, 90000, now)
        mid = self._file(tmp_path, "videos", "mid.mp4", 600, 80000, now)
        assert prune.prune(tmp_path, keep_gb=1.0, now=now,
                           min_free_gb=1000e-9, free_bytes=0) == []
        assert old.exists() and mid.exists()

    def test_a_disk_with_room_leaves_the_budget_in_charge(self, tmp_path):
        now = 1_000_000.0
        self._file(tmp_path, "videos", "a.mp4", 600, 90000, now)
        assert prune.prune(tmp_path, keep_gb=1.0, now=now,
                           min_free_gb=1000e-9, free_bytes=5000) == []

    def test_a_full_disk_still_never_takes_a_file_in_use(self, tmp_path):
        now = 1_000_000.0
        busy = self._file(tmp_path, "videos", "busy.mp4", 1000, 60, now)
        assert prune.prune(tmp_path, keep_gb=1.0, now=now,
                           min_free_gb=1.0, free_bytes=0) == []
        assert busy.exists()


class TestVersion:
    def test_the_model_id_names_the_file_and_its_bytes(self, tmp_path):
        w = tmp_path / "yolo11n-club.pt"
        w.write_bytes(b"weights v1")
        first = version.model_id(w)
        assert first.startswith("yolo11n-club-")
        w.write_bytes(b"weights v2")
        assert version.model_id(w) != first

    def test_a_renamed_copy_hashes_the_same(self, tmp_path):
        a = tmp_path / "a.pt"
        b = tmp_path / "b.pt"
        a.write_bytes(b"same")
        b.write_bytes(b"same")
        assert version.model_id(a).split("-")[-1] == version.model_id(b).split("-")[-1]

    def test_no_weights_means_the_classical_detector(self):
        assert version.model_id(None) == "classical"
        assert version.processing_version(None) == (
            f"{version.PIPELINE_VERSION}+classical")


class TestTheLeagueInTheTitle:
    """The club's titles put the league after the sheet, and keep doing it
    even when the front of the title is a draw number or a pair of skips."""

    @pytest.mark.parametrize("title,league", [
        ("11/4 - Sheet 1 - Tuesday Super League 2025-2026",
         "Tuesday Super League 2025-2026"),
        ("6/18 - Sheet 4 - Spring Skip's Choice League 2026",
         "Spring Skip's Choice League 2026"),
        ("Rice (r) vs Casey (y) - Draw 1 (15:00) - Sheet 5 - 2026 5U National Championship",
         "2026 5U National Championship"),
        ("Draw 7 (14:00) - Sheet 4 - 2026 PNWCA Club Men's and Women's Playdown",
         "2026 PNWCA Club Men's and Women's Playdown"),
    ])
    def test_it_is_what_follows_the_sheet(self, title, league):
        assert source.league_from_title(title) == league

    def test_the_sheet_still_reads_the_same_way(self):
        assert source.sheet_from_title(
            "Rice (r) vs Casey (y) - Draw 1 (15:00) - Sheet 5 - 2026 5U National") == 5

    @pytest.mark.parametrize("title", [
        "", "Some stream with no sheet at all", "4/30 - Sheet 2", "4/30 - Sheet 2 - ",
    ])
    def test_it_would_rather_say_nothing(self, title):
        """A guess from a naming convention, so it declines when the
        convention is not there rather than inventing a league."""
        assert source.league_from_title(title) is None

    def test_a_sheet_mentioned_in_passing_is_not_the_separator(self):
        """Only a segment that is *just* "Sheet N" splits the title."""
        assert source.league_from_title("Sheet 2 warm-up - Sheet 2 - Friday Night") \
            == "Friday Night"


class TestFormatFromTitle:
    @pytest.mark.parametrize("title", [
        "3/19 - Sheet 1 - Thursday Mixed Doubles League 2025-2026",
        "Sunday open doubles - Sheet 3 - Open Doubles",
        "DOUBLES - Sheet 2 - Friday",
    ])
    def test_doubles_anywhere_is_doubles(self, title):
        assert source.format_from_title(title) == "doubles"

    @pytest.mark.parametrize("title", [
        "4/30 - Sheet 2 - Spring Skip's Choice League 2026", "", None,
        "Doubleshot Coffee Cup - Sheet 1 - Bonspiel",
    ])
    def test_everything_else_is_fours(self, title):
        assert source.format_from_title(title) == "fours"


class TestRecordingsAgeOut:
    """Live recordings stay a rolling week, then go -- whatever the budget;
    downloads never age out."""

    DAY = 86400.0

    def _media(self, root, rel, size, read_ago, now):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * size)
        os.utime(p, (now - read_ago, now - read_ago))
        return p

    def _mark(self, root, vid, kept_ago, now, whole=True):
        # As keeping does: the marker records the size of the file it kept.
        f = cache.video_path(vid, root) if whole else cache.partial_path(vid, root)
        size = f.stat().st_size if f.is_file() else None
        return cache.mark_kept(vid, root, whole=whole, now=now - kept_ago, size=size)

    def test_a_recording_older_than_the_window_goes_with_its_proxy(self, tmp_path):
        now = 10 * self.DAY
        video = self._media(tmp_path, "videos/old.mp4", 100, 2 * self.DAY, now)
        proxy = self._media(tmp_path, "proxies/old.ab12.mp4", 10, 2 * self.DAY, now)
        marker = self._mark(tmp_path, "old", 8 * self.DAY, now)
        removed = prune.prune(tmp_path, keep_gb=1.0, now=now, recording_days=7)
        assert set(removed) == {video, proxy}
        assert not video.exists() and not proxy.exists() and not marker.exists()

    def test_a_recording_inside_the_window_stays(self, tmp_path):
        now = 10 * self.DAY
        video = self._media(tmp_path, "videos/new.mp4", 100, 2 * self.DAY, now)
        self._mark(tmp_path, "new", 6 * self.DAY, now)
        assert prune.prune(tmp_path, keep_gb=1.0, now=now, recording_days=7) == []
        assert video.exists()

    def test_a_download_never_ages_out(self, tmp_path):
        now = 400 * self.DAY
        video = self._media(tmp_path, "videos/harness.mp4", 100, 300 * self.DAY, now)
        assert prune.prune(tmp_path, keep_gb=1.0, now=now, recording_days=7) == []
        assert video.exists()

    def test_a_partial_recording_ages_out_too(self, tmp_path):
        now = 10 * self.DAY
        part = self._media(tmp_path, "kept/cut.ts", 100, 2 * self.DAY, now)
        self._mark(tmp_path, "cut", 8 * self.DAY, now, whole=False)
        assert prune.prune(tmp_path, keep_gb=1.0, now=now, recording_days=7) == [part]

    def test_an_old_recording_being_read_is_left_until_it_is_not(self, tmp_path):
        now = 10 * self.DAY
        video = self._media(tmp_path, "videos/busy.mp4", 100, 60, now)
        marker = self._mark(tmp_path, "busy", 8 * self.DAY, now)
        assert prune.prune(tmp_path, keep_gb=1.0, now=now, recording_days=7) == []
        assert video.exists() and marker.exists()

    def test_a_marker_whose_recording_is_already_gone_is_cleared(self, tmp_path):
        now = 10 * self.DAY
        marker = self._mark(tmp_path, "gone", 8 * self.DAY, now)
        assert prune.prune(tmp_path, keep_gb=1.0, now=now, recording_days=7) == []
        assert not marker.exists()

    def test_an_unreadable_marker_is_left_alone(self, tmp_path):
        now = 10 * self.DAY
        video = self._media(tmp_path, "videos/odd.mp4", 100, 2 * self.DAY, now)
        bad = tmp_path / "kept" / "odd.json"
        bad.parent.mkdir()
        bad.write_text("{not json")
        assert prune.prune(tmp_path, keep_gb=1.0, now=now, recording_days=7) == []
        assert video.exists() and bad.exists()

    def test_proxies_of_a_dashed_id_take_no_other_video_s(self, tmp_path):
        now = 10 * self.DAY
        self._media(tmp_path, "videos/-f3R.mp4", 100, 2 * self.DAY, now)
        mine = self._media(tmp_path, "proxies/-f3R.ab12.mp4", 10, 2 * self.DAY, now)
        other = self._media(tmp_path, "proxies/-f3Rx.ab12.mp4", 10, 2 * self.DAY, now)
        self._mark(tmp_path, "-f3R", 8 * self.DAY, now)
        removed = prune.prune(tmp_path, keep_gb=1.0, now=now, recording_days=7)
        assert mine in removed and other.exists()

    def test_markers_are_not_media(self, tmp_path):
        now = 10 * self.DAY
        self._mark(tmp_path, "x", 1 * self.DAY, now)
        assert prune.media_files(tmp_path) == []

    def test_kept_partials_count_against_the_budget(self, tmp_path):
        now = 10 * self.DAY
        part = self._media(tmp_path, "kept/cut.ts", 600, 3 * self.DAY, now)
        self._mark(tmp_path, "cut", 1 * self.DAY, now, whole=False)
        assert prune.prune(tmp_path, keep_gb=100e-9, now=now, recording_days=7) == [part]

    def test_freed_space_counts_toward_the_floor(self, tmp_path):
        # The expired recording frees 600 bytes; the floor of 500 is then met,
        # so the other (younger) video stays.
        now = 10 * self.DAY
        self._media(tmp_path, "videos/old.mp4", 600, 2 * self.DAY, now)
        self._mark(tmp_path, "old", 8 * self.DAY, now)
        keep = self._media(tmp_path, "videos/other.mp4", 600, 3 * self.DAY, now)
        prune.prune(tmp_path, keep_gb=1.0, now=now, min_free_gb=500e-9, free_bytes=0,
                    recording_days=7)
        assert keep.exists()


class TestRecordingsGiveWayFirst:
    """Space allowing: when the budget or the disk floor needs room, kept live
    recordings go first, oldest kept first; the floor never takes a download,
    and the 11 harness videos are downloads."""

    DAY = 86400.0

    def _media(self, root, rel, size, read_ago, now):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * size)
        os.utime(p, (now - read_ago, now - read_ago))
        return p

    def test_a_full_disk_takes_a_recording_before_an_old_download(self, tmp_path):
        now = 100 * self.DAY
        harness = self._media(tmp_path, "videos/hOKZoeJNTpM.mp4", 600, 90 * self.DAY, now)
        rec = self._media(tmp_path, "videos/rec1.mp4", 600, 2 * 3600, now)
        cache.mark_kept("rec1", tmp_path, whole=True, now=now - self.DAY, size=600)
        removed = prune.prune(tmp_path, keep_gb=1.0, now=now, min_free_gb=500e-9,
                              free_bytes=0, recording_days=7)
        assert removed == [rec] and harness.exists()

    def test_the_floor_never_takes_a_download(self, tmp_path):
        now = 100 * self.DAY
        old = self._media(tmp_path, "videos/old.mp4", 600, 90 * self.DAY, now)
        assert prune.prune(tmp_path, keep_gb=1.0, now=now, min_free_gb=1.0,
                           free_bytes=0, recording_days=7) == []
        assert old.exists()

    def test_recordings_go_oldest_kept_first_whatever_was_read_last(self, tmp_path):
        now = 100 * self.DAY
        older = self._media(tmp_path, "kept/a.ts", 600, 2 * 3600, now)
        newer = self._media(tmp_path, "videos/b.mp4", 600, 5 * self.DAY, now)
        cache.mark_kept("a", tmp_path, whole=False, now=now - 3 * self.DAY, size=600)
        cache.mark_kept("b", tmp_path, whole=True, now=now - 1 * self.DAY, size=600)
        removed = prune.prune(tmp_path, keep_gb=1.0, now=now, min_free_gb=500e-9,
                              free_bytes=0, recording_days=7)
        assert removed == [older] and newer.exists()

    def test_the_budget_takes_recordings_then_old_downloads(self, tmp_path):
        now = 100 * self.DAY
        old = self._media(tmp_path, "videos/old.mp4", 600, 90 * self.DAY, now)
        rec = self._media(tmp_path, "videos/rec1.mp4", 600, 2 * 3600, now)
        cache.mark_kept("rec1", tmp_path, whole=True, now=now - self.DAY, size=600)
        removed = prune.prune(tmp_path, keep_gb=100e-9, now=now, recording_days=7)
        assert removed == [rec, old]

    def test_a_recording_being_read_is_not_taken_for_space(self, tmp_path):
        now = 100 * self.DAY
        rec = self._media(tmp_path, "videos/rec1.mp4", 600, 60, now)
        cache.mark_kept("rec1", tmp_path, whole=True, now=now - self.DAY, size=600)
        assert prune.prune(tmp_path, keep_gb=1.0, now=now, min_free_gb=1.0,
                           free_bytes=0, recording_days=7) == []
        assert rec.exists()


class TestAMarkerTakesOnlyWhatItKept:
    """A marker ages out the file it was written for, never a download that
    took that video's place later."""

    DAY = 86400.0

    def _media(self, root, rel, size, read_ago, now):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * size)
        os.utime(p, (now - read_ago, now - read_ago))
        return p

    def test_a_download_after_a_partial_stays(self, tmp_path):
        now = 100 * self.DAY
        part = self._media(tmp_path, "kept/v1.ts", 300, 2 * self.DAY, now)
        cache.mark_kept("v1", tmp_path, whole=False, now=now - 8 * self.DAY, size=300)
        video = self._media(tmp_path, "videos/v1.mp4", 900, 2 * self.DAY, now)
        proxy = self._media(tmp_path, "proxies/v1.ab12.mp4", 90, 2 * self.DAY, now)
        assert prune.prune(tmp_path, keep_gb=1.0, now=now, recording_days=7) == [part]
        assert video.exists() and proxy.exists()
        assert not cache.kept_marker("v1", tmp_path).exists()

    def test_a_download_after_a_pruned_recording_stays(self, tmp_path):
        now = 100 * self.DAY
        cache.mark_kept("v1", tmp_path, whole=True, now=now - 8 * self.DAY, size=600)
        video = self._media(tmp_path, "videos/v1.mp4", 900, 2 * self.DAY, now)  # re-downloaded
        assert prune.prune(tmp_path, keep_gb=1.0, now=now, recording_days=7) == []
        assert video.exists() and not cache.kept_marker("v1", tmp_path).exists()

    def test_the_recording_itself_still_goes_with_its_proxies(self, tmp_path):
        now = 100 * self.DAY
        video = self._media(tmp_path, "videos/v1.mp4", 600, 2 * self.DAY, now)
        proxy = self._media(tmp_path, "proxies/v1.ab12.mp4", 90, 2 * self.DAY, now)
        cache.mark_kept("v1", tmp_path, whole=True, now=now - 8 * self.DAY, size=600)
        assert set(prune.prune(tmp_path, keep_gb=1.0, now=now, recording_days=7)) == {
            video, proxy}

    def test_keeping_writes_the_kept_file_s_size(self, tmp_path):
        rec = tmp_path / "live" / "v1" / "rec.0.ts"
        rec.parent.mkdir(parents=True)
        rec.write_bytes(b"x" * 123)
        cache.keep_partial(rec, "v1", tmp_path)
        import json as _json
        assert _json.loads(cache.kept_marker("v1", tmp_path).read_text())["size"] == 123


# --- kept camera games ------------------------------------------------------------------

DAY = 86400.0


def kept_game(cams, vid, age_days, now, size=1000):
    d = cams / "kept" / vid
    d.mkdir(parents=True)
    for role in ("house_a", "house_h", "long_a", "long_h"):
        (d / f"{role}.ts").write_bytes(b"x" * size)
    (d / "clock.json").write_text("{}")
    t = now - age_days * DAY
    for p in [*d.iterdir(), d]:
        os.utime(p, (t, t))
    return d


class TestKeptCameraGames:
    def test_games_past_their_days_go_first(self, tmp_path):
        now = 1_800_000_000.0
        cams = tmp_path / "cams"
        old = kept_game(cams, "old", 4, now)
        new = kept_game(cams, "new", 1, now)
        removed = prune.prune(tmp_path / "cache", keep_gb=1.0, now=now, cams_root=cams,
                              camera_days=3, free_bytes=10**12)
        assert old in removed and not old.exists() and new.exists()

    def test_under_the_floor_the_oldest_game_goes_before_any_video(self, tmp_path):
        now = 1_800_000_000.0
        cams, root = tmp_path / "cams", tmp_path / "cache"
        older = kept_game(cams, "older", 2, now, size=1000)
        newer = kept_game(cams, "newer", 1, now, size=1000)
        video = root / "videos" / "vid00000001.mp4"
        video.parent.mkdir(parents=True)
        video.write_bytes(b"v" * 1000)
        os.utime(video, (now - 30 * DAY, now - 30 * DAY))
        # 3000 bytes short of the floor: one game (4000 bytes) is enough.
        removed = prune.prune(root, keep_gb=1.0, now=now, cams_root=cams, camera_days=3,
                              min_free_gb=10000e-9, free_bytes=7000)
        assert not older.exists() and newer.exists() and video.exists()
        assert older in removed


def kept_game_at(cams, vid, age_days, now, size=1000, **meta):
    import json as _json

    d = kept_game(cams, vid, age_days, now, size)
    (d / "clock.json").write_text(_json.dumps({"kept_at": now - age_days * DAY, **meta}))
    return d


class TestADayOfFootageOnceClipped:
    NOW = 1_800_000_000.0

    def run(self, tmp_path, clipped, **kw):
        kw.setdefault("free_bytes", 10**12)
        return prune.prune(tmp_path / "cache", keep_gb=1.0, now=self.NOW,
                           cams_root=tmp_path / "cams", camera_days=1, camera_max_days=3,
                           clipped=clipped, **kw)

    def test_a_clipped_game_goes_after_a_day_an_unclipped_one_after_three(self, tmp_path):
        cams = tmp_path / "cams"
        a = kept_game_at(cams, "a", 1.5, self.NOW)
        b = kept_game_at(cams, "b", 1.5, self.NOW)
        c = kept_game_at(cams, "c", 3.5, self.NOW)
        self.run(tmp_path, lambda names: {"a"} & set(names))
        assert not a.exists() and b.exists() and not c.exists()

    def test_without_word_from_the_api_nothing_is_clipped(self, tmp_path):
        cams = tmp_path / "cams"
        a = kept_game_at(cams, "a", 1.5, self.NOW)
        self.run(tmp_path, None)
        assert a.exists()

    def test_under_the_floor_clipped_games_go_before_recordings_and_unclipped_last(
            self, tmp_path):
        import json as _json

        cams, root = tmp_path / "cams", tmp_path / "cache"
        cl = kept_game_at(cams, "cl", 0.5, self.NOW)
        un = kept_game_at(cams, "un", 0.9, self.NOW)
        video = root / "videos" / "recvid00001.mp4"
        video.parent.mkdir(parents=True)
        video.write_bytes(b"v" * 1000)
        os.utime(video, (self.NOW - 2 * DAY, self.NOW - 2 * DAY))
        marker = cache.kept_marker("recvid00001", root)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(_json.dumps({"kept_at": self.NOW - 2 * DAY, "whole": True,
                                       "size": 1000}))
        # 3000 bytes short: the clipped game (4000) alone is enough.
        self.run(tmp_path, lambda names: {"cl"}, min_free_gb=10000e-9, free_bytes=7000)
        assert not cl.exists() and un.exists() and video.exists()
        # 5000 short with the clipped game gone: the recording, then the unclipped game.
        removed = self.run(tmp_path, lambda names: set(), min_free_gb=10000e-9,
                           free_bytes=5000)
        assert not video.exists() and not un.exists()
        assert removed.index(video) < removed.index(un)

    def test_a_game_still_being_cut_is_not_aged_out(self, tmp_path):
        # Kept 30 min ago, clipped, past a short camera_days: its cut is spared.
        d = kept_game_at(tmp_path / "cams", "a", 30 / 1440, self.NOW, trim_pending=True)
        prune.prune(tmp_path / "cache", keep_gb=1.0, now=self.NOW, cams_root=tmp_path / "cams",
                    camera_days=0.01, camera_max_days=3, clipped=lambda names: set(names),
                    free_bytes=10**12)
        assert d.exists()

    def test_kept_at_dates_a_game_not_its_directory(self, tmp_path):
        import json as _json

        d = kept_game(tmp_path / "cams", "a", 0, self.NOW)
        (d / "clock.json").write_text(_json.dumps({"kept_at": self.NOW - 4 * DAY}))
        self.run(tmp_path, None)
        assert not d.exists()


def test_a_game_stuck_mid_cut_still_ages_out(tmp_path):
    now = 1_800_000_000.0
    d = kept_game_at(tmp_path / "cams", "a", 4, now, trim_pending=True)
    prune.prune(tmp_path / "cache", keep_gb=1.0, now=now, cams_root=tmp_path / "cams",
                camera_days=1, camera_max_days=3, clipped=None, free_bytes=10**12)
    assert not d.exists()
